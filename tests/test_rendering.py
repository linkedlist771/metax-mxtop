import re

from mxtop import rendering
from mxtop.formatting import format_bar
from mxtop.models import DeviceSnapshot, FrameSnapshot, ProcessSnapshot
from mxtop.rendering import _colorize_line, colorize_screen, render_once
from mxtop.ui.classify import dense_device_cell_spans, host_graph_context
from mxtop.ui.panels import render_device_panel, render_main_screen, render_snapshot_screen
from mxtop.ui.state import LayoutMode, UiState

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
    assert "52.71GiB" in output


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


def test_compact_device_data_region_extends_to_full_width():
    devices = [
        DeviceSnapshot(index=i, name="MXC500", gpu_util_percent=10, memory_util_percent=10)
        for i in range(8)
    ]
    lines = render_device_panel(
        FrameSnapshot(devices=devices, processes=[]),
        width=120,
        layout=LayoutMode.COMPACT,
        compact=True,
    )
    device_lines = [line for line in lines if line.startswith("│") and "MEM:" in line]
    assert device_lines, "expected at least one device row"
    assert all(len(line) == 120 for line in device_lines)


def test_wide_device_headers_stay_fixed_while_data_uses_requested_width():
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
        lines = render_device_panel(frame, width=width, compact=False)
        assert all(len(line) == 79 for line in lines[:5])
        data_width = width if width >= 100 else 79
        assert all(len(line) == data_width for line in lines[5:])


def test_render_once_host_panel_does_not_duplicate_right_vbar():
    frame = FrameSnapshot(
        devices=[DeviceSnapshot(index=0, gpu_util_percent=50, memory_util_percent=50)],
        processes=[],
    )
    rendered = render_main_screen(frame, width=120)
    context = host_graph_context(rendered.lines)
    coloured = [_colorize_line(row, line, context.get(row)) for row, line in enumerate(rendered.lines)]
    for line in coloured:
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


def test_colorful_bars_use_multiple_spectrum_colors():
    try:
        rendering.set_render_style(colorful=True)
        colored = rendering._style_bar_cell("│ MEM: ██████████ 90% │")
    finally:
        rendering.set_render_style(colorful=False)

    used_colors = {
        code
        for code in (
            rendering.FG_GREEN,
            rendering.FG_BRIGHT_GREEN,
            rendering.FG_YELLOW,
            rendering.FG_BRIGHT_YELLOW,
            rendering.FG_RED,
            rendering.FG_BRIGHT_RED,
        )
        if code in colored
    }
    assert len(used_colors) >= 4


def test_render_once_hides_bars_on_narrow_layout():
    frame = FrameSnapshot(
        devices=[DeviceSnapshot(index=0, name="MXC500", gpu_util_percent=71, memory_util_percent=83)],
        processes=[],
    )

    output = "\n".join(render_device_panel(frame, width=90, compact=False))

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


def test_render_once_colors_compact_device_rows_by_combined_load():
    utils = [4] * 8 + [94] * 8
    frame = FrameSnapshot(
        devices=[
            DeviceSnapshot(index=i, name="MXC500", gpu_util_percent=value, memory_util_percent=value)
            for i, value in enumerate(utils)
        ],
        processes=[],
    )

    rendered = render_main_screen(frame, UiState(layout=LayoutMode.COMPACT), width=170)
    output = "\n".join(colorize_screen(frame, rendered.lines))
    low_line = next(line for line in output.splitlines() if "│   0 " in _strip_ansi(line))
    hot_line = next(line for line in output.splitlines() if "│   8 " in _strip_ansi(line))

    assert "GPU Fan Temp Perf" in output
    assert "\x1b[1m\x1b[32m   0 " in low_line
    assert "\x1b[1m\x1b[31m   8 " in hot_line
    assert "\x1b[1m\x1b[32m 4%" in low_line
    assert "\x1b[1m\x1b[31m 94%" in hot_line


def test_render_once_colors_device_cells_by_combined_load_and_bars_by_metric():
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

    assert "\x1b[1m\x1b[32m N/A   N/A  N/A     20W / 350W " in output
    assert "\x1b[1m\x1b[32m   4096MiB / 64.00GiB " in output
    assert "\x1b[1m\x1b[33m N/A   N/A  N/A    215W / 350W " in output
    assert "\x1b[1m\x1b[33m  46.00GiB / 64.00GiB " in output
    assert "\x1b[1m\x1b[31m N/A   N/A  N/A    330W / 350W " in output
    assert "\x1b[1m\x1b[31m  56.00GiB / 64.00GiB " in output
    assert "\x1b[1m\x1b[32m 4%" in output
    assert "\x1b[1m\x1b[33m 45%" in output
    assert "\x1b[1m\x1b[31m 94%" in output


