import pytest


@pytest.fixture(autouse=True)
def isolated_mxtop_config(monkeypatch, tmp_path):
    """Keep tests independent from any real user config file."""

    monkeypatch.setenv("MXTOP_CONFIG", str(tmp_path / "mxtop-test-config.toml"))


@pytest.fixture(autouse=True)
def isolated_terminal_environment(monkeypatch):
    """Keep color decisions independent from the developer's own terminal."""

    for name in ("COLORTERM", "NO_COLOR", "FORCE_COLOR"):
        monkeypatch.delenv(name, raising=False)
