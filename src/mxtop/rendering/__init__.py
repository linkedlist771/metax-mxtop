from __future__ import annotations

from mxtop.models import FrameSnapshot
from mxtop.ui.panels import render_main_screen
from mxtop.ui.state import UiState

WIDE_MIN_WIDTH = 110


def render_once(frame: FrameSnapshot, use_color: bool = True, width: int = 120) -> str:
    del use_color
    rendered = render_main_screen(frame, UiState(), width=width)
    return "\n".join(rendered.lines)
