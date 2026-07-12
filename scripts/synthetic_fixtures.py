"""Canonical deterministic telemetry used by previews, galleries, and audits."""

from __future__ import annotations

from contextlib import contextmanager
import math
import os
import time
from collections.abc import Callable, Iterator

from mxtop.models import DeviceSnapshot, FrameSnapshot, ProcessSnapshot
from mxtop.ui import panels as ui_panels

GIB = 1024**3
MIB = 1024**2
DEVICE_MEMORY_BYTES = 64 * GIB
HOST_MEMORY_BYTES = 128 * GIB
FIXED_TIMESTAMP = 1_768_653_296.0  # Sat Jan 17 12:34:56 2026 UTC
DRIVER_VERSION = "2.31.0.5"
MACA_VERSION = "4.3.1"
HOST_USER = "alice"
HOST_NAME = "metax-dgx"
HOST_CONTEXT = f"{HOST_USER}@{HOST_NAME}"
HOST_UPTIME_TEXT = "12.3 days"

_BUS_IDS = (
    "1a",
    "3d",
    "5e",
    "81",
    "a2",
    "c3",
    "e4",
    "f5",
    "18",
    "39",
    "5a",
    "7b",
    "9c",
    "bd",
    "de",
    "ef",
)


def _host_metrics() -> tuple[float, str, float, str, float]:
    return 23.4, "42.24GiB", 33.0, "0B", 0.0


def _host_memory_total() -> int:
    return HOST_MEMORY_BYTES


def _load_average_text() -> str:
    return "Load Average:  1.42  1.85  2.07"


def _user_host() -> str:
    return HOST_CONTEXT


def _uptime_text() -> str:
    return HOST_UPTIME_TEXT


def install_host_stubs() -> None:
    """Replace live host lookups with the deterministic preview host."""
    ui_panels._host_metrics = _host_metrics  # type: ignore[assignment]
    ui_panels._host_memory_total = _host_memory_total  # type: ignore[assignment]
    ui_panels._load_average_text = _load_average_text  # type: ignore[assignment]
    ui_panels._user_host = _user_host  # type: ignore[assignment]
    if hasattr(ui_panels, "_uptime_text"):
        ui_panels._uptime_text = _uptime_text  # type: ignore[assignment]


def seed_host_history() -> None:
    """Reset and fill every host graph with the same waveform."""
    history = ui_panels._HOST_HISTORY
    history.reset()
    now = time.monotonic() - 170 * 1.1
    for step in range(170):
        now += 1.1
        wave = 50.0 + 45.0 * math.sin(step / 9.0)
        history.sample(
            cpu=18.0 + wave / 3.0,
            memory=25.0 + wave / 4.0,
            swap=0.0,
            gpu_memory=30.0 + wave / 2.0,
            gpu_utilization=wave,
            now=now,
        )


def prepare_render() -> None:
    """Put global renderer inputs into their deterministic initial state."""
    install_host_stubs()
    seed_host_history()


@contextmanager
def utc_timezone() -> Iterator[None]:
    """Make ``datetime.fromtimestamp`` stable across Linux and macOS hosts."""
    previous = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    tzset = getattr(time, "tzset", None)
    if tzset is not None:
        tzset()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        if tzset is not None:
            tzset()


