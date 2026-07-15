from __future__ import annotations

import curses
import getpass
import os
import re
import signal
import sys
import time
from collections.abc import Sequence
from typing import Any

from mxtop.backends import TelemetryBackend
from mxtop.filters import (
    apply_filters,
    requested_process_contexts,
    resolve_visible_device_indices,
    validate_process_contexts,
)
from mxtop.models import PROCESS_CREATE_TIME_TOLERANCE, FrameSnapshot, ProcessSnapshot
from mxtop import rendering as _rendering
from mxtop.sampler import SnapshotSampler
from mxtop.ui import classify
from mxtop.ui.classify import host_graph_context
from mxtop.ui.history import HostHistory
from mxtop.ui.panels import (
    MIN_SCREEN_WIDTH,
    render_host_panel,
    render_main_screen,
    render_small_terminal_message,
)
from mxtop.ui.screens import (
    ProcessMetricsHistory,
    ProcessTreeEntry,
    RenderedView,
    build_process_tree,
    find_process,
    read_process_environment,
    render_environment_screen,
    render_help_screen,
    render_metrics_screen,
    render_signal_dialog,
    render_tree_screen,
)
from mxtop.ui.state import (
    DIRECT_SORT_KEYS,
    LayoutMode,
    ProcessSignal,
    ScreenMode,
    UiState,
    keep_selection,
    next_sort,
    sort_processes,
)
from mxtop.ui.text import cell_ljust, cell_slice, cell_width, to_ascii

# Line classification and value parsing live in mxtop.ui.classify, shared with
# the ANSI renderer. Local aliases keep this module's call sites short.
_is_border_line = classify.is_border_line
_is_device_data_line = classify.is_device_data_line
_is_header_line = classify.is_header_line
_is_host_data_line = classify.is_host_data_line
_is_process_data_line = classify.is_process_data_line
_is_version_line = classify.is_version_line
_parse_percent = classify.parse_percent
_BAR_RE = classify.BAR_RE
_BRAILLE_RUN_RE = classify.BRAILLE_RUN_RE
_GPU_METRIC_RE = classify.GPU_METRIC_RE
_PROCESS_ROW_FIELDS_RE = classify.PROCESS_ROW_FIELDS_RE

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
PAIR_BRIGHT_GREEN = 12
PAIR_BRIGHT_YELLOW = 13
PAIR_BRIGHT_RED = 14
PAIR_SPECTRUM_FIRST = 15
SPECTRUM_COLORS = (40, 46, 190, 226, 208, 196)
PAIR_TREE_SELECTED = 21
MIN_TUI_WIDTH = MIN_SCREEN_WIDTH

SCROLL_STEP = 3
CTRL_SCROLL_MULTIPLIER = 5
ALT_KEY_BASE = 1 << 20
LARGE_SCROLL_OFFSET = 1 << 30
CURSOR_HOME = "\x1b[H"
CLEAR_TO_END = "\x1b[J"
HIDE_CURSOR = "\x1b[?25l"
SHOW_CURSOR = "\x1b[?25h"
SIGNAL_OPTIONS = (
    ProcessSignal.TERMINATE,
    ProcessSignal.KILL,
    ProcessSignal.INTERRUPT,
)

_METRICS_VALUE_RE = re.compile(
    r"(?:MAX )?(GPU-MEM|GPU-SM):\s*.*?((?:\d+(?:\.\d+)?)%|N/A)"
)


def render_ascii(text: str) -> str:
    return to_ascii(text)


def _draw_small_terminal(screen, width: int, height: int, *, no_unicode: bool) -> None:
    lines = render_small_terminal_message(width, height).lines
    if no_unicode:
        lines = [render_ascii(line) for line in lines]
    for row, line in enumerate(lines[:height]):
        _safe_addnstr(screen, row, 0, line, width, _line_attr(row, line))


def _alt_key(character: str) -> int:
    return ALT_KEY_BASE + ord(character)


def _setup_colors() -> None:
    if not curses.has_colors():
        return
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(PAIR_TITLE, curses.COLOR_BLACK, curses.COLOR_CYAN)
    curses.init_pair(PAIR_HEADER, curses.COLOR_CYAN, -1)
    # Black is appropriate only for an explicitly light terminal theme.
    value_color = curses.COLOR_BLACK if _rendering.LIGHT_THEME else curses.COLOR_WHITE
    curses.init_pair(PAIR_DIM, curses.COLOR_BLACK if _rendering.LIGHT_THEME else -1, -1)
    curses.init_pair(PAIR_VALUE, value_color, -1)
    curses.init_pair(PAIR_GOOD, curses.COLOR_GREEN, -1)
    curses.init_pair(PAIR_WARN, curses.COLOR_YELLOW, -1)
    curses.init_pair(PAIR_HOT, curses.COLOR_RED, -1)
    curses.init_pair(PAIR_MEM, curses.COLOR_MAGENTA, -1)
    curses.init_pair(PAIR_ERROR, curses.COLOR_WHITE, curses.COLOR_RED)
    curses.init_pair(PAIR_SELECTED, curses.COLOR_CYAN, -1)
    curses.init_pair(PAIR_SWAP, curses.COLOR_BLUE, -1)
    bright_green, bright_yellow, bright_red = (
        (10, 11, 9)
        if getattr(curses, "COLORS", 0) >= 16
        else (curses.COLOR_GREEN, curses.COLOR_YELLOW, curses.COLOR_RED)
    )
    curses.init_pair(PAIR_BRIGHT_GREEN, bright_green, -1)
    curses.init_pair(PAIR_BRIGHT_YELLOW, bright_yellow, -1)
    curses.init_pair(PAIR_BRIGHT_RED, bright_red, -1)
    if getattr(curses, "COLORS", 0) >= 256:
        for offset, color in enumerate(SPECTRUM_COLORS):
            curses.init_pair(PAIR_SPECTRUM_FIRST + offset, color, -1)
    curses.init_pair(PAIR_TREE_SELECTED, curses.COLOR_GREEN, -1)


def _attr(pair: int, extra: int = 0) -> int:
    if not curses.has_colors():
        return extra
    if pair == PAIR_DIM:
        extra |= curses.A_DIM
    return curses.color_pair(pair) | extra


def _safe_addnstr(
    screen, row: int, column: int, text: str, width: int, attr: int = 0
) -> int:
    if row < 0 or column < 0 or column >= width:
        return column
    available = width - column
    if available <= 0 or not text:
        return column
    snippet = cell_slice(text, 0, available)
    try:
        screen.addnstr(row, column, snippet, available, attr)
    except curses.error:
        return width
    return column + cell_width(snippet)


def _clamp_scroll(offset: int, content_lines: int, viewport_lines: int) -> int:
    max_offset = max(0, content_lines - max(0, viewport_lines))
    return max(0, min(offset, max_offset))


def _mouse_scroll_delta(button_state: int) -> int:
    if button_state & getattr(curses, "BUTTON4_PRESSED", 0):
        return -SCROLL_STEP
    if button_state & getattr(curses, "BUTTON5_PRESSED", 0):
        return SCROLL_STEP
    return 0


def _mouse_wheel_direction(button_state: int) -> int:
    if button_state & getattr(curses, "BUTTON4_PRESSED", 0):
        direction = -1
    elif button_state & getattr(curses, "BUTTON5_PRESSED", 1 << 21):
        direction = 1
    else:
        return 0
    if button_state & getattr(curses, "BUTTON_CTRL", 0):
        direction *= CTRL_SCROLL_MULTIPLIER
    return direction


def _mouse_clicked(button_state: int) -> bool:
    masks = (
        getattr(curses, "BUTTON1_PRESSED", 0),
        getattr(curses, "BUTTON1_CLICKED", 0),
        getattr(curses, "BUTTON3_PRESSED", 0),
        getattr(curses, "BUTTON3_CLICKED", 0),
    )
    return any(mask and button_state & mask for mask in masks)


def _draw_line(
    screen,
    row: int,
    line: str,
    width: int,
    host_context: tuple[str, float | None, bool] | None = None,
    *,
    device_level: int | None = None,
    device_dim: bool = False,
    dense_device_context: tuple[tuple[int, int, int, int], ...] | None = None,
    selected_gpu_index: int | None = None,
    process_context: tuple[int, bool] | None = None,
    process_tagged: bool | None = None,
    process_linked: bool = False,
    semantic_line: str | None = None,
) -> None:
    semantic_line = line if semantic_line is None else semantic_line
    attr = _line_attr(row, semantic_line)
    if "SUPERUSER LOGGED-IN" in semantic_line or "send signals)" in semantic_line:
        _draw_process_action_line(screen, row, line, width)
        return
    if row == 0 and (
        "(Press h for help or q to quit)" in semantic_line
        or semantic_line.startswith("mxtop ")
    ):
        _draw_title_line(screen, row, line, width)
        return
    if _is_version_line(semantic_line):
        _draw_version_line(screen, row, line, width)
        return
    if "Processes:" in semantic_line and "@" in semantic_line:
        _draw_process_title_line(screen, row, line, width, attr)
        return
    if " GPU     PID      USER  GPU-MEM" in semantic_line:
        _draw_process_header_line(screen, row, line, width)
        return
    if _is_process_data_line(semantic_line):
        _draw_process_data_line(
            screen,
            row,
            line,
            width,
            attr,
            process_context,
            tagged=process_tagged,
            linked=process_linked,
            semantic_line=semantic_line,
        )
        return
    if dense_device_context:
        _draw_dense_device_data_line(
            screen,
            row,
            line,
            width,
            dense_device_context,
            selected_gpu_index=selected_gpu_index,
        )
        return
    if _is_device_data_line(semantic_line):
        _draw_device_data_line(
            screen,
            row,
            line,
            width,
            device_level,
            dim=device_dim,
            semantic_line=semantic_line,
        )
        return
    if semantic_line.startswith("[ CPU:") or semantic_line.startswith("[ MEM:"):
        _draw_compact_host_line(screen, row, line, width)
        return
    if host_context is not None or _is_host_data_line(semantic_line):
        _draw_host_data_line(
            screen,
            row,
            line,
            width,
            host_context,
            semantic_line=semantic_line,
        )
        return
    _safe_addnstr(screen, row, 0, line, width, attr)


def _draw_process_header_line(screen, row: int, line: str, width: int) -> None:
    base_attr = _attr(PAIR_HEADER, curses.A_BOLD)
    _safe_addnstr(screen, row, 0, line, width, base_attr)
    match = re.search(r"([A-Z%][A-Z0-9%-]*)([▲▼])", line)
    if match is None:
        return
    _safe_addnstr(
        screen,
        row,
        match.start(1),
        match.group(1),
        width,
        _attr(PAIR_HEADER, curses.A_BOLD | curses.A_UNDERLINE),
    )


