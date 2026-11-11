import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from radar import config
from radar.http_client import FetchError, HttpClient
from radar.scrapers.base import Request
from radar.scrapers.metacritic import slugify
from radar.scrapers.steam import SteamStore


class SlugTest(unittest.TestCase):
    def test_trademark_symbols_removed(self):
        self.assertEqual(slugify("STAR WARS™: The Old Republic™"), "star-wars-the-old-republic")
        self.assertEqual(slugify("Tom Clancy's Ghost Recon® Wildlands"), "tom-clancys-ghost-recon-wildlands")
        self.assertEqual(slugify("Warhammer 40,000: Space Marine 2"), "warhammer-40000-space-marine-2")


class SteamBatchTest(unittest.TestCase):
    def test_batches_of_100_and_skip_games_with_appid(self):
        s = SteamStore()
        games = [{"id": i, "steam_appid": 1000 + i, "canonical_title": f"g{i}"} for i in range(250)]
        reqs = s.batch_requests(games)
        self.assertEqual(len(reqs), 3)
        self.assertEqual(s.requests_for(games[0]), [])
        self.assertEqual(len(s.requests_for({"id": 1, "canonical_title": "Sin appid"})), 1)

    def test_parse_batch_skips_free_games(self):
        body = json.dumps({
            "10": {"success": True, "data": {"price_overview": {"currency": "USD", "initial": 5999, "final": 599}}},
            "20": {"success": True, "data": []},
            "30": {"success": False}})
        req = Request("steam", "https://store.steampowered.com/api/appdetails?appids=10,20,30&cc=us")
        offers = SteamStore().parse(None, req, body).offers
        self.assertEqual([(o.steam_appid, o.price, o.regular_price) for o in offers], [(10, 5.99, 59.99)])


class _Always503(BaseHTTPRequestHandler):
    hits = 0

    def do_GET(self):  # noqa: N802
        type(self).hits += 1
        self.send_error(503)

    def log_message(self, *a):
        pass


class CircuitBreakerTest(unittest.TestCase):
    def test_opens_after_threshold_and_stops_hitting_site(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _Always503)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            s = config.load(None, {"capture": False, "cache_ttl_hours": 0, "http.max_retries": 0,
                                   "http.circuit_breaker_failures": 3, "http.backoff_base_s": 0})
            client = HttpClient(s)
            url = f"http://127.0.0.1:{srv.server_port}/x"
            for _ in range(10):
                with self.assertRaises(FetchError):
                    client.get(url)
            self.assertTrue(client.is_open(url))
            self.assertEqual(_Always503.hits, 3)       # después del umbral, no se vuelve a tocar el sitio
        finally:
            srv.shutdown()


if __name__ == "__main__":
    unittest.main()
