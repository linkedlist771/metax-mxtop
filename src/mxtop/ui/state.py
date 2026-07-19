from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math

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

DESCENDING_SORTS = frozenset(
    {
        ProcessSort.GPU_MEMORY,
        ProcessSort.GPU_UTIL,
        ProcessSort.GPU_MEMORY_BANDWIDTH,
        ProcessSort.CPU,
        ProcessSort.HOST_MEMORY,
        ProcessSort.TIME,
    }
)

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
    text_filter: str = ""
    filter_editing: bool = False
    pending_sort_key: bool = False
    active_screen: ScreenMode = ScreenMode.MAIN
    previous_screen: ScreenMode = ScreenMode.MAIN
    screen_history: list[ScreenMode] = field(default_factory=list)
    screen_view_history: list[tuple[int, int, int, bool, tuple[str, ...]]] = field(
        default_factory=list,
    )
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
        if not self.screen_history and self.active_screen != ScreenMode.MAIN:
            if self.previous_screen not in {self.active_screen, screen}:
                self.screen_history.append(self.previous_screen)
                self.screen_view_history.append(self._empty_screen_view())
        self.screen_history.append(self.active_screen)
        self.screen_view_history.append(self._screen_view())
        self.previous_screen = self.active_screen
        self._activate_screen(screen)

    def return_to_previous_screen(self) -> None:
        departed = self.active_screen
        target = self.screen_history.pop() if self.screen_history else self.previous_screen
        target_view = (
            self.screen_view_history.pop()
            if self.screen_view_history
            else self._empty_screen_view()
        )
        self.previous_screen = self.screen_history[-1] if self.screen_history else departed
        self._activate_screen(target, reset_view=False)
        self._restore_screen_view(target_view)

    def return_to_main_screen(self) -> None:
        departed = self.active_screen
        self.screen_history.clear()
        self.screen_view_history.clear()
        self.previous_screen = departed
        self._activate_screen(ScreenMode.MAIN)

    def _activate_screen(self, screen: ScreenMode, *, reset_view: bool = True) -> None:
        self.active_screen = screen
        if reset_view:
            self._reset_screen_view()
        self.pending_sort_key = False

    def _reset_screen_view(self) -> None:
        self.screen_scroll_offset = 0
        self.screen_horizontal_offset = 0
        self.screen_selected_index = 0
        self.screen_selection_active = False
        self.screen_selection_ids = ()

    def _screen_view(self) -> tuple[int, int, int, bool, tuple[str, ...]]:
        return (
            self.screen_scroll_offset,
            self.screen_horizontal_offset,
            self.screen_selected_index,
            self.screen_selection_active,
            self.screen_selection_ids,
        )

    @staticmethod
    def _empty_screen_view() -> tuple[int, int, int, bool, tuple[str, ...]]:
        return (0, 0, 0, False, ())

    def _restore_screen_view(
        self,
        view: tuple[int, int, int, bool, tuple[str, ...]],
    ) -> None:
        (
            self.screen_scroll_offset,
            self.screen_horizontal_offset,
            self.screen_selected_index,
            self.screen_selection_active,
            self.screen_selection_ids,
        ) = view

    def clear_selection(self) -> None:
        self.selected_key = None
        self.selected_index = 0
        self.selected_visible = False
        self.tagged_pids.clear()
        self.tagged_processes.clear()
        self.follow_selection = False


def _numeric_sort_value(value: float | int | None) -> tuple[bool, float]:
    # nvitop's NA sentinel compares greater than every numeric value. Keeping
    # that property preserves its ordering in both ascending and descending
    # modes without mixing incomparable Python types.
    if value is None:
        return (True, 0.0)
    number = float(value)
    if not math.isfinite(number):
        return (True, 0.0)
    return (False, number)


def process_sort_key(sort: ProcessSort, process: ProcessSnapshot) -> tuple[object, ...]:
    memory = _numeric_sort_value(process.gpu_memory_bytes)
    gpu_util = _numeric_sort_value(process.gpu_util_percent)
    gpu_memory_bandwidth = _numeric_sort_value(
        process.gpu_memory_bandwidth_util_percent
    )
    cpu = _numeric_sort_value(process.cpu_percent)
    host_memory = _numeric_sort_value(process.memory_util_percent)
    runtime = _numeric_sort_value(process.runtime_seconds)
    command = process.command or process.name
    if sort == ProcessSort.PID:
        return (process.pid, process.gpu_index)
    if sort == ProcessSort.USER:
        return (process.user or "N/A", process.pid, process.gpu_index)
    if sort == ProcessSort.GPU_MEMORY:
        return (memory, gpu_util, cpu, process.pid, process.gpu_index)
    if sort == ProcessSort.GPU_UTIL:
        return (gpu_util, memory, cpu, process.pid, process.gpu_index)
    if sort == ProcessSort.GPU_MEMORY_BANDWIDTH:
        return (gpu_memory_bandwidth, memory, cpu, process.pid, process.gpu_index)
    if sort == ProcessSort.CPU:
        return (cpu, host_memory, process.pid, process.gpu_index)
    if sort == ProcessSort.HOST_MEMORY:
        return (host_memory, cpu, process.pid, process.gpu_index)
    if sort == ProcessSort.TIME:
        return (runtime, process.pid, process.gpu_index)
    if sort == ProcessSort.COMMAND:
        return (command, process.pid, process.gpu_index)
    return (process.gpu_index, process.user or "N/A", process.pid)


def sort_is_descending(sort: ProcessSort, reverse: bool = False) -> bool:
    return (sort in DESCENDING_SORTS) != reverse


def sort_processes(
    processes: list[ProcessSnapshot],
    sort: ProcessSort,
    reverse: bool = False,
) -> list[ProcessSnapshot]:
    return sorted(
        processes,
        key=lambda process: process_sort_key(sort, process),
        reverse=sort_is_descending(sort, reverse),
    )


def process_matches_filter(process: ProcessSnapshot, text_filter: str) -> bool:
    """Case-insensitive substring match on command, name, user, and PID."""

    needle = text_filter.strip().lower()
    if not needle:
        return True
    haystacks = (
        process.command or "",
        process.name or "",
        process.user or "",
        str(process.pid),
    )
    return any(needle in value.lower() for value in haystacks)


def filter_processes_by_text(
    processes: list[ProcessSnapshot], text_filter: str
) -> list[ProcessSnapshot]:
    if not text_filter.strip():
        return processes
    return [
        process
        for process in processes
        if process_matches_filter(process, text_filter)
    ]


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
