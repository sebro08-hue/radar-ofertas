"""Línea de comandos:  python -m radar <comando> [opciones]

  init-db                         crea la base vacía
  discover                        etapa 0: llena el catálogo desde Steam Top Sellers
  run                             ejecuta el pipeline completo (etapas 1-5)
  replay-server                   sirve las respuestas capturadas con latencia simulada
  build-site                      regenera site/dist desde la base
  validate-matching               precisión y exhaustividad sobre data/validation_pairs.csv
  games list|add|set|remove|import   administrar el catálogo sin tocar código
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from . import config, db


def _settings(a) -> config.Settings:
    return config.load(a.config, {
        "mode": getattr(a, "mode", None),
        "source": getattr(a, "source", None),
        "workers.fetch": getattr(a, "fetch_workers", None),
        "workers.match": getattr(a, "match_workers", None),
        "match_executor": getattr(a, "match_executor", None),
        "replay.latency_ms": getattr(a, "latency_ms", None),
        "replay.jitter_ms": getattr(a, "jitter_ms", None),
        "db_path": getattr(a, "db", None),
    })


def cmd_init_db(a):
    s = _settings(a)
    db.connect(s.path("db_path"))
    print("base lista:", s.path("db_path"))


def cmd_discover(a):
    from .pipeline import Pipeline
    p = Pipeline(_settings(a))
    n, _ = p.discover()
    total = p.conn.execute("SELECT COUNT(*) FROM games WHERE active=1").fetchone()[0]
    print(f"{n} juegos leídos del listado; catálogo activo: {total}")


def cmd_run(a):
    from .pipeline import Pipeline
    s = _settings(a)
    server = None
    if s["source"] == "replay" and a.start_replay:
        from .replay import ReplayServer
        r = s["replay"]
        server = ReplayServer(s.path("replay_dir"), r["host"], r["port"], r["latency_ms"], r["jitter_ms"])
        server.start_background()
    try:
        rep = Pipeline(s).run(with_discover=a.discover, limit=a.limit, publish=not a.no_publish)
    finally:
        if server:
            server.stop()
    print(f"run {rep.run_id} [{rep.mode}] {rep.total_s:.2f}s  solicitudes={rep.requests} "
          f"fallidas={rep.failures} recuperadas={rep.recovered} ofertas={rep.matched_offers} banderas={rep.flags}")
    for k, v in rep.stage_times.items():
        print(f"  {k:<13} {v:8.3f}s")


def cmd_replay_server(a):
    from .replay import ReplayServer
    s = _settings(a)
    r = s["replay"]
    srv = ReplayServer(s.path("replay_dir"), r["host"], r["port"], r["latency_ms"], r["jitter_ms"])
    print(f"replay en http://{r['host']}:{r['port']}  latencia={r['latency_ms']}ms ±{r['jitter_ms']}ms  (Ctrl+C para salir)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.stop()


def cmd_build_site(a):
    from . import site
    s = _settings(a)
    print("sitio en", site.build(db.connect(s.path("db_path")), s))



def cmd_validate(a):
    from .matching import normalize, score
    s = _settings(a)
    pairs = list(csv.DictReader(open(a.file, encoding="utf-8")))
    print(f"{len(pairs)} pares")
    print(f"{'umbral':>7} {'precisión':>10} {'exhaust.':>9}  tp  fp  fn")
    for th in [x / 100 for x in range(70, 100, 2)] if a.sweep else [s["matching"]["threshold"]]:
        tp = fp = fn = 0
        for p in pairs:
            same = p["same"].strip() == "1"
            pred = score(normalize(p["a"]), normalize(p["b"])) >= th
            tp += pred and same
            fp += pred and not same
            fn += (not pred) and same
        prec = tp / (tp + fp) if tp + fp else 0
        rec = tp / (tp + fn) if tp + fn else 0
        print(f"{th:>7.2f} {prec:>10.3f} {rec:>9.3f} {tp:>3} {fp:>3} {fn:>3}")


def cmd_games(a):
    s = _settings(a)
    conn = db.connect(s.path("db_path"))
    if a.action == "list":
        for g in conn.execute("SELECT id, canonical_title, steam_appid, hltb_id, metacritic_slug, active FROM games"):
            print(dict(g))
    elif a.action == "add":
        gid = db.upsert_game(conn, {"canonical_title": a.title, "alt_titles": a.alt or [],
                                    "platforms": a.platform or [], "msrp": a.msrp,
                                    "release_date": a.release, "hard_case": int(a.hard)})
        conn.commit()
        print("id", gid)
    elif a.action == "set":
        conn.execute(f"UPDATE games SET {a.field}=? WHERE id=?", (a.value, a.id))
        conn.commit()
    elif a.action == "remove":
        conn.execute("UPDATE games SET active=0 WHERE id=?", (a.id,))
        conn.commit()
    elif a.action == "import":
        # CSV: canonical_title,alt_titles(separados por |),platforms(|),release_date,msrp,hltb_id,metacritic_slug,hard_case
        n = 0
        for row in csv.DictReader(open(a.file, encoding="utf-8")):
            db.upsert_game(conn, {
                "canonical_title": row["canonical_title"],
                "alt_titles": [x for x in row.get("alt_titles", "").split("|") if x],
                "platforms": [x for x in row.get("platforms", "").split("|") if x],
                "release_date": row.get("release_date") or None,
                "msrp": float(row["msrp"]) if row.get("msrp") else None,
                "hltb_id": int(row["hltb_id"]) if row.get("hltb_id") else None,
                "metacritic_slug": row.get("metacritic_slug") or None,
                "hard_case": int(row.get("hard_case") or 0)})
            n += 1
        conn.commit()
        print(n, "juegos importados")
    elif a.action == "export-ids":
        n = db.export_manual_ids(conn, Path(a.file), a.limit)
        print(f"{n} juegos en {a.file}; peguen en hltb_id la dirección howlongtobeat.com/game/<id> o el número")
    elif a.action == "import-ids":
        print(db.apply_manual_ids(conn, Path(a.file)), "juegos actualizados")


SETTABLE = ["canonical_title", "release_date", "msrp", "hltb_id", "metacritic_slug", "image_url",
            "kind", "hard_case", "active", "alt_titles", "platforms"]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="radar")
    ap.add_argument("--config", default=None)
    ap.add_argument("--db", default=None, help="ruta alternativa de la base")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db").set_defaults(fn=cmd_init_db)

    p = sub.add_parser("discover")
    p.add_argument("--mode", choices=["sequential", "concurrent", "parallel"])
    p.add_argument("--source", choices=["live", "replay"])
    p.set_defaults(fn=cmd_discover)

    p = sub.add_parser("run")
    p.add_argument("--mode", choices=["sequential", "concurrent", "parallel"])
    p.add_argument("--source", choices=["live", "replay"])
    p.add_argument("--fetch-workers", type=int)
    p.add_argument("--match-workers", type=int)
    p.add_argument("--match-executor", choices=["process", "thread"])
    p.add_argument("--latency-ms", type=float)
    p.add_argument("--jitter-ms", type=float)
    p.add_argument("--limit", type=int, help="procesar solo los primeros N juegos")
    p.add_argument("--discover", action="store_true", help="incluir la etapa 0")
    p.add_argument("--start-replay", action="store_true", help="levantar el servidor replay dentro del proceso")
    p.add_argument("--no-publish", action="store_true")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("replay-server")
    p.add_argument("--latency-ms", type=float)
    p.add_argument("--jitter-ms", type=float)
    p.set_defaults(fn=cmd_replay_server)

    sub.add_parser("build-site").set_defaults(fn=cmd_build_site)

    p = sub.add_parser("validate-matching")
    p.add_argument("--file", default=str(config.ROOT / "data" / "validation_pairs.csv"))
    p.add_argument("--sweep", action="store_true", help="probar varios umbrales")
    p.set_defaults(fn=cmd_validate)

    p = sub.add_parser("games")
    gsub = p.add_subparsers(dest="action", required=True)
    gsub.add_parser("list")
    g = gsub.add_parser("add")
    g.add_argument("title")
    g.add_argument("--alt", nargs="*")
    g.add_argument("--platform", nargs="*")
    g.add_argument("--msrp", type=float)
    g.add_argument("--release")
    g.add_argument("--hard", action="store_true", help="marcar como caso difícil")
    g = gsub.add_parser("set")
    g.add_argument("id", type=int)
    g.add_argument("field", choices=SETTABLE)
    g.add_argument("value")
    g = gsub.add_parser("remove")
    g.add_argument("id", type=int)
    g = gsub.add_parser("import")
    g.add_argument("file")
    g = gsub.add_parser("export-ids", help="hoja CSV para cargar IDs de HLTB/Metacritic/Xbox a mano")
    g.add_argument("file", nargs="?", default=str(config.ROOT / "data" / "ids_manual.csv"))
    g.add_argument("--limit", type=int)
    g = gsub.add_parser("import-ids", help="aplica la hoja de IDs (también se aplica sola en cada `run`)")
    g.add_argument("file", nargs="?", default=str(config.ROOT / "data" / "ids_manual.csv"))
    p.set_defaults(fn=cmd_games)

    a = ap.parse_args(argv)
    if a.cmd == "games" and a.action == "set" and a.field in ("alt_titles", "platforms"):
        a.value = json.dumps([x for x in a.value.split("|") if x], ensure_ascii=False)
    a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
