"""The claim-ID bridge: resolution by semantic anchor, never by aspose.org's opaque claim id."""

from __future__ import annotations

from foss_mcp.extraction.claim_id_bridge import (
    Declaration,
    SemanticSupportVerdict,
    SymbolIndex,
    anchor_resolves,
    resolve_anchor,
)


def _index() -> SymbolIndex:
    return SymbolIndex(
        [
            Declaration("Widget.Save", signature="Save(string path)"),
            Declaration("Widget.GetStyle", signature="GetStyle()"),
            Declaration("Widget.GetStyle", signature="GetStyle(int index)"),
        ]
    )


def test_a_single_match_resolves_and_is_supported() -> None:
    index = _index()
    assert anchor_resolves("Widget.Save", index) is True
    resolution = resolve_anchor("Widget.Save", index)
    assert resolution.semantic_support_verdict == SemanticSupportVerdict.SUPPORTED
    assert resolution.semantic_support_verdict == "supported"
    assert resolution.match_count == 1


def test_a_renamed_or_removed_api_is_unsupported() -> None:
    index = _index()
    assert anchor_resolves("Widget.LongGone", index) is False
    resolution = resolve_anchor("Widget.LongGone", index)
    assert resolution.semantic_support_verdict == "unsupported"
    assert resolution.match_count == 0


def test_an_anchor_matching_two_overloads_is_partially_supported_never_a_guess() -> None:
    index = _index()
    assert anchor_resolves("Widget.GetStyle", index) is False
    resolution = resolve_anchor("Widget.GetStyle", index)
    assert resolution.semantic_support_verdict == "partially_supported"
    assert resolution.match_count == 2


def test_an_opaque_claim_id_is_never_used_as_a_lookup_key() -> None:
    """Even when an index coincidentally has an entry keyed by the literal opaque id string,
    the bridge must not treat it as a semantic anchor."""
    index = SymbolIndex([Declaration("CLM-deadbeef"), Declaration("ERC-000123")])
    for opaque in ("CLM-deadbeef", "ERC-000123", "clm-deadbeef"):
        assert anchor_resolves(opaque, index) is False
        assert resolve_anchor(opaque, index).semantic_support_verdict == "unsupported"


def test_resolve_anchor_and_anchor_resolves_agree_on_every_verdict() -> None:
    index = _index()
    for anchor in ("Widget.Save", "Widget.GetStyle", "Widget.LongGone", "CLM-xyz"):
        supported = resolve_anchor(anchor, index).semantic_support_verdict == "supported"
        assert anchor_resolves(anchor, index) == supported


def test_symbol_index_from_anchors_builds_one_declaration_per_string() -> None:
    index = SymbolIndex.from_anchors(["A.b", "A.b", "A.c"])
    assert resolve_anchor("A.b", index).match_count == 2
    assert resolve_anchor("A.c", index).match_count == 1
    assert resolve_anchor("A.d", index).match_count == 0
