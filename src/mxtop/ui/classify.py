"""Single source of truth for classifying and parsing rendered screen lines.

Both renderers — the curses TUI (``mxtop.tui``) and the ANSI text renderer
(``mxtop.rendering``) — colorize the plain-text lines produced by
``mxtop.ui.panels``. Until panels emit styled spans directly, they must
re-discover what each line is; this module keeps that knowledge in exactly
one place so the two renderers can never drift apart.
"""

from __future__ import annotations

import re

BORDER_CHARS = {
    "╒", "╕", "╘", "╛", "╞", "╡", "╪", "╧",
    "├", "┤", "┼", "─", "═", "│", "┬", "┴", "╤",
}

DEVICE_ROW_RE = re.compile(r"^│\s*\d+\s+\S")
DENSE_DEVICE_CELL_RE = re.compile(
    r"^\s*(?P<gpu>\d+)\s+(?:N/A|MAX|MIN|[+-]?\d+C)"
    r"\s+(?:N/A|MAX|MIN|[+-]?\d+%)\s+(?:N/A|MAX|MIN|[+-]?\d+%)"
    r"\s+(?:N/A|MAX|MIN|[+-]?\d+W)\s*$"
)
PROCESS_ROW_RE = re.compile(r"^│[ =>]\s*\d+\s+\d+\s")
BAR_RE = re.compile(r"(MEM|MBW|UTL|PWR): ([█░▏▎▍▌▋▊▉ ]+) (\S+)")
GPU_METRIC_RE = re.compile(r"GPU (MEM|UTL):\s*(\S+)")
WATT_RATIO_RE = re.compile(r"(\d+(?:\.\d+)?)W\s*/\s*(\d+(?:\.\d+)?)W")
MEMORY_RATIO_RE = re.compile(
    r"(\d+(?:\.\d+)?)(B|KiB|MiB|GiB|TiB)\s*/\s*(\d+(?:\.\d+)?)(B|KiB|MiB|GiB|TiB)"
)
CELL_GPU_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)%")
PROCESS_ROW_FIELDS_RE = re.compile(
    r"^(?P<prefix>│[ =>]\s*)(?P<gpu>\d+)(?P<before_mem>.*?\s)"
    r"(?P<gpu_mem>N/A|\d+(?:\.\d+)?(?:B|KiB|MiB|GiB|TiB))"
    r"(?P<before_sm>\s+)(?P<sm>\S+)"
    r"(?P<before_gmbw>\s+)(?P<gmbw>\S+)"
    r"(?P<before_cpu>\s+)(?P<cpu>\S+)"
    r"(?P<before_mem_pct>\s+)(?P<mem_pct>\S+)"
)
BRAILLE_RUN_RE = re.compile(r"[⠀-⣿]+")

BYTE_UNITS = {
    "B": 1.0,
    "KiB": 1024.0,
    "MiB": 1024.0**2,
    "GiB": 1024.0**3,
    "TiB": 1024.0**4,
}

_HOST_LABELS = (" Load Average:", " CPU:", " MEM:", " SWP:", " GPU MEM:", " GPU UTL:")


def parse_percent(text: str) -> float | None:
    if text == "MAX":
        return 100.0
    try:
        return float(text.replace("%", ""))
    except ValueError:
        return None


def float_text(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


def ratio_percent(used: str, used_unit: str, total: str, total_unit: str) -> float | None:
    used_value = float_text(used)
    total_value = float_text(total)
    if used_value is None or total_value is None:
        return None
    used_bytes = used_value * BYTE_UNITS[used_unit]
    total_bytes = total_value * BYTE_UNITS[total_unit]
    if total_bytes <= 0:
        return None
    return min(100.0, max(0.0, used_bytes / total_bytes * 100))


def is_border_line(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and set(stripped) <= BORDER_CHARS | {" "}


def is_header_line(line: str) -> bool:
    return (
        "GPU     PID" in line
        or "GPU      PID" in line
        or "GPU  Name" in line
        or "GPU Fan Temp" in line
        or "GPU TEMP UTIL MEM%" in line
        or "Fan  Temp" in line
        or "Processes:" in line
    )


def is_device_data_line(line: str) -> bool:
    if not line.startswith("│") or "GPU-MEM" in line or is_process_data_line(line):
        return False
    if is_header_line(line):
        return False
    if "GPU MEM:" in line or "GPU UTL:" in line:
        return False
    if DEVICE_ROW_RE.match(line) and "MiB" not in line[:24]:
        return True
    return any(token in line for token in (" Pwr:", "GPU-Util", " UTL:", " PWR:"))


def dense_device_cell_spans(line: str) -> tuple[tuple[int, int, int], ...]:
    """Return ``(start, end, gpu_index)`` spans for a dense fleet row."""

    if not line.startswith("│") or not line.endswith("│"):
        return ()
    spans: list[tuple[int, int, int]] = []
    start = 1
    for cell in line[1:-1].split("│"):
        end = start + len(cell)
        match = DENSE_DEVICE_CELL_RE.fullmatch(cell)
        if match is not None:
            spans.append((start, end, int(match.group("gpu"))))
        start = end + 1
    return tuple(spans)


def is_process_data_line(line: str) -> bool:
    return bool(PROCESS_ROW_RE.match(line))


def is_process_title(line: str) -> bool:
    return "Processes:" in line and "@" in line


def is_host_overlay(line: str) -> bool:
    """The line carries one of the host-panel text overlays (no box check)."""
    return any(label in line for label in _HOST_LABELS)


def is_host_data_line(line: str) -> bool:
    return line.startswith("│") and is_host_overlay(line)


def is_version_line(line: str) -> bool:
    return line.startswith("│") and "MXTOP " in line and "Driver Version" in line


def is_graph_line(line: str) -> bool:
    return "120s" in line or "60s" in line or "30s" in line or "╴" in line


def host_graph_context(lines: list[str]) -> dict[int, tuple[str, float | None, bool]]:
    """Map host-panel rows to ``(section, right_value, right_is_memory)``.

    The host panel mirrors nvitop: 5 CPU graph rows starting at the
    "Load Average" row, the time axis, then 4 MEM rows and 1 SWP row. The
    right-hand GPU graphs are colored by the percent shown in their label
    rows (GPU MEM on the first row, GPU UTL on the last).
    """
    for index, line in enumerate(lines):
        if "Load Average:" not in line:
            continue
        gpu_mem = gpu_utl = None
        if (match := GPU_METRIC_RE.search(line)) is not None:
            gpu_mem = parse_percent(match.group(2))
        if index + 10 < len(lines) and (match := GPU_METRIC_RE.search(lines[index + 10])) is not None:
            gpu_utl = parse_percent(match.group(2))
        context: dict[int, tuple[str, float | None, bool]] = {}
        for offset in range(5):
            context[index + offset] = ("cpu", gpu_mem, True)
        for offset in range(6, 10):
            context[index + offset] = ("mem", gpu_utl, False)
        context[index + 10] = ("swp", gpu_utl, False)
        return context
    return {}
