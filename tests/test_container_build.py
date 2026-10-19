"""Container build discipline: exactly two build paths, both hash-verified installs, a
non-root user and a HEALTHCHECK, the Helm chart's NetworkPolicy never conditionally disabled,
and a real docker build of the serving image succeeding.

network: true on this card, for exactly one reason: a real docker build genuinely needs the
network (pulling the base image, downloading pinned wheels). Every assertion below is on
committed artifacts (Dockerfiles, the Helm chart) or the deterministic outcome of a real,
local build - nothing is fetched at check time beyond what the build itself needs.
"""

from __future__ import annotations

import fnmatch
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
                "FOSS_MCP_PILOT=pdf/net",
                image_tag,
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


def test_a_real_docker_build_of_the_demo_target_with_no_pilot_lists_the_baked_pilots_and_exits_two() -> None:
    """TC-350: the demo stage's ENTRYPOINT is infra/demo_entrypoint.py with an empty CMD, so a bare
    `docker run IMAGE` must not exit 0 silently (the defect the first real run exposed) and must not
    guess an identity: it prints the usage and the pilots actually baked in, and exits 2."""
    image_tag = f"foss-mcp-serving:test-build-demo-nopilot-{os.getpid()}"
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
            ["docker", "run", "--rm", image_tag],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        output = run_result.stdout + run_result.stderr
        assert run_result.returncode == 2, f"exit {run_result.returncode}: {output[-2000:]}"
        listed = set(re.findall(r"^\s*(\S+/\S+) source_kind=", output, re.MULTILINE))
        assert "pdf/net" in listed, output[-2000:]
        # re-pinned by TC-551: 39 -> 31
        assert len(listed - {"pdf/net"}) >= 31, f"only {len(listed)} pilots listed: {sorted(listed)}"
    finally:
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


# --- G3/TC-564 (RD3-121, RD3-126, RD3-124): static assertions about the Dockerfiles, which run without docker ---


def _dockerfile(name: str) -> str:
    return (REPO_ROOT / name).read_text(encoding="utf-8")


def _stage(text: str, name: str) -> str:
    """The instructions of the stage ``FROM ... AS <name>``, up to the next FROM."""
    match = re.search(rf"^FROM\s+\S+\s+AS\s+{re.escape(name)}\s*$", text, re.MULTILINE)
    assert match is not None, f"no stage named {name}"
    following = re.search(r"^FROM\s", text[match.end() :], re.MULTILINE)
    return text[match.end() : match.end() + following.start()] if following else text[match.end() :]


def _dockerignore_patterns() -> list[str]:
    lines = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


def _is_ignored(path: str, patterns: list[str]) -> bool:
    """Whether ``.dockerignore`` excludes *path* or one of its parent directories. A pattern ending in a slash
    names a directory; ``**/`` matches at any depth. (Docker's last-match-wins ``!`` re-includes are not used
    in this file.)"""
    parts = path.split("/")
    candidates = ["/".join(parts[: index + 1]) for index in range(len(parts))]
    for pattern in patterns:
        if pattern.startswith("!"):
            continue
        bare = pattern.rstrip("/")
        for candidate in candidates:
            if fnmatch.fnmatch(candidate, bare) or fnmatch.fnmatch(candidate, bare.removeprefix("**/")):
                return True
    return False


def test_both_images_copy_the_rollback_tool_to_the_path_the_runbook_runs() -> None:
    """docs/DEPLOYMENT.md tells the operator to run ``python /app/infra/rollback.py``. Round 3 found it in no
    image, so the documented recovery could not run."""
    runbook = (REPO_ROOT / "docs" / "DEPLOYMENT.md").read_text(encoding="utf-8")
    assert "/app/infra/rollback.py" in runbook
    for name in DOCKERFILES:
        text = _dockerfile(name)
        assert re.search(r"^COPY infra/rollback\.py \./infra/rollback\.py$", text, re.MULTILINE), name
        assert re.search(r"^WORKDIR /app$", text, re.MULTILINE), f"{name}: ./infra resolves under /app"
        assert re.search(r"^ENV PYTHONPATH=/app/src$", text, re.MULTILINE), f"{name}: foss_mcp must import"


def test_the_dockerignore_does_not_exclude_the_rollback_tool_or_anything_it_imports() -> None:
    patterns = _dockerignore_patterns()
    assert patterns, "the parser found no patterns"
    for needed in (
        "infra/rollback.py",
        "src/foss_mcp/indexing/generation_manifest.py",
        "src/foss_mcp/mcp/health.py",
    ):
        assert not _is_ignored(needed, patterns), f".dockerignore excludes {needed}"
    # The matcher itself works: the directories the file excludes are seen as excluded.
    assert _is_ignored("docs/DEPLOYMENT.md", patterns) and _is_ignored("tests/fixtures/bundles/x", patterns)


def test_both_images_take_the_build_revision_from_a_build_argument_and_set_it_in_the_environment() -> None:
    for name in DOCKERFILES:
        text = _dockerfile(name)
        arg = re.search(r'^ARG FOSS_MCP_BUILD_REVISION=""$', text, re.MULTILINE)
        env = re.search(r"^ENV FOSS_MCP_BUILD_REVISION=\$\{FOSS_MCP_BUILD_REVISION\}$", text, re.MULTILINE)
        assert arg and env, name
        assert arg.start() < env.start(), f"{name}: the ARG must precede the ENV that reads it"


def test_the_demo_stage_hands_the_baked_data_to_root_and_returns_to_the_unprivileged_user() -> None:
    """RD3-126: /data and /data/manifests belonged to the serving user, so a peer running the image without
    ``--read-only`` could delete or replace the baked sidecars."""
    stage = _stage(_dockerfile("Dockerfile.serving"), "demo")
    copy_data = stage.index("COPY infra/demo-manifests/ /data/manifests")
    to_root = stage.index("USER root")
    chown = stage.index("chown -R root:root /data")
    chmod_dirs = stage.index("find /data/manifests -type d -exec chmod 0555")
    chmod_files = stage.index("find /data/manifests -type f -exec chmod 0444")
    back = stage.rindex("USER foss-mcp")
    entrypoint = stage.index("ENTRYPOINT")
    assert copy_data < to_root < chown < chmod_dirs < chmod_files < back < entrypoint
    users = re.findall(r"^USER\s+(\S+)", stage, re.MULTILINE)
    assert users == ["root", "foss-mcp"], "the stage ends as the unprivileged user"


def test_the_runtime_stage_still_gives_a_mounted_claim_to_the_serving_user() -> None:
    """The chart mounts a volume at /data/manifests for the runtime stage: it must stay writable by the
    serving user there (only the baked demo data is handed to root)."""
    runtime = _stage(_dockerfile("Dockerfile.serving"), "runtime")
    assert "chown -R foss-mcp:foss-mcp /data" in runtime


def test_no_stage_of_either_dockerfile_ends_as_root() -> None:
    for name in DOCKERFILES:
        text = _dockerfile(name)
        stages = re.split(r"^FROM\s", text, flags=re.MULTILINE)[1:]
        for stage in stages:
            users = re.findall(r"^USER\s+(\S+)", stage, re.MULTILINE)
            if users:  # a stage with no USER of its own (the trailing re-anchor) inherits its base's
                assert users[-1] not in ("root", "0"), f"{name}: a stage ends as root"
