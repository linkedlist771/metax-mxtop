"""Render a reproducible gallery of mxtop command and monitor outputs."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(SCRIPT_DIR))

from mxtop.filters import apply_filters  # noqa: E402
from mxtop._compat import DATACLASS_SLOTS  # noqa: E402
from mxtop.models import FrameSnapshot  # noqa: E402
from mxtop.rendering import (  # noqa: E402
    colorize_screen,
    render_once,
    reset_intensity_thresholds,
    set_intensity_thresholds,
    set_render_style,
)
from mxtop.ui.panels import render_main_screen  # noqa: E402
from mxtop.ui.state import LayoutMode, UiState  # noqa: E402
from generate_preview import asset_is_fresh, render_to_png  # noqa: E402
from synthetic_fixtures import FRAME_BUILDERS, prepare_render, utc_timezone  # noqa: E402

GALLERY_DIR = PROJECT_ROOT / "assets" / "gallery"


@dataclass(frozen=True, **DATACLASS_SLOTS)
class Variant:
    slug: str
    cmd: str
    description: str
    frame_name: str
    width: int = 140
    color: bool = True
    light: bool = False
    colorful: bool = False
    kind: str = "once"
    layout: LayoutMode = LayoutMode.AUTO
    filter_only: tuple[int, ...] = ()
    filter_user: tuple[str, ...] = ()
    filter_pid: tuple[int, ...] = ()
    compute: bool = False
    only_compute: bool = False
    graphics: bool = False
    only_graphics: bool = False
    gpu_threshold: tuple[int, int] | None = None
    mem_threshold: tuple[int, int] | None = None
    height: int | None = None


VARIANTS: tuple[Variant, ...] = (
    Variant("once-default", "mxtop --once", "Default colored snapshot with mixed MetaX load.", "three"),
    Variant("once-no-color", "mxtop --once --no-color", "Uncolored Unicode snapshot for logs and pipes.", "three", color=False),
    Variant("json-default", "mxtop --json", "Complete valid JSON snapshot from a one-GPU fixture.", "single-idle", width=110, color=False, kind="json"),
    Variant("once-colorful", "mxtop --once --colorful", "Spectrum-like utilization bars.", "mixed4", colorful=True),
    Variant("once-light", "mxtop --once --light", "Snapshot rendered for a light terminal theme.", "three", light=True),
    Variant("monitor-full", "mxtop --monitor full", "Representative interactive full-mode frame.", "mixed4", kind="tui", layout=LayoutMode.FULL, height=40),
    Variant("monitor-compact", "mxtop --monitor compact", "Representative interactive compact-mode frame.", "eight", kind="tui", layout=LayoutMode.COMPACT, height=34),
    Variant("once-only", "mxtop --once --only 0 2", "Only GPU indices 0 and 2 and their processes.", "three", filter_only=(0, 2)),
    Variant("once-user", "mxtop --once --user alice", "Only processes owned by alice.", "three", filter_user=("alice",)),
    Variant("once-pid", "mxtop --once --pid 423901 512377", "Only the selected process IDs.", "three", filter_pid=(423901, 512377)),
    Variant("once-compute", "mxtop --once --compute", "Processes with a compute context, including mixed C+G.", "three", compute=True),
    Variant("once-only-compute", "mxtop --once --only-compute", "Processes with an exact compute-only context.", "three", only_compute=True),
    Variant("once-graphics", "mxtop --once --graphics", "Processes with a graphics context, including mixed C+G.", "three", graphics=True),
    Variant("once-only-graphics", "mxtop --once --only-graphics", "Processes with an exact graphics-only context.", "three", only_graphics=True),
    Variant("once-gpu-thresh", "mxtop --once --gpu-util-thresh 30 60", "Custom GPU intensity thresholds at 30% and 60%.", "mixed4", gpu_threshold=(30, 60)),
    Variant("once-mem-thresh", "mxtop --once --mem-util-thresh 20 50", "Custom memory intensity thresholds at 20% and 50%.", "mixed4", mem_threshold=(20, 50)),
    Variant("once-heavy", "mxtop --once", "Four-GPU saturation fixture.", "heavy"),
    Variant("once-idle", "mxtop --once", "Three-GPU idle fixture.", "idle"),
    Variant("once-many-8", "mxtop --once", "Eight-GPU mixed-load fixture at 170 columns.", "eight", width=170),
    Variant("once-many-16", "mxtop --once", "Sixteen-GPU mixed-load fixture at 180 columns.", "sixteen", width=180),
    Variant("once-missing", "mxtop --once", "Unavailable backend fields rendered as N/A.", "missing"),
)


def _filtered(frame: FrameSnapshot, variant: Variant) -> FrameSnapshot:
    if not any(
        (
            variant.filter_only,
            variant.filter_user,
            variant.filter_pid,
            variant.compute,
            variant.only_compute,
            variant.graphics,
            variant.only_graphics,
        )
    ):
        return frame
    return apply_filters(
        frame,
        device_indices=set(variant.filter_only) or None,
        users=set(variant.filter_user) or None,
        pids=set(variant.filter_pid) or None,
        compute=variant.compute,
        only_compute=variant.only_compute,
        graphics=variant.graphics,
        only_graphics=variant.only_graphics,
    )


def _colorize(frame: FrameSnapshot, lines: list[str]) -> str:
    return "\n".join(colorize_screen(frame, lines))


def render_variant_text(variant: Variant) -> str:
    reset_intensity_thresholds()
    set_render_style(light=variant.light, colorful=variant.colorful)
    if variant.gpu_threshold is not None:
        set_intensity_thresholds(gpu=variant.gpu_threshold)
    if variant.mem_threshold is not None:
        set_intensity_thresholds(memory=variant.mem_threshold)
    prepare_render()
    frame = _filtered(FRAME_BUILDERS[variant.frame_name](), variant)
    with utc_timezone():
        if variant.kind == "json":
            return json.dumps(frame.to_dict(), indent=2, sort_keys=True, allow_nan=False)
        if variant.kind == "tui":
            screen = render_main_screen(
                frame,
                UiState(layout=variant.layout),
                width=variant.width,
                height=variant.height,
            )
            return _colorize(frame, screen.lines) if variant.color else "\n".join(screen.lines)
        return render_once(frame, use_color=variant.color, width=variant.width)


def _prefixed_text(variant: Variant) -> str:
    prompt = f"$ {variant.cmd}"
    if variant.color:
        prompt = f"\x1b[1;36m{prompt}\x1b[0m"
    return f"{prompt}\n{render_variant_text(variant)}"


def render_variant_asset(variant: Variant, target: Path, *, check: bool = False) -> bool:
    output = _prefixed_text(variant)
    theme = "light" if variant.light else "dark"
    if check:
        return asset_is_fresh(target, output, theme)
    render_to_png(output, theme, target, source_name=f"gallery:{variant.slug}")
    return True


def render_all(*, check: bool = False, output_dir: Path = GALLERY_DIR) -> list[str]:
    stale: list[str] = []
    if not check:
        output_dir.mkdir(parents=True, exist_ok=True)
    for variant in VARIANTS:
        target = output_dir / f"{variant.slug}.png"
        if not render_variant_asset(variant, target, check=check):
            stale.append(variant.slug)
        elif not check:
            print(f"wrote {target.relative_to(PROJECT_ROOT) if target.is_relative_to(PROJECT_ROOT) else target}")
    reset_intensity_thresholds()
    set_render_style(light=False, colorful=False)
    return stale


def gallery_markdown() -> str:
    groups = (
        ("Snapshot modes", ("once-default", "once-no-color", "json-default")),
        ("Color and palette", ("once-colorful", "once-light")),
        ("Interactive layouts", ("monitor-full", "monitor-compact")),
        ("Device and owner filters", ("once-only", "once-user", "once-pid")),
        ("Process-type filters", ("once-compute", "once-only-compute", "once-graphics", "once-only-graphics")),
        ("Custom intensity thresholds", ("once-gpu-thresh", "once-mem-thresh")),
        ("Load profiles", ("once-idle", "once-heavy")),
        ("Multi-GPU fixtures", ("once-many-8", "once-many-16")),
        ("Missing telemetry", ("once-missing",)),
    )
    by_slug = {variant.slug: variant for variant in VARIANTS}
    sections = [
        "# mxtop Output Gallery\n",
        "Every image is generated from fixed-time canonical MetaX telemetry. PNG metadata records a digest of the exact rendered text.\n",
        "Re-render with ``uv run --locked --with pillow --with psutil python scripts/render_gallery.py``; verify freshness with the same command plus ``--check``.\n",
    ]
    for title, slugs in groups:
        sections.extend((f"## {title}\n", "| Command | Preview |", "| --- | --- |"))
        for slug in slugs:
            variant = by_slug[slug]
            sections.append(
                f"| `{variant.cmd}`<br><sub>{variant.description}</sub> "
                f"| ![{variant.slug}](assets/gallery/{variant.slug}.png) |"
            )
        sections.append("")
    return "\n".join(sections).rstrip() + "\n"


def write_gallery_markdown() -> None:
    target = PROJECT_ROOT / "GALLERY.md"
    target.write_text(gallery_markdown(), encoding="utf-8")
    print(f"wrote {target.relative_to(PROJECT_ROOT)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify PNG source digests without writing")
    parser.add_argument("--output-dir", type=Path, default=GALLERY_DIR)
    args = parser.parse_args()
    output_dir = args.output_dir if args.output_dir.is_absolute() else PROJECT_ROOT / args.output_dir
    stale = render_all(check=args.check, output_dir=output_dir)
    if args.check:
        markdown_fresh = (PROJECT_ROOT / "GALLERY.md").read_text(encoding="utf-8") == gallery_markdown()
        if not markdown_fresh:
            stale.append("GALLERY.md")
        if stale:
            print("stale gallery artifacts: " + ", ".join(stale), file=sys.stderr)
            return 1
        return 0
    write_gallery_markdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
