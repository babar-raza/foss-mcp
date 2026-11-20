"""G3/TC-856 (REQ-G3-015, RD2-278 / round-2 WORDS-PY-004): words/python discloses that it has no ``DocumentBuilder``.

A user coming from Aspose.Words for .NET/Java asks for ``DocumentBuilder``. ``aspose-words-foss`` for Python
26.7.0 has no such class (an ``ast.parse`` scan of the upstream source at the indexed commit ``2d2efee2`` finds
no definition of the name); ``lookup`` returns the two look-alikes ``MarkdownDocumentBuilder`` and
``HtmlInsertOptions``, internal Markdown-import helpers that TC-823 already labels ``partial``. This card adds
the missing explanation as a known-defect entry attached to exactly those two symbols, so the disclosure travels
with the answer instead of being something a reader must already know.

Real committed registry file and real committed ``infra/demo-manifests`` pilots throughout - never a synthetic
stand-in. Offline and deterministic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from foss_mcp.indexing.known_defects import KnownDefect, load_known_defects
from foss_mcp.mcp.tools.get_symbol import SymbolSignature, extract_fqn, get_symbol
from foss_mcp.mcp.tools.lookup import TaskAnswer, lookup
from foss_mcp.mcp.tools.search_symbols import MATCH_PARTIAL, SymbolMatch, search_symbols
from tests.conformance.corpus import ConformanceCorpus, load_demo_manifests_corpus

DEFECT_ID = "WORDS-PY-004"
MARKDOWN_BUILDER = "aspose.words_foss.md_import.document_builder.MarkdownDocumentBuilder"
HTML_OPTIONS = "aspose.words_foss.md_import.document_builder.HtmlInsertOptions"
MARKER = "no DocumentBuilder class"


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> ConformanceCorpus:
    return load_demo_manifests_corpus(tmp_path_factory.mktemp("documentbuilder-disclosure"))


def _entry() -> KnownDefect:
    matching = [defect for defect in load_known_defects("words", "python") if defect.id == DEFECT_ID]
    assert len(matching) == 1, [defect.id for defect in load_known_defects("words", "python")]
    return matching[0]


def _symbols(answer: object) -> list[SymbolMatch]:
    if isinstance(answer, TaskAnswer):
        return list(answer.symbols)
    assert isinstance(answer, list), answer
    return [match for match in answer if isinstance(match, SymbolMatch)]


# ---- the registry entry itself ----------------------------------------------------------------------------------


def test_the_entry_is_a_verified_open_class_scoped_disclosure_for_the_pinned_version() -> None:
    entry = _entry()
    assert entry.status == "open"
    assert entry.subject.kind == "class"
    assert {MARKDOWN_BUILDER, HTML_OPTIONS} <= set(entry.subject.refs)
    assert entry.applies_to.package_versions == ("26.7.0",)
    assert entry.evidence.verified_on.package == "aspose-words-foss"
    assert entry.evidence.verified_on.version == "26.7.0"
    assert 0 < len(entry.summary) <= 240
    assert MARKER in entry.summary
    assert "light_document_model" in entry.user_action
    assert "LdmDocxWriter" in entry.user_action


def test_the_entry_says_how_it_was_verified_and_that_the_package_is_not_on_pypi() -> None:
    repro = _entry().evidence.repro
    assert "2d2efee2" in repro
    assert "ast.parse" in repro
    assert "not executed" in repro


def test_the_older_words_python_entries_are_untouched() -> None:
    ids = [defect.id for defect in load_known_defects("words", "python")]
    assert ids.count("WORDS-PY-008") == 1
    assert ids.count("WORDS-PY-013") == 1
    assert ids.count(DEFECT_ID) == 1


# ---- the disclosure reaches a user who asks for DocumentBuilder -----------------------------------------------------


def test_lookup_documentbuilder_returns_only_labelled_look_alikes_each_carrying_the_disclosure(
    corpus: ConformanceCorpus,
) -> None:
    scope = corpus.pilots["words__python"].scope
    found = _symbols(lookup(corpus.store, scope, "DocumentBuilder"))
    names = {(extract_fqn(match.text) or "").strip() for match in found}
    assert {MARKDOWN_BUILDER, HTML_OPTIONS} <= names
    assert "aspose.words_foss.DocumentBuilder" not in names
    for match in found:
        assert match.match_quality == MATCH_PARTIAL
        assert any(MARKER in line for line in match.known_defects), (
            extract_fqn(match.text),
            match.known_defects,
        )


def test_search_symbols_documentbuilder_carries_the_disclosure_too(corpus: ConformanceCorpus) -> None:
    scope = corpus.pilots["words__python"].scope
    result = search_symbols(corpus.store, scope, "DocumentBuilder")
    assert isinstance(result, list)
    assert result
    for match in result:
        assert any(MARKER in line for line in match.known_defects), extract_fqn(match.text)


@pytest.mark.parametrize("fqn", [MARKDOWN_BUILDER, HTML_OPTIONS])
def test_get_symbol_of_a_look_alike_carries_the_disclosure(corpus: ConformanceCorpus, fqn: str) -> None:
    scope = corpus.pilots["words__python"].scope
    answer = get_symbol(corpus.store, scope, fqn)
    assert isinstance(answer, SymbolSignature), answer
    assert any(MARKER in line for line in answer.known_defects), answer.known_defects


def test_a_real_words_python_type_does_not_carry_the_disclosure(corpus: ConformanceCorpus) -> None:
    scope = corpus.pilots["words__python"].scope
    answer = get_symbol(corpus.store, scope, "aspose.words_foss.document.Document")
    assert isinstance(answer, SymbolSignature), answer
    assert not any(MARKER in line for line in answer.known_defects)


def test_words_net_which_really_has_documentbuilder_gets_no_such_disclosure(
    corpus: ConformanceCorpus,
) -> None:
    scope = corpus.pilots["words__net"].scope
    for match in _symbols(lookup(corpus.store, scope, "DocumentBuilder")):
        assert not any(MARKER in line for line in match.known_defects)


def test_the_registry_file_is_the_committed_one() -> None:
    path = Path(__file__).resolve().parents[3] / "config" / "known_defects" / "words__python.yaml"
    assert DEFECT_ID in path.read_text(encoding="utf-8")
