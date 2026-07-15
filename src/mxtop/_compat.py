"""Small runtime compatibility constants for supported Python versions."""

from __future__ import annotations

import sys

DATACLASS_SLOTS = {"slots": True} if sys.version_info >= (3, 10) else {}
