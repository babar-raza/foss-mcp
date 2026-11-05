"""G3/TC-677 (REQ-G3-013, C3): ``search_docs.Miss`` carries a real, zero-inclusive per-content-type
documentation chunk count across the active generation - the honestly-buildable half of C3 (the "+
reason" half - distinguishing ``upstream_has_none`` from ``not_extracted`` - remains an explicit,
disclosed, not-yet-solvable limitation; see ``plans/TC-677.yaml``'s own scope note for why it is out
of this card).

``shared_fixture_documents`` below is the ONE fixture payload this file's own ``Miss`` tests and
``tests/mcp/test_identity_content_type_counts.py``'s ``DeploymentFacts`` tests both build their
input from (imported from there, the same cross-test-file convention
``tests/conformance/test_precision_and_size_budget.py`` already uses for
``tests.mcp.tools.test_nonsense_queries_miss.NONSENSE_QUERIES``) - proof that the two surfaces
agree on the SAME input, never two independently-constructed fixtures that could silently drift
apart.
"""

from __future__ import annotations

from types import SimpleNamespace

from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.relevance import DOCUMENTATION, chunk_kind
from foss_mcp.mcp.tools.search_docs import CONTENT_TYPES, Miss, classify_content_type, search_docs

SCOPE = Scope(family="pdf", platform="net")
_GENERATION_ID = "gen-tc677"

# One chunk per real category this fixture actually uses - 3 developer_guide, 1 troubleshooting,
# 0 faq, 0 getting_started (by omission: proving the zero-inclusive default is exactly this
# card's own job) - classified by TC-517's title rule, never the fragile keyword fallback,
# confirmed against the real classify_content_type immediately below.
_DEV_GUIDE_1 = "FQN: Doc: API Overview\nGeneral reference material about the library.\n"
_DEV_GUIDE_2 = "FQN: Doc: Working with Documents\nHow the document object model is organized.\n"
_DEV_GUIDE_3 = "FQN: Doc: Advanced Configuration\nConfiguring advanced options for the library.\n"
_TROUBLESHOOTING = "FQN: Doc: Troubleshooting\nKnown issues and common errors.\n"
# A symbol chunk and an example chunk - neither is documentation, and both must be excluded from
# every count below (TC-381's own rule, reused unchanged here).
_SYMBOL = "FQN: Demo.Thing\nKind: class_declaration\nMethods:\n  - a\n  - b\n"
_EXAMPLE = "FQN: Example: sign a document\nAn example showing how to sign a document.\n"

for _text, _expected_type in (
    (_DEV_GUIDE_1, "developer_guide"),
    (_DEV_GUIDE_2, "developer_guide"),
    (_DEV_GUIDE_3, "developer_guide"),
    (_TROUBLESHOOTING, "troubleshooting"),
):
    assert chunk_kind(_text) == DOCUMENTATION, f"fixture chunk not classified as documentation: {_text!r}"
    assert classify_content_type(_text) == _expected_type, f"fixture chunk misclassified: {_text!r}"
assert chunk_kind(_SYMBOL) != DOCUMENTATION, "fixture symbol chunk must not be documentation"
assert chunk_kind(_EXAMPLE) != DOCUMENTATION, "fixture example chunk must not be documentation"

# The real, known distribution this card's own spec names verbatim (plans/TC-677.yaml, inputs[2]):
# 3 developer_guide, 0 faq, 1 troubleshooting, 0 getting_started.
EXPECTED_CONTENT_TYPE_COUNTS = {
    "getting_started": 0,
    "developer_guide": 3,
    "troubleshooting": 1,
    "faq": 0,
}


