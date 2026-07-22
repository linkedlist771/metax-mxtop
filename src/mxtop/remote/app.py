"""Web-server lifecycle for ``mxtop --remote-mode``."""

from __future__ import annotations

import asyncio
import ssl
import threading
import webbrowser

from mxtop.remote.cluster import DEFAULT_REMOTE_COMMAND_TIMEOUT, ClusterMonitor
from mxtop.remote.discovery import HostDiscovery
from mxtop.remote.web import (
    SnapshotHolder,
    access_url,
    is_loopback_bind,
    is_wildcard_bind,
    make_server,
)


def _is_loopback(bind: str) -> bool:
    return is_loopback_bind(bind)


def report_discovery(results: list[HostDiscovery]) -> None:
    print(f"mxtop discovery: checked {len(results)} SSH config host(s)")
    for result in results:
        latency = (
            "" if result.latency_ms is None else f", {round(result.latency_ms):d}ms"
        )
        if result.accepted:
            print(f"  + {result.host}: {result.gpu_count} GPU(s){latency}")
        else:
            reason = (result.reason or "probe failed").replace("\n", " ")
            print(f"  - {result.host}: {reason}{latency}")


async def _poll_loop(monitor: ClusterMonitor, holder: SnapshotHolder, stop: threading.Event) -> None:
    try:
        while not stop.is_set():
            cluster = await monitor.poll_once()
            holder.update(cluster)
            await asyncio.sleep(monitor.interval)
    finally:
        await monitor.close()


def run_remote(
    hosts: list[str],
    *,
    bind: str = "127.0.0.1",
    port: int = 8080,
    interval: float = 2.0,
    mxsmi_path: str = "mx-smi",
    command_timeout: float = DEFAULT_REMOTE_COMMAND_TIMEOUT,
    open_browser: bool = False,
    auth_token: str | None = None,
    tls_context: ssl.SSLContext | None = None,
) -> int:
    from mxtop.remote import ssh

    ssh.import_asyncssh()

    holder = SnapshotHolder()
    monitor = ClusterMonitor(
        hosts,
        interval=interval,
        mxsmi_path=mxsmi_path,
        command_timeout=command_timeout,
    )
    stop = threading.Event()
    server = make_server(
        holder,
        bind=bind,
        port=port,
        auth_token=auth_token,
        tls_context=tls_context,
    )

    def _worker() -> None:
        asyncio.run(_poll_loop(monitor, holder, stop))

    try:
        poller = threading.Thread(target=_worker, name="mxtop-cluster", daemon=True)
        poller.start()
    except Exception:
        server.server_close()
        raise

    tls_enabled = tls_context is not None
    url = access_url(bind, port, tls=tls_enabled)
    open_url = access_url(
        bind,
        port,
        auth_token=auth_token,
        tls=tls_enabled,
    )
    print(f"mxtop remote dashboard: {url}  ({len(hosts)} node(s): {', '.join(hosts)})")
    if is_wildcard_bind(bind):
        print(f"Listening on all interfaces ({bind or '*'}:{port}).")
    if auth_token is not None:
        print("Dashboard access requires the configured token (append ?token=... on first visit).")
    if not _is_loopback(bind) and tls_context is None:
        print(
            "WARNING: dashboard traffic is exposed beyond localhost over plain HTTP; "
            "configure --tls-cert/--tls-key, a TLS reverse proxy, VPN, or SSH tunnel."
        )
    if not _is_loopback(bind) and auth_token is None:
        print(
            "WARNING: dashboard is exposed beyond localhost without authentication; "
            "consider --auth-token or the MXTOP_AUTH_TOKEN environment variable."
        )
    print("Press Ctrl+C to stop.")
    if open_browser:
        try:
            opened = webbrowser.open(open_url)
        except Exception:
            print("Could not open a browser automatically; open the dashboard URL manually.")
            opened = True
        if not opened:
            print("No browser available; open the dashboard URL manually.")
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
