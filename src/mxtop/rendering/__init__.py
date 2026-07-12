from __future__ import annotations

import getpass
import math
import re

from mxtop.models import DeviceSnapshot, FrameSnapshot, ProcessSnapshot
from mxtop.ui import classify
from mxtop.ui.classify import host_graph_context
from mxtop.ui.panels import render_snapshot_screen

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
_BAR_RE = classify.BAR_RE
_BRAILLE_RUN_RE = classify.BRAILLE_RUN_RE
_GPU_METRIC_RE = classify.GPU_METRIC_RE
_PROCESS_ROW_FIELDS_RE = classify.PROCESS_ROW_FIELDS_RE

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
    rendered = render_snapshot_screen(frame, width=width)
    if not use_color:
        return "\n".join(rendered.lines)
    return "\n".join(colorize_screen(frame, rendered.lines))


def colorize_screen(frame: FrameSnapshot, lines: list[str]) -> list[str]:
    """Apply nvitop-compatible ANSI spans to a rendered MetaX screen."""

    host_context = host_graph_context(lines)
    device_context = device_row_levels(lines, frame)
    dense_device_context = dense_device_row_context(lines, frame)
    process_context = process_row_levels(lines, frame)
    return [
        _colorize_line(
            row,
            line,
            host_context.get(row),
            device_color=_level_color(device_context.get(row)),
            dense_devices=dense_device_context.get(row),
            process_context=_process_color_context(process_context.get(row)),
        )
        for row, line in enumerate(lines)
    ]


_DEVICE_INDEX_RE = re.compile(r"^│\s*(\d+)\s+")


def device_row_levels(lines: list[str], frame: FrameSnapshot) -> dict[int, int]:
    """Map rendered device rows to their combined memory/GPU load level."""

    devices = {device.index: device for device in frame.devices}
    return {
        row: device_display_level(devices[index])
        for row, index in device_row_indices(lines, frame).items()
    }


def device_row_indices(lines: list[str], frame: FrameSnapshot) -> dict[int, int]:
    """Map rendered device rows to physical MetaX device indices."""

    devices = {device.index: device for device in frame.devices}
    context: dict[int, int] = {}
    current: DeviceSnapshot | None = None
    for row, line in enumerate(lines):
        if classify.dense_device_cell_spans(line):
            current = None
            continue
        match = _DEVICE_INDEX_RE.match(line)
        if match is not None and int(match.group(1)) in devices:
            current = devices[int(match.group(1))]
        if current is not None and _is_device_data_line(line):
            context[row] = current.index
        if line.startswith(("├", "╞", "╘")) and not _is_device_data_line(line):
            if line.startswith("╘"):
                current = None
    return context


def dense_device_row_context(
    lines: list[str],
    frame: FrameSnapshot,
) -> dict[int, tuple[tuple[int, int, int, int], ...]]:
    """Map dense fleet cells to their text span, GPU index, and load level."""

    devices = {device.index: device for device in frame.devices}
    context: dict[int, tuple[tuple[int, int, int, int], ...]] = {}
    for row, line in enumerate(lines):
        cells = tuple(
            (start, end, index, device_display_level(devices[index]))
            for start, end, index in classify.dense_device_cell_spans(line)
            if index in devices
        )
        if cells:
            context[row] = cells
    return context


def device_display_level(device: DeviceSnapshot) -> int:
    """Return 0/1/2 for light/moderate/heavy combined device load."""

    levels: list[int] = []
    for value, thresholds in (
        (_device_memory_percent(device), MEM_THRESHOLDS),
        (device.gpu_util_percent, GPU_THRESHOLDS),
    ):
        if value is None or not math.isfinite(float(value)):
            continue
        if value >= thresholds[1]:
            levels.append(2)
        elif value >= thresholds[0]:
            levels.append(1)
        else:
            levels.append(0)
    return max(levels, default=1)


def _device_memory_percent(device: DeviceSnapshot) -> float | None:
    value = device.memory_util_percent
    if value is not None:
        return float(value)
    if device.memory_used_bytes is None or not device.memory_total_bytes:
        return None
    return 100.0 * device.memory_used_bytes / device.memory_total_bytes


