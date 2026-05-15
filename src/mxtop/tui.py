from __future__ import annotations

import curses
import re
import sys
import time
from typing import Any

from mxtop.backends import TelemetryBackend
from mxtop.filters import apply_filters
from mxtop.models import FrameSnapshot
from mxtop.rendering import render_once
from mxtop.sampler import SnapshotSampler
from mxtop.ui.panels import render_main_screen
from mxtop.ui.state import DIRECT_SORT_KEYS, LayoutMode, UiState, keep_selection, next_sort, sort_processes

PAIR_TITLE = 1
PAIR_HEADER = 2
PAIR_DIM = 3
PAIR_VALUE = 4
PAIR_GOOD = 5
PAIR_WARN = 6
PAIR_HOT = 7
PAIR_MEM = 8
PAIR_ERROR = 9
PAIR_SELECTED = 10
PAIR_SWAP = 11
MIN_TUI_WIDTH = 72

MEM_THRESHOLDS = (10, 80)
GPU_THRESHOLDS = (10, 75)
SCROLL_STEP = 3
CURSOR_HOME = "\x1b[H"
CLEAR_TO_END = "\x1b[J"
HIDE_CURSOR = "\x1b[?25l"
SHOW_CURSOR = "\x1b[?25h"


def _setup_colors() -> None:
    if not curses.has_colors():
        return
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(PAIR_TITLE, curses.COLOR_BLACK, curses.COLOR_CYAN)
    curses.init_pair(PAIR_HEADER, curses.COLOR_CYAN, -1)
    curses.init_pair(PAIR_DIM, curses.COLOR_BLACK, -1)
    curses.init_pair(PAIR_VALUE, curses.COLOR_WHITE, -1)
    curses.init_pair(PAIR_GOOD, curses.COLOR_GREEN, -1)
    curses.init_pair(PAIR_WARN, curses.COLOR_YELLOW, -1)
    curses.init_pair(PAIR_HOT, curses.COLOR_RED, -1)
    curses.init_pair(PAIR_MEM, curses.COLOR_MAGENTA, -1)
    curses.init_pair(PAIR_ERROR, curses.COLOR_WHITE, curses.COLOR_RED)
    curses.init_pair(PAIR_SELECTED, curses.COLOR_BLACK, curses.COLOR_WHITE)
    curses.init_pair(PAIR_SWAP, curses.COLOR_BLUE, -1)


def _attr(pair: int, extra: int = 0) -> int:
    if not curses.has_colors():
        return extra
    return curses.color_pair(pair) | extra


def _safe_addnstr(screen, row: int, column: int, text: str, width: int, attr: int = 0) -> int:
    if row < 0 or column < 0 or column >= width - 1:
        return column
    available = width - column - 1
    if available <= 0 or not text:
        return column
    snippet = text[:available]
    try:
        screen.addnstr(row, column, snippet, available, attr)
    except curses.error:
        return width - 1
    return column + len(snippet)


def _clamp_scroll(offset: int, content_lines: int, viewport_lines: int) -> int:
    max_offset = max(0, content_lines - max(0, viewport_lines))
    return max(0, min(offset, max_offset))


def _mouse_scroll_delta(button_state: int) -> int:
    if button_state & getattr(curses, "BUTTON4_PRESSED", 0):
        return -SCROLL_STEP
    if button_state & getattr(curses, "BUTTON5_PRESSED", 0):
        return SCROLL_STEP
    return 0


def _draw_line(screen, row: int, line: str, width: int) -> None:
    attr = _line_attr(row, line)
    if row == 0:
        _draw_title_line(screen, row, line, width)
        return
    if _is_version_line(line):
        _draw_version_line(screen, row, line, width)
        return
    if "Processes:" in line and "@" in line:
        _draw_process_title_line(screen, row, line, width, attr)
        return
    if _is_process_data_line(line):
        _draw_process_data_line(screen, row, line, width, attr)
        return
    if _is_device_data_line(line):
        _draw_device_data_line(screen, row, line, width)
        return
    if _is_host_data_line(line):
        _draw_host_data_line(screen, row, line, width)
        return
    _safe_addnstr(screen, row, 0, line, width, attr)


