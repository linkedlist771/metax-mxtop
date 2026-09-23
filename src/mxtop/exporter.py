"""Local Prometheus exporter: ``mxtop --export-metrics``.

Samples the local telemetry backend on the requested interval and serves
the same text exposition as the cluster dashboard's ``/metrics``, wrapped
as a single-node cluster labelled with this host's name. This covers the
dcgm-exporter-style deployment — one scrape target per GPU host — without
SSH or the remote extra.
"""

from __future__ import annotations

import socket
import ssl
import threading
import time

from mxtop.backends import TelemetryBackend
from mxtop.models import ClusterSnapshot, HostSnapshot, NodeSnapshot
from mxtop.remote.web import (
    SnapshotHolder,
    access_url,
    is_loopback_bind,
    is_wildcard_bind,
    make_server,
)


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
    tls_context: ssl.SSLContext | None = None,
) -> int:
    holder = SnapshotHolder()
    holder.update(build_local_cluster(backend))
    stop = threading.Event()
    server = make_server(
        holder,
        bind=bind,
        port=port,
        auth_token=auth_token,
        tls_context=tls_context,
    )

    def _poll() -> None:
        while not stop.wait(interval):
            holder.update(build_local_cluster(backend))

    try:
        poller = threading.Thread(target=_poll, name="mxtop-exporter", daemon=True)
        poller.start()
    except Exception:
        server.server_close()
        raise

    print(
        "mxtop metrics exporter: "
        f"{access_url(bind, port, path='/metrics', tls=tls_context is not None)}"
    )
    if is_wildcard_bind(bind):
        print(f"Listening on all interfaces ({bind or '*'}:{port}).")
    if not is_loopback_bind(bind) and tls_context is None:
        print(
            "WARNING: metrics traffic is exposed beyond localhost over plain HTTP; "
            "configure --tls-cert/--tls-key, a TLS reverse proxy, VPN, or SSH tunnel."
        )
    if not is_loopback_bind(bind) and auth_token is None:
        print(
            "WARNING: metrics endpoint is exposed beyond localhost without authentication; "
            "consider --auth-token or the MXTOP_AUTH_TOKEN environment variable."
        )
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