def _draw_title_line(screen, row: int, line: str, width: int) -> None:
    hint_start = line.find("(Press ")
    if hint_start < 0:
        _safe_addnstr(screen, row, 0, line, width, _attr(PAIR_VALUE, curses.A_BOLD))
        return
    position = _safe_addnstr(
        screen, row, 0, line[:hint_start], width, _attr(PAIR_VALUE, curses.A_BOLD)
    )
    hint = line[hint_start:]
    for token in ("h", "q"):
        prefix, found, rest = hint.partition(token)
        position = _safe_addnstr(
            screen, row, position, prefix, width, _attr(PAIR_VALUE, curses.A_BOLD)
        )
        if not found:
            return
        position = _safe_addnstr(
            screen, row, position, found, width, _attr(PAIR_MEM, curses.A_BOLD)
        )
        hint = rest
    _safe_addnstr(screen, row, position, hint, width, _attr(PAIR_VALUE, curses.A_BOLD))


def _draw_process_action_line(screen, row: int, line: str, width: int) -> None:
    _safe_addnstr(screen, row, 0, line, width, _attr(PAIR_DIM))
    caution = "!CAUTION: SUPERUSER LOGGED-IN."
    if (start := line.find(caution)) >= 0:
        _safe_addnstr(
            screen,
            row,
            start,
            "!",
            width,
            _attr(PAIR_HOT, curses.A_BOLD | curses.A_BLINK),
        )
        _safe_addnstr(
            screen,
            row,
            start + 1,
            caution[1:],
            width,
            _attr(PAIR_WARN, getattr(curses, "A_ITALIC", 0)),
        )
    for match in re.finditer(r"(\^C|T|K)(\((?:INT|TERM|KILL)\))", line):
        _safe_addnstr(
            screen,
            row,
            match.start(1),
            match.group(1),
            width,
            _attr(PAIR_MEM, curses.A_BOLD),
        )
        _safe_addnstr(
            screen,
            row,
            match.start(2),
            match.group(2),
            width,
            _attr(PAIR_HOT, curses.A_BOLD),
        )


def _signal_modal_buttons(
    dialog: Sequence[str],
    dialog_x: int,
    dialog_y: int,
) -> dict[tuple[int, int], int]:
    options_row = next(
        (
            index
            for index, line in enumerate(dialog)
            if "SIGTERM" in line and "SIGKILL" in line
        ),
        -1,
    )
    if options_row < 0:
        return {}
    buttons: dict[tuple[int, int], int] = {}
    for option in range(4):
        geometry = _signal_button_geometry(dialog, option)
        if geometry is None:
            continue
        _, left, right = geometry
        for y in range(max(0, dialog_y + options_row - 1), dialog_y + options_row + 2):
            for x in range(left, right):
                buttons[dialog_x + x, y] = option
    return buttons


def _signal_button_geometry(
    dialog: Sequence[str],
    option: int,
) -> tuple[int, int, int] | None:
    names = ("SIGTERM", "SIGKILL", "SIGINT", "Cancel")
    if not 0 <= option < len(names):
        return None
    options_row = next(
        (
            index
            for index, line in enumerate(dialog)
            if "SIGTERM" in line and "SIGKILL" in line
        ),
        -1,
    )
    if options_row < 0:
        return None
    name = names[option]
    name_x = dialog[options_row].find(name)
    if name_x < 0:
        return None
    return (
        options_row,
        max(0, name_x - 2),
        min(len(dialog[options_row]), name_x + len(name) + 2),
    )


def _draw_signal_dialog_line(
    screen,
    row: int,
    base_line: str,
    dialog_line: str | None,
    dialog_x: int,
    width: int,
    current_option: int,
    *,
    dialog_row: int | None = None,
    selected_button: tuple[int, int, int] | None = None,
) -> None:
    _safe_addnstr(screen, row, 0, base_line, width, _attr(PAIR_DIM))
    if dialog_line is None:
        return
    base_pair = PAIR_DIM if _is_border_line(dialog_line) else PAIR_VALUE
    _safe_addnstr(
        screen,
        row,
        dialog_x,
        dialog_line,
        width,
        _attr(base_pair, curses.A_BOLD),
    )
    for option, name in enumerate(("SIGTERM", "SIGKILL", "SIGINT", "Cancel")):
        start = dialog_line.find(name)
        if start < 0:
            continue
        if option == current_option and selected_button is None:
            left = max(0, start - 1)
            right = min(len(dialog_line), start + len(name) + 1)
            _safe_addnstr(
                screen,
                row,
                dialog_x + left,
                dialog_line[left:right],
                width,
                _attr(PAIR_SELECTED, curses.A_BOLD | curses.A_REVERSE),
            )
        else:
            pair = PAIR_HOT if option < 3 else PAIR_VALUE
            _safe_addnstr(
                screen,
                row,
                dialog_x + start,
                name,
                width,
                _attr(pair, curses.A_BOLD),
            )
    if selected_button is not None and dialog_row is not None:
        options_row, left, right = selected_button
        if options_row - 1 <= dialog_row <= options_row + 1:
            _safe_addnstr(
                screen,
                row,
                dialog_x + left,
                dialog_line[left:right],
                width,
                _attr(PAIR_SELECTED, curses.A_BOLD | curses.A_REVERSE),
            )


def _draw_help_line(screen, row: int, line: str, width: int, *, readonly: bool) -> None:
    if row in {0, 1} or line.rstrip() == "Press any key to return.":
        _safe_addnstr(screen, row, 0, line, width, _attr(PAIR_HEADER, curses.A_BOLD))
        return

    _safe_addnstr(screen, row, 0, line, width, _attr(PAIR_VALUE))
    if row == 3:
        _safe_addnstr(
            screen, row, 0, line[:17], width, _attr(PAIR_VALUE, curses.A_BOLD)
        )
        for match in re.finditer(r"\b[CGX]\b", line):
            _safe_addnstr(
                screen,
                row,
                match.start(),
                match.group(),
                width,
                _attr(PAIR_MEM, curses.A_BOLD),
            )
        return
    if row == 5:
        _safe_addnstr(screen, row, 0, line, width, _attr(PAIR_VALUE, curses.A_BOLD))
        return
    if row in {6, 7}:
        for text, pair in (
            ("light", PAIR_GOOD),
            ("moderate", PAIR_WARN),
            ("heavy", PAIR_HOT),
        ):
            for match in re.finditer(text, line):
                _safe_addnstr(
                    screen,
                    row,
                    match.start(),
                    match.group(),
                    width,
                    _attr(pair, curses.A_BOLD | getattr(curses, "A_ITALIC", 0)),
                )

    color_matrix = {
        9: (PAIR_GOOD, PAIR_GOOD),
        10: (PAIR_GOOD, PAIR_GOOD),
        12: (PAIR_HEADER, PAIR_WARN),
        13: (PAIR_HEADER, PAIR_WARN),
        14: (PAIR_HEADER, PAIR_HOT),
        15: (None, PAIR_HOT),
        16: (PAIR_HEADER, PAIR_HOT),
        17: (PAIR_HEADER, PAIR_GOOD),
        18: (PAIR_HEADER, PAIR_GOOD),
        19: (PAIR_HEADER, PAIR_GOOD),
        21: (PAIR_SWAP, PAIR_SWAP),
        22: (PAIR_SWAP, PAIR_SWAP),
        24: (PAIR_SWAP, PAIR_SWAP),
        25: (PAIR_SWAP, PAIR_SWAP),
        26: (PAIR_SWAP, PAIR_SWAP),
        27: (PAIR_SWAP, PAIR_SWAP),
        28: (PAIR_SWAP, PAIR_SWAP),
        29: (PAIR_MEM, PAIR_MEM),
    }
    left_pair, right_pair = color_matrix.get(row, (None, None))
    if left_pair is not None:
        _safe_addnstr(screen, row, 0, line[:12], width, _attr(left_pair, curses.A_BOLD))
    if readonly and row in {14, 15, 16}:
        _safe_addnstr(screen, row, 39, line[39:], width, _attr(PAIR_DIM))
    elif right_pair is not None:
        _safe_addnstr(
            screen, row, 39, line[39:52], width, _attr(right_pair, curses.A_BOLD)
        )


def _draw_environment_line(screen, row: int, line: str, width: int) -> None:
    if row == 0:
        end = line.find("): ")
        end = len(line) if end < 0 else end + 2
        position = _safe_addnstr(
            screen, row, 0, line[:end], width, _attr(PAIR_HEADER, curses.A_BOLD)
        )
        _safe_addnstr(screen, row, position, line[end:], width, _attr(PAIR_VALUE))
        return
    if row == 1:
        _safe_addnstr(screen, row, 0, line, width, _attr(PAIR_GOOD, curses.A_BOLD))
        return
    if line.startswith("ERROR:") or line.startswith("Could not read"):
        _safe_addnstr(screen, row, 0, line, width, _attr(PAIR_HEADER, curses.A_REVERSE))
        return
    separator = line.find("=")
    if separator < 0:
        _safe_addnstr(screen, row, 0, line, width, _attr(PAIR_VALUE))
        return
    position = _safe_addnstr(
        screen, row, 0, line[:separator], width, _attr(PAIR_SWAP, curses.A_BOLD)
    )
    position = _safe_addnstr(screen, row, position, "=", width, _attr(PAIR_MEM))
    _safe_addnstr(
        screen, row, position, line[separator + 1 :], width, _attr(PAIR_VALUE)
    )


def _draw_tree_line(
    screen,
    row: int,
    line: str,
    width: int,
    *,
    user: str | None = None,
    semantic_line: str | None = None,
) -> None:
    if row == 0:
        _safe_addnstr(
            screen,
            row,
            0,
            line,
            width,
            _attr(PAIR_HEADER, curses.A_BOLD | curses.A_REVERSE),
        )
        semantic_line = line if semantic_line is None else semantic_line
        for match in re.finditer(r"(\^C|T|K)(\((?:INT|TERM|KILL)\))", semantic_line):
            key_column = cell_width(semantic_line[: match.start(1)])
            name_column = cell_width(semantic_line[: match.start(2)])
            _safe_addnstr(
                screen,
                row,
                key_column,
                cell_slice(line, key_column, cell_width(match.group(1))),
                width,
                _attr(PAIR_MEM, curses.A_BOLD | curses.A_REVERSE),
            )
            _safe_addnstr(
                screen,
                row,
                name_column,
                cell_slice(line, name_column, cell_width(match.group(2))),
                width,
                _attr(PAIR_HOT, curses.A_BOLD | curses.A_REVERSE),
            )
        return
    owned = _is_superuser() or user is None or user == getpass.getuser()
    base_attr = _attr(PAIR_VALUE if owned else PAIR_DIM)
    _safe_addnstr(screen, row, 0, line, width, base_attr)
    semantic_line = line if semantic_line is None else semantic_line
    branch = re.search(r"(?:│  |   )*(?:├─ |└─ )", semantic_line)
    if branch is not None:
        branch_column = cell_width(semantic_line[: branch.start()])
        branch_width = cell_width(semantic_line[branch.start() : branch.end()])
        _safe_addnstr(
            screen,
            row,
            branch_column,
            cell_slice(line, branch_column, branch_width),
            width,
            _attr(PAIR_GOOD, curses.A_BOLD),
        )


