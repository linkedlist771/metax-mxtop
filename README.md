<h1 align="center">metax-mxtop</h1>

<p align="center">
  <a href="https://github.com/linkedlist771/metax-mxtop"><img src="https://img.shields.io/github/stars/linkedlist771/metax-mxtop?style=flat-square&logo=github&color=181717" alt="GitHub stars"/></a>
  <a href="https://pypi.org/project/metax-mxtop/"><img src="https://img.shields.io/pypi/v/metax-mxtop?style=flat-square&logo=pypi&logoColor=white" alt="PyPI version"/></a>
  <a href="https://github.com/linkedlist771/metax-mxtop/actions/workflows/wheels.yml"><img src="https://img.shields.io/github/actions/workflow/status/linkedlist771/metax-mxtop/wheels.yml?branch=main&style=flat-square&label=CI" alt="CI status"/></a>
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-22c55e?style=flat-square" alt="MIT License"/></a>
  <img src="https://img.shields.io/badge/Python-3.9%2B-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.9+"/>
  <img src="https://img.shields.io/badge/Platform-Linux-FCC624?style=flat-square&logo=linux&logoColor=black" alt="Linux"/>
  <img src="https://img.shields.io/badge/GPU-MetaX-6366f1?style=flat-square" alt="MetaX GPU"/>
</p>

<p align="center">
  <code>metax-mxtop</code> is an nvitop-like terminal monitor for MetaX GPUs. Install the PyPI package, then run the <code>mxtop</code> command.
</p>

<p align="center">
  <em>Built upon <a href="https://github.com/onlylrs/metax-mxtop">onlylrs/metax-mxtop</a>, with thanks to the original project for the foundation.</em>
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/linkedlist771/metax-mxtop/main/assets/mxtop-preview.png?v=readme-native-1" alt="mxtop terminal preview" width="994"/>
</p>

<p align="center"><em>Colors follow nvitop's intensity model: each device summary uses its dominant GPU or memory load, while the detailed <code>MEM</code> / <code>MBW</code> / <code>UTL</code> / <code>PWR</code> bars retain metric-specific colors (memory thresholds <code>10/80</code>, GPU thresholds <code>10/75</code>).</em></p>

<table align="center">
  <tr>
    <td align="center"><strong>Idle</strong> — light load, mostly green</td>
    <td align="center"><strong>Mixed</strong> — per-bar color bands</td>
    <td align="center"><strong>Heavy</strong> — saturation across the cluster</td>
  </tr>
  <tr>
    <td><img src="https://raw.githubusercontent.com/linkedlist771/metax-mxtop/main/assets/mxtop-preview-idle.png" alt="mxtop idle status preview"/></td>
    <td><img src="https://raw.githubusercontent.com/linkedlist771/metax-mxtop/main/assets/mxtop-preview-mixed.png" alt="mxtop mixed status preview"/></td>
    <td><img src="https://raw.githubusercontent.com/linkedlist771/metax-mxtop/main/assets/mxtop-preview-heavy.png" alt="mxtop heavy status preview"/></td>
  </tr>
</table>

<p align="center"><em>Hosts with up to 16 GPUs retain the detailed nvitop-style list. Larger 32/64-GPU fleets use an adaptive overview grid in auto, compact, and one-shot views, while full mode keeps every detailed field.</em></p>

<p align="center">
  <img src="https://raw.githubusercontent.com/linkedlist771/metax-mxtop/main/assets/mxtop-preview-many.png" alt="mxtop adaptive 64-GPU fleet overview"/>
</p>

Preview and gallery images use fixed-time deterministic synthetic MetaX-shaped telemetry so every state is reproducible. Live monitoring data still comes exclusively from the selected MetaX backend; optional fields in the fixtures illustrate presentation states rather than proving that every backend reports them.

## Features

