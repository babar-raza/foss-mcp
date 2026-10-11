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
_REPOSITORY_PRESENTER_ROOT_ON_THIS_HOST = Path(
    "E:/Users/prora/OneDrive/Documents/GitHub/repository-presenter"
)

# The sealed-candidate commit for each pilot, vendored as plain data (G2/TC-217). The
# test below always compares against these, so it never skips for want of a checkout.
SEALED_CURRENT_ROOT = FIXTURE_ROOT / "sealed_current"

_PILOT_REPOSITORY = {
    "pdf_net": ("pdf", "net"),
    "pdf_java": ("pdf", "java"),
    "pdf_go": ("pdf", "go"),
    "slides_python": ("slides", "python"),
    "cells_rust": ("cells", "rust"),
}


def _real_repository_presenter_root() -> Path | None:
    """The real checkout, if this host has one: FOSS_MCP_REPOSITORY_PRESENTER, then the
    constant above, then the E: checkout. Returns None when none is present."""
    import os

    candidates = [
        os.environ.get("FOSS_MCP_REPOSITORY_PRESENTER"),
        _REPOSITORY_PRESENTER_ROOT,
        _REPOSITORY_PRESENTER_ROOT_ON_THIS_HOST,
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_dir():
            return Path(candidate)
    return None


def _vendored_sealed_commit(pilot: str) -> str:
    return (SEALED_CURRENT_ROOT / f"{pilot}.CURRENT").read_text(encoding="utf-8").strip()


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
    ``doc_source_commit`` is the vendored sealed-candidate commit for that pilot
    (tests/fixtures/furnished/sealed_current/<pilot>.CURRENT). It always runs, with no
    skip and no xfail. Where a real repository-presenter checkout is present, the
    vendored value is also asserted equal to that checkout's own CURRENT file, so the
    drift check still happens on machines that have the checkout. The page's commit is
    NOT conflated with this project's ``--library-commit`` pin (asserted to differ for
    pdf/net and slides/python).
    """
    vendored_commit = _vendored_sealed_commit(pilot)

    fixture_path = FIXTURE_ROOT / pilot / "pages" / "_index.md"
    page = build_chunks._load_furnished_page(fixture_path)

    assert page["doc_source_commit"] == vendored_commit

    real_root = _real_repository_presenter_root()
    if real_root is not None:
        family, platform = _PILOT_REPOSITORY[pilot]
        repository = gen._read_repository(family, platform, Path("."))
        current_path = real_root / "candidates" / repository.replace("/", "__") / "CURRENT"
        real_current_commit = current_path.read_text(encoding="utf-8").strip()
        assert vendored_commit == real_current_commit

    if pilot in ("pdf_net", "slides_python"):
        assert page["doc_source_commit"] != _LIBRARY_COMMIT[pilot]


def test_every_pilot_has_a_vendored_40_hex_sealed_commit() -> None:
    """Every pilot in the sealed-commit test has a vendored CURRENT file holding exactly
    one 40-hex commit, so that test never depends on a local checkout to have a value."""
    assert set(_PILOT_REPOSITORY) == set(_EXPECTED_SINGLE_BLOCK_SHA256)
    for pilot in sorted(_PILOT_REPOSITORY):
        path = SEALED_CURRENT_ROOT / f"{pilot}.CURRENT"
        assert path.is_file(), f"missing vendored sealed commit: {path}"
        assert re.fullmatch(r"[0-9a-f]{40}", _vendored_sealed_commit(pilot)), path


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


# ---------------------------------------------------------------------------------------------
# G3/TC-351 (REQ-G3-005): the README-at-the-pinned-commit source.
#
# Everything below is offline: ``read_repo_document`` is patched with in-memory README text, so
# no test here can reach GitHub, and the chart under test is either a tiny values.yaml written
# to tmp_path or the real, committed one (read as text only).
# ---------------------------------------------------------------------------------------------

import importlib.util  # noqa: E402
import inspect  # noqa: E402
import sys  # noqa: E402

import yaml  # noqa: E402

from foss_mcp.extraction.repo_native_reader import DocumentNotPresent, DocumentPresent  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_CHART_VALUES = _REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"

_README_COMMIT = "0123456789abcdef0123456789abcdef01234567"
_README_REPOSITORY = "widget-org/Widget-FOSS-for-Go"
_README_PILOT = {
    "family": "widget",
    "platform": "go",
    "library": {"repository": _README_REPOSITORY, "commit": _README_COMMIT, "platform": "go"},
}
_README_PILOT_KEY = "widget_go"
_README_TITLE = "Widget FOSS for Go"
_README_FIRST_PARAGRAPH = (
    "Widget FOSS for Go is a pure-Go library for building widgets, with no paid dependencies."
)

_README_INTRO = (
    f"# {_README_TITLE}\n\n"
    "[![Build](https://example.invalid/build.svg)](https://example.invalid/build)\n\n"
    '<p align="center"><img src="logo.png"></p>\n\n'
    f"{_README_FIRST_PARAGRAPH}\n"
)

_README_SECTIONS = {
    "At a Glance": '```mermaid\nflowchart TD\n  PRODUCT["Widget FOSS"]\n```\n\nWidget FOSS builds widgets.',
    "Installation": "Install with the Go tool:\n\n```bash\ngo get example.invalid/widget\n```",
    "Dependencies": "### Required Package Dependencies\n\n- `gear v1.2.0`",
    "Quick Start": "Create a widget and spin it:\n\n```go\nw := widget.New()\nw.Spin()\n```",
    "Key Capabilities": "- **Make widgets.** `widget.New()` creates one.\n- **Spin widgets.** `Spin()` spins it.",
    "API Reference": "The public surface has 12 types.\n\n| Type | Description |\n| --- | --- |\n| `Widget` | The widget. |",
    "Additional Examples": (
        "### TypeScript\n\n```ts\nimport { Widget } from 'widget';\n```\n\n"
        "### C Sharp\n\n```cs\nvar w = new Widget();\n```\n\n"
        "### JavaScript\n\n```js\nconst w = new Widget();\n```\n\n"
        "### C Plus Plus\n\n```c++\nWidget w;\n```\n\n"
        "### Build files\n\n```shell\nmake\n```\n\n```xml\n<project/>\n```\n\n```toml\nname = 'w'\n```\n\n"
        "```cmake\nadd_library(w w.cpp)\n```\n\n```powershell\nGet-Widget\n```\n\n```groovy\nplugins { id 'w' }\n```"
    ),
    "Scope and Limitations": "Known issues: `Widget.Spin()` is not active on Windows.\n\nGears larger than 4cm are unsupported.",
    "Development and Testing": "\n".join(f"- development step {number}" for number in range(30)),
    "License": "MIT.",
}

# The example entries a complete README yields, in README order: (language, first line of code).
_README_EXPECTED_EXAMPLES = [
    ("go", "w := widget.New()"),
    ("typescript", "import { Widget } from 'widget';"),
    ("csharp", "var w = new Widget();"),
    ("javascript", "const w = new Widget();"),
    ("cpp", "Widget w;"),
]


def _readme(omit: tuple[str, ...] = (), intro: str = _README_INTRO) -> str:
    parts = [intro]
    for name, body in _README_SECTIONS.items():
        if name not in omit:
            parts.append(f"## {name}\n\n{body}\n")
    return "\n".join(parts)


def _readme_project(root: Path, pilots: tuple[dict, ...] = (_README_PILOT,)) -> Path:
    """A throwaway project root holding only the chart's ingestion.pilots."""
    values = root / "infra" / "helm" / "foss-mcp" / "values.yaml"
    values.parent.mkdir(parents=True, exist_ok=True)
    values.write_text(yaml.safe_dump({"ingestion": {"pilots": list(pilots)}}), encoding="utf-8")
    return root


