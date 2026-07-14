from __future__ import annotations

from mxtop.formatting import format_compact_bytes
from mxtop.models import DeviceSnapshot, FrameSnapshot, ProcessSnapshot
from mxtop.ui import panels
from mxtop.ui.history import HostHistory
from mxtop.ui.panels import (
    render_device_panel,
    render_host_panel,
    render_main_screen,
    render_process_panel,
    render_snapshot_screen,
)
from mxtop.ui.state import LayoutMode, ProcessSort, UiState
from mxtop.ui.text import cell_width


def _device(index: int, *, used_gib: int = 8, total_gib: int = 64) -> DeviceSnapshot:
    return DeviceSnapshot(
        index=index,
        name="MetaX C500",
        memory_used_bytes=used_gib * 1024**3,
        memory_total_bytes=total_gib * 1024**3,
        memory_util_percent=100.0 * used_gib / total_gib,
        gpu_util_percent=42.0,
        power_w=100.0,
        power_limit_w=350.0,
    )


def _process(gpu_index: int, pid: int) -> ProcessSnapshot:
    return ProcessSnapshot(
        gpu_index=gpu_index,
        pid=pid,
        user="alice",
        gpu_memory_bytes=4 * 1024**3,
        gpu_util_percent=42.0,
        command=f"python job-{pid}.py",
    )


def _frame(device_count: int = 2, process_count: int = 2) -> FrameSnapshot:
    return FrameSnapshot(
        devices=[_device(index) for index in range(device_count)],
        processes=[
            _process(index % max(1, device_count), 100 + index)
            for index in range(process_count)
        ],
    )


def test_snapshot_uses_full_devices_compact_host_and_natural_processes(monkeypatch):
    monkeypatch.setattr(
        panels, "_host_metrics", lambda: (25.0, "8.00GiB", 50.0, "0.00GiB", 0.0)
    )
    monkeypatch.setattr(
        panels, "_load_average_text", lambda: "Load Average:  1.00  2.00  3.00"
    )
    monkeypatch.setattr(panels, "_uptime_text", lambda: "2:00:00")
    screen = render_snapshot_screen(_frame(), width=140)

    assert "Press h" not in screen.lines[0]
    assert any("GPU  Name" in line for line in screen.lines)
    assert sum(line.startswith("[ CPU:") for line in screen.lines) == 1
    assert sum(line.startswith("[ MEM:") for line in screen.lines) == 1
    assert not any(" CPU: " in line and line.startswith("│") for line in screen.lines)
    assert any(
        line.startswith("├") and len(line) == 140
        for line in screen.lines[screen.process_start :]
    )
    assert not any(line.startswith("│>") for line in screen.lines)


def test_live_auto_compacts_device_then_host_then_process():
    frame = _frame()

    full_screen = render_main_screen(frame, UiState(), width=120, height=34)
    device_screen = render_main_screen(frame, UiState(), width=120, height=33)
    host_screen = render_main_screen(frame, UiState(), width=120, height=30)
    process_screen = render_main_screen(frame, UiState(), width=120, height=20)
    full = full_screen.lines
    device_compact = device_screen.lines
    host_compact = host_screen.lines

    assert any("GPU  Name" in line for line in full)
    assert any("GPU Fan Temp" in line for line in device_compact)
    assert any(
        "Load Average:" in line and line.startswith("│") for line in device_compact
    )
    assert any(line.startswith("[ CPU:") for line in host_compact)
    assert any(
        line.startswith("├") for line in host_compact[host_screen.process_start :]
    )
    assert not any(
        line.startswith("├")
        for line in process_screen.lines[process_screen.process_start :]
    )


def test_wide_device_panel_uses_one_consistent_width():
    lines = render_device_panel(_frame(), 140, LayoutMode.FULL, compact=False)

    assert all(cell_width(line) == 140 for line in lines)


def test_wide_live_title_spans_the_full_viewport():
    frame = _frame()
    frame.timestamp = 0

    line = panels.render_title(frame, 120)

    assert cell_width(line) == 120
    assert line.endswith("(Press h for help or q to quit)")


