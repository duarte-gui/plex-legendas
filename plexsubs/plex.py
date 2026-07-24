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


# Marca de episódio (S01E02 ou 1x02): tudo antes dela no nome do arquivo é o
# nome da série, usado para descartar candidato de OUTRA série.
_MARCADOR_EP = re.compile(r"[\s._-](S\d{1,2}E\d{1,3}|\d{1,2}x\d{1,3})", re.I)


def serie_tokens(arquivo: str) -> list[str]:
    """Palavras do nome da série (antes do SxxExx), para comparar com candidatos."""
    m = _MARCADOR_EP.search(arquivo or "")
    bruto = arquivo[:m.start()] if m else ""
    return [t for t in re.split(r"[\s._-]+", bruto.lower()) if len(t) >= 2 and t.isalnum()]


def candidato_mesma_serie(tokens: list[str], titulo_candidato: str) -> bool:
    """True se o título do candidato bate com o nome da série do arquivo.

    Evita o caso em que o Plex casa pelo SxxExx e devolve legenda de outra série
    (ex.: 'Mad Men' para 'Mad About You'). Exige que a maioria das palavras da
    série apareça no título. Sem tokens para julgar, aceita (não filtra).
    """
    if not tokens:
        return True
    alvo = _norm(titulo_candidato)
    achados = sum(1 for t in tokens if t in alvo)
    return achados / len(tokens) >= 0.6


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

    def bibliotecas(self) -> list[tuple[str, str, str]]:
        """[(key, tipo, titulo)] — só as de série."""
        r = self._req("/library/sections")
        if r is None:
            return []
        return [(d.get("key"), d.get("type"), d.get("title"))
                for d in r.iter("Directory") if d.get("type") == "show"]

    def series(self) -> list[Serie]:
        saida: list[Serie] = []
        for key, _tipo, nome_lib in self.bibliotecas():
            r = self._req(f"/library/sections/{key}/all")
            if r is None:
                continue
            for d in r.iter("Directory"):
                if d.get("ratingKey"):
                    saida.append(Serie(d.get("ratingKey"), d.get("title") or "?", nome_lib))
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

    def tem_idioma(self, ep_rk: str, prefixo: str) -> bool:
        """Se já existe legenda cujo idioma começa com `prefixo` (ex.: 'Portugu')."""
        return any(prefixo.lower() in idi.lower()
                   for _sid, idi, _sel in self.legendas_do_episodio(ep_rk))

    def streams_legenda(self, ep_rk: str) -> list[dict]:
        """Legendas do episódio com origem: idioma, se é embutida (no arquivo de
        vídeo, sem `key`) e se veio de provedor. Uma chamada, para varreduras."""
        r = self._req(f"/library/metadata/{ep_rk}")
        if r is None:
            return []
        out = []
        for p in r.iter("Part"):
            for s in p.iter("Stream"):
                if s.get("streamType") == "3":
                    out.append({"lang": s.get("language") or "",
                                "embutida": not s.get("key"),
                                "provedor": bool(s.get("providerTitle"))})
        return out

    def afinidade_atual(self, ep_rk: str, arquivo: str, prefixo: str) -> int:
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
                if s.get("streamType") == "3" and prefixo.lower() in (s.get("language") or "").lower():
                    titulo = s.get("title") or s.get("extendedDisplayTitle") or ""
                    melhor = max(melhor, afinidade_release(arquivo, titulo))
        return melhor

    def tem_legenda_de_provedor(self, ep_rk: str, prefixo: str) -> bool:
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
                if (s.get("streamType") == "3"
                        and prefixo.lower() in (s.get("language") or "").lower()
                        and s.get("providerTitle")):
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

    def arquivo_do_episodio(self, ep_rk: str) -> str:
        """Nome do arquivo de vídeo do episódio (para casar o release)."""
        r = self._req(f"/library/metadata/{ep_rk}")
        if r is None:
            return ""
        for p in r.iter("Part"):
            return (p.get("file") or "").rsplit("/", 1)[-1]
        return ""

    def buscar(self, ep_rk: str, idioma: str, arquivo: str = "",
               filtrar_serie: bool = True) -> list[Candidato]:
        """Candidatos ordenados por afinidade de release e, em empate, por score.

        Passe `arquivo` (nome do vídeo) para priorizar a legenda que casa com o
        release. Cada candidato traz `mesma_serie` (False = título é de outra
        série, casou só pelo SxxExx). Com `filtrar_serie=True` (padrão, usado na
        escolha automática) os de outra série são descartados; com False eles
        vêm marcados — para a escolha manual, em que o usuário decide.
        """
        r = self._req(f"/library/metadata/{ep_rk}/subtitles", language=idioma)
        if r is None:
            return []
        tokens = serie_tokens(arquivo) if arquivo else []
        saida = []
        for s in r:
            if s.get("id") is None:
                continue
            titulo = s.get("title") or ""
            mesma = (not tokens) or candidato_mesma_serie(tokens, titulo)
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


