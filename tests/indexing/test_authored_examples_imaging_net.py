"""Offline checks of the imaging/net authored-example sidecar (TC-461, REQ-G3-007).

``tests/fixtures/furnished/imaging_net/authored_examples.yaml`` holds three minimal examples written
against the real public API of Aspose.Imaging FOSS for .NET at the pinned commit. This file proves they
are well-formed, labelled and use only symbols that resolve in the pilot's extraction. Whether they
compile or run is decided by the ingestion verifier in the cluster, never here.

Every rule and constant is imported from ``foss_mcp.indexing.authored_examples``; nothing is retyped.
Nothing here reaches a network.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

import infra.build_chunks as build_chunks
from foss_mcp.indexing.authored_examples import (
    AUTHORED_LABEL,
    FORBIDDEN_CODE_PATTERNS,
    SIDECAR_NAME,
    load_authored_examples,
)
from foss_mcp.indexing.example_candidates import (
    CandidateExample,
    extract_candidate_examples,
    normalised_code_sha256,
)
from foss_mcp.normalization.citation import qualified_symbol_index_from_api_surface

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
PILOT = "imaging_net"
SIDECAR = FIXTURES / "furnished" / PILOT / SIDECAR_NAME
PAGE = FIXTURES / "furnished" / PILOT / "pages" / "_index.md"
API_SURFACE = FIXTURES / PILOT / "api_surface.json"
CHART_VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"

# Types whose use requires a ``using`` for the namespace on the right.
NAMESPACE_OF_TYPE = {
    "Stream": "System.IO",
    "MemoryStream": "System.IO",
    "FileStream": "System.IO",
    "File": "System.IO",
    "Path": "System.IO",
    "FileInfo": "System.IO",
    "Directory": "System.IO",
    "CancellationToken": "System.Threading",
    "CancellationTokenSource": "System.Threading",
    "Task": "System.Threading.Tasks",
}


def _pilot() -> dict:
    values = yaml.safe_load(CHART_VALUES.read_text(encoding="utf-8"))
    pilots = {f"{p['family']}_{p['platform']}": p for p in values["ingestion"]["pilots"]}
    return pilots[PILOT]


def _api_types() -> list[dict]:
    return json.loads(API_SURFACE.read_text(encoding="utf-8"))["types"]


def _load() -> tuple[CandidateExample, ...]:
    library = _pilot()["library"]
    return load_authored_examples(
        SIDECAR,
        pilot=PILOT,
        library_platform=library["platform"],
        library_commit=library["commit"],
        api_types=_api_types(),
    )


def _document() -> dict:
    return yaml.safe_load(SIDECAR.read_text(encoding="utf-8"))


def _readme_titles() -> set[str]:
    platform = _pilot()["library"]["platform"]
    page = build_chunks._load_furnished_page(PAGE)
    titles = {candidate.title for candidate in extract_candidate_examples(page)}
    titles |= {candidate.title for candidate in extract_candidate_examples(page, platform=platform)}
    return titles


def _code_without_comments(code: str) -> str:
    return "\n".join(line.split("//", 1)[0] for line in code.splitlines())


INDEXES = [0, 1, 2]


def test_the_sidecar_loads_and_yields_exactly_three_authored_candidates() -> None:
    candidates = _load()
    assert len(candidates) == 3
    assert {candidate.provenance for candidate in candidates} == {"authored"}
    assert {candidate.language for candidate in candidates} == {"csharp"}
    assert len({candidate.title for candidate in candidates}) == 3


def test_the_sidecar_is_tied_to_the_charts_pinned_library() -> None:
    document = _document()
    library = _pilot()["library"]
    assert document["pilot"] == PILOT
    assert document["library_repository"] == library["repository"]
    assert document["library_commit"] == library["commit"]
    assert document["language"] == "csharp"
    assert len(document["examples"]) == 3
    assert AUTHORED_LABEL.startswith("Authored example")


@pytest.mark.parametrize("index", INDEXES)
def test_the_code_hash_matches(index: int) -> None:
    example = _document()["examples"][index]
    assert example["code_sha256"] == normalised_code_sha256(example["code"])
    assert _load()[index].code_sha256 == example["code_sha256"]


@pytest.mark.parametrize("index", INDEXES)
def test_the_label_comment_is_the_first_non_blank_line(index: int) -> None:
    code = _document()["examples"][index]["code"]
    first = next(line.strip() for line in code.splitlines() if line.strip())
    assert first.startswith("//")
    assert "Authored example" in first


@pytest.mark.parametrize("index", INDEXES)
def test_every_declared_symbol_resolves_and_appears_in_the_code(index: int) -> None:
    example = _document()["examples"][index]
    symbols = example["symbols"]
    assert len(symbols) >= 2
    resolver = qualified_symbol_index_from_api_surface(_api_types())
    for symbol in symbols:
        assert resolver.declarations_for(symbol), f"{symbol!r} does not resolve in the api_surface"
        last_segment = symbol.rsplit(".", 1)[-1]
        assert re.search(rf"\b{re.escape(last_segment)}\b", example["code"]), symbol


@pytest.mark.parametrize("index", INDEXES)
def test_no_forbidden_pattern_matches(index: int) -> None:
    code = _document()["examples"][index]["code"]
    hits = [pattern.pattern for pattern in FORBIDDEN_CODE_PATTERNS if pattern.search(code)]
    assert not hits, hits


@pytest.mark.parametrize("index", INDEXES)
def test_the_title_differs_from_every_readme_candidate_title(index: int) -> None:
    title = _document()["examples"][index]["title"]
    readme_titles = _readme_titles()
    assert readme_titles, "the committed page must yield README candidates for this comparison"
    assert title not in readme_titles


@pytest.mark.parametrize("index", INDEXES)
def test_every_example_uses_imageprobe_and_one_of_its_members(index: int) -> None:
    code = _code_without_comments(_document()["examples"][index]["code"])
    members = {
        method["name"]
        for api_type in _api_types()
        if api_type["name"] == "ImageProbe"
        for method in api_type["methods"]
    }
    assert members, "ImageProbe must declare methods in the api_surface"
    assert "ImageProbe" in code
    assert any(re.search(rf"\bImageProbe\.{re.escape(member)}\s*\(", code) for member in members)


@pytest.mark.parametrize("index", INDEXES)
def test_top_level_statements_only(index: int) -> None:
    code = _code_without_comments(_document()["examples"][index]["code"])
    assert not re.search(r"\bMain\s*\(", code)
    assert not re.search(r"^\s*namespace\b", code, re.MULTILINE)
    assert "Aspose.Imaging.Foss" in code


@pytest.mark.parametrize("index", INDEXES)
def test_every_system_io_and_threading_type_has_its_using(index: int) -> None:
    code = _code_without_comments(_document()["examples"][index]["code"])
    usings = set(re.findall(r"^\s*using\s+([A-Za-z0-9_.]+)\s*;", code, re.MULTILINE))
    for type_name, namespace in NAMESPACE_OF_TYPE.items():
        if re.search(rf"\b{type_name}\b", code):
            assert namespace in usings, f"{type_name} needs 'using {namespace};'"
