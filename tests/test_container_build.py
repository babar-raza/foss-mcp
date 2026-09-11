"""Container build discipline: exactly two build paths, both hash-verified installs, a
non-root user and a HEALTHCHECK, the Helm chart's NetworkPolicy never conditionally disabled,
and a real docker build of the serving image succeeding.

network: true on this card, for exactly one reason: a real docker build genuinely needs the
network (pulling the base image, downloading pinned wheels). Every assertion below is on
committed artifacts (Dockerfiles, the Helm chart) or the deterministic outcome of a real,
local build - nothing is fetched at check time beyond what the build itself needs.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DOCKERFILES = ("Dockerfile.serving", "Dockerfile.ingestion")


def test_exactly_two_dockerfiles_exist() -> None:
    """Do NOT add a third: an unreferenced alternate Dockerfile is a confirmed crash-loop
    trap in the reference system, still sitting unused in its tree."""
    found = sorted(path.name for path in REPO_ROOT.glob("Dockerfile*") if path.is_file())
    assert found == sorted(DOCKERFILES), found


def test_both_dockerfiles_require_hashes() -> None:
    for name in DOCKERFILES:
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        assert "--require-hashes" in text, f"{name} does not enforce hash-verified installs"


def test_both_dockerfiles_declare_a_non_root_user() -> None:
    for name in DOCKERFILES:
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        match = re.search(r"^USER\s+(\S+)", text, re.MULTILINE)
        assert match is not None, f"{name} has no USER directive"
        assert match.group(1) not in ("root", "0"), f"{name} runs as root"


def test_both_dockerfiles_declare_a_healthcheck() -> None:
    for name in DOCKERFILES:
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        assert re.search(r"^HEALTHCHECK\b", text, re.MULTILINE), f"{name} has no HEALTHCHECK"


def test_the_helm_chart_never_disables_network_policy() -> None:
    """The reference overlay's defect: a values-driven toggle around the NetworkPolicy
    resource. This chart renders it unconditionally - reversing that defect, not copying it.
    """
    policy = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "templates" / "networkpolicy.yaml"
    assert policy.is_file()
    text = policy.read_text(encoding="utf-8")
    assert "kind: NetworkPolicy" in text
    # The one thing that would reproduce the reference overlay's defect: a Helm conditional
    # wrapped around the resource, letting a values flag turn it off. Comment lines are
    # stripped first, since this file's own header comment explains the rule by naming the
    # exact syntax it must not contain as *live* template code.
    code_lines = [line for line in text.splitlines() if not line.strip().startswith("#")]
    code_only = "\n".join(code_lines)
    assert not re.search(r"\{\{-?\s*if\b", code_only), "networkpolicy.yaml must render unconditionally"


def test_a_real_docker_build_of_the_serving_image_succeeds() -> None:
    image_tag = "foss-mcp-serving:test-build"
    try:
        result = subprocess.run(
            ["docker", "build", "-f", "Dockerfile.serving", "-t", image_tag, "."],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=900,
        )
        assert result.returncode == 0, (result.stdout + result.stderr)[-6000:]
    finally:
        subprocess.run(["docker", "rmi", "-f", image_tag], capture_output=True, text=True)
