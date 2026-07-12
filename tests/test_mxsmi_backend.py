from subprocess import CompletedProcess

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
    sample = """dev, gpu clock, memory-clock, gpu, total
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
        DMON_SAMPLE, "", backend_name="mx-smi", enrich=False,
        driver_version="1.2.3", maca_version="4.5.6",
    )
    assert frame.devices
    assert all(d.driver_version == "1.2.3" for d in frame.devices)
    assert all(d.maca_version == "4.5.6" for d in frame.devices)


def test_parse_process_table_handles_memory_units():
    processes = parse_process_table("|  0  123  python train.py  1.5GiB  |")

    assert processes[0].gpu_memory_bytes == int(1.5 * 1024**3)
    assert processes[0].name == "python train.py"


def test_parse_process_table_ignores_no_process_message():
    assert parse_process_table("|  no process found                                                               |") == []


def test_resolve_mxsmi_path_prefers_explicit_path(monkeypatch):
    monkeypatch.setenv("MXTOP_MXSMI_PATH", "/env/mx-smi")

    assert resolve_mxsmi_path("/custom/mx-smi") == "/custom/mx-smi"


def test_resolve_mxsmi_path_uses_environment(monkeypatch):
    monkeypatch.setenv("MXTOP_MXSMI_PATH", "/env/mx-smi")

    assert resolve_mxsmi_path() == "/env/mx-smi"


def test_backend_uses_resolved_executable(monkeypatch):
    calls = []

    def fake_run(args, check, text, capture_output):
        calls.append(args)
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

    frame = MxSmiBackend("/opt/mxdriver/bin/mx-smi").snapshot()

    assert calls[0][0] == "/opt/mxdriver/bin/mx-smi"
    assert frame.devices[0].name == "MXC500"
    assert frame.devices[0].driver_version == "1.2.3"
    assert frame.devices[0].maca_version == "4.5.6"
    assert frame.processes[0].pid == 967305
