"""Tests for mxtop --doctor environment diagnostics."""

from types import SimpleNamespace

from mxtop import doctor
from mxtop.cli import main
from mxtop.doctor import FAIL, PASS, WARN, CheckResult, run_doctor
from mxtop.models import DeviceSnapshot, FrameSnapshot


def test_doctor_reports_and_exit_code_track_worst_status(monkeypatch, capsys):
    monkeypatch.setattr(
        doctor,
        "CHECKS",
        (
            lambda: CheckResult("Alpha", PASS, "fine"),
            lambda: CheckResult("Beta", WARN, "meh", "try harder"),
        ),
    )
    assert run_doctor(use_color=False) == 0
    out = capsys.readouterr().out
    assert "[PASS] Alpha: fine" in out
    assert "[WARN] Beta: meh" in out
    assert "hint: try harder" in out
    assert "functional with warnings" in out

    monkeypatch.setattr(
        doctor,
        "CHECKS",
        (lambda: CheckResult("Backend", FAIL, "broken", "fix it"),),
    )
    assert run_doctor(use_color=False) == 1
    assert "no working telemetry backend" in capsys.readouterr().out


def test_doctor_survives_crashing_check(monkeypatch, capsys):
    def _check_boom():
        raise RuntimeError("kaput")

    monkeypatch.setattr(doctor, "CHECKS", (_check_boom,))
    assert run_doctor(use_color=False) == 0
    out = capsys.readouterr().out
    assert "check crashed: kaput" in out


def test_backend_check_pass_warn_fail(monkeypatch):
    class Backend:
        name = "static"

        def __init__(self, devices):
            self._devices = devices

        def snapshot(self):
            return FrameSnapshot(devices=self._devices, processes=[])

    monkeypatch.setattr(
        "mxtop.backends.create_backend",
        lambda name="auto": Backend([DeviceSnapshot(index=0)]),
    )
    assert doctor._check_backend().status == PASS

    monkeypatch.setattr(
        "mxtop.backends.create_backend", lambda name="auto": Backend([])
    )
    result = doctor._check_backend()
    assert result.status == WARN
    assert "no GPUs" in (result.hint or "")

    def _raise(name="auto"):
        raise RuntimeError("no backend")

    monkeypatch.setattr("mxtop.backends.create_backend", _raise)
    assert doctor._check_backend().status == FAIL


def test_terminal_check_flags_dumb_term(monkeypatch):
    monkeypatch.setenv("TERM", "dumb")
    assert doctor._check_terminal().status == WARN
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setattr(doctor.locale, "getpreferredencoding", lambda _=False: "UTF-8")
    assert doctor._check_terminal().status == PASS


def test_config_check_reports_invalid_keys(monkeypatch, tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("bogus = true\n")
    monkeypatch.setenv("MXTOP_CONFIG", str(path))
    result = doctor._check_config()
    assert result.status == WARN
    assert "bogus" in result.detail

    path.write_text("interval = 1.5\n")
    assert doctor._check_config().status == PASS


def test_cli_doctor_flag_runs_and_propagates_exit(monkeypatch, capsys):
    monkeypatch.setattr(
        doctor, "CHECKS", (lambda: CheckResult("Backend", FAIL, "broken"),)
    )
    rc = main(["--doctor", "--no-color"])
    assert rc == 1
    assert "mxtop" in capsys.readouterr().out


def test_python_and_psutil_checks(monkeypatch):
    assert doctor._check_python().status == PASS

    monkeypatch.setattr(
        doctor.importlib,
        "import_module",
        lambda name: SimpleNamespace(__version__="9.9")
        if name == "psutil"
        else None,
    )
    result = doctor._check_psutil()
    assert result.status == PASS
    assert result.detail == "9.9"

    def missing(_name):
        raise ModuleNotFoundError

    monkeypatch.setattr(doctor.importlib, "import_module", missing)
    result = doctor._check_psutil()
    assert result.status == WARN
    assert "pip install psutil" in (result.hint or "")


def test_pymxsml_check_installed_sdk_wheel_and_missing(monkeypatch):
    monkeypatch.setattr(
        doctor.importlib,
        "import_module",
        lambda name: SimpleNamespace(__file__="/sdk/pymxsml.py"),
    )
    result = doctor._check_pymxsml()
    assert result.status == PASS
    assert result.detail == "/sdk/pymxsml.py"

    def missing(_name):
        raise ModuleNotFoundError

    monkeypatch.setattr(doctor.importlib, "import_module", missing)
    monkeypatch.setattr(
        doctor,
        "glob",
        lambda pattern: ["/opt/maca/share/mxsml/pymxsml-2.whl"]
        if "/opt/maca/" in pattern
        else [],
    )
    result = doctor._check_pymxsml()
    assert result.status == WARN
    assert "pymxsml-2.whl" in result.detail

    monkeypatch.setattr(doctor, "glob", lambda _pattern: [])
    result = doctor._check_pymxsml()
    assert result.status == WARN
    assert "no SDK wheel" in result.detail


def test_mxsmi_check_reports_env_path_path_lookup_and_missing(monkeypatch, tmp_path):
    executable = tmp_path / "mx-smi"
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o755)
    monkeypatch.setenv("MXTOP_MXSMI_PATH", str(executable))
    result = doctor._check_mxsmi()
    assert result.status == PASS
    assert "environment variable" in result.detail

    monkeypatch.setenv("MXTOP_MXSMI_PATH", "mx-smi-missing")
    monkeypatch.setattr(
        doctor.shutil,
        "which",
        lambda value: "/usr/bin/mx-smi" if value == "mx-smi-missing" else None,
    )
    result = doctor._check_mxsmi()
    assert result.status == PASS

    monkeypatch.setattr(doctor.shutil, "which", lambda _value: None)
    result = doctor._check_mxsmi()
    assert result.status == WARN
    assert "MXTOP_MXSMI_PATH" in (result.hint or "")


def test_remote_check_missing_and_installed(monkeypatch, tmp_path):
    monkeypatch.setattr(doctor.Path, "home", lambda: tmp_path)

    def missing(_name):
        raise ModuleNotFoundError

    monkeypatch.setattr(doctor.importlib, "import_module", missing)
    result = doctor._check_remote()
    assert result.status == WARN
    assert "[remote]" in (result.hint or "")

    config = tmp_path / ".ssh" / "config"
    config.parent.mkdir()
    config.write_text("Host gpu\n")
    monkeypatch.setattr(
        doctor.importlib,
        "import_module",
        lambda name: SimpleNamespace(__version__="2.23.0"),
    )
    result = doctor._check_remote()
    assert result.status == PASS
    assert "2.23.0" in result.detail
    assert "config present" in result.detail


def test_doctor_all_pass_and_ansi_color(monkeypatch, capsys):
    monkeypatch.setattr(
        doctor,
        "CHECKS",
        (lambda: CheckResult("Backend", PASS, "one GPU"),),
    )
    assert run_doctor(use_color=True) == 0
    output = capsys.readouterr().out
    assert "\x1b[32mPASS\x1b[0m" in output
    assert "everything looks good" in output
