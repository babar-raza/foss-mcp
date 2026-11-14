"""G3/TC-672 (Q10b, REQ-G3-011): a THIRD additive admission path for ``find_examples``'s lexical-
stage per-document gate, beside the existing title anchor (TC-543/TC-586), the bounded prefix title
match (TC-635) and the identifier/symbol code anchor (TC-635/TC-670) - never replacing or weakening
any of them, and never touching the coverage floor (``relevance.RELEVANCE_THRESHOLD``) itself.

Round-2's own #1 cross-cutting finding (``docs/round2/HANDOFF.md``) was still real after TC-635 and
TC-670: both of those only widen admission for IDENTIFIER-SHAPED or KNOWN-REAL-SYMBOL query words
(``Workbook``, ``WatermarkArtifact``). A plain, ordinary distinctive word that is genuinely
meaningful to a pilot's own small example corpus but is neither (``formula``) was still rejected
outright, even at ``guarded_coverage(...) == 1.0``.

REPRODUCED LIVE against the real, committed ``infra/demo-manifests`` cells/java data (not a
synthetic claim, and reproduced BEFORE this card's fix was written - see this card's own worker
report for the real failing run): ``find_examples(store, Scope("cells", "java"), "formula")``
returned ``NoExampleFound`` even though one of cells/java's 3 real example documents (``Example:
Additional Examples``, a ``FormatCondition``/``setFormula1``/``setFormula2`` example) tokenizes to
include ``formula`` in its own CODE SNIPPET. It was rejected solely because ``title_anchored`` is
False ("Additional Examples" shares no word with "formula"), ``title_prefix_anchored`` is False, and
the existing code anchor is False (``formula`` is not identifier-shaped and not a registered real
symbol short name, so neither ``required_terms`` nor ``known_symbol_names`` ever includes it).

Mechanism: a query's plain, distinctive term now ALSO anchors via the example's own CODE SNIPPET
specifically (the same ``_extract_example(text)`` + ``tokenize()`` scoping ``code_anchored`` already
uses) when it is RARE within THIS SCOPE's OWN example corpus - present in strictly fewer than half
of this scope's own example documents' own code (``2 * count < len(example_doc_ids)``, computed
fresh every ``find_examples`` call from this scope's own ``example_doc_ids`` alone). A match admitted
through this path ALONE (the narrower identifier/symbol anchor would not have admitted the same
document) carries the new, honestly-labelled ``MATCH_BASIS_RARE_CODE_WORD``; every existing
``MATCH_BASIS_CODE_WORD`` case keeps that exact label, unchanged.

Offline and deterministic: the synthetic generations below are published through the real
``publish_generation``, with the embedding gateway replaced by a provider whose ``embed`` raises, as
the standalone image behaves with no gateway configured; the real-data test reads a COPY of the
committed ``infra/demo-manifests`` (the repository itself is never written to). No clock, no
network, no skip.
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
    MATCH_BASIS_CODE_WORD,
    MATCH_BASIS_RARE_CODE_WORD,
    MATCH_BASIS_TITLE_WORD,
    NoExampleFound,
    find_examples,
)
from foss_mcp.mcp.tools.relevance import (
    required_terms,
    title_anchored,
    title_prefix_anchored,
)
from foss_mcp.mcp.tools.search_symbols import scope_key
from foss_mcp.mcp.tools.vector_guard import _set_guard
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document
from tests.indexing.test_index_writers import DeterministicEmbeddingProvider

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"

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
    like test_find_examples_code_anchor.py's own fixture of the same name."""
    monkeypatch.setattr(find_examples_module, "GatewayEmbeddingProvider", _NoGatewayEmbeddingProvider)
    _set_guard(None)
    yield None
    _set_guard(None)


def _example(title: str, code: str) -> str:
    return f"FQN: Example: {title}\nKind: verified_example\nExample:\n{code}\nSource-Commit: {COMMIT}"