def test_render_once_colors_only_process_gpu_id_by_device_load():
    frame = FrameSnapshot(
        devices=[
            DeviceSnapshot(index=0, gpu_util_percent=4, memory_util_percent=4),
            DeviceSnapshot(index=1, gpu_util_percent=45, memory_util_percent=45),
            DeviceSnapshot(index=2, gpu_util_percent=94, memory_util_percent=94),
        ],
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

    assert "\x1b[1m\x1b[32m0\x1b[0m" in output
    assert "\x1b[1m\x1b[33m1\x1b[0m" in output
    assert "\x1b[1m\x1b[31m2\x1b[0m" in output
    assert "\x1b[1m\x1b[32m4\x1b[0m" not in output
    assert "\x1b[1m\x1b[33m45\x1b[0m" not in output
    assert "\x1b[1m\x1b[31m94\x1b[0m" not in output


def test_compact_16_gpu_wide_terminal_keeps_one_vertical_device_list():
    frame = FrameSnapshot(devices=_many_devices(), processes=[])

    lines = render_device_panel(frame, width=170, layout=LayoutMode.COMPACT, compact=True)

    assert sum(line.count("GPU Fan Temp") for line in lines) == 1
    assert any("│   0 " in line for line in lines)
    assert any("│  15 " in line for line in lines)
    assert not any("│   0 " in line and "│   8 " in line for line in lines)


def test_compact_16_gpu_narrow_terminal_keeps_one_vertical_device_list():
    frame = FrameSnapshot(devices=_many_devices(), processes=[])

    lines = render_device_panel(frame, width=120, layout=LayoutMode.COMPACT, compact=True)
    output = "\n".join(lines)

    assert "│  15 " in output
    assert not any(line.count("GPU Fan Temp") == 2 for line in lines)
    assert not any("│   0 " in line and "│   8 " in line for line in lines)


def test_dense_32_and_64_gpu_fleets_fit_adaptive_exact_width_grids():
    for count in (32, 64):
        frame = FrameSnapshot(devices=_many_devices(count), processes=[])
        for width in (79, 120, 172, 209):
            lines = render_device_panel(
                frame,
                width=width,
                layout=LayoutMode.COMPACT,
                compact=True,
            )
            spans = [span for line in lines for span in dense_device_cell_spans(line)]

            assert all(len(line) == width for line in lines)
            assert [gpu_index for _start, _end, gpu_index in spans] == list(range(count))
            assert f"GPUs: {count}" in lines[2]
            assert len(lines) <= 28


def test_dense_grid_preserves_sparse_indices_and_missing_values():
    indices = list(range(0, 64, 2))
    frame = FrameSnapshot(
        devices=[DeviceSnapshot(index=index, name="MXC500") for index in indices],
        processes=[],
    )

    lines = render_device_panel(frame, 120, LayoutMode.COMPACT, compact=True)
    spans = [span for line in lines for span in dense_device_cell_spans(line)]

    assert [gpu_index for _start, _end, gpu_index in spans] == indices
    data_lines = [line for line in lines if dense_device_cell_spans(line)]
    assert sum(line.count("N/A") for line in data_lines) == 4 * len(indices)


def test_dense_grid_bounds_extreme_metrics_without_losing_cell_context():
    devices = _many_devices(17)
    devices[0].temperature_c = -1000
    devices[0].gpu_util_percent = 10000
    devices[0].memory_util_percent = -10000
    devices[0].power_w = 1_000_000
    frame = FrameSnapshot(devices=devices, processes=[])

    lines = render_device_panel(frame, 79, LayoutMode.COMPACT, compact=True)
    spans = [span for line in lines for span in dense_device_cell_spans(line)]

    assert all(len(line) == 79 for line in lines)
    assert [gpu_index for _start, _end, gpu_index in spans] == list(range(17))
    assert "MIN" in lines[5] and "MAX" in lines[5]


def test_large_snapshot_uses_dense_overview_but_explicit_full_stays_detailed():
    frame = FrameSnapshot(devices=_many_devices(64), processes=[])

    snapshot = render_snapshot_screen(frame, width=120)
    full = render_device_panel(frame, 120, LayoutMode.FULL, compact=False)

    assert any("GPUs: 64" in line for line in snapshot.lines)
    assert any(" 63 " in line for line in snapshot.lines)
    assert not any("GPU Fleet:" in line for line in full)
    assert any("GPU  Name" in line for line in full)


def test_auto_large_fleet_always_uses_overview_even_without_a_height_budget():
    frame = FrameSnapshot(devices=_many_devices(64), processes=[])

    unbounded = render_main_screen(frame, UiState(), width=120)
    tall = render_main_screen(frame, UiState(), width=120, height=218)

    assert any("GPUs: 64" in line for line in unbounded.lines)
    assert any("GPUs: 64" in line for line in tall.lines)
    assert not any("GPU  Name" in line for line in unbounded.lines)


def test_dense_fleet_cells_receive_independent_ansi_load_colors():
    devices = _many_devices(17)
    for device, value in zip(devices[:3], (4.0, 45.0, 94.0)):
        device.gpu_util_percent = value
        device.memory_util_percent = value
    frame = FrameSnapshot(devices=devices, processes=[])
    lines = render_device_panel(frame, 120, LayoutMode.COMPACT, compact=True)
    output = "\n".join(colorize_screen(frame, lines))

    assert "\x1b[1m\x1b[32m  0" in output
    assert "\x1b[1m\x1b[33m  1" in output
    assert "\x1b[1m\x1b[31m  2" in output


def test_render_once_omits_ansi_color_when_disabled():
    frame = FrameSnapshot(devices=[DeviceSnapshot(index=0, name="MXC500")], processes=[])

    output = render_once(frame, width=140, use_color=False)

    assert "\x1b[" not in output


def test_render_once_merges_device_bottom_border_into_host_top():
    frame = FrameSnapshot(
        devices=[DeviceSnapshot(index=0, name="MXC500", gpu_util_percent=10, memory_util_percent=10)],
        processes=[],
    )

    lines = render_main_screen(frame, width=79).lines

    host_top = next(i for i, line in enumerate(lines) if "Load Average:" in line) - 1
    assert lines[host_top].startswith("╞"), "host panel should start with the merged border"
    assert not lines[host_top - 1].startswith("╘"), (
        "device bottom border must merge into the host top border (nvitop overlay)"
    )


def test_render_once_host_panel_has_five_rows_below_time_axis():
    frame = FrameSnapshot(
        devices=[DeviceSnapshot(index=0, name="MXC500", gpu_util_percent=10, memory_util_percent=10)],
        processes=[],
    )

    lines = render_main_screen(frame, width=79).lines

    axis = next(i for i, line in enumerate(lines) if "╴120s├" in line)
    bottom = next(i for i, line in enumerate(lines[axis:], start=axis) if line.startswith("╘"))
    assert bottom - axis - 1 == 5, "host panel should have 4 MEM rows + 1 SWP row below the axis"
    assert "Load Average:" in lines[axis - 5]
    assert " CPU: " in lines[axis - 4]
    assert " MEM: " in lines[bottom - 2]
    assert " SWP: " in lines[bottom - 1]


def test_render_once_extends_time_axis_labels_on_wide_terminals():
    frame = FrameSnapshot(
        devices=[DeviceSnapshot(index=0, name="MXC500", gpu_util_percent=10, memory_util_percent=10)],
        processes=[],
    )

    lines = render_main_screen(frame, width=200).lines
    axis = next(line for line in lines if "╴120s├" in line)
    assert axis.count("╴30s├") == 2
    assert axis.count("╴60s├") == 2
    assert "╴180s├" not in axis.split("┼")[0], "left axis keeps the classic 79-col labels"


def test_render_once_colors_host_history_graphs_by_section():
    from mxtop.ui import panels

    panels.reset_host_history()
    try:
        history = panels._HOST_HISTORY
        now = 0.0
        for _ in range(60):
            now += 1.1
            history.sample(cpu=80, memory=60, swap=30, gpu_memory=70, gpu_utilization=90, now=now)
        frame = FrameSnapshot(
            devices=[DeviceSnapshot(index=0, name="MXC500", gpu_util_percent=90, memory_util_percent=70)],
            processes=[],
        )

        rendered = render_main_screen(frame, width=120)
        context = host_graph_context(rendered.lines)
        lines = [
            _colorize_line(row, line, context.get(row))
            for row, line in enumerate(rendered.lines)
        ]
        plain = [_strip_ansi(line) for line in lines]
        cpu_row = next(i for i, line in enumerate(plain) if "Load Average:" in line) + 2
        mem_row = cpu_row + 4

        assert "\x1b[36m⣿" in lines[cpu_row], "CPU graph should be cyan"
        assert "\x1b[35m" in lines[mem_row], "MEM graph should be magenta"
    finally:
        panels.reset_host_history()
