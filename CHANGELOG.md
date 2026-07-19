# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `--auth-token` / `MXTOP_AUTH_TOKEN` shared-secret protection for the remote
  cluster dashboard: requests need `Authorization: Bearer <token>`, or a
  one-time `?token=` visit that sets an HttpOnly cookie. Binding beyond
  localhost without a token prints a warning.
- Click a process-table column header in the TUI to sort by that column;
  click again to reverse — mouse parity with the `o`+key direct sorts.
- `--count` / `-n N` repeats `--once` or `--json` snapshots at the
  `--interval` cadence for cron and logging pipelines; `-n` alone implies
  `--once`.
- Configuration file support: persistent defaults in
  `~/.config/mxtop/config.toml` (or `$XDG_CONFIG_HOME/mxtop/config.toml`, or
  `$MXTOP_CONFIG`), including a `[remote]` section. CLI flags and environment
  variables always take precedence; unknown or invalid keys warn instead of
  being silently ignored.
- Light theme for the remote web dashboard, following the browser's
  `prefers-color-scheme` with a header toggle persisted in the browser.
- htop-style incremental process filter in the TUI: `\` or `F4` opens a
  prompt that matches user, command, name, or PID as you type; `Enter`
  applies, `Esc` clears.
- Live trend sparklines in the remote dashboard: the Overview shows rolling
  cluster GPU-utilization, HBM, and host-CPU history, and each node detail
  page shows per-node GPU/HBM history — accumulated client-side from the
  SSE stream, bounded to the most recent 240 samples.
- A `mxtop(1)` man page (`docs/mxtop.1`), installed to `share/man/man1` and
  kept in sync with the CLI by tests.
- Truecolor support for `--colorful`: on terminals advertising
  `COLORTERM=truecolor`/`24bit` with redefinable curses colors, bar
  gradients use a smooth 16-step 24-bit green-to-red ramp instead of the
  6-color 256-palette approximation.
- Shell completions generated from the CLI itself:
  `mxtop --print-completion {bash,zsh,fish}`, kept complete by tests.
- `mxtop --doctor` environment diagnostics: PASS/WARN/FAIL checks with fix
  hints for Pymxsml, mx-smi resolution, live backend snapshots, psutil,
  terminal capabilities, config-file validity, and the remote extra; exits
  non-zero when no telemetry backend works.
- Prometheus `/metrics` endpoint on the remote dashboard: per-GPU and
  per-host gauges labelled by node/gpu/name/uuid, node reachability and
  SSH collect latency, honoring the dashboard auth token.
- `--json-lines` / `--ndjson`: one compact JSON object per line for
  streaming pipelines, combinable with `--count`.
- CI now tests Python 3.10-3.13 in a matrix alongside the existing 3.9
  wheelhouse job, with the remote extra installed.

### Changed

- Help-screen colorization now derives from line content instead of
  hard-coded row numbers, so help edits can no longer silently break colors.
- Failed pymxsml telemetry calls are logged once at debug level instead of
  silently returning defaults; `--open` browser failures print a hint
  instead of being swallowed.
- The dashboard's SSE stream is capped at 32 concurrent clients; excess
  clients receive 503 with `Retry-After` while `/api/snapshot` polling stays
  available, bounding per-connection thread growth.
- Node failures surface their SSH error reason in the dashboard (hover the
  Down badge or the hotspot DOWN entry), and empty exception messages fall
  back to the exception type name.

### Removed

- Unreachable legacy help-screen code path whose key documentation had
  drifted from the actual bindings (`mxtop.ui.help`, `render_help`,
  `UiState.show_help`).

## [0.1.25] - 2026-07

### Added

- Remote cluster dashboard (`--remote-mode`): AsyncSSH-based multi-node
  polling, SSH-config host auto-discovery, and a web UI with fleet overview,
  GPU heatmap, node inventory, process table, and per-node detail served
  over HTTP + Server-Sent Events.
- `y`/`Y` accepted to confirm process signals.

### Changed

- TUI rendering alignment fixes (0.1.24 follow-ups).

## [0.1.24] - 2026-06

### Changed

- Aligned TUI rendering with nvitop across panels.

## [0.1.23] - 2026-06

### Added

- Windows rendering alignment and field alignment fixes.

## [0.1.22] - 2026-05

### Added

- nvitop feature parity: environment screen, process tree view, per-process
  metrics screen with braille history graphs, and adaptive 32/64-GPU fleet
  grid.

## [0.1.21] - 2026-05

### Changed

- Performance: on-demand TUI repaint, shared line classifier, pymxsml caches.

## [0.1.20] and earlier

- Incremental nvitop-aligned UI: braille host history graphs (0.1.19), real
  driver/MACA version display (0.1.18), first remote-mode dashboard
  (0.1.17), dark-terminal border fixes (0.1.16), `mx-smi -L` parsing
  (0.1.15), usage-based coloring, bordered layout, and the initial
  MXSML/`mx-smi` backends. See the git history for full detail.

[Unreleased]: https://github.com/linkedlist771/metax-mxtop/compare/v0.1.25...HEAD
[0.1.25]: https://github.com/linkedlist771/metax-mxtop/compare/v0.1.24...v0.1.25
[0.1.24]: https://github.com/linkedlist771/metax-mxtop/compare/v0.1.22...v0.1.24
[0.1.23]: https://github.com/linkedlist771/metax-mxtop/compare/v0.1.22...v0.1.24
[0.1.22]: https://github.com/linkedlist771/metax-mxtop/compare/v0.1.21...v0.1.22
[0.1.21]: https://github.com/linkedlist771/metax-mxtop/compare/v0.1.20...v0.1.21