def _example_with_prose(title: str, prose: str, code: str) -> str:
    """Like ``_example`` above, but with a prose line BEFORE the ``Example:`` marker -
    ``_extract_example`` only ever returns text AFTER the marker, so this prose is part of the
    chunk's document tokens (and so of ``guarded_coverage``'s denominator and the coverage floor
    check) but never part of the code tokens the code anchors are scoped to."""
    return (
        f"FQN: Example: {title}\nKind: verified_example\n{prose}\nExample:\n{code}\nSource-Commit: {COMMIT}"
    )


def _publish(root: Path, texts: list[str]) -> GenerationManifestStore:
    """Publish one generation whose chunks have exactly *texts*, embedded 8-dimensionally - the same
    helper shape test_find_examples_code_anchor.py's own ``_publish`` uses."""
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


# --- (a) the real cells/java "formula" case, against the real committed demo-manifests -----------


def _real_store(tmp_path: Path) -> GenerationManifestStore:
    root = tmp_path / "m"
    shutil.copytree(DEMO_MANIFESTS / "cells__java__self_extracted", root / "cells__java__self_extracted")
    return GenerationManifestStore(root)


def test_the_real_cells_java_formula_case_now_anchors_through_the_rare_code_word_path(
    tmp_path: Path,
) -> None:
    store = _real_store(tmp_path)
    scope = Scope(family="cells", platform="java")
    key = scope_key(scope, "self_extracted")
    generation_id = store.read_active(key)
    assert generation_id is not None
    documents = store.read_generation(key, generation_id).payload["lexical_index"]["documents"]
    titles = {
        (document.get("text") or "").splitlines()[0][len("FQN: ") :]
        for document in documents.values()
        if (document.get("text") or "").startswith("FQN: Example: ")
    }
    assert "Example: Additional Examples" in titles

    # None of the three pre-existing admission paths would have anchored this query - the gap the
    # card's own round-2 finding is built on, confirmed directly against the real functions.
    assert required_terms("formula") == frozenset()
    assert title_anchored("formula", "cells", "Additional Examples") is False
    assert title_prefix_anchored("formula", "cells", "Additional Examples") is False

    result = find_examples(store, scope, "formula")
    assert isinstance(result, list), result
    # TC-810: the authored-example campaign grew cells/java from 3 to 8 examples; a second one now
    # legitimately names the concept (its code reads ``total.getFormula()`` four times and says so in its
    # own output), so the answer is those TWO titles - the original "Additional Examples" (setFormula1/2)
    # plus the named-range example - both labelled as the loose rare-code-word match they are.
    assert sorted(_titles(result)) == [
        "Additional Examples",
        "Define a named range scoped to the file, with a comment, and confirm it after a reload",
    ]
    assert _bases(result) == {MATCH_BASIS_RARE_CODE_WORD}


# --- (b) a word present in the code of EVERY example document (3 of 3) is NOT admitted here -------


MAJORITY_A = _example("Load a Document", "doc = Document()\nretrieve(doc)\ndoc.save()")
MAJORITY_B = _example("Inspect Metadata", "meta = Metadata()\nretrieve(meta)\nmeta.save()")
MAJORITY_C = _example("Render a Page", "page = Page()\nretrieve(page)\npage.save()")


def test_a_word_present_in_every_example_document_s_code_is_not_admitted(tmp_path: Path) -> None:
    store = _publish(tmp_path / "m", [SYMBOL_CHUNK, MAJORITY_A, MAJORITY_B, MAJORITY_C])
    # No existing anchor fires either: the boilerplate-precision guard this mechanism must not break.
    assert required_terms("retrieve") == frozenset()
    for title in ("Load a Document", "Inspect Metadata", "Render a Page"):
        assert title_anchored("retrieve", "pdf", title) is False
        assert title_prefix_anchored("retrieve", "pdf", title) is False
    result = find_examples(store, SCOPE, "retrieve")
    assert isinstance(result, NoExampleFound), result


# --- (c) the exact-half boundary (1 of 2) is NOT admitted: strictly "< half", never "<=" ----------


HALF_PRESENT = _example("Render a Shape", "shape = Shape()\nboundary(shape)\nshape.save()")
HALF_ABSENT = _example("Create a Group", "group = Group()\ngroup.save()")


