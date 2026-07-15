from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from mxtop.models import DeviceSnapshot, FrameSnapshot, ProcessSnapshot


def requested_process_contexts(
    *,
    compute: bool = False,
    only_compute: bool = False,
    graphics: bool = False,
    only_graphics: bool = False,
) -> frozenset[str]:
    contexts: set[str] = set()
    if compute or only_compute:
        contexts.add("C")
    if graphics or only_graphics:
        contexts.add("G")
    return frozenset(contexts)


def validate_process_contexts(
    frame: FrameSnapshot,
    *,
    requested: frozenset[str],
    supported: frozenset[str] | None = None,
) -> None:
    if not requested:
        return
    if supported is not None:
        normalized_supported = {value.upper() for value in supported}
        if "X" in normalized_supported:
            normalized_supported.update({"C", "G"})
        missing = requested.difference(normalized_supported)
        if missing:
            labels = {"C": "compute", "G": "graphics"}
            unavailable = ", ".join(
                labels.get(value, value) for value in sorted(missing)
            )
            backend = frame.backend or "selected"
            raise RuntimeError(
                f"process context filtering is unavailable for backend {backend!r}: "
                f"{unavailable} context telemetry is not supported"
            )
    if frame.processes and any(
        not process.process_type or not process.process_type.strip()
        for process in frame.processes
    ):
        backend = frame.backend or "selected"
        raise RuntimeError(
            f"process context filtering is unavailable for backend {backend!r}: "
            "process type telemetry was not reported"
        )


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


def resolve_visible_device_indices(
    devices: list[DeviceSnapshot],
    identifiers: Iterable[str] | None,
) -> set[int] | None:
    """Resolve a CUDA-style visibility list against a sampled frame.

    Integer indices and textual identifiers (UUID or BDF prefixes) are each
    supported, but a visibility list cannot mix the two forms.  Resolution
    stops at the first invalid identifier, matching CUDA enumeration, while a
    duplicate or ambiguous mapping invalidates the entire list.
    """
    if identifiers is None:
        return None

    devices_by_index = {device.index: device for device in devices}
    resolved: list[int] = []
    presented: set[str] = set()
    identifier_kind: str | None = None
    for raw_identifier in identifiers:
        identifier = raw_identifier.strip()
        if not identifier:
            break

        is_index = identifier.isdigit()
        kind = "index" if is_index else "text"
        if identifier_kind is None:
            identifier_kind = kind
        elif kind != identifier_kind:
            break

        presented_key = identifier if is_index else identifier.lower()
        if presented_key in presented:
            return set()
        presented.add(presented_key)

        if is_index:
            index = int(identifier)
            if index not in devices_by_index:
                break
        else:
            normalized = identifier.lower()
            matches = [
                device.index
                for device in devices
                if any(
                    value and value.lower().startswith(normalized)
                    for value in (device.uuid, device.bdf)
                )
            ]
            if len(matches) != 1:
                break
            index = matches[0]

        if index in resolved:
            return set()
        resolved.append(index)
    return set(resolved)


def filter_devices(
    devices: list[DeviceSnapshot], only: set[int] | None = None
) -> list[DeviceSnapshot]:
    if only is None:
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
    compute: bool = False,
    only_compute: bool = False,
    graphics: bool = False,
    only_graphics: bool = False,
) -> list[ProcessSnapshot]:
    result: list[ProcessSnapshot] = []
    normalized_types = (
        {value.upper() for value in process_types} if process_types else None
    )
    for process in processes:
        if device_indices is not None and process.gpu_index not in device_indices:
            continue
        if users is not None and process.user not in users:
            continue
        if pids is not None and process.pid not in pids:
            continue
        if normalized_types:
            if process.process_type is None:
                if require_process_type:
                    continue
            elif not set(process.process_type.upper()).intersection(normalized_types):
                continue
        if compute or only_compute or graphics or only_graphics:
            process_type = (process.process_type or "").upper()
            has_compute = "C" in process_type or "X" in process_type
            has_graphics = "G" in process_type or "X" in process_type
            exactly_compute = "C" in process_type and not has_graphics
            exactly_graphics = "G" in process_type and not has_compute
            predicates = (
                not compute or has_compute,
                not only_compute or exactly_compute,
                not graphics or has_graphics,
                not only_graphics or exactly_graphics,
            )
            if not all(predicates):
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
    compute: bool = False,
    only_compute: bool = False,
    graphics: bool = False,
    only_graphics: bool = False,
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
            compute=compute,
            only_compute=only_compute,
            graphics=graphics,
            only_graphics=only_graphics,
        ),
    )
