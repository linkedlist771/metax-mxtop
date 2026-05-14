from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import getpass
import os
import socket

from mxtop import __version__
from mxtop.formatting import (
    ellipsize,
    format_compact_bytes,
    format_duration,
    format_float,
    format_mib,
    format_percent,
    format_percent_precise,
    format_percent_value,
)
from mxtop.models import DeviceSnapshot, FrameSnapshot, ProcessSnapshot
from mxtop.ui.help import HELP_LINES
from mxtop.ui.state import LayoutMode, UiState, keep_selection, sort_processes

MIN_SCREEN_WIDTH = 79
DEVICE_BAR_MIN_WIDTH = 112
HOST_GRAPH_MIN_WIDTH = 100
PROCESS_MIN_WIDTH = 79


@dataclass(slots=True)
class RenderedScreen:
    lines: list[str]
    process_start: int
    process_count: int


def render_title(frame: FrameSnapshot, width: int, error: str | None = None) -> str:
    del error
    timestamp = datetime.fromtimestamp(frame.timestamp).strftime("%a %b %d %H:%M:%S %Y")
    hint = "(Press h for help or q to quit)"
    if width <= len(timestamp) + len(hint):
        return ellipsize(f"{timestamp} {hint}", width)
    return f"{timestamp}{' ' * (width - len(timestamp) - len(hint))}{hint}"


