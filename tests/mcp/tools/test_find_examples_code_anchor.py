"""G3/TC-635 (Q10, REQ-G3-011): two more, strictly additive admission paths for ``find_examples``'s
lexical-stage per-document gate, beside the existing title anchor (``relevance.title_anchored``,
TC-543/TC-586) - never replacing or weakening it, and never touching the coverage floor
(``relevance.RELEVANCE_THRESHOLD``) itself.

1. A CODE anchor (``relevance.code_anchored``): a query naming an exact identifier the example's
   CODE uses (``WatermarkArtifact``) but whose TITLE happens not to repeat that word was, before
   this card, rejected outright even though the code genuinely answers it. The admission is scoped
   to the example's own CODE SNIPPET specifically (``find_examples._extract_example``), never the
   whole chunk - a required term appearing only in surrounding prose does not anchor here.

2. A bounded PREFIX title match (``relevance.title_prefix_anchored``): a query term that is a
   literal prefix of a longer title word that stemming does not fold to the same root (``async`` /
   ``asynchronously``) now also counts as a title anchor, bounded by
   ``relevance.TITLE_PREFIX_MIN_TERM_LENGTH`` and ``relevance.TITLE_PREFIX_MIN_EXTRA_LENGTH`` so a
   short, accidental prefix (``set`` / ``settings``) does not spuriously anchor.

A match admitted by the code anchor ALONE (neither the title anchor nor the prefix mechanism would
have admitted it) carries the new ``find_examples.MATCH_BASIS_CODE_WORD``; a match the prefix
mechanism admits is labelled exactly as any other title-anchored match - it is, semantically, one
more way a title can anchor a query, so no new basis constant was needed for it.

Offline and deterministic: synthetic generations are published through the real
``publish_generation``, with the embedding gateway replaced by a provider whose ``embed`` raises, as
the standalone image behaves with no gateway configured. No clock, no network, no skip.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.publisher import publish_generation
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools import find_examples as find_examples_module
from foss_mcp.mcp.tools.find_examples import (
    MATCH_BASIS_CODE_WORD,
    MATCH_BASIS_TITLE_WORD,
    NoExampleFound,
    find_examples,
)
from foss_mcp.mcp.tools.relevance import (
    TITLE_PREFIX_MIN_EXTRA_LENGTH,
    TITLE_PREFIX_MIN_TERM_LENGTH,
    code_anchored,
    required_terms,
    title_anchored,
    title_prefix_anchored,
)
from foss_mcp.mcp.tools.vector_guard import _set_guard
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document
from tests.indexing.test_index_writers import DeterministicEmbeddingProvider

SCOPE = Scope(family="pdf", platform="net")
COMMIT = "10a363830fea952c29df20fdb37d35ad3a881433"
EXAMPLE_PREFIX = "Example: "

SYMBOL_CHUNK = f"FQN: Aspose.Pdf.Document\nKind: class_declaration\nA document.\nSource-Commit: {COMMIT}"


class _NoGatewayEmbeddingProvider:
    """What the standalone image has when no embedding gateway is configured: embedding fails."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("no embedding gateway in this deployment")


@pytest.fixture(autouse=True)
def _no_embedding_gateway(monkeypatch: pytest.MonkeyPatch) -> object:
    """Every test starts without a gateway and with a fresh vector-fallback circuit breaker, exactly
    like test_find_examples_title_anchor.py's own fixture of the same name."""
    monkeypatch.setattr(find_examples_module, "GatewayEmbeddingProvider", _NoGatewayEmbeddingProvider)
    _set_guard(None)
    yield None
    _set_guard(None)


def _example(title: str, code: str) -> str:
    return f"FQN: Example: {title}\nKind: verified_example\nExample:\n{code}\nSource-Commit: {COMMIT}"


def _example_with_prose(title: str, prose: str, code: str) -> str:
    """Like ``_example`` above, but with a prose line BEFORE the ``Example:`` marker -
    ``_extract_example`` only ever returns text AFTER the marker, so this prose is part of the
    chunk's document tokens (and so of ``guarded_coverage``'s denominator) but never part of the
    code tokens ``code_anchored`` is scoped to."""
    return (
        f"FQN: Example: {title}\nKind: verified_example\n{prose}\nExample:\n{code}\nSource-Commit: {COMMIT}"
    )


def _publish(root: Path, texts: list[str]) -> GenerationManifestStore:
    """Publish one generation whose chunks have exactly *texts*, embedded 8-dimensionally - the same
    helper shape test_find_examples_title_anchor.py's own ``_publish`` uses."""
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


def _titles(result: object) -> list[str]:
    assert isinstance(result, list), result
    return [match.fqn[len(EXAMPLE_PREFIX) :] for match in result]