def test_standard_wide_device_header_keeps_a_closed_full_width_frame():
    lines = render_device_panel(_frame(), 120, LayoutMode.FULL, compact=False)

    assert [line[-1] for line in lines[:5]] == ["╕", "│", "┤", "│", "│"]
    assert lines[5][78] == "╤"
    assert lines[5][-1] == "╡"
    assert lines[6][78] == "│"
    assert lines[6][-1] == "│"
    assert lines[-1][78] == "╧"
    assert lines[-1][-1] == "╛"


def test_standard_device_panel_keeps_a_closed_frame_before_100_columns():
    for width in (80, 92, 99):
        lines = render_device_panel(_frame(), width, LayoutMode.FULL, compact=False)

        assert all(cell_width(line) == width for line in lines)
        assert all(line[-1] in "╕│┤╡╛" for line in lines)


def test_empty_wide_device_panel_keeps_a_closed_full_width_frame():
    lines = render_device_panel(FrameSnapshot(devices=[], processes=[]), 120)

    assert all(cell_width(line) == 120 for line in lines)
    assert [line[-1] for line in lines] == ["╕", "│", "╡", "│", "╛"]


def test_device_panels_fill_every_supported_viewport_width():
    for width in (79, 92, 100, 140, 180):
        for compact in (False, True):
            layout = LayoutMode.COMPACT if compact else LayoutMode.FULL
            for device_count in (0, 1, 16, 32, 64):
                lines = render_device_panel(
                    _frame(device_count=device_count, process_count=0),
                    width,
                    layout,
                    compact=compact,
                )

                assert all(cell_width(line) == width for line in lines), (
                    width,
                    compact,
                    device_count,
                    [cell_width(line) for line in lines],
                )


def test_compact_devices_remain_one_vertical_list_and_keep_bars():
    frame = _frame(device_count=16, process_count=0)
    lines = render_device_panel(frame, 170, LayoutMode.COMPACT, compact=True)

    assert sum("GPU Fan Temp" in line for line in lines) == 1
    assert sum("MetaX C500" in line for line in lines) == 0
    assert sum(line.startswith("│") and "MEM:" in line for line in lines) == 16
    assert any("│  15 " in line for line in lines)
    assert not any("│   0 " in line and "│   8 " in line for line in lines)


def test_empty_device_panel_has_no_device_column_headers():
    lines = render_device_panel(FrameSnapshot(devices=[], processes=[]), 140)

    assert len(lines) == 5
    assert all(cell_width(line) == 140 for line in lines)
    assert "No visible devices found" in lines[3]
    assert not any("GPU  Name" in line or "Fan  Temp" in line for line in lines)


def test_compact_host_is_two_unboxed_rows(monkeypatch):
    monkeypatch.setattr(
        panels, "_host_metrics", lambda: (25.0, "8.00GiB", 50.0, "0.00GiB", 0.0)
    )
    monkeypatch.setattr(
        panels, "_load_average_text", lambda: "Load Average:  1.00  2.00  3.00"
    )
    monkeypatch.setattr(panels, "_uptime_text", lambda: "2:00:00")

    lines = render_host_panel(_frame(), 120, compact=True, history=HostHistory())

    assert len(lines) == 2
    assert lines[0].startswith("[ CPU:") and "UPTIME: 2:00:00" in lines[0]
    assert lines[1].startswith("[ MEM:") and "[ SWP:" in lines[1]
    assert all(len(line) == 120 for line in lines)


def test_compact_host_keeps_sampling_history(monkeypatch):
    history = HostHistory()
    monkeypatch.setattr(
        panels, "_host_metrics", lambda: (25.0, "8.00GiB", 50.0, "0.00GiB", 0.0)
    )
    monkeypatch.setattr(
        panels, "_load_average_text", lambda: "Load Average:  1.00  2.00  3.00"
    )

    render_host_panel(_frame(), 120, compact=True, history=history)

    assert history._last_flush is not None
    assert history._buffer["cpu"] == [25.0]
    assert history._buffer["memory"] == [50.0]
    assert history._buffer["gpu_utilization"] == [42.0]


