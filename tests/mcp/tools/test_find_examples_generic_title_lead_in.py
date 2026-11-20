"""G3/TC-797 (REQ-G3-011): an example whose README heading is generic is found by the subject its lead-in
sentence names.

RD2-143 (html/python) and RD2-203 (pdf/net), 2026-11-14 triage. ``find_examples`` gates a short query on the
example's TITLE (TC-543: the rule that keeps ``save``/``open`` boilerplate out). Forty-six of the 280 served
examples are titled with a README heading that names no subject - ``Quick Start``, ``Quick Start (2)``,
``Additional Examples``, ``Installation, Dependencies, and Quick Start`` - so no query for what the example
does could ever hit it: html/python's ``Quick Start (2)`` (lead-in "Attach a stylesheet and resolve the
computed style") answered neither ``stylesheet`` nor ``computed style``.

The fix (``relevance.lead_in_anchored``) lets the lead-in sentence stand in for the title of a GENERIC-titled
example only - every distinctive query term must be a word of it, and a lead-in longer than
``LEAD_IN_MAX_WORDS`` (a whole-workflow summary) never anchors. A specific title is judged exactly as before.
The measured retrieval-corpus effect (hit@3 never lower, wrong-confident never higher) is pinned by
``test_round3_retrieval_corpus.py``, which this card leaves unchanged.

Offline and deterministic: synthetic generations go through the real ``publish_generation`` with the
embedding gateway replaced by one whose ``embed`` raises; the real-data tests read a COPY of the committed
``infra/demo-manifests``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.publisher import publish_generation
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools import find_examples as find_examples_module
from foss_mcp.mcp.tools.find_examples import (
    MATCH_BASIS_TITLE_WORD,
    NoExampleFound,
    find_examples,
)
from foss_mcp.mcp.tools.get_symbol import extract_fqn
from foss_mcp.mcp.tools.relevance import (
    GENERIC_EXAMPLE_TITLES,
    LEAD_IN_MAX_WORDS,
    chunk_kind,
    example_lead_in,
    is_generic_example_title,
    lead_in_anchored,
    stem_token,
    title_anchored,
)
from foss_mcp.mcp.tools.search_symbols import scope_key
from foss_mcp.mcp.tools.vector_guard import _set_guard
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document
from tests.indexing.test_index_writers import DeterministicEmbeddingProvider

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"

COMMIT = "10a363830fea952c29df20fdb37d35ad3a881433"
EXAMPLE_PREFIX = "Example: "
HTML_SCOPE = Scope(family="html", platform="python")
HTML_LEAD_IN = "Attach a stylesheet and resolve the computed style"


class _NoGatewayEmbeddingProvider:
    """What the standalone image has when no embedding gateway is configured: embedding fails."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("no embedding gateway in this deployment")


@pytest.fixture(autouse=True)
def _no_embedding_gateway(monkeypatch: pytest.MonkeyPatch) -> object:
    monkeypatch.setattr(find_examples_module, "GatewayEmbeddingProvider", _NoGatewayEmbeddingProvider)
    _set_guard(None)
    yield None
    _set_guard(None)


def _chunk(title: str, lead_in: str, code: str = "value = compute()\nprint(value)") -> str:
    return f"FQN: Example: {title}\nKind: verified_example\n{lead_in}\n\nExample:\n{code}\nSource-Commit: {COMMIT}"


def _publish(root: Path, texts: list[str]) -> GenerationManifestStore:
    store = GenerationManifestStore(root)
    body = "\n\n".join(f"## section {index}\n\n{text}" for index, text in enumerate(texts))
    doc = make_document(
        source_kind=SourceKind.SELF_EXTRACTED,
        content_type="api_surface",
        provenance=Provenance(repository="aspose-html-foss/Aspose.HTML-FOSS-for-Python", commit=COMMIT),
        evidence_refs=(),
        title="html/python synthetic",
        body=body,
    )
    chunks = chunk_document(doc)
    assert [chunk.text for chunk in chunks] == texts
    lease = store.acquire_lease("html::python::self_extracted", "worker-1", "pending")
    publish_generation(
        store,
        family="html",
        platform="python",
        source_kind="self_extracted",
        expected_active=None,
        chunks=chunks,
        embedding_provider=DeterministicEmbeddingProvider(),
        lease=lease,
    )
    return store


