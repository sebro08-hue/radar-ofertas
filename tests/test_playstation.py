import json
import unittest

from radar.matching import normalize, score
from radar.scrapers.base import Request
from radar.scrapers.playstation import PlayStationStore, build_ps_index, link

SKU = "UP1004-CUSA03041_00-REDEMPTIONFULL02"


def page(product, cache):
    return ("<html><head>"
            f'<script type="application/ld+json">{json.dumps(product)}</script>'
            f'<script id="env:x" type="application/json">{json.dumps({"cache": cache})}</script>'
            "</head></html>")


def cta(kind, branding, base, disc):
    return {"__typename": "GameCTA", "price": {"basePrice": f"${base / 100:.2f}", "basePriceValue": base,
                                                "discountedValue": disc, "serviceBranding": [branding]}}


class PlayStationParseTest(unittest.TestCase):
    def parse(self, body):
        req = Request("playstation", "https://www.playstation.com/en-us/games/red-dead-redemption-2/", 1)
        return PlayStationStore().parse({"id": 1}, req, body).offers

    def test_normal_purchase_price_not_ps_plus(self):
        body = page({"@context": "http://schema.org", "@type": "Product", "name": "Red Dead Redemption 2",
                     "category": "Full Game", "sku": SKU, "offers": {"@type": "Offer", "price": 14.99, "priceCurrency": "USD"}},
                    {f"Product:{SKU}": {"platforms": ["PS4"]},
                     f"GameCTA:UPSELL_PS_PLUS_GAME_CATALOG:ADD_TO_CART:{SKU}-U001:X": cta("UPSELL", "PS_PLUS", 5999, 0),
                     f"GameCTA:ADD_TO_CART:ADD_TO_CART:{SKU}-U001:OUTRIGHT": cta("ADD", "NONE", 5999, 1499)})
        [o] = self.parse(body)
        self.assertEqual((o.store, o.price, o.regular_price, o.platform), ("PlayStation", 14.99, 59.99, "PlayStation 4"))
        self.assertEqual(o.url, f"https://store.playstation.com/en-us/product/{SKU}")

    def test_falls_back_to_schema_offer(self):
        body = page({"@type": "Product", "name": "DayZ", "category": "Full Game", "sku": SKU,
                     "offers": {"price": 27.49, "priceCurrency": "USD"}}, {})
        [o] = self.parse(body)
        self.assertEqual((o.price, o.regular_price, o.platform), (27.49, None, "PlayStation 5"))

    def test_no_base_game_or_only_subscription(self):
        self.assertEqual(self.parse(page({"@type": "Product", "name": "Pack", "category": "Add-On", "sku": SKU}, {})), [])
        free = page({"@type": "Product", "name": "X", "category": "Full Game", "sku": SKU, "offers": {"price": 0, "priceCurrency": "USD"}}, {})
        self.assertEqual(self.parse(free), [])


class PlayStationLinkTest(unittest.TestCase):
    def test_sitemap_index_and_link(self):
        xml = "".join(f"<url><loc>https://www.playstation.com/en-us/games/{s}/</loc></url>"
                      for s in ("hogwarts-legacy", "borderlands-3", "borderlands-3-ultimate-edition"))
        xml += "<url><loc>https://www.playstation.com/en-us/support/</loc></url>"
        index = build_ps_index(xml)
        games = [{"id": 1, "canonical_title": "Hogwarts Legacy", "alt_titles": []},
                 {"id": 2, "canonical_title": "Borderlands® 3", "alt_titles": []},
                 {"id": 3, "canonical_title": "Stardew Valley", "alt_titles": []},
                 {"id": 4, "canonical_title": "Hogwarts Legacy", "alt_titles": [], "ps_url": "ya-enlazado"}]
        self.assertEqual(link(games, index), {1: "https://www.playstation.com/en-us/games/hogwarts-legacy/",
                                              2: "https://www.playstation.com/en-us/games/borderlands-3/"})

    def test_playstation_title_formats_match(self):
        for canonical, store in [("Hogwarts Legacy", "Hogwarts Legacy PS5 Version"),
                                 ("Borderlands 3", "Borderlands 3 PS4™ &  PS5™"),
                                 ("Ghost of Tsushima", "Ghost Of Tsushima for PlayStation 5")]:
            self.assertEqual(score(normalize(canonical), normalize(store)), 1.0, store)
        self.assertEqual(normalize("Dungeons and Dragons"), "dungeons and dragons")


if __name__ == "__main__":
    unittest.main()
