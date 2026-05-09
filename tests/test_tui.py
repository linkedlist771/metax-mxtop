from mxtop import tui
from mxtop.models import FrameSnapshot


class FakeScreen:
    def __init__(self):
        self.calls = []

    def addnstr(self, row, column, text, count, attr=0):
        if column >= 8 or count <= 0:
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
    def raise_interrupt(_main):
        raise KeyboardInterrupt

    monkeypatch.setattr(tui.curses, "wrapper", raise_interrupt)

    assert tui.run_tui(FakeBackend(), 1.0) == 130
