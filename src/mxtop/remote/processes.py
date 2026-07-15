"""Batch enrichment for GPU processes reported by a remote node."""

from __future__ import annotations

from dataclasses import dataclass

from mxtop._compat import DATACLASS_SLOTS
from mxtop.models import ProcessSnapshot


@dataclass(frozen=True, **DATACLASS_SLOTS)
class RemoteProcessInfo:
    user: str
    cpu_percent: float
    host_memory_bytes: int
    runtime_seconds: float
    command: str


def process_details_command(processes: list[ProcessSnapshot]) -> str | None:
    pids = sorted({process.pid for process in processes if process.pid > 0})
    if not pids:
        return None
    pid_list = ",".join(str(pid) for pid in pids)
    return (
        "LC_ALL=C ps -ww -o pid= -o user= -o pcpu= -o rss= "
        f"-o etimes= -o args= -p {pid_list}"
    )


def parse_process_details(output: str) -> dict[int, RemoteProcessInfo]:
    details: dict[int, RemoteProcessInfo] = {}
    for raw_line in output.splitlines():
        parts = raw_line.strip().split(None, 5)
        if len(parts) < 5:
            continue
        try:
            pid = int(parts[0])
            cpu_percent = float(parts[2])
            host_memory_bytes = int(parts[3]) * 1024
            runtime_seconds = float(parts[4])
        except ValueError:
            continue
        details[pid] = RemoteProcessInfo(
            user=parts[1],
            cpu_percent=max(0.0, cpu_percent),
            host_memory_bytes=max(0, host_memory_bytes),
            runtime_seconds=max(0.0, runtime_seconds),
            command=parts[5] if len(parts) > 5 else "",
        )
    return details


def apply_process_details(
    processes: list[ProcessSnapshot],
    details: dict[int, RemoteProcessInfo],
) -> None:
    for process in processes:
        info = details.get(process.pid)
        if info is None:
            continue
        process.user = info.user
        process.cpu_percent = info.cpu_percent
        process.host_memory_bytes = info.host_memory_bytes
        process.runtime_seconds = info.runtime_seconds
        process.command = info.command or process.name
