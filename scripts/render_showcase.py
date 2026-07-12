"""Generate reproducible fixed-viewport TUI and command-output showcases."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(SCRIPT_DIR))

from mxtop.rendering import (  # noqa: E402
    colorize_screen,
    render_once,
    reset_intensity_thresholds,
    set_render_style,
)
from mxtop._compat import DATACLASS_SLOTS  # noqa: E402
from mxtop.ui.panels import render_main_screen  # noqa: E402
from mxtop.ui.state import LayoutMode, UiState  # noqa: E402
from generate_preview import asset_is_fresh, render_to_png  # noqa: E402
from synthetic_fixtures import FRAME_BUILDERS, prepare_render, utc_timezone  # noqa: E402

SHOWCASE_DIR = PROJECT_ROOT / "assets" / "showcase"


@dataclass(frozen=True, **DATACLASS_SLOTS)
class ShowcaseSpec:
    slug: str
    description: str
    scenario: str
    kind: str
    width: int
    height: int | None = None
    color: bool = True
    layout: LayoutMode = LayoutMode.AUTO
    command: str = ""


SHOWCASE_SPECS = (
    ShowcaseSpec("tui-092x028-idle-3gpu", "92x28 viewport, 3 idle GPUs", "idle", "tui", 92, 28),
    ShowcaseSpec("tui-122x036-mixed-3gpu", "122x36 viewport, 3 mixed-load GPUs", "small", "tui", 122, 36),
    ShowcaseSpec("tui-142x036-heavy-4gpu", "142x36 viewport, 4 heavily loaded GPUs", "heavy", "tui", 142, 36),
    ShowcaseSpec("tui-172x044-many-16gpu", "172x44 viewport, 16 mixed-load GPUs", "many", "tui", 172, 44),
    ShowcaseSpec("tui-180x044-many-64gpu", "180x44 viewport, 64-GPU fleet overview", "sixty-four", "tui", 180, 44),
    ShowcaseSpec("output-once-color-140-mixed", "Colored one-shot mixed-load output", "mixed", "once", 140, command="mxtop --once"),
    ShowcaseSpec("output-once-plain-110-idle", "Uncolored one-shot idle output", "idle", "once", 110, color=False, command="mxtop --once --no-color"),
    ShowcaseSpec("output-json-110-small", "Complete JSON output from a one-GPU fixture", "single-idle", "json", 110, color=False, command="mxtop --json"),
)


def _colorize(frame, lines: list[str]) -> str:
    return "\n".join(colorize_screen(frame, lines))


def _tui_lines(spec: ShowcaseSpec, frame) -> list[str]:
    assert spec.height is not None
    screen = render_main_screen(
        frame,
        UiState(layout=spec.layout),
        width=spec.width,
        height=spec.height,
    )
    return screen.lines


def render_showcase_text(spec: ShowcaseSpec) -> str:
    reset_intensity_thresholds()
    set_render_style(light=False, colorful=False)
    prepare_render()
    with utc_timezone():
        if spec.kind == "tui":
            frame = FRAME_BUILDERS[spec.scenario]()
            lines = _tui_lines(spec, frame)
            return _colorize(frame, lines) if spec.color else "\n".join(lines)
        frame = FRAME_BUILDERS[spec.scenario]()
        if spec.kind == "json":
            body = json.dumps(frame.to_dict(), indent=2, sort_keys=True, allow_nan=False)
        else:
            body = render_once(frame, use_color=spec.color, width=spec.width)
        prompt = f"$ {spec.command}"
        if spec.color:
            prompt = f"\x1b[1;36m{prompt}\x1b[0m"
        return f"{prompt}\n{body}"


def render_showcase_asset(spec: ShowcaseSpec, target: Path, *, check: bool = False) -> bool:
    output = render_showcase_text(spec)
    if check:
        return asset_is_fresh(target, output, "dark")
    render_to_png(output, "dark", target, source_name=f"showcase:{spec.slug}")
    return True


def render_all(*, check: bool = False, output_dir: Path = SHOWCASE_DIR) -> list[str]:
    stale: list[str] = []
    if not check:
        output_dir.mkdir(parents=True, exist_ok=True)
    for spec in SHOWCASE_SPECS:
        target = output_dir / f"{spec.slug}.png"
        if not render_showcase_asset(spec, target, check=check):
            stale.append(spec.slug)
        elif not check:
            print(f"wrote {target.relative_to(PROJECT_ROOT) if target.is_relative_to(PROJECT_ROOT) else target}")
    return stale


def showcase_markdown() -> str:
    tui_specs = [spec for spec in SHOWCASE_SPECS if spec.kind == "tui"]
    output_specs = [spec for spec in SHOWCASE_SPECS if spec.kind != "tui"]
    sections = [
        "# mxtop Preview Showcase\n",
        "These screenshots use the same fixed-time canonical MetaX telemetry as the CLI gallery. Each PNG embeds a digest of its rendered source text.\n",
        "Re-render with ``uv run --locked --with pillow --with psutil python scripts/render_showcase.py``; add ``--check`` to verify freshness.\n",
        "## Interactive TUI\n",
        "| Scenario | Preview |",
        "| --- | --- |",
    ]
    sections.extend(
        f"| {spec.description} | ![{spec.description}](assets/showcase/{spec.slug}.png) |"
        for spec in tui_specs
    )
    sections.extend(("", "## Command Output\n", "| Scenario | Preview |", "| --- | --- |"))
    sections.extend(
        f"| `{spec.command}`<br><sub>{spec.description}</sub> | ![{spec.description}](assets/showcase/{spec.slug}.png) |"
        for spec in output_specs
    )
    return "\n".join(sections).rstrip() + "\n"


def write_showcase_markdown() -> None:
    target = PROJECT_ROOT / "SHOWCASE.md"
    target.write_text(showcase_markdown(), encoding="utf-8")
    print(f"wrote {target.relative_to(PROJECT_ROOT)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify PNG source digests without writing")
    parser.add_argument("--output-dir", type=Path, default=SHOWCASE_DIR)
    args = parser.parse_args()
    output_dir = args.output_dir if args.output_dir.is_absolute() else PROJECT_ROOT / args.output_dir
    stale = render_all(check=args.check, output_dir=output_dir)
    if args.check:
        markdown_fresh = (PROJECT_ROOT / "SHOWCASE.md").read_text(encoding="utf-8") == showcase_markdown()
        if not markdown_fresh:
            stale.append("SHOWCASE.md")
        if stale:
            print("stale showcase artifacts: " + ", ".join(stale), file=sys.stderr)
            return 1
        return 0
    write_showcase_markdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
