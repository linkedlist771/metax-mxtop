"""Extract one version's Markdown release notes from CHANGELOG.md."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def extract_release_notes(version: str, changelog: str) -> str:
    heading = re.compile(
        rf"^## \[{re.escape(version)}\](?:[ \t]+-[ \t]+[^\n]*)?$",
        re.MULTILINE,
    )
    match = heading.search(changelog)
    if match is None:
        raise ValueError(f"CHANGELOG.md has no release heading for {version}")
    start = match.end()
    next_heading = re.search(r"^## \[", changelog[start:], re.MULTILINE)
    end = start + next_heading.start() if next_heading is not None else len(changelog)
    notes = changelog[start:end].strip()
    if not notes:
        raise ValueError(f"CHANGELOG.md release {version} has no notes")
    return notes + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", help="release tag, e.g. v0.1.26")
    parser.add_argument("--output", type=Path, help="write notes to this file")
    args = parser.parse_args(argv)
    if not args.tag.startswith("v"):
        parser.error("tag must start with v")
    try:
        notes = extract_release_notes(args.tag[1:], (ROOT / "CHANGELOG.md").read_text())
    except ValueError as exc:
        print(f"RELEASE ERROR: {exc}", file=sys.stderr)
        return 1
    if args.output is None:
        print(notes, end="")
    else:
        args.output.write_text(notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
