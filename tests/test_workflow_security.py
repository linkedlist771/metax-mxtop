"""CI actions are immutable supply-chain inputs."""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
WHEELS_WORKFLOW = WORKFLOWS / "wheels.yml"


def _line_indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _yaml_block(document: str, key: str) -> str:
    """Extract one YAML mapping block without requiring a YAML dependency."""

    lines = document.splitlines()
    key_line = re.compile(rf"^(?P<indent> *){re.escape(key)}:\s*(?:#.*)?$")
    for start, line in enumerate(lines):
        match = key_line.match(line)
        if match is None:
            continue
        base_indent = len(match.group("indent"))
        end = len(lines)
        for index in range(start + 1, len(lines)):
            candidate = lines[index]
            if not candidate.strip() or candidate.lstrip().startswith("#"):
                continue
            if _line_indent(candidate) <= base_indent:
                end = index
                break
        return "\n".join(lines[start:end])
    raise AssertionError(f"missing YAML block: {key}")


def _job_block(workflow: str, job: str) -> str:
    return _yaml_block(_yaml_block(workflow, "jobs"), job)


def _yaml_sequence(document: str, key: str) -> list[str]:
    lines = document.splitlines()
    key_line = re.compile(
        rf"^(?P<indent> *){re.escape(key)}:\s*(?P<value>.*?)\s*$"
    )
    for start, line in enumerate(lines):
        match = key_line.match(line)
        if match is None:
            continue
        value = match.group("value").split("#", 1)[0].strip()
        if value.startswith("[") and value.endswith("]"):
            return [
                token.strip().strip("'\"")
                for token in value[1:-1].split(",")
                if token.strip()
            ]
        if value:
            return [value.strip("'\"")]

        base_indent = len(match.group("indent"))
        values: list[str] = []
        for candidate in lines[start + 1 :]:
            if not candidate.strip() or candidate.lstrip().startswith("#"):
                continue
            if _line_indent(candidate) <= base_indent:
                break
            item = candidate.strip()
            if item.startswith("- "):
                values.append(item[2:].split("#", 1)[0].strip().strip("'\""))
        return values
    raise AssertionError(f"missing YAML sequence: {key}")


def _action_step(job: str, action: str) -> str:
    lines = job.splitlines()
    uses_line = re.compile(
        rf"^\s*(?:-\s*)?uses:\s*{re.escape(action)}@[^\s#]+"
    )
    for start, line in enumerate(lines):
        if uses_line.match(line) is None:
            continue
        uses_indent = _line_indent(line)
        end = len(lines)
        for index in range(start + 1, len(lines)):
            candidate = lines[index]
            if not candidate.strip() or candidate.lstrip().startswith("#"):
                continue
            if _line_indent(candidate) < uses_indent:
                end = index
                break
        return "\n".join(lines[start:end])
    raise AssertionError(f"missing action step: {action}")


def _yaml_scalar(document: str, key: str) -> str:
    value_line = re.compile(rf"^\s*{re.escape(key)}:\s*([^\s#]+)", re.MULTILINE)
    match = value_line.search(document)
    if match is None:
        raise AssertionError(f"missing YAML value: {key}")
    return match.group(1).strip("'\"")


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


def test_dependabot_tracks_action_python_and_browser_dependency_updates():
    config = (ROOT / ".github" / "dependabot.yml").read_text()
    assert "package-ecosystem: github-actions" in config
    assert "package-ecosystem: pip" in config
    assert "package-ecosystem: npm" in config
    assert "development-tooling:" in config
    assert config.count("interval: monthly") == 3


def test_codeql_scans_python_and_javascript_with_extended_queries():
    workflow = (WORKFLOWS / "codeql.yml").read_text()
    assert "- python" in workflow
    assert "- javascript-typescript" in workflow
    assert "queries: security-extended" in workflow
    assert "security-events: write" in workflow
    assert "head.repo.full_name == github.repository" in workflow
    assert "schedule:" in workflow
    assert "workflow_dispatch:" in workflow


