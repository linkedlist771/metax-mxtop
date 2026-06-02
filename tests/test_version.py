import pathlib
import re

from mxtop import __version__


def test_runtime_version_is_valid_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)


def test_runtime_version_matches_pyproject():
    pyproject = pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml"
    match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject.read_text(), re.MULTILINE)
    assert match is not None
    assert __version__ == match.group(1)
