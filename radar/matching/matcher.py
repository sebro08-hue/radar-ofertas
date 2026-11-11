"""Emparejamiento: decide qué oferta de cada tienda corresponde a cada juego.

`match_game` es una función de nivel de módulo con argumentos simples (tuplas y
listas), para que se pueda enviar a un ProcessPoolExecutor (tiene que ser picklable).
"""
from __future__ import annotations

from dataclasses import dataclass

from .normalize import flags, normalize
from .similarity import score


# Catálogo completo usado como candidatos "distractores". Se carga una sola vez por
# proceso/hilo worker (initializer del executor) en vez de mandarlo con cada tarea.
_CATALOG: list[str] = []


def set_catalog(titles: list[str]) -> None:
    global _CATALOG
    _CATALOG = [normalize(t) for t in titles]


@dataclass
class MatchTask:
    game_id: int
    names: list[str]                       # título canónico + alternativos
    candidates: list[tuple[str, str, int, str]]  # (tienda, título, índice de la oferta, plataforma)
    use_catalog: bool                      # comparar además contra todo el catálogo (carga de CPU)
    threshold: float
    margin: float


@dataclass
class MatchResult:
    game_id: int
    accepted: list[tuple[int, float]]       # (índice de oferta, puntaje)
    flags: list[tuple[str, str, float, str]] # (tienda, candidato, puntaje, motivo)
    best_distractor: float                  # máximo contra el catálogo (diagnóstico)


def match_game(task: MatchTask) -> MatchResult:
    names = [normalize(n) for n in task.names if n]
    # Mejor oferta por (tienda, plataforma): un minorista puede vender PS5 y Switch.
    best_per_store: dict[tuple[str, str], tuple[int, float, str]] = {}
    out_flags: list[tuple[str, str, float, str]] = []

    for store, title, idx, platform in task.candidates:
        cand = normalize(title)
        s = max(score(n, cand) for n in names)
        f = flags(title) - set().union(*(flags(n) for n in task.names))
        if "dlc_or_bundle" in f and s >= task.threshold - task.margin:
            out_flags.append((store, title, s, "dlc_or_bundle"))
            continue
        if abs(s - task.threshold) < task.margin:
            out_flags.append((store, title, s, "near_threshold"))
        elif "edition" in f and s >= task.threshold:
            out_flags.append((store, title, s, "edition"))
        key = (store, platform)
        if s >= task.threshold and s > best_per_store.get(key, (-1, -1.0, ""))[1]:
            best_per_store[key] = (idx, s, title)

    stores_seen = {c[0] for c in task.candidates}
    for store in stores_seen - {k[0] for k in best_per_store}:
        out_flags.append((store, "", 0.0, "unmatched"))

    # Carga extra opcional: comparar el juego contra todo el catálogo.
    best_d = 0.0
    if task.use_catalog:
        own = set(names)
        for nd in _CATALOG:
            if nd in own:
                continue
            for n in names:
                v = score(n, nd)
                if v > best_d:
                    best_d = v

    return MatchResult(task.game_id,
                       [(idx, s) for idx, s, _ in best_per_store.values()],
                       out_flags, best_d)
