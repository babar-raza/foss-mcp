"""G3/TC-684 (REQ-G3-011, WORDS-NET-004): a dotted ``Type.EnumMember``-shaped query no longer
silently drops ``search_symbols``' own real "did you mean" suggestion.

Round-2 finding WORDS-NET-004: ``lookup(store, Scope("words", "net"), "StyleIdentifier.Normal")``
returned a ``TaskAnswer`` with empty ``symbols`` although ``search_symbols`` itself, for the exact
same query, already returns a ``Miss`` naming the real suggestion ``Aspose.Words.StyleIdentifier`` -
the dispatch order in ``lookup.py`` (bare identifier-like query: try ``search_symbols`` first, then
fall back to ``_compose_from_docs()``) treated that ``Miss`` identically to "no evidence at all" and
silently discarded its suggestion in favour of a lower-confidence docs/example answer. Confirmed live
against the real, committed ``words/net`` pilot before this card's fix (see this card's own closeout):
``search_symbols`` returned ``Miss(suggestions=('Aspose.Words.StyleIdentifier',), ...)`` while
``lookup`` returned a ``TaskAnswer(symbols=(), ...)`` that never mentioned it anywhere.

The fix (``lookup._suggested_symbols`` + the bare-identifier dispatch branch at the end of
``lookup()``): when the bare-identifier path's own ``search_symbols`` call returns a ``Miss`` carrying
one or more real ``suggestions`` AND the docs/example answer that would otherwise be returned has not
already named a symbol of its own (``named_symbols``/``explained_symbols`` both empty), the suggested
FQN's own real symbol chunk is looked up directly in the same lexical index and merged into the
``TaskAnswer``'s existing ``symbols`` field, labelled ``match_quality="partial"`` (a hint, never a
confirmed resolution) - no new ``TaskAnswer`` field (that field list is pinned elsewhere, outside this
card's write_paths, by ``test_lookup_forgiveness.py``). This is scoped to the bare-identifier dispatch
branch only (``_looks_like_a_task_question(query)`` is ``False``) - a genuine task question's own
existing precedence (docs/example over an incidental lexical symbol collision) is untouched, and a
``Miss`` with no suggestions, or a query that already got a genuine ``search_symbols`` HIT, both take
exactly the path they took before this card.

Offline and deterministic: the real, committed ``words/net`` pilot is COPIED into a temporary
directory (the repository is never written) and the embedding gateway is stubbed out, the same pattern
``tests/mcp/tools/test_lookup_miss_reason.py`` and ``test_lookup_forgiveness.py`` already use.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp import server
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools import find_examples as find_examples_module
from foss_mcp.mcp.tools.lookup import NOTHING_MATCHED_REASON, TaskAnswer
from foss_mcp.mcp.tools.lookup import lookup as lookup_fn
from foss_mcp.mcp.tools.search_symbols import Miss, SymbolMatch, search_symbols

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
WORDS_NET = Scope(family="words", platform="net")

# The real round-2 query and the real suggestion search_symbols itself offers for it (confirmed live,
# against the real committed words/net pilot, before writing this file).
DOTTED_ENUM_MEMBER_QUERY = "StyleIdentifier.Normal"
EXPECTED_SUGGESTION_FQN = "Aspose.Words.StyleIdentifier"

# (b): a genuine multi-word task question that happens to also contain a dotted-looking substring.
TASK_QUESTION_WITH_DOTTED_SUBSTRING = "how do I set StyleIdentifier.Normal on a paragraph"

# (c): a dotted query search_symbols resolves as a genuine HIT (a real member of a real type).
DOTTED_QUERY_WITH_GENUINE_HIT = "Document.Save"

# (d) / (e): bare queries with no real symbol anywhere in this index.
BARE_QUERY_NO_MATCH_NO_SUGGESTIONS = "Zzqxv"
DOTTED_QUERY_MISS_NO_SUGGESTIONS = "AFRelationship.Nonexistent"


class _NoGatewayEmbeddingProvider:
    """What the standalone image has when no embedding gateway is configured: embedding fails."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("no embedding gateway in this deployment")


@pytest.fixture(autouse=True)
def _no_embedding_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(find_examples_module, "GatewayEmbeddingProvider", _NoGatewayEmbeddingProvider)


@pytest.fixture(scope="module")
def words_net_store(tmp_path_factory: pytest.TempPathFactory) -> GenerationManifestStore:
    root = tmp_path_factory.mktemp("tc684_words_net")
    shutil.copytree(DEMO_MANIFESTS / "words__net__self_extracted", root / "words__net__self_extracted")
    return GenerationManifestStore(root)


# ---- control: search_symbols' own real Miss, for this exact query, carries the real suggestion ------