def test_a_word_present_in_exactly_half_the_example_documents_is_not_admitted(tmp_path: Path) -> None:
    store = _publish(tmp_path / "m", [SYMBOL_CHUNK, HALF_PRESENT, HALF_ABSENT])
    assert required_terms("boundary") == frozenset()
    assert title_anchored("boundary", "pdf", "Render a Shape") is False
    assert title_prefix_anchored("boundary", "pdf", "Render a Shape") is False
    result = find_examples(store, SCOPE, "boundary")
    assert isinstance(result, NoExampleFound), result


# --- (d) a rare word present only in PROSE/TITLE, never the code, is NOT admitted here -------------


PROSE_ONLY_RARE = _example_with_prose(
    "Introduction to Documents",
    "This covers a resize operation on the page.",
    "doc = Document()\ndoc.save()",
)
PROSE_PADDING = _example("Export a Page", "page = Page()\npage.save()")


def test_a_rare_word_present_only_in_prose_is_not_admitted(tmp_path: Path) -> None:
    store = _publish(tmp_path / "m", [SYMBOL_CHUNK, PROSE_ONLY_RARE, PROSE_PADDING])
    # The word is genuinely rare (absent from every example's own CODE, so trivially "< half"), and
    # genuinely present in the chunk as a whole (so guarded_coverage's floor clears) - only the
    # code-only scoping (reused from code_anchored) keeps it from admitting.
    assert required_terms("resize") == frozenset()
    assert title_anchored("resize", "pdf", "Introduction to Documents") is False
    assert title_prefix_anchored("resize", "pdf", "Introduction to Documents") is False
    result = find_examples(store, SCOPE, "resize")
    assert isinstance(result, NoExampleFound), result


# --- (e) a query already satisfied by an existing anchor is completely unaffected -----------------


TITLE_WORD_TITLE = "Add a Watermark Annotation"
TITLE_WORD_EXAMPLE = _example(TITLE_WORD_TITLE, "page = document.page\ndocument.save(page)")


def test_a_title_anchored_query_keeps_its_existing_basis_unchanged(tmp_path: Path) -> None:
    store = _publish(tmp_path / "m", [SYMBOL_CHUNK, TITLE_WORD_EXAMPLE])
    assert title_anchored("watermark", "pdf", TITLE_WORD_TITLE) is True
    result = find_examples(store, SCOPE, "watermark")
    assert _titles(result) == [TITLE_WORD_TITLE]
    assert _bases(result) == {MATCH_BASIS_TITLE_WORD}


CODE_ANCHOR_TITLE = "Assemble a Report Bundle"
CODE_ANCHOR_EXAMPLE = _example(CODE_ANCHOR_TITLE, "builder = WatermarkArtifact()\ndocument.add(builder)")


def test_an_identifier_code_anchored_query_keeps_its_existing_basis_unchanged(tmp_path: Path) -> None:
    store = _publish(tmp_path / "m", [SYMBOL_CHUNK, CODE_ANCHOR_EXAMPLE])
    # The pre-existing TC-635/TC-670 identifier anchor already admits this one on its own - the new
    # rarity-gated path must never relabel a match the narrower mechanism already fully explains.
    assert title_anchored("WatermarkArtifact", "pdf", CODE_ANCHOR_TITLE) is False
    assert title_prefix_anchored("WatermarkArtifact", "pdf", CODE_ANCHOR_TITLE) is False
    required = required_terms("WatermarkArtifact")
    assert required  # identifier-shaped: the existing mechanism this query already satisfies
    result = find_examples(store, SCOPE, "WatermarkArtifact")
    assert _titles(result) == [CODE_ANCHOR_TITLE]
    assert _bases(result) == {MATCH_BASIS_CODE_WORD}


def test_match_basis_rare_code_word_is_a_new_distinct_string() -> None:
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
        MATCH_BASIS_RARE_CODE_WORD,
    }
    assert len(bases) == 6