def process_row_levels(lines: list[str], frame: FrameSnapshot) -> dict[int, tuple[int, bool]]:
    """Map process rows to their device level and current-user ownership."""

    devices = {device.index: device_display_level(device) for device in frame.devices}
    processes: dict[tuple[int, int], list[ProcessSnapshot]] = {}
    for process in frame.processes:
        processes.setdefault((process.gpu_index, process.pid), []).append(process)
    owner = getpass.getuser()
    for line in lines:
        if _is_process_title(line) and "@" in line:
            matches = re.findall(r"([^\s│@]+)@([^\s│]+)", line)
            if matches:
                owner = matches[-1][0]
            break
    context: dict[int, tuple[int, bool]] = {}
    for row, line in enumerate(lines):
        match = _PROCESS_ROW_FIELDS_RE.search(line)
        if match is None:
            continue
        try:
            gpu_index = int(match.group("gpu"))
            remainder = match.group("before_mem")
            pid_match = re.match(r"\s+(\d+)", remainder)
            if pid_match is None:
                continue
            pid = int(pid_match.group(1))
        except (TypeError, ValueError):
            continue
        process = next(iter(processes.get((gpu_index, pid), ())), None)
        owned = owner == "root" or process is None or process.user == owner
        context[row] = (devices.get(gpu_index, 1), owned)
    return context


def _level_color(level: int | None) -> str | None:
    return None if level is None else (FG_GREEN, FG_YELLOW, FG_RED)[level]


def _process_color_context(context: tuple[int, bool] | None) -> tuple[str, bool] | None:
    if context is None:
        return None
    level, owned = context
    return (FG_GREEN, FG_YELLOW, FG_RED)[level], owned




def _style(text: str, *codes: str) -> str:
    if not text:
        return text
    return "".join(codes) + text + RESET


def _colorize_line(
    row: int,
    line: str,
    host_context: tuple[str, float | None, bool] | None = None,
    *,
    device_color: str | None = None,
    dense_devices: tuple[tuple[int, int, int, int], ...] | None = None,
    process_context: tuple[str, bool] | None = None,
) -> str:
    if not line:
        return line
    if row == 0:
        return _colorize_title(line)
    if "SUPERUSER LOGGED-IN" in line or "send signals)" in line:
        return _colorize_process_action_line(line)
    if "backend error" in line or "error=" in line:
        return _style(line, BOLD, FG_RED)
    if _is_process_title(line):
        return _colorize_process_title(line)
    if _is_process_data_line(line):
        return _colorize_process_row(line, process_context)
    if dense_devices:
        return _colorize_dense_device_row(line, dense_devices)
    if _is_device_data_line(line):
        return _colorize_device_row(line, device_color)
    if line.startswith("[ CPU:") or line.startswith("[ MEM:"):
        return _colorize_compact_host_line(line)
    if host_context is not None or _is_host_line(line):
        return _colorize_host_line(line, host_context)
    if _is_border_line(line):
        return line
    if "MXTOP" in line and "Driver Version" in line:
        return line
    if _is_header_line(line):
        return line
    if _is_graph_line(line):
        return _style(line, DIM, _dim_fg())
    return line


def _colorize_dense_device_row(
    line: str,
    contexts: tuple[tuple[int, int, int, int], ...],
) -> str:
    output: list[str] = []
    cursor = 0
    for start, end, _index, level in contexts:
        output.append(line[cursor:start])
        output.append(_style(line[start:end], BOLD, _level_color(level) or FG_YELLOW))
        cursor = end
    output.append(line[cursor:])
    return "".join(output)


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
        return _spectrum_color(value / 100.0)
    if value >= thresholds[1]:
        return FG_RED
    if value >= thresholds[0]:
        return FG_YELLOW
    return FG_GREEN


def _spectrum_color(fraction: float) -> str:
    colors = (
        FG_GREEN,
        FG_BRIGHT_GREEN,
        FG_YELLOW,
        FG_BRIGHT_YELLOW,
        FG_RED,
        FG_BRIGHT_RED,
    )
    index = min(len(colors) - 1, max(0, round((len(colors) - 1) * fraction)))
    return colors[index]


def _bar_color(label: str, pct_text: str) -> str:
    return _intensity_color(_parse_percent(pct_text), memory=label in {"MEM", "MBW"})


def _colorize_device_row(line: str, display_color: str | None = None) -> str:
    return _colorize_device_cells(line, display_color)


