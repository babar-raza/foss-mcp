"""TC-520 (REQ-G3-005): the Extraction notice carries the parse-failure headline.

``_extraction_notice_chunks`` also emits the notice when the fixture's ``parse_failures`` is over the
engine's threshold, even with no ``:syntax-error:`` entry, and puts one headline line first. Offline.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

import infra.build_chunks as build_chunks

REPO_ROOT = Path(__file__).resolve().parents[2]
TEX = REPO_ROOT / "tests/fixtures/tex_python/api_surface.json"
EXAMPLES = [f"src/p/m{index}.py:{index + 2}: bad block {index}" for index in range(5)]


def _fixture(**extra: Any) -> dict[str, Any]:
    return {"source_repository": "org/repo", "source_commit": "0" * 40, **extra}


def _failures(failed: int, total: int, engine: str = "ast") -> dict[str, Any]:
    return {"engine": engine, "failed": failed, "total": total, "examples": EXAMPLES}


def test_notice_is_emitted_over_the_threshold_without_a_syntax_error_entry() -> None:
    chunks = build_chunks._extraction_notice_chunks(_fixture(parse_failures=_failures(35, 45)))
    assert len(chunks) == 1
    assert chunks[0].content_type == "doc"
    assert chunks[0].section_title == "Extraction notice"


def test_the_headline_comes_first_and_lists_up_to_five_examples() -> None:
    (chunk,) = build_chunks._extraction_notice_chunks(_fixture(parse_failures=_failures(35, 45)))
    lines = chunk.text.splitlines()
    assert lines[0] == "FQN: Doc: Extraction notice"
    assert lines[1] == f"35 of 45 source files could not be parsed (examples: {', '.join(EXAMPLES)})."
    assert "org/repo@" + "0" * 40 in chunk.text


def test_the_notice_is_one_chunk_with_no_backtick_and_no_blank_line() -> None:
    parse_failures = _failures(35, 45)
    parse_failures["examples"] = ["src/p/`odd`.py:2: bad `thing`", *EXAMPLES]
    fixture = _fixture(
        parse_failures=parse_failures, unresolved=["pkg.a:3:syntax-error:src/pkg/a.py:invalid syntax"]
    )
    chunks = build_chunks._extraction_notice_chunks(fixture)
    assert len(chunks) == 1
    text = chunks[0].text
    assert "`" not in text
    assert "\n\n" not in text
    assert "src/pkg/a.py line 3: invalid syntax" in text  # the existing listing stays


def test_no_notice_at_or_below_the_threshold() -> None:
    assert build_chunks._extraction_notice_chunks(_fixture(parse_failures=_failures(10, 100))) == []
    assert (
        build_chunks._extraction_notice_chunks(_fixture(parse_failures=_failures(20, 100, "tree-sitter")))
        == []
    )
    assert build_chunks._extraction_notice_chunks(_fixture(parse_failures=_failures(0, 0))) == []
    assert build_chunks._extraction_notice_chunks(_fixture(parse_failures="bad")) == []


def test_a_tree_sitter_fixture_over_its_higher_threshold_gets_the_notice() -> None:
    chunks = build_chunks._extraction_notice_chunks(
        _fixture(parse_failures=_failures(30, 100, "tree-sitter"))
    )
    assert len(chunks) == 1
    assert "30 of 100 source files could not be parsed" in chunks[0].text


def test_tex_without_parse_failures_is_still_one_chunk_without_a_headline() -> None:
    fixture = json.loads(TEX.read_text(encoding="utf-8"))
    # TC-555: the committed fixture now carries the measured parse_failures (35 of 45); the old
    # "without parse_failures" shape is the same fixture with that key removed. Its notice no longer
    # names a syntax error (line 108) but the repaired file (line 107).
    fixture.pop("parse_failures")
    chunks = build_chunks._extraction_notice_chunks(fixture)
    assert len(chunks) == 1
    assert "source files could not be parsed" not in chunks[0].text
    assert "src/aspose_tex/presentation/__init__.py line 107" in chunks[0].text


def test_tex_with_parse_failures_keeps_one_chunk_and_leads_with_the_headline() -> None:
    fixture = json.loads(TEX.read_text(encoding="utf-8"))
    fixture["parse_failures"] = _failures(35, 45)
    chunks = build_chunks._extraction_notice_chunks(fixture)
    assert len(chunks) == 1
    assert chunks[0].text.splitlines()[1].startswith("35 of 45 source files could not be parsed (examples: ")
    # TC-555: "Public names that could not be extracted" -> the repaired files; no name is lost any more.
    assert "Public names that could not be extracted" not in chunks[0].text
    assert "src/aspose_tex/presentation/__init__.py line 107" in chunks[0].text


def test_main_on_the_committed_tex_fixture_ends_symbol_chunks_with_the_coverage_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "chunks.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["build_chunks.py", "--api-surface", str(TEX), "--title", "Tex", "--out", str(out)],
    )
    build_chunks.main()
    fixture = json.loads(TEX.read_text(encoding="utf-8"))
    chunks = json.loads(out.read_text(encoding="utf-8"))["chunks"]
    symbols = [c for c in chunks if c["text"].startswith("FQN: ") and not c["text"].startswith("FQN: Doc:")]
    # TC-555: 4 -> 23 symbol chunks (21 types and 2 functions); the line carries the measured
    # parse_failures of the committed fixture instead of "at least 2 source files".
    assert len(symbols) == 23
    expected = "API-Coverage: 21 of 21 types; 2 of 2 functions; 35 of 45 source files could not be parsed"
    for chunk in symbols:
        lines = chunk["text"].splitlines()
        assert lines[-2] == expected
        assert lines[-1] == f"Source-Commit: {fixture['source_commit']}"
