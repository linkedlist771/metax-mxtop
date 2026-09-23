"""Release tags must match package and changelog metadata."""

from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from check_release_version import validate_release  # noqa: E402


def _release_tree(tmp_path: Path, version: str = "1.2.3") -> Path:
    root = tmp_path
    (root / "src" / "mxtop").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "example"\nversion = "{version}"\n'
    )
    (root / "src" / "mxtop" / "__init__.py").write_text(
        f'__version__ = "{version}"\n'
    )
    (root / "CHANGELOG.md").write_text(
        f"## [{version}] - 2026-07-20\n\n[{version}]: https://example.test/{version}\n"
    )
    return root


def test_release_metadata_accepts_consistent_tag(tmp_path):
    assert validate_release("v1.2.3", _release_tree(tmp_path)) == []


def test_release_metadata_rejects_invalid_tag(tmp_path):
    errors = validate_release("release-1.2.3", _release_tree(tmp_path))
    assert errors == [
        "release tag must be vMAJOR.MINOR.PATCH, got 'release-1.2.3'"
    ]


def test_release_metadata_reports_every_mismatch(tmp_path):
    root = _release_tree(tmp_path, "1.2.2")
    (root / "src" / "mxtop" / "__init__.py").write_text(
        '__version__ = "1.2.1"\n'
    )
    (root / "CHANGELOG.md").write_text("# Changelog\n")

    errors = validate_release("v1.2.3", root)

    assert any("pyproject.toml version 1.2.2" in error for error in errors)
    assert any("mxtop.__version__ 1.2.1" in error for error in errors)
    assert any("release heading" in error for error in errors)
    assert any("comparison link" in error for error in errors)


def test_release_metadata_reports_missing_runtime_version(tmp_path):
    root = _release_tree(tmp_path)
    (root / "src" / "mxtop" / "__init__.py").write_text("# missing\n")

    try:
        validate_release("v1.2.3", root)
    except ValueError as error:
        assert "no __version__ assignment" in str(error)
    else:
        raise AssertionError("expected ValueError")
