"""G3/TC-817 (REQ-G3-013): a README section keeps its own heading as its doc-chunk title and bucket.

Two triage rows asked for it. RD2-271 (words/net): the README section "What's not included (and where to
get it)" was indexed as a ``developer_guide`` chunk titled "Key Capabilities and API Reference", because
a furnished page folds every README section it has no block for under one block as ``### <heading>``
subsections, ``infra/build_chunks.py`` titles every chunk of a candidate with the candidate's title, and
``chunk_document`` drops a heading line from the chunk text - so the heading was lost and the section
could never classify as ``troubleshooting``. RD2-224 (pdf/typescript): its committed page is the older,
hand-written one that has no installation, quick start or limitations section at all; the README-based
generator already gives that README distinct, correctly bucketed chunks (pinned below), and the live
data picks that up at the later coordinated regeneration of the pilots.

``foss_mcp.indexing.doc_candidates.extract_doc_sections`` now carves such a section out of its block into
its own candidate. These tests drive that path directly: synthetic pages, the committed pages, and the
committed sidecar READMEs (``tests/fixtures/round3_readmes``, at the chart-pinned commits) run through the
real page generator. Nothing here depends on the committed pages having been regenerated.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import infra.generate_furnished_page as gen
from foss_mcp.indexing.doc_candidates import DocCandidate, extract_doc_sections
from foss_mcp.indexing.doc_chunking import chunk_doc_candidate
from foss_mcp.mcp.tools.search_docs import classify_content_type
from foss_mcp.normalization.document_schema import Provenance, SourceKind

REPO_ROOT = Path(__file__).resolve().parents[2]
FURNISHED = REPO_ROOT / "tests" / "fixtures" / "furnished"
README_DIR = REPO_ROOT / "tests" / "fixtures" / "round3_readmes"


def _page(*blocks: dict) -> dict:
    return {"content": {"enable": True, "block": list(blocks)}}


def _chunks(candidate: DocCandidate) -> list[tuple[str, str]]:
    """``[(classified bucket, chunk text)]`` of *candidate*, titled exactly as ``_build_doc_chunks`` titles them."""
    chunks = chunk_doc_candidate(
        candidate,
        source_kind=SourceKind.FURNISHED,
        content_type="doc",
        provenance=Provenance(repository="org/repo", commit="c0ffee", path="pages/_index.md"),
        evidence_refs=("org/repo@c0ffee",),
    )
    texts = [f"FQN: Doc: {candidate.title}\n{chunk.text}" for chunk in chunks]
    return [(classify_content_type(text), text) for text in texts]


def _buckets(page: dict) -> list[tuple[str, str]]:
    """``[(candidate title, bucket)]`` for every chunk of every candidate of *page*."""
    return [
        (candidate.title, bucket)
        for candidate in extract_doc_sections(page)
        for bucket, _ in _chunks(candidate)
    ]


def _load_committed_page(pilot: str) -> dict:
    text = (FURNISHED / pilot / "pages" / "_index.md").read_text(encoding="utf-8")
    parsed = yaml.safe_load(text.split("---", 2)[1])
    assert isinstance(parsed, dict)
    return parsed


def _readme_page(pilot: str) -> dict:
    text = (README_DIR / pilot / "README.md").read_text(encoding="utf-8")
    family, platform = pilot.split("_", 1)
    entry = {"family": family, "platform": platform, "library": {"repository": f"org/{pilot}", "commit": "c"}}
    page_text = gen.build_page_from_readme(entry, gen.parse_readme(f"org/{pilot}", "c", text))
    parsed = yaml.safe_load(page_text.split("---", 2)[1])
    assert isinstance(parsed, dict)
    return parsed


# Longer than ``doc_candidates._MIN_CARVED_CHARS``: a section shorter than that is never carved out.
_DETAIL = "The details of this section are set out in full right here. " * 5

_CAPABILITIES = (
    "### Key Capabilities\n\nCreate and edit documents.\n\n"
    "### What's not included (and where to get it)\n\n"
    f"Mail merge and document signing are not included in this edition. {_DETAIL}\n\n"
    "### Development and Testing\n\nRun the tests with the test runner."
)


# ---------------------------------------------------------------------------
# Synthetic: the section is carved out, titled by its own heading, bucketed right
# ---------------------------------------------------------------------------


def test_a_not_included_section_becomes_its_own_troubleshooting_candidate() -> None:
    page = _page({"title_left": "Key Capabilities and API Reference", "content_left": _CAPABILITIES})

    candidates = extract_doc_sections(page)

    assert [c.title for c in candidates] == [
        "Key Capabilities and API Reference",
        "Scope and Limitations: What's not included (and where to get it)",
    ]
    carved = candidates[1]
    assert carved.origin == "content"
    assert "Mail merge and document signing are not included" in carved.body
    assert "Development and Testing" not in carved.body  # the next sibling section stays in the block
    assert "not included" not in candidates[0].body  # ... and the carved section left the block

    [(bucket, text)] = _chunks(carved)
    assert bucket == "troubleshooting"
    assert text.startswith("FQN: Doc: Scope and Limitations: What's not included")
    assert [b for b, _ in _chunks(candidates[0])] == ["developer_guide"] * len(_chunks(candidates[0]))


@pytest.mark.parametrize(
    "heading",
    [
        "What it cannot do",
        "What it can't do",
        "What it can’t do",
        "Not supported",
        "Not yet implemented",
        "Features that are not available",
        "What this library does not support",
        "What the library doesn't include",
        "Out of scope",
        "What is missing",
        "Unsupported formats",
        "Known limitations",
        "Known issues",
    ],
)
def test_limitation_headings_classify_as_troubleshooting(heading: str) -> None:
    body = f"### Overview of features\n\nIt does many things.\n\n### {heading}\n\n{_DETAIL}"
    candidates = extract_doc_sections(_page({"title_left": "Key Capabilities", "content_left": body}))

    assert [c.title for c in candidates][0] == "Key Capabilities"
    assert len(candidates) == 2
    assert heading in candidates[1].title
    assert {bucket for bucket, _ in _chunks(candidates[1])} == {"troubleshooting"}


@pytest.mark.parametrize(
    "heading",
    ["Installation", "Installing on Linux", "Quick start", "Quickstart", "Getting started", "Install"],
)
def test_setup_headings_classify_as_getting_started(heading: str) -> None:
    body = (
        f"### Overview of features\n\nIt does many things.\n\n### {heading}\n\nRun the installer. {_DETAIL}"
    )
    candidates = extract_doc_sections(_page({"title_left": "Key Capabilities", "content_left": body}))

    assert len(candidates) == 2
    assert candidates[1].title == heading  # the heading already classifies: it is the title, unprefixed
    assert {bucket for bucket, _ in _chunks(candidates[1])} == {"getting_started"}


def test_a_carved_section_keeps_its_nested_subheadings_and_text() -> None:
    body = (
        "### Overview of features\n\nIt does many things.\n\n"
        "### What it cannot do\n\nIntro line.\n\n"
        f"#### Not in the public API\n\nINNER-A {_DETAIL}\n\n"
        "#### Save formats\n\nINNER-B\n\n"
        "### Choosing an edition\n\nPick one."
    )
    candidates = extract_doc_sections(_page({"title_left": "Key Capabilities", "content_left": body}))

    assert [c.title for c in candidates] == ["Key Capabilities", "Scope and Limitations: What it cannot do"]
    carved_text = "\n".join(text for _, text in _chunks(candidates[1]))
    assert "INNER-A" in carved_text and "INNER-B" in carved_text and "Intro line." in carved_text
    assert "**Not in the public API**" in carved_text and "**Save formats**" in carved_text  # kept as text
    assert "Choosing an edition" not in carved_text and "Pick one." not in carved_text
    assert len(_chunks(candidates[1])) == 1  # the lead-in is not stranded as a chunk of its own
    assert "Pick one." in "\n".join(text for _, text in _chunks(candidates[0]))


def test_several_sections_are_carved_in_body_order_and_a_right_hand_block_too() -> None:
    body = (
        f"### Intro\n\nHello.\n\n### Not supported\n\nA. {_DETAIL}\n\n### Middle\n\nB.\n\n"
        f"### Quick start\n\nC. {_DETAIL}"
    )
    page = _page(
        {"title_left": "Overview", "content_left": "plain", "title_right": "Reference", "content_right": body}
    )

    assert [c.title for c in extract_doc_sections(page)] == [
        "Overview",
        "Reference",
        "Scope and Limitations: Not supported",
        "Quick start",
    ]


def test_a_block_that_is_only_a_limitation_section_leaves_no_empty_remainder() -> None:
    page = _page({"title_left": "Reference", "content_left": f"### Not supported\n\nOnly this. {_DETAIL}"})

    candidates = extract_doc_sections(page)

    assert [(c.title, c.body) for c in candidates] == [
        ("Scope and Limitations: Not supported", f"Only this. {_DETAIL}")
    ]


# ---------------------------------------------------------------------------
# Synthetic: everything that was already right is extracted exactly as before
# ---------------------------------------------------------------------------


def test_a_block_whose_own_title_names_the_bucket_is_not_split() -> None:
    body = (
        f"### Known issues\n\nA. {_DETAIL}\n\n#### Detailed Limitations Reference\n\nB.\n\n"
        f"### Not supported\n\nC. {_DETAIL}"
    )
    candidates = extract_doc_sections(_page({"title_left": "Scope and Limitations", "content_left": body}))

    assert candidates == [DocCandidate(title="Scope and Limitations", body=body, origin="content")]


def test_an_installation_block_is_not_split_by_its_own_dependency_subsections() -> None:
    body = (
        f"### Installation\n\nInstall it. {_DETAIL}\n\n### Required Package Dependencies\n\nNone.\n\n"
        f"### Quick Start\n\nGo. {_DETAIL}"
    )
    candidates = extract_doc_sections(
        _page({"title_left": "Installation, Dependencies, and Quick Start", "content_left": body})
    )

    assert len(candidates) == 1 and candidates[0].body == body


def test_headings_without_a_bucket_never_split_a_block() -> None:
    body = (
        f"### Charts\n\nAbout charts. {_DETAIL}\n\n### Errors in charts\n\nThe Errors type. {_DETAIL}\n\n"
        f"### Significant changes\n\nNone. {_DETAIL}"
    )
    candidates = extract_doc_sections(_page({"title_left": "API Reference", "content_left": body}))

    assert candidates == [DocCandidate(title="API Reference", body=body, origin="content")]


def test_a_deeper_heading_is_never_carved_out_of_its_own_section() -> None:
    body = f"### Conversion\n\nConverters.\n\n#### Unsupported conversions\n\nNot every pair works. {_DETAIL}"
    candidates = extract_doc_sections(_page({"title_left": "API Reference", "content_left": body}))

    assert candidates == [DocCandidate(title="API Reference", body=body, origin="content")]


def test_a_heading_inside_a_fenced_code_block_is_code_not_a_heading() -> None:
    body = (
        f"### Usage\n\nRun: {_DETAIL}\n\n```bash\n### Installation\n# Not supported\n```\n\n"
        "~~~\n### Quick start\n~~~\n\nDone."
    )
    candidates = extract_doc_sections(_page({"title_left": "Key Capabilities", "content_left": body}))

    assert candidates == [DocCandidate(title="Key Capabilities", body=body, origin="content")]


def test_a_body_with_no_headings_and_the_other_sections_are_untouched() -> None:
    page = {
        "overview": {"enable": True, "title": "Overview", "content": "### Not supported\n\nOverview prose."},
        "content": {
            "enable": True,
            "block": [{"title_left": "Quick Start", "content_left": "Just prose, not supported anywhere."}],
        },
        "faq": {"enable": True, "list": [{"question": "Why not included?", "answer": "Because."}]},
    }

    assert extract_doc_sections(page) == [
        DocCandidate(title="Overview", body="### Not supported\n\nOverview prose.", origin="overview"),
        DocCandidate(title="Quick Start", body="Just prose, not supported anywhere.", origin="content"),
        DocCandidate(title="Why not included?", body="FAQ: Why not included?\n\nBecause.", origin="faq"),
    ]


# ---------------------------------------------------------------------------
# Real committed pages and READMEs
# ---------------------------------------------------------------------------


def _pilots_with_pages() -> list[str]:
    return sorted(path.parent.parent.name for path in FURNISHED.glob("*/pages/_index.md"))


def test_the_committed_words_net_page_now_serves_its_not_included_section_as_troubleshooting() -> None:
    page = _load_committed_page("words_net")

    titles = [c.title for c in extract_doc_sections(page)]
    assert "Scope and Limitations: What's not included (and where to get it)" in titles
    carved = next(c for c in extract_doc_sections(page) if c.title.startswith("Scope and Limitations"))
    assert "To keep this edition free" in carved.body
    assert {bucket for bucket, _ in _chunks(carved)} == {"troubleshooting"}
    # no chunk of the old, merged block still holds that section
    for candidate in extract_doc_sections(page):
        if candidate.title != carved.title:
            assert all("To keep this edition free" not in text for _, text in _chunks(candidate))


def test_words_net_readme_through_the_generator_files_not_included_as_troubleshooting() -> None:
    candidates = extract_doc_sections(_readme_page("words_net"))

    carved = [c for c in candidates if "not included" in c.title]
    assert [c.title for c in carved] == ["Scope and Limitations: What's not included (and where to get it)"]
    assert {bucket for bucket, _ in _chunks(carved[0])} == {"troubleshooting"}
    for candidate in candidates:
        if candidate.title == "Key Capabilities":
            assert all("To keep this edition free" not in text for _, text in _chunks(candidate))


def test_pdf_typescript_readme_gives_installation_quick_start_and_limitations_their_own_buckets() -> None:
    page = _readme_page("pdf_typescript")

    by_title: dict[str, set[str]] = {}
    for title, bucket in _buckets(page):
        by_title.setdefault(title, set()).add(bucket)
    assert by_title["Installation"] == {"getting_started"}
    assert by_title["Quick Start"] == {"getting_started"}
    assert by_title["Scope and Limitations"] == {"troubleshooting"}
    assert by_title["Key Capabilities"] == {"developer_guide"}
    assert by_title["API Reference"] == {"developer_guide"}


def test_pdf_typescript_committed_page_is_the_old_one_with_no_such_sections() -> None:
    """RD2-224's cause, pinned: the committed page predates the README-based generator (it carries no
    ``doc_source``), so it holds no installation / quick start / limitations content to bucket. The
    coordinated regeneration of the pilots is what replaces it."""
    page = _load_committed_page("pdf_typescript")

    assert "doc_source" not in page
    assert {bucket for _, bucket in _buckets(page)} == {"developer_guide", "faq"}


# Which pilots hold a section carved out by this card; every other pilot's candidates are exactly the
# titles its page names itself: nothing else changed. The committed slides/net page has its README's
# hierarchy flattened (the ``What it cannot do`` heading is followed by its children as siblings, so its own
# text is a 116-character lead-in), so it is deliberately left as it was; the same README through today's
# generator keeps the children nested and is carved.
_CARVED_COMMITTED_PAGES = {"words_net"}
_CARVED_SIDECAR_READMES = {"slides_net", "words_net"}


def _carved_titles(page: dict) -> set[str]:
    """Titles ``extract_doc_sections`` gives *page*'s candidates that the page itself does not name."""
    own_titles: set[str] = set()
    overview = page.get("overview")
    if isinstance(overview, dict) and "title" in overview:
        own_titles.add(str(overview["title"]))
    content = page.get("content")
    for block in content.get("block", []) if isinstance(content, dict) else []:
        own_titles.update(str(block[key]) for key in ("title_left", "title_right") if key in block)
    faq = page.get("faq")
    for entry in faq.get("list", []) if isinstance(faq, dict) else []:
        own_titles.add(str(entry["question"]))
    return {c.title for c in extract_doc_sections(page)} - own_titles


