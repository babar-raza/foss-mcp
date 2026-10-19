"""TC-570 (REQ-G3-009, REQ-G3-008; review row RD3-169, OQ-008): nothing in the ingestion toolchain floats.

A verified-example verdict (what find_examples may serve as verified) must not depend on the day the
ingestion image was built. Offline and static: it reads Dockerfile.ingestion as text, joins backslash
continuations into one logical instruction per line, drops comments, and never runs docker. Every test in
it runs: none is skippable.

The expected versions below are the ones the previous built image resolved (Debian 13 trixie), written by
hand from the supervisor's reading of ``foss-mcp-ingestion:local`` on 2026-10-19. They are not derived from
the Dockerfile under test. The parser helpers are themselves checked against hand-written inputs with
known answers, so a parser that silently finds nothing cannot make the Dockerfile tests pass.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "Dockerfile.ingestion"

# Packages Debian cannot pin. Empty: every apt package on this image carries an exact version.
UNPINNABLE_APT_PACKAGES: tuple[str, ...] = ()

EXPECTED_DOTNET_VERSIONS = ("8.0.426", "10.0.402")
EXPECTED_UV_PYTHON = "3.12.15"
EXPECTED_APT_PINS = {
    "git": "1:2.47.3-0+deb13u1",
    "curl": "8.14.1-2+deb13u7",
    "ca-certificates": "20250419",
    "openjdk-21-jdk": "21.0.12.1+1-1~deb13u1",
    "maven": "3.9.9-1",
    "build-essential": "12.12",
    "g++": "4:14.2.0-1",
    "cmake": "3.31.6-2",
    "git-lfs": "3.6.1-1+deb13u1",
}
EXPECTED_ARG_DEFAULTS = {
    "UV_VERSION": "0.13.0",
    "NODE_VERSION": "24.13.1",
    "GO_VERSION": "1.26.4",
    "RUST_VERSION": "1.98.1",
}
EXPECTED_LABELS = {
    "org.foss-mcp.toolchain.dotnet": "8.0.426 10.0.402",
    "org.foss-mcp.toolchain.python-uv": "3.12.15",
    "org.foss-mcp.toolchain.uv": "0.13.0",
    "org.foss-mcp.toolchain.node": "24.13.1",
    "org.foss-mcp.toolchain.go": "1.26.4",
    "org.foss-mcp.toolchain.rust": "1.98.1",
}

FULL_VERSION = re.compile(r"^\d+\.\d+\.\d+$")


def _join_instructions(text: str) -> list[tuple[int, str]]:
    """Return (first line number, instruction) with ``\\`` continuations joined and comment lines dropped."""
    out: list[tuple[int, str]] = []
    buffer: list[str] = []
    start = 0
    for number, raw in enumerate(text.splitlines(), start=1):
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


def _apt_packages(instruction: str) -> list[str]:
    """Every package token of every ``apt-get install`` in one instruction (flags dropped)."""
    packages: list[str] = []
    for segment in re.split(r"&&|;|\|\|", instruction):
        words = segment.split()
        for index in range(len(words) - 1):
            if words[index] == "apt-get" and words[index + 1] == "install":
                packages.extend(w for w in words[index + 2 :] if not w.startswith("-"))
                break
    return packages


def _labels(instructions: list[tuple[int, str]]) -> dict[str, str]:
    found: dict[str, str] = {}
    for _, instruction in instructions:
        if instruction.startswith("LABEL "):
            for token in shlex.split(instruction[len("LABEL ") :]):
                key, _, value = token.partition("=")
                found[key] = value
    return found


def _dockerfile() -> list[tuple[int, str]]:
    return _join_instructions(DOCKERFILE.read_text(encoding="utf-8"))


def _all_apt_packages() -> list[tuple[int, str]]:
    return [(n, p) for n, i in _dockerfile() if i.startswith("RUN ") for p in _apt_packages(i)]


# --- the helpers, checked against hand-written inputs with known answers -------------------------------


def test_the_apt_parser_finds_packages_and_drops_flags_in_a_known_instruction() -> None:
    known = (
        "RUN apt-get update && apt-get install -y --no-install-recommends alpha=1.0 beta gamma=2:3.4-5 "
        "&& rm -rf /var/lib/apt/lists/*"
    )
    assert _apt_packages(known) == ["alpha=1.0", "beta", "gamma=2:3.4-5"]
    assert _apt_packages("RUN pip install uv==0.13.0") == []
    assert _apt_packages("RUN apt-get update && rm -rf /var/lib/apt/lists/*") == []


def test_the_instruction_joiner_drops_comments_and_joins_continuations_in_a_known_text() -> None:
    known = "# comment apt-get install ghost\nRUN a \\\n    && b \\\n    && c\n\nENV X=1\n"
    assert _join_instructions(known) == [(2, "RUN a && b && c"), (6, "ENV X=1")]


def test_a_floating_form_is_detected_by_the_same_rules_the_dockerfile_tests_use() -> None:
    floating = "RUN apt-get install -y --no-install-recommends git=1:2.47.3-0+deb13u1 curl"
    unpinned = [p for p in _apt_packages(floating) if "=" not in p and p not in UNPINNABLE_APT_PACKAGES]
    assert unpinned == ["curl"]
    assert not FULL_VERSION.match("3.12")
    assert FULL_VERSION.match("3.12.15")


def test_the_label_parser_reads_a_known_label_instruction() -> None:
    known = [(1, 'LABEL a.b="1 2" c.d=3')]
    assert _labels(known) == {"a.b": "1 2", "c.d": "3"}


# --- Dockerfile.ingestion ------------------------------------------------------------------------------


def test_the_allow_list_of_unpinnable_apt_packages_is_empty() -> None:
    assert UNPINNABLE_APT_PACKAGES == ()


def test_no_instruction_uses_a_floating_dotnet_channel() -> None:
    instructions = _dockerfile()
    offenders = [(n, i) for n, i in instructions if "--channel" in i]
    assert offenders == [], offenders
    # And the mandated exact flag text is present, so the pin cannot be dropped silently.
    assert "--version 8.0.426" in DOCKERFILE.read_text(encoding="utf-8")


def test_the_two_dotnet_sdks_are_installed_by_exact_version_in_order() -> None:
    versions = []
    for _, instruction in _dockerfile():
        if (
            instruction.startswith("RUN ")
            and "/tmp/dotnet-install.sh" in instruction
            and "--install-dir" in instruction
        ):
            match = re.search(r"/tmp/dotnet-install\.sh\s+--version\s+(\S+)", instruction)
            assert match, instruction
            versions.append(match.group(1))
    assert tuple(versions) == EXPECTED_DOTNET_VERSIONS
    assert all(FULL_VERSION.match(v) for v in versions)


def test_every_uv_python_install_names_a_full_version() -> None:
    installs = [i for _, i in _dockerfile() if i.startswith("RUN ") and "uv python install" in i]
    assert installs, "no uv python install instruction"
    for instruction in installs:
        match = re.search(r"uv python install\s+(\S+)", instruction)
        assert match, instruction
        assert FULL_VERSION.match(match.group(1)), f"uv python install {match.group(1)!r} is not a full x.y.z"
        assert match.group(1) == EXPECTED_UV_PYTHON


def test_every_apt_get_install_package_carries_an_exact_version() -> None:
    packages = _all_apt_packages()
    assert packages, "no apt-get install package found: the parser would be vacuous"
    unpinned = [(n, p) for n, p in packages if "=" not in p and p not in UNPINNABLE_APT_PACKAGES]
    assert unpinned == [], f"apt packages without a =version pin: {unpinned}"
    empty = [(n, p) for n, p in packages if p.endswith("=") or p.startswith("=")]
    assert empty == [], empty


def test_the_apt_package_set_and_versions_equal_the_previous_images() -> None:
    found: dict[str, str] = {}
    for number, package in _all_apt_packages():
        name, _, version = package.partition("=")
        assert name not in found, f"line {number}: {name} is installed twice"
        found[name] = version
    assert found == EXPECTED_APT_PINS


def test_node_go_rust_and_uv_stay_pinned_to_the_previous_images_versions() -> None:
    defaults: dict[str, str] = {}
    for _, instruction in _dockerfile():
        match = re.match(r"^ARG (\w+)=(\S+)$", instruction)
        if match:
            defaults[match.group(1)] = match.group(2)
    for name, expected in EXPECTED_ARG_DEFAULTS.items():
        assert defaults.get(name) == expected, (name, defaults.get(name))
    assert any(i.startswith("RUN ") and "uv==${UV_VERSION}" in i for _, i in _dockerfile())


def test_the_toolchain_versions_are_recorded_as_image_labels() -> None:
    labels = _labels(_dockerfile())
    for key, expected in EXPECTED_LABELS.items():
        assert labels.get(key) == expected, (key, labels.get(key))
    apt_label = labels.get("org.foss-mcp.toolchain.apt", "")
    recorded = dict(token.partition("=")[::2] for token in apt_label.split())
    # The label omits the download tool (tests/infra/test_container_pins.py treats any instruction naming it as
    # a checksummed download); that tool is pinned on its apt line and checked by the apt tests above.
    assert recorded == {k: v for k, v in EXPECTED_APT_PINS.items() if k != "curl"}
