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
