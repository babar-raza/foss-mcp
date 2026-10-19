"""G3/TC-562 (REQ-G3-009, rows RD3-100 to RD3-109): retrieval relevance from the user's own wording.

Every case goes through the real tools over a COPY of the committed ``infra/demo-manifests`` scope directories
(no network, no container, no embedding gateway). The expectations come from upstream-shaped facts (the name of the
type that does the task, what the pilot's own documentation says it cannot do), never from a previous run of the
server. The corpus ratchet itself is in ``test_round3_retrieval_corpus.py``.
"""

from __future__ import annotations

import dataclasses
import shutil
from pathlib import Path
from types import SimpleNamespace

import anyio
import pytest
from mcp.types import CallToolRequestParams

from foss_mcp.indexing.gateway_embedding_provider import _API_KEY_ENV, _ENDPOINT_ENV
from foss_mcp.indexing.lexical_index_writer import build_lexical_index
from foss_mcp.mcp.routing import DeploymentConfig, Scope
from foss_mcp.mcp.server import create_server, tool_input_schema
from foss_mcp.mcp.tools import relevance
from foss_mcp.mcp.tools.find_examples import NoExampleFound, find_examples
from foss_mcp.mcp.tools.get_symbol import extract_fqn
from foss_mcp.mcp.tools.lookup import TaskAnswer, lookup
from foss_mcp.mcp.tools.search_docs import (
    CONTENT_TYPES,
    DocMatch,
    category_intent,
    classify_content_type,
    search_docs,
)
from foss_mcp.mcp.tools.search_docs import (
    Miss as DocsMiss,
)
from foss_mcp.mcp.tools.search_symbols import NOT_IMPLEMENTED_PHRASE, Miss, SymbolMatch, search_symbols
from tests.mcp.tools.test_round3_retrieval_corpus import (
    DEMO_MANIFESTS,
    Corpus,
    flatten,
    satisfies,
)


def _scope(pilot: str) -> Scope:
    family, platform = pilot.split("__")
    return Scope(family=family, platform=platform)


@pytest.fixture(autouse=True)
def _no_embedding_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_ENDPOINT_ENV, raising=False)
    monkeypatch.delenv(_API_KEY_ENV, raising=False)


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> Corpus:
    root = tmp_path_factory.mktemp("round3-retrieval-scopes")
    for directory in DEMO_MANIFESTS.glob("*__self_extracted"):
        shutil.copytree(directory, root / directory.name)
    return Corpus(root)


def _top(corpus: Corpus, pilot: str, query: str, n: int = 3) -> list[object]:
    return list(flatten(lookup(corpus.store, _scope(pilot), query))[:n])


def _hits(corpus: Corpus, pilot: str, query: str, *specs: str, n: int = 3) -> bool:
    return any(satisfies(item, spec) for item in _top(corpus, pilot, query, n) for spec in specs)  # type: ignore[arg-type]


def _short(text: str) -> str:
    return (extract_fqn(text) or "").replace("::", ".").split(".")[-1]


class _InMemoryStore:
    def __init__(self, *chunk_texts: str) -> None:
        chunks = [SimpleNamespace(text=text) for text in chunk_texts]
        lexical = build_lexical_index(chunks, [f"chunk-{i}" for i in range(len(chunks))], "gen-tc562")  # type: ignore[arg-type]
        self._manifest = SimpleNamespace(payload={"lexical_index": lexical})

    def read_active(self, scope: str) -> str:
        return "gen-tc562"

    def read_generation(self, scope: str, generation_id: str) -> SimpleNamespace:
        return self._manifest


def _type_chunk(fqn: str, *members: str, extra: str = "") -> str:
    lines = [f"FQN: {fqn}", "Kind: class_declaration"]
    if extra:
        lines.append(extra)
    if members:
        lines.append("Methods:")
        lines.extend(f"  - {member}()" for member in members)
    return "\n".join(lines) + "\n"


def _doc_chunk(title: str, *body: str) -> str:
    return "\n".join([f"FQN: Doc: {title}", *body]) + "\n"


# ---- RD3-100: stop words, synonyms, the switch --------------------------------------------------------------


def test_stop_words_are_function_words_never_an_api_noun() -> None:
    assert isinstance(relevance.STOP_WORDS, frozenset)
    for noun in ("page", "document", "table", "image", "save", "open", "merge", "cell", "slide", "chart"):
        assert noun not in relevance.STOP_WORDS
    for filler in ("together", "please", "whole", "every"):
        assert filler in relevance.STOP_WORDS