def _titles(result: object) -> list[str]:
    assert isinstance(result, list), result
    return [match.fqn[len(EXAMPLE_PREFIX) :] for match in result]


def _real_store(tmp_path: Path, *pilot_dirs: str) -> GenerationManifestStore:
    root = tmp_path / "m"
    for name in pilot_dirs:
        shutil.copytree(DEMO_MANIFESTS / f"{name}__self_extracted", root / f"{name}__self_extracted")
    return GenerationManifestStore(root)


def _documents(store: GenerationManifestStore, scope: Scope) -> dict[str, dict[str, str]]:
    key = scope_key(scope, "self_extracted")
    generation_id = store.read_active(key)
    assert generation_id is not None
    return store.read_generation(key, generation_id).payload["lexical_index"]["documents"]


def _generic_chunks(store: GenerationManifestStore, scope: Scope) -> list[str]:
    return [
        document["text"]
        for document in _documents(store, scope).values()
        if chunk_kind(document["text"]) == "example"
        and is_generic_example_title((extract_fqn(document["text"]) or "")[len(EXAMPLE_PREFIX) :])
    ]


# --- the generic-title rule ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "Quick Start",
        "Quick start",
        "Quick Start (2)",
        "Quick Start (3)",
        "Additional Examples",
        "Additional Examples (5)",
        "Installation, Dependencies, and Quick Start",
        "Installation, Dependencies, and Quick Start (part 2)",
        "Usage",
        "Example",
        "Getting Started",
    ],
)
def test_a_readme_heading_with_no_subject_is_a_generic_title(title: str) -> None:
    assert is_generic_example_title(title)


@pytest.mark.parametrize(
    "title",
    [
        "Query Elements By Tag Name",
        "Extract Text Fragments from a Page",
        "Quick Start Guide to Watermarks",
        "Styling",
        "Streams",
        "Tables",
        "Set Document Properties",
        "",
    ],
)
def test_a_title_that_names_a_subject_is_not_generic(title: str) -> None:
    assert not is_generic_example_title(title)


def test_the_generic_set_is_closed_and_lower_case() -> None:
    assert all(title == title.lower() for title in GENERIC_EXAMPLE_TITLES)
    assert {"quick start", "additional examples", "installation, dependencies, and quick start"} <= (
        GENERIC_EXAMPLE_TITLES
    )


# --- the lead-in sentence -----------------------------------------------------------------------


def test_the_lead_in_is_the_sentence_directly_above_the_example_marker() -> None:
    assert example_lead_in(_chunk("Quick Start (2)", f"{HTML_LEAD_IN}:")) == HTML_LEAD_IN


def test_only_the_first_sentence_of_the_paragraph_is_the_lead_in() -> None:
    text = _chunk("Quick Start", "Open a file. Then close it again. Finally print the result.")
    assert example_lead_in(text) == "Open a file."


def test_the_lead_in_is_the_paragraph_nearest_the_marker_not_the_readme_prose_above_it() -> None:
    text = (
        "FQN: Example: Installation, Dependencies, and Quick Start\nKind: verified_example\n"
        "Required Package Dependencies\n\n- `System.Drawing.Common 8.0.0`\n\nQuick Start\n\n"
        "This example creates a new document.\n\nExample:\nvar x = 1;\nSource-Commit: " + COMMIT
    )
    assert example_lead_in(text) == "This example creates a new document."


def test_runtime_and_harness_metadata_lines_are_never_the_lead_in() -> None:
    text = (
        "FQN: Example: Quick Start (2)\nKind: verified_example\nRuntime-Status: failed\n"
        "Runtime-Error: IOException thrown by Scene.save\nWith explicit save options:\n\n"
        "Example:\nscene.save()\nSource-Commit: " + COMMIT
    )
    assert example_lead_in(text) == "With explicit save options"
    bare = "FQN: Example: Quick Start\nKind: verified_example\nHarness: files placed by the verifier\n\nExample:\nx()\n"
    assert example_lead_in(bare) == ""


def test_a_chunk_without_an_example_marker_has_no_lead_in() -> None:
    assert example_lead_in("FQN: Example: Quick Start\nKind: verified_example\nSome prose.\n") == ""


