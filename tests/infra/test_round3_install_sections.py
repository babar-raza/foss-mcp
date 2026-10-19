"""G3/TC-561 (REQ-G3-009, REQ-G3-008; RD3-091, RD3-092): the ingestion side of install and product-reference truth.

* tex/python: ``import aspose_tex`` raises at the pinned commit (35 of 45 files do not parse as written; the
  extractor recovers the API with a textual repair of the lost indentation). The Extraction notice must say
  that plainly, with the real count, and the coverage line must count those files; the install answer reads the
  statement back out of the index (the round trip below).
* pdf/java: ``Document.getOrCreateInfo`` is deprecated and its javadoc names ``getInfo()``; the chunk must too.
* The call sites in ``infra/build_chunks.py`` main are asserted on committed source (a green unit test of a
  function proves nothing if no entrypoint calls it).

The independent oracles: the count of 35 unparseable files is what the stdlib ``ast`` says about the real
``aspose-tex-foss`` clone at 181015d2 (re-measured by the card's worker), and the javadoc is upstream's own
text. Offline and deterministic.
"""

from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

import pytest

import infra.build_chunks as build_chunks
from foss_mcp.indexing.chunk_builder import build_chunks_from_api_surface
from foss_mcp.mcp.tools.readme_sections import sections_from_documents

REPO_ROOT = Path(__file__).resolve().parents[2]
TEX = REPO_ROOT / "tests" / "fixtures" / "tex_python" / "api_surface.json"
PDF_JAVA = REPO_ROOT / "tests" / "fixtures" / "pdf_java" / "api_surface.json"

STATEMENT = (
    "35 of 45 source files do not parse as written; the API was recovered by a textual repair of the lost "
    "indentation; the library cannot be imported at this commit."
)