def shared_fixture_documents() -> dict[str, dict]:
    """The ONE fixture ``documents`` dict (doc_id -> ``{"chunk_id", "text"}``) both this file's own
    ``Miss`` tests and ``test_identity_content_type_counts.py``'s ``DeploymentFacts`` tests build
    their generation payload from - see the module docstring."""
    texts = [_DEV_GUIDE_1, _DEV_GUIDE_2, _DEV_GUIDE_3, _TROUBLESHOOTING, _SYMBOL, _EXAMPLE]
    return {f"doc-{index}": {"chunk_id": f"c{index}", "text": text} for index, text in enumerate(texts)}


class _Store:
    """One active generation whose chunks are exactly the given ``documents`` dict."""

    def __init__(self, documents: dict[str, dict]) -> None:
        self._manifest = SimpleNamespace(payload={"lexical_index": {"documents": documents}})

    def read_active(self, scope: str) -> str:
        return _GENERATION_ID

    def read_generation(self, scope: str, generation_id: str) -> SimpleNamespace:
        return self._manifest


class _NoActiveStore:
    """No generation has ever been published for this scope."""

    def read_active(self, scope: str) -> None:
        return None


class _EmptyDocumentsStore:
    """An active generation whose document index is empty - distinct from no generation at all."""

    def __init__(self) -> None:
        self._manifest = SimpleNamespace(payload={"lexical_index": {"documents": {}}})

    def read_active(self, scope: str) -> str:
        return _GENERATION_ID

    def read_generation(self, scope: str, generation_id: str) -> SimpleNamespace:
        return self._manifest


def test_miss_from_a_generation_with_known_distribution_reports_exact_real_counts() -> None:
    # content_type="faq" misses immediately: this fixture has 0 faq documents
    # (``_doc_ids_by_content_type``'s own grouping, TC-381). The query "???" carries no minimum
    # evidence (``relevance.has_minimum_evidence``), so ``_rule_for`` returns ``None`` before ever
    # reading this hand-built payload's missing lexical "tokens" field - exactly how a real
    # unanswerable query behaves, never a fixture-shape workaround.
    store = _Store(shared_fixture_documents())
    result = search_docs(store, SCOPE, "???", content_type="faq")
    assert isinstance(result, Miss)
    assert result.content_type_counts == EXPECTED_CONTENT_TYPE_COUNTS
    # Zero-inclusive: every real category key is present, not merely the non-zero ones.
    assert set(result.content_type_counts) == set(CONTENT_TYPES)


def test_miss_from_no_active_generation_reports_all_zeros_never_crashes() -> None:
    result = search_docs(_NoActiveStore(), SCOPE, "anything")
    assert isinstance(result, Miss)
    assert result.reason == "no published generation for this scope"
    assert result.content_type_counts == dict.fromkeys(CONTENT_TYPES, 0)


def test_miss_from_an_empty_document_index_reports_all_zeros_never_crashes() -> None:
    result = search_docs(_EmptyDocumentsStore(), SCOPE, "anything")
    assert isinstance(result, Miss)
    assert result.reason == "published generation has no document index"
    assert result.content_type_counts == dict.fromkeys(CONTENT_TYPES, 0)


def test_chunk_id_not_found_miss_also_reports_real_counts() -> None:
    # _fetch_part's own Miss construction site: a readable generation, but no documentation chunk
    # carries the given chunk_id - the other real construction site this card touches.
    store = _Store(shared_fixture_documents())
    result = search_docs(store, SCOPE, "anything", content_type="developer_guide", chunk_id="does-not-exist")
    assert isinstance(result, Miss)
    assert result.content_type_counts == EXPECTED_CONTENT_TYPE_COUNTS


def test_default_factory_never_shares_a_mutable_dict_across_instances() -> None:
    # The dataclass default is built by a factory precisely so two misses never alias the same
    # dict - mutating one's field must never leak into another's.
    first = Miss(SCOPE, "q1", "faq", "r1")
    second = Miss(SCOPE, "q2", "faq", "r2")
    assert first.content_type_counts == dict.fromkeys(CONTENT_TYPES, 0)
    first.content_type_counts["faq"] = 99
    assert second.content_type_counts["faq"] == 0
