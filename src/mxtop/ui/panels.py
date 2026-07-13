from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import getpass
import math
import os
import re
import socket
import time

from mxtop import __version__
from mxtop._compat import DATACLASS_SLOTS
from mxtop.formatting import (
    ellipsize,
    format_bar,
    format_compact_bytes,
    format_duration,
    format_mib,
    format_percent,
    format_percent_precise,
    format_percent_value,
)
from mxtop.models import (
    PROCESS_CREATE_TIME_TOLERANCE,
    DeviceSnapshot,
    FrameSnapshot,
    ProcessSnapshot,
)
from mxtop.ui.help import HELP_LINES
from mxtop.ui.history import HostHistory
from mxtop.ui.state import LayoutMode, UiState, keep_selection, sort_processes
from mxtop.ui.text import cell_ellipsize, cell_ljust, cell_rjust, cell_slice, cell_width

CORE_INNER = 77
CORE_WIDTH = 79
LEFT_INNER = 31
MID_INNER = 22
RIGHT_INNER = 22
BAR_MIN_WIDTH = 100
HOST_GRAPH_MIN_WIDTH = 100
MIN_SCREEN_WIDTH = 79
PROCESS_FIXED_PREFIX = 45
DENSE_DEVICE_THRESHOLD = 16
DENSE_DEVICE_CELL_WIDTH = 25
DENSE_DEVICE_MAX_COLUMNS = 8


@dataclass(**DATACLASS_SLOTS)
class RenderedScreen:
    lines: list[str]
    process_start: int
    process_count: int
    process_keys: tuple[str, ...] = ()
    context_lines: list[str] | None = None
    context_offset: int = 0


def render_title(frame: FrameSnapshot, width: int, error: str | None = None) -> str:
    timestamp = datetime.fromtimestamp(frame.timestamp).strftime("%a %b %d %H:%M:%S %Y")
    hint = f"(ERROR: {error})" if error else "(Press h for help or q to quit)"
    title_width = width
    if title_width <= len(timestamp) + len(hint):
        return ellipsize(f"{timestamp} {hint}", title_width)
    return f"{timestamp}{' ' * (title_width - len(timestamp) - len(hint))}{hint}"


