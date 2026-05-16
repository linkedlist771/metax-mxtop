#!/usr/bin/env python3
"""Render a battery of synthetic mxtop frames to stdout for visual review.

Designed to surface alignment issues across loads, GPU counts, and terminal widths.
Run with ``uv run --with psutil python scripts/render_scenarios.py``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mxtop.models import DeviceSnapshot, FrameSnapshot, ProcessSnapshot
from mxtop.rendering import render_once, set_render_style


def _device(
    index: int,
    *,
    name: str = "MXC500",
    gpu: float | None = 0.0,
    mem_used: int | None = 0,
    mem_total: int | None = 64 * 1024**3,
    mbw: float | None = 0.0,
    temp: float | None = 38.0,
    power: float | None = 60.0,
    power_limit: float | None = 350.0,
    fan: float | None = 30.0,
    perf: str | None = "P0",
    persistence: str | None = "Enabled",
    bdf: str | None = None,
    driver: str | None = "2.31.0.1015",
    ecc: int | None = 0,
    compute_mode: str | None = "Default",
) -> DeviceSnapshot:
    mem_util = None if mem_total in (None, 0) or mem_used is None else mem_used / mem_total * 100
    return DeviceSnapshot(
        index=index,
        name=name,
        bdf=bdf or f"0000:{index:02d}:00.0",
        temperature_c=temp,
        power_w=power,
        power_limit_w=power_limit,
        gpu_util_percent=gpu,
        memory_util_percent=mem_util,
        memory_bandwidth_util_percent=mbw,
        memory_used_bytes=mem_used,
        memory_total_bytes=mem_total,
        fan_percent=fan,
        ecc_errors=ecc,
        persistence_mode=persistence,
        performance_state=perf,
        driver_version=driver,
        compute_mode=compute_mode,
    )


def _process(
    gpu_index: int,
    pid: int,
    *,
    user: str = "alice",
    gpu_memory_mib: int = 1024,
    gpu_util: float | None = 30.0,
    gmbw: float | None = 25.0,
    cpu: float | None = 12.0,
    mem_pct: float | None = 4.0,
    runtime: float | None = 3600.0,
    process_type: str | None = "C",
    command: str = "python train.py",
) -> ProcessSnapshot:
    return ProcessSnapshot(
        gpu_index=gpu_index,
        pid=pid,
        name="python",
        gpu_memory_bytes=gpu_memory_mib * 1024**2,
        user=user,
        command=command,
        cpu_percent=cpu,
        host_memory_bytes=int(mem_pct / 100 * 64 * 1024**3) if mem_pct is not None else None,
        runtime_seconds=runtime,
        process_type=process_type,
        gpu_util_percent=gpu_util,
        gpu_memory_bandwidth_util_percent=gmbw,
        memory_util_percent=mem_pct,
    )


def scenario_single_idle() -> FrameSnapshot:
    return FrameSnapshot(
        devices=[_device(0, gpu=2.0, mem_used=1 * 1024**3, mbw=1.0, temp=35.0, power=45.0)],
        processes=[],
        backend="synthetic",
    )


def scenario_single_heavy() -> FrameSnapshot:
    return FrameSnapshot(
        devices=[
            _device(
                0,
                gpu=99.5,
                mem_used=63 * 1024**3,
                mbw=92.0,
                temp=84.0,
                power=348.0,
                fan=98.0,
            )
        ],
        processes=[
            _process(0, 12345, user="alice", gpu_memory_mib=60000, gpu_util=99.0, gmbw=92.0, cpu=420.0, mem_pct=72.0, command="python train.py --steps=1000000"),
        ],
        backend="synthetic",
    )


def scenario_mixed_4gpu() -> FrameSnapshot:
    loads = [(2.0, 0.05), (35.0, 0.4), (78.0, 0.75), (99.0, 0.98)]
    devices = [
        _device(
            i,
            gpu=gpu,
            mem_used=int(64 * 1024**3 * mem_frac),
            mbw=gpu * 0.9,
            temp=35.0 + gpu * 0.4,
            power=45.0 + gpu * 3,
            fan=20.0 + gpu * 0.7,
        )
        for i, (gpu, mem_frac) in enumerate(loads)
    ]
    processes = [
        _process(0, 100, user="alice", gpu_util=2.0, gpu_memory_mib=512, cpu=4.0, mem_pct=1.0, command="bash"),
        _process(1, 200, user="bob", gpu_util=35.0, gpu_memory_mib=18000, gmbw=30.0, cpu=110.0, mem_pct=12.0, command="python evaluate.py"),
        _process(2, 300, user="carol", gpu_util=78.0, gpu_memory_mib=44000, gmbw=70.0, cpu=380.0, mem_pct=42.0, command="python -m torch.distributed.launch train.py"),
        _process(3, 400, user="dave", gpu_util=99.0, gpu_memory_mib=62000, gmbw=95.0, cpu=600.0, mem_pct=68.0, command="python pretrain.py"),
        _process(3, 401, user="root", gpu_util=1.0, gpu_memory_mib=300, gmbw=2.0, cpu=0.5, mem_pct=0.2, command="systemd"),
    ]
    return FrameSnapshot(devices=devices, processes=processes, backend="synthetic")


def scenario_eight_mixed() -> FrameSnapshot:
    loads = [5, 18, 33, 51, 67, 80, 92, 100]
    devices = [
        _device(
            i,
            gpu=value,
            mem_used=int(64 * 1024**3 * value / 100),
            mbw=value * 0.85,
            temp=30.0 + value * 0.45,
            power=50.0 + value * 2.8,
            fan=25.0 + value * 0.6,
        )
        for i, value in enumerate(loads)
    ]
    processes = [
        _process(i, 5000 + i, gpu_util=float(value), gpu_memory_mib=int(value * 600), gmbw=value * 0.85, cpu=float(value) * 4, mem_pct=float(value) * 0.7, command=f"python job_{i}.py")
        for i, value in enumerate(loads)
    ]
    return FrameSnapshot(devices=devices, processes=processes, backend="synthetic")


def scenario_sixteen_loaded() -> FrameSnapshot:
    devices = []
    for i in range(16):
        load = (i + 1) * 6 % 101
        devices.append(
            _device(
                i,
                gpu=float(load),
                mem_used=int(64 * 1024**3 * load / 100),
                mbw=load * 0.9,
                temp=32.0 + load * 0.4,
                power=60.0 + load * 2.7,
                fan=25.0 + load * 0.55,
            )
        )
    processes = [
        _process(i % 16, 9000 + i, user=f"user{i % 4}", gpu_util=float((i + 1) * 6 % 101), gpu_memory_mib=int(((i + 1) * 6 % 101) * 500), cpu=float((i + 1) * 6 % 101) * 3, mem_pct=float((i + 1) * 6 % 101) * 0.6, command=f"python launcher.py --rank {i}")
        for i in range(8)
    ]
    return FrameSnapshot(devices=devices, processes=processes, backend="synthetic")


def scenario_missing_telemetry() -> FrameSnapshot:
    devices = [
        _device(0, gpu=None, mem_used=None, mem_total=None, mbw=None, temp=None, power=None, power_limit=None, fan=None, perf=None, persistence=None, driver=None),
        _device(
            1,
            gpu=12.0,
            mem_used=3 * 1024**3,
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
        _process(0, 700, user="ops", gpu_util=None, gmbw=None, cpu=None, mem_pct=None, runtime=None, command="(unknown)"),
    ]
    return FrameSnapshot(devices=devices, processes=processes, backend="synthetic")


def scenario_nan_values() -> FrameSnapshot:
    return FrameSnapshot(
        devices=[_device(0, gpu=float("nan"), mem_used=int(32 * 1024**3), mbw=float("inf"))],
        processes=[_process(0, 800, gpu_util=float("nan"), gmbw=float("inf"), cpu=float("nan"))],
        backend="synthetic",
    )


SCENARIOS = {
    "single-idle": scenario_single_idle,
    "single-heavy": scenario_single_heavy,
    "mixed-4gpu": scenario_mixed_4gpu,
    "eight-mixed": scenario_eight_mixed,
    "sixteen-loaded": scenario_sixteen_loaded,
    "missing-telemetry": scenario_missing_telemetry,
    "nan-values": scenario_nan_values,
}

WIDTHS = [79, 100, 120, 160, 200]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=list(SCENARIOS), default=None)
    parser.add_argument("--width", type=int, default=None)
    parser.add_argument("--color", action="store_true")
    parser.add_argument("--colorful", action="store_true")
    parser.add_argument("--light", action="store_true")
    args = parser.parse_args()

    set_render_style(light=args.light, colorful=args.colorful)

    scenarios = [args.scenario] if args.scenario else list(SCENARIOS)
    widths = [args.width] if args.width else WIDTHS
    for name in scenarios:
        frame = SCENARIOS[name]()
        for width in widths:
            divider = "=" * width
            header = f" scenario={name}  width={width}  color={args.color} ".center(width, "=")
            print(divider)
            print(header)
            print(divider)
            print(render_once(frame, use_color=args.color, width=width))
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