def _metrics_graph_context(lines: Sequence[str]) -> dict[int, tuple[int, int, int]]:
    graph_start = next(
        (row for row, line in enumerate(lines) if line.startswith("╞") and "╤" in line),
        None,
    )
    if graph_start is None:
        return {}
    split_column = lines[graph_start].find("╤")
    if split_column <= 0:
        return {}
    graph_middle = next(
        (
            row
            for row, line in enumerate(lines[graph_start + 1 :], start=graph_start + 1)
            if line.startswith("├") and "┼" in line
        ),
        None,
    )
    if graph_middle is None:
        return {}
    graph_end = next(
        (
            row
            for row, line in enumerate(
                lines[graph_middle + 1 :], start=graph_middle + 1
            )
            if line.startswith("╘") and "╧" in line
        ),
        len(lines),
    )
    values: dict[str, float | None] = {}
    for line in lines:
        for match in _METRICS_VALUE_RE.finditer(line):
            values[match.group(1)] = _parse_percent(match.group(2))
    gpu_memory_pair = _intensity_pair(values.get("GPU-MEM"), memory=True)
    gpu_sm_pair = _intensity_pair(values.get("GPU-SM"), memory=False)
    context = {
        row: (PAIR_HEADER, gpu_memory_pair, split_column)
        for row in range(graph_start + 1, graph_middle)
    }
    context.update(
        {
            row: (PAIR_MEM, gpu_sm_pair, split_column)
            for row in range(graph_middle + 1, graph_end)
        }
    )
    return context


def _draw_metrics_graph_cell(
    screen,
    row: int,
    cursor: int,
    text: str,
    semantic_text: str,
    width: int,
    graph_pair: int,
) -> int:
    runs = list(_BRAILLE_RUN_RE.finditer(semantic_text))
    if not runs and "=" in semantic_text:
        runs = list(re.finditer(r"=+", semantic_text))
    local_cursor = 0
    for match in runs:
        cursor = _draw_metrics_overlay_text(
            screen,
            row,
            cursor,
            text[local_cursor : match.start()],
            width,
        )
        cursor = _safe_addnstr(
            screen,
            row,
            cursor,
            text[match.start() : match.end()],
            width,
            _attr(graph_pair),
        )
        local_cursor = match.end()
    return _draw_metrics_overlay_text(
        screen,
        row,
        cursor,
        text[local_cursor:],
        width,
    )


def _draw_metrics_overlay_text(
    screen,
    row: int,
    cursor: int,
    text: str,
    width: int,
) -> int:
    local_cursor = 0
    for match in re.finditer(r"╴(?:\d+(?:\.\d+)?%|\d+s)", text):
        cursor = _safe_addnstr(
            screen,
            row,
            cursor,
            text[local_cursor : match.start()],
            width,
            _attr(PAIR_VALUE, curses.A_BOLD),
        )
        cursor = _safe_addnstr(
            screen,
            row,
            cursor,
            match.group(),
            width,
            _attr(PAIR_DIM),
        )
        local_cursor = match.end()
    return _safe_addnstr(
        screen,
        row,
        cursor,
        text[local_cursor:],
        width,
        _attr(PAIR_VALUE, curses.A_BOLD),
    )


def _draw_metrics_line(
    screen,
    row: int,
    line: str,
    width: int,
    graph_context: tuple[int, int, int] | None = None,
    process_context: tuple[int, bool] | None = None,
    *,
    semantic_line: str | None = None,
) -> None:
    semantic_line = line if semantic_line is None else semantic_line
    if row == 1 and "Process:" in semantic_line and "@" in semantic_line:
        _draw_process_title_line(
            screen,
            row,
            line,
            width,
            _attr(PAIR_HEADER, curses.A_BOLD),
        )
        return
    if row == 2 and " GPU     PID      USER  GPU-MEM" in semantic_line:
        _draw_process_header_line(screen, row, line, width)
        return
    if _is_process_data_line(semantic_line):
        _draw_process_data_line(
            screen,
            row,
            line,
            width,
            _attr(PAIR_VALUE),
            process_context,
            semantic_line=semantic_line,
        )
        return
    if graph_context is None:
        if semantic_line.startswith("├") and "╴" in semantic_line:
            _safe_addnstr(screen, row, 0, line, width, _attr(PAIR_DIM))
            return
        _safe_addnstr(screen, row, 0, line, width, _line_attr(row, semantic_line))
        return

    left_pair, right_pair, split_column = graph_context
    if split_column >= len(line) - 1 or split_column >= len(semantic_line) - 1:
        _safe_addnstr(screen, row, 0, line, width, _attr(PAIR_VALUE))
        return
    cursor = _safe_addnstr(screen, row, 0, line[0], width, _attr(PAIR_DIM))
    cursor = _draw_metrics_graph_cell(
        screen,
        row,
        cursor,
        line[1:split_column],
        semantic_line[1:split_column],
        width,
        left_pair,
    )
    cursor = _safe_addnstr(
        screen,
        row,
        cursor,
        line[split_column],
        width,
        _attr(PAIR_DIM),
    )
    cursor = _draw_metrics_graph_cell(
        screen,
        row,
        cursor,
        line[split_column + 1 : -1],
        semantic_line[split_column + 1 : -1],
        width,
        right_pair,
    )
    _safe_addnstr(screen, row, cursor, line[-1], width, _attr(PAIR_DIM))


def _draw_process_title_line(
    screen, row: int, line: str, width: int, attr: int
) -> None:
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
    user_pair = PAIR_WARN if line[start:at] == "root" else PAIR_MEM
    position = _safe_addnstr(
        screen, row, position, line[start:at], width, _attr(user_pair, curses.A_BOLD)
    )
    position = _safe_addnstr(screen, row, position, "@", width, attr)
    end = line.find("│" if "│" in line else "|", at)
    if end < 0:
        end = len(line)
    position = _safe_addnstr(
        screen,
        row,
        position,
        line[at + 1 : end],
        width,
        _attr(PAIR_GOOD, curses.A_BOLD),
    )
    _safe_addnstr(screen, row, position, line[end:], width, attr)


def _tree_tagged_attr(user: str | None) -> int:
    extra = curses.A_BOLD
    if not _is_superuser() and user != getpass.getuser():
        extra |= curses.A_DIM
    return _attr(PAIR_WARN, extra)


def _draw_process_data_line(
    screen,
    row: int,
    line: str,
    width: int,
    attr: int,
    context: tuple[int, bool] | None = None,
    *,
    tagged: bool | None = None,
    linked: bool = False,
    semantic_line: str | None = None,
) -> None:
    del attr
    semantic_line = line if semantic_line is None else semantic_line
    level, owned = context or (0, True)
    if tagged is None:
        tagged = False
    host_attr = _attr(PAIR_VALUE if owned else PAIR_DIM)
    match = _PROCESS_ROW_FIELDS_RE.search(semantic_line)
    if match is None:
        _safe_addnstr(screen, row, 0, line, width, host_attr)
        return
    position = _safe_addnstr(
        screen, row, 0, line[: match.end("prefix")], width, _attr(PAIR_VALUE)
    )
    position = _safe_addnstr(
        screen,
        row,
        position,
        line[match.start("gpu") : match.end("gpu")],
        width,
        _attr(_pair_for_level(level), curses.A_BOLD),
    )
    _safe_addnstr(screen, row, position, line[match.end("gpu") :], width, host_attr)
    if tagged and width > 6:
        _safe_addnstr(
            screen,
            row,
            5,
            cell_slice(line, 5, width - 6),
            width - 1,
            _attr(PAIR_WARN, curses.A_BOLD | (0 if owned else curses.A_DIM)),
        )
    if linked and len(line) > 1:
        _safe_addnstr(
            screen,
            row,
            1,
            cell_slice(line, 1, 1),
            width,
            _attr(PAIR_VALUE, curses.A_BOLD | curses.A_BLINK),
        )


def _draw_version_line(screen, row: int, line: str, width: int) -> None:
    _safe_addnstr(screen, row, 0, line, width, _attr(PAIR_VALUE, curses.A_BOLD))


def _draw_compact_host_line(screen, row: int, line: str, width: int) -> None:
    first_end = line.find("]") + 1
    if first_end <= 0:
        _safe_addnstr(screen, row, 0, line, width, _attr(PAIR_VALUE))
        return
    first_pair = PAIR_HEADER if line.startswith("[ CPU:") else PAIR_MEM
    position = _safe_addnstr(
        screen, row, 0, line[:first_end], width, _attr(first_pair, curses.A_BOLD)
    )
    second_start = line.find("[", first_end)
    if second_start < 0:
        _safe_addnstr(screen, row, position, line[first_end:], width, _attr(PAIR_VALUE))
        return
    position = _safe_addnstr(
        screen, row, position, line[first_end:second_start], width, _attr(PAIR_VALUE)
    )
    _safe_addnstr(
        screen,
        row,
        position,
        line[second_start:],
        width,
        _attr(PAIR_SWAP, curses.A_BOLD),
    )


def _intensity_pair(value: float | None, *, memory: bool) -> int:
    if value is None:
        return PAIR_WARN
    thresholds = _rendering.MEM_THRESHOLDS if memory else _rendering.GPU_THRESHOLDS
    if _rendering.COLORFUL_MODE:
        if getattr(curses, "COLORS", 0) >= 256:
            return _spectrum_pair(value / 100.0)
        low, high = thresholds
        mid_low = low + (high - low) / 3
        mid_high = low + 2 * (high - low) / 3
        if value >= high + (100 - high) / 2:
            return PAIR_BRIGHT_RED
        if value >= high:
            return PAIR_HOT
        if value >= mid_high:
            return PAIR_BRIGHT_YELLOW
        if value >= mid_low:
            return PAIR_WARN
        if value >= low:
            return PAIR_BRIGHT_GREEN
        return PAIR_GOOD
    if value >= thresholds[1]:
        return PAIR_HOT
    if value >= thresholds[0]:
        return PAIR_WARN
    return PAIR_GOOD


def _spectrum_pair(fraction: float) -> int:
    index = min(
        len(SPECTRUM_COLORS) - 1, max(0, round((len(SPECTRUM_COLORS) - 1) * fraction))
    )
    return PAIR_SPECTRUM_FIRST + index


def _pair_for_level(level: int) -> int:
    return (PAIR_GOOD, PAIR_WARN, PAIR_HOT)[max(0, min(level, 2))]


def _bar_pair(label: str, pct_text: str) -> int:
    return _intensity_pair(_parse_percent(pct_text), memory=label in {"MEM", "MBW"})


def _draw_device_data_line(
    screen,
    row: int,
    line: str,
    width: int,
    display_level: int | None = None,
    *,
    dim: bool = False,
    semantic_line: str | None = None,
) -> None:
    _draw_device_cells(
        screen,
        row,
        line,
        width,
        display_level,
        dim=dim,
        semantic_line=semantic_line,
    )


def _draw_dense_device_data_line(
    screen,
    row: int,
    line: str,
    width: int,
    contexts: tuple[tuple[int, int, int, int], ...],
    *,
    selected_gpu_index: int | None = None,
) -> None:
    cursor = 0
    for start, end, gpu_index, display_level in contexts:
        cursor = _safe_addnstr(
            screen,
            row,
            cursor,
            line[cursor:start],
            width,
            _attr(PAIR_DIM),
        )
        dim = selected_gpu_index is not None and gpu_index != selected_gpu_index
        cursor = _safe_addnstr(
            screen,
            row,
            cursor,
            line[start:end],
            width,
            _attr(
                _pair_for_level(display_level),
                curses.A_BOLD | (curses.A_DIM if dim else 0),
            ),
        )
    _safe_addnstr(screen, row, cursor, line[cursor:], width, _attr(PAIR_DIM))