def test_stop_words_are_dropped_from_a_query_and_the_switch_restores_the_old_tokenisation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert relevance.STOP_WORDS_ENABLED is True
    assert relevance.query_terms("merge cells together", "cells") == frozenset({"merge"})
    monkeypatch.setattr(relevance, "STOP_WORDS_ENABLED", False)
    assert "together" in relevance.query_terms("merge cells together", "cells")


def test_a_query_made_only_of_stop_words_keeps_them() -> None:
    assert relevance.query_terms("please together", "pdf") == frozenset({"please", "together"})


@pytest.mark.parametrize(
    ("user", "api"),
    [
        ("change", {"put", "set", "value"}),
        ("set", {"put", "set", "value"}),
        ("edit", {"put", "set", "value"}),
        ("merge", {"merge", "concat", "append"}),
        ("link", {"hyperlink"}),
        ("bold", {"font"}),
        ("colour", {"color"}),
        ("remove", {"remove", "delete"}),
        ("delete", {"remove", "delete"}),
        ("text", {"text", "string"}),
    ],
)
def test_the_user_term_synonym_map_has_the_mandated_entries(user: str, api: set[str]) -> None:
    assert api <= set(relevance.USER_TERM_SYNONYMS[user])


def test_the_symmetric_synonym_groups_are_untouched_and_the_user_map_is_one_way() -> None:
    assert len(relevance.SYNONYM_GROUPS) == 4
    # a document that says "concat" is not widened to "merge": the user map only widens the query
    assert not relevance.coverage(["merge"], ["concat"])
    assert relevance.coverage(["merge"], ["merge"]) == 1.0


def test_merge_cells_together_reaches_cells_merge_on_every_pilot_that_has_it(corpus: Corpus) -> None:
    for pilot in ("cells__net", "cells__cpp", "cells__java"):
        assert _hits(corpus, pilot, "merge cells together", "Cells.Merge"), pilot


def test_create_a_chart_from_my_data_and_add_a_hyperlink_reach_their_types(corpus: Corpus) -> None:
    assert _hits(corpus, "cells__net", "create a chart from my data", "ChartCollection.Add", "Chart")
    assert _hits(corpus, "cells__net", "add a hyperlink", "Hyperlink", "HyperlinkCollection.Add")
    assert _hits(
        corpus, "cells__net", "add a link to a website in a cell", "Hyperlink", "HyperlinkCollection.Add"
    )


def test_the_plain_words_and_the_stop_word_free_sentence_find_the_same_type(corpus: Corpus) -> None:
    plain = _top(corpus, "cells__net", "merge cells")
    padded = _top(corpus, "cells__net", "please merge the cells together")
    assert any(satisfies(i, "Cells.Merge") for i in plain)  # type: ignore[arg-type]
    assert any(satisfies(i, "Cells.Merge") for i in padded)  # type: ignore[arg-type]


# ---- RD3-101: the wrong-but-plausible list ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pilot", "query", "correct", "wrong_first"),
    [
        ("words__net", "make text bold", ("Font",), "TextPath"),
        ("words__python", "make text bold", ("Font",), "TextPath"),
        ("slides__net", "add a slide", ("SlideCollection", "Slide"), "ParagraphCollection"),
        ("slides__java", "add a slide", ("SlideCollection", "Slide"), "ParagraphCollection"),
        ("slides__python", "add a slide", ("SlideCollection", "Slide"), "ParagraphCollection"),
        ("pdf__net", "merge pdfs", ("MergeOptions", "PdfFileEditor", "Merger", "Document"), "DssMaterial"),
        ("pdf__java", "merge pdfs", ("MergeOptions", "PdfFileEditor", "Merger", "Document"), "DssMaterial"),
        ("pdf__python", "merge pdfs", ("MergeOptions", "PdfFileEditor", "Merger", "Document"), "DssMaterial"),
        ("pdf__net", "render page to image", ("Page", "Image", "PngDevice"), "SignatureAppearance"),
        ("pdf__python", "render page to image", ("Page", "Image", "PngDevice"), "SignatureAppearance"),
    ],
)
def test_the_wrong_but_plausible_answers_rank_the_correct_type_in_the_top_three_or_miss(
    corpus: Corpus, pilot: str, query: str, correct: tuple[str, ...], wrong_first: str
) -> None:
    answer = lookup(corpus.store, _scope(pilot), query)
    if isinstance(answer, Miss):
        return
    items = flatten(answer)
    assert any(satisfies(item, spec) for item in items[:3] for spec in correct), (pilot, query, items[:3])
    first_symbol = next((item for item in items if item.kind == "symbol"), None)
    assert first_symbol is None or wrong_first.lower() not in first_symbol.fqn.lower()


