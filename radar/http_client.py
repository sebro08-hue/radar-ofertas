"""Cliente HTTP compartido por todas las etapas de E/S.

- Timeout en cada solicitud.
- Reintentos con backoff exponencial y jitter ("full jitter"), con máximo definido.
- Semáforo por dominio: limita conexiones simultáneas a cada sitio.
- Sesión de `requests` por hilo (threading.local): las sesiones no son seguras entre hilos.
- Estadísticas por dominio protegidas por lock (ok / reintentadas / fallidas).
- Cortacircuitos por dominio: tras N fallos seguidos (bloqueos 403/429/503 o
  reintentos agotados), el dominio se da por caído el resto de la ejecución y las
  solicitudes siguientes fallan al instante sin tocar el sitio.
- Modo live (con captura y caché local) o modo replay (servidor local).
"""
from __future__ import annotations

import random
import threading
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

import requests

from . import replay

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
BLOCK_STATUS = {401, 403, 412}   # el sitio rechaza al cliente


def _declared_encoding(resp: requests.Response) -> str:
    """Charset declarado por el servidor, o UTF-8. No se usa `resp.text`: sin charset
    declarado, `requests` adivina la codificación analizando todo el cuerpo (~240 ms
    por página de 500 KB, CPU pura dentro de la etapa de E/S)."""
    declared = "charset=" in resp.headers.get("Content-Type", "").lower()
    return resp.encoding if declared and resp.encoding else "utf-8"


def _decode(content: bytes, encoding: str) -> str:
    try:
        return content.decode(encoding, errors="replace")
    except LookupError:                     # charset desconocido
        return content.decode("utf-8", errors="replace")


class FetchError(Exception):
    def __init__(self, url: str, reason: str, retryable: bool = True):
        super().__init__(f"{reason}: {url}")
        self.url = url
        self.reason = reason
        self.retryable = retryable