def _split_display_cells(line: str, semantic_line: str | None) -> tuple[list[str], str]:
    semantic_line = line if semantic_line is None else semantic_line
    breakpoints = [
        index for index, character in enumerate(semantic_line) if character == "│"
    ]
    if not breakpoints:
        separator = "│" if "│" in line else "|"
        return line.split(separator), separator
    pieces: list[str] = []
    start = 0
    for breakpoint in breakpoints:
        pieces.append(line[start:breakpoint])
        start = breakpoint + 1
    pieces.append(line[start:])
    return pieces, line[breakpoints[0]]


def _draw_device_cells(
    screen,
    row: int,
    line: str,
    width: int,
    display_level: int | None = None,
    *,
    dim: bool = False,
    semantic_line: str | None = None,
) -> None:
    pieces, separator = _split_display_cells(line, semantic_line)
    semantic_pieces = (line if semantic_line is None else semantic_line).split("│")
    if len(pieces) < 3:
        _safe_addnstr(screen, row, 0, line, width, _attr(PAIR_VALUE, curses.A_BOLD))
        return
    cursor = 0
    for index, piece in enumerate(pieces):
        if index:
            cursor = _safe_addnstr(
                screen, row, cursor, separator, width, _attr(PAIR_DIM)
            )
        if not piece:
            continue
        if index == 0:
            cursor = _safe_addnstr(
                screen, row, cursor, piece, width, _attr(PAIR_VALUE, curses.A_BOLD)
            )
            continue
        if index <= 3:
            cursor = _safe_addnstr(
                screen,
                row,
                cursor,
                piece,
                width,
                _attr(
                    _pair_for_level(1 if display_level is None else display_level),
                    curses.A_BOLD | (curses.A_DIM if dim else 0),
                ),
            )
        else:
            semantic_piece = (
                semantic_pieces[index] if index < len(semantic_pieces) else piece
            )
            cursor = _draw_bar_cell(
                screen,
                row,
                cursor,
                piece,
                width,
                dim=dim,
                semantic_text=semantic_piece,
            )


def _draw_bar_cell(
    screen,
    row: int,
    cursor: int,
    text: str,
    width: int,
    *,
    dim: bool = False,
    semantic_text: str | None = None,
) -> int:
    extra = curses.A_BOLD | (curses.A_DIM if dim else 0)
    semantic_text = text if semantic_text is None else semantic_text
    local_cursor = 0
    for match in _BAR_RE.finditer(semantic_text):
        label = match.group(1)
        pct_text = match.group(3)
        pair = _bar_pair(label, pct_text)
        cursor = _safe_addnstr(
            screen,
            row,
            cursor,
            text[local_cursor : match.start()],
            width,
            _attr(PAIR_VALUE, extra),
        )
        cursor = _safe_addnstr(
            screen,
            row,
            cursor,
            text[match.start() : match.start(2)],
            width,
            _attr(PAIR_HEADER, extra),
        )
        bar = text[match.start(2) : match.end(2)]
        if _rendering.COLORFUL_MODE and getattr(curses, "COLORS", 0) >= 256:
            denominator = max(1, len(bar) - 1)
            for index, character in enumerate(bar):
                character_pair = (
                    _spectrum_pair(index / denominator)
                    if character not in {"░", " "}
                    else pair
                )
                cursor = _safe_addnstr(
                    screen,
                    row,
                    cursor,
                    character,
                    width,
                    _attr(character_pair, extra),
                )
        else:
            cursor = _safe_addnstr(
                screen,
                row,
                cursor,
                bar,
                width,
                _attr(pair, extra),
            )
        cursor = _safe_addnstr(
            screen,
            row,
            cursor,
            text[match.end(2) : match.end()],
            width,
            _attr(pair, extra),
        )
        local_cursor = match.end()
    return _safe_addnstr(
        screen, row, cursor, text[local_cursor:], width, _attr(PAIR_VALUE, extra)
    )


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
    return _intensity_pair(
        _parse_percent(match.group(2)), memory=match.group(1) == "MEM"
    )


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
    graph_runs = list(_BRAILLE_RUN_RE.finditer(text))
    if not graph_runs and "=" in text:
        graph_runs = list(re.finditer(r"=+", text))
    for match in graph_runs:
        if match.start() > local_cursor:
            cursor = _safe_addnstr(
                screen,
                row,
                cursor,
                text[local_cursor : match.start()],
                width,
                text_attr,
            )
        cursor = _safe_addnstr(screen, row, cursor, match.group(), width, graph_attr)
        local_cursor = match.end()
    return _safe_addnstr(screen, row, cursor, text[local_cursor:], width, text_attr)


def _draw_host_data_line(
    screen,
    row: int,
    line: str,
    width: int,
    host_context: tuple[str, float | None, bool] | None = None,
    *,
    semantic_line: str | None = None,
) -> None:
    pieces, separator = _split_display_cells(line, semantic_line)
    cursor = 0
    if not pieces or pieces[0]:
        _safe_addnstr(screen, row, 0, line, width, _attr(PAIR_VALUE, curses.A_BOLD))
        return
    section, right_value, right_is_memory = host_context or (None, None, False)
    section_pair = _host_section_pair(section)
    cursor = _safe_addnstr(screen, row, cursor, separator, width, _attr(PAIR_DIM))
    if len(pieces) > 1:
        left_pair = _host_left_pair(pieces[1])
        cursor = _draw_host_section(
            screen,
            row,
            cursor,
            pieces[1],
            width,
            left_pair,
            section_pair if section_pair is not None else left_pair,
        )
        cursor = _safe_addnstr(screen, row, cursor, separator, width, _attr(PAIR_DIM))
    if len(pieces) > 2:
        right_text = pieces[2]
        right_pair = (
            _gpu_metric_pair(right_text) if "GPU " in right_text else PAIR_VALUE
        )
        graph_pair = (
            _intensity_pair(right_value, memory=right_is_memory)
            if right_value is not None
            else right_pair
        )
        cursor = _draw_host_section(
            screen, row, cursor, right_text, width, right_pair, graph_pair
        )
        cursor = _safe_addnstr(screen, row, cursor, separator, width, _attr(PAIR_DIM))
    for extra in pieces[3:]:
        if not extra:
            continue
        cursor = _safe_addnstr(
            screen, row, cursor, extra, width, _attr(PAIR_VALUE, curses.A_BOLD)
        )
        cursor = _safe_addnstr(screen, row, cursor, separator, width, _attr(PAIR_DIM))


def _line_attr(row: int, line: str) -> int:
    if row == 0 and not _is_border_line(line):
        return _attr(PAIR_VALUE, curses.A_BOLD)
    if "backend error" in line or "error=" in line or "ERROR:" in line:
        return _attr(PAIR_ERROR, curses.A_BOLD)
    if not line:
        return _attr(PAIR_VALUE)
    if _is_border_line(line):
        return _attr(PAIR_DIM)
    if _is_header_line(line):
        return _attr(PAIR_HEADER, curses.A_BOLD)
    return _attr(PAIR_VALUE)


def _draw_selected_row(
    screen,
    row: int,
    line: str,
    width: int,
    attr: int,
    *,
    preserve_box_border: bool,
) -> None:
    if preserve_box_border and width >= 2:
        _safe_addnstr(
            screen,
            row,
            1,
            cell_slice(line, 1, width - 2),
            width - 1,
            attr,
        )
        return
    _safe_addnstr(screen, row, 0, line, width, attr)


def _filtered_frame(
    frame: FrameSnapshot,
    options: Any | None,
    *,
    apply_context_filters: bool = True,
) -> FrameSnapshot:
    if options is None:
        return frame
    device_indices = getattr(options, "device_indices", None)
    if device_indices is None:
        device_indices = resolve_visible_device_indices(
            frame.devices,
            getattr(options, "visible_device_identifiers", None),
        )
    filtered = apply_filters(
        frame,
        device_indices=device_indices,
        users=getattr(options, "users", None),
        pids=getattr(options, "pids", None),
        process_types=getattr(options, "process_types", None),
        require_process_type=getattr(options, "require_process_type", False),
    )
    if not apply_context_filters:
        return filtered
    context_options = {
        "compute": getattr(options, "compute", False),
        "only_compute": getattr(options, "only_compute", False),
        "graphics": getattr(options, "graphics", False),
        "only_graphics": getattr(options, "only_graphics", False),
    }
    validate_process_contexts(
        filtered,
        requested=requested_process_contexts(**context_options),
        supported=getattr(options, "supported_process_contexts", None),
    )
    return apply_filters(
        filtered,
        compute=getattr(options, "compute", False),
        only_compute=getattr(options, "only_compute", False),
        graphics=getattr(options, "graphics", False),
        only_graphics=getattr(options, "only_graphics", False),
    )


def _filtered_frame_with_error(
    frame: FrameSnapshot,
    options: Any | None,
) -> tuple[FrameSnapshot, str | None]:
    try:
        return _filtered_frame(frame, options), None
    except RuntimeError as exc:
        return _filtered_frame(frame, options, apply_context_filters=False), str(exc)


def _move_selection(state: UiState, frame: FrameSnapshot, delta: int) -> None:
    processes = sort_processes(frame.processes, state.process_sort, state.reverse_sort)
    keep_selection(state, processes)
    if not processes:
        return
    residual = 0
    if state.selected_key is None:
        state.selected_index = 0 if delta > 0 else len(processes) - 1
    else:
        old_index = state.selected_index
        state.selected_index = max(0, min(old_index + delta, len(processes) - 1))
        residual = delta - (state.selected_index - old_index)
        if residual:
            state.main_screen_offset = max(0, state.main_screen_offset + residual)
    state.selected_key = processes[state.selected_index].selection_key
    if residual:
        state.selected_visible = False
        state.follow_selection = False
    else:
        state.selected_visible = True
        state.follow_selection = True


def _select_edge(state: UiState, frame: FrameSnapshot, *, last: bool) -> None:
    processes = sort_processes(frame.processes, state.process_sort, state.reverse_sort)
    keep_selection(state, processes)
    if not processes:
        return
    state.selected_index = len(processes) - 1 if last else 0
    state.selected_key = processes[state.selected_index].selection_key
    state.selected_visible = True
    state.follow_selection = True


def _command_column_offset(frame: FrameSnapshot | None) -> int:
    del frame
    # The process renderer clamps this sentinel to the longest visible row.
    return LARGE_SCROLL_OFFSET


def _selected_processes(state: UiState, frame: FrameSnapshot) -> list[ProcessSnapshot]:
    if state.tagged_pids:
        targets = [
            process for process in frame.processes if process.pid in state.tagged_pids
        ]
    elif state.selected_key is not None and state.selected_visible:
        targets = [
            process
            for process in frame.processes
            if process.selection_key == state.selected_key
        ]
        if targets and not _process_owned_by_current_user(targets[0]):
            targets = []
    else:
        targets = []
    unique: dict[int, ProcessSnapshot] = {}
    for process in targets:
        unique.setdefault(process.pid, process)
    return list(unique.values())