def test_release_publishers_require_tests_and_the_single_distribution_build():
    workflow = WHEELS_WORKFLOW.read_text()
    expected_needs = ["test-matrix", "linux-x86_64-wheelhouse"]
    build_job = _job_block(workflow, "linux-x86_64-wheelhouse")

    for publisher in ("github-release", "pypi-release"):
        assert _yaml_sequence(_job_block(workflow, publisher), "needs") == expected_needs

    build_commands = re.findall(
        r"(?m)^\s*(?:run:\s*)?(python -m build[^\n]*)$",
        workflow,
    )
    assert build_commands == ["python -m build"]
    assert "python -m build" in build_job
    assert "python -m twine check dist/*" in build_job

    checksum_step = next(
        block
        for block in re.split(r"(?m)^\s*- name:", build_job)
        if "sha256sum" in block
    )
    assert "dist/*.whl" in checksum_step
    assert "dist/*.tar.gz" in checksum_step
    assert "wheelhouse" in checksum_step
    assert "SHA256SUMS.txt" in checksum_step

    upload_step = _action_step(build_job, "actions/upload-artifact")
    assert "dist/*.whl" in upload_step
    assert "dist/*.tar.gz" in upload_step
    assert "wheelhouse" in upload_step
    assert "SHA256SUMS.txt" in upload_step
    assert "release-notes.md" in upload_step
    assert "if-no-files-found: error" in upload_step


def test_dashboard_browser_suite_gates_the_distribution_build():
    workflow = WHEELS_WORKFLOW.read_text()
    build_job = _job_block(workflow, "linux-x86_64-wheelhouse")
    setup_node = _action_step(build_job, "actions/setup-node")

    assert _yaml_scalar(_yaml_block(setup_node, "with"), "node-version") == "22"
    assert "cache: npm" in setup_node
    assert "npm ci" in build_job
    assert "npx playwright install --with-deps --only-shell chromium" in build_job
    assert "npm run test:dashboard" in build_job
    assert build_job.index("npm run test:dashboard") < build_job.index(
        "python -m build"
    )
    diagnostics = build_job.split(
        "- name: Upload dashboard browser diagnostics", 1
    )[1]
    assert build_job.index("- name: Upload workflow artifact") < build_job.index(
        "- name: Upload dashboard browser diagnostics"
    )
    assert "if: failure()" in diagnostics
    assert "path: test-results/" in diagnostics
    assert "if-no-files-found: ignore" in diagnostics


def test_pypi_publishes_the_verified_build_artifact_without_rebuilding():
    workflow = WHEELS_WORKFLOW.read_text()
    build_job = _job_block(workflow, "linux-x86_64-wheelhouse")
    pypi_job = _job_block(workflow, "pypi-release")
    upload_step = _action_step(build_job, "actions/upload-artifact")
    download_step = _action_step(pypi_job, "actions/download-artifact")

    assert _yaml_scalar(_yaml_block(upload_step, "with"), "name") == _yaml_scalar(
        _yaml_block(download_step, "with"), "name"
    )
    assert _yaml_scalar(_yaml_block(download_step, "with"), "path") == "release-assets"

    forbidden = (
        "actions/checkout@",
        "actions/setup-python@",
        "pip install",
        "python -m build",
        "scripts/",
    )
    assert not [token for token in forbidden if token in pypi_job]
    assert workflow.count("id-token: write") == 1
    permissions = _yaml_block(pypi_job, "permissions")
    assert "id-token: write" in permissions
    assert "contents:" not in permissions
    checksum_step = next(
        block
        for block in re.split(r"(?m)^\s*- name:", pypi_job)
        if "sha256sum --check SHA256SUMS.txt" in block
    )
    assert "working-directory: release-assets" in checksum_step
    assert pypi_job.index("sha256sum --check") < pypi_job.index(
        "pypa/gh-action-pypi-publish@"
    )
    assert re.search(
        r"(?m)^\s*packages-dir:\s*['\"]?release-assets/dist/?['\"]?\s*$",
        pypi_job,
    )


def test_github_release_only_handles_version_tags_without_overwriting_assets():
    workflow = WHEELS_WORKFLOW.read_text()
    github_job = _job_block(workflow, "github-release")
    push_block = _yaml_block(_yaml_block(workflow, "on"), "push")

    assert _yaml_sequence(push_block, "tags") == ["v*"]
    assert "startsWith(github.ref, 'refs/tags/v')" in github_job
    assert "startsWith(github.ref, 'refs/tags/')" not in github_job
    assert "actions/checkout@" not in github_job
    assert "scripts/" not in github_job
    assert "release-assets/release-notes.md" in github_job
    assert "--clobber" not in github_job
    assert 'tag="build-${GITHUB_SHA}"' in github_job