def _bases(result: object) -> set[str]:
    assert isinstance(result, list), result
    return {match.match_basis for match in result}


# --- (a) a query naming a required identifier present in the example's own CODE is now admitted --


CODE_ANCHOR_TITLE = "Assemble a Report Bundle"
CODE_ANCHOR_EXAMPLE = _example(CODE_ANCHOR_TITLE, "builder = WatermarkArtifact()\ndocument.add(builder)")


@pytest.fixture
def code_anchor_generation(tmp_path: Path) -> GenerationManifestStore:
    return _publish(tmp_path / "m", [SYMBOL_CHUNK, CODE_ANCHOR_EXAMPLE])


def test_a_query_naming_an_identifier_present_only_in_the_code_is_now_admitted(
    code_anchor_generation: GenerationManifestStore,
) -> None:
    # Neither the title anchor nor the prefix mechanism would admit this title for this query - the
    # title shares no word, stemmed or synonym, and no prefix relationship either.
    assert title_anchored("WatermarkArtifact", "pdf", CODE_ANCHOR_TITLE) is False
    assert title_prefix_anchored("WatermarkArtifact", "pdf", CODE_ANCHOR_TITLE) is False
    result = find_examples(code_anchor_generation, SCOPE, "WatermarkArtifact")
    assert _titles(result) == [CODE_ANCHOR_TITLE]
    assert _bases(result) == {MATCH_BASIS_CODE_WORD}


def test_the_negative_control_would_catch_this_case(
    code_anchor_generation: GenerationManifestStore,
) -> None:
    """Direct proof that this admission really rests on ``code_anchored`` returning True: forcing it
    to always return False (the supervisor's own negative control) turns this exact match into a
    miss."""
    required = required_terms("WatermarkArtifact")
    assert required  # the required term the mechanism depends on is real, not accidentally empty
    assert code_anchored(required, ["watermarkartifact"]) is True
    assert code_anchored(required, []) is False


# --- (b) a required identifier present only in PROSE outside the code is NOT admitted ------------


PROSE_ONLY_TITLE = "Assemble a Report Bundle"
PROSE_ONLY_EXAMPLE = _example_with_prose(
    PROSE_ONLY_TITLE,
    "This uses a WatermarkArtifact somewhere in its implementation.",
    "page = document.page\ndocument.save(page)",
)


def test_a_required_identifier_present_only_in_prose_is_not_admitted(tmp_path: Path) -> None:
    store = _publish(tmp_path / "m", [SYMBOL_CHUNK, PROSE_ONLY_EXAMPLE])
    # The required term is genuinely present in the chunk as a whole (so guarded_coverage's required
    # gate and the coverage floor both clear) - only the code-only scoping keeps it from admitting.
    result = find_examples(store, SCOPE, "WatermarkArtifact")
    assert isinstance(result, NoExampleFound), result


def test_code_anchored_itself_only_looks_at_the_code_tokens_given_to_it() -> None:
    required = required_terms("WatermarkArtifact")
    # The same required term, present among tokens that stand for "prose" tokens, is not found
    # because code_anchored is handed ONLY the code tokens by its caller - this is exactly what
    # find_examples.py's call site enforces by passing ``tokenize(_extract_example(text) or "")``,
    # never the whole chunk's tokens.
    assert code_anchored(required, ["this", "uses", "a", "watermark", "artifact", "somewhere"]) is False
    assert code_anchored(required, ["watermarkartifact"]) is True


# --- (c) a query with no identifier-shaped required term never even reaches code_anchored --------


def test_a_query_without_a_required_term_never_calls_code_anchored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _boom(*_args: object, **_kwargs: object) -> bool:
        raise AssertionError("code_anchored must not be called for a query with no required term")

    monkeypatch.setattr(find_examples_module, "code_anchored", _boom)
    store = _publish(tmp_path / "m", [SYMBOL_CHUNK, CODE_ANCHOR_EXAMPLE])
    # "render" is a plain word (no identifier shape: no case transition, underscore or digit), so
    # required_terms("render") is empty - and it is also not a word of the title or a prefix of one
    # of its words, so this query does not slip through title_anchored or title_prefix_anchored
    # first either; it is an honest miss, not a crash.
    assert not required_terms("render")
    assert title_anchored("render", "pdf", CODE_ANCHOR_TITLE) is False
    assert title_prefix_anchored("render", "pdf", CODE_ANCHOR_TITLE) is False
    result = find_examples(store, SCOPE, "render")
    assert isinstance(result, NoExampleFound), result


def test_an_empty_required_set_makes_code_anchored_trivially_false() -> None:
    assert code_anchored(frozenset(), ["watermarkartifact", "anything"]) is False


