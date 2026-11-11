"""Metacritic (HTML): Metascore desde el JSON-LD de la página del juego.

La búsqueda de Metacritic se carga con JavaScript, así que se va directo a
/game/<slug>/. El slug sale de `games.metacritic_slug` o se deduce del título.
"""
from __future__ import annotations

import re
import unicodedata

from .base import ParseResult, Rating, Request, Scraper

RATING = re.compile(r'"ratingValue"\s*:\s*"?(\d+(?:\.\d+)?)')
COUNT = re.compile(r'"reviewCount"\s*:\s*"?(\d+)')


def slugify(title: str) -> str:
    t = re.sub(r"[™®©]", "", title)            # antes de NFKD, que convierte ™ en "TM"
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
    t = t.replace("&", " ").replace("'", "")
    t = re.sub(r"(?<=\d),(?=\d)", "", t)       # "40,000" -> "40000"
    t = re.sub(r"[^a-z0-9]+", "-", t)
    return t.strip("-")


class Metacritic(Scraper):
    name = "metacritic"
    kind = "rating"

    def requests_for(self, game) -> list[Request]:
        slug = game.get("metacritic_slug") or slugify(game["canonical_title"])
        return [Request(self.name, f"https://www.metacritic.com/game/{slug}/", game["id"])]

    def parse(self, game, req, body) -> ParseResult:
        res = ParseResult(req)
        m = RATING.search(body)
        if m:
            c = COUNT.search(body)
            res.ratings.append(Rating("metacritic", float(m.group(1)), int(c.group(1)) if c else None))
        return res