def _patch_readmes(monkeypatch: pytest.MonkeyPatch, readmes: dict[str, str | None]) -> list[tuple]:
    """Serve *readmes* (repository -> README text, None for absent) in place of GitHub; returns the call log."""
    calls: list[tuple] = []

    def fake_read_repo_document(
        repository: str, path: str, *, ref: str | None = None, etag: str | None = None
    ):
        calls.append((repository, path, ref))
        text = readmes[repository]
        if text is None:
            return DocumentNotPresent(path=path)
        return DocumentPresent(path=path, sha="f" * 40, size=len(text), content=text)

    monkeypatch.setattr(gen, "read_repo_document", fake_read_repo_document)
    return calls


def _load_page(tmp_path: Path, page_text: str) -> dict:
    """Load *page_text* through the real, accepted furnished-page loader."""
    path = tmp_path / "loaded" / "pages" / "_index.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(page_text.encode("utf-8"))
    return build_chunks._load_furnished_page(path)


def _build(
    readme: str, monkeypatch: pytest.MonkeyPatch, pilot: dict = _README_PILOT
) -> tuple[str, gen.ReadmeFields]:
    _patch_readmes(monkeypatch, {_README_REPOSITORY: readme})
    fields = gen.readme_doc_fields(_README_REPOSITORY, _README_COMMIT)
    return gen.build_page_from_readme(pilot, fields), fields


