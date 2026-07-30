"""Cliente mínimo da API do Plex, só com o que interessa para legendas.

Endpoints usados (descobertos por inspeção, não são documentados oficialmente):

    GET /library/sections
    GET /library/sections/{id}/all
    GET /library/metadata/{rk}/allLeaves
    GET /library/metadata/{rk}
    GET /library/metadata/{rk}/subtitles?language=XX   -> candidatos com score
    PUT /library/metadata/{rk}/subtitles?key=...       -> baixa e aplica
"""
from __future__ import annotations

import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Iterator


@dataclass
class Serie:
    rating_key: str
    titulo: str
    biblioteca: str
    tipo: str = "show"   # "show" (série/anime) ou "movie" (filme)


@dataclass
class Episodio:
    rating_key: str
    temporada: str
    numero: str
    titulo: str
    arquivo: str = ""   # nome do arquivo de vídeo (para casar o release)


@dataclass
class Candidato:
    stream_id: str
    score: int
    titulo: str
    provedor: str
    afinidade: int = 0        # quão bem o release casa com o arquivo (0 = nada)
    mesma_serie: bool = True   # False = título é de outra série (casou só por SxxExx)


# Tokens de release usados para casar a legenda com o arquivo. A resolução
# pesa mais que os demais: uma legenda de 720p num arquivo 1080p costuma ter
# corte/timing diferente e dessincroniza.
_RESOLUCAO = re.compile(r"\b(2160p|1080p|720p|480p)\b", re.I)
_FONTE = re.compile(r"\b(web[\s._-]?dl|webrip|web|bluray|blu[\s._-]?ray|"
                    r"bdrip|brrip|hdtv|pdtv|dvdrip|dvd|hdrip)\b", re.I)
_CODEC = re.compile(r"\b(x264|x265|h[\s._-]?264|h[\s._-]?265|avc|hevc|xvid)\b", re.I)
_GRUPO = re.compile(r"-([A-Za-z0-9]+)(?:\.\w{2,4})?$")


def _norm(s: str) -> str:
    return re.sub(r"[\s._-]+", "", (s or "").lower())


# Idiomas que o painel oferece. A chave é o código que o Plex espera em
# `?language=` (2 partes; `por`/`pob` dão HTTP 500). `base` é a tag ISO de 2
# letras (languageTag), `codes` os códigos de 3 letras (languageCode) e `np` um
# prefixo do nome como fallback quando o stream não traz tag/código.
IDIOMAS: dict[str, dict] = {
    "pt-BR": {"nome": "Português (BR)", "base": "pt", "codes": ("por", "pob"), "np": "portugu"},
    "en":    {"nome": "English",        "base": "en", "codes": ("eng",),       "np": "english"},
    "es":    {"nome": "Español",        "base": "es", "codes": ("spa", "esp"), "np": "espa"},
    "fr":    {"nome": "Français",       "base": "fr", "codes": ("fre", "fra"), "np": "fran"},
    "de":    {"nome": "Deutsch",        "base": "de", "codes": ("ger", "deu"), "np": "german"},
    "it":    {"nome": "Italiano",       "base": "it", "codes": ("ita",),       "np": "italia"},
    "ja":    {"nome": "日本語 (JP)",     "base": "ja", "codes": ("jpn",),       "np": "japan"},
}


def nome_idioma(idioma: str) -> str:
    info = IDIOMAS.get(idioma)
    return info["nome"] if info else idioma


def casa_idioma(tag: str, code: str, nome: str, idioma: str) -> bool:
    """Se um stream de legenda (por tag/código/nome) é do idioma-alvo.

    Prioriza `languageTag` (ex.: 'pt-BR'→base 'pt') e `languageCode` (ex.:
    'por'); só cai no nome quando ambos faltam (faixas embutidas às vezes vêm
    sem código). Idioma desconhecido na tabela: casa pela base da própria tag.
    """
    info = IDIOMAS.get(idioma)
    base = info["base"] if info else idioma.split("-")[0].lower()
    codes = info["codes"] if info else ()
    np = info["np"] if info else base
    # languageTag costuma ser 2 letras ('en', 'pt-BR'), mas legenda baixada às
    # vezes vem com 3 ('eng'); aceita ambos.
    t = (tag or "").split("-")[0].lower()
    if t:
        return t == base or t in codes
    c = (code or "").lower()
    if c:
        return c == base or c in codes
    return bool(np) and np in (nome or "").lower()


