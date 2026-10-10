"""Container build discipline: exactly two build paths, both hash-verified installs, a
non-root user and a HEALTHCHECK, the Helm chart's NetworkPolicy never conditionally disabled,
and a real docker build of the serving image succeeding.

network: true on this card, for exactly one reason: a real docker build genuinely needs the
network (pulling the base image, downloading pinned wheels). Every assertion below is on
committed artifacts (Dockerfiles, the Helm chart) or the deterministic outcome of a real,
local build - nothing is fetched at check time beyond what the build itself needs.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import time
import urllib.error
import urllib.request
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
    image_tag = f"foss-mcp-serving:test-build-{os.getpid()}"
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


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_a_real_docker_build_of_the_demo_target_is_self_contained_and_serves_real_content() -> None:
    """TC-329: the `demo` stage now COPYs infra/demo-manifests/ (committed to this repo)
    instead of reading a Buildx named build-context supplied only at build time - so this
    build deliberately passes NO --build-context flag, proving it is genuinely self-contained
    from committed data alone. The live /readyz check (AGENTS.md's Integration-and-liveness
    live-content smoke test) proves the built container really serves a real, baked-in
    generation - not just that the image built."""
    image_tag = f"foss-mcp-serving:test-build-demo-{os.getpid()}"
    container_name = f"foss-mcp-serving-demo-test-{os.getpid()}"
    port = _free_port()
    try:
        build_result = subprocess.run(
            ["docker", "build", "-f", "Dockerfile.serving", "--target", "demo", "-t", image_tag, "."],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=900,
        )
        assert build_result.returncode == 0, (build_result.stdout + build_result.stderr)[-6000:]

        run_result = subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                container_name,
                "-p",
                f"{port}:8080",
                "-e",
                "FOSS_MCP_FAMILY=pdf",
                "-e",
                "FOSS_MCP_PLATFORM=net",
                image_tag,
                "python",
                "/app/infra/serve_http.py",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        assert run_result.returncode == 0, (run_result.stdout + run_result.stderr)[-2000:]

        deadline = time.monotonic() + 30
        status: int | None = None
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"http://localhost:{port}/readyz", timeout=3) as response:
                    status = response.status
                break
            except (urllib.error.URLError, OSError) as exc:
                last_error = exc
                time.sleep(1)
        assert status == 200, f"/readyz never returned 200 (last error: {last_error})"
    finally:
        subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, text=True)
        subprocess.run(["docker", "rmi", "-f", image_tag], capture_output=True, text=True)


def test_a_real_docker_build_of_the_ingestion_image_has_working_toolchains() -> None:
    """REQ-G2-048 (TC-097): Dockerfile.ingestion was generalized to add real toolchains
    for the 4 remaining original pilots that need one beyond the base image's own python3
    (pdf/typescript, pdf/java, pdf/go, cells/rust - slides/python needs nothing extra).
    This proves each toolchain genuinely works INSIDE the built image, by really running its
    own version command in a real container - not just that the Dockerfile has right-looking
    RUN lines.
    """
    image_tag = f"foss-mcp-ingestion:test-build-{os.getpid()}"
    version_commands = [
        ["node", "--version"],
        ["npm", "--version"],
        ["java", "-version"],
        ["mvn", "--version"],
        ["go", "version"],
        ["cargo", "--version"],
        ["rustc", "--version"],
    ]
    try:
        build_result = subprocess.run(
            ["docker", "build", "-f", "Dockerfile.ingestion", "-t", image_tag, "."],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1800,
        )
        assert build_result.returncode == 0, (build_result.stdout + build_result.stderr)[-6000:]

        for command in version_commands:
            run_result = subprocess.run(
                ["docker", "run", "--rm", "--entrypoint", command[0], image_tag, *command[1:]],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
            assert run_result.returncode == 0, (
                f"{' '.join(command)} failed: " + (run_result.stdout + run_result.stderr)[-2000:]
            )
    finally:
        subprocess.run(["docker", "rmi", "-f", image_tag], capture_output=True, text=True)


def test_a_real_docker_build_of_the_ingestion_image_has_all_pilot_fixtures() -> None:
    """REQ-G2-050 (TC-103): TC-098's own worker found that Dockerfile.ingestion only ever
    COPYed pdf/net's own fixture files, so every other pilot's ingest-<pilot> compose service
    failed immediately with FileNotFoundError. This proves the 10 new COPY lines (2 per pilot,
    for pdf/typescript, pdf/java, pdf/go, slides/python, cells/rust) genuinely land their
    fixture files at the exact destinations each pilot's own build_chunks.py
    --api-surface/--furnished-page flags expect, INSIDE a really-built image - not just that
    the Dockerfile text looks right.
    """
    image_tag = f"foss-mcp-ingestion:test-build-fixtures-{os.getpid()}"
    pilots = ("pdf_typescript", "pdf_java", "pdf_go", "slides_python", "cells_rust")
    expected_paths = [f"/app/fixtures/{pilot}/api_surface.json" for pilot in pilots] + [
        f"/app/fixtures/furnished/{pilot}/pages/_index.md" for pilot in pilots
    ]
    try:
        build_result = subprocess.run(
            ["docker", "build", "-f", "Dockerfile.ingestion", "-t", image_tag, "."],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1800,
        )
        assert build_result.returncode == 0, (build_result.stdout + build_result.stderr)[-6000:]

        for path in expected_paths:
            run_result = subprocess.run(
                ["docker", "run", "--rm", "--entrypoint", "test", image_tag, "-f", path],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
            assert run_result.returncode == 0, (
                f"expected fixture missing at {path}: " + (run_result.stdout + run_result.stderr)[-2000:]
            )
    finally:
        subprocess.run(["docker", "rmi", "-f", image_tag], capture_output=True, text=True)