def _device(
    index: int,
    *,
    name: str = "MetaX C500",
    gpu: float | None = 0.0,
    mem_pct: float | None = 0.0,
    mbw: float | None = 0.0,
    temp: float | None = 38.0,
    power: float | None = 60.0,
    power_limit: float | None = 350.0,
    fan: float | None = 30.0,
    perf: str | None = "P0",
    persistence: str | None = "Enabled",
    bdf: str | None = None,
    driver: str | None = DRIVER_VERSION,
    maca: str | None = MACA_VERSION,
    ecc: int | None = 0,
    compute_mode: str | None = "Default",
) -> DeviceSnapshot:
    memory_used = None if mem_pct is None else round(mem_pct / 100.0 * DEVICE_MEMORY_BYTES)
    optional: dict[str, object] = {}
    fields = DeviceSnapshot.__dataclass_fields__
    if "gpu_clock_mhz" in fields:
        optional["gpu_clock_mhz"] = None if gpu is None else 1450.0 + 2.0 * gpu
    if "memory_clock_mhz" in fields:
        optional["memory_clock_mhz"] = None if mbw is None else 1600.0 + mbw
    return DeviceSnapshot(
        index=index,
        name=name,
        bdf=bdf
        or f"{index // len(_BUS_IDS):04x}:{_BUS_IDS[index % len(_BUS_IDS)]}:00.0",
        uuid=f"MX-{index:02d}-0123456789ABCDEF",
        temperature_c=temp,
        power_w=power,
        power_limit_w=power_limit,
        gpu_util_percent=gpu,
        memory_util_percent=mem_pct,
        memory_bandwidth_util_percent=mbw,
        memory_used_bytes=memory_used,
        memory_total_bytes=None if mem_pct is None else DEVICE_MEMORY_BYTES,
        memory_free_bytes=None if memory_used is None else DEVICE_MEMORY_BYTES - memory_used,
        fan_percent=fan,
        ecc_errors=ecc,
        persistence_mode=persistence,
        performance_state=perf,
        driver_version=driver,
        maca_version=maca,
        display_active="Disabled" if gpu is not None else None,
        compute_mode=compute_mode,
        metaxlink="Active" if gpu is not None else None,
        **optional,
    )


def _process(
    gpu_index: int,
    pid: int,
    *,
    name: str = "python",
    user: str = HOST_USER,
    gpu_memory_mib: float = 1024.0,
    gpu_util: float | None = 30.0,
    gmbw: float | None = 25.0,
    cpu: float | None = 12.0,
    mem_pct: float | None = 4.0,
    runtime: float | None = 3600.0,
    process_type: str | None = "C",
    command: str = "python train.py",
) -> ProcessSnapshot:
    optional: dict[str, object] = {}
    if "create_time" in ProcessSnapshot.__dataclass_fields__:
        optional["create_time"] = None if runtime is None else FIXED_TIMESTAMP - runtime
    return ProcessSnapshot(
        gpu_index=gpu_index,
        pid=pid,
        name=name,
        gpu_memory_bytes=round(gpu_memory_mib * MIB),
        user=user,
        command=command,
        cpu_percent=cpu,
        host_memory_bytes=None if mem_pct is None else round(mem_pct / 100.0 * HOST_MEMORY_BYTES),
        runtime_seconds=runtime,
        process_type=process_type,
        gpu_util_percent=gpu_util,
        gpu_memory_bandwidth_util_percent=gmbw,
        memory_util_percent=mem_pct,
        identity=f"preview:{gpu_index}:{pid}",
        **optional,
    )


def _frame(
    devices: list[DeviceSnapshot],
    processes: list[ProcessSnapshot],
    *,
    backend: str = "pymxsml",
) -> FrameSnapshot:
    return FrameSnapshot(
        devices=devices,
        processes=processes,
        backend=backend,
        timestamp=FIXED_TIMESTAMP,
    )


