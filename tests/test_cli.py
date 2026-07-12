import json

import pytest

from mxtop import rendering
from mxtop import cli
from mxtop.cli import build_parser, main
from mxtop.models import DeviceSnapshot, FrameSnapshot, ProcessSnapshot


class StaticBackend:
    name = "static"

    def snapshot(self):
        return FrameSnapshot(
            devices=[
                DeviceSnapshot(
                    index=0,
                    name="MXC500",
                    bdf="0000:08:00.0",
                    gpu_util_percent=12,
                    memory_used_bytes=1024,
                    memory_total_bytes=2048,
                )
            ],
            processes=[],
        )


def test_cli_once_prints_text(capsys):
    rc = main(["--once", "--no-color"], backend=StaticBackend())

    captured = capsys.readouterr()
    assert rc == 0
    assert "MXTOP" in captured.out
    assert "MXC500" in captured.out


def test_cli_json_prints_frame(capsys):
    rc = main(["--json"], backend=StaticBackend())

    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert rc == 0
    assert payload["devices"][0]["name"] == "MXC500"


def test_cli_json_replaces_non_finite_telemetry_with_null(capsys):
    class NonFiniteBackend:
        name = "non-finite"

        def snapshot(self):
            return FrameSnapshot(
                devices=[DeviceSnapshot(index=0, gpu_util_percent=float("nan"))],
                processes=[ProcessSnapshot(gpu_index=0, pid=1, cpu_percent=float("inf"))],
            )

    rc = main(["--json"], backend=NonFiniteBackend())
    output = capsys.readouterr().out
    payload = json.loads(
        output,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
    )

    assert rc == 0
    assert payload["devices"][0]["gpu_util_percent"] is None
    assert payload["processes"][0]["cpu_percent"] is None


def test_cli_reports_snapshot_errors_without_traceback(capsys):
    class BrokenBackend:
        name = "broken"

        def snapshot(self):
            raise RuntimeError("telemetry unavailable")

    rc = main(["--once"], backend=BrokenBackend())

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert "MXTOP ERROR: telemetry unavailable" in captured.err


def test_cli_once_resamples_process_cpu_when_first_snapshot_is_unknown(monkeypatch, capsys):
    class TwoFrameBackend:
        name = "two-frame"

        def __init__(self):
            self.calls = 0

        def snapshot(self):
            self.calls += 1
            return FrameSnapshot(
                devices=[],
                processes=[
                    ProcessSnapshot(
                        gpu_index=0,
                        pid=123,
                        cpu_percent=None if self.calls == 1 else 50.0,
                        command="python train.py",
                    )
                ],
            )

    backend = TwoFrameBackend()
    monkeypatch.setattr("mxtop.cli.time.sleep", lambda _: None)

    rc = main(["--once", "--no-color"], backend=backend)

    captured = capsys.readouterr()
    assert rc == 0
    assert backend.calls == 2
    assert "%CPU" in captured.out
    assert "  50 " in captured.out


def test_cli_json_resamples_process_cpu_when_first_snapshot_is_unknown(monkeypatch, capsys):
    class TwoFrameBackend:
        name = "two-frame"

        def __init__(self):
            self.calls = 0

        def snapshot(self):
            self.calls += 1
            return FrameSnapshot(
                devices=[],
                processes=[
                    ProcessSnapshot(
                        gpu_index=0,
                        pid=123,
                        cpu_percent=None if self.calls == 1 else 50.0,
                        command="python train.py",
                    )
                ],
            )

    backend = TwoFrameBackend()
    monkeypatch.setattr("mxtop.cli.time.sleep", lambda _: None)

    rc = main(["--json"], backend=backend)

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 0
    assert backend.calls == 2
    assert payload["processes"][0]["cpu_percent"] == 50.0


def test_cli_filters_json_output(capsys):
    class FilterBackend:
        name = "filter"

        def snapshot(self):
            return FrameSnapshot(
                devices=[DeviceSnapshot(index=0), DeviceSnapshot(index=1)],
                processes=[
                    ProcessSnapshot(gpu_index=0, pid=10, user="alice"),
                    ProcessSnapshot(gpu_index=1, pid=11, user="bob"),
                ],
            )

    rc = main(["--json", "--only", "1", "--user", "bob"], backend=FilterBackend())

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert [device["index"] for device in payload["devices"]] == [1]
    assert [process["pid"] for process in payload["processes"]] == [11]


