from __future__ import annotations

import curses
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
MIN_TUI_WIDTH = 72
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
    curses.init_pair(PAIR_MEM, curses.COLOR_BLUE, -1)
    curses.init_pair(PAIR_ERROR, curses.COLOR_WHITE, curses.COLOR_RED)
    curses.init_pair(PAIR_SELECTED, curses.COLOR_BLACK, curses.COLOR_WHITE)


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


def _load_pair_from_line(line: str) -> int:
    for part in line.split():
        if part.endswith("%"):
            try:
                value = float(part.rstrip("%"))
            except ValueError:
                continue
            if value >= 85:
                return PAIR_HOT
            if value >= 60:
                return PAIR_WARN
            return PAIR_GOOD
    return PAIR_VALUE


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
    if "[" not in line or line.startswith(">"):
        _safe_addnstr(screen, row, 0, line, width, attr)
        return

    position = 0
    for segment_index, segment in enumerate(line.split("[")):
        if segment_index == 0:
            position = _safe_addnstr(screen, row, position, segment, width, attr)
            continue
        bar, _, rest = segment.partition("]")
        pair = PAIR_MEM if segment_index >= 2 else _load_pair_from_line(line)
        position = _safe_addnstr(screen, row, position, "[", width, _attr(PAIR_DIM))
        position = _safe_addnstr(screen, row, position, bar, width, _attr(pair, curses.A_BOLD))
        position = _safe_addnstr(screen, row, position, "]", width, _attr(PAIR_DIM))
        position = _safe_addnstr(screen, row, position, rest, width, attr)


def _line_attr(row: int, line: str) -> int:
    if row == 0:
        return _attr(PAIR_TITLE, curses.A_BOLD)
    if line.startswith(">"):
        return _attr(PAIR_SELECTED, curses.A_BOLD)
    if not line:
        return _attr(PAIR_VALUE)
    if set(line.strip()) <= {"-", " "}:
        return _attr(PAIR_DIM)
    if line in {"Devices", "Host"} or line.startswith("Processes"):
        return _attr(PAIR_HEADER, curses.A_BOLD)
    if line.startswith(("GPU  NAME", "GPU  PID", "GPU  NAME", "---")):
        return _attr(PAIR_HEADER, curses.A_BOLD)
    if "backend error" in line or "error=" in line:
        return _attr(PAIR_ERROR, curses.A_BOLD)
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