class HttpClient:
    def __init__(self, settings):
        self.s = settings
        self.source = settings["source"]
        if self.source == "replay" and not replay.is_loopback(settings["replay"]["host"]):
            raise ValueError("el servidor de replay debe escuchar en la propia máquina "
                             f"(127.0.0.1, ::1 o localhost), no en {settings['replay']['host']!r}")
        self.replay_dir: Path = settings.path("replay_dir")
        self.capture = bool(settings["capture"]) and self.source == "live"
        self.cache_ttl_s = float(settings["cache_ttl_hours"]) * 3600
        http = settings["http"]
        self.timeout = http["timeout_s"]
        self.max_retries = http["max_retries"]
        self.backoff_base = http["backoff_base_s"]
        self.backoff_max = http["backoff_max_s"]
        # Con valor cero el cortacircuitos queda desactivado.
        self.breaker_threshold = int(http.get("circuit_breaker_failures", 0))

        self._breaker_lock = threading.Lock()
        self._consecutive: dict[str, int] = defaultdict(int)
        self._open: set[str] = set()

        self._local = threading.local()
        self._sem_lock = threading.Lock()
        self._sems: dict[str, threading.BoundedSemaphore] = {}
        # Pausa mínima por dominio: un lock por dominio + el instante en que se permite
        # la próxima solicitud. Quien tiene el lock duerme lo necesario y corre la marca.
        self._pace_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._next_allowed: dict[str, float] = defaultdict(float)
        self._stats_lock = threading.Lock()
        self.stats: dict[str, dict[str, int]] = defaultdict(lambda: {"ok": 0, "retried": 0, "failed": 0})

    # ------------------------------------------------------------- utilidades

    def _session(self) -> requests.Session:
        sess = getattr(self._local, "session", None)
        if sess is None:
            sess = requests.Session()
            sess.headers.update({"User-Agent": self.s["user_agent"],
                                 "Accept-Language": "en-US,en;q=0.9"})
            self._local.session = sess
        return sess

    def _semaphore(self, key: str) -> threading.BoundedSemaphore:
        with self._sem_lock:
            sem = self._sems.get(key)
            if sem is None:
                sem = threading.BoundedSemaphore(self.s.domain_limit(key))
                self._sems[key] = sem
            return sem

    def _pace(self, domain: str) -> None:
        """Espera hasta que hayan pasado `per_domain_interval_s` desde la solicitud
        anterior al mismo dominio. Solo en modo live."""
        interval = self.s.domain_interval(domain) if self.source == "live" else 0.0
        if interval <= 0:
            return
        with self._sem_lock:                       # crear el lock del dominio sin carreras
            lock = self._pace_locks[domain]
        with lock:
            wait = self._next_allowed[domain] - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._next_allowed[domain] = time.monotonic() + interval

    def _count(self, domain: str, field: str) -> None:
        with self._stats_lock:
            self.stats[domain][field] += 1

    def _on_success(self, domain: str) -> None:
        with self._breaker_lock:
            self._consecutive[domain] = 0

    def _on_failure(self, domain: str) -> None:
        if not self.breaker_threshold:
            return
        with self._breaker_lock:
            self._consecutive[domain] += 1
            if self._consecutive[domain] >= self.breaker_threshold and domain not in self._open:
                self._open.add(domain)
                print(f"  [cortacircuitos] {domain}: {self._consecutive[domain]} fallos seguidos, "
                      f"se deja de consultar en esta ejecución")

    def is_open(self, url_or_domain: str) -> bool:
        domain = urlsplit(url_or_domain).hostname or url_or_domain
        with self._breaker_lock:
            return domain in self._open

    def open_domains(self) -> list[str]:
        with self._breaker_lock:
            return sorted(self._open)

    def _sleep_backoff(self, attempt: int) -> None:
        cap = min(self.backoff_max, self.backoff_base * (2 ** attempt))
        time.sleep(random.uniform(0, cap))

    def _cached(self, domain: str, url: str) -> bytes | None:
        if self.source != "live" or self.cache_ttl_s <= 0:
            return None
        path = replay.capture_path(self.replay_dir, domain, url)
        if path.is_file() and time.time() - path.stat().st_mtime < self.cache_ttl_s:
            return path.read_bytes()
        return None

    # ------------------------------------------------------------- API

    def get(self, url: str, cookies: dict | None = None) -> str:
        """Descarga y decodifica como texto."""
        return _decode(*self._fetch(url, cookies))

    def get_bytes(self, url: str, cookies: dict | None = None) -> bytes:
        """Descarga sin decodificar (archivos comprimidos, imágenes...)."""
        return self._fetch(url, cookies)[0]

    def _fetch(self, url: str, cookies: dict | None) -> tuple[bytes, str]:
        """Devuelve (contenido, codificación declarada). Reintenta con backoff los
        errores de red y los estados reintentables; el resto falla de inmediato."""
        domain = urlsplit(url).hostname or "?"
        cached = self._before_request(domain, url)
        if cached is not None:
            return cached, "utf-8"
        target, sem = self._route(domain, url)

        last_reason = "?"
        for attempt in range(self.max_retries + 1):
            if attempt:
                if self.is_open(domain):
                    break                   # otro hilo ya abrió el circuito: no seguir insistiendo
                self._count(domain, "retried")
                self._sleep_backoff(attempt)
            resp, last_reason = self._send(domain, target, sem, cookies)
            result = self._handle(domain, url, resp) if resp is not None else None
            if result is not None:
                return result
        self._count(domain, "failed")
        self._on_failure(domain)
        raise FetchError(url, last_reason, retryable=True)

    def _before_request(self, domain: str, url: str) -> bytes | None:
        """Corta si el circuito del dominio está abierto; devuelve la copia en caché si la hay."""
        if self.is_open(domain):
            self._count(domain, "failed")
            raise FetchError(url, "circuito abierto", retryable=False)
        cached = self._cached(domain, url)
        if cached is not None:
            self._count(domain, "ok")
        return cached

    def _route(self, domain: str, url: str) -> tuple[str, threading.BoundedSemaphore]:
        """URL a pedir y semáforo a respetar: el sitio real o el servidor de replay."""
        if self.source != "replay":
            return url, self._semaphore(domain)
        r = self.s["replay"]
        # El servidor de replay solo escucha en la propia máquina (se valida en __init__),
        # así que este tráfico nunca sale a la red y HTTPS no aportaría nada.
        target = f"http://{r['host']}:{r['port']}/r/{domain}/{replay.key_for(url)}"  # NOSONAR
        return target, self._semaphore(r["host"])

    def _send(self, domain: str, target: str, sem, cookies) -> tuple[requests.Response | None, str]:
        """Un intento. Devuelve (respuesta, motivo) o (None, nombre del error de red)."""
        try:
            with sem:
                self._pace(domain)
                resp = self._session().get(target, cookies=cookies, timeout=self.timeout)
        except requests.RequestException as exc:
            return None, type(exc).__name__
        return resp, f"HTTP {resp.status_code}"

    def _handle(self, domain: str, url: str, resp: requests.Response) -> tuple[bytes, str] | None:
        """200: devuelve el contenido. Estado reintentable: None. Otro: falla sin reintentar."""
        if resp.status_code == 200:
            self._count(domain, "ok")
            self._on_success(domain)
            if self.capture:
                replay.save_capture(self.replay_dir, domain, url, resp.content)
            return resp.content, _declared_encoding(resp)
        if resp.status_code in RETRYABLE_STATUS:
            return None
        # 403/404/412...: el sitio nos rechaza o no existe. No se insiste ni se intenta evadirlo.
        self._count(domain, "failed")
        if resp.status_code in BLOCK_STATUS:
            self._on_failure(domain)
        else:
            self._on_success(domain)       # un 404 es una respuesta normal, no un bloqueo
        raise FetchError(url, f"HTTP {resp.status_code}", retryable=False)

    def snapshot_stats(self) -> dict:
        with self._stats_lock:
            return {d: dict(v) for d, v in self.stats.items()}
