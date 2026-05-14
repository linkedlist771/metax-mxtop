from __future__ import annotations

from dataclasses import dataclass
import threading
import time

from mxtop.backends import TelemetryBackend
from mxtop.models import FrameSnapshot


@dataclass(slots=True)
class SamplerState:
    frame: FrameSnapshot | None = None
    error: str | None = None
    last_updated: float | None = None
    refreshing: bool = False
    version: int = 0


class SnapshotSampler:
    def __init__(self, backend: TelemetryBackend, interval: float) -> None:
        self.backend = backend
        self.interval = max(0.25, interval)
        self._condition = threading.Condition()
        self._state = SamplerState()
        self._stopped = False
        self._refresh_requested = True
        self._thread = threading.Thread(target=self._run, name="mxtop-sampler", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        with self._condition:
            self._stopped = True
            self._condition.notify_all()
        self._thread.join(timeout=2.0)

    def refresh_now(self) -> None:
        with self._condition:
            self._refresh_requested = True
            self._condition.notify_all()

    def snapshot(self) -> SamplerState:
        with self._condition:
            return self._copy_state_unlocked()

    def wait_for_frame(self, timeout: float | None = None) -> SamplerState:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while self._state.frame is None and self._state.error is None and not self._stopped:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    break
                self._condition.wait(remaining)
            return self._copy_state_unlocked()

    def wait_for_version(self, version: int, timeout: float | None = None) -> SamplerState:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while self._state.version <= version and not self._stopped:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    break
                self._condition.wait(remaining)
            return self._copy_state_unlocked()

    def _copy_state_unlocked(self) -> SamplerState:
        return SamplerState(
            frame=self._state.frame,
            error=self._state.error,
            last_updated=self._state.last_updated,
            refreshing=self._state.refreshing,
            version=self._state.version,
        )

    def _run(self) -> None:
        next_refresh = 0.0
        while True:
            with self._condition:
                while not self._stopped and not self._refresh_requested:
                    remaining = next_refresh - time.monotonic()
                    if remaining <= 0:
                        break
                    self._condition.wait(remaining)
                if self._stopped:
                    return
                self._refresh_requested = False
                self._state.refreshing = True
                self._condition.notify_all()

            try:
                frame = self.backend.snapshot()
            except Exception as exc:
                with self._condition:
                    self._state.error = str(exc)
                    self._state.refreshing = False
                    self._state.last_updated = time.time()
                    self._state.version += 1
                    self._condition.notify_all()
            else:
                with self._condition:
                    self._state.frame = frame
                    self._state.error = None
                    self._state.refreshing = False
                    self._state.last_updated = frame.timestamp
                    self._state.version += 1
                    self._condition.notify_all()

            next_refresh = time.monotonic() + self.interval
