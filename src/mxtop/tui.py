from __future__ import annotations

import curses
import re
import sys
import time
from typing import Any

from mxtop.backends import TelemetryBackend
from mxtop.filters import apply_filters
from mxtop.models import FrameSnapshot
from mxtop import rendering as _rendering
from mxtop.rendering import render_once
from mxtop.sampler import SnapshotSampler
from mxtop.ui import classify
from mxtop.ui.classify import host_graph_context
from mxtop.ui.panels import MIN_SCREEN_WIDTH, render_main_screen
from mxtop.ui.state import DIRECT_SORT_KEYS, LayoutMode, UiState, keep_selection, next_sort, sort_processes
from mxtop.ui.signals import SIGNAL_KEYS, send_signal
from mxtop.models import ProcessSnapshot

# Line classification and value parsing live in mxtop.ui.classify, shared with
# the ANSI renderer. Local aliases keep this module's call sites short.
_is_border_line = classify.is_border_line
_is_device_data_line = classify.is_device_data_line
_is_header_line = classify.is_header_line
_is_host_data_line = classify.is_host_data_line
_is_process_data_line = classify.is_process_data_line
_is_version_line = classify.is_version_line
_parse_percent = classify.parse_percent
_float_text = classify.float_text
_ratio_percent = classify.ratio_percent
_BAR_RE = classify.BAR_RE
_BRAILLE_RUN_RE = classify.BRAILLE_RUN_RE
_CELL_GPU_PERCENT_RE = classify.CELL_GPU_PERCENT_RE
_GPU_METRIC_RE = classify.GPU_METRIC_RE
_MEMORY_RATIO_RE = classify.MEMORY_RATIO_RE
_PROCESS_ROW_FIELDS_RE = classify.PROCESS_ROW_FIELDS_RE
_WATT_RATIO_RE = classify.WATT_RATIO_RE

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
OUTER_BORDER_MIN_HEIGHT = 10

SCROLL_STEP = 3
CURSOR_HOME = "\x1b[H"
CLEAR_TO_END = "\x1b[J"
HIDE_CURSOR = "\x1b[?25l"
SHOW_CURSOR = "\x1b[?25h"


class _OffsetScreen:
    def __init__(self, screen, row_offset: int, column_offset: int):
        self._screen = screen
        self._row_offset = row_offset
        self._column_offset = column_offset

    def addnstr(self, row: int, column: int, text: str, count: int, attr: int = 0) -> None:
        self._screen.addnstr(row + self._row_offset, column + self._column_offset, text, count, attr)


def _setup_colors() -> None:
    if not curses.has_colors():
        return
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(PAIR_TITLE, curses.COLOR_BLACK, curses.COLOR_CYAN)
    curses.init_pair(PAIR_HEADER, curses.COLOR_CYAN, -1)
    # Default foreground (not COLOR_BLACK) so borders/separators stay visible on
    # dark terminals; the subdued look comes from A_DIM in _attr(). Using black
    # here rendered every box-drawing line invisible against a dark background.
    curses.init_pair(PAIR_DIM, -1, -1)
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
    if pair == PAIR_DIM:
        extra |= curses.A_DIM
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


def _can_draw_outer_border(height: int, width: int) -> bool:
    return height >= OUTER_BORDER_MIN_HEIGHT and width >= MIN_SCREEN_WIDTH + 2


def _draw_outer_border(screen, height: int, width: int) -> None:
    if height < 2 or width < 2:
        return
    attr = _attr(PAIR_DIM)
    top = "╒" + "═" * (width - 2) + "╕"
    bottom = "╘" + "═" * (width - 2) + "╛"
    _try_addnstr(screen, 0, 0, top, width, attr)
    _try_addnstr(screen, height - 1, 0, bottom, width, attr)
    for row in range(1, height - 1):
        _try_addnstr(screen, row, 0, "│", 1, attr)
        _try_addnstr(screen, row, width - 1, "│", 1, attr)


def _try_addnstr(screen, row: int, column: int, text: str, count: int, attr: int = 0) -> None:
    try:
        screen.addnstr(row, column, text, count, attr)
    except curses.error:
        pass


