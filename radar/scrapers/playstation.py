"""PlayStation Store (HTML): precio desde la página del juego en playstation.com.

La búsqueda de store.playstation.com carga los resultados con JavaScript y robots.txt
prohíbe su API interna, así que, como con Xbox, no se busca:
1. Enlace (en el descubrimiento): el sitemap de EE. UU. de playstation.com lista unas
   1 900 páginas de juego /en-us/games/<nombre>/. Cada juego del catálogo se enlaza por
   nombre normalizado y el enlace queda en `games.ps_url`.
2. Precio: la página del juego trae un bloque schema.org `Product` con el juego base
   (categoría «Full Game»), su código de producto y la oferta en USD, y un JSON con las
   formas de compra. Se usa la compra normal (ADD_TO_CART, sin PS Plus): precio actual y
   regular en centavos. El precio «Included» de PS Plus no cuenta, solo vale para
   suscriptores.
"""
from __future__ import annotations

import json
import re

from .base import Offer, ParseResult, Request, Scraper
from .xbox import build_index

SITEMAP = "https://www.playstation.com/en-us/sitemap.xml"
STORE_PRODUCT = "https://store.playstation.com/en-us/product/{sku}"
GAME_LOC = re.compile(r"<loc>(https://www\.playstation\.com/en-us/games/([a-z0-9-]+)/)</loc>", re.I)
LD_JSON = 'type="application/ld+json"'
APP_JSON = 'type="application/json"'


# ---------------------------------------------------------------- enlace por sitemap

def games_in_sitemap(xml: str) -> list[tuple[str, str]]:
    """[(nombre en la URL, URL de la página del juego)]."""
    return [(m.group(2), m.group(1)) for m in GAME_LOC.finditer(xml)]


def link(games: list[dict], index: dict[str, str]) -> dict[int, str]:
    """game_id -> página del juego en playstation.com, para los juegos sin enlace."""
    from ..matching.normalize import normalize
    out = {}
    for g in games:
        if g.get("ps_url"):
            continue
        for name in (g["canonical_title"], *g.get("alt_titles", [])):
            url = index.get(normalize(name))
            if url:
                out[g["id"]] = url
                break
    return out


def build_ps_index(xml: str) -> dict[str, str]:
    return build_index(games_in_sitemap(xml))


# ---------------------------------------------------------------- página del juego

def _scripts(body: str, type_attr: str):
    """Contenido de cada <script ...> cuyo tag tiene type_attr (búsqueda lineal, sin regex)."""
    pos = 0
    while (start := body.find("<script", pos)) >= 0:
        tag_end = body.find(">", start)
        close = body.find("</script>", tag_end)
        if tag_end < 0 or close < 0:
            return
        if type_attr in body[start:tag_end]:
            yield body[tag_end + 1:close]
        pos = close + len("</script>")


def _json_blocks(type_attr: str, body: str):
    for text in _scripts(body, type_attr):
        try:
            yield json.loads(text)
        except ValueError:
            continue


def _base_game(body: str) -> dict | None:
    """El `Product` de schema.org del juego base (categoría «Full Game»)."""
    for block in _json_blocks(LD_JSON, body):
        for item in block if isinstance(block, list) else [block]:
            if isinstance(item, dict) and item.get("@type") == "Product" and item.get("category") == "Full Game":
                return item
    return None


def _cache_entries(body: str):
    for block in _json_blocks(APP_JSON, body):
        cache = block.get("cache") if isinstance(block, dict) else None
        if isinstance(cache, dict):
            yield from cache.items()


def _purchase(body: str, sku: str) -> tuple[dict | None, list[str]]:
    """(precio de la compra normal sin PS Plus, plataformas) del producto `sku`."""
    price, platforms = None, []
    for key, value in _cache_entries(body):
        if not isinstance(value, dict):
            continue
        if key == f"Product:{sku}" and value.get("platforms"):
            platforms = value["platforms"]
        p = value.get("price")
        if (price is None and key.startswith("GameCTA:ADD_TO_CART:") and sku in key and isinstance(p, dict)
                and p.get("serviceBranding") == ["NONE"] and isinstance(p.get("discountedValue"), int)):
            price = p
    return price, platforms


class PlayStationStore(Scraper):
    name = "playstation"
    kind = "price"

    def requests_for(self, game) -> list[Request]:
        if not game.get("ps_url"):
            return []
        return [Request(self.name, game["ps_url"], game["id"])]

    def parse(self, game, req, body) -> ParseResult:
        res = ParseResult(req)
        product = _base_game(body)
        if not product or not product.get("sku"):
            return res                                  # la página no vende el juego base
        sku = product["sku"]
        price, platforms = _purchase(body, sku)
        offer = product.get("offers") or {}
        offer = offer[0] if isinstance(offer, list) and offer else offer
        if price:
            current, regular = price["discountedValue"] / 100, (price.get("basePriceValue") or 0) / 100
        elif isinstance(offer, dict) and offer.get("priceCurrency") == "USD" and offer.get("price"):
            current, regular = float(offer["price"]), 0.0
        else:
            return res
        if current <= 0:
            return res                                  # gratis o solo con suscripción
        res.offers.append(Offer(
            store="PlayStation", title=product.get("name") or "", price=current,
            regular_price=regular if regular > current else None, currency="USD",
            url=STORE_PRODUCT.format(sku=sku), format="digital",
            platform="PlayStation 5" if "PS5" in platforms or not platforms else "PlayStation 4"))
        return res
