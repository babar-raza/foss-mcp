"""TC-637 (G3/REQ-G3-011): ``_symbol_words``'s digit-to-uppercase ``_CAMEL_BOUNDARY`` rule is correct
for a real new-word boundary (``Code128EncodeMode`` -> ``code128``, ``encode``, ``mode``) but treats a
digit followed by a SINGLE trailing uppercase letter or a short, mid-identifier uppercase acronym
fragment (a real unit/format suffix such as ``2D``, ``3D``, ``3MF``) the same as a genuine new-word
boundary, destroying it: ``ITexture2D`` -> ``i``, ``texture2``, ``d`` (``2D`` glued into a meaningless
bare ``d``), ``Microsoft3MFFormat`` -> ``microsoft3``, ``mf``, ``format`` (the real file-format acronym
``3MF`` lost entirely).

``_rejoin_digit_acronym_split`` fixes this as a post-processing step right after ``_symbol_words``'s own
split, without touching ``_CAMEL_BOUNDARY`` itself: it moves the stranded trailing digit run off the stem
and onto the acronym/unit fragment that follows it, so ``ITexture2D`` -> ``i``, ``texture``, ``2d`` and
``Microsoft3MFFormat`` -> ``microsoft``, ``3mf``, ``format`` - the unit/acronym becomes its own standalone,
whole-word-matchable token, which is what then lets a query naming only the unit (``"2d"``, ``"3mf"``)
become a real tier-2 bag-of-words hit.

Measured directly against every real digit-containing final-segment symbol name found across
``infra/demo-manifests/*/generation__*.json`` (124 names on main d7b594fd's demo manifests): 30 changed,
all a genuine over-split acronym/unit suffix corrected; the other 94 - including every already-correct
case this card names (``Code128EncodeMode``, ``Ean13Options``, ``PDF17_NS``, ``PDF20_NS``, ``PKCS1``,
``PKCS7``) - are untouched. See ``test_the_fix_only_changes_a_real_over_split_shape_across_the_whole_real_corpus``
below, which re-measures this directly against the committed corpus rather than trusting a fixed number.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

import pytest

from foss_mcp.indexing.lexical_index_writer import build_lexical_index
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_symbol import extract_fqn
from foss_mcp.mcp.tools.search_symbols import (
    _CAMEL_BOUNDARY,
    _FINAL_SEGMENT,
    _WORD_SEPARATORS,
    Miss,
    SymbolMatch,
    _match_tier,
    _rejoin_digit_acronym_split,
    _symbol_words,
    search_symbols,
)
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
SCOPE = Scope(family="pkg", platform="python")
_GENERATION_ID = "gen-tc637"


def _pre_fix_symbol_words(text: str) -> list[str]:
    """``_symbol_words``'s own body before this card - the same ``_CAMEL_BOUNDARY``/``_WORD_SEPARATORS``
    constants, with no digit-acronym rescue - reconstructed here (not duplicated as a separate regex) so
    the "before" baseline can never silently drift out of sync with the real, still-unmodified constants
    this card's fix is layered on top of."""
    spaced = _CAMEL_BOUNDARY.sub(" ", text)
    return [word.casefold() for word in _WORD_SEPARATORS.split(spaced) if word]


# ------------------------------------------------------------------------------------- the 6 named repro cases


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        # Broken before this card; must be fixed now.
        ("ITexture2D", ["i", "texture", "2d"]),
        ("A3DObject", ["a", "3d", "object"]),
        ("Microsoft3MFFormat", ["microsoft", "3mf", "format"]),
        # Already correct today; must not regress.
        ("Code128EncodeMode", ["code128", "encode", "mode"]),
        ("Ean13Options", ["ean13", "options"]),
        ("PDF17_NS", ["pdf17", "ns"]),
    ],
)
def test_symbol_words_on_the_six_named_repro_cases(name: str, expected: list[str]) -> None:
    assert _symbol_words(name) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("ITexture2D", ["i", "texture2", "d"]),
        ("A3DObject", ["a3", "d", "object"]),
        ("Microsoft3MFFormat", ["microsoft3", "mf", "format"]),
    ],
)
def test_the_three_broken_cases_really_were_broken_before_this_card(name: str, expected: list[str]) -> None:
    """Guards against a vacuous pass of the test above: confirms the pre-fix baseline really does
    reproduce the exact defect this card's purpose describes, independently of the real fix."""
    assert _pre_fix_symbol_words(name) == expected


# ------------------------------------------------------------------------------------- the helper in isolation