# Marca de episódio (S01E02 ou 1x02): tudo antes dela no nome do arquivo é o
# nome da série, usado para descartar candidato de OUTRA série.
_MARCADOR_EP = re.compile(r"[\s._-](S\d{1,2}E\d{1,3}|\d{1,2}x\d{1,3})", re.I)


def _palavras(texto: str) -> list[str]:
    """Palavras aproveitáveis de um texto (>=2 letras, alfanuméricas)."""
    return [t for t in re.split(r"[\s._-]+", (texto or "").lower())
            if len(t) >= 2 and t.isalnum()]


def serie_tokens(arquivo: str) -> list[str]:
    """Palavras do nome da série (antes do SxxExx), para comparar com candidatos."""
    m = _MARCADOR_EP.search(arquivo or "")
    bruto = arquivo[:m.start()] if m else ""
    return _palavras(bruto)


def candidato_mesma_serie(tokens: list[str], titulo_candidato: str) -> bool:
    """True se o título do candidato bate com o nome da série do arquivo.

    Evita o caso em que o Plex casa pelo SxxExx e devolve legenda de outra série
    (ex.: 'Mad Men' para 'Mad About You'). Sem tokens para julgar, aceita.

    A identidade da série está nas palavras DISTINTIVAS (≥4 letras): exige que a
    maioria delas apareça no título. Palavras curtas ('yu', 'of', 'the') são
    comuns demais para servir de prova — sem isso, 'Yu-Gi-Oh' passava por
    'Yu Yu Hakusho' (dois 'yu' batiam). Tokens são deduplicados para o 'yu'
    repetido não contar em dobro. Nome só de palavras curtas: usa todas.
    """
    if not tokens:
        return True
    alvo = _norm(titulo_candidato)
    unicos = set(tokens)
    distintivos = {t for t in unicos if len(t) >= 4}
    julgar = distintivos or unicos
    achados = sum(1 for t in julgar if t in alvo)
    return achados / len(julgar) >= 0.6


def afinidade_release(arquivo: str, candidato: str) -> int:
    """Pontua quanto o título do candidato casa com o nome do arquivo.

    resolução vale 3, fonte 2, codec 1, grupo de release 2. Zero quando nada
    coincide — nesse caso a decisão fica só com a avaliação do Plex.
    """
    def tok(regex, texto):
        m = regex.search(texto or "")
        return _norm(m.group(1)) if m else None

    peso = 0
    ra, rc = tok(_RESOLUCAO, arquivo), tok(_RESOLUCAO, candidato)
    if ra and rc and ra == rc:
        peso += 3
    def norm_fonte(f):
        return f.replace("webdl", "web").replace("dvdrip", "dvd")
    fa, fc = tok(_FONTE, arquivo), tok(_FONTE, candidato)
    if fa and fc and norm_fonte(fa) == norm_fonte(fc):
        peso += 2
    ca, cc = tok(_CODEC, arquivo), tok(_CODEC, candidato)
    if ca and cc and ca.replace("h", "x") == cc.replace("h", "x"):
        peso += 1
    ga, gc = tok(_GRUPO, arquivo), tok(_GRUPO, candidato)
    if ga and gc and ga == gc:
        peso += 2
    return peso


class PlexError(RuntimeError):
    pass


