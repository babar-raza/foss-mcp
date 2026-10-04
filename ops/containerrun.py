"""Hermetic check runner (redesign D2, DECISION_LOG 2026-10-04).

The host runner inherits the host PATH and an absolute venv, and it treats
"offline" as a dead HTTP proxy. A dead proxy does not stop a process from opening
a socket, so the offline guarantee was weaker than it looked. This runner gives
each check a container with the pinned toolchain and, for an offline card,
`--network none`, which is a real network boundary.

Stage A (this commit) is OPT-IN. `FOSS_MCP_VERIFY_RUNNER=container` selects it.
The host runner stays the default until the environment matrix in the decision
log shows identical verdicts on the same cards. Changing the default is a
separate, recorded decision, not part of this module.

Cards that need a host-only capability (`msvc`, `docker`) cannot run in the
container, so they stay on the host runner. The split is explicit, not hidden.
"""

from __future__ import annotations

import os
import posixpath

VERIFY_IMAGE = "foss-mcp-verify:local"
MOUNT_WORK = "/work"
MOUNT_JUNIT = "/junit"
CONTAINER_PYTHON = "python"

# Capabilities the container cannot provide, so such a card stays on the host.
HOST_ONLY = frozenset({"msvc", "docker"})

_MODES = ("host", "container")


def runner_mode() -> str:
    mode = os.environ.get("FOSS_MCP_VERIFY_RUNNER", "host")
    if mode not in _MODES:
        raise ValueError(f"FOSS_MCP_VERIFY_RUNNER must be one of {_MODES}, not {mode!r}")
    return mode


def eligible(card: dict) -> tuple[bool, str]:
    """True when the card can run in the container. Otherwise, the reason it cannot."""
    needs = sorted(HOST_ONLY & set(card.get("requires") or []))
    if needs:
        return False, "needs host-only capability: " + ", ".join(needs)
    return True, ""


def container_command(command: str, junit_name: str | None) -> str:
    """The shell line a check runs inside the container.

    `{python}` becomes the image's python, so no host interpreter path leaks in.
    A JUnit file is written under the mounted /junit directory, which the host can read.
    """
    cmd = command.replace("{python}", CONTAINER_PYTHON)
    if junit_name is not None:
        cmd = f'{cmd} "--junitxml={MOUNT_JUNIT}/{junit_name}"'
    return cmd


def container_env(offline: bool, canonical: dict) -> dict:
    """The only environment a container check sees. Nothing from the host PATH."""
    env = dict(canonical)
    env["PYTHONPATH"] = f"{MOUNT_WORK}/src"
    env["HOME"] = "/tmp"
    if offline:
        env["GATECTL_OFFLINE"] = "1"
    return env


def docker_run_argv(
    *,
    workdir: str,
    junit_dir: str,
    cwd_rel: str,
    command: str,
    env: dict,
    offline: bool,
    image: str = VERIFY_IMAGE,
) -> list[str]:
    """The exact `docker run` argv for one check. A list, so no host shell touches it."""
    work = posixpath.normpath(posixpath.join(MOUNT_WORK, cwd_rel))
    argv = [
        "docker", "run", "--rm",
        "--entrypoint", "sh",
        "--network", "none" if offline else "bridge",
        "-v", f"{workdir}:{MOUNT_WORK}",
        "-v", f"{junit_dir}:{MOUNT_JUNIT}",
        "-w", work,
    ]
    for key in sorted(env):
        argv += ["-e", f"{key}={env[key]}"]
    argv += [image, "-c", command]
    return argv
