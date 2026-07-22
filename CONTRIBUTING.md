# Contributing to metax-mxtop

Thanks for helping improve `mxtop`. This page covers the local workflow;
the [README](README.md) covers what the tool does.

## Development setup

```bash
git clone https://github.com/linkedlist771/metax-mxtop.git
cd metax-mxtop
pip install -e '.[remote,dev]'    # or: uv pip install -e '.[remote,dev]'
```

The `remote` extra pulls in `asyncssh` so the `--remote-mode` tests run
instead of skipping. The `dev` extra is the single source of truth for CI and
local Python test/build/release tools; the root npm lockfile pins dashboard
browser tooling. Dependabot groups routine Python and npm updates monthly.
No MetaX hardware is needed for development: the test suite and all preview
assets use deterministic synthetic fixtures.

## Running checks

Everything CI runs, locally:

```bash
# Tests (Pillow is pinned so preview PNG tests validate byte-identical output)
uv run --with pytest --with 'pillow==11.3.0' --extra remote pytest -q

# Branch coverage (CI enforces at least 75%; current baseline is about 77%)
uv run --with pytest --with pytest-cov --extra remote \
  pytest -q --cov --cov-branch --cov-report=term-missing:skip-covered

# Lint
ruff check .

# Browser regression suite (Node.js 20+; Chromium is a one-time install)
npm ci
npx playwright install --only-shell chromium
npm run test:dashboard
```

CI runs the suite on Python 3.9 through 3.13; the 3.13 leg enforces branch
coverage while the other compatibility legs stay fast. CodeQL separately scans
Python and dashboard JavaScript with extended security queries. The release
artifact job runs the Playwright suite once under Node.js 22 before it builds
any distribution. `uv run --python 3.9 ...` reproduces the Python floor
version locally.

To inspect the browser UI against the same lifecycle used in tests, run:

```bash
python scripts/serve_dashboard_fixture.py
```

The fixture prints its local URL and advances through a live training process,
a node outage, a clean process exit, and reuse of the same PID by a new process.
Pass `--step 5` for a fixed live sample or `--help` for sequence controls.

## Things the test suite enforces

- **Docs stay in sync with the CLI.** Adding an argparse option fails
  `tests/test_manpage.py` until `docs/mxtop.1` documents it. Shell
  completions are generated from the parser, so they update themselves.
- **Help-screen colors are content-based.** If you edit help lines in
  `src/mxtop/ui/screens.py`, update the matching rules in
  `mxtop.tui._help_line_colors` *and* the ANSI mirror in
  `scripts/render_showcase.py`, and bump the help showcase spec height.
- **Preview assets are reproducible.** Committed PNGs embed a source hash;
  changing rendering requires regenerating them (below) or the freshness
  tests fail.

## Regenerating preview assets

Rendering is deterministic but pinned to `pillow==11.3.0`:

```bash
uv run --with 'pillow==11.3.0' python scripts/generate_preview.py --all
uv run --with 'pillow==11.3.0' python scripts/render_gallery.py
uv run --with 'pillow==11.3.0' python scripts/render_showcase.py
```

`GALLERY.md` and `SHOWCASE.md` are rewritten by the scripts — don't edit
them by hand.

## Code style

- `ruff check .` must pass; there is no formatter — match the surrounding
  code.
- Prefer stdlib over new dependencies (the web dashboard is vanilla JS,
  the Prometheus exporter is hand-rolled, TOML uses `tomllib`/`tomli`).
- GPU management stays read-only, and anything process-changing must
  respect `--readonly` and confirm interactively.
- Broad `except Exception` is acceptable only at subsystem boundaries and
  should leave a diagnosable trace (see `mxtop.backends.pymxsml._safe`).

## Releases

Pushing to `main` builds a commit-addressed prerelease. Versioned releases
are tag-driven: bump both `version` in `pyproject.toml` and `__version__` in
`src/mxtop/__init__.py`, move the release notes out of Unreleased and add the
comparison link in `CHANGELOG.md`, then tag `vX.Y.Z` and push the tag.

Before tagging, run:

```bash
python scripts/check_release_version.py vX.Y.Z
```

GitHub Actions runs the same version and CHANGELOG guard in the single
distribution-build job. Publication waits for that job's Python 3.9 tests,
lint, Chromium dashboard suite, and offline-install check, plus the complete
Python 3.10-3.13 test matrix. The build job creates and Twine-validates the
wheel and sdist once, assembles the offline wheelhouse, and uploads one
checksum-protected release artifact.

The GitHub Release and PyPI jobs both download and verify that exact artifact;
neither rebuilds the package. Tagged GitHub Releases are create-only, so an
existing release fails instead of having its assets replaced. The PyPI job
contains only download, checksum-verification, and Trusted Publishing steps,
and it is the only job granted `id-token: write`. A metadata mismatch, corrupt
artifact, stale release note, or invalid distribution fails before
publication.
