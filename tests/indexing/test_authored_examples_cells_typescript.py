"""Offline checks of the cells/typescript authored-example sidecar (TC-745, REQ-G3-014).

``tests/fixtures/furnished/cells_typescript/authored_examples.yaml`` is the FIRST brand-new
``authored_examples.yaml`` sidecar authored for a TypeScript pilot this session. It holds five
minimal, labelled examples written against the real public API of
``aspose-cells-foss/Aspose.Cells-FOSS-for-TypeScript`` at its pinned commit (cell values and a
formula, hyperlinks, style formatting, worksheet management, and JSON/Markdown export), each
individually built and run by TC-745's author with ``npx tsx`` against the real pinned repository
before being committed here, and also independently type-checked with the real ingestion
verifier's own ``tsc --noEmit`` repository-root-mode recipe (``verify_typescript_example`` in
``src/foss_mcp/indexing/example_verifier.py``): every diagnostic that recipe produced was already
present in the pinned library's own sources before any of these five files existed, and none was
ever attributed to the candidate file.

This file proves the sidecar is well-formed, labelled, tied to the pin's commit, and uses only
symbols that resolve in the pilot's own extraction. Whether an example compiles or type-checks is
decided later by the ingestion verifier, never here: nothing in this file imports the library,
compiles or runs an example, or reaches a network.

Every rule and constant is imported from ``foss_mcp.indexing.authored_examples`` rather than
retyped.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

import infra.build_chunks as build_chunks
from foss_mcp.indexing.authored_examples import (
    FORBIDDEN_CODE_PATTERNS,
    LANGUAGE_BY_PLATFORM,
    SIDECAR_NAME,
    load_authored_examples,
    validate_authored_examples,
)
from foss_mcp.indexing.example_candidates import (
    CandidateExample,
    extract_candidate_examples,
    normalised_code_sha256,
)
from foss_mcp.normalization.citation import qualified_symbol_index_from_api_surface

PILOT = "cells_typescript"

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
SIDECAR = FIXTURES / "furnished" / PILOT / SIDECAR_NAME
PAGE = FIXTURES / "furnished" / PILOT / "pages" / "_index.md"
API_SURFACE = FIXTURES / PILOT / "api_surface.json"
CHART_VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"

# TC-851: a sixth example restores an answer the TC-802 verifier rule removed (a README example that cannot run as written).
EXPECTED_EXAMPLES = 6
EXAMPLE_NUMBERS = [1, 2, 3, 4, 5, 6]

_STATIC_MODULE_DECLARATION_RE = re.compile(
    r"""(?:^|(?<=;))[ \t\r\n]*(?P<kw>import|export)\b(?:[^'"`;]*?\bfrom)?\s*(?P<q>['"])(?P<spec>[^'"\n]+)(?P=q)[ \t]*;?""",
    re.MULTILINE,
)


def _pilot_library() -> dict:
    values = yaml.safe_load(CHART_VALUES.read_text(encoding="utf-8"))
    for pilot in values["ingestion"]["pilots"]:
        if f"{pilot['family']}_{pilot['platform']}" == PILOT:
            return pilot["library"]
    raise AssertionError(f"{PILOT} is not a pilot in values.yaml")


def _api_types() -> list[dict]:
    return json.loads(API_SURFACE.read_text(encoding="utf-8"))["types"]


def _document() -> dict:
    return yaml.safe_load(SIDECAR.read_text(encoding="utf-8"))


def _example(number: int) -> dict:
    return _document()["examples"][number - 1]


def _candidates() -> tuple[CandidateExample, ...]:
    library = _pilot_library()
    return load_authored_examples(
        SIDECAR,
        pilot=PILOT,
        library_platform=library["platform"],
        library_commit=library["commit"],
        api_types=_api_types(),
    )


def _readme_titles() -> set[str]:
    page = build_chunks._load_furnished_page(PAGE)
    platform = _pilot_library()["platform"]
    titles = {candidate.title for candidate in extract_candidate_examples(page)}
    titles |= {candidate.title for candidate in extract_candidate_examples(page, platform=platform)}
    return titles


def test_the_sidecar_loads_and_yields_six_authored_candidates() -> None:
    candidates = _candidates()
    assert len(candidates) == EXPECTED_EXAMPLES
    assert {candidate.provenance for candidate in candidates} == {"authored"}
    assert {candidate.language for candidate in candidates} == {"typescript"}


def test_the_sidecar_header_matches_the_chart_pin() -> None:
    library = _pilot_library()
    document = _document()
    assert document["schema_version"] == 1
    assert document["pilot"] == PILOT
    assert document["library_repository"] == library["repository"]
    assert document["library_commit"] == library["commit"]
    assert re.fullmatch(r"[0-9a-f]{40}", document["library_commit"])
    assert document["language"] == LANGUAGE_BY_PLATFORM[library["platform"]]
    assert len(document["examples"]) == EXPECTED_EXAMPLES


def test_the_sidecar_has_no_validation_problems() -> None:
    library = _pilot_library()
    problems = validate_authored_examples(
        _document(),
        pilot=PILOT,
        library_platform=library["platform"],
        library_commit=library["commit"],
        api_types=_api_types(),
    )
    assert problems == []


def test_the_example_titles_are_unique_and_each_has_at_least_two_symbols() -> None:
    examples = _document()["examples"]
    titles = [example["title"] for example in examples]
    assert len(set(titles)) == len(titles)
    for example in examples:
        assert len(example["symbols"]) >= 2, example["title"]


@pytest.mark.parametrize("number", EXAMPLE_NUMBERS)
def test_the_code_hash_matches(number: int) -> None:
    example = _example(number)
    assert example["code_sha256"] == normalised_code_sha256(example["code"])


@pytest.mark.parametrize("number", EXAMPLE_NUMBERS)
def test_the_label_comment_is_the_first_non_blank_line(number: int) -> None:
    code = _example(number)["code"]
    first = next(line.strip() for line in code.splitlines() if line.strip())
    assert first.startswith("//")
    assert "Authored example" in first


@pytest.mark.parametrize("number", EXAMPLE_NUMBERS)
def test_every_declared_symbol_resolves_and_appears_in_the_code(number: int) -> None:
    example = _example(number)
    index = qualified_symbol_index_from_api_surface(_api_types())
    for symbol in example["symbols"]:
        assert index.declarations_for(symbol), f"{symbol} has no declaration in the api_surface"
        last_segment = symbol.rsplit(".", 1)[-1]
        assert re.search(rf"(?<![A-Za-z0-9_]){re.escape(last_segment)}(?![A-Za-z0-9_])", example["code"]), (
            f"{symbol} is not used in the code"
        )


@pytest.mark.parametrize("number", EXAMPLE_NUMBERS)
def test_no_forbidden_pattern_matches(number: int) -> None:
    code = _example(number)["code"]
    hits = [pattern.pattern for pattern in FORBIDDEN_CODE_PATTERNS if pattern.search(code)]
    assert hits == []


@pytest.mark.parametrize("number", EXAMPLE_NUMBERS)
def test_no_url_literal_has_a_non_reserved_host(number: int) -> None:
    # Every URL literal in these examples must point at a reserved placeholder host
    # (example.com/.org/.net or a .example/.test/.invalid suffix), never a real site.
    code = _example(number)["code"]
    for match in re.finditer(r"https?://([^\s/?#\"'<>`\\]*)", code):
        host = match.group(1).split(":", 1)[0].rstrip(".").lower()
        assert host.endswith((".example.com", "example.com", "example.org", "example.net")) or any(
            host.endswith(suffix) for suffix in (".example", ".test", ".invalid")
        ), f"non-reserved host in URL literal: {match.group(0)!r}"


@pytest.mark.parametrize("number", EXAMPLE_NUMBERS)
def test_the_title_differs_from_every_readme_candidate_title(number: int) -> None:
    assert _example(number)["title"] not in _readme_titles()


@pytest.mark.parametrize("number", EXAMPLE_NUMBERS)
def test_every_example_is_a_complete_program_that_calls_main(number: int) -> None:
    code = _example(number)["code"]
    assert re.search(r"\bfunction\s+main\s*\(", code), "no `main` function declared"
    assert re.search(r"(?<![.\w])main\s*\(\s*\)\s*;", code), "`main()` is never called"


@pytest.mark.parametrize("number", EXAMPLE_NUMBERS)
def test_every_static_module_specifier_is_a_relative_import_from_the_repository_root(number: int) -> None:
    # cells/typescript has no published package entry point worth importing by name (its
    # package.json `main` is a throwaway demo script, not a real library barrel): every real
    # example - the README's own quick-start fragments included - imports `./aspose_cells`
    # directly, matching the ingestion verifier's repository-root mode
    # (`_has_relative_module_specifier` in example_verifier.py).
    code = _example(number)["code"]
    specifiers = [match.group("spec") for match in _STATIC_MODULE_DECLARATION_RE.finditer(code)]
    assert specifiers, "the example imports nothing"
    for spec in specifiers:
        assert spec.startswith(("./", "../")), f"unexpected non-relative import: {spec!r}"
    assert any(spec.rstrip("/") == "./aspose_cells" for spec in specifiers), (
        "the example does not import from ./aspose_cells"
    )


@pytest.mark.parametrize("number", EXAMPLE_NUMBERS)
def test_every_example_throws_on_an_unexpected_value_rather_than_only_logging(number: int) -> None:
    # Every example must actually check what it claims, not merely console.log it: TC-745's
    # author ran each one with `npx tsx` against the real pinned repository and confirmed every
    # assertion below passed before committing this fixture.
    code = _example(number)["code"]
    assert "throw new Error(" in code
    assert "console.log(" in code
