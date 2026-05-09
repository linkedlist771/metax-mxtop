from __future__ import annotations

import os
import time

from mxtop.models import ProcessSnapshot


def enrich_processes(processes: list[ProcessSnapshot]) -> None:
    try:
        import psutil
    except ModuleNotFoundError:
        _enrich_from_proc(processes)
        return

    for process in processes:
        try:
            proc = psutil.Process(process.pid)
            process.name = process.name or proc.name()
            process.user = proc.username()
            command = proc.cmdline()
            process.command = " ".join(command) if command else process.name
            process.cpu_percent = proc.cpu_percent(interval=None)
            process.host_memory_bytes = int(proc.memory_info().rss)
            process.runtime_seconds = max(0.0, time.time() - proc.create_time())
        except psutil.Error:
            if not process.name:
                process.name = str(process.pid)


def _enrich_from_proc(processes: list[ProcessSnapshot]) -> None:
    for process in processes:
        comm_path = f"/proc/{process.pid}/comm"
        cmdline_path = f"/proc/{process.pid}/cmdline"
        try:
            with open(comm_path, "r", encoding="utf-8") as handle:
                process.name = process.name or handle.read().strip()
        except OSError:
            process.name = process.name or str(process.pid)

        try:
            with open(cmdline_path, "rb") as handle:
                raw = handle.read().replace(b"\x00", b" ").strip()
                process.command = raw.decode("utf-8", errors="replace") or process.name
        except OSError:
            process.command = process.name

        try:
            process.user = str(os.stat(comm_path).st_uid)
        except OSError:
            process.user = None