def test_prose_after_the_marker_is_never_read_as_the_lead_in() -> None:
    text = "FQN: Example: Quick Start\nKind: verified_example\n\nExample:\n# attach a stylesheet here\nx()\n"
    assert example_lead_in(text) == ""


# --- lead_in_anchored: the rule itself ----------------------------------------------------------


def test_the_title_anchor_itself_is_unchanged_a_generic_title_still_anchors_nothing_alone() -> None:
    assert title_anchored("stylesheet", "html", "Quick Start (2)") is False
    assert title_anchored("quick start", "html", "Quick Start (2)") is True


@pytest.mark.parametrize("query", ["stylesheet", "computed style", "attach stylesheet", "style"])
def test_every_distinctive_query_term_in_the_lead_in_anchors_a_generic_titled_example(query: str) -> None:
    assert lead_in_anchored(query, "html", _chunk("Quick Start (2)", f"{HTML_LEAD_IN}:"))


@pytest.mark.parametrize("query", ["stylesheet", "computed style"])
def test_a_specific_title_never_anchors_through_its_lead_in(query: str) -> None:
    assert not lead_in_anchored(query, "html", _chunk("Walk The Element Tree", f"{HTML_LEAD_IN}:"))


def test_one_shared_word_among_several_query_terms_does_not_anchor() -> None:
    chunk = _chunk("Quick Start", "Create a new cell and save it")
    assert lead_in_anchored("create a cell", "html", chunk)
    assert not lead_in_anchored("change a cell value", "html", chunk)
    assert not lead_in_anchored("delete a worksheet cell", "html", chunk)


@pytest.mark.parametrize("query", ["save", "open", "create", "load", "add", "document", "text", "page"])
def test_a_boilerplate_verb_query_is_never_anchored_by_a_lead_in(query: str) -> None:
    chunk = _chunk(
        "Quick Start", "Create a document, add a page of text, save it, then open and load it again"
    )
    assert not lead_in_anchored(query, "pdf", chunk)


def test_a_one_word_query_the_scope_uses_everywhere_is_not_anchored_but_a_word_pair_is() -> None:
    chunk = _chunk("Quick Start", "Create a workbook, write values, style a cell, and save it")
    assert lead_in_anchored("workbook", "cells", chunk)
    assert not lead_in_anchored("workbook", "cells", chunk, ubiquitous_terms={stem_token("workbook")})
    # two words that both occur are specific together even when each is ubiquitous
    both = {stem_token("workbook"), stem_token("style")}
    assert lead_in_anchored("workbook style", "cells", chunk, ubiquitous_terms=both)


@pytest.mark.parametrize("query", ["write", "read", "show", "set", "new"])
def test_a_request_kind_verb_alone_is_never_anchored_by_a_lead_in(query: str) -> None:
    """``write`` found cells/java's Quick Start ("Create a workbook, write values, style a cell, and save
    it") although nothing in it is about writing: the round-2 recall ratchet counted one more unexplained
    answer than its frozen budget. ACTION_TERMS / WEAK_TERMS verbs say what KIND of request it is."""
    chunk = _chunk("Quick Start", "Create a workbook, write values, read them, show and set a new cell")
    assert not lead_in_anchored(query, "html", chunk)
    assert lead_in_anchored(f"{query} workbook", "html", chunk)


def test_a_query_with_no_distinctive_term_is_never_anchored() -> None:
    assert not lead_in_anchored("pdf example", "pdf", _chunk("Quick Start", "Create a PDF example"))


def test_a_lead_in_over_the_word_cap_never_anchors() -> None:
    short = "Attach a stylesheet and resolve the computed style"
    long_ = short + " " + " ".join(f"word{index}" for index in range(LEAD_IN_MAX_WORDS))
    assert lead_in_anchored("stylesheet", "html", _chunk("Quick Start", short))
    assert not lead_in_anchored("stylesheet", "html", _chunk("Quick Start", long_))


def test_a_generic_titled_example_with_no_lead_in_is_never_anchored() -> None:
    text = (
        "FQN: Example: Quick Start\nKind: verified_example\n\nExample:\nstylesheet()\nSource-Commit: "
        + COMMIT
    )
    assert not lead_in_anchored("stylesheet", "html", text)


# --- find_examples end to end, synthetic --------------------------------------------------------


