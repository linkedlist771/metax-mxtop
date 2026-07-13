from __future__ import annotations

from collections.abc import Iterable

import pytest

from mxtop.models import DeviceSnapshot, FrameSnapshot, ProcessSnapshot
from mxtop.ui.panels import (
    render_device_panel,
    render_main_screen,
    render_process_panel,
    render_small_terminal_message,
)
from mxtop.ui.screens import (
    ProcessMetricsHistory,
    render_metrics_screen,
    render_signal_dialog,
)
from mxtop.ui.state import LayoutMode, UiState
from mxtop.ui.text import character_cell_width


# Stroke weights by direction. Mixed single/double junctions need direction-aware
# weights: for example, ``╤`` is double horizontally and single vertically.
_STROKES: dict[str, dict[str, int]] = {
    "─": {"left": 1, "right": 1},
    "│": {"up": 1, "down": 1},
    "┌": {"right": 1, "down": 1},
    "┐": {"left": 1, "down": 1},
    "└": {"right": 1, "up": 1},
    "┘": {"left": 1, "up": 1},
    "├": {"up": 1, "down": 1, "right": 1},
    "┤": {"up": 1, "down": 1, "left": 1},
    "┬": {"left": 1, "right": 1, "down": 1},
    "┴": {"left": 1, "right": 1, "up": 1},
    "┼": {"left": 1, "right": 1, "up": 1, "down": 1},
    "╴": {"left": 1},
    "═": {"left": 2, "right": 2},
    "╒": {"right": 2, "down": 1},
    "╕": {"left": 2, "down": 1},
    "╘": {"right": 2, "up": 1},
    "╛": {"left": 2, "up": 1},
    "╞": {"up": 1, "down": 1, "right": 2},
    "╡": {"up": 1, "down": 1, "left": 2},
    "╤": {"left": 2, "right": 2, "down": 1},
    "╧": {"left": 2, "right": 2, "up": 1},
    "╪": {"left": 2, "right": 2, "up": 1, "down": 1},
}


def _devices(count: int) -> list[DeviceSnapshot]:
    gib = 1024**3
    return [
        DeviceSnapshot(
            index=index,
            name="MetaX C500",
            bdf=f"0000:{index % 256:02x}:00.0",
            driver_version="2.31.0.5",
            maca_version="4.3.1",
            persistence_mode="On",
            display_active="Off",
            compute_mode="Default",
            fan_percent=30.0 + index % 40,
            temperature_c=40.0 + index % 35,
            power_w=75.0 + index % 240,
            power_limit_w=350.0,
            memory_used_bytes=(4 + index % 56) * gib,
            memory_total_bytes=64 * gib,
            gpu_util_percent=float(index * 17 % 100),
            memory_util_percent=float(index * 13 % 100),
        )
        for index in range(count)
    ]


def _process(gpu_index: int, pid: int) -> ProcessSnapshot:
    gib = 1024**3
    return ProcessSnapshot(
        gpu_index=gpu_index,
        pid=pid,
        name="python",
        user="alice",
        command=f"python train.py --rank {gpu_index}",
        process_type="C",
        gpu_memory_bytes=8 * gib,
        host_memory_bytes=2 * gib,
        gpu_util_percent=50.0,
        gpu_memory_bandwidth_util_percent=25.0,
        cpu_percent=120.0,
        memory_util_percent=5.0,
        runtime_seconds=600.0,
    )


def _cell_row(line: str, width: int) -> list[str]:
    cells: list[str] = []
    for character in line:
        character_width = character_cell_width(character)
        if character_width == 0:
            continue
        cells.append(character)
        cells.extend("\0" for _ in range(character_width - 1))
    return (cells + [" "] * width)[:width]


def _stroke(character: str, direction: str) -> int | None:
    return _STROKES.get(character, {}).get(direction)


def _is_decorative_time_tick(
    row: list[str], column: int, expected_up: int | None, expected_down: int | None
) -> bool:
    if row[column] != "├" or expected_up is not None or expected_down is not None:
        return False
    prefix = "".join(row[max(0, column - 5) : column])
    return any(
        prefix.endswith(f"{seconds}s") for seconds in (30, 60, 120, 180, 240, 300)
    )