def test_process_sort_arrow_and_compact_grouping():
    frame = _frame()
    state = UiState(process_sort=ProcessSort.GPU_MEMORY)
    lines, _, _ = render_process_panel(frame, state, 120, compact=True)

    assert "GPU-MEM▼" in lines[2]
    assert not any(line.startswith("├") for line in lines)

    state.reverse_sort = True
    lines, _, _ = render_process_panel(frame, state, 120, compact=True)
    assert "GPU-MEM▲" in lines[2]


def test_process_markers_and_manual_scroll_are_independent():
    frame = _frame(device_count=2, process_count=5)
    state = UiState(scroll_offset=3, follow_selection=False)
    state.tagged_pids.add(101)

    lines, _, _ = render_process_panel(frame, state, 120, height=6, compact=True)

    assert state.scroll_offset == 3
    assert any(line.startswith("│ ") and "job-101.py" in line for line in lines)
    assert not any(line.startswith("│=") and "job-101.py" in line for line in lines)

    state.selected_key = frame.processes[1].selection_key
    state.follow_selection = True
    state.command_offset = 0
    lines, _, _ = render_process_panel(frame, state, 120, height=6, compact=True)
    assert any(line.startswith("│ ") and "job-101.py" in line for line in lines)
    assert not any(line.startswith("│>") for line in lines)


def test_process_panel_signal_hint_tracks_actionable_selection(monkeypatch):
    frame = _frame(device_count=1, process_count=1)
    state = UiState(selected_key=frame.processes[0].selection_key)
    monkeypatch.setattr(panels, "_is_superuser", lambda: False)
    monkeypatch.setattr(panels.getpass, "getuser", lambda: "alice")

    screen = render_main_screen(frame, state, width=120)
    title_row = next(
        index for index, line in enumerate(screen.lines) if "Processes:" in line
    )
    assert screen.lines[title_row - 1].startswith("╒")
    assert screen.lines[title_row - 1].endswith("╕")
    assert (
        "(Press ^C(INT)/T(TERM)/K(KILL) to send signals)" in screen.lines[title_row - 2]
    )

    state.readonly = True
    screen = render_main_screen(frame, state, width=120)
    assert not any("send signals" in line for line in screen.lines)


def test_same_host_process_on_another_gpu_uses_link_marker_independently_of_tags():
    first = _process(0, 100)
    second = _process(1, 100)
    first.create_time = second.create_time = 123.0
    frame = FrameSnapshot(devices=[_device(0), _device(1)], processes=[first, second])
    state = UiState(selected_key=first.selection_key)

    lines, _, _ = render_process_panel(frame, state, 120, compact=True)

    assert any(line.startswith("│ ") and "  0 " in line for line in lines)
    assert any(line.startswith("│=") and "  1 " in line for line in lines)
    assert state.tagged_pids == set()


def test_main_panels_are_terminal_cell_exact_with_cjk_telemetry():
    device = _device(0)
    device.name = "沐曦超长加速卡名称"
    device.compute_mode = "默认计算模式"
    process = _process(0, 100)
    process.user = "开发者用户"
    process.command = "python 训练.py --模型 大模型"
    frame = FrameSnapshot(devices=[device], processes=[process])

    for width in (79, 120):
        screen = render_main_screen(frame, UiState(), width=width)
        assert all(cell_width(line) in {0, width} for line in screen.lines)
        assert all(cell_width(line) <= width for line in screen.lines)


def test_process_header_and_host_info_scroll_together():
    process = _process(0, 100)
    process.command = "python job-100.py " + "--argument " * 12
    process.process_type = "C"
    process.cpu_percent = 120.0
    process.memory_util_percent = 5.0
    process.runtime_seconds = 65.0
    frame = FrameSnapshot(devices=[_device(0)], processes=[process])

    state = UiState(command_offset=0)
    lines, _, _ = render_process_panel(
        frame, state, 120, compact=True, mark_selection=False
    )
    assert "%CPU  %MEM  TIME  COMMAND" in lines[2]
    assert " 120     5  1:05  python job-100.py" in lines[4]

    state.command_offset = 18
    lines, _, _ = render_process_panel(
        frame, state, 120, compact=True, mark_selection=False
    )
    assert "%GMBW  COMMAND" in lines[2]
    assert "%CPU" not in lines[2]
    assert " 120 " not in lines[4]
    assert "python job-100.py" in lines[4]


