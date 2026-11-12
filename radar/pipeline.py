"""Pipeline completo con tres modos de ejecución sobre el mismo código.

    etapa 0  discover   listado paginado  -> repositorio de juegos          (E/S)
    etapa 1  fetch      descarga de cada (juego, fuente)                    (E/S)
    etapa 2  parse      HTML/JSON -> ofertas, calificaciones, duraciones     (CPU liviana)
    etapa 3  match      emparejamiento de títulos                           (CPU pesada)
    etapa 4  aggregate  escribe en la base (solo hilo principal)
    etapa 5  publish    exporta JSON para el sitio estático

Modos:
    sequential  todo en un hilo, una solicitud a la vez.
    concurrent  discover y fetch con un pool de hilos; fetch -> parse por una cola
                acotada (queue.Queue(maxsize)); match secuencial.
    parallel    igual que concurrent + match repartido entre núcleos
                (ProcessPoolExecutor, o ThreadPoolExecutor para comparar el GIL).
"""
from __future__ import annotations

import queue
import re
import threading
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass, field, replace

from . import db, scrapers
from .http_client import FetchError, HttpClient
from .matching import MatchTask, match_game, set_catalog
from .scrapers.base import ParseResult, Request, Scraper

MODES = ("sequential", "concurrent", "parallel")
BLOCK_ERRORS = ("HTTP 403", "HTTP 503", "circuito abierto")
NOT_FOUND = ("HTTP 404", "HTTP 410")


@dataclass
class Collected:
    """Resultados ya asignados a cada juego del catálogo."""
    offers: dict[int, list] = field(default_factory=dict)          # game_id -> [Offer]
    ratings: list[tuple[int, object]] = field(default_factory=list)
    durations: list[tuple[int, object]] = field(default_factory=list)
    ok: set[tuple[int, str]] = field(default_factory=set)            # (game_id, fuente) con datos frescos
    game_updates: dict[int, dict] = field(default_factory=dict)      # game_id -> campos a guardar


def _appids_in(url: str) -> list[int]:
    m = re.search(r"appids=([\d,]+)", url)
    return [int(x) for x in m.group(1).split(",")] if m else []


def pick_rotation(games: list[dict], last_try: dict[int, str], n: int) -> list[dict]:
    """Los n juegos con el último intento más antiguo; los nunca intentados van primero.
    Empata por id para que el orden sea estable entre ejecuciones."""
    return sorted(games, key=lambda g: (last_try.get(g["id"], ""), g["id"]))[:n]


def _collect_single(col: Collected, res: ParseResult) -> None:
    """Resultado de una solicitud hecha para un juego concreto."""
    gid = res.request.game_id
    col.offers.setdefault(gid, []).extend(res.offers)
    col.ratings += [(gid, r) for r in res.ratings]
    if res.duration:
        col.durations.append((gid, res.duration))
    col.ok.add((gid, res.request.source))
    if res.game_updates:
        col.game_updates.setdefault(gid, {}).update(res.game_updates)


def _collect_batch(col: Collected, res: ParseResult, by_appid: dict[int, dict]) -> None:
    """Resultado de una solicitud por lote: cada oferta/calificación trae su steam_appid."""
    for o in res.offers:
        g = by_appid.get(o.steam_appid)
        if g:
            o.title = o.title or g["canonical_title"]     # id exacto: el título es el del catálogo
            col.offers.setdefault(g["id"], []).append(o)
    col.ratings += [(by_appid[r.steam_appid]["id"], r) for r in res.ratings if r.steam_appid in by_appid]
    col.ok |= {(by_appid[a]["id"], res.request.source) for a in _appids_in(res.request.url) if a in by_appid}


@dataclass
class FetchJob:
    req: Request
    scraper: Scraper
    game: dict | None


@dataclass
class Failure:
    job: FetchJob
    error: str
    retryable: bool
    recovered: bool = False


@dataclass
class RunReport:
    run_id: int
    mode: str
    stage_times: dict = field(default_factory=dict)
    total_s: float = 0.0
    requests: int = 0
    failures: int = 0
    recovered: int = 0
    matched_offers: int = 0
    flags: int = 0