- nvitop-like terminal dashboard for MetaX GPUs, with full, compact, and automatic layouts.
- Read-only GPU telemetry through MXSML/Pymxsml or `mx-smi`.
- GPU device panel with temperature, power, utilization, memory, memory bandwidth, clocks, bus id, persistence, performance state, and driver fields when available.
- Adaptive 32/64-GPU fleet grid that preserves per-device load colors and leaves substantially more room for host/process data.
- Host panel with load average and scrolling history graphs for CPU, memory, swap, and the selected GPU's memory/utilization.
- Process table with selection, multi-process tagging, vertical and horizontal scrolling, and nvitop-compatible sorting.
- Process environment, host/GPU process-tree, per-process metrics, and built-in help screens.
- Confirmed `SIGINT`, `SIGTERM`, and `SIGKILL` actions for selected host processes, with `--readonly` to disable every process-changing action.
- ANSI-colored one-shot output, curses colors, ASCII fallback, and JSON output from the same filtered snapshot model.
- Shared device, user, PID, visibility, compute, and graphics filters across TUI, text, and JSON output.

GPU management remains read-only: `mxtop` does not update firmware, reset GPUs, or change persistence/admin settings. Interactive process signals are always presented for confirmation, validate process identity to guard against PID reuse, and enforce process ownership unless `mxtop` is running as root. Use `--readonly` when even confirmed host-process actions must be unavailable.

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

A man page ships with the package (`man mxtop` after a system-wide or
user-scheme install); it is also readable in-repo with `man -l docs/mxtop.1`.

Shell completions are generated from the CLI itself:

```bash
mxtop --print-completion bash > ~/.local/share/bash-completion/completions/mxtop
mxtop --print-completion zsh  > ~/.zfunc/_mxtop     # ensure ~/.zfunc is in fpath
mxtop --print-completion fish > ~/.config/fish/completions/mxtop.fish
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

When GPUs don't show up, `mxtop --doctor` walks the whole discovery chain —
Pymxsml importability and SDK wheels, mx-smi resolution, a live backend
snapshot with device count, terminal capabilities, and config validity —
printing a fix hint for anything that isn't a PASS.

## Usage

On a terminal, the default command opens the interactive dashboard:

```bash
mxtop
```

Use `--monitor` / `-m` to request it explicitly or choose a layout:

```bash
mxtop -m
mxtop -m full
mxtop -m compact
```

An explicit monitor requires both stdin and stdout to be TTYs. When the default command is redirected or piped, it prints one snapshot instead. `--once` / `-1` always selects one-shot text output:

```bash
mxtop --once
```

ANSI color is enabled for a terminal and can be disabled or forced:

```bash
mxtop --once --no-color
mxtop --once --force-color
```

`--json` prints one machine-readable snapshot without ANSI styling:

```bash
mxtop --json
```

`--count` / `-n` repeats one-shot or JSON output N times at the `--interval` cadence, for cron jobs and logging pipelines:

```bash
mxtop -n 10 --interval 5           # ten text snapshots, 5s apart
mxtop --json -n 60 --interval 1    # one minute of JSON samples
mxtop --json-lines -n 60 --interval 1 | jq '.devices[0].gpu_util_percent'
```

`--json-lines` / `--ndjson` emits one compact JSON object per line, the natural shape for `jq`, log shippers, and time-series ingestion.

For Prometheus-based monitoring of a single host, `--export-metrics` serves the local backend's telemetry on `/metrics` (default port `9532`, dcgm-exporter style) — no SSH or remote extra needed:

```bash
mxtop --export-metrics --port 9532 --auth-token secret
curl -H 'Authorization: Bearer secret' http://127.0.0.1:9532/metrics
```

Common filters:

```bash
mxtop --only 0 1
mxtop --only-visible
mxtop --user alice bob
mxtop --user                 # current user
mxtop --pid 1234 5678
mxtop --compute
mxtop --only-compute
mxtop --graphics
mxtop --only-graphics
```

`--compute` and `--graphics` include processes with those contexts, including mixed `X` / `C+G` processes. Their uppercase `--only-*` forms require an exact compute-only or graphics-only type. Multiple supplied filters are combined. If a backend reports processes without context-type telemetry, as some `mx-smi` versions do, requesting one of these filters exits with a stable error instead of silently excluding every process.

Backend and layout options:

```bash
mxtop --backend auto
mxtop --backend pymxsml
mxtop --backend mxsmi
mxtop --interval 1.0
mxtop --no-unicode
mxtop --readonly
```

Useful CLI flags:

| Flag | Meaning |
| --- | --- |
| `--version`, `-V` | Print the runtime version. |
| `--print-completion {bash,zsh,fish}` | Print a shell completion script and exit. |
| `--doctor` | Diagnose the environment (backends, devices, terminal, config) with PASS/WARN/FAIL checks and fix hints; exits non-zero when no telemetry backend works. |
| `--backend {auto,pymxsml,mxsmi}` | Select telemetry backend. |
| `--interval SECONDS` | Refresh interval (default `2.0`, minimum `0.25`). |
| `--once`, `-1` | Print one text snapshot and exit. |
| `--count N`, `-n N` | With `--once` or `--json`, print N snapshots separated by `--interval` and exit. Implies `--once` when neither is given. |
| `--monitor`, `-m [auto\|full\|compact]` | Run interactively, optionally choosing the layout. |
| `--json` | Print one JSON snapshot and exit. |
| `--json-lines`, `--ndjson` | Print snapshots as one compact JSON object per line (NDJSON). |
| `--export-metrics` | Serve local telemetry as a Prometheus `/metrics` endpoint (default port `9532`); honors `--bind`, `--port`, `--auth-token`, and `--interval`. |
| `--remote-command-timeout SEC` | Bound each remote dashboard SSH command (default `10.0`, minimum `0.1`). |
| `--no-color` | Disable ANSI colors in text output. |
| `--force-color` | Emit ANSI colors even when stdout is not a TTY. |
| `--colorful` | Use spectrum-like gradient colors for bar charts. On terminals advertising truecolor (`COLORTERM=truecolor`), the gradient uses a smooth 16-step 24-bit ramp; otherwise the 256-color palette. |
| `--light` | Use colors suitable for a light terminal theme. |
| `--gpu-util-thresh LOW HIGH` | Override GPU utilization intensity thresholds. |
| `--mem-util-thresh LOW HIGH` | Override GPU-memory intensity thresholds. |
| `--only INDEX...`, `-o INDEX...` | Show only selected GPU indices and their processes. |
| `--only-visible`, `-ov` | Follow `MACA_VISIBLE_DEVICES` or `CUDA_VISIBLE_DEVICES`. |
| `--user [USER...]`, `-u [USER...]` | Show selected users; with no value, use the current user. |
| `--pid PID...`, `-p PID...` | Show only selected process IDs. |
| `--compute`, `-c` | Require a compute-capable process context. |
| `--only-compute`, `-C` | Require an exact compute-only process context. |
| `--graphics`, `-g` | Require a graphics-capable process context. |
| `--only-graphics`, `-G` | Require an exact graphics-only process context. |
| `--no-unicode`, `--ascii`, `-U` | Use ASCII characters only. This is automatic for non-UTF-8 locales. |
| `--readonly` | Disable process signals in the interactive UI. |

### Environment variables

| Variable | Meaning |
| --- | --- |
| `MXTOP_MXSMI_PATH` | Override the local `mx-smi` executable discovered by the backend. |
| `MXTOP_PYMXSML_ECC_ERRORS` | Set to `1`, `true`, `yes`, or `on` to opt into cached ECC-error polling. It is disabled by default because current MetaX APIs can add more than one second to a refresh. |
| `MXTOP_MONITOR_MODE` | Comma-separated defaults: one of `auto`, `full`, `compact`, plus `colorful`/`plain`, `light`/`dark`, and/or `readonly`. Explicit CLI options take precedence. |
| `MXTOP_GPU_UTILIZATION_THRESHOLDS=LOW,HIGH` | Set GPU utilization thresholds when the CLI option is omitted. |
| `MXTOP_MEMORY_UTILIZATION_THRESHOLDS=LOW,HIGH` | Set GPU-memory thresholds when the CLI option is omitted. |
| `MACA_VISIBLE_DEVICES` | Device indices, UUID prefixes, or BDF prefixes used by `--only-visible`. |
| `CUDA_VISIBLE_DEVICES` | Fallback visibility list when `MACA_VISIBLE_DEVICES` is not set. |
| `MXTOP_AUTH_TOKEN` | Default dashboard token for `--remote-mode` when `--auth-token` is omitted. |
| `ANSI_COLORS_DISABLED` | Disable ANSI output unless `--force-color` is explicit. |
| `NO_COLOR` | Disable ANSI output unless `--force-color` is explicit. |
| `FORCE_COLOR` | Force ANSI output when neither disable variable is present. |
| `MXTOP_CONFIG` | Path to the configuration file (default: `~/.config/mxtop/config.toml`). |

### Configuration file

Persistent defaults live in `~/.config/mxtop/config.toml` (or `$XDG_CONFIG_HOME/mxtop/config.toml`, or the file named by `MXTOP_CONFIG`). Explicit CLI flags and environment variables always take precedence over the file. Unknown or invalid keys print a warning instead of being silently ignored.

```toml
interval = 1.0            # refresh cadence in seconds (min 0.25)
monitor = "full"          # default layout: auto | full | compact
colorful = true           # spectrum bar colors
light = false             # light terminal theme
readonly = false          # disable process signals
no-unicode = false        # ASCII output only
gpu-util-thresh = [10, 75]
mem-util-thresh = [10, 80]