class Plex:
    def __init__(self, url: str, token: str, timeout: int = 90) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout

    # ---------- infraestrutura ----------

    def _req(self, caminho: str, metodo: str = "GET", **params) -> ET.Element | None:
        params["X-Plex-Token"] = self.token
        alvo = f"{self.url}{caminho}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(alvo, method=metodo)
        req.add_header("Accept", "application/xml")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                corpo = r.read()
        except urllib.error.HTTPError as e:
            raise PlexError(f"{metodo} {caminho} -> HTTP {e.code}") from e
        if not corpo.strip():
            return None
        try:
            return ET.fromstring(corpo)
        except ET.ParseError as e:
            raise PlexError(f"resposta não-XML em {caminho}: {corpo[:120]!r}") from e

    # ---------- consultas ----------

    def bibliotecas(self, tipos: tuple[str, ...] = ("show",)) -> list[tuple[str, str, str]]:
        """[(key, tipo, titulo)] das bibliotecas cujos tipos casam (padrão: séries)."""
        r = self._req("/library/sections")
        if r is None:
            return []
        return [(d.get("key"), d.get("type"), d.get("title"))
                for d in r.iter("Directory") if d.get("type") in tipos]

    def series(self, lib_key: str | None = None) -> list[Serie]:
        """Itens de uma biblioteca. Sem `lib_key`: todas as de série (comportamento
        antigo). Com `lib_key`: só aquela — pode ser de filmes (itens avulsos)."""
        libs = self.bibliotecas(("show", "movie"))
        if lib_key:
            libs = [l for l in libs if l[0] == str(lib_key)]
        else:
            libs = [l for l in libs if l[1] == "show"]
        saida: list[Serie] = []
        for key, tipo, nome_lib in libs:
            r = self._req(f"/library/sections/{key}/all")
            if r is None:
                continue
            if tipo == "movie":
                for v in r.iter("Video"):
                    if v.get("ratingKey"):
                        saida.append(Serie(v.get("ratingKey"), v.get("title") or "?",
                                           nome_lib, "movie"))
            else:
                for d in r.iter("Directory"):
                    if d.get("ratingKey"):
                        saida.append(Serie(d.get("ratingKey"), d.get("title") or "?",
                                           nome_lib, "show"))
        return sorted(saida, key=lambda s: s.titulo.lower())

    def episodios(self, serie_rk: str) -> list[Episodio]:
        r = self._req(f"/library/metadata/{serie_rk}/allLeaves")
        if r is None:
            return []
        saida = []
        for v in r.iter("Video"):
            arq = ""
            for p in v.iter("Part"):
                arq = (p.get("file") or "").rsplit("/", 1)[-1]
            saida.append(Episodio(v.get("ratingKey"), v.get("parentIndex") or "?",
                                  v.get("index") or "?", v.get("title") or "", arq))
        return saida

    def legendas_do_episodio(self, ep_rk: str) -> list[tuple[str, str, bool]]:
        """[(stream_id, idioma, selecionada)] das legendas já presentes."""
        r = self._req(f"/library/metadata/{ep_rk}")
        if r is None:
            return []
        saida = []
        for p in r.iter("Part"):
            for s in p.iter("Stream"):
                if s.get("streamType") == "3":
                    saida.append((s.get("id"), s.get("language") or "",
                                  s.get("selected") == "1"))
        return saida

    def tem_idioma(self, ep_rk: str, idioma: str) -> bool:
        """Se já existe legenda no idioma-alvo (detecção por tag/código/nome)."""
        return any(casa_idioma(s["tag"], s["code"], s["lang"], idioma)
                   for s in self.streams_legenda(ep_rk))

    def streams_legenda(self, ep_rk: str) -> list[dict]:
        """Legendas do episódio com origem: idioma (nome + tag + código), se é
        embutida (no arquivo de vídeo, sem `key`) e se veio de provedor. Uma
        chamada, para varreduras."""
        r = self._req(f"/library/metadata/{ep_rk}")
        if r is None:
            return []
        out = []
        for p in r.iter("Part"):
            for s in p.iter("Stream"):
                if s.get("streamType") == "3":
                    out.append({"lang": s.get("language") or "",
                                "tag": s.get("languageTag") or "",
                                "code": s.get("languageCode") or "",
                                "embutida": not s.get("key"),
                                "provedor": bool(s.get("providerTitle")),
                                "sel": s.get("selected") == "1"})
        return out

    def afinidade_atual(self, ep_rk: str, arquivo: str, idioma: str) -> int:
        """Maior afinidade de release entre as legendas do idioma já presentes.

        Usa o título do stream (que traz o release, ex.: 'The.Rookie...1080p.WEB')
        para medir quão bem a legenda já baixada casa com o arquivo. Uma legenda
        sem release no título (só 'Português') fica com afinidade 0.
        """
        r = self._req(f"/library/metadata/{ep_rk}")
        if r is None:
            return 0
        melhor = 0
        for p in r.iter("Part"):
            for s in p.iter("Stream"):
                if s.get("streamType") == "3" and casa_idioma(
                        s.get("languageTag") or "", s.get("languageCode") or "",
                        s.get("language") or "", idioma):
                    titulo = s.get("title") or s.get("extendedDisplayTitle") or ""
                    melhor = max(melhor, afinidade_release(arquivo, titulo))
        return melhor

    def tem_legenda_de_provedor(self, ep_rk: str, idioma: str) -> bool:
        """Se alguma legenda do idioma veio de um provedor (OpenSubtitles etc.).

        Legendas geradas por nós ficam como arquivo sidecar externo, sem
        `providerTitle`. Só as baixadas de provedor têm esse campo — e são as
        únicas que faz sentido o `reavaliar` reconsiderar. Uma legenda sidecar
        (tradução, extração de embutida) não deve ser substituída por uma de
        provedor, que pode estar mal casada.
        """
        r = self._req(f"/library/metadata/{ep_rk}")
        if r is None:
            return False
        for p in r.iter("Part"):
            for s in p.iter("Stream"):
                if (s.get("streamType") == "3" and s.get("providerTitle")
                        and casa_idioma(s.get("languageTag") or "",
                                        s.get("languageCode") or "",
                                        s.get("language") or "", idioma)):
                    return True
        return False

    # ---------- busca e download ----------

    def capa(self, rk: str) -> tuple[bytes, str] | None:
        """Baixa a capa (poster) de uma série/temporada. (bytes, content-type)."""
        try:
            r = self._req(f"/library/metadata/{rk}")
        except PlexError:
            return None
        if r is None:
            return None
        thumb = ""
        for d in list(r.iter("Directory")) + list(r.iter("Video")):
            thumb = d.get("thumb") or ""
            break
        if not thumb:
            return None
        alvo = f"{self.url}{thumb}?X-Plex-Token={self.token}"
        try:
            with urllib.request.urlopen(alvo, timeout=self.timeout) as f:
                return f.read(), f.headers.get("Content-Type", "image/jpeg")
        except urllib.error.HTTPError:
            return None

    def tokens_serie(self, serie_rk: str) -> list[list[str]]:
        """Grupos de palavras dos títulos da série (localizado E original), um
        grupo por título — para a trava casar por QUALQUER um. Cobre arquivo em
        pt ('Hora de Aventura') com legenda em inglês ('Adventure Time' =
        originalTitle); juntar num grupo só diluiria o casamento."""
        r = self._req(f"/library/metadata/{serie_rk}")
        if r is None:
            return []
        d = next(iter(r.iter("Directory")), None)
        if d is None:
            d = next(iter(r.iter("Video")), None)
        if d is None:
            return []
        grupos = [_palavras(d.get("title") or ""), _palavras(d.get("originalTitle") or "")]
        return [g for g in grupos if g]

    def arquivo_do_episodio(self, ep_rk: str) -> str:
        """Nome do arquivo de vídeo do episódio (para casar o release)."""
        r = self._req(f"/library/metadata/{ep_rk}")
        if r is None:
            return ""
        for p in r.iter("Part"):
            return (p.get("file") or "").rsplit("/", 1)[-1]
        return ""

    def buscar(self, ep_rk: str, idioma: str, arquivo: str = "",
               filtrar_serie: bool = True,
               tokens_serie: list[list[str]] | None = None) -> list[Candidato]:
        """Candidatos ordenados por afinidade de release e, em empate, por score.

        Passe `arquivo` (nome do vídeo) para priorizar a legenda que casa com o
        release. Cada candidato traz `mesma_serie` (False = título é de outra
        série, casou só pelo SxxExx). Com `filtrar_serie=True` (padrão, usado na
        escolha automática) os de outra série são descartados; com False eles
        vêm marcados — para a escolha manual, em que o usuário decide.

        `tokens_serie` (títulos da série via `tokens_serie()`) é uma segunda
        fonte de identidade: um candidato conta como da mesma série se casar com
        o nome do ARQUIVO ou com o nome da SÉRIE. Isso cobre arquivo em pt e
        legenda em inglês (ex.: 'Hora de Aventura' vs 'Adventure Time').
        """
        r = self._req(f"/library/metadata/{ep_rk}/subtitles", language=idioma)
        if r is None:
            return []
        # grupos de identidade: nome do arquivo + títulos da série; casa se
        # QUALQUER grupo bater (evita diluir juntando tudo num grupo só).
        grupos = [g for g in ([serie_tokens(arquivo)] if arquivo else []) + (tokens_serie or []) if g]
        saida = []
        for s in r:
            if s.get("id") is None:
                continue
            titulo = s.get("title") or ""
            mesma = (not grupos) or any(candidato_mesma_serie(g, titulo) for g in grupos)
            if filtrar_serie and not mesma:
                continue
            try:
                score = int(s.get("score") or 0)
            except ValueError:
                score = 0
            saida.append(Candidato(s.get("id"), score, titulo,
                                   s.get("providerTitle") or "",
                                   afinidade_release(arquivo, titulo) if arquivo else 0,
                                   mesma))
        # mesma série primeiro, depois afinidade, depois score.
        return sorted(saida, key=lambda c: (c.mesma_serie, c.afinidade, c.score),
                      reverse=True)

    def aplicar(self, ep_rk: str, stream_id: str) -> None:
        """Baixa o candidato e o aplica ao episódio.

        O Plex responde 200 ou 204 — ambos são sucesso.
        """
        self._req(f"/library/metadata/{ep_rk}/subtitles", metodo="PUT",
                  key=f"/library/streams/{stream_id}")

    def conteudo_stream(self, stream_id: str) -> bytes:
        """Texto (SRT) de uma legenda externa/baixada, direto por HTTP — sem
        precisar ler o blob no banco do Plex."""
        alvo = f"{self.url}/library/streams/{stream_id}?download=1&X-Plex-Token={self.token}"
        with urllib.request.urlopen(alvo, timeout=self.timeout) as f:
            return f.read()

    def id_legenda_externa(self, ep_rk: str, idioma: str) -> str | None:
        """id da legenda EXTERNA (baixada, tem `key`) no idioma — para buscar o
        conteúdo via `conteudo_stream`. None se não houver."""
        r = self._req(f"/library/metadata/{ep_rk}")
        if r is None:
            return None
        for p in r.iter("Part"):
            for s in p.iter("Stream"):
                if (s.get("streamType") == "3" and s.get("key") and casa_idioma(
                        s.get("languageTag") or "", s.get("languageCode") or "",
                        s.get("language") or "", idioma)):
                    return s.get("id")
        return None

    def part_do_episodio(self, ep_rk: str) -> str | None:
        r = self._req(f"/library/metadata/{ep_rk}")
        if r is None:
            return None
        for p in r.iter("Part"):
            return p.get("id")
        return None

    def definir_legenda_padrao(self, part_id: str, stream_id: str) -> None:
        """Fixa a legenda default (selected=1) de uma part."""
        alvo = (f"{self.url}/library/parts/{part_id}"
                f"?subtitleStreamID={stream_id}&X-Plex-Token={self.token}")
        req = urllib.request.Request(alvo, method="PUT")
        urllib.request.urlopen(req, timeout=self.timeout).close()