def _draw_title_line(screen, row: int, line: str, width: int) -> None:
    hint_start = line.find("(Press ")
    if hint_start < 0:
        _safe_addnstr(screen, row, 0, line, width, _attr(PAIR_VALUE, curses.A_BOLD))
        return
    position = _safe_addnstr(screen, row, 0, line[:hint_start], width, _attr(PAIR_VALUE, curses.A_BOLD))
    hint = line[hint_start:]
    for token in ("h", "q"):
        prefix, found, rest = hint.partition(token)
        position = _safe_addnstr(screen, row, position, prefix, width, _attr(PAIR_VALUE, curses.A_BOLD))
        if not found:
            return
        position = _safe_addnstr(screen, row, position, found, width, _attr(PAIR_MEM, curses.A_BOLD))
        hint = rest
    _safe_addnstr(screen, row, position, hint, width, _attr(PAIR_VALUE, curses.A_BOLD))


def _draw_process_title_line(screen, row: int, line: str, width: int, attr: int) -> None:
    at = line.rfind("@")
    if at <= 0:
        _safe_addnstr(screen, row, 0, line, width, attr)
        return
    start = line.rfind(" ", 0, at)
    if start < 0:
        start = 0
    else:
        start += 1
    position = _safe_addnstr(screen, row, 0, line[:start], width, attr)
    position = _safe_addnstr(screen, row, position, line[start:at], width, _attr(PAIR_MEM, curses.A_BOLD))
    position = _safe_addnstr(screen, row, position, "@", width, attr)
    end = line.find("│", at)
    if end < 0:
        end = len(line)
    position = _safe_addnstr(screen, row, position, line[at + 1 : end], width, _attr(PAIR_GOOD, curses.A_BOLD))
    _safe_addnstr(screen, row, position, line[end:], width, attr)


def _draw_process_data_line(screen, row: int, line: str, width: int, attr: int) -> None:
    del attr
    if line.startswith("│>"):
        _safe_addnstr(screen, row, 0, line, width, _attr(PAIR_SELECTED, curses.A_BOLD | curses.A_REVERSE))
        return
    base_attr = _attr(PAIR_DIM) if " root " in line else _attr(PAIR_VALUE)
    if " root " not in line and (match := _PROCESS_ROW_FIELDS_RE.search(line)) is not None:
        _draw_process_metrics_line(screen, row, line, width, match)
        return
    position = _safe_addnstr(screen, row, 0, line[:2], width, base_attr)
    position = _safe_addnstr(screen, row, position, line[2:5], width, _attr(PAIR_GOOD, curses.A_BOLD))
    _safe_addnstr(screen, row, position, line[5:], width, base_attr)


_DEVICE_ROW_RE = re.compile(r"^│\s*\d+\s+\S")
_PROCESS_ROW_RE = re.compile(r"^│[ >]\s*\d+\s+\d+\s")
_BAR_RE = re.compile(r"(MEM|MBW|UTL|PWR): ([█░]+) (\S+)")
_HOST_BAR_RE = re.compile(r"(  )([█░]{4,})")
_GPU_METRIC_RE = re.compile(r"GPU (MEM|UTL):\s*(\S+)")
_WATT_RATIO_RE = re.compile(r"(\d+(?:\.\d+)?)W\s*/\s*(\d+(?:\.\d+)?)W")
_MEMORY_RATIO_RE = re.compile(
    r"(\d+(?:\.\d+)?)(B|KiB|MiB|GiB|TiB)\s*/\s*(\d+(?:\.\d+)?)(B|KiB|MiB|GiB|TiB)"
)
_CELL_GPU_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)%")
_PROCESS_ROW_FIELDS_RE = re.compile(
    r"^(?P<prefix>│[ >]\s*)(?P<gpu>\d+)(?P<before_mem>.*?\s)"
    r"(?P<gpu_mem>N/A|\d+(?:\.\d+)?(?:B|KiB|MiB|GiB|TiB))"
    r"(?P<before_sm>\s+)(?P<sm>\S+)"
    r"(?P<before_gmbw>\s+)(?P<gmbw>\S+)"
    r"(?P<before_cpu>\s+)(?P<cpu>\S+)"
    r"(?P<before_mem_pct>\s+)(?P<mem_pct>\S+)"
)
_BYTE_UNITS = {
    "B": 1.0,
    "KiB": 1024.0,
    "MiB": 1024.0**2,
    "GiB": 1024.0**3,
    "TiB": 1024.0**4,
}


