## More Usage

Open the interactive terminal dashboard on a TTY:

```bash
mxtop
mxtop --monitor compact
```

`--monitor` / `-m` accepts `auto`, `full`, or `compact`. Auto mode compacts the device, host, and process panels independently to fit the terminal. Hosts with more than 16 GPUs use an adaptive fleet grid in auto, compact, and one-shot views, including 32- and 64-GPU systems; full mode retains the detailed vertical device list.

The committed previews use fixed-time deterministic synthetic MetaX-shaped telemetry, including missing, nonfinite, and optional values. They validate presentation behavior without replacing live data collected by `pymxsml` or `mx-smi`, and their optional fields do not imply that every live backend supports those fields.

Print one text snapshot or one JSON snapshot:

```bash
mxtop --once                 # alias: -1
mxtop --json
```

Without an explicit output mode, a redirected or piped invocation also emits one text snapshot. Text color is used only on a TTY unless `--force-color` is set; `--no-color` and `NO_COLOR` disable it. Use `--no-unicode` / `--ascii` / `-U` for ASCII rendering (also selected automatically for a non-UTF-8 locale).

Force a backend:

```bash
mxtop --backend pymxsml
mxtop --backend mxsmi
```

Filter devices and processes:

```bash
mxtop --only 0 1            # alias: -o
mxtop --only-visible         # alias: -ov
mxtop --user alice bob       # alias: -u; omit names for the current user
mxtop --pid 1234 5678        # alias: -p
mxtop --compute              # alias: -c; includes mixed C+G/X contexts
mxtop --only-compute         # alias: -C; exact compute-only contexts
mxtop --graphics             # alias: -g
mxtop --only-graphics        # alias: -G; exact graphics-only contexts
```

Filters are combined and apply consistently to interactive, text, and JSON output. `--only-visible` resolves indices, UUID prefixes, or BDF prefixes from `MACA_VISIBLE_DEVICES`, falling back to `CUDA_VISIBLE_DEVICES`. A compute/graphics filter exits with a stable error when the selected backend reports processes but lacks context-type telemetry, which is common with `mx-smi`.

The interactive UI has five primary screens:

- Main: device, host-history, and process panels.
- Help: press `h` or `?`; any key returns.
- Environment: select a process and press `e`; `e`, `q`, or `Esc` returns.
- Process tree: press `t` to show GPU processes with related host ancestors and descendants; `t` or `q` returns.
- Process metrics: select a process and press `Enter`; `Enter`, `q`, or `Esc` returns.

Main navigation and display keys:

- `q` or `Q`: quit. `Esc` clears process selection and tags; it does not quit.
- `r`, `R`, `Ctrl-R`, or `F5`: refresh immediately.
- `a`, `f`, or `c`: select auto, full, or compact layout.
- Up/Down, Shift-Tab/Tab, or Alt-k/Alt-j: select the previous/next row.
- `Home` / `End`: select the first/last row.
- `Space`: tag or untag a process and advance; tagged processes become the action target set.
- `PageUp` / `PageDown` or `[` / `]`: scroll vertically; Alt-K/Alt-J are main-screen aliases.
- Left/Right or Alt-h/Alt-l: scroll horizontally; `Ctrl-A` / `^` and `Ctrl-E` / `$` jump to the edges.
- Mouse wheel: move vertically; Ctrl-wheel moves 5x, Shift-wheel moves horizontally, and a click selects a row.

Process sorting matches nvitop's bindings:

- `,` / `.`: previous/next sort column; `/`: invert the order.
- `on`, `op`, `ou`, `og`, `os`, `ob`, `oc`, `om`, `ot`: sort by GPU index, PID, user, GPU memory, GPU utilization, GPU memory bandwidth, CPU, host memory, or runtime.
- Use an uppercase second character (`oN`, `oP`, and so on) for reverse order.

The TUI can send confirmed signals to the selected process, or to all tagged processes: `T` opens `SIGTERM`, `K`/`k` opens `SIGKILL`, and `Ctrl-C`/`I` opens `SIGINT`. Ctrl-C therefore does not quit the TUI. Before sending, mxtop validates process identity against PID reuse and checks ownership unless running as root. Pass `--readonly`, or add `readonly` to `MXTOP_MONITOR_MODE`, to disable all process-changing actions.

Useful environment defaults:

- `MXTOP_MONITOR_MODE`: comma-separated layout and style tokens, for example `compact,colorful,dark,readonly`.
- `MXTOP_GPU_UTILIZATION_THRESHOLDS=LOW,HIGH` and `MXTOP_MEMORY_UTILIZATION_THRESHOLDS=LOW,HIGH`: intensity thresholds.
- `MXTOP_MXSMI_PATH`: local `mx-smi` executable override.
- `ANSI_COLORS_DISABLED`, `NO_COLOR`, and `FORCE_COLOR`: ANSI output control; explicit CLI flags take precedence, then disable variables, then `FORCE_COLOR`.

## Backends

`mxtop` tries backends in this order:

1. `pymxsml`: imports an installed `pymxsml` package, or auto-loads the MetaX
   SDK wheel from `/opt/maca/share/mxsml/pymxsml-*.whl` or
   `/opt/mxn100/share/mxsml/pymxsml-*.whl`.
2. `mx-smi`: falls back to MetaX's command line tool for device and process
   metrics.

The `mx-smi` backend resolves the executable in this order:

1. Explicit backend constructor path used by tests or integrations.
2. `MXTOP_MXSMI_PATH`.
3. `/opt/mxdriver/bin/mx-smi`.
4. `mx-smi` on `PATH`.

The backend uses read-only commands such as `mx-smi -L`, `mx-smi dmon --format csv`, and `mx-smi --show-process`. It does not run firmware update, GPU reset, persistence-mode mutation, or other administrative commands.

`pymxsml` usually provides better device names and UUIDs. `mx-smi` is useful when the SDK wheel is missing or incompatible. Some nvitop-like fields, such as compute/graphics process type, ECC state, sGPU, or MetaXLink, depend on the installed MetaX driver and management tool version; unavailable values are displayed as `N/A`.

## Development

Run tests with:

```bash
uv run --locked --with pytest --with psutil --with pillow==11.3.0 pytest -q
```

Run lint with:

```bash
uv run --locked --with ruff ruff check .
```

Preview, gallery, and showcase freshness checks use Pillow 11.3.0, the vendored Liberation Mono regular/bold fonts, and an algorithmic Braille renderer, so host fontconfig does not affect canonical output. PNG metadata fingerprints the Pillow and FreeType versions; a rasterizer change makes the assets stale. `scripts/generate_preview.py --custom-fonts --output PATH` remains available for explicitly noncanonical local-font renders.

The package uses a `src/` layout and exposes the console script with
`[project.scripts]` in `pyproject.toml`.
