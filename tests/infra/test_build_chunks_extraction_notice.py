"""Offline tests for the extraction notice and the private-origin syntax error (REQ-G3-005, TC-426).

A pilot whose pinned source does not parse must say so: ``_extraction_notice_chunks`` publishes one
searchable ``doc`` chunk naming the unparseable files and the public names that could not be
extracted, and the Python reader records a syntax error in a private re-export origin instead of
reporting only ``unresolved-reexport``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import infra.build_chunks as build_chunks
from foss_mcp.extraction.tree_sitter_engine.python_surface import inspect_public_surface
from foss_mcp.indexing.chunk_builder import build_chunks_from_api_surface

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests/fixtures"
TEX = FIXTURES / "tex_python/api_surface.json"
PDF = FIXTURES / "pdf_python/api_surface.json"

BROKEN_PRESENTATION = "src/aspose_tex/presentation/__init__.py"
EXPECTED_NAMES = (
    "TeXJob",
    "TeXOptions",
    "PdfDevice",
    "SvgDevice",
    "DviDevice",
    "create_input_source",
    "FileInputSource",
)


def _fixture(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# TC-555: the committed tex fixture no longer has a syntax error (its two files are repaired), so the
# notice for a file that does NOT parse is exercised on the fixture as it was before the repair:
# the same source, the same two syntax errors and the eleven re-exports they hid.
_UNPARSED_TEX_UNRESOLVED = [
    "aspose_tex._input.reader:53:syntax-error:src/aspose_tex/_input/reader.py:"
    "expected an indented block after function definition on line 52",
    "aspose_tex.presentation:108:syntax-error:src/aspose_tex/presentation/__init__.py:"
    "expected an indented block after function definition on line 107",
    "aspose_tex:7:unresolved-reexport:aspose_tex._input.reader.FileInputSource",
    "aspose_tex:7:unresolved-reexport:aspose_tex._input.reader.InputSource",
    "aspose_tex:7:unresolved-reexport:aspose_tex._input.reader.StringInputSource",
    "aspose_tex:8:unresolved-reexport:aspose_tex.presentation.DviDevice",
    "aspose_tex:8:unresolved-reexport:aspose_tex.presentation.OutputDevice",
    "aspose_tex:8:unresolved-reexport:aspose_tex.presentation.OutputFormat",
    "aspose_tex:8:unresolved-reexport:aspose_tex.presentation.PdfDevice",
    "aspose_tex:8:unresolved-reexport:aspose_tex.presentation.SvgDevice",
    "aspose_tex:8:unresolved-reexport:aspose_tex.presentation.TeXJob",
    "aspose_tex:8:unresolved-reexport:aspose_tex.presentation.TeXOptions",
    "aspose_tex:8:unresolved-reexport:aspose_tex.presentation.create_input_source",
]


def _unparsed_tex() -> dict:
    fixture = _fixture(TEX)
    fixture.pop("parse_failures", None)
    fixture["unresolved"] = list(_UNPARSED_TEX_UNRESOLVED)
    return fixture


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _run_main(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, api_surface: Path) -> list[dict]:
    out = tmp_path / "chunks.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build_chunks.py",
            "--api-surface",
            str(api_surface),
            "--title",
            "Notice fixture",
            "--out",
            str(out),
        ],
    )
    build_chunks.main()
    return json.loads(out.read_text(encoding="utf-8"))["chunks"]


# --- the notice builder, on the real fixtures -------------------------------------------------


def test_tex_fixture_yields_exactly_one_doc_notice() -> None:
    chunks = build_chunks._extraction_notice_chunks(_unparsed_tex())
    assert len(chunks) == 1
    assert chunks[0].content_type == "doc"
    assert chunks[0].section_title == "Extraction notice"


def test_notice_text_starts_with_the_doc_marker() -> None:
    (chunk,) = build_chunks._extraction_notice_chunks(_unparsed_tex())
    assert chunk.text.startswith("FQN: Doc: Extraction notice\n")


def test_notice_names_the_unparseable_file_and_line() -> None:
    (chunk,) = build_chunks._extraction_notice_chunks(_unparsed_tex())
    assert f"{BROKEN_PRESENTATION} line 108" in chunk.text


def test_notice_names_every_public_name_that_could_not_be_extracted() -> None:
    (chunk,) = build_chunks._extraction_notice_chunks(_unparsed_tex())
    for name in EXPECTED_NAMES:
        assert name in chunk.text, name
    assert "not found" in chunk.text


def test_notice_has_no_backtick_and_states_the_source_commit() -> None:
    fixture = _unparsed_tex()
    (chunk,) = build_chunks._extraction_notice_chunks(fixture)
    assert "`" not in chunk.text
    assert f"{fixture['source_repository']}@{fixture['source_commit']}" in chunk.text
    assert chunk.provenance.repository == fixture["source_repository"]
    assert chunk.provenance.commit == fixture["source_commit"]


@pytest.mark.parametrize("pilot", ["pdf_python", "html_python", "words_python"])
def test_fixtures_without_a_syntax_error_yield_no_notice(pilot: str) -> None:
    fixture = _fixture(FIXTURES / pilot / "api_surface.json")
    assert build_chunks._extraction_notice_chunks(fixture) == []


def test_a_syntax_error_without_reexports_still_yields_a_notice_without_names() -> None:
    fixture = {
        "source_repository": "example-org/example-python",
        "source_commit": "0" * 40,
        "unresolved": ["pkg.mod:3:syntax-error:src/pkg/mod.py:invalid syntax"],
    }
    (chunk,) = build_chunks._extraction_notice_chunks(fixture)
    assert "src/pkg/mod.py line 3: invalid syntax" in chunk.text
    assert "Public names" not in chunk.text


# --- main, end to end -------------------------------------------------------------------------


def test_main_on_tex_writes_the_notice_with_a_source_commit_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _unparsed_tex()
    unparsed = tmp_path / "unparsed_tex.json"
    unparsed.write_text(json.dumps(fixture), encoding="utf-8")
    chunks = _run_main(monkeypatch, tmp_path, unparsed)
    notices = [c for c in chunks if c["text"].startswith("FQN: Doc: Extraction notice")]
    assert len(notices) == 1
    assert f"{BROKEN_PRESENTATION} line 108" in notices[0]["text"]
    assert notices[0]["text"].endswith(f"\nSource-Commit: {fixture['source_commit']}")
    assert notices[0]["content_type"] == "doc"


_REPAIRED = [
    "aspose_tex._input.reader:52:repaired-syntax:src/aspose_tex/_input/reader.py:42 header lines closed with pass",
    "aspose_tex.presentation:107:repaired-syntax:src/aspose_tex/presentation/__init__.py:36 header lines closed with pass",
]


def test_a_repaired_file_gets_an_honest_sentence_in_the_notice() -> None:
    # TC-555: the notice does not print every unresolved entry verbatim, so a `repaired-syntax`
    # entry needs its own sentence; without it a repaired pilot would publish no notice at all.
    fixture = {"source_repository": "org/repo", "source_commit": "0" * 40, "unresolved": list(_REPAIRED)}
    (chunk,) = build_chunks._extraction_notice_chunks(fixture)
    assert chunk.section_title == "Extraction notice"
    assert "parsed only after their lost indentation was repaired" in chunk.text
    assert "declarations and signatures are indexed" in chunk.text
    assert "function bodies and the docstrings of functions are not" in chunk.text
    assert "src/aspose_tex/_input/reader.py line 52: 42 header lines closed with pass" in chunk.text
    assert "src/aspose_tex/presentation/__init__.py line 107: 36 header lines closed with pass" in chunk.text
    assert "could not be fully read" not in chunk.text  # nothing was skipped
    assert "Lookups for these names" not in chunk.text
    assert "`" not in chunk.text and "\n\n" not in chunk.text


def test_the_committed_tex_fixture_discloses_its_two_repaired_files() -> None:
    fixture = _fixture(TEX)
    (chunk,) = build_chunks._extraction_notice_chunks(fixture)
    assert f"{BROKEN_PRESENTATION} line 107: 36 header lines closed with pass" in chunk.text
    assert "src/aspose_tex/_input/reader.py line 52: 42 header lines closed with pass" in chunk.text
    assert "do not parse as valid source" not in chunk.text
    assert "Public names that could not be extracted" not in chunk.text
    assert f"{fixture['source_repository']}@{fixture['source_commit']}" in chunk.text


def test_a_repaired_file_and_a_syntax_error_share_one_notice() -> None:
    fixture = {
        "source_repository": "org/repo",
        "source_commit": "0" * 40,
        "unresolved": [*_REPAIRED, "pkg.mod:3:syntax-error:src/pkg/mod.py:invalid syntax"],
    }
    (chunk,) = build_chunks._extraction_notice_chunks(fixture)
    assert "src/pkg/mod.py line 3: invalid syntax" in chunk.text
    assert "42 header lines closed with pass" in chunk.text
    assert "could not be fully read" in chunk.text


def test_no_other_committed_python_fixture_has_a_repaired_entry() -> None:
    for path in sorted(FIXTURES.glob("*_python/api_surface.json")):
        if path.parent.name == "tex_python":
            continue
        assert not any(":repaired-syntax:" in entry for entry in _fixture(path).get("unresolved", [])), path


def test_main_on_the_repaired_tex_fixture_writes_the_notice_with_a_source_commit_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(TEX)
    chunks = _run_main(monkeypatch, tmp_path, TEX)
    notices = [c for c in chunks if c["text"].startswith("FQN: Doc: Extraction notice")]
    assert len(notices) == 1
    assert "36 header lines closed with pass" in notices[0]["text"]
    assert notices[0]["text"].endswith(f"\nSource-Commit: {fixture['source_commit']}")


def test_main_on_pdf_python_writes_no_notice_and_the_same_chunk_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(PDF)
    expected = build_chunks_from_api_surface(fixture, title="Notice fixture", max_types=300)
    chunks = _run_main(monkeypatch, tmp_path, PDF)
    assert not any(
        "Extraction notice" in c["section_title"] or "Extraction notice" in c["text"] for c in chunks
    )
    assert len(chunks) == len(expected)


# --- the Python reader, on tmp packages -------------------------------------------------------


def test_classes_and_functions_in_a_package_init_are_extracted(tmp_path: Path) -> None:
    _write(tmp_path, "src/pkg/__init__.py", "class Job:\n    pass\n\n\ndef make():\n    return Job()\n")
    surface = inspect_public_surface(tmp_path, ["src/pkg"])
    names = {s.qualified_name: s.kind for s in surface.symbols}
    assert names["pkg.Job"] == "class"
    assert names["pkg.make"] == "function"
    assert surface.unresolved == ()


def test_a_package_init_that_does_not_parse_yields_its_syntax_error_and_no_symbols(tmp_path: Path) -> None:
    # TC-555: was "class Job:\npass\n", a collapsed indentation the extractor now repairs; a real
    # syntax error at the same line keeps the file unparseable.
    _write(tmp_path, "src/pkg/__init__.py", "class Job:\n    x = = 1\n")
    surface = inspect_public_surface(tmp_path, ["src/pkg"])
    assert not any(s.qualified_name == "pkg.Job" for s in surface.symbols)
    assert len(surface.unresolved) == 1
    assert surface.unresolved[0].startswith("pkg:2:syntax-error:src/pkg/__init__.py:")


def test_a_reexport_from_an_unparseable_private_module_records_both_entries(tmp_path: Path) -> None:
    _write(tmp_path, "src/pkg/__init__.py", "from ._impl import Job\n")
    # TC-555: was "class Job:\npass\n" (repairable now); a real syntax error at the same line.
    _write(tmp_path, "src/pkg/_impl.py", "class Job:\n    x = = 1\n")
    surface = inspect_public_surface(tmp_path, ["src/pkg"])
    assert "pkg:1:unresolved-reexport:pkg._impl.Job" in surface.unresolved
    errors = [e for e in surface.unresolved if ":syntax-error:" in e]
    assert len(errors) == 1
    assert errors[0].startswith("pkg._impl:2:syntax-error:src/pkg/_impl.py:")


def test_one_syntax_error_entry_per_file_however_many_names_it_hides(tmp_path: Path) -> None:
    _write(tmp_path, "src/pkg/__init__.py", "from ._impl import Job, Options\n")
    # TC-555: was "class Job:\npass\n" (repairable now); a real syntax error at the same line.
    _write(tmp_path, "src/pkg/_impl.py", "class Job:\n    x = = 1\n")
    surface = inspect_public_surface(tmp_path, ["src/pkg"])
    assert len([e for e in surface.unresolved if ":syntax-error:" in e]) == 1
    assert len([e for e in surface.unresolved if ":unresolved-reexport:" in e]) == 2


def test_a_parseable_private_origin_yields_no_syntax_error(tmp_path: Path) -> None:
    _write(tmp_path, "src/pkg/__init__.py", "from ._impl import Job, Missing\n")
    _write(tmp_path, "src/pkg/_impl.py", "class Job:\n    pass\n")
    surface = inspect_public_surface(tmp_path, ["src/pkg"])
    assert not any(":syntax-error:" in e for e in surface.unresolved)
    assert "pkg:1:unresolved-reexport:pkg._impl.Missing" in surface.unresolved


def test_a_forwarded_private_origin_that_does_not_parse_is_followed(tmp_path: Path) -> None:
    _write(tmp_path, "src/pkg/__init__.py", "from ._front import Job\n")
    _write(tmp_path, "src/pkg/_front.py", "from ._back import Job\n")
    _write(tmp_path, "src/pkg/_back.py", "def broken(:\n")
    surface = inspect_public_surface(tmp_path, ["src/pkg"])
    errors = [e for e in surface.unresolved if ":syntax-error:" in e]
    assert len(errors) == 1
    assert errors[0].startswith("pkg._back:1:syntax-error:src/pkg/_back.py:")


def test_a_forwarding_cycle_terminates_without_a_syntax_error(tmp_path: Path) -> None:
    _write(tmp_path, "src/pkg/__init__.py", "from ._a import Job\n")
    _write(tmp_path, "src/pkg/_a.py", "from ._b import Job\n")
    _write(tmp_path, "src/pkg/_b.py", "from ._a import Job\n")
    surface = inspect_public_surface(tmp_path, ["src/pkg"])
    assert "pkg:1:unresolved-reexport:pkg._a.Job" in surface.unresolved
    assert not any(":syntax-error:" in e for e in surface.unresolved)
