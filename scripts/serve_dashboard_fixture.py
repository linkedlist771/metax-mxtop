"""Serve deterministic cluster telemetry for dashboard browser verification."""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from dataclasses import dataclass
import json
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
from mxtop.remote.web import (
    SnapshotHolder,
    access_url,
    create_tls_context,
    make_server,
)

GIB = 1024**3
MIB = 1024**2
FIXED_TIMESTAMP = 1_768_653_296.0
TRAINING_PID = 423_901
REMOTE_PROCESS_IDENTITY = "0:423901"


@dataclass(frozen=True)
class FixtureProfile:
    primary_host: str
    secondary_host: str
    uuid_prefix: str
    primary_identity: str
    training_command: str
    training_user: str
    reused_command: str
    reused_user: str
    evaluator_identity: str
    evaluator_command: str
    evaluator_user: str
    render_identity: str
    render_command: str
    render_user: str


FIXTURE_PROFILES = {
    "alpha": FixtureProfile(
        primary_host="atlas-01",
        secondary_host="borealis-02",
        uuid_prefix="MX-FIXTURE",
        primary_identity=REMOTE_PROCESS_IDENTITY,
        training_command="python train.py --config configs/llama3-70b.yaml --bf16",
        training_user="alice",
        reused_command=(
            "python -m inference.server --model /models/mx-70b --port 9000"
        ),
        reused_user="service",
        evaluator_identity="fixture:evaluator:424250",
        evaluator_command="python evaluate.py --suite reasoning-v2",
        evaluator_user="bob",
        render_identity="fixture:render:781044",
        render_command="python render_batch.py --queue production",
        render_user="carol",
    ),
    "beta": FixtureProfile(
        primary_host="cygnus-11",
        secondary_host="draco-12",
        uuid_prefix="MX-BETA",
        primary_identity="beta:0:423901",
        training_command="python beta_train.py --config configs/mixtral-8x22b.yaml",
        training_user="diana",
        reused_command=(
            "python -m beta_inference.server --model /models/beta-65b --port 9100"
        ),
        reused_user="beta-service",
        evaluator_identity="beta:evaluator:424250",
        evaluator_command="python beta_evaluate.py --suite code-v3",
        evaluator_user="erin",
        render_identity="beta:render:781044",
        render_command="python beta_render_batch.py --queue staging",
        render_user="frank",
    ),
}


def _fixture_profile(name: str) -> FixtureProfile:
    try:
        return FIXTURE_PROFILES[name]
    except KeyError:
        raise ValueError(f"unknown fixture profile: {name!r}") from None


def _device(
    index: int,
    *,
    uuid_prefix: str,
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
        uuid=f"{uuid_prefix}-{index:02d}",
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


def _primary_process(step: int, profile: FixtureProfile) -> ProcessSnapshot | None:
    training_util = (58.0, 67.0, 79.0, 91.0, 84.0, 72.0)
    if step < len(training_util):
        return _process(
            0,
            TRAINING_PID,
            identity=profile.primary_identity,
            create_time=None,
            runtime=15_840.0 + step * 2.0,
            command=profile.training_command,
            user=profile.training_user,
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
        identity=profile.primary_identity,
        create_time=None,
        runtime=2.0 + generation_step * 2.0,
        command=profile.reused_command,
        user=profile.reused_user,
        gpu_memory_mib=min(42_000.0, 20_480.0 + generation_step * 64.0),
        gpu_util=min(88.0, 34.0 + generation_step * 7.0),
        cpu=96.0 + generation_step * 5.0,
        host_memory_gib=min(28.0, 11.5 + generation_step * 0.1),
    )


def fixture_cluster(step: int, profile: str = "alpha") -> ClusterSnapshot:
    """Return sample ``step`` from the active -> exited -> PID-reused sequence."""

    step = max(0, int(step))
    profile_data = _fixture_profile(profile)
    primary = _primary_process(step, profile_data)
    primary_util = primary.gpu_util_percent if primary is not None else 2.0
    primary_memory = (
        primary.gpu_memory_bytes / (64 * GIB) * 100.0
        if primary is not None and primary.gpu_memory_bytes is not None
        else 3.0
    )
    primary_processes = [
        _process(
            1,
            424_250,
            identity=profile_data.evaluator_identity,
            create_time=FIXED_TIMESTAMP - 5_400.0,
            runtime=5_400.0 + step * 2.0,
            command=profile_data.evaluator_command,
            user=profile_data.evaluator_user,
            gpu_memory_mib=8_192.0,
            gpu_util=24.0 + step % 4 * 3.0,
            cpu=68.0,
            host_memory_gib=6.2,
        )
    ]
    if primary is not None:
        primary_processes.insert(0, primary)

    timestamp = FIXED_TIMESTAMP + step * 2.0
    primary_host_snapshot = HostSnapshot(
        cpu_percent=46.0 + step % 5 * 2.5,
        memory_used_bytes=92 * GIB,
        memory_total_bytes=256 * GIB,
        memory_percent=35.9,
        load_average_1m=4.72,
        load_average_5m=4.15,
        load_average_15m=3.86,
        uptime_seconds=1_064_800.0 + step * 2.0,
    )
    primary_frame = FrameSnapshot(
        devices=[
            _device(
                0,
                uuid_prefix=profile_data.uuid_prefix,
                gpu_util=primary_util or 0.0,
                memory_percent=primary_memory,
                temperature=66.0,
                power=238.0,
            ),
            _device(
                1,
                uuid_prefix=profile_data.uuid_prefix,
                gpu_util=29.0,
                memory_percent=22.0,
                temperature=54.0,
                power=166.0,
            ),
            _device(
                2,
                uuid_prefix=profile_data.uuid_prefix,
                gpu_util=3.0,
                memory_percent=5.0,
                temperature=42.0,
                power=82.0,
            ),
            _device(
                3,
                uuid_prefix=profile_data.uuid_prefix,
                gpu_util=0.0,
                memory_percent=2.0,
                temperature=40.0,
                power=76.0,
            ),
        ],
        processes=primary_processes,
        backend=f"fixture@{profile_data.primary_host}",
        timestamp=timestamp,
    )
    primary_node = (
        NodeSnapshot(
            hostname=profile_data.primary_host,
            reachable=False,
            error="SSH keepalive timed out",
            latency_ms=1_002.0,
        )
        if step == 6
        else NodeSnapshot(
            hostname=profile_data.primary_host,
            reachable=True,
            latency_ms=18.4,
            host=primary_host_snapshot,
            frame=primary_frame,
        )
    )
    secondary_node = NodeSnapshot(
        hostname=profile_data.secondary_host,
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
                    0,
                    uuid_prefix=profile_data.uuid_prefix,
                    gpu_util=63.0,
                    memory_percent=68.0,
                    temperature=61.0,
                    power=221.0,
                ),
                _device(
                    1,
                    uuid_prefix=profile_data.uuid_prefix,
                    gpu_util=47.0,
                    memory_percent=51.0,
                    temperature=58.0,
                    power=194.0,
                ),
            ],
            processes=[
                _process(
                    0,
                    781_044,
                    identity=profile_data.render_identity,
                    create_time=FIXED_TIMESTAMP - 2_100.0,
                    runtime=2_100.0 + step * 2.0,
                    command=profile_data.render_command,
                    user=profile_data.render_user,
                    gpu_memory_mib=32_768.0,
                    gpu_util=61.0,
                    cpu=188.0,
                    host_memory_gib=17.8,
                )
            ],
            backend=f"fixture@{profile_data.secondary_host}",
            timestamp=timestamp,
        ),
    )
    return ClusterSnapshot(nodes=[primary_node, secondary_node], timestamp=timestamp)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--interval", type=float, default=0.75)
    parser.add_argument("--tls-cert", metavar="CERTFILE")
    parser.add_argument("--tls-key", metavar="KEYFILE")
    parser.add_argument("--tls-key-password-file", metavar="FILE")
    parser.add_argument(
        "--profile",
        choices=tuple(FIXTURE_PROFILES),
        default="alpha",
        help="fixture cluster profile (default: alpha)",
    )
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


