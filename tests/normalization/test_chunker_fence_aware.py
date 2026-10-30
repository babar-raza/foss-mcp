"""G3/TC-611 (REQ-G3-014): the chunker is fence-aware.

TC-609/F9 measured exactly 12 orphan chunks on 3d/python, pdf/python and slides/python: a column-0
``#`` comment inside a fenced code block was read by ``chunker.py``'s ``_HEADING`` regex
(``^(#{1,6})[ \\t]+``, ``re.MULTILINE``) as a markdown heading, cutting the chunk there. The comment
text became the new "section"'s title, and the code that followed it became a second, orphaned
chunk - no content before the FIRST heading match of a body is kept by ``_heading_sections`` at
all, so whatever sat before the fake heading (often the real heading plus the start of the code)
was silently dropped too.

The fix (``chunker._fence_spans``) tracks fenced-code-block state while scanning a body for
headings and never treats a ``#``-prefixed line as a heading while it sits inside an OPEN fence (3+
backticks or tildes; a closing fence needs the SAME character, repeated at least as many times as
the opener). Real furnished-page content was checked (``tests/fixtures/furnished/**/pages/_index.md``)
and uses only backtick fences - no tilde fence was found anywhere - so cases (a)-(c) below use
backtick fences, the only style this fix needs to prove itself against on real data; the
implementation still recognizes tilde fences the same way, since real markdown allows both and the
fix does not special-case backticks.

UNCLOSED-FENCE FALLBACK (case (c)): a fence opened but never closed before the body ends is treated
as closing at end-of-body. Everything from the opening delimiter onward stays inside that one
(already-malformed) section, including any ``#``-prefixed line that would otherwise look like a
real heading. This is a deliberate trade-off, not an oversight: the alternative (treating an
unclosed fence as closing immediately, or not tracking it at all) would let one truncated fence
either leak its own code into unrelated later sections or silently swallow every real heading for
the rest of the document - this fallback instead confines the damage to the one block that is
already broken, which is the smallest honest failure mode available.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from foss_mcp.indexing.doc_candidates import extract_doc_sections
from foss_mcp.normalization.chunker import _HEADING, chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document

PROVENANCE = Provenance(repository="aspose-slides-foss/Aspose.Slides-FOSS-for-Python", commit="c0ffee" * 6)

FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "furnished"


def _document(body: str):
    return make_document(
        source_kind=SourceKind.FURNISHED,
        content_type="doc",
        provenance=PROVENANCE,
        evidence_refs=("aspose-slides-foss/Aspose.Slides-FOSS-for-Python#readme",),
        title="fence-aware chunking",
        body=body,
    )


def _load_page(fixture_path: Path) -> dict:
    """The real, committed furnished page as ``yaml.safe_load`` sees it - mirrors
    ``tests/indexing/test_doc_candidates.py``'s own ``_load_page`` helper exactly (that file is a
    different card's, read-only here; this is a same-shaped helper in this card's own new file, not
    an edit to that one)."""
    text = fixture_path.read_text(encoding="utf-8")
    front_matter = text.split("---", 2)[1]
    parsed = yaml.safe_load(front_matter)
    assert isinstance(parsed, dict)
    return parsed


# ---- (a) a fenced code block with a column-0 "#" comment stays intact within its section --------


def test_a_column_0_comment_inside_a_fence_never_splits_the_chunk() -> None:
    body = (
        "# Save a Scene\n\n"
        "Build a scene and export it.\n\n"
        "```python\n"
        "from aspose.threed import Scene\n"
        "\n"
        "scene = Scene()\n"
        "\n"
        "# Save to STL once the scene is built - a column-0 comment, the exact shape the old\n"
        "# _HEADING regex mistook for a markdown heading.\n"
        "scene.save('crate.stl')\n"
        "```\n\n"
        "The saved file can now be reopened by any STL-aware tool.\n"
    )
    chunks = chunk_document(_document(body))

    assert len(chunks) == 1
    section = chunks[0]
    assert section.section_title == "Save a Scene"
    # The whole fenced block, comment and all, uninterrupted - never split at the comment.
    assert "# Save to STL once the scene is built" in section.text
    assert "scene.save('crate.stl')" in section.text
    assert section.text.count("```") == 2
    # The prose after the closing fence is part of THIS section, never a headerless orphan chunk.
    assert "The saved file can now be reopened" in section.text
    assert not any(c.section_title.startswith("Save to STL") for c in chunks)


# ---- (b) real headings outside any fence still split correctly -----------------------------------


def test_b_real_headings_outside_any_fence_still_split_normally() -> None:
    """The fix narrows what counts as a heading - it must not disable heading-splitting
    everywhere. Three real ``##`` headings, with a fence (holding its own column-0 ``#`` comment)
    sitting between two of them, must still produce three separate sections."""
    body = (
        "## Overview\n\n"
        "A rendering and cross-platform release.\n\n"
        "## Installation\n\n"
        "```bash\n"
        "# not a heading - a shell comment inside the fence\n"
        "pip install aspose-3d-foss\n"
        "```\n\n"
        "## Usage\n\n"
        "Import the package and get started.\n"
    )
    chunks = chunk_document(_document(body))

    titles = [c.section_title for c in chunks]
    assert titles == ["Overview", "Installation", "Usage"]
    installation = next(c for c in chunks if c.section_title == "Installation")
    assert "# not a heading - a shell comment inside the fence" in installation.text
    assert "pip install aspose-3d-foss" in installation.text
    usage = next(c for c in chunks if c.section_title == "Usage")
    assert "Import the package and get started." in usage.text
    # No section was fabricated from the in-fence comment line.
    assert not any("not a heading" in title for title in titles)


def test_a2_two_separate_fences_in_one_document_are_each_tracked_independently() -> None:
    """A document with TWO separate, properly-closed fences (each carrying its own in-fence
    comment), followed by a real heading after both close - proves fence-tracking resets correctly
    between fences rather than treating the whole rest of the document as "still in a fence" once
    the first one closes, or conversely losing track and missing the second fence's comment."""
    body = (
        "## First Example\n\n"
        "```python\n"
        "# first fence's own column-0 comment\n"
        "print('one')\n"
        "```\n\n"
        "## Second Example\n\n"
        "```python\n"
        "# second fence's own column-0 comment\n"
        "print('two')\n"
        "```\n\n"
        "## Wrap Up\n\n"
        "Both examples ran.\n"
    )
    chunks = chunk_document(_document(body))

    titles = [c.section_title for c in chunks]
    assert titles == ["First Example", "Second Example", "Wrap Up"]
    first = next(c for c in chunks if c.section_title == "First Example")
    second = next(c for c in chunks if c.section_title == "Second Example")
    assert "# first fence's own column-0 comment" in first.text
    assert "# second fence's own column-0 comment" in second.text
    assert "print('one')" not in second.text
    assert "print('two')" not in first.text


