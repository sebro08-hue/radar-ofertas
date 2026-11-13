"""Captura y reproducción (modo replay).

En modo live con `capture: true`, cada respuesta se guarda en
    data/replay/<dominio>/<sha1(url)>.body   (contenido)
    data/replay/<dominio>/<sha1(url)>.url    (URL original, para depurar)

En modo replay, el cliente HTTP pide  http://host:port/r/<dominio>/<sha1>  a este
servidor, que responde con el archivo guardado después de dormir la latencia simulada.
El pipeline es el mismo en ambos modos; solo cambia de dónde salen los bytes.
"""
from __future__ import annotations

import hashlib
import ipaddress
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def is_loopback(host: str) -> bool:
    """True si el host es la propia máquina. El replay usa HTTP sin cifrar, así que
    solo se permite en la interfaz local."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def key_for(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()


def capture_path(replay_dir: Path, domain: str, url: str) -> Path:
    return replay_dir / domain / f"{key_for(url)}.body"


def save_capture(replay_dir: Path, domain: str, url: str, body: bytes) -> None:
    path = capture_path(replay_dir, domain, url)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(body)
    tmp.replace(path)                      # escritura atómica: ningún lector ve un archivo a medias
    path.with_suffix(".url").write_text(url, encoding="utf-8")


def make_handler(replay_dir: Path, latency_ms: float, jitter_ms: float, seed: int | None):
    rng = random.Random(seed)
    rng_lock = threading.Lock()            # random.Random no es seguro entre hilos

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            with rng_lock:
                delay = latency_ms + (rng.uniform(-jitter_ms, jitter_ms) if jitter_ms else 0.0)
            time.sleep(max(0.0, delay) / 1000)
            parts = self.path.strip("/").split("/")
            if len(parts) != 3 or parts[0] != "r":
                self.send_error(400)
                return
            path = replay_dir / parts[1] / f"{parts[2]}.body"
            if not path.is_file():
                self.send_error(404, "no capturado")
                return
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # silencio
            pass

    return Handler


class ReplayServer:
    def __init__(self, replay_dir: Path, host: str, port: int,
                 latency_ms: float, jitter_ms: float = 0.0, seed: int | None = 42):
        if not is_loopback(host):
            raise ValueError(f"el servidor de replay solo puede escuchar en la propia máquina, no en {host!r}")
        handler = make_handler(replay_dir, latency_ms, jitter_ms, seed)
        ThreadingHTTPServer.daemon_threads = True
        ThreadingHTTPServer.request_queue_size = 256
        self.httpd = ThreadingHTTPServer((host, port), handler)
        self.thread: threading.Thread | None = None

    def start_background(self) -> "ReplayServer":
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    def serve_forever(self) -> None:
        self.httpd.serve_forever()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
