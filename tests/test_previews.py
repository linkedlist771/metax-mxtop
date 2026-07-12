from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys

import pytest

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


def test_known_devices_have_consistent_memory_and_metax_versions() -> None:
    for name, builder in FRAME_BUILDERS.items():
        for device in builder().devices:
            if device.memory_util_percent is not None:
                expected_used = round(device.memory_util_percent / 100.0 * DEVICE_MEMORY_BYTES)
                assert device.memory_total_bytes == DEVICE_MEMORY_BYTES, name
                assert device.memory_used_bytes == expected_used, name
                assert device.memory_free_bytes == DEVICE_MEMORY_BYTES - expected_used, name
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
    assert f"Driver Version: {DRIVER_VERSION}" in plain
    assert f"MACA Version: {MACA_VERSION}" in plain
    assert f"UPTIME: {HOST_UPTIME_TEXT}" in plain


def test_png_renderer_discovers_portable_fonts_and_embeds_freshness(tmp_path: Path) -> None:
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
        assert image.info[previews.RENDER_CONFIG_KEY]


def test_gallery_and_showcase_markdown_are_generated() -> None:
    _with_pillow("generate_preview")
    gallery = importlib.import_module("render_gallery")
    showcase = importlib.import_module("render_showcase")
    assert (PROJECT_ROOT / "GALLERY.md").read_text(encoding="utf-8") == gallery.gallery_markdown()
    assert (PROJECT_ROOT / "SHOWCASE.md").read_text(encoding="utf-8") == showcase.showcase_markdown()


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
        if not previews.asset_is_fresh(PROJECT_ROOT / spec.target, output, spec.theme):
            stale.append(spec.target)
    assert stale == []


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
