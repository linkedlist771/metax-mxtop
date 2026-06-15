import signal as _signal
from unittest import mock

from mxtop.ui.signals import send_signal, SIGNAL_KEYS


def test_signal_keys_map():
    assert SIGNAL_KEYS["K"][0] == "SIGKILL"
    assert SIGNAL_KEYS["T"][0] == "SIGTERM"
    assert SIGNAL_KEYS["I"][0] == "SIGINT"


def test_send_signal_success():
    with mock.patch("os.kill") as k:
        assert send_signal(1234, _signal.SIGTERM) is None
        k.assert_called_once_with(1234, _signal.SIGTERM)


def test_send_signal_process_gone():
    with mock.patch("os.kill", side_effect=ProcessLookupError):
        assert "no longer exists" in send_signal(1, _signal.SIGTERM)


def test_send_signal_permission():
    with mock.patch("os.kill", side_effect=PermissionError):
        assert "permission denied" in send_signal(1, _signal.SIGTERM).lower()
