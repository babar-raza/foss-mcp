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
``--furnished-page`` (plus the ``--library-*`` coordinates) is given, candidate examples
are extracted from a furnished-content page (``foss_mcp.indexing.example_candidates``), each is
compiled for real in an isolated sandbox against the pinned reference library
(``foss_mcp.indexing.example_verifier``), and only a candidate that actually compiles becomes a
citable example chunk. An unverified candidate is silently excluded - a candidate failing real
compilation is the pipeline working correctly, not a defect. This whole path is skipped, and
output is byte-for-byte identical to before, when ``--furnished-page`` is omitted.

REQ-G2-048 (TC-091) generalizes that path with a new required ``--library-platform`` flag
(dotnet/python/rust/go/java/typescript) that dispatches to the matching real
``prepare_<lang>_library``/``verify_<lang>_example`` pair from
``foss_mcp.indexing.example_verifier`` (TC-081/082), via the ``_PLATFORM_DISPATCH`` table below.
``--library-platform dotnet`` (plus the pre-existing ``--library-csproj``) reproduces the
original pdf/net-only behavior byte-for-byte; the other five platforms were previously
unreachable from this CLI even though their prepare/verify functions already existed.

REQ-G2-047 (TC-112) adds a THIRD, additive path alongside the two above: real documentation
chunks built from ``--furnished-page``'s own ``overview``/``content``/``faq`` front-matter
sections (``foss_mcp.indexing.doc_candidates.extract_doc_sections``, TC-110). Unlike the
example path, no compile-verification happens here - prose is never compiled - so every
``DocCandidate`` the extractor returns becomes a chunk, no sandbox, no verified/unverified
split. This closes REQ-G2-047's confirmed gap: search_docs has never had any real content,
for any pilot, since this project's own inception.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import tempfile
from pathlib import Path

import yaml

from foss_mcp.indexing.chunk_builder import build_chunks_from_api_surface
from foss_mcp.indexing.doc_candidates import extract_doc_sections
from foss_mcp.indexing.example_candidates import extract_candidate_examples
from foss_mcp.indexing.example_verifier import (
    ExampleEnvironmentError,
    prepare_go_library,
    prepare_java_library,
    prepare_python_library,
    prepare_reference_library,
    prepare_rust_library,
    prepare_typescript_library,
    verify_dotnet_example,
    verify_go_example,
    verify_java_example,
    verify_python_example,
    verify_rust_example,
    verify_typescript_example,
)
from foss_mcp.normalization.chunker import Chunk, chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document

# REQ-G2-048 (TC-184): exit status when the verifier hits an environment failure (a toolchain
# that could not reach its package registry). 75 is EX_TEMPFAIL from sysexits.h: a retryable
# failure that is not a defect, so the ingestion Job can tell it apart from the exit 1 a
# defect produces.
ENVIRONMENT_FAILURE_EXIT = 75

# REQ-G2-048 (TC-091): the single, centrally-readable mapping from --library-platform to how
# to prepare and verify a candidate example for that pilot. Each entry is
# (prepare, verify, verify_kwargs) where:
#   - prepare(args, workdir) -> "prepared library" object (a Path in every case here, but its
#     meaning differs per platform: a built csproj file for dotnet, a venv python executable
#     for python, a cloned+built library directory for rust/go/typescript, a packaged jar for
#     java). Every real prepare_<lang>_library function takes the same (repository, commit,
#     workdir) positional shape except dotnet's prepare_reference_library, which also takes
#     csproj_relative_path - that one difference is handled explicitly here, inline, rather
#     than forcing a fake uniform signature onto it.
#   - verify is the real verify_<lang>_example function, called uniformly as
#     verify(candidate, workdir=candidate_workdir, **verify_kwargs(prepared, args)).
#   - verify_kwargs(prepared, args) -> dict of the extra keyword-only arguments verify needs
#     beyond candidate/workdir, built from the prepared library plus (for rust/go/typescript)
#     a platform-specific CLI flag.
_PLATFORM_DISPATCH = {
    "dotnet": {
        "prepare": lambda args, workdir: prepare_reference_library(
            args.library_repository, args.library_commit, args.library_csproj, workdir
        ),
        "verify": verify_dotnet_example,
        "verify_kwargs": lambda prepared, args: {"library_csproj": prepared},
    },
    "python": {
        "prepare": lambda args, workdir: prepare_python_library(
            args.library_repository, args.library_commit, workdir
        ),
        "verify": verify_python_example,
        "verify_kwargs": lambda prepared, args: {"venv_python": prepared},
    },
    "rust": {
        "prepare": lambda args, workdir: prepare_rust_library(
            args.library_repository, args.library_commit, workdir
        ),
        "verify": verify_rust_example,
        "verify_kwargs": lambda prepared, args: {
            "library_crate_name": args.library_crate_name,
            "library_dir": prepared,
        },
    },
    "go": {
        "prepare": lambda args, workdir: prepare_go_library(
            args.library_repository, args.library_commit, workdir
        ),
        "verify": verify_go_example,
        "verify_kwargs": lambda prepared, args: {
            "module_path": args.library_module_path,
            "library_dir": prepared,
        },
    },
    "java": {
        "prepare": lambda args, workdir: prepare_java_library(
            args.library_repository, args.library_commit, workdir
        ),
        "verify": verify_java_example,
        "verify_kwargs": lambda prepared, args: {"library_jar": prepared},
    },
    "typescript": {
        "prepare": lambda args, workdir: prepare_typescript_library(
            args.library_repository, args.library_commit, workdir
        ),
        "verify": verify_typescript_example,
        "verify_kwargs": lambda prepared, args: {
            "library_dir": prepared,
            "package_name": args.library_package_name,
        },
    },
}

