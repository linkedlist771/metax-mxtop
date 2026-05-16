from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
import sys
import time

from mxtop import __version__
from mxtop.backends import TelemetryBackend, create_backend
from mxtop.filters import apply_filters, normalize_indices, normalize_pids, normalize_strings
from mxtop.models import FrameSnapshot
from mxtop.rendering import (
    DEFAULT_GPU_UTILIZATION_THRESHOLDS,
    DEFAULT_MEMORY_UTILIZATION_THRESHOLDS,
    render_once,
    set_intensity_thresholds,
    set_render_style,
)
from mxtop.tui import run_tui
from mxtop.ui.state import LayoutMode

MIN_INTERVAL = 0.25
MXTOP_GPU_THRESHOLDS_ENV = "MXTOP_GPU_UTILIZATION_THRESHOLDS"
MXTOP_MEM_THRESHOLDS_ENV = "MXTOP_MEMORY_UTILIZATION_THRESHOLDS"


@dataclass(slots=True)
class RuntimeOptions:
    device_indices: set[int] | None = None
    users: set[str] | None = None
    pids: set[int] | None = None
    process_types: set[str] | None = None
    require_process_type: bool = False
    layout: LayoutMode = LayoutMode.AUTO
    no_color: bool = False
    no_unicode: bool = False


def _interval(value: str) -> float:
    interval = float(value)
    if interval < MIN_INTERVAL:
        raise argparse.ArgumentTypeError(f"interval must be at least {MIN_INTERVAL}s")
    return interval


def _single_snapshot_with_cpu_sample(backend: TelemetryBackend, options: RuntimeOptions | None = None) -> FrameSnapshot:
    frame = _apply_runtime_options(backend.snapshot(), options)
    if frame.processes and any(process.cpu_percent is None for process in frame.processes):
        time.sleep(0.1)
        frame = _apply_runtime_options(backend.snapshot(), options)
    return frame


def _apply_runtime_options(frame: FrameSnapshot, options: RuntimeOptions | None) -> FrameSnapshot:
    if options is None:
        return frame
    return apply_filters(
        frame,
        device_indices=options.device_indices,
        users=options.users,
        pids=options.pids,
        process_types=options.process_types,
        require_process_type=options.require_process_type,
    )


