"""Thin asyncssh wrapper that honours standard ``~/.ssh/config`` + ssh-agent.

asyncssh is an optional dependency (``pip install 'metax-mxtop[remote]'``) so it
is imported lazily; importing this module never requires it.
"""

from __future__ import annotations

import os
from typing import Any

SSH_CONFIG_PATH = os.path.expanduser("~/.ssh/config")
KNOWN_HOSTS_PATH = os.path.expanduser("~/.ssh/known_hosts")
INSTALL_HINT = "remote mode needs asyncssh — install it with: pip install 'metax-mxtop[remote]'"


def import_asyncssh() -> Any:
    try:
        import asyncssh
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(INSTALL_HINT) from exc
    return asyncssh


async def connect(host: str, *, connect_timeout: float = 10.0) -> Any:
    """Open a connection to ``host``, resolving HostName/User/Port/IdentityFile/
    ProxyJump from ``~/.ssh/config`` and authenticating via ssh-agent or keys.

    Password prompts are intentionally not wired up: key/agent auth is the
    "login once" model requested. Use ``ssh-copy-id`` for password-only nodes.
    """
    asyncssh = import_asyncssh()
    options: dict[str, Any] = {"connect_timeout": connect_timeout}
    if os.path.exists(SSH_CONFIG_PATH):
        options["config"] = [SSH_CONFIG_PATH]
    if os.path.exists(KNOWN_HOSTS_PATH):
        options["known_hosts"] = KNOWN_HOSTS_PATH
    return await asyncssh.connect(host, **options)
