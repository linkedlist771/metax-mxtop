from __future__ import annotations

import importlib
import hashlib
import json
from pathlib import Path
import re
import sys

import pytest

from mxtop.ui.text import cell_width

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from synthetic_fixtures import (  # noqa: E402
    DEVICE_MEMORY_BYTES,
    DRIVER_VERSION,
    FIXED_TIMESTAMP,
    FRAME_BUILDERS,
    HOST_MEMORY_BYTES,
    HOST_UPTIME_TEXT,
    MACA_VERSION,
    SCENARIO_BUILDERS,
    frame_three_gpu,
    frame_sixty_four_mixed,
    frame_thirty_two_mixed,
)


def _with_pillow(module_name: str):
    pytest.importorskip("PIL")
    return importlib.import_module(module_name)


def test_canonical_frames_are_new_and_deterministic() -> None:
    for name, builder in FRAME_BUILDERS.items():
        first = builder()
        second = builder()
        assert first is not second, name
        first_json = json.dumps(first.to_dict(), sort_keys=True, allow_nan=True)
        second_json = json.dumps(second.to_dict(), sort_keys=True, allow_nan=True)
        assert first_json == second_json, name
        assert first.timestamp == FIXED_TIMESTAMP, name


def test_scenario_renderer_uses_the_canonical_builders() -> None:
    render_scenarios = importlib.import_module("render_scenarios")
    assert render_scenarios.SCENARIOS is SCENARIO_BUILDERS
    assert {"thirty-two-loaded", "sixty-four-loaded"} <= set(render_scenarios.SCENARIOS)


def test_known_devices_have_consistent_memory_and_metax_versions() -> None:
    for name, builder in FRAME_BUILDERS.items():
        for device in builder().devices:
            if device.memory_util_percent is not None:
                expected_used = round(
                    device.memory_util_percent / 100.0 * DEVICE_MEMORY_BYTES
                )
                assert device.memory_total_bytes == DEVICE_MEMORY_BYTES, name
                assert device.memory_used_bytes == expected_used, name
                assert (
                    device.memory_free_bytes == DEVICE_MEMORY_BYTES - expected_used
                ), name
            if device.driver_version is not None:
                assert device.driver_version == DRIVER_VERSION, name
            if device.maca_version is not None:
                assert device.maca_version == MACA_VERSION, name


@pytest.mark.parametrize(
    ("builder", "device_count"),
    ((frame_thirty_two_mixed, 32), (frame_sixty_four_mixed, 64)),
)
def test_large_fleet_fixtures_have_unique_device_identities_and_high_index_process(
    builder,
    device_count: int,
) -> None:
    frame = builder()

    assert [device.index for device in frame.devices] == list(range(device_count))
    assert len({device.bdf for device in frame.devices}) == device_count
    assert len({device.uuid for device in frame.devices}) == device_count
    assert any(process.gpu_index == device_count - 1 for process in frame.processes)


def test_process_host_memory_matches_the_128_gib_fixture() -> None:
    for name, builder in FRAME_BUILDERS.items():
        for process in builder().processes:
            if process.memory_util_percent is None:
                assert process.host_memory_bytes is None, name
                continue
            expected = round(process.memory_util_percent / 100.0 * HOST_MEMORY_BYTES)
            assert process.host_memory_bytes == expected, (name, process.pid)


def test_primary_fixture_covers_all_process_type_states() -> None:
    process_types = {process.process_type for process in frame_three_gpu().processes}
    assert process_types == {"C", "G", "C+G", None}


def test_gallery_process_type_variants_exercise_distinct_filters() -> None:
    gallery = _with_pillow("render_gallery")
    frame = frame_three_gpu()
    variants = {variant.slug: variant for variant in gallery.VARIANTS}
    expected = {
        "once-compute": [423901, 512377],
        "once-only-compute": [423901],
        "once-graphics": [423908, 512377],
        "once-only-graphics": [423908],
    }
    for slug, pids in expected.items():
        filtered = gallery._filtered(frame, variants[slug])
        assert [process.pid for process in filtered.processes] == pids


