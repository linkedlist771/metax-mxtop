"""Optional TOML configuration file support.

``mxtop`` reads persistent defaults from (first match wins):

1. ``$MXTOP_CONFIG`` when set,
2. ``$XDG_CONFIG_HOME/mxtop/config.toml``,
3. ``~/.config/mxtop/config.toml``.

Values act as defaults only: environment variables and explicit CLI flags
always take precedence. Unknown keys are reported once on stderr rather
than silently ignored, so typos are discoverable.
"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]

MXTOP_CONFIG_ENV = "MXTOP_CONFIG"

TOP_LEVEL_KEYS = {
    "interval": float,
    "monitor": str,
    "colorful": bool,
    "light": bool,
    "readonly": bool,
    "no-unicode": bool,
    "gpu-util-thresh": list,
    "mem-util-thresh": list,
}

REMOTE_KEYS = {
    "bind": str,
    "port": int,
    "auth-token": str,
    "tls-cert": str,
    "tls-key": str,
    "tls-key-password-file": str,
    "mxsmi-path": str,
    "command-timeout": float,
    "open": bool,
}

MONITOR_LAYOUTS = ("auto", "full", "compact")


def config_path() -> Path:
    override = os.environ.get(MXTOP_CONFIG_ENV)
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "mxtop" / "config.toml"


def _warn(message: str) -> None:
    print(f"MXTOP CONFIG WARNING: {message}", file=sys.stderr)


def _coerce(section: str, key: str, value: Any, expected: type) -> Any | None:
    if expected is float and isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if expected is int and isinstance(value, int) and not isinstance(value, bool):
        return value
    if not isinstance(value, expected) or (expected is not bool and isinstance(value, bool)):
        _warn(f"{section}{key} should be {expected.__name__}, got {type(value).__name__}")
        return None
    return value


def _validated_thresholds(key: str, value: list[Any]) -> tuple[int, int] | None:
    if (
        len(value) != 2
        or not all(isinstance(item, int) and not isinstance(item, bool) for item in value)
    ):
        _warn(f"{key} should be a list of two integers")
        return None
    low, high = sorted(value)
    if not (0 < low <= high < 100):
        _warn(f"{key} values must satisfy 0 < LOW <= HIGH < 100")
        return None
    return low, high


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Load and validate the config file into a flat mapping.

    Returns keys named after CLI destinations (``no_unicode``) plus
    ``remote_*``-prefixed remote-section keys. Missing file -> empty dict.
    """

    path = path or config_path()
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        _warn(f"cannot read {path}: {exc}")
        return {}
    try:
        document = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        _warn(f"cannot parse {path}: {exc}")
        return {}

    config: dict[str, Any] = {}
    for key, value in document.items():
        if key == "remote":
            if not isinstance(value, dict):
                _warn("remote should be a table")
                continue
            for remote_key, remote_value in value.items():
                expected = REMOTE_KEYS.get(remote_key)
                if expected is None:
                    _warn(f"unknown key remote.{remote_key}")
                    continue
                coerced = _coerce("remote.", remote_key, remote_value, expected)
                if coerced is None:
                    continue
                if remote_key in {
                    "tls-cert",
                    "tls-key",
                    "tls-key-password-file",
                } and not coerced.strip():
                    _warn(f"remote.{remote_key} must not be empty")
                    continue
                if remote_key == "command-timeout" and (
                    not math.isfinite(coerced) or coerced < 0.1
                ):
                    _warn("remote.command-timeout must be at least 0.1")
                    continue
                config[f"remote_{remote_key.replace('-', '_')}"] = coerced
            continue
        expected = TOP_LEVEL_KEYS.get(key)
        if expected is None:
            _warn(f"unknown key {key}")
            continue
        if expected is list:
            thresholds = _validated_thresholds(key, value if isinstance(value, list) else [])
            if thresholds is not None:
                config[key.replace("-", "_")] = thresholds
            continue
        coerced = _coerce("", key, value, expected)
        if coerced is None:
            continue
        if key == "monitor" and coerced not in MONITOR_LAYOUTS:
            _warn(f"monitor should be one of {', '.join(MONITOR_LAYOUTS)}")
            continue
        if key == "interval" and coerced < 0.25:
            _warn("interval must be at least 0.25")
            continue
        config[key.replace("-", "_")] = coerced
    return config
