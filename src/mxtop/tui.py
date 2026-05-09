from __future__ import annotations

import curses
import time

from mxtop.backends import TelemetryBackend
from mxtop.rendering import render_once

PAIR_TITLE = 1
PAIR_HEADER = 2
PAIR_DIM = 3
PAIR_VALUE = 4
PAIR_GOOD = 5
PAIR_WARN = 6
PAIR_HOT = 7
PAIR_MEM = 8
PAIR_ERROR = 9
MIN_TUI_WIDTH = 72


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


def _draw_line(screen, row: int, line: str, width: int) -> None:
    if row == 0:
        _safe_addnstr(screen, row, 0, " " + line + " ", width, _attr(PAIR_TITLE, curses.A_BOLD))
        return
    if not line:
        return
    if set(line.strip()) <= {"-", " "}:
        _safe_addnstr(screen, row, 0, line, width, _attr(PAIR_DIM))
        return
    if line.startswith(("GPU  NAME", "GPU  PID")):
        _safe_addnstr(screen, row, 0, line, width, _attr(PAIR_HEADER, curses.A_BOLD))
        return
    if "backend error" in line:
        _safe_addnstr(screen, row, 0, line, width, _attr(PAIR_ERROR, curses.A_BOLD))
        return

    if "[" not in line:
        _safe_addnstr(screen, row, 0, line, width, _attr(PAIR_VALUE))
        return

    position = 0
    for segment_index, segment in enumerate(line.split("[")):
        if segment_index == 0:
            position = _safe_addnstr(screen, row, position, segment, width, _attr(PAIR_VALUE))
            continue
        bar, _, rest = segment.partition("]")
        pair = PAIR_MEM if segment_index >= 2 else _load_pair_from_line(line)
        position = _safe_addnstr(screen, row, position, "[", width, _attr(PAIR_DIM))
        position = _safe_addnstr(screen, row, position, bar, width, _attr(pair, curses.A_BOLD))
        position = _safe_addnstr(screen, row, position, "]", width, _attr(PAIR_DIM))
        position = _safe_addnstr(screen, row, position, rest, width, _attr(PAIR_VALUE))


def run_tui(backend: TelemetryBackend, interval: float) -> int:
    def _main(screen) -> None:
        curses.curs_set(0)
        _setup_colors()
        screen.nodelay(True)
        screen.timeout(100)
        last_update = 0.0
        rendered = ""
        error = ""
        while True:
            key = screen.getch()
            if key in {ord("q"), ord("Q"), 27, 3}:
                break

            now = time.monotonic()
            if now - last_update >= interval:
                try:
                    frame = backend.snapshot()
                    rendered = render_once(frame, use_color=False, width=max(80, curses.COLS))
                    error = ""
                except Exception as exc:
                    error = f"backend error: {exc}"
                last_update = now

            screen.erase()
            height, width = screen.getmaxyx()
            if width < MIN_TUI_WIDTH:
                message = f"mxtop needs at least a width of {MIN_TUI_WIDTH} to render, the current width is {width}."
                _safe_addnstr(screen, 0, 0, message, width, _attr(PAIR_ERROR, curses.A_BOLD))
                _safe_addnstr(screen, 1, 0, "Widen the terminal or press q to quit.", width, _attr(PAIR_DIM))
            else:
                for row, line in enumerate(rendered.splitlines()[: max(0, height - 2)]):
                    _draw_line(screen, row, line, width)
                footer = "q: quit  refresh: %.1fs" % interval
                if error:
                    footer = error + "  " + footer
                _safe_addnstr(screen, height - 1, 0, footer, width, _attr(PAIR_DIM))
            screen.refresh()

    try:
        curses.wrapper(_main)
    except KeyboardInterrupt:
        return 130
    return 0
