from mxtop.models import DeviceSnapshot, FrameSnapshot, ProcessSnapshot
from mxtop.formatting import format_bar
from mxtop.rendering import render_once


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


def test_format_bar_clamps_and_fills_blocks():
    assert format_bar(50, width=10) == "█████░░░░░"
    assert format_bar(120, width=4) == "████"
    assert format_bar(None, width=3) == "???"


def test_render_once_shows_bars_on_wide_layout():
    frame = FrameSnapshot(
        devices=[DeviceSnapshot(index=0, name="MXC500", gpu_util_percent=71, memory_util_percent=83)],
        processes=[],
    )

    output = render_once(frame, width=140, use_color=False)

    assert "UTIL" in output
    assert "[████" in output
    assert "MEM" in output


def test_render_once_hides_bars_on_narrow_layout():
    frame = FrameSnapshot(
        devices=[DeviceSnapshot(index=0, name="MXC500", gpu_util_percent=71, memory_util_percent=83)],
        processes=[],
    )

    output = render_once(frame, width=90, use_color=False)

    assert "GPU%" in output
    assert "[" not in output
    assert "█" not in output