# ---- RD3-102: getting started, Do, table chunks -------------------------------------------------------------


def _pilots_with_a_getting_started_document(corpus: Corpus) -> list[str]:
    found = []
    for name, pilot in corpus.pilots.items():
        if any(i.kind == "doc" and classify_content_type(i.text) == "getting_started" for i in pilot.items):
            found.append(name)
    return found


def test_getting_started_hits_on_every_pilot_that_has_a_getting_started_document(corpus: Corpus) -> None:
    pilots = _pilots_with_a_getting_started_document(corpus)
    assert len(pilots) >= 30
    for name in pilots:
        answer = lookup(corpus.store, _scope(name), "getting started")
        assert isinstance(answer, TaskAnswer), name
        assert answer.doc_matches, name
        assert all(match.content_type == "getting_started" for match in answer.doc_matches[:1]), name
        assert answer.match_quality == "exact"


@pytest.mark.parametrize(
    "phrase", ["getting started", "Getting Started", "how to install", "quickstart", "known issues", "FAQ"]
)
def test_a_query_that_is_only_a_category_name_names_the_category(phrase: str) -> None:
    assert category_intent(phrase) is not None


@pytest.mark.parametrize("phrase", ["install the pdf driver", "merge pdfs", "getting started with charts"])
def test_a_query_with_a_task_in_it_names_no_category(phrase: str) -> None:
    assert category_intent(phrase) is None


@pytest.mark.parametrize("pilot", ["pdf__net", "pdf__java"])
def test_do_finds_operators_do(corpus: Corpus, pilot: str) -> None:
    for answer in (
        search_symbols(corpus.store, _scope(pilot), "Do"),
        lookup(corpus.store, _scope(pilot), "Do"),
    ):
        assert isinstance(answer, list), pilot
        names = [_short(match.text) for match in answer if isinstance(match, SymbolMatch)]
        assert names[0] == "Do", (pilot, names)
        assert any(
            "operators" in (extract_fqn(m.text) or "").lower() for m in answer if isinstance(m, SymbolMatch)
        )


def test_a_table_chunk_whose_terms_are_far_apart_is_not_an_answer() -> None:
    far_apart = [f"| `Row{index}` | filler text number {index} |" for index in range(30)]
    rows = ["| `alphafoo` | does one thing |", *far_apart, "| `betabar` | does another |"]
    store = _InMemoryStore(_doc_chunk("API Reference", *rows))

    answer = lookup(store, _scope("pdf__net"), "alphafoo betabar")  # type: ignore[arg-type]

    assert isinstance(answer, Miss)


def test_a_single_colour_word_does_not_return_a_reference_table(corpus: Corpus) -> None:
    for pilot in ("pdf__net", "cells__net", "slides__net"):
        answer = lookup(corpus.store, _scope(pilot), "colour")
        if isinstance(answer, TaskAnswer):
            assert not any(
                "|" in match.text and match.text.count("\n|") > 8 for match in answer.doc_matches
            ), pilot


# ---- RD3-103: gibberish, minimum evidence ---------------------------------------------------------------------


def test_a_query_with_no_two_character_word_has_no_evidence() -> None:
    for query in ("a.b.c::d", "???", "x", "- - -", "a b c d"):
        assert relevance.has_minimum_evidence(query) is False, query
    for query in ("Do", "C++", "C#", "pdf", "3d", "a.bc"):
        assert relevance.has_minimum_evidence(query) is True, query


def test_gibberish_is_an_honest_miss_on_every_pilot(corpus: Corpus) -> None:
    for name in corpus.pilots:
        scope = _scope(name)
        for query in ("a.b.c::d", "x.y.z", "?!?"):
            assert isinstance(lookup(corpus.store, scope, query), Miss), (name, query)
            assert isinstance(search_docs(corpus.store, scope, query), DocsMiss), (name, query)
            assert isinstance(find_examples(corpus.store, scope, query), NoExampleFound), (name, query)


# ---- RD3-104: pagination --------------------------------------------------------------------------------------