# REQ-G2-048 (TC-091): which extra CLI flag (beyond --library-repository/--library-commit) is
# required for a given --library-platform, and its attribute name on the parsed args - keyed
# only for platforms that actually need one. python and java need no extra flag: their verify_*
# functions' extra kwarg (venv_python, library_jar) comes from their own prepare_*_library's
# return value, never a new CLI input.
_PLATFORM_REQUIRED_FLAGS: dict[str, tuple[str, str]] = {
    "dotnet": ("library_csproj", "--library-csproj"),
    "rust": ("library_crate_name", "--library-crate-name"),
    "go": ("library_module_path", "--library-module-path"),
    "typescript": ("library_package_name", "--library-package-name"),
}


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

    Dispatches to the real prepare/verify pair for ``args.library_platform`` via
    ``_PLATFORM_DISPATCH`` (REQ-G2-048/TC-091). For ``--library-platform dotnet`` this calls
    ``prepare_reference_library``/``verify_dotnet_example`` with exactly the same arguments as
    before this dispatch layer existed - byte-identical behavior for the real, currently-running
    pdf/net production path.
    """
    page = _load_furnished_page(args.furnished_page)
    candidates = extract_candidate_examples(page)

    platform_dispatch = _PLATFORM_DISPATCH[args.library_platform]

    library_workdir = Path(tempfile.mkdtemp(prefix="build_chunks_library_"))
    prepared_library = platform_dispatch["prepare"](args, library_workdir)
    verify_kwargs = platform_dispatch["verify_kwargs"](prepared_library, args)
    verify = platform_dispatch["verify"]

    verified_chunks: list[Chunk] = []
    verified_count = 0
    for candidate in candidates:
        candidate_workdir = Path(tempfile.mkdtemp(prefix="build_chunks_candidate_"))
        verification_result = verify(candidate, workdir=candidate_workdir, **verify_kwargs)
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
                    f"# Example: {candidate.title}\n\n"
                    f"FQN: Example: {candidate.title}\n"
                    f"Kind: verified_example\n"
                    f"{candidate.description}\n\n"
                    f"Example:\n{candidate.code}"
                ),
            )
            verified_chunks.extend(chunk_document(doc))

    print(f"verified {verified_count}/{len(candidates)} candidate examples")
    return verified_chunks


def _build_doc_chunks(page: dict, args: argparse.Namespace) -> list[Chunk]:
    """Extract and chunk every real documentation candidate from ``--furnished-page``'s own
    ``overview``/``content``/``faq`` sections (REQ-G2-047, TC-110's ``extract_doc_sections``).

    Genuinely simpler than ``_build_verified_example_chunks``: prose is never compiled, so
    there is no compile-verification sandbox and no verified/unverified split - every
    ``DocCandidate`` becomes a chunk. For each candidate, a real ``Document`` is built via
    ``make_document`` mirroring ``_build_verified_example_chunks``'s own real call shape
    exactly (same argument names/order), then ``chunk_document`` splits it into one or more
    section-granularity ``Chunk``s.

    ``content_type="doc"`` is this CHUNK's own build-time ``content_type`` field
    (``document_schema.py``'s ``Document``/``Chunk`` dataclass field) - nothing downstream
    currently reads it back, but it must NOT be the literal string ``"example"``:
    ``citation.py``'s ``_is_real_compile_verified_example`` checks exactly
    ``chunk.content_type == "example"`` and would wrongly exempt this real, unverified doc
    prose from citation checking if it collided.

    Every returned chunk's ``text`` is prefixed with a pseudo-FQN line of the exact shape
    ``FQN: Doc: {candidate.title}\\n``, mirroring ``_build_verified_example_chunks``'s own
    ``FQN: Example: {candidate.title}\\n`` convention exactly. This is applied to each chunk
    AFTER ``chunk_document`` splits the candidate's body - not baked into the body passed to
    ``make_document`` - because a ``content``-origin candidate's own real markdown headings
    (e.g. ``### Installation``) make ``chunk_document`` split one candidate into several
    chunks, and any text placed before a document's first heading is dropped entirely by
    ``chunker._heading_sections`` (text before the first heading match is never captured).
    Post-processing every emitted chunk is the only way to guarantee EVERY chunk this function
    publishes carries the marker, not just the first one.

    Reason the marker is required at all, discovered live during TC-109's own execution:
    ``search_symbols.py``'s own exclusion of non-symbol chunks
    (``get_symbol.extract_fqn(text).startswith("Example: ")``) is the ONLY thing stopping a
    non-symbol chunk in this shared generation from being wrongly returned by
    ``search_symbols`` (and therefore by ``lookup()``'s bare-query dispatch, which tries
    ``search_symbols`` first) as if it were a real API symbol. Without this marker, every doc
    chunk this function publishes would silently corrupt ``search_symbols``/``lookup`` for
    every pilot. A separate, already-authored follow-up card (TC-120) extends
    ``search_symbols.py``'s own exclusion to also drop ``Doc: `` - it depends on this exact
    marker shape existing.
    """
    candidates = extract_doc_sections(page)

    doc_chunks: list[Chunk] = []
    for candidate in candidates:
        doc = make_document(
            source_kind=SourceKind.FURNISHED,
            content_type="doc",
            provenance=Provenance(
                repository=args.library_repository,
                commit=args.library_commit,
                path=str(args.furnished_page),
            ),
            evidence_refs=(f"{args.library_repository}@{args.library_commit}",),
            title=candidate.title,
            body=candidate.body,
        )
        for chunk in chunk_document(doc):
            doc_chunks.append(dataclasses.replace(chunk, text=f"FQN: Doc: {candidate.title}\n{chunk.text}"))

    print(f"built {len(doc_chunks)} doc chunks from {len(candidates)} doc candidates")
    return doc_chunks


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
        "--library-platform",
        choices=sorted(_PLATFORM_DISPATCH),
        default=None,
        help="which pilot's real prepare/verify pair (REQ-G2-048) to dispatch --furnished-page "
        "verification to",
    )
    parser.add_argument(
        "--library-csproj",
        default=None,
        help="pinned reference library's repository-relative csproj path (dotnet only)",
    )
    parser.add_argument(
        "--library-crate-name", default=None, help="pinned reference crate's package name (rust only)"
    )
    parser.add_argument(
        "--library-module-path", default=None, help="pinned reference module's Go module path (go only)"
    )
    parser.add_argument(
        "--library-package-name",
        default=None,
        help="pinned reference package's npm package name (typescript only)",
    )
    args = parser.parse_args()

    furnished_args = (
        args.furnished_page,
        args.library_repository,
        args.library_commit,
        args.library_platform,
    )
    if any(furnished_args) and not all(furnished_args):
        parser.error(
            "--furnished-page, --library-repository, --library-commit, and --library-platform "
            "must all be given together"
        )

    if args.furnished_page is not None:
        required_flag = _PLATFORM_REQUIRED_FLAGS.get(args.library_platform)
        if required_flag is not None:
            attr_name, flag_name = required_flag
            if getattr(args, attr_name) is None:
                parser.error(f"--library-platform {args.library_platform} requires {flag_name}")

    fixture = json.loads(args.api_surface.read_text(encoding="utf-8"))
    chunks = build_chunks_from_api_surface(fixture, title=args.title, max_types=args.max_types)

    if args.furnished_page is not None:
        # Note: _build_verified_example_chunks loads --furnished-page itself internally, and
        # it is explicitly out of scope for this card to touch (REQ-G2-047/TC-112) - so this
        # is a real, deliberately-accepted duplicate parse, not an oversight.
        page = _load_furnished_page(args.furnished_page)
        try:
            chunks = chunks + _build_verified_example_chunks(args) + _build_doc_chunks(page, args)
        except ExampleEnvironmentError as exc:
            # TC-184: an environment failure is not a verdict on any candidate. End the build
            # here with a distinct exit code and write nothing, so the candidate is neither
            # published nor recorded as not verified.
            print(
                f"environment failure: {exc.tool} could not reach its registry "
                f"(marker {exc.marker!r}); build stopped, nothing written",
                file=sys.stderr,
            )
            sys.exit(ENVIRONMENT_FAILURE_EXIT)

    chunks = [_with_source_commit_line(chunk) for chunk in chunks]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps({"chunks": [dataclasses.asdict(chunk) for chunk in chunks]}, indent=2),
        encoding="utf-8",
    )
    print(f"wrote {len(chunks)} chunks to {args.out}")


if __name__ == "__main__":
    main()
