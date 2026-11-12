import json
import sqlite3
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from radar import config, db
from radar.pipeline import FetchJob, Pipeline
from radar.scrapers.base import Request
from radar.scrapers.metacritic import Metacritic, slug_candidates
from radar.scrapers.steam import SteamDiscover


class SlugCandidatesTest(unittest.TestCase):
    CASES = {
        "Sekiro™: Shadows Die Twice - GOTY Edition": "sekiro-shadows-die-twice",
        "S.T.A.L.K.E.R. 2: Heart of Chornobyl": "stalker-2-heart-of-chornobyl",
        "Warhammer 40,000: Space Marine 2": "warhammer-40000-space-marine-ii",
        "EA SPORTS™ Madden NFL 27": "madden-nfl-27",
        "Microsoft Flight Simulator (2020) 40th Anniversary Edition": "microsoft-flight-simulator",
        "GUILTY GEAR -STRIVE-": "guilty-gear-strive-",
        "BeamNG.drive": "beamngdrive",
    }

    def test_verified_slugs_are_candidates(self):
        for title, slug in self.CASES.items():
            self.assertIn(slug, slug_candidates(title), title)

    def test_edition_stripping_keeps_sequel_numbers(self):
        from radar.scrapers.metacritic import _strip_edition
        self.assertEqual(_strip_edition("Divinity: Original Sin 2 - Definitive Edition"), "Divinity: Original Sin 2")
        self.assertEqual(_strip_edition("Sea of Thieves: 2026 Edition"), "Sea of Thieves")
        self.assertEqual(_strip_edition("Madden NFL 27"), "Madden NFL 27")
        self.assertEqual(_strip_edition("Game of Thrones"), "Game of Thrones")

    def test_long_titles_are_linear(self):
        import time
        evil = "a" + " -" * 20000 + " x"
        t = time.perf_counter()
        slug_candidates(evil)
        self.assertLess(time.perf_counter() - t, 0.5)

    def test_exact_title_first_and_at_most_four(self):
        c = slug_candidates("Divinity: Original Sin 2 - Definitive Edition")
        self.assertEqual(c[0], "divinity-original-sin-2-definitive-edition")
        self.assertLessEqual(len(c), 4)

    def test_known_slug_is_used_alone(self):
        req = Metacritic().requests_for({"id": 1, "canonical_title": "X", "metacritic_slug": "x-game"})[0]
        self.assertEqual((req.url, req.alternates), ("https://www.metacritic.com/game/x-game/", ()))


class _OnlySecondExists(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/game/bueno/":
            body = b'{"ratingValue": 88, "reviewCount": 40}'
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def log_message(self, *a):
        pass


class AlternatesTest(unittest.TestCase):
    def test_falls_back_on_404_and_reports_used_url(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _OnlySecondExists)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{srv.server_port}/game/"
            s = config.load(None, {"capture": False, "cache_ttl_hours": 0, "http.max_retries": 0})
            p = Pipeline(s, conn=sqlite3.connect(":memory:"))
            mc = Metacritic()
            job = FetchJob(Request("metacritic", f"{base}malo/", 1, alternates=(f"{base}bueno/",)),
                           mc, {"id": 1, "canonical_title": "X"})
            body = p._fetch_one(job)
            self.assertEqual(job.req.url, f"{base}bueno/")
            import radar.scrapers.metacritic as m
            m_base = m.BASE
            m.BASE = base                       # para que el slug se recorte bien en el test
            try:
                res = mc.parse(job.game, job.req, body)
            finally:
                m.BASE = m_base
            self.assertEqual(res.ratings[0].score, 88)
            self.assertEqual(res.game_updates, {"metacritic_slug": "bueno"})
        finally:
            srv.shutdown()


class CatalogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "t.db"

    def tearDown(self):
        self.tmp.cleanup()

    def test_migration_marks_old_steam_games_as_discovered(self):
        old = sqlite3.connect(self.path)
        old.execute("CREATE TABLE games (id INTEGER PRIMARY KEY, canonical_title TEXT NOT NULL, "
                    "alt_titles TEXT NOT NULL DEFAULT '[]', platforms TEXT NOT NULL DEFAULT '[]', "
                    "release_date TEXT, msrp REAL, steam_appid INTEGER UNIQUE, metacritic_slug TEXT, "
                    "hltb_id INTEGER, image_url TEXT, kind TEXT NOT NULL DEFAULT 'game', "
                    "hard_case INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, added_at TEXT NOT NULL)")
        old.execute("INSERT INTO games (canonical_title, steam_appid, added_at) VALUES ('A', 10, 'x'), ('B', NULL, 'x')")
        old.commit(); old.close()
        c = db.connect(self.path)
        self.assertEqual(dict(c.execute("SELECT canonical_title, origin FROM games").fetchall()),
                         {"A": "discover", "B": "manual"})

    def test_retire_only_discovered_games_missing_from_listing(self):
        c = db.connect(self.path)
        for appid in (1, 2, 3):
            db.upsert_game(c, {"canonical_title": f"G{appid}", "steam_appid": appid, "origin": "discover"})
        db.upsert_game(c, {"canonical_title": "Manual"})
        self.assertEqual(db.retire_missing(c, {1, 3}), 1)
        active = {r[0] for r in c.execute("SELECT canonical_title FROM games WHERE active=1")}
        self.assertEqual(active, {"G1", "G3", "Manual"})
        db.upsert_game(c, {"canonical_title": "G2", "steam_appid": 2, "origin": "discover"})   # reaparece
        self.assertEqual(c.execute("SELECT active FROM games WHERE steam_appid=2").fetchone()[0], 1)


class DiscoverTest(unittest.TestCase):
    def test_games_only_and_skip_free(self):
        sd = SteamDiscover(1, 50)
        self.assertIn("category1=998", sd.page_requests()[0].url)
        row = ('<a href="https://store.steampowered.com/app/{id}/x/" data-ds-appid="{id}" class="search_result_row">'
               '<span class="title">{t}</span><div class="search_released responsive_secondrow">Jan 1, 2020</div>'
               '<div class="search_price_discount_combined" data-price-final="{p}"></div></a>')
        html = row.format(id=1, t="Pago", p=1999) + row.format(id=2, t="Gratis", p=0)
        res = sd.parse(None, sd.page_requests()[0], json.dumps({"results_html": html}))
        self.assertEqual([g["canonical_title"] for g in res.games], ["Pago"])
        self.assertEqual(res.games[0]["origin"], "discover")


if __name__ == "__main__":
    unittest.main()