def test_cli_rejects_too_small_interval(capsys):
    rc = None
    try:
        rc = main(["--interval", "0.1", "--once"], backend=StaticBackend())
    except SystemExit as exc:
        rc = exc.code

    assert rc == 2
    assert "interval must be at least" in capsys.readouterr().err


def test_cli_applies_threshold_flags_to_rendering(capsys):
    try:
        rc = main(
            ["--once", "--no-color", "--gpu-util-thresh", "5", "70", "--mem-util-thresh", "5", "70"],
            backend=StaticBackend(),
        )
        assert rc == 0
        assert capsys.readouterr().out
        assert rendering.GPU_THRESHOLDS == (5, 70)
        assert rendering.MEM_THRESHOLDS == (5, 70)
    finally:
        rendering.reset_intensity_thresholds()


def test_cli_reads_mxtop_threshold_env(monkeypatch, capsys):
    monkeypatch.setenv("MXTOP_GPU_UTILIZATION_THRESHOLDS", "20,77")
    monkeypatch.setenv("MXTOP_MEMORY_UTILIZATION_THRESHOLDS", "15,82")
    try:
        rc = main(["--once", "--no-color"], backend=StaticBackend())
        assert rc == 0
        assert capsys.readouterr().out
        assert rendering.GPU_THRESHOLDS == (20, 77)
        assert rendering.MEM_THRESHOLDS == (15, 82)
    finally:
        rendering.reset_intensity_thresholds()


def test_cli_accepts_style_flags(capsys):
    try:
        rc = main(["--once", "--colorful", "--light"], backend=StaticBackend())
        assert rc == 0
        assert capsys.readouterr().out
        assert rendering.LIGHT_THEME is True
        assert rendering.COLORFUL_MODE is True
    finally:
        rendering.set_render_style(light=False, colorful=False)


def test_cli_force_color_emits_ansi_when_piped(capsys, monkeypatch):
    monkeypatch.setattr("mxtop.cli.sys.stdout.isatty", lambda: False)
    rc = main(["--once", "--force-color"], backend=StaticBackend())
    out = capsys.readouterr().out
    assert rc == 0
    assert "\x1b[" in out


def test_cli_empty_force_color_environment_value_still_forces_color(capsys, monkeypatch):
    monkeypatch.delenv("ANSI_COLORS_DISABLED", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "")
    monkeypatch.setattr("mxtop.cli.sys.stdout.isatty", lambda: False)

    rc = main(["--once"], backend=StaticBackend())

    assert rc == 0
    assert "\x1b[" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("argv", "environment", "tty", "expected"),
    [
        (("--no-color", "--force-color"), {}, False, False),
        ((), {"NO_COLOR": "", "FORCE_COLOR": ""}, False, False),
        ((), {"ANSI_COLORS_DISABLED": "", "FORCE_COLOR": "1"}, False, False),
        ((), {"FORCE_COLOR": ""}, False, True),
        ((), {"TERM": "dumb"}, True, False),
        (("--force-color",), {"TERM": "dumb", "NO_COLOR": ""}, False, True),
    ],
)
def test_cli_color_precedence_matrix(monkeypatch, argv, environment, tty, expected):
    for name in ("ANSI_COLORS_DISABLED", "NO_COLOR", "FORCE_COLOR", "TERM"):
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    args = build_parser().parse_args(["--once", *argv])

    assert cli._should_use_color(
        no_color=args.no_color,
        force_color=args.force_color,
        stdout_is_tty=tty,
    ) is expected


def test_cli_plain_output_is_default_when_piped(capsys, monkeypatch):
    monkeypatch.setattr("mxtop.cli.sys.stdout.isatty", lambda: False)

    rc = main(["--once"], backend=StaticBackend())

    assert rc == 0
    assert "\x1b[" not in capsys.readouterr().out


def test_cli_accepts_nvitop_short_aliases():
    args = build_parser().parse_args(
        ["-m", "compact", "-U", "-o", "0", "2", "-u", "alice", "-p", "10", "-c", "-G"]
    )

    assert args.monitor == "compact"
    assert args.no_unicode
    assert args.only == [0, 2]
    assert args.user == ["alice"]
    assert args.pid == [10]
    assert args.compute and args.only_graphics


