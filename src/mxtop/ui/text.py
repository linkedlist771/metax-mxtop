"""Terminal text transformations shared by snapshot and curses rendering."""

from __future__ import annotations

from functools import lru_cache
import unicodedata


_ASCII_GLYPHS = str.maketrans(
    "═─╴╒╤╕╪╘╧╛┌┬┐┼└┴┘│╞╡├┤▏▎▍▌▋▊▉█░▲▼␤",
    "=--++++++++++++++||||||||||||||^v?",
)
ASCII_TRANSLATION = {
    **_ASCII_GLYPHS,
    **{codepoint: ord("=") for codepoint in range(0x2800, 0x2900)},
}


def to_ascii(text: str) -> str:
    """Translate box drawing, bars, arrows, and braille into ASCII."""

    return text.translate(ASCII_TRANSLATION)


@lru_cache(maxsize=None)
def character_cell_width(character: str) -> int:
    if not character or character in "\r\n":
        return 0
    if unicodedata.combining(character) or unicodedata.category(character) == "Cf":
        return 0
    return 2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1


# Characters already proven to occupy exactly one terminal cell. Nearly every
# rendered string is ASCII or built from such glyphs (box drawing, bars,
# braille), and for those cell arithmetic is plain ``len``/slicing. Growing
# the set is a single atomic ``set.add`` per glyph, safe under the GIL.
_NARROW_CHARACTERS: set[str] = set()


def _all_narrow(text: str) -> bool:
    """Whether every character of ``text`` is exactly one cell wide."""

    if text.isascii():
        return "\n" not in text and "\r" not in text
    characters = set(text)
    if characters <= _NARROW_CHARACTERS:
        return True
    for character in characters - _NARROW_CHARACTERS:
        if character_cell_width(character) != 1:
            return False
        _NARROW_CHARACTERS.add(character)
    return True


def cell_width(text: str) -> int:
    if _all_narrow(text):
        return len(text)
    return sum(map(character_cell_width, text))


def cell_slice(text: str, start: int = 0, width: int | None = None) -> str:
    """Slice text by terminal cells, padding a partially cut wide glyph with spaces."""

    start = max(0, start)
    end = None if width is None else start + max(0, width)
    if _all_narrow(text):
        return text[start:end]
    result: list[str] = []
    position = 0
    included_base = False
    for character in text:
        character_width = character_cell_width(character)
        if character_width == 0:
            if included_base and position >= start and (end is None or position <= end):
                result.append(character)
            continue
        next_position = position + character_width
        if next_position <= start:
            position = next_position
            included_base = False
            continue
        if end is not None and position >= end:
            break
        if position < start:
            # The slice begins inside a wide glyph: pad its visible half, but
            # never beyond the requested width.
            visible_end = next_position if end is None else min(next_position, end)
            result.append(" " * (visible_end - start))
            included_base = False
        elif end is not None and next_position > end:
            result.append(" " * (end - position))
            break
        else:
            result.append(character)
            included_base = True
        position = next_position
    return "".join(result)


def cell_ljust(text: str, width: int) -> str:
    clipped = cell_slice(text, 0, width)
    return clipped + " " * max(0, width - cell_width(clipped))


def cell_rjust(text: str, width: int) -> str:
    clipped = cell_slice(text, max(0, cell_width(text) - width), width)
    return " " * max(0, width - cell_width(clipped)) + clipped


def cell_ellipsize(text: str, width: int, marker: str = "..") -> str:
    if cell_width(text) <= width:
        return text
    marker_width = cell_width(marker)
    if width <= marker_width:
        return cell_slice(text, 0, width)
    return cell_slice(text, 0, width - marker_width) + marker
