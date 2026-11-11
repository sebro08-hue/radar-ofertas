"""Medidas de similitud implementadas a mano, en Python puro (sin librerías compiladas),
para que el efecto del GIL sea observable al paralelizar con hilos vs procesos."""
from __future__ import annotations


def levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def levenshtein_ratio(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    return 1.0 - levenshtein(a, b) / max(len(a), len(b))


def jaro(a: str, b: str) -> float:
    if a == b:
        return 1.0
    la, lb = len(a), len(b)
    if not la or not lb:
        return 0.0
    window = max(la, lb) // 2 - 1
    ma, mb = [False] * la, [False] * lb
    matches = 0
    for i, ca in enumerate(a):
        lo, hi = max(0, i - window), min(i + window + 1, lb)
        for j in range(lo, hi):
            if not mb[j] and b[j] == ca:
                ma[i] = mb[j] = True
                matches += 1
                break
    if not matches:
        return 0.0
    k = transpositions = 0
    for i in range(la):
        if ma[i]:
            while not mb[k]:
                k += 1
            if a[i] != b[k]:
                transpositions += 1
            k += 1
    m = matches
    return (m / la + m / lb + (m - transpositions / 2) / m) / 3


def jaro_winkler(a: str, b: str, p: float = 0.1) -> float:
    j = jaro(a, b)
    prefix = 0
    for ca, cb in zip(a[:4], b[:4]):
        if ca != cb:
            break
        prefix += 1
    return j + prefix * p * (1 - j)


def token_set_ratio(a: str, b: str) -> float:
    """Similitud por conjuntos de tokens: tolera orden distinto y palabras extra."""
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    inter = " ".join(sorted(ta & tb))
    ra = " ".join(sorted(ta - tb))
    rb = " ".join(sorted(tb - ta))
    s1 = (inter + " " + ra).strip()
    s2 = (inter + " " + rb).strip()
    return max(levenshtein_ratio(inter, s1) if inter else 0.0,
               levenshtein_ratio(inter, s2) if inter else 0.0,
               levenshtein_ratio(s1, s2))


def _numbers(s: str) -> set[str]:
    return {t for t in s.split() if t.isdigit()}


def token_sort_ratio(a: str, b: str) -> float:
    return levenshtein_ratio(" ".join(sorted(a.split())), " ".join(sorted(b.split())))


def score(game: str, candidate: str) -> float:
    """Puntaje 0..1 entre el título del juego y el de un resultado de tienda (ya normalizados).

    Es asimétrico a propósito:
      - Palabras que SOBRAN en el candidato pesan mucho ("elden ring" vs "elden ring
        nightreign"): casi siempre es otro producto (secuela, spin-off, DLC).
      - Palabras que FALTAN en el candidato pesan poco ("legend of zelda ..." vs "zelda ..."):
        las tiendas suelen acortar.
      - Números distintos ("dark souls 2" vs "dark souls 3") se castigan fuerte.
    """
    if game == candidate:
        return 1.0
    ta, tb = set(game.split()), set(candidate.split())
    s = 0.5 * jaro_winkler(game, candidate) + 0.5 * token_sort_ratio(game, candidate)
    s *= 0.90 ** len(tb - ta)
    s *= 0.97 ** len(ta - tb)
    if _numbers(game) != _numbers(candidate):
        s *= 0.75
    return min(1.0, s)
