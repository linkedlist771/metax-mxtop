"""Tests for the local Prometheus exporter mode."""

import threading
import urllib.request

import pytest

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


def test_run_exporter_serves_metrics(monkeypatch, capsys):
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
    assert "http://127.0.0.1:9532/metrics" in capsys.readouterr().out
    cluster = captured["holder"].current_cluster()
    assert cluster is not None and cluster.nodes[0].reachable


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
def test_run_exporter_forwards_transport_and_warns_independently(
    monkeypatch,
    capsys,
    bind,
    tls_enabled,
    auth_token,
    warns_plain_http,
    warns_unauthenticated,
):
    observed = {}

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

    monkeypatch.setattr("mxtop.exporter.threading.Thread", FakeThread)
    monkeypatch.setattr("mxtop.exporter.make_server", fake_make_server)

    assert (
        run_exporter(
            StaticBackend(),
            bind=bind,
            port=9443,
            interval=60,
            auth_token=auth_token,
            tls_context=tls_context,
        )
        == 0
    )

    assert observed["server_kwargs"] == {
        "bind": bind,
        "port": 9443,
        "auth_token": auth_token,
        "tls_context": tls_context,
    }
    scheme = "https" if tls_enabled else "http"
    host = "localhost" if bind == "0.0.0.0" else bind
    output = capsys.readouterr().out
    assert f"mxtop metrics exporter: {scheme}://{host}:9443/metrics" in output
    assert ("beyond localhost over plain HTTP" in output) is warns_plain_http
    assert ("beyond localhost without authentication" in output) is (
        warns_unauthenticated
    )


def test_run_exporter_server_creation_failure_does_not_start_poller(monkeypatch):
    thread_calls = []
    tls_context = object()
    monkeypatch.setattr(
        "mxtop.exporter.threading.Thread",
        lambda **kwargs: thread_calls.append(kwargs),
    )

    def fail_server(*_args, **kwargs):
        assert kwargs["tls_context"] is tls_context
        raise OSError("TLS listener failed")

    monkeypatch.setattr("mxtop.exporter.make_server", fail_server)

    with pytest.raises(OSError, match="TLS listener failed"):
        run_exporter(StaticBackend(), tls_context=tls_context)

    assert thread_calls == []


def test_run_exporter_thread_start_failure_closes_listener(monkeypatch):
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
    monkeypatch.setattr("mxtop.exporter.threading.Thread", FailingThread)
    monkeypatch.setattr(
        "mxtop.exporter.make_server",
        lambda *_args, **_kwargs: server,
    )

    with pytest.raises(RuntimeError, match="thread unavailable"):
        run_exporter(StaticBackend())

    assert server.closed is True


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


def test_run_exporter_prints_usable_url_for_wildcard_bind(monkeypatch, capsys):
    class InstantServer:
        def serve_forever(self):
            raise KeyboardInterrupt

        def shutdown(self):
            pass

        def server_close(self):
            pass

    monkeypatch.setattr(
        "mxtop.exporter.make_server", lambda *_args, **_kwargs: InstantServer()
    )

    assert (
        run_exporter(StaticBackend(), bind="0.0.0.0", port=9532, interval=60)
        == 0
    )

    output = capsys.readouterr().out
    assert "http://localhost:9532/metrics" in output
    assert "Listening on all interfaces (0.0.0.0:9532)" in output
