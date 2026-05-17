# mxtop CLI Gallery

Each tile below shows the rendered stdout for a common ``mxtop`` invocation. All scenes use deterministic synthetic telemetry so you can compare output across flags.

Re-render this gallery with ``uv run --with pillow --with psutil python scripts/render_gallery.py``.

## Snapshot modes

| Command | Preview |
| --- | --- |
| `mxtop --once`<br><sub>Default colored snapshot (3 active GPUs, mixed load).</sub> | ![once-default](assets/gallery/once-default.png) |
| `mxtop --once --no-color`<br><sub>Plain ASCII snapshot for logs and pipes.</sub> | ![once-no-color](assets/gallery/once-no-color.png) |
| `mxtop --json`<br><sub>JSON snapshot suitable for piping into jq / Prometheus exporters.</sub> | ![json-default](assets/gallery/json-default.png) |

## Color and palette

| Command | Preview |
| --- | --- |
| `mxtop --once --colorful`<br><sub>Five-tier intensity palette (bright green / green / yellow / bright yellow / red / bright red).</sub> | ![once-colorful](assets/gallery/once-colorful.png) |
| `mxtop --once --light`<br><sub>Light terminal theme — dim foreground swapped for readability on white backgrounds.</sub> | ![once-light](assets/gallery/once-light.png) |

## Layout modes

| Command | Preview |
| --- | --- |
| `mxtop --once --monitor full`<br><sub>Full device panel — two rows per GPU with MEM/MBW/UTL/PWR bars.</sub> | ![once-full](assets/gallery/once-full.png) |
| `mxtop --once --monitor compact`<br><sub>Compact device panel — one row per GPU, no bars.</sub> | ![once-compact](assets/gallery/once-compact.png) |

## Filters

| Command | Preview |
| --- | --- |
| `mxtop --once --only 0 2`<br><sub>Filter to specific GPU indices.</sub> | ![once-only](assets/gallery/once-only.png) |
| `mxtop --once --user alice`<br><sub>Filter processes by owner.</sub> | ![once-user](assets/gallery/once-user.png) |
| `mxtop --once --pid 423901 512377`<br><sub>Filter processes by PID.</sub> | ![once-pid](assets/gallery/once-pid.png) |
| `mxtop --once --only-compute`<br><sub>Show only compute processes when the type field is available.</sub> | ![once-only-compute](assets/gallery/once-only-compute.png) |

## Custom intensity thresholds

| Command | Preview |
| --- | --- |
| `mxtop --once --gpu-util-thresh 30 60`<br><sub>Custom GPU intensity thresholds — yellow at 30%, red at 60%.</sub> | ![once-gpu-thresh](assets/gallery/once-gpu-thresh.png) |
| `mxtop --once --mem-util-thresh 20 50`<br><sub>Custom memory intensity thresholds — yellow at 20%, red at 50%.</sub> | ![once-mem-thresh](assets/gallery/once-mem-thresh.png) |

## Load profiles

| Command | Preview |
| --- | --- |
| `mxtop --once  # idle 3-GPU host`<br><sub>Idle baseline — almost everything green, P8 power state.</sub> | ![once-idle](assets/gallery/once-idle.png) |
| `mxtop --once  # heavy 4-GPU run`<br><sub>Saturation across the cluster — most bars cross the red threshold.</sub> | ![once-heavy](assets/gallery/once-heavy.png) |

## Multi-GPU layouts

| Command | Preview |
| --- | --- |
| `mxtop --once  # 8 GPUs`<br><sub>8-GPU host with mixed load — auto-layout chooses the wide device panel.</sub> | ![once-many-8](assets/gallery/once-many-8.png) |
| `mxtop --once  # 16 GPUs`<br><sub>16-GPU host with mixed load — auto layout drops into the 8+8 compact grid.</sub> | ![once-many-16](assets/gallery/once-many-16.png) |

## Edge cases

| Command | Preview |
| --- | --- |
| `mxtop --once  # backend with missing telemetry`<br><sub>Graceful N/A rendering when the backend cannot report a metric.</sub> | ![once-missing](assets/gallery/once-missing.png) |