def test_preview_text_is_repeatable_and_uses_fixed_metadata() -> None:
    previews = _with_pillow("generate_preview")
    first = previews.render_preview_text("small", width=120)
    second = previews.render_preview_text("small", width=120)
    assert first == second
    plain = previews.ANSI_PATTERN.sub("", first)
    assert "Sat Jan 17 12:34:56 2026" in plain
    assert "SUPERUSER LOGGED-IN" not in plain
    assert f"Driver Version: {DRIVER_VERSION}" in plain
    assert f"MACA Version: {MACA_VERSION}" in plain
    assert f"UPTIME: {HOST_UPTIME_TEXT}" in plain


def _assert_box_rows_fill_width(output: str, width: int, ansi_pattern) -> None:
    plain_lines = [ansi_pattern.sub("", line) for line in output.splitlines()]
    box_rows = [
        line for line in plain_lines if line and line[0] in frozenset("╒╞╘├│┌└╔╚")
    ]
    assert box_rows
    assert all(cell_width(line) == width for line in box_rows), [
        (cell_width(line), line) for line in box_rows if cell_width(line) != width
    ]


def test_canonical_dashboard_images_use_consistent_box_widths() -> None:
    previews = _with_pillow("generate_preview")
    gallery = importlib.import_module("render_gallery")
    showcase = importlib.import_module("render_showcase")

    for spec in previews.PREVIEW_SPECS:
        output = previews.render_preview_text(
            spec.scenario,
            width=spec.width,
            height=spec.height,
            theme=spec.theme,
        )
        _assert_box_rows_fill_width(output, spec.width, previews.ANSI_PATTERN)

    for variant in gallery.VARIANTS:
        if variant.kind != "json":
            _assert_box_rows_fill_width(
                gallery.render_variant_text(variant),
                variant.width,
                previews.ANSI_PATTERN,
            )

    for spec in showcase.SHOWCASE_SPECS:
        if spec.kind in {"tui", "once", "signal"}:
            _assert_box_rows_fill_width(
                showcase.render_showcase_text(spec),
                spec.width,
                previews.ANSI_PATTERN,
            )


def test_clipped_help_preview_does_not_color_sort_row_as_footer() -> None:
    showcase = _with_pillow("render_showcase")
    lines = showcase.render_help_screen(118, 30).lines

    colored = showcase._colorize_help(lines).splitlines()

    assert lines[-1].lstrip().startswith(", .:")
    assert not colored[-1].startswith(showcase.ansi.BOLD + showcase.ansi.FG_CYAN)
    assert showcase.ansi.FG_MAGENTA in colored[-1]


def test_png_renderer_discovers_portable_fonts_and_embeds_freshness(
    tmp_path: Path,
) -> None:
    previews = _with_pillow("generate_preview")
    output = "\x1b[1;36m╒═ mxtop ═╕\x1b[0m\n│ braille: ⣿⡇ │\n╘═══════════╛"
    target = tmp_path / "preview.png"
    previews.render_to_png(output, "dark", target, source_name="test:portable-font")
    assert previews.asset_is_fresh(target, output, "dark")
    with previews.Image.open(target) as image:
        assert image.width > 20
        assert image.height > 20
        assert image.getbbox() is not None
        assert image.info[previews.FONT_KEY]
        assert (
            image.info[previews.PILLOW_VERSION_KEY] == previews.CANONICAL_PILLOW_VERSION
        )
        assert (
            image.info[previews.FREETYPE_VERSION_KEY]
            == previews.rasterizer_versions()["freetype"]
        )
        assert image.info[previews.PIXEL_HASH_KEY] == previews.pixel_digest(image)
        assert image.info[previews.RENDER_CONFIG_KEY]


