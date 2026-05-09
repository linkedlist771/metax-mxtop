from __future__ import annotations

import csv
import io
import re
import subprocess

from mxtop.host import enrich_processes
from mxtop.models import DeviceSnapshot, FrameSnapshot, ProcessSnapshot


PROCESS_ROW = re.compile(r"^\s*(\d+)\s+(\d+)\s+(.+?)\s+(\d+)\s*$")


def _float(value: str | None) -> float | None:
    if value is None or value.strip() in {"", "N/A"}:
        return None
    return float(value.strip())


def parse_dmon_csv(output: str) -> list[DeviceSnapshot]:
    rows = list(csv.reader(io.StringIO(output.strip())))
    if len(rows) < 3:
        return []

    header = [column.strip() for column in rows[0]]
    devices: list[DeviceSnapshot] = []
    for row in rows[2:]:
        if not row or not row[0].strip().isdigit():
            continue
        values = {header[index]: row[index].strip() for index in range(min(len(header), len(row)))}
        total_gb = _float(values.get("total"))
        vram_percent = _float(values.get("vram"))
        memory_total = int(total_gb * 1024**3) if total_gb is not None else None
        memory_used = None
        if memory_total is not None and vram_percent is not None:
            memory_used = int(memory_total * vram_percent / 100)

        devices.append(
            DeviceSnapshot(
                index=int(values["dev"]),
                name="MetaX GPU",
                bdf=values.get("bdfid") or None,
                temperature_c=_float(values.get("hottemp")),
                power_w=_float(values.get("power")),
                gpu_util_percent=_float(values.get("gpu")),
                memory_util_percent=vram_percent,
                memory_used_bytes=memory_used,
                memory_total_bytes=memory_total,
            )
        )
    return devices


def parse_process_table(output: str) -> list[ProcessSnapshot]:
    processes: list[ProcessSnapshot] = []
    for raw_line in output.splitlines():
        line = raw_line.strip().strip("|").strip()
        if not line or "no process found" in line.lower():
            continue
        match = PROCESS_ROW.match(line)
        if match is None:
            continue
        gpu_index, pid, name, memory_mib = match.groups()
        processes.append(
            ProcessSnapshot(
                gpu_index=int(gpu_index),
                pid=int(pid),
                name=name.strip(),
                gpu_memory_bytes=int(memory_mib) * 1024**2,
            )
        )
    return processes


class MxSmiBackend:
    name: str = "mx-smi"

    def snapshot(self) -> FrameSnapshot:
        dmon = subprocess.run(
            [
                "mx-smi",
                "dmon",
                "--show-temperature",
                "--show-board-power",
                "--show-usage",
                "--show-memory",
                "--total-memory",
                "--show-bdf",
                "--format",
                "csv",
                "-c",
                "1",
            ],
            check=True,
            text=True,
            capture_output=True,
        )
        devices = parse_dmon_csv(dmon.stdout)

        processes: list[ProcessSnapshot] = []
        process_output = subprocess.run(
            ["mx-smi", "--show-process"],
            check=False,
            text=True,
            capture_output=True,
        )
        if process_output.returncode == 0:
            processes = parse_process_table(process_output.stdout)
        enrich_processes(processes)
        return FrameSnapshot(devices=devices, processes=processes, backend=self.name)
