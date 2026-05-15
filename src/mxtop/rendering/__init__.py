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

MEM_THRESHOLDS = (10, 80)
GPU_THRESHOLDS = (10, 75)
_BYTE_UNITS = {
    "B": 1.0,
    "KiB": 1024.0,
    "MiB": 1024.0**2,
    "GiB": 1024.0**3,
    "TiB": 1024.0**4,
}


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


def _colorize_device_row(line: str) -> str:
    return _colorize_device_cells(line)


def _colorize_device_cells(line: str) -> str:
    pieces = line.split("│")
    if len(pieces) < 3:
        return _style(line, BOLD, FG_WHITE)
    out: list[str] = []
    for index, piece in enumerate(pieces):
        if index:
            out.append(_style("│", DIM, FG_WHITE))
        if not piece:
            continue
        if index == 0:
            out.append(_style(piece, BOLD, FG_WHITE))
            continue
        role = (index - 1) % 4
        out.append(_colorize_device_cell(piece, role))
    return "".join(out)


def _colorize_device_cell(text: str, role: int) -> str:
    if role == 0:
        return _style_watt_ratio(text)
    if role == 1:
        return _style_memory_ratio(text)
    if role == 2:
        return _style_gpu_percent(text)
    return _style_bar_cell(text)


def _style_watt_ratio(text: str) -> str:
    match = _WATT_RATIO_RE.search(text)
    if not match:
        return _style(text, BOLD, FG_WHITE)
    used = _float_text(match.group(1))
    limit = _float_text(match.group(2))
    value = None if used is None or not limit else min(100.0, max(0.0, used / limit * 100))
    return _style_with_span(
        text,
        match.start(),
        match.end(),
        _intensity_color(value, memory=False),
    )


def _style_memory_ratio(text: str) -> str:
    match = _MEMORY_RATIO_RE.search(text)
    if not match:
        return _style(text, BOLD, FG_WHITE)
    value = _ratio_percent(match.group(1), match.group(2), match.group(3), match.group(4))
    return _style_with_span(
        text,
        match.start(),
        match.end(),
        _intensity_color(value, memory=True),
    )


def _style_gpu_percent(text: str) -> str:
    match = _CELL_GPU_PERCENT_RE.search(text)
    if not match:
        return _style(text, BOLD, FG_WHITE)
    return _style_with_span(
        text,
        match.start(),
        match.end(),
        _intensity_color(_parse_percent(match.group(1)), memory=False),
    )


def _style_bar_cell(text: str) -> str:
    out: list[str] = []
    cursor = 0
    for match in _BAR_RE.finditer(text):
        label = match.group(1)
        bar = match.group(2)
        pct_text = match.group(3)
        color = _bar_color(label, pct_text)
        out.append(_style(text[cursor : match.start()], BOLD, FG_WHITE))
        out.append(_style(f"{label}: ", BOLD, FG_CYAN))
        out.append(_style(bar, BOLD, color))
        out.append(_style(f" {pct_text}", BOLD, color))
        cursor = match.end()
    out.append(_style(text[cursor:], BOLD, FG_WHITE))
    return "".join(out)


def _style_with_span(text: str, start: int, end: int, color: str) -> str:
    return "".join(
        [
            _style(text[:start], BOLD, FG_WHITE),
            _style(text[start:end], BOLD, color),
            _style(text[end:], BOLD, FG_WHITE),
        ]
    )


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
    match = _PROCESS_ROW_FIELDS_RE.search(line)
    if not match:
        return "".join([_style(line[:2], FG_WHITE), _style(line[2:5], BOLD, FG_GREEN), _style(line[5:], FG_WHITE)])
    out = [
        _style(match.group("prefix"), FG_WHITE),
        _style(match.group("gpu"), BOLD, FG_GREEN),
        _style(match.group("before_mem"), FG_WHITE),
        _style(match.group("gpu_mem"), FG_WHITE),
        _style(match.group("before_sm"), FG_WHITE),
        _style(match.group("sm"), BOLD, _intensity_color(_parse_percent(match.group("sm")), memory=False)),
        _style(match.group("before_gmbw"), FG_WHITE),
        _style(match.group("gmbw"), BOLD, _intensity_color(_parse_percent(match.group("gmbw")), memory=True)),
        _style(match.group("before_cpu"), FG_WHITE),
        _style(match.group("cpu"), BOLD, _intensity_color(_parse_percent(match.group("cpu")), memory=False)),
        _style(match.group("before_mem_pct"), FG_WHITE),
        _style(match.group("mem_pct"), BOLD, _intensity_color(_parse_percent(match.group("mem_pct")), memory=True)),
        _style(line[match.end() :], FG_WHITE),
    ]
    return "".join(out)


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
    return bool(stripped) and set(stripped) <= BORDER_CHARS | {" "}


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
    if _is_header_line(line):
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