def _is_device_data_line(line: str) -> bool:
    if not line.startswith("│") or "GPU-MEM" in line or _is_process_data_line(line):
        return False
    if _is_header_line(line):
        return False
    if "GPU MEM:" in line or "GPU UTL:" in line:
        return False
    if _DEVICE_ROW_RE.match(line) and "MiB" not in line[:24]:
        return True
    return any(token in line for token in (" Pwr:", "GPU-Util", " UTL:", " PWR:"))


def _is_process_data_line(line: str) -> bool:
    return bool(_PROCESS_ROW_RE.match(line))


def _is_header_line(line: str) -> bool:
    return (
        "GPU     PID" in line
        or "GPU      PID" in line
        or "GPU  Name" in line
        or "GPU Fan Temp" in line
        or "Fan  Temp" in line
        or "Processes:" in line
    )


def _is_host_data_line(line: str) -> bool:
    if not line.startswith("│"):
        return False
    return any(
        label in line
        for label in (" Load Average:", " CPU:", " MEM:", " SWP:", " GPU MEM:", " GPU UTL:")
    )


def _is_version_line(line: str) -> bool:
    return line.startswith("│") and "MXTOP " in line and "Driver Version" in line


def _draw_version_line(screen, row: int, line: str, width: int) -> None:
    _safe_addnstr(screen, row, 0, line, width, _attr(PAIR_VALUE, curses.A_BOLD))


def _intensity_pair(value: float | None, *, memory: bool) -> int:
    if value is None:
        return PAIR_WARN
    thresholds = MEM_THRESHOLDS if memory else GPU_THRESHOLDS
    if value >= thresholds[1]:
        return PAIR_HOT
    if value >= thresholds[0]:
        return PAIR_WARN
    return PAIR_GOOD


def _parse_percent(text: str) -> float | None:
    try:
        return float(text.replace("%", ""))
    except ValueError:
        return None


def _bar_pair(label: str, pct_text: str) -> int:
    return _intensity_pair(_parse_percent(pct_text), memory=label in {"MEM", "MBW"})


def _draw_device_data_line(screen, row: int, line: str, width: int) -> None:
    _draw_device_cells(screen, row, line, width)


def _draw_device_cells(screen, row: int, line: str, width: int) -> None:
    pieces = line.split("│")
    if len(pieces) < 3:
        _safe_addnstr(screen, row, 0, line, width, _attr(PAIR_VALUE, curses.A_BOLD))
        return
    cursor = 0
    for index, piece in enumerate(pieces):
        if index:
            cursor = _safe_addnstr(screen, row, cursor, "│", width, _attr(PAIR_DIM))
        if not piece:
            continue
        if index == 0:
            cursor = _safe_addnstr(screen, row, cursor, piece, width, _attr(PAIR_VALUE, curses.A_BOLD))
            continue
        role = (index - 1) % 4
        cursor = _draw_device_cell(screen, row, cursor, piece, width, role)


def _draw_device_cell(screen, row: int, cursor: int, text: str, width: int, role: int) -> int:
    if role == 0:
        return _draw_watt_ratio(screen, row, cursor, text, width)
    if role == 1:
        return _draw_memory_ratio(screen, row, cursor, text, width)
    if role == 2:
        return _draw_gpu_percent(screen, row, cursor, text, width)
    return _draw_bar_cell(screen, row, cursor, text, width)


