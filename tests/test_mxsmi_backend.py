from mxtop.backends.mxsmi import parse_dmon_csv, parse_process_table


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


def test_parse_process_table_handles_process_names_with_spaces():
    processes = parse_process_table(PROCESS_SAMPLE)

    assert len(processes) == 2
    assert processes[0].gpu_index == 0
    assert processes[0].pid == 967305
    assert processes[0].name == "python"
    assert processes[0].gpu_memory_bytes == 53978 * 1024**2
    assert processes[1].name == "python worker"


def test_parse_process_table_ignores_no_process_message():
    assert parse_process_table("|  no process found                                                               |") == []
