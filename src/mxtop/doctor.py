"""Environment diagnostics for ``mxtop --doctor``.

Answers "why don't I see my GPUs?" without a debugger: each check prints
PASS/WARN/FAIL with a fix hint, and the exit code reflects whether any
telemetry backend actually works.
"""

from __future__ import annotations

import importlib
import locale
import os
import shutil
import sys
from dataclasses import dataclass
from glob import glob
from pathlib import Path
from typing import Callable

from mxtop._compat import DATACLASS_SLOTS

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"

_STATUS_ORDER = {PASS: 0, WARN: 1, FAIL: 2}


@dataclass(**DATACLASS_SLOTS)
class CheckResult:
    name: str
    status: str
    detail: str
    hint: str | None = None


def _check_python() -> CheckResult:
    version = ".".join(map(str, sys.version_info[:3]))
    if sys.version_info < (3, 9):
        return CheckResult(
            "Python", FAIL, f"{version} is unsupported", "mxtop needs Python 3.9+"
        )
    return CheckResult("Python", PASS, version)


def _check_psutil() -> CheckResult:
    try:
        psutil = importlib.import_module("psutil")
    except ModuleNotFoundError:
        return CheckResult(
            "psutil",
            WARN,
            "not importable",
            "host telemetry and process actions need psutil: pip install psutil",
        )
    return CheckResult("psutil", PASS, getattr(psutil, "__version__", "unknown"))


def _check_pymxsml() -> CheckResult:
    try:
        module = importlib.import_module("pymxsml")
    except ModuleNotFoundError:
        wheels = sorted(glob("/opt/maca/share/mxsml/pymxsml-*.whl")) + sorted(
            glob("/opt/mxn100/share/mxsml/pymxsml-*.whl")
        )
        if wheels:
            return CheckResult(
                "Pymxsml",
                WARN,
                f"not installed, but SDK wheel found: {wheels[-1]}",
                "mxtop loads the wheel automatically; install it for other tools",
            )
        return CheckResult(
            "Pymxsml",
            WARN,
            "not installed and no SDK wheel under /opt/maca or /opt/mxn100",
            "install the MetaX SDK or rely on the mx-smi backend",
        )
    location = getattr(module, "__file__", None) or "built-in"
    return CheckResult("Pymxsml", PASS, location)


def _check_mxsmi() -> CheckResult:
    from mxtop.backends.mxsmi import DEFAULT_MXSMI_PATH, MXSMI_ENV, resolve_mxsmi_path

    resolved = resolve_mxsmi_path()
    source = (
        f"{MXSMI_ENV} environment variable"
        if os.environ.get(MXSMI_ENV)
        else "default driver path"
        if resolved == DEFAULT_MXSMI_PATH
        else "PATH lookup"
    )
    path = Path(resolved)
    if path.is_file() and os.access(resolved, os.X_OK):
        return CheckResult("mx-smi", PASS, f"{resolved} ({source})")
    if shutil.which(resolved):
        return CheckResult("mx-smi", PASS, f"{resolved} ({source})")
    return CheckResult(
        "mx-smi",
        WARN,
        f"{resolved} is not an executable file ({source})",
        f"install the MetaX driver or set {MXSMI_ENV}=/path/to/mx-smi",
    )


def _check_backend(backend_name: str = "auto") -> CheckResult:
    from mxtop.backends import create_backend

    try:
        backend = create_backend(backend_name)
        frame = backend.snapshot()
    except Exception as exc:
        return CheckResult(
            "Telemetry backend",
            FAIL,
            str(exc) or type(exc).__name__,
            "run on a host with MetaX GPUs and a working driver; "
            "see 'MetaX backend discovery' in the README",
        )
    devices = len(frame.devices)
    detail = f"{backend.name}: {devices} device(s)"
    if devices == 0:
        return CheckResult(
            "Telemetry backend",
            WARN,
            detail,
            "the backend responds but reports no GPUs; check driver state "
            "and MACA_VISIBLE_DEVICES",
        )
    return CheckResult("Telemetry backend", PASS, detail)


def _check_terminal() -> CheckResult:
    term = os.environ.get("TERM", "")
    encoding = locale.getpreferredencoding(False) or ""
    problems: list[str] = []
    if not term or term == "dumb":
        problems.append(f"TERM={term or '(unset)'}")
    if "utf" not in encoding.lower():
        problems.append(f"non-UTF-8 locale ({encoding}); ASCII fallback applies")
    colorterm = os.environ.get("COLORTERM", "")
    detail = f"TERM={term or '(unset)'}, encoding={encoding}"
    if colorterm:
        detail += f", COLORTERM={colorterm}"
    if problems:
        return CheckResult(
            "Terminal",
            WARN,
            "; ".join(problems),
            "the interactive monitor needs a capable TTY; one-shot and JSON "
            "output still work",
        )
    return CheckResult("Terminal", PASS, detail)


def _check_config() -> CheckResult:
    from mxtop.config import config_path, load_config

    path = config_path()
    if not path.exists():
        return CheckResult("Config file", PASS, f"none ({path})")
    import io
    from contextlib import redirect_stderr

    stderr = io.StringIO()
    with redirect_stderr(stderr):
        config = load_config(path)
    warnings = stderr.getvalue().strip()
    if warnings:
        first = warnings.splitlines()[0]
        return CheckResult(
            "Config file", WARN, f"{path}: {first}", "fix the reported keys"
        )
    return CheckResult("Config file", PASS, f"{path} ({len(config)} setting(s))")


def _check_remote() -> CheckResult:
    try:
        asyncssh = importlib.import_module("asyncssh")
    except ModuleNotFoundError:
        return CheckResult(
            "Remote mode",
            WARN,
            "asyncssh not installed",
            "pip install 'metax-mxtop[remote]' to use --remote-mode",
        )
    ssh_config = Path.home() / ".ssh" / "config"
    detail = f"asyncssh {getattr(asyncssh, '__version__', 'unknown')}"
    detail += ", ~/.ssh/config present" if ssh_config.exists() else ", no ~/.ssh/config"
    return CheckResult("Remote mode", PASS, detail)


CHECKS: tuple[Callable[[], CheckResult], ...] = (
    _check_python,
    _check_psutil,
    _check_pymxsml,
    _check_mxsmi,
    _check_backend,
    _check_terminal,
    _check_config,
    _check_remote,
)


def run_doctor(*, use_color: bool) -> int:
    """Run all checks, print a report, and return the exit code."""

    from mxtop import __version__

    colors = {
        PASS: "\x1b[32m" if use_color else "",
        WARN: "\x1b[33m" if use_color else "",
        FAIL: "\x1b[31m" if use_color else "",
    }
    reset = "\x1b[0m" if use_color else ""
    print(f"mxtop {__version__} doctor")
    worst = PASS
    for check in CHECKS:
        try:
            result = check()
        except Exception as exc:  # a diagnostic must never crash the doctor
            result = CheckResult(
                check.__name__.removeprefix("_check_"),
                WARN,
                f"check crashed: {exc}",
            )
        if _STATUS_ORDER[result.status] > _STATUS_ORDER[worst]:
            worst = result.status
        print(
            f"  [{colors[result.status]}{result.status}{reset}] "
            f"{result.name}: {result.detail}"
        )
        if result.hint and result.status != PASS:
            print(f"         hint: {result.hint}")
    if worst == FAIL:
        print("Result: no working telemetry backend; mxtop cannot monitor GPUs here.")
        return 1
    if worst == WARN:
        print("Result: functional with warnings.")
        return 0
    print("Result: everything looks good.")
    return 0
