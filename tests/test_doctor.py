"""Tests for mxtop --doctor environment diagnostics."""

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