def temporadas(plex: Plex, serie_rk: str) -> list[int]:
    """Números das temporadas da série, ordenados."""
    nums = {int(e.temporada) for e in plex.episodios(serie_rk) if e.temporada.isdigit()}
    return sorted(nums)


def episodios_status(plex: Plex, serie_rk: str, temporada: str,
                     idioma: str) -> Iterator[dict]:
    """Status de cada episódio de uma temporada, para a lista interativa.

    Por episódio: se tem legenda no idioma-alvo, a afinidade da atual, quais
    idiomas há embutidos (traduzíveis) e se a legenda atual veio de provedor.
    Só leitura.
    """
    eps = [e for e in plex.episodios(serie_rk) if e.temporada == str(temporada)]
    for i, ep in enumerate(eps, 1):
        arquivo = ep.arquivo or plex.arquivo_do_episodio(ep.rating_key)
        yield _card_status(plex, ep.rating_key, f"S{ep.temporada}E{ep.numero}",
                           ep.numero, ep.titulo, arquivo, idioma, i, len(eps))


def _card_status(plex: Plex, rk: str, rotulo: str, numero: str, titulo: str,
                 arquivo: str, idioma: str, i: int, total: int) -> dict:
    """Monta o dict de status de um item (episódio ou filme) para a lista."""
    streams = plex.streams_legenda(rk)
    tem_pt = any(casa_idioma(s["tag"], s["code"], s["lang"], idioma) for s in streams)
    emb_langs = sorted({s["lang"] for s in streams if s["embutida"] and s["lang"]})
    de_provedor = any(casa_idioma(s["tag"], s["code"], s["lang"], idioma) and s["provedor"]
                      for s in streams)
    afin = plex.afinidade_atual(rk, arquivo, idioma) if tem_pt else -1
    sel = next((s for s in streams if s.get("sel")), None)
    return {"i": i, "total": total, "rk": rk, "ep": rotulo, "numero": numero,
            "titulo": titulo, "tem_pt": tem_pt, "afinidade": afin,
            "emb_langs": emb_langs,
            "en_emb": any("english" in l.lower() for l in emb_langs),
            "de_provedor": de_provedor,
            "aplicada": sel["lang"] if sel else "",
            "aplicada_alvo": bool(sel and casa_idioma(sel["tag"], sel["code"], sel["lang"], idioma))}


