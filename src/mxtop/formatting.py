from __future__ import annotations

import math


def format_bytes(value: int | None) -> str:
    if value is None:
        return "N/A"
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    amount = float(value)
    for unit in units:
        if abs(amount) < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(amount)}B"
            if unit == "MiB":
                return f"{amount:.0f}MiB"
            return f"{amount:.1f}{unit}"
        amount /= 1024
    return f"{amount:.1f}TiB"


def format_compact_bytes(value: int | None) -> str:
    if value is None:
        return "N/A"
    amount = float(value)
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    for unit in units:
        if abs(amount) < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(amount)}B"
            if unit == "KiB":
                return f"{amount:.1f}KiB"
            if unit == "MiB":
                return f"{amount:.2f}MiB" if amount < 100 else f"{amount:.1f}MiB"
            return f"{amount:.2f}{unit}"
        amount /= 1024
    return f"{amount:.2f}TiB"


def format_mib(value: int | None) -> str:
    if value is None:
        return "N/A"
    amount = value / 1024**2
    if amount >= 1000:
        return f"{amount:.0f}MiB"
    if amount >= 100:
        return f"{amount:.1f}MiB"
    return f"{amount:.2f}MiB"


def _finite(value: float | None) -> bool:
    return value is not None and math.isfinite(float(value))


def format_percent(value: float | None) -> str:
    if not _finite(value):
        return "N/A"
    return f"{value:.0f}%"


def format_percent_precise(value: float | None) -> str:
    if not _finite(value):
        return "N/A"
    if abs(value - round(value)) < 0.05:
        return f"{value:.0f}%"
    return f"{value:.1f}%"


def format_percent_value(value: float | None) -> str:
    if not _finite(value):
        return "N/A"
    if abs(value - round(value)) < 0.05:
        return f"{value:.0f}"
    return f"{value:.1f}"


def format_float(value: float | None, unit: str) -> str:
    return "N/A" if value is None else f"{value:.0f}{unit}"


def format_duration(value: float | None) -> str:
    if value is None:
        return "N/A"
    seconds = max(0, int(value))
    if seconds >= 86400:
        return f"{seconds / 86400:.1f} days"
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


_SUBCELL_GLYPHS = " ▏▎▍▌▋▊▉"


def format_bar(value: float | None, width: int = 12) -> str:
    if width <= 0:
        return ""
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "░" * width
    percent = max(0.0, min(100.0, float(value)))
    eighths = round(8 * width * percent / 100)
    full, remainder = divmod(eighths, 8)
    bar = "█" * full
    if remainder:
        bar += _SUBCELL_GLYPHS[remainder]
    return bar + "░" * (width - len(bar))


def ellipsize(value: str | None, width: int, marker: str = "..") -> str:
    text = value or ""
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width <= len(marker):
        return text[:width]
    return text[: width - len(marker)] + marker
