from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from mxtop.models import DeviceSnapshot, FrameSnapshot, ProcessSnapshot
from mxtop.ui.screens import (
    HostProcessInfo,
    ProcessMetricsHistory,
    build_process_tree,
    read_process_environment,
    render_environment_screen,
    render_help_screen,
    render_metrics_screen,
    render_signal_dialog,
    render_tree_screen,
)
from mxtop.ui.text import cell_width


def _frame() -> FrameSnapshot:
    return FrameSnapshot(
        devices=[DeviceSnapshot(index=0, memory_total_bytes=64 * 1024**3)],
        processes=[
            ProcessSnapshot(
                gpu_index=0,
                pid=30,
                name="python",
                user="alice",
                command="python train.py",
                process_type="C",
                gpu_memory_bytes=32 * 1024**3,
                gpu_util_percent=75,
                gpu_memory_bandwidth_util_percent=20,
                cpu_percent=250,
                memory_util_percent=12,
                host_memory_bytes=8 * 1024**3,
                runtime_seconds=3723,
                create_time=100.0,
            )
        ],
        timestamp=200.0,
    )


def test_read_process_environment_sorts_and_preserves_values(tmp_path: Path):
    environ = tmp_path / "30" / "environ"
    environ.parent.mkdir()
    environ.write_bytes(b"ZED=last\0EMPTY=\0A=one=two\0")

    assert read_process_environment(30, proc_root=tmp_path) == [
        ("A", "one=two"),
        ("EMPTY", ""),
        ("ZED", "last"),
    ]


def test_read_process_environment_rejects_reused_pid(monkeypatch, tmp_path: Path):
    class Process:
        def __init__(self, pid: int):
            assert pid == 30

        def create_time(self) -> float:
            return 999.0

    monkeypatch.setitem(sys.modules, "psutil", SimpleNamespace(Process=Process, Error=Exception))

    with pytest.raises(ProcessLookupError, match="identity changed"):
        read_process_environment(30, proc_root=tmp_path, expected_create_time=100.0)


def test_build_process_tree_includes_ancestors_and_gpu_descendants():
    entries = build_process_tree(
        _frame(),
        [
            HostProcessInfo(1, 0, "root", "/sbin/init", 1.0),
            HostProcessInfo(20, 1, "alice", "bash", 50.0),
            HostProcessInfo(30, 20, "alice", "python train.py", 100.0),
            HostProcessInfo(31, 30, "alice", "data worker", 101.0),
            HostProcessInfo(99, 1, "bob", "unrelated", 10.0),
        ],
    )

    assert [entry.pid for entry in entries] == [1, 20, 30, 31]
    assert entries[2].device == "GPU 0"
    assert entries[3].prefix


def test_process_tree_matches_nvitop_direct_child_scope_and_sibling_order():
    entries = build_process_tree(
        _frame(),
        [
            HostProcessInfo(30, 0, "alice", "gpu", 100.0),
            HostProcessInfo(31, 30, "zoe", "worker-z", 101.0),
            HostProcessInfo(32, 30, "amy", "worker-a", 102.0),
            HostProcessInfo(33, 32, "amy", "grandchild", 103.0),
        ],
    )

    assert [entry.pid for entry in entries] == [30, 32, 31]
    assert all(entry.pid != 33 for entry in entries)


def test_process_tree_orders_independent_roots_by_pid_not_username():
    frame = FrameSnapshot(
        devices=[DeviceSnapshot(index=0)],
        processes=[
            ProcessSnapshot(gpu_index=0, pid=30, user="amy", create_time=30.0),
            ProcessSnapshot(gpu_index=0, pid=10, user="zoe", create_time=10.0),
        ],
    )

    entries = build_process_tree(
        frame,
        [
            HostProcessInfo(30, 0, "amy", "late pid", 30.0),
            HostProcessInfo(10, 0, "zoe", "early pid", 10.0),
        ],
    )

    assert [entry.pid for entry in entries] == [10, 30]


def test_secondary_screens_fit_width_and_expose_selectable_rows():
    frame = _frame()
    process = frame.processes[0]
    tree = build_process_tree(frame, [HostProcessInfo(30, command="python train.py")])

    help_view = render_help_screen(100, 20)
    environment = render_environment_screen(
        process,
        [("A", "1"), ("B", "2")],
        width=100,
        height=10,
    )
    tree_view = render_tree_screen(tree, width=100, height=10)

    assert all(len(line) == 100 for line in help_view.lines)
    assert all(len(line) == 100 for line in environment.lines)
    assert environment.selectable_count == 2
    assert all(len(line) == 100 for line in tree_view.lines)
    assert tree_view.selection_ids[0].startswith("tree:30:")


