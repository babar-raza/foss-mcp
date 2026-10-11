"""TC-377 (REQ-G3-005): the ingestion image carries .NET SDK 10 beside 8.0 and a baked Python 3.12.

Offline and static: it reads Dockerfile.ingestion as text and asserts the toolchain layers are present, pinned,
and ordered before the source COPYs, so a source-only change never rebuilds them. It never runs docker, and
every test in it runs: none is skippable.

Why the image needs this: slides/net, email/net and 3d/net target .NET 10 (a lone SDK 8.0 fails with
NETSDK1045) and words/python declares ``requires-python <3.13,>=3.10`` while the base image only has 3.13.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "Dockerfile.ingestion"

DOTNET_DIR = "/usr/share/dotnet"
PY_DIR = "/opt/python-versions"
PINNED_BASE = re.compile(r"^FROM python:3\.13-slim@sha256:[0-9a-f]{64}$")
CHANNEL = re.compile(r"--channel\s+(\S+)")
REAL_CHANNEL = re.compile(r"^\d+\.\d+$")
EXACT_VERSION = re.compile(r"^\d+(\.\d+){1,3}$")


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


def _dotnet_installs() -> list[tuple[int, str, str]]:
    """Return (line, channel, install-dir) for every dotnet-install.sh invocation."""
    found: list[tuple[int, str, str]] = []
    for number, instruction in _run_lines_with("/tmp/dotnet-install.sh --channel"):
        channel = CHANNEL.search(instruction)
        install_dir = re.search(r"--install-dir\s+(\S+)", instruction)
        assert channel and install_dir, f"line {number}: dotnet-install.sh without --channel/--install-dir"
        found.append((number, channel.group(1), install_dir.group(1)))
    return found


def _arg_default(name: str) -> str | None:
    for _, instruction in _instructions():
        match = re.match(rf"^ARG {re.escape(name)}=(\S+)$", instruction)
        if match:
            return match.group(1)
    return None


def _first_source_copy_line() -> int:
    for number, instruction in _instructions():
        if instruction.startswith("COPY "):
            return number
    raise AssertionError("Dockerfile.ingestion has no COPY instruction")


def test_the_dotnet_8_install_is_still_present() -> None:
    installs = _dotnet_installs()
    assert any(channel == "8.0" and install_dir == DOTNET_DIR for _, channel, install_dir in installs), (
        installs
    )


def test_a_dotnet_10_install_is_present_in_the_same_install_directory() -> None:
    installs = _dotnet_installs()
    ten = [(n, d) for n, channel, d in installs if channel == "10.0"]
    assert ten, f"no --channel 10.0 install: {installs}"
    assert all(d == DOTNET_DIR for _, d in ten), ten
    # One dotnet serves both SDKs: the 8.0 and 10.0 installs must share a directory.
    assert {d for _, _, d in installs} == {DOTNET_DIR}, installs


def test_every_dotnet_channel_is_a_real_channel_string() -> None:
    for number, channel, _ in _dotnet_installs():
        assert REAL_CHANNEL.match(channel), f"line {number}: channel {channel!r} is not a numeric channel"
        assert channel.lower() not in {"latest", "lts", "sts"}


def test_no_global_json_is_added_to_the_image() -> None:
    assert "global.json" not in " ".join(i for _, i in _instructions())


def test_uv_is_installed_with_an_exact_pinned_version() -> None:
    uv_installs = _run_lines_with("pip install")
    uv_installs = [(n, i) for n, i in uv_installs if re.search(r"\buv\b", i)]
    assert uv_installs, "uv is not pip-installed"
    for number, instruction in uv_installs:
        match = re.search(r"\buv==(\S+)", instruction)
        assert match, f"line {number}: uv is installed without an exact == pin"
        version = match.group(1)
        if version.startswith("${") and version.endswith("}"):
            name = version[2:-1]
            resolved = _arg_default(name)
            assert resolved is not None, f"line {number}: ARG {name} has no default"
            version = resolved
        assert EXACT_VERSION.match(version), f"line {number}: uv pin {version!r} is not an exact version"
        assert version != "latest"


def test_python_3_12_is_installed_with_uv_into_a_fixed_directory() -> None:
    installs = _run_lines_with("uv python install 3.12")
    assert len(installs) == 1, installs
    _, instruction = installs[0]
    assert f"--install-dir {PY_DIR}" in instruction, instruction


def test_python3_12_is_exposed_on_the_path_by_a_symlink_or_copy() -> None:
    installs = _run_lines_with("uv python install 3.12")
    _, instruction = installs[0]
    target = "/usr/local/bin/python3.12"
    linked = re.search(rf"\bln\s+-s\S*\s+.*{re.escape(target)}", instruction)
    copied = re.search(rf"\bcp\s+.*{re.escape(target)}", instruction)
    assert linked or copied, instruction
    # The same layer must prove the interpreter runs, so a broken link fails the build, not the verifier.
    assert "python3.12 --version" in instruction


def test_the_base_image_line_is_unchanged() -> None:
    froms = [i for _, i in _instructions() if i.startswith("FROM ")]
    assert len(froms) == 1, froms
    assert PINNED_BASE.match(froms[0]), froms[0]


def test_no_latest_tag_or_unpinned_download_is_introduced() -> None:
    text = "\n".join(i for _, i in _instructions())
    assert ":latest" not in text
    assert "--version latest" not in text
    assert not re.search(r"pip install[^&]*\buv\s*(?:$|&&|\\)", text), "uv installed without a pin"
    assert not re.search(r"\buv\s+python\s+install\s+(?:--\S+\s+)*(?:latest|--all)", text)


def test_the_toolchain_layers_follow_the_dotnet_layer_and_precede_the_source_copies() -> None:
    dotnet_8 = min(n for n, channel, _ in _dotnet_installs() if channel == "8.0")
    dotnet_10 = min(n for n, channel, _ in _dotnet_installs() if channel == "10.0")
    python_layer = _run_lines_with("uv python install 3.12")[0][0]
    first_copy = _first_source_copy_line()
    assert dotnet_8 < dotnet_10 < first_copy, (dotnet_8, dotnet_10, first_copy)
    assert dotnet_8 < python_layer < first_copy, (dotnet_8, python_layer, first_copy)


def test_the_entrypoint_user_and_healthcheck_are_untouched() -> None:
    instructions = [i for _, i in _instructions()]
    assert 'ENTRYPOINT ["python", "/app/infra/ingest.py"]' in instructions
    assert "USER foss-mcp" in instructions
    assert any(i.startswith("HEALTHCHECK") and "foss_mcp.indexing.publisher" in i for i in instructions)
