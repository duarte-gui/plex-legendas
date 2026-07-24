"""Traduz legendas SRT de um idioma para outro via Ollama (Qwen), em blocos.

O texto de cada legenda é traduzido em blocos numerados; os TEMPOS são
preservados intactos (o sincronismo vem da legenda-fonte). A saída é um SRT no
mesmo formato, pronto para subir ao Bazarr.

A fonte natural é a legenda INGLESA que o Plex baixa (blob) — muito mais
disponível que a pt-BR — ou a embutida no arquivo. Modelo padrão: o Qwen 35B na
RTX 5090 (mesma receita que traduziu o Gumball). O endereço do Ollama é
configurável (`OLLAMA_URL`) porque a GPU só é alcançável via túnel/jump.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MODELO = os.environ.get("TRADUZ_MODELO", "hauhau-qwen3-35b")

# marcas de legenda para surdos (HI) e rótulos de locutor, que atrapalham a
# leitura; removidas de forma conservadora.
_HI = re.compile(r"\[[^\]]*\]|\([A-Z][^\)]*\)|^[A-ZÀ-Ú ]{2,}:")


class Cue:
    __slots__ = ("idx", "tempo", "texto")

    def __init__(self, idx: str, tempo: str, texto: str) -> None:
        self.idx = idx
        self.tempo = tempo
        self.texto = texto


def parse_srt(texto: str) -> list[Cue]:
    """Lê um SRT em cues (índice, tempo, texto numa linha)."""
    cues: list[Cue] = []
    texto = texto.replace("﻿", "")
    for bloco in re.split(r"\r?\n\r?\n+", texto.strip()):
        linhas = bloco.splitlines()
        # acha a linha do tempo (contém -->); antes dela vai o índice
        t = next((i for i, l in enumerate(linhas) if "-->" in l), None)
        if t is None or t + 1 >= len(linhas):
            continue
        idx = linhas[t - 1].strip() if t >= 1 else str(len(cues) + 1)
        corpo = " ".join(l.strip() for l in linhas[t + 1:] if l.strip())
        cues.append(Cue(idx, linhas[t].strip(), corpo))
    return cues


def limpar_hi(texto: str) -> str:
    return _HI.sub("", texto).replace("  ", " ").strip(" -")


def _traduzir_bloco(textos: list[str], idioma_nome: str, ollama_url: str,
                    modelo: str) -> list[str]:
    """Traduz uma lista de falas; devolve a mesma quantidade (fallback = original)."""
    numeradas = "\n".join(f"{i + 1}\t{t}" for i, t in enumerate(textos))
    instr = (
        f"Traduza as falas de legenda do INGLÊS para {idioma_nome}. "
        "Mantenha a numeração e a ordem exatas; uma linha por número, no formato "
        "'N<TAB>tradução'. Linguagem natural e coloquial, preservando piadas, "
        "gírias e nomes próprios. Não explique nada, não junte falas: responda "
        "SÓ as linhas numeradas traduzidas.\n\n" + numeradas)
    payload = {
        "model": modelo,
        "messages": [{"role": "user", "content": instr}],
        "stream": False,
        "think": False,   # crucial: desliga o "pensamento" do Qwen (senão fica 10x mais lento)
        "keep_alive": "30m",   # mantém o modelo (21 GB) na VRAM entre episódios do lote
        "options": {"temperature": 0.3},
    }
    req = urllib.request.Request(
        f"{ollama_url.rstrip('/')}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as f:
        resp = json.load(f).get("message", {}).get("content", "")
    resp = re.sub(r"<think>.*?</think>", "", resp, flags=re.S)
    out: dict[int, str] = {}
    for ln in resp.splitlines():
        m = re.match(r"\s*(\d+)[\t.:)\-\s]+(.*)", ln)
        if m and m.group(2).strip():
            out[int(m.group(1))] = m.group(2).strip()
    return [out.get(i + 1, textos[i]) for i in range(len(textos))]


def traduzir_srt(srt: bytes, idioma_nome: str = "Português do Brasil",
                 bloco: int = 25, limpar: bool = True,
                 ollama_url: str = OLLAMA_URL, modelo: str = MODELO):
    """Traduz um SRT (bytes) e devolve outro SRT (bytes), tempos preservados.

    Gera, além do resultado, eventos de progresso via `yield` — ou use
    `traduzir_srt_completo` para só o resultado.
    """
    cues = parse_srt(srt.decode("utf-8", "replace"))
    textos = [limpar_hi(c.texto) if limpar else c.texto for c in cues]
    traduzidos: list[str] = []
    for i in range(0, len(textos), bloco):
        pedaco = textos[i:i + bloco]
        traduzidos += _traduzir_bloco(pedaco, idioma_nome, ollama_url, modelo)
        yield {"feitos": min(i + bloco, len(textos)), "total": len(textos)}
    linhas = []
    for c, t in zip(cues, traduzidos):
        linhas.append(f"{c.idx}\n{c.tempo}\n{t or c.texto}\n")
    yield {"srt": ("\n".join(linhas)).encode("utf-8"),
           "cues": len(cues), "total": len(textos)}


def traduzir_srt_completo(srt: bytes, **kw) -> bytes:
    """Versão simples: traduz e devolve só o SRT (bytes)."""
    res = b""
    for evt in traduzir_srt(srt, **kw):
        if "srt" in evt:
            res = evt["srt"]
    return res


def traduzir_episodio(plex, ep_rk: str, idioma_alvo: str = "pt-BR",
                      idioma_nome: str = "Português do Brasil",
                      tokens_serie=None, **kw) -> dict:
    """Garante uma legenda inglesa no episódio, baixa o texto, traduz e devolve
    o SRT no idioma-alvo. NÃO sobe nada — o chamador decide (Bazarr/Plex).

    Não faz busca de provedor do Bazarr (sem cota): a inglesa vem do download do
    Plex. Devolve {estado, srt?, bytes?}. Estados: ja_tem, sem_ingles,
    falha_download, traduzida.
    """
    from .plex import casa_idioma
    streams = plex.streams_legenda(ep_rk)
    if any(casa_idioma(s["tag"], s["code"], s["lang"], idioma_alvo) for s in streams):
        return {"estado": "ja_tem"}

    en_id = plex.id_legenda_externa(ep_rk, "en")
    if not en_id:                       # baixa a melhor inglesa pelo Plex
        arquivo = plex.arquivo_do_episodio(ep_rk)
        cands = plex.buscar(ep_rk, "en", arquivo, tokens_serie=tokens_serie)
        if not cands:
            return {"estado": "sem_ingles"}
        plex.aplicar(ep_rk, cands[0].stream_id)
        en_id = plex.id_legenda_externa(ep_rk, "en")
        if not en_id:
            return {"estado": "falha_download"}

    srt_en = plex.conteudo_stream(en_id)
    srt_pt = traduzir_srt_completo(srt_en, idioma_nome=idioma_nome, **kw)
    return {"estado": "traduzida", "srt": srt_pt, "bytes": len(srt_pt),
            "cues": srt_pt.count(b" --> ")}
