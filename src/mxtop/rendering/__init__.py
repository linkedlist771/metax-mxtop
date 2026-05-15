from __future__ import annotations

import re

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
BORDER_CHARS = {"╒", "╕", "╘", "╛", "╞", "╡", "╪", "╧", "├", "┤", "┼", "─", "═", "│", "┬", "┴", "╤"}
_DEVICE_ROW_RE = re.compile(r"^│\s*\d+\s+\S")
_PROCESS_ROW_RE = re.compile(r"^│[ >]\s*\d+\s+\d+\s")


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
    if _is_process_data_line(line):
        return _colorize_process_row(line)
    if _is_device_data_line(line):
        return _colorize_device_row(line)
    if _is_border_line(line):
        return _style(line, DIM, FG_WHITE)
    if "MXTOP" in line and "Driver Version" in line:
        return _style(line, BOLD, FG_WHITE)
    if _is_header_line(line):
        return _style(line, BOLD, FG_CYAN)
    if _is_host_line(line):
        return _colorize_host_line(line)
    if _is_graph_line(line):
        return _style(line, DIM, FG_WHITE)
    return _style(line, FG_WHITE)


_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)%")
_BAR_RE = re.compile(r"(MEM|MBW|UTL|PWR): ([█░]+) (\S+)")
_HOST_BAR_RE = re.compile(r"(  )([█░]{4,})")
_GPU_METRIC_RE = re.compile(r"GPU (MEM|UTL):\s*(\S+)")

MEM_THRESHOLDS = (10, 80)
GPU_THRESHOLDS = (10, 75)
_INTENSITY_RANK = {FG_GREEN: 0, FG_YELLOW: 1, FG_RED: 2}


def _parse_percent(text: str) -> float | None:
    try:
        return float(text.replace("%", ""))
    except ValueError:
        return None


def _intensity_color(value: float | None, *, memory: bool) -> str:
    if value is None:
        return FG_YELLOW
    thresholds = MEM_THRESHOLDS if memory else GPU_THRESHOLDS
    if value >= thresholds[1]:
        return FG_RED
    if value >= thresholds[0]:
        return FG_YELLOW
    return FG_GREEN


def _bar_color(label: str, pct_text: str) -> str:
    return _intensity_color(_parse_percent(pct_text), memory=label in {"MEM", "MBW"})


def _max_color(*colors: str) -> str:
    return max(colors, key=lambda c: _INTENSITY_RANK.get(c, 0))


def _device_body_color(line: str) -> str:
    matches = list(_BAR_RE.finditer(line))
    if matches:
        best = FG_GREEN
        for match in matches:
            label = match.group(1)
            if label in {"MEM", "UTL"}:
                best = _max_color(best, _bar_color(label, match.group(3)))
        return best
    best = FG_GREEN
    for token in _PERCENT_RE.findall(line):
        value = _parse_percent(token)
        if value is None:
            continue
        best = _max_color(best, _intensity_color(value, memory=False))
    return best


def _colorize_device_row(line: str) -> str:
    body_color = _device_body_color(line)
    out: list[str] = []
    cursor = 0
    for match in _BAR_RE.finditer(line):
        label = match.group(1)
        bar = match.group(2)
        pct_text = match.group(3)
        bar_color = _bar_color(label, pct_text)
        if match.start() > cursor:
            out.append(_style(line[cursor : match.start()], BOLD, body_color))
        out.append(_style(f"{label}: ", BOLD, FG_CYAN))
        out.append(_style(bar, BOLD, bar_color))
        out.append(_style(f" {pct_text}", BOLD, bar_color))
        cursor = match.end()
    if cursor < len(line):
        out.append(_style(line[cursor:], BOLD, body_color))
    return "".join(out)


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


def _host_left_color(text: str) -> str:
    if " CPU:" in text:
        return FG_CYAN
    if " MEM:" in text:
        return FG_MAGENTA
    if " SWP:" in text:
        return FG_BLUE
    return FG_WHITE


def _gpu_metric_color(text: str) -> str:
    match = _GPU_METRIC_RE.search(text)
    if not match:
        return FG_GREEN
    return _intensity_color(_parse_percent(match.group(2)), memory=match.group(1) == "MEM")


def _style_host_section(text: str, color: str) -> str:
    bar_match = _HOST_BAR_RE.search(text)
    if not bar_match:
        return _style(text, BOLD, color)
    return "".join(
        [
            _style(text[: bar_match.start()], BOLD, color),
            _style(bar_match.group(1), DIM),
            _style(bar_match.group(2), BOLD, color),
            _style(text[bar_match.end():], BOLD, color),
        ]
    )


def _colorize_host_line(line: str) -> str:
    pieces = line.split("│")
    if len(pieces) < 2 or pieces[0]:
        return _style(line, FG_WHITE)
    out = [_style("│", DIM, FG_WHITE)]
    if len(pieces) > 1:
        out.append(_style_host_section(pieces[1], _host_left_color(pieces[1])))
        out.append(_style("│", DIM, FG_WHITE))
    if len(pieces) > 2:
        right_text = pieces[2]
        right_color = _gpu_metric_color(right_text) if "GPU " in right_text else FG_WHITE
        out.append(_style_host_section(right_text, right_color))
        out.append(_style("│", DIM, FG_WHITE))
    for extra in pieces[3:]:
        out.append(_style(extra, FG_WHITE))
        out.append(_style("│", DIM, FG_WHITE))
    return "".join(out)


def _is_border_line(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and set(stripped) <= BORDER_CHARS


def _is_header_line(line: str) -> bool:
    return (
        "GPU     PID" in line
        or "GPU      PID" in line
        or "GPU  Name" in line
        or "GPU Fan Temp" in line
        or "Fan  Temp" in line
        or "Processes:" in line
    )


def _is_device_data_line(line: str) -> bool:
    if not line.startswith("│") or "GPU-MEM" in line or _is_process_data_line(line):
        return False
    if "GPU MEM:" in line or "GPU UTL:" in line:
        return False
    if _DEVICE_ROW_RE.match(line) and "MiB" not in line[:24]:
        return True
    return any(token in line for token in (" Pwr:", "GPU-Util", " UTL:", " PWR:"))


def _is_process_data_line(line: str) -> bool:
    return bool(_PROCESS_ROW_RE.match(line))


def _is_process_title(line: str) -> bool:
    return "Processes:" in line and "@" in line


def _is_host_line(line: str) -> bool:
    return any(label in line for label in (" Load Average:", " CPU:", " MEM:", " SWP:", " GPU MEM:", " GPU UTL:"))


def _is_graph_line(line: str) -> bool:
    return "120s" in line or "60s" in line or "30s" in line or "╴" in line
