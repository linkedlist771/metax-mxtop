from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import getpass
import os
import socket

from mxtop import __version__
from mxtop.formatting import (
    ellipsize,
    format_bar,
    format_compact_bytes,
    format_duration,
    format_mib,
    format_percent,
    format_percent_precise,
    format_percent_value,
)
from mxtop.models import DeviceSnapshot, FrameSnapshot, ProcessSnapshot
from mxtop.ui.help import HELP_LINES
from mxtop.ui.state import LayoutMode, UiState, keep_selection, sort_processes

CORE_INNER = 77
CORE_WIDTH = 79
LEFT_INNER = 31
MID_INNER = 22
RIGHT_INNER = 22
BAR_MIN_WIDTH = 100
HOST_GRAPH_MIN_WIDTH = 100
MIN_SCREEN_WIDTH = 79
PROCESS_FIXED_PREFIX = 45


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


def render_device_panel(
    frame: FrameSnapshot,
    width: int,
    layout: LayoutMode = LayoutMode.AUTO,
    *,
    compact: bool | None = None,
) -> list[str]:
    if compact is None:
        compact = layout == LayoutMode.COMPACT
    draw_bars = width >= BAR_MIN_WIDTH and not compact
    right_width = max(0, width - CORE_WIDTH) if draw_bars else 0
    driver_version = _driver_version(frame)
    lines: list[str] = []
    lines.append(_top_border(right_width))
    lines.append(_version_line(driver_version, right_width))
    lines.append(_header_top_divider(right_width))
    if compact:
        lines.append(_header_line_compact(right_width))
    else:
        lines.append(_header_line_one(right_width))
        lines.append(_header_line_two(right_width))
    lines.append(_header_data_divider(right_width, draw_bars))

    if not frame.devices:
        empty = _core_line("  No visible devices found")
        if right_width:
            empty += " " * (right_width - 1) + "│"
        lines.append(empty)
        lines.append(_bottom_border(right_width))
        return lines

    for index, device in enumerate(frame.devices):
        if index > 0:
            lines.append(_row_divider(right_width, draw_bars))
        if compact:
            row = _device_row_compact(device)
            if right_width:
                row += " " * (right_width - 1) + "│"
            lines.append(row)
        else:
            row_one = _device_row_one(device)
            row_two = _device_row_two(device)
            if draw_bars and right_width >= 3:
                bar_top, bar_bot = _device_bars(device, right_width)
                row_one += bar_top
                row_two += bar_bot
            elif right_width:
                row_one += " " * (right_width - 1) + "│"
                row_two += " " * (right_width - 1) + "│"
            lines.append(row_one)
            lines.append(row_two)
    lines.append(_bottom_border(right_width))
    return lines


def render_host_panel(frame: FrameSnapshot, width: int) -> list[str]:
    draw_bars = width >= HOST_GRAPH_MIN_WIDTH
    right_width = max(0, width - CORE_WIDTH) if draw_bars else 0
    cpu, memory_used_text, memory_pct, swap_used_text, swap_pct = _host_metrics()
    gpu_mem = _average_percent(d.memory_util_percent for d in frame.devices)
    gpu_util = _average_percent(d.gpu_util_percent for d in frame.devices)
    cpu_suffix = _cpu_bar(cpu) if draw_bars else ""
    mem_suffix = _mem_bar(memory_pct) if draw_bars else ""
    lines: list[str] = []
    lines.append(_host_top_border(right_width))
    lines.append(_host_data_line(f" Load Average:  {_load_average_text()}", _gpu_metric_text("GPU MEM", gpu_mem), right_width))
    lines.append(_host_data_line(f" CPU: {format_percent(cpu)}{cpu_suffix}", "", right_width))
    lines.append(_host_data_line("", "", right_width))
    lines.append(_host_data_line("", "", right_width))
    lines.append(_host_data_line("", "", right_width))
    lines.append(_host_time_axis(right_width))
    lines.append(_host_data_line("", "", right_width))
    lines.append(_host_data_line("", "", right_width))
    lines.append(_host_data_line(f" MEM: {memory_used_text} ({format_percent(memory_pct)}){mem_suffix}", "", right_width))
    lines.append(_host_data_line(f" SWP: {swap_used_text} ({format_percent(swap_pct)})", _gpu_metric_text("GPU UTL", gpu_util), right_width))
    lines.append(_host_bottom_border(right_width))
    return lines


