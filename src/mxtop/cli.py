from __future__ import annotations

import argparse
from dataclasses import dataclass
import getpass
import json
import locale
import math
import os
import shutil
import sys
import time

from mxtop import __version__
from mxtop._compat import DATACLASS_SLOTS
from mxtop.backends import TelemetryBackend, create_backend
from mxtop.filters import (
    apply_filters,
    normalize_indices,
    normalize_pids,
    normalize_strings,
    resolve_visible_device_indices,
)
from mxtop.jsonutil import sanitize_json_value
from mxtop.models import FrameSnapshot
from mxtop.rendering import (
    DEFAULT_GPU_UTILIZATION_THRESHOLDS,
    DEFAULT_MEMORY_UTILIZATION_THRESHOLDS,
    render_once,
    set_intensity_thresholds,
    set_render_style,
)
from mxtop.tui import run_tui
from mxtop.ui.text import to_ascii
from mxtop.ui.state import LayoutMode

MIN_INTERVAL = 0.25
MXTOP_GPU_THRESHOLDS_ENV = "MXTOP_GPU_UTILIZATION_THRESHOLDS"
MXTOP_MEM_THRESHOLDS_ENV = "MXTOP_MEMORY_UTILIZATION_THRESHOLDS"
MXTOP_MONITOR_MODE_ENV = "MXTOP_MONITOR_MODE"
VISIBLE_DEVICE_ENVS = ("MACA_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES")


@dataclass(**DATACLASS_SLOTS)
class RuntimeOptions:
    device_indices: set[int] | None = None
    users: set[str] | None = None
    pids: set[int] | None = None
    process_types: set[str] | None = None
    require_process_type: bool = False
    compute: bool = False
    only_compute: bool = False
    graphics: bool = False
    only_graphics: bool = False
    visible_device_identifiers: tuple[str, ...] | None = None
    layout: LayoutMode = LayoutMode.AUTO
    no_color: bool = False
    no_unicode: bool = False
    readonly: bool = False


def _interval(value: str) -> float:
    interval = float(value)
    if not math.isfinite(interval) or interval < MIN_INTERVAL:
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
    device_indices = options.device_indices
    if device_indices is None:
        device_indices = resolve_visible_device_indices(frame.devices, options.visible_device_identifiers)
    return apply_filters(
        frame,
        device_indices=device_indices,
        users=options.users,
        pids=options.pids,
        process_types=options.process_types,
        require_process_type=options.require_process_type,
        compute=options.compute,
        only_compute=options.only_compute,
        graphics=options.graphics,
        only_graphics=options.only_graphics,
    )


def _runtime_options(args: argparse.Namespace) -> RuntimeOptions:
    visible_identifiers = _visible_device_identifiers() if args.only_visible and args.only is None else None
    return RuntimeOptions(
        device_indices=normalize_indices(args.only),
        users=normalize_strings(args.user),
        pids=normalize_pids(args.pid),
        compute=args.compute,
        only_compute=args.only_compute,
        graphics=args.graphics,
        only_graphics=args.only_graphics,
        visible_device_identifiers=visible_identifiers,
        layout=LayoutMode(getattr(args, "monitor", None) or LayoutMode.AUTO.value),
        no_color=args.no_color,
        no_unicode=args.no_unicode,
        readonly=args.readonly,
    )


def _monitor_mode_tokens() -> set[str]:
    return {token.strip().lower() for token in os.environ.get(MXTOP_MONITOR_MODE_ENV, "").split(",") if token.strip()}


def _monitor_layout_from_env(tokens: set[str]) -> str:
    modes = tokens.intersection({mode.value for mode in LayoutMode})
    return modes.pop() if len(modes) == 1 else LayoutMode.AUTO.value


def _visible_device_identifiers() -> tuple[str, ...] | None:
    for name in VISIBLE_DEVICE_ENVS:
        if name in os.environ:
            return tuple(token.strip() for token in os.environ[name].split(","))
    return None


