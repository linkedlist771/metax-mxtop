from __future__ import annotations

from collections.abc import Callable, Iterable
from glob import glob
import importlib
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
    candidates.extend(sorted(glob("/opt/mxn100/share/mxsml/pymxsml-*.whl"), reverse=True))
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


def _safe(call: Callable[[], T], default: T | None = None) -> T | None:
    try:
        return call()
    except Exception:
        return default


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
    if value is None:
        return ()
    if isinstance(value, Iterable):
        return value
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


class PymxsmlBackend:
    name: str = "pymxsml"

    def __init__(self) -> None:
        _load_pymxsml()
        mxsml = _module("pymxsml")
        mxsml_extension = _module("pymxsml.mxsml_extension")

        _ = _callable(mxsml, "mxSmlInit")()
        _ = _callable(mxsml_extension, "mxSmlExInit")()

        # Resolve attributes once: module/function lookups are static.
        self._temperature_hotspot = _integer(getattr(mxsml, "MXSML_TEMPERATURE_HOTSPOT", 0)) or 0
        self._version_driver_unit = _integer(getattr(mxsml, "MXSML_VERSION_DRIVER", 1)) or 1
        self._get_board_power_info = _callable(mxsml, "mxSmlGetBoardPowerInfo")
        self._get_device_count = _callable(mxsml, "mxSmlGetDeviceCount")
        self._get_device_info = _callable(mxsml, "mxSmlGetDeviceInfo")
        self._get_memory_info = _callable(mxsml, "mxSmlGetMemoryInfo")
        self._get_temperature_info = _callable(mxsml, "mxSmlGetTemperatureInfo")
        self._get_compute_processes = _callable(mxsml_extension, "mxSmlExDeviceGetComputeRunningProcesses")
        self._get_handle_by_index = _callable(mxsml_extension, "mxSmlExDeviceGetHandleByIndex")
        self._get_utilization_rates = _callable(mxsml_extension, "mxSmlExDeviceGetUtilizationRates")
        self._get_maca_version = _optional_callable(mxsml, "mxSmlGetMacaVersion")
        self._get_device_version = _optional_callable(mxsml, "mxSmlGetDeviceVersion")
        self._get_driver_version = _optional_callable(mxsml_extension, "mxSmlExSystemGetDriverVersion")
        self._get_power_usage = _optional_callable(mxsml_extension, "mxSmlExDeviceGetPowerUsage")
        self._get_board_power_limit = _optional_callable(mxsml_extension, "mxSmlExDeviceGetBoardPowerLimit")
        self._get_power_mgmt_limit = _optional_callable(mxsml_extension, "mxSmlExDeviceGetPowerManagementLimit")
        self._get_board_power_limit_core = _optional_callable(mxsml, "mxSmlGetBoardPowerLimit")

        # Static values (versions, power limits) are cached after first fetch
        # so each refresh does not repeat C-library calls for data that never
        # changes while the process runs. Mirrors MxSmiBackend._versions.
        self._system_versions: tuple[str | None, str | None] | None = None
        self._device_driver_versions: dict[int, str | None] = {}
        self._device_power_limits: dict[int, float | None] = {}

    def _versions(self) -> tuple[str | None, str | None]:
        if self._system_versions is None:
            driver = _text(_safe(self._get_driver_version)) if self._get_driver_version else None
            maca = _text(_safe(self._get_maca_version)) if self._get_maca_version else None
            self._system_versions = (driver, maca)
        return self._system_versions

    def _device_driver_version(self, index: int) -> str | None:
        if index not in self._device_driver_versions:
            version = None
            if self._get_device_version is not None:
                version = _text(
                    _safe(lambda: self._get_device_version(index, self._version_driver_unit))
                )
            self._device_driver_versions[index] = version
        return self._device_driver_versions[index]

    def _power_limit(self, index: int, handle: object | None) -> float | None:
        if index not in self._device_power_limits:
            raw: object | None = None
            if handle is not None and self._get_board_power_limit is not None:
                raw = _safe(lambda: self._get_board_power_limit(handle))
            if not raw and handle is not None and self._get_power_mgmt_limit is not None:
                raw = _safe(lambda: self._get_power_mgmt_limit(handle))
            if not raw and self._get_board_power_limit_core is not None:
                raw = _safe(lambda: self._get_board_power_limit_core(index))
            self._device_power_limits[index] = normalize_power_w(_number(raw)) if raw else None
        return self._device_power_limits[index]

    def snapshot(self) -> FrameSnapshot:
        temperature_hotspot = self._temperature_hotspot
        get_board_power_info = self._get_board_power_info
        get_device_count = self._get_device_count
        get_device_info = self._get_device_info
        get_memory_info = self._get_memory_info
        get_temperature_info = self._get_temperature_info
        get_compute_processes = self._get_compute_processes
        get_handle_by_index = self._get_handle_by_index
        get_utilization_rates = self._get_utilization_rates
        get_power_usage = self._get_power_usage

        driver_version, maca_version = self._versions()

        devices: list[DeviceSnapshot] = []
        processes: list[ProcessSnapshot] = []
        count = _integer(get_device_count()) or 0
        for index in range(count):
            info = _safe(lambda index=index: get_device_info(index))
            memory = _safe(lambda index=index: get_memory_info(index))
            handle = _safe(lambda index=index: get_handle_by_index(index))
            util = _safe(lambda handle=handle: get_utilization_rates(handle)) if handle else None
            temperature = _safe(lambda index=index: get_temperature_info(index, temperature_hotspot))

            # Power draw: prefer the extension API, fall back to summing the
            # per-way board power readings from the core API.
            power_w = None
            if handle is not None and get_power_usage is not None:
                power_w = normalize_power_w(_number(_safe(lambda handle=handle: get_power_usage(handle))))
            if power_w is None:
                board_power = _safe(lambda index=index: get_board_power_info(index), [])
                power_values = [_number_attr(item, "power") for item in _items(board_power)]
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

            memory_used = used * 1024 if (used := _int_attr(memory, "vramUse")) is not None else None
            memory_total = total * 1024 if (total := _int_attr(memory, "vramTotal")) is not None else None
            memory_free = memory_total - memory_used if memory_total is not None and memory_used is not None else None
            devices.append(
                DeviceSnapshot(
                    index=index,
                    name=str(getattr(info, "deviceName", "MetaX GPU")),
                    bdf=str(getattr(info, "bdfId", "")) or None,
                    uuid=str(getattr(info, "uuid", "")) or None,
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
                )
            )

            if handle is not None:
                for process in _items(_safe(lambda handle=handle: get_compute_processes(handle), [])):
                    pid = _int_attr(process, "pid") or 0
                    used = _int_attr(process, "usedGpuMemory") or 0
                    if pid <= 0 or used <= 0:
                        continue
                    processes.append(
                        ProcessSnapshot(
                            gpu_index=index,
                            pid=pid,
                            gpu_memory_bytes=used,
                            process_type="C",
                            identity=f"{index}:{pid}",
                        )
                    )

        enrich_processes(processes)
        return FrameSnapshot(devices=devices, processes=processes, backend=self.name)