def _gpu_metric_text(label: str, value: float | None) -> str:
    return f" {label}: {format_percent_precise(value)}"


def _cpu_bar(value: float | None) -> str:
    bar = format_bar(value, width=20)
    return f"  {bar}"


def _mem_bar(value: float | None) -> str:
    bar = format_bar(value, width=20)
    return f"  {bar}"


def _load_average_text() -> str:
    try:
        load1, load5, load15 = os.getloadavg()
    except OSError:
        return "N/A  N/A  N/A"
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
    inner = max(MIN_SCREEN_WIDTH - 2, width - 2)
    user_host = _user_host()
    lines = [_top_border_simple(inner), _box_content(_process_title(inner, user_host), width)]
    lines.append(_box_content(_process_header(inner), width))
    lines.append(_middle_border(inner))
    available_rows = None if height is None else max(1, height - len(lines) - 1)

    if not processes:
        lines.append(_box_content("  No running processes found", width))
        lines.append(_bottom_border_simple(inner))
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
    prev_gpu_index: int | None = None
    for process in shown:
        if prev_gpu_index is not None and prev_gpu_index != process.gpu_index:
            lines.append("├" + "─" * inner + "┤")
        lines.append(_box_content(_process_row(process, state, inner, host_memory_total), width))
        prev_gpu_index = process.gpu_index
    lines.append(_bottom_border_simple(inner))
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
    show_host = state.layout != LayoutMode.COMPACT
    compact_devices = _should_compact_devices(frame, state.layout, height, show_host)
    if compact_devices and state.layout == LayoutMode.AUTO:
        show_host = show_host and _has_room_for_host(frame, height)
    lines.extend(render_device_panel(frame, width, state.layout, compact=compact_devices))
    if show_host:
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


def _should_compact_devices(frame: FrameSnapshot, layout: LayoutMode, height: int | None, show_host: bool) -> bool:
    if layout == LayoutMode.COMPACT:
        return True
    if layout == LayoutMode.FULL:
        return False
    device_count = len(frame.devices) or 1
    if device_count >= 8 and height is None:
        return device_count >= 12
    if height is None:
        return False
    # Title (1) + version (1) + header_top (1) + headers (2) + divider (1) + bottom (1)
    # Plus host panel (12 if shown) plus blank (1) plus process panel min (~6).
    header_overhead = 7
    host_overhead = 13 if show_host else 0
    process_min = 6
    available_for_devices = height - header_overhead - host_overhead - process_min
    full_rows_needed = device_count * 3 - 1  # 2 rows + 1 divider between, minus the last divider
    if full_rows_needed <= available_for_devices:
        return False
    compact_rows_needed = device_count * 2 - 1
    return compact_rows_needed <= max(available_for_devices, 1) or device_count >= 8


def _has_room_for_host(frame: FrameSnapshot, height: int | None) -> bool:
    if height is None:
        return True
    device_count = len(frame.devices) or 1
    header_overhead = 6  # title + version + header_top + 1 header line + divider + bottom
    compact_device_rows = device_count * 2 - 1
    process_min = 6
    host_overhead = 13
    return height >= header_overhead + compact_device_rows + host_overhead + process_min


def _core_line(content: str) -> str:
    return "│" + ellipsize(content, CORE_INNER).ljust(CORE_INNER) + "│"


