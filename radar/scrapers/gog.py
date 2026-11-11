"""GOG: catálogo JSON público (catalog.gog.com)."""
from __future__ import annotations

import json
from urllib.parse import quote

from .base import Offer, ParseResult, Request, Scraper

BASE = "https://catalog.gog.com/v1/catalog"


class GogStore(Scraper):
    name = "gog"
    kind = "price"

    def requests_for(self, game) -> list[Request]:
        q = quote(game["canonical_title"])
        return [Request(self.name,
                        f"{BASE}?limit=10&query=like:{q}&countryCode=US&currencyCode=USD&locale=en-US",
                        game["id"])]

    def parse(self, game, req, body) -> ParseResult:
        res = ParseResult(req)
        for p in json.loads(body).get("products", []):
            price = p.get("price") or {}
            final = (price.get("finalMoney") or {}).get("amount")
            base = (price.get("baseMoney") or {}).get("amount")
            if final is None:
                continue
            res.offers.append(Offer(
                store="GOG", title=p["title"], price=float(final),
                regular_price=float(base) if base else None,
                currency=(price.get("finalMoney") or {}).get("currency", "USD"),
                url=p.get("storeLink") or f"https://www.gog.com/en/game/{p.get('slug')}",
                format="digital", platform="PC", kind_hint=p.get("productType")))
        return res
