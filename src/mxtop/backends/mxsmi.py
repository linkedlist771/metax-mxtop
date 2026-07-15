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
DEFAULT_MXSMI_TIMEOUT = 10.0
# Matches both the colon form ("GPU 0: MXC500 (UUID: MX-abc)") and the real
# `mx-smi -L` form ("GPU#0    MXC600-AL    0000:06:00.0    Available (UUID: GPU-...)").
LIST_ROW = re.compile(
    r"GPU\s*#?\s*(?P<index>\d+)\s*[:：]?\s+"
    r"(?P<name>.+?)"
    r"(?:\s+(?P<bdf>[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.\d))?"
    r"(?:\s+(?P<state>Available|Unavailable|N/A|On|Off))?"
    r"(?:\s*\((?:UUID|uuid)\s*[:：]?\s*(?P<uuid>[^)]+)\))?"
    r"\s*$",
    re.IGNORECASE,
)
PROCESS_ROW = re.compile(
    r"^\s*(?P<gpu>\d+)\s+(?P<pid>\d+)\s+"
    r"(?P<name>.+?)\s+(?P<memory>[\d.]+\s*[A-Za-z]*|N/A|-)\s*$",
    re.IGNORECASE,
)
TYPED_PROCESS_ROW = re.compile(
    r"^\s*(?P<gpu>\d+)\s+(?P<pid>\d+)\s+"
    r"(?P<type>C\+G|C|G|X|N/A|-)\s+"
    r"(?P<name>.+?)\s+(?P<memory>[\d.]+\s*[A-Za-z]*|N/A|-)\s*$",
    re.IGNORECASE,
)
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
        bdf = (match.group("bdf") or "").strip() or None
        devices[index] = DeviceSnapshot(index=index, name=name, uuid=uuid, bdf=bdf)
    return devices


def parse_dmon_csv(
    output: str, known_devices: dict[int, DeviceSnapshot] | None = None
) -> list[DeviceSnapshot]:
    rows = [
        row
        for row in csv.reader(io.StringIO(output.strip()))
        if any(column.strip() for column in row)
    ]
    if len(rows) < 2:
        return []

    header = [_normalize_key(column) for column in rows[0]]
    devices: list[DeviceSnapshot] = []
    for row in rows[1:]:
        values = {
            header[index]: row[index].strip()
            for index in range(min(len(header), len(row)))
        }
        index_value = _first(values, "dev", "gpu", "gpu_id", "index", "id")
        if index_value is None or not index_value.strip().isdigit():
            continue
        index = int(index_value)
        known = known_devices.get(index) if known_devices else None
        total = _memory_bytes(
            _first(values, "total", "totalmemory", "memorytotal", "vramtotal"),
            default_unit="gb",
        )
        used = _memory_bytes(
            _first(values, "used", "usedmemory", "memoryused", "vramused"),
            default_unit="mib",
        )
        memory_util = _float(
            _first(values, "vram", "memory", "mem", "memutil", "memoryutil")
        )
        if used is None and total is not None and memory_util is not None:
            used = int(total * memory_util / 100)
        if memory_util is None:
            memory_util = _memory_util_from_used_total(used, total)
        free = total - used if total is not None and used is not None else None

        devices.append(
            DeviceSnapshot(
                index=index,
                name=known.name if known else "MetaX GPU",
                bdf=_first(values, "bdfid", "bdf", "busid", "pci", "pci_bus_id")
                or (known.bdf if known else None),
                uuid=known.uuid if known else None,
                temperature_c=_float(
                    _first(
                        values, "hottemp", "temperature", "temp", "coretemp", "soctemp"
                    )
                ),
                power_w=_float(_first(values, "power", "powerdraw", "boardpower")),
                gpu_util_percent=_float(
                    _first(values, "gpu", "gpuutil", "util", "usage")
                ),
                memory_util_percent=memory_util,
                memory_used_bytes=used,
                memory_total_bytes=total,
                memory_free_bytes=free,
                fan_percent=_float(_first(values, "fan", "fanspeed")),
                performance_state=_first(values, "pstate", "perf", "performancestate"),
                ecc_status=_first(values, "ecc", "eccstatus"),
                gpu_clock_mhz=_float(
                    _first(
                        values,
                        "xcoreclk",
                        "gpuclock",
                        "gpu_clock",
                        "coreclock",
                        "core_clock",
                        "smclock",
                        "sm_clock",
                    )
                ),
                memory_clock_mhz=_float(
                    _first(
                        values,
                        "mcclk",
                        "memoryclock",
                        "memory_clock",
                        "memclock",
                        "mem_clock",
                        "vramclock",
                    )
                ),
            )
        )
    return devices


def parse_process_table(output: str) -> list[ProcessSnapshot]:
    processes: list[ProcessSnapshot] = []
    has_context_column = any(
        re.search(r"\b(?:type|context)\b", line, re.IGNORECASE)
        for line in output.splitlines()
        if re.search(r"\bPID\b", line, re.IGNORECASE)
    )
    for raw_line in output.splitlines():
        line = raw_line.strip().strip("|").strip()
        if not line or "no process found" in line.lower():
            continue
        match = (TYPED_PROCESS_ROW if has_context_column else PROCESS_ROW).match(line)
        if match is None:
            continue
        gpu_index = int(match.group("gpu"))
        pid = int(match.group("pid"))
        memory = _memory_bytes(match.group("memory"), default_unit="mib")
        process_type = None
        if has_context_column:
            raw_process_type = (match.group("type") or "").upper()
            if raw_process_type not in {"N/A", "-"}:
                process_type = raw_process_type or None
        processes.append(
            ProcessSnapshot(
                gpu_index=gpu_index,
                pid=pid,
                name=match.group("name").strip(),
                gpu_memory_bytes=memory,
                process_type=process_type,
                identity=_identity(gpu_index, pid),
            )
        )
    return processes


