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
    afinidade: int = 0   # quão bem o release casa com o arquivo (0 = nada)


# Tokens de release usados para casar a legenda com o arquivo. A resolução
# pesa mais que os demais: uma legenda de 720p num arquivo 1080p costuma ter
# corte/timing diferente e dessincroniza.
_RESOLUCAO = re.compile(r"\b(2160p|1080p|720p|480p)\b", re.I)
_FONTE = re.compile(r"\b(web[\s._-]?dl|webrip|web|bluray|blu[\s._-]?ray|"
                    r"bdrip|brrip|hdtv|dvdrip|hdrip)\b", re.I)
_CODEC = re.compile(r"\b(x264|x265|h[\s._-]?264|h[\s._-]?265|avc|hevc|xvid)\b", re.I)
_GRUPO = re.compile(r"-([A-Za-z0-9]+)(?:\.\w{2,4})?$")


def _norm(s: str) -> str:
    return re.sub(r"[\s._-]+", "", (s or "").lower())


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
    fa, fc = tok(_FONTE, arquivo), tok(_FONTE, candidato)
    if fa and fc and fa.replace("webdl", "web") == fc.replace("webdl", "web"):
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

    # ---------- busca e download ----------

    def arquivo_do_episodio(self, ep_rk: str) -> str:
        """Nome do arquivo de vídeo do episódio (para casar o release)."""
        r = self._req(f"/library/metadata/{ep_rk}")
        if r is None:
            return ""
        for p in r.iter("Part"):
            return (p.get("file") or "").rsplit("/", 1)[-1]
        return ""

    def buscar(self, ep_rk: str, idioma: str, arquivo: str = "") -> list[Candidato]:
        """Candidatos ordenados por afinidade de release e, em empate, por score.

        Passe `arquivo` (nome do vídeo) para priorizar a legenda que casa com o
        release. Sem ele, cai no comportamento antigo (só por score).
        """
        r = self._req(f"/library/metadata/{ep_rk}/subtitles", language=idioma)
        if r is None:
            return []
        saida = []
        for s in r:
            if s.get("id") is None:
                continue
            try:
                score = int(s.get("score") or 0)
            except ValueError:
                score = 0
            titulo = s.get("title") or ""
            saida.append(Candidato(s.get("id"), score, titulo,
                                   s.get("providerTitle") or "",
                                   afinidade_release(arquivo, titulo) if arquivo else 0))
        # do maior para o menor: primeiro pela correspondência com o arquivo,
        # depois pela avaliação do Plex.
        return sorted(saida, key=lambda c: (c.afinidade, c.score), reverse=True)

    def aplicar(self, ep_rk: str, stream_id: str) -> None:
        """Baixa o candidato e o aplica ao episódio.

        O Plex responde 200 ou 204 — ambos são sucesso.
        """
        self._req(f"/library/metadata/{ep_rk}/subtitles", metodo="PUT",
                  key=f"/library/streams/{stream_id}")


def processar_serie(plex: Plex, serie_rk: str, idioma: str, score_min: int,
                    prefixo_idioma: str = "Portugu",
                    reavaliar: bool = False) -> Iterator[dict]:
    """Percorre a série e baixa o melhor candidato de cada episódio.

    Por padrão pula episódios que já têm legenda no idioma. Com `reavaliar=True`,
    reconsidera esses episódios: se existir um candidato com afinidade de release
    MAIOR que a da legenda atual, baixa e passa a usá-lo (corrige legendas mal
    casadas de execuções antigas). Nunca troca por algo de afinidade igual/menor.

    Gera um dicionário por episódio, para a interface acompanhar em tempo real.
    """
    eps = plex.episodios(serie_rk)
    for i, ep in enumerate(eps, 1):
        base = {"i": i, "total": len(eps),
                "ep": f"S{ep.temporada}E{ep.numero}", "titulo": ep.titulo}
        arquivo = ep.arquivo or plex.arquivo_do_episodio(ep.rating_key)

        ja_tem = plex.tem_idioma(ep.rating_key, prefixo_idioma)
        atual = plex.afinidade_atual(ep.rating_key, arquivo, prefixo_idioma) if ja_tem else -1
        if ja_tem and not reavaliar:
            yield {**base, "estado": "ja_tinha"}
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
