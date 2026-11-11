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
import threading
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass, field

from . import db, scrapers
from .http_client import FetchError, HttpClient
from .matching import MatchTask, match_game, set_catalog
from .scrapers.base import ParseResult, Request, Scraper

MODES = ("sequential", "concurrent", "parallel")


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

    def discover(self) -> int:
        d = self.s["discover"]
        sd = scrapers.SteamDiscover(d["pages"], d["page_size"])
        jobs = [FetchJob(r, sd, None) for r in sd.page_requests()]
        results, failures = self._fetch_and_parse(jobs, self.workers["discover"])
        count = 0
        for res in results:
            for g in res.games:
                db.upsert_game(self.conn, g)
                count += 1
        self.conn.commit()
        for f in failures:
            print(f"  [discover] falló {f.job.req.url}: {f.error}")
        return count

    # ================================================================ etapas 1-2

    def _fetch_one(self, job: FetchJob) -> str:
        return self.http.get(job.req.url, cookies=dict(job.req.cookies) or None)

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
            if not f.retryable:
                continue
            try:
                out.append(self._parse_one(f.job, self._fetch_one(f.job)))
                f.recovered = True
            except Exception as exc:
                f.error = f"{f.error} | final: {exc}"
        return out

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

    def _aggregate(self, run_id, games, results, by_game, matches, failures) -> tuple[int, int]:
        ts = db.now()
        c = self.conn
        n_offers = n_flags = 0
        games_by_id = {g["id"]: g for g in games}

        for mr in matches:
            offers = by_game[mr.game_id]
            platforms = set(games_by_id[mr.game_id]["platforms"])
            for idx, sc in mr.accepted:
                o = offers[idx]
                c.execute("""INSERT INTO price_observations (run_id, game_id, store, title_seen, price,
                             regular_price, currency, format, platform, url, match_score, observed_at)
                             VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (run_id, mr.game_id, o.store, o.title, o.price, o.regular_price, o.currency,
                           o.format, o.platform, o.url, round(sc, 4), ts))
                if o.platform:
                    platforms.add(o.platform)
                n_offers += 1
            if platforms != set(games_by_id[mr.game_id]["platforms"]):
                db.upsert_game(c, {"canonical_title": games_by_id[mr.game_id]["canonical_title"],
                                   "steam_appid": games_by_id[mr.game_id]["steam_appid"],
                                   "platforms": sorted(platforms)})
            for store, cand, sc, reason in mr.flags:
                c.execute("INSERT INTO match_flags (run_id, game_id, store, candidate, score, reason) "
                          "VALUES (?,?,?,?,?,?)", (run_id, mr.game_id, store, cand, round(sc, 4), reason))
                n_flags += 1

        for res in results:
            gid = res.request.game_id
            if gid is None:
                continue
            for r in res.ratings:
                c.execute("INSERT INTO ratings (run_id, game_id, source, score, votes, platform, observed_at) "
                          "VALUES (?,?,?,?,?,?,?)", (run_id, gid, r.source, r.score, r.votes, r.platform, ts))
            if res.duration:
                d = res.duration
                c.execute("INSERT INTO durations (run_id, game_id, main_h, plus_h, comp_h, observed_at) "
                          "VALUES (?,?,?,?,?,?)", (run_id, gid, d.main_h, d.plus_h, d.comp_h, ts))
            c.execute("""INSERT INTO source_status (game_id, source, last_ok, last_try, ok) VALUES (?,?,?,?,1)
                         ON CONFLICT(game_id, source) DO UPDATE SET last_ok=excluded.last_ok,
                         last_try=excluded.last_try, ok=1""", (gid, res.request.source, ts, ts))

        for f in failures:
            gid = f.job.req.game_id
            c.execute("INSERT INTO failures (run_id, source, game_id, url, error, recovered) VALUES (?,?,?,?,?,?)",
                      (run_id, f.job.req.source, gid, f.job.req.url, f.error, int(f.recovered)))
            if gid is not None and not f.recovered:
                c.execute("""INSERT INTO source_status (game_id, source, last_ok, last_try, ok) VALUES (?,?,NULL,?,0)
                             ON CONFLICT(game_id, source) DO UPDATE SET last_try=excluded.last_try, ok=0""",
                          (gid, f.job.req.source, ts))
        c.commit()
        return n_offers, n_flags

    # ================================================================ ejecución completa

    def run(self, with_discover: bool = False, limit: int | None = None, publish: bool = True) -> RunReport:
        t0 = time.perf_counter()
        run_id = db.start_run(self.conn, self.mode, self.s["source"], self.workers)
        rep = RunReport(run_id, self.mode)
        st = rep.stage_times

        if with_discover:
            t = time.perf_counter()
            self.discover()
            st["discover"] = time.perf_counter() - t

        games = db.load_games(self.conn, limit)
        active = scrapers.build(self.s["sources"])
        jobs = [FetchJob(r, sc, g) for g in games for sc in active.values() for r in sc.requests_for(g)]
        rep.requests = len(jobs)

        t = time.perf_counter()
        results, failures = self._fetch_and_parse(jobs, self.workers["fetch"])
        st["fetch_parse"] = time.perf_counter() - t

        t = time.perf_counter()
        results += self._retry_failed(failures)
        st["retry_failed"] = time.perf_counter() - t

        by_game: dict[int, list] = {}
        for res in results:
            if res.offers and res.request.game_id is not None:
                by_game.setdefault(res.request.game_id, []).extend(res.offers)

        t = time.perf_counter()
        matches = self._match(games, by_game)
        st["match"] = time.perf_counter() - t

        t = time.perf_counter()
        rep.matched_offers, rep.flags = self._aggregate(run_id, games, results, by_game, matches, failures)
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