def _draw_watt_ratio(screen, row: int, cursor: int, text: str, width: int) -> int:
    match = _WATT_RATIO_RE.search(text)
    if not match:
        return _safe_addnstr(screen, row, cursor, text, width, _attr(PAIR_VALUE, curses.A_BOLD))
    used = _float_text(match.group(1))
    limit = _float_text(match.group(2))
    value = None if used is None or not limit else min(100.0, max(0.0, used / limit * 100))
    return _draw_with_pair_span(
        screen,
        row,
        cursor,
        text,
        width,
        match.start(),
        match.end(),
        _intensity_pair(value, memory=False),
    )


def _draw_memory_ratio(screen, row: int, cursor: int, text: str, width: int) -> int:
    match = _MEMORY_RATIO_RE.search(text)
    if not match:
        return _safe_addnstr(screen, row, cursor, text, width, _attr(PAIR_VALUE, curses.A_BOLD))
    value = _ratio_percent(match.group(1), match.group(2), match.group(3), match.group(4))
    return _draw_with_pair_span(
        screen,
        row,
        cursor,
        text,
        width,
        match.start(),
        match.end(),
        _intensity_pair(value, memory=True),
    )


def _draw_gpu_percent(screen, row: int, cursor: int, text: str, width: int) -> int:
    match = _CELL_GPU_PERCENT_RE.search(text)
    if not match:
        return _safe_addnstr(screen, row, cursor, text, width, _attr(PAIR_VALUE, curses.A_BOLD))
    pair = _intensity_pair(_parse_percent(match.group(1)), memory=False)
    return _draw_with_pair_span(screen, row, cursor, text, width, match.start(), match.end(), pair)


def _draw_bar_cell(screen, row: int, cursor: int, text: str, width: int) -> int:
    local_cursor = 0
    for match in _BAR_RE.finditer(text):
        label = match.group(1)
        bar = match.group(2)
        pct_text = match.group(3)
        pair = _bar_pair(label, pct_text)
        cursor = _safe_addnstr(
            screen,
            row,
            cursor,
            text[local_cursor : match.start()],
            width,
            _attr(PAIR_VALUE, curses.A_BOLD),
        )
        cursor = _safe_addnstr(screen, row, cursor, f"{label}: ", width, _attr(PAIR_HEADER, curses.A_BOLD))
        cursor = _safe_addnstr(screen, row, cursor, bar, width, _attr(pair, curses.A_BOLD))
        cursor = _safe_addnstr(screen, row, cursor, f" {pct_text}", width, _attr(pair, curses.A_BOLD))
        local_cursor = match.end()
    return _safe_addnstr(screen, row, cursor, text[local_cursor:], width, _attr(PAIR_VALUE, curses.A_BOLD))


def _draw_with_pair_span(
    screen,
    row: int,
    cursor: int,
    text: str,
    width: int,
    start: int,
    end: int,
    pair: int,
) -> int:
    cursor = _safe_addnstr(screen, row, cursor, text[:start], width, _attr(PAIR_VALUE, curses.A_BOLD))
    cursor = _safe_addnstr(screen, row, cursor, text[start:end], width, _attr(pair, curses.A_BOLD))
    return _safe_addnstr(screen, row, cursor, text[end:], width, _attr(PAIR_VALUE, curses.A_BOLD))


