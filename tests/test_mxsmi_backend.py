import subprocess
from subprocess import CompletedProcess

import pytest

from mxtop.backends.mxsmi import (
    MxSmiBackend,
    build_frame_from_outputs,
    parse_dmon_csv,
    parse_list_output,
    parse_process_table,
    parse_versions,
    resolve_mxsmi_path,
)


DMON_SAMPLE = """dev, die, hottemp, soctemp, coretemp, power, gpu, vpue, vpud, visvram, vram, xtt, total, bdfid
idx, idx, C, C, C, W, %, %, %, %, %, %, GB,
0, 0, 45, 31, 40, 159, 71, 0, 0, 83, 83, 1, 64, 0000:08:00.0
1, 0, 46, 31, 36, 165, 12, 0, 0, 84, 84, 1, 64, 0000:09:00.0
"""


PROCESS_SAMPLE = """
+---------------------------------------------------------------------------------+
| Process:                                                                        |
|  GPU                    PID         Process Name                 GPU Memory     |
|                                                                  Usage(MiB)     |
|=================================================================================|
|  0                   967305         python                       53978          |
|  1                   967306         python worker                54260          |
+---------------------------------------------------------------------------------+
"""


def test_parse_dmon_csv_builds_device_snapshots():
    devices = parse_dmon_csv(DMON_SAMPLE)

    assert len(devices) == 2
    assert devices[0].index == 0
    assert devices[0].bdf == "0000:08:00.0"
    assert devices[0].temperature_c == 45.0
    assert devices[0].power_w == 159.0
    assert devices[0].gpu_util_percent == 71.0
    assert devices[0].memory_total_bytes == 64 * 1024**3
    assert devices[0].memory_used_bytes == int(64 * 1024**3 * 0.83)
    assert devices[0].memory_free_bytes == 64 * 1024**3 - int(64 * 1024**3 * 0.83)


def test_parse_dmon_csv_uses_known_device_names():
    known = parse_list_output("GPU 0: MXC500 (UUID: MX-abc)\n")
    devices = parse_dmon_csv(DMON_SAMPLE, known_devices=known)

    assert devices[0].name == "MXC500"
    assert devices[0].uuid == "MX-abc"


def test_parse_dmon_csv_reads_optional_clock_columns():
    sample = """dev, xcoreclk, mcclk, gpu, total
0, 1800 MHz, 1600 MHz, 71, 64
"""

    device = parse_dmon_csv(sample)[0]

    assert device.gpu_clock_mhz == 1800.0
    assert device.memory_clock_mhz == 1600.0


def test_parse_list_output_builds_device_map():
    devices = parse_list_output("""
GPU 0: MXC500 (UUID: MX-abc)
GPU 1: MXC550
""")

    assert devices[0].name == "MXC500"
    assert devices[0].uuid == "MX-abc"
    assert devices[1].name == "MXC550"


def test_parse_list_output_handles_hash_indexed_list_format():
    # Mirrors the `mx-smi -L` layout ("GPU#<n>  NAME  BDF  STATE (UUID: ...)")
    # with dummy identifiers.
    devices = parse_list_output("""mx-smi  version: 0.0.0
GPU#0    MXTEST-00    0000:01:00.0    Available (UUID: GPU-00000000-0000-0000-0000-000000000000)
GPU#1    MXTEST-00    0000:02:00.0    Available (UUID: GPU-11111111-1111-1111-1111-111111111111)
GPU#15   MXTEST-00    0000:0f:00.0    Available (UUID: GPU-22222222-2222-2222-2222-222222222222)
""")

    assert set(devices) == {0, 1, 15}
    assert devices[0].name == "MXTEST-00"
    assert devices[0].bdf == "0000:01:00.0"
    assert devices[0].uuid == "GPU-00000000-0000-0000-0000-000000000000"
    assert devices[15].bdf == "0000:0f:00.0"


def test_parse_process_table_handles_process_names_with_spaces():
    processes = parse_process_table(PROCESS_SAMPLE)

    assert len(processes) == 2
    assert processes[0].gpu_index == 0
    assert processes[0].pid == 967305
    assert processes[0].name == "python"
    assert processes[0].gpu_memory_bytes == 53978 * 1024**2
    assert processes[0].identity == "0:967305"
    assert processes[1].name == "python worker"


def test_parse_versions_extracts_driver_and_maca():
    block = (
        "| MX-SMI 0.0.0                     Kernel Mode Driver Version: 1.2.3      |\n"
        "| MACA Version: 4.5.6            BIOS Version: 0.0.0                     |\n"
    )
    assert parse_versions(block) == ("1.2.3", "4.5.6")


def test_parse_versions_missing_returns_none():
    assert parse_versions("nothing here") == (None, None)


def test_build_frame_stamps_versions_on_devices():
    frame = build_frame_from_outputs(
        DMON_SAMPLE,
        "",
        backend_name="mx-smi",
        enrich=False,
        driver_version="1.2.3",
        maca_version="4.5.6",
    )
    assert frame.devices
    assert all(d.driver_version == "1.2.3" for d in frame.devices)
    assert all(d.maca_version == "4.5.6" for d in frame.devices)


def test_parse_process_table_handles_memory_units():
    processes = parse_process_table("|  0  123  python train.py  1.5GiB  |")

    assert processes[0].gpu_memory_bytes == int(1.5 * 1024**3)
    assert processes[0].name == "python train.py"