def test_a_generic_titled_example_is_found_by_the_subject_its_lead_in_names(tmp_path: Path) -> None:
    store = _publish(
        tmp_path / "m",
        [
            _chunk("Quick Start", "Parse markup and print the tree"),
            _chunk("Quick Start (2)", f"{HTML_LEAD_IN}:"),
            _chunk("Read Encodings From Bytes", "Detect the encoding of raw bytes"),
        ],
    )
    for query in ("stylesheet", "computed style"):
        result = find_examples(store, HTML_SCOPE, query)
        assert _titles(result) == ["Quick Start (2)"], query
        assert result[0].match_basis == MATCH_BASIS_TITLE_WORD, query


def test_the_same_lead_in_under_a_specific_title_is_not_found(tmp_path: Path) -> None:
    store = _publish(
        tmp_path / "m",
        [
            _chunk("Walk The Element Tree", f"{HTML_LEAD_IN}:"),
            _chunk("Read Encodings From Bytes", "Detect the encoding of raw bytes"),
        ],
    )
    assert isinstance(find_examples(store, HTML_SCOPE, "stylesheet"), NoExampleFound)
    assert isinstance(find_examples(store, HTML_SCOPE, "computed style"), NoExampleFound)


@pytest.mark.parametrize("query", ["save", "open", "save it", "open it", "save and open"])
def test_boilerplate_queries_do_not_admit_a_generic_titled_example_through_an_unrelated_lead_in(
    tmp_path: Path, query: str
) -> None:
    store = _publish(
        tmp_path / "m",
        [
            _chunk(
                "Quick Start",
                "Parse markup, open the file and save the result",
                "doc = open_file()\ndoc.save()",
            ),
            _chunk("Quick Start (2)", "Open a document and save a copy", "doc.open()\ndoc.save()"),
            _chunk("Read Encodings From Bytes", "Detect the encoding of raw bytes", "detect(raw)"),
        ],
    )
    assert isinstance(find_examples(store, HTML_SCOPE, query), NoExampleFound)


def test_a_multi_term_query_sharing_one_lead_in_word_does_not_admit_the_example(tmp_path: Path) -> None:
    store = _publish(
        tmp_path / "m",
        [
            _chunk("Quick Start", "Create a workbook, write values, style a cell, and save it"),
            _chunk("Read Encodings From Bytes", "Detect the encoding of raw bytes", "detect(raw)"),
        ],
    )
    assert isinstance(
        find_examples(store, HTML_SCOPE, "delete a worksheet from the workbook"), NoExampleFound
    )


# --- RD2-143 / RD2-203 against the real, committed corpora --------------------------------------


# TC-848: the regenerated html/python chunk is titled by its real subject instead of the README heading "Quick Start (2)".
CASCADE_TITLE = (
    "Attach a stylesheet and resolve the computed style — the ID selector wins over the class rule"
)


@pytest.mark.parametrize("query", ["stylesheet", "computed style", "attach stylesheet"])
def test_rd2_143_html_python_css_cascade_example_is_found_by_its_subject(tmp_path: Path, query: str) -> None:
    store = _real_store(tmp_path, "html__python")
    result = find_examples(store, HTML_SCOPE, query)
    assert _titles(result)[0] == CASCADE_TITLE, query
    assert "attach_style_sheet" in result[0].snippet
    assert result[0].match_basis == MATCH_BASIS_TITLE_WORD
    assert result[0].confidence == "confident"


def test_rd2_143_a_three_word_query_finds_it_too_judged_by_the_coverage_floor(tmp_path: Path) -> None:
    store = _real_store(tmp_path, "html__python")
    result = find_examples(store, HTML_SCOPE, "resolve the computed style")
    assert _titles(result) == [CASCADE_TITLE]


def test_rd2_143_css_still_finds_the_cascade_example(tmp_path: Path) -> None:
    store = _real_store(tmp_path, "html__python")
    assert _titles(find_examples(store, HTML_SCOPE, "css")) == [CASCADE_TITLE]


def test_rd2_143_the_other_html_python_generic_example_and_specific_titles_still_answer_as_before(
    tmp_path: Path,
) -> None:
    store = _real_store(tmp_path, "html__python")
    assert _titles(find_examples(store, HTML_SCOPE, "query elements by tag name")) == [
        "Query Elements By Tag Name"
    ]
    assert _titles(find_examples(store, HTML_SCOPE, "execute javascript")) == [
        "Execute JavaScript Against The DOM"
    ]
    assert _titles(find_examples(store, HTML_SCOPE, "serialize dom"))[0] == "Quick Start"


