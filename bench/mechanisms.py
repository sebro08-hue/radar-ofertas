"""Comparación de mecanismos para la etapa de descarga: hilos (ThreadPoolExecutor) frente a
asyncio (un solo hilo con bucle de eventos), en modo replay.

Uso (desde la raíz del proyecto, con la captura en data/replay/ y data/bench.db):
    python bench/mechanisms.py                     # 5 repeticiones, 150 ms
    python bench/mechanisms.py --repeats 1 --workers 8 64

Mide el pipeline completo en modo concurrent con cada mecanismo y cada cantidad de
descargas simultáneas, y guarda el tiempo de la etapa fetch_parse y el tiempo de CPU del
proceso (el servidor replay corre en otro proceso y no cuenta). Para que el límite por
dominio no tape la diferencia con muchas descargas a la vez, se sube a 1024 durante la
medición. El emparejamiento no compara contra todo el catálogo: aquí solo importa la descarga.
El resumen queda en data/mechanisms.json, que muestra el panel de métricas.
"""
from __future__ import annotations

import argparse
import csv
import json
import resource
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))

from radar import config  # noqa: E402
from radar.pipeline import Pipeline  # noqa: E402
from run_sweeps import hardware, replay_server, summarize  # noqa: E402

MECHANISMS = ("thread", "asyncio")


def cpu_seconds() -> float:
    ru = resource.getrusage(resource.RUSAGE_SELF)
    return ru.ru_utime + ru.ru_stime


def fresh_db(source: Path, run_db: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        Path(f"{run_db}{suffix}").unlink(missing_ok=True)
    run_db.write_bytes(source.read_bytes())


def one_run(source_db: Path, run_db: Path, mechanism: str, workers: int, latency_ms: float) -> dict:
    fresh_db(source_db, run_db)
    base = config.load()
    limits = {**base["per_domain_limit"], base["replay"]["host"]: 1024}
    s = config.load(None, {"source": "replay", "capture": False, "db_path": str(run_db), "rotation": {},
                           "mode": "concurrent", "fetch_mechanism": mechanism, "workers.fetch": workers,
                           "per_domain_limit": limits, "matching.compare_against_catalog": False,
                           "replay.latency_ms": latency_ms})
    pipe = Pipeline(s)
    cpu0, t0 = cpu_seconds(), time.perf_counter()
    try:
        rep = pipe.run(with_discover=True, publish=False)
    finally:
        pipe.conn.close()
    return {"total": time.perf_counter() - t0, "fetch_parse": rep.stage_times.get("fetch_parse", 0.0),
            "cpu_s": cpu_seconds() - cpu0, "requests": rep.requests, "failures": rep.failures,
            "offers": rep.matched_offers}


def summary(rows: list[dict], a) -> dict:
    out = {"generated_at": datetime.now().strftime("%Y%m%d-%H%M%S"), "hardware": hardware(),
           "repeats": a.repeats, "latency_ms": a.latency_ms, "mechanisms": {}}
    for mech in MECHANISMS:
        pts = []
        for p in sorted({r["workers"] for r in rows}):
            rs = [r for r in rows if r["mechanism"] == mech and r["workers"] == p]
            if rs:
                pts.append({"p": p, "fetch_parse": summarize([r["fetch_parse"] for r in rs]),
                            "cpu_s": summarize([r["cpu_s"] for r in rs]),
                            "offers": statistics.median(r["offers"] for r in rs)})
        out["mechanisms"][mech] = pts
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--workers", type=int, nargs="+", default=[8, 16, 32, 64, 128, 256])
    ap.add_argument("--latency-ms", type=float, default=150)
    a = ap.parse_args()
    source_db, run_db = ROOT / "data" / "bench.db", ROOT / "data" / "bench-run.db"
    if not source_db.is_file() or not (ROOT / "data" / "replay").is_dir():
        sys.exit("falta la captura: data/replay/ y data/bench.db (workflow «capturar-replay», ver README)")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = ROOT / "bench" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = out_dir / f"mechanisms-{stamp}.csv"
    rows: list[dict] = []
    with replay_server(a.latency_ms, 0):
        for p in a.workers:
            for mech in MECHANISMS:
                for i in range(a.repeats):
                    r = {"mechanism": mech, "workers": p, "rep": i,
                         **one_run(source_db, run_db, mech, p, a.latency_ms)}
                    rows.append(r)
                    print(f"{mech:<8} p={p:<4} rep={i} fetch={r['fetch_parse']:.2f}s cpu={r['cpu_s']:.2f}s "
                          f"ofertas={r['offers']}", flush=True)
    with open(raw, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    result = summary(rows, a)
    (out_dir / f"mechanisms-summary-{stamp}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (ROOT / "data" / "mechanisms.json").write_text(json.dumps(result), encoding="utf-8")
    for suffix in ("", "-wal", "-shm"):
        Path(f"{run_db}{suffix}").unlink(missing_ok=True)
    print(f"\nlisto: {raw.relative_to(ROOT)} y data/mechanisms.json")


if __name__ == "__main__":
    main()