def render_small_terminal_message(width: int, height: int | None = None) -> RenderedScreen:
    message = "Terminal size is too small"
    detail = f"mxtop needs at least {MIN_SCREEN_WIDTH} columns"
    box_width = min(max(len(detail) + 4, len(message) + 4), max(20, width))
    inner = max(0, box_width - 2)
    lines = [
        "╒" + "═" * inner + "╕",
        _box_content(message.center(inner), box_width),
        _box_content(detail.center(inner), box_width),
        "╘" + "═" * inner + "╛",
    ]
    if height is not None and height > len(lines):
        padding = [""] * max(0, (height - len(lines)) // 2)
        lines = padding + lines
    return RenderedScreen([line.center(width) for line in lines], process_start=0, process_count=0)


def render_device_panel(frame: FrameSnapshot, width: int, layout: LayoutMode = LayoutMode.AUTO) -> list[str]:
    inner = max(MIN_SCREEN_WIDTH - 2, width - 2)
    right_width = _right_panel_width(width, layout)
    core_width = inner - right_width - (1 if right_width else 0)
    driver_version = _driver_version(frame)
    lines = [_top_border(core_width)]
    lines.append(_box_content(_version_line(core_width, driver_version), core_width + 2))
    lines.append(_middle_border(core_width))
    lines.append(_box_content(_device_header_one(core_width), core_width + 2))
    lines.append(_box_content(_device_header_two(core_width), core_width + 2))
    lines.append(_split_border(core_width, right_width))

    if not frame.devices:
        lines.append(_split_content("No visible devices found", "", core_width, right_width))
        lines.append(_bottom_border(core_width + right_width + (1 if right_width else 0), core_width, right_width))
        return lines

    for device in frame.devices:
        lines.append(_split_content(_device_identity_line(device, core_width), _device_mem_bar(device), core_width, right_width))
        lines.append(_split_content(_device_metrics_line(device, core_width), _device_util_bar(device), core_width, right_width))
    lines.append(_bottom_border(core_width + right_width + (1 if right_width else 0), core_width, right_width))
    return lines


def render_host_panel(frame: FrameSnapshot, width: int) -> list[str]:
    inner = max(MIN_SCREEN_WIDTH - 2, width - 2)
    right_width = 35 if width >= HOST_GRAPH_MIN_WIDTH else 0
    core_width = inner - right_width - (1 if right_width else 0)
    cpu, memory_text, swap_text = _host_metrics()
    gpu_mem = _average_percent(device.memory_util_percent for device in frame.devices)
    gpu_util = _average_percent(device.gpu_util_percent for device in frame.devices)
    lines = [_split_border(core_width, right_width)]
    lines.append(_split_content(f" Load Average:  {_load_average_text()}", f" GPU MEM: {format_percent_precise(gpu_mem)}", core_width, right_width))
    lines.append(_split_content(f" CPU: {format_percent(cpu)}", "", core_width, right_width))
    lines.append(_split_content("", "", core_width, right_width))
    lines.append(_split_content(_time_axis(core_width), _time_axis(right_width, short=True), core_width, right_width))
    lines.append(_split_content(_dotted_graph(core_width, "cyan"), _dotted_graph(right_width, "green"), core_width, right_width))
    lines.append(_split_content(_dotted_graph(core_width, "magenta"), _dotted_graph(right_width, "green"), core_width, right_width))
    lines.append(_split_content(f" MEM: {memory_text}", f" GPU UTL: {format_percent_precise(gpu_util)}", core_width, right_width))
    lines.append(_split_content(f" SWP: {swap_text}", "", core_width, right_width))
    lines.append(_bottom_border(inner, core_width, right_width))
    return lines


def _load_average_text() -> str:
    try:
        load1, load5, load15 = os.getloadavg()
    except OSError:
        return "N/A"
    return f"{load1:.2f}  {load5:.2f}  {load15:.2f}"


def visible_processes(frame: FrameSnapshot, state: UiState) -> list[ProcessSnapshot]:
    processes = sort_processes(frame.processes, state.process_sort, state.reverse_sort)
    keep_selection(state, processes)
    return processes


def render_process_panel(
    frame: FrameSnapshot,
    state: UiState,
    width: int,
    height: int | None = None,
) -> tuple[list[str], int, int]:
    processes = _process_group_order(visible_processes(frame, state), state)
    inner = max(PROCESS_MIN_WIDTH - 2, width - 2)
    user_host = _user_host()
    lines = [_top_border(inner), _box_content(_process_title(inner, user_host), width)]
    lines.append(_box_content(_process_header(inner), width))
    lines.append(_middle_border(inner))
    available_rows = None if height is None else max(1, height - len(lines) - 1)

    if not processes:
        lines.append(_box_content("No running processes found", width))
        lines.append(_bottom_border(inner))
        return lines, len(lines) - 2, 0

    if available_rows is not None:
        max_offset = max(0, len(processes) - available_rows)
        state.scroll_offset = max(0, min(state.scroll_offset, max_offset))
        if state.selected_index < state.scroll_offset:
            state.scroll_offset = state.selected_index
        if state.selected_index >= state.scroll_offset + available_rows:
            state.scroll_offset = state.selected_index - available_rows + 1
        shown = processes[state.scroll_offset : state.scroll_offset + available_rows]
    else:
        shown = processes

    process_start = len(lines)
    host_memory_total = _host_memory_total()
    for process in shown:
        lines.append(_box_content(_process_row(process, state, inner, host_memory_total), width))
    lines.append(_bottom_border(inner))
    return lines, process_start, len(shown)


def render_footer(frame: FrameSnapshot, state: UiState, interval: float, error: str | None, width: int) -> str:
    del frame
    footer = f"mode={state.layout.value}  sort={state.process_sort.value}{' reversed' if state.reverse_sort else ''}  refresh={interval:.1f}s"
    if state.pending_sort_key:
        footer += "  sort-key pending"
    if state.command_offset:
        footer += f"  command+{state.command_offset}"
    if error:
        footer = f"backend error: {error}  " + footer
    return ellipsize(footer, width)


def render_help(width: int) -> list[str]:
    return [ellipsize(line, width) for line in HELP_LINES]


def render_main_screen(
    frame: FrameSnapshot,
    state: UiState | None = None,
    *,
    width: int = 120,
    height: int | None = None,
    interval: float = 1.0,
    error: str | None = None,
) -> RenderedScreen:
    state = state or UiState()
    if state.show_help:
        return RenderedScreen(render_help(width), process_start=0, process_count=0)
    if width < MIN_SCREEN_WIDTH or (height is not None and height < 8):
        return render_small_terminal_message(width, height)

    lines: list[str] = [render_title(frame, width, error)]
    lines.extend(render_device_panel(frame, width, state.layout))
    if state.layout != LayoutMode.COMPACT:
        lines.extend(render_host_panel(frame, width))
        lines.append("")

    process_height = None
    if height is not None:
        reserved = len(lines) + 1
        process_height = max(4, height - reserved)
    process_lines, process_start, process_count = render_process_panel(frame, state, width, process_height)
    absolute_process_start = len(lines) + process_start
    lines.extend(process_lines)
    if height is not None:
        lines = lines[: max(0, height - 1)]
        lines.append(render_footer(frame, state, interval, error, width))
    return RenderedScreen(lines, process_start=absolute_process_start, process_count=process_count)


def _top_border(inner_width: int) -> str:
    return "╒" + "═" * inner_width + "╕"


def _middle_border(inner_width: int) -> str:
    return "├" + "─" * inner_width + "┤"


def _split_border(core_width: int, right_width: int) -> str:
    if right_width <= 0:
        return _middle_border(core_width)
    return "├" + "─" * core_width + "┼" + "─" * right_width + "┤"


def _bottom_border(inner_width: int, core_width: int | None = None, right_width: int = 0) -> str:
    if core_width is not None and right_width > 0:
        return "╘" + "═" * core_width + "╧" + "═" * right_width + "╛"
    return "╘" + "═" * inner_width + "╛"


def _box_content(text: str, width: int) -> str:
    inner = max(0, width - 2)
    return "│" + ellipsize(text, inner).ljust(inner) + "│"


def _split_content(core: str, right: str, core_width: int, right_width: int) -> str:
    if right_width <= 0:
        return _box_content(core, core_width + 2)
    return "│" + ellipsize(core, core_width).ljust(core_width) + "│" + ellipsize(right, right_width).ljust(right_width) + "│"


def _right_panel_width(width: int, layout: LayoutMode) -> int:
    if layout == LayoutMode.COMPACT or width < DEVICE_BAR_MIN_WIDTH:
        return 0
    return min(35, max(18, width - 103))


def _version_line(width: int, driver_version: str) -> str:
    left = f" MXTOP {__version__}"
    middle = f"Driver Version: {driver_version}"
    right = f"MX Driver Version: {driver_version}"
    return _place_three_spaced(left, middle, right, width)


def _device_header_one(width: int) -> str:
    return _place_three(" GPU  Name           Persistence-M", "Bus-Id        Disp.A", "Volatile Uncorr. ECC", width)


def _device_header_two(width: int) -> str:
    return _place_three(" Fan  Temp  Perf  Pwr:Usage/Cap", "Memory-Usage", "GPU-Util  Compute M.", width)


def _device_identity_line(device: DeviceSnapshot, width: int) -> str:
    left = f" {device.index:>3}  {ellipsize(device.name, 17):<17} {_status_text(device.persistence_mode):>12}"
    middle = f"{ellipsize(device.bdf or device.uuid or 'N/A', 16):<16} {_status_text(None):>6}"
    right = f"{_status_text(device.ecc_status):>21}"
    return _place_three(left, middle, right, width)


def _device_metrics_line(device: DeviceSnapshot, width: int) -> str:
    fan = "MAX" if device.fan_percent is not None and device.fan_percent >= 100 else format_percent(device.fan_percent)
    perf = device.performance_state or "N/A"
    power = f"{format_float(device.power_w, 'W')} / {format_float(device.power_limit_w, 'W')}"
    memory = f"{format_compact_bytes(device.memory_used_bytes)} / {format_compact_bytes(device.memory_total_bytes)}"
    left = f" {fan:>3}  {format_float(device.temperature_c, 'C'):>4}  {perf:>4}  {power:>15}"
    middle = f"{memory:>27}"
    right = f"{format_percent(device.gpu_util_percent):>8}  {_compute_mode(device):>10}"
    return _place_three(left, middle, right, width)


def _device_mem_bar(device: DeviceSnapshot) -> str:
    return f"MEM: │ {format_percent_precise(device.memory_util_percent)}"


def _device_util_bar(device: DeviceSnapshot) -> str:
    return f"UTL: │ {format_percent_precise(device.gpu_util_percent)}"


def _compute_mode(device: DeviceSnapshot) -> str:
    return device.metaxlink or "Default"


def _driver_version(frame: FrameSnapshot) -> str:
    for device in frame.devices:
        if device.driver_version:
            return device.driver_version
    return "N/A"


def _status_text(value: str | None) -> str:
    if not value:
        return "N/A"
    normalized = value.strip()
    lowered = normalized.lower()
    if lowered == "enabled":
        return "On"
    if lowered == "disabled":
        return "Off"
    return normalized


def _place_three(left: str, middle: str, right: str, width: int) -> str:
    if width < 84:
        return ellipsize(f"{left}  {middle}  {right}", width)
    left_width = max(30, min(42, width // 3 + 3))
    right_width = max(22, min(30, width // 3))
    middle_width = max(10, width - left_width - right_width - 2)
    return (
        ellipsize(left, left_width).ljust(left_width)
        + "│"
        + ellipsize(middle, middle_width).rjust(middle_width)
        + "│"
        + ellipsize(right, right_width).rjust(right_width)
    )


def _place_three_spaced(left: str, middle: str, right: str, width: int) -> str:
    if width <= len(left) + len(middle) + len(right) + 2:
        return ellipsize(f"{left}  {middle}  {right}", width)
    center_start = max(len(left) + 1, (width - len(middle)) // 2)
    right_start = width - len(right) - 1
    if center_start + len(middle) >= right_start:
        return ellipsize(f"{left}  {middle}  {right}", width)
    line = list(" " * width)
    line[: len(left)] = left
    line[center_start : center_start + len(middle)] = middle
    line[right_start : right_start + len(right)] = right
    return "".join(line)


def _host_metrics() -> tuple[float | None, str, str]:
    try:
        import psutil
    except ModuleNotFoundError:
        return None, "N/A", "N/A"
    cpu = float(psutil.cpu_percent(interval=None))
    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()
    memory_text = f"{format_compact_bytes(int(memory.used))} ({format_percent(float(memory.percent))})"
    swap_text = f"{format_compact_bytes(int(swap.used))} ({format_percent(float(swap.percent))})"
    return cpu, memory_text, swap_text


def _host_memory_total() -> int | None:
    try:
        import psutil
    except ModuleNotFoundError:
        return None
    return int(psutil.virtual_memory().total)


def _average_percent(values) -> float | None:
    known = [float(value) for value in values if value is not None]
    if not known:
        return None
    return sum(known) / len(known)


def _time_axis(width: int, *, short: bool = False) -> str:
    if width <= 0:
        return ""
    if width < 18:
        return "─" * width
    labels = ["30s"] if short else ["120s", "60s", "30s"]
    line = "─" * width
    for index, label in enumerate(labels, start=1):
        position = max(0, min(width - len(label), width * index // (len(labels) + 1)))
        line = line[:position] + label + line[position + len(label) :]
    return line


def _dotted_graph(width: int, color: str) -> str:
    del color
    if width <= 0:
        return ""
    return "." * width


def _user_host() -> str:
    user = getpass.getuser()
    host = socket.gethostname().split(".", maxsplit=1)[0]
    return f"{user}@{host}"


def _process_title(width: int, user_host: str) -> str:
    left = " Processes:"
    if width <= len(left) + len(user_host):
        return ellipsize(f"{left} {user_host}", width)
    return f"{left}{' ' * (width - len(left) - len(user_host))}{user_host}"


def _process_header(width: int) -> str:
    return ellipsize(" GPU      PID      USER  GPU-MEM %SM  %CPU  %MEM       TIME  COMMAND", width)


def _process_group_order(processes: list[ProcessSnapshot], state: UiState) -> list[ProcessSnapshot]:
    if state.process_sort.value != "default" or state.reverse_sort:
        return processes
    return sorted(processes, key=lambda process: (process.gpu_index, process.user in {None, "root"}, process.pid))


def _process_row(process: ProcessSnapshot, state: UiState, width: int, host_memory_total: int | None) -> str:
    selected = process.selection_key == state.selected_key
    marker = ">" if selected and state.command_offset else " "
    process_type = (process.process_type or " ")[:1]
    command = (process.command or process.name)[state.command_offset :]
    fixed = (
        f"{marker}{process.gpu_index:>3} {process.pid:>8} {process_type:<1} "
        f"{ellipsize(process.user, 8, marker='+'):>8} "
        f"{format_mib(process.gpu_memory_bytes):>9} "
        f"{format_percent_value(process.gpu_util_percent):>3} "
        f"{format_percent_value(process.cpu_percent):>5} "
        f"{_host_memory_percent(process, host_memory_total):>5} "
        f"{format_duration(process.runtime_seconds):>10} "
    )
    command_width = max(0, width - len(fixed))
    return fixed + ellipsize(command, command_width)


def _host_memory_percent(process: ProcessSnapshot, host_memory_total: int | None) -> str:
    if process.memory_util_percent is not None:
        return format_percent_value(process.memory_util_percent)
    if process.host_memory_bytes is None or not host_memory_total:
        return "N/A"
    return format_percent_value(process.host_memory_bytes / host_memory_total * 100)
