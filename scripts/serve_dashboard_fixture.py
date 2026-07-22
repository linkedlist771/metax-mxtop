"""Serve deterministic cluster telemetry for dashboard browser verification."""

from __future__ import annotations

import argparse
import sys
import threading

from mxtop.models import (
    ClusterSnapshot,
    DeviceSnapshot,
    FrameSnapshot,
    HostSnapshot,
    NodeSnapshot,
    ProcessSnapshot,
)
from mxtop.remote.web import SnapshotHolder, access_url, make_server

GIB = 1024**3
MIB = 1024**2
FIXED_TIMESTAMP = 1_768_653_296.0
TRAINING_PID = 423_901
REMOTE_PROCESS_IDENTITY = "0:423901"


def _device(
    index: int,
    *,
    gpu_util: float,
    memory_percent: float,
    temperature: float,
    power: float,
) -> DeviceSnapshot:
    total = 64 * GIB
    used = round(total * memory_percent / 100.0)
    return DeviceSnapshot(
        index=index,
        name="MetaX C500",
        uuid=f"MX-FIXTURE-{index:02d}",
        bdf=f"0000:{0x21 + index * 0x17:02x}:00.0",
        temperature_c=temperature,
        power_w=power,
        power_limit_w=350.0,
        gpu_util_percent=gpu_util,
        memory_util_percent=memory_percent,
        memory_bandwidth_util_percent=min(100.0, gpu_util * 0.72),
        memory_used_bytes=used,
        memory_total_bytes=total,
        memory_free_bytes=total - used,
        gpu_clock_mhz=1_650.0,
        memory_clock_mhz=1_600.0,
        driver_version="2.31.0.5",
        maca_version="4.3.1",
        compute_mode="Default",
    )


def _process(
    gpu: int,
    pid: int,
    *,
    identity: str,
    create_time: float | None,
    runtime: float,
    command: str,
    user: str,
    gpu_memory_mib: float,
    gpu_util: float,
    cpu: float,
    host_memory_gib: float,
    process_type: str = "C",
) -> ProcessSnapshot:
    return ProcessSnapshot(
        gpu_index=gpu,
        pid=pid,
        name=command.split()[0].rsplit("/", 1)[-1],
        gpu_memory_bytes=round(gpu_memory_mib * MIB),
        user=user,
        command=command,
        cpu_percent=cpu,
        host_memory_bytes=round(host_memory_gib * GIB),
        runtime_seconds=runtime,
        process_type=process_type,
        gpu_util_percent=gpu_util,
        gpu_memory_bandwidth_util_percent=min(100.0, gpu_util * 0.68),
        identity=identity,
        create_time=create_time,
    )


def _primary_process(step: int) -> ProcessSnapshot | None:
    training_util = (58.0, 67.0, 79.0, 91.0, 84.0, 72.0)
    if step < len(training_util):
        return _process(
            0,
            TRAINING_PID,
            identity=REMOTE_PROCESS_IDENTITY,
            create_time=None,
            runtime=15_840.0 + step * 2.0,
            command="python train.py --config configs/llama3-70b.yaml --bf16",
            user="alice",
            gpu_memory_mib=48_640.0 + step * 192.0,
            gpu_util=training_util[step],
            cpu=248.0 + step * 14.0,
            host_memory_gib=22.0 + step * 0.35,
        )
    if step < 8:
        return None
    generation_step = step - 8
    return _process(
        0,
        TRAINING_PID,
        identity=REMOTE_PROCESS_IDENTITY,
        create_time=None,
        runtime=2.0 + generation_step * 2.0,
        command="python -m inference.server --model /models/mx-70b --port 9000",
        user="service",
        gpu_memory_mib=min(42_000.0, 20_480.0 + generation_step * 64.0),
        gpu_util=min(88.0, 34.0 + generation_step * 7.0),
        cpu=96.0 + generation_step * 5.0,
        host_memory_gib=min(28.0, 11.5 + generation_step * 0.1),
    )


