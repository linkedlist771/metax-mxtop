"""Concurrent multi-node mx-smi collection over SSH."""

from __future__ import annotations

import asyncio
import shlex
import time
from typing import Any

from mxtop.backends.mxsmi import (
    DMON_SNAPSHOT_ARGS,
    LIST_ARGS_VARIANTS,
    PROCESS_ARGS,
    build_frame_from_outputs,
    parse_list_output,
)
from mxtop.models import ClusterSnapshot, NodeSnapshot
from mxtop.remote import ssh


def _command(mxsmi_path: str, args: list[str]) -> str:
    return " ".join(shlex.quote(token) for token in [mxsmi_path, *args])


class ClusterMonitor:
    """Holds one persistent SSH connection per host and polls them in parallel."""

    def __init__(
        self,
        hosts: list[str],
        *,
        interval: float = 2.0,
        mxsmi_path: str = "mx-smi",
        connect_timeout: float = 10.0,
    ) -> None:
        self.hosts = list(hosts)
        self.interval = max(0.5, interval)
        self.mxsmi_path = mxsmi_path
        self.connect_timeout = connect_timeout
        self._conns: dict[str, Any] = {}

    async def _connection(self, host: str) -> Any:
        conn = self._conns.get(host)
        if conn is None:
            conn = await ssh.connect(host, connect_timeout=self.connect_timeout)
            self._conns[host] = conn
        return conn

    async def _run(self, conn: Any, args: list[str]) -> tuple[int, str]:
        result = await conn.run(_command(self.mxsmi_path, args), check=False)
        return (result.exit_status or 0), (result.stdout or "")

    async def _collect(self, host: str) -> NodeSnapshot:
        start = time.monotonic()
        try:
            conn = await self._connection(host)
            known: dict = {}
            for variant in LIST_ARGS_VARIANTS:
                code, out = await self._run(conn, variant)
                if code == 0:
                    known = parse_list_output(out)
                    if known:
                        break
            _, dmon_out = await self._run(conn, DMON_SNAPSHOT_ARGS)
            proc_code, proc_out = await self._run(conn, PROCESS_ARGS)
            frame = build_frame_from_outputs(
                dmon_out,
                proc_out if proc_code == 0 else "",
                known_devices=known,
                backend_name=f"mx-smi@{host}",
                enrich=False,
            )
            latency = (time.monotonic() - start) * 1000
            return NodeSnapshot(hostname=host, reachable=True, frame=frame, latency_ms=latency)
        except Exception as exc:
            await self._drop(host)
            latency = (time.monotonic() - start) * 1000
            return NodeSnapshot(hostname=host, reachable=False, error=str(exc), latency_ms=latency)

    async def _drop(self, host: str) -> None:
        conn = self._conns.pop(host, None)
        if conn is None:
            return
        try:
            conn.close()
            await conn.wait_closed()
        except Exception:
            pass

    async def poll_once(self) -> ClusterSnapshot:
        results = await asyncio.gather(*(self._collect(host) for host in self.hosts))
        return ClusterSnapshot(nodes=list(results))

    async def close(self) -> None:
        for host in list(self._conns):
            await self._drop(host)
