import json

from mxtop.cli import main
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
