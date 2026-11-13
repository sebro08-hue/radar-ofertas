import gzip
import json
import unittest

from radar.scrapers.base import Request
from radar.scrapers.xbox import XboxStore, build_index, link, products_in_sitemap, us_sitemaps

LOC = "<url><loc>https://www.xbox.com/en-US/games/store/{slug}/{pid}</loc></url>"


def page(pid, kind="Game", list_price=59.99, msrp=59.99, available=("XboxOne", "XboxSeriesX"), title="ELDEN RING"):
    state = {"core2": {"products": {
        "productSummaries": {pid: {"title": title, "productKind": kind, "availableOn": list(available)}},
        "availabilitySummaries": {pid: {"0010": {"9ABC": {"price": {"listPrice": list_price, "msrp": msrp}}}}},
    }}}
    return f"<html><script>window.__PRELOADED_STATE__ = {json.dumps(state)};</script></html>"


class SitemapTest(unittest.TestCase):
    def test_us_sitemaps_only_and_sorted(self):
        idx = "".join(f"<loc>https://www.xbox.com/sitemap/pdp-{loc}-sitemap-{n}.xml.gz</loc>"
                      for loc, n in [("en-US", 10), ("ar-AE", 0), ("en-US", 2), ("en-GB", 1)])
        self.assertEqual([u.rsplit("-", 1)[1] for u in us_sitemaps(idx)], ["2.xml.gz", "10.xml.gz"])

    def test_gzip_sitemap_and_shortest_name_wins(self):
        xml = "<urlset>" + LOC.format(slug="sekiro-shadows-die-twice-goty-edition", pid="9aaaaaaaaaaa") \
              + LOC.format(slug="sekiro-shadows-die-twice", pid="9bbbbbbbbbbb") + "</urlset>"
        products = products_in_sitemap(gzip.compress(xml.encode()))
        self.assertEqual(len(products), 2)
        index = build_index(products)
        self.assertTrue(index["sekiro shadows die twice"].endswith("9bbbbbbbbbbb"))

    def test_link_by_normalized_title_and_alt_titles(self):
        index = build_index([("baldurs-gate-3", "u1"), ("final-fantasy-vii-remake-intergrade", "u2")])
        games = [{"id": 1, "canonical_title": "Baldur's Gate 3", "alt_titles": []},
                 {"id": 2, "canonical_title": "FF7R", "alt_titles": ["FINAL FANTASY VII REMAKE INTERGRADE"]},
                 {"id": 3, "canonical_title": "Sin Xbox", "alt_titles": []},
                 {"id": 4, "canonical_title": "Baldur's Gate 3", "alt_titles": [], "xbox_url": "ya"}]
        self.assertEqual(link(games, index), {1: "u1", 2: "u2"})


class ProductPageTest(unittest.TestCase):
    URL = "https://www.xbox.com/en-US/games/store/elden-ring/9p3j32ctxlrz"

    def parse(self, body):
        return XboxStore().parse({"id": 1}, Request("xbox", self.URL, 1), body).offers

    def test_price_platform_and_discount(self):
        o = self.parse(page("9P3J32CTXLRZ", list_price=29.99, msrp=59.99))[0]
        self.assertEqual((o.store, o.price, o.regular_price, o.platform, o.format),
                         ("Xbox", 29.99, 59.99, "Xbox Series X|S", "digital"))

    def test_skips_non_games_free_and_broken_pages(self):
        self.assertEqual(self.parse(page("9P3J32CTXLRZ", kind="Durable")), [])
        self.assertEqual(self.parse(page("9P3J32CTXLRZ", list_price=0)), [])
        self.assertEqual(self.parse("<html>sin estado</html>"), [])
        self.assertEqual(self.parse(page("OTROPRODUCTO")), [])     # el estado es de otro producto

    def test_requests_only_for_linked_games(self):
        s = XboxStore()
        self.assertEqual(s.requests_for({"id": 1, "canonical_title": "X"}), [])
        self.assertEqual(s.requests_for({"id": 1, "canonical_title": "X", "xbox_url": self.URL})[0].url, self.URL)


if __name__ == "__main__":
    unittest.main()
