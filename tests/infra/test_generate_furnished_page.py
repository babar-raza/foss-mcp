"""Tests for infra/generate_furnished_page.py (G2/TC-119, REQ-G2-047).

``sealed_candidate_doc_fields`` reads repository-presenter's own local, SEALED
candidate README - never a live upstream fetch - and returns the 3 fields
(``overview``/``content``/``faq``) ``foss_mcp.indexing.doc_candidates.extract_doc_sections``
expects, plus the candidate's own source commit.

The ABSOLUTE CONSTRAINT this card exists under: regenerating a fixture's overview/
content/faq must never change its ``single.block`` by even one byte - that block is the
live source for the already-accepted, already-in-production compile-verified example
pipeline (TC-066/079/090). The hashes recorded below were computed from each of the 5
real fixtures BEFORE this card's regeneration ran, from the committed pre-TC-119 tree -
they are the independent, falsifiable proof, not a hash recomputed after the fact.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

import infra.build_chunks as build_chunks
import infra.generate_furnished_page as gen
from foss_mcp.indexing.doc_candidates import extract_doc_sections
from foss_mcp.indexing.example_candidates import extract_candidate_examples
from foss_mcp.mcp.tools.search_docs import classify_content_type

FIXTURE_ROOT = Path("tests/fixtures/furnished")

# sha256 of each real fixture's own "single:" top-level YAML span, computed from the
# committed tree BEFORE this card touched anything - the falsifiable proof that
# regeneration left single.block byte-for-byte identical.
_EXPECTED_SINGLE_BLOCK_SHA256 = {
    "pdf_net": "76639ff2f833824a826a47c2234f08da26178ca6567e3c9e79b7b51beac6344b",
    "pdf_java": "9d095724ceae10007cc07ffae026cfd2fd40fbc6789a88986d0ef50fafb8a844",
    "pdf_go": "688eb31eb25bd7cd1c0958a37942c56032b109eb570f9e12da971ca3bf4e52c4",
    "slides_python": "f6966cd54cce959675968ddebcabad4d62810d0f739f1d046980f4516945e7b8",
    "cells_rust": "63a56226e5d7d3cb24972820a398fd805c0dc5a30e4d54b2886b12c4b3c48c37",
}

# This project's own --library-commit pin for each pilot's compile-verified-example
# path (docker-compose.yml's real ingest-<pilot> services) - deliberately NOT the same
# thing as a sealed candidate's own source commit, and must never be conflated with it.
_LIBRARY_COMMIT = {
    "pdf_net": "b7172877651413cff57a8bfe41fb8a8befb2406b",
    "pdf_java": "db2d3f0622f035825419c6d46727022064f39f15",
    "pdf_go": "286484d235196d65c9a458c5eff3d3d6539216dc",
    "slides_python": "4e63447ba79d1c27a5192844847d9f872c5b92ad",
    "cells_rust": "1a6004af47b1ef15385f9d36d381a8172428cc7e",
}

# The known, pre-existing candidate-example count per pilot (test_example_candidates.py's
# own already-accepted assertions) - re-checked here only to prove regeneration left
# single.block functionally intact too, not only byte-identical.
_EXPECTED_EXAMPLE_COUNT = {
    "pdf_net": 3,
    "pdf_java": 3,
    "pdf_go": 4,
    "slides_python": 2,
    "cells_rust": 3,
}

_REPOSITORY_PRESENTER_ROOT = Path("H:/Users/prora/OneDrive/Documents/GitHub/repository-presenter")


def _single_block_sha256(fixture_path: Path) -> str:
    text = fixture_path.read_text(encoding="utf-8")
    front_matter = text.split("---", 2)[1]
    spans = dict(gen.top_level_spans(front_matter))
    return hashlib.sha256(spans["single"].encode("utf-8")).hexdigest()


def _write_synthetic_sealed_candidate(root: Path, repository: str, commit: str, readme: str) -> None:
    candidate_dir = root / "candidates" / repository.replace("/", "__")
    candidate_dir.mkdir(parents=True, exist_ok=True)
    (candidate_dir / "CURRENT").write_text(commit, encoding="utf-8")
    commit_dir = candidate_dir / commit
    commit_dir.mkdir(parents=True, exist_ok=True)
    (commit_dir / "README.md").write_text(readme, encoding="utf-8")


_SYNTHETIC_README = """# Widget FOSS for Python

Widget FOSS for Python is a free library for making widgets, with zero paid dependencies.

## Navigation

