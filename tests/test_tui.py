from mxtop import tui
from mxtop.models import FrameSnapshot


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


def test_draw_line_does_not_write_past_current_width(monkeypatch):
    monkeypatch.setattr(tui.curses, "has_colors", lambda: False)
    screen = FakeScreen()

    tui._draw_line(screen, 5, "0    MXC500   74% [████████████] text", width=8)

    assert screen.calls


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


def test_handle_key_updates_sort_and_layout(monkeypatch):
    class FakeSampler:
        def __init__(self):
            self.refreshed = False

        def refresh_now(self):
            self.refreshed = True

    state = tui.UiState()
    sampler = FakeSampler()

    assert tui._handle_key(ord("."), state, None, sampler)
    assert state.process_sort.value == "gpu_memory"
    assert tui._handle_key(ord("/"), state, None, sampler)
    assert state.reverse_sort is True
    assert tui._handle_key(ord("c"), state, None, sampler)
    assert state.layout.value == "compact"
    assert tui._handle_key(ord("r"), state, None, sampler)
    assert sampler.refreshed is True


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


def test_device_usage_fields_use_independent_tui_colors(monkeypatch):
    monkeypatch.setattr(tui, "_attr", lambda pair, extra=0: pair)
    screen = FakeScreen(column_limit=160)
    line = (
        "│ N/A   N/A  N/A     20W / 350W │   4.00GiB / 64.00GiB │"
        "      4%      Default │"
    )

    tui._draw_device_data_line(screen, 0, line, 120)

    colored_segments = {(text, attr) for _, _, text, _, attr in screen.calls}
    assert ("20W / 350W", tui.PAIR_GOOD) in colored_segments
    assert ("4.00GiB / 64.00GiB", tui.PAIR_GOOD) in colored_segments
    assert ("4%", tui.PAIR_GOOD) in colored_segments

    hot_screen = FakeScreen(column_limit=160)
    hot_line = (
        "│ N/A   N/A  N/A    330W / 350W │  56.00GiB / 64.00GiB │"
        "     94%      Default │"
    )

    tui._draw_device_data_line(hot_screen, 0, hot_line, 120)

    hot_segments = {(text, attr) for _, _, text, _, attr in hot_screen.calls}
    assert ("330W / 350W", tui.PAIR_HOT) in hot_segments
    assert ("56.00GiB / 64.00GiB", tui.PAIR_HOT) in hot_segments
    assert ("94%", tui.PAIR_HOT) in hot_segments
