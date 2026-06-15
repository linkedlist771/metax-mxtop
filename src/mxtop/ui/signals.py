from __future__ import annotations

import os
import signal as _signal

SIGNAL_KEYS: dict[str, tuple[str, int]] = {
    "K": ("SIGKILL", int(_signal.SIGKILL)),
    "T": ("SIGTERM", int(_signal.SIGTERM)),
    "I": ("SIGINT", int(_signal.SIGINT)),
}


def send_signal(pid: int, sig: int) -> str | None:
    """Send ``sig`` to ``pid``. Return None on success, else a human message."""
    try:
        os.kill(pid, sig)
        return None
    except ProcessLookupError:
        return f"pid {pid} no longer exists"
    except PermissionError:
        return f"permission denied for pid {pid}"
    except OSError as exc:
        return f"failed to signal pid {pid}: {exc}"