def test_png_renderer_keeps_ansi_segments_on_the_same_pixel_grid(
    tmp_path: Path,
) -> None:
    previews = _with_pillow("generate_preview")
    plain = "╒" + "═" * 118 + "╕"
    segmented = "".join(f"\x1b[37m{character}\x1b[0m" for character in plain)
    target = tmp_path / "cell-grid.png"

    previews.render_to_png(f"{plain}\n{segmented}", "dark", target)

    with previews.Image.open(target) as image:
        rgb = image.convert("RGB")
        background = previews.THEMES["dark"]["bg"]
        line_height = image.height // 4

        def right_edge(row: int) -> int:
            top = line_height * (row + 1)
            return max(
                x
                for y in range(top, top + line_height)
                for x in range(image.width)
                if rgb.getpixel((x, y)) != background
            )

        assert right_edge(0) == right_edge(1)


def test_png_renderer_shapes_combining_marks_with_their_base_cell(
    tmp_path: Path,
) -> None:
    previews = _with_pillow("generate_preview")
    image_chops = pytest.importorskip("PIL.ImageChops")
    lines = ("é", "e\u0301", "e\x1b[37m\u0301\x1b[0m")
    target = tmp_path / "combining-mark.png"

    previews.render_to_png("\n".join(lines), "dark", target)

    with previews.Image.open(target) as image:
        line_height = image.height // (len(lines) + 2)
        rendered_lines = [
            image.crop(
                (
                    0,
                    line_height * (row + 1),
                    image.width,
                    line_height * (row + 2),
                )
            ).convert("RGB")
            for row in range(len(lines))
        ]
        assert all(
            image_chops.difference(rendered_lines[0], rendered).getbbox() is None
            for rendered in rendered_lines[1:]
        )


def test_renderer_source_digest_ignores_platform_line_endings(tmp_path: Path) -> None:
    previews = _with_pillow("generate_preview")
    unix = tmp_path / "unix.py"
    windows = tmp_path / "windows.py"
    legacy = tmp_path / "legacy.py"
    unix.write_bytes(b"first\nsecond\n")
    windows.write_bytes(b"first\r\nsecond\r\n")
    legacy.write_bytes(b"first\rsecond\r")

    expected = previews.portable_source_digest(unix)
    assert previews.portable_source_digest(windows) == expected
    assert previews.portable_source_digest(legacy) == expected


def test_png_renderer_sizes_wide_text_by_terminal_cells(tmp_path: Path) -> None:
    previews = _with_pillow("generate_preview")
    output = "│界│"
    target = tmp_path / "wide-cell.png"

    previews.render_to_png(output, "dark", target)

    regular = previews.discover_font("regular")
    font = previews._load_font(regular, 18)
    pixel_cell_width = max(1, round(float(font.getlength("M"))))
    with previews.Image.open(target) as image:
        assert image.width == pixel_cell_width * (cell_width(output) + 2)


def test_png_renderer_keeps_plain_spaces_transparent(tmp_path: Path) -> None:
    previews = _with_pillow("generate_preview")
    target = tmp_path / "spaces.png"

    previews.render_to_png("   ", "dark", target)

    with previews.Image.open(target) as image:
        assert set(image.getdata()) == {previews.THEMES["dark"]["bg"]}


