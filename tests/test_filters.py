from mxtop.filters import apply_filters, filter_processes, resolve_visible_device_indices
from mxtop.models import DeviceSnapshot, FrameSnapshot, ProcessSnapshot


def test_apply_filters_limits_devices_and_processes():
    frame = FrameSnapshot(
        devices=[DeviceSnapshot(index=0), DeviceSnapshot(index=1)],
        processes=[ProcessSnapshot(gpu_index=0, pid=10), ProcessSnapshot(gpu_index=1, pid=11)],
    )

    filtered = apply_filters(frame, device_indices={1})

    assert [device.index for device in filtered.devices] == [1]
    assert [process.pid for process in filtered.processes] == [11]


def test_filter_processes_supports_users_pids_and_types():
    processes = [
        ProcessSnapshot(gpu_index=0, pid=10, user="alice", process_type="C"),
        ProcessSnapshot(gpu_index=0, pid=11, user="bob", process_type="G"),
        ProcessSnapshot(gpu_index=0, pid=12, user="alice", process_type=None),
    ]

    assert [p.pid for p in filter_processes(processes, users={"alice"})] == [10, 12]
    assert [p.pid for p in filter_processes(processes, pids={11})] == [11]
    assert [p.pid for p in filter_processes(processes, process_types={"C"})] == [10, 12]
    assert [p.pid for p in filter_processes(processes, process_types={"C"}, require_process_type=True)] == [10]


def test_process_context_filters_are_independent_and_combine_with_and():
    processes = [
        ProcessSnapshot(gpu_index=0, pid=10, process_type="C"),
        ProcessSnapshot(gpu_index=0, pid=11, process_type="G"),
        ProcessSnapshot(gpu_index=0, pid=12, process_type="C+G"),
        ProcessSnapshot(gpu_index=0, pid=13, process_type="X"),
        ProcessSnapshot(gpu_index=0, pid=14, process_type=None),
    ]

    assert [p.pid for p in filter_processes(processes, compute=True)] == [10, 12, 13]
    assert [p.pid for p in filter_processes(processes, only_compute=True)] == [10]
    assert [p.pid for p in filter_processes(processes, graphics=True)] == [11, 12, 13]
    assert [p.pid for p in filter_processes(processes, only_graphics=True)] == [11]
    assert [p.pid for p in filter_processes(processes, compute=True, graphics=True)] == [12, 13]
    assert filter_processes(processes, only_compute=True, only_graphics=True) == []


def test_empty_device_set_filters_every_device_and_process():
    frame = FrameSnapshot(
        devices=[DeviceSnapshot(index=0)],
        processes=[ProcessSnapshot(gpu_index=0, pid=10)],
    )

    filtered = apply_filters(frame, device_indices=set())

    assert filtered.devices == []
    assert filtered.processes == []


def test_visible_devices_resolve_indices_uuid_prefixes_and_bus_ids():
    devices = [
        DeviceSnapshot(index=0, uuid="GPU-aaaa", bdf="0000:01:00.0"),
        DeviceSnapshot(index=2, uuid="GPU-bbbb", bdf="0000:02:00.0"),
    ]

    assert resolve_visible_device_indices(devices, ["2", "GPU-aaa"]) == {0, 2}
    assert resolve_visible_device_indices(devices, ["0000:01"]) == {0}
    assert resolve_visible_device_indices(devices, []) == set()