def test_only_the_expected_committed_pages_have_a_carved_section() -> None:
    carved = {pilot: _carved_titles(_load_committed_page(pilot)) for pilot in _pilots_with_pages()}

    assert {pilot for pilot, titles in carved.items() if titles} == _CARVED_COMMITTED_PAGES
    assert carved["words_net"] == {"Scope and Limitations: What's not included (and where to get it)"}


def test_only_the_expected_sidecar_readmes_have_a_carved_section() -> None:
    carved = {
        path.parent.name: _carved_titles(_readme_page(path.parent.name))
        for path in sorted(README_DIR.glob("*/README.md"))
    }

    assert len(carved) >= 16
    assert {pilot for pilot, titles in carved.items() if titles} == _CARVED_SIDECAR_READMES
    assert carved["words_net"] == {"Scope and Limitations: What's not included (and where to get it)"}
    assert carved["slides_net"] == {"Scope and Limitations: What it cannot do"}


def test_slides_net_readme_carves_what_it_cannot_do_with_all_of_its_nested_sections() -> None:
    candidates = extract_doc_sections(_readme_page("slides_net"))

    [carved] = [c for c in candidates if c.title == "Scope and Limitations: What it cannot do"]
    assert {bucket for bucket, _ in _chunks(carved)} == {"troubleshooting"}
    for heading in ("Not in the public API", "Save formats", "Text language"):
        assert f"**{heading}**" in carved.body
    assert "Choosing an edition" not in carved.body  # the next README section is not part of it
    for _, text in _chunks(carved):  # no stub: every chunk of the section has a real body
        assert len(text.split("\n", 1)[1]) >= 200, text