[remote]                  # --remote-mode defaults
bind = "127.0.0.1"
port = 8080
auth-token = "change-me"
mxsmi-path = "mx-smi"
command-timeout = 10.0   # maximum seconds for each remote command
open = false
```

## Remote mode (cluster dashboard)

Aggregate several SSH-reachable nodes into one local web dashboard:

```bash
pip install 'metax-mxtop[remote]'        # pulls in asyncssh
# Auto-discover concrete Host aliases from ~/.ssh/config which have mx-smi:
mxtop --remote-mode --open
# Add explicitly named nodes to the discovered set:
mxtop --remote-mode --discover --nodes nodeA nodeB --open
# Or monitor only an explicit set:
mxtop --remote-mode --nodes nodeA nodeB nodeC --open
# or list hosts in a file (one per line, # comments allowed)
mxtop --remote-mode --nodes-file ~/hosts.txt --port 8080
```

- With no `--nodes` or `--nodes-file`, mxtop enumerates concrete `Host`
  aliases in `~/.ssh/config` (including `Include` files), probes them in
  parallel using key/agent authentication, and selects nodes where
  `mx-smi -L` reports at least one GPU. Wildcard `Host` patterns cannot be
  enumerated and are skipped. Use `--discover` to merge discovered nodes with
  an explicit list.
- Host names are plain SSH aliases resolved from your `~/.ssh/config`
  (HostName/User/Port/IdentityFile/ProxyJump all honoured), so nodes with
  different MetaX card configurations work without extra setup.
- Authentication uses your SSH keys / `ssh-agent` — the "login once" model.
  For password-only nodes, run `ssh-copy-id` first; mxtop does not store
  passwords.
- The dashboard polls every node concurrently (`--interval`, default 2s),
  serves on `127.0.0.1` by default (`--bind` to change), and streams live
  updates over Server-Sent Events. Wildcard binds (`0.0.0.0` / `::`) print a
  usable localhost access URL separately from the all-interfaces listener;
  IPv6 access URLs are bracketed correctly. Every SSH telemetry command has a
  finite deadline (`--remote-command-timeout`, default 10s), so a hung command
  marks only that node down and reconnects it on the next sample instead of
  freezing updates for the whole fleet.
- Dashboard access can be protected with a shared token via `--auth-token`
  or the `MXTOP_AUTH_TOKEN` environment variable. Requests must then carry
  `Authorization: Bearer <token>`, or visit `http://host:port/?token=<token>`
  once — the token is stored in an `HttpOnly` cookie for the rest of the
  session. When `--bind` exposes the dashboard beyond localhost without a
  token, mxtop prints a warning: all cluster telemetry (hostnames, users,
  process command lines) would otherwise be readable by anyone who can reach
  the port.
- A Prometheus endpoint at `/metrics` exports the latest cluster snapshot as
  text exposition: per-GPU utilization, memory, bandwidth, temperature,
  power, clocks, and ECC errors labelled by `node`/`gpu`/`name`/`uuid`, plus
  host CPU/RAM/load, per-node reachability (`mxtop_node_up`) and SSH collect
  latency. Point a Prometheus `scrape_config` at the dashboard (with the
  bearer token when set) for alerting and long-term history:

  ```yaml
  scrape_configs:
    - job_name: mxtop
      static_configs:
        - targets: ["monitor-host:8080"]
      authorization:
        type: Bearer
        credentials: <your --auth-token value>
  ```