@pytest.mark.parametrize(
    ("words", "expected"),
    [
        ([], []),
        (["Options"], ["Options"]),  # no trailing digit anywhere: untouched
        (["Code128", "Encode", "Mode"], ["Code128", "Encode", "Mode"]),  # real word follows: untouched
        (["Texture2", "D"], ["Texture", "2D"]),  # bare trailing letter: always rejoins
        (["Rotation3", "D"], ["Rotation", "3D"]),  # bare trailing letter at the very end: still rejoins
        (["Microsoft3", "MF", "Format"], ["Microsoft", "3MF", "Format"]),  # mid-identifier acronym fragment
        (["PDF17", "NS"], ["PDF17", "NS"]),  # same acronym shape, but it is the LAST word: untouched
        (["3", "D", "Foo"], ["3D", "Foo"]),  # digit run is the whole first word: no empty word emitted
    ],
)
def test_rejoin_digit_acronym_split_directly(words: list[str], expected: list[str]) -> None:
    assert _rejoin_digit_acronym_split(words) == expected


# ------------------------------------------------------------------------------------- _match_tier end to end


@pytest.mark.parametrize(
    ("query", "fqn"),
    [
        ("2d", "Aspose.ThreeD.ITexture2D"),
        ("3d", "Aspose.ThreeD.A3DObject"),
        ("3mf", "Aspose.ThreeD.Microsoft3MFFormat"),
        ("texture 2d", "Aspose.ThreeD.ITexture2D"),
    ],
)
def test_match_tier_now_finds_the_bare_unit_or_acronym(query: str, fqn: str) -> None:
    assert _match_tier(query, fqn) == 2


@pytest.mark.parametrize(
    ("query", "fqn"),
    [
        ("2d", "Aspose.ThreeD.ITexture2D"),
        ("3mf", "Aspose.ThreeD.Microsoft3MFFormat"),
    ],
)
def test_match_tier_would_have_missed_this_before_the_fix(query: str, fqn: str) -> None:
    """The same query against the SAME real FQN, replayed through the pre-fix word list: the bare
    unit/acronym word the query names never existed as a whole word, so it was an honest Miss."""
    pre_fix_words = _pre_fix_symbol_words(fqn)
    assert query not in pre_fix_words


# ------------------------------------------------------------------------------------- search_symbols end to end


class _InMemoryStore:
    """One active generation of in-memory chunks, standing in for GenerationManifestStore."""

    def __init__(self, *bodies: str) -> None:
        chunks = []
        for body in bodies:
            doc = make_document(
                source_kind=SourceKind.SELF_EXTRACTED,
                content_type="api_surface",
                provenance=Provenance(repository="org/repo", commit="tc637"),
                evidence_refs=(),
                title="pkg/python API surface",
                body=body,
            )
            chunks.extend(chunk_document(doc))
        chunk_ids = [f"chunk-{index}" for index in range(len(chunks))]
        self.lexical = build_lexical_index(chunks, chunk_ids, _GENERATION_ID)
        self._manifest = SimpleNamespace(payload={"lexical_index": self.lexical})

    def read_active(self, scope: str) -> str:
        return _GENERATION_ID

    def read_generation(self, scope: str, generation_id: str) -> SimpleNamespace:
        return self._manifest


def _chunk(fqn: str, *, filler: str = "") -> str:
    lines = [f"## {fqn}", "", f"FQN: {fqn}", "Kind: class"]
    if filler:
        lines.append(filler)
    return "\n".join(lines) + "\n"


def _fqns(result: list[SymbolMatch] | Miss) -> list[str]:
    assert not isinstance(result, Miss), result
    return [str(extract_fqn(match.text)) for match in result]


def test_search_symbols_now_finds_a_real_pilot_symbol_by_its_unit_suffix_alone() -> None:
    store = _InMemoryStore(
        _chunk("Aspose.ThreeD.ITexture2D"),
        _chunk(
            "Aspose.ThreeD.Unrelated",
            filler="A long filler sentence unrelated to any texture or dimensionality at all.",
        ),
    )

    assert _fqns(search_symbols(store, SCOPE, "2d")) == ["Aspose.ThreeD.ITexture2D"]  # type: ignore[arg-type]


def test_search_symbols_finds_the_same_symbol_by_a_natural_word_order_sentence() -> None:
    store = _InMemoryStore(_chunk("Aspose.ThreeD.ITexture2D"))

    assert _fqns(search_symbols(store, SCOPE, "texture 2d")) == [  # type: ignore[arg-type]
        "Aspose.ThreeD.ITexture2D"
    ]


def test_search_symbols_finds_the_3mf_format_acronym() -> None:
    store = _InMemoryStore(_chunk("Aspose.ThreeD.Microsoft3MFFormat"))

    assert _fqns(search_symbols(store, SCOPE, "3mf")) == ["Aspose.ThreeD.Microsoft3MFFormat"]  # type: ignore[arg-type]


# ------------------------------------------------------------------------------------- the real, whole corpus


_NON_SYMBOL_FQN_PREFIXES = ("Example: ", "Doc: ")