def _process_owned_by_current_user(process: ProcessSnapshot) -> bool:
    return _is_superuser() or process.user == getpass.getuser()


def _is_superuser() -> bool:
    try:
        if os.geteuid() == 0:
            return True
    except AttributeError:
        pass
    return False


def _expected_create_time(
    process: ProcessSnapshot, frame: FrameSnapshot
) -> float | None:
    create_time = getattr(process, "create_time", None)
    if create_time is not None:
        return float(create_time)
    if process.runtime_seconds is not None:
        return float(frame.timestamp) - float(process.runtime_seconds)
    return None


def _same_process_identity(expected: float | None, actual: float | None) -> bool:
    return (
        expected is not None
        and actual is not None
        and abs(actual - expected) <= PROCESS_CREATE_TIME_TOLERANCE
    )


def _same_process_on_host(
    left: ProcessSnapshot,
    right: ProcessSnapshot,
    frame: FrameSnapshot,
) -> bool:
    if left.pid != right.pid:
        return False
    left_create_time = _expected_create_time(left, frame)
    right_create_time = _expected_create_time(right, frame)
    if left_create_time is None or right_create_time is None:
        return True
    return abs(left_create_time - right_create_time) <= PROCESS_CREATE_TIME_TOLERANCE


def _prune_tags(state: UiState, identities: dict[int, float | None]) -> None:
    for pid in tuple(state.tagged_pids):
        expected = state.tagged_processes.get(pid, (None, None))[0]
        if not _same_process_identity(expected, identities.get(pid)):
            state.tagged_pids.discard(pid)
            state.tagged_processes.pop(pid, None)


def _tagged_action_targets(state: UiState) -> list[tuple[int, float | None]]:
    return [
        (pid, create_time)
        for pid in sorted(state.tagged_pids)
        for create_time, _user in [state.tagged_processes.get(pid, (None, None))]
        if create_time is not None
    ]


def _begin_signal(
    state: UiState,
    frame: FrameSnapshot | None,
    process_signal: ProcessSignal,
    *,
    readonly: bool,
) -> None:
    state.status_message = None
    if readonly or state.readonly:
        state.status_message = "Process actions are disabled by --readonly."
        return
    targets: list[tuple[int, float | None]] = []
    if state.active_screen == ScreenMode.TREE and state.tagged_pids:
        targets.extend(_tagged_action_targets(state))
    elif (
        state.active_screen == ScreenMode.TREE
        and state.screen_selection_active
        and state.screen_target_pid is not None
    ):
        try:
            is_superuser = os.geteuid() == 0
        except AttributeError:
            is_superuser = False
        if is_superuser or state.screen_target_user == getpass.getuser():
            targets.append((state.screen_target_pid, state.screen_target_create_time))
        else:
            state.status_message = "Selected process is owned by another user."
            return
    elif frame is not None:
        for process in _selected_processes(state, frame):
            expected = (
                state.tagged_processes.get(process.pid, (None, None))[0]
                if process.pid in state.tagged_pids
                else _expected_create_time(process, frame)
            )
            if expected is not None:
                targets.append((process.pid, expected))
        selected = find_process(frame, state.selected_key)
        if (
            not targets
            and selected is not None
            and not state.tagged_pids
            and not _process_owned_by_current_user(selected)
        ):
            state.status_message = "Selected process is owned by another user."
            return
    if not targets:
        state.status_message = "No process selected."
        return
    state.pending_signal = process_signal
    state.pending_signal_targets = tuple(dict.fromkeys(targets))
    state.pending_signal_option = _signal_option_index(process_signal)


def _cancel_signal(state: UiState) -> None:
    state.pending_signal = None
    state.pending_signal_targets = ()
    state.pending_signal_option = 0


def _execute_pending_signal(state: UiState) -> None:
    process_signal = state.pending_signal
    targets = state.pending_signal_targets
    if process_signal is None or not targets:
        _cancel_signal(state)
        return
    try:
        import psutil
    except ModuleNotFoundError:
        state.status_message = "psutil is required for process actions."
        _cancel_signal(state)
        return

    errors: list[str] = []
    sent_count = 0
    current_user = getpass.getuser()
    is_superuser = False
    try:
        is_superuser = os.geteuid() == 0
    except AttributeError:
        pass
    for pid, expected_create_time in targets:
        if expected_create_time is None:
            errors.append(f"PID {pid}: process creation time unavailable")
            continue
        try:
            process = psutil.Process(pid)
            actual_create_time = float(process.create_time())
            if (
                abs(actual_create_time - expected_create_time)
                > PROCESS_CREATE_TIME_TOLERANCE
            ):
                errors.append(f"PID {pid}: process identity changed")
                continue
            if not is_superuser and process.username() != current_user:
                errors.append(f"PID {pid}: process is owned by another user")
                continue
            if process_signal == ProcessSignal.TERMINATE:
                process.terminate()
            elif process_signal == ProcessSignal.KILL:
                process.kill()
            else:
                process.send_signal(signal.SIGINT)
            sent_count += 1
        except (psutil.Error, OSError) as exc:
            errors.append(f"PID {pid}: {exc}")
    if errors and sent_count:
        noun = "process" if sent_count == 1 else "processes"
        state.status_message = (
            f"Sent {process_signal.value} signal to {sent_count} {noun}; "
            + "; ".join(errors)
        )
    elif errors:
        state.status_message = "; ".join(errors)
    else:
        state.status_message = f"Sent {process_signal.value} signal."
    state.clear_selection()
    _cancel_signal(state)


def _handle_signal_dialog_key(key: int, state: UiState) -> bool:
    if key in {27, ord("q"), ord("Q"), ord("c"), ord("C"), ord("n"), ord("N")}:
        _cancel_signal(state)
        return True
    if key in {curses.KEY_LEFT, ord(","), ord("<"), ord("["), curses.KEY_BTAB}:
        state.pending_signal_option = (state.pending_signal_option - 1) % 4
        if state.pending_signal_option < len(SIGNAL_OPTIONS):
            state.pending_signal = SIGNAL_OPTIONS[state.pending_signal_option]
        return True
    if key in {curses.KEY_RIGHT, ord("."), ord(">"), ord("]"), ord("\t")}:
        state.pending_signal_option = (state.pending_signal_option + 1) % 4
        if state.pending_signal_option < len(SIGNAL_OPTIONS):
            state.pending_signal = SIGNAL_OPTIONS[state.pending_signal_option]
        return True
    if ord("1") <= key <= ord("4"):
        state.pending_signal_option = key - ord("1")
        if state.pending_signal_option == 3:
            _cancel_signal(state)
        else:
            state.pending_signal = SIGNAL_OPTIONS[state.pending_signal_option]
            _execute_pending_signal(state)
        return True
    direct = {
        ord("t"): ProcessSignal.TERMINATE,
        ord("T"): ProcessSignal.TERMINATE,
        ord("k"): ProcessSignal.KILL,
        ord("K"): ProcessSignal.KILL,
        ord("i"): ProcessSignal.INTERRUPT,
        ord("I"): ProcessSignal.INTERRUPT,
        3: ProcessSignal.INTERRUPT,
    }
    if key in direct:
        state.pending_signal = direct[key]
        state.pending_signal_option = _signal_option_index(state.pending_signal)
        _execute_pending_signal(state)
    elif key in {ord("\n"), curses.KEY_ENTER, ord(" "), ord("y"), ord("Y")}:
        if state.pending_signal_option == 3:
            _cancel_signal(state)
        else:
            _execute_pending_signal(state)
    return True


def _screen_select_move(state: UiState, delta: int) -> None:
    count = len(state.screen_selection_ids)
    if count <= 0:
        return
    state.screen_selection_active = True
    state.screen_selected_index = max(
        0, min(state.screen_selected_index + delta, count - 1)
    )


def _tag_selected(state: UiState, frame: FrameSnapshot) -> None:
    processes = sort_processes(frame.processes, state.process_sort, state.reverse_sort)
    keep_selection(state, processes)
    if state.selected_key is None or not processes:
        return
    process = processes[state.selected_index]
    if process.pid in state.tagged_pids:
        state.tagged_pids.remove(process.pid)
        state.tagged_processes.pop(process.pid, None)
    else:
        state.tagged_pids.add(process.pid)
        state.tagged_processes[process.pid] = (
            _expected_create_time(process, frame),
            process.user,
        )
    _move_selection(state, frame, 1)


def _tag_tree_selected(state: UiState) -> None:
    pid = state.screen_target_pid
    if pid is None:
        return
    if pid in state.tagged_pids:
        state.tagged_pids.remove(pid)
        state.tagged_processes.pop(pid, None)
    else:
        if state.screen_target_create_time is None:
            state.status_message = "Process creation time is unavailable."
            return
        state.tagged_pids.add(pid)
        state.tagged_processes[pid] = (
            state.screen_target_create_time,
            state.screen_target_user,
        )
    _screen_select_move(state, 1)


def _handle_mouse(
    state: UiState,
    frame: FrameSnapshot | None,
    *,
    mouse_rows: dict[int, int] | None,
    modal_buttons: dict[tuple[int, int], int] | None,
) -> None:
    try:
        _, mouse_x, mouse_y, _, button_state = curses.getmouse()
    except curses.error:
        return
    direction = _mouse_wheel_direction(button_state)
    if direction:
        if state.pending_signal is not None:
            state.pending_signal_option = (state.pending_signal_option + direction) % 4
            if state.pending_signal_option < len(SIGNAL_OPTIONS):
                state.pending_signal = SIGNAL_OPTIONS[state.pending_signal_option]
        elif button_state & getattr(curses, "BUTTON_SHIFT", 0):
            if state.active_screen == ScreenMode.MAIN:
                state.command_offset = max(0, state.command_offset + 2 * direction)
            else:
                state.screen_horizontal_offset = max(
                    0, state.screen_horizontal_offset + 2 * direction
                )
        elif state.active_screen == ScreenMode.MAIN and frame is not None:
            _move_selection(state, frame, direction)
        else:
            _screen_select_move(state, direction)
        return
    if state.pending_signal is not None and _mouse_clicked(button_state):
        option = (
            None if modal_buttons is None else modal_buttons.get((mouse_x, mouse_y))
        )
        if option is None:
            return
        state.pending_signal_option = option
        if option == 3:
            _cancel_signal(state)
        else:
            state.pending_signal = SIGNAL_OPTIONS[option]
            _execute_pending_signal(state)
        return
    if not _mouse_clicked(button_state):
        return
    if mouse_rows is None or mouse_y not in mouse_rows:
        if state.active_screen == ScreenMode.MAIN:
            state.clear_selection()
        elif state.active_screen == ScreenMode.TREE:
            state.tagged_pids.clear()
            state.tagged_processes.clear()
            _clear_screen_target(state)
        return
    index = mouse_rows[mouse_y]
    if state.active_screen == ScreenMode.MAIN and frame is not None:
        processes = sort_processes(
            frame.processes, state.process_sort, state.reverse_sort
        )
        if 0 <= index < len(processes):
            state.selected_index = index
            state.selected_key = processes[index].selection_key
            state.selected_visible = True
            state.follow_selection = True
    else:
        state.screen_selection_active = True
        state.screen_selected_index = max(0, index)


