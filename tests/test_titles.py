import json
import tempfile
import unittest
from pathlib import Path

from radar import db
from radar.matching.titles import alt_titles, clean_store_title, kind_for


class StoreTitleTest(unittest.TestCase):
    def test_store_noise_is_removed(self):
        for raw, want in [
            ("Cyberpunk 2077 - Xbox One", "Cyberpunk 2077"),
            ("Sniper Elite 5 (Xbox Series X/Xbox One)", "Sniper Elite 5"),
            ("Warhammer 40,000: Space Marine 2 | Standard Edition | XBOX Series X|S Digital Code", "Warhammer 40,000: Space Marine 2"),
            ("Dragon Ball Xenoverse 2 - PlayStation 4 Standard Edition", "Dragon Ball Xenoverse 2"),
            ("Call of Duty: Black Ops 7 /PS5", "Call of Duty: Black Ops 7"),
            ("Clair Obscur: Expedition 33, Xbox Series X", "Clair Obscur: Expedition 33"),
            ("Horizon Zero Dawn Remastered PS5", "Horizon Zero Dawn Remastered"),
            ("Fallout 4 - Xbox One (Renewed)", "Fallout 4"),
            ("Helldivers II", "Helldivers II"),
        ]:
            self.assertEqual(clean_store_title(raw), want, raw)

    def test_alternative_titles(self):
        self.assertEqual(alt_titles("HELLDIVERS™ 2", ["Helldivers II", "HELLDIVERS 2 - PS5", "Helldivers 2 Soundtrack"]),
                         ["Helldivers II"])                       # «HELLDIVERS 2» es el mismo nombre; la banda sonora no
        self.assertEqual(alt_titles("Fallout 3: Game of the Year Edition", ["Fallout 3 - Xbox 360"]), ["Fallout 3"])
        self.assertEqual(alt_titles("Cyberpunk 2077", ["Cyberpunk 2077: Ultimate Edition"]), [])   # otra edición
        self.assertEqual(alt_titles("The Elder Scrolls V: Skyrim", ["Elder Scrolls V: Skyrim"]), [])  # solo sin «The»

    def test_kind(self):
        self.assertEqual(kind_for("Fallout 3: Game of the Year Edition"), "game+dlc")
        self.assertEqual(kind_for("Horizon Forbidden West™ Complete Edition"), "game+dlc")
        self.assertEqual(kind_for("Halo: The Master Chief Collection"), "game+dlc")
        self.assertEqual(kind_for("Gold Rush"), "game")
        self.assertEqual(kind_for("Le Mans Ultimate"), "game")
        self.assertEqual(kind_for("Graveyard Keeper OST"), "dlc")
        self.assertEqual(kind_for("The Jackbox Party Pack 7", known_game=True), "game")


class CatalogTitlesTest(unittest.TestCase):
    def test_update_titles_and_kinds(self):
        with tempfile.TemporaryDirectory() as d:
            conn = db.connect(Path(d) / "t.db")
            ids = {t: db.upsert_game(conn, {"canonical_title": t, "steam_appid": n, "origin": "discover"})
                   for n, t in enumerate(["HELLDIVERS™ 2", "Fallout 3: Game of the Year Edition",
                                          "Call of Duty®: Modern Warfare® II", "Call of Duty®: Modern Warfare® 2 (2009)"], 1)}
            manual = db.upsert_game(conn, {"canonical_title": "Tetris", "alt_titles": ["Тетрис"], "origin": "manual"})
            obs = [(ids["HELLDIVERS™ 2"], "Xbox", "Helldivers II", 1.0),
                   (ids["HELLDIVERS™ 2"], "Amazon", "Helldivers Deluxe Pack DLC", 0.95),
                   (ids["Fallout 3: Game of the Year Edition"], "GOG", "Fallout 3", 0.92),
                   (ids["Fallout 3: Game of the Year Edition"], "Amazon", "Fallout 3 New Vegas Bundle", 0.85),   # puntaje bajo
                   (ids["Call of Duty®: Modern Warfare® II"], "Amazon", "Call of Duty: Modern Warfare 2 - PS4", 1.0)]
            conn.executemany("INSERT INTO price_observations (run_id, game_id, store, title_seen, price, currency, format, "
                             "match_score, observed_at) VALUES (1, ?, ?, ?, 10, 'USD', 'digital', ?, '2026-11-15T00:00:00')", obs)
            self.assertEqual(db.update_titles_and_kinds(conn), 3)
            got = {r[0]: (json.loads(r[1]), r[2]) for r in conn.execute("SELECT canonical_title, alt_titles, kind FROM games")}
            self.assertEqual(got["HELLDIVERS™ 2"], (["Helldivers II"], "game"))
            self.assertEqual(got["Fallout 3: Game of the Year Edition"], (["Fallout 3"], "game+dlc"))
            self.assertEqual(got["Call of Duty®: Modern Warfare® II"][0], [])     # ambiguo: también es el de 2009
            self.assertEqual(got["Tetris"], (["Тетрис"], "game"))                 # lo cargado a mano se conserva
            self.assertTrue(manual)
            conn.close()


if __name__ == "__main__":
    unittest.main()