def render_small_terminal_message(
    width: int, height: int | None = None
) -> RenderedScreen:
    message = "Terminal size is too small"
    detail = f"mxtop needs at least {MIN_SCREEN_WIDTH} columns"
    box_width = min(max(len(detail) + 4, len(message) + 4), max(20, width))
    inner = max(0, box_width - 2)
    lines = [
        "╒" + "═" * inner + "╕",
        _box_content(message.center(inner), box_width),
        _box_content(detail.center(inner), box_width),
        "╘" + "═" * inner + "╛",
    ]
    if height is not None and height > len(lines):
        padding = [""] * max(0, (height - len(lines)) // 2)
        lines = padding + lines
    return RenderedScreen(
        [line.center(width) for line in lines], process_start=0, process_count=0
    )


def render_device_panel(
    frame: FrameSnapshot,
    width: int,
    layout: LayoutMode = LayoutMode.AUTO,
    *,
    compact: bool | None = None,
) -> list[str]:
    if compact is None:
        compact = layout == LayoutMode.COMPACT
    if _uses_dense_device_grid(frame, compact=compact):
        return _render_dense_device_panel(frame, width)
    draw_bars = width >= BAR_MIN_WIDTH
    right_width = max(0, width - CORE_WIDTH)
    driver_version = _driver_version(frame)
    maca_version = _maca_version(frame)
    lines = [
        _top_border(right_width),
        _version_line(driver_version, right_width, maca_version),
    ]

    if not frame.devices:
        inner = CORE_INNER + right_width
        lines.extend(
            (
                "╞" + "═" * inner + "╡",
                _box_content("  No visible devices found", inner + 2),
                "╘" + "═" * inner + "╛",
            )
        )
        return lines

    lines.append(_header_top_divider(right_width))
    if compact:
        lines.append(_header_line_compact(right_width))
    else:
        lines.append(_header_line_one(right_width))
        lines.append(_header_line_two(right_width))
    lines.append(_header_data_divider(right_width))

    for index, device in enumerate(frame.devices):
        if index > 0:
            lines.append(_row_divider(right_width))
        if compact:
            row = _device_row_compact(device)
            if draw_bars and right_width >= 3:
                row += _device_bars(device, right_width, compact=True)[0]
            elif right_width:
                row += " " * (right_width - 1) + "│"
            lines.append(row)
        else:
            row_one = _device_row_one(device)
            row_two = _device_row_two(device)
            if draw_bars and right_width >= 3:
                bar_top, bar_bot = _device_bars(device, right_width, compact=False)
                row_one += bar_top
                row_two += bar_bot
            elif right_width:
                row_one += " " * (right_width - 1) + "│"
                row_two += " " * (right_width - 1) + "│"
            lines.append(row_one)
            lines.append(row_two)
    lines.append(_bottom_border(right_width))
    return lines


def _uses_dense_device_grid(frame: FrameSnapshot, *, compact: bool) -> bool:
    return compact and len(frame.devices) > DENSE_DEVICE_THRESHOLD


def _dense_device_columns(width: int, device_count: int) -> int:
    if device_count <= 0:
        return 1
    return max(
        1,
        min(
            DENSE_DEVICE_MAX_COLUMNS,
            device_count,
            max(1, (width - 1) // (DENSE_DEVICE_CELL_WIDTH + 1)),
        ),
    )


def _dense_device_cell_widths(width: int, columns: int) -> tuple[int, ...]:
    block_width, remainder = divmod(width - 1, columns)
    return tuple(block_width - 1 + (column < remainder) for column in range(columns))


def _render_dense_device_panel(frame: FrameSnapshot, width: int) -> list[str]:
    width = max(MIN_SCREEN_WIDTH, width)
    columns = _dense_device_columns(width, len(frame.devices))
    cell_widths = _dense_device_cell_widths(width, columns)
    verticals = _dense_device_verticals(cell_widths)
    lines = [
        "╒" + "═" * (width - 2) + "╕",
        _version_line(_driver_version(frame), width - CORE_WIDTH, _maca_version(frame)),
        _dense_fleet_divider(width, len(frame.devices), cell_widths),
        _dense_device_line(cell_widths, header=True),
        _double_horizontal_rule(width, verticals, verticals),
    ]
    for offset in range(0, len(frame.devices), columns):
        row_devices = frame.devices[offset : offset + columns]
        lines.append(_dense_device_line(cell_widths, devices=row_devices))
    lines.append(_double_horizontal_rule(width, verticals, frozenset()))
    return lines


def _dense_fleet_divider(
    width: int,
    device_count: int,
    cell_widths: tuple[int, ...],
) -> str:
    label = f" GPUs: {device_count} "
    line = "├" + label + "─" * max(0, width - len(label) - 2) + "┤"
    return _replace_characters(
        line,
        {index: "┬" for index in _dense_device_separator_indices(cell_widths)},
    )


def _dense_device_separator_indices(
    cell_widths: tuple[int, ...],
) -> tuple[int, ...]:
    cursor = 0
    separators: list[int] = []
    for column_width in cell_widths[:-1]:
        cursor += column_width + 1
        separators.append(cursor)
    return tuple(separators)


def _dense_device_verticals(cell_widths: tuple[int, ...]) -> frozenset[int]:
    width = sum(cell_widths) + len(cell_widths) + 1
    return frozenset((0, *_dense_device_separator_indices(cell_widths), width - 1))


def _dense_device_line(
    cell_widths: tuple[int, ...],
    *,
    devices: list[DeviceSnapshot] | None = None,
    header: bool = False,
) -> str:
    devices = devices or []
    cells: list[str] = []
    for column, column_width in enumerate(cell_widths):
        if header:
            text = "GPU TEMP UTIL MEM%  POWER"
        elif column < len(devices):
            text = _dense_device_text(devices[column])
        else:
            text = ""
        cells.append(ellipsize(text, column_width).ljust(column_width))
    return "│" + "│".join(cells) + "│"


def _dense_device_text(device: DeviceSnapshot) -> str:
    return "{index:>3} {temp:>4} {util:>4} {memory:>4} {power:>6}".format(
        index=device.index,
        temp=_dense_metric(device.temperature_c, "C", 4),
        util=_dense_metric(device.gpu_util_percent, "%", 4),
        memory=_dense_metric(_device_memory_percent(device), "%", 4),
        power=_dense_metric(device.power_w, "W", 6),
    )


def _dense_metric(value: float | None, suffix: str, width: int) -> str:
    if value is None or not math.isfinite(float(value)):
        return "N/A"
    text = f"{value:.0f}{suffix}"
    if len(text) <= width:
        return text
    return "MAX" if value >= 0 else "MIN"


_HOST_HISTORY = HostHistory()


def reset_host_history() -> None:
    _HOST_HISTORY.reset()


def render_host_panel(
    frame: FrameSnapshot,
    width: int,
    *,
    compact: bool = False,
    history: HostHistory | None = None,
    selected_gpu_index: int | None = None,
) -> list[str]:
    history = history if history is not None else _HOST_HISTORY
    draw_graphs = width >= HOST_GRAPH_MIN_WIDTH
    right_width = max(0, width - CORE_WIDTH)
    right_inner = max(0, right_width - 1) if draw_graphs else 0
    cpu, memory_used_text, memory_pct, swap_used_text, swap_pct = _host_metrics()
    aggregate_gpu_mem = _weighted_memory_percent(frame.devices)
    aggregate_gpu_util = _average_percent(d.gpu_util_percent for d in frame.devices)

    selected_devices = [
        device for device in frame.devices if device.index == selected_gpu_index
    ]
    if len(frame.devices) > 1 and selected_devices:
        gpu_mem = _weighted_memory_percent(selected_devices)
        gpu_util = _average_percent(
            device.gpu_util_percent for device in selected_devices
        )
        gpu_label = f"GPU {selected_gpu_index}"
    else:
        gpu_mem = aggregate_gpu_mem
        gpu_util = aggregate_gpu_util
        gpu_label = "AVG GPU" if len(frame.devices) > 1 else "GPU"

    # Host graphs keep sampling even when the compact layout does not draw them.
    # This mirrors nvitop's background host sampler and prevents gaps after a
    # terminal resize back to the full layout.
    history.set_gpu_scope(selected_gpu_index if selected_devices else None)
    history.sample(
        cpu=cpu,
        memory=memory_pct,
        swap=swap_pct,
        gpu_memory=gpu_mem,
        gpu_utilization=gpu_util,
    )

    if compact:
        load_average = _load_average_text()
        width_right = len(load_average) + 4
        width_left = width - 2 - width_right
        cpu_bar = "[ {} ]".format(
            _named_bar(
                "CPU", cpu, width_left - 4, extra_text=f"UPTIME: {_uptime_text()}"
            )
        )
        memory_bar = "[ {} ]".format(
            _named_bar(
                "MEM",
                memory_pct,
                width_left - 4,
                extra_text=f"USED: {memory_used_text}",
            )
        )
        swap_bar = "[ {} ]".format(_named_bar("SWP", swap_pct, width_right - 4))
        return [
            f"{cpu_bar}  ( {load_average} )",
            f"{memory_bar}  {swap_bar}",
        ]

    # nvitop layout: 5 graph rows (CPU) above the time axis, 5 below
    # (4-row MEM hanging down + 1-row SWP), text overlaid on the graphs.
    top_rows = history.cpu.render(CORE_INNER)
    bottom_rows = history.memory.render(CORE_INNER) + history.swap.render(CORE_INNER)
    top_rows[0] = _overlay(top_rows[0], f" {_load_average_text()} ")
    top_rows[1] = _overlay(top_rows[1], f" CPU: {_host_percent_text(cpu)} ")
    bottom_rows[3] = _overlay(
        bottom_rows[3], f" MEM: {memory_used_text} ({_host_percent_text(memory_pct)}) "
    )
    bottom_rows[4] = _overlay(
        bottom_rows[4], f" SWP: {swap_used_text} ({_host_percent_text(swap_pct)}) "
    )

    if right_inner:
        right_top = history.gpu_memory.render(right_inner)
        right_bottom = history.gpu_utilization.render(right_inner)
        right_top[0] = _overlay(
            right_top[0], f" {gpu_label} MEM: {_host_percent_text(gpu_mem)} "
        )
        right_bottom[4] = _overlay(
            right_bottom[4], f" {gpu_label} UTL: {_host_percent_text(gpu_util)} "
        )
    else:
        right_top = right_bottom = [""] * 5

    lines: list[str] = [_host_top_border(right_width)]
    for left, right in zip(top_rows, right_top):
        lines.append(_host_data_line(left, right, right_width))
    lines.append(_host_time_axis(right_width, draw_graphs=draw_graphs))
    for left, right in zip(bottom_rows, right_bottom):
        lines.append(_host_data_line(left, right, right_width))
    lines.append(_host_bottom_border(right_width))
    return lines


def _overlay(base: str, text: str, start: int = 0) -> str:
    text = text[: max(0, len(base) - start)]
    return base[:start] + text + base[start + len(text) :]


def _host_percent_text(value: float | None) -> str:
    if value is None or not math.isfinite(float(value)):
        return "N/A"
    return f"{value:.1f}%"


def _load_average_text() -> str:
    try:
        values = os.getloadavg()
    except OSError:
        return "Load Average: N/A N/A N/A"
    return "Load Average: {} {} {}".format(
        *(f"{value:5.2f}"[:5] if value < 10000.0 else "9999+" for value in values)
    )


def _uptime_text() -> str:
    try:
        import psutil

        seconds = max(0, int(time.time() - psutil.boot_time()))
    except (ModuleNotFoundError, OSError):
        return "N/A"
    days, remainder = divmod(seconds, 86400)
    if days:
        return f"{days + remainder / 86400:.1f} days"
    return format_duration(remainder)


def visible_processes(frame: FrameSnapshot, state: UiState) -> list[ProcessSnapshot]:
    processes = sort_processes(frame.processes, state.process_sort, state.reverse_sort)
    keep_selection(state, processes)
    return processes


def render_process_panel(
    frame: FrameSnapshot,
    state: UiState,
    width: int,
    height: int | None = None,
    *,
    compact: bool = False,
    mark_selection: bool = True,
) -> tuple[list[str], int, int]:
    processes = _process_group_order(visible_processes(frame, state), state)
    inner = max(MIN_SCREEN_WIDTH - 2, width - 2)
    user_host = _user_host()
    time_width = max(
        4,
        max(
            (len(format_duration(process.runtime_seconds)) for process in processes),
            default=4,
        ),
    )
    host_memory_total = _host_memory_total()
    max_command_offset = max(
        (
            max(
                0,
                cell_width(_process_host_info(process, host_memory_total, time_width))
                - max(
                    0,
                    inner
                    - cell_width(
                        _process_gpu_info(
                            process, state=state, mark_selection=mark_selection
                        )
                    ),
                ),
            )
            for process in processes
        ),
        default=0,
    )
    state.command_offset = max(0, min(state.command_offset, max_command_offset))
    top_border = _process_top_border(inner)
    lines = [top_border, _box_content(_process_title(inner, user_host), width)]
    lines.append(
        _box_content(_process_header(inner, state, time_width=time_width), width)
    )
    lines.append(_middle_border(inner))
    available_rows = None if height is None else max(1, height - len(lines) - 1)

    if not processes:
        if mark_selection:
            state.selected_visible = False
        lines.append(_box_content("  No running processes found", width))
        lines.append(_bottom_border_simple(inner))
        return lines, len(lines) - 2, 0

    if available_rows is not None:
        max_offset = max(0, len(processes) - available_rows)
        state.scroll_offset = max(0, min(state.scroll_offset, max_offset))
        if state.selected_key is not None and state.follow_selection:
            if state.selected_index < state.scroll_offset:
                state.scroll_offset = state.selected_index
            if state.selected_index >= state.scroll_offset + available_rows:
                state.scroll_offset = state.selected_index - available_rows + 1
        shown = processes[state.scroll_offset : state.scroll_offset + available_rows]
        if not compact:
            while (
                len(shown) > 1
                and _process_body_height(shown, compact=False) > available_rows
            ):
                shown.pop()
    else:
        shown = processes

    if mark_selection:
        state.selected_visible = any(
            process.selection_key == state.selected_key for process in shown
        )
        lines[0] = _process_top_border(inner)

    process_start = len(lines)
    prev_gpu_index: int | None = None
    for process in shown:
        if (
            not compact
            and prev_gpu_index is not None
            and prev_gpu_index != process.gpu_index
        ):
            lines.append("├" + "─" * inner + "┤")
        lines.append(
            _box_content(
                _process_row(
                    process,
                    state,
                    inner,
                    host_memory_total,
                    mark_selection=mark_selection,
                    time_width=time_width,
                ),
                width,
            )
        )
        prev_gpu_index = process.gpu_index
    lines.append(_bottom_border_simple(inner))
    return lines, process_start, len(shown)


def _process_body_height(processes: list[ProcessSnapshot], *, compact: bool) -> int:
    if compact or not processes:
        return len(processes)
    groups = 1 + sum(
        left.gpu_index != right.gpu_index
        for left, right in zip(processes, processes[1:])
    )
    return len(processes) + groups - 1


def render_help(width: int) -> list[str]:
    return [ellipsize(line, width) for line in HELP_LINES]


def render_main_screen(
    frame: FrameSnapshot,
    state: UiState | None = None,
    *,
    width: int = 120,
    height: int | None = None,
    interval: float = 1.0,
    error: str | None = None,
    history: HostHistory | None = None,
) -> RenderedScreen:
    del interval
    state = state or UiState()
    if state.show_help:
        return RenderedScreen(render_help(width), process_start=0, process_count=0)
    if width < MIN_SCREEN_WIDTH or (height is not None and height < 8):
        return render_small_terminal_message(width, height)

    device_compact, host_compact, process_compact = _layout_compaction(
        frame,
        state,
        height,
        width,
    )
    selected_gpu_index = _selected_gpu_index(frame, state)
    lines: list[str] = [render_title(frame, width, error)]
    lines.extend(
        render_device_panel(frame, width, state.layout, compact=device_compact)
    )
    if host_compact:
        lines.extend(render_host_panel(frame, width, compact=True, history=history))
    else:
        host_lines = render_host_panel(
            frame,
            width,
            compact=False,
            history=history,
            selected_gpu_index=selected_gpu_index,
        )
        if lines and host_lines and lines[-1].startswith("╘"):
            host_lines[0] = _merge_double_horizontal_borders(lines.pop(), host_lines[0])
        lines.extend(host_lines)
    lines.append("")

    process_panel_top = len(lines)
    process_lines, process_start, process_count = render_process_panel(
        frame,
        state,
        width,
        None,
        compact=process_compact,
    )
    absolute_process_start = len(lines) + process_start
    lines.extend(process_lines)
    processes = _process_group_order(visible_processes(frame, state), state)
    row_keys = _process_row_keys(lines, absolute_process_start, processes)

    if height is None:
        state.selected_visible = state.selected_key in row_keys.values()
        lines[process_panel_top - 1] = _process_action_line(width, state, processes)
        return RenderedScreen(
            lines,
            process_start=absolute_process_start,
            process_count=process_count,
            process_keys=tuple(row_keys.values()),
        )

    max_offset = max(0, len(lines) - height)
    state.main_screen_offset = max(0, min(state.main_screen_offset, max_offset))
    if state.selected_key is not None and state.follow_selection:
        selected_row = next(
            (row for row, key in row_keys.items() if key == state.selected_key),
            None,
        )
        if selected_row is not None:
            if selected_row < state.main_screen_offset:
                state.main_screen_offset = selected_row
            elif selected_row >= state.main_screen_offset + height:
                state.main_screen_offset = selected_row - height + 1
    offset = max(0, min(state.main_screen_offset, max_offset))
    state.main_screen_offset = offset
    visible_rows = {
        row - offset: key
        for row, key in row_keys.items()
        if offset <= row < offset + height
    }
    visible_start = min(visible_rows, default=0)
    state.selected_visible = state.selected_key in visible_rows.values()
    lines[process_panel_top] = _process_top_border(max(MIN_SCREEN_WIDTH - 2, width - 2))
    lines[process_panel_top - 1] = _process_action_line(width, state, processes)
    visible_lines = lines[offset : offset + height]
    return RenderedScreen(
        visible_lines,
        process_start=visible_start,
        process_count=len(visible_rows),
        process_keys=tuple(visible_rows.values()),
        context_lines=lines,
        context_offset=offset,
    )


def render_snapshot_screen(frame: FrameSnapshot, *, width: int = 120) -> RenderedScreen:
    width = max(MIN_SCREEN_WIDTH, width)
    state = UiState(layout=LayoutMode.FULL)
    timestamp = datetime.fromtimestamp(frame.timestamp).strftime("%a %b %d %H:%M:%S %Y")
    compact_devices = len(frame.devices) > DENSE_DEVICE_THRESHOLD
    lines = [
        timestamp,
        *render_device_panel(frame, width, LayoutMode.FULL, compact=compact_devices),
    ]
    lines.extend(render_host_panel(frame, width, compact=True))
    lines.append("")
    process_lines, process_start, process_count = render_process_panel(
        frame,
        state,
        width,
        compact=False,
        mark_selection=False,
    )
    absolute_process_start = len(lines) + process_start
    lines.extend(process_lines)
    processes = _process_group_order(visible_processes(frame, state), state)
    return RenderedScreen(
        lines,
        process_start=absolute_process_start,
        process_count=process_count,
        process_keys=tuple(
            _process_row_keys(lines, absolute_process_start, processes).values()
        ),
    )


_PROCESS_DATA_PREFIX_RE = re.compile(r"^│[ >=]\s*-?\d+\s+\d+")


def _process_row_keys(
    lines: list[str],
    process_start: int,
    processes: list[ProcessSnapshot],
) -> dict[int, str]:
    rows = [
        row
        for row in range(max(0, process_start), len(lines))
        if _PROCESS_DATA_PREFIX_RE.match(lines[row]) is not None
    ]
    return {row: process.selection_key for row, process in zip(rows, processes)}


def _layout_compaction(
    frame: FrameSnapshot,
    state: UiState,
    height: int | None,
    width: int,
) -> tuple[bool, bool, bool]:
    process_sorted = state.process_sort.value != "default"
    no_unicode = getattr(state, "no_unicode", False)
    large_fleet = len(frame.devices) > DENSE_DEVICE_THRESHOLD
    if state.layout == LayoutMode.COMPACT:
        return True, True, True
    if state.layout == LayoutMode.FULL:
        return False, no_unicode, process_sorted
    if height is None:
        return large_fleet, no_unicode, process_sorted

    device_count = len(frame.devices)
    process_count = len(frame.processes)
    process_device_count = len({process.gpu_index for process in frame.processes})
    device_full = 6 if device_count == 0 else 7 + 3 * device_count
    if device_count > DENSE_DEVICE_THRESHOLD:
        columns = _dense_device_columns(width, device_count)
        device_compact = 6 + math.ceil(device_count / columns)
    else:
        device_compact = 6 if device_count == 0 else 6 + 2 * device_count
    host_full = 2 if no_unicode else 12
    host_compact = 2
    process_full = 1 + max(6, 4 + process_count + process_device_count)

    compact_device = large_fleet or height < device_full + host_full + process_full
    compact_host = no_unicode or height < device_compact + host_full + process_full
    compact_process = height < device_compact + host_compact + process_full
    return compact_device, compact_host, compact_process or process_sorted


def _selected_gpu_index(frame: FrameSnapshot, state: UiState) -> int | None:
    if len(frame.devices) <= 1 or state.selected_key is None:
        return None
    for process in frame.processes:
        if process.selection_key == state.selected_key:
            return process.gpu_index
    return None


def _core_line(content: str) -> str:
    return "│" + cell_ljust(cell_ellipsize(content, CORE_INNER), CORE_INNER) + "│"


def _top_border(right_width: int) -> str:
    base = "╒" + "═" * CORE_INNER + "╕"
    if right_width:
        base = base[:-1] + "═" * right_width + "╕"
    return base


def _version_line(
    driver_version: str, right_width: int, maca_version: str | None = None
) -> str:
    parts = [
        f"MXTOP {__version__}",
        f"Driver Version: {driver_version}",
        f"MACA Version: {maca_version or 'N/A'}",
    ]
    if sum(len(part) for part in parts) % 2 == 0:
        parts[0] += " "
    total = sum(len(p) for p in parts)
    seps = " " * max(2, (75 - total) // 2)
    content = seps.join(parts).ljust(75)
    base = f"│ {content} │"
    if right_width:
        base = base[:-1] + " " * right_width + "│"
    return base


def _header_top_divider(right_width: int) -> str:
    base = (
        "├" + "─" * LEFT_INNER + "┬" + "─" * MID_INNER + "┬" + "─" * RIGHT_INNER + "┤"
    )
    if right_width:
        base = base[:-1] + "─" * right_width + "┤"
    return base


def _header_line_one(right_width: int) -> str:
    base = "│ GPU  Name        Persistence-M│ Bus-Id        Disp.A │ Volatile Uncorr. ECC │"
    if right_width:
        base = base[:-1] + " " * right_width + "│"
    return base


def _header_line_two(right_width: int) -> str:
    base = "│ Fan  Temp  Perf  Pwr:Usage/Cap│         Memory-Usage │ GPU-Util  Compute M. │"
    if right_width:
        base = base[:-1] + " " * right_width + "│"
    return base


def _header_line_compact(right_width: int) -> str:
    base = "│ GPU Fan Temp Perf Pwr:Usg/Cap │         Memory-Usage │ GPU-Util  Compute M. │"
    if right_width:
        base = base[:-1] + " " * right_width + "│"
    return base


def _header_data_divider(right_width: int) -> str:
    base = (
        "╞" + "═" * LEFT_INNER + "╪" + "═" * MID_INNER + "╪" + "═" * RIGHT_INNER + "╡"
    )
    if right_width:
        base = base[:-1] + "╤" + "═" * (right_width - 1) + "╡"
    separator = _device_bar_separator_index(right_width)
    return _replace_characters(base, {separator: "╤"} if separator is not None else {})


def _row_divider(right_width: int) -> str:
    base = (
        "├" + "─" * LEFT_INNER + "┼" + "─" * MID_INNER + "┼" + "─" * RIGHT_INNER + "┤"
    )
    if right_width:
        base = base[:-1] + "┼" + "─" * (right_width - 1) + "┤"
    separator = _device_bar_separator_index(right_width)
    return _replace_characters(base, {separator: "┼"} if separator is not None else {})


def _bottom_border(right_width: int) -> str:
    base = (
        "╘" + "═" * LEFT_INNER + "╧" + "═" * MID_INNER + "╧" + "═" * RIGHT_INNER + "╛"
    )
    if right_width:
        base = base[:-1] + "╧" + "═" * (right_width - 1) + "╛"
    separator = _device_bar_separator_index(right_width)
    return _replace_characters(base, {separator: "╧"} if separator is not None else {})


def _replace_characters(line: str, replacements: dict[int, str]) -> str:
    if not replacements:
        return line
    characters = list(line)
    for index, character in replacements.items():
        if 0 <= index < len(characters):
            characters[index] = character
    return "".join(characters)


def _double_horizontal_rule(
    width: int,
    vertical_above: frozenset[int],
    vertical_below: frozenset[int],
) -> str:
    characters = ["═"] * width
    internal = {
        (False, True): "╤",
        (True, False): "╧",
        (True, True): "╪",
    }
    left = {
        (False, True): "╒",
        (True, False): "╘",
        (True, True): "╞",
    }
    right = {
        (False, True): "╕",
        (True, False): "╛",
        (True, True): "╡",
    }
    for index in vertical_above | vertical_below:
        directions = (index in vertical_above, index in vertical_below)
        junctions = left if index == 0 else right if index == width - 1 else internal
        characters[index] = junctions[directions]
    return "".join(characters)


_DOUBLE_VERTICAL_UP = frozenset("│╞╡╪╘╧╛")
_DOUBLE_VERTICAL_DOWN = frozenset("│╞╡╪╒╤╕")


def _merge_double_horizontal_borders(upper: str, lower: str) -> str:
    if len(upper) != len(lower):
        return lower
    vertical_above = frozenset(
        index
        for index, character in enumerate(upper)
        if character in _DOUBLE_VERTICAL_UP
    )
    vertical_below = frozenset(
        index
        for index, character in enumerate(lower)
        if character in _DOUBLE_VERTICAL_DOWN
    )
    return _double_horizontal_rule(len(lower), vertical_above, vertical_below)


def _device_row_one(device: DeviceSnapshot) -> str:
    name_text = device.name or "N/A"
    name = cell_ljust(_cell_tail_ellipsize(name_text, 19), 19)
    persistence = _on_off(device.persistence_mode)
    bdf = cell_ljust(
        cell_ellipsize(device.bdf or device.uuid or "N/A", 16, marker=".."), 16
    )
    disp = _on_off(device.display_active)
    ecc = _ecc_text(device.ecc_errors)
    left = f" {device.index:>3}  {name} {cell_rjust(persistence, 4)} "
    mid = f" {bdf} {cell_rjust(disp, 3)} "
    right = f" {cell_rjust(ecc, 20)} "
    return f"│{left}│{mid}│{right}│"


def _device_row_compact(device: DeviceSnapshot) -> str:
    fan = _fan_text(device.fan_percent)
    temp = _temp_text(device.temperature_c)
    perf = cell_slice(device.performance_state or "N/A", 0, 3)
    power = _power_status(device.power_w, device.power_limit_w)
    memory = f"{format_compact_bytes(device.memory_used_bytes)} / {format_compact_bytes(device.memory_total_bytes)}"
    util = format_percent(device.gpu_util_percent)
    compute = cell_slice(device.compute_mode or "N/A", 0, 11)
    left = f" {device.index:>3} {fan:>3} {temp:>4} {cell_ljust(perf, 3)}{power:>13} "
    mid = f" {memory:>20} "
    right = f" {util:>7}  {cell_rjust(compute, 11)} "
    return f"│{left}│{mid}│{right}│"


def _device_row_two(device: DeviceSnapshot) -> str:
    fan = _fan_text(device.fan_percent)
    temp = _temp_text(device.temperature_c)
    perf = cell_slice(device.performance_state or "N/A", 0, 4)
    power = _power_status(device.power_w, device.power_limit_w)
    memory = f"{format_compact_bytes(device.memory_used_bytes)} / {format_compact_bytes(device.memory_total_bytes)}"
    util = format_percent(device.gpu_util_percent)
    compute = cell_slice(device.compute_mode or "N/A", 0, 11)
    left = f" {fan:>3}  {temp:>4}  {cell_ljust(perf, 4)} {power:>13} "
    mid = f" {memory:>20} "
    right = f" {util:>7}  {cell_rjust(compute, 11)} "
    return f"│{left}│{mid}│{right}│"


def _device_bars(
    device: DeviceSnapshot,
    right_width: int,
    *,
    compact: bool,
) -> tuple[str, str]:
    inner = right_width - 1
    if inner <= 0:
        return "", ""
    bar_widths = _device_bar_widths(right_width)
    if bar_widths is not None:
        left, right = bar_widths
        top_right_label = "UTL" if compact else "MBW"
        top_right_value = (
            device.gpu_util_percent if compact else device.memory_bandwidth_util_percent
        )
        top_right_extra = (
            _clock_text(device.gpu_clock_mhz)
            if compact
            else _clock_text(device.memory_clock_mhz)
        )
        top = (
            " "
            + _named_bar(
                "MEM",
                _device_memory_percent(device),
                left,
                extra_text=format_compact_bytes(device.memory_used_bytes),
            )
            + " │ "
            + _named_bar(
                top_right_label, top_right_value, right, extra_text=top_right_extra
            )
            + " │"
        )
        if compact:
            return top, ""
        bot = (
            " "
            + _named_bar(
                "UTL",
                device.gpu_util_percent,
                left,
                extra_text=_clock_text(device.gpu_clock_mhz),
            )
            + " │ "
            + _named_bar(
                "PWR",
                _power_util(device),
                right,
                extra_text=_power_draw_text(device.power_w),
            )
            + " │"
        )
        return top, bot
    top = (
        " "
        + _named_bar(
            "MEM",
            _device_memory_percent(device),
            inner - 2,
            extra_text=format_compact_bytes(device.memory_used_bytes),
        )
        + " │"
    )
    if compact:
        return top, ""
    bot = (
        " "
        + _named_bar(
            "UTL",
            device.gpu_util_percent,
            inner - 2,
            extra_text=_clock_text(device.gpu_clock_mhz),
        )
        + " │"
    )
    return top, bot


def _device_bar_widths(right_width: int) -> tuple[int, int] | None:
    if right_width < 44:
        return None
    return (
        (right_width - 6 + 1) // 2 - 1,
        (right_width - 6) // 2 + 1,
    )


def _device_bar_separator_index(right_width: int) -> int | None:
    bar_widths = _device_bar_widths(right_width)
    if bar_widths is None:
        return None
    left, _right = bar_widths
    return CORE_WIDTH + left + 2


def _clock_text(value: float | None) -> str:
    return (
        "" if value is None or not math.isfinite(float(value)) else f"@ {value:.0f}MHz"
    )


def _power_draw_text(value: float | None) -> str:
    return "" if value is None or not math.isfinite(float(value)) else f"{value:.0f}W"


def _power_util(device: DeviceSnapshot) -> float | None:
    if device.power_w is None or not device.power_limit_w:
        return None
    return min(100.0, max(0.0, device.power_w / device.power_limit_w * 100))


def _device_memory_percent(device: DeviceSnapshot) -> float | None:
    if device.memory_util_percent is not None:
        return float(device.memory_util_percent)
    if device.memory_used_bytes is None or not device.memory_total_bytes:
        return None
    return 100.0 * device.memory_used_bytes / device.memory_total_bytes


def _named_bar(
    label: str, value: float | None, width: int, *, extra_text: str = ""
) -> str:
    if width <= 0:
        return ""
    suffix_text = _bar_suffix_text(value)
    suffix = f" {suffix_text}"
    label_text = f"{label}: "
    extra = f" {extra_text}" if extra_text and extra_text != "N/A" else ""
    minimum = len(label_text) + len(suffix) + 1
    if extra and width - len(extra) < minimum:
        extra = ""
    chart_width = width - len(extra)
    bar_width = max(1, chart_width - len(label_text) - len(suffix))
    bar = format_bar(value, width=bar_width)
    chart = (label_text + bar + suffix)[:chart_width].ljust(chart_width)
    return (chart + extra)[:width].ljust(width)


def _bar_suffix_text(value: float | None) -> str:
    if value is None or not math.isfinite(float(value)):
        return "N/A"
    if value >= 100:
        return "MAX"
    return format_percent_precise(value)


def _host_top_border(right_width: int) -> str:
    base = "╒" + "═" * CORE_INNER + "╕"
    if right_width:
        base = base[:-1] + "╤" + "═" * (right_width - 1) + "╕"
    return base


def _host_data_line(left: str, right: str, right_width: int) -> str:
    line = "│" + ellipsize(left, CORE_INNER).ljust(CORE_INNER) + "│"
    if right_width:
        inner = max(0, right_width - 1)
        line += ellipsize(right, inner).ljust(inner) + "│"
    return line


def _host_time_axis(right_width: int, *, draw_graphs: bool) -> str:
    axis = "├────────────╴120s├─────────────────────────╴60s├──────────╴30s├──────────────┤"
    if right_width:
        inner = right_width - 1
        right_axis = _time_axis_right(inner) if draw_graphs else "─" * inner
        axis = axis[:-1] + "┼" + right_axis + "┤"
    return axis


def _time_axis_right(width: int) -> str:
    if width <= 0:
        return ""
    line = list("─" * width)
    labels = [
        (20, "╴30s├"),
        (35, "╴60s├"),
        (66, "╴120s├"),
        (96, "╴180s├"),
        (126, "╴240s├"),
        (156, "╴300s├"),
    ]
    for offset, label in labels:
        if offset > width:
            break
        start = width - offset
        line[start : start + len(label)] = list(label)
    return "".join(line)[:width]


def _host_bottom_border(right_width: int) -> str:
    base = "╘" + "═" * CORE_INNER + "╛"
    if right_width:
        base = base[:-1] + "╧" + "═" * (right_width - 1) + "╛"
    return base


def _top_border_simple(inner_width: int) -> str:
    return "╒" + "═" * inner_width + "╕"


def _process_top_border(inner_width: int) -> str:
    return _top_border_simple(inner_width)


def _process_action_line(
    width: int, state: UiState, processes: list[ProcessSnapshot]
) -> str:
    caution = "!CAUTION: SUPERUSER LOGGED-IN." if _is_superuser() else ""
    hint = ""
    if not state.readonly and _has_actionable_selection(state, processes):
        hint = "(Press ^C(INT)/T(TERM)/K(KILL) to send signals)"
    inner = max(0, width)
    if not caution:
        return cell_rjust(hint, inner)
    if not hint:
        return cell_ljust(caution, inner)
    available = inner - cell_width(caution) - cell_width(hint)
    if available < 1:
        return cell_ljust(cell_ellipsize(f"{caution} {hint}", inner), inner)
    return caution + " " * available + hint


def _is_superuser() -> bool:
    try:
        return os.geteuid() == 0
    except AttributeError:
        return getpass.getuser() == "root"


def _has_actionable_selection(state: UiState, processes: list[ProcessSnapshot]) -> bool:
    live_pids = {process.pid for process in processes}
    if state.tagged_pids.intersection(live_pids):
        return True
    selected = next(
        (
            process
            for process in processes
            if process.selection_key == state.selected_key
        ),
        None,
    )
    return (
        state.selected_visible
        and selected is not None
        and (_is_superuser() or selected.user == getpass.getuser())
    )


def _middle_border(inner_width: int) -> str:
    return "╞" + "═" * inner_width + "╡"


def _bottom_border_simple(inner_width: int) -> str:
    return "╘" + "═" * inner_width + "╛"


def _box_content(text: str, width: int) -> str:
    inner = max(0, width - 2)
    return "│" + cell_ljust(cell_ellipsize(text, inner), inner) + "│"


def _driver_version(frame: FrameSnapshot) -> str:
    for device in frame.devices:
        if device.driver_version:
            return device.driver_version
    return "N/A"


def _maca_version(frame: FrameSnapshot) -> str:
    for device in frame.devices:
        if device.maca_version:
            return device.maca_version
    return "N/A"


def _on_off(value: str | None) -> str:
    if not value:
        return "N/A"
    lowered = value.strip().lower()
    if lowered in {"enabled", "on", "1", "true"}:
        return "On"
    if lowered in {"disabled", "off", "0", "false"}:
        return "Off"
    return value.strip()[:4]


def _ecc_text(value: int | None) -> str:
    if value is None:
        return "N/A"
    return str(value)


def _fan_text(value: float | None) -> str:
    if value is None:
        return "N/A"
    if value >= 100:
        return "MAX"
    return f"{value:.0f}%"


def _temp_text(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.0f}C"


def _power_status(power: float | None, limit: float | None) -> str:
    left = "N/A" if power is None else f"{power:.0f}W"
    right = "N/A" if limit is None else f"{limit:.0f}W"
    return f"{left} / {right}"


def _host_metrics() -> tuple[float | None, str, float | None, str, float | None]:
    try:
        import psutil
    except ModuleNotFoundError:
        return None, "N/A", None, "N/A", None
    cpu = float(psutil.cpu_percent(interval=None))
    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()
    memory_used = format_compact_bytes(int(memory.used), min_unit="GiB")
    swap_used = format_compact_bytes(int(swap.used), min_unit="GiB")
    return cpu, memory_used, float(memory.percent), swap_used, float(swap.percent)


def _host_memory_total() -> int | None:
    try:
        import psutil
    except ModuleNotFoundError:
        return None
    return int(psutil.virtual_memory().total)


def _average_percent(values) -> float | None:
    known = [
        float(value)
        for value in values
        if value is not None and math.isfinite(float(value))
    ]
    if not known:
        return None
    return sum(known) / len(known)


def _weighted_memory_percent(devices: list[DeviceSnapshot]) -> float | None:
    pairs = [
        (device.memory_used_bytes, device.memory_total_bytes)
        for device in devices
        if device.memory_used_bytes is not None
        and device.memory_total_bytes is not None
        and device.memory_total_bytes > 0
    ]
    if pairs:
        used = sum(pair[0] for pair in pairs)
        total = sum(pair[1] for pair in pairs)
        return 100.0 * used / total if total > 0 else None
    return _average_percent(device.memory_util_percent for device in devices)


def _user_host() -> str:
    user = getpass.getuser()
    host = socket.gethostname().split(".", maxsplit=1)[0]
    return f"{user}@{host}"


def _process_title(width: int, user_host: str) -> str:
    left = " Processes:"
    left_width = cell_width(left)
    user_host_width = cell_width(user_host)
    if width <= left_width + user_host_width:
        return cell_ellipsize(f"{left} {user_host}", width)
    return f"{left}{' ' * (width - left_width - user_host_width - 1)}{user_host} "


def _process_header(
    width: int, state: UiState | None = None, *, time_width: int = 4
) -> str:
    gpu_header = " GPU     PID      USER  GPU-MEM %SM %GMBW  "
    host_header = "%CPU  %MEM  " + "TIME".rjust(time_width) + "  COMMAND"
    host_offset = max(0, state.command_offset if state is not None else 0)
    command_start = 14 + time_width
    visible_host_header = (
        host_header[host_offset:] if host_offset < command_start else "COMMAND"
    )
    base = (gpu_header + visible_host_header).ljust(width)
    if state is not None and (
        state.process_sort.value != "default" or state.reverse_sort
    ):
        columns = {
            "default": ("GPU", False),
            "pid": ("PID", False),
            "user": ("USER", False),
            "gpu_memory": ("GPU-MEM", True),
            "gpu_util": ("%SM", True),
            "gpu_memory_bandwidth": ("%GMBW", True),
            "cpu": ("%CPU", True),
            "host_memory": ("%MEM", True),
            "time": ("TIME", True),
            "command": ("COMMAND", False),
        }
        sort_column = columns.get(state.process_sort.value)
        if sort_column is not None:
            column, descending = sort_column
            indicator = "▼" if descending != state.reverse_sort else "▲"
            start = base.find(column)
            end = start + len(column)
            if start >= 0 and end < len(base):
                base = base[:end] + indicator + base[end + 1 :]
    return ellipsize(base, width).ljust(width)


def _process_group_order(
    processes: list[ProcessSnapshot], state: UiState
) -> list[ProcessSnapshot]:
    if state.process_sort.value != "default" or state.reverse_sort:
        return processes
    return sorted(
        processes,
        key=lambda process: (process.gpu_index, process.user or "", process.pid),
    )


def _process_row(
    process: ProcessSnapshot,
    state: UiState,
    width: int,
    host_memory_total: int | None,
    *,
    mark_selection: bool,
    time_width: int,
) -> str:
    gpu_info = _process_gpu_info(process, state=state, mark_selection=mark_selection)
    host_info = _process_host_info(process, host_memory_total, time_width)
    visible_host_info = cell_slice(host_info, max(0, state.command_offset))
    host_width = max(0, width - cell_width(gpu_info))
    return gpu_info + cell_ellipsize(visible_host_info, host_width)


def _process_gpu_info(
    process: ProcessSnapshot,
    *,
    state: UiState | None,
    mark_selection: bool,
) -> str:
    selected = state is not None and process.selection_key == state.selected_key
    tagged = state is not None and process.pid in state.tagged_pids
    linked = state is not None and _same_host_process_as_selection(process, state)
    marker = (
        ">"
        if selected and mark_selection
        else "="
        if (tagged or linked) and mark_selection
        else " "
    )
    process_type = (process.process_type or "-").replace("C+G", "X")
    process_type = cell_slice(process_type, 0, 1)
    user_text = cell_rjust(cell_ellipsize(process.user or "N/A", 7, marker="+"), 7)
    return (
        f"{marker}{process.gpu_index:>3} {process.pid:>7} {process_type:>1} {user_text} "
        f"{format_mib(process.gpu_memory_bytes):>8} "
        f"{format_percent_value(process.gpu_util_percent):>3} "
        f"{format_percent_value(process.gpu_memory_bandwidth_util_percent):>5}  "
    )


def _same_host_process_as_selection(process: ProcessSnapshot, state: UiState) -> bool:
    if state.selected_key is None or process.selection_key == state.selected_key:
        return False
    # A selection key always contains the PID; require the same creation stamp
    # when one is available so a reused PID is never linked accidentally.
    parts = state.selected_key.rsplit(":", 2)
    try:
        selected_pid = int(parts[-2] if len(parts) >= 3 else parts[-1])
    except ValueError:
        return False
    if process.pid != selected_pid:
        return False
    if process.create_time is None or len(parts) < 3:
        return True
    try:
        return (
            abs(process.create_time - float(parts[-1])) <= PROCESS_CREATE_TIME_TOLERANCE
        )
    except ValueError:
        return True


def _cell_tail_ellipsize(text: str, width: int, marker: str = "..") -> str:
    if cell_width(text) <= width:
        return text
    marker_width = cell_width(marker)
    if width <= marker_width:
        return cell_slice(text, max(0, cell_width(text) - width), width)
    return marker + cell_slice(
        text, cell_width(text) - (width - marker_width), width - marker_width
    )


def _process_host_info(
    process: ProcessSnapshot,
    host_memory_total: int | None,
    time_width: int,
) -> str:
    command = process.command or process.name
    return (
        f"{format_percent_value(process.cpu_percent):>4}  "
        f"{_host_memory_percent(process, host_memory_total):>4}  "
        f"{format_duration(process.runtime_seconds):>{time_width}}  "
        f"{command}"
    )


def _host_memory_percent(
    process: ProcessSnapshot, host_memory_total: int | None
) -> str:
    if process.memory_util_percent is not None:
        return format_percent_value(process.memory_util_percent)
    if process.host_memory_bytes is None or not host_memory_total:
        return "N/A"
    return format_percent_value(process.host_memory_bytes / host_memory_total * 100)
