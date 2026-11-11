"""Tipos comunes de los scrapers.

Cada scraper sabe dos cosas:
  1. `requests_for(game)`: qué URLs pedir para un juego (puede ser ninguna).
  2. `parse(game, req, body)`: convertir el texto descargado en resultados.

Los scrapers no hacen E/S ni tocan la base: eso lo decide el pipeline según el modo.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Request:
    source: str
    url: str
    game_id: int | None = None
    cookies: tuple = ()          # tupla de pares para que el Request sea hashable / picklable


@dataclass
class Offer:
    """Un resultado de tienda. Todavía no se sabe a qué juego corresponde: eso lo decide el emparejamiento."""
    store: str
    title: str
    price: float
    regular_price: float | None
    currency: str
    url: str
    format: str = "digital"      # digital | physical
    platform: str | None = None
    kind_hint: str | None = None  # game | dlc | pack | ...


@dataclass
class Rating:
    source: str
    score: float                 # 0..100
    votes: int | None = None
    platform: str | None = None


@dataclass
class Duration:
    main_h: float | None
    plus_h: float | None
    comp_h: float | None


@dataclass
class ParseResult:
    request: Request
    offers: list[Offer] = field(default_factory=list)
    ratings: list[Rating] = field(default_factory=list)
    duration: Duration | None = None
    games: list[dict] = field(default_factory=list)   # solo para descubrimiento (etapa 0)


class Scraper:
    name: str = "?"
    kind: str = "?"              # price | rating | duration | discover

    def requests_for(self, game: dict) -> list[Request]:
        return []

    def parse(self, game: dict | None, req: Request, body: str) -> ParseResult:
        raise NotImplementedError


# ---------------------------------------------------------------- ayudas de parseo

_TAG = re.compile(r"<[^>]+>")
_MONEY = re.compile(r"([A-Z]{3}|[$€£])?\s*([\d.,]+)")
_SYMBOL = {"$": "USD", "€": "EUR", "£": "GBP"}


def strip_tags(fragment: str) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", fragment)).split())


def parse_money(text: str) -> tuple[float, str] | None:
    """'$59.99' -> (59.99, 'USD');  'CRC 15,367.92' -> (15367.92, 'CRC')."""
    m = _MONEY.search(text.replace("\xa0", " "))
    if not m:
        return None
    cur = _SYMBOL.get(m.group(1) or "$", m.group(1) or "USD")
    try:
        return float(m.group(2).replace(",", "")), cur
    except ValueError:
        return None


PLATFORM_PATTERNS = [
    ("PlayStation 5", re.compile(r"\b(ps5|playstation 5)\b", re.I)),
    ("PlayStation 4", re.compile(r"\b(ps4|playstation 4)\b", re.I)),
    ("Nintendo Switch 2", re.compile(r"\bswitch 2\b", re.I)),
    ("Nintendo Switch", re.compile(r"\b(nintendo switch|switch)\b", re.I)),
    ("Xbox Series X|S", re.compile(r"\bxbox series\b", re.I)),
    ("Xbox One", re.compile(r"\bxbox one\b", re.I)),
    ("PC", re.compile(r"\b(pc|windows|steam code)\b", re.I)),
]


def detect_platform(title: str) -> str | None:
    for name, pat in PLATFORM_PATTERNS:
        if pat.search(title):
            return name
    return None
