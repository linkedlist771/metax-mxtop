import asyncio
from types import SimpleNamespace

import pytest

from mxtop.models import ProcessSnapshot
from mxtop.remote import ssh
from mxtop.remote.cluster import (
    DEFAULT_REMOTE_COMMAND_TIMEOUT,
    ClusterMonitor,
)
from mxtop.remote.host import (
    HOST_TELEMETRY_COMMAND,
    parse_host_telemetry,
)
from mxtop.remote.processes import (
    apply_process_details,
    parse_process_details,
    process_details_command,
)


HOST_FIRST = """cpu 100 0 50 800 50 0 0 0
load 1.25 1.50 1.75
uptime 90061.5
mem 1024000 256000 128000
"""

HOST_SECOND = """cpu 140 0 70 870 60 0 0 0
load 2.25 2.50 2.75
uptime 90063.5
mem 1024000 204800 128000
"""

DMON = """dev, die, hottemp, drmossoctemp, drmoscoretemp, hbmtemp, power, gpu, visvram, vram, xtt, total, bdfid
idx, idx, C, C, C, C, W, %, %, %, %, GB,
0, 0, 34, N/A, 31, 40, 254, 37, 78, 78, 0, 72, 0000:01:00.0
"""


def test_host_telemetry_uses_cpu_deltas_and_parses_linux_metrics():
    first, first_cpu = parse_host_telemetry(HOST_FIRST)
    second, second_cpu = parse_host_telemetry(HOST_SECOND, first_cpu)

    assert HOST_TELEMETRY_COMMAND.startswith("LC_ALL=C awk")
    assert first.cpu_percent is None
    assert first.memory_total_bytes == 1024000 * 1024
    assert first.memory_used_bytes == 768000 * 1024
    assert first.memory_percent == pytest.approx(75.0)
    assert first.load_average_1m == 1.25
    assert first.load_average_5m == 1.5
    assert first.load_average_15m == 1.75
    assert first.uptime_seconds == 90061.5
    assert second.cpu_percent == pytest.approx(42.857142857)
    assert second.memory_percent == pytest.approx(80.0)
    assert second_cpu is not None


def test_host_telemetry_tolerates_missing_and_malformed_fields():
    snapshot, cpu_sample = parse_host_telemetry(
        "cpu bad data\nmem 0 0 0\nload 1 nope 3\nuptime -2\n"
    )

    assert cpu_sample is None
    assert snapshot.cpu_percent is None
    assert snapshot.memory_total_bytes is None
    assert snapshot.load_average_1m is None
    assert snapshot.uptime_seconds == 0.0


def test_remote_process_details_are_batched_parsed_and_applied():
    processes = [
        ProcessSnapshot(gpu_index=1, pid=20, name="python"),
        ProcessSnapshot(gpu_index=0, pid=10, name="worker"),
        ProcessSnapshot(gpu_index=2, pid=20, name="python"),
    ]

    command = process_details_command(processes)
    details = parse_process_details(
        "  10 alice 12.5 2048 3600 python -m worker --rank 0\n"
        "  20 bob 0.0 1024 45 /opt/app --serve\n"
        " malformed row\n"
    )
    apply_process_details(processes, details)

    assert command is not None and command.endswith("-p 10,20")
    assert processes[0].user == "bob"
    assert processes[0].host_memory_bytes == 1024 * 1024
    assert processes[0].runtime_seconds == 45
    assert processes[0].command == "/opt/app --serve"
    assert processes[1].user == "alice"
    assert processes[1].cpu_percent == 12.5
    assert processes[1].command == "python -m worker --rank 0"
    assert processes[2].user == "bob"


