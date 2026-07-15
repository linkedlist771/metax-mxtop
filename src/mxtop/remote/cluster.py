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
    VERSION_ARGS_VARIANTS,
    build_frame_from_outputs,
    parse_list_output,
    parse_versions,
)
from mxtop.models import ClusterSnapshot, NodeSnapshot
from mxtop.remote.host import (
    HOST_TELEMETRY_COMMAND,
    HostCpuSample,
    parse_host_telemetry,
)
from mxtop.remote.processes import (
    apply_process_details,
    parse_process_details,
    process_details_command,
)
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
        self._versions: dict[str, tuple[str | None, str | None]] = {}
        self._host_cpu_samples: dict[str, HostCpuSample] = {}

    async def _connection(self, host: str) -> Any:
        conn = self._conns.get(host)
        if conn is None:
            conn = await ssh.connect(host, connect_timeout=self.connect_timeout)
            self._conns[host] = conn
        return conn

    async def _run_command(self, conn: Any, command: str) -> tuple[int, str]:
        result = await conn.run(command, check=False)
        return (result.exit_status or 0), (result.stdout or "")

    async def _run(self, conn: Any, args: list[str]) -> tuple[int, str]:
        return await self._run_command(conn, _command(self.mxsmi_path, args))

    async def _versions_for(self, host: str, conn: Any) -> tuple[str | None, str | None]:
        # Versions are static; fetch once per host and cache.
        if host not in self._versions:
            versions: tuple[str | None, str | None] = (None, None)
            for variant in VERSION_ARGS_VARIANTS:
                code, out = await self._run(conn, variant)
                if code == 0:
                    parsed = parse_versions(out)
                    if any(parsed):
                        versions = parsed
                        break
            self._versions[host] = versions
        return self._versions[host]

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
            driver_version, maca_version = await self._versions_for(host, conn)
            (dmon_code, dmon_out), (proc_code, proc_out), (host_code, host_out) = (
                await asyncio.gather(
                    self._run(conn, DMON_SNAPSHOT_ARGS),
                    self._run(conn, PROCESS_ARGS),
                    self._run_command(conn, HOST_TELEMETRY_COMMAND),
                )
            )
            frame = build_frame_from_outputs(
                dmon_out if dmon_code == 0 else "",
                proc_out if proc_code == 0 else "",
                known_devices=known,
                backend_name=f"mx-smi@{host}",
                enrich=False,
                driver_version=driver_version,
                maca_version=maca_version,
            )
            host_snapshot = None
            if host_code == 0:
                host_snapshot, cpu_sample = parse_host_telemetry(
                    host_out, self._host_cpu_samples.get(host)
                )
                if cpu_sample is not None:
                    self._host_cpu_samples[host] = cpu_sample

            details_command = process_details_command(frame.processes)
            if details_command is not None:
                _, details_out = await self._run_command(conn, details_command)
                apply_process_details(
                    frame.processes, parse_process_details(details_out)
                )
            latency = (time.monotonic() - start) * 1000
            return NodeSnapshot(
                hostname=host,
                reachable=True,
                frame=frame,
                latency_ms=latency,
                host=host_snapshot,
            )
        except Exception as exc:
            await self._drop(host)
            latency = (time.monotonic() - start) * 1000
            return NodeSnapshot(hostname=host, reachable=False, error=str(exc), latency_ms=latency)

    async def _drop(self, host: str) -> None:
        self._host_cpu_samples.pop(host, None)
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
