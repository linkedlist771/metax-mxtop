from __future__ import annotations

from contextlib import contextmanager
import sys
from types import SimpleNamespace

from mxtop.host import enrich_processes
from mxtop.models import ProcessSnapshot


def test_enrich_processes_calculates_cpu_percent_from_elapsed_cpu_time(monkeypatch):
    samples = iter(
        [
            SimpleNamespace(user=10.0, system=2.0),
            SimpleNamespace(user=10.5, system=2.0),
        ]
    )

    class FakeProcess:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def name(self) -> str:
            return "python"

        def username(self) -> str:
            return "alice"

        def cmdline(self) -> list[str]:
            return ["python", "train.py"]

        def memory_info(self) -> SimpleNamespace:
            return SimpleNamespace(rss=1024)

        def memory_percent(self) -> float:
            return 1.25

        def create_time(self) -> float:
            return 100.0

        def cpu_times(self) -> SimpleNamespace:
            return next(samples)

    fake_psutil = SimpleNamespace(Process=FakeProcess, Error=Exception)
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)
    monkeypatch.setattr("mxtop.host.time.time", lambda: 200.0)

    process = ProcessSnapshot(gpu_index=0, pid=123)
    enrich_processes([process])

    assert process.cpu_percent is None

    monkeypatch.setattr("mxtop.host.time.time", lambda: 201.0)
    enrich_processes([process])

    assert process.cpu_percent == 50.0
    assert process.memory_util_percent == 1.25


def test_enrich_processes_samples_a_pid_shared_by_multiple_gpus_once(monkeypatch):
    samples = iter(
        [
            SimpleNamespace(user=20.0, system=4.0),
            SimpleNamespace(user=20.5, system=4.0),
        ]
    )
    process_calls = 0
    oneshot_calls = 0

    class FakeProcess:
        def __init__(self, pid: int) -> None:
            nonlocal process_calls
            process_calls += 1
            assert pid == 456

        @contextmanager
        def oneshot(self):
            nonlocal oneshot_calls
            oneshot_calls += 1
            yield

        def name(self) -> str:
            return "python"

        def username(self) -> str:
            return "alice"

        def cmdline(self) -> list[str]:
            return ["python", "distributed.py"]

        def memory_info(self) -> SimpleNamespace:
            return SimpleNamespace(rss=4096)

        def create_time(self) -> float:
            return 150.0

        def cpu_times(self) -> SimpleNamespace:
            return next(samples)

    fake_psutil = SimpleNamespace(Process=FakeProcess, Error=Exception)
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)
    sample_time = 300.0
    monkeypatch.setattr("mxtop.host.time.time", lambda: sample_time)

    processes = [
        ProcessSnapshot(gpu_index=0, pid=456, name="mx-worker"),
        ProcessSnapshot(gpu_index=1, pid=456),
    ]
    enrich_processes(processes)

    assert process_calls == 1
    assert [process.name for process in processes] == ["mx-worker", "python"]
    assert [process.cpu_percent for process in processes] == [None, None]

    sample_time = 301.0
    enrich_processes(processes)

    assert process_calls == 2
    assert oneshot_calls == 2
    assert [process.cpu_percent for process in processes] == [50.0, 50.0]
    assert {process.command for process in processes} == {"python distributed.py"}
    assert {process.host_memory_bytes for process in processes} == {4096}
    assert {process.create_time for process in processes} == {150.0}


def test_enrich_processes_fallback_reads_proc_metrics(monkeypatch, tmp_path):
    proc = tmp_path / "proc" / "123"
    proc.mkdir(parents=True)
    (proc / "comm").write_text("python\n", encoding="utf-8")
    (proc / "cmdline").write_bytes(b"python\x00train.py\x00")
    stat_fields = ["0"] * 52
    stat_fields[13] = "1000"
    stat_fields[14] = "200"
    stat_fields[21] = "10000"
    (proc / "stat").write_text(" ".join(stat_fields), encoding="utf-8")
    (proc / "status").write_text("Name:\tpython\nVmRSS:\t2048 kB\n", encoding="utf-8")

    real_open = open
    real_stat = __import__("os").stat

    opened: list[str] = []

    def fake_open(path, *args, **kwargs):
        if isinstance(path, str) and path.startswith("/proc/123/"):
            opened.append(path)
            return real_open(str(proc / path.rsplit("/", 1)[1]), *args, **kwargs)
        return real_open(path, *args, **kwargs)

    def fake_stat(path):
        if path == "/proc/123/comm":
            return SimpleNamespace(st_uid=31965)
        return real_stat(path)

    monkeypatch.setitem(sys.modules, "psutil", None)
    monkeypatch.setattr("builtins.open", fake_open)
    monkeypatch.setattr("mxtop.host.os.stat", fake_stat)
    monkeypatch.setattr("mxtop.host._read_boot_time", lambda: 100.0)
    monkeypatch.setattr("mxtop.host._safe_clock_ticks", lambda: 100)
    monkeypatch.setattr("mxtop.host._safe_total_memory", lambda: 8 * 1024**2)
    monkeypatch.setattr("mxtop.host.time.time", lambda: 250.0)

    process = ProcessSnapshot(gpu_index=0, pid=123)
    peer = ProcessSnapshot(gpu_index=1, pid=123)
    enrich_processes([process, peer])

    assert process.name == "python"
    assert process.command == "python train.py"
    assert process.user == "31965"
    assert process.runtime_seconds == 50.0
    assert process.host_memory_bytes == 2048 * 1024
    assert process.memory_util_percent == 25.0
    assert peer.cpu_percent == process.cpu_percent
    assert peer.create_time == process.create_time
    assert len(opened) == 4

    stat_fields[13] = "1050"
    (proc / "stat").write_text(" ".join(stat_fields), encoding="utf-8")
    monkeypatch.setattr("mxtop.host.time.time", lambda: 251.0)
    enrich_processes([process, peer])

    assert process.cpu_percent == 50.0
    assert peer.cpu_percent == 50.0
    assert len(opened) == 8
