import re

from mxtop.formatting import format_bar
from mxtop.models import DeviceSnapshot, FrameSnapshot, ProcessSnapshot
from mxtop.rendering import render_once

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _many_devices(count=16):
    return [
        DeviceSnapshot(index=i, name="MXC500", gpu_util_percent=12, memory_util_percent=8)
        for i in range(count)
    ]


def _strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


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


def test_format_bar_uses_subcell_glyphs():
    assert format_bar(25, width=4) == "█░░░"
    assert format_bar(50, width=4) == "██░░"
    bar = format_bar(12.5, width=4)
    assert bar.startswith("▌") or bar.startswith("█")
    assert len(bar) == 4


def test_format_bar_handles_non_finite():
    assert format_bar(float("nan"), width=5) == "░░░░░"
    assert format_bar(float("inf"), width=5) == "░░░░░"


def test_render_once_shows_max_for_saturated_bar():
    frame = FrameSnapshot(
        devices=[
            DeviceSnapshot(
                index=0,
                name="MXC500",
                memory_used_bytes=64 * 1024**3,
                memory_total_bytes=64 * 1024**3,
                memory_util_percent=100,
                gpu_util_percent=100,
            )
        ],
        processes=[],
    )

    output = render_once(frame, width=140, use_color=False)

    assert " MAX " in output
    assert "MEM: " in output


def test_render_once_survives_nan_and_inf_values():
    frame = FrameSnapshot(
        devices=[
            DeviceSnapshot(
                index=0,
                name="MXC500",
                gpu_util_percent=float("nan"),
                memory_util_percent=float("inf"),
                memory_bandwidth_util_percent=float("nan"),
            )
        ],
        processes=[
            ProcessSnapshot(
                gpu_index=0,
                pid=99,
                gpu_util_percent=float("nan"),
                cpu_percent=float("inf"),
                command="weird",
            )
        ],
    )

    output = render_once(frame, width=140, use_color=False)

    assert "weird" in output
    assert "N/A" in output


def test_render_once_compact_panel_extends_to_full_width():
    devices = [
        DeviceSnapshot(index=i, name="MXC500", gpu_util_percent=10, memory_util_percent=10)
        for i in range(8)
    ]
    output = render_once(FrameSnapshot(devices=devices, processes=[]), width=120, use_color=False)
    device_lines = [line for line in output.splitlines() if line.startswith("│") and "MXC500" in line]
    assert device_lines, "expected at least one device row"
    assert all(len(line) == 120 for line in device_lines)


def test_render_once_lines_share_the_requested_width():
    frame = FrameSnapshot(
        devices=[
            DeviceSnapshot(
                index=0,
                name="MXC500",
                gpu_util_percent=42,
                memory_used_bytes=32 * 1024**3,
                memory_total_bytes=64 * 1024**3,
                memory_util_percent=50,
                memory_bandwidth_util_percent=40,
                power_w=200,
                power_limit_w=350,
            ),
            DeviceSnapshot(index=1, name="MXC500", gpu_util_percent=12, memory_util_percent=8),
        ],
        processes=[],
    )
    for width in (79, 100, 120, 160, 200):
        output = render_once(frame, width=width, use_color=False)
        for line in output.splitlines():
            if not line:
                continue
            assert len(line) == width, (
                f"line width mismatch at width={width}: expected {width}, got {len(line)}: {line!r}"
            )


def test_render_once_host_panel_does_not_duplicate_right_vbar():
    frame = FrameSnapshot(
        devices=[DeviceSnapshot(index=0, gpu_util_percent=50, memory_util_percent=50)],
        processes=[],
    )
    coloured = render_once(frame, width=120, use_color=True)
    for line in coloured.splitlines():
        plain = ANSI_RE.sub("", line)
        if "GPU MEM:" in plain or "GPU UTL:" in plain:
            assert not plain.endswith("││"), f"trailing duplicate vbar in {plain!r}"


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


def test_render_once_colors_compact_device_usage_independently():
    utils = [4] * 8 + [94] * 8
    frame = FrameSnapshot(
        devices=[
            DeviceSnapshot(index=i, name="MXC500", gpu_util_percent=value, memory_util_percent=value)
            for i, value in enumerate(utils)
        ],
        processes=[],
    )

    output = render_once(frame, width=170, use_color=True)
    device_line = next(
        line
        for line in output.splitlines()
        if "│   0 " in _strip_ansi(line) and "│   8 " in _strip_ansi(line)
    )

    assert "GPU Fan Temp Perf" in output
    assert "\x1b[1m\x1b[32m4%" in device_line
    assert "\x1b[1m\x1b[31m94%" in device_line


def test_render_once_colors_device_usage_fields_independently():
    frame = FrameSnapshot(
        devices=[
            DeviceSnapshot(
                index=0,
                name="MXC500",
                power_w=20,
                power_limit_w=350,
                gpu_util_percent=4,
                memory_used_bytes=4 * 1024**3,
                memory_total_bytes=64 * 1024**3,
            ),
            DeviceSnapshot(
                index=1,
                name="MXC500",
                power_w=215,
                power_limit_w=350,
                gpu_util_percent=45,
                memory_used_bytes=46 * 1024**3,
                memory_total_bytes=64 * 1024**3,
            ),
            DeviceSnapshot(
                index=2,
                name="MXC500",
                power_w=330,
                power_limit_w=350,
                gpu_util_percent=94,
                memory_used_bytes=56 * 1024**3,
                memory_total_bytes=64 * 1024**3,
            ),
        ],
        processes=[],
    )

    output = render_once(frame, width=120, use_color=True)

    assert "\x1b[1m\x1b[32m20W / 350W" in output
    assert "\x1b[1m\x1b[32m4.00GiB / 64.00GiB" in output
    assert "\x1b[1m\x1b[32m4%" in output
    assert "\x1b[1m\x1b[33m215W / 350W" in output
    assert "\x1b[1m\x1b[33m46.00GiB / 64.00GiB" in output
    assert "\x1b[1m\x1b[33m45%" in output
    assert "\x1b[1m\x1b[31m330W / 350W" in output
    assert "\x1b[1m\x1b[31m56.00GiB / 64.00GiB" in output
    assert "\x1b[1m\x1b[31m94%" in output


def test_render_once_colors_process_usage_columns():
    frame = FrameSnapshot(
        devices=[],
        processes=[
            ProcessSnapshot(
                gpu_index=0,
                pid=10,
                user="alice",
                gpu_memory_bytes=10 * 1024**2,
                gpu_util_percent=4,
                gpu_memory_bandwidth_util_percent=1,
                cpu_percent=5,
                memory_util_percent=1,
                command="low",
            ),
            ProcessSnapshot(
                gpu_index=1,
                pid=20,
                user="bob",
                gpu_memory_bytes=1000 * 1024**2,
                gpu_util_percent=45,
                gpu_memory_bandwidth_util_percent=72,
                cpu_percent=150,
                memory_util_percent=20,
                command="mid",
            ),
            ProcessSnapshot(
                gpu_index=2,
                pid=30,
                user="carol",
                gpu_memory_bytes=50000 * 1024**2,
                gpu_util_percent=94,
                gpu_memory_bandwidth_util_percent=88,
                cpu_percent=300,
                memory_util_percent=90,
                command="hot",
            ),
        ],
    )

    output = render_once(frame, width=140, use_color=True)

    assert "\x1b[1m\x1b[32m4\x1b[0m" in output
    assert "\x1b[1m\x1b[33m45\x1b[0m" in output
    assert "\x1b[1m\x1b[31m94\x1b[0m" in output


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