def test_search_symbols_pages_with_offset_total_matches_and_next_offset(corpus: Corpus) -> None:
    scope = _scope("html__python")
    first = search_symbols(corpus.store, scope, "element", top_k=5)
    assert isinstance(first, list) and len(first) == 5
    total = first[0].total_matches
    assert total > 10
    assert first[0].next_offset == 5 and all(match.total_matches == total for match in first)
    second = search_symbols(corpus.store, scope, "element", top_k=5, offset=first[0].next_offset)
    assert isinstance(second, list)
    assert not {m.doc_id for m in first} & {m.doc_id for m in second}
    last = search_symbols(corpus.store, scope, "element", top_k=5, offset=total - 1)
    assert isinstance(last, list) and len(last) == 1 and last[0].next_offset is None
    past = search_symbols(corpus.store, scope, "element", top_k=5, offset=total)
    assert isinstance(past, Miss) and "past the last" in past.reason


def _call(corpus: Corpus, pilot: str, tool: str, arguments: dict[str, object]):
    scope = _scope(pilot)
    server = create_server(DeploymentConfig(family=scope.family, platform=scope.platform), corpus.store)
    entry = server.get_request_handler("tools/call")
    assert entry is not None

    async def run():
        with anyio.fail_after(120):
            return await entry.handler(None, CallToolRequestParams(name=tool, arguments=arguments))

    return anyio.run(run)


def test_the_server_advertises_and_accepts_the_offset_argument(corpus: Corpus) -> None:
    schema = tool_input_schema("search_symbols")
    assert schema["properties"]["offset"]["minimum"] == 0 and "offset" not in schema.get("required", [])
    result = _call(corpus, "html__python", "search_symbols", {"query": "element", "top_k": 3, "offset": 3})
    assert result.is_error is False
    items = result.structured_content["result"]
    assert len(items) >= 1 and items[0]["next_offset"] == 6 and items[0]["total_matches"] > 6


def test_the_server_treats_content_type_as_optional_on_search_docs(corpus: Corpus) -> None:
    schema = tool_input_schema("search_docs")
    assert "content_type" not in schema.get("required", [])
    assert schema["properties"]["content_type"]["enum"] == list(CONTENT_TYPES)
    result = _call(corpus, "pdf__net", "search_docs", {"query": "install"})
    assert result.is_error is False
    assert result.structured_content["result"]


# ---- RD3-105: Unicode folding ------------------------------------------------------------------------------------


def test_fold_query_is_nfkc_and_drops_zero_width_characters() -> None:
    assert relevance.fold_query("Ｗｏｒｋｂｏｏｋ") == "Workbook"
    assert relevance.fold_query("Work​book") == "Workbook"
    assert relevance.fold_query("ﬁle") == "file"
    assert relevance.fold_query("plain ASCII") == "plain ASCII"


def test_a_full_width_type_name_hits(corpus: Corpus) -> None:
    scope = _scope("cells__net")
    for answer in (
        search_symbols(corpus.store, scope, "Ｗｏｒｋｂｏｏｋ"),
        lookup(corpus.store, scope, "Ｗｏｒｋｂｏｏｋ"),
    ):
        assert isinstance(answer, list)
        assert _short(answer[0].text) == "Workbook"


def test_a_non_latin_query_is_still_an_honest_miss(corpus: Corpus) -> None:
    assert isinstance(lookup(corpus.store, _scope("cells__net"), "工作簿"), Miss)


# ---- RD3-106: the lexical-only mode is not an error ---------------------------------------------------------------


def test_the_by_design_lexical_only_mode_says_not_configured_not_error(corpus: Corpus) -> None:
    misses = 0
    for name in ("pdf__net", "cells__python", "slides__java", "3d__net", "words__python"):
        result = find_examples(corpus.store, _scope(name), "teleport the zebra through quantum glue")
        assert isinstance(result, NoExampleFound), name
        misses += 1
        assert result.vector_fallback in (None, "not_configured"), name
        assert "skipped: error" not in result.reason and "skipped_error" not in str(result), name
        if result.vector_fallback == "not_configured":
            assert "not configured" in result.reason
    assert misses == 5


