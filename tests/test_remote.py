import asyncio
import json
import math
from types import SimpleNamespace
import time
import urllib.request

from mxtop.backends.mxsmi import build_frame_from_outputs
from mxtop.models import ClusterSnapshot, DeviceSnapshot, FrameSnapshot, NodeSnapshot
from mxtop.remote import discovery, ssh
from mxtop.remote.app import report_discovery
from mxtop.remote.discovery import discover_configured_hosts
from mxtop.remote.nodes import load_hosts, merge_hosts
from mxtop.remote.web import (
    SnapshotHolder,
    _sanitize,
    load_dashboard_assets,
    make_server,
)

DMON = """dev, die, hottemp, drmossoctemp, drmoscoretemp, hbmtemp, power, gpu, visvram, vram, xtt, total, bdfid
idx, idx, C, C, C, C, W, %, %, %, %, GB,
0, 0, 34, N/A, 31, 40, 254, 37, 78, 78, 0, 72, 0000:01:00.0
1, 0, 35, 31, 31, 39, 254, 38, 78, 78, 0, 72, 0000:02:00.0
"""
LIST = "GPU#0    MXTEST-00    0000:01:00.0    Available (UUID: GPU-0)\n"


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

    assert set(assets) == {"index.html", "dashboard.css", "dashboard.js"}
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in assets["index.html"]
    assert all(
        f'data-route="{route}"' in assets["index.html"]
        for route in ("overview", "nodes", "processes")
    )
    assert "@media (max-width: 760px)" in assets["dashboard.css"]
    assert "overflow-x: auto" in assets["dashboard.css"]
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
        assert response.readline().startswith(b"data: ")
        response.close()
        holder.update(ClusterSnapshot(nodes=[]))
        holder.update(ClusterSnapshot(nodes=[]))
        time.sleep(0.1)
    finally:
        server.shutdown()
        server.server_close()

    assert "Exception occurred during processing" not in capsys.readouterr().err
