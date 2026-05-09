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

    def snapshot(self) -> FrameSnapshot:
        mxsml = _module("pymxsml")
        mxsml_extension = _module("pymxsml.mxsml_extension")
        temperature_hotspot = _integer(getattr(mxsml, "MXSML_TEMPERATURE_HOTSPOT")) or 0
        get_board_power_info = _callable(mxsml, "mxSmlGetBoardPowerInfo")
        get_device_count = _callable(mxsml, "mxSmlGetDeviceCount")
        get_device_info = _callable(mxsml, "mxSmlGetDeviceInfo")
        get_memory_info = _callable(mxsml, "mxSmlGetMemoryInfo")
        get_temperature_info = _callable(mxsml, "mxSmlGetTemperatureInfo")
        get_compute_processes = _callable(mxsml_extension, "mxSmlExDeviceGetComputeRunningProcesses")
        get_handle_by_index = _callable(mxsml_extension, "mxSmlExDeviceGetHandleByIndex")
        get_utilization_rates = _callable(mxsml_extension, "mxSmlExDeviceGetUtilizationRates")

        devices: list[DeviceSnapshot] = []
        processes: list[ProcessSnapshot] = []
        count = _integer(get_device_count()) or 0
        for index in range(count):
            info = _safe(lambda index=index: get_device_info(index))
            memory = _safe(lambda index=index: get_memory_info(index))
            handle = _safe(lambda index=index: get_handle_by_index(index))
            util = _safe(lambda handle=handle: get_utilization_rates(handle)) if handle else None
            temperature = _safe(lambda index=index: get_temperature_info(index, temperature_hotspot))
            board_power = _safe(lambda index=index: get_board_power_info(index), [])
            power_w = None
            power_values = [_number_attr(item, "power") for item in _items(board_power)]
            power_sum = sum(value for value in power_values if value is not None)
            if power_sum:
                power_w = normalize_power_w(power_sum)

            devices.append(
                DeviceSnapshot(
                    index=index,
                    name=str(getattr(info, "deviceName", "MetaX GPU")),
                    bdf=str(getattr(info, "bdfId", "")) or None,
                    uuid=str(getattr(info, "uuid", "")) or None,
                    temperature_c=normalize_temperature_c(_number(temperature)),
                    power_w=power_w,
                    gpu_util_percent=_number_attr(util, "gpu"),
                    memory_util_percent=_number_attr(util, "memory"),
                    memory_used_bytes=(used * 1024 if (used := _int_attr(memory, "vramUse")) is not None else None),
                    memory_total_bytes=(total * 1024 if (total := _int_attr(memory, "vramTotal")) is not None else None),
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
                        )
                    )

        enrich_processes(processes)
        return FrameSnapshot(devices=devices, processes=processes, backend=self.name)