def _unicode_supported() -> bool:
    encoding = locale.getpreferredencoding(False).lower().replace("-", "")
    return encoding in {"utf8", "utf_8"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="An nvitop-like monitor for MetaX GPUs.")
    _ = parser.add_argument("--version", "-V", action="version", version=f"mxtop {__version__}")
    _ = parser.add_argument("--backend", choices=["auto", "pymxsml", "mxsmi"], default="auto")
    remote = parser.add_argument_group("remote mode")
    _ = remote.add_argument(
        "--remote-mode",
        action="store_true",
        help="serve a local web dashboard aggregating multiple SSH nodes",
    )
    _ = remote.add_argument(
        "--nodes",
        nargs="+",
        metavar="HOST",
        help="ssh hosts/aliases to monitor (resolved via ~/.ssh/config)",
    )
    _ = remote.add_argument("--nodes-file", help="file with one ssh host per line (# comments allowed)")
    _ = remote.add_argument("--port", type=int, default=8080, help="dashboard port (default: 8080)")
    _ = remote.add_argument("--bind", default="127.0.0.1", help="dashboard bind address (default: 127.0.0.1)")
    _ = remote.add_argument("--remote-mxsmi-path", default="mx-smi", help="mx-smi path on remote hosts")
    _ = remote.add_argument("--open", action="store_true", help="open the dashboard in a browser")
    _ = parser.add_argument("--interval", type=_interval, default=2.0, help="refresh interval in seconds")
    mode = parser.add_mutually_exclusive_group()
    _ = mode.add_argument("--once", "-1", action="store_true", help="print one text snapshot and exit")
    _ = mode.add_argument(
        "--monitor",
        "-m",
        nargs="?",
        choices=[layout.value for layout in LayoutMode],
        default=argparse.SUPPRESS,
        help="run interactively (mode defaults to MXTOP_MONITOR_MODE or auto)",
    )
    _ = mode.add_argument("--json", action="store_true", help="print one JSON snapshot and exit")
    _ = parser.add_argument("--no-color", action="store_true", help="disable ANSI color output")
    _ = parser.add_argument("--only", "-o", nargs="+", type=int, help="show only selected GPU indices")
    _ = parser.add_argument("--only-visible", "-ov", action="store_true", help="show only visible devices")
    _ = parser.add_argument("--user", "-u", nargs="*", help="show selected users (current user when omitted)")
    _ = parser.add_argument("--pid", "-p", nargs="+", type=int, help="show only selected process IDs")
    _ = parser.add_argument("--compute", "-c", action="store_true", help="show processes with compute context")
    _ = parser.add_argument("--only-compute", "-C", action="store_true", help="show exactly compute processes")
    _ = parser.add_argument("--graphics", "-g", action="store_true", help="show processes with graphics context")
    _ = parser.add_argument("--only-graphics", "-G", action="store_true", help="show exactly graphics processes")
    _ = parser.add_argument("--no-unicode", "--ascii", "-U", action="store_true", help="use ASCII characters only")
    _ = parser.add_argument("--readonly", action="store_true", help="disable process-changing actions")
    _ = parser.add_argument(
        "--colorful",
        action="store_true",
        help="use spectrum-like gradient colors for bar charts on 256-color terminals",
    )
    _ = parser.add_argument(
        "--light",
        action="store_true",
        help="use colors suitable for light terminal themes",
    )
    _ = parser.add_argument(
        "--force-color",
        action="store_true",
        help="emit ANSI colour even when stdout is not a TTY",
    )
    _ = parser.add_argument(
        "--gpu-util-thresh",
        nargs=2,
        type=int,
        choices=range(1, 100),
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
        type=int,
        choices=range(1, 100),
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
    if not (0 < low < high < 100):
        return None
    return low, high


def _apply_intensity_thresholds(args: argparse.Namespace) -> None:
    gpu = _coerce_threshold(args.gpu_util_thresh) or _parse_threshold_env(os.environ.get(MXTOP_GPU_THRESHOLDS_ENV))
    memory = _coerce_threshold(args.mem_util_thresh) or _parse_threshold_env(os.environ.get(MXTOP_MEM_THRESHOLDS_ENV))
    if gpu is not None or memory is not None:
        set_intensity_thresholds(gpu=gpu, memory=memory)


def _report_invalid_device_indices(frame: FrameSnapshot, options: RuntimeOptions) -> bool:
    if options.device_indices is None:
        return False
    valid = {device.index for device in frame.devices}
    invalid = sorted(options.device_indices.difference(valid))
    if not invalid:
        return False
    label = "indices" if len(invalid) > 1 else "index"
    print(f"MXTOP ERROR: Invalid device {label}: {invalid if len(invalid) > 1 else invalid[0]}.", file=sys.stderr)
    return True


def _snapshot_width() -> int:
    return max(79, min(140, shutil.get_terminal_size(fallback=(120, 24)).columns))


def _should_use_color(*, no_color: bool, force_color: bool, stdout_is_tty: bool) -> bool:
    if no_color:
        return False
    if force_color:
        return True
    if "ANSI_COLORS_DISABLED" in os.environ or "NO_COLOR" in os.environ:
        return False
    if "FORCE_COLOR" in os.environ:
        return True
    return os.environ.get("TERM") != "dumb" and stdout_is_tty


def main(argv: list[str] | None = None, backend: TelemetryBackend | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    monitor_tokens = _monitor_mode_tokens()
    explicit_monitor = hasattr(args, "monitor")
    stdin_is_tty = sys.stdin.isatty()
    stdout_is_tty = sys.stdout.isatty()
    monitor_unavailable = explicit_monitor and not (stdin_is_tty and stdout_is_tty)
    if monitor_unavailable:
        print("MXTOP ERROR: monitor mode requires stdin and stdout to be TTY terminals", file=sys.stderr)
    monitor_requested = (explicit_monitor and not monitor_unavailable) or (
        not args.once and not args.json and stdin_is_tty and stdout_is_tty and not args.remote_mode
    )
    if monitor_requested:
        requested_layout = getattr(args, "monitor", None)
        args.monitor = requested_layout or _monitor_layout_from_env(monitor_tokens)
    if args.user is not None and not args.user:
        args.user.append(getpass.getuser())
    if not args.colorful:
        args.colorful = "colorful" in monitor_tokens and "plain" not in monitor_tokens
    if not args.light:
        args.light = "light" in monitor_tokens and "dark" not in monitor_tokens
    if not args.readonly:
        args.readonly = "readonly" in monitor_tokens
    args.no_unicode = args.no_unicode or not _unicode_supported()

    if args.remote_mode:
        from mxtop.remote.app import load_hosts, run_remote

        hosts = load_hosts(args.nodes, args.nodes_file)
        if not hosts:
            parser.error("--remote-mode requires --nodes or --nodes-file")
        return run_remote(
            hosts,
            bind=args.bind,
            port=args.port,
            interval=args.interval,
            mxsmi_path=args.remote_mxsmi_path,
            open_browser=args.open,
        )

    _apply_intensity_thresholds(args)
    set_render_style(light=args.light, colorful=args.colorful)
    options = _runtime_options(args)
    try:
        selected_backend = backend or create_backend(args.backend)
    except Exception as exc:
        print(f"MXTOP ERROR: {exc}", file=sys.stderr)
        return 1

    if args.json:
        try:
            frame = _single_snapshot_with_cpu_sample(selected_backend, options)
        except Exception as exc:
            print(f"MXTOP ERROR: {exc}", file=sys.stderr)
            return 1
        had_errors = _report_invalid_device_indices(frame, options)
        print(json.dumps(sanitize_json_value(frame.to_dict()), indent=2, sort_keys=True, allow_nan=False))
        return int(had_errors)

    use_color = _should_use_color(
        no_color=options.no_color,
        force_color=args.force_color,
        stdout_is_tty=stdout_is_tty,
    )
    if args.once or not monitor_requested:
        try:
            frame = _single_snapshot_with_cpu_sample(selected_backend, options)
        except Exception as exc:
            print(f"MXTOP ERROR: {exc}", file=sys.stderr)
            return 1
        had_errors = _report_invalid_device_indices(frame, options)
        output = render_once(frame, use_color=use_color, width=_snapshot_width())
        print(to_ascii(output) if options.no_unicode else output)
        return int(had_errors or monitor_unavailable)

    try:
        preflight = _apply_runtime_options(selected_backend.snapshot(), options)
    except Exception as exc:
        print(f"MXTOP ERROR: {exc}", file=sys.stderr)
        return 1
    had_errors = _report_invalid_device_indices(preflight, options)
    if not preflight.devices:
        output = render_once(preflight, use_color=use_color, width=_snapshot_width())
        print(to_ascii(output) if options.no_unicode else output)
        return int(had_errors)

    tui_result = run_tui(
        selected_backend,
        args.interval,
        options=options,
    )
    if tui_result == 1:
        try:
            frame = _single_snapshot_with_cpu_sample(selected_backend, options)
        except Exception as exc:
            print(f"MXTOP ERROR: {exc}", file=sys.stderr)
            return 1
        output = render_once(frame, use_color=use_color, width=_snapshot_width())
        print(to_ascii(output) if options.no_unicode else output)
    return tui_result or int(had_errors)


if __name__ == "__main__":
    raise SystemExit(main())
