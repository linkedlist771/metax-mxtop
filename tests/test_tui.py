import sys
from types import SimpleNamespace

from mxtop import tui
from mxtop.models import DeviceSnapshot, FrameSnapshot, ProcessSnapshot
from mxtop.rendering import dense_device_row_context
from mxtop.ui.panels import render_device_panel, render_process_panel
from mxtop.ui.state import LayoutMode, ProcessSignal, ProcessSort, ScreenMode


class FakeScreen:
    def __init__(self, column_limit=8):
        self.calls = []
        self.column_limit = column_limit

    def addnstr(self, row, column, text, count, attr=0):
        if column >= self.column_limit or count <= 0:
            raise RuntimeError("would be curses ERR")
        self.calls.append((row, column, text[:count], count, attr))


class FakeBackend:
    name = "fake"

    def snapshot(self):
        return FrameSnapshot(devices=[], processes=[])


class FakeSampler:
    def __init__(self):
        self.refreshed = False

    def refresh_now(self):
        self.refreshed = True


def process_frame() -> FrameSnapshot:
    return FrameSnapshot(
        devices=[],
        processes=[
            ProcessSnapshot(gpu_index=0, pid=10, user="alice", create_time=100.0),
            ProcessSnapshot(gpu_index=0, pid=20, user="alice", create_time=200.0),
        ],
    )


def test_live_context_filter_error_preserves_untyped_processes():
    frame = FrameSnapshot(
        devices=[DeviceSnapshot(index=0)],
        processes=[ProcessSnapshot(gpu_index=0, pid=123)],
        backend="mx-smi",
    )
    options = SimpleNamespace(compute=True)

    filtered, error = tui._filtered_frame_with_error(frame, options)

    assert [process.pid for process in filtered.processes] == [123]
    assert error is not None
    assert "process type telemetry was not reported" in error


def test_live_context_filter_applies_normally_when_types_are_available():
    frame = FrameSnapshot(
        devices=[DeviceSnapshot(index=0)],
        processes=[
            ProcessSnapshot(gpu_index=0, pid=123, process_type="C"),
            ProcessSnapshot(gpu_index=0, pid=124, process_type="G"),
        ],
        backend="mx-smi",
    )
    options = SimpleNamespace(compute=True)

    filtered, error = tui._filtered_frame_with_error(frame, options)

    assert [process.pid for process in filtered.processes] == [123]
    assert error is None


def test_draw_line_does_not_write_past_current_width(monkeypatch):
    monkeypatch.setattr(tui.curses, "has_colors", lambda: False)
    screen = FakeScreen()

    tui._draw_line(screen, 5, "0    MXC500   74% [████████████] text", width=8)

    assert screen.calls


def test_safe_addnstr_keeps_the_terminal_last_column():
    screen = FakeScreen(column_limit=4)

    end = tui._safe_addnstr(screen, 0, 0, "abcd", width=4)

    assert end == 4
    assert screen.calls[-1][2] == "abcd"


def test_live_small_terminal_view_is_centered_and_framed(monkeypatch):
    monkeypatch.setattr(tui.curses, "has_colors", lambda: False)
    screen = FakeScreen(column_limit=60)

    tui._draw_small_terminal(screen, 60, 12, no_unicode=False)

    rendered = [text for _, _, text, _, _ in screen.calls]
    top = next(line for line in rendered if "╒" in line)
    assert top.index("╒") > 0
    assert any("Terminal size is too small" in line for line in rendered)
    assert any("mxtop needs at least 79 columns" in line for line in rendered)
    assert any("╘" in line for line in rendered)


def test_run_tui_treats_keyboard_interrupt_as_clean_exit(monkeypatch):
    def raise_interrupt():
        raise KeyboardInterrupt

    monkeypatch.setattr(tui.curses, "initscr", raise_interrupt)

    assert tui.run_tui(FakeBackend(), 1.0) == 130


def test_scroll_offset_clamps_to_rendered_content():
    assert tui._clamp_scroll(5, content_lines=20, viewport_lines=10) == 5
    assert tui._clamp_scroll(50, content_lines=20, viewport_lines=10) == 10
    assert tui._clamp_scroll(-5, content_lines=20, viewport_lines=10) == 0
    assert tui._clamp_scroll(5, content_lines=8, viewport_lines=10) == 0


def test_scroll_delta_handles_mouse_wheel_constants(monkeypatch):
    monkeypatch.setattr(tui.curses, "BUTTON4_PRESSED", 0x10000, raising=False)
    monkeypatch.setattr(tui.curses, "BUTTON5_PRESSED", 0x200000, raising=False)

    assert tui._mouse_scroll_delta(0x10000) == -3
    assert tui._mouse_scroll_delta(0x200000) == 3
    assert tui._mouse_scroll_delta(0) == 0


def test_dim_pair_is_visible_on_dark_terminals(monkeypatch):
    # Regression: borders/separators were drawn with COLOR_BLACK foreground,
    # which is invisible on dark terminals (live TUI showed no borders even
    # though the exit text frame did). They must use the default foreground.
    pairs: dict[int, tuple[int, int]] = {}
    monkeypatch.setattr(tui.curses, "has_colors", lambda: True)
    monkeypatch.setattr(tui.curses, "start_color", lambda: None, raising=False)
    monkeypatch.setattr(tui.curses, "use_default_colors", lambda: None, raising=False)
    monkeypatch.setattr(
        tui.curses,
        "init_pair",
        lambda pair, fg, bg: pairs.__setitem__(pair, (fg, bg)),
        raising=False,
    )

    tui._setup_colors()

    dim_fg, _ = pairs[tui.PAIR_DIM]
    assert dim_fg != tui.curses.COLOR_BLACK
    assert pairs[tui.PAIR_SELECTED] == (tui.curses.COLOR_CYAN, -1)
    assert pairs[tui.PAIR_TREE_SELECTED] == (tui.curses.COLOR_GREEN, -1)

    monkeypatch.setattr(tui.curses, "color_pair", lambda pair: 0, raising=False)
    assert tui._attr(tui.PAIR_DIM) & tui.curses.A_DIM