# --- TC-810: the scope-size cap is 12 (was 6), and one mention is enough for a term UNIQUE in the scope


def _padding_examples(count: int) -> list[str]:
    """*count* unrelated examples whose titles and code share no word with the queries below."""
    return [
        _example(f"Pad Topic {index}", f"pad{index} = Pad{index}()\npad{index}.run()")
        for index in range(count)
    ]


def test_the_scope_cap_is_twelve_examples_and_twelve_is_a_cliff(tmp_path: Path) -> None:
    unique_once = _example("Mount a Bracket", "bracket = Bracket()\nbracket.flange()\nbracket.run()")
    # 12 examples (the unique one + 11 pads): inside the cap, a one-time mention of a unique term answers.
    inside = _publish(tmp_path / "inside", [SYMBOL_CHUNK, unique_once, *_padding_examples(11)])
    result = find_examples(inside, SCOPE, "flange")
    assert _titles(result) == ["Mount a Bracket"]
    assert _bases(result) == {MATCH_BASIS_RARE_CODE_WORD}
    # 13 examples: past the cap the identical query is an honest miss again (the cliff slides/net and
    # slides/java sit behind: 13 and 16 examples).
    beyond = _publish(tmp_path / "beyond", [SYMBOL_CHUNK, unique_once, *_padding_examples(12)])
    assert isinstance(find_examples(beyond, SCOPE, "flange"), NoExampleFound)


def test_a_term_in_two_examples_must_still_recur_twice_in_the_one_that_answers(tmp_path: Path) -> None:
    once_a = _example("Mount a Bracket", "bracket = Bracket()\nbracket.flange()\nbracket.run()")
    once_b = _example("Mount a Rail", "rail = Rail()\nrail.flange()\nrail.run()")
    store = _publish(tmp_path / "m", [SYMBOL_CHUNK, once_a, once_b, *_padding_examples(6)])
    # "flange" is in the code of two examples, each mentions it once: not unique, not recurring - a miss.
    assert isinstance(find_examples(store, SCOPE, "flange"), NoExampleFound)
    twice = _example("Mount a Rail", "rail = Rail()\nrail.flange()\nrail.flange()\nrail.run()")
    store = _publish(tmp_path / "n", [SYMBOL_CHUNK, once_a, twice, *_padding_examples(6)])
    assert _titles(find_examples(store, SCOPE, "flange")) == ["Mount a Rail"]


def test_a_loose_rare_code_word_match_is_served_after_a_title_anchored_one(tmp_path: Path) -> None:
    # The code-only example names the word six times (a strong bm25 score); the title-anchored example
    # only in its title. The loose, body-only mention must not outrank the example the query names.
    loud = _example("Mount a Bracket", "\n".join("bracket.flange()" for _ in range(6)))
    titled = _example("Weld a Flange Joint", "joint = Joint()\njoint.run()")
    store = _publish(tmp_path / "m", [SYMBOL_CHUNK, loud, titled, *_padding_examples(5)])
    result = find_examples(store, SCOPE, "flange")
    assert _titles(result) == ["Weld a Flange Joint", "Mount a Bracket"]
    assert [match.match_basis for match in result] == [  # type: ignore[union-attr]
        MATCH_BASIS_TITLE_WORD,
        MATCH_BASIS_RARE_CODE_WORD,
    ]


def test_the_real_cells_java_scope_sits_in_the_widened_band(tmp_path: Path) -> None:
    # cells/java has 8 examples (cap 6 excluded it). Its "formula" test above is the pinned case; this
    # pins that the scope really is in the widened band, so the widening - not a data change - did it.
    store = _real_store(tmp_path)
    scope = Scope(family="cells", platform="java")
    key = scope_key(scope, "self_extracted")
    generation_id = store.read_active(key)
    assert generation_id is not None
    documents = store.read_generation(key, generation_id).payload["lexical_index"]["documents"]
    count = sum(1 for d in documents.values() if (d.get("text") or "").startswith("FQN: Example: "))
    assert 6 < count <= find_examples_module._RARE_CODE_WORD_MAX_SCOPE_EXAMPLES
