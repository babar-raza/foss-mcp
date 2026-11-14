"""Offline checks of the note/python authored-example sidecar (TC-769, REQ-G3-014).

``tests/fixtures/furnished/note_python/authored_examples.yaml`` holds exactly five minimal,
labelled examples written against the real public API of ``aspose-note-foss`` at the pinned
commit, each exercising a real ``testfiles/*.one`` fixture (``SimpleHistory.one``,
``Issue1Subpages.one``, ``AttachedFileWithTag.one``, ``TableWithTag.one``, ``ImageWithTag.one``)
that none of the pilot's 12 README-derived candidates already cover: a page's revision history
via ``PageHistory``, telling a subpage apart from its parent via ``Page.Level``, and reading the
OneNote tags attached directly to an attachment, a table, and an image. None of them exercises
either upstream limitation already disclosed in ``config/known_defects/note__python.yaml``
(``DocumentVisitor`` not dispatching into tables/attachments, or ``RichText.Text`` embedding raw
hyperlink field codes) - none uses ``DocumentVisitor`` or reads ``RichText.Text`` at all.

This file proves they are well-formed, labelled, tied to the pilot's pinned commit and use only
symbols that resolve in the pilot's extraction. Whether they run is decided later by the ingestion
verifier, never here: nothing in this file imports the library, runs an example, or reaches a
network.

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

PILOT = "note_python"

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
SIDECAR = FIXTURES / "furnished" / PILOT / SIDECAR_NAME
PAGE = FIXTURES / "furnished" / PILOT / "pages" / "_index.md"
API_SURFACE = FIXTURES / PILOT / "api_surface.json"
CHART_VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"
KNOWN_DEFECTS = REPO_ROOT / "config" / "known_defects" / "note__python.yaml"

EXPECTED_EXAMPLES = 5
EXAMPLE_INDEXES = list(range(EXPECTED_EXAMPLES))

_IMPORT_RE = re.compile(r"(?m)^[ \t]*(?:import\s+([^\s#]+)|from\s+([^\s#]+)\s+import\b)")

# The two upstream limitations config/known_defects/note__python.yaml already discloses (TC-769's
# own instructions): DocumentVisitor never dispatching into Table/TableRow/TableCell/AttachedFile
# (NOTE-PY-002), and RichText.Text embedding raw hyperlink field codes (NOTE-PY-008). None of the
# five new examples may exercise either, so none may use DocumentVisitor or read RichText.Text.
_DISCLOSED_DEFECT_IDS = ("NOTE-PY-002", "NOTE-PY-008")


def _pilot_library() -> dict:
    values = yaml.safe_load(CHART_VALUES.read_text(encoding="utf-8"))
    for pilot in values["ingestion"]["pilots"]:
        if f"{pilot['family']}_{pilot['platform']}" == PILOT:
            return pilot["library"]
    raise AssertionError(f"{PILOT} is not a pilot in values.yaml")


def _api_types() -> list[dict]:
    return json.loads(API_SURFACE.read_text(encoding="utf-8"))["types"]


def _document() -> dict:
    return yaml.safe_load(SIDECAR.read_bytes().decode("utf-8"))


def _example(index: int) -> dict:
    return _document()["examples"][index]


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


def test_the_sidecar_loads_and_yields_exactly_five_authored_candidates() -> None:
    candidates = _candidates()
    assert len(candidates) == EXPECTED_EXAMPLES
    assert {candidate.provenance for candidate in candidates} == {"authored"}
    assert {candidate.language for candidate in candidates} == {"python"}


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


def test_a_stale_commit_is_refused_by_the_validator() -> None:
    library = _pilot_library()
    document = _document()
    document["library_commit"] = "0" * 40
    problems = validate_authored_examples(
        document,
        pilot=PILOT,
        library_platform=library["platform"],
        library_commit=library["commit"],
        api_types=_api_types(),
    )
    assert any("rule 3" in problem for problem in problems), problems


def test_the_example_titles_are_unique_and_differ_from_every_readme_title() -> None:
    examples = _document()["examples"]
    titles = [example["title"] for example in examples]
    assert len(set(titles)) == len(titles)
    readme_titles = _readme_titles()
    assert readme_titles, "the committed page must carry at least one README candidate"
    assert not set(titles) & readme_titles
    for example in examples:
        assert len(example["symbols"]) >= 2, example["title"]


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_the_code_hash_matches(index: int) -> None:
    example = _example(index)
    assert example["code_sha256"] == normalised_code_sha256(example["code"])


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_the_label_comment_is_the_first_non_blank_line(index: int) -> None:
    code = _example(index)["code"]
    first = next(line.strip() for line in code.splitlines() if line.strip())
    assert first.startswith("#")
    assert "Authored example" in first


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_every_declared_symbol_resolves_and_appears_in_the_code(index: int) -> None:
    example = _example(index)
    index_of_symbols = qualified_symbol_index_from_api_surface(_api_types())
    for symbol in example["symbols"]:
        assert index_of_symbols.declarations_for(symbol), f"{symbol} has no declaration in the api_surface"
        last_segment = symbol.rsplit(".", 1)[-1]
        assert re.search(rf"(?<![A-Za-z0-9_]){re.escape(last_segment)}(?![A-Za-z0-9_])", example["code"]), (
            f"{symbol} is not used in the code"
        )


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_no_forbidden_pattern_matches(index: int) -> None:
    code = _example(index)["code"]
    hits = [pattern.pattern for pattern in FORBIDDEN_CODE_PATTERNS if pattern.search(code)]
    assert hits == []


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_every_example_imports_from_the_pilots_own_package(index: int) -> None:
    code = _example(index)["code"]
    modules = [module or package for module, package in _IMPORT_RE.findall(code)]
    assert modules, "the example imports nothing"
    library_imports = [module for module in modules if module.startswith("aspose.note")]
    assert library_imports, "the example imports nothing from aspose.note"
    # Every other import is standard library; a third-party package would not resolve in the verifier's project.
    stdlib_modules = {"sys"}
    for module in modules:
        top_level = module.split(".", 1)[0]
        assert module.startswith("aspose.note") or top_level in stdlib_modules, f"unexpected import: {module}"


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_no_example_carries_unittest_or_sys_path_scaffolding(index: int) -> None:
    # The upstream examples/*.py scripts carry no unittest harness of their own, but the sibling
    # cells/python sidecar (the template for this file) came from unittest.TestCase sources, so
    # the same guard is kept here for consistency: an authored example must be standalone logic.
    code = _example(index)["code"]
    assert "unittest" not in code
    assert "sys.path" not in code
    assert "self.assert" not in code


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_no_example_depends_on_a_file_that_does_not_exist(index: int) -> None:
    code = _example(index)["code"]
    # note/python's Document.Save() writes only PDF (.one-save is unimplemented upstream, see
    # config/known_defects/note__python.yaml); none of these examples saves anything to disk.
    assert ".save(" not in code.lower()


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_no_example_uses_documentvisitor_or_reads_richtext_text(index: int) -> None:
    # NOTE-PY-002 (DocumentVisitor never dispatches into Table/TableRow/TableCell/AttachedFile) and
    # NOTE-PY-008 (RichText.Text embeds raw hyperlink field codes) are both already disclosed in
    # config/known_defects/note__python.yaml; no new example may be built around either path.
    code = _example(index)["code"]
    assert "DocumentVisitor" not in code
    assert "RichText" not in code
    assert ".Text" not in code


def test_the_known_defects_file_still_discloses_the_two_limitations_these_examples_avoid() -> None:
    document = yaml.safe_load(KNOWN_DEFECTS.read_text(encoding="utf-8"))
    disclosed_ids = {defect["id"] for defect in document["defects"]}
    for defect_id in _DISCLOSED_DEFECT_IDS:
        assert defect_id in disclosed_ids, f"{defect_id} is no longer disclosed in {KNOWN_DEFECTS}"


def test_every_example_loads_a_one_test_fixture_that_really_exists_in_the_pinned_repository() -> None:
    # Confirms each example names a real testfiles/*.one fixture of the pinned upstream repository
    # (Appendix C: 12 real .one files at the pinned commit) rather than an invented file name.
    real_testfiles = {
        "3ImagesWithDifferentAlignment.one",
        "AttachedFileWithTag.one",
        "FormattedRichText.one",
        "ImageWithTag.one",
        "Issue1Subpages.one",
        "NumberedListWithTags.one",
        "OnePageWithFile.one",
        "SimpleHistory.one",
        "SimpleImageFromSeparateFile.one",
        "SimpleTable.one",
        "TableWithTag.one",
        "TagSizes.one",
    }
    fixture_re = re.compile(r'"([A-Za-z0-9_.]+\.one)"')
    for index in EXAMPLE_INDEXES:
        code = _example(index)["code"]
        named = set(fixture_re.findall(code))
        assert named, f"example {index} names no .one fixture"
        assert named <= real_testfiles, f"example {index} names an unknown fixture: {named - real_testfiles}"


def test_the_two_examples_that_anchored_the_query_read_page_titles_do_not_start_with_the_verb_read() -> None:
    # TC-839: once this sidecar is baked, the title word "read" anchored the find_examples query "read page
    # titles" (a request for page titles) on "Read a Document's Revision History" and "Read OneNote Tags on
    # an Attached File": two answers that are about neither, a new wrong-confident case that used up the whole
    # 43/729 ceiling of test_round3_retrieval_corpus.py. They are titled by their subject instead.
    titles = {example["title"] for example in _document()["examples"]}
    retitled = (
        "Document Revision History Loaded With LoadHistory",
        "OneNote Tags on an Attached File and Their Status",
    )
    for title in retitled:
        assert title in titles
        assert "read" not in re.findall(r"[A-Za-z]+", title.lower()), title
        assert len(title.split()) <= 12, title
