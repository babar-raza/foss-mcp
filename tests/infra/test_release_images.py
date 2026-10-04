"""Offline checks for the release image build (TC-192 / REQ-G2-043).

These tests read committed source text only. They never run docker, and they
never run git against a real repository.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERIGNORE_PATH = REPO_ROOT / ".dockerignore"
BUILD_SCRIPT_PATH = REPO_ROOT / "scripts" / "release" / "build_images.py"
REVISION_LABEL = "org.opencontainers.image.revision"


def _build_script_source() -> str:
    return BUILD_SCRIPT_PATH.read_text(encoding="utf-8")


def test_dockerignore_excludes_nested_pycache():
    lines = DOCKERIGNORE_PATH.read_text(encoding="utf-8").splitlines()
    assert "**/__pycache__/" in [line.strip() for line in lines], (
        ".dockerignore has no literal line '**/__pycache__/'; nested "
        "src/**/__pycache__ .pyc files would be sent to the image build"
    )


def test_build_script_exports_head_with_git_archive_and_stamps_revision_label():
    source = _build_script_source()
    assert '"archive"' in source, "build script has no git archive call"
    assert '"git"' in source, "build script does not run git"
    assert REVISION_LABEL in source, f"build script does not set the {REVISION_LABEL} label"


def test_build_script_refuses_dirty_tree_by_guard_text():
    source = _build_script_source()
    tree = ast.parse(source, filename=str(BUILD_SCRIPT_PATH))
    guards = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "refuse_if_dirty"
    ]
    assert len(guards) == 1, "build script has no refuse_if_dirty guard"
    guard_text = ast.get_source_segment(source, guards[0]) or ""
    assert "git" in guard_text and '"status"' in guard_text, "dirty-tree guard does not call git status"
    assert "dirty" in guard_text, "dirty-tree guard does not refuse a dirty tree"
