from __future__ import annotations

from typing import Protocol

from mxtop.backends.mxsmi import MxSmiBackend
from mxtop.backends.pymxsml import PymxsmlBackend
from mxtop.models import FrameSnapshot


class TelemetryBackend(Protocol):
    name: str

    def snapshot(self) -> FrameSnapshot: ...


class _PrefetchedBackend:
    """Return backend auto-detection's successful probe exactly once."""

    def __init__(self, backend: TelemetryBackend, frame: FrameSnapshot) -> None:
        self.name = backend.name
        self.process_context_types = getattr(backend, "process_context_types", None)
        self._backend = backend
        self._frame: FrameSnapshot | None = frame

    def snapshot(self) -> FrameSnapshot:
        if self._frame is not None:
            frame = self._frame
            self._frame = None
            return frame
        return self._backend.snapshot()


def create_backend(name: str = "auto") -> TelemetryBackend:
    if name == "pymxsml":
        return PymxsmlBackend()
    if name == "mxsmi":
        return MxSmiBackend()
    if name != "auto":
        raise ValueError(f"unknown backend: {name}")

    errors: list[str] = []
    for backend_type in (PymxsmlBackend, MxSmiBackend):
        try:
            backend = backend_type()
            frame = backend.snapshot()
            return _PrefetchedBackend(backend, frame)
        except Exception as exc:
            errors.append(f"{backend_type.__name__}: {exc}")
    raise RuntimeError("no MetaX telemetry backend available: " + "; ".join(errors))