def _top_border(right_width: int) -> str:
    del right_width
    return "╒" + "═" * CORE_INNER + "╕"


def _version_line(driver_version: str, right_width: int) -> str:
    del right_width
    parts = [
        f"MXTOP {__version__}",
        f"Driver Version: {driver_version}",
        f"MX Driver Version: {driver_version}",
    ]
    total = sum(len(p) for p in parts)
    seps = " " * max(2, (75 - total) // 2)
    content = seps.join(parts).ljust(75)
    return f"│ {content} │"


def _header_top_divider(right_width: int) -> str:
    del right_width
    return "├" + "─" * LEFT_INNER + "┬" + "─" * MID_INNER + "┬" + "─" * RIGHT_INNER + "┤"


def _header_line_one(right_width: int) -> str:
    del right_width
    return "│ GPU  Name        Persistence-M│ Bus-Id        Disp.A │ Volatile Uncorr. ECC │"


def _header_line_two(right_width: int) -> str:
    del right_width
    return "│ Fan  Temp  Perf  Pwr:Usage/Cap│         Memory-Usage │ GPU-Util  Compute M. │"


def _header_line_compact(right_width: int) -> str:
    del right_width
    return "│ GPU Fan Temp Perf Pwr:Usg/Cap │         Memory-Usage │ GPU-Util  Compute M. │"


def _header_data_divider(right_width: int, draw_bars: bool) -> str:
    base = "╞" + "═" * LEFT_INNER + "╪" + "═" * MID_INNER + "╪" + "═" * RIGHT_INNER + "╡"
    if right_width:
        connector = "╪" if draw_bars else "╡"
        base = base[:-1] + connector + "═" * (right_width - 1) + "╕"
    return base


def _row_divider(right_width: int, draw_bars: bool) -> str:
    base = "├" + "─" * LEFT_INNER + "┼" + "─" * MID_INNER + "┼" + "─" * RIGHT_INNER + "┤"
    if right_width:
        connector = "┼" if draw_bars else "┤"
        base = base[:-1] + connector + "─" * (right_width - 1) + "┤"
    return base


def _bottom_border(right_width: int) -> str:
    base = "╘" + "═" * LEFT_INNER + "╧" + "═" * MID_INNER + "╧" + "═" * RIGHT_INNER + "╛"
    if right_width:
        base = base[:-1] + "╧" + "═" * (right_width - 1) + "╛"
    return base


def _device_row_one(device: DeviceSnapshot) -> str:
    name = ellipsize(device.name, 19, marker="..").ljust(19)
    persistence = _on_off(device.persistence_mode)
    bdf = ellipsize(device.bdf or device.uuid or "N/A", 16, marker="..").ljust(16)
    disp = _on_off(device.display_active) if device.display_active is not None else "Off"
    ecc = _ecc_text(device.ecc_errors)
    left = f" {device.index:>3}  {name} {persistence:>4} "
    mid = f" {bdf} {disp:>3} "
    right = f" {ecc:>20} "
    return f"│{left}│{mid}│{right}│"


def _device_row_compact(device: DeviceSnapshot) -> str:
    fan = _fan_text(device.fan_percent)
    temp = _temp_text(device.temperature_c)
    perf = (device.performance_state or "N/A")[:3]
    power = _power_status(device.power_w, device.power_limit_w)
    memory = f"{format_compact_bytes(device.memory_used_bytes)} / {format_compact_bytes(device.memory_total_bytes)}"
    util = format_percent(device.gpu_util_percent)
    compute = (device.compute_mode or "Default")[:11]
    left = f" {device.index:>3} {fan:>3} {temp:>4} {perf:<3}{power:>13} "
    mid = f" {memory:>20} "
    right = f" {util:>7}  {compute:>11} "
    return f"│{left}│{mid}│{right}│"


def _device_row_two(device: DeviceSnapshot) -> str:
    fan = _fan_text(device.fan_percent)
    temp = _temp_text(device.temperature_c)
    perf = (device.performance_state or "N/A")[:4]
    power = _power_status(device.power_w, device.power_limit_w)
    memory = f"{format_compact_bytes(device.memory_used_bytes)} / {format_compact_bytes(device.memory_total_bytes)}"
    util = format_percent(device.gpu_util_percent)
    compute = (device.compute_mode or "Default")[:11]
    left = f" {fan:>3}  {temp:>4}  {perf:^4} {power:>13} "
    mid = f" {memory:>20} "
    right = f" {util:>7}  {compute:>11} "
    return f"│{left}│{mid}│{right}│"


def _device_bars(device: DeviceSnapshot, right_width: int) -> tuple[str, str]:
    inner = right_width - 1
    if inner <= 0:
        return "", ""
    if inner >= 44:
        left = (inner - 3) // 2
        right = inner - 3 - left
        top = " " + _named_bar("MEM", device.memory_util_percent, left) + " │ " + _named_bar("MBW", device.memory_bandwidth_util_percent, right) + " │"
        bot = " " + _named_bar("UTL", device.gpu_util_percent, left) + " │ " + _named_bar("PWR", _power_util(device), right) + " │"
        return top, bot
    top = " " + _named_bar("MEM", device.memory_util_percent, inner - 2) + " │"
    bot = " " + _named_bar("UTL", device.gpu_util_percent, inner - 2) + " │"
    return top, bot


def _power_util(device: DeviceSnapshot) -> float | None:
    if device.power_w is None or not device.power_limit_w:
        return None
    return min(100.0, max(0.0, device.power_w / device.power_limit_w * 100))


def _named_bar(label: str, value: float | None, width: int) -> str:
    if width <= 0:
        return ""
    suffix = f" {format_percent_precise(value)}"
    label_text = f"{label}: "
    bar_width = max(1, width - len(label_text) - len(suffix))
    bar = format_bar(value, width=bar_width)
    return (label_text + bar + suffix)[:width].ljust(width)


def _host_top_border(right_width: int) -> str:
    base = "╞" + "═" * LEFT_INNER + "╧" + "═" * MID_INNER + "╧" + "═" * RIGHT_INNER + "╡"
    if right_width:
        base = base[:-1] + "╪" + "═" * (right_width - 1) + "╡"
    return base


def _host_data_line(left: str, right: str, right_width: int) -> str:
    line = "│" + ellipsize(left, CORE_INNER).ljust(CORE_INNER) + "│"
    if right_width:
        inner = max(0, right_width - 1)
        line += ellipsize(right, inner).ljust(inner) + "│"
    return line


def _host_time_axis(right_width: int) -> str:
    axis = "├────────────╴120s├─────────────────────────╴60s├──────────╴30s├──────────────┤"
    if right_width:
        inner = right_width - 1
        right_axis = _time_axis_right(inner)
        axis = axis[:-1] + "┼" + right_axis + "┤"
    return axis


def _time_axis_right(width: int) -> str:
    if width <= 0:
        return ""
    line = list("─" * width)
    labels = [(20, "╴30s├"), (35, "╴60s├"), (66, "╴120s├")]
    for offset, label in labels:
        if offset > width:
            break
        start = width - offset
        line[start : start + len(label)] = list(label)
    return "".join(line)[:width]


def _host_bottom_border(right_width: int) -> str:
    base = "╘" + "═" * CORE_INNER + "╛"
    if right_width:
        base = base[:-1] + "╧" + "═" * (right_width - 1) + "╛"
    return base


def _top_border_simple(inner_width: int) -> str:
    return "╒" + "═" * inner_width + "╕"


def _middle_border(inner_width: int) -> str:
    return "╞" + "═" * inner_width + "╡"


def _bottom_border_simple(inner_width: int) -> str:
    return "╘" + "═" * inner_width + "╛"


def _box_content(text: str, width: int) -> str:
    inner = max(0, width - 2)
    return "│" + ellipsize(text, inner).ljust(inner) + "│"


def _driver_version(frame: FrameSnapshot) -> str:
    for device in frame.devices:
        if device.driver_version:
            return device.driver_version
    return "N/A"


def _on_off(value: str | None) -> str:
    if not value:
        return "Off"
    lowered = value.strip().lower()
    if lowered in {"enabled", "on", "1", "true"}:
        return "On"
    if lowered in {"disabled", "off", "0", "false"}:
        return "Off"
    return value.strip()[:4]


def _ecc_text(value: int | None) -> str:
    if value is None:
        return "N/A"
    return str(value)


def _fan_text(value: float | None) -> str:
    if value is None:
        return "N/A"
    if value >= 100:
        return "MAX"
    return f"{value:.0f}%"


def _temp_text(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.0f}C"


def _power_status(power: float | None, limit: float | None) -> str:
    left = "N/A" if power is None else f"{power:.0f}W"
    right = "N/A" if limit is None else f"{limit:.0f}W"
    return f"{left} / {right}"


def _host_metrics() -> tuple[float | None, str, float | None, str, float | None]:
    try:
        import psutil
    except ModuleNotFoundError:
        return None, "N/A", None, "N/A", None
    cpu = float(psutil.cpu_percent(interval=None))
    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()
    memory_used = format_compact_bytes(int(memory.used))
    swap_used = format_compact_bytes(int(swap.used))
    return cpu, memory_used, float(memory.percent), swap_used, float(swap.percent)


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


def _user_host() -> str:
    user = getpass.getuser()
    host = socket.gethostname().split(".", maxsplit=1)[0]
    return f"{user}@{host}"


def _process_title(width: int, user_host: str) -> str:
    left = " Processes:"
    if width <= len(left) + len(user_host):
        return ellipsize(f"{left} {user_host}", width)
    return f"{left}{' ' * (width - len(left) - len(user_host) - 1)}{user_host} "


def _process_header(width: int) -> str:
    base = " GPU     PID      USER  GPU-MEM %SM %GMBW  %CPU  %MEM    TIME  COMMAND"
    return ellipsize(base, width).ljust(width)


def _process_group_order(processes: list[ProcessSnapshot], state: UiState) -> list[ProcessSnapshot]:
    if state.process_sort.value != "default" or state.reverse_sort:
        return processes
    return sorted(processes, key=lambda process: (process.gpu_index, process.user in {None, "root"}, process.pid))


def _process_row(process: ProcessSnapshot, state: UiState, width: int, host_memory_total: int | None) -> str:
    selected = process.selection_key == state.selected_key
    marker = ">" if selected and state.command_offset else " "
    process_type = (process.process_type or "-")[:1]
    command = (process.command or process.name)[state.command_offset :]
    user_text = ellipsize(process.user, 7, marker="+").rjust(7)
    fixed = (
        f"{marker}{process.gpu_index:>3} {process.pid:>7} {process_type:>1} {user_text} "
        f"{format_mib(process.gpu_memory_bytes):>8} "
        f"{format_percent_value(process.gpu_util_percent):>3} "
        f"{format_percent_value(process.gpu_memory_bandwidth_util_percent):>5}  "
        f"{format_percent_value(process.cpu_percent):>4}  "
        f"{_host_memory_percent(process, host_memory_total):>4}  "
        f"{format_duration(process.runtime_seconds):>7}  "
    )
    command_width = max(0, width - len(fixed))
    return fixed + ellipsize(command, command_width)


def _host_memory_percent(process: ProcessSnapshot, host_memory_total: int | None) -> str:
    if process.memory_util_percent is not None:
        return format_percent_value(process.memory_util_percent)
    if process.host_memory_bytes is None or not host_memory_total:
        return "N/A"
    return format_percent_value(process.host_memory_bytes / host_memory_total * 100)
