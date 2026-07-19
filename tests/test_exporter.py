"""Tests for the local Prometheus exporter mode."""

import threading
import urllib.request

from mxtop.exporter import build_local_cluster, run_exporter
from mxtop.models import DeviceSnapshot, FrameSnapshot


class StaticBackend:
    name = "static"

    def snapshot(self):
        return FrameSnapshot(
            devices=[DeviceSnapshot(index=0, name="MXC500", gpu_util_percent=21.0)],
            processes=[],
        )


class BrokenBackend:
    name = "broken"

    def snapshot(self):
        raise TimeoutError()


def test_build_local_cluster_wraps_frame_with_hostname():
    cluster = build_local_cluster(StaticBackend(), hostname="gpu-box")

    assert len(cluster.nodes) == 1
    node = cluster.nodes[0]
    assert node.hostname == "gpu-box"
    assert node.reachable is True
    assert node.frame is not None
    assert node.frame.devices[0].name == "MXC500"
    assert node.latency_ms is not None


def test_build_local_cluster_reports_backend_failure():
    cluster = build_local_cluster(BrokenBackend(), hostname="gpu-box")

    node = cluster.nodes[0]
    assert node.reachable is False
    # Empty str(TimeoutError()) falls back to the type name.
    assert node.error == "TimeoutError"


def test_run_exporter_serves_metrics(monkeypatch):
    exit_codes = []

    class InstantServer:
        def __init__(self):
            self.served = False

        def serve_forever(self):
            self.served = True
            raise KeyboardInterrupt

        def shutdown(self):
            pass

        def server_close(self):
            pass

    instant = InstantServer()
    captured = {}

    def fake_make_server(holder, *, bind, port, auth_token=None, **_):
        captured.update(holder=holder, bind=bind, port=port, auth_token=auth_token)
        return instant

    monkeypatch.setattr("mxtop.exporter.make_server", fake_make_server)

    exit_codes.append(
        run_exporter(
            StaticBackend(),
            bind="127.0.0.1",
            port=9532,
            interval=60.0,
            auth_token="tok",
        )
    )

    assert exit_codes == [0]
    assert instant.served
    assert captured["port"] == 9532
    assert captured["auth_token"] == "tok"
    cluster = captured["holder"].current_cluster()
    assert cluster is not None and cluster.nodes[0].reachable


def test_exporter_end_to_end_over_http():
    from mxtop.remote.web import SnapshotHolder, make_server

    holder = SnapshotHolder()
    holder.update(build_local_cluster(StaticBackend(), hostname="gpu-box"))
    server = make_server(holder, bind="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        with urllib.request.urlopen(f"http://{host}:{port}/metrics", timeout=5) as resp:
            body = resp.read().decode()
        assert 'mxtop_node_up{node="gpu-box"} 1' in body
        assert 'mxtop_gpu_utilization_percent{node="gpu-box",gpu="0",name="MXC500"} 21.0' in body
    finally:
        server.shutdown()
        server.server_close()
