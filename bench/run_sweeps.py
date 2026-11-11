"""Barridos de rendimiento en modo replay.

Uso (desde la raíz del proyecto):
    python bench/run_sweeps.py                       # barrido completo, 5 repeticiones
    python bench/run_sweeps.py --repeats 2 --limit 100 --latency-ms 50   # prueba rápida

Qué hace:
  1. Copia data/radar.db a data/bench.db (las mediciones no ensucian el historial real).
  2. Levanta el servidor replay en un PROCESO aparte (si viviera en este proceso,
     sus hilos competirían por el GIL con los workers medidos).
  3. Mide:  T_sec (modo sequential)
            barrido de workers de E/S (modo concurrent):  1, 2, 4, 8, 16, 32, 64
            barrido de workers de emparejamiento (modo parallel): 1, 2, 4, ..., lógicos, 2x lógicos
  4. Guarda las filas crudas (CSV) y un resumen con mediana, mín, máx, speedup absoluto
     y relativo, eficiencia y Karp–Flatt (JSON), que el panel de métricas del sitio muestra.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from radar import config  # noqa: E402
from radar.pipeline import Pipeline  # noqa: E402

STAGES = ["fetch_parse", "retry_failed", "match", "aggregate"]


def hardware() -> dict:
    info = {"python": sys.version.split()[0], "os": platform.platform(),
            "logical_cpus": os.cpu_count(), "processor": platform.processor()}
    try:
        out = subprocess.run(["lscpu"], capture_output=True, text=True, check=True).stdout
        kv = dict(line.split(":", 1) for line in out.splitlines() if ":" in line)
        kv = {k.strip(): v.strip() for k, v in kv.items()}
        info["processor"] = kv.get("Model name", info["processor"])
        tpc = int(kv.get("Thread(s) per core", 1))
        cps = int(kv.get("Core(s) per socket", 0))
        sockets = int(kv.get("Socket(s)", 1))
        info["physical_cores"] = cps * sockets or None
        info["smt"] = tpc > 1
    except (OSError, subprocess.CalledProcessError, ValueError):
        pass
    try:
        mem = Path("/proc/meminfo").read_text().split("\n")[0].split()[1]
        info["memory_gb"] = round(int(mem) / 1024 / 1024, 1)
    except OSError:
        pass
    return info


def wait_port(host: str, port: int, timeout: float = 10) -> None:
    import socket
    end = time.time() + timeout
    while time.time() < end:
        with socket.socket() as s:
            if s.connect_ex((host, port)) == 0:
                return
        time.sleep(0.1)
    raise RuntimeError("el servidor replay no arrancó")


def one_run(overrides: dict, limit: int | None) -> dict:
    s = config.load(None, overrides)
    rep = Pipeline(s).run(limit=limit, publish=False)
    return {"total": rep.total_s, **{k: rep.stage_times.get(k, 0.0) for k in STAGES},
            "requests": rep.requests, "failures": rep.failures}


def summarize(values: list[float]) -> dict:
    v = sorted(values)
    return {"median": statistics.median(v), "min": v[0], "max": v[-1],
            "stdev": statistics.stdev(v) if len(v) > 1 else 0.0}


def karp_flatt(s_rel: float, p: int) -> float | None:
    if p <= 1 or s_rel <= 0:
        return None
    return (1 / s_rel - 1 / p) / (1 - 1 / p)


def curve(rows: list[dict], key: str, metric: str, t_sec: float) -> list[dict]:
    by_p: dict[int, list[float]] = {}
    for r in rows:
        by_p.setdefault(r[key], []).append(r[metric])
    t1 = statistics.median(by_p[min(by_p)])
    out = []
    for p in sorted(by_p):
        st = summarize(by_p[p])
        s_rel = t1 / st["median"]
        out.append({"p": p, **st, "S_abs": t_sec / st["median"], "S_rel": s_rel,
                    "E": s_rel / p, "karp_flatt": karp_flatt(s_rel, p)})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--latency-ms", type=float, default=150)
    ap.add_argument("--jitter-ms", type=float, default=0)
    ap.add_argument("--io-workers", type=int, nargs="+", default=[1, 2, 4, 8, 16, 32, 64])
    ap.add_argument("--match-workers", type=int, nargs="+", default=None)
    ap.add_argument("--match-fetch-workers", type=int, default=16)
    ap.add_argument("--executors", nargs="+", default=["process"], choices=["process", "thread"])
    a = ap.parse_args()

    logical = os.cpu_count() or 4
    if a.match_workers is None:
        a.match_workers = sorted({p for p in (1, 2, 4, 8, 16, 32) if p <= logical} | {logical, 2 * logical})

    base = config.load()
    src_db = base.path("db_path")
    bench_db = src_db.with_name("bench.db")
    shutil.copy(src_db, bench_db)
    common = {"source": "replay", "db_path": str(bench_db), "capture": False,
              "replay.latency_ms": a.latency_ms, "replay.jitter_ms": a.jitter_ms}

    r = base["replay"]
    server = subprocess.Popen([sys.executable, "-m", "radar", "replay-server",
                               "--latency-ms", str(a.latency_ms), "--jitter-ms", str(a.jitter_ms)],
                              cwd=ROOT, stdout=subprocess.DEVNULL)
    rows = []
    try:
        wait_port(r["host"], r["port"])

        def record(tag, mode, fw, mw, ex):
            for i in range(a.repeats):
                res = one_run({**common, "mode": mode, "workers.fetch": fw,
                               "workers.match": mw, "match_executor": ex}, a.limit)
                rows.append({"sweep": tag, "mode": mode, "fetch_workers": fw, "match_workers": mw,
                             "executor": ex, "latency_ms": a.latency_ms, "jitter_ms": a.jitter_ms,
                             "rep": i, **res})
                print(f"{tag:<10} {mode:<10} fw={fw:<3} mw={mw:<3} {ex:<7} rep={i} total={res['total']:.2f}s "
                      f"fetch={res['fetch_parse']:.2f}s match={res['match']:.2f}s", flush=True)

        record("baseline", "sequential", 1, 1, "process")
        for fw in a.io_workers:
            record("io", "concurrent", fw, 1, "process")
        for ex in a.executors:
            for mw in a.match_workers:
                record(f"cpu_{ex}", "parallel", a.match_fetch_workers, mw, ex)
    finally:
        server.terminate()
        server.wait()

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = ROOT / "bench" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / f"raw-{stamp}.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    base_rows = [x for x in rows if x["sweep"] == "baseline"]
    t_sec = {m: statistics.median(x[m] for x in base_rows) for m in ["total", "fetch_parse", "match"]}
    summary = {
        "generated_at": stamp, "hardware": hardware(), "repeats": a.repeats, "limit": a.limit,
        "latency_ms": a.latency_ms, "jitter_ms": a.jitter_ms,
        "T_sec": {m: summarize([x[m] for x in base_rows]) for m in ["total", *STAGES]},
        "io": {"fetch_parse": curve([x for x in rows if x["sweep"] == "io"], "fetch_workers", "fetch_parse", t_sec["fetch_parse"]),
               "total": curve([x for x in rows if x["sweep"] == "io"], "fetch_workers", "total", t_sec["total"])},
        "cpu": {ex: {"match": curve([x for x in rows if x["sweep"] == f"cpu_{ex}"], "match_workers", "match", t_sec["match"]),
                     "total": curve([x for x in rows if x["sweep"] == f"cpu_{ex}"], "match_workers", "total", t_sec["total"])}
                for ex in a.executors},
    }
    (out_dir / f"summary-{stamp}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    site_data = base.path("site_dir") / "data"
    site_data.mkdir(parents=True, exist_ok=True)
    (site_data / "bench.json").write_text(json.dumps(summary), encoding="utf-8")
    print(f"\nlisto: bench/results/raw-{stamp}.csv y summary-{stamp}.json (copiado a site/dist/data/bench.json)")


if __name__ == "__main__":
    main()