- [At a Glance](#at-a-glance)

## At a Glance

```mermaid
flowchart TD
  PRODUCT["Widget FOSS"]
```

## Key Capabilities

- **Make widgets.** Create a `Widget` with `Widget.create()`.

## Installation

Install from PyPI:

```bash
pip install widget-foss
```

## Dependencies

### Required Package Dependencies

- `six 1.16.0`

## Quick Start

```python
widget = Widget.create()
widget.frobnicate()
```

## Additional Examples

Not used by this card.

## API Reference

The public surface has 12 types.

| Class | Description |
| --- | --- |
| `Widget` | The main widget class. |

## Documentation & Resources

- Found a bug? Open an issue.

## Scope and Limitations

Widget FOSS does not support gears larger than 4cm.

- `Widget.spin()` is present but not active on Windows.

## Development and Testing

Run `pytest`.

## License

MIT.
"""


def test_sealed_candidate_doc_fields_parses_the_real_expected_shape(tmp_path: Path) -> None:
    _write_synthetic_sealed_candidate(tmp_path, "widget-org/widget-foss", "abc123deadbeef", _SYNTHETIC_README)

    fields, commit = gen.sealed_candidate_doc_fields("widget-org/widget-foss", tmp_path)

    assert commit == "abc123deadbeef"

    assert fields["overview"]["enable"] is True
    assert fields["overview"]["title"] == "Widget FOSS for Python"
    assert "```mermaid" in fields["overview"]["content"]
    assert "PRODUCT" in fields["overview"]["content"]

    block = fields["content"]["block"]
    assert fields["content"]["enable"] is True
    assert len(block) == 2

    pair_one = block[0]
    assert "Install from PyPI" in pair_one["content_left"]
    assert "six 1.16.0" in pair_one["content_left"]
    assert "widget = Widget.create()" in pair_one["content_left"]
    assert "Make widgets" in pair_one["content_right"]
    assert "public surface has 12 types" in pair_one["content_right"]
    # The table's separator row must never survive into embedded content.
    assert "---" not in pair_one["content_left"]
    assert "---" not in pair_one["content_right"]

    pair_two = block[1]
    assert pair_two["title_left"] == "Scope and Limitations"
    assert "gears larger than 4cm" in pair_two["content_left"]
    assert "title_right" not in pair_two
    assert "content_right" not in pair_two

    assert fields["faq"] == {"enable": False, "list": []}


def test_sealed_candidate_doc_fields_raises_clear_error_when_current_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError) as excinfo:
        gen.sealed_candidate_doc_fields("widget-org/widget-foss", tmp_path)
    message = str(excinfo.value)
    assert "widget-org/widget-foss" in message
    assert "CURRENT" in message


def test_sealed_candidate_doc_fields_raises_clear_error_when_readme_missing(tmp_path: Path) -> None:
    candidate_dir = tmp_path / "candidates" / "widget-org__widget-foss"
    candidate_dir.mkdir(parents=True)
    (candidate_dir / "CURRENT").write_text("abc123deadbeef", encoding="utf-8")
    # No README.md written at candidate_dir/abc123deadbeef/.

    with pytest.raises(FileNotFoundError) as excinfo:
        gen.sealed_candidate_doc_fields("widget-org/widget-foss", tmp_path)
    message = str(excinfo.value)
    assert "widget-org/widget-foss" in message
    assert "abc123deadbeef" in message
    assert "README.md" in message


def test_sealed_candidate_doc_fields_raises_when_a_required_section_is_missing(tmp_path: Path) -> None:
    broken_readme = _SYNTHETIC_README.replace("## Scope and Limitations", "## Something Else Entirely")
    _write_synthetic_sealed_candidate(tmp_path, "widget-org/widget-foss", "abc123deadbeef", broken_readme)

    with pytest.raises(KeyError) as excinfo:
        gen.sealed_candidate_doc_fields("widget-org/widget-foss", tmp_path)
    message = str(excinfo.value)
    assert "widget-org/widget-foss" in message
    assert "Scope and Limitations" in message


def test_scope_and_limitations_block_classifies_as_troubleshooting(tmp_path: Path) -> None:
    """The card's own explicit requirement: check search_docs's real hints first so the
    Scope and Limitations block's own retained TEXT (never a heading that
    chunk_document would strip away) actually classifies as troubleshooting.
    """
    _write_synthetic_sealed_candidate(tmp_path, "widget-org/widget-foss", "abc123deadbeef", _SYNTHETIC_README)

    fields, _ = gen.sealed_candidate_doc_fields("widget-org/widget-foss", tmp_path)

    scope_body = fields["content"]["block"][1]["content_left"]
    assert not scope_body.lstrip().startswith("#")
    assert classify_content_type(scope_body) == "troubleshooting"


def test_sanitize_section_text_strips_table_separator_rows() -> None:
    text = "| Class | Description |\n| --- | --- |\n| `Widget` | A widget. |\n"

    cleaned = gen._sanitize_section_text(text)

    assert "---" not in cleaned
    assert "| Class | Description |" in cleaned
    assert "| `Widget` | A widget. |" in cleaned


def test_sanitize_section_text_refuses_a_residual_literal_triple_dash() -> None:
    with pytest.raises(ValueError, match="---"):
        gen._sanitize_section_text("A sentence with an em-dash-like run---right in the middle.")


def test_merge_sections_avoids_a_redundant_heading_when_the_body_already_has_one() -> None:
    sections = {
        "Installation": "Run pip install.",
        "Dependencies": "### Required Package Dependencies\n\n- six",
    }

    merged = gen._merge_sections(sections, ["Installation", "Dependencies"])

    assert merged.count("### Installation") == 1
    # Dependencies' own real subheading is preserved once - not duplicated with a
    # synthetic "### Dependencies" wrapper on top of it.
    assert merged.count("### Dependencies") == 0
    assert merged.count("### Required Package Dependencies") == 1


@pytest.mark.parametrize("pilot", sorted(_EXPECTED_SINGLE_BLOCK_SHA256))
def test_regenerated_fixture_keeps_single_block_byte_identical(pilot: str) -> None:
    fixture_path = FIXTURE_ROOT / pilot / "pages" / "_index.md"
    assert _single_block_sha256(fixture_path) == _EXPECTED_SINGLE_BLOCK_SHA256[pilot]


@pytest.mark.parametrize("pilot", sorted(_EXPECTED_SINGLE_BLOCK_SHA256))
def test_regenerated_fixture_front_matter_has_no_stray_triple_dash(pilot: str) -> None:
    """Only the 2 real ``---`` front-matter fences may exist in the whole file - a
    stray one anywhere inside embedded content would corrupt the accepted,
    in-production ``_load_furnished_page``'s naive ``text.split("---", 2)``.
    """
    fixture_path = FIXTURE_ROOT / pilot / "pages" / "_index.md"
    text = fixture_path.read_text(encoding="utf-8")
    assert text.count("---") == 2
    assert re.match(r"^---\n", text)


@pytest.mark.parametrize("pilot", sorted(_EXPECTED_SINGLE_BLOCK_SHA256))
def test_regenerated_fixture_loads_through_the_real_production_parser(pilot: str) -> None:
    """Exercises the REAL, already-accepted ``infra.build_chunks._load_furnished_page``
    and the real ``extract_doc_sections``/``extract_candidate_examples`` - never a
    re-implementation - against each real, regenerated fixture.
    """
    fixture_path = FIXTURE_ROOT / pilot / "pages" / "_index.md"
    page = build_chunks._load_furnished_page(fixture_path)

    assert page["faq"] == {"enable": False, "list": []}
    assert page["overview"]["enable"] is True

    doc_candidates = extract_doc_sections(page)
    origins = [c.origin for c in doc_candidates]
    assert origins.count("overview") == 1
    assert origins.count("content") == 3
    assert origins.count("faq") == 0

    scope_candidate = next(c for c in doc_candidates if c.title == "Scope and Limitations")
    assert classify_content_type(scope_candidate.body) == "troubleshooting"

    # single.block is functionally intact too, not only byte-identical: the same
    # number of compile-verified example CANDIDATES this pilot's own already-accepted
    # test_example_candidates.py test asserts still extract cleanly.
    examples = extract_candidate_examples(page)
    assert len(examples) == _EXPECTED_EXAMPLE_COUNT[pilot]


@pytest.mark.parametrize("pilot", sorted(_EXPECTED_SINGLE_BLOCK_SHA256))
def test_doc_source_commit_is_the_sealed_candidates_own_commit(pilot: str) -> None:
    """Proves the closeout requirement directly: each regenerated page's own
    ``doc_source_commit`` is repository-presenter's real, current sealed-candidate
    commit for that repository - read fresh from the real local checkout, never
    hardcoded - and is NOT conflated with this project's own ``--library-commit`` pin
    (asserted to differ for the 2 pilots confirmed to genuinely differ: pdf/net and
    slides/python).
    """
    if not _REPOSITORY_PRESENTER_ROOT.is_dir():
        pytest.skip(f"repository-presenter checkout not present at {_REPOSITORY_PRESENTER_ROOT}")

    family, platform = {
        "pdf_net": ("pdf", "net"),
        "pdf_java": ("pdf", "java"),
        "pdf_go": ("pdf", "go"),
        "slides_python": ("slides", "python"),
        "cells_rust": ("cells", "rust"),
    }[pilot]
    repository = gen._read_repository(family, platform, Path("."))
    current_path = _REPOSITORY_PRESENTER_ROOT / "candidates" / repository.replace("/", "__") / "CURRENT"
    real_current_commit = current_path.read_text(encoding="utf-8").strip()

    fixture_path = FIXTURE_ROOT / pilot / "pages" / "_index.md"
    page = build_chunks._load_furnished_page(fixture_path)

    assert page["doc_source_commit"] == real_current_commit

    if pilot in ("pdf_net", "slides_python"):
        assert page["doc_source_commit"] != _LIBRARY_COMMIT[pilot]


def test_pdf_typescript_fixture_is_left_completely_untouched() -> None:
    """G2/TC-119's own explicit scope boundary: repository-presenter has no sealed
    candidate for pdf/typescript yet, so its fixture must stay on its prior content -
    visibly, not silently stale.
    """
    fixture_path = FIXTURE_ROOT / "pdf_typescript" / "pages" / "_index.md"
    page = build_chunks._load_furnished_page(fixture_path)

    # The pre-existing pilot_manual_export.py content is still faq.enable: true with
    # real FAQ entries - the opposite of every regenerated pilot's faq.enable: false.
    assert page["faq"]["enable"] is True
    assert "doc_source_commit" not in page
