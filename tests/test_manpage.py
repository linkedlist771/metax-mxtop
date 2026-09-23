"""Keep docs/mxtop.1 aligned with the actual CLI surface."""

from pathlib import Path

from mxtop.cli import build_parser

MAN_PAGE = Path(__file__).resolve().parent.parent / "docs" / "mxtop.1"


def test_man_page_documents_every_long_option():
    source = MAN_PAGE.read_text()
    # roff escapes hyphens as \-
    normalized = source.replace("\\-", "-")
    parser = build_parser()
    documented_exceptions = {"--help"}
    missing = [
        option
        for action in parser._actions  # noqa: SLF001 - argparse has no public option list
        for option in action.option_strings
        if option.startswith("--")
        and option not in documented_exceptions
        and option not in normalized
    ]
    assert not missing, f"man page is missing options: {missing}"


def test_man_page_mentions_key_environment_variables():
    normalized = MAN_PAGE.read_text().replace("\\-", "-")
    for name in (
        "MXTOP_MXSMI_PATH",
        "MXTOP_MONITOR_MODE",
        "MXTOP_AUTH_TOKEN",
        "MXTOP_CONFIG",
        "MACA_VISIBLE_DEVICES",
        "NO_COLOR",
    ):
        assert name in normalized, f"man page is missing environment variable {name}"