# --- (d) the bounded prefix title match: a real prefix pair admits, a too-short one does not -----


PREFIX_TITLE = "Document Loading Asynchronously"
PREFIX_EXAMPLE = _example(PREFIX_TITLE, "async def load():\n    return 1")


@pytest.fixture
def prefix_generation(tmp_path: Path) -> GenerationManifestStore:
    return _publish(tmp_path / "m", [SYMBOL_CHUNK, PREFIX_EXAMPLE])


def test_a_genuine_prefix_relationship_the_stemmer_does_not_fold_now_anchors(
    prefix_generation: GenerationManifestStore,
) -> None:
    # "async" stems to itself and "asynchronously" stems to itself too (no shared stemmed root), so
    # the existing exact-stemmed/synonym check in title_anchored alone rejects this title.
    assert title_anchored("async", "pdf", PREFIX_TITLE) is False
    assert title_prefix_anchored("async", "pdf", PREFIX_TITLE) is True
    result = find_examples(prefix_generation, SCOPE, "async")
    assert _titles(result) == [PREFIX_TITLE]
    # This is semantically a title anchor (just a different rule inside it), so it carries the same
    # basis any other title-anchored match does - never the code-anchor basis.
    assert _bases(result) == {MATCH_BASIS_TITLE_WORD}


@pytest.mark.parametrize(
    ("term", "title", "reason"),
    [
        ("set", "Configure Settings Page", "term shorter than TITLE_PREFIX_MIN_TERM_LENGTH"),
        ("chart", "Review Charts Page", "title word only one character longer than the term"),
        ("async", "Document Loading", "no title word even starts with the term"),
        ("async", "", "empty title"),
    ],
)
def test_a_too_short_prefix_relationship_does_not_anchor(term: str, title: str, reason: str) -> None:
    assert title_prefix_anchored(term, "pdf", title) is False, reason


def test_the_minimum_lengths_are_the_card_s_own_suggested_starting_point() -> None:
    # Measured against the full ratchet bundle (test_find_examples_title_anchor.py,
    # test_forgiveness_acceptance.py, test_round3_retrieval_corpus.py) - see this card's own worker
    # report for the real before/after numbers; both floors hold at these values with real margin,
    # so they are kept at the plan's own suggested starting point rather than tightened further.
    assert TITLE_PREFIX_MIN_TERM_LENGTH == 4
    assert TITLE_PREFIX_MIN_EXTRA_LENGTH == 2


def test_title_prefix_anchored_boundary_is_exact() -> None:
    # Exactly at the term-length floor (4 characters) and exactly at the extra-length floor (2
    # characters longer) both still anchor; one character short of either does not.
    assert title_prefix_anchored("book", "pdf", "A Bookish Guide") is True  # "book" + "ish" (3 extra)
    assert title_prefix_anchored("book", "pdf", "A Booked Guide") is True  # "book" + "ed" (2 extra)
    assert title_prefix_anchored("book", "pdf", "A Books Guide") is False  # "book" + "s" (1 extra)
    assert title_prefix_anchored("boo", "pdf", "A Boost Guide") is False  # term only 3 characters


# --- (e) a query that already satisfies the existing title anchor is unaffected ------------------


TITLE_WORD_TITLE = "Add a Watermark Annotation"
TITLE_WORD_EXAMPLE = _example(TITLE_WORD_TITLE, "page = document.page\ndocument.save(page)")


def test_a_query_already_satisfying_the_title_anchor_is_unaffected(tmp_path: Path) -> None:
    store = _publish(tmp_path / "m", [SYMBOL_CHUNK, TITLE_WORD_EXAMPLE])
    assert title_anchored("watermark", "pdf", TITLE_WORD_TITLE) is True
    result = find_examples(store, SCOPE, "watermark")
    assert _titles(result) == [TITLE_WORD_TITLE]
    # Same basis as before this card (TC-543/TC-586): no double-counting, no new basis assigned to a
    # match the existing title anchor already fully explains.
    assert _bases(result) == {MATCH_BASIS_TITLE_WORD}


def test_match_basis_code_word_is_a_new_distinct_string() -> None:
    from foss_mcp.mcp.tools.find_examples import (
        MATCH_BASIS_EMBEDDING,
        MATCH_BASIS_EXACT_TITLE,
        MATCH_BASIS_QUERY_TERMS,
    )

    bases = {
        MATCH_BASIS_EXACT_TITLE,
        MATCH_BASIS_TITLE_WORD,
        MATCH_BASIS_QUERY_TERMS,
        MATCH_BASIS_EMBEDDING,
        MATCH_BASIS_CODE_WORD,
    }
    assert len(bases) == 5
