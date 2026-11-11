"""Benchmark: estrategias para agregar contadores compartidos desde varios hilos
(por ejemplo, ofertas por plataforma durante una ejecución).

Uso:
    python bench/contention.py --items 2000000 --threads 1 2 4 8 --repeats 5

Cuatro variantes sobre los mismos datos:
  unsafe   sin sincronización                -> condición de carrera (pierde conteos)
  lock     un lock global en cada incremento -> correcto, pero los hilos compiten por el lock
  local    estado local por hilo (threading.local) combinado al final -> correcto, casi sin contención
  queue    colección concurrente (queue.SimpleQueue) que un hilo consolida al final

Por qué la variante `unsafe` pierde conteos: `counts[k] = counts[k] + 1` no es atómico.
Son tres pasos (leer, sumar, escribir). Si el intérprete cambia de hilo entre la lectura
y la escritura, otro hilo escribe en medio y una de las dos sumas se pierde. Para que el
cambio de hilo sea frecuente se reduce `sys.setswitchinterval` y se hace la operación en
pasos separados.
"""
from __future__ import annotations

import queue
import random
import sys
import threading
import time
from collections import Counter

PLATFORMS = ["PC", "PlayStation 5", "PlayStation 4", "Nintendo Switch", "Xbox Series X|S", "Xbox One"]


def make_items(n: int, seed: int = 7) -> list[str]:
    rng = random.Random(seed)
    return [rng.choice(PLATFORMS) for _ in range(n)]


def _split(items: list[str], k: int) -> list[list[str]]:
    return [items[i::k] for i in range(k)]


def _run_threads(target, parts) -> float:
    threads = [threading.Thread(target=target, args=(p,)) for p in parts]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return time.perf_counter() - t0


def _bump(v: int) -> int:
    # Una llamada a función entre leer y escribir: en CPython 3.10+ el cambio de hilo
    # solo ocurre en ciertos puntos (llamadas, saltos hacia atrás), y este es uno.
    # Representa el "procesar la oferta" que haría el código real antes de sumar.
    return v + 1


def unsafe(items, n_threads):
    counts = {p: 0 for p in PLATFORMS}

    def work(part):
        for p in part:
            v = counts[p]          # leer
            v = _bump(v)           # sumar  <- aquí puede cambiar de hilo
            counts[p] = v          # escribir: pisa lo que otro hilo escribió entre medio

    elapsed = _run_threads(work, _split(items, n_threads))
    return counts, elapsed


def with_lock(items, n_threads):
    counts = {p: 0 for p in PLATFORMS}
    lock = threading.Lock()

    def work(part):
        for p in part:
            with lock:
                v = counts[p]
                v = _bump(v)
                counts[p] = v

    elapsed = _run_threads(work, _split(items, n_threads))
    return counts, elapsed


def thread_local(items, n_threads):
    total = Counter()
    merge_lock = threading.Lock()
    local = threading.local()

    def work(part):
        local.counts = Counter()          # cada hilo tiene el suyo: no hay nada que proteger
        for p in part:
            v = local.counts[p]
            v = _bump(v)
            local.counts[p] = v
        with merge_lock:                  # un solo acceso sincronizado por hilo
            total.update(local.counts)

    elapsed = _run_threads(work, _split(items, n_threads))   # primero correr, luego leer
    return dict(total), elapsed


def concurrent_collection(items, n_threads):
    q: queue.SimpleQueue = queue.SimpleQueue()

    def work(part):
        for p in part:
            q.put(p)

    elapsed = _run_threads(work, _split(items, n_threads))
    t0 = time.perf_counter()
    total = Counter()
    while not q.empty():
        total[q.get_nowait()] += 1
    return dict(total), elapsed + (time.perf_counter() - t0)


VARIANTS = {"unsafe": unsafe, "lock": with_lock, "local": thread_local, "queue": concurrent_collection}


def run(n_items: int = 2_000_000, threads=(1, 2, 4, 8), repeats: int = 5,
        switch_interval: float = 1e-6) -> list[dict]:
    items = make_items(n_items)
    expected = Counter(items)
    old = sys.getswitchinterval()
    sys.setswitchinterval(switch_interval)
    rows = []
    try:
        for name, fn in VARIANTS.items():
            for k in threads:
                times, lost = [], []
                for _ in range(repeats):
                    counts, elapsed = fn(items, k)
                    times.append(elapsed)
                    lost.append(n_items - sum(counts.values()))
                times.sort()
                rows.append({"variant": name, "threads": k, "median_s": times[len(times) // 2],
                             "min_s": times[0], "max_s": times[-1],
                             "lost_updates_max": max(lost),
                             "correct": all(x == 0 for x in lost)})
    finally:
        sys.setswitchinterval(old)
    assert sum(expected.values()) == n_items
    return rows


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--items", type=int, default=2_000_000)
    ap.add_argument("--threads", type=int, nargs="+", default=[1, 2, 4, 8])
    ap.add_argument("--repeats", type=int, default=5)
    a = ap.parse_args()
    rows = run(a.items, tuple(a.threads), a.repeats)
    print(f"{'variante':<8} {'hilos':>5} {'mediana':>9} {'min':>8} {'max':>8} {'perdidos':>9} correcto")
    for r in rows:
        print(f"{r['variant']:<8} {r['threads']:>5} {r['median_s']:>8.3f}s {r['min_s']:>7.3f}s "
              f"{r['max_s']:>7.3f}s {r['lost_updates_max']:>9} {r['correct']}")


if __name__ == "__main__":
    main()