def test_rd2_203_pdf_net_boilerplate_queries_still_do_not_admit_the_installation_example(
    tmp_path: Path,
) -> None:
    """The RD2-203 queries ``save`` and ``hello world`` name no word of the example's lead-in ("This example
    demonstrates creating a new PDF document with a text fragment and opening an existing PDF to extract its
    text content"), so the lead-in cannot answer them - they stay honest misses, as the title-anchor
    invariant requires for ``save``. The heading words (``quick start``, ``install``) keep answering."""
    scope = Scope(family="pdf", platform="net")
    store = _real_store(tmp_path, "pdf__net")
    for query in ("save", "hello world"):
        assert isinstance(find_examples(store, scope, query), NoExampleFound), query
    for query in ("quick start", "install", "dependencies"):
        assert _titles(find_examples(store, scope, query)) == ["Installation, Dependencies, and Quick Start"]


def test_rd2_203_pdf_net_installation_lead_in_is_too_long_to_stand_in_for_a_title(tmp_path: Path) -> None:
    store = _real_store(tmp_path, "pdf__net")
    chunks = _generic_chunks(store, Scope(family="pdf", platform="net"))
    assert len(chunks) == 1
    assert len(example_lead_in(chunks[0]).split()) > LEAD_IN_MAX_WORDS
    assert not lead_in_anchored("extract text", "pdf", chunks[0])


def test_a_generic_titled_example_is_found_by_its_lead_in_in_pdf_cpp(tmp_path: Path) -> None:
    scope = Scope(family="pdf", platform="cpp")
    store = _real_store(tmp_path, "pdf__cpp")
    result = find_examples(store, scope, "extract text from a pdf")
    assert _titles(result)[0] == "Quick Start"
    assert "extract text" in example_lead_in(_generic_chunks(store, scope)[0])


# --- the 32 real pilots: the measured census and the byte-for-byte guarantee for specific titles --


def _all_example_chunks(tmp_path: Path) -> list[tuple[str, str, str]]:
    root = tmp_path / "all"
    for directory in DEMO_MANIFESTS.glob("*__self_extracted"):
        shutil.copytree(directory, root / directory.name)
    store = GenerationManifestStore(root)
    rows: list[tuple[str, str, str]] = []
    for directory in sorted(root.glob("*__self_extracted")):
        family, platform, _ = directory.name.split("__")
        for document in _documents(store, Scope(family=family, platform=platform)).values():
            text = document["text"]
            if chunk_kind(text) == "example":
                rows.append((family, platform, text))
    return rows