def _with_outer_text_border(lines: list[str], width: int) -> list[str]:
    if width < 2:
        return lines
    inner_width = width - 2
    top = "╒" + "═" * inner_width + "╕"
    bottom = "╘" + "═" * inner_width + "╛"
    body = ["│" + line[:inner_width].ljust(inner_width) + "│" for line in lines]
    return [top, *body, bottom]


def _clamp_scroll(offset: int, content_lines: int, viewport_lines: int) -> int:
    max_offset = max(0, content_lines - max(0, viewport_lines))
    return max(0, min(offset, max_offset))


def _mouse_scroll_delta(button_state: int) -> int:
    if button_state & getattr(curses, "BUTTON4_PRESSED", 0):
        return -SCROLL_STEP
    if button_state & getattr(curses, "BUTTON5_PRESSED", 0):
        return SCROLL_STEP
    return 0


def _draw_line(
    screen,
    row: int,
    line: str,
    width: int,
    host_context: tuple[str, float | None, bool] | None = None,
) -> None:
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
    if host_context is not None or _is_host_data_line(line):
        _draw_host_data_line(screen, row, line, width, host_context)
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


def _draw_version_line(screen, row: int, line: str, width: int) -> None:
    _safe_addnstr(screen, row, 0, line, width, _attr(PAIR_VALUE, curses.A_BOLD))


def _intensity_pair(value: float | None, *, memory: bool) -> int:
    if value is None:
        return PAIR_WARN
    thresholds = _rendering.MEM_THRESHOLDS if memory else _rendering.GPU_THRESHOLDS
    if value >= thresholds[1]:
        return PAIR_HOT
    if value >= thresholds[0]:
        return PAIR_WARN
    return PAIR_GOOD


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


def _host_section_pair(section: str | None) -> int | None:
    if section == "cpu":
        return PAIR_HEADER
    if section == "mem":
        return PAIR_MEM
    if section == "swp":
        return PAIR_SWAP
    return None


def _draw_host_section(
    screen,
    row: int,
    cursor: int,
    text: str,
    width: int,
    pair: int,
    graph_pair: int | None = None,
) -> int:
    """Draw overlay text with ``pair`` and braille graph runs with ``graph_pair``."""
    text_attr = _attr(pair, curses.A_BOLD)
    graph_attr = _attr(graph_pair if graph_pair is not None else pair)
    local_cursor = 0
    for match in _BRAILLE_RUN_RE.finditer(text):
        if match.start() > local_cursor:
            cursor = _safe_addnstr(screen, row, cursor, text[local_cursor : match.start()], width, text_attr)
        cursor = _safe_addnstr(screen, row, cursor, match.group(), width, graph_attr)
        local_cursor = match.end()
    return _safe_addnstr(screen, row, cursor, text[local_cursor:], width, text_attr)


def _draw_host_data_line(
    screen,
    row: int,
    line: str,
    width: int,
    host_context: tuple[str, float | None, bool] | None = None,
) -> None:
    pieces = line.split("│")
    cursor = 0
    if not pieces or pieces[0]:
        _safe_addnstr(screen, row, 0, line, width, _attr(PAIR_VALUE, curses.A_BOLD))
        return
    section, right_value, right_is_memory = host_context or (None, None, False)
    section_pair = _host_section_pair(section)
    cursor = _safe_addnstr(screen, row, cursor, "│", width, _attr(PAIR_DIM))
    if len(pieces) > 1:
        left_pair = _host_left_pair(pieces[1])
        cursor = _draw_host_section(
            screen, row, cursor, pieces[1], width, left_pair,
            section_pair if section_pair is not None else left_pair,
        )
        cursor = _safe_addnstr(screen, row, cursor, "│", width, _attr(PAIR_DIM))
    if len(pieces) > 2:
        right_text = pieces[2]
        right_pair = _gpu_metric_pair(right_text) if "GPU " in right_text else PAIR_VALUE
        graph_pair = (
            _intensity_pair(right_value, memory=right_is_memory) if right_value is not None else right_pair
        )
        cursor = _draw_host_section(screen, row, cursor, right_text, width, right_pair, graph_pair)
        cursor = _safe_addnstr(screen, row, cursor, "│", width, _attr(PAIR_DIM))
    for extra in pieces[3:]:
        if not extra:
            continue
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
    if _is_border_line(line):
        return _attr(PAIR_DIM)
    if _is_header_line(line):
        return _attr(PAIR_HEADER, curses.A_BOLD)
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


