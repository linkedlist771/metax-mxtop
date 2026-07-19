import pytest

from mxtop.cli import main
from mxtop.config import config_path, load_config
from mxtop.models import DeviceSnapshot, FrameSnapshot


class StaticBackend:
    name = "static"

    def snapshot(self):
        return FrameSnapshot(
            devices=[DeviceSnapshot(index=0, name="MXC500")],
            processes=[],
        )


def test_config_path_prefers_explicit_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MXTOP_CONFIG", str(tmp_path / "custom.toml"))
    assert config_path() == tmp_path / "custom.toml"


def test_config_path_uses_xdg_config_home(monkeypatch, tmp_path):
    monkeypatch.delenv("MXTOP_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert config_path() == tmp_path / "mxtop" / "config.toml"


def test_load_config_missing_file_is_empty(tmp_path):
    assert load_config(tmp_path / "absent.toml") == {}


def test_load_config_reads_and_normalizes_keys(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
interval = 1.5
monitor = "compact"
colorful = true
light = true
readonly = true
no-unicode = true
gpu-util-thresh = [20, 70]
mem-util-thresh = [15, 85]

[remote]
bind = "0.0.0.0"
port = 9000
auth-token = "s3cret"
mxsmi-path = "/opt/bin/mx-smi"
open = true
"""
    )
    config = load_config(path)
    assert config == {
        "interval": 1.5,
        "monitor": "compact",
        "colorful": True,
        "light": True,
        "readonly": True,
        "no_unicode": True,
        "gpu_util_thresh": (20, 70),
        "mem_util_thresh": (15, 85),
        "remote_bind": "0.0.0.0",
        "remote_port": 9000,
        "remote_auth_token": "s3cret",
        "remote_mxsmi_path": "/opt/bin/mx-smi",
        "remote_open": True,
    }


def test_load_config_warns_on_unknown_and_invalid_keys(tmp_path, capsys):
    path = tmp_path / "config.toml"
    path.write_text(
        """
colour = true
interval = 0.1
monitor = "huge"
gpu-util-thresh = [5]
[remote]
timeout = 3
"""
    )
    config = load_config(path)
    err = capsys.readouterr().err
    assert config == {}
    assert "unknown key colour" in err
    assert "interval must be at least 0.25" in err
    assert "monitor should be one of" in err
    assert "gpu-util-thresh should be a list of two integers" in err
    assert "unknown key remote.timeout" in err


def test_load_config_warns_on_parse_error(tmp_path, capsys):
    path = tmp_path / "config.toml"
    path.write_text("interval = [not toml")
    assert load_config(path) == {}
    assert "cannot parse" in capsys.readouterr().err


def test_load_config_rejects_wrong_types(tmp_path, capsys):
    path = tmp_path / "config.toml"
    path.write_text('interval = "fast"\ncolorful = "yes"\n')
    assert load_config(path) == {}
    err = capsys.readouterr().err
    assert "interval should be float" in err
    assert "colorful should be bool" in err


@pytest.fixture
def config_file(monkeypatch, tmp_path):
    path = tmp_path / "config.toml"
    monkeypatch.setenv("MXTOP_CONFIG", str(path))
    return path


def test_cli_uses_config_interval_for_count(config_file, monkeypatch, capsys):
    config_file.write_text("interval = 0.5\n")
    sleeps: list[float] = []
    monkeypatch.setattr("mxtop.cli.time.sleep", lambda value: sleeps.append(value))

    rc = main(["--once", "--no-color", "-n", "2"], backend=StaticBackend())

    assert rc == 0
    assert sleeps == [0.5]
    assert capsys.readouterr().out.count("MXC500") == 2


def test_cli_flag_overrides_config_interval(config_file, monkeypatch, capsys):
    config_file.write_text("interval = 9.0\n")
    sleeps: list[float] = []
    monkeypatch.setattr("mxtop.cli.time.sleep", lambda value: sleeps.append(value))

    rc = main(
        ["--once", "--no-color", "-n", "2", "--interval", "0.25"],
        backend=StaticBackend(),
    )

    assert rc == 0
    assert sleeps == [0.25]
    capsys.readouterr()
