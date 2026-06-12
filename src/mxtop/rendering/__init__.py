from __future__ import annotations

from mxtop.models import FrameSnapshot
from mxtop.ui import classify
from mxtop.ui.classify import host_graph_context
from mxtop.ui.panels import render_main_screen
from mxtop.ui.state import UiState

# Line classification and value parsing live in mxtop.ui.classify, shared with
# the curses TUI. Local aliases keep this module's call sites short.
_is_border_line = classify.is_border_line
_is_device_data_line = classify.is_device_data_line
_is_graph_line = classify.is_graph_line
_is_header_line = classify.is_header_line
_is_host_line = classify.is_host_overlay
_is_process_data_line = classify.is_process_data_line
_is_process_title = classify.is_process_title
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
FG_BLACK = "\x1b[30m"
FG_BRIGHT_GREEN = "\x1b[92m"
FG_BRIGHT_YELLOW = "\x1b[93m"
FG_BRIGHT_RED = "\x1b[91m"
LIGHT_THEME = False
COLORFUL_MODE = False


def set_render_style(light: bool | None = None, colorful: bool | None = None) -> None:
    """Toggle visual modes that mirror nvitop's ``--light`` / ``--colorful``."""
    global LIGHT_THEME, COLORFUL_MODE
    if light is not None:
        LIGHT_THEME = bool(light)
    if colorful is not None:
        COLORFUL_MODE = bool(colorful)


def _dim_fg() -> str:
    return FG_BLACK if LIGHT_THEME else FG_WHITE


BORDER_CHARS = classify.BORDER_CHARS


def render_once(frame: FrameSnapshot, use_color: bool = True, width: int = 120) -> str:
    rendered = render_main_screen(frame, UiState(), width=width)
    if not use_color:
        return "\n".join(rendered.lines)
    host_context = host_graph_context(rendered.lines)
    return "\n".join(
        _colorize_line(row, line, host_context.get(row))
        for row, line in enumerate(rendered.lines)
    )




def _style(text: str, *codes: str) -> str:
    if not text:
        return text
    return "".join(codes) + text + RESET


def _colorize_line(
    row: int,
    line: str,
    host_context: tuple[str, float | None, bool] | None = None,
) -> str:
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
    if host_context is not None or _is_host_line(line):
        return _colorize_host_line(line, host_context)
    if _is_border_line(line):
        return _style(line, DIM, _dim_fg())
    if "MXTOP" in line and "Driver Version" in line:
        return _style(line, BOLD, FG_WHITE)
    if _is_header_line(line):
        return _style(line, BOLD, FG_CYAN)
    if _is_graph_line(line):
        return _style(line, DIM, _dim_fg())
    return _style(line, FG_WHITE)


DEFAULT_GPU_UTILIZATION_THRESHOLDS: tuple[int, int] = (10, 75)
DEFAULT_MEMORY_UTILIZATION_THRESHOLDS: tuple[int, int] = (10, 80)
GPU_THRESHOLDS: tuple[int, int] = DEFAULT_GPU_UTILIZATION_THRESHOLDS
MEM_THRESHOLDS: tuple[int, int] = DEFAULT_MEMORY_UTILIZATION_THRESHOLDS


def set_intensity_thresholds(
    gpu: tuple[int, int] | None = None,
    memory: tuple[int, int] | None = None,
) -> None:
    """Override the intensity colouring thresholds used by the renderer."""
    global GPU_THRESHOLDS, MEM_THRESHOLDS
    if gpu is not None:
        low, high = sorted(int(value) for value in gpu)
        GPU_THRESHOLDS = (low, high)
    if memory is not None:
        low, high = sorted(int(value) for value in memory)
        MEM_THRESHOLDS = (low, high)


def reset_intensity_thresholds() -> None:
    """Reset intensity thresholds back to nvitop-aligned defaults."""
    set_intensity_thresholds(
        gpu=DEFAULT_GPU_UTILIZATION_THRESHOLDS,
        memory=DEFAULT_MEMORY_UTILIZATION_THRESHOLDS,
    )


def _intensity_color(value: float | None, *, memory: bool) -> str:
    if value is None:
        return FG_YELLOW
    thresholds = MEM_THRESHOLDS if memory else GPU_THRESHOLDS
    if COLORFUL_MODE:
        low, high = thresholds
        mid_low = low + (high - low) / 3
        mid_high = low + 2 * (high - low) / 3
        if value >= high + (100 - high) / 2:
            return FG_BRIGHT_RED
        if value >= high:
            return FG_RED
        if value >= mid_high:
            return FG_BRIGHT_YELLOW
        if value >= mid_low:
            return FG_YELLOW
        if value >= low:
            return FG_BRIGHT_GREEN
        return FG_GREEN
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
            out.append(_style("│", DIM, _dim_fg()))
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
        return _style(line, DIM, _dim_fg())
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


_HOST_SECTION_COLORS = {"cpu": FG_CYAN, "mem": FG_MAGENTA, "swp": FG_BLUE}


def _style_host_section(text: str, color: str, graph_color: str | None = None) -> str:
    """Style overlay text with ``color`` and braille graph runs with ``graph_color``."""
    graph_color = graph_color or color
    out: list[str] = []
    cursor = 0
    for match in _BRAILLE_RUN_RE.finditer(text):
        if match.start() > cursor:
            out.append(_style(text[cursor : match.start()], BOLD, color))
        out.append(_style(match.group(), graph_color))
        cursor = match.end()
    out.append(_style(text[cursor:], BOLD, color))
    return "".join(out)


def _colorize_host_line(line: str, host_context: tuple[str, float | None, bool] | None = None) -> str:
    pieces = line.split("│")
    if len(pieces) < 2 or pieces[0]:
        return _style(line, FG_WHITE)
    section, right_value, right_is_memory = host_context or (None, None, False)
    section_color = _HOST_SECTION_COLORS.get(section or "")
    out = [_style("│", DIM, _dim_fg())]
    if len(pieces) > 1:
        label_color = _host_left_color(pieces[1])
        out.append(_style_host_section(pieces[1], label_color, section_color or label_color))
        out.append(_style("│", DIM, _dim_fg()))
    if len(pieces) > 2:
        right_text = pieces[2]
        right_color = _gpu_metric_color(right_text) if "GPU " in right_text else FG_WHITE
        graph_color = _intensity_color(right_value, memory=right_is_memory) if right_value is not None else right_color
        out.append(_style_host_section(right_text, right_color, graph_color))
        out.append(_style("│", DIM, _dim_fg()))
    for extra in pieces[3:]:
        if not extra:
            continue
        out.append(_style(extra, FG_WHITE))
        out.append(_style("│", DIM, _dim_fg()))
    return "".join(out)