def frame_three_gpu() -> FrameSnapshot:
    devices = [
        _device(0, gpu=88.0, mem_pct=92.0, mbw=64.0, temp=63.0, power=215.0, fan=42.0),
        _device(1, gpu=74.0, mem_pct=71.0, mbw=48.0, temp=58.0, power=199.0, fan=39.0),
        _device(2, gpu=0.0, mem_pct=4.5, mbw=0.0, temp=41.0, power=78.0, fan=33.0, perf="P8"),
    ]
    processes = [
        _process(
            0,
            423901,
            gpu_memory_mib=51200,
            gpu_util=88.0,
            gmbw=64.0,
            cpu=312.4,
            mem_pct=14.2,
            runtime=4 * 3600 + 27 * 60,
            process_type="C",
            command="python train.py --config configs/llama3-70b.yaml --bf16",
        ),
        _process(
            0,
            423908,
            gpu_memory_mib=4096,
            gpu_util=12.0,
            gmbw=8.0,
            cpu=42.1,
            mem_pct=2.4,
            runtime=27 * 60,
            process_type="G",
            command="python visualize.py --checkpoint /data/ckpt/step-12000",
        ),
        _process(
            1,
            512377,
            user="bob",
            gpu_memory_mib=42000,
            gpu_util=74.0,
            gmbw=48.0,
            cpu=215.0,
            mem_pct=9.8,
            runtime=2 * 86400 + 5 * 3600,
            process_type="C+G",
            command="python -m vllm.entrypoints.api_server --model qwen2-72b",
        ),
        _process(
            1,
            512402,
            user="bob",
            gpu_memory_mib=3000,
            gpu_util=0.0,
            gmbw=0.0,
            cpu=1.2,
            mem_pct=0.9,
            runtime=600,
            process_type=None,
            command="python sampler.py",
        ),
        _process(
            2,
            99001,
            name="metaxctl",
            user="root",
            gpu_memory_mib=128,
            gpu_util=0.0,
            gmbw=0.0,
            cpu=0.0,
            mem_pct=0.1,
            runtime=7 * 86400,
            process_type=None,
            command="/opt/mxdriver/bin/metaxctl serve",
        ),
    ]
    return _frame(devices, processes)


def frame_idle_three() -> FrameSnapshot:
    loads = ((0.0, 3.5, 0.0), (2.0, 4.2, 1.0), (0.0, 2.8, 0.0))
    devices = [
        _device(
            index,
            gpu=gpu,
            mem_pct=memory,
            mbw=mbw,
            temp=37.0 + index,
            power=72.0 + index * 1.5,
            fan=30.0,
            perf="P8",
        )
        for index, (gpu, memory, mbw) in enumerate(loads)
    ]
    processes = [
        _process(
            0,
            99001,
            name="metaxctl",
            user="root",
            gpu_memory_mib=128,
            gpu_util=0.0,
            gmbw=0.0,
            cpu=0.1,
            mem_pct=0.1,
            runtime=12 * 86400,
            process_type=None,
            command="/opt/mxdriver/bin/metaxctl serve",
        ),
    ]
    return _frame(devices, processes)


def frame_mixed_four() -> FrameSnapshot:
    loads = ((5.0, 8.0), (35.0, 40.0), (75.0, 78.0), (98.0, 95.0))
    devices = [
        _device(
            index,
            gpu=gpu,
            mem_pct=memory,
            mbw=gpu * 0.85,
            temp=38.0 + gpu * 0.4,
            power=60.0 + gpu * 2.8,
            fan=25.0 + gpu * 0.55,
        )
        for index, (gpu, memory) in enumerate(loads)
    ]
    processes = [
        _process(0, 100, gpu_memory_mib=512, gpu_util=5, gmbw=4, cpu=4, mem_pct=1.0, command="bash"),
        _process(1, 200, user="bob", gpu_memory_mib=18000, gpu_util=35, gmbw=30, cpu=110, mem_pct=12, command="python evaluate.py --batch 64"),
        _process(2, 300, user="carol", gpu_memory_mib=44000, gpu_util=75, gmbw=66, cpu=380, mem_pct=42, command="python -m torch.distributed.launch train.py"),
        _process(3, 400, user="dave", gpu_memory_mib=62000, gpu_util=98, gmbw=92, cpu=600, mem_pct=68, command="python pretrain.py --bf16 --grad-accum 4"),
        _process(3, 401, name="systemd", user="root", gpu_memory_mib=300, gpu_util=0, gmbw=0, cpu=0.5, mem_pct=0.2, process_type=None, command="systemd"),
    ]
    return _frame(devices, processes)