def test_canonical_fonts_are_vendored_hashed_and_ignore_host_configuration(
    monkeypatch,
    tmp_path: Path,
) -> None:
    previews = _with_pillow("generate_preview")
    assert previews.PIL.__version__ == previews.CANONICAL_PILLOW_VERSION
    assert previews.rasterizer_versions()["freetype"] != "unknown"
    expected = {
        "LiberationMono-Regular.ttf": "395fa5ab8d40c8eba390ced528744ea75a7f69aabf3e68b6f925ca0e39a27370",
        "LiberationMono-Bold.ttf": "626655e94dd82f3f42549daf995c921b0915fa8ab1f4b839559e8892ea41d240",
    }
    for name, digest in expected.items():
        path = previews.FONT_DIR / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    assert "SIL OPEN FONT LICENSE Version 1.1" in previews.FONT_LICENSE.read_text(
        encoding="utf-8"
    )

    monkeypatch.setenv("MXTOP_PREVIEW_FONT", str(previews.CANONICAL_BOLD_FONT))
    monkeypatch.setenv("MXTOP_PREVIEW_BOLD_FONT", str(previews.CANONICAL_REGULAR_FONT))
    monkeypatch.setenv("MXTOP_PREVIEW_SYMBOL_FONT", str(previews.CANONICAL_BOLD_FONT))
    regular = previews.discover_font("regular")
    bold = previews.discover_font("bold")
    assert (
        regular is not None
        and regular.label == "assets/fonts/LiberationMono-Regular.ttf:0"
    )
    assert bold is not None and bold.label == "assets/fonts/LiberationMono-Bold.ttf:0"

    output = "canonical braille: ⣿⡇"
    target = tmp_path / "canonical.png"
    previews.render_to_png(output, "dark", target)
    assert previews.asset_is_fresh(target, output, "dark")

    pillow_freetype_version = previews.features.version
    monkeypatch.setattr(previews.features, "version", lambda feature: "0.0-test")
    assert not previews.asset_is_fresh(target, output, "dark")
    monkeypatch.setattr(previews.features, "version", pillow_freetype_version)

    monkeypatch.setattr(previews.PIL, "__version__", "0.0-test")
    assert not previews.asset_is_fresh(target, output, "dark")
    with pytest.raises(
        RuntimeError, match="canonical preview rendering requires Pillow"
    ):
        previews.render_to_png(output, "dark", tmp_path / "wrong-pillow.png")


def test_explicit_noncanonical_font_output_remains_available(
    monkeypatch, tmp_path: Path
) -> None:
    previews = _with_pillow("generate_preview")
    monkeypatch.setenv("MXTOP_PREVIEW_FONT", str(previews.CANONICAL_BOLD_FONT))
    custom = previews.discover_font("regular", canonical=False)
    assert custom is not None and custom.path == previews.CANONICAL_BOLD_FONT

    target = tmp_path / "custom.png"
    previews.render_to_png("custom", "dark", target, canonical_fonts=False)
    with previews.Image.open(target) as image:
        assert image.info[previews.FONT_KEY] == "assets/fonts/LiberationMono-Bold.ttf:0"
    assert not previews.asset_is_fresh(target, "custom", "dark")


def test_gallery_and_showcase_markdown_are_generated() -> None:
    _with_pillow("generate_preview")
    gallery = importlib.import_module("render_gallery")
    showcase = importlib.import_module("render_showcase")
    assert (PROJECT_ROOT / "GALLERY.md").read_text(
        encoding="utf-8"
    ) == gallery.gallery_markdown()
    assert (PROJECT_ROOT / "SHOWCASE.md").read_text(
        encoding="utf-8"
    ) == showcase.showcase_markdown()


def test_every_unique_fixture_and_secondary_screen_has_a_canonical_asset() -> None:
    previews = _with_pillow("generate_preview")
    gallery = importlib.import_module("render_gallery")
    showcase = importlib.import_module("render_showcase")
    covered_names = {
        *(spec.scenario for spec in previews.PREVIEW_SPECS),
        *(variant.frame_name for variant in gallery.VARIANTS),
        *(spec.scenario for spec in showcase.SHOWCASE_SPECS),
    }
    covered_builders = {id(FRAME_BUILDERS[name]) for name in covered_names}
    assert covered_builders == {id(builder) for builder in FRAME_BUILDERS.values()}
    assert showcase.SECONDARY_KINDS == {
        "help",
        "environment",
        "environment-error",
        "tree",
        "tree-empty",
        "metrics",
        "signal",
    }
    assert showcase.SECONDARY_KINDS <= {spec.kind for spec in showcase.SHOWCASE_SPECS}


def test_nonfinite_gallery_fixture_is_strict_json() -> None:
    gallery = _with_pillow("render_gallery")
    variant = next(item for item in gallery.VARIANTS if item.slug == "json-nonfinite")
    output = gallery.render_variant_text(variant)
    payload = json.loads(output)
    assert "NaN" not in output and "Infinity" not in output
    assert payload["devices"][0]["gpu_util_percent"] is None
    assert payload["processes"][0]["gpu_util_percent"] is None


