"""Resolve a furnished-content citation by semantic anchor, never by aspose.org's claim id.

aspose.org mints a "claim id" (``CLM-...`` / ``ERC-...``) by hashing an anchor string plus a
kind tag, using a private regex that is an internal implementation detail rather than a
published contract - aspose.org's own team broke citation continuity once by changing it. An
independent extractor cannot reproduce that hash space and must not try.

Instead, this bridge matches on the SEMANTIC ANCHOR (``ClassName.MemberName`` / a symbol's fully
qualified name), which both aspose.org and foss-mcp's own extraction derive independently from
the same source. An anchor "resolves" only when it names exactly one declaration in foss-mcp's
own extraction: zero declarations means the API was renamed or removed (``unsupported``); two or
more means the anchor is genuinely ambiguous between overloads, and the bridge is never allowed
to guess which one a citation meant (``partially_supported``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

# aspose.org's own opaque id space (docs/RESEARCH_AND_GUIDELINES equivalent: 4.7a). Never a
# semantic anchor, and never looked up as one - checked before any index access, so it cannot
# collide with a real anchor by accident.
_OPAQUE_CLAIM_ID = re.compile(r"^(CLM|ERC)-", re.IGNORECASE)


class SemanticSupportVerdict(str, Enum):
    """Whether foss-mcp's own extraction backs a citation's anchor."""

    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class Declaration:
    """One declaration foss-mcp's own extraction found, named by its semantic anchor.

    ``signature`` distinguishes overloads sharing one anchor; it carries no meaning beyond that
    (the bridge never picks among overloads, so it never inspects it).
    """

    anchor: str
    signature: str = ""


@dataclass(frozen=True)
class Resolution:
    """The verdict for one citation anchor, plus how many declarations backed it."""

    anchor: str
    semantic_support_verdict: SemanticSupportVerdict
    match_count: int = 0


class SymbolIndex:
    """An in-memory index from semantic anchor to the declarations foss-mcp's own extraction
    found there - one entry per declaration, so an anchor with two overloads has two entries.
    """

    def __init__(self, declarations: Iterable[Declaration] = ()) -> None:
        self._by_anchor: dict[str, list[Declaration]] = {}
        for declaration in declarations:
            self._by_anchor.setdefault(declaration.anchor, []).append(declaration)

    @classmethod
    def from_anchors(cls, anchors: Iterable[str]) -> SymbolIndex:
        """Build from bare anchor strings, one per declared overload."""
        return cls(Declaration(anchor=anchor) for anchor in anchors)

    def declarations_for(self, anchor: str) -> tuple[Declaration, ...]:
        return tuple(self._by_anchor.get(anchor, ()))


def _is_opaque_claim_id(candidate: str) -> bool:
    return bool(_OPAQUE_CLAIM_ID.match(candidate))


def anchor_resolves(anchor: str, index: SymbolIndex) -> bool:
    """True only when *anchor* names exactly one declaration - a clean, unambiguous resolution.

    An anchor with zero declarations does not resolve (renamed or removed); one with two or more
    does not resolve either, because resolving it would mean guessing which overload a citation
    meant, and the ambiguity rule forbids that.
    """
    if _is_opaque_claim_id(anchor):
        return False
    return len(index.declarations_for(anchor)) == 1


def resolve_anchor(anchor: str, index: SymbolIndex) -> Resolution:
    """The full verdict for *anchor*: supported, partially_supported, or unsupported."""
    if _is_opaque_claim_id(anchor):
        return Resolution(anchor, SemanticSupportVerdict.UNSUPPORTED, match_count=0)
    matches = index.declarations_for(anchor)
    if anchor_resolves(anchor, index):
        return Resolution(anchor, SemanticSupportVerdict.SUPPORTED, match_count=len(matches))
    if matches:
        return Resolution(anchor, SemanticSupportVerdict.PARTIALLY_SUPPORTED, match_count=len(matches))
    return Resolution(anchor, SemanticSupportVerdict.UNSUPPORTED, match_count=0)
