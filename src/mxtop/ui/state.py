from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from mxtop.models import ProcessSnapshot


class LayoutMode(str, Enum):
    AUTO = "auto"
    FULL = "full"
    COMPACT = "compact"


class ProcessSort(str, Enum):
    DEFAULT = "default"
    PID = "pid"
    USER = "user"
    GPU_MEMORY = "gpu_memory"
    GPU_UTIL = "gpu_util"
    CPU = "cpu"
    HOST_MEMORY = "host_memory"
    TIME = "time"
    COMMAND = "command"


SORT_ORDER = [
    ProcessSort.DEFAULT,
    ProcessSort.GPU_MEMORY,
    ProcessSort.GPU_UTIL,
    ProcessSort.CPU,
    ProcessSort.HOST_MEMORY,
    ProcessSort.TIME,
    ProcessSort.PID,
    ProcessSort.USER,
    ProcessSort.COMMAND,
]
DIRECT_SORT_KEYS = {
    "g": ProcessSort.DEFAULT,
    "m": ProcessSort.GPU_MEMORY,
    "u": ProcessSort.GPU_UTIL,
    "c": ProcessSort.CPU,
    "h": ProcessSort.HOST_MEMORY,
    "t": ProcessSort.TIME,
    "p": ProcessSort.PID,
    "U": ProcessSort.USER,
    "C": ProcessSort.COMMAND,
}


@dataclass(slots=True)
class UiState:
    layout: LayoutMode = LayoutMode.AUTO
    selected_key: str | None = None
    selected_index: int = 0
    scroll_offset: int = 0
    command_offset: int = 0
    process_sort: ProcessSort = ProcessSort.DEFAULT
    reverse_sort: bool = False
    show_help: bool = False
    pending_sort_key: bool = False


def process_sort_key(sort: ProcessSort, process: ProcessSnapshot) -> tuple[object, ...]:
    memory = process.gpu_memory_bytes or 0
    gpu_util = process.gpu_util_percent if process.gpu_util_percent is not None else -1.0
    cpu = process.cpu_percent if process.cpu_percent is not None else -1.0
    host_memory = process.host_memory_bytes or 0
    runtime = process.runtime_seconds if process.runtime_seconds is not None else -1.0
    command = process.command or process.name
    if sort == ProcessSort.PID:
        return (process.pid, process.gpu_index)
    if sort == ProcessSort.USER:
        return (process.user or "", process.gpu_index, process.pid)
    if sort == ProcessSort.GPU_MEMORY:
        return (-memory, process.gpu_index, process.pid)
    if sort == ProcessSort.GPU_UTIL:
        return (-gpu_util, process.gpu_index, process.pid)
    if sort == ProcessSort.CPU:
        return (-cpu, process.gpu_index, process.pid)
    if sort == ProcessSort.HOST_MEMORY:
        return (-host_memory, process.gpu_index, process.pid)
    if sort == ProcessSort.TIME:
        return (-runtime, process.gpu_index, process.pid)
    if sort == ProcessSort.COMMAND:
        return (command, process.gpu_index, process.pid)
    return (process.gpu_index, -memory, process.pid)


def sort_processes(processes: list[ProcessSnapshot], sort: ProcessSort, reverse: bool = False) -> list[ProcessSnapshot]:
    return sorted(processes, key=lambda process: process_sort_key(sort, process), reverse=reverse)


def next_sort(sort: ProcessSort, step: int) -> ProcessSort:
    index = SORT_ORDER.index(sort)
    return SORT_ORDER[(index + step) % len(SORT_ORDER)]


def keep_selection(state: UiState, processes: list[ProcessSnapshot]) -> None:
    if not processes:
        state.selected_key = None
        state.selected_index = 0
        state.scroll_offset = 0
        return
    keys = [process.selection_key for process in processes]
    if state.selected_key in keys:
        state.selected_index = keys.index(state.selected_key)
    else:
        state.selected_index = max(0, min(state.selected_index, len(processes) - 1))
        state.selected_key = processes[state.selected_index].selection_key