def _colorize_device_cells(line: str, display_color: str | None = None) -> str:
    pieces = line.split("│")
    if len(pieces) < 3:
        return _style(line, BOLD, display_color or FG_YELLOW)
    out: list[str] = []
    for index, piece in enumerate(pieces):
        if index:
            out.append("│")
        if not piece:
            continue
        if index == 0:
            out.append(piece)
            continue
        if index <= 3:
            out.append(_style(piece, BOLD, display_color or FG_YELLOW))
        else:
            out.append(_style_bar_cell(piece))
    return "".join(out)


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
        if COLORFUL_MODE:
            denominator = max(1, len(bar) - 1)
            out.extend(
                _style(
                    character,
                    BOLD,
                    _spectrum_color(index / denominator)
                    if character not in {"░", " "}
                    else color,
                )
                for index, character in enumerate(bar)
            )
        else:
            out.append(_style(bar, BOLD, color))
        out.append(_style(f" {pct_text}", BOLD, color))
        cursor = match.end()
    out.append(_style(text[cursor:], BOLD, FG_WHITE))
    return "".join(out)


def _colorize_title(line: str) -> str:
    hint_start = line.find("(Press ")
    if hint_start < 0:
        return line
    output = [line[:hint_start]]
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


def _colorize_process_action_line(line: str) -> str:
    spans: list[tuple[int, int, tuple[str, ...]]] = []
    caution = "!CAUTION: SUPERUSER LOGGED-IN."
    if (start := line.find(caution)) >= 0:
        spans.append((start, start + 1, (BOLD, FG_RED)))
        spans.append((start + 1, start + len(caution), (FG_YELLOW,)))
    for match in re.finditer(r"(\^C|T|K)(\((?:INT|TERM|KILL)\))", line):
        spans.append((match.start(1), match.end(1), (BOLD, FG_MAGENTA)))
        spans.append((match.start(2), match.end(2), (BOLD, FG_RED)))
    output: list[str] = []
    cursor = 0
    for start, end, codes in sorted(spans):
        output.append(_style(line[cursor:start], DIM, _dim_fg()))
        output.append(_style(line[start:end], *codes))
        cursor = end
    output.append(_style(line[cursor:], DIM, _dim_fg()))
    return "".join(output)


def _colorize_process_title(line: str) -> str:
    at = line.rfind("@")
    if at <= 0:
        return _style(line, BOLD, FG_CYAN)
    start = line.rfind(" ", 0, at)
    start = 0 if start < 0 else start + 1
    end = line.find("│", at)
    end = len(line) if end < 0 else end
    user_color = FG_YELLOW if line[start:at] == "root" else FG_MAGENTA
    return "".join(
        [
            _style(line[:start], BOLD, FG_CYAN),
            _style(line[start:at], BOLD, user_color),
            _style("@", BOLD, FG_CYAN),
            _style(line[at + 1 : end], BOLD, FG_GREEN),
            _style(line[end:], BOLD, FG_CYAN),
        ]
    )


def _colorize_process_row(
    line: str,
    context: tuple[str, bool] | None = None,
) -> str:
    if line.startswith("│>"):
        return _style(line, BOLD, REVERSE, FG_CYAN)
    if len(line) < 5:
        return line
    match = _PROCESS_ROW_FIELDS_RE.search(line)
    device_color, owned = context or (FG_GREEN, True)
    if not match:
        return "".join([line[:2], _style(line[2:5], BOLD, device_color), line[5:]])
    before_gpu = match.group("prefix")
    after_gpu = line[match.end("gpu") :]
    if line.startswith("│="):
        codes = (BOLD, FG_YELLOW) if owned else (BOLD, DIM, FG_YELLOW)
        before_gpu = _style(before_gpu, *codes)
        after_gpu = _style(after_gpu, *codes)
    elif not owned:
        before_gpu = _style(before_gpu, DIM, _dim_fg())
        after_gpu = _style(after_gpu, DIM, _dim_fg())
    return "".join(
        [
            before_gpu,
            _style(match.group("gpu"), BOLD, device_color),
            after_gpu,
        ]
    )


def _colorize_compact_host_line(line: str) -> str:
    if line.startswith("[ CPU:"):
        end = line.find("]") + 1
        if end <= 0:
            return _style(line, BOLD, FG_CYAN)
        return _style(line[:end], BOLD, FG_CYAN) + line[end:]
    end = line.find("]") + 1
    if end <= 0:
        return _style(line, BOLD, FG_MAGENTA)
    second = line.find("[", end)
    if second < 0:
        return _style(line, BOLD, FG_MAGENTA)
    return "".join(
        [
            _style(line[:end], BOLD, FG_MAGENTA),
            line[end:second],
            _style(line[second:], BOLD, FG_BLUE),
        ]
    )


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