def test_help_screen_includes_project_license_without_visible_readonly_suffixes():
    view = render_help_screen(100, readonly=True)
    rendered = "\n".join(view.lines)

    assert view.lines[0].startswith("mxtop ")
    assert "mxtop contributors, 2026" in view.lines[0]
    assert "Released under the MIT License." in view.lines[1]
    assert "disabled by --readonly" not in rendered
    assert all(cell_width(line) == 100 for line in view.lines)


def test_help_sort_block_matches_nvitop_geometry():
    lines = [line.rstrip() for line in render_help_screen(100).lines]

    start = lines.index("      on oN: sort by GPU-INDEX")
    assert lines[start : start + 6] == [
        "      on oN: sort by GPU-INDEX",
        "      op oP: sort by PID                      ob oB: sort by %GMBW",
        "      ou oU: sort by USER                     oc oC: sort by %CPU",
        "      og oG: sort by GPU-MEM                  om oM: sort by %MEM",
        "      os oS: sort by %SM                      ot oT: sort by TIME",
        "        , .: select sort column                   /: invert sort order",
    ]


def test_environment_screen_matches_error_copy_and_scrolls_command_and_rows():
    process = _frame().processes[0]
    failed = render_environment_screen(
        process,
        [],
        width=72,
        height=5,
        error="[Errno 13] Permission denied",
    )
    scrolled = render_environment_screen(
        process,
        [("LONG_KEY", "0123456789")],
        width=40,
        height=4,
        horizontal_offset=5,
    )

    assert failed.lines[2].rstrip() == "Could not read process environment."
    assert "[Errno" not in "\n".join(failed.lines)
    assert scrolled.lines[0].startswith("Environment of process 30")
    assert scrolled.lines[2].startswith("KEY=0123456789")


def test_environment_screen_normalizes_embedded_newlines_and_has_no_fake_empty_row():
    process = _frame().processes[0]
    unicode_view = render_environment_screen(
        process,
        [("MULTILINE", "first\nsecond")],
        width=60,
        height=4,
    )
    ascii_view = render_environment_screen(
        process,
        [("MULTILINE", "first\nsecond")],
        width=60,
        height=4,
        no_unicode=True,
    )
    empty_view = render_environment_screen(process, [], width=60, height=4)

    assert "MULTILINE=first␤second" in unicode_view.lines[2]
    assert "MULTILINE=first?second" in ascii_view.lines[2]
    assert empty_view.lines == empty_view.lines[:2]
    assert empty_view.selectable_count == 0


def test_tree_screen_matches_nvitop_columns_and_horizontal_command_view():
    entries = build_process_tree(
        _frame(),
        [
            HostProcessInfo(
                30,
                command="python train.py --epochs 100",
                num_threads=12,
                cpu_percent=250.25,
                memory_percent=12.25,
                runtime_seconds=3723,
            )
        ],
    )
    view = render_tree_screen(entries, width=100, height=4)
    command_only = render_tree_screen(entries, width=20, height=4, horizontal_offset=10_000)

    assert "PID  USER" in view.lines[0]
    assert "DEVICE  NLWP  %CPU  %MEM     TIME  COMMAND" in view.lines[0]
    assert "  12 250.2  12.2  1:02:03  python train.py" in view.lines[1]
    assert command_only.lines[0] == "COMMAND".ljust(20)
    assert "epochs 100" in command_only.lines[1]


def test_secondary_screens_keep_terminal_width_with_cjk_content():
    process = _frame().processes[0]
    process.command = "python 训练.py"
    environment = render_environment_screen(
        process,
        [("MODEL", "大模型\n第二行")],
        width=60,
        height=4,
    )
    tree = render_tree_screen(
        [
            build_process_tree(
                _frame(),
                [HostProcessInfo(30, user="开发者", command="python 训练.py")],
            )[0]
        ],
        width=60,
        height=4,
    )

    assert all(cell_width(line) == 60 for line in environment.lines)
    assert all(cell_width(line) == 60 for line in tree.lines)