def test_a_stand_in_provider_that_fails_is_still_reported_as_an_error(
    corpus: Corpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    from foss_mcp.mcp.tools import find_examples as module

    def broken() -> object:
        raise RuntimeError("FOSS_MCP_EMBEDDING_GATEWAY_ENDPOINT must be set")

    monkeypatch.setattr(module, "GatewayEmbeddingProvider", broken)
    result = find_examples(corpus.store, _scope("pdf__net"), "teleport the zebra through quantum glue")
    assert isinstance(result, NoExampleFound)
    assert result.vector_fallback == "skipped_error"


# ---- RD3-107: search_docs without content_type ----------------------------------------------------------------------


def test_search_docs_searches_every_category_when_content_type_is_omitted(corpus: Corpus) -> None:
    scope = _scope("pdf__net")
    everything = search_docs(corpus.store, scope, "install")
    assert isinstance(everything, list) and everything
    assert all(isinstance(match, DocMatch) and match.content_type in CONTENT_TYPES for match in everything)
    narrow = search_docs(corpus.store, scope, "install", "getting_started")
    assert isinstance(narrow, list)
    assert {m.doc_id for m in narrow} <= {m.doc_id for m in everything}
    wrong_category = search_docs(corpus.store, scope, "install", "faq")
    assert isinstance(wrong_category, DocsMiss)


def test_search_docs_with_a_bad_content_type_still_raises() -> None:
    with pytest.raises(ValueError):
        search_docs(_InMemoryStore(_doc_chunk("Guide", "x y")), _scope("pdf__net"), "x", "nonsense")  # type: ignore[arg-type]


def test_a_documentation_chunk_can_be_fetched_by_id_without_a_content_type(corpus: Corpus) -> None:
    scope = _scope("pdf__net")
    first = search_docs(corpus.store, scope, "install")
    assert isinstance(first, list)
    part = search_docs(corpus.store, scope, "install", None, chunk_id=first[0].chunk_id, offset=0)
    assert isinstance(part, list) and part[0].content_type == first[0].content_type


# ---- RD3-108: aliases --------------------------------------------------------------------------------------------


def test_a_re_exported_type_is_one_result_with_its_aliases_listed(corpus: Corpus) -> None:
    result = search_symbols(corpus.store, _scope("note__python"), "Document")
    assert isinstance(result, list)
    bodies = [m.text.split("\n", 1)[1] for m in result]
    exact_names = [extract_fqn(m.text) for m in result]
    assert len(set(exact_names)) == len(exact_names)
    first = result[0]
    assert first.aliases, first.aliases
    assert all(alias not in exact_names for alias in first.aliases)
    assert len(bodies) == len(result)


# ---- RD3-109 / marker contract with TC-559: limitation chunks first --------------------------------------------------


@pytest.mark.parametrize(
    ("pilot", "query"),
    [
        ("slides__java", "save presentation as PDF"),
        ("slides__net", "save presentation as PDF"),
        ("slides__cpp", "export to PDF"),
        ("3d__net", "render a scene"),
    ],
)
def test_a_capability_the_documentation_lists_as_unsupported_answers_with_that_documentation_first(
    corpus: Corpus, pilot: str, query: str
) -> None:
    answer = lookup(corpus.store, _scope(pilot), query)
    assert isinstance(answer, TaskAnswer), (pilot, query)
    first = answer.doc_matches[0].text.lower()
    assert any(
        cue in first for cue in ("not supported", "not implemented", "unsupported", "throws", "no pdf")
    ), first
    assert answer.match_quality == "exact"


def test_the_same_holds_for_a_synthetic_words_net_scope_document() -> None:
    scope_doc = _doc_chunk(
        "Scope and Limitations",
        "- Export to PDF is not supported in this FOSS build: Document.Save throws for SaveFormat.Pdf.",
        "",
        "- Mail merge is supported.",
    )
    other = _doc_chunk("Key Capabilities", "Document export lists formats: docx, markdown and text files.")
    store = _InMemoryStore(scope_doc, other, _type_chunk("Aspose.Words.Document", "Save"))

    answer = lookup(store, _scope("words__net"), "export to PDF")  # type: ignore[arg-type]

    assert isinstance(answer, TaskAnswer)
    assert "not supported" in answer.doc_matches[0].text


def test_a_question_nobody_documents_as_unsupported_gets_no_limitation_first(corpus: Corpus) -> None:
    answer = lookup(corpus.store, _scope("cells__net"), "merge cells together")
    assert isinstance(answer, TaskAnswer)
    assert not any("not supported" in match.text.lower() for match in answer.doc_matches[:1])


def test_a_symbol_chunk_that_says_not_implemented_in_this_build_carries_the_phrase() -> None:
    chunk = _type_chunk(
        "Aspose.ThreeD.Renderer",
        "Render",
        extra=f"Status: {NOT_IMPLEMENTED_PHRASE}: Render throws NotImplementedException.",
    )
    store = _InMemoryStore(chunk, _type_chunk("Aspose.ThreeD.Scene", "Open"))

    found = search_symbols(store, _scope("3d__net"), "Renderer")  # type: ignore[arg-type]

    assert isinstance(found, list)
    assert NOT_IMPLEMENTED_PHRASE in found[0].text
    assert NOT_IMPLEMENTED_PHRASE in found[0].limitation
    plain = search_symbols(store, _scope("3d__net"), "Scene")  # type: ignore[arg-type]
    assert isinstance(plain, list) and plain[0].limitation == ""


# ---- symbols in the user's words: verbs, plurals, partial answers ---------------------------------------------------


def test_a_plural_of_a_camel_case_name_and_an_add_verb_find_the_type(corpus: Corpus) -> None:
    scope = _scope("cells__net")
    plural = search_symbols(corpus.store, scope, "Workbooks")
    assert isinstance(plural, list) and _short(plural[0].text) == "Workbook"
    verb = search_symbols(corpus.store, scope, "add Hyperlink")
    assert isinstance(verb, list) and _short(verb[0].text) == "Hyperlink"
    gerund = search_symbols(corpus.store, scope, "merging cells")
    assert isinstance(gerund, list) and any(m for m in gerund if "Merge" in m.text)


def test_a_typo_still_gives_did_you_mean(corpus: Corpus) -> None:
    result = search_symbols(corpus.store, _scope("cells__net"), "Workbok")
    assert isinstance(result, Miss)
    assert "Aspose.Cells_FOSS.Workbook" in result.suggestions


def test_dotted_and_partial_names_stay_honest_misses(corpus: Corpus) -> None:
    scope = _scope("cells__net")
    for query in ("Workbook.NoSuchMember", "Workboo", "Cells::Merg"):
        assert isinstance(search_symbols(corpus.store, scope, query), Miss), query


def test_an_invented_sentence_is_an_honest_miss_on_every_pilot(corpus: Corpus) -> None:
    for name in corpus.pilots:
        for query in ("teleport the quantum zebra to mars", "bake a chocolate souffle in the oven"):
            assert isinstance(lookup(corpus.store, _scope(name), query), Miss), (name, query)


def test_every_symbol_result_is_labelled(corpus: Corpus) -> None:
    for query in ("add a slide", "merge cells together", "Workbook", "change a workbook cell value"):
        result = search_symbols(corpus.store, _scope("cells__net"), query)
        if isinstance(result, list):
            assert all(match.match_quality in ("exact", "partial") for match in result), query


def test_the_exact_name_still_ranks_first_on_a_sample_of_every_pilot(corpus: Corpus) -> None:
    import random

    rng = random.Random(562)
    checked = 0
    for name, pilot in corpus.pilots.items():
        by_short: dict[str, int] = {}
        for item in pilot.items:
            if item.kind == "symbol":
                key = item.fqn.replace("::", ".").split(".")[-1]
                by_short[key] = by_short.get(key, 0) + 1
        unique = sorted(key for key, count in by_short.items() if count == 1 and len(key) > 4)
        for key in rng.sample(unique, min(4, len(unique))):
            first = next(i for i in flatten(lookup(corpus.store, _scope(name), key)) if i.kind == "symbol")
            assert first.fqn.replace("::", ".").split(".")[-1] == key, (name, key)
            checked += 1
    assert checked >= 100


def test_the_new_task_answer_fields_are_json_ordered_symbols_first() -> None:
    names = [field.name for field in dataclasses.fields(TaskAnswer)]
    assert names.index("symbols") < names.index("example") < names.index("doc_matches")


def test_the_tools_are_wired_to_the_relevance_layer() -> None:
    from foss_mcp.mcp.tools import lookup as lookup_module
    from foss_mcp.mcp.tools import search_docs as docs_module
    from foss_mcp.mcp.tools import search_symbols as symbols_module

    root = Path(lookup_module.__file__).parent
    assert "fold_query" in (root / "lookup.py").read_text(encoding="utf-8")
    assert "has_minimum_evidence" in (root / "search_docs.py").read_text(encoding="utf-8")
    assert "evidence_terms" in (root / "search_symbols.py").read_text(encoding="utf-8")
    assert hasattr(docs_module, "category_intent") and hasattr(symbols_module, "NOT_IMPLEMENTED_PHRASE")
