"""AsyncSSH connection layer for remote monitoring."""

from __future__ import annotations

import os
from typing import Any

SSH_CONFIG_PATH = os.path.expanduser("~/.ssh/config")
KNOWN_HOSTS_PATH = os.path.expanduser("~/.ssh/known_hosts")
INSTALL_HINT = (
    "remote mode needs asyncssh; install it with: pip install 'metax-mxtop[remote]'"
)


def import_asyncssh() -> Any:
    """Import the optional remote dependency only when it is needed."""

    try:
        import asyncssh
    except ImportError as exc:  # pragma: no cover - exercised without the extra
        raise RuntimeError(INSTALL_HINT) from exc
    return asyncssh


async def connect(host: str, *, connect_timeout: float = 10.0) -> Any:
    """Connect through OpenSSH config using keys or ssh-agent only."""

    asyncssh = import_asyncssh()
    options: dict[str, Any] = {
        "connect_timeout": connect_timeout,
        "password_auth": False,
        "kbdint_auth": False,
    }
    if os.path.exists(SSH_CONFIG_PATH):
        options["config"] = [SSH_CONFIG_PATH]
    if os.path.exists(KNOWN_HOSTS_PATH):
        options["known_hosts"] = KNOWN_HOSTS_PATH
    return await asyncssh.connect(host, **options)
