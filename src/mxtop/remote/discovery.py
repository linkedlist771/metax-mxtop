"""Discover passwordless SSH nodes which expose a working ``mx-smi``."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import glob
import os
from pathlib import Path
import shlex
import time

from mxtop.backends.mxsmi import parse_list_output
from mxtop.remote import ssh


@dataclass(frozen=True)
class HostDiscovery:
    host: str
    accepted: bool
    gpu_count: int = 0
    latency_ms: float | None = None
    reason: str | None = None


def configured_hosts(config_path: str | os.PathLike[str] | None = None) -> list[str]:
    """Read concrete Host aliases from OpenSSH config and Include files."""

    root = Path(config_path or ssh.SSH_CONFIG_PATH).expanduser()
    visited: set[Path] = set()
    aliases: dict[str, None] = {}

    def visit(path: Path) -> None:
        path = path.expanduser().resolve(strict=False)
        if path in visited or not path.is_file():
            return
        visited.add(path)
        for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                tokens = shlex.split(raw_line, comments=True, posix=True)
            except ValueError:
                continue
            if not tokens:
                continue
            keyword = tokens[0].lower()
            if keyword == "include":
                for value in tokens[1:]:
                    pattern = Path(os.path.expandvars(os.path.expanduser(value)))
                    if not pattern.is_absolute():
                        pattern = path.parent / pattern
                    for match in sorted(glob.glob(str(pattern))):
                        visit(Path(match))
            elif keyword == "host":
                for alias in tokens[1:]:
                    if alias.startswith("!") or any(
                        marker in alias for marker in ("*", "?", "[")
                    ):
                        continue
                    aliases.setdefault(alias, None)

    visit(root)
    return list(aliases)


def _probe_command(mxsmi_path: str) -> str:
    executable = shlex.quote(mxsmi_path)
    available = (
        f"test -x {executable}"
        if "/" in mxsmi_path
        else f"command -v {executable} >/dev/null 2>&1"
    )
    return f"if {available}; then exec {executable} -L; else exit 127; fi"


async def probe_host(
    host: str,
    *,
    mxsmi_path: str = "mx-smi",
    connect_timeout: float = 5.0,
    command_timeout: float = 8.0,
) -> HostDiscovery:
    """Verify key-based SSH access and parse the remote GPU inventory."""

    started = time.monotonic()
    conn = None
    try:
        conn = await ssh.connect(host, connect_timeout=connect_timeout)
        result = await conn.run(
            _probe_command(mxsmi_path),
            check=False,
            timeout=command_timeout,
        )
        latency_ms = (time.monotonic() - started) * 1000.0
        if result.exit_status == 127:
            return HostDiscovery(
                host,
                False,
                latency_ms=latency_ms,
                reason=f"{mxsmi_path} not found",
            )
        if result.exit_status != 0:
            error = (result.stderr or result.stdout or "mx-smi probe failed").strip()
            return HostDiscovery(
                host,
                False,
                latency_ms=latency_ms,
                reason=error.splitlines()[0],
            )
        devices = parse_list_output(result.stdout or "")
        if not devices:
            return HostDiscovery(
                host,
                False,
                latency_ms=latency_ms,
                reason="mx-smi reported no GPUs",
            )
        return HostDiscovery(host, True, len(devices), latency_ms)
    except Exception as exc:
        latency_ms = (time.monotonic() - started) * 1000.0
        reason = str(exc).strip() or type(exc).__name__
        return HostDiscovery(host, False, latency_ms=latency_ms, reason=reason)
    finally:
        if conn is not None:
            try:
                conn.close()
                await conn.wait_closed()
            except Exception:
                pass


async def discover_hosts(
    hosts: list[str],
    *,
    mxsmi_path: str = "mx-smi",
    connect_timeout: float = 5.0,
    command_timeout: float = 8.0,
    concurrency: int = 8,
) -> list[HostDiscovery]:
    """Probe candidates concurrently while preserving SSH-config order."""

    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def probe(host: str) -> HostDiscovery:
        async with semaphore:
            return await probe_host(
                host,
                mxsmi_path=mxsmi_path,
                connect_timeout=connect_timeout,
                command_timeout=command_timeout,
            )

    return list(await asyncio.gather(*(probe(host) for host in hosts)))


def discover_configured_hosts(
    *,
    mxsmi_path: str = "mx-smi",
) -> tuple[list[str], list[HostDiscovery]]:
    """Discover usable GPU nodes from the current user's SSH config."""

    candidates = configured_hosts()
    if not candidates:
        return [], []
    results = asyncio.run(discover_hosts(candidates, mxsmi_path=mxsmi_path))
    return [result.host for result in results if result.accepted], results
