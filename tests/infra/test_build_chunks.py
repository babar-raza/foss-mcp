"""Tests for infra/build_chunks.py (REQ-G2-048, TC-068, TC-070, TC-091).

Real end-to-end: a real furnished-content fixture, a real git clone of the
pinned pdf/net reference library (via example_verifier.prepare_reference_library),
and a real `dotnet build` per candidate example (via example_verifier.verify_dotnet_example).
Nothing here is mocked - the whole point of this card is that only a really
compile-verified example becomes a citable chunk.

This is genuinely slow (shallow clone + NuGet restore + up to 4 dotnet builds:
one for the reference library, one per candidate example) and requires network
access and a .NET 8 SDK.

TC-091 adds a second real end-to-end test proving the --library-platform dispatch layer for
a non-dotnet pilot (slides/python): a real git clone + venv + `pip install -e` of the pinned
reference library (via example_verifier.prepare_python_library), and a real Python subprocess
execution per candidate example (via example_verifier.verify_python_example). This one also
requires network access, plus a Python interpreter capable of creating a venv.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest
import yaml

import infra.build_chunks as build_chunks
from foss_mcp.indexing.doc_candidates import extract_doc_sections
from foss_mcp.indexing.example_candidates import extract_candidate_examples
from foss_mcp.indexing.example_verifier import prepare_reference_library, verify_dotnet_example
from foss_mcp.normalization.citation import (
    citable_chunks,
    known_counts_from_fixture,
    symbol_index_from_api_surface,
    validate_document,
)

API_SURFACE = Path("tests/fixtures/pdf_net/api_surface.json")
FURNISHED_PAGE = Path("tests/fixtures/furnished/pdf_net/pages/_index.md")

# Pinned exactly as indexed - matches config/products/pdf/net.yaml's `repository`
# and tests/fixtures/pdf_net/api_surface.json's own `source_commit`. Never "latest".
LIBRARY_REPOSITORY = "aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET"
LIBRARY_COMMIT = "b7172877651413cff57a8bfe41fb8a8befb2406b"
LIBRARY_CSPROJ = "src/Aspose.Pdf.Foss.csproj"

# One real, distinctive method call per real candidate in the real fixture - used only to
# identify, inside published chunk text, which of the 3 real candidates a chunk came from
# (chunk_document gives every paragraph-split chunk an empty section_title, so title alone
# cannot identify one). Never used to assume a verification outcome, only an identity.
_DISTINCTIVE_CODE_MARKER = {
    "Open a PDF, Add a Link Annotation, and Save": "AddLinkAnnotation",
    "Add a Watermark Annotation": "AddWatermarkAnnotation",
    "Extract Text Fragments from a Page": "TextFragmentAbsorber",
}


def _load_real_page() -> dict:
    text = FURNISHED_PAGE.read_text(encoding="utf-8")
    front_matter = text.split("---", 2)[1]
    parsed = yaml.safe_load(front_matter)
    assert isinstance(parsed, dict)
    return parsed


def _run_main(argv: list[str]) -> None:
    old_argv = sys.argv
    sys.argv = ["build_chunks.py"] + argv
    try:
        build_chunks.main()
    finally:
        sys.argv = old_argv


def test_api_surface_only_path_is_byte_identical_to_before_the_furnished_flags(
    tmp_path: Path,
) -> None:
    """The pre-existing --api-surface-only path (TC-064's own real production ingestion)
    must be completely unaffected by this card's change: running main() with only the
    original flags must produce exactly the output build_chunks_from_api_surface plus
    the Source-Commit line convention already produced, with no furnished/example
    content anywhere in it.
    """
    out_path = tmp_path / "chunks.json"

    _run_main(
        [
            "--api-surface",
            str(API_SURFACE),
            "--title",
            "Aspose.PDF FOSS for .NET",
            "--out",
            str(out_path),
        ]
    )

    produced = json.loads(out_path.read_text(encoding="utf-8"))

    # Independently reproduce the pre-existing (TC-064) behavior directly from the
    # underlying building blocks build_chunks.py already used before this card, and
    # assert byte-for-byte equality of the serialized JSON.
    import dataclasses

    from foss_mcp.indexing.chunk_builder import build_chunks_from_api_surface

    fixture = json.loads(API_SURFACE.read_text(encoding="utf-8"))
    expected_chunks = build_chunks_from_api_surface(fixture, title="Aspose.PDF FOSS for .NET", max_types=20)
    expected_chunks = [
        dataclasses.replace(c, text=f"{c.text}\nSource-Commit: {c.provenance.commit}")
        for c in expected_chunks
    ]
    # Round-trip through JSON exactly like the real --out file does (dataclasses.asdict
    # keeps tuples as tuples; json.dumps/json.loads turns them into lists) so this is a
    # genuine byte-for-byte comparison of what actually got written, not an artifact of
    # comparing an in-memory tuple against a JSON-decoded list.
    expected = json.loads(json.dumps({"chunks": [dataclasses.asdict(c) for c in expected_chunks]}))

    assert produced == expected
    assert not any("Example:" in c["text"] for c in produced["chunks"])
    assert all(c["source_kind"] == "self_extracted" for c in produced["chunks"])


def test_furnished_page_adds_real_compile_verified_example_chunks(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Real end-to-end: real furnished fixture -> real candidate extraction -> real
    isolated dotnet compile verification -> only a really-verified candidate becomes
    a citable chunk, alongside the existing type-based chunks.

    The ground truth this test checks against is itself determined empirically, by
    independently running the exact same real compile-verification path
    (example_verifier.prepare_reference_library / verify_dotnet_example) a second time -
    never an assumed/hardcoded count of how many of the 3 real candidates verify. This
    is deliberately strict about correspondence (a real, independently-verified
    candidate's own distinctive code must appear; a real, independently-unverified
    candidate's own distinctive code must NOT appear) so that publishing an unverified
    candidate instead of a verified one - exactly what inverting the verified-guard does -
    is a real, detected failure, not merely "some chunk with an Example: marker exists".
    """
    page = _load_real_page()
    candidates = extract_candidate_examples(page)
    assert {c.title for c in candidates} == set(_DISTINCTIVE_CODE_MARKER)

    ground_truth_library_workdir = tmp_path_factory.mktemp("tc068_ground_truth_library")
    ground_truth_library_csproj = prepare_reference_library(
        repository=LIBRARY_REPOSITORY,
        commit=LIBRARY_COMMIT,
        csproj_relative_path=LIBRARY_CSPROJ,
        workdir=ground_truth_library_workdir,
    )
    ground_truth_verified_titles = set()
    for candidate in candidates:
        candidate_workdir = tmp_path_factory.mktemp("tc068_ground_truth_candidate")
        result = verify_dotnet_example(
            candidate, library_csproj=ground_truth_library_csproj, workdir=candidate_workdir
        )
        if result.verified:
            ground_truth_verified_titles.add(candidate.title)
    assert ground_truth_verified_titles, "expected at least one real candidate to independently verify"
    print(f"ground truth (independent real compile pass): verified={sorted(ground_truth_verified_titles)}")

    out_path = tmp_path / "chunks.json"
    _run_main(
        [
            "--api-surface",
            str(API_SURFACE),
            "--title",
            "Aspose.PDF FOSS for .NET",
            "--out",
            str(out_path),
            "--furnished-page",
            str(FURNISHED_PAGE),
            "--library-repository",
            LIBRARY_REPOSITORY,
            "--library-commit",
            LIBRARY_COMMIT,
            "--library-platform",
            "dotnet",
            "--library-csproj",
            LIBRARY_CSPROJ,
        ]
    )

    produced = json.loads(out_path.read_text(encoding="utf-8"))
    chunks = produced["chunks"]

    # The pre-existing type-based chunks are still there (300 real types, capped at
    # max_types=20 default -> 20 real "## <TypeName>" sections from build_chunks_from_api_surface).
    type_chunks = [c for c in chunks if c["content_type"] == "api_surface"]
    assert len(type_chunks) > 0

    example_chunks = [c for c in chunks if c["content_type"] == "example"]
    assert len(example_chunks) >= 1, (
        "expected at least one real candidate example to really compile-verify against "
        f"{LIBRARY_REPOSITORY}@{LIBRARY_COMMIT}, but none did"
    )

    # Every emitted example chunk's source_kind is "furnished".
    assert all(c["source_kind"] == "furnished" for c in example_chunks)

    # At least one chunk's text contains "Example:" and a real, recognizable code
    # fragment - not a synthetic placeholder.
    matching = [c for c in example_chunks if "Example:" in c["text"] and "Aspose.Pdf" in c["text"]]
    assert matching, [c["text"] for c in example_chunks]

    # The Source-Commit convention applies uniformly - an example chunk's trailing
    # line names the real pinned library commit it was compiled against, not the
    # api-surface fixture's commit.
    for chunk in example_chunks:
        assert chunk["text"].splitlines()[-1] == f"Source-Commit: {LIBRARY_COMMIT}"
        assert chunk["provenance"]["repository"] == LIBRARY_REPOSITORY
        assert chunk["provenance"]["commit"] == LIBRARY_COMMIT

    # The real, load-bearing assertion: published example content corresponds EXACTLY to
    # the independently-determined ground truth - a really-verified candidate's own
    # distinctive code is present, and a really-unverified candidate's own distinctive
    # code is absent. Inverting the verified-guard swaps this correspondence and fails here.
    all_example_text = "\n".join(c["text"] for c in example_chunks)
    for candidate in candidates:
        marker = _DISTINCTIVE_CODE_MARKER[candidate.title]
        is_published = marker in all_example_text
        if candidate.title in ground_truth_verified_titles:
            assert is_published, (
                f"real, independently-verified candidate {candidate.title!r} "
                f"(marker {marker!r}) is missing from the published example chunks"
            )
        else:
            assert not is_published, (
                f"real, independently-UNVERIFIED candidate {candidate.title!r} "
                f"(marker {marker!r}) was published anyway"
            )

    # TC-070/REQ-G2-048: before the '# Example: {title}' heading fix, the example body
    # had no markdown heading, so chunker.py's chunk_document() fell back to
    # _paragraph_sections and fractured a single verified candidate's body into TWO
    # chunks at its one blank line - a description-only chunk (ranks highest on a bare
    # query term appearing in prose) and a code-only chunk (the term never matches
    # inside a bare identifier like AddWatermarkAnnotation) - so find_examples could
    # only ever surface one half, never both. Confirmed against the real produced
    # chunks.json before writing this assertion: with the heading fix, this run
    # produces exactly one example chunk (the Watermark candidate is the only one of
    # the 3 real candidates that compile-verifies today), and its single text contains
    # both the description prose and the real 'AddWatermarkAnnotation' code.
    assert len(example_chunks) == len(ground_truth_verified_titles), (
        "expected exactly ONE chunk per real verified candidate (chunk fragmentation "
        f"regression), got {len(example_chunks)} chunks for verified titles "
        f"{sorted(ground_truth_verified_titles)}: {[c['text'] for c in example_chunks]}"
    )
    for candidate in candidates:
        if candidate.title not in ground_truth_verified_titles:
            continue
        marker = _DISTINCTIVE_CODE_MARKER[candidate.title]
        atomic_chunks = [
            c for c in example_chunks if marker in c["text"] and candidate.description in c["text"]
        ]
        assert len(atomic_chunks) == 1, (
            f"expected exactly one chunk containing BOTH {candidate.title!r}'s "
            f"description prose and its real code marker {marker!r} together (never "
            f"split into a description-only chunk and a code-only chunk), found "
            f"{len(atomic_chunks)}: {[c['text'] for c in example_chunks]}"
        )

    print(f"real chunks.json: {len(type_chunks)} type chunks, {len(example_chunks)} example chunks")
    for chunk in example_chunks:
        print("---- example chunk ----")
        print(chunk["text"])

    # REQ-G2-047 (TC-112): the real production entrypoint's --furnished-page branch now
    # ALSO publishes real documentation chunks alongside the type-based and example chunks
    # above, additively, in the same run - proving the wiring into main() itself (AGENTS.md's
    # "Integration and liveness": a call site in the real entrypoint file, not just a passing
    # unit test of the function in isolation).
    doc_chunks = [c for c in chunks if c["content_type"] == "doc"]
    assert doc_chunks, "expected main()'s --furnished-page branch to publish real doc chunks too"
    assert all(c["source_kind"] == "furnished" for c in doc_chunks)
    for chunk in doc_chunks:
        first_line = chunk["text"].splitlines()[0]
        assert first_line.startswith("FQN: Doc: "), chunk["text"]
        assert chunk["provenance"]["repository"] == LIBRARY_REPOSITORY
        assert chunk["provenance"]["commit"] == LIBRARY_COMMIT
        assert chunk["text"].splitlines()[-1] == f"Source-Commit: {LIBRARY_COMMIT}"


# --- REQ-G2-047 (TC-112): real documentation chunks (TC-110's extract_doc_sections) --------


def _doc_build_args() -> argparse.Namespace:
    return argparse.Namespace(
        furnished_page=FURNISHED_PAGE,
        library_repository=LIBRARY_REPOSITORY,
        library_commit=LIBRARY_COMMIT,
    )


def _marker_title(chunk_text: str) -> str:
    first_line = chunk_text.splitlines()[0]
    assert first_line.startswith("FQN: Doc: "), chunk_text
    return first_line[len("FQN: Doc: ") :]


def test_build_doc_chunks_marker_shape_and_content_type(tmp_path: Path) -> None:
    """Real, offline (no network/dotnet needed - _build_doc_chunks never compiles anything):
    every chunk built from pdf/net's own real furnished page carries the exact required
    ``FQN: Doc: {candidate.title}\\n`` marker line (load-bearing for TC-120's own
    search_symbols.py exclusion), a "doc" content_type (never "example" - citation.py's
    _is_real_compile_verified_example checks exactly that literal string), and "furnished"
    source_kind/provenance pointing at the real pinned library.
    """
    page = _load_real_page()
    args = _doc_build_args()

    candidates = extract_doc_sections(page)
    assert candidates, "expected pdf/net's real furnished page to yield real doc candidates"
    real_titles = {c.title for c in candidates}

    doc_chunks = build_chunks._build_doc_chunks(page, args)
    assert doc_chunks

    for chunk in doc_chunks:
        assert chunk.content_type == "doc"
        assert chunk.content_type != "example"
        assert chunk.source_kind == "furnished"
        assert chunk.provenance.repository == LIBRARY_REPOSITORY
        assert chunk.provenance.commit == LIBRARY_COMMIT
        assert chunk.provenance.path == str(FURNISHED_PAGE)
        title = _marker_title(chunk.text)
        assert title in real_titles

    # The real overview candidate's own body (a mermaid diagram, no blank lines) produces
    # exactly one paragraph-based chunk (chunker.py's own _paragraph_sections path, since the
    # body has no markdown headings) - so its exact marker line is independently checkable.
    overview_title = next(c.title for c in candidates if c.origin == "overview")
    overview_chunks = [c for c in doc_chunks if _marker_title(c.text) == overview_title]
    assert overview_chunks
    assert overview_chunks[0].text.splitlines()[0] == f"FQN: Doc: {overview_title}"


def test_furnished_page_doc_chunks_survive_real_citable_chunks_pass(tmp_path: Path) -> None:
    """The card's own required real proof: real extraction + real chunking + a real
    citable_chunks pass (symbol_index_from_api_surface/validate_document/citable_chunks,
    exactly as infra/ingest.py itself does, not a mock) against pdf/net's own real furnished
    fixture and real api_surface.json - at least one chunk from each of the overview/content
    origins survives as citable.

    pdf/net's own real faq.enable is confirmed False in
    tests/fixtures/furnished/pdf_net/pages/_index.md (``faq: {enable: false, list: []}``), so
    no faq candidate exists at all here - never asserted to survive for pdf/net specifically.
    """
    page = _load_real_page()
    args = _doc_build_args()

    candidates = extract_doc_sections(page)
    title_to_origin = {c.title: c.origin for c in candidates}
    assert not any(c.origin == "faq" for c in candidates), (
        "pdf/net's own faq.enable is False - a real faq candidate here would mean this "
        "fixture changed and the origin-coverage assertions below need re-checking"
    )
    assert {c.origin for c in candidates} == {"overview", "content"}

    doc_chunks = build_chunks._build_doc_chunks(page, args)

    fixture = json.loads(API_SURFACE.read_text(encoding="utf-8"))
    symbol_index = symbol_index_from_api_surface(fixture["types"])
    known_counts = known_counts_from_fixture(fixture)
    validated = validate_document(doc_chunks, symbol_index, known_counts)
    citable = citable_chunks(validated)

    assert citable, "expected at least one real doc chunk to survive the real citable_chunks pass"

    surviving_origins = {title_to_origin[_marker_title(chunk.text)] for chunk in citable}
    assert "overview" in surviving_origins, (
        "expected at least one overview-origin doc chunk to survive citation validation"
    )
    assert "content" in surviving_origins, (
        "expected at least one content-origin doc chunk to survive citation validation"
    )


def test_furnished_flags_are_all_required_together(tmp_path: Path) -> None:
    """Giving only some of the four furnished-path flags (--furnished-page,
    --library-repository, --library-commit, --library-platform - REQ-G2-048/TC-091 replaced
    --library-csproj with the more general --library-platform in this required group) is a
    usage error, not a silent partial behavior - argparse's own error path, no network or
    dotnet needed.
    """
    out_path = tmp_path / "chunks.json"

    with pytest.raises(SystemExit):
        _run_main(
            [
                "--api-surface",
                str(API_SURFACE),
                "--title",
                "Aspose.PDF FOSS for .NET",
                "--out",
                str(out_path),
                "--furnished-page",
                str(FURNISHED_PAGE),
                # library-* flags deliberately omitted
            ]
        )


def test_furnished_platform_specific_required_flag_is_enforced(tmp_path: Path) -> None:
    """REQ-G2-048/TC-091: --library-platform dotnet without the dotnet-specific
    --library-csproj flag is a usage error with a clear message naming the missing flag -
    argparse's own error path, no network or dotnet needed.
    """
    out_path = tmp_path / "chunks.json"

    with pytest.raises(SystemExit):
        _run_main(
            [
                "--api-surface",
                str(API_SURFACE),
                "--title",
                "Aspose.PDF FOSS for .NET",
                "--out",
                str(out_path),
                "--furnished-page",
                str(FURNISHED_PAGE),
                "--library-repository",
                LIBRARY_REPOSITORY,
                "--library-commit",
                LIBRARY_COMMIT,
                "--library-platform",
                "dotnet",
                # --library-csproj deliberately omitted
            ]
        )


# --- slides/python (REQ-G2-048/TC-091 dispatch layer) -----------------------------------

SLIDES_PYTHON_FURNISHED_PAGE = Path("tests/fixtures/furnished/slides_python/pages/_index.md")

# Pinned exactly as TC-081's own inputs used - the same real, non-mocked commit already
# proven to prepare_python_library/verify_python_example cleanly.
SLIDES_PYTHON_LIBRARY_REPOSITORY = "aspose-slides-foss/Aspose.Slides-FOSS-for-Python"
SLIDES_PYTHON_LIBRARY_COMMIT = "4e63447ba79d1c27a5192844847d9f872c5b92ad"


def test_furnished_page_dispatches_to_real_verify_python_example_for_slides_python(
    tmp_path: Path,
) -> None:
    """Real end-to-end, non-dotnet proof that the new --library-platform dispatch layer
    genuinely reaches verify_python_example (not just a mocked/unit-tested dispatch dict):
    a real furnished slides/python fixture, a real git clone + venv + `pip install -e` of the
    pinned reference library (example_verifier.prepare_python_library), and a real Python
    subprocess execution of each candidate (example_verifier.verify_python_example).

    This is the card's explicit acceptance bar for the dispatch mechanism itself (AGENTS.md's
    2026-09-25 "Integration and liveness" section): a real, non-dotnet platform actually
    invoked through this CLI, not merely unit-tested in isolation. The other 4 non-dotnet
    platforms get their own live E2E proof in future cards, once each pilot's own
    docker-compose ingestion service is wired up.
    """
    out_path = tmp_path / "chunks.json"

    _run_main(
        [
            "--api-surface",
            str(API_SURFACE),
            "--title",
            "Aspose.PDF FOSS for .NET",
            "--out",
            str(out_path),
            "--furnished-page",
            str(SLIDES_PYTHON_FURNISHED_PAGE),
            "--library-repository",
            SLIDES_PYTHON_LIBRARY_REPOSITORY,
            "--library-commit",
            SLIDES_PYTHON_LIBRARY_COMMIT,
            "--library-platform",
            "python",
            # no extra platform-specific flag needed for python
        ]
    )

    produced = json.loads(out_path.read_text(encoding="utf-8"))
    chunks = produced["chunks"]

    example_chunks = [c for c in chunks if c["content_type"] == "example"]
    assert example_chunks, (
        "expected at least one real slides/python candidate example to really "
        f"execute-verify against {SLIDES_PYTHON_LIBRARY_REPOSITORY}@{SLIDES_PYTHON_LIBRARY_COMMIT} "
        "via the --library-platform python dispatch path, but none did"
    )

    # Every emitted example chunk's source_kind is "furnished", and its provenance is the
    # slides/python library's own repository/commit - not the pdf/net --api-surface fixture's.
    for chunk in example_chunks:
        assert chunk["source_kind"] == "furnished"
        assert chunk["provenance"]["repository"] == SLIDES_PYTHON_LIBRARY_REPOSITORY
        assert chunk["provenance"]["commit"] == SLIDES_PYTHON_LIBRARY_COMMIT
        assert chunk["text"].splitlines()[-1] == f"Source-Commit: {SLIDES_PYTHON_LIBRARY_COMMIT}"

    # A real, recognizable fragment of the real slides/python fixture's own code - not a
    # synthetic placeholder - proving verify_python_example really ran real candidate code.
    all_example_text = "\n".join(c["text"] for c in example_chunks)
    assert "aspose.slides_foss" in all_example_text

    print(f"real slides/python chunks.json: {len(example_chunks)} example chunks")
    for chunk in example_chunks:
        print("---- example chunk ----")
        print(chunk["text"])