@lru_cache(maxsize=1)
def _real_digit_containing_names() -> tuple[str, ...]:
    """Every real final-segment symbol name with a digit in it, found by parsing
    ``payload.lexical_index.documents`` of every committed ``infra/demo-manifests/*/generation__*.json``
    (not a hand-picked subset), per this card's own measurement instruction. Excludes names that are
    purely numeric and TC-068/TC-112's own non-symbol pseudo-FQNs (``Example: ...``, ``Doc: ...``) - those
    are prose titles, not real symbol names, and are never fed through ``_symbol_words`` in production."""
    names: set[str] = set()
    for generation_file in sorted(DEMO_MANIFESTS.glob("*/generation__*.json")):
        data = json.loads(generation_file.read_text(encoding="utf-8"))
        payload = data.get("payload") or {}
        documents = (payload.get("lexical_index") or {}).get("documents") or {}
        for document in documents.values():
            text = document.get("text", "")
            fqn_line = next(
                (line[len("FQN:") :].strip() for line in text.splitlines() if line.startswith("FQN:")),
                None,
            )
            if fqn_line is None or fqn_line.startswith(_NON_SYMBOL_FQN_PREFIXES):
                continue
            final = _FINAL_SEGMENT.split(fqn_line)[-1]
            if not final or not any(character.isdigit() for character in final):
                continue
            if final.replace("_", "").isdigit():
                continue
            names.add(final)
    return tuple(sorted(names))


def test_the_real_corpus_has_a_real_population_of_digit_containing_symbol_names() -> None:
    # Measured 121 real symbol names (124 including the 3 non-symbol "Example:"/pseudo-title entries this
    # production-faithful extraction excludes) on main d7b594fd's committed demo manifests. A loose floor
    # guards against silent corpus drift emptying this measurement without anyone noticing.
    assert len(_real_digit_containing_names()) >= 100


_ALREADY_CORRECT_REAL_NAMES = {
    "Code128EncodeMode": ["code128", "encode", "mode"],
    "Code39EncodeMode": ["code39", "encode", "mode"],
    "Ean13Options": ["ean13", "options"],
    "Ean8Options": ["ean8", "options"],
    "PDF17_NS": ["pdf17", "ns"],
    "PDF20_NS": ["pdf20", "ns"],
    "PKCS1": ["pkcs1"],
    "PKCS7": ["pkcs7"],
}


def test_every_named_already_correct_real_symbol_in_the_corpus_is_unchanged() -> None:
    real_names = set(_real_digit_containing_names())
    checked = [name for name in _ALREADY_CORRECT_REAL_NAMES if name in real_names]
    assert len(checked) >= 4, "guard against a vacuous pass: none of the known-correct names were found"
    for name in checked:
        assert _symbol_words(name) == _ALREADY_CORRECT_REAL_NAMES[name]
        # And the fix is a genuine no-op for these: the pre-fix split already agreed.
        assert _pre_fix_symbol_words(name) == _ALREADY_CORRECT_REAL_NAMES[name]


def test_the_fix_only_changes_a_real_over_split_shape_across_the_whole_real_corpus() -> None:
    """Re-measures this card's own claim directly against the committed corpus, rather than trusting a
    fixed number written down once: every real digit-containing final-segment name that changes between
    the pre-fix and the fixed splitter changes because of exactly the over-split shape this card
    describes (a digit-ending word immediately followed by a bare single letter or a short, mid-identifier
    uppercase acronym fragment) - never anything else - and the fix changes a real, non-trivial number of
    them (never a vacuous no-op)."""
    changed: list[str] = []
    for name in _real_digit_containing_names():
        before = _pre_fix_symbol_words(name)
        after = _symbol_words(name)
        if before == after:
            continue
        changed.append(name)
        # Every change must be explained by the exact over-split shape: SOME adjacent pair in the pre-fix
        # words is a digit-ending word immediately followed by a bare letter or short all-caps fragment.
        assert any(
            before[index]
            and before[index][-1].isdigit()
            and _looks_like_a_stranded_acronym_fragment(before[index + 1])
            for index in range(len(before) - 1)
        ), f"{name}: {before} -> {after} changed for a reason other than the over-split shape"
    # Measured 30 on main d7b594fd's committed demo manifests; a loose floor guards against a vacuous pass.
    assert len(changed) >= 20


def _looks_like_a_stranded_acronym_fragment(word: str) -> bool:
    # `before`/`after` here are already casefolded (``_pre_fix_symbol_words``/``_symbol_words`` both
    # casefold), so this checks shape (short, letters-only) rather than the original upper case directly -
    # the production rejoin logic itself checks case on the pre-casefold words; this test-only helper just
    # needs to recognize the same short-fragment SHAPE after the fact.
    return bool(word) and word.isalpha() and len(word) <= 2


def test_no_real_corpus_name_still_has_the_bug_shape_after_the_fix() -> None:
    """The structural invariant the fix exists to establish: after ``_symbol_words``, no digit-ending word
    is ever immediately followed by a bare single-letter word, for any real name in the whole corpus."""
    offenders: list[str] = []
    for name in _real_digit_containing_names():
        words = _symbol_words(name)
        for index in range(len(words) - 1):
            if words[index] and words[index][-1].isdigit() and len(words[index + 1]) == 1:
                offenders.append(f"{name}: {words}")
                break
    assert offenders == []
