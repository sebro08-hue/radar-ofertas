"""Barridos de rendimiento en modo replay.

Uso (desde la raíz del proyecto):
    python bench/run_sweeps.py                                   # barrido completo, 5 repeticiones
    python bench/run_sweeps.py --repeats 1 --limit 50 --quick    # prueba rápida del script

Requisitos: una captura completa en data/replay/ y la base con el mismo catálogo en
data/bench.db (ambas salen del workflow «capturar-replay»; ver README).

Qué hace:
  1. Antes de CADA ejecución copia data/bench.db a data/bench-run.db. Así todas las
     ejecuciones parten del mismo estado (misma rotación, mismo catálogo) y las
     mediciones no ensucian el historial real.
  2. Levanta el servidor replay en un PROCESO aparte (si viviera en este proceso,
     sus hilos competirían por el GIL con los workers medidos). Lo reinicia al
     cambiar la latencia simulada.
  3. Mide, con todas las etapas y el pipeline completo:
       baseline   modo sequential                            -> T_sec
       io         modo concurrent, workers de E/S 1..64
       cpu_<ex>   modo parallel, workers de emparejamiento 1..2x lógicos,
                  con ProcessPoolExecutor y con ThreadPoolExecutor
       latency    modo parallel con varias latencias fijas y una con jitter
  4. Guarda las filas crudas (CSV, se escribe a medida que avanza) y un resumen con
     mediana, mín, máx, desviación, speedup absoluto y relativo, eficiencia,
     Karp–Flatt y la curva de Amdahl con la fracción serial estimada. El resumen se
     copia a data/bench.json, que el panel de métricas del sitio muestra.
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import json
import os
import platform
import shutil
import socket
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

STAGES = ["discover", "fetch_parse", "retry_failed", "igdb", "match", "aggregate"]
METRICS = ["total", *STAGES]


# ---------------------------------------------------------------- equipo de medición
def hardware() -> dict:
    info = {"python": sys.version.split()[0], "os": platform.platform(),
            "logical_cpus": os.cpu_count(), "processor": platform.processor()}
    try:
        out = subprocess.run(["lscpu"], capture_output=True, text=True, check=True,
                             env={**os.environ, "LC_ALL": "C"}).stdout    # nombres en inglés, sin importar el idioma
        kv = {k.strip(): v.strip() for k, v in
              (line.split(":", 1) for line in out.splitlines() if ":" in line)}
        info["processor"] = kv.get("Model name", info["processor"])
        cps, sockets = int(kv.get("Core(s) per socket", 0)), int(kv.get("Socket(s)", 1))
        info["physical_cores"] = cps * sockets or None
        info["smt"] = int(kv.get("Thread(s) per core", 1)) > 1
    except (OSError, subprocess.CalledProcessError, ValueError):
        pass
    # Procesadores híbridos de Intel: núcleos de rendimiento (con SMT) y de eficiencia (sin SMT).
    for kind, path in (("p_core_cpus", "/sys/devices/cpu_core/cpus"), ("e_core_cpus", "/sys/devices/cpu_atom/cpus")):
        with contextlib.suppress(OSError):
            info[kind] = Path(path).read_text().strip()
    with contextlib.suppress(OSError, ValueError, IndexError):
        kb = Path("/proc/meminfo").read_text().split("\n")[0].split()[1]
        info["memory_gb"] = round(int(kb) / 1024 / 1024, 1)
    return info


# ---------------------------------------------------------------- servidor replay
def wait_port(host: str, port: int, timeout: float = 15) -> None:
    end = time.time() + timeout
    while time.time() < end:
        with socket.socket() as s:
            if s.connect_ex((host, port)) == 0:
                return
        time.sleep(0.1)
    raise RuntimeError("el servidor replay no arrancó")


@contextlib.contextmanager
def replay_server(latency_ms: float, jitter_ms: float):
    r = config.load()["replay"]
    proc = subprocess.Popen([sys.executable, "-m", "radar", "replay-server",
                             "--latency-ms", str(latency_ms), "--jitter-ms", str(jitter_ms)],
                            cwd=ROOT, stdout=subprocess.DEVNULL)
    try:
        wait_port(r["host"], r["port"])
        yield
    finally:
        proc.terminate()
        proc.wait()


# ---------------------------------------------------------------- ejecución
class Bench:
    def __init__(self, a, source_db: Path, raw_path: Path):
        self.a, self.source_db, self.raw_path = a, source_db, raw_path
        self.run_db = source_db.with_name("bench-run.db")
        self.rows: list[dict] = []

    def fresh_db(self) -> None:
        """Copia nueva de la base. La base usa WAL: si quedara el -wal de la ejecución
        anterior junto a la copia, SQLite lo aplicaría y la ejecución no partiría del
        mismo estado."""
        for suffix in ("", "-wal", "-shm"):
            Path(f"{self.run_db}{suffix}").unlink(missing_ok=True)
        shutil.copy(self.source_db, self.run_db)

    def one_run(self, overrides: dict) -> dict:
        self.fresh_db()                                    # mismo punto de partida en cada ejecución
        s = config.load(None, {"source": "replay", "capture": False, "db_path": str(self.run_db),
                               "rotation": {}, **overrides})
        pipe = Pipeline(s)
        try:
            rep = pipe.run(with_discover=True, limit=self.a.limit, publish=False)
        finally:
            pipe.conn.close()
        return {"total": rep.total_s, **{k: rep.stage_times.get(k, 0.0) for k in STAGES},
                "requests": rep.requests, "failures": rep.failures}

    def record(self, tag: str, mode: str, fw: int, mw: int, ex: str, lat: float, jit: float) -> None:
        for i in range(self.a.repeats):
            res = self.one_run({"mode": mode, "workers.fetch": fw, "workers.match": mw,
                                "match_executor": ex, "replay.latency_ms": lat, "replay.jitter_ms": jit})
            row = {"sweep": tag, "mode": mode, "fetch_workers": fw, "match_workers": mw, "executor": ex,
                   "latency_ms": lat, "jitter_ms": jit, "rep": i, **res}
            self._append(row)
            print(f"{tag:<12} {mode:<10} fw={fw:<3} mw={mw:<3} {ex:<7} lat={lat:g}±{jit:g} rep={i} "
                  f"total={res['total']:.2f}s fetch={res['fetch_parse']:.2f}s match={res['match']:.2f}s "
                  f"fallidas={res['failures']}", flush=True)

    def _append(self, row: dict) -> None:
        new = not self.rows
        self.rows.append(row)
        with open(self.raw_path, "a", newline="", encoding="utf-8") as fh:   # se guarda a medida que avanza
            w = csv.DictWriter(fh, fieldnames=list(row))
            if new:
                w.writeheader()
            w.writerow(row)


# ---------------------------------------------------------------- métricas
def summarize(values: list[float]) -> dict:
    v = sorted(values)
    return {"median": statistics.median(v), "min": v[0], "max": v[-1],
            "stdev": statistics.stdev(v) if len(v) > 1 else 0.0}


def karp_flatt(s_rel: float, p: int) -> float | None:
    if p <= 1 or s_rel <= 0:
        return None
    return (1 / s_rel - 1 / p) / (1 - 1 / p)


def amdahl(f: float, p: int) -> float:
    return 1 / (f + (1 - f) / p)


def curve(rows: list[dict], key: str, metric: str, t_sec: float, max_p: int) -> dict:
    """Speedups de una etapa (o del total) a lo largo de un barrido de workers.
    La fracción serial f se estima con la mediana de Karp–Flatt para 1 < p <= max_p
    (más allá de los núcleos lógicos, e mezcla la fracción serial con la sobrecarga)."""
    by_p: dict[int, list[float]] = {}
    for r in rows:
        by_p.setdefault(r[key], []).append(r[metric])
    t1 = statistics.median(by_p[min(by_p)])
    points = []
    for p in sorted(by_p):
        st = summarize(by_p[p])
        s_rel = t1 / st["median"] if st["median"] else 0.0
        points.append({"p": p, **st, "S_abs": t_sec / st["median"] if st["median"] else 0.0,
                       "S_rel": s_rel, "E": s_rel / p, "karp_flatt": karp_flatt(s_rel, p)})
    es = [x["karp_flatt"] for x in points if x["karp_flatt"] is not None and x["p"] <= max_p]
    f = min(max(statistics.median(es), 0.0), 1.0) if es else None
    for x in points:
        x["amdahl"] = amdahl(f, x["p"]) if f is not None else None
    return {"T_par1": t1, "f": f, "points": points}


def stage_table(rows: list[dict]) -> list[dict]:
    """Mediana de cada etapa por configuración: compara los tres modos etapa por etapa."""
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        k = (r["sweep"], r["mode"], r["fetch_workers"], r["match_workers"], r["executor"],
             r["latency_ms"], r["jitter_ms"])
        groups.setdefault(k, []).append(r)
    out = []
    for (sweep, mode, fw, mw, ex, lat, jit), rs in groups.items():
        out.append({"sweep": sweep, "mode": mode, "fetch_workers": fw, "match_workers": mw,
                    "executor": ex, "latency_ms": lat, "jitter_ms": jit,
                    **{m: summarize([x[m] for x in rs]) for m in METRICS}})
    return out


def latency_table(rows: list[dict], a, logical: int) -> list[dict]:
    """Tiempo por etapa del modo parallel (mismos workers) al variar la latencia simulada:
    muestra cuál etapa es el cuello de botella y cómo cambia."""
    same = [x for x in rows if x["sweep"] in ("latency", "jitter")
            or (x["sweep"] == "cpu_process" and x["match_workers"] == logical)]
    out = []
    for key in sorted({(x["latency_ms"], x["jitter_ms"]) for x in same}):
        rs = [x for x in same if (x["latency_ms"], x["jitter_ms"]) == key]
        out.append({"latency_ms": key[0], "jitter_ms": key[1],
                    **{m: summarize([x[m] for x in rs]) for m in METRICS}})
    return out


def build_summary(a, rows: list[dict], logical: int) -> dict:
    base = [x for x in rows if x["sweep"] == "baseline"]
    t_sec = {m: statistics.median(x[m] for x in base) for m in METRICS}
    io = [x for x in rows if x["sweep"] == "io"]
    io_max = max(a.io_workers)              # E/S: los workers no están atados a los núcleos
    summary = {
        "hardware": hardware(), "repeats": a.repeats, "limit": a.limit,
        "latency_ms": a.latency_ms, "jitter_ms": 0,
        "T_sec": {m: summarize([x[m] for x in base]) for m in METRICS},
        "io": {m: curve(io, "fetch_workers", m, t_sec[m], io_max) for m in ["fetch_parse", "total"]},
        "cpu": {ex: {m: curve([x for x in rows if x["sweep"] == f"cpu_{ex}"], "match_workers", m, t_sec[m], logical)
                     for m in ["match", "total"]}
                for ex in a.executors},
        "stages": stage_table(rows),
        "latency": latency_table(rows, a, logical),
    }
    return summary


# ---------------------------------------------------------------- main
def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--latency-ms", type=float, default=150, help="latencia de los barridos de workers")
    ap.add_argument("--io-workers", type=int, nargs="+", default=[1, 2, 4, 8, 16, 32, 64])
    ap.add_argument("--match-workers", type=int, nargs="+", default=None)
    ap.add_argument("--match-fetch-workers", type=int, default=16)
    ap.add_argument("--executors", nargs="+", default=["process", "thread"], choices=["process", "thread"])
    ap.add_argument("--latencies", type=float, nargs="+", default=[0, 50, 150, 300])
    ap.add_argument("--jitter-ms", type=float, default=50, help="variante con variación aleatoria")
    ap.add_argument("--source-db", default=str(ROOT / "data" / "bench.db"))
    ap.add_argument("--quick", action="store_true", help="pocas configuraciones, para probar el script")
    a = ap.parse_args()
    logical = os.cpu_count() or 4
    if a.match_workers is None:
        a.match_workers = sorted({p for p in (1, 2, 4, 8, 16, 32) if p <= logical} | {logical, 2 * logical})
    if a.quick:
        a.io_workers, a.match_workers, a.latencies = [1, 8], [1, 2], [0, 150]
    return a, logical


def main():
    a, logical = parse_args()
    source_db = Path(a.source_db)
    if not source_db.is_file() or not (ROOT / "data" / "replay").is_dir():
        sys.exit("falta la captura: data/replay/ y data/bench.db (workflow «capturar-replay», ver README)")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = ROOT / "bench" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    b = Bench(a, source_db, out_dir / f"raw-{stamp}.csv")
    best_mw = logical

    with replay_server(a.latency_ms, 0):
        b.record("baseline", "sequential", 1, 1, "process", a.latency_ms, 0)
        for fw in a.io_workers:
            b.record("io", "concurrent", fw, 1, "process", a.latency_ms, 0)
        for ex in a.executors:
            for mw in a.match_workers:
                b.record(f"cpu_{ex}", "parallel", a.match_fetch_workers, mw, ex, a.latency_ms, 0)
    for lat in a.latencies:
        if lat == a.latency_ms:
            continue                        # ya medido en el barrido cpu_process con mw = lógicos
        with replay_server(lat, 0):
            b.record("latency", "parallel", a.match_fetch_workers, best_mw, "process", lat, 0)
    with replay_server(a.latency_ms, a.jitter_ms):
        b.record("jitter", "parallel", a.match_fetch_workers, best_mw, "process", a.latency_ms, a.jitter_ms)

    summary = {"generated_at": stamp, **build_summary(a, b.rows, logical), "jitter_variant_ms": a.jitter_ms}
    (out_dir / f"summary-{stamp}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (ROOT / "data" / "bench.json").write_text(json.dumps(summary), encoding="utf-8")
    for suffix in ("", "-wal", "-shm"):
        Path(f"{b.run_db}{suffix}").unlink(missing_ok=True)
    print(f"\nlisto: bench/results/raw-{stamp}.csv, summary-{stamp}.json y data/bench.json")


if __name__ == "__main__":
    main()
