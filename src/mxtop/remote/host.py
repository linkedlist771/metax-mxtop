"""Parse lightweight Linux host telemetry collected over SSH."""

from __future__ import annotations

from dataclasses import dataclass

from mxtop._compat import DATACLASS_SLOTS
from mxtop.models import HostSnapshot


HOST_TELEMETRY_COMMAND = (
    "LC_ALL=C awk '"
    'FILENAME == "/proc/stat" && $1 == "cpu" { '
    'print "cpu", $2, $3, $4, $5, $6, $7, $8, $9 } '
    'FILENAME == "/proc/meminfo" && $1 == "MemTotal:" { total = $2 } '
    'FILENAME == "/proc/meminfo" && $1 == "MemAvailable:" { available = $2 } '
    'FILENAME == "/proc/meminfo" && $1 == "MemFree:" { free = $2 } '
    'FILENAME == "/proc/loadavg" { print "load", $1, $2, $3 } '
    'FILENAME == "/proc/uptime" { print "uptime", $1 } '
    'END { print "mem", total + 0, available + 0, free + 0 }'
    "' /proc/stat /proc/meminfo /proc/loadavg /proc/uptime"
)


@dataclass(frozen=True, **DATACLASS_SLOTS)
class HostCpuSample:
    idle: float
    total: float


def _numbers(parts: list[str], count: int) -> list[float] | None:
    if len(parts) < count:
        return None
    try:
        return [float(value) for value in parts[:count]]
    except ValueError:
        return None


def parse_host_telemetry(
    output: str,
    previous_cpu: HostCpuSample | None = None,
) -> tuple[HostSnapshot, HostCpuSample | None]:
    """Return a host snapshot and the raw CPU counters for the next poll."""

    snapshot = HostSnapshot()
    current_cpu: HostCpuSample | None = None

    for raw_line in output.splitlines():
        parts = raw_line.split()
        if not parts:
            continue
        key, values = parts[0], parts[1:]

        if key == "cpu":
            counters = _numbers(values, 8)
            if counters is None:
                continue
            user, nice, system, idle, iowait, irq, softirq, steal = counters
            idle_total = idle + iowait
            total = user + nice + system + idle_total + irq + softirq + steal
            current_cpu = HostCpuSample(idle=idle_total, total=total)
            if previous_cpu is not None:
                total_delta = total - previous_cpu.total
                idle_delta = idle_total - previous_cpu.idle
                if total_delta > 0:
                    snapshot.cpu_percent = max(
                        0.0,
                        min(100.0, (total_delta - idle_delta) / total_delta * 100.0),
                    )

        elif key == "mem":
            memory = _numbers(values, 3)
            if memory is None:
                continue
            total_kib, available_kib, free_kib = memory
            if total_kib <= 0:
                continue
            available_kib = available_kib if available_kib > 0 else free_kib
            total_bytes = int(total_kib * 1024)
            used_bytes = max(0, total_bytes - int(available_kib * 1024))
            snapshot.memory_total_bytes = total_bytes
            snapshot.memory_used_bytes = used_bytes
            snapshot.memory_percent = min(100.0, used_bytes / total_bytes * 100.0)

        elif key == "load":
            load = _numbers(values, 3)
            if load is not None:
                (
                    snapshot.load_average_1m,
                    snapshot.load_average_5m,
                    snapshot.load_average_15m,
                ) = load

        elif key == "uptime":
            uptime = _numbers(values, 1)
            if uptime is not None:
                snapshot.uptime_seconds = max(0.0, uptime[0])

    return snapshot, current_cpu
