import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bench"))
import run_sweeps  # noqa: E402


def rows_following_amdahl(f, t1, ps, key="match_workers", metric="match"):
    """Tiempos exactos de un programa con fracción serial f: T(p) = T1 * (f + (1 - f) / p)."""
    return [{key: p, metric: t1 * (f + (1 - f) / p)} for p in ps]


class BenchMetricsTest(unittest.TestCase):
    def test_karp_flatt_formula(self):
        self.assertIsNone(run_sweeps.karp_flatt(1.0, 1))
        self.assertAlmostEqual(run_sweeps.karp_flatt(4.0, 4), 0.0)          # speedup perfecto: nada serial
        self.assertAlmostEqual(run_sweeps.karp_flatt(1.0, 8), 1.0)          # sin mejora: todo serial

    def test_amdahl_formula(self):
        self.assertAlmostEqual(run_sweeps.amdahl(0.0, 8), 8.0)
        self.assertAlmostEqual(run_sweeps.amdahl(1.0, 8), 1.0)
        self.assertAlmostEqual(run_sweeps.amdahl(0.1, 4), 1 / (0.1 + 0.9 / 4))

    def test_curve_recovers_serial_fraction(self):
        rows = rows_following_amdahl(0.2, 10.0, [1, 2, 4, 8])
        c = run_sweeps.curve(rows, "match_workers", "match", t_sec=12.0, max_p=8)
        self.assertAlmostEqual(c["f"], 0.2)
        self.assertAlmostEqual(c["T_par1"], 10.0)
        p8 = c["points"][-1]
        self.assertAlmostEqual(p8["S_rel"], run_sweeps.amdahl(0.2, 8))
        self.assertAlmostEqual(p8["S_abs"], 12.0 / (10.0 * (0.2 + 0.8 / 8)))   # contra T_sec, no T_par(1)
        self.assertAlmostEqual(p8["E"], p8["S_rel"] / 8)
        self.assertAlmostEqual(p8["amdahl"], p8["S_rel"])

    def test_curve_ignores_oversubscription_when_estimating_f(self):
        rows = rows_following_amdahl(0.1, 10.0, [1, 2, 4])
        rows.append({"match_workers": 24, "match": 10.0})                   # más workers que núcleos: empeora
        c = run_sweeps.curve(rows, "match_workers", "match", t_sec=10.0, max_p=4)
        self.assertAlmostEqual(c["f"], 0.1)

    def test_dispersion_is_reported(self):
        st = run_sweeps.summarize([3.0, 1.0, 2.0])
        self.assertEqual((st["median"], st["min"], st["max"]), (2.0, 1.0, 3.0))
        self.assertAlmostEqual(st["stdev"], 1.0)


if __name__ == "__main__":
    unittest.main()