def _poc_module():
    script = _REPO_ROOT / "scripts" / "poc" / "pilot_workflow_proof.py"
    spec = importlib.util.spec_from_file_location("pilot_workflow_proof_for_tc351", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run_github(root: Path, *extra: str) -> None:
    gen.main(["--source", "github", "--project-root", str(root), *extra])


def test_readme_doc_fields_reads_readme_md_at_the_pinned_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_readmes(monkeypatch, {_README_REPOSITORY: _readme()})

    fields = gen.readme_doc_fields(_README_REPOSITORY, _README_COMMIT)

    assert calls == [(_README_REPOSITORY, "README.md", _README_COMMIT)]
    assert fields.title == _README_TITLE
    assert fields.commit == _README_COMMIT
    assert fields.summary == _README_FIRST_PARAGRAPH
    assert fields.found == tuple(name for name in gen._REQUIRED_SECTIONS)
    assert fields.missing == ()
    assert [(block.language, block.code.splitlines()[0]) for block in fields.blocks if block.is_example] == (
        _README_EXPECTED_EXAMPLES
    )


def test_a_complete_readme_builds_a_complete_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page_text, fields = _build(_readme(), monkeypatch)
    page = _load_page(tmp_path, page_text)

    assert fields.missing == ()
    assert page["layout"] == "plugin"
    assert page["title"] == _README_TITLE
    assert page["github_url"] == f"https://github.com/{_README_REPOSITORY}"
    assert page["doc_source_commit"] == _README_COMMIT
    assert page["family_name"] == "Widget FOSS"
    assert page["plugin_platform"] == "Go"
    assert page["head_title"].startswith(_README_TITLE)
    assert page["description"] == _README_FIRST_PARAGRAPH
    assert page["head_description"] == _README_FIRST_PARAGRAPH
    assert page["faq"] == {"enable": False, "list": []}

    candidates = extract_doc_sections(page)
    assert [(c.origin, c.title) for c in candidates] == [
        ("overview", _README_TITLE),
        ("content", "Installation, Dependencies, and Quick Start"),
        ("content", "Key Capabilities and API Reference"),
        ("content", "Scope and Limitations"),
    ]
    by_title = {c.title: c.body for c in candidates}
    left = by_title["Installation, Dependencies, and Quick Start"]
    assert "Install with the Go tool" in left and "gear v1.2.0" in left and "w.Spin()" in left
    assert left.count("### Installation") == 1
    assert left.count("### Required Package Dependencies") == 1  # its own heading is not wrapped again
    right = by_title["Key Capabilities and API Reference"]
    assert "Spin widgets" in right and "public surface has 12 types" in right
    assert "```mermaid" in by_title[_README_TITLE]
    # The table's separator row is dropped (the same rule the sealed path applies); the rows stay.
    assert "| `Widget` | The widget. |" in right and "| --- |" not in right


def test_the_scope_and_limitations_block_classifies_as_troubleshooting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page_text, _ = _build(_readme(), monkeypatch)
    candidates = extract_doc_sections(_load_page(tmp_path, page_text))

    scope = next(c for c in candidates if c.title == "Scope and Limitations")

    assert not scope.body.lstrip().startswith("#")
    assert "Gears larger than 4cm" in scope.body
    assert classify_content_type(scope.body) == "troubleshooting"


def test_the_page_yields_one_example_entry_per_eligible_fence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page_text, _ = _build(_readme(), monkeypatch)
    page = _load_page(tmp_path, page_text)

    examples = extract_candidate_examples(page)

    assert [(e.language, e.code.splitlines()[0]) for e in examples] == _README_EXPECTED_EXAMPLES
    # Only the first fence of an entry is read as an example, so each entry holds exactly one.
    for entry in page["single"]["block"]:
        assert entry["content"].count("```") == 2
    # A description is the prose paragraph just before the fence.
    assert examples[0].description == "Create a widget and spin it:"
    assert examples[0].title == "Quick Start"


def test_fence_tags_are_normalised_and_build_tags_never_become_examples(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page_text, fields = _build(_readme(), monkeypatch)
    languages = [e.language for e in extract_candidate_examples(_load_page(tmp_path, page_text))]

    assert languages == ["go", "typescript", "csharp", "javascript", "cpp"]
    for raw_tag in ("```ts\n", "```cs\n", "```js\n", "```c++\n"):
        assert raw_tag not in page_text
    # shell, xml, toml, cmake, powershell, groovy (and bash, mermaid) are parsed, kept, never examples.
    skipped = {block.language for block in fields.blocks if not block.is_example}
    assert skipped == {"mermaid", "bash", "shell", "xml", "toml", "cmake", "powershell", "groovy"}
    assert "```bash" in page_text and "```mermaid" in page_text  # still in the prose
    for tag in ("shell", "xml", "toml", "cmake", "powershell", "groovy"):
        assert tag not in languages


def test_a_readme_without_dependencies_still_builds_a_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page_text, fields = _build(_readme(omit=("Dependencies",)), monkeypatch)
    candidates = extract_doc_sections(_load_page(tmp_path, page_text))

    assert fields.missing == ("Dependencies",)
    assert [c.title for c in candidates if c.origin == "content"][0] == "Installation and Quick Start"
    assert "gear v1.2.0" not in page_text and "Required Package Dependencies" not in page_text
    assert len(candidates) == 4


def test_a_readme_missing_several_sections_gives_a_partial_page_and_reports_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    omitted = ("At a Glance", "Key Capabilities", "API Reference", "Scope and Limitations")
    root = _readme_project(tmp_path / "project")
    _patch_readmes(monkeypatch, {_README_REPOSITORY: _readme(omit=omitted)})

    _run_github(root)

    output = capsys.readouterr().out
    page = build_chunks._load_furnished_page(gen._fixture_path(root, _README_PILOT_KEY))
    assert page["overview"] == {"enable": False}
    assert [(c.origin, c.title) for c in extract_doc_sections(page)] == [
        ("content", "Installation, Dependencies, and Quick Start")
    ]
    assert "title_right" not in page["content"]["block"][0]
    for name in omitted:
        assert name in output.split("missing:")[1]
    assert "sections found: Installation, Dependencies, Quick Start;" in output
    # Nothing was invented to stand in for the absent sections.
    assert "Spin widgets" not in str(page) and "Gears larger than 4cm" not in str(page)


def test_strict_refuses_a_readme_missing_any_section_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _readme_project(tmp_path / "project")
    _patch_readmes(monkeypatch, {_README_REPOSITORY: _readme(omit=("Dependencies",))})

    with pytest.raises(SystemExit) as excinfo:
        _run_github(root, "--strict")

    assert excinfo.value.code == 3
    assert "Dependencies" in capsys.readouterr().err
    assert not gen._fixture_path(root, _README_PILOT_KEY).exists()

    # The same README without --strict is accepted: Dependencies is optional.
    _run_github(root)
    assert gen._fixture_path(root, _README_PILOT_KEY).is_file()


def _readme_without_h1() -> str:
    return _readme(intro=_README_INTRO.replace(f"# {_README_TITLE}", _README_TITLE))


def _readme_without_tagged_blocks() -> str:
    return re.sub(r"```[^\n`]+\n", "```\n", _readme())


def _readme_too_short() -> str:
    return f"# {_README_TITLE}\n\n{_README_FIRST_PARAGRAPH}\n\n```go\nw := widget.New()\n```\n"


@pytest.mark.parametrize(
    ("readme", "reason"),
    [
        pytest.param(_readme_without_h1, "no H1", id="no-h1"),
        pytest.param(_readme_without_tagged_blocks, "no language-tagged code block", id="no-tagged-block"),
        pytest.param(_readme_too_short, "non-blank lines", id="under-40-lines"),
        pytest.param(lambda: None, "no README.md", id="absent"),
    ],
)
def test_a_thin_readme_is_refused_with_exit_3_and_nothing_is_written(
    readme,
    reason: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _readme_project(tmp_path / "project")
    _patch_readmes(monkeypatch, {_README_REPOSITORY: readme()})

    with pytest.raises(SystemExit) as excinfo:
        _run_github(root, "--pilot", _README_PILOT_KEY)

    assert excinfo.value.code == 3
    error = capsys.readouterr().err
    assert "REFUSED" in error and _README_PILOT_KEY in error and reason in error
    assert not (root / "tests").exists(), "a refused README must not even create the fixture directory"


def test_a_thin_readme_is_refused_in_the_library_and_names_every_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_readmes(monkeypatch, {_README_REPOSITORY: "Just a sentence.\n"})

    with pytest.raises(gen.ReadmeTooThinError) as excinfo:
        gen.readme_doc_fields(_README_REPOSITORY, _README_COMMIT)

    message = str(excinfo.value)
    assert _README_REPOSITORY in message and _README_COMMIT in message
    assert "non-blank lines" in message and "no H1" in message and "language-tagged" in message


def test_one_thin_pilot_is_refused_while_the_others_are_still_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    thin_pilot = {
        "family": "thin",
        "platform": "net",
        "library": {"repository": "widget-org/Thin-FOSS-for-.NET", "commit": "b" * 40},
    }
    root = _readme_project(tmp_path / "project", (_README_PILOT, thin_pilot))
    _patch_readmes(
        monkeypatch, {_README_REPOSITORY: _readme(), "widget-org/Thin-FOSS-for-.NET": "# Thin\n\nTiny.\n"}
    )

    with pytest.raises(SystemExit) as excinfo:
        _run_github(root)  # no --pilot: every chart pilot

    assert excinfo.value.code == 3
    assert gen._fixture_path(root, _README_PILOT_KEY).is_file()
    assert not gen._fixture_path(root, "thin_net").exists()
    captured = capsys.readouterr()
    assert "thin_net: REFUSED" in captured.err and f"{_README_PILOT_KEY}: wrote" in captured.out


def test_literal_triple_dash_lines_never_let_the_page_parser_split_the_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tricky = dict(_README_SECTIONS)
    tricky["Installation"] = (
        "Install with the Go tool:\n\n---\n\nA run of dashes---inside a sentence, and a rule of ------.\n\n"
        "```yaml\n---\nname: widget\n---\n```\n\n```bash\ngo get example.invalid/widget\n```"
    )
    tricky["API Reference"] = "| Type | Description |\n|---|---|\n| `Widget` | The widget. |"
    readme = _README_INTRO + "\n" + "\n".join(f"## {name}\n\n{body}\n" for name, body in tricky.items())

    page_text, _ = _build(readme, monkeypatch)
    page = _load_page(tmp_path, page_text)

    # The accepted loader splits on the first two "---" substrings: only the two fences may exist.
    assert page_text.count("---") == 2
    assert page_text.startswith("---\n") and page_text.endswith("\n---\n")
    candidates = {c.title: c.body for c in extract_doc_sections(page)}
    left = candidates["Installation, Dependencies, and Quick Start"]
    # A code block's own "---" lines are real content and survive byte for byte ...
    assert "```yaml\n---\nname: widget\n---\n```" in left
    # ... and so does a dash run inside a sentence, while a bare rule line and a table rule row are dropped.
    assert "dashes---inside a sentence, and a rule of ------." in left
    assert "\n---\n\n" not in left.replace("```yaml\n---\n", "").replace("\n---\n```", "")
    assert "|---|" not in candidates["Key Capabilities and API Reference"]
    assert "| `Widget` | The widget. |" in candidates["Key Capabilities and API Reference"]
    yaml_example = next(e for e in extract_candidate_examples(page) if e.language == "yaml")
    assert yaml_example.code == "---\nname: widget\n---"


def test_headings_inside_fenced_code_are_code_not_sections() -> None:
    readme = _readme().replace(
        "```bash\ngo get example.invalid/widget\n```",
        "```bash\n# Install the module\n## Quick Start\n# end\ngo get example.invalid/widget\n```",
    )

    fields = gen.parse_readme(_README_REPOSITORY, _README_COMMIT, readme)

    assert fields.title == _README_TITLE
    assert "## Quick Start\n# end" in fields.sections["Installation"]
    assert fields.sections["Quick Start"].startswith("Create a widget and spin it:")


def test_a_setext_h1_and_decorated_headings_are_understood() -> None:
    readme = _readme(intro=f"{_README_TITLE}\n=========\n\n{_README_FIRST_PARAGRAPH}\n").replace(
        "## Installation", "## Installation ##"
    )
    readme = readme.replace("## Quick Start", "## Quickstart")

    fields = gen.parse_readme(_README_REPOSITORY, _README_COMMIT, readme)

    assert fields.title == _README_TITLE
    assert fields.summary == _README_FIRST_PARAGRAPH
    assert "Installation" in fields.found and "Quick Start" in fields.found


def test_the_same_readme_always_gives_a_byte_identical_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, _ = _build(_readme(), monkeypatch)
    second, _ = _build(_readme(), monkeypatch)
    assert first == second

    first_root = _readme_project(tmp_path / "first")
    second_root = _readme_project(tmp_path / "second")
    _patch_readmes(monkeypatch, {_README_REPOSITORY: _readme()})
    _run_github(first_root)
    _run_github(second_root)
    first_bytes = gen._fixture_path(first_root, _README_PILOT_KEY).read_bytes()
    assert first_bytes == gen._fixture_path(second_root, _README_PILOT_KEY).read_bytes()
    assert first_bytes == first.encode("utf-8")
    assert b"\r" not in first_bytes, "pages are written with LF line endings on every platform"
    # Re-running over an existing page changes nothing.
    _run_github(first_root)
    assert gen._fixture_path(first_root, _README_PILOT_KEY).read_bytes() == first_bytes


def test_tabs_unicode_and_odd_whitespace_round_trip_exactly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    readme = _readme().replace(
        "w := widget.New()\nw.Spin()",
        "func main() {\n\tw := widget.New()  \n\tw.Spin() // spins — \U0001f680 é\n}\n\n\n",
    )

    page_text, _ = _build(readme, monkeypatch)
    page = _load_page(tmp_path, page_text)

    example = extract_candidate_examples(page)[0]
    assert example.language == "go"
    assert example.code == "func main() {\n\tw := widget.New()  \n\tw.Spin() // spins — \U0001f680 é\n}"


def test_furnished_title_finds_the_generated_title(monkeypatch: pytest.MonkeyPatch) -> None:
    poc = _poc_module()
    page_text, _ = _build(_readme(), monkeypatch)

    assert poc.furnished_title(page_text) == _README_TITLE

    awkward = f"{_README_TITLE}: the guide, 100% #1"
    page_text, _ = _build(_readme(intro=_README_INTRO.replace(_README_TITLE, awkward, 1)), monkeypatch)
    assert poc.furnished_title(page_text) == awkward


def test_build_page_refuses_a_page_that_would_claim_the_wrong_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_readmes(monkeypatch, {_README_REPOSITORY: _readme()})
    fields = gen.readme_doc_fields(_README_REPOSITORY, "e" * 40)
    with pytest.raises(ValueError, match="pins"):
        gen.build_page_from_readme(_README_PILOT, fields)

    for broken in ({"family": "widget", "platform": "go"}, {"family": "widget", "library": {}}):
        with pytest.raises(ValueError):
            gen.build_page_from_readme(broken, fields)


def test_a_page_is_generated_for_a_real_chart_pilot_pinned_to_its_chart_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chart = gen._load_chart_pilots(_REPO_ROOT)
    assert len(chart) == 40
    for key, pilot in chart.items():
        family, platform, repository, commit = gen._pilot_identity(pilot)
        assert key == f"{family}_{platform}"
        assert repository and re.fullmatch(r"[0-9a-f]{40}", commit), key

    pilot = chart["pdf_go"]
    repository = pilot["library"]["repository"]
    _patch_readmes(monkeypatch, {repository: _readme()})
    fields = gen.readme_doc_fields(repository, pilot["library"]["commit"])
    page = _load_page(tmp_path, gen.build_page_from_readme(pilot, fields))

    assert page["doc_source_commit"] == pilot["library"]["commit"]
    assert page["github_url"] == f"https://github.com/{repository}"
    assert page["plugin_platform"] == "Go"


def test_dry_run_reports_but_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _readme_project(tmp_path / "project")
    _patch_readmes(monkeypatch, {_README_REPOSITORY: _readme(omit=("Dependencies",))})

    _run_github(root, "--pilot", _README_PILOT_KEY, "--dry-run")

    output = capsys.readouterr().out
    assert (
        f"{_README_PILOT_KEY}: would write tests/fixtures/furnished/{_README_PILOT_KEY}/pages/_index.md"
        in output
    )
    assert "missing: Dependencies;" in output and "examples: 5;" in output and re.search(r"\d+ bytes", output)
    assert not (root / "tests").exists()


def test_the_report_names_found_and_missing_sections_examples_and_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _readme_project(tmp_path / "project")
    _patch_readmes(monkeypatch, {_README_REPOSITORY: _readme()})

    _run_github(root, "--pilot", _README_PILOT_KEY)

    output = capsys.readouterr().out
    written = gen._fixture_path(root, _README_PILOT_KEY)
    assert output.strip() == (
        f"{_README_PILOT_KEY}: wrote tests/fixtures/furnished/{_README_PILOT_KEY}/pages/_index.md - "
        f"sections found: {', '.join(gen._REQUIRED_SECTIONS)}; missing: none; examples: 5; "
        f"{written.stat().st_size} bytes"
    )
    _run_github(root, "--pilot", _README_PILOT_KEY)
    assert ": replaced " in capsys.readouterr().out


def test_pilot_is_repeatable_and_validated_against_the_charts_pilots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    other = {
        "family": "gadget",
        "platform": "nodejs",
        "library": {"repository": "widget-org/Gadget-FOSS-for-Node.js", "commit": "c" * 40},
    }
    third = {
        "family": "gizmo",
        "platform": "cpp",
        "library": {"repository": "widget-org/Gizmo-FOSS-for-Cpp", "commit": "d" * 40},
    }
    root = _readme_project(tmp_path / "project", (_README_PILOT, other, third))
    calls = _patch_readmes(
        monkeypatch,
        {
            _README_REPOSITORY: _readme(),
            "widget-org/Gadget-FOSS-for-Node.js": _readme(),
            "widget-org/Gizmo-FOSS-for-Cpp": _readme(),
        },
    )

    _run_github(root, "--pilot", "gizmo_cpp", "--pilot", _README_PILOT_KEY, "--pilot", "gizmo_cpp")

    assert [call[0] for call in calls] == ["widget-org/Gizmo-FOSS-for-Cpp", _README_REPOSITORY]
    assert (
        gen._fixture_path(root, "gizmo_cpp").is_file()
        and not gen._fixture_path(root, "gadget_nodejs").exists()
    )
    assert "plugin_platform: C++" in gen._fixture_path(root, "gizmo_cpp").read_text(encoding="utf-8")
    capsys.readouterr()


def test_an_unknown_pilot_exits_2_before_any_network_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _readme_project(tmp_path / "project")
    calls = _patch_readmes(monkeypatch, {_README_REPOSITORY: _readme()})

    with pytest.raises(SystemExit) as excinfo:
        _run_github(root, "--pilot", _README_PILOT_KEY, "--pilot", "nope_nope")

    assert excinfo.value.code == 2
    assert "nope_nope" in capsys.readouterr().err
    assert calls == []
    assert not (root / "tests").exists()

    with pytest.raises(SystemExit) as excinfo:
        gen.main(
            [
                "--repository-presenter-root",
                str(tmp_path),
                "--project-root",
                str(root),
                "--pilot",
                "nope_nope",
            ]
        )
    assert excinfo.value.code == 2


def test_the_default_sealed_source_behaves_as_before_and_never_touches_the_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("--source sealed must never read from GitHub")

    monkeypatch.setattr(gen, "read_repo_document", forbidden)
    project = tmp_path / "project"
    presenter = tmp_path / "presenter"
    fixture = project / "tests" / "fixtures" / "furnished" / "pdf_net" / "pages" / "_index.md"
    fixture.parent.mkdir(parents=True)
    fixture.write_bytes((FIXTURE_ROOT / "pdf_net" / "pages" / "_index.md").read_bytes())
    manifest = project / "config" / "products" / "pdf" / "net.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("repository: widget-org/widget-foss\n", encoding="utf-8")
    _write_synthetic_sealed_candidate(
        presenter, "widget-org/widget-foss", "abc123deadbeef", _SYNTHETIC_README
    )

    gen.main(
        ["--repository-presenter-root", str(presenter), "--project-root", str(project), "--pilot", "pdf_net"]
    )

    expected_path = Path("tests/fixtures/furnished/pdf_net/pages/_index.md")
    assert capsys.readouterr().out.strip() == f"regenerated pdf_net from sealed candidate ({expected_path})"
    page = build_chunks._load_furnished_page(fixture)
    assert page["doc_source_commit"] == "abc123deadbeef"
    assert _single_block_sha256(fixture) == _EXPECTED_SINGLE_BLOCK_SHA256["pdf_net"]
    assert page["overview"]["title"] == "Widget FOSS for Python"

    # Without the root the sealed source cannot run, exactly as it was required before.
    with pytest.raises(SystemExit) as excinfo:
        gen.main(["--project-root", str(project)])
    assert excinfo.value.code == 2
    # --dry-run and --strict belong to the github source only.
    with pytest.raises(SystemExit) as excinfo:
        gen.main(["--repository-presenter-root", str(presenter), "--project-root", str(project), "--dry-run"])
    assert excinfo.value.code == 2


def test_no_token_or_environment_value_is_ever_printed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    token = "ghp_TC351_NOT_A_REAL_TOKEN_0123456789"
    other = "tc351-some-other-environment-value"
    monkeypatch.setenv("GITHUB_TOKEN", token)
    monkeypatch.setenv("TC351_OTHER_VALUE", other)
    thin = {
        "family": "thin",
        "platform": "net",
        "library": {"repository": "widget-org/Thin-FOSS-for-.NET", "commit": "b" * 40},
    }
    broken = {
        "family": "down",
        "platform": "java",
        "library": {"repository": "widget-org/Down-FOSS-for-Java", "commit": "c" * 40},
    }
    root = _readme_project(tmp_path / "project", (_README_PILOT, thin, broken))
    texts = {_README_REPOSITORY: _readme(), "widget-org/Thin-FOSS-for-.NET": "# T\n"}

    def fake_read_repo_document(
        repository: str, path: str, *, ref: str | None = None, etag: str | None = None
    ):
        if repository == "widget-org/Down-FOSS-for-Java":
            raise OSError(f"connection failed with Authorization: Bearer {token} and {other}")
        return DocumentPresent(path=path, sha="f" * 40, size=1, content=texts[repository])

    monkeypatch.setattr(gen, "read_repo_document", fake_read_repo_document)

    for arguments in ([], ["--dry-run"], ["--strict"], ["--pilot", "nope_nope"], ["--help"]):
        with pytest.raises(SystemExit):
            _run_github(root, *arguments)

    captured = capsys.readouterr()
    everything = captured.out + captured.err
    assert token not in everything and other not in everything and "Bearer" not in everything
    assert "down_java: FAILED" in everything and "OSError" in everything  # the failure is still reported
    # The generator itself never reads the environment at all.
    source = inspect.getsource(gen)
    assert "environ" not in source and "getenv" not in source


def test_a_failed_fetch_exits_1_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import urllib.error

    root = _readme_project(tmp_path / "project")

    def rate_limited(*args: object, **kwargs: object) -> None:
        raise urllib.error.HTTPError("https://api.invalid/x", 403, "rate limit exceeded", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(gen, "read_repo_document", rate_limited)

    with pytest.raises(SystemExit) as excinfo:
        _run_github(root)

    assert excinfo.value.code == 1
    assert "HTTPError (HTTP 403)" in capsys.readouterr().err
    assert not (root / "tests").exists()


def test_the_generator_reads_nothing_until_the_github_source_is_asked_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Importing the module and parsing text never calls the reader; only readme_doc_fields does."""

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("the reader must not be called")

    monkeypatch.setattr(gen, "read_repo_document", forbidden)

    fields = gen.parse_readme(_README_REPOSITORY, _README_COMMIT, _readme())
    gen.build_page_from_readme(_README_PILOT, fields)

    with pytest.raises(AssertionError, match="must not be called"):
        gen.readme_doc_fields(_README_REPOSITORY, _README_COMMIT)
