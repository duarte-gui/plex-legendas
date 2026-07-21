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


@dataclass
class Candidato:
    stream_id: str
    score: int
    titulo: str
    provedor: str


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
        return [Episodio(v.get("ratingKey"), v.get("parentIndex") or "?",
                         v.get("index") or "?", v.get("title") or "")
                for v in r.iter("Video")]

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

    # ---------- busca e download ----------

    def buscar(self, ep_rk: str, idioma: str) -> list[Candidato]:
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
            saida.append(Candidato(s.get("id"), score,
                                   s.get("title") or "",
                                   s.get("providerTitle") or ""))
        return sorted(saida, key=lambda c: c.score, reverse=True)

    def aplicar(self, ep_rk: str, stream_id: str) -> None:
        """Baixa o candidato e o aplica ao episódio.

        O Plex responde 200 ou 204 — ambos são sucesso.
        """
        self._req(f"/library/metadata/{ep_rk}/subtitles", metodo="PUT",
                  key=f"/library/streams/{stream_id}")


def processar_serie(plex: Plex, serie_rk: str, idioma: str, score_min: int,
                    prefixo_idioma: str = "Portugu") -> Iterator[dict]:
    """Percorre a série e baixa o melhor candidato de cada episódio faltante.

    Gera um dicionário por episódio, para a interface acompanhar em tempo real.
    """
    eps = plex.episodios(serie_rk)
    for i, ep in enumerate(eps, 1):
        base = {"i": i, "total": len(eps),
                "ep": f"S{ep.temporada}E{ep.numero}", "titulo": ep.titulo}

        if plex.tem_idioma(ep.rating_key, prefixo_idioma):
            yield {**base, "estado": "ja_tinha"}
            continue

        cands = plex.buscar(ep.rating_key, idioma)
        if not cands:
            yield {**base, "estado": "sem_candidato"}
            continue

        melhor = cands[0]
        if melhor.score < score_min:
            yield {**base, "estado": "score_baixo", "score": melhor.score}
            continue

        try:
            plex.aplicar(ep.rating_key, melhor.stream_id)
        except PlexError as e:
            yield {**base, "estado": "erro", "detalhe": str(e)}
            continue

        yield {**base, "estado": "baixada", "score": melhor.score,
               "release": melhor.titulo, "provedor": melhor.provedor}