class Pipeline:
    def __init__(self, settings, conn=None):
        self.s = settings
        self.mode = settings["mode"]
        if self.mode not in MODES:
            raise ValueError(f"modo desconocido: {self.mode}")
        self.conn = conn or db.connect(settings.path("db_path"))
        self.http = HttpClient(settings)
        self.workers = dict(settings["workers"])

    # ================================================================ etapa 0

    def discover(self) -> tuple[int, list[ParseResult]]:
        """Devuelve (juegos leídos, resultados). Los resultados traen además las
        calificaciones de usuarios de Steam, que run() aprovecha."""
        d = self.s["discover"]
        sd = scrapers.SteamDiscover(d["pages"], d["page_size"])
        jobs = [FetchJob(r, sd, None) for r in sd.page_requests()]
        results, failures = self._fetch_and_parse(jobs, self.workers["discover"])
        seen: set[int] = set()
        for res in results:
            for g in res.games:
                db.upsert_game(self.conn, g)
                seen.add(g["steam_appid"])
        count = len(seen)
        # Solo se retiran juegos si se leyó el listado completo: con páginas
        # faltantes, un juego "ausente" puede estar en la página que falló.
        if not failures and count:
            retired = db.retire_missing(self.conn, seen)
            if retired:
                print(f"  [discover] {retired} juegos ya no están en el listado y salen del catálogo")
        self.conn.commit()
        for f in failures:
            print(f"  [discover] falló {f.job.req.url}: {f.error}")
        return count, results

    # ================================================================ etapas 1-2

    def _fetch_one(self, job: FetchJob) -> str:
        """Descarga la URL de la solicitud; si responde 404/410, prueba sus alternativas
        en orden. La que funciona queda en job.req para que el parser sepa cuál fue.
        Cada job lo maneja un solo hilo, así que modificarlo aquí es seguro."""
        cookies = dict(job.req.cookies) or None
        urls = (job.req.url, *job.req.alternates)
        for i, url in enumerate(urls):
            try:
                body = self.http.get(url, cookies=cookies)
            except FetchError as exc:
                if i < len(urls) - 1 and exc.reason in NOT_FOUND:
                    continue
                raise
            if i:
                job.req = replace(job.req, url=url, alternates=())
            return body
        raise AssertionError("inalcanzable")

    def _parse_one(self, job: FetchJob, body: str) -> ParseResult:
        return job.scraper.parse(job.game, job.req, body)

    def _fetch_and_parse(self, jobs: list[FetchJob], n_workers: int):
        results: list[ParseResult] = []
        failures: list[Failure] = []

        if self.mode == "sequential":
            for job in jobs:
                try:
                    results.append(self._parse_one(job, self._fetch_one(job)))
                except FetchError as exc:
                    failures.append(Failure(job, exc.reason, exc.retryable))
                except Exception as exc:  # error de parseo: el sitio cambió de estructura
                    failures.append(Failure(job, f"parse: {exc!r}", False))
            return results, failures

        # concurrent / parallel: workers de E/S -> cola acotada -> parser (hilo principal)
        fetched: queue.Queue = queue.Queue(maxsize=self.s["queues"]["fetched"])
        DONE = object()

        def worker(job: FetchJob) -> None:
            try:
                fetched.put((job, self._fetch_one(job), None))  # bloquea si la cola está llena
            except FetchError as exc:
                fetched.put((job, None, exc))

        def producer() -> None:
            with ThreadPoolExecutor(max_workers=n_workers, thread_name_prefix="fetch") as pool:
                for job in jobs:
                    pool.submit(worker, job)
            fetched.put(DONE)

        threading.Thread(target=producer, name="fetch-producer", daemon=True).start()
        while True:
            item = fetched.get()
            if item is DONE:
                break
            job, body, exc = item
            if exc is not None:
                failures.append(Failure(job, exc.reason, exc.retryable))
                continue
            try:
                results.append(self._parse_one(job, body))
            except Exception as e:
                failures.append(Failure(job, f"parse: {e!r}", False))
        return results, failures

    def _retry_failed(self, failures: list[Failure]) -> list[ParseResult]:
        """Cola de fallidos: un último intento, en serie, al final de la ejecución."""
        out = []
        for f in failures:
            # No se insiste con un sitio que ya nos bloqueó (403/503) ni con uno cuyo
            # cortacircuitos está abierto: solo alarga la corrida y molesta al sitio.
            if not f.retryable or self.http.is_open(f.job.req.url) or \
                    any(code in f.error for code in BLOCK_ERRORS):
                continue
            try:
                out.append(self._parse_one(f.job, self._fetch_one(f.job)))
                f.recovered = True
            except Exception as exc:
                f.error = f"{f.error} | final: {exc}"
        return out

    def _games_for(self, source: str, games: list[dict]) -> list[dict]:
        """Juegos a consultar en una fuente. Con rotación, los N consultados hace más
        tiempo (los nunca consultados primero); sin rotación, todos."""
        n = self.s.rotation_size(source)
        if not n or n >= len(games):
            return games
        return pick_rotation(games, db.last_tries(self.conn, source), n)

    @staticmethod
    def _collect(games: list[dict], results: list[ParseResult]) -> Collected:
        """Asigna cada resultado a su juego: por game_id de la solicitud o, en las
        solicitudes por lote (game_id=None), por steam_appid."""
        by_appid = {g["steam_appid"]: g for g in games if g.get("steam_appid")}
        col = Collected()
        for res in results:
            if res.request.game_id is not None:
                _collect_single(col, res)
            else:
                _collect_batch(col, res, by_appid)
        return col

    # ================================================================ etapa 3

    def _match(self, games: list[dict], by_game: dict[int, list]) -> list:
        m = self.s["matching"]
        tasks = []
        for g in games:
            offers = by_game.get(g["id"], [])
            if not offers:
                continue
            tasks.append(MatchTask(
                game_id=g["id"],
                names=[g["canonical_title"], *g["alt_titles"]],
                candidates=[(o.store, o.title, i, o.platform or "") for i, o in enumerate(offers)],
                use_catalog=bool(m["compare_against_catalog"]),
                threshold=m["threshold"], margin=m["doubtful_margin"]))
        catalog = [g["canonical_title"] for g in games]

        if self.mode != "parallel":
            set_catalog(catalog)
            return [match_game(t) for t in tasks]

        n = self.workers["match"]
        if self.s["match_executor"] == "thread":
            set_catalog(catalog)
            with ThreadPoolExecutor(max_workers=n, thread_name_prefix="match") as pool:
                return list(pool.map(match_game, tasks))
        chunk = max(1, len(tasks) // (n * 4))
        with ProcessPoolExecutor(max_workers=n, initializer=set_catalog, initargs=(catalog,)) as pool:
            return list(pool.map(match_game, tasks, chunksize=chunk))

    # ================================================================ etapa 4

    def _aggregate(self, run_id, games, col: Collected, matches, failures) -> tuple[int, int]:
        ts = db.now()
        games_by_id = {g["id"]: g for g in games}
        n_offers = n_flags = 0
        for mr in matches:
            offers, flags = self._save_match(run_id, ts, games_by_id[mr.game_id], col.offers[mr.game_id], mr)
            n_offers += offers
            n_flags += flags
        self._save_source_data(run_id, ts, col)
        by_appid = {g["steam_appid"]: g["id"] for g in games if g.get("steam_appid")}
        for f in failures:
            self._save_failure(run_id, ts, f, by_appid)
        self.conn.commit()
        return n_offers, n_flags

    def _save_match(self, run_id, ts, game: dict, offers: list, mr) -> tuple[int, int]:
        """Guarda las ofertas aceptadas y las banderas de un juego; suma plataformas nuevas."""
        c = self.conn
        platforms = set(game["platforms"])
        for idx, sc in mr.accepted:
            o = offers[idx]
            c.execute("""INSERT INTO price_observations (run_id, game_id, store, title_seen, price,
                         regular_price, currency, format, platform, url, match_score, observed_at)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (run_id, mr.game_id, o.store, o.title, o.price, o.regular_price, o.currency,
                       o.format, o.platform, o.url, round(sc, 4), ts))
            if o.platform:
                platforms.add(o.platform)
        if platforms != set(game["platforms"]):
            db.upsert_game(c, {"canonical_title": game["canonical_title"], "steam_appid": game["steam_appid"],
                               "platforms": sorted(platforms)})
        c.executemany("INSERT INTO match_flags (run_id, game_id, store, candidate, score, reason) VALUES (?,?,?,?,?,?)",
                      [(run_id, mr.game_id, store, cand, round(sc, 4), reason) for store, cand, sc, reason in mr.flags])
        return len(mr.accepted), len(mr.flags)

    def _save_source_data(self, run_id, ts, col: Collected) -> None:
        """Calificaciones, duraciones y estado 'al día' de cada (juego, fuente)."""
        c = self.conn
        c.executemany("INSERT INTO ratings (run_id, game_id, source, score, votes, platform, observed_at) "
                      "VALUES (?,?,?,?,?,?,?)",
                      [(run_id, gid, r.source, r.score, r.votes, r.platform, ts) for gid, r in col.ratings])
        c.executemany("INSERT INTO durations (run_id, game_id, main_h, plus_h, comp_h, observed_at) "
                      "VALUES (?,?,?,?,?,?)",
                      [(run_id, gid, d.main_h, d.plus_h, d.comp_h, ts) for gid, d in col.durations])
        c.executemany("""INSERT INTO source_status (game_id, source, last_ok, last_try, ok) VALUES (?,?,?,?,1)
                         ON CONFLICT(game_id, source) DO UPDATE SET last_ok=excluded.last_ok,
                         last_try=excluded.last_try, ok=1""", [(gid, src, ts, ts) for gid, src in col.ok])
        for gid, fields in col.game_updates.items():
            db.update_game_fields(c, gid, fields)

    def _save_failure(self, run_id, ts, f: Failure, by_appid: dict[int, int]) -> None:
        """Registra el fallo y, si no se recuperó, marca la fuente como desactualizada."""
        req = f.job.req
        self.conn.execute("INSERT INTO failures (run_id, source, game_id, url, error, recovered) VALUES (?,?,?,?,?,?)",
                          (run_id, req.source, req.game_id, req.url, f.error, int(f.recovered)))
        if f.recovered:
            return
        # Una solicitud por lote que falla deja desactualizados a todos los juegos del lote.
        if req.game_id is not None:
            stale = [req.game_id]
        else:
            stale = [by_appid[a] for a in _appids_in(req.url) if a in by_appid]
        self.conn.executemany("""INSERT INTO source_status (game_id, source, last_ok, last_try, ok) VALUES (?,?,NULL,?,0)
                                 ON CONFLICT(game_id, source) DO UPDATE SET last_try=excluded.last_try, ok=0""",
                              [(g, req.source, ts) for g in stale])

    # ================================================================ ejecución completa

    def run(self, with_discover: bool = False, limit: int | None = None, publish: bool = True) -> RunReport:
        t0 = time.perf_counter()
        run_id = db.start_run(self.conn, self.mode, self.s["source"], self.workers)
        rep = RunReport(run_id, self.mode)
        st = rep.stage_times

        discovered: list[ParseResult] = []
        if with_discover:
            t = time.perf_counter()
            _, discovered = self.discover()
            st["discover"] = time.perf_counter() - t

        games = db.load_games(self.conn, limit)
        active = scrapers.build(self.s["sources"])
        jobs = [FetchJob(r, sc, None) for sc in active.values() for r in sc.batch_requests(games)]
        for name, sc in active.items():
            for g in self._games_for(name, games):
                jobs += [FetchJob(r, sc, g) for r in sc.requests_for(g)]
        rep.requests = len(jobs)

        t = time.perf_counter()
        results, failures = self._fetch_and_parse(jobs, self.workers["fetch"])
        st["fetch_parse"] = time.perf_counter() - t

        t = time.perf_counter()
        results += self._retry_failed(failures)
        st["retry_failed"] = time.perf_counter() - t

        # Las páginas del descubrimiento solo aportan calificaciones (las ofertas salen del lote).
        col = self._collect(games, results + [ParseResult(r.request, ratings=r.ratings) for r in discovered])

        t = time.perf_counter()
        matches = self._match(games, col.offers)
        st["match"] = time.perf_counter() - t

        t = time.perf_counter()
        rep.matched_offers, rep.flags = self._aggregate(run_id, games, col, matches, failures)
        st["aggregate"] = time.perf_counter() - t

        rep.failures = sum(1 for f in failures if not f.recovered)
        rep.recovered = sum(1 for f in failures if f.recovered)
        rep.total_s = time.perf_counter() - t0
        db.finish_run(self.conn, run_id, st, rep.total_s, self.http.snapshot_stats())

        if publish:   # se publica después de cerrar la ejecución para que el panel la incluya
            from . import site
            t = time.perf_counter()
            site.build(self.conn, self.s)
            st["publish"] = time.perf_counter() - t
            rep.total_s = time.perf_counter() - t0
            db.finish_run(self.conn, run_id, st, rep.total_s, {})
        return rep