# ---- (c) an unclosed fence: the documented fallback ------------------------------------------------


def test_c_an_unclosed_fence_falls_back_to_closing_at_end_of_body() -> None:
    """An opened-but-never-closed fence must not make the rest of a real document unscannable in
    the OTHER direction either: everything from the opening delimiter to the end of the body stays
    inside the one section the fence opened in - including a line that looks like a real heading -
    rather than the chunker crashing, hanging, or (the regression this card fixes) treating that
    line as a real heading and splitting there anyway."""
    body = (
        "# Truncated Example\n\n"
        "The fence below is never closed - a real, if malformed, README can look like this.\n\n"
        "```python\n"
        "import os\n"
        "\n"
        "# a column-0 comment, same shape as the orphan-triggering ones\n"
        "print(os.getcwd())\n"
        "\n"
        "# Looks Like A Heading But Is Inside The Unclosed Fence\n"
        "print('still inside the broken fence')\n"
    )
    chunks = chunk_document(_document(body))

    assert len(chunks) == 1
    section = chunks[0]
    assert section.section_title == "Truncated Example"
    # Both in-fence comment lines - including the one shaped exactly like a heading - stay inside
    # this one section; neither became its own chunk.
    assert "# a column-0 comment, same shape as the orphan-triggering ones" in section.text
    assert "# Looks Like A Heading But Is Inside The Unclosed Fence" in section.text
    assert "print('still inside the broken fence')" in section.text
    titles = [c.section_title for c in chunks]
    assert "Looks Like A Heading But Is Inside The Unclosed Fence" not in titles


# ---- (d) a REAL regression reproduction, against real furnished-page content ---------------------


def test_d_real_slides_python_quick_start_no_longer_orphans_on_the_in_fence_comments() -> None:
    """``tests/fixtures/furnished/slides_python/pages/_index.md`` is one of TC-609's three
    confirmed-affected pilots. Its real, committed "Installation, Dependencies, and Quick Start"
    content block's ``### Quick Start`` subsection carries a fenced Python example with TWO
    column-0 ``#`` comments inside the fence ("Create a new presentation...", "Open an existing
    presentation...") - read through the real, production ``extract_doc_sections`` (TC-110), never
    a second, hand-rolled extraction.

    First this proves the raw real text WOULD have triggered the old defect: the OLD, unfixed
    ``_HEADING`` regex (imported from the real module, not re-derived) matches both in-fence
    comment lines in addition to the four genuine ``###`` headings. Then it proves the FIXED
    ``chunk_document`` no longer acts on those two matches: the whole ``### Quick Start``
    subsection - prose, fence open, both comments, both code blocks, fence close - survives as ONE
    section, and neither in-fence comment line ever becomes a chunk title of its own.
    """
    page = _load_page(FIXTURE_ROOT / "slides_python" / "pages" / "_index.md")
    candidates = extract_doc_sections(page)
    candidate = next(c for c in candidates if c.title == "Installation, Dependencies, and Quick Start")

    # The real body contains both in-fence comment lines, verbatim.
    assert "# Create a new presentation" in candidate.body
    assert "# Open an existing presentation" in candidate.body

    # Sanity: the OLD (unfixed) regex really does match both in-fence lines on this real text -
    # the defect this card closes is real on this exact committed fixture, not a synthetic stand-in.
    old_heading_lines = [match.group(0) for match in _HEADING.finditer(candidate.body)]
    assert any(line.startswith("# Create a new presentation") for line in old_heading_lines)
    assert any(line.startswith("# Open an existing presentation") for line in old_heading_lines)

    doc = make_document(
        source_kind=SourceKind.FURNISHED,
        content_type="doc",
        provenance=PROVENANCE,
        evidence_refs=("aspose-slides-foss/Aspose.Slides-FOSS-for-Python#readme",),
        title=candidate.title,
        body=candidate.body,
    )
    chunks = chunk_document(doc)
    titles = [chunk.section_title for chunk in chunks]

    # Neither in-fence comment produced its own chunk - the orphan-fragment defect is gone.
    assert not any(title.startswith("Create a new presentation") for title in titles)
    assert not any(title.startswith("Open an existing presentation") for title in titles)

    quick_start = next(chunk for chunk in chunks if chunk.section_title == "Quick Start")
    assert "# Create a new presentation" in quick_start.text
    assert "# Open an existing presentation" in quick_start.text
    assert quick_start.text.count("```") == 2
    assert 'prs.save("output.pptx", SaveFormat.PPTX)' in quick_start.text
