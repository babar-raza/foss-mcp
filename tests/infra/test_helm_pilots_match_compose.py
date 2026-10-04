"""TC-195: every pilot in the chart matches its docker-compose.yml ingestion service.

Offline. Reads infra/helm/foss-mcp/values.yaml and docker-compose.yml as text and parses them. For
each pilot it finds the ingest-<family>-<platform> service and compares the arguments the chart
passes to build_chunks.py and ingest.py with the arguments compose passes. No helm, no docker, no
network.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"
COMPOSE = REPO_ROOT / "docker-compose.yml"

SIX_PILOTS = {
    ("pdf", "net"),
    ("slides", "python"),
    ("pdf", "typescript"),
    ("pdf", "go"),
    ("pdf", "java"),
    ("cells", "rust"),
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

    assert pilot["library"]["commit"] == build["--library-commit"], "library commit"
    assert pilot["apiSurface"] == build["--api-surface"], "apiSurface (build_chunks)"
    assert pilot["apiSurface"] == ingest["--api-surface"], "apiSurface (ingest)"
    assert pilot["furnishedPage"] == build["--furnished-page"], "furnished page"
    assert pilot["maxTypes"] == int(build["--max-types"]), "maxTypes"

    assert pilot["title"] == build["--title"], "title"
    assert pilot["sourceKind"] == ingest["--source-kind"], "sourceKind"
    assert pilot["heldBy"] == ingest["--held-by"], "heldBy"

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


def test_chart_has_exactly_six_pilots_one_per_platform() -> None:
    keys = [(p["family"], p["platform"]) for p in _pilots()]
    assert len(keys) == 6, keys
    assert len(set(keys)) == len(keys), f"duplicate pilot platform: {keys}"
    assert set(keys) == SIX_PILOTS, keys
