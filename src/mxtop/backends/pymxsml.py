from __future__ import annotations

from collections.abc import Callable, Iterable
from glob import glob
import importlib
import math
import os
import sys
from types import ModuleType
from typing import Any, TypeVar, cast

from mxtop.host import enrich_processes
from mxtop.models import DeviceSnapshot, FrameSnapshot, ProcessSnapshot


def _load_pymxsml() -> None:
    try:
        _ = importlib.import_module("pymxsml")
        return
    except ModuleNotFoundError:
        pass

    candidates = sorted(glob("/opt/maca/share/mxsml/pymxsml-*.whl"), reverse=True)
    candidates.extend(
        sorted(glob("/opt/mxn100/share/mxsml/pymxsml-*.whl"), reverse=True)
    )
    for wheel in candidates:
        if wheel not in sys.path:
            sys.path.insert(0, wheel)
        try:
            _ = importlib.import_module("pymxsml")
            return
        except ModuleNotFoundError:
            continue
    raise ModuleNotFoundError("pymxsml is not installed and no SDK wheel was found")


T = TypeVar("T")
PYMXSML_ECC_ERRORS_ENV = "MXTOP_PYMXSML_ECC_ERRORS"


def _safe(call: Callable[[], T], default: T | None = None) -> T | None:
    try:
        return call()
    except Exception:
        return default


def _env_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _module(name: str) -> ModuleType:
    return importlib.import_module(name)


def _callable(module: ModuleType, name: str) -> Callable[..., object]:
    value = cast(object, getattr(module, name))
    if not callable(value):
        raise TypeError(f"{module.__name__}.{name} is not callable")
    return value


def _optional_callable(module: ModuleType, name: str) -> Callable[..., object] | None:
    value = cast(object, getattr(module, name, None))
    return value if callable(value) else None


