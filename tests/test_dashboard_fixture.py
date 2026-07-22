import json
from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from serve_dashboard_fixture import (  # noqa: E402
    REMOTE_PROCESS_IDENTITY,
    TRAINING_PID,
    _next_step,
    fixture_cluster,
)


def _primary(step):
    node = fixture_cluster(step).nodes[0]
    if node.frame is None:
        return None
    return next(
        (process for process in node.frame.processes if process.pid == TRAINING_PID),
        None,
    )


def test_dashboard_fixture_covers_exit_and_pid_reuse_generations():
    training = _primary(5)
    exited = _primary(6)
    reused = _primary(8)

    assert training is not None
    assert training.identity == REMOTE_PROCESS_IDENTITY
    assert training.create_time is None
    assert training.runtime_seconds == 15_850.0
    assert exited is None
    assert fixture_cluster(6).nodes[0].reachable is False
    assert fixture_cluster(7).nodes[0].reachable is True
    assert reused is not None
    assert reused.identity == REMOTE_PROCESS_IDENTITY
    assert reused.create_time is None
    assert reused.runtime_seconds == 2.0
    assert reused.command is not None and "inference.server" in reused.command
    assert reused.command != training.command


def test_dashboard_fixture_is_serializable_and_trends_metrics():
    first = fixture_cluster(0)
    later = fixture_cluster(4)
    first_process = _primary(0)
    later_process = _primary(4)

    assert first_process is not None and later_process is not None
    assert later.timestamp > first.timestamp
    assert later_process.gpu_util_percent != first_process.gpu_util_percent
    assert later_process.gpu_memory_bytes > first_process.gpu_memory_bytes
    assert json.loads(json.dumps(later.to_dict()))["nodes"][0]["hostname"] == "atlas-01"


def test_dashboard_fixture_stop_step_is_inclusive():
    assert _next_step(4, 5) == 5
    assert _next_step(5, 5) is None
    assert _next_step(5, None) == 6
