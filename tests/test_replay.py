import tempfile
import unittest
from pathlib import Path

from radar import config
from radar.http_client import HttpClient
from radar.replay import ReplayServer, is_loopback


class LoopbackOnlyTest(unittest.TestCase):
    def test_is_loopback(self):
        for host in ("127.0.0.1", "127.0.0.2", "::1", "localhost"):
            self.assertTrue(is_loopback(host), host)
        for host in ("0.0.0.0", "192.168.1.10", "example.com", ""):
            self.assertFalse(is_loopback(host), host)

    def test_client_refuses_remote_replay_host(self):
        s = config.load(None, {"source": "replay", "replay.host": "192.168.1.10"})
        with self.assertRaises(ValueError):
            HttpClient(s)

    def test_server_refuses_to_listen_outside_the_machine(self):
        with tempfile.TemporaryDirectory() as d, self.assertRaises(ValueError):
            ReplayServer(Path(d), "0.0.0.0", 0, latency_ms=0)


if __name__ == "__main__":
    unittest.main()
