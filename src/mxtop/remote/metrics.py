"""Prometheus text exposition for the cluster dashboard's /metrics endpoint.

Hand-rolled (no client library): the exposition format is a stable,
line-oriented text protocol, and the dashboard only exports gauges built
from the latest ClusterSnapshot.
"""

from __future__ import annotations

import math

from mxtop.models import ClusterSnapshot, DeviceSnapshot, NodeSnapshot

_PREFIX = "mxtop"

_DEVICE_GAUGES: tuple[tuple[str, str, str], ...] = (
    ("gpu_utilization_percent", "gpu_util_percent", "GPU utilization"),
    ("gpu_memory_used_bytes", "memory_used_bytes", "GPU memory used"),
    ("gpu_memory_total_bytes", "memory_total_bytes", "GPU memory total"),
    (
        "gpu_memory_bandwidth_utilization_percent",
        "memory_bandwidth_util_percent",
        "GPU memory bandwidth utilization",
    ),
    ("gpu_temperature_celsius", "temperature_c", "GPU temperature"),
    ("gpu_power_watts", "power_w", "GPU power draw"),
    ("gpu_power_limit_watts", "power_limit_w", "GPU power limit"),
    ("gpu_clock_megahertz", "gpu_clock_mhz", "GPU clock"),
    ("gpu_ecc_errors_total", "ecc_errors", "Total ECC errors reported"),
)

_HOST_GAUGES: tuple[tuple[str, str, str], ...] = (
    ("host_cpu_percent", "cpu_percent", "Host CPU utilization"),
    ("host_memory_used_bytes", "memory_used_bytes", "Host memory used"),
    ("host_memory_total_bytes", "memory_total_bytes", "Host memory total"),
    ("host_load1", "load_average_1m", "Host 1-minute load average"),
)


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


def _device_labels(hostname: str, device: DeviceSnapshot) -> str:
    labels = [
        f'node="{_escape_label(hostname)}"',
        f'gpu="{device.index}"',
        f'name="{_escape_label(device.name or "MetaX GPU")}"',
    ]
    if device.uuid:
        labels.append(f'uuid="{_escape_label(device.uuid)}"')
    return "{" + ",".join(labels) + "}"


def render_metrics(cluster: ClusterSnapshot) -> str:
    """Render the latest cluster snapshot as Prometheus text exposition."""

    lines: list[str] = []

    lines.append(f"# HELP {_PREFIX}_node_up Whether the node was reachable (1/0).")
    lines.append(f"# TYPE {_PREFIX}_node_up gauge")
    for node in cluster.nodes:
        label = f'{{node="{_escape_label(node.hostname)}"}}'
        lines.append(f"{_PREFIX}_node_up{label} {int(bool(node.reachable))}")

    lines.append(
        f"# HELP {_PREFIX}_node_collect_latency_seconds "
        "Time to collect the node's telemetry over SSH."
    )
    lines.append(f"# TYPE {_PREFIX}_node_collect_latency_seconds gauge")
    for node in cluster.nodes:
        if _finite(node.latency_ms):
            label = f'{{node="{_escape_label(node.hostname)}"}}'
            lines.append(
                f"{_PREFIX}_node_collect_latency_seconds{label} "
                f"{node.latency_ms / 1000.0:.6f}"
            )

    for metric, attribute, help_text in _DEVICE_GAUGES:
        samples = []
        for node in cluster.nodes:
            for device in _devices(node):
                value = getattr(device, attribute)
                if _finite(value):
                    samples.append(
                        f"{_PREFIX}_{metric}"
                        f"{_device_labels(node.hostname, device)} {value}"
                    )
        if samples:
            lines.append(f"# HELP {_PREFIX}_{metric} {help_text}.")
            lines.append(f"# TYPE {_PREFIX}_{metric} gauge")
            lines.extend(samples)

    for metric, attribute, help_text in _HOST_GAUGES:
        samples = []
        for node in cluster.nodes:
            if node.host is None:
                continue
            value = getattr(node.host, attribute)
            if _finite(value):
                label = f'{{node="{_escape_label(node.hostname)}"}}'
                samples.append(f"{_PREFIX}_{metric}{label} {value}")
        if samples:
            lines.append(f"# HELP {_PREFIX}_{metric} {help_text}.")
            lines.append(f"# TYPE {_PREFIX}_{metric} gauge")
            lines.extend(samples)

    lines.append(
        f"# HELP {_PREFIX}_gpu_processes GPU processes reported per node."
    )
    lines.append(f"# TYPE {_PREFIX}_gpu_processes gauge")
    for node in cluster.nodes:
        if node.frame is not None:
            label = f'{{node="{_escape_label(node.hostname)}"}}'
            lines.append(f"{_PREFIX}_gpu_processes{label} {len(node.frame.processes)}")

    lines.append(
        f"# HELP {_PREFIX}_snapshot_timestamp_seconds "
        "Unix time of the exported cluster snapshot."
    )
    lines.append(f"# TYPE {_PREFIX}_snapshot_timestamp_seconds gauge")
    lines.append(f"{_PREFIX}_snapshot_timestamp_seconds {cluster.timestamp:.3f}")

    return "\n".join(lines) + "\n"


def _devices(node: NodeSnapshot) -> list[DeviceSnapshot]:
    return node.frame.devices if node.frame is not None else []
