"""G2/TC-223: docs/DEPLOYMENT.md and the layout section agree with the tools they describe.

The runbook is read here, so a tool that gains a flag or a script the runbook does not name fails a
test, and so does a runbook that drops a failure the card requires it to cover.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = REPO_ROOT / "docs" / "DEPLOYMENT.md"
LAYOUT = REPO_ROOT / "docs" / "REPOSITORY_LAYOUT.md"
DEPLOY_DIR = REPO_ROOT / "scripts" / "deploy"
TOOLS = {
    "install_pilots.py": DEPLOY_DIR / "install_pilots.py",
    "prove_pilots.py": DEPLOY_DIR / "prove_pilots.py",
}


def _runbook() -> str:
    return RUNBOOK.read_text(encoding="utf-8")


def _troubleshooting_section() -> str:
    text = _runbook()
    start = text.index("## Troubleshooting")
    end = text.index("## Known limits")
    return text[start:end]


def test_runbook_names_every_script_the_operator_runs():
    text = _runbook()
    for script in (
        "scripts/deploy/pilots.py",
        "scripts/deploy/install_pilots.py",
        "scripts/deploy/prove_pilots.py",
        "scripts/release/build_images.py",
        "scripts/poc/pilot_workflow_proof.py",
    ):
        assert script in text, f"runbook does not name {script}"


def test_runbook_covers_every_troubleshooting_failure_the_card_requires():
    section = _troubleshooting_section()
    required = (
        "FileNotFoundError",
        "/app/fixtures/<pilot>/api_surface.json",
        "HTTP 403",
        "rate limit exceeded",
        "Init:0/1",
        "port 8201 already in use",
        "Docker",
    )
    missing = [phrase for phrase in required if phrase not in section]
    assert not missing, f"troubleshooting table lacks: {missing}"


def test_runbook_states_the_known_limits_plainly():
    text = _runbook()
    assert "Known limits" in text
    for pilot in (
        "pdf_cpp",
        "cells_cpp",
        "words_python",
        "words_net",
        "slides_java",
        "cells_go",
        "cells_typescript",
        "jmap_rust",
    ):
        assert pilot in text, f"known limits do not name {pilot}"
    assert "1 Gi" in text


def test_runbook_flags_name_options_the_tools_really_accept():
    text = _runbook()
    for tool, path in TOOLS.items():
        source = path.read_text(encoding="utf-8")
        command_lines = [line for line in text.splitlines() if tool in line and "python" in line]
        assert command_lines, f"runbook shows no command for {tool}"
        for line in command_lines:
            for flag in re.findall(r"--[a-z][a-z-]*", line):
                assert f'"{flag}"' in source, f"runbook uses {flag} with {tool}, which does not accept it"


def test_runbook_covers_the_tool_options_it_documents():
    text = _runbook()
    for flag in (
        "--only",
        "--dry-run",
        "--parallel",
        "--timeout",
        "--base-port",
        "--report-dir",
        "--values-dir",
        "--namespace",
        "--image-tag",
    ):
        assert flag in text, f"runbook does not document {flag}"


def test_runbook_states_the_tools_never_remove_anything():
    text = _runbook()
    assert "never removes" in text or "never delete" in text
    assert "uninstall" in text.lower()


def test_layout_doc_names_the_deployment_directories():
    text = LAYOUT.read_text(encoding="utf-8")
    assert "scripts/deploy" in text
    assert "tests/deploy" in text