def _handle_key(
    key: int,
    state: UiState,
    frame: FrameSnapshot | None,
    sampler: SnapshotSampler,
    *,
    readonly: bool = False,
    mouse_rows: dict[int, int] | None = None,
    modal_buttons: dict[tuple[int, int], int] | None = None,
) -> bool:
    if key == -1:
        return True
    if key == curses.KEY_RESIZE:
        return True
    state.status_message = None
    if key == curses.KEY_MOUSE:
        _handle_mouse(state, frame, mouse_rows=mouse_rows, modal_buttons=modal_buttons)
        return True
    if state.pending_signal is not None:
        return _handle_signal_dialog_key(key, state)
    if state.active_screen == ScreenMode.HELP:
        state.return_to_previous_screen()
        if state.active_screen == ScreenMode.TREE:
            state.screen_selection_active = state.screen_target_pid is not None
        return True
    if state.pending_sort_key:
        state.pending_sort_key = False
        if 0 <= key <= 255:
            character = chr(key)
            sort = DIRECT_SORT_KEYS.get(character.lower())
            if sort is not None:
                state.process_sort = sort
                state.reverse_sort = character.isupper()
                state.follow_selection = True
        return True

    if key in {ord("h"), ord("?")}:
        state.switch_screen(ScreenMode.HELP)
        return True
    if key in {ord("r"), ord("R"), 18, getattr(curses, "KEY_F5", curses.KEY_F0 + 5)}:
        if state.active_screen == ScreenMode.MAIN:
            state.clear_selection()
            state.scroll_offset = 0
            state.main_screen_offset = 0
            state.command_offset = 0
        sampler.refresh_now()
        return True

    if state.active_screen == ScreenMode.ENVIRON:
        if key in {ord("e"), ord("q"), ord("Q"), 27}:
            state.return_to_previous_screen()
            if state.active_screen == ScreenMode.TREE:
                state.screen_selection_active = state.screen_target_pid is not None
        elif key in {
            curses.KEY_UP,
            curses.KEY_PPAGE,
            curses.KEY_BTAB,
            _alt_key("k"),
            ord("["),
        }:
            _screen_select_move(state, -1)
        elif key in {
            curses.KEY_DOWN,
            curses.KEY_NPAGE,
            ord("\t"),
            _alt_key("j"),
            ord("]"),
        }:
            _screen_select_move(state, 1)
        elif key == curses.KEY_HOME:
            state.screen_selection_active = True
            state.screen_selected_index = 0
        elif key == curses.KEY_END and state.screen_selection_ids:
            state.screen_selection_active = True
            state.screen_selected_index = len(state.screen_selection_ids) - 1
        elif key in {curses.KEY_LEFT, _alt_key("h")}:
            state.screen_horizontal_offset = max(0, state.screen_horizontal_offset - 5)
        elif key in {curses.KEY_RIGHT, _alt_key("l")}:
            state.screen_horizontal_offset += 5
        elif key in {1, ord("^")}:
            state.screen_horizontal_offset = 0
        return True

    if state.active_screen == ScreenMode.TREE:
        if key in {ord("t"), ord("q"), ord("Q")}:
            _return_from_tree(state, frame)
            return True
        if key == 27:
            state.tagged_pids.clear()
            state.tagged_processes.clear()
            _clear_screen_target(state)
            return True
        if key == ord(" "):
            if state.screen_selection_active:
                _tag_tree_selected(state)
            return True
        if key == ord("e"):
            if state.screen_target_pid is None:
                _remember_selected_target(state, frame, fallback_host=True)
            state.switch_screen(ScreenMode.ENVIRON)
            return True
    elif state.active_screen == ScreenMode.METRICS:
        if key in {ord("\n"), curses.KEY_ENTER, ord("q"), ord("Q"), 27}:
            state.return_to_main_screen()
            return True
        if key == ord("e"):
            state.switch_screen(ScreenMode.ENVIRON)
            return True

    if state.active_screen != ScreenMode.MAIN:
        if key in {
            curses.KEY_UP,
            curses.KEY_BTAB,
            _alt_key("k"),
            curses.KEY_PPAGE,
            ord("["),
        }:
            _screen_select_move(state, -1)
        elif key in {
            curses.KEY_DOWN,
            ord("\t"),
            _alt_key("j"),
            curses.KEY_NPAGE,
            ord("]"),
        }:
            _screen_select_move(state, 1)
        elif key == curses.KEY_HOME:
            state.screen_selection_active = True
            state.screen_selected_index = 0
        elif key == curses.KEY_END and state.screen_selection_ids:
            state.screen_selection_active = True
            state.screen_selected_index = len(state.screen_selection_ids) - 1
        elif key in {curses.KEY_LEFT, _alt_key("h")}:
            state.screen_horizontal_offset = max(0, state.screen_horizontal_offset - 5)
        elif key in {curses.KEY_RIGHT, _alt_key("l")}:
            state.screen_horizontal_offset += 5
        elif key in {1, ord("^")}:
            state.screen_horizontal_offset = 0
        elif key in {ord("T"), ord("K"), ord("k"), 3, ord("I")}:
            process_signal = (
                ProcessSignal.TERMINATE
                if key == ord("T")
                else ProcessSignal.KILL
                if key in {ord("K"), ord("k")}
                else ProcessSignal.INTERRUPT
            )
            _begin_signal(state, frame, process_signal, readonly=readonly)
        return True

    if key in {ord("q"), ord("Q")}:
        return False
    if key == 27:
        state.clear_selection()
    elif key == ord("e"):
        _remember_selected_target(state, frame, fallback_host=True)
        state.switch_screen(ScreenMode.ENVIRON)
    elif key == ord("t"):
        if frame is not None and frame.processes:
            _enter_tree(state, frame)
    elif key in {ord("\n"), curses.KEY_ENTER}:
        if state.selected_key is not None:
            _remember_selected_target(state, frame)
            state.switch_screen(ScreenMode.METRICS)
    elif key in {ord("a")}:
        state.layout = LayoutMode.AUTO
    elif key in {ord("f")}:
        state.layout = LayoutMode.FULL
    elif key in {ord("c")}:
        state.layout = LayoutMode.COMPACT
    elif key in {ord(","), ord("<")}:
        state.process_sort = next_sort(state.process_sort, -1)
        state.reverse_sort = False
        state.follow_selection = True
    elif key in {ord("."), ord(">")}:
        state.process_sort = next_sort(state.process_sort, 1)
        state.reverse_sort = False
        state.follow_selection = True
    elif key == ord("/"):
        state.reverse_sort = not state.reverse_sort
        state.follow_selection = True
    elif key == ord("o"):
        state.pending_sort_key = True
    elif key in {curses.KEY_UP, curses.KEY_BTAB, _alt_key("k")} and frame is not None:
        _move_selection(state, frame, -1)
    elif key in {curses.KEY_DOWN, ord("\t"), _alt_key("j")} and frame is not None:
        _move_selection(state, frame, 1)
    elif key == curses.KEY_HOME and frame is not None:
        _select_edge(state, frame, last=False)
    elif key == curses.KEY_END and frame is not None:
        _select_edge(state, frame, last=True)
    elif key == ord(" ") and frame is not None:
        _tag_selected(state, frame)
    elif key in {curses.KEY_PPAGE, ord("["), _alt_key("K")}:
        state.main_screen_offset = max(0, state.main_screen_offset - 1)
        state.selected_visible = False
        state.follow_selection = False
    elif key in {curses.KEY_NPAGE, ord("]"), _alt_key("J")}:
        state.main_screen_offset += 1
        state.selected_visible = False
        state.follow_selection = False
    elif key in {curses.KEY_LEFT, _alt_key("h")}:
        state.command_offset = max(0, state.command_offset - 2)
    elif key in {curses.KEY_RIGHT, _alt_key("l")}:
        state.command_offset += 2
    elif key in {1, ord("^")}:
        state.command_offset = 0
    elif key in {5, ord("$")}:
        state.command_offset = _command_column_offset(frame)
    elif key in {ord("T"), ord("K"), ord("k"), 3, ord("I")}:
        process_signal = (
            ProcessSignal.TERMINATE
            if key == ord("T")
            else ProcessSignal.KILL
            if key in {ord("K"), ord("k")}
            else ProcessSignal.INTERRUPT
        )
        _begin_signal(state, frame, process_signal, readonly=readonly)
    return True


def _remember_selected_target(
    state: UiState,
    frame: FrameSnapshot | None,
    *,
    fallback_host: bool = False,
) -> None:
    _clear_screen_target(state)
    process = find_process(frame, state.selected_key) if frame is not None else None
    if process is not None:
        state.screen_target_pid = process.pid
        state.screen_target_create_time = _expected_create_time(process, frame)
        state.screen_target_user = process.user
        state.screen_target_command = process.command or process.name
        return
    if not fallback_host:
        return
    state.screen_target_pid = os.getpid()
    state.screen_target_user = getpass.getuser()
    state.screen_target_command = " ".join(sys.argv) or "mxtop"
    try:
        import psutil

        host_process = psutil.Process(state.screen_target_pid)
        state.screen_target_create_time = float(host_process.create_time())
        state.screen_target_user = host_process.username()
        command = host_process.cmdline()
        if command:
            state.screen_target_command = " ".join(command)
    except ModuleNotFoundError:
        pass
    except (psutil.Error, OSError):
        pass


def _enter_tree(state: UiState, frame: FrameSnapshot) -> None:
    _remember_selected_target(state, frame)
    had_target = state.screen_target_pid is not None
    state.clear_selection()
    state.switch_screen(ScreenMode.TREE)
    state.screen_selection_active = had_target


def _clear_screen_target(state: UiState) -> None:
    state.screen_selection_active = False
    state.screen_target_pid = None
    state.screen_target_create_time = None
    state.screen_target_user = None
    state.screen_target_command = None


def _sync_tree_selection(state: UiState, entries: Sequence[ProcessTreeEntry]) -> None:
    """Keep a tree selection only while the same process identity exists."""

    if not state.screen_selection_active or state.screen_target_pid is None:
        return
    expected = state.screen_target_create_time
    if expected is None:
        _clear_screen_target(state)
        return
    selected_index = next(
        (
            index
            for index, entry in enumerate(entries)
            if entry.pid == state.screen_target_pid
            and (
                expected is None
                or (
                    entry.create_time is not None
                    and abs(entry.create_time - expected)
                    <= PROCESS_CREATE_TIME_TOLERANCE
                )
            )
        ),
        None,
    )
    if selected_index is None:
        _clear_screen_target(state)
        return
    state.screen_selected_index = selected_index