def _assert_box_topology(lines: Iterable[str], *, context: str) -> None:
    lines = list(lines)
    width = max(
        (sum(character_cell_width(char) for char in line) for line in lines), default=0
    )
    grid = [_cell_row(line, width) for line in lines]
    violations: list[str] = []

    for row_index, row in enumerate(grid):
        if not any(character in {"─", "═"} for character in row):
            continue
        text = "".join(row).replace("\0", " ")

        # The fleet label intentionally creates exactly two gaps in its rule.
        # Every other adjacent horizontal stroke must be reciprocal and have the
        # same weight.
        allowed_label_gaps: set[int] = set()
        if "GPUs:" in text:
            rule_start = text.find("─", text.index("GPUs:"))
            allowed_label_gaps = {0, rule_start - 1}
        for column, (left, right) in enumerate(zip(row, row[1:])):
            outgoing = _stroke(left, "right")
            incoming = _stroke(right, "left")
            if (outgoing is not None or incoming is not None) and outgoing != incoming:
                if column in allowed_label_gaps:
                    continue
                violations.append(
                    f"row {row_index}, cells {column}-{column + 1}: "
                    f"{left!r} right={outgoing}, {right!r} left={incoming}"
                )

        # Divider junctions must describe exactly the vertical separators that
        # reach them from the rows above and below. This catches both missing
        # junctions and visible half-cell stubs.
        for column, character in enumerate(row):
            expected_up = (
                _stroke(grid[row_index - 1][column], "down") if row_index > 0 else None
            )
            expected_down = (
                _stroke(grid[row_index + 1][column], "up")
                if row_index + 1 < len(grid)
                else None
            )
            if _is_decorative_time_tick(row, column, expected_up, expected_down):
                continue
            actual_up = _stroke(character, "up")
            actual_down = _stroke(character, "down")
            if actual_up != expected_up or actual_down != expected_down:
                violations.append(
                    f"row {row_index}, cell {column}: {character!r} has "
                    f"up/down={actual_up}/{actual_down}, expected "
                    f"{expected_up}/{expected_down} from "
                    f"{grid[row_index - 1][column]!r}/"
                    f"{grid[row_index + 1][column]!r}"
                    if 0 < row_index < len(grid) - 1
                    else f"row {row_index}, cell {column}: {character!r} has "
                    f"up/down={actual_up}/{actual_down}, expected "
                    f"{expected_up}/{expected_down}"
                )

    assert not violations, context + "\n" + "\n".join(violations[:30])


@pytest.mark.parametrize("width", [79, 80, 92, 99, 100, 122, 123, 140, 180])
@pytest.mark.parametrize("compact", [False, True], ids=["full", "compact"])
def test_standard_device_panel_has_connected_box_junctions(
    width: int, compact: bool
) -> None:
    frame = FrameSnapshot(devices=_devices(3), processes=[])

    lines = render_device_panel(frame, width, compact=compact)

    _assert_box_topology(
        lines, context=f"standard device panel width={width}, compact={compact}"
    )


@pytest.mark.parametrize(
    ("device_count", "width"),
    [(17, 79), (32, 80), (32, 92), (32, 99), (32, 140), (64, 180)],
)
def test_dense_device_grid_has_connected_box_junctions(
    device_count: int, width: int
) -> None:
    frame = FrameSnapshot(devices=_devices(device_count), processes=[])

    lines = render_device_panel(frame, width, LayoutMode.COMPACT, compact=True)

    _assert_box_topology(
        lines, context=f"dense device grid count={device_count}, width={width}"
    )


@pytest.mark.parametrize(
    ("device_count", "width", "layout"),
    [
        (0, 79, LayoutMode.FULL),
        (0, 140, LayoutMode.FULL),
        (3, 80, LayoutMode.FULL),
        (3, 92, LayoutMode.FULL),
        (3, 99, LayoutMode.FULL),
        (3, 123, LayoutMode.FULL),
        (3, 140, LayoutMode.COMPACT),
        (32, 79, LayoutMode.AUTO),
        (32, 80, LayoutMode.AUTO),
        (32, 92, LayoutMode.AUTO),
        (32, 99, LayoutMode.AUTO),
        (32, 100, LayoutMode.AUTO),
        (64, 180, LayoutMode.AUTO),
    ],
)
def test_main_screen_panel_transitions_have_connected_box_junctions(
    device_count: int, width: int, layout: LayoutMode
) -> None:
    frame = FrameSnapshot(devices=_devices(device_count), processes=[])

    screen = render_main_screen(frame, UiState(layout=layout), width=width)

    _assert_box_topology(
        screen.context_lines or screen.lines,
        context=(
            f"main screen transition count={device_count}, width={width}, "
            f"layout={layout.value}"
        ),
    )


@pytest.mark.parametrize("width", [79, 80, 92, 99, 140, 180])
@pytest.mark.parametrize("compact", [False, True], ids=["grouped", "compact"])
def test_process_panel_has_connected_box_junctions(width: int, compact: bool) -> None:
    processes = [_process(gpu_index, 1000 + gpu_index) for gpu_index in range(3)]
    frame = FrameSnapshot(devices=_devices(3), processes=processes)

    lines, _, _ = render_process_panel(
        frame, UiState(), width, compact=compact, mark_selection=False
    )

    _assert_box_topology(
        lines, context=f"process panel width={width}, compact={compact}"
    )


@pytest.mark.parametrize(("width", "height"), [(79, 28), (92, 20), (120, 30)])
def test_metrics_panel_has_connected_box_junctions(width: int, height: int) -> None:
    process = _process(0, 1000)
    frame = FrameSnapshot(devices=_devices(1), processes=[process])
    history = ProcessMetricsHistory()
    for _ in range(30):
        history.sample(frame, process.selection_key, host_memory_total=128 * 1024**3)

    view = render_metrics_screen(frame, process, history, width=width, height=height)

    _assert_box_topology(view.lines, context=f"metrics width={width}, height={height}")


@pytest.mark.parametrize("width", [79, 92, 120])
def test_signal_dialog_has_connected_box_junctions(width: int) -> None:
    lines = render_signal_dialog(
        [(1000, "alice"), (1001, "开发者")],
        width=width,
        signal_name="SIGTERM",
    )

    _assert_box_topology(lines, context=f"signal dialog width={width}")


@pytest.mark.parametrize("width", [20, 42, 79])
def test_small_terminal_message_has_connected_box_junctions(width: int) -> None:
    screen = render_small_terminal_message(width)

    _assert_box_topology(screen.lines, context=f"small terminal width={width}")
