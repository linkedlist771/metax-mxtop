# mxtop Preview Showcase

These screenshots use the same fixed-time canonical MetaX telemetry as the CLI gallery. Each PNG embeds a digest of its rendered source text.

Re-render with ``uv run --locked --with pillow --with psutil python scripts/render_showcase.py``; add ``--check`` to verify freshness.

## Interactive TUI

| Scenario | Preview |
| --- | --- |
| 92x28 viewport, 3 idle GPUs | ![92x28 viewport, 3 idle GPUs](assets/showcase/tui-092x028-idle-3gpu.png) |
| 122x36 viewport, 3 mixed-load GPUs | ![122x36 viewport, 3 mixed-load GPUs](assets/showcase/tui-122x036-mixed-3gpu.png) |
| 142x36 viewport, 4 heavily loaded GPUs | ![142x36 viewport, 4 heavily loaded GPUs](assets/showcase/tui-142x036-heavy-4gpu.png) |
| 172x44 viewport, 16 mixed-load GPUs | ![172x44 viewport, 16 mixed-load GPUs](assets/showcase/tui-172x044-many-16gpu.png) |
| 180x44 viewport, 64-GPU fleet overview | ![180x44 viewport, 64-GPU fleet overview](assets/showcase/tui-180x044-many-64gpu.png) |

## Command Output

| Scenario | Preview |
| --- | --- |
| `mxtop --once`<br><sub>Colored one-shot mixed-load output</sub> | ![Colored one-shot mixed-load output](assets/showcase/output-once-color-140-mixed.png) |
| `mxtop --once --no-color`<br><sub>Uncolored one-shot idle output</sub> | ![Uncolored one-shot idle output](assets/showcase/output-once-plain-110-idle.png) |
| `mxtop --json`<br><sub>Complete JSON output from a one-GPU fixture</sub> | ![Complete JSON output from a one-GPU fixture](assets/showcase/output-json-110-small.png) |
