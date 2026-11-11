"""Etapa 5: exporta la base a JSON y copia el sitio estático a site/dist.

El sitio (site/static) es HTML + JS sin servidor: lee data/*.json y hace filtros,
orden, paginación, detalle e historial en el navegador. Así se puede publicar en
GitHub Pages sin backend.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from .config import ROOT

STATIC = ROOT / "site" / "static"


def _latest_prices(conn) -> dict[int, list[dict]]:
    """Última observación de cada (juego, tienda, url)."""
    rows = conn.execute("""
        SELECT p.* FROM price_observations p
        JOIN (SELECT game_id, store, url, MAX(id) AS mid FROM price_observations
              GROUP BY game_id, store, url) last ON last.mid = p.id
        ORDER BY p.game_id, p.price""").fetchall()
    out: dict[int, list[dict]] = {}
    for r in rows:
        out.setdefault(r["game_id"], []).append({
            "store": r["store"], "title": r["title_seen"], "price": r["price"],
            "regular": r["regular_price"], "currency": r["currency"], "format": r["format"],
            "platform": r["platform"], "url": r["url"], "observed_at": r["observed_at"]})
    return out


def _min_history(conn) -> dict[int, float]:
    return {r[0]: r[1] for r in conn.execute(
        "SELECT game_id, MIN(price) FROM price_observations WHERE currency='USD' GROUP BY game_id")}


def _latest_by(conn, table: str, cols: str) -> dict[int, list[dict]]:
    rows = conn.execute(f"""SELECT t.* FROM {table} t JOIN (SELECT game_id, {cols}, MAX(id) mid
                            FROM {table} GROUP BY game_id, {cols}) l ON l.mid=t.id""").fetchall()
    out: dict[int, list[dict]] = {}
    for r in rows:
        out.setdefault(r["game_id"], []).append(dict(r))
    return out


def build(conn, settings) -> Path:
    dist: Path = settings.path("site_dir")
    data_dir = dist / "data"
    if STATIC.is_dir():
        shutil.copytree(STATIC, dist, dirs_exist_ok=True)
    (data_dir / "games").mkdir(parents=True, exist_ok=True)

    last_run = conn.execute("SELECT * FROM runs WHERE finished_at IS NOT NULL OR id=(SELECT MAX(id) FROM runs) "
                            "ORDER BY id DESC LIMIT 1").fetchone()
    prices = _latest_prices(conn)
    hist_min = _min_history(conn)
    ratings = _latest_by(conn, "ratings", "source")
    durations = _latest_by(conn, "durations", "run_id")
    status = {}
    for r in conn.execute("SELECT * FROM source_status"):
        status.setdefault(r["game_id"], []).append(dict(r))

    cards = []
    for g in conn.execute("SELECT * FROM games WHERE active=1"):
        gid = g["id"]
        offers = [o for o in prices.get(gid, []) if o["currency"] == "USD"]
        if len(offers) < 2:          # sin al menos 2 tiendas no hay nada que comparar
            continue
        best = min(offers, key=lambda o: o["price"])
        regular = best["regular"] or g["msrp"]
        discount = round(100 * (1 - best["price"] / regular)) if regular and regular > best["price"] else 0
        rts = {r["source"]: r["score"] for r in ratings.get(gid, [])}
        stale = [s["source"] for s in status.get(gid, []) if not s["ok"]]
        card = {
            "id": gid, "title": g["canonical_title"], "image": g["image_url"],
            "platforms": json.loads(g["platforms"]), "kind": g["kind"],
            "formats": sorted({o["format"] for o in offers}), "stores": sorted({o["store"] for o in offers}),
            "best_price": best["price"], "best_store": best["store"], "regular": regular,
            "discount_pct": discount, "on_sale": discount > 0,
            "historic_low": best["price"] <= hist_min.get(gid, best["price"]),
            "rating": rts.get("metacritic") or rts.get("steam_users"),
            "stale_sources": stale,
        }
        cards.append(card)

        history = [dict(r) for r in conn.execute(
            "SELECT store, price, observed_at FROM price_observations WHERE game_id=? ORDER BY observed_at", (gid,))]
        dur = durations.get(gid, [])
        detail = {**card, "msrp": g["msrp"], "release_date": g["release_date"],
                  "alt_titles": json.loads(g["alt_titles"]), "offers": prices.get(gid, []),
                  "ratings": ratings.get(gid, []), "duration": dur[-1] if dur else None,
                  "history": history, "source_status": status.get(gid, [])}
        (data_dir / "games" / f"{gid}.json").write_text(json.dumps(detail, ensure_ascii=False), encoding="utf-8")

    (data_dir / "cards.json").write_text(json.dumps({
        "updated_at": last_run["finished_at"] or last_run["started_at"] if last_run else None,
        "cards": cards}, ensure_ascii=False), encoding="utf-8")

    _metrics(conn, data_dir, last_run)
    return dist


def _metrics(conn, data_dir: Path, last_run) -> None:
    metrics = {"run": dict(last_run) if last_run else None, "requests": [], "flags": [], "failures": []}
    if last_run:
        rid = last_run["id"]
        metrics["requests"] = [dict(r) for r in conn.execute("SELECT * FROM request_stats WHERE run_id=?", (rid,))]
        metrics["flags"] = [dict(r) for r in conn.execute("""
            SELECT f.store, f.candidate, f.score, f.reason, g.canonical_title AS game
            FROM match_flags f JOIN games g ON g.id=f.game_id WHERE f.run_id=? ORDER BY f.reason, g.canonical_title
            """, (rid,))]
        metrics["failures"] = [dict(r) for r in conn.execute("SELECT * FROM failures WHERE run_id=?", (rid,))]
    # La curva de speedup la escribe bench/run_sweeps.py en data/bench.json; se respeta si existe.
    (data_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, default=str), encoding="utf-8")
