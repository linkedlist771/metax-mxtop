# mxtop Output Gallery

Every image is generated from fixed-time deterministic synthetic MetaX-shaped telemetry. PNG metadata records a digest of the exact rendered text.

Re-render with ``uv run --locked --with pillow==11.3.0 --with psutil python scripts/render_gallery.py``; verify freshness with the same command plus ``--check``.

## Snapshot modes

| Command | Preview |
| --- | --- |
| `mxtop --once`<br><sub>Default colored snapshot with mixed MetaX load.</sub> | ![once-default](assets/gallery/once-default.png) |
| `mxtop --once --no-color`<br><sub>Uncolored Unicode snapshot for logs and pipes.</sub> | ![once-no-color](assets/gallery/once-no-color.png) |
| `mxtop --json`<br><sub>Complete valid JSON snapshot from a one-GPU fixture.</sub> | ![json-default](assets/gallery/json-default.png) |
| `mxtop --json`<br><sub>Nonfinite backend values normalized to strict JSON nulls.</sub> | ![json-nonfinite](assets/gallery/json-nonfinite.png) |

## Color and palette

| Command | Preview |
| --- | --- |
| `mxtop --once --colorful`<br><sub>Spectrum-like utilization bars.</sub> | ![once-colorful](assets/gallery/once-colorful.png) |
| `mxtop --once --light`<br><sub>Snapshot rendered for a light terminal theme.</sub> | ![once-light](assets/gallery/once-light.png) |

## Interactive layouts

| Command | Preview |
| --- | --- |
| `mxtop --monitor full`<br><sub>Representative interactive full-mode frame.</sub> | ![monitor-full](assets/gallery/monitor-full.png) |
| `mxtop --monitor compact`<br><sub>Representative interactive compact-mode frame.</sub> | ![monitor-compact](assets/gallery/monitor-compact.png) |

## Device and owner filters

| Command | Preview |
| --- | --- |
| `mxtop --once --only 0 2`<br><sub>Only GPU indices 0 and 2 and their processes.</sub> | ![once-only](assets/gallery/once-only.png) |
| `mxtop --once --user alice`<br><sub>Only processes owned by alice.</sub> | ![once-user](assets/gallery/once-user.png) |
| `mxtop --once --pid 423901 512377`<br><sub>Only the selected process IDs.</sub> | ![once-pid](assets/gallery/once-pid.png) |

## Process-type filters

| Command | Preview |
| --- | --- |
| `mxtop --once --compute`<br><sub>Processes with a compute context, including mixed C+G.</sub> | ![once-compute](assets/gallery/once-compute.png) |
| `mxtop --once --only-compute`<br><sub>Processes with an exact compute-only context.</sub> | ![once-only-compute](assets/gallery/once-only-compute.png) |
| `mxtop --once --graphics`<br><sub>Processes with a graphics context, including mixed C+G.</sub> | ![once-graphics](assets/gallery/once-graphics.png) |
| `mxtop --once --only-graphics`<br><sub>Processes with an exact graphics-only context.</sub> | ![once-only-graphics](assets/gallery/once-only-graphics.png) |

## Custom intensity thresholds

| Command | Preview |
| --- | --- |
| `mxtop --once --gpu-util-thresh 30 60`<br><sub>Custom GPU intensity thresholds at 30% and 60%.</sub> | ![once-gpu-thresh](assets/gallery/once-gpu-thresh.png) |
| `mxtop --once --mem-util-thresh 20 50`<br><sub>Custom memory intensity thresholds at 20% and 50%.</sub> | ![once-mem-thresh](assets/gallery/once-mem-thresh.png) |

## Load profiles

| Command | Preview |
| --- | --- |
| `mxtop --once`<br><sub>Three-GPU idle fixture.</sub> | ![once-idle](assets/gallery/once-idle.png) |
| `mxtop --once`<br><sub>Single-GPU saturation and maximum-value fixture.</sub> | ![once-single-heavy](assets/gallery/once-single-heavy.png) |
| `mxtop --once`<br><sub>Four-GPU saturation fixture.</sub> | ![once-heavy](assets/gallery/once-heavy.png) |

## Multi-GPU fixtures

| Command | Preview |
| --- | --- |
| `mxtop --once`<br><sub>Eight-GPU mixed-load fixture at 170 columns.</sub> | ![once-many-8](assets/gallery/once-many-8.png) |
| `mxtop --once`<br><sub>Sixteen-GPU mixed-load fixture at 180 columns.</sub> | ![once-many-16](assets/gallery/once-many-16.png) |
| `mxtop --once`<br><sub>Adaptive 32-GPU fleet fixture at 180 columns.</sub> | ![once-many-32](assets/gallery/once-many-32.png) |
| `mxtop --once`<br><sub>Adaptive 64-GPU fleet fixture at 180 columns.</sub> | ![once-many-64](assets/gallery/once-many-64.png) |

## Missing telemetry

| Command | Preview |
| --- | --- |
| `mxtop --once`<br><sub>Unavailable backend fields rendered as N/A.</sub> | ![once-missing](assets/gallery/once-missing.png) |
