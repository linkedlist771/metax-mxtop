from __future__ import annotations

from contextlib import nullcontext
import os
import time

from mxtop.models import ProcessSnapshot

_CPU_SAMPLES: dict[tuple[int, float], tuple[float, float]] = {}
_MAX_CPU_SAMPLES = 2048


def _calculate_cpu_percent(pid: int, process_identity: float, process_cpu_seconds: float, sample_time: float) -> float | None:
    key = (pid, process_identity)
    previous = _CPU_SAMPLES.get(key)
    _CPU_SAMPLES[key] = (process_cpu_seconds, sample_time)
    if len(_CPU_SAMPLES) > _MAX_CPU_SAMPLES:
        oldest_key = min(_CPU_SAMPLES, key=lambda sample_key: _CPU_SAMPLES[sample_key][1])
        del _CPU_SAMPLES[oldest_key]
    if previous is None:
        return None

    previous_cpu_seconds, previous_sample_time = previous
    elapsed = sample_time - previous_sample_time
    if elapsed <= 0:
        return None
    return max(0.0, (process_cpu_seconds - previous_cpu_seconds) / elapsed * 100)


def enrich_processes(processes: list[ProcessSnapshot]) -> None:
    try:
        import psutil
    except ModuleNotFoundError:
        _enrich_from_proc(processes)
        return

    for process_group in _group_processes_by_pid(processes):
        pid = process_group[0].pid
        try:
            sample_time = time.time()
            proc = psutil.Process(pid)
            oneshot = getattr(proc, "oneshot", None)
            with oneshot() if callable(oneshot) else nullcontext():
                host_name = (
                    proc.name()
                    if any(not process.name for process in process_group)
                    else ""
                )
                user = proc.username()
                command = proc.cmdline()
                cpu_times = proc.cpu_times()
                create_time = proc.create_time()
                cpu_percent = _calculate_cpu_percent(
                    pid,
                    create_time,
                    float(cpu_times.user + cpu_times.system),
                    sample_time,
                )
                host_memory_bytes = int(proc.memory_info().rss)
                memory_percent = getattr(proc, "memory_percent", None)
                try:
                    memory_util_percent = (
                        float(memory_percent()) if callable(memory_percent) else None
                    )
                except psutil.Error:
                    memory_util_percent = None
                runtime_seconds = max(0.0, sample_time - create_time)
        except psutil.Error:
            for process in process_group:
                if not process.name:
                    process.name = str(pid)
            continue

        command_text = " ".join(command)
        for process in process_group:
            process.name = process.name or host_name or str(pid)
            process.user = user
            process.command = command_text or process.name
            process.create_time = create_time
            process.cpu_percent = cpu_percent
            process.host_memory_bytes = host_memory_bytes
            process.memory_util_percent = memory_util_percent
            process.runtime_seconds = runtime_seconds


def _group_processes_by_pid(processes: list[ProcessSnapshot]) -> list[list[ProcessSnapshot]]:
    groups: dict[int, list[ProcessSnapshot]] = {}
    for process in processes:
        groups.setdefault(process.pid, []).append(process)
    return list(groups.values())


def _enrich_from_proc(processes: list[ProcessSnapshot]) -> None:
    boot_time = _safe_boot_time()
    clock_ticks = _safe_clock_ticks()
    total_memory_bytes = _safe_total_memory()
    for process_group in _group_processes_by_pid(processes):
        pid = process_group[0].pid
        comm_path = f"/proc/{pid}/comm"
        cmdline_path = f"/proc/{pid}/cmdline"
        stat_path = f"/proc/{pid}/stat"
        status_path = f"/proc/{pid}/status"
        try:
            with open(comm_path, "r", encoding="utf-8") as handle:
                host_name = handle.read().strip()
        except OSError:
            host_name = str(pid)
        for process in process_group:
            process.name = process.name or host_name or str(pid)

        try:
            with open(cmdline_path, "rb") as handle:
                raw = handle.read().replace(b"\x00", b" ").strip()
                command = raw.decode("utf-8", errors="replace")
        except OSError:
            command = ""
        for process in process_group:
            process.command = command or process.name

        try:
            user = str(os.stat(comm_path).st_uid)
        except OSError:
            user = None
        for process in process_group:
            process.user = user

        try:
            with open(stat_path, "r", encoding="utf-8") as handle:
                stat = handle.read().split()
            if boot_time is not None:
                process_cpu_seconds = (int(stat[13]) + int(stat[14])) / clock_ticks
                start_ticks = int(stat[21])
                create_time = boot_time + start_ticks / clock_ticks
                sample_time = time.time()
                cpu_percent = _calculate_cpu_percent(pid, float(start_ticks), process_cpu_seconds, sample_time)
                runtime_seconds = max(0.0, sample_time - create_time)
                for process in process_group:
                    process.create_time = create_time
                    process.cpu_percent = cpu_percent
                    process.runtime_seconds = runtime_seconds
        except (OSError, IndexError, ValueError):
            pass

        try:
            with open(status_path, "r", encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("VmRSS:"):
                        host_memory_bytes = int(line.split()[1]) * 1024
                        for process in process_group:
                            process.host_memory_bytes = host_memory_bytes
                            process.memory_util_percent = (
                                host_memory_bytes / total_memory_bytes * 100.0
                                if total_memory_bytes
                                else None
                            )
                        break
        except (OSError, IndexError, ValueError):
            pass


def _read_boot_time() -> float:
    with open("/proc/uptime", "r", encoding="utf-8") as handle:
        uptime_seconds = float(handle.read().split()[0])
    return time.time() - uptime_seconds


def _safe_boot_time() -> float | None:
    try:
        return _read_boot_time()
    except (OSError, IndexError, ValueError):
        return None


def _read_total_memory() -> int:
    with open("/proc/meminfo", "r", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    raise ValueError("MemTotal is unavailable")


def _safe_total_memory() -> int | None:
    try:
        return _read_total_memory()
    except (OSError, IndexError, ValueError):
        return None


def _read_clock_ticks() -> int:
    return int(os.sysconf(os.sysconf_names["SC_CLK_TCK"]))


def _safe_clock_ticks() -> int:
    try:
        return _read_clock_ticks()
    except (KeyError, OSError, ValueError):
        return 100