def test_light_theme_uses_dark_foreground_for_normal_and_dim_text(monkeypatch):
    pairs: dict[int, tuple[int, int]] = {}
    monkeypatch.setattr(tui._rendering, "LIGHT_THEME", True)
    monkeypatch.setattr(tui.curses, "has_colors", lambda: True)
    monkeypatch.setattr(tui.curses, "start_color", lambda: None, raising=False)
    monkeypatch.setattr(tui.curses, "use_default_colors", lambda: None, raising=False)
    monkeypatch.setattr(
        tui.curses,
        "init_pair",
        lambda pair, fg, bg: pairs.__setitem__(pair, (fg, bg)),
        raising=False,
    )

    tui._setup_colors()

    assert pairs[tui.PAIR_VALUE][0] == tui.curses.COLOR_BLACK
    assert pairs[tui.PAIR_DIM][0] == tui.curses.COLOR_BLACK


def test_unowned_tree_tag_keeps_dim_attribute(monkeypatch):
    monkeypatch.setattr(tui.curses, "has_colors", lambda: False)
    monkeypatch.setattr(tui, "_is_superuser", lambda: False)
    monkeypatch.setattr(tui.getpass, "getuser", lambda: "alice")

    attr = tui._tree_tagged_attr("bob")

    assert attr & tui.curses.A_BOLD
    assert attr & tui.curses.A_DIM


def test_handle_key_updates_sort_and_layout(monkeypatch):
    state = tui.UiState()
    sampler = FakeSampler()

    assert tui._handle_key(ord("."), state, None, sampler)
    assert state.process_sort == ProcessSort.PID
    assert tui._handle_key(ord("/"), state, None, sampler)
    assert state.reverse_sort is True
    assert tui._handle_key(ord("c"), state, None, sampler)
    assert state.layout.value == "compact"
    assert tui._handle_key(ord("r"), state, None, sampler)
    assert sampler.refreshed is True


def test_page_keys_move_whole_main_screen_one_row():
    state = tui.UiState(main_screen_offset=3)
    sampler = FakeSampler()

    assert tui._handle_key(tui.curses.KEY_PPAGE, state, process_frame(), sampler)
    assert state.main_screen_offset == 2
    assert state.scroll_offset == 0
    assert state.follow_selection is False

    assert tui._handle_key(tui.curses.KEY_NPAGE, state, process_frame(), sampler)
    assert state.main_screen_offset == 3


def test_selection_starts_clear_and_escape_does_not_quit():
    state = tui.UiState()
    sampler = FakeSampler()
    frame = process_frame()

    assert state.selected_key is None
    assert tui._handle_key(tui.curses.KEY_DOWN, state, frame, sampler)
    assert state.selected_key == frame.processes[0].selection_key
    assert tui._handle_key(27, state, frame, sampler)
    assert state.selected_key is None
    assert not tui._handle_key(ord("q"), state, frame, sampler)


def test_selection_identity_rejects_pid_reuse_between_frames():
    state = tui.UiState()
    first = process_frame()
    state.selected_key = first.processes[0].selection_key
    state.selected_index = 0
    replacement = ProcessSnapshot(gpu_index=0, pid=10, user="alice", create_time=999.0)

    tui.keep_selection(state, [replacement])

    assert state.selected_key is None


def test_navigation_aliases_and_tags_follow_nvitop():
    state = tui.UiState()
    sampler = FakeSampler()
    frame = process_frame()

    tui._handle_key(ord("\t"), state, frame, sampler)
    assert state.selected_index == 0
    tui._handle_key(ord(" "), state, frame, sampler)
    assert state.tagged_pids == {10}
    assert state.selected_index == 1
    tui._handle_key(tui.curses.KEY_HOME, state, frame, sampler)
    assert state.selected_index == 0
    tui._handle_key(tui.curses.KEY_END, state, frame, sampler)
    assert state.selected_index == 1


def test_direct_sort_bindings_use_case_for_direction():
    state = tui.UiState()
    sampler = FakeSampler()

    for key, expected in {
        "n": ProcessSort.DEFAULT,
        "p": ProcessSort.PID,
        "u": ProcessSort.USER,
        "g": ProcessSort.GPU_MEMORY,
        "s": ProcessSort.GPU_UTIL,
        "b": ProcessSort.GPU_MEMORY_BANDWIDTH,
        "c": ProcessSort.CPU,
        "m": ProcessSort.HOST_MEMORY,
        "t": ProcessSort.TIME,
    }.items():
        tui._handle_key(ord("o"), state, None, sampler)
        tui._handle_key(ord(key), state, None, sampler)
        assert state.process_sort == expected
        assert state.reverse_sort is False
        tui._handle_key(ord("o"), state, None, sampler)
        tui._handle_key(ord(key.upper()), state, None, sampler)
        assert state.process_sort == expected
        assert state.reverse_sort is True


def test_direct_sort_prefix_survives_idle_polls():
    state = tui.UiState()
    sampler = FakeSampler()

    tui._handle_key(ord("o"), state, None, sampler)
    tui._handle_key(-1, state, None, sampler)
    tui._handle_key(-1, state, None, sampler)
    tui._handle_key(ord("g"), state, None, sampler)

    assert state.process_sort == ProcessSort.GPU_MEMORY
    assert not state.pending_sort_key


def test_screen_routes_return_without_quitting():
    state = tui.UiState()
    sampler = FakeSampler()
    frame = process_frame()

    tui._handle_key(ord("h"), state, frame, sampler)
    assert state.active_screen == ScreenMode.HELP
    tui._handle_key(ord("x"), state, frame, sampler)
    assert state.active_screen == ScreenMode.MAIN


def test_nested_help_and_environment_routes_restore_their_origin():
    state = tui.UiState()
    sampler = FakeSampler()
    frame = process_frame()
    tui._handle_key(tui.curses.KEY_DOWN, state, frame, sampler)
    tui._handle_key(ord("t"), state, frame, sampler)
    state.screen_scroll_offset = 3
    state.screen_horizontal_offset = 5
    state.screen_selected_index = 1
    state.screen_selection_active = True
    state.screen_selection_ids = ("tree:10", "tree:20")
    state.screen_target_pid = 20
    state.screen_target_create_time = 200.0
    state.screen_target_user = "alice"

    tui._handle_key(ord("e"), state, frame, sampler)
    state.screen_scroll_offset = 7
    state.screen_horizontal_offset = 9
    tui._handle_key(ord("h"), state, frame, sampler)
    tui._handle_key(ord("x"), state, frame, sampler)

    assert state.active_screen == ScreenMode.ENVIRON
    assert state.screen_scroll_offset == 7
    assert state.screen_horizontal_offset == 9

    tui._handle_key(ord("q"), state, frame, sampler)
    assert state.active_screen == ScreenMode.TREE
    assert state.screen_scroll_offset == 3
    assert state.screen_horizontal_offset == 5
    assert state.screen_selected_index == 1
    assert state.screen_selection_active

    tui._handle_key(ord("q"), state, frame, sampler)
    assert state.active_screen == ScreenMode.MAIN
    assert not state.screen_history
    assert not state.screen_view_history


