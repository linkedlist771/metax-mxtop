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
