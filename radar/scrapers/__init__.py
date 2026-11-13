"""Registro de scrapers. Para agregar una fuente: crear el módulo y sumarlo aquí."""
from .amazon import AmazonStore
from .base import Duration, Offer, ParseResult, Rating, Request, Scraper
from .gog import GogStore
from .hltb import Hltb
from .metacritic import Metacritic
from .steam import SteamDiscover, SteamStore
from .xbox import XboxStore

REGISTRY: dict[str, type[Scraper]] = {
    "steam": SteamStore,
    "gog": GogStore,
    "amazon": AmazonStore,
    "metacritic": Metacritic,
    "hltb": Hltb,
    "xbox": XboxStore,
}


def build(names: list[str]) -> dict[str, Scraper]:
    """Scrapers de las fuentes activas. Las fuentes que no son scrapers por página
    (p. ej. "igdb", que consulta por lotes) las maneja el pipeline aparte."""
    return {n: REGISTRY[n]() for n in names if n in REGISTRY}


__all__ = ["REGISTRY", "build", "SteamDiscover", "Scraper", "Request", "Offer",
           "Rating", "Duration", "ParseResult"]
