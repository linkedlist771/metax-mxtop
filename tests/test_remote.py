import json
import math
import urllib.request

from mxtop.backends.mxsmi import build_frame_from_outputs
from mxtop.models import ClusterSnapshot, DeviceSnapshot, FrameSnapshot, NodeSnapshot
from mxtop.remote.app import load_hosts
from mxtop.remote.web import SnapshotHolder, _sanitize, make_server

DMON = """dev, die, hottemp, drmossoctemp, drmoscoretemp, hbmtemp, power, gpu, visvram, vram, xtt, total, bdfid
idx, idx, C, C, C, C, W, %, %, %, %, GB,
0, 0, 34, N/A, 31, 40, 254, 37, 78, 78, 0, 72, 0000:01:00.0
1, 0, 35, 31, 31, 39, 254, 38, 78, 78, 0, 72, 0000:02:00.0
"""
LIST = "GPU#0    MXTEST-00    0000:01:00.0    Available (UUID: GPU-0)\n"


def test_build_frame_from_outputs_reuses_parsers():
    known = {0: DeviceSnapshot(index=0, name="MXTEST-00")}
    frame = build_frame_from_outputs(DMON, "", known_devices=known, backend_name="mx-smi@n1", enrich=False)

    assert frame.backend == "mx-smi@n1"
    assert [d.index for d in frame.devices] == [0, 1]
    assert frame.devices[0].temperature_c == 34.0
    assert frame.devices[0].power_w == 254.0
    assert frame.devices[0].name == "MXTEST-00"
    assert frame.processes == []


def test_load_hosts_merges_args_file_comments_and_dedupes(tmp_path):
    nodes_file = tmp_path / "nodes.txt"
    nodes_file.write_text("# cluster a\nnodeA\nnodeB  nodeC\n\nnodeA  # dup\n")

    hosts = load_hosts(["nodeX", "nodeY,nodeZ"], str(nodes_file))

    assert hosts == ["nodeX", "nodeY", "nodeZ", "nodeA", "nodeB", "nodeC"]


def test_load_hosts_empty_when_nothing_given():
    assert load_hosts(None, None) == []


def test_sanitize_replaces_non_finite_floats():
    cleaned = _sanitize({"a": float("nan"), "b": [float("inf"), 1.5], "c": "x"})
    assert cleaned == {"a": None, "b": [None, 1.5], "c": "x"}
    assert json.dumps(cleaned)  # valid JSON, no NaN/Infinity tokens


def test_snapshot_holder_versions_and_payload():
    holder = SnapshotHolder()
    assert holder.current() == ("{}", 0)

    cluster = ClusterSnapshot(nodes=[NodeSnapshot(hostname="n1", reachable=True, frame=FrameSnapshot([], []))])
    holder.update(cluster)
    payload, version = holder.current()

    assert version == 1
    decoded = json.loads(payload)
    assert decoded["nodes"][0]["hostname"] == "n1"


def test_snapshot_holder_sanitizes_payload():
    holder = SnapshotHolder()
    device = DeviceSnapshot(index=0, gpu_util_percent=float("nan"))
    holder.update(ClusterSnapshot(nodes=[NodeSnapshot("n1", True, FrameSnapshot([device], []))]))
    payload, _ = holder.current()
    # json.loads rejects NaN with parse_constant raising, proving it was sanitized
    decoded = json.loads(payload, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    assert decoded["nodes"][0]["frame"]["devices"][0]["gpu_util_percent"] is None


def test_cluster_snapshot_to_dict_is_serializable():
    cluster = ClusterSnapshot(nodes=[NodeSnapshot("n1", False, error="boom")])
    data = cluster.to_dict()
    assert data["nodes"][0]["error"] == "boom"
    assert math.isfinite(data["timestamp"])


def test_web_server_serves_snapshot_and_index():
    holder = SnapshotHolder()
    holder.update(ClusterSnapshot(nodes=[NodeSnapshot("n1", True, FrameSnapshot([], []))]))
    server = make_server(holder, bind="127.0.0.1", port=0)
    import threading

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[0], server.server_address[1]
        with urllib.request.urlopen(f"http://{host}:{port}/api/snapshot", timeout=5) as resp:
            payload = json.loads(resp.read())
        assert payload["nodes"][0]["hostname"] == "n1"
        with urllib.request.urlopen(f"http://{host}:{port}/", timeout=5) as resp:
            assert b"mxtop" in resp.read()
    finally:
        server.shutdown()
        server.server_close()