def test_metrics_environment_returns_then_metrics_exits_explicitly_to_main():
    state = tui.UiState()
    sampler = FakeSampler()
    frame = process_frame()
    tui._handle_key(tui.curses.KEY_DOWN, state, frame, sampler)
    tui._handle_key(ord("\n"), state, frame, sampler)
    assert state.active_screen == ScreenMode.METRICS

    tui._handle_key(ord("e"), state, frame, sampler)
    assert tui._metrics_tracking(state)
    tui._handle_key(ord("q"), state, frame, sampler)
    assert state.active_screen == ScreenMode.METRICS

    tui._handle_key(ord("q"), state, frame, sampler)
    assert state.active_screen == ScreenMode.MAIN
    assert not state.screen_history


def test_tree_route_transfers_selection_and_supports_tagging():
    state = tui.UiState()
    sampler = FakeSampler()
    frame = process_frame()
    tui._handle_key(tui.curses.KEY_DOWN, state, frame, sampler)

    tui._handle_key(ord("t"), state, frame, sampler)
    assert state.active_screen == ScreenMode.TREE
    assert state.selected_key is None
    assert state.screen_selection_active

    state.screen_selection_ids = ("tree:10:100", "tree:20:200")
    state.screen_selected_index = 1
    state.screen_target_pid = 20
    state.screen_target_create_time = 200.0
    state.screen_target_user = "alice"
    tui._handle_key(ord(" "), state, frame, sampler)
    assert state.tagged_pids == {20}

    tui._handle_key(ord("q"), state, frame, sampler)
    assert state.active_screen == ScreenMode.MAIN
    assert state.selected_key == frame.processes[1].selection_key
    assert state.tagged_pids == set()
    tui._handle_key(ord("t"), state, frame, sampler)
    assert state.active_screen == ScreenMode.TREE
    tui._handle_key(ord("q"), state, frame, sampler)
    assert state.active_screen == ScreenMode.MAIN

    tui._handle_key(tui.curses.KEY_DOWN, state, frame, sampler)
    tui._handle_key(ord("e"), state, frame, sampler)
    assert state.active_screen == ScreenMode.ENVIRON
    tui._handle_key(27, state, frame, sampler)
    assert state.active_screen == ScreenMode.MAIN
    tui._handle_key(ord("\n"), state, frame, sampler)
    assert state.active_screen == ScreenMode.METRICS
    tui._handle_key(27, state, frame, sampler)
    assert state.active_screen == ScreenMode.MAIN


def test_tree_refresh_clears_missing_or_reused_process_selection():
    state = tui.UiState(
        active_screen=ScreenMode.TREE,
        screen_selection_active=True,
        screen_selected_index=1,
        screen_target_pid=20,
        screen_target_create_time=200.0,
        screen_target_user="alice",
        screen_target_command="python",
    )
    reused = tui.ProcessTreeEntry(
        pid=20,
        ppid=1,
        user="alice",
        command="other process",
        device="Host",
        create_time=None,
    )

    tui._sync_tree_selection(state, [reused])

    assert not state.screen_selection_active
    assert state.screen_target_pid is None
    assert state.screen_target_create_time is None


def test_tree_refresh_preserves_exact_process_identity():
    state = tui.UiState(
        active_screen=ScreenMode.TREE,
        screen_selection_active=True,
        screen_target_pid=20,
        screen_target_create_time=200.0,
    )
    entries = [
        tui.ProcessTreeEntry(10, 1, "alice", "first", "Host", 100.0),
        tui.ProcessTreeEntry(20, 1, "alice", "second", "GPU 0", 200.005),
    ]

    tui._sync_tree_selection(state, entries)

    assert state.screen_selection_active
    assert state.screen_selected_index == 1


def test_tree_refresh_interval_tracks_nvitop_cadence():
    assert tui._tree_snapshot_interval(0.3) == 0.1
    assert tui._tree_snapshot_interval(1.5) == 0.5
    assert tui._tree_snapshot_interval(30.0) == 1.0


def test_metrics_refresh_interval_and_sampling_are_independent_of_frame_versions():
    frame = process_frame()
    process = frame.processes[0]
    history = tui.ProcessMetricsHistory()

    assert abs(tui._metrics_snapshot_interval(0.3) - 0.1) < 1e-9
    assert tui._metrics_snapshot_interval(30.0) == 1.0
    sampled_at = tui._sample_metrics_if_due(
        history,
        frame,
        process,
        now=10.0,
        sampled_at=float("-inf"),
        interval=0.1,
    )
    assert sampled_at == 10.0
    assert len(history.cpu) == 1

    sampled_at = tui._sample_metrics_if_due(
        history,
        frame,
        process,
        now=10.05,
        sampled_at=sampled_at,
        interval=0.1,
    )
    assert sampled_at == 10.0
    assert len(history.cpu) == 1

    sampled_at = tui._sample_metrics_if_due(
        history,
        frame,
        process,
        now=10.1,
        sampled_at=sampled_at,
        interval=0.1,
    )
    assert sampled_at == 10.1
    assert len(history.cpu) == 2