def frame_heavy_four() -> FrameSnapshot:
    devices = [
        _device(
            index,
            gpu=96.0 - index * 1.5,
            mem_pct=93.0 - index * 0.8,
            mbw=88.0 - index * 1.2,
            temp=78.0 + index,
            power=320.0 + index,
            fan=85.0,
        )
        for index in range(4)
    ]
    users = ("alice", "bob", "carol", "dave")
    processes = [
        _process(
            index,
            410000 + index * 17,
            user=users[index],
            gpu_memory_mib=(92.0 - index * 0.5) / 100.0 * 60 * 1024,
            gpu_util=96.0 - index * 1.5,
            gmbw=88.0 - index * 1.2,
            cpu=320.0 - index * 12,
            mem_pct=15.0 + index,
            runtime=4 * 3600 + index * 600,
            command=f"python -m train --rank {index} --config configs/llama3.yaml",
        )
        for index in range(4)
    ]
    return _frame(devices, processes)


def frame_eight_mixed() -> FrameSnapshot:
    loads = (5, 18, 33, 51, 67, 80, 92, 100)
    devices = [
        _device(
            index,
            gpu=float(value),
            mem_pct=min(99.0, value * 0.85 + 4.0),
            mbw=value * 0.8,
            temp=30.0 + value * 0.45,
            power=50.0 + value * 2.8,
            fan=25.0 + value * 0.6,
        )
        for index, value in enumerate(loads)
    ]
    users = ("alice", "bob", "carol", "dave")
    processes = [
        _process(
            index,
            5000 + index,
            user=users[index % len(users)],
            gpu_memory_mib=value * 600,
            gpu_util=float(value),
            gmbw=value * 0.8,
            cpu=value * 4.0,
            mem_pct=value * 0.6,
            runtime=600 + index * 1200,
            command=f"python -m train --rank {index} --config configs/llama3.yaml",
        )
        for index, value in enumerate(loads)
    ]
    return _frame(devices, processes)


def frame_sixteen_mixed() -> FrameSnapshot:
    utils = (88, 74, 0, 92, 12, 67, 81, 55, 19, 99, 24, 41, 73, 60, 8, 35)
    devices = [
        _device(
            index,
            gpu=float(value),
            mem_pct=min(99.0, value * 0.85 + index * 1.7),
            mbw=value * 0.8,
            temp=38.0 + value // 4,
            power=70.0 + value * 2.4,
            fan=30.0 + value // 5,
            perf="P8" if value == 0 else "P0",
        )
        for index, value in enumerate(utils)
    ]
    users = ("alice", "bob", "carol", "dave")
    processes = [
        _process(
            index,
            410000 + index * 17,
            user=users[index % len(users)],
            gpu_memory_mib=value * 500,
            gpu_util=float(value),
            gmbw=value * 0.8,
            cpu=20.0 + (index * 19) % 200,
            mem_pct=2.0 + index % 5,
            runtime=600 + index * 1234,
            command=f"python -m train --rank {index} --config configs/llama3.yaml",
        )
        for index, value in enumerate(utils[:12])
        if value > 0
    ]
    return _frame(devices, processes)


