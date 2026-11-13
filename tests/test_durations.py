import csv
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from radar import db, igdb
from radar.site import _preferred_duration


class FakeHttp:
    """Responde como Twitch e IGDB y registra lo que se pidió."""

    def __init__(self, external, ttb):
        self.external, self.ttb, self.calls = external, ttb, []

    def post(self, url, data, headers=None, store=True):
        self.calls.append((url, data, store))
        if url == igdb.TOKEN_URL:
            return json.dumps({"access_token": "tok"})
        if url.endswith("/external_games"):
            return json.dumps([r for r in self.external if f'"{r["uid"]}"' in data])
        if url.endswith("/game_time_to_beats"):
            return json.dumps([r for r in self.ttb if str(r["game_id"]) in data])
        raise AssertionError(url)


class IgdbTest(unittest.TestCase):
    def setUp(self):
        self.http = FakeHttp(
            external=[{"uid": "10", "game": 100}, {"uid": "20", "game": 200}, {"uid": "30", "game": 300}],
            ttb=[{"game_id": 100, "hastily": 36000, "normally": 72000, "completely": 180000},
                 {"game_id": 200, "hastily": None, "normally": None, "completely": None}])

    def test_steam_to_igdb_to_hours(self):
        out = igdb.Igdb(self.http, "id", "secret").durations([10, 20, 30, 40])
        self.assertEqual(list(out), [10])                     # 20 sin datos, 30 sin medir, 40 no está en IGDB
        d = out[10]
        self.assertEqual((d.main_h, d.plus_h, d.comp_h, d.source), (10.0, 20.0, 50.0, "igdb"))

    def test_token_request_is_never_stored(self):
        igdb.Igdb(self.http, "id", "secret").durations([10])
        token_calls = [c for c in self.http.calls if c[0] == igdb.TOKEN_URL]
        self.assertEqual(len(token_calls), 1)
        self.assertFalse(token_calls[0][2])
        self.assertTrue(all(c[2] for c in self.http.calls if c[0] != igdb.TOKEN_URL))

    def test_no_token_in_replay(self):
        igdb.Igdb(self.http, "", "", replaying=True).durations([10])
        self.assertFalse(any(c[0] == igdb.TOKEN_URL for c in self.http.calls))

    def test_batches_of_500(self):
        igdb.Igdb(self.http, "id", "secret").durations(list(range(1, 1201)))
        self.assertEqual(sum(c[0].endswith("/external_games") for c in self.http.calls), 3)

    def test_credentials_from_environment(self):
        with mock.patch.dict(os.environ, {"IGDB_CLIENT_ID": "a", "IGDB_CLIENT_SECRET": "b"}):
            self.assertEqual(igdb.credentials(), ("a", "b"))
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(igdb.credentials())


class ManualIdsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.conn = db.connect(self.dir / "t.db")
        db.upsert_game(self.conn, {"canonical_title": "Elden Ring", "steam_appid": 1245620, "origin": "discover"})
        db.upsert_game(self.conn, {"canonical_title": "Hades", "steam_appid": 1145360, "origin": "discover"})

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_export_then_fill_then_apply(self):
        path = self.dir / "ids.csv"
        self.assertEqual(db.export_manual_ids(self.conn, path), 2)
        rows = list(csv.DictReader(open(path, encoding="utf-8")))
        self.assertIn("howlongtobeat.com/?q=", rows[0]["buscar_en_hltb"])
        for r in rows:
            if r["canonical_title"] == "Elden Ring":
                r["hltb_id"] = "68151"
            if r["canonical_title"] == "Hades":
                r["hltb_id"] = "no-es-numero"                 # celda mal escrita: se ignora
        with open(path, "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        self.assertEqual(db.apply_manual_ids(self.conn, path), 1)
        got = dict(self.conn.execute("SELECT canonical_title, hltb_id FROM games").fetchall())
        self.assertEqual(got, {"Elden Ring": 68151, "Hades": None})

    def test_missing_sheet_is_fine(self):
        self.assertEqual(db.apply_manual_ids(self.conn, self.dir / "no-existe.csv"), 0)


class SiteDurationTest(unittest.TestCase):
    def test_hltb_preferred_over_igdb(self):
        rows = [{"source": "igdb", "main_h": 10}, {"source": "hltb", "main_h": 12}]
        self.assertEqual(_preferred_duration(rows)["source"], "hltb")
        self.assertEqual(_preferred_duration([{"source": "igdb", "main_h": 10}])["source"], "igdb")
        self.assertIsNone(_preferred_duration([]))


if __name__ == "__main__":
    unittest.main()