def _draw_process_metrics_line(screen, row: int, line: str, width: int, match: re.Match[str]) -> None:
    cursor = 0
    cursor = _safe_addnstr(screen, row, cursor, match.group("prefix"), width, _attr(PAIR_VALUE))
    cursor = _safe_addnstr(screen, row, cursor, match.group("gpu"), width, _attr(PAIR_GOOD, curses.A_BOLD))
    cursor = _safe_addnstr(screen, row, cursor, match.group("before_mem"), width, _attr(PAIR_VALUE))
    cursor = _safe_addnstr(screen, row, cursor, match.group("gpu_mem"), width, _attr(PAIR_VALUE))
    cursor = _safe_addnstr(screen, row, cursor, match.group("before_sm"), width, _attr(PAIR_VALUE))
    cursor = _safe_addnstr(
        screen,
        row,
        cursor,
        match.group("sm"),
        width,
        _attr(_intensity_pair(_parse_percent(match.group("sm")), memory=False), curses.A_BOLD),
    )
    cursor = _safe_addnstr(screen, row, cursor, match.group("before_gmbw"), width, _attr(PAIR_VALUE))
    cursor = _safe_addnstr(
        screen,
        row,
        cursor,
        match.group("gmbw"),
        width,
        _attr(_intensity_pair(_parse_percent(match.group("gmbw")), memory=True), curses.A_BOLD),
    )
    cursor = _safe_addnstr(screen, row, cursor, match.group("before_cpu"), width, _attr(PAIR_VALUE))
    cursor = _safe_addnstr(
        screen,
        row,
        cursor,
        match.group("cpu"),
        width,
        _attr(_intensity_pair(_parse_percent(match.group("cpu")), memory=False), curses.A_BOLD),
    )
    cursor = _safe_addnstr(screen, row, cursor, match.group("before_mem_pct"), width, _attr(PAIR_VALUE))
    cursor = _safe_addnstr(
        screen,
        row,
        cursor,
        match.group("mem_pct"),
        width,
        _attr(_intensity_pair(_parse_percent(match.group("mem_pct")), memory=True), curses.A_BOLD),
    )
    _safe_addnstr(screen, row, cursor, line[match.end() :], width, _attr(PAIR_VALUE))