def test_the_real_corpus_census_of_generic_titled_examples(tmp_path: Path) -> None:
    rows = _all_example_chunks(tmp_path)
    # TC-848 re-pin (cohort C1 regenerated: authored/real titles replaced 12 README placeholders such as "Quick Start (2)" and
    # "Additional Examples (3)" in 8 pilots, and the cohort gained 24 examples): 280 -> 304 examples, 46 -> 34 generic titled,
    # 24 -> 22 pilots (email/python and note/python have none left), 40 -> 28 that can anchor on a short lead-in.
    # TC-849 re-pin (cohort C2, the six .NET pilots, regenerated: README examples whose run failed or printed nothing are no
    # longer published and the pilots' authored examples now carry the run): 304 -> 300 examples (3d/net 5 -> 6, cells/net 13 -> 11,
    # email/net 9 -> 7, pdf/net 8 -> 8, slides/net 13 -> 12, words/net 9 -> 9), 34 -> 30 generic titled (email/net "Quick Start",
    # cells/net "Quick Start (2)" and "(3)" and 3d/net "Additional Examples" are gone), 22 -> 20 pilots (email/net and 3d/net have
    # none left), 28 -> 24 that can anchor on a short lead-in. cells/net then regained its two PDF-export README examples once the .NET
    # verifier shipped the SkiaSharp native asset (TC-867), so 300 -> 302 examples; the generic-title counts are unchanged.
    # TC-850 re-pin (cohort C3, the four Java pilots, regenerated: README examples whose run failed or printed nothing are no
    # longer published): 302 -> 296 examples (pdf/java 7 -> 8, slides/java 16 -> 13, cells/java 8 -> 8, 3d/java 8 -> 4), 30 -> 28 generic
    # titled (3d/java "Quick Start (2)" and cells/java "Quick Start (2)" are gone), still 20 pilots, 24 -> 22 that can anchor on a short
    # lead-in (both removed examples had one).
    # TC-851 re-pin (cohort C4, the three TypeScript pilots, regenerated: README examples whose run failed or printed nothing are no
    # longer published; one authored example per pilot restores an answer): 296 -> 291 examples (pdf/typescript 9 -> 7, cells/typescript
    # 8 -> 6, 3d/typescript 8 -> 7), 28 -> 25 generic titled (3d/typescript "Quick Start" and cells/typescript "Quick Start" and
    # "Quick Start (2)" are gone; the authored cells/typescript "Quick Start: save Products in XLSX format, update and reload" is not a
    # generic title), 20 -> 18 pilots (3d/typescript and cells/typescript have none left), 22 -> 20 that can anchor on a short lead-in.
    # TC-852 re-pin (cohort C5, the last cohort: pdf/go, cells/go, cells/rust, pdf/cpp, cells/cpp, slides/cpp and email/cpp, regenerated:
    # README examples whose run failed or printed nothing are no longer published; authored examples restore two answers): 291 -> 290
    # examples (cells/go 13 -> 12; pdf/go 11 -> 11 and pdf/cpp 8 -> 8 with an authored example each for the dropped "Fill AcroForm Fields" and
    # "Open a Password-Protected Document"; email/cpp 7 -> 7), 25 -> 23 generic titled (cells/go "Quick Start (2)" is gone, and email/cpp
    # "Quick Start (2)" is replaced by the README example "Create a message and save it as both MSG and EML" (no longer a placeholder title)), still 18 pilots (cells/go and
    # email/cpp keep "Quick Start"), 20 -> 18 that can anchor on a short lead-in (both removed examples had one).
    assert len(rows) == 290
    generic = [r for r in rows if is_generic_example_title((extract_fqn(r[2]) or "")[len(EXAMPLE_PREFIX) :])]
    assert len(generic) == 23
    assert len({(family, platform) for family, platform, _ in generic}) == 18
    # Every generic-titled example the corpus holds has a lead-in sentence but one (slides/net "Quick start").
    without = [r for r in generic if not example_lead_in(r[2])]
    assert [(f, p) for f, p, _ in without] == [("slides", "net")]
    # 18 of the 23 (was 20 of 25, 22 of 28, 24 of 30, 28 of 34, 40 of 46) name one subject in at most LEAD_IN_MAX_WORDS words and can anchor.
    short = [r for r in generic if 0 < len(example_lead_in(r[2]).split()) <= LEAD_IN_MAX_WORDS]
    assert len(short) == 18


QUERIES = (
    "stylesheet",
    "computed style",
    "extract text",
    "create a workbook",
    "export to pdf",
    "load an existing workbook",
    "convert",
    "save",
    "open",
    "table",
    "watermark annotation",
)


def test_no_specific_titled_example_in_any_pilot_ever_anchors_through_a_lead_in(tmp_path: Path) -> None:
    for family, _platform, text in _all_example_chunks(tmp_path):
        if is_generic_example_title((extract_fqn(text) or "")[len(EXAMPLE_PREFIX) :]):
            continue
        for query in QUERIES:
            assert not lead_in_anchored(query, family, text), (family, extract_fqn(text), query)


def test_a_generic_lead_in_anchor_always_needs_every_distinctive_term_in_the_sentence(tmp_path: Path) -> None:
    from foss_mcp.indexing.lexical_index_writer import tokenize
    from foss_mcp.mcp.tools.relevance import distinctive_terms, present_terms

    for family, _platform, text in _all_example_chunks(tmp_path):
        if not is_generic_example_title((extract_fqn(text) or "")[len(EXAMPLE_PREFIX) :]):
            continue
        lead_in = example_lead_in(text)
        for query in QUERIES:
            if lead_in_anchored(query, family, text):
                terms = distinctive_terms(query, family)
                assert terms and present_terms(terms, tokenize(lead_in)) == terms
                assert len(lead_in.split()) <= LEAD_IN_MAX_WORDS
