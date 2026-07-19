import pytest


@pytest.fixture(autouse=True)
def isolated_mxtop_config(monkeypatch, tmp_path):
    """Keep tests independent from any real user config file."""

    monkeypatch.setenv("MXTOP_CONFIG", str(tmp_path / "mxtop-test-config.toml"))
