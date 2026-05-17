"""Render a gallery of mxtop outputs for common CLI parameter combinations.

Each scenario produces a PNG that shows what stdout looks like when running the
matching ``mxtop`` invocation. Output goes to ``assets/gallery/``.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from mxtop import ui  # noqa: E402
from mxtop.filters import apply_filters  # noqa: E402
from mxtop.models import DeviceSnapshot, FrameSnapshot, ProcessSnapshot  # noqa: E402
from mxtop.rendering import (  # noqa: E402
    render_once,
    reset_intensity_thresholds,
    set_intensity_thresholds,
    set_render_style,
    _colorize_line,
)
from mxtop.ui import panels as ui_panels  # noqa: E402
from mxtop.ui.panels import render_main_screen  # noqa: E402
from mxtop.ui.state import LayoutMode, UiState  # noqa: E402

from generate_preview import THEMES, render_to_png  # noqa: E402

GALLERY_DIR = PROJECT_ROOT / "assets" / "gallery"


def _stub_host_metrics() -> tuple[float, str, float, str, float]:
    return 23.4, "42.3GiB", 33.0, "0B", 0.0


def _stub_host_memory_total() -> int:
    return 128 * 1024**3


def _stub_load_average() -> str:
    return "1.42  1.85  2.07"


def _stub_user_host() -> str:
    return "alice@metax-dgx"


ui_panels._host_metrics = _stub_host_metrics  # type: ignore[assignment]
ui_panels._host_memory_total = _stub_host_memory_total  # type: ignore[assignment]
ui_panels._load_average_text = _stub_load_average  # type: ignore[assignment]
ui_panels._user_host = _stub_user_host  # type: ignore[assignment]
_ = ui


def _device(
    index: int,
    *,
    name: str = "MetaX C500",
    gpu: float | None = 0.0,
    mem_pct: float | None = 0.0,
    mbw: float | None = 0.0,
    temp: float | None = 38.0,
    power: float | None = 60.0,
    power_limit: float | None = 350.0,
    fan: float | None = 30.0,
    perf: str | None = "P0",
    persistence: str | None = "Enabled",
    bdf: str | None = None,
    driver: str | None = "2.31.0.5",
) -> DeviceSnapshot:
    mem_total = 64 * 1024**3
    mem_used = None if mem_pct is None else int(mem_pct / 100 * mem_total)
    return DeviceSnapshot(
        index=index,
        name=name,
        bdf=bdf or f"0000:{0x1a + index * 2:02x}:00.0",
        temperature_c=temp,
        power_w=power,
        power_limit_w=power_limit,
        gpu_util_percent=gpu,
        memory_util_percent=mem_pct,
        memory_bandwidth_util_percent=mbw,
        memory_used_bytes=mem_used,
        memory_total_bytes=mem_total,
        fan_percent=fan,
        ecc_errors=0,
        persistence_mode=persistence,
        performance_state=perf,
        driver_version=driver,
    )


def _process(
    gpu_index: int,
    pid: int,
    *,
    user: str = "alice",
    gpu_memory_mib: int = 1024,
    gpu_util: float | None = 30.0,
    gmbw: float | None = 25.0,
    cpu: float | None = 12.0,
    mem_pct: float | None = 4.0,
    runtime: float | None = 3600.0,
    process_type: str | None = "C",
    command: str = "python train.py",
) -> ProcessSnapshot:
    return ProcessSnapshot(
        gpu_index=gpu_index,
        pid=pid,
        name="python",
        gpu_memory_bytes=gpu_memory_mib * 1024**2,
        user=user,
        command=command,
        cpu_percent=cpu,
        host_memory_bytes=int(mem_pct / 100 * 64 * 1024**3) if mem_pct is not None else None,
        runtime_seconds=runtime,
        process_type=process_type,
        gpu_util_percent=gpu_util,
        gpu_memory_bandwidth_util_percent=gmbw,
        memory_util_percent=mem_pct,
    )


def frame_three_gpu() -> FrameSnapshot:
    devices = [
        _device(0, gpu=88, mem_pct=92, mbw=64, temp=63, power=215, fan=42, perf="P0"),
        _device(1, gpu=74, mem_pct=71, mbw=48, temp=58, power=199, fan=39, perf="P0"),
        _device(2, gpu=0,  mem_pct=4.5, mbw=0, temp=41, power=78, fan=33, perf="P8"),
    ]
    processes = [
        _process(0, 423901, user="alice", gpu_memory_mib=51200, gpu_util=88, gmbw=64,
                 cpu=312.4, mem_pct=14.2, runtime=4*3600+27*60,
                 command="python train.py --config configs/llama3-70b.yaml --bf16"),
        _process(0, 423908, user="alice", gpu_memory_mib=4096, gpu_util=12, gmbw=8,
                 cpu=42.1, mem_pct=2.4, runtime=27*60,
                 command="python eval.py --checkpoint /data/ckpt/step-12000"),
        _process(1, 512377, user="bob", gpu_memory_mib=42000, gpu_util=74, gmbw=48,
                 cpu=215.0, mem_pct=9.8, runtime=86400*2+5*3600,
                 command="python -m vllm.entrypoints.api_server --model qwen2-72b"),
        _process(1, 512402, user="bob", gpu_memory_mib=3000, gpu_util=0, gmbw=0,
                 cpu=1.2, mem_pct=0.9, runtime=600, command="python sampler.py"),
        _process(2, 99001, user="root", gpu_memory_mib=128, gpu_util=0, gmbw=0,
                 cpu=0.0, mem_pct=0.1, runtime=86400*7,
                 command="/opt/mxdriver/bin/metaxctl serve"),
    ]
    return FrameSnapshot(devices=devices, processes=processes, backend="pymxsml")


def frame_idle_three() -> FrameSnapshot:
    devices = [
        _device(i, gpu=val, mem_pct=mem, mbw=mbw, temp=37 + i, power=72 + i * 1.5,
                fan=30, perf="P8")
        for i, (val, mem, mbw) in enumerate([(0.0, 3.5, 0.0), (2.0, 4.2, 1.0), (0.0, 2.8, 0.0)])
    ]
    processes = [
        _process(0, 99001, user="root", gpu_memory_mib=128, gpu_util=0, gmbw=0,
                 cpu=0.1, mem_pct=0.1, runtime=86400*12,
                 command="/opt/mxdriver/bin/metaxctl serve"),
    ]
    return FrameSnapshot(devices=devices, processes=processes, backend="pymxsml")


def frame_heavy_four() -> FrameSnapshot:
    devices = [
        _device(i, gpu=96 - i * 1.5, mem_pct=93 - i * 0.8, mbw=88 - i * 1.2,
                temp=78 + i, power=320 + i, fan=85, perf="P0")
        for i in range(4)
    ]
    users = ["alice", "bob", "carol", "dave"]
    processes = [
        _process(i, 410000 + i * 17, user=users[i],
                 gpu_memory_mib=int((92 - i * 0.5) * 600),
                 gpu_util=96 - i * 1.5, gmbw=88 - i * 1.2,
                 cpu=320 - i * 12, mem_pct=15 + i,
                 runtime=4 * 3600 + i * 600,
                 command=f"python -m train --rank {i} --config configs/llama3.yaml")
        for i in range(4)
    ]
    return FrameSnapshot(devices=devices, processes=processes, backend="pymxsml")


def frame_mixed_four() -> FrameSnapshot:
    loads = [(5, 8), (35, 40), (75, 78), (98, 95)]
    devices = [
        _device(i, gpu=gpu, mem_pct=mem, mbw=gpu * 0.85,
                temp=38 + gpu * 0.4, power=60 + gpu * 2.8, fan=25 + gpu * 0.55,
                perf="P0" if gpu > 0 else "P8")
        for i, (gpu, mem) in enumerate(loads)
    ]
    processes = [
        _process(0, 100, user="alice", gpu_util=5, gmbw=4, gpu_memory_mib=512,
                 cpu=4, mem_pct=1.0, command="bash"),
        _process(1, 200, user="bob", gpu_util=35, gmbw=30, gpu_memory_mib=18000,
                 cpu=110, mem_pct=12, command="python evaluate.py --batch 64"),
        _process(2, 300, user="carol", gpu_util=75, gmbw=66, gpu_memory_mib=44000,
                 cpu=380, mem_pct=42,
                 command="python -m torch.distributed.launch train.py"),
        _process(3, 400, user="dave", gpu_util=98, gmbw=92, gpu_memory_mib=62000,
                 cpu=600, mem_pct=68, command="python pretrain.py --bf16 --grad-accum 4"),
        _process(3, 401, user="root", gpu_util=0, gmbw=0, gpu_memory_mib=300,
                 cpu=0.5, mem_pct=0.2, command="systemd"),
    ]
    return FrameSnapshot(devices=devices, processes=processes, backend="pymxsml")


def frame_eight_mixed() -> FrameSnapshot:
    loads = [5, 18, 33, 51, 67, 80, 92, 100]
    devices = [
        _device(i, gpu=v, mem_pct=v * 0.85 + 4, mbw=v * 0.8,
                temp=30 + v * 0.45, power=50 + v * 2.8, fan=25 + v * 0.6,
                perf="P0" if v > 0 else "P8")
        for i, v in enumerate(loads)
    ]
    users = ["alice", "bob", "carol", "dave"]
    processes = [
        _process(i, 5000 + i, user=users[i % len(users)],
                 gpu_memory_mib=int(v * 600), gpu_util=v, gmbw=v * 0.8,
                 cpu=v * 4, mem_pct=v * 0.6,
                 runtime=600 + i * 1200,
                 command=f"python -m train --rank {i} --config configs/llama3.yaml")
        for i, v in enumerate(loads)
    ]
    return FrameSnapshot(devices=devices, processes=processes, backend="pymxsml")


def frame_sixteen_mixed() -> FrameSnapshot:
    utils = [88, 74, 0, 92, 12, 67, 81, 55, 19, 99, 24, 41, 73, 60, 8, 35]
    devices = [
        _device(i, gpu=float(v), mem_pct=min(99.0, v * 0.85 + i * 1.7),
                mbw=v * 0.8, temp=38 + v // 4, power=70 + v * 2.4,
                fan=30 + v // 5, perf="P0" if v > 0 else "P8")
        for i, v in enumerate(utils)
    ]
    users = ["alice", "bob", "carol", "dave"]
    processes = [
        _process(i, 410000 + i * 17, user=users[i % len(users)],
                 gpu_memory_mib=int(v * 500),
                 gpu_util=float(v), gmbw=v * 0.8,
                 cpu=20 + (i * 19) % 200, mem_pct=2.0 + (i % 5),
                 runtime=600 + i * 1234,
                 command=f"python -m train --rank {i} --config configs/llama3.yaml")
        for i, v in enumerate(utils[:12]) if v > 0
    ]
    return FrameSnapshot(devices=devices, processes=processes, backend="pymxsml")


def frame_missing_telemetry() -> FrameSnapshot:
    devices = [
        DeviceSnapshot(
            index=0, name="MetaX C500", bdf="0000:1a:00.0",
            temperature_c=None, power_w=None, power_limit_w=None,
            gpu_util_percent=None, memory_util_percent=None,
            memory_bandwidth_util_percent=None,
            memory_used_bytes=None, memory_total_bytes=None,
            fan_percent=None, ecc_errors=None,
            persistence_mode=None, performance_state=None, driver_version=None,
        ),
        _device(1, gpu=12, mem_pct=14, mbw=None, temp=42, power=80,
                power_limit=None, fan=None, perf="P2", persistence="Disabled"),
    ]
    processes = [
        _process(0, 700, user="ops", gpu_util=None, gmbw=None, cpu=None,
                 mem_pct=None, runtime=None, gpu_memory_mib=512, command="(unknown)"),
    ]
    return FrameSnapshot(devices=devices, processes=processes, backend="mxsmi")


@dataclass(slots=True)
class Variant:
    slug: str
    cmd: str
    description: str
    frame_name: str
    width: int = 140
    color: bool = True
    light: bool = False
    colorful: bool = False
    layout: LayoutMode = LayoutMode.AUTO
    json_output: bool = False
    json_truncate: int | None = None
    filter_only: list[int] | None = None
    filter_user: list[str] | None = None
    filter_pid: list[int] | None = None
    only_compute: bool = False
    gpu_threshold: tuple[int, int] | None = None
    mem_threshold: tuple[int, int] | None = None
    height: int | None = None


FRAMES = {
    "three": frame_three_gpu,
    "idle": frame_idle_three,
    "heavy": frame_heavy_four,
    "mixed4": frame_mixed_four,
    "eight": frame_eight_mixed,
    "sixteen": frame_sixteen_mixed,
    "missing": frame_missing_telemetry,
}


VARIANTS: list[Variant] = [
    Variant("once-default", "mxtop --once",
            "Default colored snapshot (3 active GPUs, mixed load).",
            "three"),
    Variant("once-no-color", "mxtop --once --no-color",
            "Plain ASCII snapshot for logs and pipes.",
            "three", color=False),
    Variant("once-colorful", "mxtop --once --colorful",
            "Five-tier intensity palette (bright green / green / yellow / bright yellow / red / bright red).",
            "mixed4", colorful=True),
    Variant("once-light", "mxtop --once --light",
            "Light terminal theme — dim foreground swapped for readability on white backgrounds.",
            "three", light=True),
    Variant("once-compact", "mxtop --once --monitor compact",
            "Compact device panel — one row per GPU, no bars.",
            "eight", layout=LayoutMode.COMPACT, width=140),
    Variant("once-full", "mxtop --once --monitor full",
            "Full device panel — two rows per GPU with MEM/MBW/UTL/PWR bars.",
            "mixed4", layout=LayoutMode.FULL, width=140),
    Variant("once-only", "mxtop --once --only 0 2",
            "Filter to specific GPU indices.",
            "three", filter_only=[0, 2]),
    Variant("once-user", "mxtop --once --user alice",
            "Filter processes by owner.",
            "three", filter_user=["alice"]),
    Variant("once-pid", "mxtop --once --pid 423901 512377",
            "Filter processes by PID.",
            "three", filter_pid=[423901, 512377]),
    Variant("once-only-compute", "mxtop --once --only-compute",
            "Show only compute processes when the type field is available.",
            "three", only_compute=True),
    Variant("once-gpu-thresh", "mxtop --once --gpu-util-thresh 30 60",
            "Custom GPU intensity thresholds — yellow at 30%, red at 60%.",
            "mixed4", gpu_threshold=(30, 60)),
    Variant("once-mem-thresh", "mxtop --once --mem-util-thresh 20 50",
            "Custom memory intensity thresholds — yellow at 20%, red at 50%.",
            "mixed4", mem_threshold=(20, 50)),
    Variant("once-heavy", "mxtop --once  # heavy 4-GPU run",
            "Saturation across the cluster — most bars cross the red threshold.",
            "heavy"),
    Variant("once-idle", "mxtop --once  # idle 3-GPU host",
            "Idle baseline — almost everything green, P8 power state.",
            "idle"),
    Variant("once-many-8", "mxtop --once  # 8 GPUs",
            "8-GPU host with mixed load — auto-layout chooses the wide device panel.",
            "eight", width=170),
    Variant("once-many-16", "mxtop --once  # 16 GPUs",
            "16-GPU host with mixed load — auto layout drops into the 8+8 compact grid.",
            "sixteen", width=180),
    Variant("once-missing", "mxtop --once  # backend with missing telemetry",
            "Graceful N/A rendering when the backend cannot report a metric.",
            "missing"),
    Variant("json-default", "mxtop --json",
            "JSON snapshot suitable for piping into jq / Prometheus exporters.",
            "three", json_output=True, json_truncate=40, width=110),
]


def _filtered(frame: FrameSnapshot, variant: Variant) -> FrameSnapshot:
    if not any((variant.filter_only, variant.filter_user, variant.filter_pid, variant.only_compute)):
        return frame
    process_types = {"C"} if variant.only_compute else None
    return apply_filters(
        frame,
        device_indices=set(variant.filter_only) if variant.filter_only else None,
        users=set(variant.filter_user) if variant.filter_user else None,
        pids=set(variant.filter_pid) if variant.filter_pid else None,
        process_types=process_types,
        require_process_type=variant.only_compute,
    )


def _render_text(variant: Variant) -> str:
    frame = FRAMES[variant.frame_name]()
    frame = _filtered(frame, variant)

    if variant.json_output:
        payload = json.dumps(frame.to_dict(), indent=2, sort_keys=True, default=str)
        lines = payload.split("\n")
        if variant.json_truncate and len(lines) > variant.json_truncate:
            lines = lines[: variant.json_truncate] + ["  ..."]
        return "\n".join(lines)

    reset_intensity_thresholds()
    set_render_style(light=variant.light, colorful=variant.colorful)
    if variant.gpu_threshold is not None:
        set_intensity_thresholds(gpu=variant.gpu_threshold)
    if variant.mem_threshold is not None:
        set_intensity_thresholds(memory=variant.mem_threshold)

    if variant.layout != LayoutMode.AUTO:
        state = UiState(layout=variant.layout)
        screen = render_main_screen(frame, state, width=variant.width, height=variant.height)
        if not variant.color:
            return "\n".join(screen.lines)
        return "\n".join(_colorize_line(row, line) for row, line in enumerate(screen.lines))
    return render_once(frame, use_color=variant.color, width=variant.width)


def _prefix(variant: Variant) -> str:
    prompt = f"$ {variant.cmd}"
    return f"\x1b[1;36m{prompt}\x1b[0m"


def _render_all() -> list[Variant]:
    GALLERY_DIR.mkdir(parents=True, exist_ok=True)
    completed: list[Variant] = []
    for variant in VARIANTS:
        text = _render_text(variant)
        if variant.json_output:
            full = "\x1b[1;36m$ " + variant.cmd + "\x1b[0m\n" + text
        else:
            full = _prefix(variant) + "\n" + text
        theme = "light" if variant.light else "dark"
        target = GALLERY_DIR / f"{variant.slug}.png"
        render_to_png(full, theme, target)
        completed.append(variant)
        print(f"wrote {target.relative_to(PROJECT_ROOT)}")
    reset_intensity_thresholds()
    set_render_style(light=False, colorful=False)
    return completed


def _write_gallery_md(variants: list[Variant]) -> None:
    md = PROJECT_ROOT / "GALLERY.md"
    sections: list[str] = []
    sections.append("# mxtop CLI Gallery\n")
    sections.append(
        "Each tile below shows the rendered stdout for a common ``mxtop`` invocation. "
        "All scenes use deterministic synthetic telemetry so you can compare output across flags.\n"
    )
    sections.append(
        "Re-render this gallery with ``uv run --with pillow --with psutil python scripts/render_gallery.py``.\n"
    )

    groups = [
        ("Snapshot modes", ["once-default", "once-no-color", "json-default"]),
        ("Color and palette", ["once-colorful", "once-light"]),
        ("Layout modes", ["once-full", "once-compact"]),
        ("Filters", ["once-only", "once-user", "once-pid", "once-only-compute"]),
        ("Custom intensity thresholds", ["once-gpu-thresh", "once-mem-thresh"]),
        ("Load profiles", ["once-idle", "once-heavy"]),
        ("Multi-GPU layouts", ["once-many-8", "once-many-16"]),
        ("Edge cases", ["once-missing"]),
    ]
    by_slug = {v.slug: v for v in variants}
    for title, slugs in groups:
        sections.append(f"## {title}\n")
        sections.append("| Command | Preview |")
        sections.append("| --- | --- |")
        for slug in slugs:
            variant = by_slug.get(slug)
            if variant is None:
                continue
            sections.append(
                f"| `{variant.cmd}`<br><sub>{variant.description}</sub> "
                f"| ![{variant.slug}](assets/gallery/{variant.slug}.png) |"
            )
        sections.append("")

    md.write_text("\n".join(sections).rstrip() + "\n", encoding="utf-8")
    print(f"wrote {md.relative_to(PROJECT_ROOT)}")


def main() -> int:
    completed = _render_all()
    _write_gallery_md(completed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
