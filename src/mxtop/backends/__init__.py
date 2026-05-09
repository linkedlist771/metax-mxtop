from __future__ import annotations

from typing import Protocol

from mxtop.backends.mxsmi import MxSmiBackend
from mxtop.backends.pymxsml import PymxsmlBackend
from mxtop.models import FrameSnapshot


class TelemetryBackend(Protocol):
    name: str

    def snapshot(self) -> FrameSnapshot: ...


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
            _ = backend.snapshot()
            return backend
        except Exception as exc:
            errors.append(f"{backend_type.__name__}: {exc}")
    raise RuntimeError("no MetaX telemetry backend available: " + "; ".join(errors))
