"""Keep published package metadata aligned with the supported runtime."""

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.9/3.10
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())


def test_python_classifiers_cover_supported_ci_range():
    classifiers = set(PROJECT["project"]["classifiers"])
    for minor in range(9, 14):
        assert f"Programming Language :: Python :: 3.{minor}" in classifiers
    assert PROJECT["project"]["requires-python"] == ">=3.9"
    assert "Programming Language :: Python :: Implementation :: CPython" in classifiers


def test_package_metadata_links_community_documents():
    urls = PROJECT["project"]["urls"]
    assert urls["Changelog"].endswith("/CHANGELOG.md")
    assert urls["Contributing"].endswith("/CONTRIBUTING.md")
    assert urls["Security"].endswith("/SECURITY.md")
    assert urls["Issues"].endswith("/issues")


def test_pep639_license_expression_and_policy_files_are_present():
    assert PROJECT["project"]["license"] == "MIT"
    for name in ("LICENSE", "SECURITY.md", "CONTRIBUTING.md", "CHANGELOG.md"):
        assert (ROOT / name).is_file()
