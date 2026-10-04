"""Capability probes for taskcard `requires:` (redesign D1, DECISION_LOG 2026-10-04).

A card that needs a tool or a network path declares it. Before its checks run,
gatectl asks this module which declared capabilities are missing. A missing
capability is an ENVIRONMENT condition, not a code defect, so it yields the
verdict BLOCKED_ENV, which never counts against the card's attempt budget and
is never reported as FAIL.

Every probe is pure and side-effect free, and each one answers one question.
A probe that cannot run at all reports the capability as missing. It never
reports it as present on a guess.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from collections.abc import Callable

# The closed vocabulary. schemas/taskcard.schema.json enumerates the same names,
# and a test asserts the two lists agree, so they cannot drift apart.
KNOWN = (
    "bash",
    "cmake",
    "docker",
    "dotnet",
    "git",
    "go",
    "java",
    "msvc",
    "net",
    "node",
    "python",
    "rust",
)

_VSWHERE = r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"


def _which(name: str) -> bool:
    return shutil.which(name) is not None


def _python() -> bool:
    # gatectl itself is running under the interpreter, so this is always true.
    # It is probed anyway so a card can state the dependency explicitly.
    return bool(os.path.exists(sys.executable))


def _git() -> bool:
    return _which("git")


def _bash() -> bool:
    # On Windows, bash on PATH is often the WSL launcher. That launcher lives in
    # System32 or WindowsApps, and it broke .githooks in this project. Accept only
    # a bash that is not one of those launchers.
    path = shutil.which("bash")
    if path is None:
        return False
    lowered = path.lower()
    return "system32" not in lowered and "windowsapps" not in lowered


def _docker() -> bool:
    if not _which("docker"):
        return False
    try:
        r = subprocess.run(
            ["docker", "info"], capture_output=True, text=True, timeout=20
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def _net() -> bool:
    # A plain TCP connect to GitHub on 443. This is a reachability check, not an
    # authenticated one, and it proves nothing about what a card will fetch.
    try:
        with socket.create_connection(("github.com", 443), timeout=5):
            return True
    except OSError:
        return False


def _msvc() -> bool:
    # The MSVC C++ build tools are what link.exe and vcvarsall.bat belong to.
    # vswhere is the only supported way to find them without guessing a path.
    if not os.path.exists(_VSWHERE):
        return False
    try:
        r = subprocess.run(
            [
                _VSWHERE,
                "-latest",
                "-products",
                "*",
                "-requires",
                "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                "-property",
                "installationPath",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0 and bool(r.stdout.strip())


PROBES: dict[str, Callable[[], bool]] = {
    "bash": _bash,
    "cmake": lambda: _which("cmake"),
    "docker": _docker,
    "dotnet": lambda: _which("dotnet"),
    "git": _git,
    "go": lambda: _which("go"),
    "java": lambda: _which("java") and _which("javac"),
    "msvc": _msvc,
    "net": _net,
    "node": lambda: _which("node"),
    "python": _python,
    "rust": lambda: _which("cargo"),
}

assert set(PROBES) == set(KNOWN), "PROBES and KNOWN must name the same capabilities"


def missing(requires: list[str] | None) -> list[str]:
    """Return the declared capabilities that are NOT present, sorted.

    An empty or absent `requires` returns [], so every existing card behaves as
    it did before this module existed. An unknown name raises. A typo must never
    silently count as present or as missing.
    """
    if not requires:
        return []
    out = []
    for name in sorted(set(requires)):
        if name not in PROBES:
            raise ValueError(f"unknown capability {name!r}; known: {', '.join(KNOWN)}")
        if not PROBES[name]():
            out.append(name)
    return out
