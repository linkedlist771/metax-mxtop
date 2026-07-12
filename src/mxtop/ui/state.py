from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from mxtop._compat import DATACLASS_SLOTS
from mxtop.models import ProcessSnapshot


class LayoutMode(str, Enum):
    AUTO = "auto"
    FULL = "full"
    COMPACT = "compact"


class ScreenMode(str, Enum):
    MAIN = "main"
    ENVIRON = "environ"
    TREE = "tree"
    METRICS = "metrics"
    HELP = "help"


class ProcessSignal(str, Enum):
    TERMINATE = "terminate"
    KILL = "kill"
    INTERRUPT = "interrupt"


class ProcessSort(str, Enum):
    DEFAULT = "default"
    PID = "pid"
    USER = "user"
    GPU_MEMORY = "gpu_memory"
    GPU_UTIL = "gpu_util"
    GPU_MEMORY_BANDWIDTH = "gpu_memory_bandwidth"
    CPU = "cpu"
    HOST_MEMORY = "host_memory"
    TIME = "time"
    # Kept as a MetaX extension, but excluded from nvitop's sort cycle.
    COMMAND = "command"


SORT_ORDER = [
    ProcessSort.DEFAULT,
    ProcessSort.PID,
    ProcessSort.USER,
    ProcessSort.GPU_MEMORY,
    ProcessSort.GPU_UTIL,
    ProcessSort.GPU_MEMORY_BANDWIDTH,
    ProcessSort.CPU,
    ProcessSort.HOST_MEMORY,
    ProcessSort.TIME,
]

# nvitop's direct order bindings. Uppercase uses the same field in reverse.
DIRECT_SORT_KEYS = {
    "n": ProcessSort.DEFAULT,
    "p": ProcessSort.PID,
    "u": ProcessSort.USER,
    "g": ProcessSort.GPU_MEMORY,
    "s": ProcessSort.GPU_UTIL,
    "b": ProcessSort.GPU_MEMORY_BANDWIDTH,
    "c": ProcessSort.CPU,
    "m": ProcessSort.HOST_MEMORY,
    "t": ProcessSort.TIME,
}


@dataclass(**DATACLASS_SLOTS)
class UiState:
    layout: LayoutMode = LayoutMode.AUTO
    selected_key: str | None = None
    selected_index: int = 0
    selected_visible: bool = False
    scroll_offset: int = 0
    main_screen_offset: int = 0
    command_offset: int = 0
    process_sort: ProcessSort = ProcessSort.DEFAULT
    reverse_sort: bool = False
    show_help: bool = False
    pending_sort_key: bool = False
    active_screen: ScreenMode = ScreenMode.MAIN
    previous_screen: ScreenMode = ScreenMode.MAIN
    screen_scroll_offset: int = 0
    screen_horizontal_offset: int = 0
    screen_selected_index: int = 0
    screen_selection_active: bool = False
    screen_selection_ids: tuple[str, ...] = ()
    screen_target_pid: int | None = None
    screen_target_create_time: float | None = None
    screen_target_user: str | None = None
    screen_target_command: str | None = None
    tagged_pids: set[int] = field(default_factory=set)
    tagged_processes: dict[int, tuple[float | None, str | None]] = field(default_factory=dict)
    pending_signal: ProcessSignal | None = None
    pending_signal_targets: tuple[tuple[int, float | None], ...] = ()
    pending_signal_option: int = 0
    status_message: str | None = None
    readonly: bool = False
    no_unicode: bool = False
    follow_selection: bool = True

    def switch_screen(self, screen: ScreenMode) -> None:
        if screen == self.active_screen:
            return
        self.previous_screen = self.active_screen
        self.active_screen = screen
        self.show_help = screen == ScreenMode.HELP
        self.screen_scroll_offset = 0
        self.screen_horizontal_offset = 0
        self.screen_selected_index = 0
        self.screen_selection_active = False
        self.screen_selection_ids = ()
        self.pending_sort_key = False

    def return_to_previous_screen(self) -> None:
        target = self.previous_screen
        self.previous_screen = self.active_screen
        self.active_screen = target
        self.show_help = target == ScreenMode.HELP
        self.screen_scroll_offset = 0
        self.screen_horizontal_offset = 0
        self.screen_selected_index = 0
        self.screen_selection_active = False
        self.screen_selection_ids = ()

    def clear_selection(self) -> None:
        self.selected_key = None
        self.selected_index = 0
        self.selected_visible = False
        self.tagged_pids.clear()
        self.tagged_processes.clear()
        self.follow_selection = False


def process_sort_key(sort: ProcessSort, process: ProcessSnapshot) -> tuple[object, ...]:
    memory = process.gpu_memory_bytes or 0
    gpu_util = process.gpu_util_percent if process.gpu_util_percent is not None else -1.0
    gpu_memory_bandwidth = (
        process.gpu_memory_bandwidth_util_percent
        if process.gpu_memory_bandwidth_util_percent is not None
        else -1.0
    )
    cpu = process.cpu_percent if process.cpu_percent is not None else -1.0
    host_memory = process.host_memory_bytes or 0
    runtime = process.runtime_seconds if process.runtime_seconds is not None else -1.0
    command = process.command or process.name
    if sort == ProcessSort.PID:
        return (process.pid, process.gpu_index)
    if sort == ProcessSort.USER:
        return (process.user or "", process.pid, process.gpu_index)
    if sort == ProcessSort.GPU_MEMORY:
        return (-memory, -gpu_util, -cpu, process.pid, process.gpu_index)
    if sort == ProcessSort.GPU_UTIL:
        return (-gpu_util, -memory, -cpu, process.pid, process.gpu_index)
    if sort == ProcessSort.GPU_MEMORY_BANDWIDTH:
        return (-gpu_memory_bandwidth, -memory, -cpu, process.pid, process.gpu_index)
    if sort == ProcessSort.CPU:
        return (-cpu, -host_memory, process.pid, process.gpu_index)
    if sort == ProcessSort.HOST_MEMORY:
        return (-host_memory, -cpu, process.pid, process.gpu_index)
    if sort == ProcessSort.TIME:
        return (-runtime, process.pid, process.gpu_index)
    if sort == ProcessSort.COMMAND:
        return (command, process.pid, process.gpu_index)
    return (process.gpu_index, process.user or "", process.pid)


def sort_processes(
    processes: list[ProcessSnapshot],
    sort: ProcessSort,
    reverse: bool = False,
) -> list[ProcessSnapshot]:
    return sorted(processes, key=lambda process: process_sort_key(sort, process), reverse=reverse)


def next_sort(sort: ProcessSort, step: int) -> ProcessSort:
    index = SORT_ORDER.index(sort)
    return SORT_ORDER[(index + step) % len(SORT_ORDER)]


def keep_selection(state: UiState, processes: list[ProcessSnapshot]) -> None:
    if not processes:
        state.selected_key = None
        state.selected_index = 0
        state.scroll_offset = 0
        state.tagged_pids.clear()
        state.tagged_processes.clear()
        return

    live_pids = {process.pid for process in processes}
    state.tagged_pids.intersection_update(live_pids)
    state.tagged_processes = {
        pid: identity
        for pid, identity in state.tagged_processes.items()
        if pid in state.tagged_pids
    }
    if state.selected_key is None:
        state.selected_index = max(0, min(state.selected_index, len(processes) - 1))
        return

    keys = [process.selection_key for process in processes]
    if state.selected_key in keys:
        state.selected_index = keys.index(state.selected_key)
    else:
        state.selected_key = None
        state.selected_index = max(0, min(state.selected_index, len(processes) - 1))
        state.follow_selection = False
