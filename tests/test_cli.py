import json

from mxtop.cli import main
from mxtop.models import DeviceSnapshot, FrameSnapshot


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