- The web UI provides a fleet overview with a switchable GPU heatmap, live
  trend sparklines (cluster GPU utilization, HBM, and host CPU; per-node
  GPU/HBM on the detail page), a searchable node inventory, cluster-wide
  host CPU/RAM/load telemetry, a process table, and per-node GPU, host, and
  process detail. Click a PID or command to open an investigation page with
  current values and rolling CPU, GPU-utilization, GPU-memory, and host-memory
  history. An exited process or unreachable node keeps its last sample visible;
  creation time or backend identity defines generations when available, with
  ended-state, command, and runtime-reset reconciliation covering remote PID
  reuse. A new generation never inherits the previous process's charts. Remote
  process rows are enriched with one batched `ps` query per node; missing Linux
  host fields degrade to unavailable values without taking the node offline. View
  state is encoded in the URL hash, so browser back/forward navigation works
  without reconnecting to the nodes. Dark and light themes follow the
  browser's `prefers-color-scheme` and can be switched with the header
  toggle; the choice persists in the browser.
- Each node runs the same `mx-smi` queries as the local backend; override the
  remote binary with `--remote-mxsmi-path` if it is not on `PATH`.

## Interactive keys

### General and navigation

| Key | Action |
| --- | --- |
| `q`, `Q` | Quit from the main screen; return from a detail screen. |
| `h`, `?` | Open help. Any key returns to the previous screen. |
| `r`, `R`, `Ctrl-R`, `F5` | Refresh immediately and resume if paused. On the main screen this also resets selection and scrolling. |
| `p`, `Z` | Pause or resume live updates on the main screen (the status line shows PAUSED). |
| `a`, `f`, `c` | Switch to auto, full, or compact layout. |
| Up/Down, `Shift-Tab`/`Tab`, `Alt-k`/`Alt-j` | Select the previous/next process or detail row. |
| `Home`, `End` | Select the first/last row. |
| `Space` | Tag or untag the current process, then advance. Tagged processes form the action target set. |
| `\`, `F4` | Filter the process table incrementally (htop-style): type to match user, command, or PID; `Enter` applies, `Esc` clears. |
| `Esc` | Clear the active text filter first; then clear the main-screen selection and tags; return from environment or metrics. It does not quit mxtop. |
| `PageUp`/`PageDown`, `[`/`]` | Scroll the viewport vertically; `Alt-K`/`Alt-J` are main-screen aliases. |
| Left/Right, `Alt-h`/`Alt-l` | Scroll horizontally. |
| `Ctrl-A`, `^` | Return to the leftmost column. |
| `Ctrl-E`, `$` | Jump to the rightmost process-table column. |
| Mouse wheel | Move vertically; hold Ctrl for 5x movement or Shift for horizontal movement. Click a row to select it. Click a process-table column header to sort by it; click again to reverse. |

### Screens and process actions

| Key | Action |
| --- | --- |
| `e` | Show the selected process environment. Press `e`, `q`, or `Esc` to return. |
| `t` | Show the GPU process tree, including related host ancestors and descendants. Press `t` or `q` to return. |
| `Enter` | Show rolling metrics for the selected process. Press `Enter`, `q`, or `Esc` to return. |
| `T` | Open confirmation for `SIGTERM`. |
| `K`, `k` | Open confirmation for `SIGKILL`. |
| `Ctrl-C`, `I` | Open confirmation for `SIGINT`. Ctrl-C does not quit the TUI. |

Signals apply to all tagged processes when tags exist, otherwise to the selected process (or highlighted process-tree entry). The dialog supports Left/Right, Tab/Shift-Tab, `,`/`.`, mouse wheel, and button clicks; choose `SIGTERM`, `SIGKILL`, `SIGINT`, or Cancel, then confirm with `Enter`, `Space`, or `y`. Process actions require `psutil` and are disabled by `--readonly`.

### Process sorting

| Key | Action |
| --- | --- |
| `,`, `<` / `.`, `>` | Cycle to the previous/next sort column. |
| `/` | Invert the current sort order. |
| `on` / `oN` | Sort by GPU index in normal/reverse order. |
| `op` / `oP` | Sort by PID in normal/reverse order. |
| `ou` / `oU` | Sort by user in normal/reverse order. |
| `og` / `oG` | Sort by GPU memory in normal/reverse order. |
| `os` / `oS` | Sort by GPU utilization in normal/reverse order. |
| `ob` / `oB` | Sort by GPU memory-bandwidth utilization in normal/reverse order. |
| `oc` / `oC` | Sort by CPU utilization in normal/reverse order. |
| `om` / `oM` | Sort by host-memory utilization in normal/reverse order. |
| `ot` / `oT` | Sort by runtime in normal/reverse order. |

## Packaging and releases

The PyPI distribution name is `metax-mxtop`; the Python import package and CLI command are both `mxtop`.

```bash
pip install metax-mxtop
mxtop --version
```

Branch and pull-request runs execute the Python compatibility matrix and build and test the offline wheelhouse. Release publication is gated on the Python 3.9 wheelhouse checks, the full Python 3.10-3.13 test matrix, lint, and the Chromium dashboard suite. A push to `main` creates a commit-addressed prerelease; versioned GitHub and PyPI publication is limited to `v*` tags:

1. Bump `pyproject.toml`, `src/mxtop/__init__.py`, and the local package entry in `uv.lock`.
2. Regenerate the preview, gallery, and showcase assets and run their `--check` commands.
3. Run the full test, lint, and package-build commands below.
4. Commit the change.
5. Create a new semver tag, for example `v0.1.22`.
6. Push the commit and that tag.

GitHub Actions builds and Twine-validates the wheel and source distribution once, bundles them with the offline wheelhouse and a shared `SHA256SUMS.txt`, and makes both publishers download and verify that same workflow artifact. Automation refuses to overwrite an existing tagged GitHub Release. The minimal PyPI job performs no checkout or rebuild; it publishes the prebuilt distributions through OIDC Trusted Publishing.

## More previews

See the [output gallery](https://github.com/linkedlist771/metax-mxtop/blob/main/GALLERY.md) and [screen showcase](https://github.com/linkedlist771/metax-mxtop/blob/main/SHOWCASE.md) for rendered stdout and deterministic secondary-screen fixtures across palettes, layouts, filters, edge telemetry, and 32/64-GPU fleets.

## Security

See [SECURITY.md](SECURITY.md) for responsible disclosure and secure deployment guidance. In particular, `--auth-token` controls access but does not encrypt HTTP traffic; use a TLS reverse proxy, VPN, or SSH tunnel across untrusted networks.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full workflow, including the
docs-sync tests and help-screen colorization rules. Run tests and lint locally:

```bash
uv run --locked --with pytest --with psutil --with pillow==11.3.0 pytest -q
uv run --locked --with ruff ruff check .
npm ci && npx playwright install --only-shell chromium
npm run test:dashboard
uv run --locked --with build python -m build
```

Canonical assets use Pillow 11.3.0, the vendored Liberation Mono fonts, and an algorithmic Braille renderer. Their metadata fingerprints the Pillow and FreeType versions as well as the font bytes and renderer source. Regenerate the three canonical asset sets with:

```bash
uv run --locked --with pillow==11.3.0 --with psutil python scripts/generate_preview.py --all
uv run --locked --with pillow==11.3.0 --with psutil python scripts/render_gallery.py
uv run --locked --with pillow==11.3.0 --with psutil python scripts/render_showcase.py
```

To create an explicitly noncanonical preview with local fonts, set `MXTOP_PREVIEW_FONT`, `MXTOP_PREVIEW_BOLD_FONT`, and optionally `MXTOP_PREVIEW_SYMBOL_FONT`, then pass `--custom-fonts` with a noncanonical output path:

```bash
uv run --locked --with pillow==11.3.0 --with psutil python scripts/generate_preview.py \
  --custom-fonts --output /tmp/mxtop-custom.png
```

More background is available in the [usage guide](https://github.com/linkedlist771/metax-mxtop/blob/main/INTRO.md).
