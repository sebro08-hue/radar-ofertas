"""Descarga con asyncio: la misma etapa de E/S que los workers de hilos, con otro mecanismo.

Con hilos, cada descarga en curso ocupa un hilo del sistema operativo que pasa casi todo
el tiempo bloqueado esperando la red. Con asyncio hay un solo hilo y un bucle de eventos:
cada descarga es una corrutina que cede el control mientras espera, y el bucle atiende a
la que tenga datos listos. «p workers» pasa a ser «p descargas en vuelo a la vez»
(un asyncio.Semaphore).

Solo para el modo replay: el proyecto usa la biblioteca estándar más `requests`, que es
bloqueante, y un cliente HTTPS asíncrono completo (redirecciones, gzip, chunked, cookies)
para los sitios reales no está en la biblioteca estándar. El servidor de replay habla
HTTP/1.0 simple por la propia máquina, así que basta con asyncio.open_connection.
El cliente respeta lo mismo que HttpClient en replay: el límite por dominio, las URL
alternativas ante 404 y las estadísticas por sitio que muestra el panel de métricas.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from urllib.parse import urlsplit

from . import replay
from .http_client import FetchError

NOT_FOUND = ("HTTP 404", "HTTP 410")


class AsyncReplayFetcher:
    def __init__(self, http, settings, n_workers: int):
        if settings["source"] != "replay":
            raise ValueError("la descarga con asyncio solo está disponible en modo replay")
        self.http = http                       # para las estadísticas y el cortacircuitos
        r = settings["replay"]
        self.host, self.port = r["host"], int(r["port"])
        self.timeout = float(settings["http"]["timeout_s"])
        self.n_workers = n_workers
        self.domain_limit = settings.domain_limit(self.host)

    async def _get(self, url: str) -> str:
        """Una solicitud al servidor de replay, como HttpClient._route la arma."""
        domain = urlsplit(url).hostname or "?"
        path = f"/r/{domain}/{replay.key_for(url)}"
        async with self._domain_sem:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(self.host, self.port), self.timeout)
            try:
                writer.write(f"GET {path} HTTP/1.0\r\nHost: {self.host}\r\n\r\n".encode("ascii"))
                await writer.drain()
                raw = await asyncio.wait_for(reader.read(), self.timeout)   # HTTP/1.0: hasta que cierra
            finally:
                writer.close()
        head, _, body = raw.partition(b"\r\n\r\n")
        status = int(head.split(b" ", 2)[1]) if head.startswith(b"HTTP/") else 0
        if status == 200:
            self.http._count(domain, "ok")
            self.http._on_success(domain)
            return body.decode("utf-8", errors="replace")
        self.http._count(domain, "failed")
        self.http._on_failure(domain)
        raise FetchError(url, f"HTTP {status}", retryable=False)

    async def _fetch_one(self, job):
        """Como Pipeline._fetch_one: si responde 404/410, prueba las URL alternativas."""
        urls = (job.req.url, *job.req.alternates)
        async with self._workers:
            for i, url in enumerate(urls):
                try:
                    body = await self._get(url)
                except FetchError as exc:
                    if i < len(urls) - 1 and exc.reason in NOT_FOUND:
                        continue
                    return job, None, exc
                except (OSError, asyncio.TimeoutError) as exc:
                    return job, None, FetchError(url, f"red: {exc!r}", retryable=True)
                if i:
                    job.req = replace(job.req, url=url, alternates=())
                return job, body, None
        raise AssertionError("inalcanzable")

    async def _run(self, jobs, on_result) -> None:
        # Los semáforos se crean dentro del bucle de eventos que los usa.
        self._workers = asyncio.Semaphore(self.n_workers)
        self._domain_sem = asyncio.Semaphore(self.domain_limit)
        for fut in asyncio.as_completed([self._fetch_one(j) for j in jobs]):
            on_result(*(await fut))           # el parser corre en este mismo hilo, como el consumidor de la cola

    def fetch_all(self, jobs, on_result) -> None:
        """Descarga todas las solicitudes y llama on_result(job, cuerpo, error) para cada una
        a medida que terminan."""
        asyncio.run(self._run(jobs, on_result))
