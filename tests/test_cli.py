import json
import os
import subprocess
import sys

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


def test_cli_count_repeats_once_output(monkeypatch, capsys):
    sleeps: list[float] = []
    monkeypatch.setattr("mxtop.cli.time.sleep", lambda value: sleeps.append(value))

    rc = main(
        ["--once", "--no-color", "--count", "3", "--interval", "0.5"],
        backend=StaticBackend(),
    )

    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out.count("MXC500") == 3
    assert sleeps == [0.5, 0.5]


def test_cli_count_implies_once(monkeypatch, capsys):
    monkeypatch.setattr("mxtop.cli.time.sleep", lambda _: None)

    rc = main(["-n", "2", "--no-color"], backend=StaticBackend())

    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out.count("MXC500") == 2


def test_cli_count_repeats_json_output(monkeypatch, capsys):
    monkeypatch.setattr("mxtop.cli.time.sleep", lambda _: None)

    rc = main(["--json", "-n", "2"], backend=StaticBackend())

    captured = capsys.readouterr()
    decoder = json.JSONDecoder()
    text = captured.out.strip()
    payloads = []
    while text:
        payload, end = decoder.raw_decode(text)
        payloads.append(payload)
        text = text[end:].lstrip()

    assert rc == 0
    assert len(payloads) == 2
    assert all(p["devices"][0]["name"] == "MXC500" for p in payloads)