def filme_status(plex: Plex, filme_rk: str, idioma: str) -> Iterator[dict]:
    """Status de um filme (item único, sem temporada), no mesmo formato de card."""
    r = plex._req(f"/library/metadata/{filme_rk}")
    titulo = arquivo = ""
    if r is not None:
        for v in r.iter("Video"):
            titulo = v.get("title") or ""
            break
        for p in r.iter("Part"):
            arquivo = (p.get("file") or "").rsplit("/", 1)[-1]
            break
    yield _card_status(plex, filme_rk, "Filme", "", titulo, arquivo, idioma, 1, 1)


def cobertura_serie(plex: Plex, serie_rk: str, idioma: str) -> Iterator[dict]:
    """Varre a série SEM baixar nada e informa quantos episódios já têm legenda.

    Só leitura — para o painel mostrar o estado atual antes de qualquer ação.
    Gera um evento por episódio, com a contagem acumulada.
    """
    eps = plex.episodios(serie_rk)
    com = emb = 0
    for i, ep in enumerate(eps, 1):
        streams = plex.streams_legenda(ep.rating_key)
        tem = any(casa_idioma(s["tag"], s["code"], s["lang"], idioma) for s in streams)
        en_emb = any(s["embutida"] and "english" in s["lang"].lower() for s in streams)
        if tem:
            com += 1
        if en_emb:
            emb += 1
        yield {"i": i, "total": len(eps), "rk": ep.rating_key,
               "ep": f"S{ep.temporada}E{ep.numero}", "titulo": ep.titulo,
               "com": com, "emb": emb, "tem": tem, "en_emb": en_emb}