def fixture_cluster(step: int) -> ClusterSnapshot:
    """Return sample ``step`` from the active -> exited -> PID-reused sequence."""

    step = max(0, int(step))
    primary = _primary_process(step)
    primary_util = primary.gpu_util_percent if primary is not None else 2.0
    primary_memory = (
        primary.gpu_memory_bytes / (64 * GIB) * 100.0
        if primary is not None and primary.gpu_memory_bytes is not None
        else 3.0
    )
    atlas_processes = [
        _process(
            1,
            424_250,
            identity="fixture:evaluator:424250",
            create_time=FIXED_TIMESTAMP - 5_400.0,
            runtime=5_400.0 + step * 2.0,
            command="python evaluate.py --suite reasoning-v2",
            user="bob",
            gpu_memory_mib=8_192.0,
            gpu_util=24.0 + step % 4 * 3.0,
            cpu=68.0,
            host_memory_gib=6.2,
        )
    ]
    if primary is not None:
        atlas_processes.insert(0, primary)

    timestamp = FIXED_TIMESTAMP + step * 2.0
    atlas_host = HostSnapshot(
        cpu_percent=46.0 + step % 5 * 2.5,
        memory_used_bytes=92 * GIB,
        memory_total_bytes=256 * GIB,
        memory_percent=35.9,
        load_average_1m=4.72,
        load_average_5m=4.15,
        load_average_15m=3.86,
        uptime_seconds=1_064_800.0 + step * 2.0,
    )
    atlas_frame = FrameSnapshot(
        devices=[
            _device(
                0,
                gpu_util=primary_util or 0.0,
                memory_percent=primary_memory,
                temperature=66.0,
                power=238.0,
            ),
            _device(
                1, gpu_util=29.0, memory_percent=22.0, temperature=54.0, power=166.0
            ),
            _device(2, gpu_util=3.0, memory_percent=5.0, temperature=42.0, power=82.0),
            _device(3, gpu_util=0.0, memory_percent=2.0, temperature=40.0, power=76.0),
        ],
        processes=atlas_processes,
        backend="fixture@atlas-01",
        timestamp=timestamp,
    )
    atlas = (
        NodeSnapshot(
            hostname="atlas-01",
            reachable=False,
            error="SSH keepalive timed out",
            latency_ms=1_002.0,
        )
        if step == 6
        else NodeSnapshot(
            hostname="atlas-01",
            reachable=True,
            latency_ms=18.4,
            host=atlas_host,
            frame=atlas_frame,
        )
    )
    borealis = NodeSnapshot(
        hostname="borealis-02",
        reachable=True,
        latency_ms=27.1,
        host=HostSnapshot(
            cpu_percent=28.0,
            memory_used_bytes=64 * GIB,
            memory_total_bytes=192 * GIB,
            memory_percent=33.3,
            load_average_1m=2.08,
            load_average_5m=2.31,
            load_average_15m=2.42,
            uptime_seconds=742_200.0 + step * 2.0,
        ),
        frame=FrameSnapshot(
            devices=[
                _device(
                    0, gpu_util=63.0, memory_percent=68.0, temperature=61.0, power=221.0
                ),
                _device(
                    1, gpu_util=47.0, memory_percent=51.0, temperature=58.0, power=194.0
                ),
            ],
            processes=[
                _process(
                    0,
                    781_044,
                    identity="fixture:render:781044",
                    create_time=FIXED_TIMESTAMP - 2_100.0,
                    runtime=2_100.0 + step * 2.0,
                    command="python render_batch.py --queue production",
                    user="carol",
                    gpu_memory_mib=32_768.0,
                    gpu_util=61.0,
                    cpu=188.0,
                    host_memory_gib=17.8,
                )
            ],
            backend="fixture@borealis-02",
            timestamp=timestamp,
        ),
    )
    return ClusterSnapshot(nodes=[atlas, borealis], timestamp=timestamp)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--interval", type=float, default=0.75)
    parser.add_argument(
        "--step",
        type=int,
        help="serve one fixed sequence step instead of advancing samples",
    )
    parser.add_argument("--start-step", type=int, default=0)
    parser.add_argument(
        "--stop-step",
        type=int,
        help="hold an advancing sequence at this step",
    )
    parser.add_argument(
        "--control-stdin",
        action="store_true",
        help="publish a step whenever its number is read from standard input",
    )
    return parser


def _next_step(current: int, stop_step: int | None) -> int | None:
    if stop_step is not None and current >= stop_step:
        return None
    return current + 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 0 <= args.port <= 65535:
        raise SystemExit("port must be between 0 and 65535")
    if args.interval <= 0:
        raise SystemExit("interval must be positive")
    if args.step is not None and args.step < 0:
        raise SystemExit("step must be non-negative")
    if args.start_step < 0:
        raise SystemExit("start-step must be non-negative")
    if args.stop_step is not None and args.stop_step < args.start_step:
        raise SystemExit("stop-step must not be less than start-step")
    if args.step is not None and args.control_stdin:
        raise SystemExit("step and control-stdin cannot be combined")

    holder = SnapshotHolder()
    initial_step = args.step if args.step is not None else args.start_step
    holder.update(fixture_cluster(initial_step))
    server = make_server(holder, bind=args.bind, port=args.port)
    stop = threading.Event()
    poller = None

    if args.control_stdin:

        def control() -> None:
            for line in sys.stdin:
                try:
                    step = int(line.strip())
                    if step < 0:
                        raise ValueError
                except ValueError:
                    print(
                        f"Ignoring invalid dashboard fixture step: {line.strip()!r}",
                        file=sys.stderr,
                        flush=True,
                    )
                    continue
                holder.update(fixture_cluster(step))

        poller = threading.Thread(
            target=control,
            name="mxtop-dashboard-fixture-control",
            daemon=True,
        )
        poller.start()
    elif args.step is None:

        def advance() -> None:
            step = args.start_step
            while not stop.wait(args.interval):
                next_step = _next_step(step, args.stop_step)
                if next_step is None:
                    continue
                step = next_step
                holder.update(fixture_cluster(step))

        poller = threading.Thread(
            target=advance, name="mxtop-dashboard-fixture", daemon=True
        )
        poller.start()

    port = server.server_address[1]
    print(f"mxtop dashboard fixture: {access_url(args.bind, port)}", flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
        if poller is not None and not args.control_stdin:
            poller.join(timeout=2.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
