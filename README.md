# mxtop

`mxtop` is an nvitop-like terminal monitor for MetaX GPUs. It shows live GPU
temperature, power, utilization, memory use, and GPU processes from MetaX
management tooling.

The PyPI distribution name is `metax-mxtop` because `mxtop` is already occupied
on public PyPI by an unrelated Apple Silicon project. The installed terminal
command is still `mxtop`.

## Install

From this checkout:

```bash
python -m pip install -e .
```

If your environment uses `uv`:

```bash
uv pip install -e .
```

## Usage

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

In the interactive UI, press `q`, `Q`, or `Esc` to exit.

## Backends

`mxtop` tries backends in this order:

1. `pymxsml`: imports an installed `pymxsml` package, or auto-loads the MetaX
   SDK wheel from `/opt/maca/share/mxsml/pymxsml-*.whl` or
   `/opt/mxn100/share/mxsml/pymxsml-*.whl`.
2. `mx-smi`: falls back to `mx-smi dmon --format csv` for device metrics and
   parses `mx-smi --show-process` for GPU process memory.

The `pymxsml` backend gives better device names and UUIDs. The `mx-smi` backend
is useful when the SDK wheel is missing or incompatible.

## Development

Run tests with:

```bash
uv run --with pytest --with psutil pytest -q
```

The package uses a `src/` layout and exposes the console script with
`[project.scripts]` in `pyproject.toml`.
