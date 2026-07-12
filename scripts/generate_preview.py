"""Render deterministic mxtop frames as PNG terminal previews."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

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
from synthetic_fixtures import (  # noqa: E402
    FRAME_BUILDERS,
    build_frame,
    prepare_render,
    utc_timezone,
)

ANSI_PATTERN = re.compile(r"\x1b\[(\d+(?:;\d+)*)m")
SYMBOL_SPLIT = re.compile(r"([█▏▎▍▌▋▊▉\u2800-\u28ff]+)")
_BLOCK_FRACTIONS = {character: index / 8.0 for index, character in enumerate(" ▏▎▍▌▋▊▉")}
_BLOCK_FRACTIONS["█"] = 1.0
SOURCE_HASH_KEY = "mxtop-source-sha256"
SOURCE_NAME_KEY = "mxtop-source-name"
THEME_KEY = "mxtop-theme"
FONT_KEY = "mxtop-font"
RENDER_CONFIG_KEY = "mxtop-render-config-sha256"

THEMES = {
    "dark": {
        "bg": (15, 18, 23),
        "fg": (218, 220, 224),
        "selection_bg": (45, 90, 175),
        "30": (32, 32, 32),
        "31": (220, 80, 80),
        "32": (102, 195, 110),
        "33": (220, 188, 70),
        "34": (90, 158, 220),
        "35": (200, 110, 200),
        "36": (90, 200, 215),
        "37": (218, 220, 224),
        "90": (100, 104, 112),
        "91": (255, 105, 105),
        "92": (130, 225, 138),
        "93": (255, 220, 95),
        "94": (120, 185, 245),
        "95": (235, 135, 235),
        "96": (120, 225, 235),
        "97": (245, 246, 248),
    },
    "light": {
        "bg": (250, 250, 250),
        "fg": (40, 44, 52),
        "selection_bg": (180, 200, 235),
        "30": (200, 200, 200),
        "31": (210, 70, 70),
        "32": (45, 145, 60),
        "33": (175, 130, 30),
        "34": (50, 110, 200),
        "35": (160, 70, 175),
        "36": (40, 145, 160),
        "37": (40, 44, 52),
        "90": (135, 138, 145),
        "91": (225, 55, 55),
        "92": (35, 160, 55),
        "93": (195, 140, 20),
        "94": (45, 105, 205),
        "95": (175, 55, 185),
        "96": (30, 155, 170),
        "97": (20, 24, 30),
    },
}


@dataclass(frozen=True, **DATACLASS_SLOTS)
class FontSpec:
    path: Path
    index: int = 0

    @property
    def label(self) -> str:
        return f"{self.path}:{self.index}"


@dataclass(frozen=True, **DATACLASS_SLOTS)
class PreviewSpec:
    target: str
    scenario: str
    width: int = 140
    theme: str = "dark"
    height: int | None = None


PREVIEW_SPECS = (
    PreviewSpec("assets/mxtop-preview.png", "small"),
    PreviewSpec("assets/mxtop-dark.png", "small"),
    PreviewSpec("assets/mxtop-light.png", "small", theme="light"),
    PreviewSpec("assets/mxtop-preview-light.png", "small", theme="light"),
    PreviewSpec("assets/mxtop-preview-idle.png", "idle"),
    PreviewSpec("assets/mxtop-preview-mixed.png", "mixed"),
    PreviewSpec("assets/mxtop-preview-heavy.png", "heavy"),
    PreviewSpec("assets/mxtop-preview-many.png", "sixty-four", width=180, height=44),
)

_REGULAR_FONT_CANDIDATES = (
    FontSpec(Path("/System/Library/Fonts/Menlo.ttc"), 0),
    FontSpec(Path("/Library/Fonts/Menlo.ttc"), 0),
    FontSpec(Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf")),
    FontSpec(Path("/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf")),
    FontSpec(Path("/usr/share/fonts/truetype/freefont/FreeMono.ttf")),
    FontSpec(Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc")),
)
_BOLD_FONT_CANDIDATES = (
    FontSpec(Path("/System/Library/Fonts/Menlo.ttc"), 1),
    FontSpec(Path("/Library/Fonts/Menlo.ttc"), 1),
    FontSpec(Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf")),
    FontSpec(Path("/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf")),
    FontSpec(Path("/usr/share/fonts/truetype/freefont/FreeMonoBold.ttf")),
)
_SYMBOL_FONT_CANDIDATES = (
    FontSpec(Path("/System/Library/Fonts/Apple Symbols.ttf")),
    FontSpec(Path("/usr/share/fonts/opentype/unifont/unifont.otf")),
    FontSpec(Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf")),
    FontSpec(Path("/usr/share/fonts/truetype/freefont/FreeMono.ttf")),
)


def _env_font(name: str) -> FontSpec | None:
    value = os.environ.get(name)
    if not value:
        return None
    path_text, separator, index_text = value.rpartition(":")
    if separator and index_text.isdigit():
        return FontSpec(Path(path_text), int(index_text))
    return FontSpec(Path(value))


def _fontconfig(pattern: str) -> FontSpec | None:
    try:
        result = subprocess.run(
            ["fc-match", "-f", "%{file}\t%{index}\n", pattern],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    first = result.stdout.splitlines()[0] if result.stdout else ""
    path_text, _, index_text = first.partition("\t")
    path = Path(path_text)
    if not path.is_file():
        return None
    return FontSpec(path, int(index_text or "0"))


def discover_font(kind: str = "regular") -> FontSpec | None:
    """Resolve a usable font without assuming a particular operating system."""
    if kind == "bold":
        env_name = "MXTOP_PREVIEW_BOLD_FONT"
        candidates = _BOLD_FONT_CANDIDATES
        pattern = "monospace:style=Bold"
    elif kind == "symbols":
        env_name = "MXTOP_PREVIEW_SYMBOL_FONT"
        candidates = _SYMBOL_FONT_CANDIDATES
        pattern = "Unifont"
    else:
        env_name = "MXTOP_PREVIEW_FONT"
        candidates = _REGULAR_FONT_CANDIDATES
        pattern = "monospace:style=Regular"
    configured = _env_font(env_name)
    if configured is not None and configured.path.is_file():
        return configured
    for candidate in candidates:
        if candidate.path.is_file():
            return candidate
    return _fontconfig(pattern)


def _load_font(spec: FontSpec | None, font_size: int):
    if spec is not None:
        try:
            return ImageFont.truetype(str(spec.path), font_size, index=spec.index)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=font_size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def parse_segments(line: str) -> Iterable[tuple[str, list[str]]]:
    cursor = 0
    state: list[str] = []
    for match in ANSI_PATTERN.finditer(line):
        if match.start() > cursor:
            yield line[cursor : match.start()], list(state)
        for code in match.group(1).split(";"):
            if code in {"", "0"}:
                state = []
            else:
                state.append(code)
        cursor = match.end()
    if cursor < len(line):
        yield line[cursor:], list(state)


def source_digest(output: str) -> str:
    return hashlib.sha256(output.encode("utf-8")).hexdigest()


@lru_cache(maxsize=None)
def _font_fingerprint(spec: FontSpec | None) -> str:
    if spec is None:
        return "Pillow default"
    try:
        digest = hashlib.sha256(spec.path.read_bytes()).hexdigest()
    except OSError:
        digest = "unreadable"
    return f"{spec.label}:{digest}"


def render_config_digest(
    theme_name: str,
    font_size: int,
    regular_spec: FontSpec | None,
    bold_spec: FontSpec | None,
    symbol_spec: FontSpec | None,
) -> str:
    renderer_source = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    payload = {
        "font_size": font_size,
        "fonts": [
            _font_fingerprint(regular_spec),
            _font_fingerprint(bold_spec),
            _font_fingerprint(symbol_spec),
        ],
        "renderer": renderer_source,
        "theme": THEMES[theme_name],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _colorized_screen(frame, width: int, height: int | None) -> str:
    if height is None:
        return render_once(frame, use_color=True, width=width)
    screen = render_main_screen(
        frame,
        UiState(layout=LayoutMode.AUTO),
        width=width,
        height=height,
    )
    return "\n".join(colorize_screen(frame, screen.lines))


def render_preview_text(
    scenario: str = "small",
    *,
    width: int = 140,
    height: int | None = None,
    theme: str = "dark",
) -> str:
    reset_intensity_thresholds()
    set_render_style(light=theme == "light", colorful=False)
    prepare_render()
    with utc_timezone():
        return _colorized_screen(build_frame(scenario), width, height)


def render_to_png(
    output: str,
    theme_name: str,
    target: Path,
    *,
    source_name: str = "",
    font_size: int = 18,
) -> None:
    theme = THEMES[theme_name]
    regular_spec = discover_font("regular")
    bold_spec = discover_font("bold") or regular_spec
    symbol_spec = discover_font("symbols") or regular_spec
    font = _load_font(regular_spec, font_size)
    bold_font = _load_font(bold_spec, font_size)
    symbol_font = _load_font(symbol_spec, font_size)
    char_width = max(1, round(float(font.getlength("M"))))
    line_height = font_size + 6

    lines = output.split("\n")
    max_cols = max((len(ANSI_PATTERN.sub("", line)) for line in lines), default=80)
    width = char_width * (max_cols + 2)
    height = line_height * (len(lines) + 2)
    image = Image.new("RGB", (width, height), theme["bg"])
    draw = ImageDraw.Draw(image)

    for row, raw_line in enumerate(lines):
        x = char_width
        y = line_height * (row + 1)
        for text, state in parse_segments(raw_line):
            bold = "1" in state
            reverse = "7" in state
            fg_code = next((code for code in reversed(state) if code in theme), "37")
            fg = theme.get(fg_code, theme["fg"])
            bg = theme["bg"]
            if "2" in state:
                fg = _dim(fg, theme["bg"])
            if reverse:
                fg, bg = bg, fg
                if bg == theme["bg"]:
                    bg = theme["selection_bg"]
            text_width = char_width * len(text)
            if bg != theme["bg"]:
                draw.rectangle((x, y, x + text_width, y + line_height), fill=bg)
            chosen_font = bold_font if bold else font
            run_x = x
            for part in SYMBOL_SPLIT.split(text):
                if not part:
                    continue
                if SYMBOL_SPLIT.fullmatch(part):
                    for column, char in enumerate(part):
                        cell_x = run_x + char_width * column
                        fraction = _BLOCK_FRACTIONS.get(char)
                        if fraction is None:
                            draw.text((cell_x, y), char, fill=fg, font=symbol_font)
                        else:
                            block_width = max(1, round(char_width * fraction))
                            draw.rectangle(
                                (cell_x, y, cell_x + block_width - 1, y + font_size + 1),
                                fill=fg,
                            )
                else:
                    draw.text((run_x, y), part, fill=fg, font=chosen_font)
                run_x += char_width * len(part)
            x += text_width

    metadata = PngImagePlugin.PngInfo()
    metadata.add_text(SOURCE_HASH_KEY, source_digest(output))
    metadata.add_text(SOURCE_NAME_KEY, source_name)
    metadata.add_text(THEME_KEY, theme_name)
    metadata.add_text(FONT_KEY, regular_spec.label if regular_spec is not None else "Pillow default")
    metadata.add_text(
        RENDER_CONFIG_KEY,
        render_config_digest(theme_name, font_size, regular_spec, bold_spec, symbol_spec),
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, pnginfo=metadata)


def asset_is_fresh(target: Path, output: str, theme: str) -> bool:
    if not target.is_file():
        return False
    try:
        regular_spec = discover_font("regular")
        bold_spec = discover_font("bold") or regular_spec
        symbol_spec = discover_font("symbols") or regular_spec
        with Image.open(target) as image:
            return (
                image.info.get(SOURCE_HASH_KEY) == source_digest(output)
                and image.info.get(THEME_KEY) == theme
                and image.info.get(FONT_KEY)
                == (regular_spec.label if regular_spec is not None else "Pillow default")
                and image.info.get(RENDER_CONFIG_KEY)
                == render_config_digest(theme, 18, regular_spec, bold_spec, symbol_spec)
                and image.width > 0
                and image.height > 0
            )
    except OSError:
        return False


def render_preview_spec(spec: PreviewSpec, *, check: bool = False) -> bool:
    output = render_preview_text(
        spec.scenario,
        width=spec.width,
        height=spec.height,
        theme=spec.theme,
    )
    target = PROJECT_ROOT / spec.target
    if check:
        return asset_is_fresh(target, output, spec.theme)
    render_to_png(output, spec.theme, target, source_name=spec.target)
    print(f"wrote {target.relative_to(PROJECT_ROOT)}")
    return True


def _matching_spec(target: Path) -> PreviewSpec | None:
    resolved = target if target.is_absolute() else PROJECT_ROOT / target
    for spec in PREVIEW_SPECS:
        if resolved == PROJECT_ROOT / spec.target:
            return spec
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--theme", choices=list(THEMES), default="dark")
    parser.add_argument("--output", default="assets/mxtop-preview.png", type=Path)
    parser.add_argument("--width", type=int, default=140)
    parser.add_argument("--height", type=int, default=None)
    parser.add_argument("--scenario", choices=sorted(FRAME_BUILDERS), default="small")
    parser.add_argument("--all", action="store_true", help="render every README preview asset")
    parser.add_argument("--check", action="store_true", help="check embedded source digests without writing")
    args = parser.parse_args()

    if args.all:
        stale = [spec.target for spec in PREVIEW_SPECS if not render_preview_spec(spec, check=args.check)]
        if stale:
            print("stale preview assets: " + ", ".join(stale), file=sys.stderr)
            return 1
        return 0

    target = args.output if args.output.is_absolute() else PROJECT_ROOT / args.output
    spec = _matching_spec(args.output)
    if spec is not None and (
        args.scenario,
        args.width,
        args.height,
        args.theme,
    ) == ("small", 140, None, "dark"):
        scenario, width, height, theme = spec.scenario, spec.width, spec.height, spec.theme
    else:
        scenario, width, height, theme = args.scenario, args.width, args.height, args.theme
    output = render_preview_text(scenario, width=width, height=height, theme=theme)
    if args.check:
        if asset_is_fresh(target, output, theme):
            return 0
        print(f"stale preview asset: {target}", file=sys.stderr)
        return 1
    render_to_png(output, theme, target, source_name=str(args.output))
    print(f"wrote {target}")
    return 0


def _dim(color: tuple[int, int, int], bg: tuple[int, int, int]) -> tuple[int, int, int]:
    return tuple(int(component * 0.6 + background * 0.4) for component, background in zip(color, bg))


if __name__ == "__main__":
    raise SystemExit(main())
