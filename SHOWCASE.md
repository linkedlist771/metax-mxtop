# mxtop Preview Showcase

The screenshots below use deterministic sample telemetry so layout, GPU count,
and output mode can be compared side by side.

## Interactive TUI

| Scenario | Preview |
| --- | --- |
| 92x28 terminal, 3 idle GPUs | ![92x28 idle 3 GPU TUI](assets/showcase/tui-092x028-idle-3gpu.png) |
| 122x36 terminal, 3 mixed-load GPUs | ![122x36 mixed 3 GPU TUI](assets/showcase/tui-122x036-mixed-3gpu.png) |
| 142x36 terminal, 4 heavy-load GPUs | ![142x36 heavy 4 GPU TUI](assets/showcase/tui-142x036-heavy-4gpu.png) |
| 172x44 terminal, 16-GPU compact layout | ![172x44 16 GPU TUI](assets/showcase/tui-172x044-many-16gpu.png) |

## Command Output

| Scenario | Preview |
| --- | --- |
| `mxtop --once`, colored mixed-load output | ![colored one-shot output](assets/showcase/output-once-color-140-mixed.png) |
| `mxtop --once --no-color`, plain idle output | ![plain one-shot output](assets/showcase/output-once-plain-110-idle.png) |
| `mxtop --json`, truncated JSON snapshot | ![JSON output](assets/showcase/output-json-110-small.png) |
