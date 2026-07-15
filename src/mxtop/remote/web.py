"""HTTP and SSE transport for the remote cluster dashboard."""

from __future__ import annotations

from importlib.resources import files
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from mxtop.jsonutil import sanitize_json_value
from mxtop.models import ClusterSnapshot

_sanitize = sanitize_json_value

_ASSET_TYPES = {
    "index.html": "text/html; charset=utf-8",
    "dashboard.css": "text/css; charset=utf-8",
    "dashboard.js": "text/javascript; charset=utf-8",
}


class SnapshotHolder:
    """Thread-safe latest-snapshot store bridging the poller and HTTP."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._payload = "{}"
        self._version = 0

    def update(self, cluster: ClusterSnapshot) -> None:
        payload = json.dumps(sanitize_json_value(cluster.to_dict()), allow_nan=False)
        with self._condition:
            self._payload = payload
            self._version += 1
            self._condition.notify_all()

    def current(self) -> tuple[str, int]:
        with self._condition:
            return self._payload, self._version

    def wait(self, last_version: int, timeout: float) -> tuple[str, int]:
        with self._condition:
            if self._version <= last_version:
                self._condition.wait(timeout)
            return self._payload, self._version


def load_dashboard_assets() -> dict[str, bytes]:
    """Load dashboard resources from the installed package."""

    root = files("mxtop.remote").joinpath("static")
    return {name: root.joinpath(name).read_bytes() for name in _ASSET_TYPES}


def _make_handler(
    holder: SnapshotHolder,
    assets: dict[str, bytes],
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def _send(self, code: int, content_type: str, body: bytes) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            try:
                self.wfile.write(body)
            except OSError:
                pass

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            path = urlsplit(self.path).path
            if path in ("/", "/index.html"):
                self._send(200, _ASSET_TYPES["index.html"], assets["index.html"])
            elif path == "/assets/dashboard.css":
                self._send(
                    200,
                    _ASSET_TYPES["dashboard.css"],
                    assets["dashboard.css"],
                )
            elif path == "/assets/dashboard.js":
                self._send(
                    200,
                    _ASSET_TYPES["dashboard.js"],
                    assets["dashboard.js"],
                )
            elif path == "/favicon.ico":
                self._send(204, "image/x-icon", b"")
            elif path == "/api/snapshot":
                payload, _ = holder.current()
                self._send(200, "application/json", payload.encode())
            elif path == "/api/stream":
                self._stream()
            else:
                self._send(404, "text/plain; charset=utf-8", b"not found")

        def _stream(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            last = -1
            try:
                while True:
                    payload, version = holder.wait(last, timeout=15.0)
                    if version != last:
                        last = version
                        self.wfile.write(f"data: {payload}\n\n".encode())
                    else:
                        self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
            except (OSError, ValueError):
                return
            finally:
                self.close_connection = True

    return Handler


def make_server(
    holder: SnapshotHolder,
    *,
    bind: str = "127.0.0.1",
    port: int = 8080,
) -> ThreadingHTTPServer:
    assets = load_dashboard_assets()
    return ThreadingHTTPServer((bind, port), _make_handler(holder, assets))
