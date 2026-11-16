"""Etapa 5: exporta la base a JSON y copia el sitio estático a site/dist.

El sitio (site/static) es HTML + JS sin servidor: lee data/*.json y hace filtros,
orden, paginación, detalle e historial en el navegador. Así se puede publicar en
GitHub Pages sin backend.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from .config import ROOT

STATIC = ROOT / "site" / "static"


# Una oferta que la tienda no volvió a confirmar en este tiempo ya no se muestra.
MAX_OFFER_AGE_DAYS = 14


def _latest_prices(conn) -> dict[int, list[dict]]:
    """Ofertas vigentes de cada juego: de cada tienda, solo las de la última ejecución
    que la consultó para ese juego (si un emparejamiento cambió, la oferta vieja no
    queda colgada) y de los últimos MAX_OFFER_AGE_DAYS días. Precio > 0 siempre."""
    rows = conn.execute("""
        SELECT p.* FROM price_observations p
        JOIN (SELECT game_id, store, MAX(run_id) AS r FROM price_observations
              WHERE price > 0 GROUP BY game_id, store) last
          ON last.game_id = p.game_id AND last.store = p.store AND last.r = p.run_id
        WHERE p.price > 0 AND p.observed_at >= strftime('%Y-%m-%dT%H:%M:%S', 'now', ?)
          AND p.id IN (SELECT MAX(id) FROM price_observations GROUP BY game_id, store, url)
        ORDER BY p.game_id, p.price""", (f"-{MAX_OFFER_AGE_DAYS} days",)).fetchall()
    out: dict[int, list[dict]] = {}
    for r in rows:
        out.setdefault(r["game_id"], []).append({
            "store": r["store"], "title": r["title_seen"], "price": r["price"],
            "regular": r["regular_price"], "currency": r["currency"], "format": r["format"],
            "platform": r["platform"], "url": r["url"], "observed_at": r["observed_at"]})
    return out


def _min_history(conn) -> dict[int, float]:
    return {r[0]: r[1] for r in conn.execute(
        "SELECT game_id, MIN(price) FROM price_observations WHERE currency='USD' AND price > 0 GROUP BY game_id")}


def _latest_by(conn, table: str, cols: str) -> dict[int, list[dict]]:
    rows = conn.execute(f"""SELECT t.* FROM {table} t JOIN (SELECT game_id, {cols}, MAX(id) mid
                            FROM {table} GROUP BY game_id, {cols}) l ON l.mid=t.id""").fetchall()
    out: dict[int, list[dict]] = {}
    for r in rows:
        out.setdefault(r["game_id"], []).append(dict(r))
    return out


RATING_PRIORITY = ("metacritic", "steam_users")    # la que muestra la tarjeta


def _rating(rows: list[dict]) -> tuple[float | None, str | None]:
    """(puntaje, fuente) de la calificación preferida."""
    by_source = {r["source"]: r["score"] for r in rows}
    for source in RATING_PRIORITY:
        if by_source.get(source):
            return by_source[source], source
    return None, None


def _card(g, offers: list[dict], hist_min: float | None, ratings: list[dict], status: list[dict]) -> dict:
    best = min(offers, key=lambda o: o["price"])
    regular = best["regular"] or g["msrp"]
    discount = round(100 * (1 - best["price"] / regular)) if regular and regular > best["price"] else 0
    rating, rating_source = _rating(ratings)
    stores = sorted({o["store"] for o in offers})
    return {
        "id": g["id"], "title": g["canonical_title"], "image": g["image_url"],
        "platforms": json.loads(g["platforms"]), "kind": g["kind"], "alt_titles": json.loads(g["alt_titles"] or "[]"),
        "formats": sorted({o["format"] for o in offers}), "stores": stores, "store_count": len(stores),
        "best_price": best["price"], "best_store": best["store"], "regular": regular,
        "discount_pct": discount, "on_sale": discount > 0,
        "historic_low": best["price"] <= (hist_min if hist_min is not None else best["price"]),
        "rating": rating, "rating_source": rating_source,
        "stale_sources": [s["source"] for s in status if not s["ok"]],
    }


VERSIONED_ASSETS = ("style.css", "app.js")
PAGES = ("index.html", "game.html", "metrics.html")


def _version_assets(dist: Path) -> None:
    """Agrega ?v=<hash del contenido> a style.css y app.js en cada página: cuando cambian,
    el navegador descarga la versión nueva en vez de usar la que tenía guardada."""
    versions = {name: hashlib.sha1((STATIC / name).read_bytes()).hexdigest()[:10] for name in VERSIONED_ASSETS}
    for name in PAGES:
        html = (STATIC / name).read_text(encoding="utf-8")
        for asset, v in versions.items():
            html = html.replace(f'"{asset}"', f'"{asset}?v={v}"')
        (dist / name).write_text(html, encoding="utf-8")


def build(conn, settings) -> Path:
    dist: Path = settings.path("site_dir")
    data_dir = dist / "data"
    if STATIC.is_dir():
        shutil.copytree(STATIC, dist, dirs_exist_ok=True)
        _version_assets(dist)
    (data_dir / "games").mkdir(parents=True, exist_ok=True)

    last_run = conn.execute("SELECT * FROM runs WHERE finished_at IS NOT NULL OR id=(SELECT MAX(id) FROM runs) "
                            "ORDER BY id DESC LIMIT 1").fetchone()
    prices = _latest_prices(conn)
    hist_min = _min_history(conn)
    ratings = _latest_by(conn, "ratings", "source")
    durations = _latest_by(conn, "durations", "source")    # la última de cada fuente
    status = {}
    for r in conn.execute("SELECT * FROM source_status"):
        status.setdefault(r["game_id"], []).append(dict(r))

    cards = []
    for g in conn.execute("SELECT * FROM games WHERE active=1"):
        gid = g["id"]
        offers = [o for o in prices.get(gid, []) if o["currency"] == "USD"]
        if not offers:               # sin ningún precio en USD no hay nada que mostrar
            continue
        card = _card(g, offers, hist_min.get(gid), ratings.get(gid, []), status.get(gid, []))
        cards.append(card)

        history = [dict(r) for r in conn.execute(
            "SELECT store, price, observed_at FROM price_observations WHERE game_id=? AND price > 0 "
            "ORDER BY observed_at", (gid,))]
        dur = _preferred_duration(durations.get(gid, []))
        detail = {**card, "msrp": g["msrp"], "release_date": g["release_date"],
                  "alt_titles": json.loads(g["alt_titles"]), "offers": prices.get(gid, []),
                  "ratings": ratings.get(gid, []), "duration": dur,
                  "history": history, "source_status": status.get(gid, [])}
        (data_dir / "games" / f"{gid}.json").write_text(json.dumps(detail, ensure_ascii=False), encoding="utf-8")

    (data_dir / "cards.json").write_text(json.dumps({
        "updated_at": last_run["finished_at"] or last_run["started_at"] if last_run else None,
        "cards": cards}, ensure_ascii=False), encoding="utf-8")

    _metrics(conn, data_dir, last_run)
    return dist


def _preferred_duration(rows: list[dict]) -> dict | None:
    """How Long To Beat si está; si no, IGDB (respaldo)."""
    by_source = {r["source"]: r for r in rows}
    return by_source.get("hltb") or by_source.get("igdb")


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
    metrics["hard_cases"] = [dict(r) for r in conn.execute(
        "SELECT canonical_title AS game, hard_reason AS reason FROM games "
        "WHERE active=1 AND hard_case=1 ORDER BY canonical_title")]
    # Resultados de los benchmarks (bench/run_sweeps.py y bench/contention.py), si existen.
    for name in ("bench.json", "contention.json"):
        src = ROOT / "data" / name
        if src.is_file():
            shutil.copy(src, data_dir / name)
    (data_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, default=str), encoding="utf-8")
