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
    requested_process_contexts,
    resolve_visible_device_indices,
    validate_process_contexts,
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
MXTOP_AUTH_TOKEN_ENV = "MXTOP_AUTH_TOKEN"
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
    supported_process_contexts: frozenset[str] | None = None


def _interval(value: str) -> float:
    interval = float(value)
    if not math.isfinite(interval) or interval < MIN_INTERVAL:
        raise argparse.ArgumentTypeError(f"interval must be at least {MIN_INTERVAL}s")
    return interval


def _port(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def _count(value: str) -> int:
    count = int(value)
    if count < 1:
        raise argparse.ArgumentTypeError("count must be at least 1")
    return count


def _single_snapshot_with_cpu_sample(
    backend: TelemetryBackend, options: RuntimeOptions | None = None
) -> FrameSnapshot:
    frame = _apply_runtime_options(backend.snapshot(), options)
    if frame.processes and any(
        process.cpu_percent is None for process in frame.processes
    ):
        time.sleep(0.1)
        frame = _apply_runtime_options(backend.snapshot(), options)
    return frame


def _apply_runtime_options(
    frame: FrameSnapshot, options: RuntimeOptions | None
) -> FrameSnapshot:
    if options is None:
        return frame
    device_indices = options.device_indices
    if device_indices is None:
        device_indices = resolve_visible_device_indices(
            frame.devices, options.visible_device_identifiers
        )
    filtered = apply_filters(
        frame,
        device_indices=device_indices,
        users=options.users,
        pids=options.pids,
        process_types=options.process_types,
        require_process_type=options.require_process_type,
    )
    requested_contexts = requested_process_contexts(
        compute=options.compute,
        only_compute=options.only_compute,
        graphics=options.graphics,
        only_graphics=options.only_graphics,
    )
    validate_process_contexts(
        filtered,
        requested=requested_contexts,
        supported=options.supported_process_contexts,
    )
    return apply_filters(
        filtered,
        compute=options.compute,
        only_compute=options.only_compute,
        graphics=options.graphics,
        only_graphics=options.only_graphics,
    )


def _runtime_options(args: argparse.Namespace) -> RuntimeOptions:
    visible_identifiers = (
        _visible_device_identifiers()
        if args.only_visible and args.only is None
        else None
    )
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
    return {
        token.strip().lower()
        for token in os.environ.get(MXTOP_MONITOR_MODE_ENV, "").split(",")
        if token.strip()
    }


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
    parser = argparse.ArgumentParser(
        prog="mxtop",
        description="An interactive MetaX-GPU process viewer.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    _ = parser.add_argument(
        "--version", "-V", action="version", version=f"mxtop {__version__}"
    )
    _ = parser.add_argument(
        "--backend", choices=["auto", "pymxsml", "mxsmi"], default="auto"
    )
    mode = parser.add_mutually_exclusive_group()
    _ = mode.add_argument(
        "--once", "-1", action="store_true", help="print one text snapshot and exit"
    )
    _ = mode.add_argument(
        "--monitor",
        "-m",
        nargs="?",
        choices=[layout.value for layout in LayoutMode],
        default=argparse.SUPPRESS,
        help=(
            "run interactively and handle user input\n"
            "If the layout is omitted, use MXTOP_MONITOR_MODE (fallback: auto)."
        ),
    )
    _ = mode.add_argument(
        "--json", action="store_true", help="print one JSON snapshot and exit"
    )
    _ = mode.add_argument(
        "--remote-mode",
        action="store_true",
        help="serve a local web dashboard aggregating multiple SSH nodes",
    )
    _ = parser.add_argument(
        "--interval",
        type=_interval,
        default=2.0,
        metavar="SEC",
        help="process status update interval in seconds (default: 2)",
    )
    _ = parser.add_argument(
        "--count",
        "-n",
        type=_count,
        default=None,
        metavar="N",
        help=(
            "with --once or --json, print N snapshots separated by --interval\n"
            "and exit (default: 1)"
        ),
    )
    _ = parser.add_argument(
        "--no-unicode",
        "--ascii",
        "-U",
        action="store_true",
        help="use ASCII characters only",
    )
    _ = parser.add_argument(
        "--readonly", action="store_true", help="disable process-changing actions"
    )

    coloring = parser.add_argument_group("coloring")
    _ = coloring.add_argument(
        "--no-color", action="store_true", help="disable ANSI color output"
    )
    _ = coloring.add_argument(
        "--colorful",
        action="store_true",
        help=(
            "use spectrum-like gradient colors for bar charts\n"
            "This option requires a terminal with 256-color support."
        ),
    )
    _ = coloring.add_argument(
        "--light",
        action="store_true",
        help="use colors suitable for light terminal themes",
    )
    _ = coloring.add_argument(
        "--force-color",
        action="store_true",
        help="emit ANSI colour even when stdout is not a TTY",
    )
    _ = coloring.add_argument(
        "--gpu-util-thresh",
        nargs=2,
        type=int,
        choices=range(1, 100),
        metavar=("LOW", "HIGH"),
        default=None,
        help=(
            "GPU utilization intensity thresholds\n"
            f"(default: {DEFAULT_GPU_UTILIZATION_THRESHOLDS[0]} "
            f"{DEFAULT_GPU_UTILIZATION_THRESHOLDS[1]})\n"
            f"Falls back to {MXTOP_GPU_THRESHOLDS_ENV}=LOW,HIGH when omitted."
        ),
    )
    _ = coloring.add_argument(
        "--mem-util-thresh",
        nargs=2,
        type=int,
        choices=range(1, 100),
        metavar=("LOW", "HIGH"),
        default=None,
        help=(
            "GPU memory intensity thresholds\n"
            f"(default: {DEFAULT_MEMORY_UTILIZATION_THRESHOLDS[0]} "
            f"{DEFAULT_MEMORY_UTILIZATION_THRESHOLDS[1]})\n"
            f"Falls back to {MXTOP_MEM_THRESHOLDS_ENV}=LOW,HIGH when omitted."
        ),
    )

    device_filtering = parser.add_argument_group("device filtering")
    _ = device_filtering.add_argument(
        "--only",
        "-o",
        nargs="+",
        type=int,
        metavar="INDEX",
        help="show only selected GPU indices; suppresses --only-visible",
    )
    _ = device_filtering.add_argument(
        "--only-visible",
        "-ov",
        action="store_true",
        help="show only devices in MACA_VISIBLE_DEVICES or CUDA_VISIBLE_DEVICES",
    )

    process_filtering = parser.add_argument_group("process filtering")
    _ = process_filtering.add_argument(
        "--compute",
        "-c",
        action="store_true",
        help="show processes with compute context (C or C+G/X)",
    )
    _ = process_filtering.add_argument(
        "--only-compute",
        "-C",
        action="store_true",
        help="show exactly compute-only processes (C)",
    )
    _ = process_filtering.add_argument(
        "--graphics",
        "-g",
        action="store_true",
        help="show processes with graphics context (G or C+G/X)",
    )
    _ = process_filtering.add_argument(
        "--only-graphics",
        "-G",
        action="store_true",
        help="show exactly graphics-only processes (G)",
    )
    _ = process_filtering.add_argument(
        "--user",
        "-u",
        nargs="*",
        metavar="USERNAME",
        help="show selected users (current user when omitted)",
    )
    _ = process_filtering.add_argument(
        "--pid",
        "-p",
        nargs="+",
        type=int,
        metavar="PID",
        help="show only selected process IDs",
    )

    remote = parser.add_argument_group("remote mode")
    _ = remote.add_argument(
        "--nodes",
        nargs="+",
        metavar="HOST",
        default=argparse.SUPPRESS,
        help="ssh hosts/aliases to monitor (resolved via ~/.ssh/config)",
    )
    _ = remote.add_argument(
        "--nodes-file",
        default=argparse.SUPPRESS,
        help="file with one ssh host per line (# comments allowed)",
    )
    _ = remote.add_argument(
        "--discover",
        action="store_true",
        default=argparse.SUPPRESS,
        help=(
            "discover passwordless SSH config hosts with working mx-smi\n"
            "This is the default when --nodes and --nodes-file are omitted."
        ),
    )
    _ = remote.add_argument(
        "--port",
        type=_port,
        default=argparse.SUPPRESS,
        help="dashboard port (default: 8080)",
    )
    _ = remote.add_argument(
        "--bind",
        default=argparse.SUPPRESS,
        help="dashboard bind address (default: 127.0.0.1)",
    )
    _ = remote.add_argument(
        "--remote-mxsmi-path",
        default=argparse.SUPPRESS,
        help="mx-smi path on remote hosts",
    )
    _ = remote.add_argument(
        "--auth-token",
        default=argparse.SUPPRESS,
        metavar="TOKEN",
        help=(
            "require this token for dashboard access\n"
            f"(default: {MXTOP_AUTH_TOKEN_ENV} environment variable, if set)"
        ),
    )
    _ = remote.add_argument(
        "--open",
        action="store_true",
        default=argparse.SUPPRESS,
        help="open the dashboard in a browser",
    )
    return parser


def _parse_threshold_env(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    try:
        parts = list(map(int, value.split(",")))[:2]
    except ValueError:
        return None
    if len(parts) != 2:
        return None
    low, high = sorted(parts)
    if not (0 < low <= high < 100):
        return None
    return low, high


def _coerce_threshold(
    values: list[float] | tuple[float, float] | None,
) -> tuple[int, int] | None:
    if values is None:
        return None
    if len(values) != 2:
        return None
    low, high = sorted(int(value) for value in values)
    if not (0 < low <= high < 100):
        return None
    return low, high


def _apply_intensity_thresholds(args: argparse.Namespace) -> None:
    gpu = _coerce_threshold(args.gpu_util_thresh) or _parse_threshold_env(
        os.environ.get(MXTOP_GPU_THRESHOLDS_ENV)
    )
    memory = _coerce_threshold(args.mem_util_thresh) or _parse_threshold_env(
        os.environ.get(MXTOP_MEM_THRESHOLDS_ENV)
    )
    if gpu is not None or memory is not None:
        set_intensity_thresholds(gpu=gpu, memory=memory)


def _report_invalid_device_indices(
    frame: FrameSnapshot, options: RuntimeOptions
) -> bool:
    if options.device_indices is None:
        return False
    valid = {device.index for device in frame.devices}
    invalid = sorted(options.device_indices.difference(valid))
    if not invalid:
        return False
    label = "indices" if len(invalid) > 1 else "index"
    print(
        f"MXTOP ERROR: Invalid device {label}: {invalid if len(invalid) > 1 else invalid[0]}.",
        file=sys.stderr,
    )
    return True


def _snapshot_width() -> int:
    return max(79, min(140, shutil.get_terminal_size(fallback=(79, 24)).columns))


REMOTE_ARGUMENTS = (
    "nodes",
    "nodes_file",
    "discover",
    "port",
    "bind",
    "remote_mxsmi_path",
    "auth_token",
    "open",
)

REMOTE_LOCAL_ONLY_OPTIONS = (
    "--backend",
    "--only-visible",
    "-ov",
    "--only",
    "-o",
    "--user",
    "-u",
    "--pid",
    "-p",
    "--compute",
    "-c",
    "--only-compute",
    "-C",
    "--graphics",
    "-g",
    "--only-graphics",
    "-G",
    "--no-unicode",
    "--ascii",
    "-U",
    "--readonly",
    "--no-color",
    "--colorful",
    "--light",
    "--force-color",
    "--gpu-util-thresh",
    "--mem-util-thresh",
)


def _supplied_option(argv: list[str], option: str) -> bool:
    if option.startswith("--"):
        for token in argv:
            supplied = token.partition("=")[0]
            if supplied == option:
                return True
            if len(supplied) > 2 and supplied.startswith("--") and option.startswith(supplied):
                return True
        return False
    return any(token == option or token.startswith(option) for token in argv)


def _validate_remote_arguments(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    argv: list[str],
) -> None:
    if args.remote_mode:
        unsupported = next(
            (
                option
                for option in REMOTE_LOCAL_ONLY_OPTIONS
                if _supplied_option(argv, option)
            ),
            None,
        )
        if unsupported is not None:
            parser.error(f"{unsupported} is not supported with --remote-mode")
        return
    supplied = [name for name in REMOTE_ARGUMENTS if hasattr(args, name)]
    if supplied:
        option = "--" + supplied[0].replace("_", "-")
        parser.error(f"{option} requires --remote-mode")


def _should_use_color(
    *, no_color: bool, force_color: bool, stdout_is_tty: bool
) -> bool:
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
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(raw_argv)
    _validate_remote_arguments(parser, args, raw_argv)
    if args.count is not None:
        if hasattr(args, "monitor") or args.remote_mode:
            parser.error("--count requires --once or --json")
        if not args.json:
            args.once = True

    monitor_tokens = _monitor_mode_tokens()
    explicit_monitor = hasattr(args, "monitor")
    stdin_is_tty = sys.stdin.isatty()
    stdout_is_tty = sys.stdout.isatty()
    monitor_unavailable = explicit_monitor and not (stdin_is_tty and stdout_is_tty)
    if monitor_unavailable:
        print(
            "MXTOP ERROR: monitor mode requires stdin and stdout to be TTY terminals",
            file=sys.stderr,
        )
    monitor_requested = (explicit_monitor and not monitor_unavailable) or (
        not args.once
        and not args.json
        and stdin_is_tty
        and stdout_is_tty
        and not args.remote_mode
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
        from mxtop.remote.app import report_discovery, run_remote
        from mxtop.remote.discovery import discover_configured_hosts
        from mxtop.remote.nodes import load_hosts, merge_hosts

        try:
            hosts = load_hosts(
                getattr(args, "nodes", None), getattr(args, "nodes_file", None)
            )
            mxsmi_path = getattr(args, "remote_mxsmi_path", "mx-smi")
            if getattr(args, "discover", False) or not hosts:
                discovered, results = discover_configured_hosts(mxsmi_path=mxsmi_path)
                report_discovery(results)
                hosts = merge_hosts(hosts, discovered)
        except Exception as exc:
            print(f"MXTOP ERROR: {exc}", file=sys.stderr)
            return 1
        if not hosts:
            print(
                "MXTOP ERROR: no passwordless SSH config hosts with a working "
                "mx-smi installation were found",
                file=sys.stderr,
            )
            return 1
        try:
            return run_remote(
                hosts,
                bind=getattr(args, "bind", "127.0.0.1"),
                port=getattr(args, "port", 8080),
                interval=args.interval,
                mxsmi_path=mxsmi_path,
                open_browser=getattr(args, "open", False),
                auth_token=(
                    getattr(args, "auth_token", None)
                    or os.environ.get(MXTOP_AUTH_TOKEN_ENV)
                    or None
                ),
            )
        except Exception as exc:
            print(f"MXTOP ERROR: {exc}", file=sys.stderr)
            return 1

    _apply_intensity_thresholds(args)
    set_render_style(light=args.light, colorful=args.colorful)
    options = _runtime_options(args)
    try:
        selected_backend = backend or create_backend(args.backend)
    except Exception as exc:
        print(f"MXTOP ERROR: {exc}", file=sys.stderr)
        return 1
    options.supported_process_contexts = getattr(
        selected_backend,
        "process_context_types",
        None,
    )

    if args.json:
        had_errors = False
        for iteration in range(args.count or 1):
            if iteration:
                time.sleep(args.interval)
            try:
                frame = _single_snapshot_with_cpu_sample(selected_backend, options)
            except Exception as exc:
                print(f"MXTOP ERROR: {exc}", file=sys.stderr)
                return 1
            had_errors = _report_invalid_device_indices(frame, options) or had_errors
            print(
                json.dumps(
                    sanitize_json_value(frame.to_dict()),
                    indent=2,
                    sort_keys=True,
                    allow_nan=False,
                )
            )
        return int(had_errors)

    use_color = _should_use_color(
        no_color=options.no_color,
        force_color=args.force_color,
        stdout_is_tty=stdout_is_tty,
    )
    if args.once or not monitor_requested:
        had_errors = False
        for iteration in range(args.count or 1):
            if iteration:
                time.sleep(args.interval)
            try:
                frame = _single_snapshot_with_cpu_sample(selected_backend, options)
            except Exception as exc:
                print(f"MXTOP ERROR: {exc}", file=sys.stderr)
                return 1
            had_errors = _report_invalid_device_indices(frame, options) or had_errors
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
