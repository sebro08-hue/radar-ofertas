"""IGDB: duraciones de respaldo cuando un juego no tiene `hltb_id`.

How Long To Beat no se puede enlazar de forma automática (su robots.txt prohíbe /api,
que es su búsqueda, y no publica sitemap), así que sus IDs se cargan a mano. Para el
resto del catálogo se usa la API de IGDB, que es pública con credenciales de Twitch:

  1. Token:   POST id.twitch.tv/oauth2/token  (client_credentials)
  2. Steam -> IGDB:  /v4/external_games   where external_game_source = 1 (Steam)
  3. Duración:       /v4/game_time_to_beats  hastily / normally / completely (segundos)

Todo en lotes de 500 (máximo de la API): ~700 juegos son 4 o 5 solicitudes.
Credenciales: variables de entorno IGDB_CLIENT_ID e IGDB_CLIENT_SECRET (en GitHub
Actions, como secretos del repositorio). Sin ellas, la fuente se omite.
"""
from __future__ import annotations

import json
import os
from urllib.parse import urlencode

from .http_client import HttpClient
from .scrapers.base import Duration

TOKEN_URL = "https://id.twitch.tv/oauth2/token"
API = "https://api.igdb.com/v4"
STEAM_SOURCE = 1
LIMIT = 500


def credentials() -> tuple[str, str] | None:
    cid, secret = os.environ.get("IGDB_CLIENT_ID"), os.environ.get("IGDB_CLIENT_SECRET")
    return (cid, secret) if cid and secret else None


def _chunks(items: list, n: int):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def _hours(seconds) -> float | None:
    return round(seconds / 3600, 1) if seconds else None


class Igdb:
    def __init__(self, http: HttpClient, client_id: str, client_secret: str, replaying: bool = False):
        self.http = http
        self.client_id = client_id
        self.client_secret = client_secret
        self.replaying = replaying
        self._headers: dict | None = None

    def _auth(self) -> dict:
        if self._headers is None:
            token = "replay"                 # en replay las respuestas salen de las capturas
            if not self.replaying:
                body = urlencode({"client_id": self.client_id, "client_secret": self.client_secret,
                                  "grant_type": "client_credentials"})
                # store=False: la solicitud lleva el secreto; no se guarda en caché ni capturas.
                resp = self.http.post(TOKEN_URL, body, {"Content-Type": "application/x-www-form-urlencoded"},
                                      store=False)
                token = json.loads(resp)["access_token"]
            self._headers = {"Client-ID": self.client_id, "Authorization": f"Bearer {token}",
                             "Accept": "application/json"}
        return self._headers

    def _query(self, endpoint: str, body: str) -> list[dict]:
        return json.loads(self.http.post(f"{API}/{endpoint}", body, self._auth()))

    def igdb_ids(self, appids: list[int]) -> dict[int, int]:
        """steam_appid -> id de juego en IGDB."""
        out: dict[int, int] = {}
        for chunk in _chunks(sorted(set(appids)), LIMIT):
            uids = ",".join(f'"{a}"' for a in chunk)
            rows = self._query("external_games", f"fields game,uid; where external_game_source = {STEAM_SOURCE} "
                                                 f"& uid = ({uids}); limit {LIMIT};")
            for r in rows:
                if r.get("game") and str(r.get("uid", "")).isdigit():
                    out.setdefault(int(r["uid"]), int(r["game"]))
        return out

    def durations(self, appids: list[int]) -> dict[int, Duration]:
        """steam_appid -> Duration (fuente "igdb") para los juegos que IGDB tiene medidos."""
        ids = self.igdb_ids(appids)
        by_game: dict[int, dict] = {}
        for chunk in _chunks(sorted(set(ids.values())), LIMIT):
            rows = self._query("game_time_to_beats", "fields game_id,hastily,normally,completely; "
                                                     f"where game_id = ({','.join(map(str, chunk))}); limit {LIMIT};")
            by_game.update({int(r["game_id"]): r for r in rows if r.get("game_id")})
        out = {}
        for appid, gid in ids.items():
            r = by_game.get(gid)
            if not r:
                continue
            d = Duration(_hours(r.get("hastily")), _hours(r.get("normally")), _hours(r.get("completely")), "igdb")
            if any(v is not None for v in (d.main_h, d.plus_h, d.comp_h)):
                out[appid] = d
        return out
