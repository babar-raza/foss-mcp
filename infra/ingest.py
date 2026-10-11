"""Ingestion entrypoint: a plain CLI, no message broker.

Publishes one generation from pre-built chunks (JSON) through the CAS manifest path
(``foss_mcp.indexing.publisher``, ``foss_mcp.indexing.generation_manifest``) - the same
authority path TC-015 built. "Ingestion" is deliberately not called "worker": there is no
queue here to work against, just a command that runs, publishes, and exits.

No production ``EmbeddingProvider`` ships anywhere in ``src/`` yet (see
``foss_mcp.indexing.embedding_provider`` - the deterministic one exists for tests only, and
is never importable from a production path). ``--embedding-provider`` names one by import
path (``module.path:ClassName``) so this entry point is genuinely complete now and needs no
further change once a real provider is added by a later card.
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from foss_mcp.extraction.claim_id_bridge import SymbolIndex
from foss_mcp.indexing.embedding_provider import EmbeddingProvider

# PublishSafetyError is re-exported: this module used to define its own PublishSafetyError
# (and the _assert_publish_is_safe guard that raised it) here, enforced only at this CLI's
# own call site. TC-242 moved that guard inside generation_manifest.publish() itself, so
# EVERY direct caller of publish()/GenerationManifestStore is covered, not only this CLI.
# The name is kept importable from here so nothing outside this card's write_paths (this
# module's own existing callers/tests importing it as `ingest.PublishSafetyError`) breaks.
from foss_mcp.indexing.generation_manifest import (  # noqa: F401
    GenerationManifestStore,
    PublishSafetyError,
)
from foss_mcp.indexing.publisher import publish_generation
from foss_mcp.indexing.vector_index_writer import is_example_chunk
from foss_mcp.mcp.tools.get_symbol import extract_fqn
from foss_mcp.normalization.chunker import Chunk
from foss_mcp.normalization.citation import (
    INSUFFICIENT_EVIDENCE,
    SUPPORTED,
    UNSUPPORTED,
    citable_chunks,
    describe_unresolved_anchors,
    find_numeric_claims,
    known_counts_from_fixture,
    namespaces_from_api_surface,
    neutralise_unresolved_anchors,
    qualified_symbol_index_from_api_surface,
    symbol_index_from_api_surface,
    validate_document,
    validate_numeric_claim,
)
from foss_mcp.normalization.document_schema import NOT_CHECKED, Provenance, ValidationResult


def _load_embedding_provider(spec: str) -> EmbeddingProvider:
    module_name, _, class_name = spec.partition(":")
    if not module_name or not class_name:
        raise ValueError(f"--embedding-provider must be 'module.path:ClassName', got {spec!r}")
    module = importlib.import_module(module_name)
    return getattr(module, class_name)()


def _load_chunks(path: Path) -> list[Chunk]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        Chunk(
            section_title=entry["section_title"],
            text=entry["text"],
            source_kind=entry["source_kind"],
            content_type=entry["content_type"],
            provenance=Provenance(**entry["provenance"]),
            trust_tier=entry["trust_tier"],
            evidence_refs=tuple(entry.get("evidence_refs", ())),
            validation=ValidationResult(**entry["validation"]) if "validation" in entry else NOT_CHECKED,
        )
        for entry in data["chunks"]
    ]


# An ``FQN:`` value with one of these prefixes names a verified example or a documentation page,
# not an extracted API symbol (infra/build_chunks.py writes them), so those chunks stay subject
# to citation validation like any other prose the project generated.
_NON_SYMBOL_FQN_PREFIXES = ("Example: ", "Doc: ")


@dataclass(frozen=True)
class SelectionReport:
    """What ``select_publishable_chunks`` did, as plain counts."""

    total: int
    published: int
    api_symbols: int
    api_symbols_kept: int
    excluded_other: int
    # TC-425: furnished documentation / example chunks (content_type "doc" or "example"). They
    # are excluded only for a stated count that contradicts the extraction, so the number
    # excluded is ``furnished_total - furnished_published``.
    furnished_total: int = 0
    furnished_published: int = 0
    anchors_neutralised: int = 0
    counts_qualified: int = 0

    @property
    def furnished_excluded(self) -> int:
        return self.furnished_total - self.furnished_published

    def summary_line(self) -> str:
        return (
            f"citation: published {self.published} of {self.total} chunks; "
            f"api symbols kept {self.api_symbols_kept} of {self.api_symbols}; "
            f"excluded {self.excluded_other} other chunks"
        )

    def furnished_summary_line(self) -> str:
        return (
            f"citation: furnished chunks published {self.furnished_published} of {self.furnished_total}; "
            f"anchors neutralised {self.anchors_neutralised}; "
            f"counts qualified in {self.counts_qualified} chunks; "
            f"excluded {self.furnished_excluded} for contradicted counts"
        )


# content_type values of the chunks infra/build_chunks.py builds from a furnished page: "doc" for
# an overview/content/faq section, "example" for a compile-verified example (TC-425).
_FURNISHED_CONTENT_TYPES = ("doc", "example")

_UNCORROBORATED_COUNTS_NOTE = (
    "Note: counts stated in this section come from the upstream documentation and were not "
    "corroborated against the extracted API."
)


def _is_furnished_chunk(chunk: Chunk) -> bool:
    return chunk.content_type in _FURNISHED_CONTENT_TYPES


def _is_api_symbol_chunk(chunk: Chunk) -> bool:
    """A self-extracted API-symbol chunk: ``api_surface`` content whose ``FQN:`` line names a
    real symbol (not an ``Example: ``/``Doc: `` pseudo-FQN). Its text IS the extraction, so its
    docstring is upstream's own words, not a claim this project generated.
    """
    if chunk.content_type != "api_surface":
        return False
    fqn = extract_fqn(chunk.text)
    return fqn is not None and not fqn.startswith(_NON_SYMBOL_FQN_PREFIXES)


def _select_furnished_chunk(
    chunk: Chunk,
    qualified_index: SymbolIndex,
    namespaces: frozenset[str],
    known_counts: Mapping[str, int],
) -> tuple[Chunk | None, int, bool]:
    """``(chunk to publish or None, anchors neutralised, counts qualified)`` for one furnished chunk.

    A furnished chunk is dropped only when a stated count CONTRADICTS the extraction
    (``validate_numeric_claim`` verdict ``unsupported``). Otherwise it is published: a "doc"
    chunk with every still-unresolved inline anchor un-backticked, an "example" chunk unchanged,
    and either with a Note line appended when some stated count could not be corroborated.
    """
    contradicted = False
    uncorroborated = False
    for number, unit in find_numeric_claims(chunk.text):
        result = validate_numeric_claim(number, unit, known_counts)
        if result.verdict == UNSUPPORTED:
            contradicted = True
            print(f"excluded: {chunk.section_title} count {number} {unit} -> {result.detail}")
        elif result.verdict == INSUFFICIENT_EVIDENCE:
            uncorroborated = True
    if contradicted:
        return None, 0, False
    text = chunk.text
    neutralised = 0
    if chunk.content_type == "doc":
        text, neutralised = neutralise_unresolved_anchors(text, qualified_index, namespaces)
    if uncorroborated:
        text = f"{text}\n{_UNCORROBORATED_COUNTS_NOTE}"
        validation = ValidationResult(
            verdict=INSUFFICIENT_EVIDENCE, detail="stated counts not corroborated by the extraction"
        )
    else:
        validation = ValidationResult(verdict=SUPPORTED, detail="no stated count contradicts the extraction")
    return dataclasses.replace(chunk, text=text, validation=validation), neutralised, uncorroborated


def select_publishable_chunks(
    chunks: Sequence[Chunk], fixture: Mapping[str, object]
) -> tuple[list[Chunk], SelectionReport]:
    """The chunks to publish for ``--api-surface``, in input order, plus a count report.

    Every API-symbol chunk is kept whatever its validation verdict: an unresolvable inline code
    span inside an upstream docstring is no reason to delete the symbol, and the unresolved
    anchors are printed as ``kept api symbol: ...`` so nothing is silent. Every other chunk is
    validated and filtered exactly as before (``excluded: ...`` lines, SUPPORTED-only).
    """
    symbol_index = symbol_index_from_api_surface(fixture["types"])
    qualified_index = qualified_symbol_index_from_api_surface(fixture["types"])
    namespaces = namespaces_from_api_surface(fixture["types"])
    known_counts = known_counts_from_fixture(fixture)
    validated_chunks = validate_document(chunks, symbol_index, known_counts)
    selected: list[Chunk] = []
    api_symbols = 0
    excluded_other = 0
    furnished_total = 0
    furnished_published = 0
    anchors_neutralised = 0
    counts_qualified = 0
    for validated in validated_chunks:
        if _is_furnished_chunk(validated):
            furnished_total += 1
            kept, neutralised, qualified = _select_furnished_chunk(
                validated, qualified_index, namespaces, known_counts
            )
            if kept is not None:
                furnished_published += 1
                anchors_neutralised += neutralised
                counts_qualified += 1 if qualified else 0
                selected.append(kept)
            continue
        is_symbol = _is_api_symbol_chunk(validated)
        label = "kept api symbol" if is_symbol else "excluded"
        for section_title, resolution in describe_unresolved_anchors([validated], symbol_index):
            print(
                f"{label}: {section_title!r} anchor {resolution.anchor!r} -> "
                f"{resolution.semantic_support_verdict.value} (match_count={resolution.match_count})"
            )
        if is_symbol:
            api_symbols += 1
            selected.append(validated)
        elif citable_chunks([validated]):
            selected.append(validated)
        else:
            excluded_other += 1
    report = SelectionReport(
        total=len(chunks),
        published=len(selected),
        api_symbols=api_symbols,
        api_symbols_kept=api_symbols,
        excluded_other=excluded_other,
        furnished_total=furnished_total,
        furnished_published=furnished_published,
        anchors_neutralised=anchors_neutralised,
        counts_qualified=counts_qualified,
    )
    return selected, report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--source-kind", required=True)
    parser.add_argument("--chunks", type=Path, required=True, help="pre-built chunks, as JSON")
    parser.add_argument("--manifest-store", type=Path, required=True)
    parser.add_argument("--embedding-provider", required=True, help="module.path:ClassName")
    parser.add_argument("--held-by", default="ingestion")
    parser.add_argument(
        "--api-surface",
        type=Path,
        default=None,
        help=(
            "self-extracted api_surface.json to validate chunks' citations against "
            "(TC-014's validate_document/citable_chunks). Omit to publish all loaded "
            "chunks unchanged, as before this card - not every source_kind has a "
            "self-extracted symbol index to validate against."
        ),
    )
    parser.add_argument(
        "--vector-scope",
        choices=("all", "examples"),
        default="all",
        help=(
            "which chunks get an embedding in the vector index. 'all' (default) embeds every "
            "chunk. 'examples' embeds and stores only chunks carrying an 'Example:' block - the "
            "only chunks find_examples' vector fallback can return - which keeps a generation "
            "file small and makes far fewer embedding-gateway calls. The lexical index always "
            "holds every chunk either way."
        ),
    )
    args = parser.parse_args()

    store = GenerationManifestStore(args.manifest_store)
    scope = "::".join((args.family, args.platform, args.source_kind))
    expected_active = store.read_active(scope)
    lease = store.acquire_lease(scope, args.held_by, "pending")
    chunks = _load_chunks(args.chunks)
    if args.api_surface is not None:
        fixture = json.loads(args.api_surface.read_text(encoding="utf-8"))
        chunks, report = select_publishable_chunks(chunks, fixture)
        print(report.summary_line())
        print(report.furnished_summary_line())
    embedding_provider = _load_embedding_provider(args.embedding_provider)

    # The empty-generation / >50% regression guard (PublishSafetyError) is no longer
    # checked here: TC-242 moved it inside generation_manifest.publish() itself (called by
    # publish_generation below), so it is enforced for every direct caller of
    # publish()/GenerationManifestStore, not only this CLI. PublishSafetyError propagates
    # out of this call exactly as it did out of the old pre-check, uncaught, and nothing
    # is activated when it is raised.
    generation_id = publish_generation(
        store,
        family=args.family,
        platform=args.platform,
        source_kind=args.source_kind,
        expected_active=expected_active,
        chunks=chunks,
        embedding_provider=embedding_provider,
        lease=lease,
        vector_chunk_filter=is_example_chunk if args.vector_scope == "examples" else None,
    )
    print(f"published {generation_id}")


if __name__ == "__main__":
    main()
