"""HTTP and SSE transport for the remote cluster dashboard."""

from __future__ import annotations

import hmac
import ipaddress
import json
import socket
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from mxtop.jsonutil import sanitize_json_value
from mxtop.models import ClusterSnapshot

_sanitize = sanitize_json_value

_ASSET_TYPES = {
    "index.html": "text/html; charset=utf-8",
    "dashboard.css": "text/css; charset=utf-8",
    "dashboard.js": "text/javascript; charset=utf-8",
    "theme.js": "text/javascript; charset=utf-8",
}

_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
        # The dashboard sets layout custom properties and bar widths at runtime.
        "style-src 'self' 'unsafe-inline'; script-src 'self'; object-src 'none'; "
        "base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
    ),
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}

_TOKEN_COOKIE = "mxtop_token"

# ThreadingHTTPServer spawns one thread per connection; SSE clients hold
# theirs open indefinitely, so cap them to bound thread growth.
MAX_SSE_CLIENTS = 32
WILDCARD_BINDS = frozenset({"", "*", "0.0.0.0", "::", "[::]"})


def normalized_bind(bind: str) -> str:
    """Normalize user-facing bind aliases into socket-compatible hosts."""

    bind = bind.strip()
    if bind == "*":
        return ""
    return bind.strip("[]")


def is_wildcard_bind(bind: str) -> bool:
    return bind.strip() in WILDCARD_BINDS


def access_url(
    bind: str,
    port: int,
    *,
    path: str = "/",
    auth_token: str | None = None,
) -> str:
    """Return a usable browser/client URL for a listening bind address."""

    host = "localhost" if is_wildcard_bind(bind) else normalized_bind(bind)
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    path = "/" + path.lstrip("/")
    url = f"http://{host}:{port}{path}"
    if auth_token is not None:
        url += "?" + urlencode({"token": auth_token})
    return url


class SnapshotHolder:
    """Thread-safe latest-snapshot store bridging the poller and HTTP."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._payload = "{}"
        self._version = 0
        self._cluster: ClusterSnapshot | None = None

    def update(self, cluster: ClusterSnapshot) -> None:
        payload = json.dumps(sanitize_json_value(cluster.to_dict()), allow_nan=False)
        with self._condition:
            self._payload = payload
            self._cluster = cluster
            self._version += 1
            self._condition.notify_all()

    def current(self) -> tuple[str, int]:
        with self._condition:
            return self._payload, self._version

    def current_cluster(self) -> ClusterSnapshot | None:
        with self._condition:
            return self._cluster

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
    auth_token: str | None,
    max_sse_clients: int = MAX_SSE_CLIENTS,
) -> type[BaseHTTPRequestHandler]:
    sse_slots = threading.BoundedSemaphore(max_sse_clients)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def _send_security_headers(self) -> None:
            self.send_header("X-Content-Type-Options", "nosniff")
            for name, value in _SECURITY_HEADERS.items():
                self.send_header(name, value)

        def _send(
            self,
            code: int,
            content_type: str,
            body: bytes,
            extra_headers: dict[str, str] | None = None,
        ) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self._send_security_headers()
            for name, value in (extra_headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            try:
                self.wfile.write(body)
            except OSError:
                pass

        def _token_matches(self, presented: str | None) -> bool:
            if auth_token is None:
                return True
            if not presented:
                return False
            return hmac.compare_digest(presented, auth_token)

        def _presented_token(self, query: str) -> str | None:
            authorization = self.headers.get("Authorization", "")
            if authorization.startswith("Bearer "):
                return authorization[len("Bearer ") :]
            query_tokens = parse_qs(query).get("token")
            if query_tokens:
                return query_tokens[0]
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            if _TOKEN_COOKIE in cookie:
                return cookie[_TOKEN_COOKIE].value
            return None

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            split = urlsplit(self.path)
            path = split.path
            authorized = self._token_matches(self._presented_token(split.query))
            if not authorized and path != "/favicon.ico":
                self._send(
                    401,
                    "text/plain; charset=utf-8",
                    b"unauthorized: provide the dashboard token via "
                    b"'Authorization: Bearer <token>' or '?token=<token>'",
                )
                return
            # A valid ?token= visit sets a cookie so the SPA's later
            # fetch/EventSource requests (which cannot add headers) pass.
            cookie_headers: dict[str, str] | None = None
            if auth_token is not None and parse_qs(split.query).get("token"):
                cookie = SimpleCookie()
                cookie[_TOKEN_COOKIE] = auth_token
                morsel = cookie[_TOKEN_COOKIE]
                morsel["httponly"] = True
                morsel["samesite"] = "Strict"
                morsel["path"] = "/"
                morsel["max-age"] = 86400
                cookie_headers = {
                    "Set-Cookie": morsel.OutputString(),
                    "Cache-Control": "no-store",
                }
            if path in ("/", "/index.html"):
                self._send(
                    200,
                    _ASSET_TYPES["index.html"],
                    assets["index.html"],
                    cookie_headers,
                )
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
            elif path == "/assets/theme.js":
                self._send(200, _ASSET_TYPES["theme.js"], assets["theme.js"])
            elif path == "/favicon.ico":
                self._send(204, "image/x-icon", b"")
            elif path == "/api/snapshot":
                payload, _ = holder.current()
                self._send(200, "application/json", payload.encode())
            elif path == "/metrics":
                from mxtop.remote.metrics import render_metrics

                cluster = holder.current_cluster()
                body = "" if cluster is None else render_metrics(cluster)
                self._send(
                    200,
                    "text/plain; version=0.0.4; charset=utf-8",
                    body.encode(),
                )
            elif path == "/api/stream":
                self._stream()
            else:
                self._send(404, "text/plain; charset=utf-8", b"not found")

        def _stream(self) -> None:
            if not sse_slots.acquire(blocking=False):
                self._send(
                    503,
                    "text/plain; charset=utf-8",
                    b"too many live-stream clients; retry later or use /api/snapshot",
                    {"Retry-After": "5"},
                )
                return
            try:
                self._stream_events()
            finally:
                sse_slots.release()

        def _stream_events(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self._send_security_headers()
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
    auth_token: str | None = None,
    max_sse_clients: int = MAX_SSE_CLIENTS,
) -> ThreadingHTTPServer:
    assets = load_dashboard_assets()
    socket_bind = normalized_bind(bind)
    server_type = ThreadingHTTPServer
    try:
        is_ipv6 = ipaddress.ip_address(socket_bind).version == 6
    except ValueError:
        is_ipv6 = False
    if is_ipv6:
        class IPv6ThreadingHTTPServer(ThreadingHTTPServer):
            address_family = socket.AF_INET6

        server_type = IPv6ThreadingHTTPServer
    return server_type(
        (socket_bind, port),
        _make_handler(holder, assets, auth_token, max_sse_clients),
    )
