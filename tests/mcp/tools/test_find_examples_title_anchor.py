"""G3/TC-543 (REQ-G3-008, REQ-G3-001): a one- or two-word find_examples query is answered only by an
example whose TITLE contains one of its words.

Before this rule a short query was satisfied by any example whose CODE used the word: ``save`` returned
three PDF examples (Split and Merge, Encrypt with Permissions, Fill AcroForm Fields) none of them about
saving, at ``coverage`` 1.0, which reads as a perfect match. Measured with the real function over the 40
committed pilots on main d7b594fd: the eight generic words ``save open create page add text document
load`` returned 443 examples before the rule and 64 after (``save`` 102 -> 17), and ``set password``
returned 7 examples and now returns none. The totals are stated here and NOT asserted, because the data
refresh changes the examples; what is asserted is the property (every returned example is anchored).

Offline and deterministic: synthetic generations are published through the real ``publish_generation``;
the real-data tests read a COPY of the committed ``infra/demo-manifests`` (the repository is never
written) with the embedding gateway replaced by a provider whose ``embed`` raises, as the standalone
image behaves. No clock, no network, no skip.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.publisher import publish_generation
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools import find_examples as find_examples_module
from foss_mcp.mcp.tools.find_examples import ExampleMatch, NoExampleFound, find_examples
from foss_mcp.mcp.tools.relevance import (
    TITLE_ANCHOR_GRADUATED_MAX_TERMS,
    TITLE_ANCHOR_MAX_TERMS,
    distinctive_terms,
    title_anchored,
)
from foss_mcp.mcp.tools.vector_guard import _set_guard
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document
from tests.indexing.test_index_writers import DeterministicEmbeddingProvider
from tests.mcp.tools.test_forgiveness_acceptance import (
    EXAMPLE_ROWS,
    MAX_VERBOSE_WRONG,
    MIN_CONCISE_HITS,
    MIN_VERBOSE_HITS,
    PILOTS,
    _directory_of,
    _example_counts,
    _scope_of,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"

SCOPE = Scope(family="pdf", platform="net")
COMMIT = "10a363830fea952c29df20fdb37d35ad3a881433"
EXAMPLE_PREFIX = "Example: "

GENERIC_WORDS = ("save", "open", "create", "page", "add", "text", "document", "load")

WATERMARK_TITLE = "Add a Watermark Annotation"
TEXT_TITLE = "Extract Text Fragments from a Page"


class _NoGatewayEmbeddingProvider:
    """What the standalone image has when no embedding gateway is configured: embedding fails."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("no embedding gateway in this deployment")


@pytest.fixture(autouse=True)
def _no_embedding_gateway(monkeypatch: pytest.MonkeyPatch) -> object:
    """Every test starts without a gateway and with a fresh vector-fallback circuit breaker."""
    monkeypatch.setattr(find_examples_module, "GatewayEmbeddingProvider", _NoGatewayEmbeddingProvider)
    _set_guard(None)
    yield None
    _set_guard(None)


