"""Xbox Store (HTML): precio desde la página de producto.

El robots.txt de xbox.com prohíbe las páginas de búsqueda, así que no se busca:
1. Enlace (en el descubrimiento): los sitemaps de EE. UU. listan unas 45 000 páginas
   de producto como /en-US/games/store/<nombre>/<id>. Se arma un índice nombre -> URL
   y cada juego del catálogo se enlaza por nombre normalizado. El enlace queda en
   `games.xbox_url`.
2. Precio: la página de producto trae un JSON (`window.__PRELOADED_STATE__`) con
   título, tipo de producto, plataformas, precio actual (`listPrice`) y regular (`msrp`).
"""
from __future__ import annotations

import gzip
import json
import re

from ..matching.normalize import normalize
from .base import Offer, ParseResult, Request, Scraper

SITEMAP_INDEX = "https://www.xbox.com/sitemap.xml"
US_SITEMAP = re.compile(r"<loc>(https://www\.xbox\.com/sitemap/pdp-en-US-sitemap-\d+\.xml\.gz)</loc>")
PRODUCT_LOC = re.compile(r"<loc>(https://www\.xbox\.com/en-US/games/store/([a-z0-9-]+)/([a-z0-9]{12}))</loc>", re.I)
STATE_MARK = "window.__PRELOADED_STATE__"
PLATFORMS = [("XboxSeriesX", "Xbox Series X|S"), ("XboxOne", "Xbox One"), ("PC", "PC")]


# ---------------------------------------------------------------- enlace por sitemaps

def us_sitemaps(index_xml: str) -> list[str]:
    return sorted(set(US_SITEMAP.findall(index_xml)), key=lambda u: int(re.search(r"-(\d+)\.xml", u).group(1)))


def products_in_sitemap(content: bytes) -> list[tuple[str, str]]:
    """[(nombre en la URL, URL del producto)] de un sitemap (comprimido o no)."""
    if content[:2] == b"\x1f\x8b":
        content = gzip.decompress(content)
    return [(m.group(2), m.group(1)) for m in PRODUCT_LOC.finditer(content.decode("utf-8", "replace"))]


def build_index(products: list[tuple[str, str]]) -> dict[str, str]:
    """Nombre normalizado -> URL. Si varias páginas normalizan igual (p. ej. el juego y
    su "GOTY Edition"), gana el nombre más corto: casi siempre es el juego base."""
    best: dict[str, tuple[int, str]] = {}
    for slug, url in products:
        key = normalize(slug.replace("-", " "))
        if key and (key not in best or len(slug) < best[key][0]):
            best[key] = (len(slug), url)
    return {k: url for k, (_, url) in best.items()}


def link(games: list[dict], index: dict[str, str]) -> dict[int, str]:
    """game_id -> URL de Xbox, para los juegos sin enlace cuyo nombre aparece en el índice."""
    out = {}
    for g in games:
        if g.get("xbox_url"):
            continue
        for name in (g["canonical_title"], *g.get("alt_titles", [])):
            url = index.get(normalize(name))
            if url:
                out[g["id"]] = url
                break
    return out


# ---------------------------------------------------------------- página de producto

def _state(body: str) -> dict | None:
    i = body.find(STATE_MARK)
    if i < 0:
        return None
    try:
        state, _ = json.JSONDecoder().raw_decode(body, body.index("{", i))
    except ValueError:
        return None
    return state


def _price(avail: dict) -> dict | None:
    """Primer bloque `price` dentro de availabilitySummaries[<id>] (anidado por SKU)."""
    stack = [avail]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if isinstance(node.get("price"), dict) and "listPrice" in node["price"]:
                return node["price"]
            stack.extend(node.values())
    return None


class XboxStore(Scraper):
    name = "xbox"
    kind = "price"

    def requests_for(self, game) -> list[Request]:
        if not game.get("xbox_url"):
            return []
        return [Request(self.name, game["xbox_url"], game["id"])]

    def parse(self, game, req, body) -> ParseResult:
        res = ParseResult(req)
        state = _state(body)
        if not state:
            return res
        pid = req.url.rstrip("/").rsplit("/", 1)[-1].upper()
        products = (state.get("core2") or {}).get("products") or {}
        summary = (products.get("productSummaries") or {}).get(pid) or {}
        price = _price((products.get("availabilitySummaries") or {}).get(pid) or {})
        if summary.get("productKind") != "Game" or not price or not price.get("listPrice"):
            return res                      # no es un juego, es gratis o no está a la venta
        available = summary.get("availableOn") or []
        platform = next((name for key, name in PLATFORMS if key in available), None)
        current, regular = float(price["listPrice"]), float(price.get("msrp") or 0)
        res.offers.append(Offer(
            store="Xbox", title=summary.get("title") or "", price=current,
            regular_price=regular if regular > current else None,
            currency=price.get("currencyCode") or "USD", url=req.url,
            format="digital", platform=platform))
        return res
