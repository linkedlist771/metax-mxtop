"""Helpers for standards-compliant JSON output."""

from __future__ import annotations

import math
from typing import Any


def sanitize_json_value(value: Any) -> Any:
    """Replace non-finite floats recursively so strict JSON encoders accept them."""

    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: sanitize_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_json_value(item) for item in value]
    return value
