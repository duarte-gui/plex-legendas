"""Exporta para arquivo as legendas que o Plex guarda dentro do banco.

O Plex não grava a legenda baixada ao lado do vídeo: ela vira um blob
comprimido em gzip dentro de `com.plexapp.plugins.library.blobs.db`, e o
stream correspondente fica com `url = 'blob://'`.

Consequência prática: a legenda funciona no Plex, mas nenhum outro player a
enxerga, e o Bazarr segue considerando o episódio pendente.

Este módulo lê os blobs e envia cada um ao Bazarr, que grava o arquivo ao lado
do vídeo com as permissões corretas e o registra no próprio banco. Assim não é
preciso dar acesso de escrita ao compartilhamento de mídia.
"""
from __future__ import annotations

import binascii
import gzip
import io
import json
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass

CONSULTA_STREAMS = (
    "SELECT ms.id || '~' || mp.file "
    "FROM media_streams ms JOIN media_parts mp ON mp.id = ms.media_part_id "
    "WHERE ms.stream_type_id = 3 AND ms.url = 'blob://' "
    "AND ms.language IN ({idiomas});"
)


@dataclass
class LegendaEmBlob:
    stream_id: str
    arquivo_video: str


class AcessoPlexDB:
    """Lê o banco do Plex. Suporta acesso local ou por SSH.

    O binário `Plex SQLite` acompanha a instalação do Plex e é usado para não
    depender de um sqlite3 externo. A abertura é sempre em modo somente-leitura,
    para não interferir com o servidor em execução.
    """

    def __init__(self, dir_bancos: str, sqlite_bin: str,
                 ssh_destino: str | None = None,
                 prefixo: list[str] | None = None) -> None:
        self.dir = dir_bancos.rstrip("/")
        self.sqlite = sqlite_bin
        self.ssh = ssh_destino
        self.prefixo = prefixo or []

    def _sql(self, banco: str, consulta: str) -> str:
        interno = [self.sqlite, f"file:{self.dir}/{banco}?mode=ro", consulta]
        cmd = self.prefixo + interno
        if self.ssh:
            cmd = ["ssh", "-o", "BatchMode=yes", self.ssh,
                   " ".join(_aspas(p) for p in cmd)]
        r = subprocess.run(cmd, capture_output=True, timeout=300)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.decode("utf-8", "replace")[:300])
        return r.stdout.decode("utf-8", "replace")

    def listar(self, idiomas: tuple[str, ...] = ("por", "pob")) -> list[LegendaEmBlob]:
        lista = ", ".join(f"'{i}'" for i in idiomas)
        saida = self._sql("com.plexapp.plugins.library.db",
                          CONSULTA_STREAMS.format(idiomas=lista))
        itens = []
        for linha in saida.splitlines():
            if "~" in linha:
                sid, _, arq = linha.partition("~")
                itens.append(LegendaEmBlob(sid.strip(), arq.strip()))
        return itens

    def conteudo(self, stream_id: str) -> bytes:
        """Devolve o SRT já descomprimido."""
        hexa = self._sql(
            "com.plexapp.plugins.library.blobs.db",
            f"SELECT hex(blob) FROM blobs "
            f"WHERE linked_id={int(stream_id)} AND linked_type='media_stream';"
        ).strip()
        if not hexa:
            raise LookupError(f"sem blob para o stream {stream_id}")
        return gzip.decompress(binascii.unhexlify(hexa))


def _aspas(p: str) -> str:
    return "'" + p.replace("'", "'\\''") + "'"


class Bazarr:
    def __init__(self, url: str, apikey: str) -> None:
        self.url = url.rstrip("/")
        self.key = apikey

    def _get(self, caminho: str):
        req = urllib.request.Request(self.url + caminho,
                                     headers={"X-API-KEY": self.key})
        with urllib.request.urlopen(req, timeout=120) as f:
            return json.load(f)

    def mapa_de_episodios(self) -> dict[str, tuple[int, int]]:
        """{nome-do-arquivo: (id_da_serie, id_do_episodio)}"""
        mapa: dict[str, tuple[int, int]] = {}
        for s in self._get("/api/series")["data"]:
            sid = s["sonarrSeriesId"]
            try:
                eps = self._get(f"/api/episodes?seriesid[]={sid}")["data"]
            except Exception:
                continue
            for e in eps:
                caminho = e.get("path") or ""
                if caminho:
                    mapa[caminho.rsplit("/", 1)[-1]] = (sid, e["sonarrEpisodeId"])
        return mapa

    def enviar(self, serie_id: int, episodio_id: int, srt: bytes,
               nome: str, idioma: str = "pt-BR") -> int:
        limite = "----plexsubs7f3a9c1e"
        campos = {"seriesid": str(serie_id), "episodeid": str(episodio_id),
                  "language": idioma, "forced": "false", "hi": "false"}
        buf = io.BytesIO()
        for k, v in campos.items():
            buf.write(f"--{limite}\r\n".encode())
            buf.write(f'Content-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        buf.write(f"--{limite}\r\n".encode())
        buf.write(f'Content-Disposition: form-data; name="file"; filename="{nome}"\r\n'
                  "Content-Type: application/octet-stream\r\n\r\n".encode())
        buf.write(srt)
        buf.write(f"\r\n--{limite}--\r\n".encode())

        req = urllib.request.Request(
            self.url + "/api/episodes/subtitles", data=buf.getvalue(),
            headers={"X-API-KEY": self.key,
                     "Content-Type": f"multipart/form-data; boundary={limite}"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=180) as f:
                return f.status
        except urllib.error.HTTPError as e:
            return e.code


def exportar(db: AcessoPlexDB, bazarr: Bazarr, idioma: str = "pt-BR",
             simular: bool = False):
    """Gera um dicionário por legenda processada."""
    mapa = bazarr.mapa_de_episodios()
    for item in db.listar():
        nome_video = item.arquivo_video.rsplit("/", 1)[-1]
        alvo = mapa.get(nome_video)
        if not alvo:
            yield {"arquivo": nome_video, "estado": "sem_episodio"}
            continue
        try:
            srt = db.conteudo(item.stream_id)
        except Exception as e:
            yield {"arquivo": nome_video, "estado": "erro", "detalhe": str(e)}
            continue

        if simular:
            yield {"arquivo": nome_video, "estado": "simulado", "bytes": len(srt)}
            continue

        base = nome_video.rsplit(".", 1)[0]
        # 200 e 204 são sucesso: o Bazarr responde 204 quando grava sem corpo.
        codigo = bazarr.enviar(alvo[0], alvo[1], srt, f"{base}.srt", idioma)
        yield {"arquivo": nome_video,
               "estado": "enviada" if codigo in (200, 204) else "erro",
               "http": codigo, "bytes": len(srt)}