def _selected_process(state: UiState, frame: FrameSnapshot | None) -> ProcessSnapshot | None:
    if frame is None:
        return None
    procs = sort_processes(frame.processes, state.process_sort, state.reverse_sort)
    for proc in procs:
        if proc.selection_key == state.selected_key:
            return proc
    return None


def _handle_key(key: int, state: UiState, frame: FrameSnapshot | None, sampler: SnapshotSampler) -> bool:
    if key in {ord("q"), ord("Q"), 27, 3}:
        return False
    if key == -1:
        return True
    if state.pending_signal is not None:
        label, signum, pid = state.pending_signal
        state.pending_signal = None
        if key in {ord("y"), ord("Y")}:
            err = send_signal(pid, signum)
            state.status_message = err or f"sent {label} to pid {pid}"
        else:
            state.status_message = "cancelled"
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
    elif 0 <= key <= 255 and chr(key) in SIGNAL_KEYS and frame is not None:
        target = _selected_process(state, frame)
        if target is None:
            state.status_message = "no process selected"
        else:
            label, signum = SIGNAL_KEYS[chr(key)]
            state.pending_signal = (label, signum, target.pid)
            state.status_message = f"send {label} to pid {target.pid}? (y/n)"
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
        # Repaint only when something actually changed: a new sampler frame,
        # a key press (every UI mutation goes through keys, including
        # KEY_RESIZE/mouse), or a terminal size change — plus a 1 Hz heartbeat
        # so the host history graphs keep scrolling when --interval > 1s.
        # Idle cost drops from a full render + colorize every 100 ms tick to
        # a plain getch wait.
        painted_version = -1
        painted_size = (-1, -1)
        painted_at = 0.0
        while True:
            sampler_state = sampler.snapshot()
            frame = _filtered_frame(sampler_state.frame, options) if sampler_state.frame is not None else None
            key = screen.getch()
            if not _handle_key(key, state, frame, sampler):
                break
            size = screen.getmaxyx()
            now = time.monotonic()
            if (
                key == -1
                and sampler_state.version == painted_version
                and size == painted_size
                and now - painted_at < 1.0
            ):
                continue
            painted_version = sampler_state.version
            painted_size = size
            painted_at = now

            screen.erase()
            height, width = size
            bordered = _can_draw_outer_border(height, width)
            draw_screen = _OffsetScreen(screen, 1, 1) if bordered else screen
            draw_height = height - 2 if bordered else height
            draw_width = width - 2 if bordered else width
            if bordered:
                _draw_outer_border(screen, height, width)

            if draw_width < MIN_TUI_WIDTH:
                message = (
                    f"mxtop needs at least a width of {MIN_TUI_WIDTH} to render, "
                    f"the current width is {draw_width}."
                )
                _safe_addnstr(draw_screen, 0, 0, message, draw_width, _attr(PAIR_ERROR, curses.A_BOLD))
                _safe_addnstr(draw_screen, 1, 0, "Widen the terminal or press q to quit.", draw_width, _attr(PAIR_DIM))
                screen.refresh()
                continue

            if frame is None:
                error = sampler_state.error or "loading telemetry"
                _safe_addnstr(draw_screen, 0, 0, f"MXTOP  {error}", draw_width, _attr(PAIR_TITLE, curses.A_BOLD))
                _safe_addnstr(draw_screen, 1, 0, "q: quit  r: refresh", draw_width, _attr(PAIR_DIM))
                screen.refresh()
                continue

            rendered = render_main_screen(
                frame,
                state,
                width=max(80, draw_width),
                height=draw_height,
                interval=interval,
                error=sampler_state.error,
            )
            host_context = host_graph_context(rendered.lines)
            for row, line in enumerate(rendered.lines[:draw_height]):
                _draw_line(draw_screen, row, line, draw_width, host_context.get(row))
            final_lines = _with_outer_text_border(rendered.lines, width) if bordered else rendered.lines
            final_rendered = "\n".join(final_lines)
            screen.refresh()

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
