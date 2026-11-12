import threading
import time
import unittest

from radar import config
from radar.http_client import HttpClient
from radar.pipeline import pick_rotation


class RotationTest(unittest.TestCase):
    def test_never_tried_first_then_oldest(self):
        games = [{"id": i} for i in range(1, 6)]
        last = {1: "2026-11-10", 2: "2026-11-12", 3: "2026-11-11"}   # 4 y 5 nunca consultados
        self.assertEqual([g["id"] for g in pick_rotation(games, last, 3)], [4, 5, 1])

    def test_full_cycle_covers_catalog(self):
        games = [{"id": i} for i in range(1, 11)]
        last, seen = {}, []
        for day in range(4):                       # 4 días x 3 juegos >= 10 juegos
            batch = pick_rotation(games, last, 3)
            seen += [g["id"] for g in batch]
            last.update({g["id"]: f"2026-11-{10 + day:02d}" for g in batch})
        self.assertEqual(set(seen), set(range(1, 11)))


class PacingTest(unittest.TestCase):
    def _client(self, source):
        s = config.load(None, {"source": source, "capture": False, "cache_ttl_hours": 0})
        s["per_domain_interval_s"] = {"ejemplo.test": 0.2}
        return HttpClient(s)

    def test_requests_to_same_domain_are_spaced(self):
        c = self._client("live")
        starts = []
        lock = threading.Lock()

        def hit():
            c._pace("ejemplo.test")
            with lock:
                starts.append(time.monotonic())

        threads = [threading.Thread(target=hit) for _ in range(4)]   # 4 hilos a la vez
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        starts.sort()
        gaps = [b - a for a, b in zip(starts, starts[1:])]
        self.assertTrue(all(g >= 0.19 for g in gaps), gaps)

    def test_no_pause_in_replay(self):
        c = self._client("replay")
        t = time.monotonic()
        for _ in range(5):
            c._pace("ejemplo.test")
        self.assertLess(time.monotonic() - t, 0.05)


if __name__ == "__main__":
    unittest.main()
