"""Entry point for ``mxtop --remote-mode``: inventory + poller + web server."""

from __future__ import annotations

import asyncio
import threading
import webbrowser
from pathlib import Path

from mxtop.remote.cluster import ClusterMonitor
from mxtop.remote.web import SnapshotHolder, make_server


def load_hosts(nodes: list[str] | None, nodes_file: str | None) -> list[str]:
    """Build the host list from ``--nodes`` tokens and/or a ``--nodes-file``.

    Hosts are ssh aliases resolved via ``~/.ssh/config``. Commas and whitespace
    both separate entries; ``#`` comments and blank lines in the file are
    ignored. Order is preserved and duplicates removed.
    """
    raw: list[str] = []
    for token in nodes or []:
        raw.extend(token.replace(",", " ").split())
    if nodes_file:
        for line in Path(nodes_file).read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                raw.extend(line.replace(",", " ").split())
    seen: dict[str, None] = {}
    for host in raw:
        seen.setdefault(host, None)
    return list(seen)


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
