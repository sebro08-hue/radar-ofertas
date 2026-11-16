import json
from datetime import datetime, timedelta, timezone
import tempfile
import unittest
from pathlib import Path

from radar import config, db, site


class SiteCardsTest(unittest.TestCase):
    def test_single_store_games_are_published_and_marked(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            conn = db.connect(d / "t.db")
            solo = db.upsert_game(conn, {"canonical_title": "Solo en Steam", "steam_appid": 1, "origin": "discover"})
            dos = db.upsert_game(conn, {"canonical_title": "En dos tiendas", "steam_appid": 2, "origin": "discover"})
            nada = db.upsert_game(conn, {"canonical_title": "Sin precio", "steam_appid": 3, "origin": "discover"})
            now = datetime.now(timezone.utc)
            fresh, old = now.isoformat(timespec="seconds"), (now - timedelta(days=30)).isoformat(timespec="seconds")
            rows = [(1, solo, "Steam", 10.0, "a", fresh), (1, dos, "Steam", 20.0, "b", fresh), (1, dos, "GOG", 15.0, "c", fresh),
                    (1, solo, "Amazon", 0.0, "libro", fresh),          # precio 0: no cuenta
                    (1, solo, "GOG", 5.0, "vieja", old),               # sin confirmar hace 30 días: no cuenta
                    (1, dos, "Amazon", 9.0, "emparejamiento-viejo", fresh),
                    (2, dos, "Amazon", 12.0, "emparejamiento-nuevo", fresh)]   # la última ejecución reemplaza a la anterior
            conn.executemany(
                "INSERT INTO price_observations (run_id, game_id, store, title_seen, price, currency, format, url, observed_at) "
                "VALUES (?, ?, ?, 't', ?, 'USD', 'digital', ?, ?)", rows)
            conn.commit()
            dist = site.build(conn, config.load(None, {"site_dir": str(d / "dist")}))
            cards = {c["title"]: c for c in json.loads((dist / "data" / "cards.json").read_text())["cards"]}
            self.assertEqual(set(cards), {"Solo en Steam", "En dos tiendas"})       # sin precio no se publica
            self.assertEqual(cards["Solo en Steam"]["store_count"], 1)
            self.assertEqual(cards["Solo en Steam"]["best_price"], 10.0)
            self.assertEqual(cards["En dos tiendas"]["store_count"], 3)
            self.assertEqual(cards["En dos tiendas"]["best_store"], "Amazon")
            self.assertEqual(cards["En dos tiendas"]["best_price"], 12.0)
            self.assertTrue((dist / "data" / "games" / f"{solo}.json").is_file())
            self.assertFalse((dist / "data" / "games" / f"{nada}.json").exists())
            html = (dist / "index.html").read_text(encoding="utf-8")
            self.assertRegex(html, r'href="style\.css\?v=[0-9a-f]{10}"')     # el navegador no usa una versión vieja
            self.assertRegex(html, r'src="app\.js\?v=[0-9a-f]{10}"')
            conn.close()


if __name__ == "__main__":
    unittest.main()
