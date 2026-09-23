"""Completion scripts must stay in sync with the argparse surface."""

import pytest

from mxtop.cli import build_parser, main
from mxtop.completions import render_completion

SHELLS = ("bash", "zsh", "fish")


def _long_options():
    parser = build_parser()
    return [
        option
        for action in parser._actions  # noqa: SLF001
        for option in action.option_strings
        if option.startswith("--")
    ]


@pytest.mark.parametrize("shell", SHELLS)
def test_completion_covers_every_long_option(shell):
    script = render_completion(build_parser(), shell)
    needle = {
        "bash": lambda option: option in script,
        "zsh": lambda option: f"'{option}[" in script,
        "fish": lambda option: f"-l {option[2:]}" in script,
    }[shell]
    missing = [option for option in _long_options() if not needle(option)]
    assert not missing, f"{shell} completion is missing: {missing}"


@pytest.mark.parametrize("shell", SHELLS)
def test_completion_includes_choice_values(shell):
    script = render_completion(build_parser(), shell)
    for choice in ("pymxsml", "compact", "fish"):
        assert choice in script


def test_completion_suppresses_large_numeric_choice_ranges():
    script = render_completion(build_parser(), "bash")
    assert '"1 2 3' not in script


@pytest.mark.parametrize(
    ("shell", "needle"),
    (
        ("bash", '--tls-cert)\n            COMPREPLY=( $(compgen -f -- "$cur") )'),
        ("zsh", "'--tls-cert[PEM certificate chain for direct HTTPS]:file:_files'"),
        ("fish", "-l tls-cert -r -F"),
    ),
)
def test_completion_treats_tls_values_as_files(shell, needle):
    assert needle in render_completion(build_parser(), shell)


def test_cli_prints_completion_and_exits(capsys):
    rc = main(["--print-completion", "bash"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.startswith("# bash completion for mxtop")
    assert "complete -F _mxtop mxtop" in out


def test_render_completion_rejects_unknown_shell():
    with pytest.raises(ValueError):
        render_completion(build_parser(), "powershell")