def _fixture(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _tex_as_written() -> dict:
    """The committed tex fixture (11 of 45 unreadable after the repair) as a re-extraction by TC-561 records it."""
    fixture = _fixture(TEX)
    fixture["parse_failures"] = {**fixture["parse_failures"], "as_written_failed": 35}
    return fixture


# --- the Extraction notice -------------------------------------------------------------------------------------------


def test_the_notice_states_the_real_count_and_that_the_library_cannot_be_imported() -> None:
    (chunk,) = build_chunks._extraction_notice_chunks(_tex_as_written())

    assert chunk.text.startswith("FQN: Doc: Extraction notice\n")
    assert STATEMENT in chunk.text
    assert "cannot be imported at this commit" in chunk.text


def test_the_notice_no_longer_calls_the_eleven_the_whole_story() -> None:
    (chunk,) = build_chunks._extraction_notice_chunks(_tex_as_written())

    # TC-561: was "11 of 45 source files could not be parsed", which read as 34 healthy files
    assert "11 of 45 source files could not be parsed" not in chunk.text
    assert "11 of 45 source files could not be read even after the repair" in chunk.text
    assert chunk.text.index("35 of 45") < chunk.text.index("11 of 45")


def test_the_notice_has_no_backtick_and_no_blank_line_so_the_chunker_keeps_it_whole() -> None:
    (chunk,) = build_chunks._extraction_notice_chunks(_tex_as_written())

    assert "`" not in chunk.text
    assert "\n\n" not in chunk.text


def test_files_that_do_not_parse_as_written_alone_are_enough_for_a_notice() -> None:
    fixture = {
        "source_repository": "acme/pkg",
        "source_commit": "c" * 40,
        "types": [],
        "parse_failures": {"engine": "ast", "failed": 0, "total": 10, "examples": [], "as_written_failed": 3},
    }

    (chunk,) = build_chunks._extraction_notice_chunks(fixture)

    assert "3 of 10 source files do not parse as written" in chunk.text
    assert "could not be read even after the repair" not in chunk.text


@pytest.mark.parametrize("value", [0, -1, True, "35", None, 3.5], ids=repr)
def test_a_missing_or_malformed_as_written_count_adds_no_statement(value: object) -> None:
    fixture = {
        "source_repository": "acme/pkg",
        "source_commit": "c" * 40,
        "types": [],
        "parse_failures": {
            "engine": "ast",
            "failed": 0,
            "total": 10,
            "examples": [],
            "as_written_failed": value,
        },
    }

    assert build_chunks._extraction_notice_chunks(fixture) == []
    assert build_chunks._as_written_failures(fixture) == 0


def test_the_committed_tex_fixture_without_the_count_keeps_its_notice_as_it_was() -> None:
    (chunk,) = build_chunks._extraction_notice_chunks(_fixture(TEX))

    assert "cannot be imported" not in chunk.text
    assert "11 of 45 source files could not be parsed" in chunk.text


def test_the_statement_survives_the_trip_through_the_index_to_the_install_reader() -> None:
    (chunk,) = build_chunks._extraction_notice_chunks(_tex_as_written())

    sections = sections_from_documents(
        {"scope::doc::0": {"text": f"{chunk.text}\nSource-Commit: 181015d2eee3"}}
    )

    assert sections.import_warning == STATEMENT


def test_the_import_statement_helper_is_the_one_sentence_pair() -> None:
    assert build_chunks.as_written_import_statement(35, 45) == STATEMENT


# --- the coverage line -------------------------------------------------------------------------------------------------


def _coverage_lines(fixture: dict) -> set[str]:
    chunks = build_chunks_from_api_surface(fixture, title="tex", max_types=1000)
    lines: set[str] = set()
    for chunk in chunks:
        for line in chunk.text.splitlines():
            if line.startswith("API-Coverage:"):
                lines.add(line)
    return lines


def test_the_coverage_line_counts_the_files_python_rejects() -> None:
    lines = _coverage_lines(build_chunks._coverage_fixture(_tex_as_written()))

    assert lines and all("35 of 45 source files could not be parsed" in line for line in lines)
    assert not any("11 of 45" in line for line in lines)


def test_the_coverage_line_of_a_fixture_without_the_count_is_unchanged() -> None:
    fixture = _fixture(TEX)

    assert build_chunks._coverage_fixture(fixture) is fixture
    lines = _coverage_lines(fixture)
    assert lines and all("11 of 45 source files could not be parsed" in line for line in lines)


def test_the_coverage_fixture_does_not_mutate_what_it_was_given() -> None:
    fixture = _tex_as_written()
    before = copy.deepcopy(fixture)

    build_chunks._coverage_fixture(fixture)

    assert fixture == before


# --- deprecation replacements (RD3-091) ------------------------------------------------------------------------------


# Upstream's own javadoc of Document.getOrCreateInfo in Aspose.PDF-FOSS-for-Java at 736cedaa, and the doc
# comment the extractor recorded for it.
UPSTREAM_JAVADOC = "@deprecated use {@link #getInfo()} directly; it auto-creates as of Sprint 20."
RECORDED_DOC = "Deprecated alias for {@link #getInfo()}."


@pytest.mark.parametrize(
    ("doc", "expected"),
    [
        pytest.param(RECORDED_DOC, "getInfo()", id="recorded-doc"),
        pytest.param(UPSTREAM_JAVADOC, "getInfo()", id="upstream-javadoc"),
        pytest.param("Deprecated. See {@link Other#run(int) run}.", "Other#run(int)", id="link-with-label"),
        pytest.param("Use Bar.baz() instead.", "Bar.baz()", id="use-sentence"),
        pytest.param("Replaced by `newName`.", "newName", id="replaced-by"),
        pytest.param("Deprecated.", "", id="names-nothing"),
        pytest.param(None, "", id="no-doc"),
    ],
)
def test_the_replacement_a_doc_comment_names(doc: object, expected: str) -> None:
    assert build_chunks._deprecation_replacement(doc) == expected


def test_a_deprecated_member_with_no_recorded_reason_gets_use_and_the_name() -> None:
    fixture = {
        "types": [
            {
                "name": "Document",
                "methods": [
                    {
                        "name": "getOrCreateInfo",
                        "deprecated": True,
                        "deprecated_reason": "",
                        "doc": RECORDED_DOC,
                    }
                ],
            }
        ]
    }

    result = build_chunks.name_deprecation_replacements(fixture)

    assert result["types"][0]["methods"][0]["deprecated_reason"] == "use getInfo()"
    assert fixture["types"][0]["methods"][0]["deprecated_reason"] == ""  # the input is not mutated


def test_a_reason_the_extractor_recorded_is_never_replaced() -> None:
    fixture = {
        "types": [
            {
                "name": "T",
                "properties": [
                    {
                        "name": "p",
                        "deprecated": True,
                        "deprecated_reason": "Use Q instead",
                        "doc": RECORDED_DOC,
                    }
                ],
            }
        ]
    }

    assert build_chunks.name_deprecation_replacements(fixture) is fixture


def test_a_member_that_is_not_deprecated_is_left_alone_whatever_its_doc_says() -> None:
    fixture = {"types": [{"name": "T", "methods": [{"name": "m", "deprecated": False, "doc": RECORDED_DOC}]}]}

    assert build_chunks.name_deprecation_replacements(fixture) is fixture


def test_the_committed_pdf_java_chunk_names_getinfo_as_the_replacement() -> None:
    fixture = _fixture(PDF_JAVA)
    before = next(
        method
        for entry in fixture["types"]
        if entry.get("name") == "Document"
        for method in entry["methods"]
        if method["name"] == "getOrCreateInfo"
    )
    assert before["deprecated"] is True and before["deprecated_reason"] == ""
    assert "{@link #getInfo()}" in before["doc"]  # the oracle: upstream's javadoc names it

    chunks = build_chunks_from_api_surface(
        build_chunks.name_deprecation_replacements(fixture), title="pdf java", max_types=1000
    )
    document = next(chunk for chunk in chunks if chunk.text.startswith("FQN: org.aspose.pdf.Document\n"))

    # TC-557: "getOrCreateInfo() -> DocumentInfo (deprecated: use getInfo())" -> "... -> DocumentInfo throws IOException
    # (deprecated: use getInfo())": Document.java line 1060 declares ``throws IOException``, which is now served
    assert "getOrCreateInfo() -> DocumentInfo throws IOException (deprecated: use getInfo())" in document.text


def test_without_the_step_the_same_chunk_only_says_deprecated() -> None:
    chunks = build_chunks_from_api_surface(_fixture(PDF_JAVA), title="pdf java", max_types=1000)
    document = next(chunk for chunk in chunks if chunk.text.startswith("FQN: org.aspose.pdf.Document\n"))

    # TC-557: "-> DocumentInfo (deprecated)" -> "-> DocumentInfo throws IOException (deprecated)" (the throws clause is served)
    assert "getOrCreateInfo() -> DocumentInfo throws IOException (deprecated)" in document.text
    assert "use getInfo()" not in document.text


# --- the entrypoint calls them ----------------------------------------------------------------------------------------------------


def test_main_builds_its_chunks_through_both_steps() -> None:
    source = (REPO_ROOT / "infra" / "build_chunks.py").read_text(encoding="utf-8")

    assert re.search(r"_coverage_fixture\(\s*name_deprecation_replacements\(fixture\)\s*\)", source), (
        "main must pass the fixture through name_deprecation_replacements and _coverage_fixture"
    )
    assert "_extraction_notice_chunks(fixture)" in source


def test_main_end_to_end_writes_the_statement_and_the_deprecation_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tex = tmp_path / "tex.json"
    tex.write_text(json.dumps(_tex_as_written()), encoding="utf-8")
    java = tmp_path / "java.json"
    java.write_text(PDF_JAVA.read_text(encoding="utf-8"), encoding="utf-8")

    texts: dict[str, str] = {}
    for name, api_surface in (("tex", tex), ("java", java)):
        out = tmp_path / f"{name}.chunks.json"
        monkeypatch.setattr(
            sys,
            "argv",
            ["build_chunks.py", "--api-surface", str(api_surface), "--title", name, "--out", str(out)],
        )
        build_chunks.main()
        texts[name] = "\n\n".join(
            chunk["text"] for chunk in json.loads(out.read_text(encoding="utf-8"))["chunks"]
        )

    assert STATEMENT in texts["tex"]
    assert "API-Coverage:" in texts["tex"] and "35 of 45 source files could not be parsed" in texts["tex"]
    # TC-557: "-> DocumentInfo (deprecated: ...)" -> "-> DocumentInfo throws IOException (deprecated: ...)"
    assert "getOrCreateInfo() -> DocumentInfo throws IOException (deprecated: use getInfo())" in texts["java"]
