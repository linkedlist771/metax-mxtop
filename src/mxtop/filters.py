from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from mxtop.models import DeviceSnapshot, FrameSnapshot, ProcessSnapshot


def normalize_indices(indices: Iterable[int] | None) -> set[int] | None:
    if indices is None:
        return None
    return {int(index) for index in indices}


def normalize_strings(values: Iterable[str] | None) -> set[str] | None:
    if values is None:
        return None
    return {value for value in values if value}


def normalize_pids(values: Iterable[int] | None) -> set[int] | None:
    if values is None:
        return None
    return {int(value) for value in values}


def filter_devices(devices: list[DeviceSnapshot], only: set[int] | None = None) -> list[DeviceSnapshot]:
    if not only:
        return list(devices)
    return [device for device in devices if device.index in only]


def filter_processes(
    processes: list[ProcessSnapshot],
    *,
    device_indices: set[int] | None = None,
    users: set[str] | None = None,
    pids: set[int] | None = None,
    process_types: set[str] | None = None,
    require_process_type: bool = False,
) -> list[ProcessSnapshot]:
    result: list[ProcessSnapshot] = []
    normalized_types = {value.upper() for value in process_types} if process_types else None
    for process in processes:
        if device_indices and process.gpu_index not in device_indices:
            continue
        if users and process.user not in users:
            continue
        if pids and process.pid not in pids:
            continue
        if normalized_types:
            if process.process_type is None:
                if require_process_type:
                    continue
            elif not set(process.process_type.upper()).intersection(normalized_types):
                continue
        result.append(process)
    return result


def apply_filters(
    frame: FrameSnapshot,
    *,
    device_indices: set[int] | None = None,
    users: set[str] | None = None,
    pids: set[int] | None = None,
    process_types: set[str] | None = None,
    require_process_type: bool = False,
) -> FrameSnapshot:
    return replace(
        frame,
        devices=filter_devices(frame.devices, device_indices),
        processes=filter_processes(
            frame.processes,
            device_indices=device_indices,
            users=users,
            pids=pids,
            process_types=process_types,
            require_process_type=require_process_type,
        ),
    )
