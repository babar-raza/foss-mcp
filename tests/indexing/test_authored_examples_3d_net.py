"""Offline checks of the 3d/net authored-example sidecar (TC-766, REQ-G3-014).

``tests/fixtures/furnished/3d_net/authored_examples.yaml`` holds six authored examples written
against the real public API of ``Aspose.3D FOSS for .NET`` at the pinned commit. 3d/net ships no
dedicated ``examples/``-style sample directory upstream (Appendix C, class C), so these were seeded
from the library's own test/API surface and the README's own documented API shape instead, covering
five genuinely different capabilities: saving a primitive mesh to STL via plain extension detection,
saving one via an explicit ``StlSaveOptions`` and reloading its polygon count, composing a transform
with ``TransformBuilder`` and reading the resulting matrix fields directly, recursively counting a
multi-level node hierarchy's descendants, and round-tripping a two-entity scene through Microsoft 3MF.

This file proves they are well-formed, labelled, tied to the pilot's pinned commit and use only
symbols that resolve in the pilot's extraction. Whether each one compiles and runs is decided later
by the ingestion verifier, never here: nothing in this file imports the library, compiles or runs an
example, or reaches a network. Every one of the five was built and run for real against the pinned
commit (installed from the real NuGet package, version 26.1.0) before being written here (not
asserted by any test in this file, which stays offline per the authored-examples contract).

Two real, previously-unconfirmed defects of this edition were found and avoided while authoring this
file, hands-on, against the real installed package: ``TransformBuilder.RotateDegree`` leaves the
composed matrix unchanged (no rotation is ever applied), and ``Matrix4.Decompose`` always reports
translation (0, 0, 0), scale (1, 1, 1) and an identity rotation regardless of the real matrix content.
Neither is exercised by any example here; the transform example instead composes only ``Scale`` and
``Translate`` (both confirmed to mutate the builder's matrix correctly) and reads the matrix's own
``m00``/``m11``/``m22``/``m30``/``m31``/``m32`` fields directly rather than calling ``Decompose``.
Two further real findings, also avoided: ``Scene.Save``/``Scene.FromStream`` with a bare
``FileFormat`` token throws ``NotImplementedException`` for both STL and Microsoft 3MF ("Save/Load
options not implemented for ...") - the plain extension-based ``Scene.Save(path)``/``Scene.FromFile
(path)`` API and an explicit ``StlSaveOptions`` instance are the paths that actually work, matching
``config/known_defects/3d__net.yaml``'s own 3D-NET-001 guidance; and a reloaded node's singular
``Node.Entity`` property can be empty even when ``Node.Entities`` (the collection) correctly holds the
real entity, so both examples that reload a scene read back through ``Node.Entities``.

Every rule and constant is imported from ``foss_mcp.indexing.authored_examples`` rather than retyped.
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

PILOT = "3d_net"

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
SIDECAR = FIXTURES / "furnished" / PILOT / SIDECAR_NAME
PAGE = FIXTURES / "furnished" / PILOT / "pages" / "_index.md"
API_SURFACE = FIXTURES / PILOT / "api_surface.json"
CHART_VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"

# TC-849: 5 -> 6 (one authored example was added to this sidecar to win back an answer a crashing README example lost).
EXPECTED_EXAMPLES = 6
EXAMPLE_NUMBERS = [1, 2, 3, 4, 5, 6]

#  A true using-DIRECTIVE (``using Namespace;``), never a using-DECLARATION (``using var x = ...;``):
#  the token right after ``using`` must be a bare dotted identifier followed directly by ``;``.
_USING_RE = re.compile(r"(?m)^[ \t]*using\s+(?:static\s+)?([A-Za-z_][\w.]*)\s*;")


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


def test_the_sidecar_loads_and_yields_exactly_six_authored_candidates() -> None:
    candidates = _candidates()
    assert len(candidates) == EXPECTED_EXAMPLES
    assert {candidate.provenance for candidate in candidates} == {"authored"}
    assert {candidate.language for candidate in candidates} == {"csharp"}


def test_the_sidecar_header_matches_the_chart_pin() -> None:
    library = _pilot_library()
    document = _document()
    assert document["schema_version"] == 1
    assert document["pilot"] == PILOT
    assert document["library_repository"] == library["repository"]
    assert document["library_commit"] == library["commit"]
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
def test_the_title_differs_from_every_readme_candidate_title(number: int) -> None:
    assert _example(number)["title"] not in _readme_titles()


_EXAMPLES_WITH_AN_EXPLICIT_ASSERTION = [2, 3, 4, 5, 6]


@pytest.mark.parametrize("number", _EXAMPLES_WITH_AN_EXPLICIT_ASSERTION)
def test_every_example_that_checks_a_value_has_a_real_assertion(number: int) -> None:
    """Example 1 (save a box mesh, matching the pilot's own pre-existing TC-565 builtin style) has
    no explicit check beyond running to completion - the same shape as the pilot's own prior
    authored example - so it is not required to ``throw`` here."""
    code = _example(number)["code"]
    assert "throw new Exception(" in code


@pytest.mark.parametrize("number", EXAMPLE_NUMBERS)
def test_every_library_using_directive_imports_from_the_pilot_namespace(number: int) -> None:
    code = _example(number)["code"]
    usings = [match.group(1).strip() for match in _USING_RE.finditer(code)]
    assert usings, "every example declares at least one using directive"
    for using in usings:
        assert using == "Aspose.ThreeD" or using.startswith("Aspose.ThreeD."), using


def test_no_two_examples_share_identical_code() -> None:
    codes = [_example(number)["code"] for number in EXAMPLE_NUMBERS]
    assert len(set(codes)) == len(codes)


def test_no_example_title_uses_the_word_node() -> None:
    """TC-766: this session closed a real, fleet-wide ``find_examples`` regression caused by
    generic authored-example titles like "node" colliding with unrelated queries via
    ``title_anchored`` (``src/foss_mcp/mcp/tools/relevance.py``); 3d/net's own sibling
    3d/typescript was hit hardest by the word "node" (TC-763). None of this card's new titles use
    that word, deliberately, even though 3d/net's own README-derived candidate "Enumerate a
    Scene's Node Hierarchy" already does (not this card's to change).

    TC-839: exactly ONE title is exempt, ``NODE_TITLE_EXEMPTION``. Once this sidecar is baked, "node"
    is in the code of 5 of the scope's 10 examples, so the round-2 pair 3d/net "node" (frozen as
    must-hit, answered by the README COLLADA example while "node" was unique in code) is no longer
    answered by the rare-code-word path (strictly fewer than half) and became an honest miss. 3d/net
    is not the 3d/typescript collision: its one-word "node" query is exactly what the descendant-
    counting example is about (20 mentions in its code). Every other title still may not use the word."""
    for number in EXAMPLE_NUMBERS:
        title = _example(number)["title"]
        if title == NODE_TITLE_EXEMPTION:
            continue
        title_words = re.findall(r"[A-Za-z]+", title.lower())
        assert "node" not in title_words, title


NODE_TITLE_EXEMPTION = "Recursively Count Every Descendant Node Across a Multi-Level Child Hierarchy"


def test_the_one_title_that_uses_the_word_node_is_the_descendant_counting_example() -> None:
    with_node = [
        _example(number)["title"]
        for number in EXAMPLE_NUMBERS
        if "node" in re.findall(r"[A-Za-z]+", _example(number)["title"].lower())
    ]
    assert with_node == [NODE_TITLE_EXEMPTION]
    assert len(NODE_TITLE_EXEMPTION.split()) <= 12
