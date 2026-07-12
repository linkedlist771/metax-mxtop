from mxtop.models import FrameSnapshot
from mxtop.sampler import SnapshotSampler


class CountingBackend:
    name = "counting"

    def __init__(self):
        self.calls = 0

    def snapshot(self):
        self.calls += 1
        return FrameSnapshot(devices=[], processes=[], backend=self.name)


class FailingBackend:
    name = "failing"

    def snapshot(self):
        raise RuntimeError("boom")


def test_sampler_collects_initial_frame():
    backend = CountingBackend()
    sampler = SnapshotSampler(backend, interval=10)
    sampler.start()
    try:
        state = sampler.wait_for_frame(timeout=1)
    finally:
        sampler.stop()

    assert state.frame is not None
    assert state.frame.backend == "counting"
    assert backend.calls == 1


def test_sampler_records_backend_errors():
    sampler = SnapshotSampler(FailingBackend(), interval=10)
    sampler.start()
    try:
        state = sampler.wait_for_frame(timeout=1)
    finally:
        sampler.stop()

    assert state.error == "boom"


def test_sampler_refresh_now_triggers_another_snapshot():
    backend = CountingBackend()
    sampler = SnapshotSampler(backend, interval=10)
    sampler.start()
    try:
        initial = sampler.wait_for_frame(timeout=1)
        sampler.refresh_now()
        _ = sampler.wait_for_version(initial.version, timeout=1)
    finally:
        sampler.stop()

    assert backend.calls >= 2


def test_sampler_retains_last_frame_and_exposes_later_refresh_error():
    class FlakyBackend:
        name = "flaky"

        def __init__(self):
            self.calls = 0

        def snapshot(self):
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("telemetry unavailable")
            return FrameSnapshot(devices=[], processes=[], backend=self.name)

    sampler = SnapshotSampler(FlakyBackend(), interval=10)
    sampler.start()
    try:
        initial = sampler.wait_for_frame(timeout=1)
        sampler.refresh_now()
        failed = sampler.wait_for_version(initial.version, timeout=1)
    finally:
        sampler.stop()

    assert failed.frame is initial.frame
    assert failed.error == "telemetry unavailable"
