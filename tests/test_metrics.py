"""Tests for the Prometheus /metrics exposition."""

import urllib.request

from mxtop.models import (
    ClusterSnapshot,
    DeviceSnapshot,
    FrameSnapshot,
    HostSnapshot,
    NodeSnapshot,
)
from mxtop.remote.metrics import render_metrics
from mxtop.remote.web import SnapshotHolder, make_server


def _cluster() -> ClusterSnapshot:
    return ClusterSnapshot(
        nodes=[
            NodeSnapshot(
                hostname="node-a",
                reachable=True,
                latency_ms=12.5,
                frame=FrameSnapshot(
                    devices=[
                        DeviceSnapshot(
                            index=0,
                            name="MXC500",
                            uuid="GPU-abc",
                            gpu_util_percent=42.0,
                            memory_used_bytes=2 * 1024**3,
                            memory_total_bytes=64 * 1024**3,
                            temperature_c=55.0,
                            power_w=210.0,
                            gpu_clock_mhz=float("nan"),
                        )
                    ],
                    processes=[],
                ),
                host=HostSnapshot(
                    cpu_percent=33.0,
                    memory_used_bytes=8 * 1024**3,
                    memory_total_bytes=32 * 1024**3,
                    load_average_1m=1.5,
                ),
            ),
            NodeSnapshot(hostname='we"ird\nname', reachable=False, error="boom"),
        ],
        timestamp=1_752_000_000.0,
    )


def test_render_metrics_exposes_gauges_with_labels():
    text = render_metrics(_cluster())

    assert '# TYPE mxtop_node_up gauge' in text
    assert 'mxtop_node_up{node="node-a"} 1' in text
    assert 'mxtop_node_up{node="we\\"ird\\nname"} 0' in text
    assert (
        'mxtop_gpu_utilization_percent{node="node-a",gpu="0",name="MXC500",uuid="GPU-abc"} 42.0'
        in text
    )
    assert 'mxtop_gpu_memory_total_bytes' in text
    assert 'mxtop_host_cpu_percent{node="node-a"} 33.0' in text
    assert 'mxtop_node_collect_latency_seconds{node="node-a"} 0.012500' in text
    assert 'mxtop_gpu_processes{node="node-a"} 0' in text
    assert 'mxtop_snapshot_timestamp_seconds 1752000000.000' in text
    assert text.endswith("\n")


def test_render_metrics_skips_non_finite_and_missing_values():
    text = render_metrics(_cluster())
    assert "gpu_clock_megahertz" not in text  # NaN sample suppressed entirely
    assert 'mxtop_host_cpu_percent{node="we' not in text  # down node has no host


def test_metrics_endpoint_serves_prometheus_text_and_respects_auth():
    holder = SnapshotHolder()
    holder.update(_cluster())
    server = make_server(holder, bind="127.0.0.1", port=0, auth_token="tok")
    import threading
    import urllib.error

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        base = f"http://{host}:{port}"
        try:
            urllib.request.urlopen(f"{base}/metrics", timeout=5)
            raise AssertionError("expected 401 without token")
        except urllib.error.HTTPError as error:
            assert error.code == 401

        request = urllib.request.Request(
            f"{base}/metrics", headers={"Authorization": "Bearer tok"}
        )
        with urllib.request.urlopen(request, timeout=5) as resp:
            body = resp.read().decode()
            assert resp.headers.get_content_type() == "text/plain"
            assert "version=0.0.4" in resp.headers.get("Content-Type", "")
        assert "mxtop_node_up" in body
    finally:
        server.shutdown()
        server.server_close()


def test_metrics_endpoint_empty_before_first_snapshot():
    holder = SnapshotHolder()
    server = make_server(holder, bind="127.0.0.1", port=0)
    import threading

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        with urllib.request.urlopen(f"http://{host}:{port}/metrics", timeout=5) as resp:
            assert resp.status == 200
            assert resp.read() == b""
    finally:
        server.shutdown()
        server.server_close()
