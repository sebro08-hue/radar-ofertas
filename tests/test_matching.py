import unittest

from radar.matching import jaro_winkler, levenshtein, normalize, score


class NormalizeTest(unittest.TestCase):
    def test_platform_and_edition_suffixes(self):
        self.assertEqual(normalize("The Witcher 3: Wild Hunt - Game of the Year Edition"), "witcher 3 wild hunt")
        self.assertEqual(normalize("Resident Evil 4 (PS5)"), "resident evil 4")

    def test_roman_numerals_and_symbols(self):
        self.assertEqual(normalize("DARK SOULS™ III"), "dark souls 3")
        self.assertEqual(normalize("Pokémon & Friends"), "pokemon and friends")


class SimilarityTest(unittest.TestCase):
    def test_levenshtein(self):
        self.assertEqual(levenshtein("kitten", "sitting"), 3)

    def test_jaro_winkler_known_value(self):
        self.assertAlmostEqual(jaro_winkler("martha", "marhta"), 0.9611, places=3)

    def test_sequels_score_low(self):
        self.assertLess(score(normalize("Dark Souls III"), normalize("Dark Souls II")), 0.82)

    def test_spinoff_scores_low(self):
        self.assertLess(score(normalize("Elden Ring"), normalize("ELDEN RING NIGHTREIGN")), 0.82)

    def test_same_game_scores_high(self):
        self.assertGreaterEqual(score(normalize("Baldur's Gate 3"), normalize("Baldurs Gate III")), 0.82)


if __name__ == "__main__":
    unittest.main()


class HardCasesAndEditionNamesTest(unittest.TestCase):
    def test_named_editions_and_store_noise(self):
        from radar.matching import normalize
        for raw, want in [
            ("Kingdom Come: Deliverance II Royal Edition", "kingdom come deliverance 2"),
            ("Darkest Dungeon®: The Collector's Edition", "darkest dungeon"),
            ("Hollow Knight - For Nintendo Switch", "hollow knight"),
            ("Hunting Simulator 3 - XSX", "hunting simulator 3"),
            ("The Witcher 3: Wild Hunt - Game of the Year Edition", "witcher 3 wild hunt"),
            ("Call of Duty: Infinite Warfare - PS4 Legacy Edition", "call of duty infinite warfare"),
        ]:
            self.assertEqual(normalize(raw), want, raw)

    def test_non_latin_text_is_not_dropped(self):
        from radar.matching import normalize, score
        guide = normalize("Valor Mortis ゲームガイド (Japanese Edition)")
        self.assertNotEqual(guide, normalize("Valor Mortis"))
        self.assertLess(score(normalize("Valor Mortis"), guide), 0.84)

    def test_hard_case_reasons(self):
        from radar.matching.hard_cases import hard_reasons
        got = hard_reasons(["Cities: Skylines II", "Cities: Skylines", "Fallout 3: Game of the Year Edition",
                            "Resident Evil 4 Remake", "Stardew Valley", "Total War: WARHAMMER III"])
        self.assertIn("secuela con nombre parecido", got["Cities: Skylines II"])
        self.assertIn("número romano", got["Cities: Skylines II"])
        self.assertIn("edición", got["Fallout 3: Game of the Year Edition"])
        self.assertIn("remasterización", got["Resident Evil 4 Remake"])
        self.assertNotIn("Stardew Valley", got)
        self.assertEqual(got["Total War: WARHAMMER III"], ["número romano"])   # sin su saga en el catálogo


class MarkHardCasesTest(unittest.TestCase):
    def test_manual_marks_are_kept_and_auto_marks_refresh(self):
        import tempfile
        from pathlib import Path
        from radar import db
        with tempfile.TemporaryDirectory() as d:
            conn = db.connect(Path(d) / "t.db")
            db.upsert_game(conn, {"canonical_title": "Borderlands 2", "steam_appid": 1, "origin": "discover"})
            db.upsert_game(conn, {"canonical_title": "Borderlands 3", "steam_appid": 2, "origin": "discover"})
            db.upsert_game(conn, {"canonical_title": "Tetris", "hard_case": 1, "origin": "manual"})
            self.assertEqual(db.mark_hard_cases(conn), 3)
            conn.execute("UPDATE games SET active=0 WHERE steam_appid=2")      # sale la otra entrega
            self.assertEqual(db.mark_hard_cases(conn), 1)                     # queda solo el manual
            rows = dict(conn.execute("SELECT canonical_title, hard_reason FROM games WHERE hard_case=1").fetchall())
            self.assertEqual(rows, {"Tetris": None})
            conn.close()
