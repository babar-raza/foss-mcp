"""Bridge TC-062's real chunk builder to infra/ingest.py's ``--chunks`` flag.

``foss_mcp.indexing.chunk_builder.build_chunks_from_api_surface`` (TC-062) returns real
``Chunk`` dataclass objects, not JSON - before this script existed, nothing in the repo ever
called it outside of tests (the 2026-09-25 audit's finding: a real production ingestion run had
no CLI step that could turn a raw ``api_surface.json`` extraction fixture into a
``--chunks PATH`` file ``infra/ingest.py``'s own ``_load_chunks()`` can read). This script is
that bridge: load ``--api-surface``, call ``build_chunks_from_api_surface`` with the exact
prescribed call shape, serialize the result to ``{"chunks": [...]}`` via ``dataclasses.asdict``
(never hand-duplicated field names), write it to ``--out``.

Each chunk's ``text`` also gets one appended ``Source-Commit: <sha>`` line here. This is this
project's own already-established convention for recovering a generation's indexed source
commit from chunk text (``foss_mcp.mcp.tools.get_symbol``'s ``FQN:`` line is the same pattern;
``foss_mcp.mcp.tools.report_index_freshness``'s own docstring names this exact convention,
because the lexical index persists each chunk's text but not its ``Provenance``).
``build_chunks_from_api_surface`` itself does not add this line - it is shared, generic
chunk-building logic reused verbatim by several pilots' inline builders, none of which add it
either - so it is added here, once, at the ingestion-adapter boundary this script owns, using
each chunk's own ``provenance.commit`` (already set by ``build_chunks_from_api_surface`` from
the fixture's ``source_commit``) so it can never drift from what the chunk was actually built
from.

REQ-G2-048 (TC-068) adds a second, optional path alongside the type-based one above: when
``--furnished-page`` (plus the three ``--library-*`` coordinates) is given, candidate examples
are extracted from a furnished-content page (``foss_mcp.indexing.example_candidates``), each is
compiled for real in an isolated sandbox against the pinned reference library
(``foss_mcp.indexing.example_verifier``), and only a candidate that actually compiles becomes a
citable example chunk. An unverified candidate is silently excluded - a candidate failing real
compilation is the pipeline working correctly, not a defect. This whole path is skipped, and
output is byte-for-byte identical to before, when ``--furnished-page`` is omitted.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import tempfile
from pathlib import Path

import yaml

from foss_mcp.indexing.chunk_builder import build_chunks_from_api_surface
from foss_mcp.indexing.example_candidates import extract_candidate_examples
from foss_mcp.indexing.example_verifier import prepare_reference_library, verify_dotnet_example
from foss_mcp.normalization.chunker import Chunk, chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document


def _with_source_commit_line(chunk: Chunk) -> Chunk:
    return dataclasses.replace(chunk, text=f"{chunk.text}\nSource-Commit: {chunk.provenance.commit}")


def _load_furnished_page(path: Path) -> dict:
    """Parse a Hugo page-bundle's front matter - the exact real pattern TC-066's own
    real-fixture test uses (split on the first two ``---`` fences, ``yaml.safe_load``).
    """
    text = path.read_text(encoding="utf-8")
    front_matter = text.split("---", 2)[1]
    parsed = yaml.safe_load(front_matter)
    assert isinstance(parsed, dict)
    return parsed


def _build_verified_example_chunks(args: argparse.Namespace) -> list[Chunk]:
    """Extract, really compile-verify, and chunk every candidate example from
    ``--furnished-page`` - an unverified candidate is silently excluded, never published,
    never logged as an error.
    """
    page = _load_furnished_page(args.furnished_page)
    candidates = extract_candidate_examples(page)

    library_workdir = Path(tempfile.mkdtemp(prefix="build_chunks_library_"))
    library_csproj = prepare_reference_library(
        args.library_repository,
        args.library_commit,
        args.library_csproj,
        library_workdir,
    )

    verified_chunks: list[Chunk] = []
    verified_count = 0
    for candidate in candidates:
        candidate_workdir = Path(tempfile.mkdtemp(prefix="build_chunks_candidate_"))
        verification_result = verify_dotnet_example(
            candidate, library_csproj=library_csproj, workdir=candidate_workdir
        )
        if verification_result.verified:
            verified_count += 1
            doc = make_document(
                source_kind=SourceKind.FURNISHED,
                content_type="example",
                provenance=Provenance(
                    repository=args.library_repository,
                    commit=args.library_commit,
                    path=str(args.furnished_page),
                ),
                evidence_refs=(f"{args.library_repository}@{args.library_commit}",),
                title=candidate.title,
                body=(
                    f"FQN: Example: {candidate.title}\n"
                    f"Kind: verified_example\n"
                    f"{candidate.description}\n\n"
                    f"Example:\n{candidate.code}"
                ),
            )
            verified_chunks.extend(chunk_document(doc))

    print(f"verified {verified_count}/{len(candidates)} candidate examples")
    return verified_chunks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-surface", type=Path, required=True, help="raw extraction fixture JSON")
    parser.add_argument("--title", required=True)
    parser.add_argument("--max-types", type=int, default=20)
    parser.add_argument("--out", type=Path, required=True, help="where to write the --chunks JSON")
    parser.add_argument(
        "--furnished-page",
        type=Path,
        default=None,
        help="optional Hugo-bundle YAML page (e.g. tests/fixtures/furnished/pdf_net/pages/_index.md) "
        "to extract and real-compile-verify candidate examples from (REQ-G2-048)",
    )
    parser.add_argument("--library-repository", default=None, help="pinned reference library's repository")
    parser.add_argument("--library-commit", default=None, help="pinned reference library's commit")
    parser.add_argument(
        "--library-csproj", default=None, help="pinned reference library's repository-relative csproj path"
    )
    args = parser.parse_args()

    furnished_args = (args.furnished_page, args.library_repository, args.library_commit, args.library_csproj)
    if any(furnished_args) and not all(furnished_args):
        parser.error(
            "--furnished-page, --library-repository, --library-commit, and --library-csproj "
            "must all be given together"
        )

    fixture = json.loads(args.api_surface.read_text(encoding="utf-8"))
    chunks = build_chunks_from_api_surface(fixture, title=args.title, max_types=args.max_types)

    if args.furnished_page is not None:
        chunks = chunks + _build_verified_example_chunks(args)

    chunks = [_with_source_commit_line(chunk) for chunk in chunks]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps({"chunks": [dataclasses.asdict(chunk) for chunk in chunks]}, indent=2),
        encoding="utf-8",
    )
    print(f"wrote {len(chunks)} chunks to {args.out}")


if __name__ == "__main__":
    main()
