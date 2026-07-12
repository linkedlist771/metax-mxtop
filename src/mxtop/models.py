from __future__ import annotations

from dataclasses import asdict, dataclass, field
import time

from mxtop._compat import DATACLASS_SLOTS

PROCESS_CREATE_TIME_TOLERANCE = 0.01


@dataclass(**DATACLASS_SLOTS)
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
    memory_bandwidth_util_percent: float | None = None
    memory_used_bytes: int | None = None
    memory_total_bytes: int | None = None
    memory_free_bytes: int | None = None
    fan_percent: float | None = None
    ecc_status: str | None = None
    ecc_errors: int | None = None
    persistence_mode: str | None = None
    performance_state: str | None = None
    driver_version: str | None = None
    maca_version: str | None = None
    display_active: str | None = None
    compute_mode: str | None = None
    metaxlink: str | None = None
    gpu_clock_mhz: float | None = None
    memory_clock_mhz: float | None = None


@dataclass(**DATACLASS_SLOTS)
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
    gpu_memory_bandwidth_util_percent: float | None = None
    memory_util_percent: float | None = None
    identity: str | None = None
    create_time: float | None = None

    @property
    def selection_key(self) -> str:
        base = self.identity or f"{self.gpu_index}:{self.pid}"
        if self.create_time is None:
            return base
        return f"{base}:{self.create_time:.6f}"


@dataclass(**DATACLASS_SLOTS)
class FrameSnapshot:
    devices: list[DeviceSnapshot]
    processes: list[ProcessSnapshot]
    backend: str = "unknown"
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(**DATACLASS_SLOTS)
class NodeSnapshot:
    """One remote node's telemetry for the cluster dashboard."""

    hostname: str
    reachable: bool = False
    frame: FrameSnapshot | None = None
    error: str | None = None
    latency_ms: float | None = None


@dataclass(**DATACLASS_SLOTS)
class ClusterSnapshot:
    nodes: list[NodeSnapshot] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
