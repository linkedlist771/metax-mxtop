from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import threading
from types import SimpleNamespace
import time
from urllib.parse import urlencode
import urllib.request

import pytest

from mxtop.backends.mxsmi import build_frame_from_outputs
from mxtop.models import ClusterSnapshot, DeviceSnapshot, FrameSnapshot, NodeSnapshot
from mxtop.remote import discovery, ssh
from mxtop.remote import app as remote_app
from mxtop.remote import web as remote_web
from mxtop.remote.app import _is_loopback, report_discovery
from mxtop.remote.discovery import discover_configured_hosts
from mxtop.remote.nodes import load_hosts, merge_hosts
from mxtop.remote.web import (
    SnapshotHolder,
    _sanitize,
    access_url,
    create_tls_context,
    load_dashboard_assets,
    make_server,
)

DMON = """dev, die, hottemp, drmossoctemp, drmoscoretemp, hbmtemp, power, gpu, visvram, vram, xtt, total, bdfid
idx, idx, C, C, C, C, W, %, %, %, %, GB,
0, 0, 34, N/A, 31, 40, 254, 37, 78, 78, 0, 72, 0000:01:00.0
1, 0, 35, 31, 31, 39, 254, 38, 78, 78, 0, 72, 0000:02:00.0
"""
LIST = "GPU#0    MXTEST-00    0000:01:00.0    Available (UUID: GPU-0)\n"


