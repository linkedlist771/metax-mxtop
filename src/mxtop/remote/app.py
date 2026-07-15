"""Web-server lifecycle for ``mxtop --remote-mode``."""

from __future__ import annotations

import asyncio
import threading
import webbrowser

from mxtop.remote.cluster import ClusterMonitor
from mxtop.remote.discovery import HostDiscovery
from mxtop.remote.web import SnapshotHolder, make_server


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
    open_browser: bool = False,
) -> int:
    from mxtop.remote import ssh

    ssh.import_asyncssh()

    holder = SnapshotHolder()
    monitor = ClusterMonitor(hosts, interval=interval, mxsmi_path=mxsmi_path)
    stop = threading.Event()

    def _worker() -> None:
        asyncio.run(_poll_loop(monitor, holder, stop))

    poller = threading.Thread(target=_worker, name="mxtop-cluster", daemon=True)
    poller.start()

    server = make_server(holder, bind=bind, port=port)
    url = f"http://{bind}:{port}/"
    print(f"mxtop remote dashboard: {url}  ({len(hosts)} node(s): {', '.join(hosts)})")
    print("Press Ctrl+C to stop.")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
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