def _return_from_tree(state: UiState, frame: FrameSnapshot | None) -> None:
    target = (
        _screen_target_process(state, frame)
        if frame is not None and state.screen_selection_active
        else None
    )
    state.clear_selection()
    state.return_to_main_screen()
    if target is None or frame is None:
        return
    processes = sort_processes(frame.processes, state.process_sort, state.reverse_sort)
    state.selected_key = target.selection_key
    state.selected_index = next(
        (
            index
            for index, process in enumerate(processes)
            if process.selection_key == target.selection_key
        ),
        0,
    )
    state.follow_selection = True


def _screen_target_process(
    state: UiState, frame: FrameSnapshot
) -> ProcessSnapshot | None:
    if state.screen_target_pid is None:
        return find_process(frame, state.selected_key)
    expected = state.screen_target_create_time
    return next(
        (
            process
            for process in frame.processes
            if process.pid == state.screen_target_pid
            and expected is not None
            and (
                (actual := _expected_create_time(process, frame)) is not None
                and abs(actual - expected) <= PROCESS_CREATE_TIME_TOLERANCE
            )
        ),
        None,
    )


def _signal_name(process_signal: ProcessSignal) -> str:
    return {
        ProcessSignal.TERMINATE: "SIGTERM",
        ProcessSignal.KILL: "SIGKILL",
        ProcessSignal.INTERRUPT: "SIGINT",
    }[process_signal]