def test_process_horizontal_offset_clamps_to_longest_visible_host_info():
    short = _process(0, 100)
    long = _process(0, 101)
    long.command = "python " + "x" * 120
    frame = FrameSnapshot(devices=[_device(0)], processes=[short, long])
    state = UiState(command_offset=10_000)

    render_process_panel(frame, state, 100, compact=True)

    assert 0 < state.command_offset < 200


def test_hidden_host_sort_does_not_corrupt_gpu_header_and_natural_reverse_has_arrow():
    process = _process(0, 100)
    process.command = "python " + "x" * 120
    frame = FrameSnapshot(devices=[_device(0)], processes=[process])
    state = UiState(process_sort=ProcessSort.CPU, command_offset=18)

    lines, _, _ = render_process_panel(frame, state, 100, compact=True)

    assert "GP▼" not in lines[2]
    assert "%CPU" not in lines[2]

    state.process_sort = ProcessSort.DEFAULT
    state.reverse_sort = True
    state.command_offset = 0
    lines, _, _ = render_process_panel(frame, state, 100, compact=True)
    assert "GPU▼" in lines[2]


def test_live_title_surfaces_backend_error_while_showing_stale_frame():
    screen = render_main_screen(
        _frame(),
        UiState(),
        width=120,
        height=34,
        error="telemetry unavailable",
    )

    assert "ERROR: telemetry unavailable" in screen.lines[0]


def test_missing_device_fields_remain_na():
    frame = FrameSnapshot(
        devices=[DeviceSnapshot(index=0, name="MetaX C500")], processes=[]
    )
    lines = render_device_panel(frame, 79, LayoutMode.FULL, compact=False)
    rows = [line for line in lines if line.startswith("│") and "MetaX C500" in line]

    assert len(rows) == 1
    assert " N/A " in rows[0]
    assert "Off" not in rows[0]
    assert "N/A" in lines[7]
    assert "Default" not in lines[7]


def test_memory_formatting_matches_nvitop_unit_thresholds():
    assert format_compact_bytes(4 * 1024**3) == "4096MiB"
    assert format_compact_bytes(20 * 1024**3) == "20.00GiB"
    assert format_compact_bytes(0, min_unit="GiB") == "0.00GiB"


def test_host_gpu_memory_average_is_weighted(monkeypatch):
    frame = FrameSnapshot(
        devices=[
            _device(0, used_gib=8, total_gib=16),
            _device(1, used_gib=8, total_gib=64),
        ],
        processes=[],
    )
    monkeypatch.setattr(
        panels, "_host_metrics", lambda: (25.0, "8.00GiB", 50.0, "0.00GiB", 0.0)
    )
    monkeypatch.setattr(
        panels, "_load_average_text", lambda: "Load Average:  1.00  2.00  3.00"
    )

    lines = render_host_panel(frame, 120, history=HostHistory())

    assert any("AVG GPU MEM: 20.0%" in line for line in lines)


def test_live_host_uses_selected_device_gpu_labels_and_values(monkeypatch):
    devices = [
        _device(0, used_gib=8, total_gib=16),
        _device(1, used_gib=8, total_gib=64),
    ]
    devices[0].gpu_util_percent = 10.0
    devices[1].gpu_util_percent = 90.0
    processes = [_process(0, 100), _process(1, 101)]
    frame = FrameSnapshot(devices=devices, processes=processes)
    state = UiState(selected_key=processes[1].selection_key)
    monkeypatch.setattr(
        panels, "_host_metrics", lambda: (25.0, "8.00GiB", 50.0, "0.00GiB", 0.0)
    )
    monkeypatch.setattr(
        panels, "_load_average_text", lambda: "Load Average:  1.00  2.00  3.00"
    )

    history = HostHistory()
    lines = render_main_screen(frame, state, width=120, history=history).lines

    assert any("GPU 1 MEM: 12.5%" in line for line in lines)
    assert any("GPU 1 UTL: 90.0%" in line for line in lines)
    assert history._buffer["gpu_memory"] == [12.5]
    assert history._buffer["gpu_utilization"] == [90.0]


