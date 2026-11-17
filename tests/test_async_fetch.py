import tempfile
import unittest
from pathlib import Path

from radar import config, replay
from radar.async_fetch import AsyncReplayFetcher
from radar.http_client import HttpClient
from radar.pipeline import FetchJob
from radar.replay import ReplayServer
from radar.scrapers.base import Request


class AsyncFetchTest(unittest.TestCase):
    """La descarga con asyncio devuelve lo mismo que HttpClient con hilos, contra el mismo replay."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.pages = {f"https://example.com/juego/{i}": f"<html>página {i} ñ</html>".encode() for i in range(30)}
        self.pages["https://example.com/alternativa"] = b"<html>alternativa</html>"
        for url, body in self.pages.items():
            replay.save_capture(d, "example.com", url, body)
        self.server = ReplayServer(d, "127.0.0.1", 0, latency_ms=5).start_background()
        port = self.server.httpd.server_address[1]
        self.s = config.load(None, {"source": "replay", "replay_dir": str(d), "replay.port": port, "capture": False})

    def tearDown(self):
        self.server.stop()
        self.tmp.cleanup()

    def fetch_async(self, jobs, workers=8):
        http, got = HttpClient(self.s), {}
        AsyncReplayFetcher(http, self.s, workers).fetch_all(jobs, lambda job, body, exc: got.__setitem__(job.req.url, (body, exc)))
        return http, got

    def test_same_bodies_as_threads(self):
        urls = [u for u in self.pages if "/juego/" in u]
        http_async, got = self.fetch_async([FetchJob(Request("x", u, None), None, None) for u in urls])
        threads = HttpClient(self.s)
        for u in urls:
            self.assertEqual(got[u][0], threads.get(u))
        self.assertEqual(http_async.stats["example.com"]["ok"], len(urls))

    def test_alternates_and_missing_pages(self):
        jobs = [FetchJob(Request("x", "https://example.com/no-existe", None, alternates=("https://example.com/alternativa",)), None, None),
                FetchJob(Request("x", "https://example.com/tampoco", None), None, None)]
        _, got = self.fetch_async(jobs)
        self.assertEqual(got["https://example.com/alternativa"][0], "<html>alternativa</html>")   # job.req apunta a la que funcionó
        self.assertEqual(got["https://example.com/tampoco"][1].reason, "HTTP 404")

    def test_only_in_replay(self):
        with self.assertRaises(ValueError):
            AsyncReplayFetcher(None, config.load(None, {"source": "live"}), 4)


if __name__ == "__main__":
    unittest.main()