def _text(value: object | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode(errors="replace")
    text = str(value).strip()
    return text or None


def _items(value: object | None) -> Iterable[object]:
    if value is None or isinstance(value, (str, bytes, bytearray)):
        return ()
    try:
        return iter(cast(Any, value))
    except TypeError:
        return ()


def _number_attr(value: object | None, attr: str) -> float | None:
    if value is None:
        return None
    raw = cast(Any, getattr(value, attr, None))
    return float(raw) if raw is not None else None


def _int_attr(value: object | None, attr: str) -> int | None:
    if value is None:
        return None
    raw = cast(Any, getattr(value, attr, None))
    return int(raw) if raw is not None else None


def _number(value: object | None) -> float | None:
    if value is None:
        return None
    return float(cast(Any, value))


def _integer(value: object | None) -> int | None:
    if value is None:
        return None
    return int(cast(Any, value))


def normalize_temperature_c(value: float | int | None) -> float | None:
    if value is None:
        return None
    result = float(value)
    return result / 100 if result > 1000 else result


def normalize_power_w(value: float | int | None) -> float | None:
    if value is None:
        return None
    result = float(value)
    return result / 1000 if result > 1000 else result


def _numeric_values(value: object | None) -> list[float]:
    candidates = (value,) if isinstance(value, (int, float)) else _items(value)
    result: list[float] = []
    for candidate in candidates:
        try:
            number = float(cast(Any, candidate))
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            result.append(number)
    return result


def _constant_values(module: ModuleType, *names: str) -> tuple[int, ...]:
    values: list[int] = []
    for name in names:
        value = _integer(getattr(module, name, None))
        if value is not None and value not in values:
            values.append(value)
    return tuple(values)


def format_performance_state(value: object | None) -> str | None:
    try:
        state = _integer(value)
    except (TypeError, ValueError):
        return None
    return f"P{state}" if state is not None and 0 <= state <= 15 else None


def format_ecc_status(value: object | None) -> str | None:
    try:
        state = _integer(value)
    except (TypeError, ValueError):
        return None
    if state == 0:
        return "Disabled"
    if state == 1:
        return "Enabled"
    return None


def total_ecc_errors(value: object | None) -> int | None:
    if value is None:
        return None
    fields = ("dramCE", "dramUE", "sramCE", "sramUE")
    counts = [_int_attr(value, field) for field in fields]
    known = [max(0, count) for count in counts if count is not None]
    if known:
        return sum(known)
    try:
        scalar = _integer(value)
    except (TypeError, ValueError):
        return None
    return None if scalar is None else max(0, scalar)


def format_metaxlink_status(value: object | None) -> str | None:
    states = [int(state) for state in _numeric_values(value)]
    if not states:
        return None
    if any(state == 1 for state in states):
        return "Active"
    if any(state in {2, 3, 4} for state in states):
        return "Down"
    if all(state in {0, 5} for state in states):
        return "Inactive"
    return None


class PymxsmlBackend:
    name: str = "pymxsml"
    process_context_types = frozenset({"C"})

    def __init__(self) -> None:
        _load_pymxsml()
        mxsml = _module("pymxsml")
        mxsml_extension = _module("pymxsml.mxsml_extension")

        _ = _callable(mxsml, "mxSmlInit")()
        _ = _callable(mxsml_extension, "mxSmlExInit")()

        # Resolve attributes once: module/function lookups are static.
        self._temperature_hotspot = (
            _integer(getattr(mxsml, "MXSML_TEMPERATURE_HOTSPOT", 0)) or 0
        )
        self._version_driver_unit = (
            _integer(getattr(mxsml, "MXSML_VERSION_DRIVER", 1)) or 1
        )
        self._get_board_power_info = _callable(mxsml, "mxSmlGetBoardPowerInfo")
        self._get_device_count = _callable(mxsml, "mxSmlGetDeviceCount")
        self._get_device_info = _callable(mxsml, "mxSmlGetDeviceInfo")
        self._get_memory_info = _callable(mxsml, "mxSmlGetMemoryInfo")
        self._get_temperature_info = _callable(mxsml, "mxSmlGetTemperatureInfo")
        self._get_compute_processes = _callable(
            mxsml_extension, "mxSmlExDeviceGetComputeRunningProcesses"
        )
        self._get_graphics_processes = _optional_callable(
            mxsml_extension, "mxSmlExDeviceGetGraphicsRunningProcesses"
        ) or _optional_callable(mxsml, "nvmlDeviceGetGraphicsRunningProcesses")
        self.process_context_types = frozenset(
            {"C", "G"} if self._get_graphics_processes is not None else {"C"}
        )
        self._get_handle_by_index = _callable(
            mxsml_extension, "mxSmlExDeviceGetHandleByIndex"
        )
        self._get_utilization_rates = _callable(
            mxsml_extension, "mxSmlExDeviceGetUtilizationRates"
        )
        self._get_maca_version = _optional_callable(mxsml, "mxSmlGetMacaVersion")
        self._get_device_version = _optional_callable(mxsml, "mxSmlGetDeviceVersion")
        self._get_driver_version = _optional_callable(
            mxsml_extension, "mxSmlExSystemGetDriverVersion"
        )
        self._get_power_usage = _optional_callable(
            mxsml_extension, "mxSmlExDeviceGetPowerUsage"
        )
        self._get_board_power_limit = _optional_callable(
            mxsml_extension, "mxSmlExDeviceGetBoardPowerLimit"
        )
        self._get_power_mgmt_limit = _optional_callable(
            mxsml_extension, "mxSmlExDeviceGetPowerManagementLimit"
        )
        self._get_board_power_limit_core = _optional_callable(
            mxsml, "mxSmlGetBoardPowerLimit"
        )
        self._get_fan_speed = _optional_callable(
            mxsml_extension, "mxSmlExDeviceGetFanSpeed"
        )
        self._get_performance_state = _optional_callable(
            mxsml_extension, "mxSmlExDeviceGetPerformanceState"
        )
        self._get_clock_info = _optional_callable(
            mxsml_extension, "mxSmlExDeviceGetClockInfo"
        )
        self._get_clocks = _optional_callable(mxsml, "mxSmlGetClocks")
        self._gpu_clock_types = _constant_values(mxsml, "MXSML_CLOCK_XCORE")
        if not self._gpu_clock_types:
            self._gpu_clock_types = _constant_values(mxsml, "MXSML_CLOCK_CSC")
        self._memory_clock_types = _constant_values(
            mxsml, "MXSML_CLOCK_MC0", "MXSML_CLOCK_MC1"
        )
        if not self._memory_clock_types:
            self._memory_clock_types = _constant_values(mxsml, "MXSML_CLOCK_MC")
        self._get_ecc_state = _optional_callable(mxsml, "mxSmlGetEccState")
        self._get_total_ecc_errors = (
            _optional_callable(mxsml, "mxSmlGetTotalEccErrors")
            if _env_enabled(PYMXSML_ECC_ERRORS_ENV)
            else None
        )
        self._get_metaxlink_port_state = _optional_callable(
            mxsml, "mxSmlGetMetaXLinkPortState"
        )

        # Static values (versions, power limits) are cached after first fetch
        # so each refresh does not repeat C-library calls for data that never
        # changes while the process runs. Mirrors MxSmiBackend._versions.
        self._system_versions: tuple[str | None, str | None] | None = None
        self._device_driver_versions: dict[int, str | None] = {}
        self._device_power_limits: dict[int, float | None] = {}
        self._device_ecc_errors: dict[int, int | None] = {}
        self._ecc_error_cursor = 0

    def _versions(self) -> tuple[str | None, str | None]:
        if self._system_versions is None:
            driver = (
                _text(_safe(self._get_driver_version))
                if self._get_driver_version
                else None
            )
            maca = (
                _text(_safe(self._get_maca_version)) if self._get_maca_version else None
            )
            self._system_versions = (driver, maca)
        return self._system_versions

    def _device_driver_version(self, index: int) -> str | None:
        if index not in self._device_driver_versions:
            version = None
            if self._get_device_version is not None:
                version = _text(
                    _safe(
                        lambda: self._get_device_version(
                            index, self._version_driver_unit
                        )
                    )
                )
            self._device_driver_versions[index] = version
        return self._device_driver_versions[index]

    def _power_limit(self, index: int, handle: object | None) -> float | None:
        if index not in self._device_power_limits:
            raw: object | None = None
            if handle is not None and self._get_board_power_limit is not None:
                raw = _safe(lambda: self._get_board_power_limit(handle))
            if (
                not raw
                and handle is not None
                and self._get_power_mgmt_limit is not None
            ):
                raw = _safe(lambda: self._get_power_mgmt_limit(handle))
            if not raw and self._get_board_power_limit_core is not None:
                raw = _safe(lambda: self._get_board_power_limit_core(index))
            self._device_power_limits[index] = (
                normalize_power_w(_number(raw)) if raw else None
            )
        return self._device_power_limits[index]

    def _clock_mhz(
        self,
        index: int,
        handle: object | None,
        clock_types: tuple[int, ...],
    ) -> float | None:
        get_clock_info = getattr(self, "_get_clock_info", None)
        get_clocks = getattr(self, "_get_clocks", None)
        values: list[float] = []
        if get_clocks is not None:
            for clock_type in clock_types:
                values.extend(
                    _numeric_values(
                        _safe(
                            lambda clock_type=clock_type: get_clocks(
                                index, clock_type
                            )
                        )
                    )
                )
            if values:
                return max(values)
        if handle is not None and get_clock_info is not None:
            for clock_type in clock_types:
                values.extend(
                    _numeric_values(
                        _safe(
                            lambda clock_type=clock_type: get_clock_info(
                                handle, clock_type
                            )
                        )
                    )
                )
        return max(values) if values else None

    def _ecc_errors_for_snapshot(
        self,
        count: int,
        getter: Callable[..., object] | None,
    ) -> tuple[int | None, dict[int, int | None]]:
        cache = getattr(self, "_device_ecc_errors", None)
        if cache is None:
            cache = self._device_ecc_errors = {}
        if count <= 0 or getter is None:
            return None, cache
        cursor = getattr(self, "_ecc_error_cursor", 0)
        self._ecc_error_cursor = cursor + 1
        return cursor % count, cache

    def snapshot(self) -> FrameSnapshot:
        temperature_hotspot = self._temperature_hotspot
        get_board_power_info = self._get_board_power_info
        get_device_count = self._get_device_count
        get_device_info = self._get_device_info
        get_memory_info = self._get_memory_info
        get_temperature_info = self._get_temperature_info
        get_compute_processes = self._get_compute_processes
        get_graphics_processes = getattr(self, "_get_graphics_processes", None)
        get_handle_by_index = self._get_handle_by_index
        get_utilization_rates = self._get_utilization_rates
        get_power_usage = self._get_power_usage
        get_fan_speed = getattr(self, "_get_fan_speed", None)
        get_performance_state = getattr(self, "_get_performance_state", None)
        get_ecc_state = getattr(self, "_get_ecc_state", None)
        get_total_ecc_errors = getattr(self, "_get_total_ecc_errors", None)
        get_metaxlink_port_state = getattr(
            self, "_get_metaxlink_port_state", None
        )

        driver_version, maca_version = self._versions()

        devices: list[DeviceSnapshot] = []
        process_map: dict[tuple[int, int], ProcessSnapshot] = {}
        count = _integer(get_device_count()) or 0
        ecc_error_index, ecc_error_cache = self._ecc_errors_for_snapshot(
            count, get_total_ecc_errors
        )
        for index in range(count):
            info = _safe(lambda index=index: get_device_info(index))
            memory = _safe(lambda index=index: get_memory_info(index))
            handle = _safe(lambda index=index: get_handle_by_index(index))
            util = (
                _safe(lambda handle=handle: get_utilization_rates(handle))
                if handle
                else None
            )
            temperature = _safe(
                lambda index=index: get_temperature_info(index, temperature_hotspot)
            )
            fan_values = (
                _numeric_values(
                    _safe(lambda handle=handle: get_fan_speed(handle))
                )
                if handle is not None and get_fan_speed is not None
                else []
            )
            fan_percent = fan_values[0] if fan_values else None
            performance_state = (
                format_performance_state(
                    _safe(lambda handle=handle: get_performance_state(handle))
                )
                if handle is not None and get_performance_state is not None
                else None
            )
            gpu_clock_mhz = self._clock_mhz(
                index,
                handle,
                getattr(self, "_gpu_clock_types", ()),
            )
            memory_clock_mhz = self._clock_mhz(
                index,
                handle,
                getattr(self, "_memory_clock_types", ()),
            )
            ecc_status = (
                format_ecc_status(_safe(lambda index=index: get_ecc_state(index)))
                if get_ecc_state is not None
                else None
            )
            if index == ecc_error_index and get_total_ecc_errors is not None:
                refreshed_errors = total_ecc_errors(
                    _safe(lambda index=index: get_total_ecc_errors(index))
                )
                if refreshed_errors is not None or index not in ecc_error_cache:
                    ecc_error_cache[index] = refreshed_errors
            ecc_errors = ecc_error_cache.get(index)
            metaxlink = (
                format_metaxlink_status(
                    _safe(lambda index=index: get_metaxlink_port_state(index))
                )
                if get_metaxlink_port_state is not None
                else None
            )

            # Power draw: prefer the extension API, fall back to summing the
            # per-way board power readings from the core API.
            power_w = None
            if handle is not None and get_power_usage is not None:
                power_w = normalize_power_w(
                    _number(_safe(lambda handle=handle: get_power_usage(handle)))
                )
            if power_w is None:
                board_power = _safe(lambda index=index: get_board_power_info(index), [])
                power_values = [
                    _number_attr(item, "power") for item in _items(board_power)
                ]
                power_sum = sum(value for value in power_values if value is not None)
                if power_sum:
                    power_w = normalize_power_w(power_sum)

            # Power limit: extension board limit -> management limit -> core
            # API; static, so cached per device after the first fetch.
            power_limit_w = self._power_limit(index, handle)

            # Driver version: per-device fallback when the system-wide call failed.
            device_driver_version = driver_version
            if device_driver_version is None:
                device_driver_version = self._device_driver_version(index)

            memory_used = (
                used * 1024
                if (used := _int_attr(memory, "vramUse")) is not None
                else None
            )
            memory_total = (
                total * 1024
                if (total := _int_attr(memory, "vramTotal")) is not None
                else None
            )
            memory_free = (
                memory_total - memory_used
                if memory_total is not None and memory_used is not None
                else None
            )
            devices.append(
                DeviceSnapshot(
                    index=index,
                    name=_text(getattr(info, "deviceName", None)) or "MetaX GPU",
                    bdf=_text(getattr(info, "bdfId", None)),
                    uuid=_text(getattr(info, "uuid", None)),
                    temperature_c=normalize_temperature_c(_number(temperature)),
                    power_w=power_w,
                    power_limit_w=power_limit_w,
                    driver_version=device_driver_version,
                    maca_version=maca_version,
                    gpu_util_percent=_number_attr(util, "gpu"),
                    memory_util_percent=_number_attr(util, "memory"),
                    memory_used_bytes=memory_used,
                    memory_total_bytes=memory_total,
                    memory_free_bytes=memory_free,
                    fan_percent=fan_percent,
                    performance_state=performance_state,
                    ecc_status=ecc_status,
                    ecc_errors=ecc_errors,
                    metaxlink=metaxlink,
                    gpu_clock_mhz=gpu_clock_mhz,
                    memory_clock_mhz=memory_clock_mhz,
                )
            )

            if handle is not None:
                process_sources = [("C", get_compute_processes)]
                if get_graphics_processes is not None:
                    process_sources.append(("G", get_graphics_processes))
                for context, process_getter in process_sources:
                    raw_processes = _safe(
                        lambda process_getter=process_getter, handle=handle: (
                            process_getter(handle)
                        ),
                        [],
                    )
                    for process in _items(raw_processes):
                        pid = _int_attr(process, "pid") or 0
                        used = _int_attr(process, "usedGpuMemory")
                        if pid <= 0:
                            continue
                        used_memory = used if used is not None and used > 0 else None
                        key = (index, pid)
                        existing = process_map.get(key)
                        if existing is not None:
                            if existing.process_type != context:
                                existing.process_type = "C+G"
                            if used_memory is not None:
                                existing.gpu_memory_bytes = max(
                                    existing.gpu_memory_bytes or 0,
                                    used_memory,
                                )
                            continue
                        process_map[key] = ProcessSnapshot(
                            gpu_index=index,
                            pid=pid,
                            gpu_memory_bytes=used_memory,
                            process_type=context,
                            identity=f"{index}:{pid}",
                        )

        processes = list(process_map.values())
        enrich_processes(processes)
        return FrameSnapshot(devices=devices, processes=processes, backend=self.name)
