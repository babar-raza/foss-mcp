"""G3/TC-823 (REQ-G3-011, RD2-230 / RD2-278): a query naming a concept the library does not have is not ``exact``.

``search_symbols`` labels a hit ``exact`` on its two loosest evidence tiers (``bag_of_words``: every word of
the query is a word of the candidate's qualified name; ``evidence_sentence``) even when the candidate does not
hold what the user named: words/python ``DocumentBuilder`` -> ``MarkdownDocumentBuilder`` and ``HtmlInsertOptions``
(only the module path is called ``document_builder``), slides/cpp ``hyperlink`` -> a marker interface that
declares nothing. ``lookup`` now re-checks each such label against the candidate and degrades it to
``partial``, naming the words it does not explain; the hit itself stays, in its place.

Offline and deterministic: synthetic symbol chunks plus the committed ``infra/demo-manifests`` copied to a
temp dir; the embedding gateway is never configured.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from foss_mcp.indexing.lexical_index_writer import build_lexical_index
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_symbol import extract_fqn
from foss_mcp.mcp.tools.lookup import TaskAnswer, lookup
from foss_mcp.mcp.tools.search_symbols import MATCH_EXACT, MATCH_PARTIAL, SymbolMatch, search_symbols
from tests.conformance.corpus import ConformanceCorpus, load_demo_manifests_corpus

REPO_ROOT = Path(__file__).resolve().parents[3]
LOOKUP_SOURCE = REPO_ROOT / "src" / "foss_mcp" / "mcp" / "tools" / "lookup.py"

_GENERATION_ID = "gen-tc823-exact-label"
SCOPE = Scope(family="words", platform="python")


class _InMemoryStore:
    def __init__(self, *chunk_texts: str) -> None:
        chunks = [SimpleNamespace(text=text) for text in chunk_texts]
        lexical = build_lexical_index(chunks, [f"chunk-{i}" for i in range(len(chunks))], _GENERATION_ID)  # type: ignore[arg-type]
        self._manifest = SimpleNamespace(payload={"lexical_index": lexical})

    def read_active(self, scope: str) -> str:
        return _GENERATION_ID

    def read_generation(self, scope: str, generation_id: str) -> SimpleNamespace:
        return self._manifest


def _symbol(fqn: str, description: str, *methods: str) -> str:
    lines = [f"FQN: {fqn}", "Kind: class", description]
    if methods:
        lines += ["", "Methods:", *[f"  - {method}" for method in methods]]
    return "\n".join(lines) + "\n"


def _symbols(answer: object) -> list[SymbolMatch]:
    if isinstance(answer, TaskAnswer):
        return list(answer.symbols)
    assert isinstance(answer, list), answer
    return [match for match in answer if isinstance(match, SymbolMatch)]


def _by_name(matches: list[SymbolMatch]) -> dict[str, SymbolMatch]:
    return {
        (extract_fqn(match.text) or "").rsplit(".", 1)[-1].rsplit("::", 1)[-1]: match for match in matches
    }


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> ConformanceCorpus:
    return load_demo_manifests_corpus(tmp_path_factory.mktemp("exact-label-corpus"))


# ---- a CamelCase identifier the candidate is not -------------------------------------------------------

_NO_DOCUMENT_BUILDER = (
    _symbol(
        "pkg.md_import.document_builder.MarkdownDocumentBuilder",
        "Builds a document out of Markdown.",
        "build(markdown: str) -> Document",
    ),
    _symbol(
        "pkg.md_import.document_builder.HtmlInsertOptions",
        "Options for inserting HTML.",
        "set_base_url(url: str) -> None",
    ),
    _symbol("pkg.Document", "A document.", "save(path: str) -> None"),
)


def test_the_fixture_is_what_search_symbols_calls_exact_on_the_bag_of_words_tier() -> None:
    """Non-vacuous: before this card's fix the two look-alikes ARE ``exact`` ``bag_of_words`` hits."""
    found = search_symbols(_InMemoryStore(*_NO_DOCUMENT_BUILDER), SCOPE, "DocumentBuilder")  # type: ignore[arg-type]
    assert isinstance(found, list)
    named = _by_name(found)
    assert named["MarkdownDocumentBuilder"].match_quality == MATCH_EXACT
    assert named["MarkdownDocumentBuilder"].match_kind == "bag_of_words"
    assert named["HtmlInsertOptions"].match_quality == MATCH_EXACT


def test_a_name_that_merely_ends_with_the_identifier_or_shares_its_module_is_partial() -> None:
    answer = lookup(_InMemoryStore(*_NO_DOCUMENT_BUILDER), SCOPE, "DocumentBuilder")  # type: ignore[arg-type]
    found = _symbols(answer)
    assert found, "the look-alikes stay in the answer: an honest nearest answer, not a miss"
    for match in found:
        assert match.match_quality == MATCH_PARTIAL
        assert match.unmatched_terms == ("DocumentBuilder",)


