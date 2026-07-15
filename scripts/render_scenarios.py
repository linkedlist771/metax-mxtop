#!/usr/bin/env python3
"""Render deterministic mxtop frames to stdout for visual review.

Run with ``uv run --with psutil python scripts/render_scenarios.py``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(SCRIPT_DIR))

from mxtop.rendering import render_once, set_render_style  # noqa: E402
from synthetic_fixtures import (  # noqa: E402
    SCENARIO_BUILDERS,
    prepare_render,
    utc_timezone,
)

SCENARIOS = SCENARIO_BUILDERS
WIDTHS = [79, 100, 120, 160, 200]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=list(SCENARIOS), default=None)
    parser.add_argument("--width", type=int, default=None)
    parser.add_argument("--color", action="store_true")
    parser.add_argument("--colorful", action="store_true")
    parser.add_argument("--light", action="store_true")
    args = parser.parse_args()

    set_render_style(light=args.light, colorful=args.colorful)
    scenarios = [args.scenario] if args.scenario else list(SCENARIOS)
    widths = [args.width] if args.width else WIDTHS
    with utc_timezone():
        for name in scenarios:
            for width in widths:
                prepare_render()
                frame = SCENARIOS[name]()
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
