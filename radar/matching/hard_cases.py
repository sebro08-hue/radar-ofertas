"""Casos difíciles del catálogo para el emparejamiento.

Un juego es difícil de emparejar cuando su título se confunde fácilmente con el de
otro producto: remasterizaciones, ediciones, números romanos, secuelas o juegos de
la misma saga con nombres parecidos. La lista se calcula sobre el catálogo completo
(una secuela solo es difícil si su saga también está en el catálogo).
"""
from __future__ import annotations

import re
from collections import Counter

from .normalize import flags, normalize

REMASTER = re.compile(r"\b(remaster(ed)?|remake|reforged|redux|reloaded|resynced)\b", re.I)
ROMAN = re.compile(r"\b(ii|iii|iv|vi|vii|viii|ix|xi|xii|xiii)\b", re.I)   # sin "v" ni "x" sueltas


def _prefix(norm: str) -> str:
    """Las dos primeras palabras: identifica la saga ("call of", "assetto corsa")."""
    return " ".join(norm.split()[:2])


def _without_numbers(norm: str) -> str:
    return " ".join(w for w in norm.split() if not w.isdigit())


def hard_reasons(titles: list[str]) -> dict[str, list[str]]:
    """{título: [motivos]} para los títulos que son casos difíciles."""
    norms = {t: normalize(t) for t in titles}
    by_base = Counter(_without_numbers(n) for n in norms.values())
    by_prefix = Counter(_prefix(n) for n in norms.values() if len(n.split()) >= 2)
    out: dict[str, list[str]] = {}
    for t, n in norms.items():
        reasons = []
        if REMASTER.search(t):
            reasons.append("remasterización")
        if "edition" in flags(t) or "director" in t.lower():
            reasons.append("edición")
        if ROMAN.search(t):
            reasons.append("número romano")
        has_number = any(w.isdigit() for w in n.split())
        if has_number and by_base[_without_numbers(n)] > 1:
            reasons.append("secuela con nombre parecido")
        elif len(n.split()) >= 2 and by_prefix[_prefix(n)] > 1:
            reasons.append("misma saga")
        if reasons:
            out[t] = reasons
    return out
