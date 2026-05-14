from __future__ import annotations

from mxtop.models import FrameSnapshot
from mxtop.ui.panels import render_main_screen
from mxtop.ui.state import UiState

WIDE_MIN_WIDTH = 110
RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
REVERSE = "\x1b[7m"
FG_CYAN = "\x1b[36m"
FG_GREEN = "\x1b[32m"
FG_YELLOW = "\x1b[33m"
FG_RED = "\x1b[31m"
FG_MAGENTA = "\x1b[35m"
FG_BLUE = "\x1b[34m"
FG_WHITE = "\x1b[37m"
BORDER_CHARS = {"╒", "╕", "╘", "╛", "╞", "╡", "╪", "╧", "├", "┤", "┼", "─", "═", "│"}


def render_once(frame: FrameSnapshot, use_color: bool = True, width: int = 120) -> str:
    rendered = render_main_screen(frame, UiState(), width=width)
    if not use_color:
        return "\n".join(rendered.lines)
    return "\n".join(_colorize_line(row, line) for row, line in enumerate(rendered.lines))


def _style(text: str, *codes: str) -> str:
    if not text:
        return text
    return "".join(codes) + text + RESET


def _colorize_line(row: int, line: str) -> str:
    if not line:
        return line
    if row == 0:
        return _colorize_title(line)
    if "backend error" in line or "error=" in line:
        return _style(line, BOLD, FG_RED)
    if _is_process_title(line):
        return _colorize_process_title(line)
    if _is_device_data_line(line):
        return _style(line, BOLD, FG_GREEN)
    if _is_process_data_line(line):
        return _colorize_process_row(line)
    if _is_border_line(line):
        return _style(line, DIM, FG_WHITE)
    if _is_header_line(line):
        return _style(line, BOLD, FG_CYAN)
    if _is_host_line(line):
        return _colorize_host_line(line)
    if _is_graph_line(line):
        return _style(line, DIM, FG_WHITE)
    if "MXTOP" in line and "Driver Version" in line:
        return _style(line, BOLD, FG_WHITE)
    return _style(line, FG_WHITE)


def _colorize_title(line: str) -> str:
    hint_start = line.find("(Press ")
    if hint_start < 0:
        return _style(line, BOLD, FG_WHITE)
    output = [_style(line[:hint_start], BOLD, FG_WHITE)]
    hint = line[hint_start:]
    for token in ("h", "q"):
        prefix, found, rest = hint.partition(token)
        output.append(_style(prefix, BOLD, FG_WHITE))
        if not found:
            return "".join(output)
        output.append(_style(found, BOLD, FG_MAGENTA))
        hint = rest
    output.append(_style(hint, BOLD, FG_WHITE))
    return "".join(output)


def _colorize_process_title(line: str) -> str:
    at = line.rfind("@")
    if at <= 0:
        return _style(line, BOLD, FG_CYAN)
    start = line.rfind(" ", 0, at)
    start = 0 if start < 0 else start + 1
    end = line.find("│", at)
    end = len(line) if end < 0 else end
    return "".join(
        [
            _style(line[:start], BOLD, FG_CYAN),
            _style(line[start:at], BOLD, FG_MAGENTA),
            _style("@", BOLD, FG_CYAN),
            _style(line[at + 1 : end], BOLD, FG_GREEN),
            _style(line[end:], BOLD, FG_CYAN),
        ]
    )


def _colorize_process_row(line: str) -> str:
    if line.startswith("│>"):
        return _style(line, BOLD, REVERSE, FG_WHITE)
    if " root " in line:
        return _style(line, DIM, FG_WHITE)
    if len(line) < 5:
        return _style(line, FG_WHITE)
    return "".join([_style(line[:2], FG_WHITE), _style(line[2:5], BOLD, FG_GREEN), _style(line[5:], FG_WHITE)])


def _colorize_host_line(line: str) -> str:
    if " SWP:" in line:
        return _style(line, FG_BLUE)
    if " MEM:" in line:
        return _style(line, FG_MAGENTA)
    if " CPU:" in line:
        return _style(line, FG_CYAN)
    if "GPU MEM:" in line or "GPU UTL:" in line:
        return _style(line, FG_GREEN)
    return _style(line, FG_WHITE)


def _is_border_line(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and set(stripped) <= BORDER_CHARS


def _is_header_line(line: str) -> bool:
    return "GPU      PID" in line or "GPU  Name" in line or "Fan  Temp" in line or "Processes:" in line


def _is_device_data_line(line: str) -> bool:
    return line.startswith("│") and ("MEM: │" in line or "UTL: │" in line)


def _is_process_data_line(line: str) -> bool:
    return line.startswith("│") and "MiB" in line and (" days " in line or " /" in line or ":" in line)


def _is_process_title(line: str) -> bool:
    return "Processes:" in line and "@" in line


def _is_host_line(line: str) -> bool:
    return any(label in line for label in ("Load Average:", "CPU:", "MEM:", "SWP:", "GPU MEM:", "GPU UTL:"))


def _is_graph_line(line: str) -> bool:
    return "..." in line or "120s" in line or "60s" in line or "30s" in line