def test_process_metrics_samples_meta_x_values_and_renders_four_graphs():
    frame = _frame()
    process = frame.processes[0]
    history = ProcessMetricsHistory()
    for _ in range(8):
        assert history.sample(frame, process.selection_key, host_memory_total=128 * 1024**3) is process

    view = render_metrics_screen(frame, process, history, width=100, height=30)

    assert any("Process:" in line for line in view.lines)
    assert any("MAX CPU:" in line for line in view.lines)
    assert any("MAX GPU-MEM:" in line and "/" in line for line in view.lines)
    assert any("GPU-MEM:" in line for line in view.lines)
    assert any("HOST-MEM:" in line for line in view.lines)
    assert any("MAX HOST-MEM:" in line and "/" in line for line in view.lines)
    assert any("GPU-SM:" in line for line in view.lines)
    assert any("MAX GPU-SM:" in line for line in view.lines)
    assert any(line.startswith("│     CPU:") for line in view.lines)
    assert any("│     GPU-MEM:" in line for line in view.lines)
    assert any(line.startswith("│     HOST-MEM:") for line in view.lines)
    assert any("│     GPU-SM:" in line for line in view.lines)
    assert any("30s" in line for line in view.lines)
    assert sum("├╴" in line for line in view.lines) >= 2
    assert history.host_memory_total == 128 * 1024**3
    assert history.gpu_memory_total == 64 * 1024**3
    assert all(len(line) == 100 for line in view.lines)


def test_process_metrics_is_terminal_cell_exact_with_cjk_fields():
    frame = _frame()
    process = frame.processes[0]
    process.user = "开发者用户"
    process.command = "python 训练.py --模型 大模型"
    history = ProcessMetricsHistory()
    for _ in range(8):
        history.sample(frame, process.selection_key, host_memory_total=128 * 1024**3)

    view = render_metrics_screen(frame, process, history, width=79, height=28)

    assert len(view.lines) == 28
    assert all(cell_width(line) == 79 for line in view.lines)


def test_process_metrics_time_axis_expands_through_five_minutes():
    frame = _frame()
    process = frame.processes[0]
    history = ProcessMetricsHistory()
    history.sample(frame, process.selection_key, host_memory_total=128 * 1024**3)

    view = render_metrics_screen(frame, process, history, width=340, height=30)
    axis = next(line for line in view.lines if "30s" in line and "┼" in line)

    for label in ("30s", "60s", "120s", "180s", "240s", "300s"):
        assert label in axis


def test_process_metrics_infers_known_totals_for_seeded_histories():
    frame = _frame()
    process = frame.processes[0]
    history = ProcessMetricsHistory()
    history.selection_key = process.selection_key
    history.gpu_memory.extend((45.0, 50.0))
    history.host_memory.extend((10.0, 12.0))
    history.cpu.extend((100.0, 250.0))
    history.gpu_utilization.extend((25.0, 75.0))

    view = render_metrics_screen(frame, process, history, width=120, height=30)
    rendered = "\n".join(view.lines)

    assert "MAX GPU-MEM: 32.00GiB (50%) / 64.00GiB" in rendered
    assert "MAX HOST-MEM:" in rendered
    assert "MAX GPU-MEM: N/A" not in rendered
    assert all(cell_width(line) == 120 for line in view.lines)


def test_signal_dialog_shows_all_confirmed_signal_choices():
    dialog = render_signal_dialog(
        [(30, "alice"), (31, "bob")],
        width=100,
        signal_name="SIGTERM",
        current_option=1,
    )

    rendered = "\n".join(dialog)
    assert "30(alice)" in rendered
    assert "31(bob)" in rendered
    assert "SIGTERM" in rendered
    assert "[SIGKILL]" in rendered
    assert "Send signal to the following processes?" in rendered
    assert "Send SIGTERM" not in rendered
    assert any("┌" in line and "┐" in line for line in dialog)
    assert any("└" in line and "┘" in line for line in dialog)


def test_signal_dialog_wraps_without_omitting_narrow_multi_target_details():
    targets = [(pid, f"user-{pid}") for pid in range(1000, 1012)]
    dialog = render_signal_dialog(targets, width=79, signal_name="SIGKILL")
    rendered = "\n".join(dialog)

    assert all(f"{pid}(user-{pid})" in rendered for pid, _ in targets)
    assert all(cell_width(line) <= 79 for line in dialog)


def test_signal_dialog_is_cell_safe_with_cjk_usernames():
    dialog = render_signal_dialog(
        [(30, "开发者用户"), (31, "测试用户")],
        width=79,
        signal_name="SIGINT",
        current_option=2,
    )

    assert "[SIGINT]" in "\n".join(dialog)
    assert all(cell_width(line) <= 79 for line in dialog)


def test_tree_signal_hint_only_appears_when_actionable():
    entries = build_process_tree(_frame(), [HostProcessInfo(30, command="python train.py")])

    idle = render_tree_screen(entries, width=100, height=10)
    active = render_tree_screen(entries, width=100, height=10, actionable=True)
    readonly = render_tree_screen(entries, width=100, height=10, actionable=True, readonly=True)

    assert "send signals" not in idle.lines[0]
    assert "send signals" in active.lines[0]
    assert "send signals" not in readonly.lines[0]