def test_a_lead_in_shorter_than_a_chunk_stays_in_its_block_on_a_flattened_page() -> None:
    """The committed slides/net shape: the heading's own text is a lead-in, its children are siblings."""
    body = (
        "### Overview\n\nIt does many things.\n\n"
        "### What it cannot do\n\nThis section is the point of this file; it is what the API does not contain.\n\n"
        "### Not in the public API\n\n" + "A line of detail about what is missing. " * 20
    )
    candidates = extract_doc_sections(_page({"title_left": "Key Capabilities", "content_left": body}))

    assert candidates == [DocCandidate(title="Key Capabilities", body=body, origin="content")]


# ---------------------------------------------------------------------------
# The generator keeps a kept section's own children nested under it
# ---------------------------------------------------------------------------


def test_demote_headings_goes_one_level_deeper_outside_fences_only() -> None:
    text = (
        "intro\n\n### Child\n\n```bash\n### not a heading\n```\n\n###### Deepest\n\n  ## Indented\n\n#hashtag"
    )

    assert gen._demote_headings(text) == (
        "intro\n\n#### Child\n\n```bash\n### not a heading\n```\n\n###### Deepest\n\n  ### Indented\n\n#hashtag"
    )


def test_a_kept_section_keeps_its_children_nested_below_its_own_heading() -> None:
    detail = "A line of detail about what is missing today. " * 8
    readme = (
        "# Widgets\n\nA widget library for everyone.\n\n## Features\n\nMake widgets.\n\n"
        "## What it cannot do\n\nLead-in sentence that says what follows.\n\n"
        f"### Not in the public API\n\n{detail}\n\n### Save formats\n\n{detail}\n\n"
        "## Contributing\n\nPlease do.\n\n"
        + "```python\nprint(1)\n```\n\n" * 3
        + "\n".join(f"Filler line {n} so the README is long enough to be documentation." for n in range(45))
    )
    fields = gen.parse_readme("org/widgets", "c0ffee", readme)

    body = fields.sections["Key Capabilities"]
    assert "### What it cannot do" in body
    assert "#### Not in the public API" in body and "#### Save formats" in body
    assert "\n### Not in the public API" not in body
