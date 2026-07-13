"""Secondary nvitop-style screens backed by MetaX process snapshots.

The functions in this module are intentionally independent of curses.  They
return fixed-width text plus row metadata, which keeps process inspection and
screen navigation deterministic in tests and lets the live TUI add colors and
selection attributes in one place.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
import getpass
import math
from pathlib import Path
import socket
import time

from mxtop import __version__
from mxtop._compat import DATACLASS_SLOTS
from mxtop.formatting import (
    format_compact_bytes,
    format_duration,
    format_mib,
    format_percent_precise,
    format_percent_value,
)
from mxtop.models import PROCESS_CREATE_TIME_TOLERANCE, FrameSnapshot, ProcessSnapshot
from mxtop.ui.history import HistoryGraph
from mxtop.ui.text import cell_ellipsize, cell_ljust, cell_rjust, cell_slice, cell_width


@dataclass(**DATACLASS_SLOTS)
class RenderedView:
    lines: list[str]
    selectable_start: int = 0
    selectable_count: int = 0
    selection_ids: tuple[str, ...] = ()


@dataclass(frozen=True, **DATACLASS_SLOTS)
class HostProcessInfo:
    pid: int
    ppid: int = 0
    user: str = "N/A"
    command: str = "N/A"
    create_time: float | None = None
    num_threads: int | None = None
    cpu_percent: float | None = None
    memory_percent: float | None = None
    runtime_seconds: float | None = None


@dataclass(frozen=True, **DATACLASS_SLOTS)
class ProcessTreeEntry:
    pid: int
    ppid: int
    user: str
    command: str
    device: str
    create_time: float | None
    prefix: str = ""
    num_threads: int | None = None
    cpu_percent: float | None = None
    memory_percent: float | None = None
    runtime_seconds: float | None = None

    @property
    def selection_id(self) -> str:
        stamp = "?" if self.create_time is None else f"{self.create_time:.6f}"
        return f"tree:{self.pid}:{stamp}"


@dataclass(**DATACLASS_SLOTS)
class ProcessMetricsHistory:
    """Rolling history for the four metrics shown by the process detail view."""

    maxlen: int = 4096
    selection_key: str | None = None
    cpu: deque[float | None] = field(init=False)
    host_memory: deque[float | None] = field(init=False)
    gpu_memory: deque[float | None] = field(init=False)
    gpu_utilization: deque[float | None] = field(init=False)
    host_memory_total: int | None = None
    gpu_memory_total: int | None = None

    def __post_init__(self) -> None:
        self.cpu = deque(maxlen=self.maxlen)
        self.host_memory = deque(maxlen=self.maxlen)
        self.gpu_memory = deque(maxlen=self.maxlen)
        self.gpu_utilization = deque(maxlen=self.maxlen)

    def reset(self, selection_key: str | None = None) -> None:
        self.selection_key = selection_key
        self.cpu.clear()
        self.host_memory.clear()
        self.gpu_memory.clear()
        self.gpu_utilization.clear()
        self.host_memory_total = None
        self.gpu_memory_total = None

    def sample(
        self,
        frame: FrameSnapshot,
        selection_key: str | None,
        *,
        host_memory_total: int | None = None,
    ) -> ProcessSnapshot | None:
        if selection_key != self.selection_key:
            self.reset(selection_key)
        process = find_process(frame, selection_key)
        if process is None:
            return None
        device = next((item for item in frame.devices if item.index == process.gpu_index), None)
        if host_memory_total is None:
            try:
                import psutil

                host_memory_total = int(psutil.virtual_memory().total)
            except (ModuleNotFoundError, OSError):
                pass
        gpu_memory_percent = _ratio_percent(
            process.gpu_memory_bytes,
            None if device is None else device.memory_total_bytes,
        )
        host_memory_percent = process.memory_util_percent
        if host_memory_percent is None:
            host_memory_percent = _ratio_percent(process.host_memory_bytes, host_memory_total)
        self.cpu.append(_finite_or_none(process.cpu_percent))
        self.host_memory.append(_finite_or_none(host_memory_percent))
        self.gpu_memory.append(_finite_or_none(gpu_memory_percent))
        self.gpu_utilization.append(_finite_or_none(process.gpu_util_percent))
        self.host_memory_total = host_memory_total
        self.gpu_memory_total = None if device is None else device.memory_total_bytes
        return process


def _finite_or_none(value: float | None) -> float | None:
    if value is None or not math.isfinite(float(value)):
        return None
    return float(value)


def _ratio_percent(used: int | None, total: int | None) -> float | None:
    if used is None or not total:
        return None
    return max(0.0, min(100.0, 100.0 * used / total))


def find_process(frame: FrameSnapshot, selection_key: str | None) -> ProcessSnapshot | None:
    if selection_key is None:
        return None
    return next((process for process in frame.processes if process.selection_key == selection_key), None)


def read_process_environment(
    pid: int,
    *,
    proc_root: str | Path = "/proc",
    expected_create_time: float | None = None,
) -> list[tuple[str, str]]:
    """Read ``/proc/<pid>/environ`` after optionally validating PID identity."""

    if expected_create_time is not None:
        try:
            import psutil

            actual_create_time = float(psutil.Process(pid).create_time())
        except (ModuleNotFoundError, OSError) as exc:
            raise ProcessLookupError(f"cannot validate process {pid}: {exc}") from exc
        except psutil.Error as exc:
            raise ProcessLookupError(f"cannot validate process {pid}: {exc}") from exc
        if abs(actual_create_time - expected_create_time) > PROCESS_CREATE_TIME_TOLERANCE:
            raise ProcessLookupError(f"process {pid} identity changed")

    payload = (Path(proc_root) / str(pid) / "environ").read_bytes()
    variables: list[tuple[str, str]] = []
    for item in payload.split(b"\0"):
        if not item:
            continue
        text = item.decode("utf-8", errors="replace")
        key, separator, value = text.partition("=")
        variables.append((key, value if separator else ""))
    return sorted(variables, key=lambda item: (item[0], item[1]))


def collect_host_processes() -> list[HostProcessInfo]:
    try:
        import psutil
    except ModuleNotFoundError:
        return []

    result: list[HostProcessInfo] = []
    for process in psutil.process_iter(
        attrs=(
            "pid",
            "ppid",
            "username",
            "name",
            "cmdline",
            "create_time",
            "num_threads",
            "cpu_percent",
            "memory_percent",
        ),
        ad_value=None,
    ):
        try:
            info = process.info
            command_parts = info.get("cmdline") or []
            command = " ".join(str(part) for part in command_parts) or str(info.get("name") or info["pid"])
            create_time = float(info["create_time"]) if info.get("create_time") is not None else None
            result.append(
                HostProcessInfo(
                    pid=int(info["pid"]),
                    ppid=int(info.get("ppid") or 0),
                    user=str(info.get("username") or "N/A"),
                    command=command,
                    create_time=create_time,
                    num_threads=int(info["num_threads"]) if info.get("num_threads") is not None else None,
                    cpu_percent=_finite_or_none(info.get("cpu_percent")),
                    memory_percent=_finite_or_none(info.get("memory_percent")),
                    runtime_seconds=(
                        max(0.0, time.time() - create_time)
                        if create_time is not None
                        else None
                    ),
                )
            )
        except (psutil.Error, KeyError, TypeError, ValueError):
            continue
    return result


def build_process_tree(
    frame: FrameSnapshot,
    host_processes: Iterable[HostProcessInfo] | None = None,
) -> list[ProcessTreeEntry]:
    """Return GPU processes together with their ancestors and descendants."""

    infos = list(collect_host_processes() if host_processes is None else host_processes)
    by_pid = {process.pid: process for process in infos}
    gpu_indices: dict[int, set[int]] = defaultdict(set)
    for process in frame.processes:
        gpu_indices[process.pid].add(process.gpu_index)
        if process.pid not in by_pid:
            by_pid[process.pid] = HostProcessInfo(
                pid=process.pid,
                user=process.user or "N/A",
                command=process.command or process.name or str(process.pid),
                create_time=process.create_time,
                cpu_percent=process.cpu_percent,
                memory_percent=process.memory_util_percent,
                runtime_seconds=process.runtime_seconds,
            )

    children: dict[int, list[int]] = defaultdict(list)
    for process in by_pid.values():
        if process.pid != process.ppid:
            children[process.ppid].append(process.pid)
    for pids in children.values():
        pids.sort(key=lambda pid: (by_pid[pid].user, pid))

    included = set(gpu_indices)
    for pid in tuple(included):
        cursor = pid
        visited: set[int] = set()
        while cursor in by_pid and cursor not in visited:
            visited.add(cursor)
            parent = by_pid[cursor].ppid
            if parent <= 0 or parent not in by_pid:
                break
            included.add(parent)
            cursor = parent

    for parent in gpu_indices:
        included.update(children.get(parent, ()))

    roots = sorted(pid for pid in included if by_pid[pid].ppid not in included)
    entries: list[ProcessTreeEntry] = []

    def visit(pid: int, ancestry_last: tuple[bool, ...], is_last: bool, visited: set[int]) -> None:
        if pid in visited:
            return
        visited = visited | {pid}
        process = by_pid[pid]
        branch = ""
        if ancestry_last:
            branch = "".join("   " if last else "│  " for last in ancestry_last[:-1])
            branch += "└─ " if is_last else "├─ "
        indices = sorted(gpu_indices.get(pid, ()))
        if indices:
            device = "GPU " + ",".join(map(str, indices))
        else:
            device = "Host"
        entries.append(
            ProcessTreeEntry(
                pid=pid,
                ppid=process.ppid,
                user=process.user,
                command=process.command,
                device=device,
                create_time=process.create_time,
                prefix=branch,
                num_threads=process.num_threads,
                cpu_percent=process.cpu_percent,
                memory_percent=process.memory_percent,
                runtime_seconds=process.runtime_seconds,
            )
        )
        child_pids = [child for child in children.get(pid, ()) if child in included]
        for index, child in enumerate(child_pids):
            visit(child, (*ancestry_last, is_last), index == len(child_pids) - 1, visited)

    for index, root in enumerate(roots):
        visit(root, (), index == len(roots) - 1, set())
    return entries


def render_help_screen(
    width: int,
    height: int | None = None,
    *,
    readonly: bool = False,
    offset: int = 0,
    gpu_thresholds: tuple[int, int] = (10, 75),
    memory_thresholds: tuple[int, int] = (10, 80),
) -> RenderedView:
    del readonly
    lines = [
        f"mxtop {__version__} - (C) mxtop contributors, 2026.",
        "Released under the MIT License.",
        "",
        "GPU Process Type: C: Compute, G: Graphics, X/C+G: Mixed.",
        "",
        "Device coloring rules by loading intensity:",
        f"  - GPU utilization: light < {gpu_thresholds[0]:2d}% <= moderate < {gpu_thresholds[1]:2d}% <= heavy.",
        f"  - GPU-MEM percent: light < {memory_thresholds[0]:2d}% <= moderate < {memory_thresholds[1]:2d}% <= heavy.",
        "",
        "      a f c: change display mode                h ?: show this help screen",
        "       F5 r: force refresh window                 q: quit",
        "",
        "     Arrows: scroll process list              Space: tag/untag current process",
        "       Home: select the first process           Esc: clear process selection",
        "        End: select the last process       Ctrl-C I: interrupt selected process",
        "                                                  K: kill selected process",
        "   Ctrl-A ^: scroll to left most                  T: terminate selected process",
        "   Ctrl-E $: scroll to right most                 e: show process environment",
        "   PageUp [: scroll entire screen up              t: toggle tree-view screen",
        " PageDown ]: scroll entire screen down        Enter: show process metrics",
        "",
        "      Wheel: scroll process list        Shift-Wheel: scroll horizontally",
        "        Tab: scroll process list          Ctrl-Wheel: fast scroll (5x)",
        "",
        "      on oN: sort by GPU-INDEX               op oP: sort by PID",
        "      ou oU: sort by USER                    og oG: sort by GPU-MEM",
        "      os oS: sort by %SM                     ob oB: sort by %GMBW",
        "      oc oC: sort by %CPU                    om oM: sort by %MEM",
        "      ot oT: sort by TIME                      , .: select sort column",
        "                                                /: invert sort order",
        "",
        "Press any key to return.",
    ]
    viewport = len(lines) if height is None else max(1, height)
    offset = max(0, min(offset, max(0, len(lines) - viewport)))
    shown = lines[offset : offset + viewport]
    return RenderedView([cell_ljust(cell_ellipsize(line, width), width) for line in shown])


def render_environment_screen(
    process: ProcessSnapshot | None,
    variables: Sequence[tuple[str, str]],
    *,
    width: int,
    height: int | None = None,
    selected_index: int = 0,
    scroll_offset: int = 0,
    horizontal_offset: int = 0,
    error: str | None = None,
    no_unicode: bool = False,
) -> RenderedView:
    if process is None:
        return _message_view("No process selected.", width, height)
    if process.gpu_index < 0:
        context = "Host"
    else:
        context = f"GPU: {_process_type_name(process.process_type)}"
    title_prefix = f"Environment of process {process.pid} ({process.user or 'N/A'}@{context}): "
    command = process.command or process.name or "N/A"
    title_offset = max(
        0,
        min(horizontal_offset, cell_width(title_prefix) + cell_width(command) - width),
    )
    title = title_prefix + cell_slice(command, title_offset)
    header = [cell_ljust(cell_slice(title, 0, width), width), "#" * width]
    if error:
        rows = ["Could not read process environment."]
    elif variables:
        newline = "?" if no_unicode else "␤"
        rows = [f"{key}={value.replace(chr(10), newline)}" for key, value in variables]
    else:
        rows = []
    viewport = len(rows) if height is None else max(1, height - len(header))
    selected_index = max(0, min(selected_index, len(rows) - 1))
    scroll_offset = max(0, min(scroll_offset, max(0, len(rows) - viewport)))
    if selected_index < scroll_offset:
        scroll_offset = selected_index
    elif selected_index >= scroll_offset + viewport:
        scroll_offset = selected_index - viewport + 1
    shown = rows[scroll_offset : scroll_offset + viewport]
    rendered = header + [cell_ljust(cell_slice(row, horizontal_offset, width), width) for row in shown]
    return RenderedView(
        rendered,
        selectable_start=len(header),
        selectable_count=len(shown) if not error else 0,
        selection_ids=(
            tuple(f"env:{scroll_offset + index}" for index in range(len(shown)))
            if not error
            else ()
        ),
    )


def render_tree_screen(
    entries: Sequence[ProcessTreeEntry],
    *,
    width: int,
    height: int | None = None,
    selected_index: int = 0,
    scroll_offset: int = 0,
    horizontal_offset: int = 0,
    readonly: bool = False,
    actionable: bool = False,
) -> RenderedView:
    hint = (
        "(Press ^C(INT)/T(TERM)/K(KILL) to send signals)"
        if actionable and not readonly
        else ""
    )
    pid_width = max(3, max((len(str(entry.pid)) for entry in entries), default=3))
    user_width = max(4, max((cell_width(entry.user) for entry in entries), default=4))
    device_width = max(6, max((cell_width(entry.device) for entry in entries), default=6))
    threads_width = max(4, max((len(_integer_text(entry.num_threads)) for entry in entries), default=4))
    time_width = max(4, max((len(format_duration(entry.runtime_seconds)) for entry in entries), default=4))
    columns = "  ".join(
        (
            "PID".rjust(pid_width),
            "USER".ljust(user_width),
            "DEVICE".rjust(device_width),
            "NLWP".rjust(threads_width),
            "%CPU",
            "%MEM",
            "TIME".rjust(time_width),
            "COMMAND",
        )
    )
    command_offset = cell_width(columns) - len("COMMAND")
    max_line_width = max(
        (
            command_offset + cell_width(entry.prefix) + cell_width(entry.command)
            for entry in entries
        ),
        default=len(columns),
    )
    horizontal_offset = max(0, min(horizontal_offset, max(0, max_line_width - width)))
    header = (
        cell_ljust(cell_slice(columns, horizontal_offset, width), width)
        if horizontal_offset < command_offset
        else "COMMAND".ljust(width)
    )
    if hint and len(hint) < width:
        start = width - len(hint)
        header = header[:start] + hint
    if not entries:
        return RenderedView([header, "No running GPU processes found.".ljust(width)])
    viewport = len(entries) if height is None else max(1, height - 1)
    selected_index = max(0, min(selected_index, len(entries) - 1))
    scroll_offset = max(0, min(scroll_offset, max(0, len(entries) - viewport)))
    if selected_index < scroll_offset:
        scroll_offset = selected_index
    elif selected_index >= scroll_offset + viewport:
        scroll_offset = selected_index - viewport + 1
    shown = entries[scroll_offset : scroll_offset + viewport]
    rows: list[str] = []
    for entry in shown:
        fixed = "{}  {}  {}  {} {:>5} {:>5}  {}  ".format(
            str(entry.pid).rjust(pid_width),
            cell_ljust(entry.user, user_width),
            cell_rjust(entry.device, device_width),
            _integer_text(entry.num_threads).rjust(threads_width),
            _tree_cpu_percent(entry.cpu_percent),
            _tree_memory_percent(entry.memory_percent),
            format_duration(entry.runtime_seconds).rjust(time_width),
        )
        raw = fixed + entry.prefix + entry.command
        rows.append(cell_ljust(cell_slice(raw, horizontal_offset, width), width))
    return RenderedView(
        [cell_ljust(cell_ellipsize(header, width), width), *rows],
        selectable_start=1,
        selectable_count=len(rows),
        selection_ids=tuple(entry.selection_id for entry in shown),
    )


def _integer_text(value: int | None) -> str:
    return "N/A" if value is None else str(value)


def _tree_cpu_percent(value: float | None) -> str:
    value = _finite_or_none(value)
    if value is None:
        return "N/A"
    if value < 1000.0:
        return f"{value:.1f}"
    if value < 10000.0:
        return str(int(value))
    return "9999+"


def _tree_memory_percent(value: float | None) -> str:
    value = _finite_or_none(value)
    return "N/A" if value is None else f"{value:.1f}"


def render_metrics_screen(
    frame: FrameSnapshot,
    process: ProcessSnapshot | None,
    history: ProcessMetricsHistory,
    *,
    width: int,
    height: int,
) -> RenderedView:
    if process is None:
        return _message_view("No process selected.", width, height)
    width = max(42, width)
    height = max(16, height)
    inner = width - 2
    left_width = max(20, (inner - 1) // 2)
    right_width = inner - left_width - 1
    graph_rows = max(8, height - 8)
    upper_height = max(3, graph_rows // 2)
    lower_height = max(3, graph_rows - upper_height)
    user_host = f"{getpass.getuser()}@{socket.gethostname().split('.', 1)[0]}"
    device = next((item for item in frame.devices if item.index == process.gpu_index), None)
    gpu_memory_total = history.gpu_memory_total or (
        None if device is None else device.memory_total_bytes
    )
    host_memory_total = history.host_memory_total
    if (
        host_memory_total is None
        and process.host_memory_bytes is not None
        and (_last(history.host_memory) or 0.0) > 0.0
    ):
        host_memory_total = round(
            process.host_memory_bytes * 100.0 / float(_last(history.host_memory) or 1.0)
        )

    title = _split_title("Process:", user_host, inner)
    header = _metrics_header(process, inner)
    values = _metrics_values(process, inner)
    cpu_bound = _dynamic_metric_bound(history.cpu, minimum=10.0, initial=100.0, maximum=1000.0)
    gpu_memory_bound = _dynamic_metric_bound(history.gpu_memory, minimum=10.0, initial=100.0)
    host_memory_bound = _dynamic_metric_bound(history.host_memory, minimum=10.0, initial=100.0)
    gpu_bound = _dynamic_metric_bound(history.gpu_utilization, minimum=10.0, initial=100.0)
    cpu_lines = _render_metric_graph(history.cpu, left_width, upper_height, cpu_bound)
    gpu_mem_lines = _render_metric_graph(history.gpu_memory, right_width, upper_height, gpu_memory_bound)
    host_mem_lines = _render_metric_graph(
        history.host_memory,
        left_width,
        lower_height,
        host_memory_bound,
        upsidedown=True,
    )
    gpu_lines = _render_metric_graph(
        history.gpu_utilization,
        right_width,
        lower_height,
        gpu_bound,
        upsidedown=True,
    )

    cpu_lines[0] = _overlay(cpu_lines[0], f" MAX CPU: {_format_max(history.cpu, '%')} ")
    if upper_height > 1:
        cpu_lines[1] = _overlay(cpu_lines[1], f" CPU: {format_percent_precise(_last(history.cpu))} ")
    gpu_mem_lines[0] = _overlay(
        gpu_mem_lines[0],
        " " + _memory_metric_label(
            "MAX GPU-MEM",
            _known_value_max(history.gpu_memory),
            gpu_memory_total,
            include_total=True,
        ) + " ",
    )
    if upper_height > 1:
        gpu_mem_lines[1] = _overlay(
            gpu_mem_lines[1],
            " " + _memory_metric_label(
                "GPU-MEM",
                _last(history.gpu_memory),
                gpu_memory_total,
                used_bytes=process.gpu_memory_bytes,
            ) + " ",
        )
    host_mem_lines[-2] = _overlay(
        host_mem_lines[-2],
        " " + _memory_metric_label(
            "HOST-MEM",
            _last(history.host_memory),
            host_memory_total,
            used_bytes=process.host_memory_bytes,
        ) + " ",
    )
    host_mem_lines[-1] = _overlay(
        host_mem_lines[-1],
        " " + _memory_metric_label(
            "MAX HOST-MEM",
            _known_value_max(history.host_memory),
            host_memory_total,
            include_total=True,
        ) + " ",
    )
    gpu_lines[-2] = _overlay(
        gpu_lines[-2],
        f" GPU-SM: {format_percent_precise(_last(history.gpu_utilization))} ",
    )
    gpu_lines[-1] = _overlay(
        gpu_lines[-1],
        f" MAX GPU-SM: {_format_max(history.gpu_utilization, '%')} ",
    )

    lines = [
        "╒" + "═" * inner + "╕",
        "│" + title + "│",
        "│" + header + "│",
        "╞" + "═" * inner + "╡",
        "│" + values + "│",
        "╞" + "═" * left_width + "╤" + "═" * right_width + "╡",
    ]
    lines.extend("│" + left + "│" + right + "│" for left, right in zip(cpu_lines, gpu_mem_lines))
    lines.append(
        "├" + _metric_time_axis(left_width) + "┼" + _metric_time_axis(right_width) + "┤"
    )
    lines.extend("│" + left + "│" + right + "│" for left, right in zip(host_mem_lines, gpu_lines))
    lines.append("╘" + "═" * left_width + "╧" + "═" * right_width + "╛")
    _apply_metric_ticks(
        lines,
        start=6,
        graph_height=upper_height,
        left_width=left_width,
        left_bound=cpu_bound,
        right_bound=gpu_memory_bound,
        upsidedown=False,
        protected_rows={0, 1},
    )
    _apply_metric_ticks(
        lines,
        start=7 + upper_height,
        graph_height=lower_height,
        left_width=left_width,
        left_bound=host_memory_bound,
        right_bound=gpu_bound,
        upsidedown=True,
        protected_rows={lower_height - 2, lower_height - 1},
    )
    return RenderedView(lines[:height])


def render_signal_dialog(
    targets: Sequence[tuple[int, str | None]],
    *,
    width: int,
    signal_name: str,
    current_option: int = 0,
) -> list[str]:
    del signal_name
    labels = [
        f"{pid}({cell_ellipsize(user or 'N/A', 24, marker='+')})"
        for pid, user in targets
    ]
    options = ("SIGTERM", "SIGKILL", "SIGINT", "Cancel")
    current_option %= len(options)
    button_width = 11
    button_inners = [
        _cell_center(f"[{name}]" if index == current_option else name, button_width - 2)
        for index, name in enumerate(options)
    ]
    button_rows = [
        " ".join("┌" + "─" * (button_width - 2) + "┐" for _ in options),
        " ".join("│" + content + "│" for content in button_inners),
        " ".join("└" + "─" * (button_width - 2) + "┘" for _ in options),
    ]
    max_inner = max(1, width - 2)
    content_width = max(1, max_inner - 4)
    if len(labels) == 1:
        message_lines = [f"Send signal to process {labels[0]}?"]
    else:
        message_lines = ["Send signal to the following processes?", ""]
        current = ""
        for label in labels:
            candidate = label if not current else f"{current} {label}"
            if current and cell_width(candidate) > content_width:
                message_lines.append(current)
                current = label
            else:
                current = candidate
        if current:
            message_lines.append(current)
    widest = max(
        max((cell_width(line) for line in message_lines), default=0),
        max(cell_width(line) for line in button_rows),
    )
    inner = min(widest + 4, max_inner)
    return [
        "╒" + "═" * inner + "╕",
        *("│" + _cell_center(cell_ellipsize(line, inner), inner) + "│" for line in message_lines),
        "│" + " " * inner + "│",
        *("│" + _cell_center(cell_ellipsize(line, inner), inner) + "│" for line in button_rows),
        "╘" + "═" * inner + "╛",
    ]


def _process_type_name(value: str | None) -> str:
    normalized = (value or "").upper()
    if normalized == "C":
        return "Compute"
    if normalized == "G":
        return "Graphics"
    if "C" in normalized and "G" in normalized or normalized == "X":
        return "Compute+Graphics"
    return "N/A"


def _message_view(message: str, width: int, height: int | None) -> RenderedView:
    lines = [_cell_center(message, width)]
    if height and height > 1:
        lines = [""] * ((height - 1) // 2) + lines
    return RenderedView([cell_ljust(cell_ellipsize(line, width), width) for line in lines])


def _split_title(left: str, right: str, width: int) -> str:
    left_width = cell_width(left)
    right_width = cell_width(right)
    if left_width + right_width + 1 > width:
        return cell_ljust(cell_ellipsize(f" {left} {right}", width), width)
    return f" {left}{' ' * (width - left_width - right_width - 2)}{right} "


def _metrics_header(process: ProcessSnapshot, width: int) -> str:
    del process
    text = " GPU     PID      USER  GPU-MEM %SM %GMBW  %CPU  %MEM    TIME  COMMAND"
    return cell_ljust(cell_ellipsize(text, width), width)


def _metrics_values(process: ProcessSnapshot, width: int) -> str:
    user = cell_rjust(cell_ellipsize(process.user or "N/A", 7, marker="+"), 7)
    process_type = cell_slice((process.process_type or "-").replace("C+G", "X"), 0, 1)
    fixed = (
        f" {process.gpu_index:>3} {process.pid:>7} {process_type} {user} "
        f"{format_mib(process.gpu_memory_bytes):>8} "
        f"{format_percent_value(process.gpu_util_percent):>3} "
        f"{format_percent_value(process.gpu_memory_bandwidth_util_percent):>5}  "
        f"{format_percent_value(process.cpu_percent):>4}  "
        f"{format_percent_value(process.memory_util_percent):>4}  "
        f"{format_duration(process.runtime_seconds):>7}  "
    )
    return cell_ljust(cell_ellipsize(fixed + (process.command or process.name), width), width)


def _known_max(values: Iterable[float | None], *, default: float) -> float:
    known = [value for value in values if value is not None and math.isfinite(value)]
    return max(known, default=default)


def _known_value_max(values: Iterable[float | None]) -> float | None:
    known = [value for value in values if value is not None and math.isfinite(value)]
    return max(known) if known else None


def _dynamic_metric_bound(
    values: Iterable[float | None],
    *,
    minimum: float,
    initial: float,
    maximum: float = 100.0,
) -> float:
    value = _known_value_max(values)
    if value is None:
        return initial
    target = max(minimum, min(maximum, value * 1.1))
    candidates = (10, 20, 40, 50, 80, 100, 200, 400, 500, 800, 1000)
    return float(next((candidate for candidate in candidates if candidate >= target), maximum))


def _memory_metric_label(
    name: str,
    percent: float | None,
    total: int | None,
    *,
    used_bytes: int | None = None,
    include_total: bool = False,
) -> str:
    if used_bytes is None and percent is not None and total:
        used_bytes = round(total * percent / 100.0)
    if percent is None:
        text = f"{name}: N/A"
    else:
        text = f"{name}: {format_compact_bytes(used_bytes)} ({format_percent_precise(percent)})"
    if include_total and total:
        text += f" / {format_compact_bytes(total)}"
    return text


def _metric_time_axis(width: int) -> str:
    if width <= 0:
        return ""
    line = list("─" * width)
    for offset, label in (
        (19, "╴30s├"),
        (34, "╴60s├"),
        (65, "╴120s├"),
        (95, "╴180s├"),
        (125, "╴240s├"),
        (155, "╴300s├"),
    ):
        if offset > width:
            break
        start = width - offset
        line[start : min(width, start + len(label))] = list(label[: width - start])
    return "".join(line)


def _apply_metric_ticks(
    lines: list[str],
    *,
    start: int,
    graph_height: int,
    left_width: int,
    left_bound: float,
    right_bound: float,
    upsidedown: bool,
    protected_rows: set[int],
) -> None:
    for separator, end, bound in (
        (0, left_width + 1, left_bound),
        (left_width + 1, len(lines[0]) - 1, right_bound),
    ):
        for value in _metric_tick_values(bound):
            fraction = value / max(bound, 1.0)
            row = round(fraction * (graph_height - 1))
            if not upsidedown:
                row = graph_height - 1 - row
            if row in protected_rows or not (0 <= row < graph_height):
                continue
            line_index = start + row
            if not (0 <= line_index < len(lines)):
                continue
            chars = list(lines[line_index])
            chars[separator] = "├"
            label = f"╴{value:g}% "
            label_end = min(end, separator + 1 + len(label))
            chars[separator + 1 : label_end] = list(label[: label_end - separator - 1])
            lines[line_index] = "".join(chars)


def _metric_tick_values(bound: float) -> tuple[float, ...]:
    values = []
    for fraction in (0.25, 0.5, 0.75):
        value = round(bound * fraction)
        if value > 0 and value not in values:
            values.append(value)
    return tuple(values)


def _cell_center(text: str, width: int) -> str:
    text = cell_ellipsize(text, width)
    padding = max(0, width - cell_width(text))
    left = padding // 2
    return " " * left + text + " " * (padding - left)


def _last(values: Sequence[float | None] | deque[float | None]) -> float | None:
    return values[-1] if values else None


def _format_max(values: Iterable[float | None], suffix: str = "") -> str:
    known = [value for value in values if value is not None and math.isfinite(value)]
    return "N/A" if not known else f"{max(known):.1f}{suffix}"


def _render_metric_graph(
    values: Iterable[float | None],
    width: int,
    height: int,
    bound: float,
    *,
    upsidedown: bool = False,
) -> list[str]:
    graph = HistoryGraph(height, upsidedown=upsidedown)
    for value in values:
        graph.add(None if value is None else 100.0 * value / max(bound, 1.0))
    return graph.render(width)


def _overlay(base: str, text: str) -> str:
    text = cell_ellipsize(text, cell_width(base), marker="..")
    return text + cell_slice(base, cell_width(text))
