"""Standard-library web server for the remote cluster dashboard.

No third-party web framework: ``http.server`` with a JSON endpoint and a
Server-Sent-Events stream, plus a single self-contained HTML page.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from mxtop.jsonutil import sanitize_json_value
from mxtop.models import ClusterSnapshot

_sanitize = sanitize_json_value


class SnapshotHolder:
    """Thread-safe latest-snapshot store bridging the asyncio poller and HTTP."""

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


def _make_handler(holder: SnapshotHolder, html: bytes) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:  # silence default stderr logging
            pass

        def _send(self, code: int, content_type: str, body: bytes) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                self._send(200, "text/html; charset=utf-8", html)
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
            except (BrokenPipeError, ConnectionResetError, ValueError):
                return

    return Handler


def make_server(holder: SnapshotHolder, *, bind: str = "127.0.0.1", port: int = 8080) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((bind, port), _make_handler(holder, DASHBOARD_HTML.encode()))


DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>mxtop &mdash; cluster</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; background: #0b0e14; color: #c8d3f5;
         font: 13px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
  header { padding: 12px 18px; border-bottom: 1px solid #1c2230; display: flex;
           align-items: baseline; gap: 14px; position: sticky; top: 0; background: #0b0e14; }
  header h1 { font-size: 15px; margin: 0; color: #82aaff; letter-spacing: .5px; }
  header .meta { color: #5c6680; font-size: 12px; }
  #grid { display: grid; gap: 14px; padding: 16px;
          grid-template-columns: repeat(auto-fill, minmax(420px, 1fr)); }
  .node { border: 1px solid #1c2230; border-radius: 8px; background: #11151f; overflow: hidden; }
  .node > .head { display: flex; justify-content: space-between; align-items: center;
                  padding: 8px 12px; background: #161b28; border-bottom: 1px solid #1c2230; }
  .node .name { font-weight: 600; color: #c3e88d; }
  .node.down .name { color: #ff757f; }
  .node .lat { color: #5c6680; font-size: 11px; }
  .err { padding: 10px 12px; color: #ff757f; white-space: pre-wrap; }
  .ver { padding: 4px 12px; font-size: 11px; border-bottom: 1px solid #1c2230; }
  table { width: 100%; border-collapse: collapse; }
  th, td { padding: 3px 8px; text-align: right; white-space: nowrap; }
  th { color: #5c6680; font-weight: 500; border-bottom: 1px solid #1c2230; font-size: 11px; }
  td.l, th.l { text-align: left; }
  tbody tr:nth-child(even) { background: #0e121b; }
  .bar { display: inline-block; width: 84px; height: 9px; border-radius: 2px;
         background: #1c2230; overflow: hidden; vertical-align: middle; margin-right: 6px; }
  .bar > span { display: block; height: 100%; }
  .g { background: #c3e88d; } .y { background: #ffcb6b; } .r { background: #ff757f; }
  .muted { color: #5c6680; }
  footer { padding: 8px 18px; color: #5c6680; border-top: 1px solid #1c2230; font-size: 11px; }
</style>
</head>
<body>
<header>
  <h1>mxtop</h1>
  <span class="meta" id="summary">connecting&hellip;</span>
</header>
<div id="grid"></div>
<footer id="status">waiting for first sample&hellip;</footer>
<script>
const grid = document.getElementById('grid');
const summary = document.getElementById('summary');
const status = document.getElementById('status');

function cls(p) { if (p == null) return 'muted'; if (p >= 80) return 'r'; if (p >= 40) return 'y'; return 'g'; }
function pct(p) { return p == null ? 'N/A' : Math.round(p) + '%'; }
function gib(bytes) { return bytes == null ? 'N/A' : (bytes / 1073741824).toFixed(1) + 'G'; }
function bar(p) {
  const w = p == null ? 0 : Math.max(0, Math.min(100, p));
  return '<span class="bar"><span class="' + cls(p) + '" style="width:' + w + '%"></span></span>';
}

function deviceRows(devices) {
  if (!devices || !devices.length) return '<tr><td colspan="5" class="muted l">no devices</td></tr>';
  return devices.map(d => {
    const memPct = d.memory_util_percent;
    return '<tr>' +
      '<td class="l">' + d.index + '</td>' +
      '<td class="l muted">' + (d.name || '') + '</td>' +
      '<td>' + (d.temperature_c == null ? 'N/A' : Math.round(d.temperature_c) + 'C') + '</td>' +
      '<td>' + (d.power_w == null ? 'N/A' : Math.round(d.power_w) + 'W') + '</td>' +
      '<td class="l">' + bar(d.gpu_util_percent) + pct(d.gpu_util_percent) + '</td>' +
      '<td class="l">' + bar(memPct) + gib(d.memory_used_bytes) + '/' + gib(d.memory_total_bytes) + '</td>' +
    '</tr>';
  }).join('');
}

function nodeCard(n) {
  if (!n.reachable) {
    return '<div class="node down"><div class="head"><span class="name">' + n.hostname +
           '</span><span class="lat">unreachable</span></div>' +
           '<div class="err">' + (n.error || 'connection failed') + '</div></div>';
  }
  const devs = n.frame ? n.frame.devices : [];
  const procs = n.frame ? n.frame.processes.length : 0;
  const d0 = devs[0] || {};
  const ver = 'driver ' + (d0.driver_version || 'N/A') + ' &middot; MACA ' + (d0.maca_version || 'N/A');
  return '<div class="node"><div class="head"><span class="name">' + n.hostname +
    '</span><span class="lat">' + devs.length + ' GPU &middot; ' + procs + ' proc &middot; ' +
    (n.latency_ms == null ? '' : Math.round(n.latency_ms) + 'ms') + '</span></div>' +
    '<div class="ver muted">' + ver + '</div>' +
    '<table><thead><tr><th class="l">#</th><th class="l">name</th><th>temp</th><th>pwr</th>' +
    '<th class="l">util</th><th class="l">mem</th></tr></thead><tbody>' +
    deviceRows(devs) + '</tbody></table></div>';
}

function render(cluster) {
  const nodes = cluster.nodes || [];
  grid.innerHTML = nodes.map(nodeCard).join('');
  const up = nodes.filter(n => n.reachable).length;
  const gpus = nodes.reduce((a, n) => a + (n.frame ? n.frame.devices.length : 0), 0);
  summary.textContent = up + '/' + nodes.length + ' nodes up · ' + gpus + ' GPUs';
  status.textContent = 'updated ' + new Date().toLocaleTimeString();
}

const es = new EventSource('/api/stream');
es.onmessage = e => { try { render(JSON.parse(e.data)); } catch (_) {} };
es.onerror = () => { status.textContent = 'stream disconnected — retrying…'; };
</script>
</body>
</html>
"""
