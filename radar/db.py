"""Repositorio SQLite.

Regla: solo el hilo principal escribe en la base (etapa de agregación). Los workers
devuelven resultados y nunca tocan la conexión, así se evita compartir la conexión
entre hilos.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id              INTEGER PRIMARY KEY,
    canonical_title TEXT NOT NULL,
    alt_titles      TEXT NOT NULL DEFAULT '[]',   -- JSON list
    platforms       TEXT NOT NULL DEFAULT '[]',   -- JSON list
    release_date    TEXT,
    msrp            REAL,
    steam_appid     INTEGER UNIQUE,
    metacritic_slug TEXT,
    hltb_id         INTEGER,
    image_url       TEXT,
    kind            TEXT NOT NULL DEFAULT 'game', -- game | game+dlc | dlc | other
    hard_case       INTEGER NOT NULL DEFAULT 0,   -- caso difícil de emparejamiento
    active          INTEGER NOT NULL DEFAULT 1,
    added_at        TEXT NOT NULL,
    origin          TEXT NOT NULL DEFAULT 'manual' -- discover | manual
);

-- Historial: cada observación se agrega, nunca se sobrescribe.
CREATE TABLE IF NOT EXISTS price_observations (
    id            INTEGER PRIMARY KEY,
    run_id        INTEGER NOT NULL,
    game_id       INTEGER NOT NULL REFERENCES games(id),
    store         TEXT NOT NULL,
    title_seen    TEXT NOT NULL,
    price         REAL NOT NULL,
    regular_price REAL,
    currency      TEXT NOT NULL,
    format        TEXT NOT NULL,   -- digital | physical
    platform      TEXT,
    url           TEXT,
    match_score   REAL,
    observed_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_price_game ON price_observations(game_id, store, observed_at);

CREATE TABLE IF NOT EXISTS ratings (
    id          INTEGER PRIMARY KEY,
    run_id      INTEGER NOT NULL,
    game_id     INTEGER NOT NULL REFERENCES games(id),
    source      TEXT NOT NULL,
    score       REAL NOT NULL,       -- 0..100
    votes       INTEGER,
    platform    TEXT,
    observed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS durations (
    id          INTEGER PRIMARY KEY,
    run_id      INTEGER NOT NULL,
    game_id     INTEGER NOT NULL REFERENCES games(id),
    main_h      REAL, plus_h REAL, comp_h REAL,
    observed_at TEXT NOT NULL
);

-- Última vez que cada fuente dio datos de cada juego (para marcar datos desactualizados).
CREATE TABLE IF NOT EXISTS source_status (
    game_id   INTEGER NOT NULL,
    source    TEXT NOT NULL,
    last_ok   TEXT,
    last_try  TEXT NOT NULL,
    ok        INTEGER NOT NULL,
    PRIMARY KEY (game_id, source)
);

CREATE TABLE IF NOT EXISTS match_flags (
    id        INTEGER PRIMARY KEY,
    run_id    INTEGER NOT NULL,
    game_id   INTEGER NOT NULL,
    store     TEXT NOT NULL,
    candidate TEXT,
    score     REAL,
    reason    TEXT NOT NULL      -- unmatched | near_threshold | edition | dlc_or_bundle
);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    mode        TEXT NOT NULL,
    source      TEXT NOT NULL,
    workers     TEXT NOT NULL,      -- JSON
    stage_times TEXT,               -- JSON {etapa: segundos}
    total_s     REAL
);

CREATE TABLE IF NOT EXISTS request_stats (
    run_id  INTEGER NOT NULL,
    domain  TEXT NOT NULL,
    ok      INTEGER NOT NULL,
    retried INTEGER NOT NULL,
    failed  INTEGER NOT NULL,
    PRIMARY KEY (run_id, domain)
);

CREATE TABLE IF NOT EXISTS failures (
    id        INTEGER PRIMARY KEY,
    run_id    INTEGER NOT NULL,
    source    TEXT NOT NULL,
    game_id   INTEGER,
    url       TEXT NOT NULL,
    error     TEXT NOT NULL,
    recovered INTEGER NOT NULL DEFAULT 0  -- 1 si el reintento final funcionó
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Cambios de esquema para bases creadas con versiones anteriores."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(games)")}
    if "origin" not in cols:
        # Hasta ahora todo juego con steam_appid salió del descubrimiento.
        conn.execute("ALTER TABLE games ADD COLUMN origin TEXT NOT NULL DEFAULT 'manual'")
        conn.execute("UPDATE games SET origin='discover' WHERE steam_appid IS NOT NULL")
        conn.commit()


# ---------------------------------------------------------------- juegos

def upsert_game(conn: sqlite3.Connection, g: dict) -> int:
    """Inserta un juego o actualiza el existente (por steam_appid o título)."""
    row = None
    if g.get("steam_appid"):
        row = conn.execute("SELECT id FROM games WHERE steam_appid=?", (g["steam_appid"],)).fetchone()
    if row is None:
        row = conn.execute("SELECT id FROM games WHERE lower(canonical_title)=lower(?)",
                           (g["canonical_title"],)).fetchone()
    fields = {k: g.get(k) for k in ("canonical_title", "release_date", "msrp", "steam_appid",
                                     "metacritic_slug", "hltb_id", "image_url", "kind", "hard_case")
              if g.get(k) is not None}
    for k in ("alt_titles", "platforms"):
        if g.get(k) is not None:
            fields[k] = json.dumps(g[k], ensure_ascii=False)
    if g.get("origin") == "discover":
        fields["active"] = 1                  # reaparece en el listado: vuelve al catálogo
    if row:
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE games SET {sets} WHERE id=?", (*fields.values(), row["id"]))
        return row["id"]
    fields["added_at"] = now()
    fields["origin"] = g.get("origin") or "manual"   # el origen solo se fija al crear el juego
    cols = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    cur = conn.execute(f"INSERT INTO games ({cols}) VALUES ({marks})", tuple(fields.values()))
    return cur.lastrowid


def load_games(conn: sqlite3.Connection, limit: int | None = None) -> list[dict]:
    sql = "SELECT * FROM games WHERE active=1 ORDER BY id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    games = []
    for r in conn.execute(sql):
        g = dict(r)
        g["alt_titles"] = json.loads(g["alt_titles"])
        g["platforms"] = json.loads(g["platforms"])
        games.append(g)
    return games


UPDATABLE_FIELDS = {"metacritic_slug", "hltb_id", "image_url"}


def update_game_fields(conn: sqlite3.Connection, game_id: int, fields: dict) -> None:
    """Actualiza campos de un juego que una fuente descubrió (lista blanca de columnas)."""
    fields = {k: v for k, v in fields.items() if k in UPDATABLE_FIELDS}
    if fields:
        sets = ", ".join(f"{k}=?" for k in fields)
        # Los nombres de columna salen de UPDATABLE_FIELDS (lista blanca); los valores van como parámetros.
        conn.execute(f"UPDATE games SET {sets} WHERE id=?", (*fields.values(), game_id))  # NOSONAR


def retire_missing(conn: sqlite3.Connection, seen_appids: set[int]) -> int:
    """Desactiva los juegos que vinieron del descubrimiento y ya no aparecen en el listado.
    No borra nada: el historial de precios se conserva y el juego vuelve si reaparece.
    Los juegos agregados a mano (origin='manual') nunca se tocan."""
    active = [r[0] for r in conn.execute(
        "SELECT steam_appid FROM games WHERE origin='discover' AND active=1 AND steam_appid IS NOT NULL")]
    gone = [a for a in active if a not in seen_appids]
    conn.executemany("UPDATE games SET active=0 WHERE steam_appid=?", [(a,) for a in gone])
    return len(gone)


def last_tries(conn: sqlite3.Connection, source: str) -> dict[int, str]:
    """Último intento (exitoso o no) de cada juego en una fuente."""
    return {r[0]: r[1] for r in conn.execute(
        "SELECT game_id, last_try FROM source_status WHERE source=?", (source,))}


# ---------------------------------------------------------------- ejecuciones

def start_run(conn, mode: str, source: str, workers: dict) -> int:
    cur = conn.execute("INSERT INTO runs (started_at, mode, source, workers) VALUES (?,?,?,?)",
                       (now(), mode, source, json.dumps(workers)))
    conn.commit()
    return cur.lastrowid


def finish_run(conn, run_id: int, stage_times: dict, total_s: float, request_stats: dict) -> None:
    conn.execute("UPDATE runs SET finished_at=?, stage_times=?, total_s=? WHERE id=?",
                 (now(), json.dumps(stage_times), total_s, run_id))
    for domain, s in request_stats.items():
        conn.execute("INSERT OR REPLACE INTO request_stats VALUES (?,?,?,?,?)",
                     (run_id, domain, s["ok"], s["retried"], s["failed"]))
    conn.commit()
