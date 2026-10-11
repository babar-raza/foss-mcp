"""G3/TC-381: documentation search, lookup composition and example search share ONE relevance rule.

Symbols are not documentation, and the product-family word ("pdf") cannot manufacture a match.
Unit tests pin the shared rule (``stem_token``, ``query_terms``, ``coverage``, ``chunk_kind``,
snippet extraction); the REAL-DATA tests copy committed demo-manifest scope directories into a
temporary directory and assert the QA cases that motivated the card against real chunk shapes.

Offline and deterministic: no network, no clock, nothing written inside the repository.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools import find_examples as find_examples_module
from foss_mcp.mcp.tools import lookup as lookup_module
from foss_mcp.mcp.tools import search_docs as search_docs_module
from foss_mcp.mcp.tools.find_examples import (
    ExampleMatch,
    NoExampleFound,
    _extract_example,
    find_examples,
)
from foss_mcp.mcp.tools.lookup import TaskAnswer, lookup
from foss_mcp.mcp.tools.relevance import (
    RELEVANCE_THRESHOLD,
    chunk_kind,
    coverage,
    query_terms,
    stem_token,
)
from foss_mcp.mcp.tools.search_docs import DocMatch, search_docs

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"

PDF_NET = Scope(family="pdf", platform="net")
PDF_GO = Scope(family="pdf", platform="go")
PDF_TYPESCRIPT = Scope(family="pdf", platform="typescript")
CELLS_CPP = Scope(family="cells", platform="cpp")
SLIDES_PYTHON = Scope(family="slides", platform="python")


def _real_store(tmp_path_factory: pytest.TempPathFactory, scope: Scope) -> GenerationManifestStore:
    """A store over a COPY of the committed demo-manifest scope directory (the repo is never written)."""
    directory = f"{scope.family}__{scope.platform}__self_extracted"
    root = tmp_path_factory.mktemp(f"relevance_{scope.family}_{scope.platform}")
    shutil.copytree(DEMO_MANIFESTS / directory, root / directory)
    return GenerationManifestStore(root)


@pytest.fixture(scope="module")
def net_store(tmp_path_factory: pytest.TempPathFactory) -> GenerationManifestStore:
    return _real_store(tmp_path_factory, PDF_NET)


@pytest.fixture(scope="module")
def go_store(tmp_path_factory: pytest.TempPathFactory) -> GenerationManifestStore:
    return _real_store(tmp_path_factory, PDF_GO)


@pytest.fixture(scope="module")
def typescript_store(tmp_path_factory: pytest.TempPathFactory) -> GenerationManifestStore:
    return _real_store(tmp_path_factory, PDF_TYPESCRIPT)


@pytest.fixture(scope="module")
def cpp_store(tmp_path_factory: pytest.TempPathFactory) -> GenerationManifestStore:
    return _real_store(tmp_path_factory, CELLS_CPP)


@pytest.fixture(scope="module")
def slides_store(tmp_path_factory: pytest.TempPathFactory) -> GenerationManifestStore:
    return _real_store(tmp_path_factory, SLIDES_PYTHON)


def _first_line(text: str) -> str:
    return text.splitlines()[0]


def _doc_match_texts(result: object) -> list[str]:
    """The text of every documentation match a tool result carries (empty for a miss)."""
    if isinstance(result, TaskAnswer):
        return [match.text for match in result.doc_matches]
    if isinstance(result, list):
        return [match.text for match in result if isinstance(match, DocMatch)]
    return []


def _example_fqns(result: object) -> list[str]:
    if isinstance(result, TaskAnswer):
        return [result.example.fqn] if result.example is not None else []
    if isinstance(result, list):
        return [match.fqn for match in result if isinstance(match, ExampleMatch)]
    return []


# ---- (a) unit tests: the shared rule -----------------------------------------------------------


@pytest.mark.parametrize(
    ("word", "stem"),
    [
        ("install", "install"),
        ("installs", "install"),
        ("installing", "install"),
        ("installation", "install"),
        ("Installations", "install"),
        ("pages", "page"),
        ("images", "image"),
        ("queries", "query"),
        ("boxes", "box"),
        ("classes", "class"),
        ("class", "class"),
        ("converted", "convert"),
        ("pdf", "pdf"),
        ("  Pages ", "page"),
    ],
)
def test_stem_token(word: str, stem: str) -> None:
    assert stem_token(word) == stem


def test_stem_token_keeps_short_words_whole() -> None:
    # 'ing' needs 3+ characters left, 'ed' needs a word longer than 4, a final 's' a word longer than 3.
    assert stem_token("king") == "king"
    assert stem_token("bed") == "bed"
    assert stem_token("bus") == "bus"


def test_query_terms_drops_the_family_word() -> None:
    assert query_terms("split a pdf into pages", "pdf") == frozenset({"split", "page"})


def test_query_terms_drops_generic_words() -> None:
    assert query_terms("Aspose FOSS library api for watermarks", "pdf") == frozenset({"watermark"})


def test_query_terms_stems_both_the_query_and_the_family() -> None:
    assert query_terms("installing cells", "cells") == frozenset({"install"})


def test_query_terms_falls_back_to_the_full_set_when_nothing_remains() -> None:
    assert query_terms("pdf", "pdf") == frozenset({"pdf"})
    assert query_terms("the api", "pdf") == frozenset({"api"})


def test_coverage_is_the_fraction_of_terms_present() -> None:
    assert coverage({"save", "document"}, ["document", "save"]) == 1.0
    assert coverage({"save", "document"}, ["document"]) == 0.5
    assert coverage({"save", "document"}, ["unrelated"]) == 0.0


def test_coverage_stems_the_document_side() -> None:
    assert coverage({"install"}, ["installation", "dependencies"]) == 1.0
    assert coverage({"page"}, ["pages"]) == 1.0


def test_coverage_of_no_terms_is_zero() -> None:
    assert coverage(frozenset(), ["anything"]) == 0.0


def test_relevance_threshold_is_the_measured_tc273_floor() -> None:
    assert RELEVANCE_THRESHOLD == 0.6
    # two terms: both must be present; three: at least two.
    assert coverage({"a", "b"}, ["a"]) < RELEVANCE_THRESHOLD
    assert coverage({"a", "b", "c"}, ["a", "b"]) >= RELEVANCE_THRESHOLD


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("FQN: Aspose.Pdf.Document\nKind: class", "symbol"),
        ("FQN: Example: Add a Watermark Annotation\nKind: verified_example\n\nExample:\ncode()", "example"),
        ("FQN: Doc: Installation, Dependencies, and Quick Start\nbody", "documentation"),
        ("Plain hand-written documentation prose with no FQN line.", "documentation"),
    ],
)
def test_chunk_kind(text: str, kind: str) -> None:
    assert chunk_kind(text) == kind


def test_extract_example_returns_the_code_after_the_last_example_line() -> None:
    text = (
        "FQN: Example: Demo\nKind: verified_example\nProse mentioning Example: inline.\n\n"
        "Example:\nfirst = 1\n\nsecond = 2\nSource-Commit: abc123"
    )
    assert _extract_example(text) == "first = 1\n\nsecond = 2"


def test_extract_example_uses_the_last_exact_example_line_and_strips_trailing_lines() -> None:
    text = "FQN: Example: Demo\nExample:\nnot code\nExample:\ncode()\n\nSource-Commit: a\n\n"
    assert _extract_example(text) == "code()"


def test_extract_example_without_an_exact_line_is_none() -> None:
    assert _extract_example("FQN: Example: Demo\nSee Example: inline only.") is None


# ---- (b) real-data tests ----------------------------------------------------------------------


def test_net_search_docs_document_returns_no_symbol_chunk(net_store: GenerationManifestStore) -> None:
    result = search_docs(net_store, PDF_NET, "Document", "developer_guide")

    assert isinstance(result, list) and result
    assert all(chunk_kind(match.text) == "documentation" for match in result)
    assert not any(chunk_kind(match.text) == "symbol" for match in result)


def test_net_lookup_save_a_document_returns_no_symbol_as_documentation(
    net_store: GenerationManifestStore,
) -> None:
    result = lookup(net_store, PDF_NET, "how do I save a document")

    texts = _doc_match_texts(result)
    assert not any(chunk_kind(text) == "symbol" for text in texts)
    assert not any(chunk_kind(text) == "example" for text in texts)


def test_cpp_lookup_save_a_document_has_no_doc_match_from_symbol_chunks(
    cpp_store: GenerationManifestStore,
) -> None:
    result = lookup(cpp_store, CELLS_CPP, "how do I save a document")

    assert not any(chunk_kind(text) != "documentation" for text in _doc_match_texts(result))


@pytest.mark.parametrize(
    "query", ["rotate a page", "convert a pdf to images", "how do I extract text from a pdf"]
)
def test_go_lookup_never_answers_with_split_and_merge_or_fill_acroform(
    go_store: GenerationManifestStore, query: str
) -> None:
    result = lookup(go_store, PDF_GO, query)

    assert not any("Example: Split and Merge PDFs" in text for text in _doc_match_texts(result))
    assert not any(chunk_kind(text) != "documentation" for text in _doc_match_texts(result))
    assert "Example: Fill AcroForm Fields" not in _example_fqns(result)


@pytest.mark.parametrize("query", ["rotate a page", "convert a pdf to images"])
def test_go_lookup_does_not_answer_with_the_installation_page(
    go_store: GenerationManifestStore, query: str
) -> None:
    result = lookup(go_store, PDF_GO, query)

    assert not any(
        _first_line(text).startswith("FQN: Doc: Installation") for text in _doc_match_texts(result)
    )


def test_go_find_examples_extract_text_is_not_fill_acroform(go_store: GenerationManifestStore) -> None:
    result = find_examples(go_store, PDF_GO, "how do I extract text from a pdf")

    assert "Example: Fill AcroForm Fields" not in _example_fqns(result)


def test_net_find_examples_split_a_pdf_is_not_the_watermark_example(
    net_store: GenerationManifestStore,
) -> None:
    result = find_examples(net_store, PDF_NET, "split a pdf into pages")

    assert "Example: Add a Watermark Annotation" not in _example_fqns(result)
    assert isinstance(result, NoExampleFound)


def test_typescript_find_examples_extract_text_is_not_create_a_new_pdf(
    typescript_store: GenerationManifestStore,
) -> None:
    result = find_examples(typescript_store, PDF_TYPESCRIPT, "how do I extract text from a pdf")

    assert "Example: Create a New PDF from Scratch" not in _example_fqns(result)


def test_net_find_examples_watermark_true_positive(net_store: GenerationManifestStore) -> None:
    result = find_examples(net_store, PDF_NET, "add a watermark to a PDF")

    assert isinstance(result, list) and result
    assert result[0].fqn == "Example: Add a Watermark Annotation"
    assert result[0].found is True
    assert result[0].coverage >= RELEVANCE_THRESHOLD


@pytest.mark.parametrize(("store_name", "scope"), [("net_store", PDF_NET), ("go_store", PDF_GO)])
def test_search_docs_install_finds_the_installation_page(
    request: pytest.FixtureRequest, store_name: str, scope: Scope
) -> None:
    store = request.getfixturevalue(store_name)

    result = search_docs(store, scope, "install", "getting_started")

    assert isinstance(result, list) and result
    assert all(match.content_type == "getting_started" for match in result)
    assert any(_first_line(match.text).startswith("FQN: Doc: Installation") for match in result)


def test_typescript_find_examples_convert_a_pdf_to_html_true_positive(
    typescript_store: GenerationManifestStore,
) -> None:
    result = find_examples(typescript_store, PDF_TYPESCRIPT, "convert a pdf to html")

    assert isinstance(result, list) and result
    assert result[0].fqn.startswith("Example: Convert a PDF to HTML")


def test_search_docs_never_returns_an_example_chunk_whatever_its_words(
    go_store: GenerationManifestStore,
) -> None:
    # "Fill AcroForm Fields" is a real go example; its words must not make it documentation.
    for content_type in ("getting_started", "developer_guide", "troubleshooting", "faq"):
        result = search_docs(go_store, PDF_GO, "fill acroform fields", content_type)
        assert not any(chunk_kind(text) != "documentation" for text in _doc_match_texts(result))


# ---- (c) snippets are the code alone ----------------------------------------------------------


def _all_lexical_example_snippets(store: GenerationManifestStore, scope: Scope, queries: list[str]):
    snippets = []
    for query in queries:
        result = find_examples(store, scope, query)
        if isinstance(result, list):
            snippets.extend(result)
    return snippets


def test_go_example_snippets_are_code_only(go_store: GenerationManifestStore) -> None:
    matches = _all_lexical_example_snippets(
        go_store,
        PDF_GO,
        ["Example: Fill AcroForm Fields", "split and merge pdfs", "encrypt with permissions"],
    )

    assert matches
    for match in matches:
        assert not match.snippet.startswith(("Example", "Kind:", "FQN:"))
        assert "Kind: verified_example" not in match.snippet
        assert "Source-Commit:" not in match.snippet
        assert "{{<" not in match.snippet
        assert match.snippet == match.snippet.rstrip()


def test_go_fill_acroform_snippet_is_exactly_the_code(go_store: GenerationManifestStore) -> None:
    result = find_examples(go_store, PDF_GO, "Example: Fill AcroForm Fields")

    assert isinstance(result, list) and len(result) == 1
    assert result[0].source == "exact"
    assert result[0].snippet.startswith('doc, _ := pdf.Open("template.pdf")')
    assert result[0].snippet.endswith('doc.Save("filled.pdf")')


def test_slides_python_example_snippets_are_code_only(slides_store: GenerationManifestStore) -> None:
    matches = _all_lexical_example_snippets(
        slides_store,
        SLIDES_PYTHON,
        ["Example: Format Text and Apply a Fill Effect", "Example: Create a Presentation and Add a Shape"],
    )

    assert len(matches) == 2
    for match in matches:
        assert match.snippet.startswith("import ")
        assert "Source-Commit:" not in match.snippet
        assert "Kind:" not in match.snippet
        assert "{{<" not in match.snippet


# ---- (d) the miss shape ------------------------------------------------------------------------


def test_no_example_found_carries_found_false_and_a_reason(net_store: GenerationManifestStore) -> None:
    result = find_examples(net_store, PDF_NET, "split a pdf into pages")

    assert isinstance(result, NoExampleFound)
    assert result.found is False
    assert isinstance(result.reason, str) and result.reason.strip()


def test_no_example_found_without_a_published_generation_says_so(tmp_path: Path) -> None:
    result = find_examples(GenerationManifestStore(tmp_path / "empty"), PDF_NET, "anything")

    assert isinstance(result, NoExampleFound)
    assert result.found is False
    assert result.reason == "no published generation for this scope"


def test_example_match_has_found_true_by_default() -> None:
    match = ExampleMatch(PDF_NET, "g", "Example: x", "code()", coverage=1.0, source="exact")

    assert match.found is True


# ---- wiring: the production tools actually use the shared rule ---------------------------------


@pytest.mark.parametrize("module", [search_docs_module, find_examples_module, lookup_module])
def test_each_tool_imports_the_shared_relevance_module(module: object) -> None:
    source = Path(module.__file__).read_text(encoding="utf-8")  # type: ignore[attr-defined]

    assert "from foss_mcp.mcp.tools.relevance import" in source


def test_the_tools_no_longer_keep_a_private_coverage_rule() -> None:
    source = Path(find_examples_module.__file__).read_text(encoding="utf-8")  # type: ignore[attr-defined]

    assert "_COVERAGE_THRESHOLD" not in source
    assert "RELEVANCE_THRESHOLD" in source
    assert "query_terms(" in source