def test_secondary_screens_keep_host_history_sampling(monkeypatch):
    calls: list[int | None] = []
    monkeypatch.setattr(
        tui,
        "render_host_panel",
        lambda _frame, _width, **kwargs: (
            calls.append(kwargs["selected_gpu_index"]) or []
        ),
    )
    frame = process_frame()
    state = tui.UiState(
        active_screen=ScreenMode.METRICS,
        screen_target_pid=10,
        screen_target_create_time=100.0,
    )
    history = tui.HostHistory()
    assert abs(tui._host_snapshot_interval(0.3) - 0.1) < 1e-9
    assert tui._host_snapshot_interval(30.0) == 0.5

    sampled_at = tui._sample_host_history_if_due(
        history,
        frame,
        state,
        now=5.0,
        sampled_at=float("-inf"),
        interval=0.5,
    )
    assert calls == [0]
    assert sampled_at == 5.0

    sampled_at = tui._sample_host_history_if_due(
        history,
        frame,
        state,
        now=5.25,
        sampled_at=sampled_at,
        interval=0.5,
    )
    assert calls == [0]
    assert sampled_at == 5.0

    tui._sample_host_history_if_due(
        history,
        frame,
        state,
        now=5.5,
        sampled_at=sampled_at,
        interval=0.5,
    )
    assert calls == [0, 0]


def test_reused_or_missing_tag_identity_is_pruned():
    state = tui.UiState(
        tagged_pids={20, 30},
        tagged_processes={20: (200.0, "alice"), 30: (300.0, "alice")},
    )

    tui._prune_tags(state, {20: 999.0})

    assert state.tagged_pids == set()
    assert state.tagged_processes == {}


def test_environment_without_selection_replaces_a_stale_target_with_host(monkeypatch):
    class Process:
        def __init__(self, pid):
            self.pid = pid

        def create_time(self):
            return 123.0

        def username(self):
            return "alice"

        def cmdline(self):
            return ["python", "-m", "mxtop"]

    monkeypatch.setitem(
        sys.modules, "psutil", SimpleNamespace(Process=Process, Error=Exception)
    )
    monkeypatch.setattr(tui.os, "getpid", lambda: 77)
    state = tui.UiState(
        screen_target_pid=20,
        screen_target_create_time=200.0,
        screen_target_user="stale",
        screen_target_command="old",
    )

    tui._remember_selected_target(state, process_frame(), fallback_host=True)

    assert state.screen_target_pid == 77
    assert state.screen_target_create_time == 123.0
    assert state.screen_target_user == "alice"
    assert state.screen_target_command == "python -m mxtop"


def test_offscreen_untagged_selection_is_not_actionable():
    state = tui.UiState()
    sampler = FakeSampler()
    frame = process_frame()
    tui._handle_key(tui.curses.KEY_DOWN, state, frame, sampler)
    assert state.selected_visible

    tui._handle_key(tui.curses.KEY_NPAGE, state, frame, sampler)
    tui._handle_key(ord("K"), state, frame, sampler)

    assert not state.selected_visible
    assert state.pending_signal is None


def test_offscreen_tags_remain_actionable(monkeypatch):
    monkeypatch.setattr(tui.getpass, "getuser", lambda: "alice")
    monkeypatch.setattr(tui.os, "geteuid", lambda: 1000)
    state = tui.UiState()
    sampler = FakeSampler()
    frame = process_frame()
    tui._handle_key(tui.curses.KEY_DOWN, state, frame, sampler)
    tui._handle_key(ord(" "), state, frame, sampler)
    tui._handle_key(tui.curses.KEY_NPAGE, state, frame, sampler)

    tui._handle_key(ord("K"), state, frame, sampler)

    assert state.pending_signal == ProcessSignal.KILL
    assert state.pending_signal_targets == ((10, 100.0),)


def test_readonly_blocks_signal_dialog():
    state = tui.UiState(readonly=True)
    sampler = FakeSampler()
    frame = process_frame()
    tui._handle_key(tui.curses.KEY_DOWN, state, frame, sampler)

    tui._handle_key(ord("K"), state, frame, sampler, readonly=True)

    assert state.pending_signal is None
    assert "readonly" in (state.status_message or "")


def test_unowned_selection_does_not_open_signal_dialog(monkeypatch):
    state = tui.UiState()
    sampler = FakeSampler()
    frame = process_frame()
    for process in frame.processes:
        process.user = "bob"
    monkeypatch.setattr(tui.getpass, "getuser", lambda: "alice")
    monkeypatch.setattr(tui.os, "geteuid", lambda: 1000)
    tui._handle_key(tui.curses.KEY_DOWN, state, frame, sampler)

    tui._handle_key(ord("K"), state, frame, sampler)

    assert state.pending_signal is None
    assert "another user" in (state.status_message or "")


def test_signal_confirmation_validates_process_creation_time(monkeypatch):
    calls: list[str] = []

    class Error(Exception):
        pass

    class Process:
        def __init__(self, pid):
            assert pid == 10

        def create_time(self):
            return 100.0

        def username(self):
            return "alice"

        def kill(self):
            calls.append("kill")

        def terminate(self):
            calls.append("terminate")

        def send_signal(self, _signal):
            calls.append("interrupt")

    monkeypatch.setitem(
        sys.modules, "psutil", SimpleNamespace(Process=Process, Error=Error)
    )
    monkeypatch.setattr(tui.getpass, "getuser", lambda: "alice")
    monkeypatch.setattr(tui.os, "geteuid", lambda: 1000)
    state = tui.UiState()
    sampler = FakeSampler()
    frame = process_frame()
    tui._handle_key(tui.curses.KEY_DOWN, state, frame, sampler)

    tui._handle_key(ord("K"), state, frame, sampler)
    assert state.pending_signal == ProcessSignal.KILL
    tui._handle_key(ord("\n"), state, frame, sampler)

    assert calls == ["kill"]
    assert state.pending_signal is None


def test_signal_confirmation_rejects_reused_pid(monkeypatch):
    calls: list[str] = []

    class Error(Exception):
        pass

    class Process:
        def __init__(self, _pid):
            pass

        def create_time(self):
            return 999.0

        def username(self):
            return "alice"

        def kill(self):
            calls.append("kill")

    monkeypatch.setitem(
        sys.modules, "psutil", SimpleNamespace(Process=Process, Error=Error)
    )
    monkeypatch.setattr(tui.getpass, "getuser", lambda: "alice")
    monkeypatch.setattr(tui.os, "geteuid", lambda: 1000)
    state = tui.UiState()
    sampler = FakeSampler()
    frame = process_frame()
    tui._handle_key(tui.curses.KEY_DOWN, state, frame, sampler)

    tui._handle_key(ord("K"), state, frame, sampler)
    tui._handle_key(ord("\n"), state, frame, sampler)

    assert calls == []
    assert "identity changed" in (state.status_message or "")


