"""Web-server lifecycle for ``mxtop --remote-mode``."""

from __future__ import annotations

import asyncio
import ipaddress
import threading
import webbrowser

from mxtop.remote.cluster import DEFAULT_REMOTE_COMMAND_TIMEOUT, ClusterMonitor
from mxtop.remote.discovery import HostDiscovery
from mxtop.remote.web import (
    SnapshotHolder,
    access_url,
    is_wildcard_bind,
    make_server,
    normalized_bind,
)


def _is_loopback(bind: str) -> bool:
    bind = normalized_bind(bind)
    if bind == "localhost":
        return True
    try:
        return ipaddress.ip_address(bind).is_loopback
    except ValueError:
        return False


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

    def _worker() -> None:
        asyncio.run(_poll_loop(monitor, holder, stop))

    poller = threading.Thread(target=_worker, name="mxtop-cluster", daemon=True)
    poller.start()

    server = make_server(holder, bind=bind, port=port, auth_token=auth_token)
    url = access_url(bind, port)
    open_url = access_url(bind, port, auth_token=auth_token)
    print(f"mxtop remote dashboard: {url}  ({len(hosts)} node(s): {', '.join(hosts)})")
    if is_wildcard_bind(bind):
        print(f"Listening on all interfaces ({bind or '*'}:{port}).")
    if auth_token is not None:
        print("Dashboard access requires the configured token (append ?token=... on first visit).")
    elif not _is_loopback(bind):
        print(
            "WARNING: dashboard is exposed beyond localhost without authentication; "
            "consider --auth-token or the MXTOP_AUTH_TOKEN environment variable."
        )
    print("Press Ctrl+C to stop.")
    if open_browser:
        try:
            opened = webbrowser.open(open_url)
        except Exception as exc:
            print(f"Could not open a browser automatically: {exc}")
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
