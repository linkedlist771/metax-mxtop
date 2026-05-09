from __future__ import annotations

import argparse
import json
import sys

from mxtop import __version__
from mxtop.backends import TelemetryBackend, create_backend
from mxtop.rendering import render_once
from mxtop.tui import run_tui


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="An nvitop-like monitor for MetaX GPUs.")
    _ = parser.add_argument("--version", action="version", version=f"mxtop {__version__}")
    _ = parser.add_argument("--backend", choices=["auto", "pymxsml", "mxsmi"], default="auto")
    _ = parser.add_argument("--interval", type=float, default=1.0, help="refresh interval in seconds")
    _ = parser.add_argument("--once", "-1", action="store_true", help="print one text snapshot and exit")
    _ = parser.add_argument("--json", action="store_true", help="print one JSON snapshot and exit")
    _ = parser.add_argument("--no-color", action="store_true", help="disable ANSI color output")
    return parser


def main(argv: list[str] | None = None, backend: TelemetryBackend | None = None) -> int:
    args = build_parser().parse_args(argv)
    selected_backend = backend or create_backend(args.backend)

    if args.json:
        print(json.dumps(selected_backend.snapshot().to_dict(), indent=2, sort_keys=True))
        return 0

    if args.once or not sys.stdout.isatty():
        print(render_once(selected_backend.snapshot(), use_color=not args.no_color))
        return 0

    return run_tui(selected_backend, args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