def _frame_many_mixed(device_count: int) -> FrameSnapshot:
    utils = tuple((17 + index * 37 + (index // 8) * 11) % 101 for index in range(device_count))
    devices = [
        _device(
            index,
            gpu=float(value),
            mem_pct=min(99.0, value * 0.83 + (index % 7) * 1.9),
            mbw=value * 0.78,
            temp=35.0 + value * 0.45,
            power=55.0 + value * 2.7,
            fan=min(95.0, 25.0 + value * 0.62),
            perf="P8" if value < 5 else "P0",
        )
        for index, value in enumerate(utils)
    ]
    process_indices = sorted({*range(0, device_count, 8), device_count - 1})
    users = ("alice", "bob", "carol", "dave")
    processes = [
        _process(
            index,
            620000 + index * 19,
            user=users[index % len(users)],
            gpu_memory_mib=max(512, utils[index] * 500),
            gpu_util=float(utils[index]),
            gmbw=utils[index] * 0.78,
            cpu=20.0 + (index * 23) % 240,
            mem_pct=2.0 + index % 8,
            runtime=900 + index * 911,
            command=f"python -m train --rank {index} --world-size {device_count}",
        )
        for index in process_indices
    ]
    return _frame(devices, processes)


def frame_thirty_two_mixed() -> FrameSnapshot:
    return _frame_many_mixed(32)


def frame_sixty_four_mixed() -> FrameSnapshot:
    return _frame_many_mixed(64)


def frame_single_idle() -> FrameSnapshot:
    return _frame(
        [_device(0, gpu=2.0, mem_pct=1.5625, mbw=1.0, temp=35.0, power=45.0)],
        [],
    )


def frame_single_heavy() -> FrameSnapshot:
    return _frame(
        [_device(0, gpu=99.5, mem_pct=98.4375, mbw=92.0, temp=84.0, power=348.0, fan=98.0)],
        [
            _process(
                0,
                12345,
                gpu_memory_mib=60000,
                gpu_util=99.0,
                gmbw=92.0,
                cpu=420.0,
                mem_pct=72.0,
                command="python train.py --steps=1000000",
            ),
        ],
    )


def frame_missing_telemetry() -> FrameSnapshot:
    devices = [
        _device(
            0,
            gpu=None,
            mem_pct=None,
            mbw=None,
            temp=None,
            power=None,
            power_limit=None,
            fan=None,
            perf=None,
            persistence=None,
            driver=None,
            maca=None,
            ecc=None,
            compute_mode=None,
        ),
        _device(
            1,
            gpu=12.0,
            mem_pct=14.0,
            mbw=None,
            temp=42.0,
            power=80.0,
            power_limit=None,
            fan=None,
            perf="P2",
            persistence="Disabled",
        ),
    ]
    processes = [
        _process(
            0,
            700,
            name="unknown",
            user="ops",
            gpu_memory_mib=512,
            gpu_util=None,
            gmbw=None,
            cpu=None,
            mem_pct=None,
            runtime=None,
            process_type=None,
            command="(unknown)",
        ),
    ]
    return _frame(devices, processes, backend="mxsmi")


def frame_nan_values() -> FrameSnapshot:
    return _frame(
        [_device(0, gpu=float("nan"), mem_pct=50.0, mbw=float("inf"))],
        [_process(0, 800, gpu_util=float("nan"), gmbw=float("inf"), cpu=float("nan"))],
        backend="synthetic",
    )


FrameBuilder = Callable[[], FrameSnapshot]

FRAME_BUILDERS: dict[str, FrameBuilder] = {
    "small": frame_three_gpu,
    "three": frame_three_gpu,
    "idle": frame_idle_three,
    "mixed": frame_mixed_four,
    "mixed4": frame_mixed_four,
    "heavy": frame_heavy_four,
    "eight": frame_eight_mixed,
    "many": frame_sixteen_mixed,
    "sixteen": frame_sixteen_mixed,
    "thirty-two": frame_thirty_two_mixed,
    "32": frame_thirty_two_mixed,
    "sixty-four": frame_sixty_four_mixed,
    "64": frame_sixty_four_mixed,
    "missing": frame_missing_telemetry,
    "nan": frame_nan_values,
    "single-idle": frame_single_idle,
    "single-heavy": frame_single_heavy,
}

SCENARIO_BUILDERS: dict[str, FrameBuilder] = {
    "single-idle": frame_single_idle,
    "single-heavy": frame_single_heavy,
    "mixed-4gpu": frame_mixed_four,
    "eight-mixed": frame_eight_mixed,
    "sixteen-loaded": frame_sixteen_mixed,
    "missing-telemetry": frame_missing_telemetry,
    "nan-values": frame_nan_values,
}


def build_frame(name: str) -> FrameSnapshot:
    return FRAME_BUILDERS[name]()