def test_mouse_click_selects_mapped_process_row(monkeypatch):
    frame = process_frame()
    state = tui.UiState()
    sampler = FakeSampler()
    button = getattr(tui.curses, "BUTTON1_CLICKED", 0x4)
    monkeypatch.setattr(tui.curses, "getmouse", lambda: (0, 4, 12, 0, button))

    tui._handle_key(tui.curses.KEY_MOUSE, state, frame, sampler, mouse_rows={12: 1})

    assert state.selected_key == frame.processes[1].selection_key


def test_mouse_click_outside_process_rows_clears_selection_and_tags(monkeypatch):
    frame = process_frame()
    state = tui.UiState()
    sampler = FakeSampler()
    tui._handle_key(tui.curses.KEY_DOWN, state, frame, sampler)
    tui._handle_key(ord(" "), state, frame, sampler)
    assert state.tagged_pids
    button = getattr(tui.curses, "BUTTON1_CLICKED", 0x4)
    monkeypatch.setattr(tui.curses, "getmouse", lambda: (0, 4, 3, 0, button))

    tui._handle_key(tui.curses.KEY_MOUSE, state, frame, sampler, mouse_rows={12: 1})

    assert state.selected_key is None
    assert not state.tagged_pids


def test_mouse_click_outside_tree_rows_clears_tree_selection(monkeypatch):
    button = getattr(tui.curses, "BUTTON1_CLICKED", 0x4)
    monkeypatch.setattr(tui.curses, "getmouse", lambda: (0, 4, 3, 0, button))
    state = tui.UiState(
        active_screen=ScreenMode.TREE,
        screen_selection_active=True,
        screen_target_pid=10,
        screen_target_create_time=100.0,
        tagged_pids={10},
        tagged_processes={10: (100.0, "alice")},
    )

    tui._handle_key(
        tui.curses.KEY_MOUSE,
        state,
        process_frame(),
        FakeSampler(),
        mouse_rows={12: 1},
    )

    assert not state.screen_selection_active
    assert state.screen_target_pid is None
    assert not state.tagged_pids


def test_header_click_sorts_and_reverses(monkeypatch):
    frame = process_frame()
    state = tui.UiState()
    sampler = FakeSampler()
    header = tui.PROCESS_HEADER_MARKER + " %SM %GMBW  %CPU  %MEM  TIME  COMMAND"
    spans = tui._header_sort_spans(header)
    pid_start = header.find("PID")
    button = getattr(tui.curses, "BUTTON1_CLICKED", 0x4)
    monkeypatch.setattr(
        tui.curses, "getmouse", lambda: (0, pid_start, 5, 0, button)
    )

    tui._handle_key(
        tui.curses.KEY_MOUSE, state, frame, sampler, header_rows={5: spans}
    )
    assert state.process_sort == tui.ProcessSort.PID
    assert state.reverse_sort is False

    tui._handle_key(
        tui.curses.KEY_MOUSE, state, frame, sampler, header_rows={5: spans}
    )
    assert state.process_sort == tui.ProcessSort.PID
    assert state.reverse_sort is True


def test_header_sort_spans_resolve_overlapping_labels():
    header = tui.PROCESS_HEADER_MARKER + " %SM %GMBW  %CPU  %MEM  TIME  COMMAND"
    spans = tui._header_sort_spans(header)
    by_sort = {sort: (start, end) for start, end, sort in spans}

    gpu_mem = by_sort[tui.ProcessSort.GPU_MEMORY]
    assert header[gpu_mem[0] : gpu_mem[1]] == "GPU-MEM"
    gpu = by_sort[tui.ProcessSort.DEFAULT]
    assert header[gpu[0] : gpu[1]] == "GPU"
    assert gpu[0] < gpu_mem[0]
    host_mem = by_sort[tui.ProcessSort.HOST_MEMORY]
    assert header[host_mem[0] : host_mem[1]] == "%MEM"


def test_shift_mouse_wheel_scrolls_host_columns(monkeypatch):
    state = tui.UiState()
    sampler = FakeSampler()
    button = getattr(tui.curses, "BUTTON5_PRESSED", 1 << 21)
    shift = getattr(tui.curses, "BUTTON_SHIFT", 0)
    monkeypatch.setattr(tui.curses, "getmouse", lambda: (0, 4, 12, 0, button | shift))

    tui._handle_key(tui.curses.KEY_MOUSE, state, process_frame(), sampler)

    assert state.command_offset == 2


def test_ctrl_e_scrolls_to_the_end_of_the_longest_command():
    frame = process_frame()
    frame.processes[0].command = (
        "python train.py " + "--argument value " * 20 + "FINAL-TOKEN"
    )
    state = tui.UiState()

    tui._handle_key(5, state, frame, FakeSampler())
    assert state.command_offset == tui.LARGE_SCROLL_OFFSET

    lines, _, _ = render_process_panel(frame, state, 79, compact=True)
    assert state.command_offset < tui.LARGE_SCROLL_OFFSET
    assert any("FINAL-TOKEN" in line for line in lines)


def test_selection_movement_forwards_edge_residual_to_the_dashboard():
    frame = process_frame()
    state = tui.UiState(
        selected_key=frame.processes[-1].selection_key,
        selected_index=len(frame.processes) - 1,
        main_screen_offset=7,
    )

    tui._move_selection(state, frame, 1)

    assert state.selected_index == len(frame.processes) - 1
    assert state.main_screen_offset == 8
    assert not state.selected_visible
    assert not state.follow_selection

    tui._move_selection(state, frame, -1)
    assert state.selected_index == 0
    assert state.main_screen_offset == 8
    assert state.selected_visible
    assert state.follow_selection