def _runtime_options(args: argparse.Namespace) -> RuntimeOptions:
    process_types: set[str] | None = None
    require_process_type = False
    if args.compute or args.only_compute:
        process_types = {"C"}
        require_process_type = args.only_compute
    if args.graphics or args.only_graphics:
        process_types = (process_types or set()) | {"G"}
        require_process_type = require_process_type or args.only_graphics
    return RuntimeOptions(
        device_indices=normalize_indices(args.only),
        users=normalize_strings(args.user),
        pids=normalize_pids(args.pid),
        process_types=process_types,
        require_process_type=require_process_type,
        layout=LayoutMode(args.monitor),
        no_color=args.no_color,
        no_unicode=args.no_unicode,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="An nvitop-like monitor for MetaX GPUs.")
    _ = parser.add_argument("--version", action="version", version=f"mxtop {__version__}")
    _ = parser.add_argument("--backend", choices=["auto", "pymxsml", "mxsmi"], default="auto")
    _ = parser.add_argument("--interval", type=_interval, default=1.0, help="refresh interval in seconds")
    _ = parser.add_argument("--once", "-1", action="store_true", help="print one text snapshot and exit")
    _ = parser.add_argument("--json", action="store_true", help="print one JSON snapshot and exit")
    _ = parser.add_argument("--no-color", action="store_true", help="disable ANSI color output")
    _ = parser.add_argument("--monitor", choices=[mode.value for mode in LayoutMode], default=LayoutMode.AUTO.value)
    _ = parser.add_argument("--only", nargs="+", type=int, help="show only selected GPU indices")
    _ = parser.add_argument("--only-visible", action="store_true", help="reserved for MetaX visible-device environments")
    _ = parser.add_argument("--user", nargs="+", help="show only processes owned by users")
    _ = parser.add_argument("--pid", nargs="+", type=int, help="show only selected process IDs")
    _ = parser.add_argument("--compute", action="store_true", help="prefer compute processes when process type is available")
    _ = parser.add_argument("--only-compute", action="store_true", help="show only compute processes when process type is available")
    _ = parser.add_argument("--graphics", action="store_true", help="prefer graphics processes when process type is available")
    _ = parser.add_argument("--only-graphics", action="store_true", help="show only graphics processes when process type is available")
    _ = parser.add_argument("--no-unicode", "--ascii", action="store_true", help="reserve ASCII-only rendering mode")
    _ = parser.add_argument(
        "--colorful",
        action="store_true",
        help="use a 5-tier intensity palette for usage-coloured bars and percentages",
    )
    _ = parser.add_argument(
        "--light",
        action="store_true",
        help="adjust dim foreground for light terminal themes",
    )
    _ = parser.add_argument(
        "--force-color",
        action="store_true",
        help="emit ANSI colour even when stdout is not a TTY",
    )
    _ = parser.add_argument(
        "--gpu-util-thresh",
        nargs=2,
        type=float,
        metavar=("LOW", "HIGH"),
        default=None,
        help=(
            "GPU utilization intensity thresholds (default: "
            f"{DEFAULT_GPU_UTILIZATION_THRESHOLDS[0]} {DEFAULT_GPU_UTILIZATION_THRESHOLDS[1]}). "
            f"Falls back to env {MXTOP_GPU_THRESHOLDS_ENV}=LOW,HIGH when omitted."
        ),
    )
    _ = parser.add_argument(
        "--mem-util-thresh",
        nargs=2,
        type=float,
        metavar=("LOW", "HIGH"),
        default=None,
        help=(
            "GPU memory intensity thresholds (default: "
            f"{DEFAULT_MEMORY_UTILIZATION_THRESHOLDS[0]} {DEFAULT_MEMORY_UTILIZATION_THRESHOLDS[1]}). "
            f"Falls back to env {MXTOP_MEM_THRESHOLDS_ENV}=LOW,HIGH when omitted."
        ),
    )
    return parser


def _parse_threshold_env(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    try:
        parts = [int(float(token)) for token in value.split(",")[:2]]
    except ValueError:
        return None
    if len(parts) != 2:
        return None
    low, high = sorted(parts)
    if not (0 < low < high < 100):
        return None
    return low, high


def _coerce_threshold(values: list[float] | tuple[float, float] | None) -> tuple[int, int] | None:
    if values is None:
        return None
    if len(values) != 2:
        return None
    low, high = sorted(int(value) for value in values)
    if not (0 <= low < high <= 100):
        return None
    return low, high


def _apply_intensity_thresholds(args: argparse.Namespace) -> None:
    gpu = _coerce_threshold(args.gpu_util_thresh) or _parse_threshold_env(os.environ.get(MXTOP_GPU_THRESHOLDS_ENV))
    memory = _coerce_threshold(args.mem_util_thresh) or _parse_threshold_env(os.environ.get(MXTOP_MEM_THRESHOLDS_ENV))
    if gpu is not None or memory is not None:
        set_intensity_thresholds(gpu=gpu, memory=memory)


def main(argv: list[str] | None = None, backend: TelemetryBackend | None = None) -> int:
    args = build_parser().parse_args(argv)
    _apply_intensity_thresholds(args)
    set_render_style(light=args.light, colorful=args.colorful)
    options = _runtime_options(args)
    selected_backend = backend or create_backend(args.backend)

    if args.json:
        print(json.dumps(_single_snapshot_with_cpu_sample(selected_backend, options).to_dict(), indent=2, sort_keys=True))
        return 0

    use_color = not options.no_color and (args.force_color or sys.stdout.isatty() or args.once)
    if args.once or not sys.stdout.isatty():
        print(render_once(_single_snapshot_with_cpu_sample(selected_backend, options), use_color=use_color))
        return 0

    return run_tui(
        selected_backend,
        args.interval,
        options=options,
    )


if __name__ == "__main__":
    raise SystemExit(main())