def test_cli_once_and_monitor_are_mutually_exclusive(capsys):
    try:
        build_parser().parse_args(["--once", "--monitor"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("parser accepted mutually exclusive output modes")
    assert "not allowed with argument" in capsys.readouterr().err


def test_cli_json_and_once_are_mutually_exclusive(capsys):
    try:
        build_parser().parse_args(["--json", "--once"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("parser accepted mutually exclusive output modes")
    assert "not allowed with argument" in capsys.readouterr().err


def test_cli_rejects_non_finite_intervals(capsys):
    for value in ("nan", "inf", "-inf"):
        try:
            build_parser().parse_args(["--once", f"--interval={value}"])
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError(f"parser accepted interval {value}")
        assert "interval must be at least" in capsys.readouterr().err


def test_cli_user_without_values_selects_current_user(monkeypatch, capsys):
    class UserBackend:
        name = "users"

        def snapshot(self):
            return FrameSnapshot(
                devices=[],
                processes=[
                    ProcessSnapshot(gpu_index=0, pid=10, user="alice"),
                    ProcessSnapshot(gpu_index=0, pid=11, user="bob"),
                ],
            )

    monkeypatch.setattr(cli.getpass, "getuser", lambda: "alice")

    rc = main(["--json", "--user"], backend=UserBackend())

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert [process["pid"] for process in payload["processes"]] == [10]


def test_cli_only_visible_uses_maca_visible_devices(monkeypatch, capsys):
    class DeviceBackend:
        name = "devices"

        def snapshot(self):
            return FrameSnapshot(
                devices=[DeviceSnapshot(index=0), DeviceSnapshot(index=1)],
                processes=[
                    ProcessSnapshot(gpu_index=0, pid=10),
                    ProcessSnapshot(gpu_index=1, pid=11),
                ],
            )

    monkeypatch.setenv("MACA_VISIBLE_DEVICES", "1")

    rc = main(["--json", "--only-visible"], backend=DeviceBackend())

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert [device["index"] for device in payload["devices"]] == [1]
    assert [process["pid"] for process in payload["processes"]] == [11]


def test_cli_reports_invalid_device_indices_but_keeps_valid_output(capsys):
    rc = main(["--once", "--no-color", "--only", "0", "9"], backend=StaticBackend())

    captured = capsys.readouterr()
    assert rc == 1
    assert "MXC500" in captured.out
    assert "Invalid device index: 9" in captured.err


def test_cli_monitor_uses_environment_mode_and_readonly(monkeypatch):
    captured = {}
    monkeypatch.setenv("MXTOP_MONITOR_MODE", "compact,readonly")
    monkeypatch.setattr("mxtop.cli.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("mxtop.cli.sys.stdout.isatty", lambda: True)
    monkeypatch.setattr(
        cli,
        "run_tui",
        lambda backend, interval, options: captured.update(interval=interval, options=options) or 0,
    )

    rc = main(["--monitor"], backend=StaticBackend())

    assert rc == 0
    assert captured["options"].layout.value == "compact"
    assert captured["options"].readonly is True


def test_cli_explicit_monitor_falls_back_to_snapshot_outside_tty(monkeypatch, capsys):
    monkeypatch.setattr("mxtop.cli.sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("mxtop.cli.sys.stdout.isatty", lambda: False)

    rc = main(["--monitor", "--no-color"], backend=StaticBackend())

    captured = capsys.readouterr()
    assert rc == 1
    assert "MXTOP" in captured.out
    assert "requires stdin and stdout" in captured.err


def test_cli_curses_failure_falls_back_to_snapshot(monkeypatch, capsys):
    monkeypatch.setattr("mxtop.cli.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("mxtop.cli.sys.stdout.isatty", lambda: True)
    monkeypatch.setattr(cli, "run_tui", lambda *_args, **_kwargs: 1)

    rc = main(["--monitor", "--no-color"], backend=StaticBackend())

    assert rc == 1
    assert "MXTOP" in capsys.readouterr().out


def test_cli_ascii_translates_box_and_graph_characters(capsys):
    rc = main(["--once", "--ascii", "--no-color"], backend=StaticBackend())

    output = capsys.readouterr().out
    assert rc == 0
    assert "╒" not in output
    assert "│" not in output