def test_process_drawing_advances_by_terminal_cells_for_cjk(monkeypatch):
    process = ProcessSnapshot(
        gpu_index=0,
        pid=10,
        user="开发者",
        command="python 训练.py",
        process_type="C",
    )
    frame = FrameSnapshot(devices=[DeviceSnapshot(index=0)], processes=[process])
    lines, _, _ = render_process_panel(frame, tui.UiState(), 100, compact=True)
    line = next(line for line in lines if tui._is_process_data_line(line))
    monkeypatch.setattr(tui, "_attr", lambda pair, extra=0: (pair, extra))
    screen = FakeScreen(column_limit=100)

    tui._draw_process_data_line(
        screen,
        0,
        line,
        100,
        (tui.PAIR_VALUE, 0),
        (0, True),
        semantic_line=line,
    )

    for previous, current in zip(screen.calls, screen.calls[1:]):
        assert current[1] == previous[1] + tui.cell_width(previous[2])


def test_linked_multi_gpu_marker_blinks_without_impersonating_a_tag(monkeypatch):
    first = ProcessSnapshot(gpu_index=0, pid=10, user="alice", create_time=100.0)
    second = ProcessSnapshot(gpu_index=1, pid=10, user="alice", create_time=100.0)
    frame = FrameSnapshot(
        devices=[DeviceSnapshot(index=0), DeviceSnapshot(index=1)],
        processes=[first, second],
    )
    state = tui.UiState(selected_key=first.selection_key)
    lines, _, _ = render_process_panel(frame, state, 100, compact=True)
    linked_line = next(line for line in lines if line.startswith("│="))
    monkeypatch.setattr(tui, "_attr", lambda pair, extra=0: (pair, extra))

    linked_screen = FakeScreen(column_limit=100)
    tui._draw_process_data_line(
        linked_screen,
        0,
        linked_line,
        100,
        0,
        (0, True),
        tagged=False,
        linked=True,
        semantic_line=linked_line,
    )
    assert any(
        column == 1
        and text == "="
        and attr[0] == tui.PAIR_VALUE
        and attr[1] & tui.curses.A_BLINK
        for _, column, text, _, attr in linked_screen.calls
    )
    assert all(attr[0] != tui.PAIR_WARN for _, _, _, _, attr in linked_screen.calls)

    tagged_screen = FakeScreen(column_limit=100)
    tui._draw_process_data_line(
        tagged_screen,
        0,
        linked_line,
        100,
        0,
        (0, True),
        tagged=True,
        linked=True,
        semantic_line=linked_line,
    )
    assert any(attr[0] == tui.PAIR_WARN for _, _, _, _, attr in tagged_screen.calls)
    warning_calls = [call for call in tagged_screen.calls if call[-1][0] == tui.PAIR_WARN]
    assert [(column, count) for _, column, _, count, _ in warning_calls] == [(5, 94)]
    assert all(column not in {0, 1, 2} for _, column, _, _, _ in warning_calls)


def test_main_selection_preserves_box_borders_but_tree_selection_does_not(monkeypatch):
    monkeypatch.setattr(tui, "_attr", lambda pair, extra=0: (pair, extra))
    line = "│" + "x" * 98 + "│"
    selected_attr = (tui.PAIR_SELECTED, tui.curses.A_BOLD | tui.curses.A_REVERSE)

    main = FakeScreen(column_limit=100)
    tui._draw_selected_row(
        main, 0, line, 100, selected_attr, preserve_box_border=True
    )
    assert main.calls == [(0, 1, "x" * 98, 98, selected_attr)]

    tree = FakeScreen(column_limit=100)
    tui._draw_selected_row(
        tree, 0, line, 100, selected_attr, preserve_box_border=False
    )
    assert tree.calls == [(0, 0, line, 100, selected_attr)]


def test_unowned_tags_still_open_a_confirmation_and_report_at_execution(monkeypatch):
    calls: list[str] = []

    class Error(Exception):
        pass

    class Process:
        def __init__(self, pid):
            assert pid == 10

        def create_time(self):
            return 100.0

        def username(self):
            return "bob"

        def kill(self):
            calls.append("kill")

    frame = process_frame()
    frame.processes[0].user = "bob"
    state = tui.UiState(
        tagged_pids={10},
        tagged_processes={10: (100.0, "bob")},
    )
    monkeypatch.setattr(tui.getpass, "getuser", lambda: "alice")
    monkeypatch.setattr(tui.os, "geteuid", lambda: 1000)
    monkeypatch.setitem(
        sys.modules, "psutil", SimpleNamespace(Process=Process, Error=Error)
    )

    tui._begin_signal(state, frame, ProcessSignal.KILL, readonly=False)

    assert state.pending_signal == ProcessSignal.KILL
    assert state.pending_signal_targets == ((10, 100.0),)
    tui._execute_pending_signal(state)
    assert calls == []
    assert "another user" in (state.status_message or "")


def test_mixed_tag_signal_reports_successes_and_failures(monkeypatch):
    calls: list[int] = []

    class Error(Exception):
        pass

    class Process:
        def __init__(self, pid):
            self.pid = pid

        def create_time(self):
            return float(self.pid * 10)

        def username(self):
            return "alice" if self.pid == 10 else "bob"

        def kill(self):
            calls.append(self.pid)

    monkeypatch.setattr(tui.getpass, "getuser", lambda: "alice")
    monkeypatch.setattr(tui.os, "geteuid", lambda: 1000)
    monkeypatch.setitem(
        sys.modules, "psutil", SimpleNamespace(Process=Process, Error=Error)
    )
    state = tui.UiState(
        pending_signal=ProcessSignal.KILL,
        pending_signal_targets=((10, 100.0), (20, 200.0)),
    )

    tui._execute_pending_signal(state)

    assert calls == [10]
    assert "Sent kill signal to 1 process" in (state.status_message or "")
    assert "PID 20: process is owned by another user" in (state.status_message or "")


def test_signal_dialog_navigation_aliases_and_numeric_choices(monkeypatch):
    state = tui.UiState(
        pending_signal=ProcessSignal.TERMINATE,
        pending_signal_targets=((10, 100.0),),
    )
    executed: list[ProcessSignal | None] = []
    monkeypatch.setattr(
        tui,
        "_execute_pending_signal",
        lambda value: executed.append(value.pending_signal),
    )

    tui._handle_signal_dialog_key(ord("["), state)
    assert state.pending_signal_option == 3
    tui._handle_signal_dialog_key(ord("]"), state)
    assert state.pending_signal_option == 0
    tui._handle_signal_dialog_key(ord("2"), state)
    assert executed == [ProcessSignal.KILL]

    state.pending_signal = ProcessSignal.TERMINATE
    state.pending_signal_targets = ((10, 100.0),)
    tui._handle_signal_dialog_key(ord("n"), state)
    assert state.pending_signal is None