# --- title_anchored, case by case ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "family", "title", "expected"),
    [
        ("save", "cells", "Create, Save, and Reload a Workbook", True),
        ("save", "pdf", "Fill AcroForm Fields", False),
        ("page dimensions", "pdf", "Inspect Document Page Dimensions", True),
        ("convert watermark", "pdf", "Add a Watermark Annotation", True),
        ("convert watermark", "pdf", "Fill AcroForm Fields", False),
        ("export", "pdf", "Save as PDF", True),
        ("pages", "pdf", "Page Dimensions", True),
        ("pdf save", "pdf", "PDF Basics", False),
        ("pdf save", "pdf", "Save a Copy", True),
        # TC-586: above TITLE_ANCHOR_MAX_TERMS (2) the query is still title-checked, now up to
        # TITLE_ANCHOR_GRADUATED_MAX_TERMS (4) - a three-term query with no title word anywhere in the
        # generation's titles is rejected, exactly the graduated protection this card adds.
        ("alpha beta gamma", "pdf", "Unrelated Title", False),
        ("alpha beta gamma", "pdf", "", False),
        ("alpha beta gamma delta", "pdf", "Unrelated Title", False),
        # Five terms is past the graduated ceiling: the coverage floor alone applies again, unchanged.
        ("alpha beta gamma delta epsilon", "pdf", "Unrelated Title", True),
        ("save", "pdf", "", False),
    ],
    ids=[
        "word-in-title",
        "word-only-in-code",
        "both-words-in-title",
        "second-word-only-in-title",
        "neither-word-in-title",
        "synonym-export-for-save",
        "plural-stems-to-the-title-word",
        "family-word-in-title-does-not-anchor",
        "family-word-ignored-other-word-anchors",
        "three-terms-unrelated-title-now-anchored",
        "three-terms-empty-title-now-anchored",
        "four-terms-at-the-graduated-ceiling-still-anchored",
        "five-terms-past-the-graduated-ceiling-escapes",
        "empty-title",
    ],
)
def test_title_anchored(query: str, family: str, title: str, expected: bool) -> None:
    assert title_anchored(query, family, title) is expected


def test_the_threshold_of_distinctive_terms_is_two() -> None:
    assert TITLE_ANCHOR_MAX_TERMS == 2


def test_the_graduated_ceiling_is_double_the_original_threshold() -> None:
    # TC-586: the unconditional escape above TITLE_ANCHOR_MAX_TERMS is gone. A query is still judged
    # against the title up to TITLE_ANCHOR_GRADUATED_MAX_TERMS distinctive terms; only past that higher
    # ceiling does the coverage floor alone decide, so a longer query is never LESS protected than a
    # shorter one - only, past a measured point, protected by a different mechanism.
    assert TITLE_ANCHOR_GRADUATED_MAX_TERMS == 2 * TITLE_ANCHOR_MAX_TERMS
    assert title_anchored("alpha beta", "pdf", "Unrelated") is False
    assert title_anchored("alpha beta gamma", "pdf", "Unrelated") is False
    assert title_anchored("alpha beta gamma delta", "pdf", "Unrelated") is False
    assert title_anchored("alpha beta gamma delta epsilon", "pdf", "Unrelated") is True


# --- synthetic generations ----------------------------------------------------------------------


def _example(title: str, code: str) -> str:
    return f"FQN: Example: {title}\nKind: verified_example\nExample:\n{code}\nSource-Commit: {COMMIT}"


SYMBOL_CHUNK = f"FQN: Aspose.Pdf.Document\nKind: class_declaration\nA document.\nSource-Commit: {COMMIT}"

# Both examples' CODE contains the words save, page, text and watermark; only the title differs.
WATERMARK_EXAMPLE = _example(
    WATERMARK_TITLE,
    "page = document.page\ntext = watermark\ndocument.save(text, page, watermark)",
)
TEXT_EXAMPLE = _example(
    TEXT_TITLE,
    "page = document.page\ntext = watermark\ndocument.save(text, page, watermark)",
)
# Five distinctive words that appear in the code of an example whose title ("Inspect Layout Metrics")
# says none of them - used to probe every side of the TC-586 graduated ceiling (TITLE_ANCHOR_MAX_TERMS
# through TITLE_ANCHOR_GRADUATED_MAX_TERMS and just past it).
LAYOUT_EXAMPLE = _example("Inspect Layout Metrics", "alpha = beta + gamma\ndelta = epsilon")


def _publish(root: Path, texts: list[str]) -> GenerationManifestStore:
    """Publish one generation whose chunks have exactly *texts*, embedded 8-dimensionally."""
    store = GenerationManifestStore(root)
    body = "\n\n".join(f"## section {index}\n\n{text}" for index, text in enumerate(texts))
    doc = make_document(
        source_kind=SourceKind.SELF_EXTRACTED,
        content_type="api_surface",
        provenance=Provenance(repository="aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET", commit=COMMIT),
        evidence_refs=(),
        title="pdf/net synthetic",
        body=body,
    )
    chunks = chunk_document(doc)
    assert [chunk.text for chunk in chunks] == texts
    lease = store.acquire_lease("pdf::net::self_extracted", "worker-1", "pending")
    publish_generation(
        store,
        family="pdf",
        platform="net",
        source_kind="self_extracted",
        expected_active=None,
        chunks=chunks,
        embedding_provider=DeterministicEmbeddingProvider(),
        lease=lease,
    )
    return store