# Command argument lists shared by the local backend and the remote (SSH) path,
# so both run identical mx-smi invocations and feed the same parsers.
LIST_ARGS_VARIANTS: tuple[list[str], ...] = (["-L"], ["--list"])
DMON_SNAPSHOT_ARGS: list[str] = [
    "dmon",
    "--show-temperature",
    "--show-board-power",
    "--show-usage",
    "--show-memory",
    "--total-memory",
    "--show-bdf",
    "--show-clock",
    "--format",
    "csv",
    "-c",
    "1",
]
PROCESS_ARGS: list[str] = ["--show-process"]
# Bare `mx-smi` (and `--show-version`) print the static version block; we parse
# both the kernel-mode driver version and the MACA (CUDA-equivalent) version.
VERSION_ARGS_VARIANTS: tuple[list[str], ...] = (["--show-version"], [])
_DRIVER_VERSION_RE = re.compile(
    r"(?:Kernel\s*Mode\s*)?Driver\s*Version\s*[:：]\s*([^\s|]+)", re.IGNORECASE
)
_MACA_VERSION_RE = re.compile(r"MACA\s*Version\s*[:：]\s*([^\s|]+)", re.IGNORECASE)


def parse_versions(output: str) -> tuple[str | None, str | None]:
    """Return ``(driver_version, maca_version)`` parsed from mx-smi output."""
    driver = _DRIVER_VERSION_RE.search(output)
    maca = _MACA_VERSION_RE.search(output)
    return (
        driver.group(1).strip() if driver else None,
        maca.group(1).strip() if maca else None,
    )


def build_frame_from_outputs(
    dmon_output: str,
    process_output: str,
    *,
    known_devices: dict[int, DeviceSnapshot] | None = None,
    backend_name: str = "mx-smi",
    enrich: bool = True,
    driver_version: str | None = None,
    maca_version: str | None = None,
) -> FrameSnapshot:
    """Assemble a FrameSnapshot from raw mx-smi command output.

    Transport-agnostic: the caller supplies the dmon/process text (from a local
    subprocess or an SSH channel) and an optional parsed device map from -L.
    ``enrich`` should stay False for remote hosts (psutil would read the wrong
    machine). ``driver_version``/``maca_version`` are system-wide and stamped on
    every device so the header can show them.
    """
    known_devices = known_devices or {}
    devices = parse_dmon_csv(dmon_output, known_devices=known_devices)
    if not devices and known_devices:
        devices = list(known_devices.values())
    if driver_version is not None or maca_version is not None:
        for device in devices:
            if driver_version is not None:
                device.driver_version = driver_version
            if maca_version is not None:
                device.maca_version = maca_version
    processes = parse_process_table(process_output) if process_output else []
    if enrich:
        enrich_processes(processes)
    return FrameSnapshot(devices=devices, processes=processes, backend=backend_name)


class MxSmiBackend:
    name: str = "mx-smi"

    def __init__(
        self,
        executable: str | None = None,
        *,
        timeout: float = DEFAULT_MXSMI_TIMEOUT,
    ) -> None:
        self.executable = resolve_mxsmi_path(executable)
        self.timeout = max(0.1, float(timeout))
        self._versions: tuple[str | None, str | None] | None = None
        self._known_devices: dict[int, DeviceSnapshot] | None = None

    def _run(
        self, args: list[str], *, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [self.executable, *args],
            check=check,
            text=True,
            capture_output=True,
            errors="replace",
            timeout=self.timeout,
        )

    def _list_devices(self) -> dict[int, DeviceSnapshot]:
        if self._known_devices is not None:
            return self._known_devices
        for list_args in LIST_ARGS_VARIANTS:
            result = self._run(list_args, check=False)
            if result.returncode == 0:
                devices = parse_list_output(result.stdout)
                if devices:
                    self._known_devices = devices
                    return self._known_devices
        return {}

    def _driver_versions(self) -> tuple[str | None, str | None]:
        # Versions are static; fetch once and cache. Try --show-version, then
        # fall back to bare mx-smi (whose header carries the same block).
        if self._versions is None:
            self._versions = (None, None)
            for version_args in VERSION_ARGS_VARIANTS:
                result = self._run(version_args, check=False)
                if result.returncode == 0:
                    parsed = parse_versions(result.stdout)
                    if any(parsed):
                        self._versions = parsed
                        break
        return self._versions

    def snapshot(self) -> FrameSnapshot:
        known_devices = self._list_devices()
        driver_version, maca_version = self._driver_versions()
        dmon = self._run(DMON_SNAPSHOT_ARGS)
        process_output = self._run(PROCESS_ARGS, check=False)
        return build_frame_from_outputs(
            dmon.stdout,
            process_output.stdout if process_output.returncode == 0 else "",
            known_devices=known_devices,
            backend_name=self.name,
            enrich=True,
            driver_version=driver_version,
            maca_version=maca_version,
        )
