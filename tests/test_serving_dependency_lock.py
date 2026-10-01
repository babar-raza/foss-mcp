"""REQ-G2-047 (TC-138): the serving image must install only its own real runtime
dependencies, never dev/ops/ingestion-only tooling.

Confirmed directly in this repo before this card: requirements.in lumps PyYAML/jsonschema
(ops/gatectl.py only), pytest/ruff/mypy (dev-only) and tree-sitter* (extraction/ingestion-only)
together with mcp (the only real runtime dependency infra/serve_http.py or
infra/serve_stdio.py actually imports) into one undifferentiated requirements.lock, and
Dockerfile.serving installed that FULL lock - shipping pytest/mypy/ruff/tree-sitter into the
production serving image.

This test proves the fix: a separate requirements-serving.in/.lock pair naming and pinning
only the real serving-path dependencies, and Dockerfile.serving wired to install from it
instead of the full lock.

network: true on this card, for one reason: a real docker build genuinely needs the network
(pulling the base image, resolving wheels). Every assertion below is on committed artifacts
(requirements-serving.in, requirements-serving.lock, Dockerfile.serving) or the deterministic
outcome of a real, local build - nothing is fetched at check time beyond what the build itself
needs.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

# Package name prefixes that must NEVER appear in the serving-only lock: dev/ops tooling
# and extraction/ingestion-only dependencies pinned in requirements.lock but never imported
# by the serving path (infra/serve_http.py, infra/serve_stdio.py, or anything reachable from
# them under src/foss_mcp/mcp/, src/foss_mcp/indexing/, src/foss_mcp/extraction/, and
# src/foss_mcp/normalization/).
FORBIDDEN_PACKAGE_PREFIXES = ("pytest", "mypy", "ruff", "tree-sitter")

# A pinned requirement line looks like "package-name==1.2.3 \" (uv's --generate-hashes
# format continues each hash on its own indented line below it).
PINNED_LINE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==", re.MULTILINE)


def _serving_lock_text() -> str:
    path = REPO_ROOT / "requirements-serving.lock"
    assert path.is_file(), "requirements-serving.lock does not exist"
    return path.read_text(encoding="utf-8")


def _pinned_package_names(lock_text: str) -> set[str]:
    return {match.group(1).lower() for match in PINNED_LINE.finditer(lock_text)}


def test_serving_lock_excludes_dev_ops_and_ingestion_only_packages() -> None:
    names = _pinned_package_names(_serving_lock_text())
    for forbidden_prefix in FORBIDDEN_PACKAGE_PREFIXES:
        offenders = [name for name in names if name.startswith(forbidden_prefix)]
        assert not offenders, (
            f"requirements-serving.lock pins {offenders}, a dev/ops/ingestion-only "
            f"dependency the serving image must never ship"
        )


def test_serving_lock_includes_the_real_runtime_dependency() -> None:
    names = _pinned_package_names(_serving_lock_text())
    assert "mcp" in names, "requirements-serving.lock does not pin mcp, the real runtime dependency"


def test_serving_lock_is_hash_pinned() -> None:
    text = _serving_lock_text()
    assert "--hash=sha256:" in text, "requirements-serving.lock is not hash-pinned"


def test_dockerfile_serving_installs_from_the_serving_only_lock() -> None:
    text = (REPO_ROOT / "Dockerfile.serving").read_text(encoding="utf-8")
    assert "COPY requirements-serving.lock" in text, (
        "Dockerfile.serving does not COPY requirements-serving.lock"
    )
    assert re.search(r"pip install .*--require-hashes.*-r requirements-serving\.lock", text), (
        "Dockerfile.serving does not install --require-hashes from requirements-serving.lock"
    )
    # The defect this card fixes: a COPY or RUN line installing the FULL, undifferentiated
    # requirements.lock. Only COPY/RUN lines are checked (not comments, which legitimately
    # discuss requirements.lock by name) - and only a literal "requirements.lock" token, which
    # does not match "requirements-serving.lock".
    instruction_lines = [
        line
        for line in text.splitlines()
        if line.strip().startswith("COPY") or line.strip().startswith("RUN")
    ]
    offending = [line for line in instruction_lines if re.search(r"\brequirements\.lock\b", line)]
    assert not offending, f"Dockerfile.serving still installs from the full requirements.lock: {offending}"


def test_a_real_docker_build_of_the_serving_image_can_still_import_the_server() -> None:
    """A slimmer lock that silently breaks the real image is a regression, not an
    improvement: build the real image and confirm it can still import foss_mcp.mcp.server.
    """
    image_tag = f"foss-mcp-serving-test:{os.getpid()}"
    try:
        build_result = subprocess.run(
            ["docker", "build", "-f", "Dockerfile.serving", "-t", image_tag, "."],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=900,
        )
        assert build_result.returncode == 0, (build_result.stdout + build_result.stderr)[-6000:]

        run_result = subprocess.run(
            ["docker", "run", "--rm", image_tag, "python", "-c", "import foss_mcp.mcp.server"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        assert run_result.returncode == 0, (
            "built serving image cannot import foss_mcp.mcp.server: "
            + (run_result.stdout + run_result.stderr)[-4000:]
        )
    finally:
        subprocess.run(["docker", "rmi", "-f", image_tag], capture_output=True, text=True)