@pytest.fixture
def two_examples(tmp_path: Path) -> GenerationManifestStore:
    return _publish(tmp_path / "m", [SYMBOL_CHUNK, WATERMARK_EXAMPLE, TEXT_EXAMPLE])


def _titles(result: object) -> list[str]:
    assert isinstance(result, list), result
    return [match.fqn[len(EXAMPLE_PREFIX) :] for match in result]


def test_a_short_query_needs_a_title_word_in_a_synthetic_generation(
    two_examples: GenerationManifestStore,
) -> None:
    # Every one of these words is in the code of both examples, so only the title rule tells them apart.
    assert isinstance(find_examples(two_examples, SCOPE, "save"), NoExampleFound)
    assert _titles(find_examples(two_examples, SCOPE, "watermark")) == [WATERMARK_TITLE]
    assert _titles(find_examples(two_examples, SCOPE, "page")) == [TEXT_TITLE]
    assert _titles(find_examples(two_examples, SCOPE, "text")) == [TEXT_TITLE]


def test_a_two_word_query_needs_one_title_word(two_examples: GenerationManifestStore) -> None:
    assert _titles(find_examples(two_examples, SCOPE, "save watermark")) == [WATERMARK_TITLE]
    assert _titles(find_examples(two_examples, SCOPE, "save text")) == [TEXT_TITLE]
    # Three distinctive terms, still title-checked under TC-586 (3 <= TITLE_ANCHOR_GRADUATED_MAX_TERMS):
    # "watermark" anchors WATERMARK_TITLE and "text" anchors TEXT_TITLE, so both answer - not because the
    # rule has switched off, but because each title genuinely contains one of the three terms.
    assert sorted(_titles(find_examples(two_examples, SCOPE, "save text watermark"))) == sorted(
        [WATERMARK_TITLE, TEXT_TITLE]
    )


def test_a_query_without_a_distinctive_term_is_the_honest_miss(
    two_examples: GenerationManifestStore,
) -> None:
    for query in ("pdf examples", "Example:", "pdf sample"):
        assert isinstance(find_examples(two_examples, SCOPE, query), NoExampleFound), query


def test_a_query_past_the_graduated_ceiling_is_judged_by_the_coverage_floor_alone(tmp_path: Path) -> None:
    store = _publish(tmp_path / "m", [SYMBOL_CHUNK, LAYOUT_EXAMPLE])
    # Two distinctive terms: the title rule applies (unchanged since TC-543) and rejects it.
    assert isinstance(find_examples(store, SCOPE, "alpha beta"), NoExampleFound)
    # TC-586: three and four distinctive terms are now STILL title-checked - none of them is a word of
    # "Inspect Layout Metrics", so where the pre-TC-586 unconditional escape would have answered, the
    # graduated rule now rejects both, exactly the slides/cpp bug this card fixes reproduced synthetically.
    assert isinstance(find_examples(store, SCOPE, "alpha beta gamma"), NoExampleFound)
    assert isinstance(find_examples(store, SCOPE, "alpha beta gamma delta"), NoExampleFound)
    # Five distinctive terms is past TITLE_ANCHOR_GRADUATED_MAX_TERMS: the coverage floor alone decides
    # again, exactly as every term count above TITLE_ANCHOR_MAX_TERMS did before this card.
    result = find_examples(store, SCOPE, "alpha beta gamma delta epsilon")
    assert _titles(result) == ["Inspect Layout Metrics"]


