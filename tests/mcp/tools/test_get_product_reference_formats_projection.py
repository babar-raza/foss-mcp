"""G3/TC-798 (REQ-G3-013): the ``formats`` projection serves whole statements, never fragments or examples.

Two defects, both measured over the 32 real baked pilots before this card:

1. ``_format_statements`` tested ONE hard-wrapped physical README line at a time, so the served answer was
   often a mid-sentence fragment ("...and write STL (ASCII and binary),": 3d/typescript 8 of 8 lines).
   It now joins a wrapped bullet/sentence first, cuts an over-long one at a clause boundary (marked with an
   ellipsis), drops the Enterprise-edition tail, and falls back to a README format table / enumeration row
   when it finds fewer than three statements.
2. The ``formats`` heading rule matched EXAMPLE headings ("Convert Every Input Format to PDF", "Handle an
   Unrecognized 3D File Format"), so words/python and 3d/net served a code recipe as their formats answer.

The first half of this module is synthetic (offline, deterministic); the second half sweeps the real,
committed ``infra/demo-manifests`` pilots exactly as ``tests/conformance/test_reference_content.py`` does.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import foss_mcp.mcp.tools.get_product_reference as gpr
import infra.fetch_product_reference as fetch_product_reference
from foss_mcp.indexing.gateway_embedding_provider import _API_KEY_ENV, _ENDPOINT_ENV
from foss_mcp.mcp.tools.get_product_reference import (
    NotAvailable,
    ProductReferenceInputs,
    ReferenceContent,
    get_product_reference,
)
from foss_mcp.mcp.tools.readme_sections import ReadmeSection, ReadmeSections, sections_from_markdown
from tests.conformance.corpus import DEMO_MANIFESTS, ConformanceCorpus, load_demo_manifests_corpus

COMMIT = "0123456789abcdef0123456789abcdef01234567"
HEADING = "File-format statements quoted from the README (selected by keyword, not an exhaustive list):"
ELLIPSIS = "…"
CAP = 300


def _section(title: str, text: str) -> ReadmeSection:
    return ReadmeSection(title=title, text=text, commit=COMMIT, ordered=True, parts=1)


def _lines(capabilities: str = "", limitations: str = "") -> list[str]:
    """The served statement lines (without the heading) for synthetic section texts."""
    readme = ReadmeSections(
        capabilities=_section("Key Capabilities", capabilities) if capabilities else None,
        limitations=_section("Scope and Limitations", limitations) if limitations else None,
    )
    result = get_product_reference(ProductReferenceInputs(), "formats", readme=readme)
    assert isinstance(result, ReferenceContent), result
    lines = result.text.split("\n")
    assert lines[0] == HEADING
    return lines[1:]


# ---- joining hard-wrapped bullets and sentences ----------------------------------------------------


def test_a_hard_wrapped_bullet_is_one_whole_statement() -> None:
    text = (
        "- Read glTF 2.0 and Wavefront OBJ scenes, and write STL (ASCII and binary),\n"
        "  FBX (ASCII) and COLLADA through scene.open() and scene.save().\n"
        "- Fast and small."
    )

    assert _lines(text) == [
        "- Read glTF 2.0 and Wavefront OBJ scenes, and write STL (ASCII and binary), "
        "FBX (ASCII) and COLLADA through scene.open() and scene.save()."
    ]


def test_a_wrapped_sentence_that_continues_without_indentation_is_joined() -> None:
    text = "The library reads DOCX and writes PDF, and it also\nexports Markdown for diffing."

    assert _lines(text) == [
        "- The library reads DOCX and writes PDF, and it also exports Markdown for diffing."
    ]


def test_the_keyword_test_runs_on_the_whole_bullet_not_on_each_physical_line() -> None:
    # Neither physical line alone names a format AND a verb; the joined bullet does.
    text = "- Saves the workbook through a streaming\n  writer to XLSX."

    assert _lines(text) == ["- Saves the workbook through a streaming writer to XLSX."]


def test_a_line_that_ends_a_sentence_starts_a_new_item() -> None:
    text = "Exports documents to STL format.\nLoads DOCX documents."

    assert _lines(text) == ["- Exports documents to STL format.", "- Loads DOCX documents."]


def test_each_sentence_of_a_bullet_is_judged_on_its_own() -> None:
    text = "- It is fast. It saves PDF files. Docs are online. It loads DOCX files."

    assert _lines(text) == ["- It saves PDF files", "- It loads DOCX files."]


def test_an_abbreviation_does_not_end_a_sentence() -> None:
    text = "- Writes several formats, e.g. PDF and SVG. It is fast."

    assert _lines(text) == ["- Writes several formats, e.g. PDF and SVG"]


def test_code_fences_tables_and_headings_are_not_joined_into_statements() -> None:
    text = "\n".join(
        [
            "- Writes PDF",
            "| `PdfSaveOptions` | Saves PDF |",
            "```python",
            "doc.save('a.pdf')  # saves PDF",
            "```",
            "#### Exports SVG",
            "- Loads CSV.",
        ]
    )

    assert _lines(text) == ["- Writes PDF", "- Loads CSV."]


def test_an_api_signature_line_is_not_a_format_statement() -> None:
    text = "\n".join(
        [
            "- `LdmPdfWriter.write(doc, output_path) -> None`",
            "- `LdmDocxWriter.write(doc, output_path) -> None` / `write_to_bytes(doc) -> bytes`",
            "- Reads DOCX and writes PDF.",
        ]
    )

    assert _lines(text) == ["- Reads DOCX and writes PDF."]


# ---- the cap lands on a clause boundary, never mid-word --------------------------------------------


def _long_statement() -> str:
    return (
        "Read glTF 2.0 (JSON and binary GLB) and Wavefront OBJ scenes, and write STL (ASCII and binary), FBX "
        "(ASCII), COLLADA (`.dae`, via xmldom), and glTF 2.0 JSON scenes through scene.open() and "
        "scene.save() — OBJ export and binary GLB export both carry real, current defects; see Scope "
        "and Limitations before relying on either of them in production."
    )


def test_an_over_long_statement_is_cut_at_a_clause_boundary_and_marked() -> None:
    statement = _long_statement()
    assert len(statement) > CAP

    (line,) = _lines("- " + statement)

    body = line[2:]
    assert len(body) <= CAP and body.endswith(ELLIPSIS)
    # the cut is the longest prefix ending at a strong clause boundary (here the semicolon), not inside a
    # word or a parenthesis, and the "see Scope and Limitations" tail is what the marker stands for
    assert body == statement[: statement.index("; see")] + ELLIPSIS
    assert statement.startswith(body[:-1])
    assert body[:-1].count("(") == body[:-1].count(")")


def test_a_cut_never_lands_inside_parentheses_or_a_code_span() -> None:
    statement = (
        "Loads DOCX through the reader (the `Document.load(path, options, encoding, strict, "
        "lenient, recover, repair, validate, lazy, eager)` form, which also exposes every one of the "
        "optional keyword arguments listed below) and saves PDF; later clauses follow here, and here, "
        "and then a few more words so that the whole statement is well past the three hundred mark."
    )
    assert len(statement) > CAP

    (line,) = _lines("- " + statement)

    body = line[2:-1]
    assert body.count("(") == body.count(")") and body.count("`") % 2 == 0


def test_a_statement_with_no_boundary_at_all_is_still_cut_at_the_cap() -> None:
    (line,) = _lines("- Supports PDF " + "z" * 600)

    assert len(line[2:]) == CAP


def test_a_statement_that_fits_the_cap_is_served_unchanged() -> None:
    statement = "Reads DOCX. " + "x" * 10
    exact = "Writes PDF " + "y" * (CAP - len("Writes PDF "))
    assert len(exact) == CAP

    assert _lines("- " + exact) == ["- " + exact]
    assert _lines("- " + statement) == ["- " + statement]


def test_when_only_a_later_clause_names_a_format_that_clause_is_served() -> None:
    statement = (
        "**Not in the public API**: charts, SmartArt, OLE objects, and mathematical text (no add_chart "
        "or equivalent for the rest); animations and slide transitions (no timeline, no transition "
        "type); group shapes (no add_group_shape, no way for one shape to hold child shapes); video and "
        "audio (not modelled); and export to non-PPTX formats (PDF, HTML, SVG)."
    )
    assert len(statement) > CAP

    (line,) = _lines("- " + statement)

    assert line == "- " + ELLIPSIS + "and export to non-PPTX formats (PDF, HTML, SVG)."


# ---- the Enterprise-edition tail is not a format claim ---------------------------------------------


def test_the_enterprise_edition_tail_is_dropped() -> None:
    limitations = (
        "- Rendering is not implemented, so nothing exports to PDF.\n"
        "\n"
        "These limitations don't apply to\n"
        "[Aspose.3D — Enterprise Edition](https://products.aspose.com/3d/), which adds rendering,\n"
        "and can export PDF and FBX files.\n"
    )

    assert _lines(limitations=limitations) == ["- Rendering is not implemented, so nothing exports to PDF."]


def test_a_commercial_edition_sentence_is_dropped() -> None:
    text = "- Reads DOCX.\n- The commercial edition also writes PDF."

    assert _lines(text) == ["- Reads DOCX."]


# ---- fewer than three statements: the README's own format table / enumeration row -----------------


def test_a_format_enumeration_row_backs_up_a_thin_answer() -> None:
    text = (
        "- Writes pages through `PdfDevice`, or `SvgDevice` for SVG output.\n"
        "\n"
        "| Enumeration | Description |\n"
        "| `OutputFormat` | Supported output formats: DVI, PDF, SVG. |\n"
        "| `TeXOptions` | Configuration for a job. |\n"
    )

    assert _lines(text) == [
        "- Writes pages through `PdfDevice`, or `SvgDevice` for SVG output.",
        "- `OutputFormat` - Supported output formats: DVI, PDF, SVG.",
    ]


def test_a_table_headed_format_backs_up_a_thin_answer() -> None:
    text = "- Loads DOCX.\n\n| Format | Load | Save |\n|---|---|---|\n| DOCX | yes | yes |\n| Markdown | yes | no |\n"

    assert _lines(text) == ["- Loads DOCX.", "- DOCX - yes - yes", "- Markdown - yes - no"]


def test_the_table_fallback_is_not_used_when_three_statements_were_found() -> None:
    text = "- Loads DOCX.\n- Saves PDF.\n- Exports SVG.\n\n| Format | Load |\n| EPUB | yes |\n"

    assert _lines(text) == ["- Loads DOCX.", "- Saves PDF.", "- Exports SVG."]


def test_an_api_table_row_that_is_not_a_format_table_is_not_a_fallback() -> None:
    text = "- Saves JSON.\n\n| `GltfImporter` | Reads glTF JSON (`.json`) files |\n"

    assert _lines(text) == ["- Saves JSON."]


def test_no_statement_and_no_table_is_still_the_honest_not_available() -> None:
    readme = ReadmeSections(capabilities=_section("Key Capabilities", "- Fast.\n- Small."))

    assert get_product_reference(ProductReferenceInputs(), "formats", readme=readme) == NotAvailable(
        "formats", gpr._FORMATS_NONE_REASON
    )


# ---- the formats heading rule ----------------------------------------------------------------------


def _formats_of(markdown: str) -> ReadmeSection | None:
    return sections_from_markdown(markdown).formats


@pytest.mark.parametrize(
    "heading",
    [
        "Convert Every Input Format to PDF",
        "Convert Image-Containing Documents to Every Output Format",
        "Handle an Unrecognized 3D File Format",
        "Detect the Input Format of a Stream",
        "Save a Workbook in Any Output Format",
        "Load Each Supported Format Through One Call",
    ],
)
def test_an_imperative_example_heading_is_not_the_formats_section(heading: str) -> None:
    markdown = f"## {heading}\n\nSome prose about it.\n\n```python\ndoc.save('a.pdf')\n```\n"

    assert _formats_of(markdown) is None


@pytest.mark.parametrize(
    "heading",
    [
        "Supported formats",
        "Supported File Formats",
        "Save formats",
        "Load formats",
        "Output formats",
        "Formats",
    ],
)
def test_a_noun_phrase_formats_heading_is_the_formats_section(heading: str) -> None:
    section = _formats_of(f"## {heading}\n\n| Format | Load |\n|---|---|\n| DOCX | yes |\n")

    assert section is not None and section.title == heading and "| DOCX | yes |" in section.text


def test_a_formats_heading_whose_body_is_only_code_is_rejected() -> None:
    markdown = "## Supported formats\n\n```python\nfor path in paths:\n    doc.save(path)\n```\n"

    assert _formats_of(markdown) is None


def test_a_formats_heading_with_prose_next_to_code_is_kept() -> None:
    markdown = "## Supported formats\n\nDOCX and PDF.\n\n```python\ndoc.save('a.pdf')\n```\n"

    section = _formats_of(markdown)

    assert section is not None and "DOCX and PDF." in section.text


def test_the_stricter_rule_applies_to_the_formats_section_only() -> None:
    markdown = "## Install\n\nRun the installer.\n\n## Capabilities\n\n```text\nonly code\n```\n"

    sections = sections_from_markdown(markdown)

    assert sections.install is not None and sections.capabilities is not None


def test_an_example_heading_never_reaches_the_served_formats_answer() -> None:
    markdown = (
        "## Convert Every Input Format to PDF\n\n```python\nDocument('a.docx').save('a.pdf')\n```\n\n"
        "## Key Capabilities\n\n- Reads DOCX and writes PDF.\n- Saves Markdown.\n- Exports HTML.\n"
    )
    sections = sections_from_markdown(markdown)

    assert sections.formats is None
    result = get_product_reference(ProductReferenceInputs(), "formats", readme=sections)
    assert isinstance(result, ReferenceContent) and "```" not in result.text


# ---- the real 32-pilot corpus ----------------------------------------------------------------------

EXPECTED_PILOT_COUNT = 32
# Pilots whose README genuinely holds no format statement and so degrade to the honest NotAvailable.
# Measured against the committed corpus: none - all 32 READMEs state at least one.
GENUINELY_WITHOUT_A_FORMAT_STATEMENT: frozenset[str] = frozenset()
# The answers that are a whole README "formats" section (a table), not keyword-selected lines.
SECTION_ANSWERS = frozenset({"imaging__net", "slides__net", "words__net"})
# Pilots the triage found already clean - they must not get worse.
CLEAN_BEFORE = ("cells__net", "cells__rust", "email__net", "pdf__go", "pdf__java")


@pytest.fixture(autouse=True)
def _no_embedding_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_ENDPOINT_ENV, raising=False)
    monkeypatch.delenv(_API_KEY_ENV, raising=False)


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> ConformanceCorpus:
    return load_demo_manifests_corpus(tmp_path_factory.mktemp("tc798-formats-corpus"))


class _Answer:
    def __init__(self, pilot: str, result: ReferenceContent | NotAvailable, sources: str) -> None:
        self.pilot = pilot
        self.result = result
        self.sources = sources  # the capabilities + limitations text the statements were selected from

    @property
    def lines(self) -> list[str]:
        assert isinstance(self.result, ReferenceContent)
        return self.result.text.split("\n")[1:]


@pytest.fixture(scope="module")
def answers(corpus: ConformanceCorpus) -> dict[str, _Answer]:
    from foss_mcp.mcp.tools.readme_sections import read_readme_sections

    out: dict[str, _Answer] = {}
    for name in sorted(corpus.pilots):
        pilot = corpus.pilots[name]
        sidecar = DEMO_MANIFESTS / fetch_product_reference.sidecar_name(
            pilot.scope.family, pilot.scope.platform
        )
        inputs = fetch_product_reference.load_manifest_sidecar(sidecar)
        assert inputs is not None, sidecar
        index = read_readme_sections(corpus.store, pilot.scope)
        result = get_product_reference(inputs, "formats", readme=index)
        effective = gpr._effective_readme(inputs, index)
        assert effective is not None
        sources = " ".join(
            _flow(section.text) for section in (effective.capabilities, effective.limitations) if section
        )
        out[name] = _Answer(name, result, sources)
    return out


_BREAK = "¶"  # a block boundary: a bullet start, blank line, code fence, table row, heading or <details>
_BULLET = re.compile(r"^\s*(?:[-*+•]\s+|\d+[.)]\s+)")


def _flow(text: str) -> str:
    """*text* as one line of README flow: wrapped lines joined by a space, and every block boundary marked
    with ``_BREAK``. Written independently of the production joiner; it only needs to expose where an item
    or sentence of the README really begins and ends."""
    parts: list[str] = []
    in_fence = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith(("```", "~~~")):
            in_fence = not in_fence
            parts.append(_BREAK)
        elif in_fence:
            continue
        elif line.startswith("|"):
            parts.append(_BREAK + " " + line)  # kept readable: a table-fallback row is checked against it
        elif not line or line.startswith(("#", "<")):
            parts.append(_BREAK)
        elif _BULLET.match(raw):
            parts.append(_BREAK + " " + _BULLET.sub("", raw).strip())
        else:
            parts.append(line)
    return " ".join(parts)


def _is_whole(line: str, sources: str) -> bool:
    """True when *line* (a ``"- "`` bullet) is a whole quoted statement of *sources* (``_flow`` text): it
    starts where an item or sentence of the README starts and ends where one ends (the full stop between two
    sentences of one item is not quoted, as before this card), or carries the ellipsis that marks an honest
    cut; a table-fallback row is checked cell by cell."""
    body = line[2:]
    if body.startswith(ELLIPSIS) or body.endswith(ELLIPSIS):
        return body.strip(ELLIPSIS) in sources
    position = sources.find(body)
    while position >= 0:
        before = sources[:position].rstrip()
        rest = sources[position + len(body) :].lstrip()
        starts_ok = not before or before.endswith((_BREAK, ".", "!", "?"))
        ends_ok = body.endswith((".", "!", "?")) or not rest or rest.startswith((_BREAK, ".", "!", "?"))
        if starts_ok and ends_ok and not body.endswith((",", ";", "(", "—", " and", " or", " the")):
            return True
        position = sources.find(body, position + 1)
    cells = [cell for cell in body.split(" - ") if cell]
    return len(cells) > 1 and all(cell in sources or cell.strip("`") in sources for cell in cells)


def test_the_corpus_has_all_32_pilots(answers: dict[str, _Answer]) -> None:
    assert len(answers) == EXPECTED_PILOT_COUNT
    assert Path(DEMO_MANIFESTS).is_dir()


def test_every_pilot_answers_or_degrades_to_the_honest_reason(answers: dict[str, _Answer]) -> None:
    unavailable = {name for name, answer in answers.items() if isinstance(answer.result, NotAvailable)}

    assert unavailable == GENUINELY_WITHOUT_A_FORMAT_STATEMENT
    for name in unavailable:
        assert answers[name].result == NotAvailable("formats", gpr._FORMATS_NONE_REASON)


def test_no_pilot_answers_with_a_code_only_example(answers: dict[str, _Answer]) -> None:
    code_only = []
    for name, answer in answers.items():
        if not isinstance(answer.result, ReferenceContent):
            continue
        text = answer.result.text
        prose = [
            line for line in re.sub(r"```.*?```", "", text, flags=re.DOTALL).splitlines() if line.strip()
        ]
        is_statement_answer = text.startswith(HEADING)
        if (
            text.lstrip().startswith("```")
            or not prose
            or not (is_statement_answer or name in SECTION_ANSWERS)
        ):
            code_only.append(name)

    assert code_only == []


def test_the_two_example_heading_pilots_now_serve_format_statements(answers: dict[str, _Answer]) -> None:
    for name in ("words__python", "3d__net"):
        text = answers[name].result.text  # type: ignore[union-attr]
        assert text.startswith(HEADING), name
        assert "Convert Every Input Format" not in text and "Unrecognized" not in text, name
        assert len(answers[name].lines) >= 3, name


def test_no_pilot_serves_an_unterminated_or_mid_sentence_line(answers: dict[str, _Answer]) -> None:
    offenders: list[str] = []
    for name, answer in answers.items():
        if name in SECTION_ANSWERS or not isinstance(answer.result, ReferenceContent):
            continue
        for line in answer.lines:
            if not _is_whole(line, answer.sources):
                offenders.append(f"{name}: {line[-110:]}")

    assert offenders == []


def test_every_statement_line_is_within_the_cap_and_marked_when_cut(answers: dict[str, _Answer]) -> None:
    for name, answer in answers.items():
        if name in SECTION_ANSWERS or not isinstance(answer.result, ReferenceContent):
            continue
        assert 1 <= len(answer.lines) <= 8, name
        for line in answer.lines:
            assert line.startswith("- ") and len(line) <= CAP + 2, (name, len(line))


def test_the_whole_readme_section_answers_are_unchanged_in_kind(answers: dict[str, _Answer]) -> None:
    for name in SECTION_ANSWERS:
        result = answers[name].result
        assert isinstance(result, ReferenceContent)
        assert not result.text.startswith(HEADING), name
        assert "Verbatim README section" in (result.notice or ""), name


# The five pilots the triage found clean must not get worse: still whole statements, no ellipsis cut, and
# (pdf/java, the one of them that quoted a sentence about the commercial edition) no Enterprise-edition tail.
# Measured: cells/net, cells/rust, email/net and pdf/go are byte-identical before and after this card.
@pytest.mark.parametrize("pilot", CLEAN_BEFORE)
def test_the_already_clean_pilots_are_still_clean(answers: dict[str, _Answer], pilot: str) -> None:
    answer = answers[pilot]
    assert isinstance(answer.result, ReferenceContent)

    assert len(answer.lines) >= 3
    assert all(_is_whole(line, answer.sources) for line in answer.lines), answer.lines
    assert not any(ELLIPSIS in line for line in answer.lines), "a clean pilot must not need an ellipsis cut"
    assert "commercial edition" not in answer.result.text.lower()
