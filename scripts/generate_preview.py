"""Render a dummy mxtop frame and save it as a PNG screenshot."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from mxtop import ui  # noqa: E402
from mxtop.models import DeviceSnapshot, FrameSnapshot, ProcessSnapshot  # noqa: E402
from mxtop.rendering import render_once  # noqa: E402
from mxtop.ui import panels as ui_panels  # noqa: E402


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
_ = ui  # keep module reference alive

ANSI_PATTERN = re.compile(r"\x1b\[(\d+(?:;\d+)*)m")

THEMES = {
    "dark": {
        "bg": (15, 18, 23),
        "fg": (218, 220, 224),
        "selection_bg": (45, 90, 175),
        "30": (32, 32, 32),
        "31": (220, 80, 80),
        "32": (102, 195, 110),
        "33": (220, 188, 70),
        "34": (90, 158, 220),
        "35": (200, 110, 200),
        "36": (90, 200, 215),
        "37": (218, 220, 224),
    },
    "light": {
        "bg": (250, 250, 250),
        "fg": (40, 44, 52),
        "selection_bg": (180, 200, 235),
        "30": (200, 200, 200),
        "31": (210, 70, 70),
        "32": (45, 145, 60),
        "33": (175, 130, 30),
        "34": (50, 110, 200),
        "35": (160, 70, 175),
        "36": (40, 145, 160),
        "37": (40, 44, 52),
    },
}


def build_frame() -> FrameSnapshot:
    devices = [
        DeviceSnapshot(
            index=0,
            name="MetaX C500",
            bdf="0000:1a:00.0",
            uuid=None,
            temperature_c=63,
            power_w=215.4,
            power_limit_w=350,
            fan_percent=42,
            gpu_util_percent=88,
            memory_util_percent=92.3,
            memory_bandwidth_util_percent=64,
            memory_used_bytes=59 * 1024**3,
            memory_total_bytes=64 * 1024**3,
            performance_state="P0",
            ecc_errors=0,
            persistence_mode="Enabled",
            driver_version="2.31.0.5",
        ),
        DeviceSnapshot(
            index=1,
            name="MetaX C500",
            bdf="0000:3d:00.0",
            temperature_c=58,
            power_w=198.7,
            power_limit_w=350,
            fan_percent=39,
            gpu_util_percent=74,
            memory_util_percent=71.2,
            memory_bandwidth_util_percent=48,
            memory_used_bytes=45 * 1024**3,
            memory_total_bytes=64 * 1024**3,
            performance_state="P0",
            ecc_errors=0,
            persistence_mode="Enabled",
            driver_version="2.31.0.5",
        ),
        DeviceSnapshot(
            index=2,
            name="MetaX C500",
            bdf="0000:5e:00.0",
            temperature_c=41,
            power_w=78.0,
            power_limit_w=350,
            fan_percent=33,
            gpu_util_percent=0,
            memory_util_percent=4.5,
            memory_used_bytes=3 * 1024**3,
            memory_total_bytes=64 * 1024**3,
            performance_state="P8",
            ecc_errors=0,
            persistence_mode="Enabled",
            driver_version="2.31.0.5",
        ),
    ]
    processes = [
        ProcessSnapshot(
            gpu_index=0,
            pid=423901,
            name="python",
            user="alice",
            gpu_memory_bytes=51200 * 1024**2,
            gpu_util_percent=88,
            cpu_percent=312.4,
            host_memory_bytes=18 * 1024**3,
            memory_util_percent=14.2,
            runtime_seconds=4 * 3600 + 27 * 60 + 5,
            command="python train.py --config configs/llama3-70b.yaml --bf16",
            process_type="C",
        ),
        ProcessSnapshot(
            gpu_index=0,
            pid=423908,
            name="python",
            user="alice",
            gpu_memory_bytes=4096 * 1024**2,
            gpu_util_percent=12,
            cpu_percent=42.1,
            host_memory_bytes=3 * 1024**3,
            memory_util_percent=2.4,
            runtime_seconds=27 * 60 + 5,
            command="python eval.py --checkpoint /data/ckpt/step-12000",
            process_type="C",
        ),
        ProcessSnapshot(
            gpu_index=1,
            pid=512377,
            name="python",
            user="bob",
            gpu_memory_bytes=42000 * 1024**2,
            gpu_util_percent=74,
            cpu_percent=215.0,
            host_memory_bytes=12 * 1024**3,
            memory_util_percent=9.8,
            runtime_seconds=86400 * 2 + 5 * 3600,
            command="python -m vllm.entrypoints.api_server --model qwen2-72b",
            process_type="C",
        ),
        ProcessSnapshot(
            gpu_index=1,
            pid=512402,
            name="python",
            user="bob",
            gpu_memory_bytes=3000 * 1024**2,
            gpu_util_percent=0,
            cpu_percent=1.2,
            host_memory_bytes=1 * 1024**3,
            memory_util_percent=0.9,
            runtime_seconds=600,
            command="python sampler.py",
            process_type="C",
        ),
        ProcessSnapshot(
            gpu_index=2,
            pid=99001,
            name="metaxctl",
            user="root",
            gpu_memory_bytes=128 * 1024**2,
            cpu_percent=0.0,
            host_memory_bytes=80 * 1024**2,
            memory_util_percent=0.1,
            runtime_seconds=86400 * 7,
            command="/opt/mxdriver/bin/metaxctl serve",
            process_type="C",
        ),
    ]
    return FrameSnapshot(devices=devices, processes=processes, backend="pymxsml")


def build_idle_frame() -> FrameSnapshot:
    devices = [
        DeviceSnapshot(
            index=i,
            name="MetaX C500",
            bdf=f"0000:{0x1a + i * 2:02x}:00.0",
            temperature_c=37 + i,
            power_w=72.0 + i * 1.5,
            power_limit_w=350,
            fan_percent=30,
            gpu_util_percent=val,
            memory_util_percent=mem,
            memory_bandwidth_util_percent=mbw,
            memory_used_bytes=int(mem / 100 * 64 * 1024**3),
            memory_total_bytes=64 * 1024**3,
            performance_state="P8",
            ecc_errors=0,
            persistence_mode="Enabled",
            driver_version="2.31.0.5",
        )
        for i, (val, mem, mbw) in enumerate([(0.0, 3.5, 0.0), (2.0, 4.2, 1.0), (0.0, 2.8, 0.0)])
    ]
    processes = [
        ProcessSnapshot(
            gpu_index=0,
            pid=99001,
            name="metaxctl",
            user="root",
            gpu_memory_bytes=128 * 1024**2,
            cpu_percent=0.1,
            host_memory_bytes=64 * 1024**2,
            memory_util_percent=0.1,
            runtime_seconds=86400 * 12,
            command="/opt/mxdriver/bin/metaxctl serve",
            process_type="C",
        ),
    ]
    return FrameSnapshot(devices=devices, processes=processes, backend="pymxsml")


def build_mixed_frame() -> FrameSnapshot:
    devices = [
        DeviceSnapshot(
            index=0,
            name="MetaX C500",
            bdf="0000:1a:00.0",
            temperature_c=72,
            power_w=312.0,
            power_limit_w=350,
            fan_percent=68,
            gpu_util_percent=94,
            memory_util_percent=88,
            memory_bandwidth_util_percent=42,
            memory_used_bytes=56 * 1024**3,
            memory_total_bytes=64 * 1024**3,
            performance_state="P0",
            ecc_errors=0,
            persistence_mode="Enabled",
            driver_version="2.31.0.5",
        ),
        DeviceSnapshot(
            index=1,
            name="MetaX C500",
            bdf="0000:3d:00.0",
            temperature_c=58,
            power_w=160.0,
            power_limit_w=350,
            fan_percent=42,
            gpu_util_percent=45,
            memory_util_percent=72,
            memory_bandwidth_util_percent=18,
            memory_used_bytes=46 * 1024**3,
            memory_total_bytes=64 * 1024**3,
            performance_state="P0",
            ecc_errors=0,
            persistence_mode="Enabled",
            driver_version="2.31.0.5",
        ),
        DeviceSnapshot(
            index=2,
            name="MetaX C500",
            bdf="0000:5e:00.0",
            temperature_c=41,
            power_w=78.0,
            power_limit_w=350,
            fan_percent=33,
            gpu_util_percent=4,
            memory_util_percent=6,
            memory_bandwidth_util_percent=2,
            memory_used_bytes=4 * 1024**3,
            memory_total_bytes=64 * 1024**3,
            performance_state="P8",
            ecc_errors=0,
            persistence_mode="Enabled",
            driver_version="2.31.0.5",
        ),
    ]
    processes = [
        ProcessSnapshot(
            gpu_index=0, pid=423901, name="python", user="alice",
            gpu_memory_bytes=51200 * 1024**2, gpu_util_percent=94, cpu_percent=298.4,
            host_memory_bytes=22 * 1024**3, memory_util_percent=17.2,
            runtime_seconds=6 * 3600 + 14 * 60,
            command="python train.py --config configs/llama3-70b.yaml --bf16",
            process_type="C",
        ),
        ProcessSnapshot(
            gpu_index=1, pid=512377, name="python", user="bob",
            gpu_memory_bytes=42000 * 1024**2, gpu_util_percent=45, cpu_percent=128.0,
            host_memory_bytes=14 * 1024**3, memory_util_percent=10.9,
            runtime_seconds=86400 + 3 * 3600,
            command="python -m vllm.entrypoints.api_server --model qwen2-72b",
            process_type="C",
        ),
        ProcessSnapshot(
            gpu_index=2, pid=99001, name="metaxctl", user="root",
            gpu_memory_bytes=128 * 1024**2, cpu_percent=0.0,
            host_memory_bytes=80 * 1024**2, memory_util_percent=0.1,
            runtime_seconds=86400 * 7,
            command="/opt/mxdriver/bin/metaxctl serve",
            process_type="C",
        ),
    ]
    return FrameSnapshot(devices=devices, processes=processes, backend="pymxsml")


def build_heavy_frame() -> FrameSnapshot:
    devices = [
        DeviceSnapshot(
            index=i,
            name="MetaX C500",
            bdf=f"0000:{0x1a + i * 2:02x}:00.0",
            temperature_c=78 + i,
            power_w=320.0 + i,
            power_limit_w=350,
            fan_percent=85,
            gpu_util_percent=96.0 - i * 1.5,
            memory_util_percent=93.0 - i * 0.8,
            memory_bandwidth_util_percent=88.0 - i * 1.2,
            memory_used_bytes=int((93 - i * 0.8) / 100 * 64 * 1024**3),
            memory_total_bytes=64 * 1024**3,
            performance_state="P0",
            ecc_errors=0,
            persistence_mode="Enabled",
            driver_version="2.31.0.5",
        )
        for i in range(4)
    ]
    processes = [
        ProcessSnapshot(
            gpu_index=i,
            pid=410000 + i * 17,
            name="python",
            user=["alice", "bob", "carol", "dave"][i],
            gpu_memory_bytes=int((92 - i * 0.5) / 100 * 60 * 1024**3),
            gpu_util_percent=96.0 - i * 1.5,
            cpu_percent=320.0 - i * 12,
            host_memory_bytes=(20 - i) * 1024**3,
            memory_util_percent=15.0 + i,
            runtime_seconds=4 * 3600 + i * 600,
            command=f"python -m train --rank {i} --config configs/llama3.yaml",
            process_type="C",
        )
        for i in range(4)
    ]
    return FrameSnapshot(devices=devices, processes=processes, backend="pymxsml")


def build_many_frame() -> FrameSnapshot:
    devices: list[DeviceSnapshot] = []
    utils = [88, 74, 0, 92, 12, 67, 81, 55, 19, 99, 24, 41, 73, 60, 8, 35]
    for i in range(16):
        util = utils[i]
        mem_pct = min(99.0, max(2.0, util * 0.85 + (i * 1.7)))
        devices.append(
            DeviceSnapshot(
                index=i,
                name="MetaX C500",
                bdf=f"0000:{0x1a + i * 2:02x}:00.0",
                temperature_c=38 + (util // 4),
                power_w=70 + util * 2.4,
                power_limit_w=350,
                fan_percent=30 + (util // 5),
                gpu_util_percent=float(util),
                memory_util_percent=mem_pct,
                memory_used_bytes=int(mem_pct / 100 * 64 * 1024**3),
                memory_total_bytes=64 * 1024**3,
                performance_state="P0" if util > 0 else "P8",
                ecc_errors=0,
                persistence_mode="Enabled",
                driver_version="2.31.0.5",
            )
        )
    processes: list[ProcessSnapshot] = []
    users = ["alice", "bob", "carol", "dave"]
    for i, util in enumerate(utils[:12]):
        if util <= 0:
            continue
        processes.append(
            ProcessSnapshot(
                gpu_index=i,
                pid=410000 + i * 17,
                name="python",
                user=users[i % len(users)],
                gpu_memory_bytes=int(util / 100 * 60 * 1024**3),
                gpu_util_percent=float(util),
                cpu_percent=20 + (i * 19) % 200,
                host_memory_bytes=int((2 + i % 5) * 1024**3),
                memory_util_percent=2.0 + (i % 5),
                runtime_seconds=600 + i * 1234,
                command=f"python -m train --rank {i} --config configs/llama3.yaml",
                process_type="C",
            )
        )
    return FrameSnapshot(devices=devices, processes=processes, backend="pymxsml")


def parse_segments(line: str) -> Iterable[tuple[str, list[str]]]:
    cursor = 0
    state: list[str] = []
    for match in ANSI_PATTERN.finditer(line):
        if match.start() > cursor:
            yield line[cursor : match.start()], list(state)
        codes = match.group(1).split(";")
        for code in codes:
            if code in {"", "0"}:
                state = []
            else:
                state.append(code)
        cursor = match.end()
    if cursor < len(line):
        yield line[cursor:], list(state)


def render_to_png(output: str, theme_name: str, target: Path) -> None:
    theme = THEMES[theme_name]
    font_path = "/System/Library/Fonts/Menlo.ttc"
    font_size = 18
    font = ImageFont.truetype(font_path, font_size)
    bold_font = ImageFont.truetype(font_path, font_size, index=1)
    char_width = font.getbbox("M")[2]
    line_height = font_size + 6

    lines = output.split("\n")
    max_cols = max((len(ANSI_PATTERN.sub("", line)) for line in lines), default=80)
    width = char_width * (max_cols + 2)
    height = line_height * (len(lines) + 2)

    image = Image.new("RGB", (width, height), theme["bg"])
    draw = ImageDraw.Draw(image)

    for row, raw_line in enumerate(lines):
        x = char_width
        y = line_height * (row + 1)
        for text, state in parse_segments(raw_line):
            bold = "1" in state
            reverse = "7" in state
            fg_code = next((c for c in state if c in theme), "37")
            fg = theme.get(fg_code, theme["fg"])
            bg = theme["bg"]
            if "2" in state:
                fg = _dim(fg, theme["bg"])
            if reverse:
                fg, bg = bg, fg
                if bg == theme["bg"]:
                    bg = theme["selection_bg"]
            text_width = char_width * len(text)
            if bg != theme["bg"]:
                draw.rectangle((x, y, x + text_width, y + line_height), fill=bg)
            chosen_font = bold_font if bold else font
            draw.text((x, y), text, fill=fg, font=chosen_font)
            x += text_width
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target)


def _dim(color: tuple[int, int, int], bg: tuple[int, int, int]) -> tuple[int, int, int]:
    return tuple(int(c * 0.6 + b * 0.4) for c, b in zip(color, bg))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--theme", choices=list(THEMES), default="dark")
    parser.add_argument("--output", default="assets/mxtop-preview.png", type=Path)
    parser.add_argument("--width", type=int, default=140)
    parser.add_argument(
        "--scenario",
        choices=["small", "many", "idle", "mixed", "heavy"],
        default="small",
    )
    args = parser.parse_args()

    builders = {
        "small": build_frame,
        "many": build_many_frame,
        "idle": build_idle_frame,
        "mixed": build_mixed_frame,
        "heavy": build_heavy_frame,
    }
    frame = builders[args.scenario]()
    if args.scenario == "many":
        from mxtop.ui.panels import render_main_screen
        from mxtop.ui.state import UiState, LayoutMode
        from mxtop.rendering import _colorize_line
        screen = render_main_screen(frame, UiState(layout=LayoutMode.AUTO), width=args.width, height=50)
        rendered = "\n".join(_colorize_line(row, line) for row, line in enumerate(screen.lines))
    else:
        rendered = render_once(frame, use_color=True, width=args.width)
    target = args.output
    if not target.is_absolute():
        target = PROJECT_ROOT / target
    render_to_png(rendered, args.theme, target)
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
