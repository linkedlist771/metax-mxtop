"""Benchmark the live TUI loop end to end against deterministic telemetry.

Drives ``mxtop.tui.run_tui`` through a fake curses screen so the measured
cost covers filtering, layout, semantic colorization, and every draw call of
a real repaint. Each scenario also prints a fingerprint of all draw calls
(row, column, text, attributes), so a pure performance change can be checked
for byte-identical output by comparing fingerprints before and after.

Usage::

    python scripts/bench_tui.py            # timings + fingerprints
    python scripts/bench_tui.py --profile  # cProfile the 64-GPU scenario
"""

from __future__ import annotations

import argparse
import cProfile
import curses
import hashlib
import io
import pstats
import statistics
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import synthetic_fixtures as fixtures  # noqa: E402

from mxtop import tui  # noqa: E402
from mxtop.models import FrameSnapshot  # noqa: E402
from mxtop.ui.state import LayoutMode  # noqa: E402

QUIT = ord("q")


class _Screen:
    """Minimal curses window replaying a scripted key sequence."""

    def __init__(self, keys: list[int], size: tuple[int, int]) -> None:
        self._keys = list(keys)
        self._size = size
        self.frames: list[list[tuple]] = []
        self._current: list[tuple] = []
        self.paint_seconds: list[float] = []
        self._paint_started: float | None = None

    def getch(self) -> int:
        return self._keys.pop(0) if self._keys else QUIT

    def getmaxyx(self) -> tuple[int, int]:
        return self._size

    def erase(self) -> None:
        self._paint_started = time.perf_counter()
        self._current = []

    def addnstr(self, row, column, text, count, attr=0) -> None:
        height, width = self._size
        if row >= height or column >= width or count <= 0:
            raise curses.error("out of bounds")
        self._current.append((row, column, text[:count], attr))

    def refresh(self) -> None:
        if self._paint_started is not None:
            self.paint_seconds.append(time.perf_counter() - self._paint_started)
            self._paint_started = None
        self.frames.append(self._current)

    def nodelay(self, _flag) -> None:
        pass

    def timeout(self, _ms) -> None:
        pass

    def keypad(self, _flag) -> None:
        pass


class _Backend:
    name = "bench"

    def __init__(self, frame: FrameSnapshot) -> None:
        self._frame = frame

    def snapshot(self) -> FrameSnapshot:
        return self._frame


class _Sampler:
    """Deterministic stand-in: the frame is available before the first paint."""

    def __init__(self, backend, interval) -> None:
        self.backend = backend
        self._state = tui.SamplerState(
            frame=backend.snapshot(), last_updated=0.0, version=1
        )

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def refresh_now(self) -> None:
        pass

    def snapshot(self):
        return self._state


def _install_fake_curses() -> None:
    for name in (
        "curs_set",
        "noecho",
        "echo",
        "cbreak",
        "nocbreak",
        "endwin",
        "start_color",
        "use_default_colors",
        "mousemask",
        "ungetch",
    ):
        setattr(curses, name, lambda *args, **kwargs: None)
    curses.has_colors = lambda: True
    curses.can_change_color = lambda: False
    curses.init_pair = lambda *args: None
    curses.color_pair = lambda pair: pair << 8
    tui.SnapshotSampler = _Sampler  # type: ignore[assignment]
    tui.HostHistory = _frozen_history  # type: ignore[assignment]


def _frozen_history():
    """Seeded host graphs whose bucket never flushes, for exact fingerprints."""

    fixtures.seed_host_history()
    history = fixtures.ui_panels._HOST_HISTORY
    history.interval = float("inf")
    return history


def run_scenario(
    frame: FrameSnapshot, keys: list[int], size: tuple[int, int], layout: LayoutMode
) -> _Screen:
    screen = _Screen(keys, size)
    curses.initscr = lambda: screen
    options = SimpleNamespace(layout=layout, readonly=False, no_unicode=False)
    real_stdout = sys.stdout
    sys.stdout = io.StringIO()  # swallow the cursor show/hide escapes
    try:
        tui.run_tui(_Backend(frame), 1.0, options)
    finally:
        sys.stdout = real_stdout
    return screen


def _fingerprint(screen: _Screen) -> str:
    digest = hashlib.sha256()
    for frame in screen.frames:
        digest.update(repr(frame).encode())
    return digest.hexdigest()[:16]


SCENARIOS = {
    "3gpu-122x36": ("three", (36, 122), LayoutMode.AUTO),
    "16gpu-172x44": ("sixteen", (44, 172), LayoutMode.AUTO),
    "64gpu-180x44": ("64", (44, 180), LayoutMode.AUTO),
    "64gpu-full-200x60": ("64", (60, 200), LayoutMode.FULL),
}


def _with_processes(frame: FrameSnapshot, count: int) -> FrameSnapshot:
    """Grow the process table to stress sorting, scrolling, and row drawing."""

    base = frame.processes
    extra = []
    for index in range(count):
        source = base[index % len(base)]
        extra.append(
            fixtures._process(
                source.gpu_index,
                700000 + index,
                user=("alice", "bob", "carol")[index % 3],
                gpu_memory_mib=256 + (index * 97) % 8000,
                gpu_util=float((index * 13) % 100),
                cpu=float((index * 7) % 400),
                command=f"python worker.py --shard {index} --data /mnt/data/{index}",
            )
        )
    return FrameSnapshot(
        devices=frame.devices,
        processes=[*base, *extra],
        backend=frame.backend,
        timestamp=frame.timestamp,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repaints", type=int, default=60)
    parser.add_argument("--idle", type=int, default=200)
    parser.add_argument("--processes", type=int, default=150)
    parser.add_argument("--profile", action="store_true")
    args = parser.parse_args()

    _install_fake_curses()
    fixtures.prepare_render()
    with fixtures.utc_timezone():
        # Scroll through the process list: every key forces a full repaint.
        repaint_keys = [curses.KEY_DOWN] * args.repaints
        idle_keys = [-1] * args.idle
        if args.profile:
            frame = _with_processes(fixtures.build_frame("64"), args.processes)
            profiler = cProfile.Profile()
            profiler.enable()
            run_scenario(frame, repaint_keys, (60, 200), LayoutMode.FULL)
            profiler.disable()
            pstats.Stats(profiler).sort_stats("cumulative").print_stats(35)
            pstats.Stats(profiler).sort_stats("tottime").print_stats(25)
            return 0

        print(f"{'scenario':<22}{'repaint ms':>12}{'p95 ms':>9}{'idle us':>10}  fingerprint")
        for label, (fixture, size, layout) in SCENARIOS.items():
            frame = _with_processes(fixtures.build_frame(fixture), args.processes)
            screen = run_scenario(frame, repaint_keys, size, layout)
            paints = sorted(screen.paint_seconds[1:]) or [0.0]
            median = statistics.median(paints) * 1000
            p95 = paints[int(len(paints) * 0.95) - 1] * 1000
            started = time.perf_counter()
            run_scenario(frame, idle_keys, size, layout)
            idle_total = time.perf_counter() - started
            # Subtract the one initial paint to isolate the idle tick cost.
            idle = max(0.0, idle_total - median / 1000) / max(1, args.idle) * 1e6
            print(
                f"{label:<22}{median:>12.2f}{p95:>9.2f}{idle:>10.1f}  {_fingerprint(screen)}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
