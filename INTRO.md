## More Usage

Open the interactive terminal dashboard:

```bash
mxtop
```

Print one text snapshot:

```bash
mxtop --once
```

Print one JSON snapshot:

```bash
mxtop --json
```

Force a backend:

```bash
mxtop --backend pymxsml
mxtop --backend mxsmi
```

Filter devices and processes:

```bash
mxtop --only 0 1
mxtop --user alice bob
mxtop --pid 1234 5678
mxtop --monitor compact
```

The interactive UI is read-only and supports these keys:

- `q`, `Q`, `Esc`, or `Ctrl-C`: quit
- `h` or `?`: toggle help
- `r`: refresh now
- `j`/`k` or arrow keys: move the selected process
- `PageUp`/`PageDown`: scroll the process table
- left/right arrows: horizontally scroll the command column
- `,` / `.`: previous/next process sort
- `/`: reverse the current process sort
- `o` then a sort key: direct sort (`g`, `m`, `u`, `c`, `h`, `t`, `p`)
- `a`, `f`, `c`: auto/full/compact layout

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
uv run --with pytest --with psutil pytest -q
```

Run lint with:

```bash
uv run --with ruff ruff check .
```

The package uses a `src/` layout and exposes the console script with
`[project.scripts]` in `pyproject.toml`.
