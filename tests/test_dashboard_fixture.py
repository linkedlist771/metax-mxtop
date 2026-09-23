import json
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from serve_dashboard_fixture import (  # noqa: E402
    FIXED_TIMESTAMP,
    REMOTE_PROCESS_IDENTITY,
    TRAINING_PID,
    _next_step,
    _parse_control_line,
    _publish_control_updates,
    build_parser,
    fixture_cluster,
)
from mxtop.remote.web import SnapshotHolder  # noqa: E402


def _primary(step, profile="alpha"):
    node = fixture_cluster(step, profile).nodes[0]
    if node.frame is None:
        return None
    return next(
        (process for process in node.frame.processes if process.pid == TRAINING_PID),
        None,
    )


def _processes(cluster):
    return [
        process
        for node in cluster.nodes
        if node.frame is not None
        for process in node.frame.processes
    ]


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


def test_beta_profile_is_distinct_but_keeps_lifecycle_steps():
    alpha = fixture_cluster(5)
    beta = fixture_cluster(5, "beta")
    alpha_processes = _processes(alpha)
    beta_processes = _processes(beta)

    assert alpha.timestamp == beta.timestamp == FIXED_TIMESTAMP + 10.0
    assert {node.hostname for node in alpha.nodes}.isdisjoint(
        node.hostname for node in beta.nodes
    )
    assert {process.user for process in alpha_processes}.isdisjoint(
        process.user for process in beta_processes
    )
    assert {process.command for process in alpha_processes}.isdisjoint(
        process.command for process in beta_processes
    )
    assert {process.identity for process in alpha_processes}.isdisjoint(
        process.identity for process in beta_processes
    )
    assert sorted(process.pid for process in alpha_processes) == sorted(
        process.pid for process in beta_processes
    )

    training = _primary(5, "beta")
    reused = _primary(8, "beta")
    assert training is not None and reused is not None
    assert fixture_cluster(6, "beta").nodes[0].reachable is False
    assert fixture_cluster(7, "beta").nodes[0].reachable is True
    assert reused.identity == training.identity
    assert reused.runtime_seconds == 2.0
    assert reused.command != training.command


def test_fixture_parser_accepts_profile_and_control_switches():
    args = build_parser().parse_args(["--profile", "beta", "--step", "4"])
    assert args.profile == "beta"
    assert args.step == 4

    profile, step = _parse_control_line(
        '{"profile": "beta", "step": 3}', "alpha"
    )
    assert (profile, step) == ("beta", 3)
    assert _parse_control_line("4", profile) == ("beta", 4)


@pytest.mark.parametrize(
    "line",
    (
        "",
        "not-json",
        "[]",
        "{}",
        '{"profile": "beta"}',
        '{"profile": "missing", "step": 1}',
        '{"profile": "beta", "step": -1}',
        '{"profile": "beta", "step": true}',
        '{"profile": "beta", "step": 1.5}',
        '{"profile": "beta", "step": 1, "extra": true}',
    ),
)
def test_fixture_control_rejects_invalid_lines(line):
    with pytest.raises(ValueError):
        _parse_control_line(line, "alpha")


def test_control_stream_warns_and_keeps_the_selected_profile(capsys):
    holder = SnapshotHolder()
    _publish_control_updates(
        holder,
        (
            '{"profile": "beta", "step": 1}\n',
            '{"profile": "missing", "step": 9}\n',
            "2\n",
        ),
        "alpha",
    )

    payload, version = holder.current()
    cluster = json.loads(payload)
    assert version == 2
    assert cluster["timestamp"] == FIXED_TIMESTAMP + 4.0
    assert cluster["nodes"][0]["hostname"] == "cygnus-11"
    assert "Ignoring invalid dashboard fixture control" in capsys.readouterr().err