def test_control_search_symbols_itself_offers_the_real_suggestion(
    words_net_store: GenerationManifestStore,
) -> None:
    miss = search_symbols(words_net_store, WORDS_NET, DOTTED_ENUM_MEMBER_QUERY, top_k=10)
    assert isinstance(miss, Miss)
    assert miss.suggestions == (EXPECTED_SUGGESTION_FQN,)


# ---- (a): the real round-2 case now surfaces the suggestion through lookup --------------------------


def test_a_dotted_enum_member_query_now_surfaces_the_real_suggestion(
    words_net_store: GenerationManifestStore,
) -> None:
    answer = lookup_fn(words_net_store, WORDS_NET, DOTTED_ENUM_MEMBER_QUERY)

    assert isinstance(answer, TaskAnswer)
    assert answer.symbols, "the real search_symbols suggestion was not surfaced anywhere"
    assert any(EXPECTED_SUGGESTION_FQN in match.text for match in answer.symbols)
    # It is a hint, not a confirmed resolution: never promoted to an exact match.
    assert all(match.match_quality != "exact" for match in answer.symbols)


def test_a_dotted_enum_member_query_through_the_server_wrapper_also_surfaces_it(
    words_net_store: GenerationManifestStore,
) -> None:
    payload = server._to_jsonable(
        server._wrap_lookup(words_net_store, WORDS_NET)(query=DOTTED_ENUM_MEMBER_QUERY)
    )
    rendered = str(payload)
    assert EXPECTED_SUGGESTION_FQN in rendered


# ---- (b): a genuine task question with a dotted-looking substring is unaffected ----------------------


def test_a_genuine_task_question_with_a_dotted_substring_still_routes_through_docs(
    words_net_store: GenerationManifestStore,
) -> None:
    answer = lookup_fn(words_net_store, WORDS_NET, TASK_QUESTION_WITH_DOTTED_SUBSTRING)

    assert isinstance(answer, TaskAnswer)
    # The task-question dispatch branch is untouched by this card's fix: no suggestion is merged in,
    # since that merge logic lives only in the bare-identifier branch below it.
    assert answer.symbols == ()
    assert answer.doc_matches or answer.example is not None


# ---- (c): a dotted query search_symbols resolves as a genuine HIT is completely unaffected -----------


def test_a_dotted_query_with_a_genuine_hit_is_unaffected(words_net_store: GenerationManifestStore) -> None:
    direct = search_symbols(words_net_store, WORDS_NET, DOTTED_QUERY_WITH_GENUINE_HIT, top_k=10)
    answer = lookup_fn(words_net_store, WORDS_NET, DOTTED_QUERY_WITH_GENUINE_HIT)

    assert isinstance(direct, list) and direct
    assert isinstance(answer, list)
    assert answer == direct
    assert all(isinstance(match, SymbolMatch) for match in answer)


# ---- (d): a non-dotted bare query with no match and no suggestions is unaffected ----------------------


def test_a_bare_query_with_no_match_and_no_suggestions_is_unaffected(
    words_net_store: GenerationManifestStore,
) -> None:
    symbol_miss = search_symbols(words_net_store, WORDS_NET, BARE_QUERY_NO_MATCH_NO_SUGGESTIONS, top_k=10)
    answer = lookup_fn(words_net_store, WORDS_NET, BARE_QUERY_NO_MATCH_NO_SUGGESTIONS)

    assert isinstance(symbol_miss, Miss)
    assert symbol_miss.suggestions == ()
    assert isinstance(answer, Miss)
    assert answer.suggestions == ()
    assert answer.reason == NOTHING_MATCHED_REASON.format(query=BARE_QUERY_NO_MATCH_NO_SUGGESTIONS)


# ---- (e): a dotted query whose Miss carries NO suggestions falls through exactly as before -----------


def test_a_dotted_query_with_a_miss_and_no_suggestions_falls_through_unchanged(
    words_net_store: GenerationManifestStore,
) -> None:
    symbol_miss = search_symbols(words_net_store, WORDS_NET, DOTTED_QUERY_MISS_NO_SUGGESTIONS, top_k=10)
    answer = lookup_fn(words_net_store, WORDS_NET, DOTTED_QUERY_MISS_NO_SUGGESTIONS)

    assert isinstance(symbol_miss, Miss)
    assert symbol_miss.suggestions == ()
    assert isinstance(answer, Miss)
    assert answer.suggestions == ()
    assert answer.reason == NOTHING_MATCHED_REASON.format(query=DOTTED_QUERY_MISS_NO_SUGGESTIONS)


# ---- the Qualified: tool-description sentence (REQ-G3-011, round-2's second finding at this spot) ----


def test_qualified_line_is_explained_in_both_tool_descriptions() -> None:
    search_symbols_description = server.TOOL_DESCRIPTIONS["search_symbols"]
    get_symbol_description = server.TOOL_DESCRIPTIONS["get_symbol"]

    for description in (search_symbols_description, get_symbol_description):
        assert "Qualified:" in description
        assert "FQN:" in description
        assert "package" in description or "crate" in description
