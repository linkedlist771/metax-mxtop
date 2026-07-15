"""Explicit remote-node inventory helpers."""

from __future__ import annotations

from pathlib import Path


def load_hosts(nodes: list[str] | None, nodes_file: str | None) -> list[str]:
    """Load ordered, unique SSH hosts from CLI tokens and an optional file."""

    raw: list[str] = []
    for token in nodes or []:
        raw.extend(token.replace(",", " ").split())
    if nodes_file:
        for line in Path(nodes_file).read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                raw.extend(line.replace(",", " ").split())
    return merge_hosts(raw)


def merge_hosts(*groups: list[str]) -> list[str]:
    """Merge host groups without changing their first-seen order."""

    merged: dict[str, None] = {}
    for group in groups:
        for host in group:
            merged.setdefault(host, None)
    return list(merged)
