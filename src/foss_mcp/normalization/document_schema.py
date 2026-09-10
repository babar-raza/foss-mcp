"""The one versioned document envelope every source normalizes into.

Three sources feed this schema, and they are not equally trustworthy: what `foss_mcp.extraction`
reads directly from source code (self-extracted) is ground truth about the code itself; a
furnished product page is marketing prose, independently re-verifiable but not authored by the
code; a repository's own manifest is high-trust structured data, while its release notes range
from a real migration note to a filled-in template that says nothing (see
``foss_mcp.extraction.github_release_reader.classify_richness``). ``trust_tier_for`` keeps that
distinction explicit rather than flattening every source into one undifferentiated "content".
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

SCHEMA_VERSION = "1"


class SourceKind(str, Enum):
    SELF_EXTRACTED = "self_extracted"
    FURNISHED = "furnished"
    REPO_NATIVE_MANIFEST = "repo_native_manifest"
    REPO_NATIVE_RELEASE_NOTES = "repo_native_release_notes"


class TrustTier(str, Enum):
    HIGHEST = "highest"
    HIGH = "high"
    MEDIUM = "medium"
    VARIABLE = "variable"


_STATIC_TRUST_TIER: dict[SourceKind, TrustTier] = {
    SourceKind.SELF_EXTRACTED: TrustTier.HIGHEST,
    SourceKind.REPO_NATIVE_MANIFEST: TrustTier.HIGH,
    SourceKind.FURNISHED: TrustTier.MEDIUM,
}

# A release note's trust depends on what it actually says, not just where it came from - the
# richest a release body can be classified (github_release_reader.classify_richness) sets the
# ceiling: a real migration note is trustworthy prose, a filled-in template or an empty body
# is not, however well-formatted either one looks.
_RELEASE_NOTE_TRUST_BY_RICHNESS: dict[str, TrustTier] = {
    "detailed": TrustTier.HIGH,
    "templated": TrustTier.VARIABLE,
    "none": TrustTier.VARIABLE,
}


def trust_tier_for(source_kind: SourceKind | str, *, richness: str | None = None) -> TrustTier:
    """The trust tier for *source_kind* - dynamic for release notes, static for everything else."""
    kind = SourceKind(source_kind)
    if kind is SourceKind.REPO_NATIVE_RELEASE_NOTES:
        return _RELEASE_NOTE_TRUST_BY_RICHNESS.get(richness or "none", TrustTier.VARIABLE)
    return _STATIC_TRUST_TIER[kind]


@dataclass(frozen=True)
class Provenance:
    """Where a document came from, exactly - never a description of the source, the source."""

    repository: str
    commit: str
    path: str = ""


@dataclass(frozen=True)
class ValidationResult:
    """The citation-resolution verdict for a document or chunk - traceability, not correctness.

    A resolved anchor says the symbol exists in foss-mcp's own extraction; it says nothing
    about whether the surrounding prose is otherwise true (`foss_mcp.normalization.citation`).
    """

    verdict: str = "not_checked"
    detail: str = ""

    @property
    def resolved(self) -> bool:
        return self.verdict == "supported"


NOT_CHECKED = ValidationResult(verdict="not_checked")


@dataclass(frozen=True)
class NormalizedDocument:
    """The envelope every source is normalized into. Every field below is required by the
    card's contract (``source_kind``, ``content_type``, ``provenance``, ``evidence_refs``,
    ``validation``); ``trust_tier``, ``title`` and ``body`` carry what the rest of
    normalization (chunking, citation) needs to do its own job.
    """

    source_kind: str
    content_type: str
    provenance: Provenance
    evidence_refs: tuple[str, ...]
    validation: ValidationResult
    trust_tier: str
    title: str = ""
    body: str = ""


def make_document(
    *,
    source_kind: SourceKind | str,
    content_type: str,
    provenance: Provenance,
    evidence_refs: tuple[str, ...] = (),
    title: str = "",
    body: str = "",
    richness: str | None = None,
    validation: ValidationResult = NOT_CHECKED,
) -> NormalizedDocument:
    """Build a document with its trust tier derived from ``source_kind`` (and ``richness`` for
    release notes), so a caller cannot forget to set it or set it inconsistently.
    """
    kind = SourceKind(source_kind)
    return NormalizedDocument(
        source_kind=kind.value,
        content_type=content_type,
        provenance=provenance,
        evidence_refs=tuple(evidence_refs),
        validation=validation,
        trust_tier=trust_tier_for(kind, richness=richness).value,
        title=title,
        body=body,
    )


def to_dict(doc: NormalizedDocument) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "source_kind": doc.source_kind,
        "content_type": doc.content_type,
        "provenance": asdict(doc.provenance),
        "evidence_refs": list(doc.evidence_refs),
        "validation": asdict(doc.validation),
        "trust_tier": doc.trust_tier,
        "title": doc.title,
        "body": doc.body,
    }


def from_dict(data: dict[str, Any]) -> NormalizedDocument:
    return NormalizedDocument(
        source_kind=data["source_kind"],
        content_type=data["content_type"],
        provenance=Provenance(**data["provenance"]),
        evidence_refs=tuple(data["evidence_refs"]),
        validation=ValidationResult(**data["validation"]),
        trust_tier=data["trust_tier"],
        title=data.get("title", ""),
        body=data.get("body", ""),
    )
