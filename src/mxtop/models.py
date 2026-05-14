from __future__ import annotations

from dataclasses import asdict, dataclass, field
import time


@dataclass(slots=True)
class DeviceSnapshot:
    index: int
    name: str = "MetaX GPU"
    bdf: str | None = None
    uuid: str | None = None
    temperature_c: float | None = None
    power_w: float | None = None
    power_limit_w: float | None = None
    gpu_util_percent: float | None = None
    memory_util_percent: float | None = None
    memory_used_bytes: int | None = None
    memory_total_bytes: int | None = None
    memory_free_bytes: int | None = None
    fan_percent: float | None = None
    ecc_status: str | None = None
    persistence_mode: str | None = None
    performance_state: str | None = None
    driver_version: str | None = None
    metaxlink: str | None = None


@dataclass(slots=True)
class ProcessSnapshot:
    gpu_index: int
    pid: int
    name: str = ""
    gpu_memory_bytes: int | None = None
    user: str | None = None
    command: str | None = None
    cpu_percent: float | None = None
    host_memory_bytes: int | None = None
    runtime_seconds: float | None = None
    process_type: str | None = None
    gpu_util_percent: float | None = None
    memory_util_percent: float | None = None
    identity: str | None = None

    @property
    def selection_key(self) -> str:
        return self.identity or f"{self.gpu_index}:{self.pid}"


@dataclass(slots=True)
class FrameSnapshot:
    devices: list[DeviceSnapshot]
    processes: list[ProcessSnapshot]
    backend: str = "unknown"
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
