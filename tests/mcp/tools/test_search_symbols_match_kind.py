"""search_symbols' own ``match_kind`` field (TC-673, G3/REQ-G3-011, Q13).

Q13 named three things: "rank type > member > namespace", "match_kind" and "namespace words never
exact". This card's own investigation (see ``search_symbols.py``'s module docstring, TC-673's own
paragraph) found the first and third ALREADY the real, correct behaviour - untouched here except for
one new regression test (below) pinning the third. The genuinely missing piece was ``match_kind``
itself: ``SymbolMatch`` carried ``match_quality`` (``"exact"``/``"partial"``) but no way to tell WHICH
of the five admission tiers, or which of the three further fallback stages, actually produced a given
hit. This file proves the new field is populated with the right, honestly-named literal at every one
of those eight sites, and that doing so changes nothing about ranking order or any existing
``match_quality`` value - the same in-memory ``_InMemoryStore``/``_type_chunk`` fixture pattern
``test_search_symbols_prefix_tier.py`` and ``test_search_symbols_forgiveness.py`` already use, plus a
COPY of the real committed ``pdf/go`` pilot data for one byte-for-byte regression proof against a
real, previously-measured example (``SetPassword``).

Offline: no network, no container, no embedding gateway. The one real-pilot test below reads a COPY of
the committed ``infra/demo-manifests`` pdf/go scope directory, never the original.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.lexical_index_writer import build_lexical_index
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_symbol import extract_fqn
from foss_mcp.mcp.tools.search_symbols import MATCH_EXACT, MATCH_PARTIAL, Miss, SymbolMatch, search_symbols

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
SCOPE = Scope(family="sample", platform="python")
PDF_GO = Scope(family="pdf", platform="go")
_GENERATION_ID = "gen-tc673-match-kind"

# The card's own real, previously-measured RST-14 example (see test_search_symbols_forgiveness.py).
DECLARING = {"TextBoxField", "Document"}
INHERITING = {
    "RichTextBoxField",
    "NumberField",
    "DateField",
    "FileSelectBoxField",
    "BarcodeField",
    "PasswordBoxField",
}


class _InMemoryStore:
    """Stands in for GenerationManifestStore: one active generation, held entirely in memory - the
    same pattern ``test_search_symbols_prefix_tier.py``/``test_search_symbols_forgiveness.py`` already
    established."""

    def __init__(self, *chunk_texts: str) -> None:
        chunks = [SimpleNamespace(text=text) for text in chunk_texts]
        lexical = build_lexical_index(chunks, [f"chunk-{i}" for i in range(len(chunks))], _GENERATION_ID)  # type: ignore[arg-type]
        self._manifest = SimpleNamespace(payload={"lexical_index": lexical})

    def read_active(self, scope: str) -> str:
        return _GENERATION_ID

    def read_generation(self, scope: str, generation_id: str) -> SimpleNamespace:
        return self._manifest


def _type_chunk(fqn: str, *members: str) -> str:
    lines = [f"FQN: {fqn}", "Kind: class_declaration"]
    if members:
        lines.append("Methods:")
        lines.extend(f"  - {member}" for member in members)
    return "\n".join(lines) + "\n"


def _search(store: object, query: str, *, scope: Scope = SCOPE) -> object:
    return search_symbols(store, scope, query)  # type: ignore[arg-type]


def _matches(result: object) -> list[SymbolMatch]:
    assert isinstance(result, list), f"expected a list of matches, got {result!r}"
    assert all(isinstance(match, SymbolMatch) for match in result)
    return result


def _by_fqn(matches: list[SymbolMatch]) -> dict[str, SymbolMatch]:
    return {(extract_fqn(match.text) or "").strip(): match for match in matches}


# ---- (a) full-FQN query -> "exact_fqn" ----------------------------------------------------------


def test_a_full_fqn_query_gets_exact_fqn_match_kind() -> None:
    store = _InMemoryStore(_type_chunk("Sample.Pdf.PdfDocument"))

    result = _matches(_search(store, "Sample.Pdf.PdfDocument"))

    assert len(result) == 1
    assert result[0].match_kind == "exact_fqn"
    assert result[0].match_quality == MATCH_EXACT  # unchanged by this card


# ---- (b) final-segment-only query -> "final_segment" -------------------------------------------


def test_a_final_segment_query_gets_final_segment_match_kind() -> None:
    store = _InMemoryStore(_type_chunk("Sample.Pdf.PdfDocument"))

    result = _matches(_search(store, "PdfDocument"))

    assert len(result) == 1
    assert result[0].match_kind == "final_segment"
    assert result[0].match_quality == MATCH_EXACT  # unchanged by this card


# ---- (c) member-name query -> "member_declared" / "member_inherited" correctly distinguished ---


def test_a_member_query_distinguishes_declared_from_inherited_match_kind() -> None:
    # The same declaring-before-inheriting fixture shape test_search_symbols_memberless_last.py and
    # test_search_symbols_forgiveness.py already use for this exact tier.
    store = _InMemoryStore(
        _type_chunk("Pkg.Base", "Reset()"),
        _type_chunk("Pkg.Child", "Reset()  (inherited from Base)"),
    )

    result = _matches(_search(store, "Reset"))
    by_fqn = _by_fqn(result)

    assert by_fqn["Pkg.Base"].match_kind == "member_declared"
    assert by_fqn["Pkg.Child"].match_kind == "member_inherited"
    # Neither existing match_quality value moved.
    assert by_fqn["Pkg.Base"].match_quality == MATCH_EXACT
    assert by_fqn["Pkg.Child"].match_quality == MATCH_EXACT


@pytest.fixture(scope="module")
def pdf_go_store(tmp_path_factory: pytest.TempPathFactory) -> GenerationManifestStore:
    root = tmp_path_factory.mktemp("match_kind_pdf_go")
    shutil.copytree(DEMO_MANIFESTS / "pdf__go__self_extracted", root / "pdf__go__self_extracted")
    return GenerationManifestStore(root)


def test_set_password_on_real_pdf_go_data_keeps_its_existing_quality_and_ordering(
    pdf_go_store: GenerationManifestStore,
) -> None:
    """(f) Byte-for-byte regression proof against the card's own real, previously-measured example
    (RST-14, pdf/go ``SetPassword``): the declaring-before-inheriting ORDER and every existing
    ``match_quality`` value are completely unchanged; ``match_kind`` now additionally, correctly
    labels each one "member_declared"/"member_inherited"."""
    result = _matches(search_symbols(pdf_go_store, PDF_GO, "SetPassword"))
    names = [(extract_fqn(match.text) or "").split(".")[-1] for match in result]

    assert set(names) == DECLARING | INHERITING
    assert names.index("Document") < 2
    last_declaring = max(names.index(name) for name in DECLARING)
    assert all(names.index(name) > last_declaring for name in INHERITING), names
    # Pre-existing behaviour, unchanged by this card.
    assert all(match.match_quality == MATCH_EXACT for match in result)
    for name, match in zip(names, result, strict=True):
        expected_kind = "member_declared" if name in DECLARING else "member_inherited"
        assert match.match_kind == expected_kind, (name, match.match_kind)


# ---- (d) bag-of-words-only query -> "bag_of_words" ----------------------------------------------

_WATERMARK_STORE = _InMemoryStore(
    _type_chunk("Sample.PdfDocument.AddWatermarkAnnotation"),
    _type_chunk("Sample.Networking.HttpClient"),
)


def test_a_bag_of_words_query_gets_bag_of_words_match_kind() -> None:
    result = _matches(_search(_WATERMARK_STORE, "watermark"))

    hit = _by_fqn(result)["Sample.PdfDocument.AddWatermarkAnnotation"]
    assert hit.match_kind == "bag_of_words"
    assert hit.match_quality == MATCH_EXACT  # unchanged by this card


# ---- (e) a genuine prefix query (TC-654's own "Watermar"/"AFRelation" examples) -> "prefix" -----


def test_a_prefix_only_query_gets_prefix_match_kind() -> None:
    result = _matches(_search(_WATERMARK_STORE, "Watermar"))

    hit = _by_fqn(result)["Sample.PdfDocument.AddWatermarkAnnotation"]
    assert hit.match_kind == "prefix"
    assert hit.match_quality == MATCH_PARTIAL  # unchanged by this card (TC-654's own floor)


def test_an_afrelation_prefix_query_gets_prefix_match_kind_too() -> None:
    store = _InMemoryStore(_type_chunk("Aspose::Pdf::AFRelationship"))

    result = _matches(_search(store, "AFRelation"))

    hit = _by_fqn(result)["Aspose::Pdf::AFRelationship"]
    assert hit.match_kind == "prefix"
    assert hit.match_quality == MATCH_PARTIAL


# ---- bonus coverage: the three further fallback stages (not required by the card's own 7 proofs,
# but the same honest labelling this card adds, so every one of the eight literal values is pinned) --


def test_a_single_plural_query_gets_inflected_name_match_kind() -> None:
    store = _InMemoryStore(_type_chunk("Pkg.Widget", "Run()"))

    result = _matches(_search(store, "Widgets"))

    assert result[0].match_kind == "inflected_name"
    assert result[0].match_quality == MATCH_EXACT  # unchanged by this card


def test_a_query_only_the_per_token_fallback_can_explain_gets_token_fallback_match_kind() -> None:
    # "using"/"property" are both noise words naming_tokens itself drops (_NAMING_NOISE_WORDS) but
    # _evidence_ranking's own, separately-maintained noise list does not - so they count as two
    # extra, unexplainable (foreign) concepts there, raising evidence_ranking's own `needed` floor
    # (ceil(0.6 * 4) == 3) out of reach of the two real, genuinely-explained words ("send" via the
    # member, "email" via the type name) alone; evidence_ranking is confirmed empty for this query
    # (see this test), so only the gated per-token fallback (naming_tokens' own smaller, 2-word
    # total, needed == 2) ever admits it - confirmed directly: the resulting match_kind is
    # "token_fallback", not "evidence_sentence".
    store = _InMemoryStore(
        _type_chunk("Sample.Email", "Send()"),
        _type_chunk("Sample.Mailbox", "Open()"),
    )
    query = "send email using property"

    result = _matches(_search(store, query))

    hit = _by_fqn(result)["Sample.Email"]
    assert hit.match_kind == "token_fallback"
    assert hit.match_quality == MATCH_EXACT  # unchanged by this card


def test_a_sentence_only_the_evidence_stage_can_explain_gets_evidence_sentence_match_kind() -> None:
    store = _InMemoryStore(_type_chunk("Pkg.Widget", "DoSomething()"), _type_chunk("Pkg.Other"))

    # "add a widget" only the sentence-evidence stage (TC-562) can answer - no tier-based match, no
    # bare member name, no single-word inflection (two real words, one of them a light verb).
    result = _matches(_search(store, "add a widget", scope=Scope(family="widgets", platform="python")))

    hit = _by_fqn(result)["Pkg.Widget"]
    assert hit.match_kind == "evidence_sentence"


# ---- (g) regression: the EXISTING "namespace words never exact" guarantee, pinned, no code change --


def test_internal_namespace_word_never_reaches_match_exact_via_evidence_sentence() -> None:
    """Point 2 of this card's own purpose: ``_evidence_ranking``'s own ``_Scored.exact`` property
    already requires ``not self.internal`` - a candidate living in an internal-looking namespace
    (``_INTERNAL_NAMESPACE``: ``.internal.``/``.core.``/``.xml.``/``.engine.``/``.impl.``) never
    returns ``MATCH_EXACT`` through the sentence-evidence fallback, even when it is the only
    candidate that explains every strong concept of the query via a member match (``via_member``).
    This is a NEW TEST ONLY pinning already-existing behaviour - no change to ``_Scored``,
    ``_evidence_ranking`` or ``_INTERNAL_NAMESPACE`` themselves.
    """
    # "protect" -> USER_TERM_SYNONYMS widens it to "password", which is a real word of the type's own
    # declared member "SetPassword" - strong enough evidence to admit the type, but it lives under
    # ".internal." so it must still never be reported as an exact match.
    store = _InMemoryStore(_type_chunk("Sample.internal.SecurityHelper", "SetPassword()"))

    result = _matches(_search(store, "protect"))

    assert len(result) == 1
    assert result[0].match_kind == "evidence_sentence"
    assert result[0].match_quality == MATCH_PARTIAL  # never MATCH_EXACT for an internal namespace hit


def test_the_same_member_evidence_is_match_partial_once_the_type_is_not_internal_but_has_zero_fit() -> None:
    """G3/TC-703 UPDATE: same evidence, same member, no internal namespace this time - originally a
    sanity control proving the difference above was really the namespace check, not some other
    accident of this fixture, back when ``_Scored.exact`` granted ``exact`` via ``via_member`` alone
    with no name-fit threshold at all. TC-703 (REQ-G3-011, closing CELLS-RUST-001/CELLS-CPP-007/
    CELLS-NET-009) now requires ``fit > 0.0`` even on the ``via_member`` path. This fixture's own fit
    is genuinely 0 - no word of ``SecurityHelper``'s own name matches "protect"/"password"; the
    member-only evidence is the type's SOLE explanation - so even with the internal-namespace
    variable controlled away (removed here, unlike the sibling test above), the honest result is now
    MATCH_PARTIAL, never MATCH_EXACT: removing the internal-namespace penalty no longer rescues a
    zero-fit via_member hit on its own."""
    store = _InMemoryStore(_type_chunk("Sample.SecurityHelper", "SetPassword()"))

    result = _matches(_search(store, "protect"))

    assert len(result) == 1
    assert result[0].match_kind == "evidence_sentence"
    assert result[0].match_quality == MATCH_PARTIAL


def test_a_nonzero_fit_via_member_hit_still_reaches_match_exact_once_not_internal() -> None:
    """G3/TC-703's own positive-branch proof: ``_Scored.exact``'s new condition is
    ``fit >= 0.5 or (via_member and fit > 0.0)`` - the sibling test above pins the negative branch
    (via_member with fit == 0 stays partial); this one pins that a via_member hit with GENUINE,
    nonzero (but sub-0.5) name fit still reaches MATCH_EXACT once the type is not internal, so the
    fix narrows the via_member shortcut rather than deleting it. The fixture explains "workbook" via
    the type's own name directly (contributing real, nonzero fit) and "password" only via its
    declared ``SetPassword`` member (``via_member=True``), so fit is nonzero but well under 0.5."""
    store = _InMemoryStore(_type_chunk("Sample.WorkbookSecurityHelper", "SetPassword()"))

    result = _matches(_search(store, "workbook password"))

    assert len(result) == 1
    assert result[0].match_kind == "evidence_sentence"
    assert result[0].match_quality == MATCH_EXACT


# ---- a Miss never carries a match_kind at all (there is no SymbolMatch to carry one) -------------


def test_a_genuine_miss_is_still_a_miss_unaffected_by_this_card() -> None:
    result = _search(_WATERMARK_STORE, "Zephyrine")

    assert isinstance(result, Miss)
    assert result.reason == "no symbol matches 'Zephyrine'"
