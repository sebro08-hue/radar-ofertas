"""Amazon (minorista, HTML). Es la fuente más frágil: puede bloquear en cualquier momento.

Se usa con límite de 1 conexión simultánea (ver settings.yaml) y caché local.
La cookie `i18n-prefs=USD` pide precios en dólares; si Amazon igual responde en otra
moneda, la moneda queda registrada en la observación.
"""
from __future__ import annotations

import re
from urllib.parse import quote_plus

from .base import Offer, ParseResult, Request, Scraper, detect_platform, parse_money, strip_tags

BASE = "https://www.amazon.com/s"
SPLIT = 'data-component-type="s-search-result"'
H2 = re.compile(r"<h2[^>]*>(.*?)</h2>", re.S)
PRICE = re.compile(r'class="a-price"[^>]*>\s*<span class="a-offscreen">([^<]*)<')
REGULAR = re.compile(r'class="a-price a-text-price"[^>]*>\s*<span class="a-offscreen">([^<]*)<')
LINK = re.compile(r'href="(/[^"]*?/dp/[A-Z0-9]{10})')


class AmazonStore(Scraper):
    name = "amazon"
    kind = "price"
    cookies = (("i18n-prefs", "USD"), ("lc-main", "en_US"))

    def requests_for(self, game) -> list[Request]:
        q = quote_plus(f"{game['canonical_title']} video game")
        return [Request(self.name, f"{BASE}?k={q}&i=videogames", game["id"], self.cookies)]

    def parse(self, game, req, body) -> ParseResult:
        res = ParseResult(req)
        for block in body.split(SPLIT)[1:]:
            block = block[:12000]
            h2, price = H2.search(block), PRICE.search(block)
            if not h2 or not price:
                continue
            money = parse_money(price.group(1))
            if not money:
                continue
            reg = REGULAR.search(block)
            reg_money = parse_money(reg.group(1)) if reg else None
            link = LINK.search(block)
            title = strip_tags(h2.group(1))
            digital = re.search(r"digital code|download", title, re.I)
            res.offers.append(Offer(
                store="Amazon", title=title, price=money[0],
                regular_price=reg_money[0] if reg_money else None, currency=money[1],
                url="https://www.amazon.com" + link.group(1) if link else req.url,
                format="digital" if digital else "physical",
                platform=detect_platform(title)))
        return res
