"""TC-364: the eleven wave-1 Python documentation pages are wired into the chart and compose.

Offline. Reads infra/helm/foss-mcp/values.yaml, docker-compose.yml and the committed furnished pages
as files. No helm, no docker, no network. For each wave-1 pilot it pins that the chart references the
page, that the page exists and carries the README provenance of the pilot's pinned commit, and that
the compose ingest service passes the same page and the same library flags to build_chunks.py.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"
COMPOSE = REPO_ROOT / "docker-compose.yml"
FURNISHED = REPO_ROOT / "tests" / "fixtures" / "furnished"

WAVE1_FAMILIES = [
    "words",
    "cells",
    "jmap",
    "pdf",
    "email",
    "barcode",
    "note",
    "html",
    "page",
    "3d",
    "tex",
]

# Pilots that already referenced a furnished page before wave 1.
ALREADY_FURNISHED = {
    ("pdf", "net"),
    ("slides", "python"),
    ("pdf", "typescript"),
    ("pdf", "go"),
    ("pdf", "java"),
    ("cells", "rust"),
}


def _pilots() -> list[dict[str, Any]]:
    return yaml.safe_load(VALUES.read_text(encoding="utf-8"))["ingestion"]["pilots"]


def _pilot(family: str) -> dict[str, Any]:
    matches = [p for p in _pilots() if p["family"] == family and p["platform"] == "python"]
    assert len(matches) == 1, f"expected exactly one {family}/python pilot, got {len(matches)}"
    return matches[0]


def _expected_page(family: str) -> str:
    return f"/app/fixtures/furnished/{family}_python/pages/_index.md"


def _front_matter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path} has no front matter"
    end = text.index("\n---", 4)
    return yaml.safe_load(text[4:end])


def _build_chunks_tokens(family: str) -> list[str]:
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    tokens = shlex.split(services[f"ingest-{family}-python"]["command"][0])
    assert tokens[:2] == ["python", "/app/infra/build_chunks.py"], tokens[:2]
    assert "&&" in tokens, f"ingest-{family}-python is not the two-step chain"
    return tokens[: tokens.index("&&")]


def _flag_index(tokens: list[str], flag: str) -> int:
    assert tokens.count(flag) == 1, f"{flag} appears {tokens.count(flag)} times in build_chunks.py"
    return tokens.index(flag)


@pytest.mark.parametrize("family", WAVE1_FAMILIES)
def test_chart_references_the_furnished_page(family: str) -> None:
    assert _pilot(family).get("furnishedPage") == _expected_page(family), family


@pytest.mark.parametrize("family", WAVE1_FAMILIES)
def test_furnished_page_exists_with_readme_provenance_at_the_pinned_commit(family: str) -> None:
    page = FURNISHED / f"{family}_python" / "pages" / "_index.md"
    assert page.is_file(), f"{page} does not exist"
    front = _front_matter(page)
    assert front.get("doc_source") == "github_readme", family
    assert front.get("doc_source_commit") == _pilot(family)["library"]["commit"], family


@pytest.mark.parametrize("family", WAVE1_FAMILIES)
def test_compose_passes_the_same_page_and_library_flags_before_out(family: str) -> None:
    library = _pilot(family)["library"]
    tokens = _build_chunks_tokens(family)
    out_index = _flag_index(tokens, "--out")
    expected = {
        "--furnished-page": _expected_page(family),
        "--library-repository": library["repository"],
        "--library-commit": library["commit"],
        "--library-platform": library["platform"],
    }
    for flag, value in expected.items():
        index = _flag_index(tokens, flag)
        assert tokens[index + 1] == value, f"{family}: {flag} is {tokens[index + 1]!r}, want {value!r}"
        assert index < out_index, f"{family}: {flag} comes after --out"
    assert library["platform"] == "python", family


def test_only_wave1_and_previously_furnished_pilots_have_a_furnished_page() -> None:
    allowed = ALREADY_FURNISHED | {(family, "python") for family in WAVE1_FAMILIES}
    furnished = {(p["family"], p["platform"]) for p in _pilots() if p.get("furnishedPage")}
    assert furnished <= allowed, sorted(furnished - allowed)
    assert {(family, "python") for family in WAVE1_FAMILIES} <= furnished
