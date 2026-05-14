from mxtop.filters import apply_filters, filter_processes
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