def test_cluster_monitor_collects_host_and_process_context(monkeypatch):
    class FakeConnection:
        def __init__(self):
            self.host_calls = 0
            self.closed = False

        async def run(self, command, *, check, timeout):
            assert check is False
            assert timeout == DEFAULT_REMOTE_COMMAND_TIMEOUT
            if command == "mx-smi -L":
                stdout = "GPU#0 MXTEST 0000:01:00.0 Available (UUID: GPU-0)\n"
            elif command == "mx-smi --show-version":
                stdout = "Driver Version: 3.9.3\nMACA Version: 3.8.0\n"
            elif "mx-smi dmon" in command:
                stdout = DMON
            elif command == "mx-smi --show-process":
                stdout = (
                    "GPU PID Type Process GPU Memory\n"
                    "0 42 C python 256 MiB\n"
                )
            elif command == HOST_TELEMETRY_COMMAND:
                self.host_calls += 1
                stdout = HOST_FIRST if self.host_calls == 1 else HOST_SECOND
            elif command.endswith("-p 42"):
                stdout = "42 alice 25.0 4096 7200 python -m server\n"
            else:
                raise AssertionError(f"unexpected command: {command}")
            return SimpleNamespace(exit_status=0, stdout=stdout)

        def close(self):
            self.closed = True

        async def wait_closed(self):
            pass

    connection = FakeConnection()

    async def fake_connect(host, *, connect_timeout):
        assert host == "node-a"
        assert connect_timeout == 10.0
        return connection

    monkeypatch.setattr(ssh, "connect", fake_connect)

    async def collect_twice():
        monitor = ClusterMonitor(["node-a"])
        try:
            first = await monitor.poll_once()
            second = await monitor.poll_once()
            return first, second
        finally:
            await monitor.close()

    first, second = asyncio.run(collect_twice())
    first_node = first.nodes[0]
    node = second.nodes[0]

    assert first_node.host is not None and first_node.host.cpu_percent is None
    assert node.reachable is True
    assert node.host is not None
    assert node.host.cpu_percent == pytest.approx(42.857142857)
    assert node.host.memory_percent == pytest.approx(80.0)
    assert node.frame is not None
    assert len(node.frame.devices) == 1
    assert len(node.frame.processes) == 1
    assert node.frame.processes[0].user == "alice"
    assert node.frame.processes[0].command == "python -m server"
    assert node.frame.processes[0].host_memory_bytes == 4096 * 1024
    assert connection.host_calls == 2
    assert connection.closed is True


@pytest.mark.parametrize(
    "command_timeout",
    (0.0, -1.0, float("nan"), float("inf"), float("-inf")),
)
def test_cluster_monitor_rejects_invalid_command_timeouts(command_timeout):
    with pytest.raises(ValueError, match="positive and finite"):
        ClusterMonitor(["node-a"], command_timeout=command_timeout)


def test_cluster_monitor_timeout_isolated_per_host_and_reconnects(monkeypatch):
    class HealthyConnection:
        def __init__(self, host):
            self.host = host
            self.closed = False
            self.timeouts = []

        async def run(self, command, *, check, timeout):
            assert check is False
            self.timeouts.append(timeout)
            if command == "mx-smi -L":
                stdout = (
                    f"GPU#0 MXTEST-{self.host} 0000:01:00.0 "
                    f"Available (UUID: GPU-{self.host})\n"
                )
            elif command == "mx-smi --show-version":
                stdout = "Driver Version: 3.9.3\nMACA Version: 3.8.0\n"
            elif "mx-smi dmon" in command:
                stdout = DMON
            elif command == "mx-smi --show-process":
                stdout = "GPU PID Type Process GPU Memory\n"
            elif command == HOST_TELEMETRY_COMMAND:
                stdout = HOST_FIRST
            else:
                raise AssertionError(f"unexpected command: {command}")
            return SimpleNamespace(exit_status=0, stdout=stdout)

        def close(self):
            self.closed = True

        async def wait_closed(self):
            pass

    class HangingConnection:
        def __init__(self):
            self.closed = False
            self.timeouts = []

        async def run(self, _command, *, check, timeout):
            assert check is False
            self.timeouts.append(timeout)
            await asyncio.wait_for(asyncio.Event().wait(), timeout=timeout)

        def close(self):
            self.closed = True

        async def wait_closed(self):
            pass

    healthy = HealthyConnection("healthy")
    hanging = HangingConnection()
    recovered = HealthyConnection("recovered")
    connect_calls = {"healthy": 0, "flaky": 0}

    async def fake_connect(host, *, connect_timeout):
        assert connect_timeout == 10.0
        connect_calls[host] += 1
        if host == "healthy":
            return healthy
        return hanging if connect_calls[host] == 1 else recovered

    monkeypatch.setattr(ssh, "connect", fake_connect)

    async def poll_twice():
        monitor = ClusterMonitor(
            ["healthy", "flaky"],
            command_timeout=0.02,
        )
        try:
            first = await asyncio.wait_for(monitor.poll_once(), timeout=0.5)
            second = await asyncio.wait_for(monitor.poll_once(), timeout=0.5)
            return first, second
        finally:
            await monitor.close()

    first, second = asyncio.run(poll_twice())

    assert first.nodes[0].reachable is True
    assert first.nodes[0].frame is not None
    assert first.nodes[1].reachable is False
    assert first.nodes[1].error == "TimeoutError"
    assert hanging.timeouts == [0.02]
    assert hanging.closed is True
    assert connect_calls == {"healthy": 1, "flaky": 2}
    assert second.nodes[0].reachable is True
    assert second.nodes[1].reachable is True
    assert second.nodes[1].frame is not None
    assert second.nodes[1].frame.backend == "mx-smi@flaky"
    assert healthy.timeouts and set(healthy.timeouts) == {0.02}
    assert recovered.timeouts and set(recovered.timeouts) == {0.02}
    assert healthy.closed is True
    assert recovered.closed is True
