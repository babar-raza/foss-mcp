"""TC-456 (REQ-G3-007): the ingestion image carries git-lfs.

words/net's reference library ships its binary dependencies (packages/Aspose.JavaAttributes,
packages/Aspose.CodePorting) as Git LFS objects. Without git-lfs on the image, `git checkout`
leaves 129-byte LFS pointer files in their place, and `dotnet build` fails with NETSDK1049
followed by thousands of missing-type errors.

Offline and static, the same continuation-joining approach as
tests/infra/test_ingestion_toolchain.py: it reads Dockerfile.ingestion as text, joins backslash
continuations into one logical instruction per RUN, and never runs docker. Every test in it
runs: none is skippable.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "Dockerfile.ingestion"

PINNED_BASE = re.compile(r"^FROM python:3\.13-slim@sha256:[0-9a-f]{64}$")


def _text() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


def _instructions() -> list[tuple[int, str]]:
    """Return (first line number, instruction text) with ``\\`` continuations joined and comments dropped."""
    out: list[tuple[int, str]] = []
    buffer: list[str] = []
    start = 0
    for number, raw in enumerate(_text().splitlines(), start=1):
        stripped = raw.strip()
        if not buffer and (not stripped or stripped.startswith("#")):
            continue
        if not buffer:
            start = number
        if stripped.endswith("\\"):
            buffer.append(stripped[:-1].strip())
            continue
        buffer.append(stripped)
        out.append((start, " ".join(part for part in buffer if part)))
        buffer = []
    if buffer:
        out.append((start, " ".join(part for part in buffer if part)))
    return out


def _run_lines_with(needle: str) -> list[tuple[int, str]]:
    return [(n, i) for n, i in _instructions() if i.startswith("RUN ") and needle in i]


def _git_lfs_install_lines() -> list[tuple[int, str]]:
    return [(n, i) for n, i in _run_lines_with("apt-get install") if re.search(r"\bgit-lfs\b", i)]


def _first_source_copy_line() -> int:
    for number, instruction in _instructions():
        if instruction.startswith("COPY "):
            return number
    raise AssertionError("Dockerfile.ingestion has no COPY instruction")


def _gpp_cmake_line() -> int:
    for number, instruction in _run_lines_with("apt-get install"):
        # TC-570: (?<!\S)g\+\+(?!\S) -> (?<!\S)g\+\+(?:=\S+)?(?!\S) (g++ is now pinned as g++=<version>)
        if re.search(r"(?<!\S)g\+\+(?:=\S+)?(?!\S)", instruction) and re.search(r"\bcmake\b", instruction):
            return number
    raise AssertionError("Dockerfile.ingestion has no g++/cmake apt-get install instruction")


def test_exactly_one_instruction_installs_git_lfs() -> None:
    installs = _git_lfs_install_lines()
    assert len(installs) == 1, installs


def test_git_lfs_install_uses_no_install_recommends_and_cleans_apt_lists() -> None:
    _, instruction = _git_lfs_install_lines()[0]
    assert "apt-get install -y --no-install-recommends" in instruction, instruction
    assert "rm -rf /var/lib/apt/lists/*" in instruction, instruction


def test_git_lfs_install_instruction_also_runs_git_lfs_version() -> None:
    _, instruction = _git_lfs_install_lines()[0]
    assert "git lfs version" in instruction, instruction


def test_git_lfs_layer_follows_gpp_cmake_and_precedes_first_copy() -> None:
    gpp_cmake_line = _gpp_cmake_line()
    lfs_line, _ = _git_lfs_install_lines()[0]
    first_copy_line = _first_source_copy_line()
    assert gpp_cmake_line < lfs_line < first_copy_line, (gpp_cmake_line, lfs_line, first_copy_line)


def test_no_instruction_installs_git_lfs_globally_or_system_wide() -> None:
    text = "\n".join(i for _, i in _instructions())
    assert "git lfs install --system" not in text
    assert "git lfs install --global" not in text
    for match in re.finditer(r"git\s+lfs\s+\S+[^\n]*", text):
        fragment = match.group(0)
        assert "--system" not in fragment, fragment
        assert "--global" not in fragment, fragment


def test_no_curl_or_wget_download_mentions_git_lfs() -> None:
    for number, instruction in _instructions():
        # TC-570: every instruction -> every instruction except the LABEL metadata (the toolchain label lists the
        # pinned apt packages, curl=... and git-lfs=... among them, and downloads nothing)
        if instruction.startswith("LABEL "):
            continue
        if re.search(r"\b(curl|wget)\b", instruction):
            assert "git-lfs" not in instruction and "git lfs" not in instruction, (number, instruction)


def test_the_single_from_line_is_unchanged() -> None:
    froms = [i for _, i in _instructions() if i.startswith("FROM ")]
    assert len(froms) == 1, froms
    assert PINNED_BASE.match(froms[0]), froms[0]
