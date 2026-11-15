"""Normalización de títulos (Python puro)."""
from __future__ import annotations

import re
import unicodedata

ROMAN = {"ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7",
         "viii": "8", "ix": "9", "x": "10", "xi": "11", "xii": "12", "xiii": "13"}

# Sufijos de plataforma que las tiendas agregan al título.
PLATFORM_WORDS = re.compile(
    r"\b(for )?(nintendo switch 2|nintendo switch|switch 2|switch|playstation 5|playstation 4|ps5|ps4|"
    r"xbox series x\|?s|xbox series x|xbox series|xbox one|xsx|xbox|pc|steam|windows|mac|"
    r"digital code|download code|standard edition|european version|us version)\b")

# "Royal Edition", "The Collector's Edition", "Voidfarer Edition": el nombre de la edición
# también sobra, no solo la palabra "edition".
NAMED_EDITION = re.compile(r"\b(the )?[a-z0-9]+ edition\b")

# Palabras de edición: se quitan del título pero se reportan como "edición" en las banderas.
EDITION_WORDS = re.compile(
    r"\b(game of the year|goty|deluxe|definitive|complete|ultimate|gold|premium|"
    r"collector'?s|launch|enhanced|digital deluxe|digital|director'?s cut|standard|edition)\b")

# Si aparecen, probablemente el resultado no es el juego base.
DLC_WORDS = re.compile(r"\b(dlc|season pass|expansion|soundtrack|ost|bundle|pack|"
                       r"add-?on|upgrade|costume|skin|artbook|guide|currency|coins|points)\b")


def _mark_non_latin(text: str) -> str:
    """Texto en otra escritura (p. ej. japonés) desaparecería al pasar a ASCII y el título
    quedaría igual al del juego; se deja una marca para que cuente como diferencia."""
    out, prev = [], False
    for ch in text:
        other = unicodedata.category(ch) == "Lo"
        if other and not prev:
            out.append(" nonlatin ")
        elif not other:
            out.append(ch)
        prev = other
    return "".join(out)


def _ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def normalize(title: str) -> str:
    t = re.sub(r"[™®©]", "", title)              # antes de NFKD, que convierte ™ en "TM"
    t = _ascii(_mark_non_latin(t)).lower()
    t = re.sub(r"['’`´]", "", t)                 # marvel's -> marvels
    t = t.replace("&", " and ")
    t = re.sub(r"\(.*?\)|\[.*?\]", " ", t)       # (PS5), [Digital], etc.
    t = PLATFORM_WORDS.sub(" ", t)
    t = re.sub(r"\bgame of the year\b", "goty", t)     # antes de NAMED_EDITION: "year edition"
    t = NAMED_EDITION.sub(" ", t)
    t = EDITION_WORDS.sub(" ", t)
    t = re.sub(r"[^a-z0-9 ]+", " ", t)           # puntuación, guiones, dos puntos
    tokens = [ROMAN.get(tok, tok) for tok in t.split()]
    if tokens and tokens[0] == "the":
        tokens = tokens[1:]
    return " ".join(tokens)


def flags(title: str) -> set[str]:
    low = _ascii(title).lower()
    out = set()
    if EDITION_WORDS.search(low):
        out.add("edition")
    if DLC_WORDS.search(low):
        out.add("dlc_or_bundle")
    return out
