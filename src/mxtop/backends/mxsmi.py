from __future__ import annotations

import csv
import io
import os
from pathlib import Path
import re
import shutil
import subprocess

from mxtop.host import enrich_processes
from mxtop.models import DeviceSnapshot, FrameSnapshot, ProcessSnapshot

MXSMI_ENV = "MXTOP_MXSMI_PATH"
DEFAULT_MXSMI_PATH = "/opt/mxdriver/bin/mx-smi"
LIST_ROW = re.compile(
    r"(?:GPU\s*)?(?P<index>\d+)\s*[:：]\s*(?P<name>.*?)(?:\s*\((?:UUID|uuid)\s*[:：]?\s*(?P<uuid>[^)]+)\))?\s*$"
)
PROCESS_ROW = re.compile(r"^\s*(?P<gpu>\d+)\s+(?P<pid>\d+)\s+(?P<name>.+?)\s+(?P<memory>[\d.]+\s*[A-Za-z]*|N/A|-)\s*$")
NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")
MEMORY_UNITS = {
    "b": 1,
    "k": 1024,
    "kb": 1024,
    "kib": 1024,
    "m": 1024**2,
    "mb": 1024**2,
    "mib": 1024**2,
    "g": 1024**3,
    "gb": 1024**3,
    "gib": 1024**3,
    "t": 1024**4,
    "tb": 1024**4,
    "tib": 1024**4,
}


def resolve_mxsmi_path(explicit_path: str | None = None) -> str:
    if explicit_path:
        return explicit_path
    if env_path := os.environ.get(MXSMI_ENV):
        return env_path
    if Path(DEFAULT_MXSMI_PATH).exists():
        return DEFAULT_MXSMI_PATH
    return shutil.which("mx-smi") or "mx-smi"


def _normalize_key(value: str) -> str:
    return value.strip().lower().replace(" ", "").replace("-", "_")


def _float(value: str | None) -> float | None:
    if value is None or value.strip().lower() in {"", "n/a", "na", "none", "-", "--"}:
        return None
    match = NUMBER.search(value.replace(",", ""))
    return float(match.group()) if match else None


def _first(values: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        if key in values and values[key].strip():
            return values[key]
    return None


def _memory_bytes(value: str | None, default_unit: str = "mib") -> int | None:
    number = _float(value)
    if number is None:
        return None
    unit_match = re.search(r"([A-Za-z]+)", value or "")
    unit = unit_match.group(1).lower() if unit_match else default_unit.lower()
    return int(number * MEMORY_UNITS.get(unit, MEMORY_UNITS[default_unit.lower()]))


def _memory_util_from_used_total(used: int | None, total: int | None) -> float | None:
    if used is None or not total:
        return None
    return used / total * 100


def _identity(gpu_index: int, pid: int) -> str:
    return f"{gpu_index}:{pid}"


def parse_list_output(output: str) -> dict[int, DeviceSnapshot]:
    devices: dict[int, DeviceSnapshot] = {}
    for raw_line in output.splitlines():
        line = raw_line.strip().strip("|").strip()
        if not line:
            continue
        match = LIST_ROW.search(line)
        if match is None:
            continue
        index = int(match.group("index"))
        name = (match.group("name") or "MetaX GPU").strip()
        uuid = (match.group("uuid") or "").strip() or None
        devices[index] = DeviceSnapshot(index=index, name=name, uuid=uuid)
    return devices


def parse_dmon_csv(output: str, known_devices: dict[int, DeviceSnapshot] | None = None) -> list[DeviceSnapshot]:
    rows = [row for row in csv.reader(io.StringIO(output.strip())) if any(column.strip() for column in row)]
    if len(rows) < 2:
        return []

    header = [_normalize_key(column) for column in rows[0]]
    devices: list[DeviceSnapshot] = []
    for row in rows[1:]:
        values = {header[index]: row[index].strip() for index in range(min(len(header), len(row)))}
        index_value = _first(values, "dev", "gpu", "gpu_id", "index", "id")
        if index_value is None or not index_value.strip().isdigit():
            continue
        index = int(index_value)
        known = known_devices.get(index) if known_devices else None
        total = _memory_bytes(_first(values, "total", "totalmemory", "memorytotal", "vramtotal"), default_unit="gb")
        used = _memory_bytes(_first(values, "used", "usedmemory", "memoryused", "vramused"), default_unit="mib")
        memory_util = _float(_first(values, "vram", "memory", "mem", "memutil", "memoryutil"))
        if used is None and total is not None and memory_util is not None:
            used = int(total * memory_util / 100)
        if memory_util is None:
            memory_util = _memory_util_from_used_total(used, total)
        free = total - used if total is not None and used is not None else None

        devices.append(
            DeviceSnapshot(
                index=index,
                name=known.name if known else "MetaX GPU",
                bdf=_first(values, "bdfid", "bdf", "busid", "pci", "pci_bus_id") or (known.bdf if known else None),
                uuid=known.uuid if known else None,
                temperature_c=_float(_first(values, "hottemp", "temperature", "temp", "coretemp", "soctemp")),
                power_w=_float(_first(values, "power", "powerdraw", "boardpower")),
                gpu_util_percent=_float(_first(values, "gpu", "gpuutil", "util", "usage")),
                memory_util_percent=memory_util,
                memory_used_bytes=used,
                memory_total_bytes=total,
                memory_free_bytes=free,
                fan_percent=_float(_first(values, "fan", "fanspeed")),
                performance_state=_first(values, "pstate", "perf", "performancestate"),
                ecc_status=_first(values, "ecc", "eccstatus"),
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
        gpu_index = int(match.group("gpu"))
        pid = int(match.group("pid"))
        memory = _memory_bytes(match.group("memory"), default_unit="mib")
        processes.append(
            ProcessSnapshot(
                gpu_index=gpu_index,
                pid=pid,
                name=match.group("name").strip(),
                gpu_memory_bytes=memory,
                identity=_identity(gpu_index, pid),
            )
        )
    return processes


class MxSmiBackend:
    name: str = "mx-smi"

    def __init__(self, executable: str | None = None) -> None:
        self.executable = resolve_mxsmi_path(executable)

    def _run(self, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [self.executable, *args],
            check=check,
            text=True,
            capture_output=True,
        )

    def _list_devices(self) -> dict[int, DeviceSnapshot]:
        for list_args in (["-L"], ["--list"]):
            result = self._run(list_args, check=False)
            if result.returncode == 0:
                devices = parse_list_output(result.stdout)
                if devices:
                    return devices
        return {}

    def snapshot(self) -> FrameSnapshot:
        known_devices = self._list_devices()
        dmon = self._run(
            [
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
            ]
        )
        devices = parse_dmon_csv(dmon.stdout, known_devices=known_devices)
        if not devices and known_devices:
            devices = list(known_devices.values())

        process_output = self._run(["--show-process"], check=False)
        processes = parse_process_table(process_output.stdout) if process_output.returncode == 0 else []
        enrich_processes(processes)
        return FrameSnapshot(devices=devices, processes=processes, backend=self.name)
