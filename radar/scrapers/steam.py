"""Steam: descubrimiento (Top Sellers paginado), precios y reseñas de usuarios.

Se usa el endpoint de búsqueda `search/results/?infinite=1`, que devuelve JSON con un
fragmento HTML (`results_html`). Ese fragmento se parsea como HTML.
`cc=us` fija la moneda en USD (si no, Steam usa la del país de la IP).
"""
from __future__ import annotations

import json
import re
from urllib.parse import quote_plus

from .base import Offer, ParseResult, Rating, Request, Scraper, parse_money, strip_tags

BASE = "https://store.steampowered.com/search/results/"
ROW = re.compile(r'<a href="(https://store\.steampowered\.com/(app|bundle|sub)/[^"]+)"(.*?)</a>', re.S)
APPID = re.compile(r'data-ds-appid="(\d+)"')
TITLE = re.compile(r'<span class="title">(.*?)</span>', re.S)
RELEASED = re.compile(r'class="search_released[^"]*">\s*([^<]*?)\s*</div>', re.S)
FINAL = re.compile(r'data-price-final="(\d+)"')
ORIGINAL = re.compile(r'class="discount_original_price">([^<]*)<')
REVIEW = re.compile(r'(\d+)% of the ([\d,]+) user reviews')


def _rows(body: str):
    data = json.loads(body)
    fragment = data.get("results_html", "")
    for m in ROW.finditer(fragment):
        url, kind, inner = m.group(1), m.group(2), m.group(3)
        block = m.group(0)
        t = TITLE.search(inner)
        if not t:
            continue
        appid = APPID.search(block)
        final = FINAL.search(block)
        orig = ORIGINAL.search(block)
        rel = RELEASED.search(inner)
        rev = REVIEW.search(block)
        yield {
            "url": url.split("?")[0],
            "kind": kind,
            "appid": int(appid.group(1).split(",")[0]) if appid else None,
            "title": strip_tags(t.group(1)),
            "price": int(final.group(1)) / 100 if final else None,
            "regular": parse_money(orig.group(1))[0] if orig and parse_money(orig.group(1)) else None,
            "released": rel.group(1).strip() if rel else None,
            "review_pct": int(rev.group(1)) if rev else None,
            "review_votes": int(rev.group(2).replace(",", "")) if rev else None,
        }


class SteamDiscover(Scraper):
    """Etapa 0: recorre las páginas de Top Sellers."""
    name = "steam_discover"
    kind = "discover"

    def __init__(self, pages: int, page_size: int):
        self.pages, self.page_size = pages, page_size

    def page_requests(self) -> list[Request]:
        return [Request(self.name,
                        f"{BASE}?filter=topsellers&cc=us&infinite=1&count={self.page_size}"
                        f"&start={p * self.page_size}")
                for p in range(self.pages)]

    def parse(self, game, req, body) -> ParseResult:
        res = ParseResult(req)
        for r in _rows(body):
            if r["kind"] != "app" or not r["appid"]:
                continue
            res.games.append({
                "canonical_title": r["title"],
                "steam_appid": r["appid"],
                "release_date": r["released"],
                "msrp": r["regular"] or r["price"],
                "platforms": ["PC"],
                "image_url": f"https://cdn.akamai.steamstatic.com/steam/apps/{r['appid']}/header.jpg",
            })
        return res


class SteamStore(Scraper):
    """Precios en Steam + calificación de usuarios de Steam (sale en la misma fila)."""
    name = "steam"
    kind = "price"

    def requests_for(self, game) -> list[Request]:
        q = quote_plus(game["canonical_title"])
        return [Request(self.name, f"{BASE}?term={q}&cc=us&infinite=1&count=10", game["id"])]

    def parse(self, game, req, body) -> ParseResult:
        res = ParseResult(req)
        for r in _rows(body):
            if r["price"] is None:
                continue
            res.offers.append(Offer(
                store="Steam", title=r["title"], price=r["price"], regular_price=r["regular"],
                currency="USD", url=r["url"], format="digital", platform="PC",
                kind_hint="bundle" if r["kind"] != "app" else None))
            # La reseña solo cuenta si la fila es exactamente el juego (appid conocido).
            if game and r["appid"] and r["appid"] == game.get("steam_appid") and r["review_pct"] is not None:
                res.ratings.append(Rating("steam_users", r["review_pct"], r["review_votes"], "PC"))
        return res