def _parse_control_line(line: str, current_profile: str) -> tuple[str, int]:
    stripped = line.strip()
    try:
        step = int(stripped)
    except ValueError:
        pass
    else:
        if step < 0:
            raise ValueError("step must be non-negative")
        _fixture_profile(current_profile)
        return current_profile, step

    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError("expected an integer step or JSON control object") from exc
    if not isinstance(payload, dict):
        raise ValueError("JSON control must be an object")
    if set(payload) != {"profile", "step"}:
        raise ValueError("JSON control requires exactly 'profile' and 'step'")
    profile = payload["profile"]
    step = payload["step"]
    if not isinstance(profile, str):
        raise ValueError("profile must be a string")
    _fixture_profile(profile)
    if isinstance(step, bool) or not isinstance(step, int) or step < 0:
        raise ValueError("step must be a non-negative integer")
    return profile, step


def _publish_control_updates(
    holder: SnapshotHolder,
    lines: Iterable[str],
    initial_profile: str,
) -> None:
    current_profile = initial_profile
    for line in lines:
        try:
            current_profile, step = _parse_control_line(line, current_profile)
        except ValueError as exc:
            print(
                f"Ignoring invalid dashboard fixture control: {line.strip()!r} ({exc})",
                file=sys.stderr,
                flush=True,
            )
            continue
        holder.update(fixture_cluster(step, current_profile))


def _next_step(current: int, stop_step: int | None) -> int | None:
    if stop_step is not None and current >= stop_step:
        return None
    return current + 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
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
    if (args.tls_cert is None) != (args.tls_key is None):
        parser.error("--tls-cert and --tls-key must be provided together")
    if args.tls_key_password_file is not None and args.tls_cert is None:
        parser.error("--tls-key-password-file requires --tls-cert and --tls-key")

    try:
        tls_context = create_tls_context(
            args.tls_cert,
            args.tls_key,
            key_password_file=args.tls_key_password_file,
        )
    except Exception as exc:
        raise SystemExit(f"TLS setup failed: {exc}") from exc

    holder = SnapshotHolder()
    initial_step = args.step if args.step is not None else args.start_step
    holder.update(fixture_cluster(initial_step, args.profile))
    server = make_server(
        holder,
        bind=args.bind,
        port=args.port,
        tls_context=tls_context,
    )
    stop = threading.Event()
    poller = None

    if args.control_stdin:
        poller = threading.Thread(
            target=_publish_control_updates,
            args=(holder, sys.stdin, args.profile),
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
                holder.update(fixture_cluster(step, args.profile))

        poller = threading.Thread(
            target=advance, name="mxtop-dashboard-fixture", daemon=True
        )
        poller.start()

    port = server.server_address[1]
    print(
        f"mxtop dashboard fixture: "
        f"{access_url(args.bind, port, tls=tls_context is not None)}",
        flush=True,
    )
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