def _float_text(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


def _ratio_percent(used: str, used_unit: str, total: str, total_unit: str) -> float | None:
    used_value = _float_text(used)
    total_value = _float_text(total)
    if used_value is None or total_value is None:
        return None
    used_bytes = used_value * _BYTE_UNITS[used_unit]
    total_bytes = total_value * _BYTE_UNITS[total_unit]
    if total_bytes <= 0:
        return None
    return min(100.0, max(0.0, used_bytes / total_bytes * 100))


def _host_left_pair(text: str) -> int:
    if " CPU:" in text:
        return PAIR_HEADER
    if " MEM:" in text:
        return PAIR_MEM
    if " SWP:" in text:
        return PAIR_SWAP
    return PAIR_VALUE


def _gpu_metric_pair(text: str) -> int:
    match = _GPU_METRIC_RE.search(text)
    if not match:
        return PAIR_GOOD
    return _intensity_pair(_parse_percent(match.group(2)), memory=match.group(1) == "MEM")


def _draw_host_section(screen, row: int, cursor: int, text: str, width: int, pair: int) -> int:
    section_attr = _attr(pair, curses.A_BOLD)
    bar_match = _HOST_BAR_RE.search(text)
    if bar_match:
        cursor = _safe_addnstr(screen, row, cursor, text[: bar_match.start()], width, section_attr)
        cursor = _safe_addnstr(screen, row, cursor, bar_match.group(1), width, _attr(PAIR_DIM))
        cursor = _safe_addnstr(screen, row, cursor, bar_match.group(2), width, section_attr)
        cursor = _safe_addnstr(screen, row, cursor, text[bar_match.end():], width, section_attr)
    else:
        cursor = _safe_addnstr(screen, row, cursor, text, width, section_attr)
    return cursor


def _draw_host_data_line(screen, row: int, line: str, width: int) -> None:
    pieces = line.split("│")
    cursor = 0
    if not pieces or pieces[0]:
        _safe_addnstr(screen, row, 0, line, width, _attr(PAIR_VALUE, curses.A_BOLD))
        return
    cursor = _safe_addnstr(screen, row, cursor, "│", width, _attr(PAIR_DIM))
    if len(pieces) > 1:
        left_pair = _host_left_pair(pieces[1])
        cursor = _draw_host_section(screen, row, cursor, pieces[1], width, left_pair)
        cursor = _safe_addnstr(screen, row, cursor, "│", width, _attr(PAIR_DIM))
    if len(pieces) > 2:
        right_text = pieces[2]
        right_pair = _gpu_metric_pair(right_text) if "GPU " in right_text else PAIR_VALUE
        cursor = _draw_host_section(screen, row, cursor, right_text, width, right_pair)
        cursor = _safe_addnstr(screen, row, cursor, "│", width, _attr(PAIR_DIM))
    for extra in pieces[3:]:
        cursor = _safe_addnstr(screen, row, cursor, extra, width, _attr(PAIR_VALUE, curses.A_BOLD))
        cursor = _safe_addnstr(screen, row, cursor, "│", width, _attr(PAIR_DIM))


def _line_attr(row: int, line: str) -> int:
    if row == 0:
        return _attr(PAIR_VALUE, curses.A_BOLD)
    if "backend error" in line or "error=" in line:
        return _attr(PAIR_ERROR, curses.A_BOLD)
    if line.startswith("│>"):
        return _attr(PAIR_SELECTED, curses.A_BOLD | curses.A_REVERSE)
    if not line:
        return _attr(PAIR_VALUE)
    stripped = line.strip()
    if stripped and set(stripped) <= {
        " ",
        "╒",
        "╕",
        "╘",
        "╛",
        "╞",
        "╡",
        "╪",
        "╧",
        "├",
        "┤",
        "┼",
        "─",
        "═",
        "┬",
        "┴",
        "╤",
        "│",
    }:
        return _attr(PAIR_DIM)
    if (
        "Processes:" in line
        or "GPU     PID" in line
        or "GPU      PID" in line
        or "GPU  Name" in line
        or "Fan  Temp" in line
        or "GPU Fan Temp" in line
    ):
        return _attr(PAIR_HEADER, curses.A_BOLD)
    if "Load Average:" in line or " CPU:" in line or " MEM:" in line or " SWP:" in line:
        return _attr(PAIR_VALUE)
    return _attr(PAIR_VALUE)


def _filtered_frame(frame: FrameSnapshot, options: Any | None) -> FrameSnapshot:
    if options is None:
        return frame
    return apply_filters(
        frame,
        device_indices=getattr(options, "device_indices", None),
        users=getattr(options, "users", None),
        pids=getattr(options, "pids", None),
        process_types=getattr(options, "process_types", None),
        require_process_type=getattr(options, "require_process_type", False),
    )


def _move_selection(state: UiState, frame: FrameSnapshot, delta: int) -> None:
    processes = sort_processes(frame.processes, state.process_sort, state.reverse_sort)
    keep_selection(state, processes)
    if not processes:
        return
    state.selected_index = max(0, min(state.selected_index + delta, len(processes) - 1))
    state.selected_key = processes[state.selected_index].selection_key


def _handle_key(key: int, state: UiState, frame: FrameSnapshot | None, sampler: SnapshotSampler) -> bool:
    if key in {ord("q"), ord("Q"), 27, 3}:
        return False
    if key == -1:
        return True
    if state.pending_sort_key:
        state.pending_sort_key = False
        if 0 <= key <= 255 and (sort := DIRECT_SORT_KEYS.get(chr(key))) is not None:
            state.process_sort = sort
        return True
    if key in {ord("h"), ord("?")}:
        state.show_help = not state.show_help
    elif key == ord("r"):
        sampler.refresh_now()
    elif key in {ord("a"), ord("A")}:
        state.layout = LayoutMode.AUTO
    elif key in {ord("f"), ord("F")}:
        state.layout = LayoutMode.FULL
    elif key in {ord("c"), ord("C")}:
        state.layout = LayoutMode.COMPACT
    elif key in {ord(","), ord("<")}:
        state.process_sort = next_sort(state.process_sort, -1)
    elif key in {ord("."), ord(">")}:
        state.process_sort = next_sort(state.process_sort, 1)
    elif key == ord("/"):
        state.reverse_sort = not state.reverse_sort
    elif key == ord("o"):
        state.pending_sort_key = True
    elif key == curses.KEY_MOUSE:
        try:
            _, _, _, _, button_state = curses.getmouse()
        except curses.error:
            button_state = 0
        state.scroll_offset += _mouse_scroll_delta(button_state)
    elif key in {curses.KEY_UP, ord("k")} and frame is not None:
        _move_selection(state, frame, -1)
    elif key in {curses.KEY_DOWN, ord("j")} and frame is not None:
        _move_selection(state, frame, 1)
    elif key == curses.KEY_PPAGE:
        state.scroll_offset -= 5
    elif key == curses.KEY_NPAGE:
        state.scroll_offset += 5
    elif key == curses.KEY_LEFT:
        state.command_offset = max(0, state.command_offset - 4)
    elif key == curses.KEY_RIGHT:
        state.command_offset += 4
    return True


def run_tui(backend: TelemetryBackend, interval: float, options: Any | None = None) -> int:
    final_rendered = ""
    state = UiState(layout=getattr(options, "layout", LayoutMode.AUTO))
    sampler = SnapshotSampler(backend, interval)
    sampler.start()

    def _main(screen) -> None:
        nonlocal final_rendered
        curses.curs_set(0)
        _setup_colors()
        try:
            curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
        except curses.error:
            pass
        screen.nodelay(True)
        screen.timeout(100)
        while True:
            sampler_state = sampler.snapshot()
            frame = _filtered_frame(sampler_state.frame, options) if sampler_state.frame is not None else None
            key = screen.getch()
            if not _handle_key(key, state, frame, sampler):
                break

            screen.erase()
            height, width = screen.getmaxyx()
            if width < MIN_TUI_WIDTH:
                message = f"mxtop needs at least a width of {MIN_TUI_WIDTH} to render, the current width is {width}."
                _safe_addnstr(screen, 0, 0, message, width, _attr(PAIR_ERROR, curses.A_BOLD))
                _safe_addnstr(screen, 1, 0, "Widen the terminal or press q to quit.", width, _attr(PAIR_DIM))
                screen.refresh()
                continue

            if frame is None:
                error = sampler_state.error or "loading telemetry"
                _safe_addnstr(screen, 0, 0, f"MXTOP  {error}", width, _attr(PAIR_TITLE, curses.A_BOLD))
                _safe_addnstr(screen, 1, 0, "q: quit  r: refresh", width, _attr(PAIR_DIM))
                screen.refresh()
                continue

            rendered = render_main_screen(
                frame,
                state,
                width=max(80, width),
                height=height,
                interval=interval,
                error=sampler_state.error,
            )
            for row, line in enumerate(rendered.lines[:height]):
                _draw_line(screen, row, line, width)
            final_rendered = "\n".join(rendered.lines)
            screen.refresh()
            time.sleep(0.02)

    try:
        screen = curses.initscr()
        try:
            curses.noecho()
            curses.cbreak()
            screen.keypad(True)
            sys.stdout.write(HIDE_CURSOR)
            sys.stdout.flush()
            _main(screen)
        finally:
            sampler.stop()
            screen.keypad(False)
            curses.nocbreak()
            curses.echo()
            curses.endwin()
            sys.stdout.write(SHOW_CURSOR)
            if final_rendered:
                sys.stdout.write(final_rendered)
                sys.stdout.write("\n")
            else:
                sampler_state = sampler.snapshot()
                if sampler_state.frame is not None:
                    sys.stdout.write(render_once(_filtered_frame(sampler_state.frame, options), use_color=False))
                    sys.stdout.write("\n")
            sys.stdout.flush()
    except KeyboardInterrupt:
        sampler.stop()
        return 130
    return 0
