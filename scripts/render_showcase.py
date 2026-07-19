"""Generate reproducible fixed-viewport TUI and command-output showcases."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(SCRIPT_DIR))

from mxtop.rendering import (  # noqa: E402
    colorize_screen,
    render_once,
    reset_intensity_thresholds,
    set_render_style,
)
from mxtop import rendering as ansi  # noqa: E402
from mxtop._compat import DATACLASS_SLOTS  # noqa: E402
from mxtop.ui import screens as screen_views  # noqa: E402
from mxtop.ui.panels import render_main_screen  # noqa: E402
from mxtop.ui.screens import (  # noqa: E402
    HostProcessInfo,
    ProcessMetricsHistory,
    build_process_tree,
    render_environment_screen,
    render_help_screen,
    render_metrics_screen,
    render_signal_dialog,
    render_tree_screen,
)
from mxtop.ui.state import LayoutMode, UiState  # noqa: E402
from generate_preview import asset_is_fresh, render_to_png  # noqa: E402
from synthetic_fixtures import (  # noqa: E402
    FRAME_BUILDERS,
    HOST_NAME,
    HOST_USER,
    prepare_render,
    utc_timezone,
)

SHOWCASE_DIR = PROJECT_ROOT / "assets" / "showcase"


@dataclass(frozen=True, **DATACLASS_SLOTS)
class ShowcaseSpec:
    slug: str
    description: str
    scenario: str
    kind: str
    width: int
    height: int | None = None
    color: bool = True
    layout: LayoutMode = LayoutMode.AUTO
    command: str = ""


SHOWCASE_SPECS = (
    ShowcaseSpec("tui-092x028-idle-3gpu", "92x28 viewport, 3 idle GPUs", "idle", "tui", 92, 28),
    ShowcaseSpec("tui-122x036-mixed-3gpu", "122x36 viewport, 3 mixed-load GPUs", "small", "tui", 122, 36),
    ShowcaseSpec("tui-142x036-heavy-4gpu", "142x36 viewport, 4 heavily loaded GPUs", "heavy", "tui", 142, 36),
    ShowcaseSpec("tui-172x044-many-16gpu", "172x44 viewport, 16 mixed-load GPUs", "many", "tui", 172, 44),
    ShowcaseSpec("tui-180x044-many-64gpu", "180x44 viewport, 64-GPU fleet overview", "sixty-four", "tui", 180, 44),
    ShowcaseSpec("screen-help-118x035", "Help screen with nvitop-compatible key groups", "small", "help", 118, 35),
    ShowcaseSpec("screen-environment-120x018", "Process environment with empty, long, and multiline values", "small", "environment", 120, 18),
    ShowcaseSpec("screen-environment-error-100x008", "Stable process-environment permission error", "small", "environment-error", 100, 8),
    ShowcaseSpec("screen-tree-140x018", "GPU process tree with host ancestors and direct children", "small", "tree", 140, 18),
    ShowcaseSpec("screen-tree-empty-100x008", "Empty GPU process-tree state", "single-idle", "tree-empty", 100, 8),
    ShowcaseSpec("screen-metrics-120x030", "Rolling per-process CPU, memory, and GPU metrics", "small", "metrics", 120, 30),
    ShowcaseSpec("dialog-signal-120x034", "Multi-process signal confirmation with all choices", "small", "signal", 120, 34),
    ShowcaseSpec("output-once-color-140-mixed", "Colored one-shot mixed-load output", "mixed", "once", 140, command="mxtop --once"),
    ShowcaseSpec("output-once-plain-110-idle", "Uncolored one-shot idle output", "idle", "once", 110, color=False, command="mxtop --once --no-color"),
    ShowcaseSpec("output-json-110-small", "Complete JSON output from a one-GPU fixture", "single-idle", "json", 110, color=False, command="mxtop --json"),
)

SECONDARY_KINDS = {
    "help",
    "environment",
    "environment-error",
    "tree",
    "tree-empty",
    "metrics",
    "signal",
}


def _colorize(frame, lines: list[str]) -> str:
    return "\n".join(colorize_screen(frame, lines))


def _tui_lines(spec: ShowcaseSpec, frame) -> list[str]:
    assert spec.height is not None
    screen = render_main_screen(
        frame,
        UiState(layout=spec.layout),
        width=spec.width,
        height=spec.height,
    )
    return screen.lines


def _style(text: str, *codes: str) -> str:
    return text if not text else "".join(codes) + text + ansi.RESET


def _styled_spans(line: str, spans: list[tuple[int, int, tuple[str, ...]]]) -> str:
    output: list[str] = []
    cursor = 0
    for start, end, codes in sorted(spans):
        start = max(cursor, min(len(line), start))
        end = max(start, min(len(line), end))
        output.append(line[cursor:start])
        output.append(_style(line[start:end], *codes))
        cursor = end
    output.append(line[cursor:])
    return "".join(output)


_HELP_SIGNAL_ACTIONS = (
    "interrupt selected process",
    "kill selected process",
    "terminate selected process",
)


def _help_key_colors(line: str) -> tuple[str | None, str | None]:
    """Mirror mxtop.tui._help_line_colors for the ANSI showcase renderer."""

    if any(action in line for action in _HELP_SIGNAL_ACTIONS):
        return ansi.FG_CYAN, ansi.FG_RED
    if "select sort column" in line:
        return ansi.FG_MAGENTA, ansi.FG_MAGENTA
    if "sort by" in line:
        return ansi.FG_BLUE, ansi.FG_BLUE
    if "Wheel:" in line:
        return ansi.FG_BLUE, ansi.FG_BLUE
    if "show this help screen" in line or line.rstrip().endswith(": quit"):
        return ansi.FG_GREEN, ansi.FG_GREEN
    if "pause/resume" in line:
        return ansi.FG_GREEN, None
    if "tag/untag" in line or "clear process selection" in line:
        return ansi.FG_CYAN, ansi.FG_YELLOW
    if "filter processes" in line:
        return ansi.FG_CYAN, ansi.FG_YELLOW
    if (
        "show process environment" in line
        or "toggle tree-view" in line
        or "show process metrics" in line
    ):
        return ansi.FG_CYAN, ansi.FG_GREEN
    if "scroll" in line or "select the" in line:
        return ansi.FG_CYAN, None
    return None, None


def _colorize_help(lines: list[str]) -> str:
    output = list(lines)
    for row, line in enumerate(output):
        stripped = line.rstrip()
        if (
            (stripped.startswith("mxtop ") and "(C)" in stripped)
            or stripped.startswith("Released under")
            or stripped == "Press any key to return."
        ):
            output[row] = _style(line, ansi.BOLD, ansi.FG_CYAN)
            continue
        if stripped.startswith("GPU Process Type:"):
            spans = [(0, 17, (ansi.BOLD,))]
            for marker in ("C:", "G:", "X/"):
                start = line.find(marker)
                if start >= 0:
                    spans.append((start, start + 1, (ansi.BOLD, ansi.FG_MAGENTA)))
            output[row] = _styled_spans(line, spans)
            continue
        if stripped.startswith("Device coloring rules"):
            output[row] = _style(line, ansi.BOLD)
            continue
        if "GPU utilization:" in line or "GPU-MEM percent:" in line:
            spans = []
            for word, color in (
                ("light", ansi.FG_GREEN),
                ("moderate", ansi.FG_YELLOW),
                ("heavy", ansi.FG_RED),
            ):
                start = line.find(word)
                if start >= 0:
                    spans.append((start, start + len(word), (ansi.BOLD, color)))
            output[row] = _styled_spans(line, spans)
            continue
        left, right = _help_key_colors(line)
        spans = []
        if left is not None and line[:12].strip():
            spans.append((0, 12, (ansi.BOLD, left)))
        if right is not None:
            spans.append((39, 52, (ansi.BOLD, right)))
        if spans:
            output[row] = _styled_spans(line, spans)
    return "\n".join(output)


def _colorize_environment(lines: list[str], *, selected: int | None, error: bool) -> str:
    output: list[str] = []
    for row, line in enumerate(lines):
        if row == selected:
            output.append(_style(line, ansi.REVERSE, ansi.FG_CYAN))
        elif row == 0:
            end = line.find("): ")
            end = len(line) if end < 0 else end + 2
            output.append(_style(line[:end], ansi.BOLD, ansi.FG_CYAN) + line[end:])
        elif row == 1:
            output.append(_style(line, ansi.BOLD, ansi.FG_GREEN))
        elif error and row == 2:
            output.append(_style(line, ansi.BOLD, ansi.REVERSE, ansi.FG_CYAN))
        elif "=" in line:
            key, value = line.split("=", 1)
            output.append(
                _style(key, ansi.BOLD, ansi.FG_BLUE)
                + _style("=", ansi.FG_MAGENTA)
                + value
            )
        else:
            output.append(line)
    return "\n".join(output)


def _colorize_tree(lines: list[str], *, selected: int | None) -> str:
    output: list[str] = []
    for row, line in enumerate(lines):
        if row == 0:
            output.append(_style(line, ansi.BOLD, ansi.REVERSE, ansi.FG_CYAN))
            continue
        if row == selected:
            output.append(_style(line, ansi.BOLD, ansi.REVERSE, ansi.FG_GREEN))
            continue
        user_match = re.match(r"\s*\d+\s+(\S+)", line)
        codes = (ansi.DIM, ansi.FG_WHITE) if user_match and user_match.group(1) != HOST_USER else ()
        branch = re.search(r"(?:│  |   )*(?:├─ |└─ )", line)
        if branch is None:
            output.append(_style(line, *codes))
        else:
            output.append(
                _style(line[: branch.start()], *codes)
                + _style(line[branch.start() : branch.end()], ansi.BOLD, ansi.FG_GREEN)
                + _style(line[branch.end() :], *codes)
            )
    return "\n".join(output)


_BRAILLE_RE = re.compile(r"[\u2800-\u28ff]+")


def _colorize_metric_cell(text: str, graph_color: str) -> str:
    output: list[str] = []
    cursor = 0
    for match in _BRAILLE_RE.finditer(text):
        output.append(_style(text[cursor : match.start()], ansi.BOLD, ansi.FG_WHITE))
        output.append(_style(match.group(), graph_color))
        cursor = match.end()
    output.append(_style(text[cursor:], ansi.BOLD, ansi.FG_WHITE))
    value = "".join(output)
    return re.sub(
        r"(╴(?:\d+(?:\.\d+)?%|\d+s))",
        lambda match: _style(match.group(1), ansi.DIM, ansi.FG_WHITE),
        value,
    )


def _colorize_metrics(lines: list[str]) -> str:
    output = list(lines)
    graph_top = next((row for row, line in enumerate(lines) if line.startswith("╞") and "╤" in line), -1)
    graph_middle = next(
        (row for row, line in enumerate(lines) if row > graph_top and line.startswith("├") and "┼" in line),
        -1,
    )
    graph_bottom = next(
        (row for row, line in enumerate(lines) if row > graph_middle and line.startswith("╘") and "╧" in line),
        len(lines),
    )
    split_column = lines[graph_top].find("╤") if graph_top >= 0 else -1
    for row, line in enumerate(lines):
        if row == 1:
            output[row] = ansi._colorize_process_title(line)
        elif row == 2:
            output[row] = (
                _style(line[0], ansi.DIM, ansi.FG_WHITE)
                + _style(line[1:-1], ansi.BOLD, ansi.REVERSE, ansi.FG_CYAN)
                + _style(line[-1], ansi.DIM, ansi.FG_WHITE)
            )
        elif row == 4:
            output[row] = _style(line, ansi.BOLD, ansi.FG_WHITE)
        elif row == graph_middle:
            output[row] = _style(line, ansi.DIM, ansi.FG_WHITE)
        elif graph_top < row < graph_middle or graph_middle < row < graph_bottom:
            split = split_column
            if split < 0:
                continue
            left_color = ansi.FG_CYAN if row < graph_middle else ansi.FG_MAGENTA
            right_color = ansi.FG_RED if row < graph_middle else ansi.FG_YELLOW
            output[row] = (
                _style(line[0], ansi.DIM, ansi.FG_WHITE)
                + _colorize_metric_cell(line[1:split], left_color)
                + _style(line[split], ansi.DIM, ansi.FG_WHITE)
                + _colorize_metric_cell(line[split + 1 : -1], right_color)
                + _style(line[-1], ansi.DIM, ansi.FG_WHITE)
            )
    return "\n".join(output)


def _colorize_signal_canvas(
    canvas: list[str],
    dialog: list[str],
    width: int,
    height: int,
) -> str:
    dialog_width = max(map(len, dialog), default=0)
    top = max(0, (height - len(dialog)) // 2)
    left = max(0, (width - dialog_width) // 2)
    output: list[str] = []
    for row, line in enumerate(canvas):
        dialog_row = row - top
        if not (0 <= dialog_row < len(dialog)):
            output.append(_style(line, ansi.DIM, ansi.FG_WHITE))
            continue
        dialog_line = dialog[dialog_row]
        end = left + len(dialog_line)
        middle = dialog_line
        selected = middle.find("[SIGTERM]")
        if selected >= 0:
            middle = (
                middle[:selected]
                + _style("[SIGTERM]", ansi.BOLD, ansi.REVERSE, ansi.FG_CYAN)
                + middle[selected + len("[SIGTERM]") :]
            )
        output.append(
            _style(line[:left], ansi.DIM, ansi.FG_WHITE)
            + _style(middle, ansi.BOLD, ansi.FG_WHITE)
            + _style(line[end:], ansi.DIM, ansi.FG_WHITE)
        )
    return "\n".join(output)


def _environment_variables() -> tuple[tuple[str, str], ...]:
    return (
        ("CUDA_VISIBLE_DEVICES", "0,1,2"),
        ("EMPTY_VALUE", ""),
        ("MACA_PATH", "/opt/maca:/opt/mxdriver/lib:/usr/local/lib"),
        ("MODEL_CONFIG", "name=qwen2-72b\nprecision=bf16"),
        ("MXTOP_MONITOR_MODE", "auto,colorful,dark"),
        ("PATH", "/opt/maca/bin:/usr/local/bin:/usr/bin:/bin"),
        ("RANK", "0"),
        ("WORLD_SIZE", "64"),
    )


def _tree_entries(frame):
    processes = frame.processes
    return build_process_tree(
        frame,
        (
            HostProcessInfo(1, 0, "root", "/sbin/init", 1.0, 1, 0.1, 0.1, 12 * 86400),
            HostProcessInfo(420000, 1, "alice", "bash -l", 100.0, 2, 0.4, 0.2, 6 * 3600),
            HostProcessInfo(423901, 420000, "alice", processes[0].command or "python", processes[0].create_time, 96, 312.4, 14.2, processes[0].runtime_seconds),
            HostProcessInfo(423908, 423901, "alice", processes[1].command or "python", processes[1].create_time, 18, 42.1, 2.4, processes[1].runtime_seconds),
            HostProcessInfo(510000, 1, "bob", "sshd: bob@pts/4", 200.0, 1, 0.1, 0.1, 3 * 86400),
            HostProcessInfo(512377, 510000, "bob", processes[2].command or "python", processes[2].create_time, 64, 215.0, 9.8, processes[2].runtime_seconds),
            HostProcessInfo(512402, 512377, "bob", processes[3].command or "python", processes[3].create_time, 8, 1.2, 0.9, processes[3].runtime_seconds),
            HostProcessInfo(99001, 1, "root", processes[4].command or "metaxctl", processes[4].create_time, 4, 0.0, 0.1, processes[4].runtime_seconds),
        ),
    )


def _metrics_history(process) -> ProcessMetricsHistory:
    history = ProcessMetricsHistory()
    history.selection_key = process.selection_key
    history.host_memory_total = 128 * 1024**3
    history.gpu_memory_total = 76 * 1024**3
    for step in range(96):
        history.cpu.append(190.0 + 125.0 * math.sin(step / 8.0))
        history.host_memory.append(11.5 + 2.7 * math.sin(step / 13.0))
        history.gpu_memory.append(76.0 + 16.0 * math.sin(step / 17.0))
        history.gpu_utilization.append(62.0 + 30.0 * math.sin(step / 9.0))
    return history


def _overlay_dialog(lines: list[str], dialog: list[str], width: int, height: int) -> list[str]:
    canvas = [line[:width].ljust(width) for line in lines[:height]]
    canvas.extend(" " * width for _ in range(height - len(canvas)))
    dialog_width = max(map(len, dialog), default=0)
    top = max(0, (height - len(dialog)) // 2)
    left = max(0, (width - dialog_width) // 2)
    for row, line in enumerate(dialog):
        target = top + row
        if target >= height:
            break
        clipped = line[: max(0, width - left)]
        canvas[target] = canvas[target][:left] + clipped + canvas[target][left + len(clipped) :]
    return canvas


def _secondary_text(spec: ShowcaseSpec, frame) -> str:
    assert spec.height is not None
    process = frame.processes[0] if frame.processes else None
    if spec.kind == "help":
        return _colorize_help(render_help_screen(spec.width, spec.height).lines)
    if spec.kind in {"environment", "environment-error"}:
        view = render_environment_screen(
            process,
            _environment_variables() if spec.kind == "environment" else (),
            width=spec.width,
            height=spec.height,
            selected_index=3,
            error="Permission denied" if spec.kind == "environment-error" else None,
        )
        return _colorize_environment(
            view.lines,
            selected=5 if spec.kind == "environment" else None,
            error=spec.kind == "environment-error",
        )
    if spec.kind in {"tree", "tree-empty"}:
        entries = _tree_entries(frame) if spec.kind == "tree" else ()
        view = render_tree_screen(
            entries,
            width=spec.width,
            height=spec.height,
            selected_index=2,
            actionable=bool(entries),
        )
        return _colorize_tree(view.lines, selected=3 if entries else None)
    if spec.kind == "metrics":
        assert process is not None
        old_user = screen_views.getpass.getuser
        old_hostname = screen_views.socket.gethostname
        screen_views.getpass.getuser = lambda: HOST_USER
        screen_views.socket.gethostname = lambda: HOST_NAME
        try:
            view = render_metrics_screen(
                frame,
                process,
                _metrics_history(process),
                width=spec.width,
                height=spec.height,
            )
        finally:
            screen_views.getpass.getuser = old_user
            screen_views.socket.gethostname = old_hostname
        return _colorize_metrics(view.lines)
    if spec.kind == "signal":
        dialog = render_signal_dialog(
            ((423901, "alice"), (423908, "alice"), (512377, "bob"), (512402, "bob")),
            width=spec.width,
            signal_name="SIGTERM",
            current_option=0,
        )
        canvas = _overlay_dialog(_tui_lines(spec, frame), dialog, spec.width, spec.height)
        return _colorize_signal_canvas(canvas, dialog, spec.width, spec.height)
    raise ValueError(f"unknown secondary showcase kind: {spec.kind}")


def render_showcase_text(spec: ShowcaseSpec) -> str:
    reset_intensity_thresholds()
    set_render_style(light=False, colorful=False)
    prepare_render()
    with utc_timezone():
        if spec.kind == "tui":
            frame = FRAME_BUILDERS[spec.scenario]()
            lines = _tui_lines(spec, frame)
            return _colorize(frame, lines) if spec.color else "\n".join(lines)
        frame = FRAME_BUILDERS[spec.scenario]()
        if spec.kind in SECONDARY_KINDS:
            return _secondary_text(spec, frame)
        if spec.kind == "json":
            body = json.dumps(frame.to_dict(), indent=2, sort_keys=True, allow_nan=False)
        else:
            body = render_once(frame, use_color=spec.color, width=spec.width)
        prompt = f"$ {spec.command}"
        if spec.color:
            prompt = f"\x1b[1;36m{prompt}\x1b[0m"
        return f"{prompt}\n{body}"


def render_showcase_asset(spec: ShowcaseSpec, target: Path, *, check: bool = False) -> bool:
    output = render_showcase_text(spec)
    if check:
        return asset_is_fresh(target, output, "dark")
    render_to_png(output, "dark", target, source_name=f"showcase:{spec.slug}")
    return True


def render_all(*, check: bool = False, output_dir: Path = SHOWCASE_DIR) -> list[str]:
    stale: list[str] = []
    if not check:
        output_dir.mkdir(parents=True, exist_ok=True)
    for spec in SHOWCASE_SPECS:
        target = output_dir / f"{spec.slug}.png"
        if not render_showcase_asset(spec, target, check=check):
            stale.append(spec.slug)
        elif not check:
            print(f"wrote {target.relative_to(PROJECT_ROOT) if target.is_relative_to(PROJECT_ROOT) else target}")
    return stale


def showcase_markdown() -> str:
    tui_specs = [spec for spec in SHOWCASE_SPECS if spec.kind == "tui"]
    secondary_specs = [spec for spec in SHOWCASE_SPECS if spec.kind in SECONDARY_KINDS]
    output_specs = [
        spec for spec in SHOWCASE_SPECS if spec.kind not in SECONDARY_KINDS | {"tui"}
    ]
    sections = [
        "# mxtop Preview Showcase\n",
        "These screenshots use the same fixed-time deterministic synthetic MetaX-shaped telemetry as the CLI gallery. Each PNG embeds a digest of its rendered source text.\n",
        "Re-render with ``uv run --locked --with pillow==11.3.0 --with psutil python scripts/render_showcase.py``; add ``--check`` to verify freshness.\n",
        "## Interactive TUI\n",
        "| Scenario | Preview |",
        "| --- | --- |",
    ]
    sections.extend(
        f"| {spec.description} | ![{spec.description}](assets/showcase/{spec.slug}.png) |"
        for spec in tui_specs
    )
    sections.extend(("", "## Secondary Screens\n", "| Scenario | Preview |", "| --- | --- |"))
    sections.extend(
        f"| {spec.description} | ![{spec.description}](assets/showcase/{spec.slug}.png) |"
        for spec in secondary_specs
    )
    sections.extend(("", "## Command Output\n", "| Scenario | Preview |", "| --- | --- |"))
    sections.extend(
        f"| `{spec.command}`<br><sub>{spec.description}</sub> | ![{spec.description}](assets/showcase/{spec.slug}.png) |"
        for spec in output_specs
    )
    return "\n".join(sections).rstrip() + "\n"


def write_showcase_markdown() -> None:
    target = PROJECT_ROOT / "SHOWCASE.md"
    target.write_text(showcase_markdown(), encoding="utf-8")
    print(f"wrote {target.relative_to(PROJECT_ROOT)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify PNG source digests without writing")
    parser.add_argument("--output-dir", type=Path, default=SHOWCASE_DIR)
    args = parser.parse_args()
    output_dir = args.output_dir if args.output_dir.is_absolute() else PROJECT_ROOT / args.output_dir
    stale = render_all(check=args.check, output_dir=output_dir)
    if args.check:
        markdown_fresh = (PROJECT_ROOT / "SHOWCASE.md").read_text(encoding="utf-8") == showcase_markdown()
        if not markdown_fresh:
            stale.append("SHOWCASE.md")
        if stale:
            print("stale showcase artifacts: " + ", ".join(stale), file=sys.stderr)
            return 1
        return 0
    write_showcase_markdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
