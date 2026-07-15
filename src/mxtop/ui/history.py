"""Braille history graphs for the host panel, visually matching nvitop.

Each character column packs two consecutive samples (left/right braille dot
columns); the newest sample sits at the right edge and the chart scrolls left.
Implemented from the Unicode braille bit layout (U+2800 block), not copied
from nvitop (which is GPL-3 while this project is MIT).
"""

from __future__ import annotations

from collections import deque
import time

_BRAILLE_BASE = 0x2800
# Cumulative dot bits for one braille column at fill levels 0-4.
# Upward graphs grow from the bottom dots (7, 3, 2, 1 / 8, 6, 5, 4);
# upside-down graphs hang from the top dots (1, 2, 3, 7 / 4, 5, 6, 8).
_LEFT_UP = (0, 0x40, 0x44, 0x46, 0x47)
_RIGHT_UP = (0, 0x80, 0xA0, 0xB0, 0xB8)
_LEFT_DOWN = (0, 0x01, 0x03, 0x07, 0x47)
_RIGHT_DOWN = (0, 0x08, 0x18, 0x38, 0xB8)


class HistoryGraph:
    """Fixed-bound (0-100) sample history rendered as braille columns."""

    def __init__(self, height: int, *, upsidedown: bool = False, maxlen: int = 4096) -> None:
        self.height = height
        self.upsidedown = upsidedown
        self.samples: deque[float | None] = deque(maxlen=maxlen)

    def add(self, value: float | None) -> None:
        self.samples.append(value)

    @property
    def last_value(self) -> float | None:
        return self.samples[-1] if self.samples else None

    def _column_levels(self, value: float | None) -> list[int]:
        """Dots lit per cell (bottom-up), 0-4 for each of the ``height`` cells."""
        if value is None:
            return [0] * self.height
        scaled = self.height * min(max(float(value), 0.0), 100.0) / 100.0
        # Keep a visible tick for zero/near-zero samples so idle still draws.
        scaled = max(scaled, 0.2)
        return [min(max(round(4 * (scaled - cell)), 0), 4) for cell in range(self.height)]

    def render(self, width: int) -> list[str]:
        if width <= 0:
            return [""] * self.height
        needed = 2 * width
        samples = list(self.samples)[-needed:]
        samples = [None] * (needed - len(samples)) + samples
        left_bits = _LEFT_DOWN if self.upsidedown else _LEFT_UP
        right_bits = _RIGHT_DOWN if self.upsidedown else _RIGHT_UP
        columns = [
            (self._column_levels(samples[2 * x]), self._column_levels(samples[2 * x + 1]))
            for x in range(width)
        ]
        lines: list[str] = []
        for row in range(self.height):
            cell = row if self.upsidedown else self.height - 1 - row
            chars: list[str] = []
            for left_levels, right_levels in columns:
                code = left_bits[left_levels[cell]] | right_bits[right_levels[cell]]
                chars.append(chr(_BRAILLE_BASE + code) if code else " ")
            lines.append("".join(chars))
        return lines


_METRICS = ("cpu", "memory", "swap", "gpu_memory", "gpu_utilization")


class HostHistory:
    """The five host-panel graphs, bucketing samples into fixed intervals.

    Mirrors nvitop's layout: CPU (5 rows up), virtual memory (4 rows hanging
    down), swap (1 row up), and average GPU memory/utilization (5 rows each)
    for the right-hand column on wide terminals.
    """

    def __init__(self, interval: float = 1.0) -> None:
        self.interval = interval
        self.cpu = HistoryGraph(5)
        self.memory = HistoryGraph(4, upsidedown=True)
        self.swap = HistoryGraph(1)
        self.gpu_memory = HistoryGraph(5)
        self.gpu_utilization = HistoryGraph(5, upsidedown=True)
        self._graphs = {
            "cpu": self.cpu,
            "memory": self.memory,
            "swap": self.swap,
            "gpu_memory": self.gpu_memory,
            "gpu_utilization": self.gpu_utilization,
        }
        self._buffer: dict[str, list[float]] = {name: [] for name in _METRICS}
        self._last_flush: float | None = None
        self._gpu_scope: int | None = None

    def reset(self) -> None:
        for graph in self._graphs.values():
            graph.samples.clear()
        for bucket in self._buffer.values():
            bucket.clear()
        self._last_flush = None
        self._gpu_scope = None

    def set_gpu_scope(self, gpu_index: int | None) -> None:
        """Switch the GPU graphs without discarding host CPU/memory history."""

        if gpu_index == self._gpu_scope:
            return
        self._gpu_scope = gpu_index
        self.gpu_memory.samples.clear()
        self.gpu_utilization.samples.clear()
        self._buffer["gpu_memory"].clear()
        self._buffer["gpu_utilization"].clear()

    def sample(
        self,
        *,
        cpu: float | None = None,
        memory: float | None = None,
        swap: float | None = None,
        gpu_memory: float | None = None,
        gpu_utilization: float | None = None,
        now: float | None = None,
    ) -> None:
        now = time.monotonic() if now is None else now
        values = {
            "cpu": cpu,
            "memory": memory,
            "swap": swap,
            "gpu_memory": gpu_memory,
            "gpu_utilization": gpu_utilization,
        }
        for name, value in values.items():
            if value is not None:
                self._buffer[name].append(float(value))
        if self._last_flush is None:
            self._last_flush = now
            return
        if now - self._last_flush >= self.interval:
            for name, bucket in self._buffer.items():
                average = sum(bucket) / len(bucket) if bucket else None
                self._graphs[name].add(average)
                bucket.clear()
            self._last_flush = now