def fonte_traducao(plex: Plex, serie_rk: str, temporada: str, idioma: str,
                   tokens_serie: list[list[str]] | None = None) -> Iterator[dict]:
    """Por episódio, classifica a FONTE de tradução disponível — para o painel
    mostrar quem dá pra traduzir e quem só o Whisper resolve. Só leitura.

    Classes: 'tem' (já tem o idioma-alvo), 'emb' (inglês embutido no arquivo),
    'online' (inglês achável no Plex, passa a trava de série), 'sem' (nenhum
    inglês em lugar nenhum → Whisper). A busca online (cara) só roda em quem não
    tem o alvo nem inglês embutido.
    """
    eps = [e for e in plex.episodios(serie_rk) if e.temporada == str(temporada)]
    for i, ep in enumerate(eps, 1):
        streams = plex.streams_legenda(ep.rating_key)
        if any(casa_idioma(s["tag"], s["code"], s["lang"], idioma) for s in streams):
            classe = "tem"
        elif any(s["embutida"] and "english" in s["lang"].lower() for s in streams):
            classe = "emb"
        else:
            arq = ep.arquivo or plex.arquivo_do_episodio(ep.rating_key)
            cands = plex.buscar(ep.rating_key, "en", arq, tokens_serie=tokens_serie)
            classe = "online" if cands else "sem"
        yield {"i": i, "total": len(eps), "rk": ep.rating_key,
               "ep": f"S{ep.temporada}E{ep.numero}", "classe": classe}


