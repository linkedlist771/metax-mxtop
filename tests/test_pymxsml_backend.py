import ctypes
from types import SimpleNamespace

from mxtop.backends import pymxsml
from mxtop.backends.pymxsml import (
    PYMXSML_ECC_ERRORS_ENV,
    PymxsmlBackend,
    _env_enabled,
    format_ecc_status,
    format_metaxlink_status,
    format_performance_state,
    normalize_power_w,
    normalize_temperature_c,
    total_ecc_errors,
)


def test_normalize_temperature_handles_scaled_pymxsml_values():
    assert normalize_temperature_c(4775) == 47.75
    assert normalize_temperature_c(47.75) == 47.75


def test_normalize_power_handles_scaled_pymxsml_values():
    assert normalize_power_w(181800) == 181.8
    assert normalize_power_w(181.8) == 181.8


def test_optional_device_metric_normalizers_are_stable():
    assert format_performance_state(0) == "P0"
    assert format_performance_state(15) == "P15"
    assert format_performance_state(32) is None
    assert format_ecc_status(0) == "Disabled"
    assert format_ecc_status(1) == "Enabled"
    assert total_ecc_errors(
        SimpleNamespace(dramCE=1, dramUE=2, sramCE=3, sramUE=4)
    ) == 10
    assert format_metaxlink_status([0, 1, 5]) == "Active"
    assert format_metaxlink_status([0, 2, 5]) == "Down"
    assert format_metaxlink_status([0, 5, 5]) == "Inactive"
    assert format_metaxlink_status((ctypes.c_uint * 3)(0, 1, 5)) == "Active"


def test_slow_ecc_error_polling_requires_explicit_environment_opt_in(monkeypatch):
    for value in ("1", "true", "YES", "on"):
        monkeypatch.setenv(PYMXSML_ECC_ERRORS_ENV, value)
        assert _env_enabled(PYMXSML_ECC_ERRORS_ENV)
    for value in ("", "0", "false", "unexpected"):
        monkeypatch.setenv(PYMXSML_ECC_ERRORS_ENV, value)
        assert not _env_enabled(PYMXSML_ECC_ERRORS_ENV)


def test_clock_prefers_core_api_and_uses_extension_only_as_fallback():
    backend = object.__new__(PymxsmlBackend)
    extension_calls = []
    backend._get_clock_info = lambda handle, clock_type: extension_calls.append(
        (handle, clock_type)
    )
    backend._get_clocks = lambda _index, _clock_type: 1800

    assert backend._clock_mhz(0, 1, (11,)) == 1800.0
    assert extension_calls == []

    backend._get_clocks = lambda _index, _clock_type: None
    backend._get_clock_info = lambda handle, clock_type: (
        extension_calls.append((handle, clock_type)) or 1700
    )
    assert backend._clock_mhz(0, 1, (11,)) == 1700.0
    assert extension_calls == [(1, 11)]


def test_pymxsml_snapshot_preserves_64_devices_and_processes(monkeypatch):
    backend = object.__new__(PymxsmlBackend)
    ecc_calls = []
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
    backend._get_total_ecc_errors = lambda index: (
        ecc_calls.append(index)
        or SimpleNamespace(dramCE=index, dramUE=0, sramCE=0, sramUE=0)
    )
    backend._power_limit = lambda index, handle: 350.0
    backend._versions = lambda: ("1.2.3", "4.5.6")
    backend._device_driver_version = lambda index: None
    monkeypatch.setattr(pymxsml, "enrich_processes", lambda processes: None)

    frame = backend.snapshot()
    next_frame = backend.snapshot()

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
    assert ecc_calls == [0, 1]
    assert frame.devices[0].ecc_errors == 0
    assert frame.devices[1].ecc_errors is None
    assert next_frame.devices[0].ecc_errors == 0
    assert next_frame.devices[1].ecc_errors == 1


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


def test_pymxsml_snapshot_collects_optional_read_only_device_metrics(monkeypatch):
    backend = object.__new__(PymxsmlBackend)
    backend._temperature_hotspot = 0
    backend._get_device_count = lambda: 1
    backend._get_device_info = lambda _index: SimpleNamespace(
        deviceName=b"MXTEST",
        bdfId=b"0000:01:00.0",
        uuid=b"GPU-test",
    )
    backend._get_memory_info = lambda _index: SimpleNamespace(
        vramUse=1024**2,
        vramTotal=64 * 1024**3,
    )
    backend._get_handle_by_index = lambda _index: 1
    backend._get_utilization_rates = lambda _handle: SimpleNamespace(
        gpu=25.0,
        memory=50.0,
    )
    backend._get_temperature_info = lambda _index, _unit: 4200
    backend._get_board_power_info = lambda _index: []
    backend._get_compute_processes = lambda _handle: []
    backend._get_graphics_processes = None
    backend._get_power_usage = None
    backend._power_limit = lambda _index, _handle: 350.0
    backend._versions = lambda: ("1.2.3", "4.5.6")
    backend._device_driver_version = lambda _index: None
    backend._get_fan_speed = lambda _handle: 42
    backend._get_performance_state = lambda _handle: 8
    backend._get_clock_info = lambda _handle, clock_type: {
        11: 1800,
        3: 1600,
    }[clock_type]
    backend._get_clocks = None
    backend._gpu_clock_types = (11,)
    backend._memory_clock_types = (3,)
    backend._get_ecc_state = lambda _index: 1
    backend._get_total_ecc_errors = lambda _index: SimpleNamespace(
        dramCE=1,
        dramUE=2,
        sramCE=3,
        sramUE=4,
    )
    backend._get_metaxlink_port_state = lambda _index: [0, 1, 5]
    monkeypatch.setattr(pymxsml, "enrich_processes", lambda processes: None)

    device = backend.snapshot().devices[0]

    assert device.name == "MXTEST"
    assert device.fan_percent == 42.0
    assert device.performance_state == "P8"
    assert device.gpu_clock_mhz == 1800.0
    assert device.memory_clock_mhz == 1600.0
    assert device.ecc_status == "Enabled"
    assert device.ecc_errors == 10
    assert device.metaxlink == "Active"
