from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os

from mxtop.formatting import ellipsize, format_bar, format_bytes, format_duration, format_float, format_mib, format_percent
from mxtop.models import FrameSnapshot, ProcessSnapshot
from mxtop.ui.help import HELP_LINES
from mxtop.ui.state import LayoutMode, UiState, keep_selection, sort_processes

DEVICE_WIDE_MIN_WIDTH = 110
PROCESS_WIDE_MIN_WIDTH = 116


@dataclass(slots=True)
class RenderedScreen:
    lines: list[str]
    process_start: int
    process_count: int


def render_title(frame: FrameSnapshot, width: int, error: str | None = None) -> str:
    timestamp = datetime.fromtimestamp(frame.timestamp).strftime("%Y-%m-%d %H:%M:%S")
    status = f"MXTOP {timestamp}  backend={frame.backend}  GPUs={len(frame.devices)}  procs={len(frame.processes)}"
    if error:
        status += f"  error={error}"
    return ellipsize(status, width)


def render_device_panel(frame: FrameSnapshot, width: int, layout: LayoutMode = LayoutMode.AUTO) -> list[str]:
    compact = layout == LayoutMode.COMPACT or (layout == LayoutMode.AUTO and width < DEVICE_WIDE_MIN_WIDTH)
    lines = ["Devices"]
    if compact:
        lines.extend(
            [
                "GPU  NAME        BDF/UUID        TEMP   PWR   GPU%  MEM%  MEMORY",
                "---  ----------  --------------  -----  ----  ----  ----  ----------------",
            ]
        )
    else:
        lines.extend(
            [
                "GPU  NAME        BDF/UUID        TEMP   POWER  UTIL                 MEM                  MEMORY",
                "---  ----------  --------------  -----  -----  -------------------  -------------------  ----------------",
            ]
        )
    if not frame.devices:
        lines.append("no MetaX devices found")
        return lines

    for device in frame.devices:
        identity = device.bdf or device.uuid or ""
        memory = f"{format_bytes(device.memory_used_bytes)}/{format_bytes(device.memory_total_bytes)}"
        prefix = (
            f"{device.index:<3}  {ellipsize(device.name, 10):<10}  "
            f"{ellipsize(identity, 14):<14}  "
            f"{format_float(device.temperature_c, 'C'):>5}  "
        )
        if compact:
            lines.append(
                prefix
                + f"{format_float(device.power_w, 'W'):>4}  "
                + f"{format_percent(device.gpu_util_percent):>4}  "
                + f"{format_percent(device.memory_util_percent):>4}  "
                + memory
            )
        else:
            lines.append(
                prefix
                + f"{format_float(device.power_w, 'W'):>5}  "
                + f"{format_percent(device.gpu_util_percent):>4} [{format_bar(device.gpu_util_percent)}]  "
                + f"{format_percent(device.memory_util_percent):>4} [{format_bar(device.memory_util_percent)}]  "
                + memory
            )
    return lines


def render_host_panel(width: int) -> list[str]:
    try:
        import psutil
    except ModuleNotFoundError:
        return ["Host", "CPU N/A  MEM N/A  LOAD " + _load_average_text()]

    cpu = psutil.cpu_percent(interval=None)
    memory = psutil.virtual_memory()
    return [
        "Host",
        ellipsize(
            f"CPU {format_percent(cpu)}  MEM {format_bytes(int(memory.used))}/{format_bytes(int(memory.total))} ({format_percent(float(memory.percent))})  LOAD {_load_average_text()}",
            width,
        ),
    ]


def _load_average_text() -> str:
    try:
        load1, load5, load15 = os.getloadavg()
    except OSError:
        return "N/A"
    return f"{load1:.2f} {load5:.2f} {load15:.2f}"


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
    processes = visible_processes(frame, state)
    compact = state.layout == LayoutMode.COMPACT or (state.layout == LayoutMode.AUTO and width < PROCESS_WIDE_MIN_WIDTH)
    title = f"Processes  sort={state.process_sort.value}{' desc' if state.reverse_sort else ''}"
    lines = [title]
    if compact:
        lines.extend(
            [
                "GPU  PID       USER          GPU-MEM   CPU%      TIME  COMMAND",
                "---  --------  ------------  --------  ----  --------  ----------------",
            ]
        )
        command_column = 61
    else:
        lines.extend(
            [
                "GPU  PID       USER          GPU-MEM   GPU%  CPU%      TIME  HOST-MEM  COMMAND",
                "---  --------  ------------  --------  ----  ----  --------  --------  ----------------",
            ]
        )
        command_column = 77

    available_rows = None if height is None else max(1, height - len(lines))
    if not processes:
        lines.append("no GPU processes found")
        return lines, len(lines) - 1, 0

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
    for process in shown:
        selected = process.selection_key == state.selected_key
        command = (process.command or process.name)[state.command_offset :]
        marker = ">" if selected else " "
        if compact:
            row = (
                f"{marker}{process.gpu_index:<2}  {process.pid:<8}  "
                f"{ellipsize(process.user, 12):<12}  "
                f"{format_mib(process.gpu_memory_bytes):>8}  "
                f"{format_percent(process.cpu_percent):>4}  "
                f"{format_duration(process.runtime_seconds):>8}  "
                f"{ellipsize(command, max(10, width - command_column))}"
            )
        else:
            row = (
                f"{marker}{process.gpu_index:<2}  {process.pid:<8}  "
                f"{ellipsize(process.user, 12):<12}  "
                f"{format_mib(process.gpu_memory_bytes):>8}  "
                f"{format_percent(process.gpu_util_percent):>4}  "
                f"{format_percent(process.cpu_percent):>4}  "
                f"{format_duration(process.runtime_seconds):>8}  "
                f"{format_bytes(process.host_memory_bytes):>8}  "
                f"{ellipsize(command, max(10, width - command_column))}"
            )
        lines.append(row)
    return lines, process_start, len(shown)


def render_footer(frame: FrameSnapshot, state: UiState, interval: float, error: str | None, width: int) -> str:
    footer = f"q quit  h help  r refresh  a/f/c layout  sort {state.process_sort.value}  refresh {interval:.1f}s"
    if state.command_offset:
        footer += f"  cmd+{state.command_offset}"
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

    lines: list[str] = [render_title(frame, width, error), ""]
    lines.extend(render_device_panel(frame, width, state.layout))
    lines.append("")
    if state.layout != LayoutMode.COMPACT:
        lines.extend(render_host_panel(width))
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