def test_switching_selected_gpu_resets_only_gpu_history(monkeypatch):
    frame = _frame(device_count=2, process_count=2)
    state = UiState(selected_key=frame.processes[0].selection_key)
    history = HostHistory(interval=0.0)
    monkeypatch.setattr(
        panels, "_host_metrics", lambda: (25.0, "8.00GiB", 50.0, "0.00GiB", 0.0)
    )
    monkeypatch.setattr(
        panels, "_load_average_text", lambda: "Load Average:  1.00  2.00  3.00"
    )

    render_main_screen(frame, state, width=120, history=history)
    render_main_screen(frame, state, width=120, history=history)
    assert history.cpu.samples
    assert history.gpu_memory.samples

    state.selected_key = frame.processes[1].selection_key
    render_main_screen(frame, state, width=120, history=history)

    assert history.cpu.samples
    assert len(history.gpu_memory.samples) == 1


def test_no_unicode_forces_only_host_compact():
    state = UiState(layout=LayoutMode.FULL, no_unicode=True)
    screen = render_main_screen(_frame(), state, width=120, height=40)

    assert any("GPU  Name" in line for line in screen.lines)
    assert any(line.startswith("[ CPU:") for line in screen.lines)
    assert any(line.startswith("├") for line in screen.lines[screen.process_start :])


def test_live_screen_has_no_status_footer():
    lines = render_main_screen(_frame(), UiState(), width=120, height=34).lines

    assert not any(line.startswith("mode=") or "refresh=" in line for line in lines)
    assert cell_width(lines[0]) == 120


def test_auto_64_gpu_overview_keeps_highest_gpu_and_process_panel_visible():
    frame = _frame(device_count=64, process_count=1)
    frame.processes[0].gpu_index = 63

    screen = render_main_screen(frame, UiState(), width=120, height=40)

    assert any("GPUs: 64" in line for line in screen.lines)
    assert any(" 63 " in line for line in screen.lines)
    assert any("Processes:" in line for line in screen.lines)
    assert any("job-100.py" in line for line in screen.lines)


def test_main_screen_offset_moves_the_whole_dashboard_and_preserves_process_ids():
    frame = _frame(device_count=2, process_count=12)
    state = UiState(layout=LayoutMode.COMPACT, follow_selection=False)
    top = render_main_screen(frame, state, width=120, height=18)

    state.main_screen_offset = 1
    shifted = render_main_screen(frame, state, width=120, height=18)

    assert top.lines[0].rstrip().endswith("(Press h for help or q to quit)")
    assert shifted.lines[0] == top.lines[1]
    assert shifted.process_count == len(shifted.process_keys)
    assert shifted.process_count == sum(
        panels._PROCESS_DATA_PREFIX_RE.match(line) is not None for line in shifted.lines
    )


def test_offscreen_selection_hides_signal_hint_but_tags_keep_it():
    frame = _frame(device_count=2, process_count=12)
    selected = frame.processes[-1]
    state = UiState(
        layout=LayoutMode.COMPACT,
        selected_key=selected.selection_key,
        selected_index=len(frame.processes) - 1,
        follow_selection=False,
        main_screen_offset=0,
    )

    hidden = render_main_screen(frame, state, width=120, height=18)

    assert not state.selected_visible
    assert not any("send signals" in line for line in hidden.lines)

    state.tagged_pids.add(frame.processes[0].pid)
    tagged = render_main_screen(frame, state, width=120, height=18)
    assert any("send signals" in line for line in tagged.context_lines or tagged.lines)