def test_the_exact_title_branch_is_unchanged(two_examples: GenerationManifestStore) -> None:
    result = find_examples(two_examples, SCOPE, f"{EXAMPLE_PREFIX}{TEXT_TITLE}")
    assert isinstance(result, list)
    assert [(match.source, match.coverage, match.match_basis) for match in result] == [
        ("exact", 1.0, "exact_title")
    ]
    assert _titles(result) == [TEXT_TITLE]


class _RecordingProvider:
    """A fake gateway whose query embedding is exactly one stored example point's own vector."""

    def __init__(self, vector: tuple[float, ...], seen: list[str]) -> None:
        self._vector = vector
        self._seen = seen

    def embed(self, texts: list[str]) -> list[tuple[float, ...]]:
        self._seen.extend(texts)
        return [self._vector for _ in texts]


def _point_vector(store: GenerationManifestStore, title: str) -> tuple[float, ...]:
    key = "pdf::net::self_extracted"
    manifest = store.read_generation(key, store.read_active(key) or "")
    points = [p for p in manifest.payload["vector_index"]["points"] if f"FQN: Example: {title}" in p["text"]]
    assert len(points) == 1
    return tuple(points[0]["vector"])


def test_the_vector_fallback_is_unchanged_and_runs_for_a_short_query_that_the_title_rule_rejected(
    monkeypatch: pytest.MonkeyPatch, two_examples: GenerationManifestStore
) -> None:
    seen: list[str] = []
    vector = _point_vector(two_examples, WATERMARK_TITLE)
    monkeypatch.setattr(
        find_examples_module, "GatewayEmbeddingProvider", lambda: _RecordingProvider(vector, seen)
    )
    # "save" is in the code of both examples (the old lexical stage would have answered) and in no title.
    result = find_examples(two_examples, SCOPE, "save")
    assert seen == ["save"]
    assert isinstance(result, list) and result
    assert {match.source for match in result} == {"vector"}
    assert result[0].fqn == f"{EXAMPLE_PREFIX}{WATERMARK_TITLE}"
    assert result[0].coverage == pytest.approx(1.0)
    assert {match.match_basis for match in result} == {"embedding_similarity"}


def test_the_vector_fallback_does_not_run_when_the_title_rule_accepted_an_example(
    monkeypatch: pytest.MonkeyPatch, two_examples: GenerationManifestStore
) -> None:
    seen: list[str] = []
    monkeypatch.setattr(
        find_examples_module, "GatewayEmbeddingProvider", lambda: _RecordingProvider((1.0,) * 8, seen)
    )
    result = find_examples(two_examples, SCOPE, "watermark")
    assert seen == []
    assert {match.source for match in result} == {"lexical"}  # type: ignore[union-attr]


def test_match_basis_is_set_per_branch(
    monkeypatch: pytest.MonkeyPatch, two_examples: GenerationManifestStore, tmp_path: Path
) -> None:
    exact = find_examples(two_examples, SCOPE, f"{EXAMPLE_PREFIX}{WATERMARK_TITLE}")
    short = find_examples(two_examples, SCOPE, "watermark")
    longer_store = _publish(tmp_path / "longer", [SYMBOL_CHUNK, LAYOUT_EXAMPLE])
    # Five distinctive terms: past TITLE_ANCHOR_GRADUATED_MAX_TERMS, so the coverage floor alone applies.
    longer = find_examples(longer_store, SCOPE, "alpha beta gamma delta epsilon")
    monkeypatch.setattr(
        find_examples_module,
        "GatewayEmbeddingProvider",
        lambda: _RecordingProvider(_point_vector(two_examples, TEXT_TITLE), []),
    )
    vector = find_examples(two_examples, SCOPE, "save")
    bases = {
        "exact": exact,
        "short": short,
        "longer": longer,
        "vector": vector,
    }
    assert {name: {m.match_basis for m in result} for name, result in bases.items()} == {  # type: ignore[union-attr]
        "exact": {"exact_title"},
        "short": {"title_word_and_example_text"},
        "longer": {"query_terms_in_example_text"},
        "vector": {"embedding_similarity"},
    }


