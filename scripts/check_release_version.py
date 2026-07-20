"""Validate that a release tag, package versions, and changelog agree."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.9/3.10
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent
VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+")


def project_version(root: Path = ROOT) -> str:
    document = tomllib.loads((root / "pyproject.toml").read_text())
    return str(document["project"]["version"])


def runtime_version(root: Path = ROOT) -> str:
    source = (root / "src" / "mxtop" / "__init__.py").read_text()
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', source, re.MULTILINE)
    if match is None:
        raise ValueError("src/mxtop/__init__.py has no __version__ assignment")
    return match.group(1)


def validate_release(tag: str, root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    if not tag.startswith("v") or not VERSION_PATTERN.fullmatch(tag[1:]):
        return [f"release tag must be vMAJOR.MINOR.PATCH, got {tag!r}"]
    tagged_version = tag[1:]
    package_version = project_version(root)
    module_version = runtime_version(root)
    if package_version != tagged_version:
        errors.append(
            f"tag {tag} does not match pyproject.toml version {package_version}"
        )
    if module_version != tagged_version:
        errors.append(
            f"tag {tag} does not match mxtop.__version__ {module_version}"
        )

    changelog = (root / "CHANGELOG.md").read_text()
    if f"## [{tagged_version}]" not in changelog:
        errors.append(f"CHANGELOG.md has no ## [{tagged_version}] release heading")
    if f"[{tagged_version}]:" not in changelog:
        errors.append(f"CHANGELOG.md has no [{tagged_version}]: comparison link")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", help="release tag, e.g. v0.1.26")
    args = parser.parse_args(argv)
    errors = validate_release(args.tag)
    if errors:
        for error in errors:
            print(f"RELEASE ERROR: {error}", file=sys.stderr)
        return 1
    print(f"release metadata is consistent for {args.tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
