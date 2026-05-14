<h1 align="center">metax-mxtop</h1>

<p align="center">
  <a href="https://github.com/linkedlist771/metax-mxtop"><img src="https://img.shields.io/github/stars/linkedlist771/metax-mxtop?style=flat-square&logo=github&color=181717" alt="GitHub stars"/></a>
  <a href="https://pypi.org/project/metax-mxtop/"><img src="https://img.shields.io/pypi/v/metax-mxtop?style=flat-square&logo=pypi&logoColor=white" alt="PyPI version"/></a>
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-22c55e?style=flat-square" alt="MIT License"/></a>
  <img src="https://img.shields.io/badge/Python-3.9%2B-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.9+"/>
  <img src="https://img.shields.io/badge/Platform-Linux-FCC624?style=flat-square&logo=linux&logoColor=black" alt="Linux"/>
  <img src="https://img.shields.io/badge/GPU-MetaX-6366f1?style=flat-square" alt="MetaX GPU"/>
</p>

<p align="center">
  <code>metax-mxtop</code> is an nvitop-like terminal monitor for MetaX GPUs. Install the PyPI package, then run the <code>mxtop</code> command.
</p>

## Features

- nvitop-like terminal dashboard for MetaX GPUs.
- Read-only monitoring through MXSML/Pymxsml or `mx-smi`.
- GPU device panel with temperature, power, utilization, memory, bus id, persistence, performance state, and driver fields when available.
- Host panel with load average, CPU, memory, swap, and aggregate GPU utilization.
- Process table with GPU, PID, user, GPU memory, GPU utilization, CPU, host memory, runtime, command, selection, scrolling, and sorting.
- ANSI-colored one-shot output and curses TUI colors aligned with the nvitop-style visual hierarchy.
- Shared filters for TUI, text, and JSON output.

The monitor is intentionally read-only. It does not run firmware update, GPU reset, persistence-mode mutation, process kill, or other destructive/admin commands.

## Install

From PyPI:

```bash
pip install -U metax-mxtop
```

The installed command is:

```bash
mxtop
```

From source:

```bash
git clone https://github.com/linkedlist771/metax-mxtop.git
cd metax-mxtop
pip install -e .
```

## MetaX backend discovery

`mxtop` tries backends in this order when `--backend auto` is used:

1. Pymxsml/MXSML, when importable and usable.
2. `mx-smi`, when the CLI is available.

For the `mx-smi` backend, the executable path is resolved in this order:

1. Explicit backend path when constructed by callers.
2. `MXTOP_MXSMI_PATH` environment variable.
3. `/opt/mxdriver/bin/mx-smi`.
4. `mx-smi` from `PATH`.

Example:

```bash
MXTOP_MXSMI_PATH=/opt/mxdriver/bin/mx-smi mxtop --backend mxsmi
```

## Usage

Interactive dashboard:

```bash
mxtop
```

One-shot colored text output:

```bash
mxtop --once
```

Plain text output:

```bash
mxtop --once --no-color
```

JSON output:

```bash
mxtop --json
```

Common filters:

```bash
mxtop --only 0 1
mxtop --user alice bob
mxtop --pid 1234 5678
mxtop --only-compute
mxtop --only-graphics
```

Backend and layout options:

```bash
mxtop --backend auto
mxtop --backend pymxsml
mxtop --backend mxsmi
mxtop --interval 1.0
mxtop --monitor auto
mxtop --monitor full
mxtop --monitor compact
```

Useful CLI flags:

| Flag | Meaning |
| --- | --- |
| `--version` | Print the runtime version. |
| `--backend {auto,pymxsml,mxsmi}` | Select telemetry backend. |
| `--interval SECONDS` | TUI refresh interval, minimum `0.25`. |
| `--once`, `-1` | Print one text snapshot and exit. |
| `--json` | Print one JSON snapshot and exit. |
| `--no-color` | Disable ANSI colors in text output. |
| `--monitor {auto,full,compact}` | Select TUI layout mode. |
| `--only INDEX...` | Show only selected GPU indices. |
| `--user USER...` | Show only processes owned by selected users. |
| `--pid PID...` | Show only selected process IDs. |
| `--compute`, `--graphics` | Prefer matching process types when available. |
| `--only-compute`, `--only-graphics` | Require matching process types when available. |
| `--ascii`, `--no-unicode` | Reserve ASCII-only rendering mode. |

## Interactive keys

| Key | Action |
| --- | --- |
| `q`, `Esc`, `Ctrl-C` | Quit. |
| `h`, `?` | Toggle help. |
| `r` | Refresh immediately. |
| `j`, `k`, arrow up/down | Move selected process. |
| `PageUp`, `PageDown` | Scroll vertically. |
| Arrow left/right | Scroll the command column horizontally. |
| `,`, `.` | Cycle process sort field. |
| `/` | Reverse current sort. |
| `a` | Auto layout. |
| `f` | Full layout. |
| `c` | Compact layout. |
| `o` then a sort key | Direct process sort when supported. |

## Packaging and releases

The PyPI distribution name is `metax-mxtop`; the Python import package and CLI command are both `mxtop`.

```bash
pip install metax-mxtop
mxtop --version
```

Release automation is tag-driven:

1. Bump `pyproject.toml`, `src/mxtop/__init__.py`, and `tests/test_version.py` to the new version.
2. Commit the change.
3. Create a new semver tag, for example `v0.1.4`.
4. Push the commit and that tag.

GitHub Actions then builds the wheelhouse, creates or updates the GitHub Release, and publishes the package to PyPI through Trusted Publishing for `v*` tags.

## Development

Run tests and lint locally:

```bash
uv run --with pytest --with psutil pytest -q
uv run --with ruff ruff check .
uv run --with build python -m build
```

More background is available in [INTRO.md](INTRO.md).