def test_signal_dialog_y_aliases_confirm_selected_signal(monkeypatch):
    executed: list[ProcessSignal | None] = []
    monkeypatch.setattr(
        tui,
        "_execute_pending_signal",
        lambda value: executed.append(value.pending_signal),
    )

    for key in (ord("y"), ord("Y")):
        state = tui.UiState(
            pending_signal=ProcessSignal.TERMINATE,
            pending_signal_targets=((10, 100.0),),
        )
        tui._handle_signal_dialog_key(key, state)

    assert executed == [ProcessSignal.TERMINATE, ProcessSignal.TERMINATE]


def test_signal_modal_uses_full_button_hitboxes_and_dims_base(monkeypatch):
    dialog = tui.render_signal_dialog(
        [(10, "alice")],
        width=100,
        signal_name="SIGKILL",
        current_option=1,
    )
    dialog_x, dialog_y = 10, 5
    buttons = tui._signal_modal_buttons(dialog, dialog_x, dialog_y)
    options_row = next(index for index, line in enumerate(dialog) if "SIGTERM" in line)
    kill_x = dialog[options_row].index("SIGKILL") + dialog_x
    assert buttons[kill_x, dialog_y + options_row - 1] == 1
    assert buttons[kill_x, dialog_y + options_row] == 1
    assert buttons[kill_x, dialog_y + options_row + 1] == 1

    monkeypatch.setattr(tui, "_attr", lambda pair, extra=0: (pair, extra))
    screen = FakeScreen(column_limit=120)
    tui._draw_signal_dialog_line(
        screen,
        0,
        "underlying screen".ljust(100),
        dialog[options_row],
        dialog_x,
        100,
        1,
    )
    assert screen.calls[0][-1][0] == tui.PAIR_DIM
    assert any(
        "SIGKILL" in text
        and attr[0] == tui.PAIR_SELECTED
        and attr[1] & tui.curses.A_REVERSE
        for _, _, text, _, attr in screen.calls
    )

    selected_button = tui._signal_button_geometry(dialog, 1)
    assert selected_button is not None
    button_top = FakeScreen(column_limit=120)
    tui._draw_signal_dialog_line(
        button_top,
        0,
        "underlying screen".ljust(100),
        dialog[options_row - 1],
        dialog_x,
        100,
        1,
        dialog_row=options_row - 1,
        selected_button=selected_button,
    )
    assert any(attr[0] == tui.PAIR_SELECTED for _, _, _, _, attr in button_top.calls)


def test_signal_button_click_executes_the_selected_action_immediately(monkeypatch):
    state = tui.UiState(
        pending_signal=ProcessSignal.TERMINATE,
        pending_signal_targets=((10, 100.0),),
    )
    executed: list[ProcessSignal | None] = []
    monkeypatch.setattr(
        tui,
        "_execute_pending_signal",
        lambda value: executed.append(value.pending_signal),
    )
    button = getattr(tui.curses, "BUTTON1_CLICKED", 0x4)
    monkeypatch.setattr(tui.curses, "getmouse", lambda: (0, 8, 9, 0, button))

    tui._handle_mouse(
        state,
        process_frame(),
        mouse_rows=None,
        modal_buttons={(8, 9): 2},
    )

    assert executed == [ProcessSignal.INTERRUPT]


def test_dialog_overlay_is_terminal_cell_safe_for_cjk():
    lines = ["背景" * 30]
    dialog = ["│ 用户开发者 │"]

    overlaid = tui._overlay_dialog(lines, dialog, width=40, height=5)

    assert all(tui.cell_width(line) == 40 for line in overlaid)
    assert "用户开发者" in overlaid[2]


def test_help_colors_key_groups_and_dims_readonly_signal_actions(monkeypatch):
    lines = tui.render_help_screen(118, 40, readonly=True).lines
    monkeypatch.setattr(tui, "_attr", lambda pair, extra=0: (pair, extra))

    header = FakeScreen(column_limit=118)
    tui._draw_help_line(header, 0, lines[0], 118, readonly=True)
    assert header.calls[0][-1][0] == tui.PAIR_HEADER

    readonly_row = FakeScreen(column_limit=118)
    tui._draw_help_line(readonly_row, 14, lines[14], 118, readonly=True)
    assert any(
        column == 39 and attr[0] == tui.PAIR_DIM
        for _, column, _, _, attr in readonly_row.calls
    )

    writable_row = FakeScreen(column_limit=118)
    tui._draw_help_line(writable_row, 14, lines[14], 118, readonly=False)
    assert any(
        column == 39 and attr[0] == tui.PAIR_HOT
        for _, column, _, _, attr in writable_row.calls
    )


def test_metrics_graphs_use_dedicated_section_and_intensity_colors(monkeypatch):
    left_width, right_width = 24, 25
    lines = [
        "╞" + "═" * left_width + "╤" + "═" * right_width + "╡",
        "│"
        + " MAX CPU: 50% ⣿".ljust(left_width)
        + "│"
        + " MAX GPU-MEM: 90% ⣿".ljust(right_width)
        + "│",
        "├" + "─" * left_width + "┼" + "─" * right_width + "┤",
        "│"
        + " HOST-MEM: 20% ⣿".ljust(left_width)
        + "│"
        + " GPU-SM: 50% ⣿".ljust(right_width)
        + "│",
        "╘" + "═" * left_width + "╧" + "═" * right_width + "╛",
    ]
    context = tui._metrics_graph_context(lines)
    assert context[1][:2] == (tui.PAIR_HEADER, tui.PAIR_HOT)
    assert context[3][:2] == (tui.PAIR_MEM, tui.PAIR_WARN)

    monkeypatch.setattr(tui, "_attr", lambda pair, extra=0: (pair, extra))
    upper = FakeScreen(column_limit=120)
    tui._draw_metrics_line(upper, 1, lines[1], 120, context[1], semantic_line=lines[1])
    graph_attrs = [attr for _, _, text, _, attr in upper.calls if "⣿" in text]
    assert [attr[0] for attr in graph_attrs] == [tui.PAIR_HEADER, tui.PAIR_HOT]

    lower = FakeScreen(column_limit=120)
    tui._draw_metrics_line(lower, 3, lines[3], 120, context[3], semantic_line=lines[3])
    graph_attrs = [attr for _, _, text, _, attr in lower.calls if "⣿" in text]
    assert [attr[0] for attr in graph_attrs] == [tui.PAIR_MEM, tui.PAIR_WARN]


