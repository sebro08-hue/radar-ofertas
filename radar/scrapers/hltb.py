"""How Long To Beat (HTML): página del juego por id.

La búsqueda de HLTB funciona con JavaScript y un token, así que no se usa: el id se
guarda en `games.hltb_id` (se llena a mano o con `python -m radar games set ...`).
Los juegos sin hltb_id simplemente no generan solicitud.
"""
from __future__ import annotations

import re

from .base import Duration, ParseResult, Request, Scraper

FIELD = {k: re.compile(rf'"{k}"\s*:\s*(\d+)') for k in ("comp_main", "comp_plus", "comp_100")}


def _hours(body: str, key: str) -> float | None:
    m = FIELD[key].search(body)
    if not m or int(m.group(1)) == 0:
        return None
    return round(int(m.group(1)) / 3600, 1)


class Hltb(Scraper):
    name = "hltb"
    kind = "duration"

    def requests_for(self, game) -> list[Request]:
        if not game.get("hltb_id"):
            return []
        return [Request(self.name, f"https://howlongtobeat.com/game/{game['hltb_id']}", game["id"])]

    def parse(self, game, req, body) -> ParseResult:
        res = ParseResult(req)
        d = Duration(_hours(body, "comp_main"), _hours(body, "comp_plus"), _hours(body, "comp_100"))
        if any(v is not None for v in (d.main_h, d.plus_h, d.comp_h)):
            res.duration = d
        return res