def test_package_readme_uses_release_portable_origin_urls() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    image_urls = re.findall(r'<img[^>]+src="([^"]+)"', readme)
    assert image_urls
    assert all(url.startswith("https://") for url in image_urls)
    assert 'src="assets/' not in readme
    assert "](GALLERY.md)" not in readme
    assert "](SHOWCASE.md)" not in readme
    assert "](INTRO.md)" not in readme
    assert (
        "https://raw.githubusercontent.com/linkedlist771/metax-mxtop/main/assets/"
        in readme
    )

    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "[project.urls]" in pyproject
    assert (
        'Repository = "https://github.com/linkedlist771/metax-mxtop.git"' in pyproject
    )


def test_canonical_commands_pin_the_pillow_version() -> None:
    previews = _with_pillow("generate_preview")
    expected = f"--with pillow=={previews.CANONICAL_PILLOW_VERSION}"
    for relative_path in ("README.md", "INTRO.md", "GALLERY.md", "SHOWCASE.md"):
        text = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
        pillow_arguments = re.findall(r"--with pillow(?:==[^\s`]+)?", text)
        assert pillow_arguments, relative_path
        assert set(pillow_arguments) == {expected}, relative_path

    workflow = (PROJECT_ROOT / ".github/workflows/wheels.yml").read_text(
        encoding="utf-8"
    )
    assert f"pillow=={previews.CANONICAL_PILLOW_VERSION}" in workflow
    assert not re.search(r"\bpillow\b(?!==)", workflow, flags=re.IGNORECASE)


def test_committed_preview_assets_are_fresh() -> None:
    previews = _with_pillow("generate_preview")
    stale = []
    for spec in previews.PREVIEW_SPECS:
        output = previews.render_preview_text(
            spec.scenario,
            width=spec.width,
            height=spec.height,
            theme=spec.theme,
        )
        if not previews.asset_is_fresh(
            PROJECT_ROOT / spec.target,
            output,
            spec.theme,
            font_size=spec.font_size,
        ):
            stale.append(spec.target)
    assert stale == []


def test_readme_primary_preview_uses_native_display_resolution() -> None:
    previews = _with_pillow("generate_preview")
    spec = next(
        item for item in previews.PREVIEW_SPECS if item.target == "assets/mxtop-preview.png"
    )

    assert spec.font_size == 12
    with previews.Image.open(PROJECT_ROOT / spec.target) as image:
        assert 900 <= image.width <= 1024
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        assert (
            f'mxtop-preview.png?v=readme-native-1" '
            f'alt="mxtop terminal preview" width="{image.width}"'
            in readme
        )


def test_committed_gallery_assets_are_fresh_and_complete() -> None:
    _with_pillow("generate_preview")
    gallery = importlib.import_module("render_gallery")
    expected = {f"{variant.slug}.png" for variant in gallery.VARIANTS}
    actual = {path.name for path in gallery.GALLERY_DIR.glob("*.png")}
    assert actual == expected
    assert gallery.render_all(check=True) == []


def test_committed_showcase_assets_are_fresh_and_complete() -> None:
    _with_pillow("generate_preview")
    showcase = importlib.import_module("render_showcase")
    expected = {f"{spec.slug}.png" for spec in showcase.SHOWCASE_SPECS}
    actual = {path.name for path in showcase.SHOWCASE_DIR.glob("*.png")}
    assert actual == expected
    assert showcase.render_all(check=True) == []


def test_all_committed_pngs_have_real_pixel_variation() -> None:
    previews = _with_pillow("generate_preview")
    for path in sorted((PROJECT_ROOT / "assets").rglob("*.png")):
        with previews.Image.open(path) as image:
            assert image.width > 0 and image.height > 0, path
            assert any(
                low != high for low, high in image.convert("RGB").getextrema()
            ), path
