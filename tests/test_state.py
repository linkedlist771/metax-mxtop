from mxtop.models import ProcessSnapshot
from mxtop.ui.state import ProcessSort, sort_is_descending, sort_processes


def _process(pid: int, **values) -> ProcessSnapshot:
    user = values.pop("user", "alice")
    return ProcessSnapshot(gpu_index=0, pid=pid, user=user, **values)


def test_metric_sort_direction_and_ties_match_nvitop() -> None:
    processes = [
        _process(10, gpu_memory_bytes=50),
        _process(20, gpu_memory_bytes=50),
        _process(30, gpu_memory_bytes=None),
        _process(40, gpu_memory_bytes=10),
    ]

    descending = sort_processes(processes, ProcessSort.GPU_MEMORY)
    ascending = sort_processes(processes, ProcessSort.GPU_MEMORY, reverse=True)

    assert [process.pid for process in descending] == [30, 20, 10, 40]
    assert [process.pid for process in ascending] == [40, 10, 20, 30]
    assert sort_is_descending(ProcessSort.GPU_MEMORY)
    assert not sort_is_descending(ProcessSort.GPU_MEMORY, reverse=True)


def test_natural_sort_direction_uses_reverse_as_an_inversion() -> None:
    processes = [_process(20), _process(10), _process(30)]

    natural = sort_processes(processes, ProcessSort.PID)
    reversed_natural = sort_processes(processes, ProcessSort.PID, reverse=True)

    assert [process.pid for process in natural] == [10, 20, 30]
    assert [process.pid for process in reversed_natural] == [30, 20, 10]
    assert not sort_is_descending(ProcessSort.PID)
    assert sort_is_descending(ProcessSort.PID, reverse=True)


def test_host_memory_sort_uses_process_memory_percent() -> None:
    processes = [
        _process(10, host_memory_bytes=8 * 1024**3, memory_util_percent=5.0),
        _process(20, host_memory_bytes=2 * 1024**3, memory_util_percent=25.0),
    ]

    ordered = sort_processes(processes, ProcessSort.HOST_MEMORY)

    assert [process.pid for process in ordered] == [20, 10]


def test_missing_username_sorts_as_nvitop_na_value() -> None:
    processes = [
        _process(10, user=None),
        _process(20, user="A"),
        _process(30, user="alice"),
    ]

    ordered = sort_processes(processes, ProcessSort.USER)
    reversed_order = sort_processes(processes, ProcessSort.USER, reverse=True)

    assert [process.pid for process in ordered] == [20, 10, 30]
    assert [process.pid for process in reversed_order] == [30, 10, 20]


def test_non_finite_metrics_sort_as_unavailable() -> None:
    processes = [
        _process(10, gpu_util_percent=25.0),
        _process(20, gpu_util_percent=float("nan")),
        _process(30, gpu_util_percent=float("inf")),
    ]

    ordered = sort_processes(processes, ProcessSort.GPU_UTIL)

    assert {process.pid for process in ordered[:2]} == {20, 30}
    assert ordered[-1].pid == 10
