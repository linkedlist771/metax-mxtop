"""Versioned GitHub releases use the matching CHANGELOG section."""

from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from extract_release_notes import extract_release_notes  # noqa: E402


def test_extract_release_notes_stops_at_next_release():
    changelog = """# Changelog

## [1.2.3] - 2026-07-20

### Added

- New thing.

### Fixed

- Important fix.

## [1.2.2] - 2026-07-01

- Old thing.

[1.2.3]: https://example.test
"""
    notes = extract_release_notes("1.2.3", changelog)
    assert "### Added" in notes
    assert "Important fix" in notes
    assert "Old thing" not in notes
    assert "[1.2.3]:" not in notes
    assert notes.endswith("\n")


def test_extract_release_notes_accepts_heading_without_date():
    assert extract_release_notes("1.0.0", "## [1.0.0]\n\n- First.\n") == "- First.\n"


def test_extract_release_notes_rejects_missing_or_empty_release():
    for changelog, message in (
        ("# Changelog\n", "no release heading"),
        ("## [1.0.0]\n\n## [0.9.0]\n\n- Old.\n", "has no notes"),
    ):
        try:
            extract_release_notes("1.0.0", changelog)
        except ValueError as error:
            assert message in str(error)
        else:
            raise AssertionError("expected ValueError")
