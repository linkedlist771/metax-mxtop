"""Local Prometheus exporter: ``mxtop --export-metrics``.

Samples the local telemetry backend on the requested interval and serves
the same text exposition as the cluster dashboard's ``/metrics``, wrapped
as a single-node cluster labelled with this host's name. This covers the
dcgm-exporter-style deployment — one scrape target per GPU host — without
SSH or the remote extra.
"""

from __future__ import annotations

import socket
import threading
import time

from mxtop.backends import TelemetryBackend
from mxtop.models import ClusterSnapshot, HostSnapshot, NodeSnapshot
from mxtop.remote.web import SnapshotHolder, access_url, make_server


def _local_host_snapshot() -> HostSnapshot | None:
    try:
        import psutil
    except ModuleNotFoundError:
        return None
    try:
        memory = psutil.virtual_memory()
        load1, _, _ = psutil.getloadavg()
        return HostSnapshot(
            cpu_percent=float(psutil.cpu_percent(interval=None)),
            memory_used_bytes=int(memory.used),
            memory_total_bytes=int(memory.total),
            memory_percent=float(memory.percent),
            load_average_1m=float(load1),
        )
    except Exception:
        return None


def build_local_cluster(
    backend: TelemetryBackend, hostname: str | None = None
) -> ClusterSnapshot:
    """Wrap a local backend snapshot as a single-node cluster."""

    hostname = hostname or socket.gethostname()
    started = time.monotonic()
    try:
        frame = backend.snapshot()
    except Exception as exc:
        latency = (time.monotonic() - started) * 1000
        reason = str(exc).strip() or type(exc).__name__
        return ClusterSnapshot(
            nodes=[
                NodeSnapshot(
                    hostname=hostname,
                    reachable=False,
                    error=reason,
                    latency_ms=latency,
                )
            ]
        )
    latency = (time.monotonic() - started) * 1000
    return ClusterSnapshot(
        nodes=[
            NodeSnapshot(
                hostname=hostname,
                reachable=True,
                frame=frame,
                latency_ms=latency,
                host=_local_host_snapshot(),
            )
        ]
    )


def run_exporter(
    backend: TelemetryBackend,
    *,
    bind: str = "127.0.0.1",
    port: int = 9532,
    interval: float = 2.0,
    auth_token: str | None = None,
) -> int:
    holder = SnapshotHolder()
    holder.update(build_local_cluster(backend))
    stop = threading.Event()

    def _poll() -> None:
        while not stop.wait(interval):
            holder.update(build_local_cluster(backend))

    poller = threading.Thread(target=_poll, name="mxtop-exporter", daemon=True)
    poller.start()

    server = make_server(holder, bind=bind, port=port, auth_token=auth_token)
    print(f"mxtop metrics exporter: {access_url(bind, port, path='/metrics')}")
    if bind in {"", "0.0.0.0", "::", "[::]", "*"}:
        print(f"Listening on all interfaces ({bind or '*'}:{port}).")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        poller.join(timeout=2.0)
    return 0
