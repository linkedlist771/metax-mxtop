from mxtop import backends
from mxtop.models import FrameSnapshot


def test_auto_backend_reuses_the_successful_probe_frame(monkeypatch):
    frames = [
        FrameSnapshot(devices=[], processes=[], backend="preferred", timestamp=1.0),
        FrameSnapshot(devices=[], processes=[], backend="preferred", timestamp=2.0),
    ]
    calls = []

    class PreferredBackend:
        name = "preferred"
        def snapshot(self):
            frame = frames[len(calls)]
            calls.append(frame)
            return frame

    class UnusedFallback:
        def __init__(self):
            raise AssertionError("fallback should not be constructed")

    monkeypatch.setattr(backends, "PymxsmlBackend", PreferredBackend)
    monkeypatch.setattr(backends, "MxSmiBackend", UnusedFallback)

    backend = backends.create_backend("auto")

    assert backend.name == "preferred"
    assert len(calls) == 1
    assert backend.snapshot() is frames[0]
    assert len(calls) == 1
    assert backend.snapshot() is frames[1]
    assert len(calls) == 2


def test_auto_backend_prefetches_the_fallback_after_a_failed_probe(monkeypatch):
    frame = FrameSnapshot(devices=[], processes=[], backend="fallback")
    fallback_calls = []

    class BrokenBackend:
        name = "broken"

        def snapshot(self):
            raise RuntimeError("unavailable")

    class FallbackBackend:
        name = "fallback"
        def snapshot(self):
            fallback_calls.append(None)
            return frame

    monkeypatch.setattr(backends, "PymxsmlBackend", BrokenBackend)
    monkeypatch.setattr(backends, "MxSmiBackend", FallbackBackend)

    backend = backends.create_backend("auto")

    assert len(fallback_calls) == 1
    assert backend.snapshot() is frame
    assert len(fallback_calls) == 1