def temporadas(plex: Plex, serie_rk: str) -> list[int]:
    """Números das temporadas da série, ordenados."""
    nums = {int(e.temporada) for e in plex.episodios(serie_rk) if e.temporada.isdigit()}
    return sorted(nums)


def episodios_status(plex: Plex, serie_rk: str, temporada: str,
                     idioma: str, prefixo_idioma: str = "Portugu") -> Iterator[dict]:
    """Status de cada episódio de uma temporada, para a lista interativa.

    Por episódio: se tem legenda no idioma, a afinidade da atual, se tem inglês
    embutido (traduzível) e se a legenda atual veio de provedor. Só leitura.
    """
    eps = [e for e in plex.episodios(serie_rk) if e.temporada == str(temporada)]
    for i, ep in enumerate(eps, 1):
        arquivo = ep.arquivo or plex.arquivo_do_episodio(ep.rating_key)
        streams = plex.streams_legenda(ep.rating_key)
        tem_pt = any(prefixo_idioma.lower() in s["lang"].lower() for s in streams)
        emb_langs = sorted({s["lang"] for s in streams if s["embutida"] and s["lang"]})
        de_provedor = any(prefixo_idioma.lower() in s["lang"].lower() and s["provedor"]
                          for s in streams)
        afin = plex.afinidade_atual(ep.rating_key, arquivo, prefixo_idioma) if tem_pt else -1
        yield {"i": i, "total": len(eps), "rk": ep.rating_key,
               "ep": f"S{ep.temporada}E{ep.numero}", "numero": ep.numero,
               "titulo": ep.titulo, "tem_pt": tem_pt, "afinidade": afin,
               "emb_langs": emb_langs,
               "en_emb": any("english" in l.lower() for l in emb_langs),
               "de_provedor": de_provedor}


def cobertura_serie(plex: Plex, serie_rk: str,
                    prefixo_idioma: str = "Portugu") -> Iterator[dict]:
    """Varre a série SEM baixar nada e informa quantos episódios já têm legenda.

    Só leitura — para o painel mostrar o estado atual antes de qualquer ação.
    Gera um evento por episódio, com a contagem acumulada.
    """
    eps = plex.episodios(serie_rk)
    com = emb = 0
    for i, ep in enumerate(eps, 1):
        streams = plex.streams_legenda(ep.rating_key)
        tem = any(prefixo_idioma.lower() in s["lang"].lower() for s in streams)
        en_emb = any(s["embutida"] and "english" in s["lang"].lower() for s in streams)
        if tem:
            com += 1
        if en_emb:
            emb += 1
        yield {"i": i, "total": len(eps), "rk": ep.rating_key,
               "ep": f"S{ep.temporada}E{ep.numero}", "titulo": ep.titulo,
               "com": com, "emb": emb, "tem": tem, "en_emb": en_emb}


def processar_serie(plex: Plex, serie_rk: str, idioma: str, score_min: int,
                    prefixo_idioma: str = "Portugu",
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
    for i, ep in enumerate(eps, 1):
        arquivo = ep.arquivo or plex.arquivo_do_episodio(ep.rating_key)
        streams = plex.streams_legenda(ep.rating_key)
        ja_tem = any(prefixo_idioma.lower() in s["lang"].lower() for s in streams)
        en_emb = any(s["embutida"] and "english" in s["lang"].lower() for s in streams)
        base = {"i": i, "total": len(eps), "rk": ep.rating_key, "en_emb": en_emb,
                "ep": f"S{ep.temporada}E{ep.numero}", "titulo": ep.titulo}
        atual = plex.afinidade_atual(ep.rating_key, arquivo, prefixo_idioma) if ja_tem else -1
        if ja_tem and not reavaliar:
            yield {**base, "estado": "ja_tinha"}
            continue
        if not ja_tem and so_existentes:
            yield {**base, "estado": "ignorado_vazio"}
            continue
        # Nunca substitui legenda que não veio de provedor (tradução/embutida
        # nossa, sidecar externo): reavaliar só reconsidera downloads de provedor.
        if ja_tem and reavaliar and not plex.tem_legenda_de_provedor(ep.rating_key, prefixo_idioma):
            yield {**base, "estado": "protegida"}
            continue

        cands = plex.buscar(ep.rating_key, idioma, arquivo)
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