def processar_serie(plex: Plex, serie_rk: str, idioma: str, score_min: int,
                    reavaliar: bool = False,
                    so_existentes: bool = False,
                    temporada: str = "") -> Iterator[dict]:
    """Percorre a série e baixa o melhor candidato de cada episódio.

    Por padrão pula episódios que já têm legenda no idioma e baixa nos vazios.

    - `reavaliar=True`: reconsidera também os que já têm legenda; se existir um
      candidato com afinidade de release MAIOR que a atual, troca por ele (corrige
      legendas mal casadas). Nunca troca por afinidade igual ou menor.
    - `so_existentes=True`: nunca preenche episódios vazios — atua só sobre os que
      já têm legenda. Combinado com `reavaliar`, torna seguro rodar em qualquer
      série (não adiciona legenda a conteúdo dublado que deve ficar sem, nem toca
      em legendas geradas por outro meio que estejam num episódio sem candidato).

    Gera um dicionário por episódio, para a interface acompanhar em tempo real.
    """
    eps = plex.episodios(serie_rk)
    if temporada:
        eps = [e for e in eps if e.temporada == str(temporada)]
    tks_serie = plex.tokens_serie(serie_rk)   # identidade da série (localizado + original)
    for i, ep in enumerate(eps, 1):
        arquivo = ep.arquivo or plex.arquivo_do_episodio(ep.rating_key)
        streams = plex.streams_legenda(ep.rating_key)
        ja_tem = any(casa_idioma(s["tag"], s["code"], s["lang"], idioma) for s in streams)
        en_emb = any(s["embutida"] and "english" in s["lang"].lower() for s in streams)
        base = {"i": i, "total": len(eps), "rk": ep.rating_key, "en_emb": en_emb,
                "ep": f"S{ep.temporada}E{ep.numero}", "titulo": ep.titulo}
        atual = plex.afinidade_atual(ep.rating_key, arquivo, idioma) if ja_tem else -1
        if ja_tem and not reavaliar:
            yield {**base, "estado": "ja_tinha"}
            continue
        if not ja_tem and so_existentes:
            yield {**base, "estado": "ignorado_vazio"}
            continue
        # Nunca substitui legenda que não veio de provedor (tradução/embutida
        # nossa, sidecar externo): reavaliar só reconsidera downloads de provedor.
        if ja_tem and reavaliar and not plex.tem_legenda_de_provedor(ep.rating_key, idioma):
            yield {**base, "estado": "protegida"}
            continue

        cands = plex.buscar(ep.rating_key, idioma, arquivo, tokens_serie=tks_serie)
        if not cands:
            yield {**base, "estado": "sem_candidato"}
            continue

        melhor = cands[0]
        if melhor.score < score_min:
            yield {**base, "estado": "score_baixo", "score": melhor.score}
            continue

        # No modo reavaliar, só troca se achar afinidade estritamente maior.
        if ja_tem and melhor.afinidade <= atual:
            yield {**base, "estado": "ja_otima", "afinidade": atual}
            continue

        try:
            plex.aplicar(ep.rating_key, melhor.stream_id)
        except PlexError as e:
            yield {**base, "estado": "erro", "detalhe": str(e)}
            continue

        if ja_tem:
            yield {**base, "estado": "melhorada", "score": melhor.score,
                   "afinidade": melhor.afinidade, "afinidade_antes": atual,
                   "release": melhor.titulo, "provedor": melhor.provedor}
            continue

        yield {**base, "estado": "baixada", "score": melhor.score,
               "afinidade": melhor.afinidade,
               "release": melhor.titulo, "provedor": melhor.provedor}