def test_a_candidate_that_really_is_the_identifier_stays_exact() -> None:
    real = _symbol("pkg.DocumentBuilder", "Builds documents.", "write(text: str) -> None")
    found = _symbols(lookup(_InMemoryStore(real, *_NO_DOCUMENT_BUILDER), SCOPE, "DocumentBuilder"))  # type: ignore[arg-type]
    named = _by_name(found)
    assert named["DocumentBuilder"].match_quality == MATCH_EXACT
    assert named["DocumentBuilder"].match_kind == "final_segment"
    assert named["MarkdownDocumentBuilder"].match_quality == MATCH_PARTIAL


def test_a_type_that_declares_the_identifier_as_a_member_keeps_its_label() -> None:
    owner = _symbol("pkg.Writer", "Writes things.", "DocumentBuilder() -> Builder  (Makes the builder.)")
    found = _symbols(lookup(_InMemoryStore(owner, *_NO_DOCUMENT_BUILDER), SCOPE, "DocumentBuilder"))  # type: ignore[arg-type]
    writer = _by_name(found).get("Writer")
    assert writer is not None
    assert writer.match_quality == MATCH_EXACT


def test_the_lookup_answer_to_a_bare_identifier_query_is_no_task_answer_with_an_exact_label() -> None:
    """The label of the whole answer follows its symbols: nothing here may claim ``exact``."""
    answer = lookup(_InMemoryStore(*_NO_DOCUMENT_BUILDER), SCOPE, "DocumentBuilder")  # type: ignore[arg-type]
    assert not any(match.match_quality == MATCH_EXACT for match in _symbols(answer))
    if isinstance(answer, TaskAnswer):
        assert answer.match_quality == MATCH_PARTIAL


# ---- a plain word that only the candidate's name carries -------------------------------------------------

_MARKER_INTERFACE = _symbol(
    "pkg.IHyperlinkContainer",
    "Base interface for objects that can contain hyperlinks.",
    "~IHyperlinkContainer() [virtual, defaulted]",
)
_REAL_HYPERLINK_USER = _symbol(
    "pkg.HyperlinkManager",
    "Manages hyperlinks.",
    "add_hyperlink(url: str) -> None  (Adds a hyperlink.)",
    "remove_hyperlink(index: int) -> None",
)


def test_an_empty_marker_interface_is_not_an_exact_answer_for_its_name_word() -> None:
    found = _symbols(
        lookup(_InMemoryStore(_MARKER_INTERFACE, _symbol("pkg.Slide", "A slide.")), SCOPE, "hyperlink")
    )  # type: ignore[arg-type]
    named = _by_name(found)
    assert named["IHyperlinkContainer"].match_quality == MATCH_PARTIAL
    assert named["IHyperlinkContainer"].unmatched_terms


def test_a_type_that_lists_a_member_about_the_word_stays_exact() -> None:
    found = _symbols(lookup(_InMemoryStore(_MARKER_INTERFACE, _REAL_HYPERLINK_USER), SCOPE, "hyperlink"))  # type: ignore[arg-type]
    named = _by_name(found)
    assert named["HyperlinkManager"].match_quality == MATCH_EXACT
    assert named["IHyperlinkContainer"].match_quality == MATCH_PARTIAL


# ---- the real repros ------------------------------------------------------------------------------------------


def test_words_python_document_builder_is_not_exact(corpus: ConformanceCorpus) -> None:
    pilot = corpus.pilots["words__python"]
    found = _symbols(lookup(corpus.store, pilot.scope, "DocumentBuilder"))
    assert found
    assert all(match.match_quality == MATCH_PARTIAL for match in found), [
        (extract_fqn(match.text), match.match_quality) for match in found
    ]


def test_words_net_real_document_builder_is_still_exact_and_its_options_are_not(
    corpus: ConformanceCorpus,
) -> None:
    pilot = corpus.pilots["words__net"]
    named = _by_name(_symbols(lookup(corpus.store, pilot.scope, "DocumentBuilder")))
    assert named["DocumentBuilder"].match_quality == MATCH_EXACT
    assert named["DocumentBuilderOptions"].match_quality == MATCH_PARTIAL


def test_slides_cpp_hyperlink_marker_interface_is_not_exact(corpus: ConformanceCorpus) -> None:
    pilot = corpus.pilots["slides__cpp"]
    found = _symbols(lookup(corpus.store, pilot.scope, "hyperlink"))
    named = _by_name(found)
    assert named["IHyperlinkContainer"].match_quality == MATCH_PARTIAL


# ---- liveness: every search_symbols result lookup returns passes through the check -----------------------------


def test_every_search_symbols_call_in_lookup_goes_through_the_label_check() -> None:
    source = LOOKUP_SOURCE.read_text(encoding="utf-8")
    body = source[source.index("def lookup(") :]
    calls = body.count("search_symbols(store, scope, query")
    wrapped = body.count("_precise_symbol_labels(")
    # the very first call (no minimum evidence: a one-letter exact name) is deliberately unwrapped
    assert wrapped >= 3
    assert calls - wrapped <= 1
