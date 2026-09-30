"""Repository hygiene tests (TC-127 / REQ-G2-047).

This repository is public (`github.com/babar-raza/foss-mcp`) and declares
``license = {text = "MIT"}`` in pyproject.toml, but until this card had no
LICENSE file embodying that declaration, and no .dockerignore - meaning every
`docker build` sent the entire working tree (.git history, evidence/, ops/,
every gatectl worktree, the local .venv) to the daemon as build context.

These tests assert both files exist with real content, not just presence:

  * LICENSE must contain the literal substring "MIT License" (the real,
    OSI-approved wording's own title line), not merely exist as an empty or
    placeholder file.
  * .dockerignore must exist and actually exclude at least .git and .venv -
    the two most consequential omissions (history and a multi-hundred-MB
    virtualenv) - so the file can't be an empty stub that happens to exist.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LICENSE_PATH = REPO_ROOT / "LICENSE"
DOCKERIGNORE_PATH = REPO_ROOT / ".dockerignore"


def _dockerignore_excludes(pattern_fragment: str, lines: list[str]) -> bool:
    """True if some non-comment, non-blank .dockerignore line names the given
    path fragment (e.g. ".git" or ".venv") as something to exclude.

    Deliberately tolerant of trailing slashes / leading "./" so this doesn't
    depend on one exact spelling, while still requiring the real path
    fragment to appear as its own token rather than as a substring of an
    unrelated entry.
    """
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        normalized = line.removeprefix("./").rstrip("/")
        if normalized == pattern_fragment:
            return True
    return False


def test_license_file_exists():
    assert LICENSE_PATH.is_file(), f"{LICENSE_PATH} does not exist"


def test_license_contains_real_mit_license_text():
    text = LICENSE_PATH.read_text(encoding="utf-8")
    assert "MIT License" in text, (
        "LICENSE does not contain the literal substring 'MIT License' - "
        "it must embody the real, OSI-approved MIT License text declared by "
        'pyproject.toml\'s license = {text = "MIT"}, not a paraphrase or '
        "placeholder."
    )
    # A handful of load-bearing phrases from the real OSI-approved wording,
    # so a truncated or paraphrased stand-in (which could still contain the
    # two-word substring "MIT License" in a heading) does not pass.
    for phrase in (
        "Permission is hereby granted, free of charge",
        'THE SOFTWARE IS PROVIDED "AS IS"',
        "WITHOUT WARRANTY OF ANY KIND",
    ):
        assert phrase in text, f"LICENSE is missing expected MIT License wording: {phrase!r}"


def test_license_names_this_projects_copyright_holder():
    text = LICENSE_PATH.read_text(encoding="utf-8")
    assert "Copyright (c)" in text, "LICENSE has no copyright line"


def test_dockerignore_file_exists():
    assert DOCKERIGNORE_PATH.is_file(), f"{DOCKERIGNORE_PATH} does not exist"


def test_dockerignore_excludes_git_and_venv():
    lines = DOCKERIGNORE_PATH.read_text(encoding="utf-8").splitlines()
    missing = [fragment for fragment in (".git", ".venv") if not _dockerignore_excludes(fragment, lines)]
    assert not missing, f".dockerignore does not exclude: {missing}"


def test_dockerignore_excludes_governance_and_cache_directories():
    """The other directories a real `docker build .` would otherwise
    needlessly send as context: supervisor/worker governance state and
    dev-tool caches, none of which any Dockerfile COPYs."""
    lines = DOCKERIGNORE_PATH.read_text(encoding="utf-8").splitlines()
    expected = [
        "evidence",
        "ops",
        "plans",
        "project",
        ".gatectl-worktrees",
        ".pytest_cache",
        "__pycache__",
    ]
    missing = [fragment for fragment in expected if not _dockerignore_excludes(fragment, lines)]
    assert not missing, f".dockerignore does not exclude: {missing}"


def test_dockerignore_does_not_exclude_paths_the_dockerfiles_actually_copy():
    """Dockerfile.serving and Dockerfile.ingestion COPY src/, config/, and
    infra/ directly - a .dockerignore that excluded those would silently
    break every real image build. This guards against that regression."""
    lines = DOCKERIGNORE_PATH.read_text(encoding="utf-8").splitlines()
    required_present = ["src", "config", "infra", "requirements.lock"]
    wrongly_excluded = [fragment for fragment in required_present if _dockerignore_excludes(fragment, lines)]
    assert not wrongly_excluded, f".dockerignore excludes path(s) a real Dockerfile COPYs: {wrongly_excluded}"