@pytest.fixture
def tls_material(tmp_path):
    openssl = shutil.which("openssl")
    if openssl is None:
        pytest.skip("openssl is required for the HTTPS transport tests")

    def generate(name: str, password: str | None = None) -> tuple[Path, Path]:
        cert_file = tmp_path / f"{name}-cert.pem"
        key_file = tmp_path / f"{name}-key.pem"
        command = [
            openssl,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-keyout",
            str(key_file),
            "-out",
            str(cert_file),
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost,IP:127.0.0.1",
        ]
        if password is None:
            command.append("-nodes")
        else:
            command.extend(("-passout", f"pass:{password}"))
        subprocess.run(
            command,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        return cert_file, key_file

    return generate


def test_build_frame_from_outputs_reuses_parsers():
    known = {0: DeviceSnapshot(index=0, name="MXTEST-00")}
    frame = build_frame_from_outputs(DMON, "", known_devices=known, backend_name="mx-smi@n1", enrich=False)

    assert frame.backend == "mx-smi@n1"
    assert [d.index for d in frame.devices] == [0, 1]
    assert frame.devices[0].temperature_c == 34.0
    assert frame.devices[0].power_w == 254.0
    assert frame.devices[0].name == "MXTEST-00"
    assert frame.processes == []


def test_load_hosts_merges_args_file_comments_and_dedupes(tmp_path):
    nodes_file = tmp_path / "nodes.txt"
    nodes_file.write_text("# cluster a\nnodeA\nnodeB  nodeC\n\nnodeA  # dup\n")

    hosts = load_hosts(["nodeX", "nodeY,nodeZ"], str(nodes_file))

    assert hosts == ["nodeX", "nodeY", "nodeZ", "nodeA", "nodeB", "nodeC"]


def test_load_hosts_empty_when_nothing_given():
    assert load_hosts(None, None) == []


def test_merge_hosts_preserves_order_and_removes_duplicates():
    assert merge_hosts(["node-a", "node-b"], ["node-b", "node-c"]) == [
        "node-a",
        "node-b",
        "node-c",
    ]


def test_configured_hosts_follows_includes_and_skips_patterns(tmp_path):
    conf_d = tmp_path / "conf.d"
    conf_d.mkdir()
    included = conf_d / "cluster.conf"
    included.write_text(
        f"Host node-b node-c\nHost node-* !node-b\nInclude {tmp_path / 'config'}\n",
        encoding="utf-8",
    )
    config = tmp_path / "config"
    config.write_text(
        "Host node-a\n"
        "  HostName 10.0.0.1\n"
        "Include conf.d/*.conf\n"
        "Host *\n"
        "Host node-c node-d # duplicate plus a new alias\n",
        encoding="utf-8",
    )

    assert discovery.configured_hosts(config) == [
        "node-a",
        "node-b",
        "node-c",
        "node-d",
    ]


def test_connect_disables_interactive_auth(monkeypatch, tmp_path):
    observed = {}
    expected_connection = object()

    class FakeAsyncSSH:
        async def connect(self, host, **options):
            observed["host"] = host
            observed["options"] = options
            return expected_connection

    config = tmp_path / "config"
    known_hosts = tmp_path / "known_hosts"
    config.write_text("Host node-a\n")
    known_hosts.write_text("node-a key\n")
    monkeypatch.setattr(ssh, "SSH_CONFIG_PATH", str(config))
    monkeypatch.setattr(ssh, "KNOWN_HOSTS_PATH", str(known_hosts))
    monkeypatch.setattr(ssh, "import_asyncssh", lambda: FakeAsyncSSH())

    connection = asyncio.run(ssh.connect("node-a", connect_timeout=3.0))

    assert connection is expected_connection
    assert observed["host"] == "node-a"
    assert observed["options"] == {
        "connect_timeout": 3.0,
        "password_auth": False,
        "kbdint_auth": False,
        "config": [str(config)],
        "known_hosts": str(known_hosts),
    }


def test_probe_host_accepts_working_mxsmi(monkeypatch):
    class FakeConnection:
        closed = False
        waited = False

        async def run(self, command, **options):
            assert "mx-smi" in command
            assert options == {"check": False, "timeout": 8.0}
            return SimpleNamespace(exit_status=0, stdout=LIST, stderr="")

        def close(self):
            self.closed = True

        async def wait_closed(self):
            self.waited = True

    connection = FakeConnection()

    async def fake_connect(host, *, connect_timeout):
        assert host == "node-a"
        assert connect_timeout == 5.0
        return connection

    monkeypatch.setattr(ssh, "connect", fake_connect)

    result = asyncio.run(discovery.probe_host("node-a"))

    assert result.accepted is True
    assert result.gpu_count == 1
    assert result.reason is None
    assert connection.closed is True
    assert connection.waited is True


def test_probe_host_rejects_missing_mxsmi(monkeypatch):
    class FakeConnection:
        async def run(self, *_args, **_kwargs):
            return SimpleNamespace(exit_status=127, stdout="", stderr="")

        def close(self):
            pass

        async def wait_closed(self):
            pass

    async def fake_connect(*_args, **_kwargs):
        return FakeConnection()

    monkeypatch.setattr(ssh, "connect", fake_connect)

    result = asyncio.run(discovery.probe_host("node-a"))

    assert result.accepted is False
    assert result.reason == "mx-smi not found"


def test_discover_hosts_is_concurrent_and_preserves_config_order(monkeypatch):
    async def fake_probe(host, **_kwargs):
        await asyncio.sleep({"slow": 0.03, "fast": 0.0}[host])
        return discovery.HostDiscovery(host, host == "fast", gpu_count=8)

    monkeypatch.setattr(discovery, "probe_host", fake_probe)

    results = asyncio.run(discovery.discover_hosts(["slow", "fast"], concurrency=2))

    assert [result.host for result in results] == ["slow", "fast"]
    assert [result.accepted for result in results] == [False, True]


def test_discover_configured_hosts_returns_only_accepted(monkeypatch):
    results = [
        discovery.HostDiscovery("node-a", True, gpu_count=8),
        discovery.HostDiscovery("node-b", False, reason="offline"),
    ]

    async def fake_discover(hosts, *, mxsmi_path):
        assert hosts == ["node-a", "node-b"]
        assert mxsmi_path == "/opt/mx-smi"
        return results

    monkeypatch.setattr(discovery, "configured_hosts", lambda: ["node-a", "node-b"])
    monkeypatch.setattr(discovery, "discover_hosts", fake_discover)

    accepted, observed = discover_configured_hosts(mxsmi_path="/opt/mx-smi")

    assert accepted == ["node-a"]
    assert observed == results


def test_report_discovery_prints_inventory_and_failure(capsys):
    report_discovery(
        [
            discovery.HostDiscovery("node-a", True, gpu_count=8, latency_ms=10.2),
            discovery.HostDiscovery("node-b", False, reason="offline", latency_ms=20.8),
        ]
    )

    output = capsys.readouterr().out
    assert "checked 2 SSH config host(s)" in output
    assert "+ node-a: 8 GPU(s), 10ms" in output
    assert "- node-b: offline, 21ms" in output


def test_run_remote_passes_command_timeout_to_cluster_monitor(monkeypatch, capsys):
    observed = {}

    class FakeMonitor:
        interval = 2.0

        def __init__(self, hosts, **kwargs):
            observed.update(hosts=hosts, kwargs=kwargs)

    class FakeThread:
        def __init__(self, *, target, name, daemon):
            observed.update(target=target, thread_name=name, daemon=daemon)

        def start(self):
            observed["started"] = True

        def join(self, *, timeout):
            observed["join_timeout"] = timeout

    class InstantServer:
        def serve_forever(self):
            raise KeyboardInterrupt

        def shutdown(self):
            observed["shutdown"] = True

        def server_close(self):
            observed["closed"] = True

    monkeypatch.setattr(ssh, "import_asyncssh", lambda: object())
    monkeypatch.setattr(remote_app, "ClusterMonitor", FakeMonitor)
    monkeypatch.setattr(remote_app.threading, "Thread", FakeThread)
    monkeypatch.setattr(
        remote_app,
        "make_server",
        lambda *_args, **_kwargs: InstantServer(),
    )

    rc = remote_app.run_remote(
        ["node-a"],
        interval=1.5,
        mxsmi_path="/opt/mx-smi",
        command_timeout=2.5,
    )

    assert rc == 0
    assert observed["hosts"] == ["node-a"]
    assert observed["kwargs"] == {
        "interval": 1.5,
        "mxsmi_path": "/opt/mx-smi",
        "command_timeout": 2.5,
    }
    assert observed["thread_name"] == "mxtop-cluster"
    assert observed["daemon"] is True
    assert observed["started"] is True
    assert observed["shutdown"] is True
    assert observed["closed"] is True
    assert observed["join_timeout"] == 2.0
    assert "mxtop remote dashboard" in capsys.readouterr().out


@pytest.mark.parametrize(
    (
        "bind",
        "tls_enabled",
        "auth_token",
        "warns_plain_http",
        "warns_unauthenticated",
    ),
    (
        ("127.0.0.1", False, None, False, False),
        ("0.0.0.0", False, None, True, True),
        ("0.0.0.0", False, "token", True, False),
        ("0.0.0.0", True, None, False, True),
        ("0.0.0.0", True, "token", False, False),
    ),
)
def test_run_remote_forwards_transport_opens_https_and_warns_independently(
    monkeypatch,
    capsys,
    bind,
    tls_enabled,
    auth_token,
    warns_plain_http,
    warns_unauthenticated,
):
    observed = {}

    class FakeMonitor:
        interval = 60.0

        def __init__(self, *_args, **_kwargs):
            pass

    class FakeThread:
        def __init__(self, *, target, name, daemon):
            observed.update(target=target, thread_name=name, daemon=daemon)

        def start(self):
            observed["thread_started"] = True

        def join(self, *, timeout):
            observed["join_timeout"] = timeout

    class InstantServer:
        def serve_forever(self):
            raise KeyboardInterrupt

        def shutdown(self):
            pass

        def server_close(self):
            pass

    tls_context = object() if tls_enabled else None

    def fake_make_server(holder, **kwargs):
        observed.update(holder=holder, server_kwargs=kwargs)
        return InstantServer()

    opened = []
    monkeypatch.setattr(ssh, "import_asyncssh", lambda: object())
    monkeypatch.setattr(remote_app, "ClusterMonitor", FakeMonitor)
    monkeypatch.setattr(remote_app.threading, "Thread", FakeThread)
    monkeypatch.setattr(remote_app, "make_server", fake_make_server)
    monkeypatch.setattr(
        remote_app.webbrowser,
        "open",
        lambda url: opened.append(url) or True,
    )

    assert (
        remote_app.run_remote(
            ["node-a"],
            bind=bind,
            port=8443,
            open_browser=True,
            auth_token=auth_token,
            tls_context=tls_context,
        )
        == 0
    )

    assert observed["server_kwargs"] == {
        "bind": bind,
        "port": 8443,
        "auth_token": auth_token,
        "tls_context": tls_context,
    }
    scheme = "https" if tls_enabled else "http"
    host = "localhost" if bind == "0.0.0.0" else bind
    query = "?token=token" if auth_token else ""
    assert opened == [f"{scheme}://{host}:8443/{query}"]
    output = capsys.readouterr().out
    assert f"mxtop remote dashboard: {scheme}://{host}:8443/" in output
    assert ("beyond localhost over plain HTTP" in output) is warns_plain_http
    assert ("beyond localhost without authentication" in output) is (
        warns_unauthenticated
    )


def test_run_remote_server_creation_failure_does_not_start_poller(monkeypatch):
    class FakeMonitor:
        interval = 60.0

        def __init__(self, *_args, **_kwargs):
            pass

    thread_calls = []
    tls_context = object()
    monkeypatch.setattr(ssh, "import_asyncssh", lambda: object())
    monkeypatch.setattr(remote_app, "ClusterMonitor", FakeMonitor)
    monkeypatch.setattr(
        remote_app.threading,
        "Thread",
        lambda **kwargs: thread_calls.append(kwargs),
    )

    def fail_server(*_args, **kwargs):
        assert kwargs["tls_context"] is tls_context
        raise OSError("TLS listener failed")

    monkeypatch.setattr(remote_app, "make_server", fail_server)

    with pytest.raises(OSError, match="TLS listener failed"):
        remote_app.run_remote(["node-a"], tls_context=tls_context)

    assert thread_calls == []


def test_run_remote_thread_start_failure_closes_listener(monkeypatch):
    class FakeMonitor:
        interval = 60.0

        def __init__(self, *_args, **_kwargs):
            pass

    class FailingThread:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            raise RuntimeError("thread unavailable")

    class BoundServer:
        closed = False

        def server_close(self):
            self.closed = True

    server = BoundServer()
    monkeypatch.setattr(ssh, "import_asyncssh", lambda: object())
    monkeypatch.setattr(remote_app, "ClusterMonitor", FakeMonitor)
    monkeypatch.setattr(remote_app.threading, "Thread", FailingThread)
    monkeypatch.setattr(remote_app, "make_server", lambda *_args, **_kwargs: server)

    with pytest.raises(RuntimeError, match="thread unavailable"):
        remote_app.run_remote(["node-a"])

    assert server.closed is True


def test_run_remote_does_not_echo_token_from_browser_errors(monkeypatch, capsys):
    class FakeMonitor:
        interval = 60.0

        def __init__(self, *_args, **_kwargs):
            pass

    class FakeThread:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            pass

        def join(self, **_kwargs):
            pass

    class InstantServer:
        def serve_forever(self):
            raise KeyboardInterrupt

        def shutdown(self):
            pass

        def server_close(self):
            pass

    def fail_browser(url):
        raise RuntimeError(f"could not launch {url}")

    monkeypatch.setattr(ssh, "import_asyncssh", lambda: object())
    monkeypatch.setattr(remote_app, "ClusterMonitor", FakeMonitor)
    monkeypatch.setattr(remote_app.threading, "Thread", FakeThread)
    monkeypatch.setattr(remote_app, "make_server", lambda *_args, **_kwargs: InstantServer())
    monkeypatch.setattr(remote_app.webbrowser, "open", fail_browser)

    assert (
        remote_app.run_remote(
            ["node-a"],
            open_browser=True,
            auth_token="do-not-log-this-token",
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "Could not open a browser automatically" in output
    assert "do-not-log-this-token" not in output


def test_sanitize_replaces_non_finite_floats():
    cleaned = _sanitize({"a": float("nan"), "b": [float("inf"), 1.5], "c": "x"})
    assert cleaned == {"a": None, "b": [None, 1.5], "c": "x"}
    assert json.dumps(cleaned)  # valid JSON, no NaN/Infinity tokens


def test_snapshot_holder_versions_and_payload():
    holder = SnapshotHolder()
    assert holder.current() == ("{}", 0)

    cluster = ClusterSnapshot(nodes=[NodeSnapshot(hostname="n1", reachable=True, frame=FrameSnapshot([], []))])
    holder.update(cluster)
    payload, version = holder.current()

    assert version == 1
    decoded = json.loads(payload)
    assert decoded["nodes"][0]["hostname"] == "n1"


def test_snapshot_holder_sanitizes_payload():
    holder = SnapshotHolder()
    device = DeviceSnapshot(index=0, gpu_util_percent=float("nan"))
    holder.update(ClusterSnapshot(nodes=[NodeSnapshot("n1", True, FrameSnapshot([device], []))]))
    payload, _ = holder.current()
    # json.loads rejects NaN with parse_constant raising, proving it was sanitized
    decoded = json.loads(payload, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    assert decoded["nodes"][0]["frame"]["devices"][0]["gpu_util_percent"] is None


def test_cluster_snapshot_to_dict_is_serializable():
    cluster = ClusterSnapshot(nodes=[NodeSnapshot("n1", False, error="boom")])
    data = cluster.to_dict()
    assert data["nodes"][0]["error"] == "boom"
    assert math.isfinite(data["timestamp"])


def test_web_server_serves_snapshot_and_index():
    holder = SnapshotHolder()
    holder.update(ClusterSnapshot(nodes=[NodeSnapshot("n1", True, FrameSnapshot([], []))]))
    server = make_server(holder, bind="127.0.0.1", port=0)
    import threading

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[0], server.server_address[1]
        with urllib.request.urlopen(f"http://{host}:{port}/api/snapshot", timeout=5) as resp:
            payload = json.loads(resp.read())
        assert payload["nodes"][0]["hostname"] == "n1"
        with urllib.request.urlopen(f"http://{host}:{port}/", timeout=5) as resp:
            html = resp.read()
            assert resp.headers.get_content_type() == "text/html"
            assert resp.headers["X-Frame-Options"] == "DENY"
            assert resp.headers["Referrer-Policy"] == "no-referrer"
            csp = resp.headers["Content-Security-Policy"]
            assert "script-src 'self'" in csp
            assert "frame-ancestors 'none'" in csp
        assert b"mxtop" in html
        assert b'data-route="overview"' in html
        with urllib.request.urlopen(
            f"http://{host}:{port}/assets/dashboard.css", timeout=5
        ) as resp:
            css = resp.read()
            assert resp.headers.get_content_type() == "text/css"
        assert b".kpi-strip" in css
        with urllib.request.urlopen(
            f"http://{host}:{port}/assets/dashboard.js", timeout=5
        ) as resp:
            javascript = resp.read()
            assert resp.headers.get_content_type() == "text/javascript"
        assert b"renderOverview" in javascript
        with urllib.request.urlopen(
            f"http://{host}:{port}/assets/theme.js", timeout=5
        ) as resp:
            theme = resp.read()
            assert resp.headers.get_content_type() == "text/javascript"
        assert b"searchParams.delete" in theme
        with urllib.request.urlopen(
            f"http://{host}:{port}/assets/history-storage.js", timeout=5
        ) as resp:
            history_storage = resp.read()
            assert resp.headers.get_content_type() == "text/javascript"
        assert b"mxtopHistoryStorage" in history_storage
        assert b"AES-GCM" in history_storage
        request = urllib.request.Request(f"http://{host}:{port}/favicon.ico")
        with urllib.request.urlopen(request, timeout=5) as resp:
            assert resp.status == 204
    finally:
        server.shutdown()
        server.server_close()


def test_dashboard_assets_have_views_and_responsive_guards():
    assets = {
        name: payload.decode("utf-8")
        for name, payload in load_dashboard_assets().items()
    }

    assert set(assets) == {
        "index.html",
        "dashboard.css",
        "dashboard.js",
        "history-storage.js",
        "theme.js",
    }
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in assets["index.html"]
    assert all(
        f'data-route="{route}"' in assets["index.html"]
        for route in ("overview", "nodes", "processes")
    )
    assert "@media (max-width: 760px)" in assets["dashboard.css"]
    assert "overflow-x: auto" in assets["dashboard.css"]
    assert 'data-theme="light"' in assets["dashboard.css"]
    assert '<meta name="color-scheme" content="dark light">' in assets["index.html"]
    assert 'id="theme-toggle"' in assets["index.html"]
    assert 'src="/assets/theme.js"' in assets["index.html"]
    assert 'src="/assets/history-storage.js"' in assets["index.html"]
    assert assets["index.html"].index('src="/assets/theme.js"') < assets[
        "index.html"
    ].index('src="/assets/history-storage.js"') < assets["index.html"].index(
        'src="/assets/dashboard.js"'
    )
    assert "prefers-color-scheme" in assets["theme.js"]
    assert "mxtop-theme" in assets["theme.js"]
    assert 'searchParams.delete("token")' in assets["theme.js"]
    rotation = 'sessionStorage.removeItem("mxtop-history-session-v1")'
    assert rotation in assets["theme.js"]
    assert assets["theme.js"].index(rotation) < assets["theme.js"].index(
        'searchParams.delete("token")'
    )
    assert "mxtopHistoryStorage" in assets["history-storage.js"]
    assert 'DB_NAME = "mxtop-dashboard-history"' in assets["history-storage.js"]
    assert 'name: "AES-GCM"' in assets["history-storage.js"]
    assert "additionalData" in assets["history-storage.js"]
    assert "token" not in assets["history-storage.js"].lower()
    assert "<script>" not in assets["index.html"]
    assert 'id="download-snapshot"' in assets["index.html"]
    assert "download-snapshot" in assets["dashboard.js"]
    assert 'navigate("overview")' in assets["dashboard.js"]
    assert "function renderOverview()" in assets["dashboard.js"]
    assert "function renderNodes()" in assets["dashboard.js"]
    assert "function renderProcesses()" in assets["dashboard.js"]
    assert "function renderNodeDetail(host)" in assets["dashboard.js"]


def test_web_server_enforces_auth_token():
    holder = SnapshotHolder()
    holder.update(ClusterSnapshot(nodes=[NodeSnapshot("n1", True, FrameSnapshot([], []))]))
    server = make_server(holder, bind="127.0.0.1", port=0, auth_token="s3cret")
    import threading
    import urllib.error

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[0], server.server_address[1]
        base = f"http://{host}:{port}"

        try:
            urllib.request.urlopen(f"{base}/api/snapshot", timeout=5)
            raise AssertionError("expected HTTP 401 without token")
        except urllib.error.HTTPError as error:
            assert error.code == 401

        request = urllib.request.Request(
            f"{base}/api/snapshot",
            headers={"Authorization": "Bearer s3cret"},
        )
        with urllib.request.urlopen(request, timeout=5) as resp:
            payload = json.loads(resp.read())
        assert payload["nodes"][0]["hostname"] == "n1"

        with urllib.request.urlopen(f"{base}/?token=s3cret", timeout=5) as resp:
            cookie = resp.headers.get("Set-Cookie", "")
            assert "mxtop_token=s3cret" in cookie
            assert "HttpOnly" in cookie
            assert "SameSite=Strict" in cookie
            assert "Max-Age=86400" in cookie
            assert "Secure" not in cookie
            assert resp.headers["Cache-Control"] == "no-store"

        cookie_request = urllib.request.Request(
            f"{base}/api/snapshot",
            headers={"Cookie": "mxtop_token=s3cret"},
        )
        with urllib.request.urlopen(cookie_request, timeout=5) as resp:
            assert resp.status == 200

        try:
            urllib.request.urlopen(f"{base}/api/snapshot?token=wrong", timeout=5)
            raise AssertionError("expected HTTP 401 with wrong token")
        except urllib.error.HTTPError as error:
            assert error.code == 401
    finally:
        server.shutdown()
        server.server_close()


def test_auth_cookie_round_trips_special_character_token():
    from http.cookies import SimpleCookie
    import threading

    token = 'value with spaces;quotes="and\\slashes'
    holder = SnapshotHolder()
    holder.update(ClusterSnapshot(nodes=[]))
    server = make_server(holder, bind="127.0.0.1", port=0, auth_token=token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        base = f"http://{host}:{port}"
        query = urlencode({"token": token})
        with urllib.request.urlopen(f"{base}/?{query}", timeout=5) as resp:
            set_cookie = resp.headers["Set-Cookie"]
        parsed = SimpleCookie(set_cookie)
        assert parsed["mxtop_token"].value == token

        cookie_request = urllib.request.Request(
            f"{base}/api/snapshot",
            headers={"Cookie": parsed["mxtop_token"].OutputString()},
        )
        with urllib.request.urlopen(cookie_request, timeout=5) as resp:
            assert resp.status == 200
    finally:
        server.shutdown()
        server.server_close()


def test_web_server_caps_sse_clients():
    holder = SnapshotHolder()
    holder.update(ClusterSnapshot(nodes=[]))
    server = make_server(holder, bind="127.0.0.1", port=0, max_sse_clients=1)
    import threading
    import urllib.error

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        base = f"http://{host}:{port}"
        first = urllib.request.urlopen(f"{base}/api/stream", timeout=5)
        assert first.readline().startswith(b"data: ")

        try:
            urllib.request.urlopen(f"{base}/api/stream", timeout=5)
            raise AssertionError("expected HTTP 503 beyond the SSE cap")
        except urllib.error.HTTPError as error:
            assert error.code == 503
            assert error.headers.get("Retry-After") == "5"

        # Snapshot polling stays available even at the cap.
        with urllib.request.urlopen(f"{base}/api/snapshot", timeout=5) as resp:
            assert resp.status == 200

        first.close()
        # The handler only notices the disconnect when a write fails, which
        # can take more than one send due to TCP buffering — keep publishing
        # snapshots until the slot frees.
        deadline = time.time() + 5
        while time.time() < deadline:
            holder.update(ClusterSnapshot(nodes=[]))
            try:
                second = urllib.request.urlopen(f"{base}/api/stream", timeout=5)
                break
            except urllib.error.HTTPError:
                time.sleep(0.05)
        else:
            raise AssertionError("SSE slot was not released after disconnect")
        second.close()
    finally:
        server.shutdown()
        server.server_close()


def test_web_server_treats_sse_disconnect_as_normal(capsys):
    holder = SnapshotHolder()
    holder.update(ClusterSnapshot(nodes=[]))
    server = make_server(holder, bind="127.0.0.1", port=0)
    import threading

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        response = urllib.request.urlopen(
            f"http://{host}:{port}/api/stream", timeout=5
        )
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Referrer-Policy"] == "no-referrer"
        assert "script-src 'self'" in response.headers["Content-Security-Policy"]
        assert response.readline().startswith(b"data: ")
        response.close()
        holder.update(ClusterSnapshot(nodes=[]))
        holder.update(ClusterSnapshot(nodes=[]))
        time.sleep(0.1)
    finally:
        server.shutdown()
        server.server_close()

    assert "Exception occurred during processing" not in capsys.readouterr().err


def test_access_url_handles_wildcards_ipv6_paths_and_tokens():
    assert access_url("127.0.0.1", 8080) == "http://127.0.0.1:8080/"
    assert access_url("0.0.0.0", 8080) == "http://localhost:8080/"
    assert access_url("::", 8080) == "http://localhost:8080/"
    assert access_url("2001:db8::1", 8080) == "http://[2001:db8::1]:8080/"
    assert access_url("[2001:db8::1]", 8080) == "http://[2001:db8::1]:8080/"
    assert access_url("gpu-host", 9532, path="metrics") == "http://gpu-host:9532/metrics"
    assert (
        access_url("0.0.0.0", 8080, auth_token="a&b #c?")
        == "http://localhost:8080/?token=a%26b+%23c%3F"
    )
    assert access_url("0.0.0.0", 8443, tls=True) == "https://localhost:8443/"
    assert (
        access_url("[::1]", 8443, path="api/snapshot", tls=True)
        == "https://[::1]:8443/api/snapshot"
    )


def test_tls_context_validates_material_and_encrypted_key_passwords(
    tls_material,
    tmp_path,
):
    cert_file, key_file = tls_material("plain")
    context = create_tls_context(str(cert_file), str(key_file))
    assert context is not None
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2
    assert context.options & ssl.OP_NO_COMPRESSION
    assert create_tls_context(None, None) is None

    with pytest.raises(ValueError, match="configured together"):
        create_tls_context(str(cert_file), None)
    with pytest.raises(ValueError, match="requires a certificate"):
        create_tls_context(None, None, key_password_file="password.txt")

    encrypted_cert, encrypted_key = tls_material("encrypted", "correct horse")
    with pytest.raises(ValueError, match="requires --tls-key-password-file"):
        create_tls_context(str(encrypted_cert), str(encrypted_key))

    password_file = tmp_path / "tls-password"
    password_file.write_text("correct horse\n")
    encrypted_context = create_tls_context(
        str(encrypted_cert),
        str(encrypted_key),
        key_password_file=str(password_file),
    )
    assert encrypted_context is not None

    other_cert, other_key = tls_material("other")
    assert other_cert.exists()
    with pytest.raises(ssl.SSLError):
        create_tls_context(str(cert_file), str(other_key))

    password_file.write_text("first\nsecond\n")
    with pytest.raises(ValueError, match="must contain one line"):
        create_tls_context(
            str(encrypted_cert),
            str(encrypted_key),
            key_password_file=str(password_file),
        )

    password_file.write_text("correct horse\n\n")
    with pytest.raises(ValueError, match="must contain one line"):
        create_tls_context(
            str(encrypted_cert),
            str(encrypted_key),
            key_password_file=str(password_file),
        )

    password_file.write_text("\n")
    with pytest.raises(ValueError, match="password file is empty"):
        create_tls_context(
            str(encrypted_cert),
            str(encrypted_key),
            key_password_file=str(password_file),
        )

    with pytest.raises(FileNotFoundError):
        create_tls_context(
            str(encrypted_cert),
            str(encrypted_key),
            key_password_file=str(tmp_path / "missing-password"),
        )

    malformed_cert = tmp_path / "malformed-cert.pem"
    malformed_key = tmp_path / "malformed-key.pem"
    malformed_cert.write_text("not a certificate\n")
    malformed_key.write_text("not a private key\n")
    with pytest.raises(ssl.SSLError):
        create_tls_context(str(malformed_cert), str(malformed_key))


def test_https_server_serves_snapshot_sse_and_secure_auth_cookie(tls_material):
    cert_file, key_file = tls_material("server")
    tls_context = create_tls_context(str(cert_file), str(key_file))
    holder = SnapshotHolder()
    holder.update(
        ClusterSnapshot(nodes=[NodeSnapshot("secure-node", True, FrameSnapshot([], []))])
    )
    server = make_server(
        holder,
        bind="127.0.0.1",
        port=0,
        auth_token="s3cret",
        tls_context=tls_context,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        base = f"https://localhost:{port}"
        client_context = ssl.create_default_context(cafile=str(cert_file))
        bearer_request = urllib.request.Request(
            f"{base}/api/snapshot",
            headers={"Authorization": "Bearer s3cret"},
        )
        with urllib.request.urlopen(
            bearer_request,
            timeout=5,
            context=client_context,
        ) as resp:
            assert resp.status == 200
        with urllib.request.urlopen(
            f"{base}/?token=s3cret",
            timeout=5,
            context=client_context,
        ) as resp:
            assert resp.status == 200
            cookie = resp.headers["Set-Cookie"]
            assert "Secure" in cookie
            assert "HttpOnly" in cookie
            assert "SameSite=Strict" in cookie
            assert resp.headers["Cache-Control"] == "no-store"
        cookie_header = cookie.split(";", 1)[0]
        snapshot_request = urllib.request.Request(
            f"{base}/api/snapshot",
            headers={"Cookie": cookie_header},
        )
        with urllib.request.urlopen(
            snapshot_request,
            timeout=5,
            context=client_context,
        ) as resp:
            snapshot = json.loads(resp.read())
        assert snapshot["nodes"][0]["hostname"] == "secure-node"

        metrics_request = urllib.request.Request(
            f"{base}/metrics",
            headers={"Cookie": cookie_header},
        )
        with urllib.request.urlopen(
            metrics_request,
            timeout=5,
            context=client_context,
        ) as resp:
            metrics = resp.read().decode()
        assert 'mxtop_node_up{node="secure-node"} 1' in metrics

        stream_request = urllib.request.Request(
            f"{base}/api/stream",
            headers={"Cookie": cookie_header},
        )
        with urllib.request.urlopen(
            stream_request,
            timeout=5,
            context=client_context,
        ) as resp:
            event = resp.readline().decode()
        assert event.startswith("data: ")
        assert json.loads(event.removeprefix("data: "))["nodes"][0][
            "hostname"
        ] == "secure-node"

        with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
            with client_context.wrap_socket(raw, server_hostname="localhost") as secure:
                assert secure.version() in {"TLSv1.2", "TLSv1.3"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_stalled_or_plain_http_tls_client_does_not_block_https(
    tls_material,
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(remote_web, "TLS_HANDSHAKE_TIMEOUT", 0.1)
    cert_file, key_file = tls_material("stall")
    server = make_server(
        SnapshotHolder(),
        bind="127.0.0.1",
        port=0,
        tls_context=create_tls_context(str(cert_file), str(key_file)),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    stalled = socket.create_connection(server.server_address[:2], timeout=2)
    try:
        time.sleep(0.25)
        port = server.server_address[1]
        context = ssl.create_default_context(cafile=str(cert_file))
        started = time.monotonic()
        with urllib.request.urlopen(
            f"https://localhost:{port}/api/snapshot",
            timeout=2,
            context=context,
        ) as resp:
            assert resp.status == 200
        assert time.monotonic() - started < 2

        with socket.create_connection(server.server_address[:2], timeout=2) as plain:
            plain.sendall(b"GET / HTTP/1.0\r\n\r\n")
        with urllib.request.urlopen(
            f"https://localhost:{port}/api/snapshot",
            timeout=2,
            context=context,
        ) as resp:
            assert resp.status == 200
    finally:
        stalled.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert "Exception occurred during processing" not in capsys.readouterr().err


def test_web_server_binds_ipv6_loopback_when_available():
    import socket

    holder = SnapshotHolder()
    try:
        server = make_server(holder, bind="::1", port=0)
    except OSError as error:
        import pytest

        pytest.skip(f"IPv6 loopback unavailable: {error}")
    try:
        assert server.address_family == socket.AF_INET6
        assert server.server_address[0] == "::1"
    finally:
        server.server_close()


def test_bind_normalization_handles_star_and_bracketed_loopback():
    assert _is_loopback("127.0.0.1")
    assert _is_loopback("[::1]")
    assert not _is_loopback("*")
    assert not _is_loopback("[::]")


def test_web_server_accepts_star_wildcard_bind():
    holder = SnapshotHolder()
    server = make_server(holder, bind="*", port=0)
    try:
        assert server.server_address[0] == "0.0.0.0"
    finally:
        server.server_close()
