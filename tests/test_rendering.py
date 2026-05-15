from mxtop.models import DeviceSnapshot, FrameSnapshot, ProcessSnapshot
from mxtop.formatting import format_bar
from mxtop.rendering import render_once


def _many_devices(count=16):
    return [
        DeviceSnapshot(index=i, name="MXC500", gpu_util_percent=12, memory_util_percent=8)
        for i in range(count)
    ]


def test_render_once_includes_gpu_and_process_rows():
    frame = FrameSnapshot(
        devices=[
            DeviceSnapshot(
                index=0,
                name="MXC500",
                bdf="0000:08:00.0",
                temperature_c=45,
                power_w=159,
                gpu_util_percent=71,
                memory_used_bytes=56 * 1024**3,
                memory_total_bytes=64 * 1024**3,
            )
        ],
        processes=[
            ProcessSnapshot(
                gpu_index=0,
                pid=967305,
                name="python",
                gpu_memory_bytes=53978 * 1024**2,
                user="alice",
                command="python train.py",
            )
        ],
    )

    output = render_once(frame, use_color=False)

    assert "MXTOP" in output
    assert "MXC500" in output
    assert "0000:08:00.0" in output
    assert "71%" in output
    assert "python train.py" in output
    assert "53978MiB" in output


def test_render_once_includes_process_runtime():
    frame = FrameSnapshot(
        devices=[],
        processes=[
            ProcessSnapshot(
                gpu_index=0,
                pid=967305,
                gpu_memory_bytes=53978 * 1024**2,
                runtime_seconds=3723,
                command="python train.py",
            )
        ],
    )

    output = render_once(frame, use_color=False)

    assert "TIME" in output
    assert "1:02:03" in output


def test_format_bar_clamps_and_fills_blocks():
    assert format_bar(50, width=10) == "█████░░░░░"
    assert format_bar(120, width=4) == "████"
    assert format_bar(None, width=3) == "░░░"


def test_render_once_shows_bars_on_wide_layout():
    frame = FrameSnapshot(
        devices=[DeviceSnapshot(index=0, name="MXC500", gpu_util_percent=71, memory_util_percent=83)],
        processes=[],
    )

    output = render_once(frame, width=140, use_color=False)

    assert "UTL:" in output
    assert "MEM:" in output
    assert "83%" in output


def test_render_once_hides_bars_on_narrow_layout():
    frame = FrameSnapshot(
        devices=[DeviceSnapshot(index=0, name="MXC500", gpu_util_percent=71, memory_util_percent=83)],
        processes=[],
    )

    output = render_once(frame, width=90, use_color=False)

    assert "GPU-Util" in output
    assert "UTL:" not in output
    assert "█" not in output


def test_render_once_orders_processes_by_gpu_id_then_pid():
    frame = FrameSnapshot(
        devices=[],
        processes=[
            ProcessSnapshot(gpu_index=2, pid=20, gpu_memory_bytes=900 * 1024**2, command="gpu2-large"),
            ProcessSnapshot(gpu_index=0, pid=10, gpu_memory_bytes=100 * 1024**2, command="gpu0-small"),
            ProcessSnapshot(gpu_index=0, pid=11, gpu_memory_bytes=200 * 1024**2, command="gpu0-large"),
            ProcessSnapshot(gpu_index=1, pid=12, gpu_memory_bytes=300 * 1024**2, command="gpu1-mid"),
        ],
    )

    output = render_once(frame, width=120, use_color=False)

    assert output.index("gpu0-small") < output.index("gpu0-large")
    assert output.index("gpu0-large") < output.index("gpu1-mid")
    assert output.index("gpu1-mid") < output.index("gpu2-large")


def test_render_once_includes_host_and_process_gpu_columns():
    frame = FrameSnapshot(
        devices=[],
        processes=[
            ProcessSnapshot(gpu_index=0, pid=10, gpu_util_percent=33, cpu_percent=22, command="python train.py"),
        ],
    )

    output = render_once(frame, width=140, use_color=False)

    assert "Load Average" in output
    assert "GPU-MEM" in output
    assert "%SM" in output
    assert "  33 " in output


def test_render_once_emits_ansi_color_when_enabled():
    frame = FrameSnapshot(
        devices=[
            DeviceSnapshot(
                index=0,
                name="MXC500",
                gpu_util_percent=88,
                memory_util_percent=92,
                memory_bandwidth_util_percent=64,
            )
        ],
        processes=[ProcessSnapshot(gpu_index=0, pid=10, user="alice", gpu_memory_bytes=100 * 1024**2)],
    )

    output = render_once(frame, width=140, use_color=True)

    assert "\x1b[" in output
    assert "\x1b[31m" in output
    assert "\x1b[35m" in output
    assert "\x1b[36mMEM: " in output
    assert "\x1b[36mUTL: " in output


def test_render_once_colors_compact_device_rows():
    frame = FrameSnapshot(devices=_many_devices(), processes=[])

    output = render_once(frame, width=170, use_color=True)
    device_line = next(
        line for line in output.splitlines() if "│   0 " in line and "│   8 " in line
    )

    assert "GPU Fan Temp Perf" in output
    # 12% gpu_util falls in nvitop's MODERATE band (10..75) → yellow body color
    assert device_line.startswith("\x1b[1m\x1b[33m")


def test_render_once_uses_two_compact_columns_for_16_gpu_wide_terminal():
    frame = FrameSnapshot(devices=_many_devices(), processes=[])

    output = render_once(frame, width=170, use_color=False)
    lines = output.splitlines()

    assert any(line.count("GPU Fan Temp") == 2 for line in lines)
    assert any("│   0 " in line and "│   8 " in line for line in lines)
    assert any("│   7 " in line and "│  15 " in line for line in lines)


def test_render_once_keeps_single_compact_column_when_16_gpu_terminal_is_narrow():
    frame = FrameSnapshot(devices=_many_devices(), processes=[])

    output = render_once(frame, width=120, use_color=False)
    lines = output.splitlines()

    assert "│  15 " in output
    assert not any(line.count("GPU Fan Temp") == 2 for line in lines)
    assert not any("│   0 " in line and "│   8 " in line for line in lines)


def test_render_once_omits_ansi_color_when_disabled():
    frame = FrameSnapshot(devices=[DeviceSnapshot(index=0, name="MXC500")], processes=[])

    output = render_once(frame, width=140, use_color=False)

    assert "\x1b[" not in output