def test_match_basis_defaults_to_empty_for_a_hand_built_match() -> None:
    match = ExampleMatch(SCOPE, "generation", "Example: x", "code", coverage=1.0, source="lexical")
    assert match.match_basis == ""


def test_match_basis_is_the_last_field() -> None:
    names = list(ExampleMatch.__dataclass_fields__)
    # TC-544 appended three additive fields (language, source_commit, verification) after match_basis,
    # so match_basis is no longer last; it still follows provenance and the earlier fields unchanged.
    # TC-584 appended one more additive field (confidence) after verification.
    assert names[-6:] == [
        "provenance",
        "match_basis",
        "language",
        "source_commit",
        "verification",
        "confidence",
    ]
    assert names[:-5] == [
        "scope",
        "generation_id",
        "fqn",
        "snippet",
        "coverage",
        "source",
        "found",
        "provenance",
    ]


@pytest.mark.parametrize(
    "basis",
    ["exact_title", "title_word_and_example_text", "query_terms_in_example_text", "embedding_similarity"],
)
def test_the_coverage_docstring_names_every_match_basis(basis: str) -> None:
    assert basis in (ExampleMatch.__doc__ or "")


# --- over a copy of the committed generations of the 32 pilots -----------------------------------


class _Stores:
    """Lazily copies each pilot's committed scope directory and opens a store over the copy."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._by_directory: dict[str, GenerationManifestStore] = {}

    def of(self, directory: str) -> GenerationManifestStore:
        if directory not in self._by_directory:
            shutil.copytree(DEMO_MANIFESTS / directory, self._root / directory)
            self._by_directory[directory] = GenerationManifestStore(self._root)
        return self._by_directory[directory]


@pytest.fixture(scope="module")
def stores(tmp_path_factory: pytest.TempPathFactory) -> _Stores:
    return _Stores(tmp_path_factory.mktemp("title_anchor"))


def _answers(stores_: _Stores, query: str) -> list[tuple[str, ExampleMatch]]:
    """Every (pilot, match) the real find_examples returns for *query* over the 32 pilots."""
    found: list[tuple[str, ExampleMatch]] = []
    for directory in PILOTS:  # PILOTS are the committed scope directories, e.g. pdf__net__self_extracted
        result = find_examples(stores_.of(directory), _scope_of(directory), query, top_k=50)
        if isinstance(result, list):
            found.extend((directory, match) for match in result)
    return found


@pytest.fixture(scope="module")
def generic_word_answers(stores: _Stores) -> dict[str, list[tuple[str, ExampleMatch]]]:
    # Provider replacement for a module-scoped fixture (the autouse one is function scoped).
    original = find_examples_module.GatewayEmbeddingProvider
    find_examples_module.GatewayEmbeddingProvider = _NoGatewayEmbeddingProvider  # type: ignore[misc]
    _set_guard(None)
    try:
        return {word: _answers(stores, word) for word in GENERIC_WORDS}
    finally:
        find_examples_module.GatewayEmbeddingProvider = original  # type: ignore[misc]
        _set_guard(None)


def test_there_are_thirty_two_pilots() -> None:
    # re-pinned by TC-551: 40 -> 32
    assert len(PILOTS) == 32


@pytest.mark.parametrize("word", GENERIC_WORDS)
def test_generic_one_word_queries_return_only_title_anchored_examples(
    word: str, generic_word_answers: dict[str, list[tuple[str, ExampleMatch]]]
) -> None:
    answers = generic_word_answers[word]
    assert answers, f"{word!r} returns no example on any pilot, so this test would be vacuous"
    for pilot, match in answers:
        title = match.fqn[len(EXAMPLE_PREFIX) :]
        assert match.fqn.startswith(EXAMPLE_PREFIX), (pilot, match.fqn)
        assert title_anchored(word, _scope_of(pilot).family, title), (pilot, word, match.fqn)
        assert match.match_basis == "title_word_and_example_text", (pilot, word, match.fqn)


def test_pdf_net_watermark_and_pdf_java_page_dimensions_still_answer_as_the_live_tests_pin(
    stores: _Stores,
) -> None:
    net = _directory_of("pdf/net")
    watermark = find_examples(stores.of(net), _scope_of(net), "watermark")
    assert isinstance(watermark, list)
    assert any("Add a Watermark Annotation" in match.fqn for match in watermark[:3])
    java = _directory_of("pdf/java")
    dimensions = find_examples(stores.of(java), _scope_of(java), "page dimensions")
    assert isinstance(dimensions, list)
    assert any("Inspect Document Page Dimensions" in match.fqn for match in dimensions[:3])


def test_the_slides_cpp_export_format_reproduction_is_fixed(stores: _Stores) -> None:
    """TC-586's real reproduction, re-confirmed against the committed infra/demo-manifests on
    2026-10-26 (commit 469ce77a07b988c49deb930632b8276c89ec6b66): slides/cpp has exactly two verified
    examples, "Comments and Replies" and "Document Properties", and BOTH examples' code ends with the
    same boilerplate call, ``pres.save(..., SaveFormat::PPTX)`` - it has nothing to do with either
    example's real topic. Before this card, the three-distinctive-term query below (deck, export, pptx)
    hit title_anchored's unconditional escape (3 > the old TITLE_ANCHOR_MAX_TERMS of 2) and the coverage
    floor alone answered with BOTH unrelated examples, at coverage 1.0 and 0.67. Neither example answers
    a question about exporting to a format, so the honest result is a miss.
    """
    directory = _directory_of("slides/cpp")
    scope = _scope_of(directory)
    query = "export the deck to pptx"
    assert len(distinctive_terms(query, scope.family)) == 3
    for title in ("Comments and Replies", "Document Properties"):
        assert title_anchored(query, scope.family, title) is False, title
    result = find_examples(stores.of(directory), scope, query)
    assert isinstance(result, NoExampleFound), result


def test_a_synthetic_four_term_query_with_no_title_match_is_excluded(tmp_path: Path) -> None:
    """The second half of the card's requirement: a synthetic 4+ term query whose title genuinely
    contains none of its distinctive terms is correctly excluded by the new graduation, independent of
    the real slides/cpp data above."""
    store = _publish(tmp_path / "synthetic", [SYMBOL_CHUNK, LAYOUT_EXAMPLE])
    query = "alpha beta gamma delta"
    assert len(distinctive_terms(query, SCOPE.family)) == 4
    assert title_anchored(query, SCOPE.family, "Inspect Layout Metrics") is False
    assert isinstance(find_examples(store, SCOPE, query), NoExampleFound)


def test_the_tc516_example_tables_are_unchanged(stores: _Stores) -> None:
    assert len(EXAMPLE_ROWS) == 26
    concise = _example_counts(stores, 2)  # type: ignore[arg-type]
    assert concise["hit"] >= MIN_CONCISE_HITS and concise["wrong"] == 0, f"concise: {concise}"
    verbose = _example_counts(stores, 3)  # type: ignore[arg-type]
    assert verbose["hit"] >= MIN_VERBOSE_HITS and verbose["wrong"] <= MAX_VERBOSE_WRONG, f"verbose: {verbose}"


def test_set_password_returns_only_examples_with_a_title_word(stores: _Stores) -> None:
    # Before the rule "set password" returned 7 examples over the 40 pilots, none of them about
    # passwords (their code said "set" and "password" in unrelated places). The pilots whose example is
    # titled Password-Protect are not returned for it before or after: their code never says "set", so
    # the two-term coverage floor rejects them already. Every example returned now has set or password in
    # its title.
    for pilot, match in _answers(stores, "set password"):
        title = match.fqn[len(EXAMPLE_PREFIX) :].lower()
        assert re.search(r"\b(set|passwords?)\b", title), (pilot, match.fqn)
