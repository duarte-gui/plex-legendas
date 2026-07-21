"""Ponto de entrada: `python -m plexsubs [servir|processar|exportar]`."""
import argparse
import sys

from .config import Config, ConfigError, carregar_dotenv


def main() -> int:
    carregar_dotenv()
    p = argparse.ArgumentParser(prog="plexsubs", description="Legendas via API do Plex")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("servir", help="sobe a interface web (padrão)")

    pr = sub.add_parser("processar", help="baixa legendas de uma série")
    pr.add_argument("serie", help="ratingKey da série no Plex")
    pr.add_argument("--score", type=int, help="score mínimo aceito")

    ex = sub.add_parser("exportar", help="exporta os blobs do Plex via Bazarr")
    ex.add_argument("--simular", action="store_true", help="não envia nada")

    sub.add_parser("series", help="lista as séries e seus ratingKeys")

    a = p.parse_args()
    try:
        cfg = Config()
    except ConfigError as e:
        print(f"erro: {e}", file=sys.stderr)
        return 2

    if a.cmd in (None, "servir"):
        from .web import servir
        servir(cfg)
        return 0

    from .plex import Plex
    plex = Plex(cfg.plex_url, cfg.plex_token)

    if a.cmd == "series":
        for s in plex.series():
            print(f"{s.rating_key:<8} {s.titulo}   [{s.biblioteca}]")
        return 0

    if a.cmd == "processar":
        from .plex import processar_serie
        tot = {}
        for e in processar_serie(plex, a.serie, cfg.idioma, a.score or cfg.score_min):
            tot[e["estado"]] = tot.get(e["estado"], 0) + 1
            extra = f"  score={e['score']}" if "score" in e else ""
            print(f"[{e['i']:>3}/{e['total']}] {e['ep']:<8} {e['estado']}{extra}")
        print("\nresumo:", ", ".join(f"{k}={v}" for k, v in sorted(tot.items())))
        return 0

    if a.cmd == "exportar":
        import os
        from .exportar import AcessoPlexDB, Bazarr, exportar
        if not cfg.tem_bazarr:
            print("erro: defina BAZARR_URL e BAZARR_APIKEY", file=sys.stderr)
            return 2
        db = AcessoPlexDB(
            dir_bancos=os.environ["PLEX_DB_DIR"],
            sqlite_bin=os.environ.get("PLEX_SQLITE", "/usr/lib/plexmediaserver/Plex SQLite"),
            ssh_destino=os.environ.get("PLEX_SSH") or None,
            prefixo=(os.environ.get("PLEX_CMD_PREFIXO") or "").split() or None,
        )
        tot = {}
        for e in exportar(db, Bazarr(cfg.bazarr_url, cfg.bazarr_key), cfg.idioma, a.simular):
            tot[e["estado"]] = tot.get(e["estado"], 0) + 1
            print(f"  {e['estado']:<14} {e['arquivo'][:64]}")
        print("\nresumo:", ", ".join(f"{k}={v}" for k, v in sorted(tot.items())))
        return 0

    p.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
