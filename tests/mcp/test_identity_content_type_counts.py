"""G3/TC-677 (REQ-G3-013, C3): ``identity.DeploymentFacts`` carries the same real, zero-inclusive
per-content-type documentation chunk count ``search_docs.Miss`` now carries - the identical
``chunk_kind``/``DOCUMENTATION`` + ``classify_content_type`` mechanism, threaded through
``_facts_from_payload``'s own existing loop (no second pass over the manifest).

Test (c) below (``test_deployment_facts_counts_agree_byte_for_byte_with_the_miss_surface``) proves
the two surfaces agree by reusing the LITERAL SAME fixture ``documents`` dict
``tests/mcp/tools/test_search_docs_content_type_counts.py`` builds
(``shared_fixture_documents()``) for both a ``DeploymentFacts`` and a ``Miss`` - never two
independently-asserted counts that could silently drift apart.
"""

from __future__ import annotations

from types import SimpleNamespace

from foss_mcp.mcp.identity import NEUTRAL_FACTS, DeploymentFacts, _facts_from_payload
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.search_docs import CONTENT_TYPES, Miss, search_docs
from tests.mcp.tools.test_search_docs_content_type_counts import (
    EXPECTED_CONTENT_TYPE_COUNTS,
    shared_fixture_documents,
)

SCOPE = Scope(family="pdf", platform="net")


def _payload(documents: dict[str, dict]) -> dict:
    return {"lexical_index": {"documents": documents}}


class _Store:
    """One active generation whose chunks are exactly the given ``documents`` dict - the same
    minimal shape ``test_search_docs_content_type_counts.py``'s own ``_Store`` uses, duplicated
    locally here (not shared) so each test file stands on its own construction, the way
    ``tests/mcp/tools/test_report_index_freshness_content_type_counts.py``'s own docstring already
    explains this suite's convention of duplicating small store helpers rather than sharing them."""

    def __init__(self, documents: dict[str, dict]) -> None:
        self._manifest = SimpleNamespace(payload=_payload(documents))

    def read_active(self, scope: str) -> str:
        return "gen-tc677"

    def read_generation(self, scope: str, generation_id: str) -> SimpleNamespace:
        return self._manifest


def test_deployment_facts_from_the_shared_fixture_reports_exact_real_counts() -> None:
    facts = _facts_from_payload(_payload(shared_fixture_documents()))
    assert facts.content_type_counts == EXPECTED_CONTENT_TYPE_COUNTS
    # Zero-inclusive: every real category key is present, not merely the non-zero ones.
    assert set(facts.content_type_counts) == set(CONTENT_TYPES)


def test_deployment_facts_counts_agree_byte_for_byte_with_the_miss_surface() -> None:
    # The SAME documents dict feeds both surfaces - proof they agree on real input, not two
    # independently-constructed fixtures that could silently drift apart.
    documents = shared_fixture_documents()
    facts = _facts_from_payload(_payload(documents))
    miss = search_docs(_Store(documents), SCOPE, "???", content_type="faq")
    assert isinstance(miss, Miss)
    assert facts.content_type_counts == miss.content_type_counts
    assert facts.content_type_counts == EXPECTED_CONTENT_TYPE_COUNTS


def test_deployment_facts_from_an_unreadable_generation_reports_all_zeros_never_crashes() -> None:
    # No "lexical_index" key at all - the honest "nothing readable" shape read_deployment_facts's
    # own broad except already swallows upstream; _facts_from_payload itself must still not crash
    # on it and must still report the all-zero default.
    assert _facts_from_payload({}).content_type_counts == dict.fromkeys(CONTENT_TYPES, 0)


def test_deployment_facts_from_an_empty_document_index_reports_all_zeros_never_crashes() -> None:
    assert _facts_from_payload(_payload({})).content_type_counts == dict.fromkeys(CONTENT_TYPES, 0)


def test_neutral_facts_carries_the_all_zero_default() -> None:
    assert NEUTRAL_FACTS.content_type_counts == dict.fromkeys(CONTENT_TYPES, 0)


def test_default_factory_never_shares_a_mutable_dict_across_instances() -> None:
    # The dataclass default is built by a factory precisely so two facts never alias the same
    # dict - mutating one's field must never leak into another's.
    first = DeploymentFacts(None, None, None, "lexical-only")
    second = DeploymentFacts(None, None, None, "lexical-only")
    assert first.content_type_counts == dict.fromkeys(CONTENT_TYPES, 0)
    first.content_type_counts["faq"] = 99
    assert second.content_type_counts["faq"] == 0