def test_parse_process_table_reads_optional_compute_and_graphics_contexts():
    processes = parse_process_table(
        "\n".join(
            (
                "| GPU PID Type Process Name GPU Memory |",
                "| 0 123 C python train.py 1GiB |",
                "| 1 124 G compositor 512MiB |",
                "| 1 125 C+G shared-context 256MiB |",
                "| 2 126 X combined-context 128MiB |",
            )
        )
    )

    assert [process.process_type for process in processes] == ["C", "G", "C+G", "X"]
    assert processes[0].name == "python train.py"


def test_parse_process_table_does_not_infer_context_without_a_type_header():
    processes = parse_process_table("| 0 123 G compositor worker 512MiB |")

    assert processes[0].process_type is None
    assert processes[0].name == "G compositor worker"


def test_parse_process_table_preserves_unavailable_context_in_typed_table():
    processes = parse_process_table(
        "| GPU PID Type Process Name GPU Memory |\n"
        "| 0 123 N/A python worker 512MiB |"
    )

    assert len(processes) == 1
    assert processes[0].process_type is None
    assert processes[0].name == "python worker"


def test_parse_process_table_ignores_no_process_message():
    assert (
        parse_process_table(
            "|  no process found                                                               |"
        )
        == []
    )


def test_resolve_mxsmi_path_prefers_explicit_path(monkeypatch):
    monkeypatch.setenv("MXTOP_MXSMI_PATH", "/env/mx-smi")

    assert resolve_mxsmi_path("/custom/mx-smi") == "/custom/mx-smi"


def test_resolve_mxsmi_path_uses_environment(monkeypatch):
    monkeypatch.setenv("MXTOP_MXSMI_PATH", "/env/mx-smi")

    assert resolve_mxsmi_path() == "/env/mx-smi"


def test_backend_uses_resolved_executable(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        sub = args[1] if len(args) > 1 else ""
        if sub == "-L":
            return CompletedProcess(args, 0, "GPU 0: MXC500 (UUID: MX-abc)\n", "")
        if sub == "--show-version":
            return CompletedProcess(
                args, 0, "Kernel Mode Driver Version: 1.2.3\nMACA Version: 4.5.6\n", ""
            )
        if sub == "dmon":
            return CompletedProcess(args, 0, DMON_SAMPLE, "")
        if sub == "--show-process":
            return CompletedProcess(args, 0, PROCESS_SAMPLE, "")
        return CompletedProcess(args, 1, "", "")

    monkeypatch.setattr("mxtop.backends.mxsmi.subprocess.run", fake_run)

    backend = MxSmiBackend("/opt/mxdriver/bin/mx-smi", timeout=3.5)
    frame = backend.snapshot()
    _ = backend.snapshot()

    commands = [args for args, _kwargs in calls]
    assert commands[0][0] == "/opt/mxdriver/bin/mx-smi"
    assert sum(command[1:2] == ["-L"] for command in commands) == 1
    assert sum(command[1:2] == ["--show-version"] for command in commands) == 1
    assert sum(command[1:2] == ["dmon"] for command in commands) == 2
    assert sum(command[1:2] == ["--show-process"] for command in commands) == 2
    assert all(kwargs["timeout"] == 3.5 for _args, kwargs in calls)
    assert all(kwargs["errors"] == "replace" for _args, kwargs in calls)
    assert "--show-clock" in next(
        command for command in commands if command[1:2] == ["dmon"]
    )
    assert frame.devices[0].name == "MXC500"
    assert frame.devices[0].driver_version == "1.2.3"
    assert frame.devices[0].maca_version == "4.5.6"
    assert frame.processes[0].pid == 967305


def test_backend_propagates_mxsmi_timeout(monkeypatch):
    def timed_out(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr("mxtop.backends.mxsmi.subprocess.run", timed_out)

    with pytest.raises(subprocess.TimeoutExpired):
        MxSmiBackend("/opt/mxdriver/bin/mx-smi", timeout=0.5).snapshot()


def test_recorded_style_mxsmi_outputs_preserve_a_64_gpu_fleet():
    list_output = "\n".join(
        f"GPU#{index}    MXTEST-64    0000:{index + 1:02x}:00.0    Available "
        f"(UUID: GPU-{index:032x})"
        for index in range(64)
    )
    dmon_output = "\n".join(
        [
            "dev,hottemp,power,gpu,vram,total,bdfid,fan,pstate,ecc,gpuclock,memoryclock",
            *(
                f"{index},{40 + index % 8},{120 + index},{index % 100},{(index * 3) % 100},"
                f"64,0000:{index + 1:02x}:00.0,{20 + index % 70},P{index % 4},Enabled,"
                f"{1500 + index},{1200 + index}"
                for index in range(64)
            ),
        ]
    )
    process_output = "\n".join(
        f"| {index} {10000 + index} python worker-{index}.py {1024 + index}MiB |"
        for index in range(64)
    )

    frame = build_frame_from_outputs(
        dmon_output,
        process_output,
        known_devices=parse_list_output(list_output),
        enrich=False,
        driver_version="1.2.3",
        maca_version="4.5.6",
    )

    assert len(frame.devices) == 64
    assert len(frame.processes) == 64
    assert frame.devices[-1].index == 63
    assert frame.devices[-1].name == "MXTEST-64"
    assert frame.devices[-1].fan_percent == 83.0
    assert frame.devices[-1].gpu_clock_mhz == 1563.0
    assert frame.devices[-1].memory_clock_mhz == 1263.0
    assert frame.devices[-1].driver_version == "1.2.3"
    assert frame.devices[-1].maca_version == "4.5.6"
    assert frame.processes[-1].gpu_index == 63
    assert frame.processes[-1].pid == 10063
    assert frame.processes[-1].gpu_memory_bytes == 1087 * 1024**2
