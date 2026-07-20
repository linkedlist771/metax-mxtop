"""CI actions are immutable supply-chain inputs."""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def test_third_party_actions_are_pinned_to_full_commit_shas():
    mutable: list[str] = []
    uses_line = re.compile(r"^\s*uses:\s*([^\s#]+)", re.MULTILINE)
    for workflow in WORKFLOWS.glob("*.yml"):
        for reference in uses_line.findall(workflow.read_text()):
            if reference.startswith("./"):
                continue
            action, separator, revision = reference.rpartition("@")
            if not separator or not action or not FULL_SHA.fullmatch(revision):
                mutable.append(f"{workflow.name}: {reference}")
    assert not mutable, "mutable action references:\n" + "\n".join(mutable)


def test_dependabot_tracks_action_and_python_dependency_updates():
    config = (ROOT / ".github" / "dependabot.yml").read_text()
    assert "package-ecosystem: github-actions" in config
    assert "package-ecosystem: pip" in config
    assert "development-tooling:" in config
    assert config.count("interval: monthly") == 2
