from types import SimpleNamespace

from mxtop.backends import pymxsml
from mxtop.backends.pymxsml import (
    PymxsmlBackend,
    normalize_power_w,
    normalize_temperature_c,
)


def test_normalize_temperature_handles_scaled_pymxsml_values():
    assert normalize_temperature_c(4775) == 47.75
    assert normalize_temperature_c(47.75) == 47.75


def test_normalize_power_handles_scaled_pymxsml_values():
    assert normalize_power_w(181800) == 181.8
    assert normalize_power_w(181.8) == 181.8


def test_pymxsml_snapshot_preserves_64_devices_and_processes(monkeypatch):
    backend = object.__new__(PymxsmlBackend)
    backend._temperature_hotspot = 0
    backend._get_device_count = lambda: 64
    backend._get_device_info = lambda index: SimpleNamespace(
        deviceName="MXTEST-64",
        bdfId=f"0000:{index + 1:02x}:00.0",
        uuid=f"GPU-{index:032x}",
    )
    backend._get_memory_info = lambda index: SimpleNamespace(
        vramUse=(index + 1) * 1024**2,
        vramTotal=64 * 1024**3,
    )
    backend._get_handle_by_index = lambda index: index + 1
    backend._get_utilization_rates = lambda handle: SimpleNamespace(
        gpu=float((handle * 3) % 100),
        memory=float((handle * 5) % 100),
    )
    backend._get_temperature_info = lambda index, _unit: 4000 + index
    backend._get_board_power_info = lambda index: [SimpleNamespace(power=100 + index)]
    backend._get_compute_processes = lambda handle: [
        SimpleNamespace(pid=20000 + handle - 1, usedGpuMemory=handle * 1024**2)
    ]
    backend._get_graphics_processes = None
    backend._get_power_usage = None
    backend._power_limit = lambda index, handle: 350.0
    backend._versions = lambda: ("1.2.3", "4.5.6")
    backend._device_driver_version = lambda index: None
    monkeypatch.setattr(pymxsml, "enrich_processes", lambda processes: None)

    frame = backend.snapshot()

    assert frame.backend == "pymxsml"
    assert len(frame.devices) == 64
    assert len(frame.processes) == 64
    assert frame.devices[-1].index == 63
    assert frame.devices[-1].name == "MXTEST-64"
    assert frame.devices[-1].temperature_c == 40.63
    assert frame.devices[-1].power_w == 163.0
    assert frame.devices[-1].power_limit_w == 350.0
    assert frame.devices[-1].driver_version == "1.2.3"
    assert frame.devices[-1].maca_version == "4.5.6"
    assert frame.processes[-1].gpu_index == 63
    assert frame.processes[-1].pid == 20063
    assert frame.processes[-1].process_type == "C"


def test_pymxsml_merges_compute_and_graphics_process_contexts(monkeypatch):
    backend = object.__new__(PymxsmlBackend)
    backend._temperature_hotspot = 0
    backend._get_device_count = lambda: 1
    backend._get_device_info = lambda _index: SimpleNamespace(
        deviceName="MXTEST",
        bdfId="0000:01:00.0",
        uuid="GPU-test",
    )
    backend._get_memory_info = lambda _index: SimpleNamespace(
        vramUse=1024**2,
        vramTotal=64 * 1024**3,
    )
    backend._get_handle_by_index = lambda _index: 1
    backend._get_utilization_rates = lambda _handle: SimpleNamespace(
        gpu=1.0, memory=2.0
    )
    backend._get_temperature_info = lambda _index, _unit: 4000
    backend._get_board_power_info = lambda _index: []
    backend._get_compute_processes = lambda _handle: [
        SimpleNamespace(pid=100, usedGpuMemory=10 * 1024**2)
    ]
    backend._get_graphics_processes = lambda _handle: [
        SimpleNamespace(pid=100, usedGpuMemory=12 * 1024**2),
        SimpleNamespace(pid=101, usedGpuMemory=5 * 1024**2),
        SimpleNamespace(pid=102, usedGpuMemory=None),
    ]
    backend._get_power_usage = None
    backend._power_limit = lambda _index, _handle: None
    backend._versions = lambda: (None, None)
    backend._device_driver_version = lambda _index: None
    monkeypatch.setattr(pymxsml, "enrich_processes", lambda processes: None)

    frame = backend.snapshot()

    assert [(process.pid, process.process_type) for process in frame.processes] == [
        (100, "C+G"),
        (101, "G"),
        (102, "G"),
    ]
    assert frame.processes[0].gpu_memory_bytes == 12 * 1024**2
    assert frame.processes[2].gpu_memory_bytes is None