def test_cli_count_rejects_monitor_mode(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--monitor", "--count", "2"], backend=StaticBackend())

    assert excinfo.value.code == 2
    assert "--count requires --once or --json" in capsys.readouterr().err


def test_cli_count_rejects_zero(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--once", "--count", "0"], backend=StaticBackend())

    assert excinfo.value.code == 2
    assert "count must be at least 1" in capsys.readouterr().err


def test_cli_json_replaces_non_finite_telemetry_with_null(capsys):
    class NonFiniteBackend:
        name = "non-finite"

        def snapshot(self):
            return FrameSnapshot(
                devices=[DeviceSnapshot(index=0, gpu_util_percent=float("nan"))],
                processes=[
                    ProcessSnapshot(gpu_index=0, pid=1, cpu_percent=float("inf"))
                ],
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


def test_cli_once_resamples_process_cpu_when_first_snapshot_is_unknown(
    monkeypatch, capsys
):
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


def test_cli_json_resamples_process_cpu_when_first_snapshot_is_unknown(
    monkeypatch, capsys
):
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
            [
                "--once",
                "--no-color",
                "--gpu-util-thresh",
                "5",
                "70",
                "--mem-util-thresh",
                "5",
                "70",
            ],
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


@pytest.mark.parametrize(
    "value",
    ("inf,80", "10.5,80", "1e1,80", "10,80,bad", "garbage", "10"),
)
def test_cli_ignores_malformed_threshold_environments(value):
    assert cli._parse_threshold_env(value) is None


def test_cli_threshold_environment_accepts_two_strict_integers():
    assert cli._parse_threshold_env("80,10") == (10, 80)


def test_cli_thresholds_accept_equal_boundaries():
    assert cli._parse_threshold_env("42,42") == (42, 42)
    assert cli._coerce_threshold([42.0, 42.0]) == (42, 42)


def test_cli_malformed_threshold_environment_never_crashes(monkeypatch, capsys):
    monkeypatch.setenv("MXTOP_GPU_UTILIZATION_THRESHOLDS", "inf,80")

    rc = main(["--once", "--no-color"], backend=StaticBackend())

    assert rc == 0
    assert "MXTOP" in capsys.readouterr().out


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


def test_cli_empty_force_color_environment_value_still_forces_color(
    capsys, monkeypatch
):
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

    assert (
        cli._should_use_color(
            no_color=args.no_color,
            force_color=args.force_color,
            stdout_is_tty=tty,
        )
        is expected
    )


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


def test_cli_help_has_stable_program_name_and_groups(capsys):
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(["--help"])

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert output.startswith("usage: mxtop ")
    assert "coloring:" in output
    assert "device filtering:" in output
    assert "process filtering:" in output
    assert "remote mode:" in output


def test_cli_non_tty_width_uses_nvitop_fallback(monkeypatch):
    observed = {}

    def fake_terminal_size(fallback):
        observed["fallback"] = fallback
        return os.terminal_size(fallback)

    monkeypatch.setattr(cli.shutil, "get_terminal_size", fake_terminal_size)

    assert cli._snapshot_width() == 79
    assert observed["fallback"] == (79, 24)


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


@pytest.mark.parametrize("mode", ("--once", "--json", "--monitor"))
def test_cli_remote_mode_is_mutually_exclusive_with_local_modes(mode, capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["--remote-mode", mode, "--nodes", "node-a"], backend=StaticBackend())

    assert exc_info.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    (
        ("--nodes", "node-a", "--once"),
        ("--nodes-file", "nodes.txt", "--once"),
        ("--discover", "--once"),
        ("--port", "9000", "--once"),
        ("--bind", "0.0.0.0", "--once"),
        ("--remote-mxsmi-path", "/opt/mx-smi", "--once"),
        ("--open", "--once"),
    ),
)
def test_cli_remote_arguments_require_remote_mode(argv, capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(list(argv), backend=StaticBackend())

    assert exc_info.value.code == 2
    assert "requires --remote-mode" in capsys.readouterr().err


@pytest.mark.parametrize(
    "local_args",
    (
        ("--backend", "mxsmi"),
        ("--back", "mxsmi"),
        ("--only", "0"),
        ("--compute",),
        ("--comp",),
        ("--graphics",),
        ("--no-color",),
        ("--gpu-util-thresh", "10", "80"),
    ),
)
def test_cli_remote_mode_rejects_local_only_arguments(local_args, capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["--remote-mode", "--nodes", "node-a", *local_args])

    assert exc_info.value.code == 2
    assert "is not supported with --remote-mode" in capsys.readouterr().err


@pytest.mark.parametrize("port", ("0", "65536", "-1"))
def test_cli_rejects_invalid_remote_ports(port, capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["--remote-mode", "--nodes", "node-a", f"--port={port}"])

    assert exc_info.value.code == 2
    assert "port must be between 1 and 65535" in capsys.readouterr().err


def test_cli_reports_remote_inventory_errors_without_traceback(tmp_path, capsys):
    missing = tmp_path / "missing-nodes.txt"

    rc = main(["--remote-mode", "--nodes-file", str(missing)])

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert captured.err.startswith("MXTOP ERROR: ")
    assert "missing-nodes.txt" in captured.err


def test_cli_reports_remote_startup_errors_on_stderr(monkeypatch, capsys):
    from mxtop.remote import app as remote_app

    def fail_remote(*_args, **_kwargs):
        raise RuntimeError("remote dependency unavailable")

    monkeypatch.setattr(remote_app, "run_remote", fail_remote)

    rc = main(["--remote-mode", "--nodes", "node-a"])

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert captured.err == "MXTOP ERROR: remote dependency unavailable\n"


def test_cli_remote_mode_discovers_hosts_when_nodes_are_omitted(monkeypatch, capsys):
    from mxtop.remote import app as remote_app
    from mxtop.remote import discovery
    from mxtop.remote.discovery import HostDiscovery

    observed = {}
    results = [HostDiscovery("node-a", True, gpu_count=8)]
    monkeypatch.setattr(
        discovery,
        "discover_configured_hosts",
        lambda **_kwargs: (["node-a"], results),
    )
    monkeypatch.setattr(
        remote_app,
        "report_discovery",
        lambda value: observed.setdefault("results", value),
    )
    monkeypatch.setattr(
        remote_app,
        "run_remote",
        lambda hosts, **kwargs: observed.update(hosts=hosts, kwargs=kwargs) or 0,
    )

    rc = main(["--remote-mode"])

    assert rc == 0
    assert observed["results"] == results
    assert observed["hosts"] == ["node-a"]
    assert observed["kwargs"]["mxsmi_path"] == "mx-smi"
    assert capsys.readouterr().err == ""


def test_cli_discover_merges_explicit_and_configured_hosts(monkeypatch):
    from mxtop.remote import app as remote_app
    from mxtop.remote import discovery

    observed = {}

    def capture_hosts(hosts, **_kwargs):
        observed["hosts"] = hosts
        return 0

    monkeypatch.setattr(
        discovery,
        "discover_configured_hosts",
        lambda **_kwargs: (["node-b", "node-c"], []),
    )
    monkeypatch.setattr(remote_app, "report_discovery", lambda _results: None)
    monkeypatch.setattr(remote_app, "run_remote", capture_hosts)

    rc = main(["--remote-mode", "--nodes", "node-a", "node-b", "--discover"])

    assert rc == 0
    assert observed["hosts"] == ["node-a", "node-b", "node-c"]


def test_cli_explicit_hosts_do_not_trigger_discovery(monkeypatch):
    from mxtop.remote import app as remote_app
    from mxtop.remote import discovery

    monkeypatch.setattr(
        discovery,
        "discover_configured_hosts",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected discovery")),
    )
    monkeypatch.setattr(remote_app, "run_remote", lambda *_args, **_kwargs: 0)

    assert main(["--remote-mode", "--nodes", "node-a"]) == 0


def test_cli_remote_mode_reports_empty_discovery(monkeypatch, capsys):
    from mxtop.remote import app as remote_app
    from mxtop.remote import discovery

    monkeypatch.setattr(
        discovery, "discover_configured_hosts", lambda **_kwargs: ([], [])
    )
    monkeypatch.setattr(remote_app, "report_discovery", lambda _results: None)

    rc = main(["--remote-mode"])

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert "no passwordless SSH config hosts" in captured.err


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
        lambda backend, interval, options: (
            captured.update(interval=interval, options=options) or 0
        ),
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


def test_cli_context_filter_reports_unavailable_backend_telemetry(capsys):
    class UnknownContextBackend:
        name = "mx-smi"

        def snapshot(self):
            return FrameSnapshot(
                devices=[DeviceSnapshot(index=0)],
                processes=[ProcessSnapshot(gpu_index=0, pid=123, cpu_percent=0.0)],
                backend=self.name,
            )

    rc = main(["--json", "--compute"], backend=UnknownContextBackend())

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert (
        "process context filtering is unavailable for backend 'mx-smi'" in captured.err
    )
    assert "process type telemetry was not reported" in captured.err


def test_cli_context_filter_allows_an_idle_backend(capsys):
    class IdleBackend:
        name = "mx-smi"

        def snapshot(self):
            return FrameSnapshot(
                devices=[DeviceSnapshot(index=0)], processes=[], backend=self.name
            )

    rc = main(["--json", "--compute"], backend=IdleBackend())

    assert rc == 0
    assert json.loads(capsys.readouterr().out)["processes"] == []


def test_cli_context_filter_rejects_backend_known_not_to_support_graphics(capsys):
    class ComputeOnlyBackend:
        name = "compute-only"
        process_context_types = frozenset({"C"})

        def snapshot(self):
            return FrameSnapshot(
                devices=[DeviceSnapshot(index=0)], processes=[], backend=self.name
            )

    rc = main(["--json", "--graphics"], backend=ComputeOnlyBackend())

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert "graphics context telemetry is not supported" in captured.err


def test_python_m_mxtop_runs_the_cli_module():
    result = subprocess.run(
        [sys.executable, "-m", "mxtop", "--version"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    assert result.stdout == f"mxtop {cli.__version__}\n"
    assert result.stderr == ""
