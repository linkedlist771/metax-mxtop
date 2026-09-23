from mxtop.ui.text import cell_ellipsize, cell_ljust, cell_slice, cell_width, to_ascii


def test_to_ascii_translates_borders_bars_arrows_and_braille():
    output = to_ascii("╒═╤═╕ │ █▌░ ▲▼ ⣿")

    assert output == "+=+=+ | ||| ^v ="
    assert output.isascii()


def test_terminal_cell_helpers_handle_wide_and_combining_characters():
    text = "A训练e\u0301Z"

    assert cell_width(text) == 7
    assert cell_width(cell_ljust(text, 10)) == 10
    assert cell_slice(text, 1, 4) == "训练"
    assert cell_width(cell_ellipsize(text, 6)) == 6


def _reference_cell_width(text):
    from mxtop.ui.text import character_cell_width

    return sum(character_cell_width(character) for character in text)


def test_fast_paths_agree_with_per_character_widths():
    # Narrow ASCII, narrow box/bar/braille glyphs, and mixed wide/zero-width
    # text must all measure and slice exactly like the per-character walk.
    samples = [
        "",
        "plain ascii text",
        "tab\tand\x07bell",
        "line\nbreak\r",
        "│ 0  MetaX C500 ║ ██████▌░░ ⣿⣶⣤ ╘═╛",
        "A训练éZ",
        "zero​width",
        "▏▎▍▌▋▊▉█ 训",
    ]
    for text in samples:
        assert cell_width(text) == _reference_cell_width(text)
        for start in range(0, len(text) + 2):
            for width in (None, 0, 1, 3, 7, 40):
                sliced = cell_slice(text, start, width)
                if width is not None:
                    assert cell_width(sliced) <= width
        assert cell_width(cell_ljust(text, 30)) == max(30, 0)


def test_narrow_slicing_is_plain_string_slicing():
    text = "│ ██▌ ⣿⣤ ═══ ascii"
    for start in range(len(text) + 1):
        for width in range(len(text) + 1):
            assert cell_slice(text, start, width) == text[start : start + width]
    assert cell_slice(text, 3) == text[3:]


def test_slice_starting_inside_a_wide_glyph_respects_the_width():
    assert cell_slice("训练", 1, 0) == ""
    assert cell_slice("训练", 1, 1) == " "
    assert cell_slice("训练", 1, 3) == " 练"


def test_format_percent_fit_keeps_values_within_the_column():
    from mxtop.formatting import format_percent_fit

    assert format_percent_fit(None) == "N/A"
    assert format_percent_fit(42.1) == "42.1"
    assert format_percent_fit(312.4) == "312"
    assert format_percent_fit(9999.4) == "9999"
    assert format_percent_fit(12800.0) == "13k"
    assert all(
        len(format_percent_fit(value)) <= 4
        for value in (0.0, 5.55, 99.96, 100.0, 999.5, 1234.5, 99999.0)
    )
