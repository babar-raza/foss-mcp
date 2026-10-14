"""Offline checks of the jmap/net authored-example sidecar (TC-463, REQ-G3-007).

``tests/fixtures/furnished/jmap_net/authored_examples.yaml`` holds three minimal examples written
against the real public API of the pinned library commit, because the README supplies at most one
example the .NET verifier can compile. This file proves the sidecar is well formed, labelled and
uses only symbols that resolve in the pilot's extraction. Whether the examples compile is decided
later by the ingestion verifier in the cluster, never here.

Nothing in this file reaches a network; every rule and constant is imported from
``foss_mcp.indexing.authored_examples`` rather than retyped.
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
    LANGUAGE_BY_PLATFORM,
    SIDECAR_NAME,
    load_authored_examples,
)
from foss_mcp.indexing.example_candidates import (
    CandidateExample,
    extract_candidate_examples,
    normalised_code_sha256,
)
from foss_mcp.normalization.citation import qualified_symbol_index_from_api_surface

PILOT = "jmap_net"
REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
SIDECAR = FIXTURES / "furnished" / PILOT / SIDECAR_NAME
PAGE = FIXTURES / "furnished" / PILOT / "pages" / "_index.md"
API_SURFACE = FIXTURES / PILOT / "api_surface.json"
CHART_VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"

EXAMPLE_COUNT = 3
ENTRY_SYMBOLS = ("JmapClient", "JmapRequestEnvelope", "Invocation", "IJmapTransport")


def _library() -> dict:
    values = yaml.safe_load(CHART_VALUES.read_text(encoding="utf-8"))
    pilots = {f"{p['family']}_{p['platform']}": p for p in values["ingestion"]["pilots"]}
    return pilots[PILOT]["library"]


def _api_types() -> list[dict]:
    return json.loads(API_SURFACE.read_text(encoding="utf-8"))["types"]


def _load() -> tuple[CandidateExample, ...]:
    library = _library()
    return load_authored_examples(
        SIDECAR,
        pilot=PILOT,
        library_platform=library["platform"],
        library_commit=library["commit"],
        api_types=_api_types(),
    )


def _raw_examples() -> list[dict]:
    return yaml.safe_load(SIDECAR.read_text(encoding="utf-8"))["examples"]


def _whole_word(word: str, code: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(word)}(?![A-Za-z0-9_])", code) is not None


EXAMPLE_INDEXES = list(range(EXAMPLE_COUNT))


def test_sidecar_loads_through_the_loader_with_three_authored_candidates() -> None:
    candidates = _load()
    assert len(candidates) == EXAMPLE_COUNT
    assert {candidate.provenance for candidate in candidates} == {"authored"}
    assert {candidate.language for candidate in candidates} == {LANGUAGE_BY_PLATFORM["dotnet"]}


def test_sidecar_is_tied_to_the_pinned_library_in_the_chart() -> None:
    library = _library()
    document = yaml.safe_load(SIDECAR.read_text(encoding="utf-8"))
    assert document["pilot"] == PILOT
    assert document["library_repository"] == library["repository"]
    assert document["library_commit"] == library["commit"]
    assert document["language"] == LANGUAGE_BY_PLATFORM[library["platform"]]
    assert library["platform"] == "dotnet"


def test_sidecar_file_is_utf8_without_bom_and_lf_only() -> None:
    raw = SIDECAR.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in raw
    raw.decode("utf-8")


def test_published_label_names_the_authored_example_header_words() -> None:
    assert "Authored example" in AUTHORED_LABEL


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_code_hash_matches(index: int) -> None:
    example = _raw_examples()[index]
    assert example["code_sha256"] == normalised_code_sha256(example["code"])


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_label_comment_is_the_first_non_blank_line(index: int) -> None:
    code = _raw_examples()[index]["code"]
    first = next(line.strip() for line in code.splitlines() if line.strip())
    assert first.startswith("//")
    assert "Authored example" in first


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_every_declared_symbol_resolves_and_appears_in_the_code(index: int) -> None:
    example = _raw_examples()[index]
    symbols = example["symbols"]
    assert len(symbols) >= 2
    symbol_index = qualified_symbol_index_from_api_surface(_api_types())
    for symbol in symbols:
        assert symbol_index.declarations_for(symbol), f"{symbol} has no declaration in the api_surface"
        assert _whole_word(symbol.rsplit(".", 1)[-1], example["code"]), f"{symbol} is not used in the code"


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_no_forbidden_pattern_matches(index: int) -> None:
    code = _raw_examples()[index]["code"]
    matched = [pattern.pattern for pattern in FORBIDDEN_CODE_PATTERNS if pattern.search(code)]
    assert not matched, matched


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_title_differs_from_every_readme_candidate_title(index: int) -> None:
    page = build_chunks._load_furnished_page(PAGE)
    readme_titles = {candidate.title for candidate in extract_candidate_examples(page)}
    readme_titles |= {candidate.title for candidate in extract_candidate_examples(page, platform="dotnet")}
    assert readme_titles, "the committed page must yield README candidates"
    assert _raw_examples()[index]["title"] not in readme_titles


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_example_uses_the_core_client_api(index: int) -> None:
    code = _raw_examples()[index]["code"]
    assert any(_whole_word(symbol, code) for symbol in ENTRY_SYMBOLS)


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_example_that_builds_a_client_also_implements_the_transport(index: int) -> None:
    code = _raw_examples()[index]["code"]
    if re.search(r"\bnew\s+JmapClient\s*\(", code):
        assert re.search(r"\bclass\s+\w+\s*:\s*IJmapTransport\b", code), (
            "a client is built, so a transport class must be implemented in the same code"
        )


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_no_bare_thread_name_outside_the_qualified_one(index: int) -> None:
    code = _raw_examples()[index]["code"]
    bare = re.findall(r"(?<!Aspose\.Jmap\.)\bThread\b", code)
    assert not bare


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_list_mailboxes_is_always_called_with_an_account_id(index: int) -> None:
    code = _raw_examples()[index]["code"]
    for match in re.finditer(r"\bListMailboxesAsync\s*\(\s*(\S)", code):
        assert match.group(1) != ")", "ListMailboxesAsync needs the account id argument"


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_usings_are_explicit_and_helper_types_follow_the_statements(index: int) -> None:
    code = _raw_examples()[index]["code"]
    assert re.search(r"^using System;$", code, re.MULTILINE)
    assert re.search(r"^using Aspose\.Jmap;$", code, re.MULTILINE)
    declaration = re.search(r"^(?:sealed\s+)?class\s+\w+", code, re.MULTILINE)
    if declaration is not None:
        assert code.index("Console.WriteLine") < declaration.start()
    assert not re.search(r"^namespace\b", code, re.MULTILINE)


def test_the_three_examples_have_distinct_titles_and_code() -> None:
    examples = _raw_examples()
    assert len({example["title"] for example in examples}) == EXAMPLE_COUNT
    assert len({example["code_sha256"] for example in examples}) == EXAMPLE_COUNT