def test_new_layout_rows_are_detected_for_colored_drawing():
    device_row = "│   0  42%  63C  P0     215W/350W │       59GiB / 64GiB │     88%      Default │ MEM: ████████░░ 92%"
    second_column = "│   8  42%  63C  P0     215W/350W │       59GiB / 64GiB │     88%      Default │"
    two_column_device_row = f"{device_row} {second_column}"
    process_row = "│    0  423901    alice  51200MiB  88    64   312%  18GiB  4:27:05 python train.py │"
    host_row = "│ CPU:  23%  ████░░░░░░░░░░░░░░░░ MEM:  33% 42.3GiB/128GiB │"
    version_row = "│ MXTOP 0.1.5  Driver Version: 2.31.0.5 │"

    assert tui._is_device_data_line(device_row)
    assert tui._is_device_data_line(two_column_device_row)
    assert tui._is_process_data_line(process_row)
    assert tui._is_host_data_line(host_row)
    assert tui._is_version_line(version_row)


def test_device_core_cells_use_combined_tui_color(monkeypatch):
    monkeypatch.setattr(tui, "_attr", lambda pair, extra=0: pair)
    screen = FakeScreen(column_limit=160)
    line = (
        "│ N/A   N/A  N/A     20W / 350W │   4.00GiB / 64.00GiB │"
        "      4%      Default │"
    )

    tui._draw_device_data_line(screen, 0, line, 120, display_level=0)

    colored_segments = {(text, attr) for _, _, text, _, attr in screen.calls}
    assert any(
        "20W / 350W" in text and attr == tui.PAIR_GOOD
        for text, attr in colored_segments
    )
    assert any(
        "4.00GiB / 64.00GiB" in text and attr == tui.PAIR_GOOD
        for text, attr in colored_segments
    )
    assert any(
        "4%" in text and attr == tui.PAIR_GOOD for text, attr in colored_segments
    )

    hot_screen = FakeScreen(column_limit=160)
    hot_line = (
        "│ N/A   N/A  N/A    330W / 350W │  56.00GiB / 64.00GiB │"
        "     94%      Default │"
    )

    tui._draw_device_data_line(hot_screen, 0, hot_line, 120, display_level=2)

    hot_segments = {(text, attr) for _, _, text, _, attr in hot_screen.calls}
    assert any(
        "330W / 350W" in text and attr == tui.PAIR_HOT for text, attr in hot_segments
    )
    assert any(
        "56.00GiB / 64.00GiB" in text and attr == tui.PAIR_HOT
        for text, attr in hot_segments
    )
    assert any("94%" in text and attr == tui.PAIR_HOT for text, attr in hot_segments)


def test_unselected_device_rows_are_dimmed(monkeypatch):
    monkeypatch.setattr(tui.curses, "has_colors", lambda: False)
    screen = FakeScreen(column_limit=160)
    line = "│ N/A   N/A  N/A     20W / 350W │   4.00GiB / 64.00GiB │      4%      Default │"

    tui._draw_device_data_line(screen, 0, line, 120, display_level=0, dim=True)

    core_attrs = [attr for _, _, text, _, attr in screen.calls if "20W / 350W" in text]
    assert core_attrs and core_attrs[0] & tui.curses.A_DIM


def test_dense_device_cells_color_and_dim_independently(monkeypatch):
    monkeypatch.setattr(tui, "_attr", lambda pair, extra=0: (pair, extra))
    devices = [
        DeviceSnapshot(
            index=index,
            temperature_c=40,
            power_w=80,
            gpu_util_percent=value,
            memory_util_percent=value,
        )
        for index, value in enumerate((4.0, 45.0, 94.0, *([4.0] * 14)))
    ]
    frame = FrameSnapshot(devices=devices, processes=[])
    lines = render_device_panel(frame, 120, LayoutMode.COMPACT, compact=True)
    contexts = dense_device_row_context(lines, frame)
    row, context = next(iter(contexts.items()))
    screen = FakeScreen(column_limit=120)

    tui._draw_dense_device_data_line(
        screen,
        0,
        lines[row],
        120,
        context,
        selected_gpu_index=1,
    )

    by_gpu = {
        int(text.split()[0]): attr
        for _row, _column, text, _count, attr in screen.calls
        if text.strip() and text.split()[0].isdigit()
    }
    assert by_gpu[0][0] == tui.PAIR_GOOD
    assert by_gpu[1][0] == tui.PAIR_WARN
    assert by_gpu[2][0] == tui.PAIR_HOT
    assert by_gpu[0][1] & tui.curses.A_DIM
    assert not by_gpu[1][1] & tui.curses.A_DIM
    assert by_gpu[2][1] & tui.curses.A_DIM


def test_ascii_lines_keep_semantic_tui_coloring(monkeypatch):
    monkeypatch.setattr(tui, "_attr", lambda pair, extra=0: pair)
    device = (
        "│   0 N/A  N/A N/A    20W / 350W │   4.00GiB / 64.00GiB │"
        "      4%      Default │ MEM: ███░░ 40% │"
    )
    process = "│    0      10 C   alice  512.0MiB   4     1     5     1  1:00  python │"
    screen = FakeScreen(column_limit=180)

    tui._draw_line(
        screen,
        1,
        tui.render_ascii(device),
        170,
        device_level=0,
        semantic_line=device,
    )
    tui._draw_line(
        screen,
        2,
        tui.render_ascii(process),
        170,
        process_context=(0, True),
        semantic_line=process,
    )

    assert any(
        "20W / 350W" in text and attr == tui.PAIR_GOOD
        for _, _, text, _, attr in screen.calls
    )
    assert any(
        text == "MEM: " and attr == tui.PAIR_HEADER
        for _, _, text, _, attr in screen.calls
    )
    assert any(
        text == "0" and attr == tui.PAIR_GOOD for _, _, text, _, attr in screen.calls
    )
