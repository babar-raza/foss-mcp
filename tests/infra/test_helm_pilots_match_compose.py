"""TC-195: every pilot in the chart matches its docker-compose.yml ingestion service.

Offline. Reads infra/helm/foss-mcp/values.yaml and docker-compose.yml as text and parses them. For
each pilot it finds the ingest-<family>-<platform> service and compares the arguments the chart
passes to build_chunks.py and ingest.py with the arguments compose passes. No helm, no docker, no
network.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"
COMPOSE = REPO_ROOT / "docker-compose.yml"

# TC-205: fourteen pilots, two per platform across seven platforms, from different teams.
FOURTEEN_PILOTS = {
    ("pdf", "net"),
    ("slides", "python"),
    ("pdf", "typescript"),
    ("pdf", "go"),
    ("pdf", "java"),
    ("cells", "rust"),
    ("pdf", "cpp"),
    ("words", "python"),
    ("words", "net"),
    ("slides", "java"),
    ("cells", "go"),
    ("cells", "typescript"),
    ("jmap", "rust"),
    ("cells", "cpp"),
}

# build_chunks.py flag for each library field the chart template can pass, and the key it has in
# values.yaml's pilot library block.
LIBRARY_FLAGS = {
    "--library-repository": "repository",
    "--library-commit": "commit",
    "--library-platform": "platform",
    "--library-csproj": "csproj",
    "--library-package-name": "packageName",
    "--library-module-path": "modulePath",
    "--library-crate-name": "crateName",
}


def _pilots() -> list[dict[str, Any]]:
    values = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
    return values["ingestion"]["pilots"]


def _pilot(family: str, platform: str) -> dict[str, Any]:
    matches = [p for p in _pilots() if p["family"] == family and p["platform"] == platform]
    assert len(matches) == 1, f"expected exactly one {family}/{platform} pilot, got {len(matches)}"
    return matches[0]


def _compose_service(family: str, platform: str) -> dict[str, Any]:
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    return services[f"ingest-{family}-{platform}"]


def _flags(tokens: list[str]) -> dict[str, str]:
    flags: dict[str, str] = {}
    index = 0
    while index < len(tokens):
        if tokens[index].startswith("--"):
            flags[tokens[index]] = tokens[index + 1]
            index += 2
        else:
            index += 1
    return flags


def _compose_arguments(family: str, platform: str) -> tuple[dict[str, str], dict[str, str]]:
    """The build_chunks.py and ingest.py flags of one ingest service, split on its && chain."""
    tokens = shlex.split(_compose_service(family, platform)["command"][0])
    assert "&&" in tokens, f"ingest-{family}-{platform} is not the two-step chain"
    split_at = tokens.index("&&")
    build, ingest = tokens[:split_at], tokens[split_at + 1 :]
    assert build[:2] == ["python", "/app/infra/build_chunks.py"], build[:2]
    assert ingest[:2] == ["python", "/app/infra/ingest.py"], ingest[:2]
    return _flags(build), _flags(ingest)


def _assert_pilot_matches_compose(family: str, platform: str) -> None:
    pilot = _pilot(family, platform)
    build, ingest = _compose_arguments(family, platform)

    assert ingest["--family"] == family, family
    assert ingest["--platform"] == platform, platform
    assert pilot["family"] == family and pilot["platform"] == platform

    # A self-extracted pilot has an empty library block and no --library-commit flag: both None.
    # pdf/cpp is the one pilot whose compose service defines no library flags, so its library is
    # checked against its fixture's source_repository and source_commit instead.
    if (family, platform) == ("pdf", "cpp"):
        fixture = json.loads(
            (REPO_ROOT / "tests" / "fixtures" / "pdf_cpp" / "api_surface.json").read_text(encoding="utf-8")
        )
        assert pilot["library"]["repository"] == fixture["source_repository"], "library repository"
        assert pilot["library"]["commit"] == fixture["source_commit"], "library commit"
    else:
        assert pilot["library"].get("commit") == build.get("--library-commit"), "library commit"
    assert pilot["apiSurface"] == build["--api-surface"], "apiSurface (build_chunks)"
    assert pilot["apiSurface"] == ingest["--api-surface"], "apiSurface (ingest)"
    # A self-extracted pilot has no furnished page: the key is absent in values.yaml and the flag
    # is absent in compose, so both sides are None and still compare equal.
    assert pilot.get("furnishedPage") == build.get("--furnished-page"), "furnished page"
    assert pilot["maxTypes"] == int(build["--max-types"]), "maxTypes"

    assert pilot["title"] == build["--title"], "title"
    assert pilot["sourceKind"] == ingest["--source-kind"], "sourceKind"
    assert pilot["heldBy"] == ingest["--held-by"], "heldBy"

    if (family, platform) != ("pdf", "cpp"):
        compose_library = {key: build[flag] for flag, key in LIBRARY_FLAGS.items() if flag in build}
        assert pilot["library"] == compose_library, "library block"


def test_pdf_net_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("pdf", "net")


def test_slides_python_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("slides", "python")


def test_pdf_typescript_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("pdf", "typescript")


def test_pdf_go_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("pdf", "go")


def test_pdf_java_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("pdf", "java")


def test_cells_rust_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("cells", "rust")


def test_pdf_cpp_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("pdf", "cpp")


def test_words_python_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("words", "python")


def test_words_net_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("words", "net")


def test_slides_java_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("slides", "java")


def test_cells_go_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("cells", "go")


def test_cells_typescript_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("cells", "typescript")


def test_jmap_rust_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("jmap", "rust")


def test_cells_cpp_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("cells", "cpp")


def _serving_name(family: str, platform: str) -> str:
    # The pdf/net serving service predates the naming pattern and is called plain "serving".
    return "serving" if (family, platform) == ("pdf", "net") else f"serving-{family}-{platform}"


def test_every_pilot_has_a_serving_service_with_its_identity() -> None:
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    for family, platform in sorted(FOURTEEN_PILOTS):
        serving = services[_serving_name(family, platform)]
        assert serving["environment"]["FOSS_MCP_FAMILY"] == family, (family, platform)
        assert serving["environment"]["FOSS_MCP_PLATFORM"] == platform, (family, platform)
        assert serving["environment"]["FOSS_MCP_SOURCE_KIND"] == _pilot(family, platform)["sourceKind"]
    host_ports = [
        services[_serving_name(family, platform)]["ports"][0].split(":")[0]
        for family, platform in sorted(FOURTEEN_PILOTS)
    ]
    assert len(set(host_ports)) == len(host_ports), host_ports


def test_chart_has_exactly_six_pilots_one_per_platform() -> None:
    keys = [(p["family"], p["platform"]) for p in _pilots()]
    assert len(keys) == 14, keys
    assert len(set(keys)) == len(keys), f"duplicate pilot platform: {keys}"
    assert set(keys) == FOURTEEN_PILOTS, keys