def _overlay_dialog(
    lines: list[str], dialog: list[str], width: int, height: int
) -> list[str]:
    canvas = [cell_ljust(cell_slice(line, 0, width), width) for line in lines[:height]]
    canvas.extend(" " * width for _ in range(max(0, height - len(canvas))))
    if not dialog or not canvas:
        return canvas
    start_y = max(0, (height - len(dialog)) // 2)
    for offset, dialog_line in enumerate(dialog):
        row = start_y + offset
        if row >= len(canvas):
            break
        snippet = cell_slice(dialog_line, 0, width)
        snippet_width = cell_width(snippet)
        start_x = max(0, (width - snippet_width) // 2)
        base = canvas[row]
        canvas[row] = cell_ljust(
            cell_slice(base, 0, start_x)
            + snippet
            + cell_slice(
                base, start_x + snippet_width, width - start_x - snippet_width
            ),
            width,
        )
    return canvas


def _signal_option_index(process_signal: ProcessSignal | None) -> int:
    return {
        ProcessSignal.TERMINATE: 0,
        ProcessSignal.KILL: 1,
        ProcessSignal.INTERRUPT: 2,
    }.get(process_signal, 0)


def _tree_snapshot_interval(interval: float) -> float:
    return min(max(interval / 3.0, 0.1), 1.0)


def _metrics_snapshot_interval(interval: float) -> float:
    return min(max(interval / 3.0, 0.01), 1.0)


def _host_snapshot_interval(interval: float) -> float:
    return min(max(interval / 3.0, 0.01), 0.5)


def _metrics_tracking(state: UiState) -> bool:
    return (
        state.active_screen == ScreenMode.METRICS
        or ScreenMode.METRICS in state.screen_history
    )


def _sample_metrics_if_due(
    history: ProcessMetricsHistory,
    frame: FrameSnapshot,
    process: ProcessSnapshot | None,
    *,
    now: float,
    sampled_at: float,
    interval: float,
) -> float:
    selection_key = None if process is None else process.selection_key
    if selection_key != history.selection_key or now - sampled_at >= interval - 1e-9:
        history.sample(frame, selection_key)
        return now
    return sampled_at


def _sample_host_history_if_due(
    history: HostHistory,
    frame: FrameSnapshot,
    state: UiState,
    *,
    now: float,
    sampled_at: float,
    interval: float,
) -> float:
    if now - sampled_at < interval - 1e-9:
        return sampled_at
    selected = find_process(frame, state.selected_key)
    if selected is None:
        selected = _screen_target_process(state, frame)
    selected_gpu_index = None if selected is None else selected.gpu_index
    render_host_panel(
        frame,
        MIN_SCREEN_WIDTH,
        compact=False,
        history=history,
        selected_gpu_index=selected_gpu_index,
    )
    return now


def _decode_alt_key(screen, key: int) -> int:
    if key != 27:
        return key
    following = screen.getch()
    if following == -1:
        return key
    if 0 <= following <= 255:
        return ALT_KEY_BASE + following
    return following


def run_tui(
    backend: TelemetryBackend, interval: float, options: Any | None = None
) -> int:
    readonly = bool(getattr(options, "readonly", False))
    no_unicode = bool(getattr(options, "no_unicode", False))
    state = UiState(
        layout=getattr(options, "layout", LayoutMode.AUTO),
        readonly=readonly,
        no_unicode=no_unicode,
    )
    sampler = SnapshotSampler(backend, interval)
    host_history = HostHistory()
    metrics_history = ProcessMetricsHistory()
    sampler.start()

    def _main(screen) -> None:
        try:
            curses.curs_set(0)
        except curses.error:
            pass
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
        mouse_rows: dict[int, int] = {}
        modal_buttons: dict[tuple[int, int], int] = {}
        tree_entries: list[ProcessTreeEntry] = []
        tree_version = -1
        tree_refreshed_at = float("-inf")
        metrics_sampled_at = float("-inf")
        metrics_was_tracking = False
        host_history_sampled_at = float("-inf")
        environment_key: tuple[int, float | None] | None = None
        environment_variables: list[tuple[str, str]] = []
        environment_error: str | None = None
        while True:
            sampler_state = sampler.snapshot()
            filter_error: str | None = None
            if sampler_state.frame is None:
                frame = None
            else:
                frame, filter_error = _filtered_frame_with_error(
                    sampler_state.frame, options
                )
            raw_key = screen.getch()
            key = _decode_alt_key(screen, raw_key)
            refresh_environment = state.active_screen == ScreenMode.ENVIRON and key in {
                ord("r"),
                ord("R"),
                18,
                getattr(curses, "KEY_F5", curses.KEY_F0 + 5),
            }
            if not _handle_key(
                key,
                state,
                frame,
                sampler,
                readonly=readonly,
                mouse_rows=mouse_rows,
                modal_buttons=modal_buttons,
            ):
                break
            size = screen.getmaxyx()
            now = time.monotonic()
            metrics_tracking = _metrics_tracking(state)
            if metrics_was_tracking and not metrics_tracking:
                metrics_history.reset()
                metrics_sampled_at = float("-inf")
            metrics_was_tracking = metrics_tracking
            if state.active_screen == ScreenMode.TREE:
                heartbeat = _tree_snapshot_interval(interval)
            elif metrics_tracking:
                heartbeat = _metrics_snapshot_interval(interval)
            else:
                heartbeat = 1.0
            if state.active_screen != ScreenMode.MAIN:
                heartbeat = min(heartbeat, _host_snapshot_interval(interval))
            if (
                raw_key == -1
                and sampler_state.version == painted_version
                and size == painted_size
                and now - painted_at < heartbeat
            ):
                continue
            painted_version = sampler_state.version
            painted_size = size
            painted_at = now

            screen.erase()
            height, width = size
            draw_screen = screen
            draw_height = height
            draw_width = width

            if draw_width < MIN_TUI_WIDTH:
                _draw_small_terminal(
                    draw_screen,
                    draw_width,
                    draw_height,
                    no_unicode=no_unicode,
                )
                screen.refresh()
                continue

            if frame is None and state.active_screen != ScreenMode.HELP:
                error = sampler_state.error or "loading telemetry"
                _safe_addnstr(
                    draw_screen,
                    0,
                    0,
                    f"MXTOP  {error}",
                    draw_width,
                    _attr(PAIR_TITLE, curses.A_BOLD),
                )
                _safe_addnstr(
                    draw_screen,
                    1,
                    0,
                    "q: quit  r: refresh",
                    draw_width,
                    _attr(PAIR_DIM),
                )
                screen.refresh()
                continue

            render_width = max(MIN_SCREEN_WIDTH, draw_width)
            if frame is not None and metrics_tracking:
                metrics_process = _screen_target_process(state, frame)
                metrics_sampled_at = _sample_metrics_if_due(
                    metrics_history,
                    frame,
                    metrics_process,
                    now=now,
                    sampled_at=metrics_sampled_at,
                    interval=_metrics_snapshot_interval(interval),
                )
            if frame is not None and state.active_screen != ScreenMode.MAIN:
                host_history_sampled_at = _sample_host_history_if_due(
                    host_history,
                    frame,
                    state,
                    now=now,
                    sampled_at=host_history_sampled_at,
                    interval=_host_snapshot_interval(interval),
                )
            semantic_lines: list[str] | None = None
            semantic_offset = 0
            if state.active_screen == ScreenMode.HELP:
                view = render_help_screen(
                    render_width,
                    draw_height,
                    readonly=readonly,
                    offset=state.screen_scroll_offset,
                )
            elif frame is None:
                view = RenderedView([])
            elif state.active_screen == ScreenMode.MAIN:
                _prune_tags(
                    state,
                    {
                        process.pid: _expected_create_time(process, frame)
                        for process in frame.processes
                    },
                )
                rendered = render_main_screen(
                    frame,
                    state,
                    width=render_width,
                    height=draw_height,
                    interval=interval,
                    error=sampler_state.error or filter_error,
                    history=host_history,
                )
                semantic_lines = rendered.context_lines
                semantic_offset = rendered.context_offset
                ordered = sort_processes(
                    frame.processes, state.process_sort, state.reverse_sort
                )
                view = RenderedView(
                    rendered.lines,
                    selectable_start=rendered.process_start,
                    selectable_count=rendered.process_count,
                    selection_ids=rendered.process_keys,
                )
            elif state.active_screen == ScreenMode.ENVIRON:
                process = _screen_target_process(state, frame)
                if process is None and state.screen_target_pid is not None:
                    process = ProcessSnapshot(
                        gpu_index=-1,
                        pid=state.screen_target_pid,
                        name=state.screen_target_command
                        or str(state.screen_target_pid),
                        user=state.screen_target_user,
                        command=state.screen_target_command,
                        create_time=state.screen_target_create_time,
                    )
                current_environment_key = (
                    (process.pid, _expected_create_time(process, frame))
                    if process is not None
                    else None
                )
                if refresh_environment or current_environment_key != environment_key:
                    environment_key = current_environment_key
                    environment_variables = []
                    environment_error = None
                    if process is not None:
                        try:
                            environment_variables = read_process_environment(
                                process.pid,
                                expected_create_time=_expected_create_time(
                                    process, frame
                                ),
                            )
                        except OSError as exc:
                            environment_error = str(exc)
                row_count = (
                    len(environment_variables) if environment_error is None else 0
                )
                state.screen_selection_ids = tuple(
                    f"env:{index}" for index in range(row_count)
                )
                state.screen_selection_active = row_count > 0
                state.screen_selected_index = max(
                    0, min(state.screen_selected_index, row_count - 1)
                )
                view = render_environment_screen(
                    process,
                    environment_variables,
                    width=render_width,
                    height=draw_height,
                    selected_index=state.screen_selected_index,
                    scroll_offset=state.screen_scroll_offset,
                    horizontal_offset=state.screen_horizontal_offset,
                    error=environment_error,
                    no_unicode=no_unicode,
                )
            elif state.active_screen == ScreenMode.TREE:
                if (
                    sampler_state.version != tree_version
                    or now - tree_refreshed_at >= _tree_snapshot_interval(interval)
                ):
                    tree_entries = build_process_tree(frame)
                    tree_version = sampler_state.version
                    tree_refreshed_at = now
                    state.screen_selection_ids = tuple(
                        entry.selection_id for entry in tree_entries
                    )
                    _prune_tags(
                        state,
                        {entry.pid: entry.create_time for entry in tree_entries},
                    )
                    _sync_tree_selection(state, tree_entries)
                if tree_entries and state.screen_selection_active:
                    state.screen_selected_index = max(
                        0, min(state.screen_selected_index, len(tree_entries) - 1)
                    )
                    selected_entry = tree_entries[state.screen_selected_index]
                    state.screen_target_pid = selected_entry.pid
                    state.screen_target_create_time = selected_entry.create_time
                    state.screen_target_user = selected_entry.user
                    state.screen_target_command = selected_entry.command
                view = render_tree_screen(
                    tree_entries,
                    width=render_width,
                    height=draw_height,
                    selected_index=state.screen_selected_index,
                    scroll_offset=state.screen_scroll_offset,
                    horizontal_offset=state.screen_horizontal_offset,
                    readonly=readonly,
                    actionable=(
                        bool(_tagged_action_targets(state))
                        or (
                            state.screen_selection_active
                            and state.screen_target_pid is not None
                            and (
                                _is_superuser()
                                or state.screen_target_user == getpass.getuser()
                            )
                        )
                    ),
                )
            else:
                process = _screen_target_process(state, frame)
                view = render_metrics_screen(
                    frame,
                    process,
                    metrics_history,
                    width=render_width,
                    height=draw_height,
                )

            original_lines = list(view.lines)
            display_status = state.status_message
            if display_status is None and state.active_screen != ScreenMode.MAIN:
                display_status = filter_error or sampler_state.error
            if display_status and state.pending_signal is None and original_lines:
                status = f" {display_status} "[:render_width]
                original_lines[-1] = status.ljust(render_width)
            display_lines = list(original_lines)
            modal_buttons = {}
            modal_dialog: list[str] | None = None
            selected_button: tuple[int, int, int] | None = None
            dialog_x = dialog_y = 0
            if state.pending_signal is not None:
                target_users = {
                    process.pid: process.user for process in frame.processes
                }
                target_users.update(
                    {pid: user for pid, (_, user) in state.tagged_processes.items()}
                )
                if state.screen_target_pid is not None:
                    target_users[state.screen_target_pid] = state.screen_target_user
                signal_targets = [
                    (pid, target_users.get(pid))
                    for pid, _ in state.pending_signal_targets
                ]
                modal_dialog = render_signal_dialog(
                    signal_targets,
                    width=render_width,
                    signal_name=_signal_name(state.pending_signal),
                    current_option=state.pending_signal_option,
                )
                dialog_y = max(0, (draw_height - len(modal_dialog)) // 2)
                dialog_x = max(0, (render_width - cell_width(modal_dialog[0])) // 2)
                modal_buttons = _signal_modal_buttons(modal_dialog, dialog_x, dialog_y)
                selected_button = _signal_button_geometry(
                    modal_dialog,
                    state.pending_signal_option,
                )
                display_lines = _overlay_dialog(
                    display_lines,
                    modal_dialog,
                    render_width,
                    draw_height,
                )

            base_display_lines = list(original_lines)
            if no_unicode:
                display_lines = [render_ascii(line) for line in display_lines]
                base_display_lines = [render_ascii(line) for line in base_display_lines]
                if modal_dialog is not None:
                    modal_dialog = [render_ascii(line) for line in modal_dialog]
            base_display_lines.extend(
                " " * render_width
                for _ in range(max(0, draw_height - len(base_display_lines)))
            )
            use_semantic_source = (
                semantic_lines is not None
                and state.status_message is None
                and state.pending_signal is None
            )
            context_lines = semantic_lines if use_semantic_source else original_lines
            context_offset = semantic_offset if use_semantic_source else 0

            def visible_context(values):
                return {
                    row - context_offset: value
                    for row, value in values.items()
                    if context_offset <= row < context_offset + draw_height
                }

            host_context = visible_context(host_graph_context(context_lines))
            device_context = (
                visible_context(_rendering.device_row_levels(context_lines, frame))
                if state.active_screen == ScreenMode.MAIN and frame is not None
                else {}
            )
            device_indices = (
                visible_context(_rendering.device_row_indices(context_lines, frame))
                if state.active_screen == ScreenMode.MAIN and frame is not None
                else {}
            )
            dense_device_context = (
                visible_context(
                    _rendering.dense_device_row_context(context_lines, frame)
                )
                if state.active_screen == ScreenMode.MAIN and frame is not None
                else {}
            )
            selected_process = (
                None if frame is None else find_process(frame, state.selected_key)
            )
            selected_gpu_index = (
                None if selected_process is None else selected_process.gpu_index
            )
            process_context = (
                visible_context(_rendering.process_row_levels(context_lines, frame))
                if frame is not None
                else {}
            )
            metrics_graph_context = (
                _metrics_graph_context(original_lines)
                if state.active_screen == ScreenMode.METRICS
                else {}
            )
            mouse_rows = {}
            selected_rows: set[int] = set()
            tagged_selected_rows: set[int] = set()
            tagged_rows: set[int] = set()
            main_tagged_rows: set[int] = set()
            linked_process_rows: set[int] = set()
            tree_row_users: dict[int, str] = {}
            row_origin = 0
            if state.active_screen == ScreenMode.MAIN and frame is not None:
                ordered = sort_processes(
                    frame.processes, state.process_sort, state.reverse_sort
                )
                process_indexes = {
                    process.selection_key: index
                    for index, process in enumerate(ordered)
                }
                visible_keys = iter(view.selection_ids)
                for row, line in enumerate(original_lines[:draw_height]):
                    if _is_process_data_line(line):
                        selection_key = next(visible_keys, None)
                        if selection_key is None:
                            continue
                        mouse_rows[row + row_origin] = process_indexes.get(
                            selection_key, 0
                        )
                        process = ordered[process_indexes[selection_key]]
                        if process.pid in state.tagged_pids:
                            main_tagged_rows.add(row)
                        if (
                            selected_process is not None
                            and process.selection_key != selected_process.selection_key
                            and _same_process_on_host(process, selected_process, frame)
                        ):
                            linked_process_rows.add(row)
                        if selection_key == state.selected_key:
                            selected_rows.add(row)
                            if process.pid in state.tagged_pids:
                                tagged_selected_rows.add(row)
            elif view.selectable_count:
                for visible_index, selection_id in enumerate(view.selection_ids):
                    row = view.selectable_start + visible_index
                    try:
                        absolute_index = state.screen_selection_ids.index(selection_id)
                    except ValueError:
                        absolute_index = state.screen_scroll_offset + visible_index
                    mouse_rows[row + row_origin] = absolute_index
                    if (
                        state.active_screen == ScreenMode.TREE
                        and 0 <= absolute_index < len(tree_entries)
                    ):
                        tree_row_users[row] = tree_entries[absolute_index].user
                        if tree_entries[absolute_index].pid in state.tagged_pids:
                            tagged_rows.add(row)
                    if (
                        state.screen_selection_active
                        and absolute_index == state.screen_selected_index
                    ):
                        selected_rows.add(row)
                        if (
                            state.active_screen == ScreenMode.TREE
                            and row in tagged_rows
                        ):
                            tagged_selected_rows.add(row)

            for row, line in enumerate(display_lines[:draw_height]):
                modal_row = row - dialog_y
                if modal_dialog is not None:
                    dialog_line = (
                        modal_dialog[modal_row]
                        if 0 <= modal_row < len(modal_dialog)
                        else None
                    )
                    _draw_signal_dialog_line(
                        draw_screen,
                        row,
                        base_display_lines[row],
                        dialog_line,
                        dialog_x,
                        draw_width,
                        state.pending_signal_option,
                        dialog_row=modal_row,
                        selected_button=selected_button,
                    )
                elif state.active_screen == ScreenMode.HELP:
                    _draw_help_line(
                        draw_screen,
                        row,
                        line,
                        draw_width,
                        readonly=readonly,
                    )
                elif state.active_screen == ScreenMode.ENVIRON:
                    _draw_environment_line(draw_screen, row, line, draw_width)
                elif state.active_screen == ScreenMode.TREE:
                    _draw_tree_line(
                        draw_screen,
                        row,
                        line,
                        draw_width,
                        user=tree_row_users.get(row),
                        semantic_line=original_lines[row],
                    )
                elif state.active_screen == ScreenMode.METRICS:
                    _draw_metrics_line(
                        draw_screen,
                        row,
                        line,
                        draw_width,
                        metrics_graph_context.get(row),
                        process_context.get(row),
                        semantic_line=original_lines[row],
                    )
                else:
                    _draw_line(
                        draw_screen,
                        row,
                        line,
                        draw_width,
                        host_context.get(row),
                        device_level=device_context.get(row),
                        device_dim=(
                            selected_gpu_index is not None
                            and device_indices.get(row, selected_gpu_index)
                            != selected_gpu_index
                        ),
                        dense_device_context=dense_device_context.get(row),
                        selected_gpu_index=selected_gpu_index,
                        process_context=process_context.get(row),
                        process_tagged=(row in main_tagged_rows),
                        process_linked=(row in linked_process_rows),
                        semantic_line=original_lines[row],
                    )
                if (
                    modal_dialog is None
                    and row in tagged_rows
                    and row not in selected_rows
                ):
                    _safe_addnstr(
                        draw_screen,
                        row,
                        0,
                        line,
                        draw_width,
                        _tree_tagged_attr(tree_row_users.get(row)),
                    )
                if modal_dialog is None and row in selected_rows:
                    pair = (
                        PAIR_WARN
                        if row in tagged_selected_rows
                        else (
                            PAIR_TREE_SELECTED
                            if state.active_screen == ScreenMode.TREE
                            else PAIR_SELECTED
                        )
                    )
                    _draw_selected_row(
                        draw_screen,
                        row,
                        line,
                        draw_width,
                        _attr(pair, curses.A_BOLD | curses.A_REVERSE),
                        preserve_box_border=state.active_screen == ScreenMode.MAIN,
                    )
            screen.refresh()

    try:
        screen = curses.initscr()
        try:
            curses.noecho()
            curses.cbreak()
            screen.keypad(True)
            previous_sigint = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT, lambda *_: curses.ungetch(3))
            sys.stdout.write(HIDE_CURSOR)
            sys.stdout.flush()
            _main(screen)
        finally:
            sampler.stop()
            if "previous_sigint" in locals():
                signal.signal(signal.SIGINT, previous_sigint)
            try:
                screen.keypad(False)
                curses.nocbreak()
                curses.echo()
                curses.endwin()
            except curses.error:
                pass
            sys.stdout.write(SHOW_CURSOR)
            sys.stdout.flush()
    except KeyboardInterrupt:
        sampler.stop()
        return 130
    except curses.error as exc:
        sampler.stop()
        print(f"MXTOP ERROR: failed to initialize curses ({exc})", file=sys.stderr)
        return 1
    return 0
