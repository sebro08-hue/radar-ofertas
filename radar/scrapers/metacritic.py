"""Metacritic (HTML): Metascore desde el JSON-LD de la página del juego.

La búsqueda de Metacritic se carga con JavaScript, así que se va directo a
/game/<slug>/. Si el juego ya tiene `metacritic_slug`, se usa ese. Si no, se prueban
hasta 4 slugs deducidos del título, en orden, hasta que uno responda; el que funciona
se guarda en el juego y desde la siguiente ejecución cuesta una sola solicitud.
"""
from __future__ import annotations

import re
import unicodedata

from .base import ParseResult, Rating, Request, Scraper

BASE = "https://www.metacritic.com/game/"
MAX_CANDIDATES = 4
RATING = re.compile(r'"ratingValue"\s*:\s*"?(\d+(?:\.\d+)?)')
COUNT = re.compile(r'"reviewCount"\s*:\s*"?(\d+)')

# Palabras que marcan una edición al final del título: "- GOTY Edition",
# ": 2026 Edition", " – The Definitive Edition", "Enhanced", "Director's Cut"...
EDITION_WORDS = {"edition", "goty", "game", "of", "the", "year", "definitive", "enhanced", "complete",
                 "deluxe", "ultimate", "anniversary", "remastered", "director's", "directors", "cut"}
SEPARATORS = {"-", "–", "—", ":"}
ORDINAL_OR_YEAR = re.compile(r"\d+(?:st|nd|rd|th)?")
PAREN_YEAR = re.compile(r"\(\d{4}\)")
BRAND_PREFIX = re.compile(r"^(?:EA SPORTS|EA)\s*™?\s+", re.I)
ROMAN = {"2": "ii", "3": "iii", "4": "iv", "5": "v", "6": "vi", "7": "vii", "8": "viii", "9": "ix"}


def slugify(title: str) -> str:
    t = re.sub(r"[™®©]", "", title)            # antes de NFKD, que convierte ™ en "TM"
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
    t = t.replace("&", " ").replace("'", "").replace(".", "")   # S.T.A.L.K.E.R. -> stalker
    t = re.sub(r"(?<=\d),(?=\d)", "", t)       # "40,000" -> "40000"
    t = re.sub(r"[^a-z0-9]+", "-", t)
    return t.strip("-")


def _strip_edition(title: str) -> str:
    """Quita la edición del final del título, palabra por palabra desde atrás.
    Un año u ordinal ("2026", "40th") solo se quita si va pegado a la edición, sin un
    separador de por medio: "Sea of Thieves: 2026 Edition" -> "Sea of Thieves", pero
    "Original Sin 2 - Definitive Edition" conserva el 2 y "Madden NFL 27" no cambia."""
    words = PAREN_YEAR.sub(" ", title).split()
    removed_edition = crossed_separator = False
    while len(words) > 1:
        w = words[-1].strip(":").lower()
        if w in EDITION_WORDS:
            removed_edition = removed_edition or w not in {"of", "the"}
        elif w in SEPARATORS or w == "":
            crossed_separator = True
        elif not (removed_edition and not crossed_separator and ORDINAL_OR_YEAR.fullmatch(w)):
            break
        words.pop()
    if not removed_edition:
        return title.strip()
    return " ".join(words).rstrip(" -–—:")


def _roman(slug: str) -> str:
    return "-".join(ROMAN.get(part, part) for part in slug.split("-"))


def slug_candidates(title: str) -> list[str]:
    """Slugs posibles, del más literal al más libre."""
    clean = BRAND_PREFIX.sub("", _strip_edition(title))
    cands = [slugify(title), slugify(_strip_edition(title)), slugify(clean), _roman(slugify(clean))]
    if title.rstrip().endswith("-"):           # Metacritic conserva el guion final: "guilty-gear-strive-"
        cands.append(slugify(title) + "-")
    out: list[str] = []
    for c in cands:
        if c and c not in out:
            out.append(c)
    return out[:MAX_CANDIDATES]


class Metacritic(Scraper):
    name = "metacritic"
    kind = "rating"

    def requests_for(self, game) -> list[Request]:
        if game.get("metacritic_slug"):
            slugs = [game["metacritic_slug"]]
        else:
            slugs = slug_candidates(game["canonical_title"])
        urls = [f"{BASE}{s}/" for s in slugs]
        return [Request(self.name, urls[0], game["id"], alternates=tuple(urls[1:]))]

    def parse(self, game, req, body) -> ParseResult:
        res = ParseResult(req)
        m = RATING.search(body)
        if m:
            c = COUNT.search(body)
            res.ratings.append(Rating("metacritic", float(m.group(1)), int(c.group(1)) if c else None))
            if game and not game.get("metacritic_slug"):
                res.game_updates["metacritic_slug"] = req.url.removeprefix(BASE).strip("/")
        return res
