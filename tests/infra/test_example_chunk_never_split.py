"""Offline tests for TC-800 (REQ-G3-014, RD2-030): every verified example is exactly ONE chunk.

``infra/build_chunks.py`` writes an example body unfenced. The fence-aware chunker (TC-611) therefore
never engaged, and the plain chunker cut a README example at every column-0 ``#`` comment line of a
python program: the code after it became a headerless orphan chunk and the "verified" snippet was
served truncated. These tests drive the REAL ``build_chunks.main`` path (a fake prepare and a fake
verifier, the same seam ``test_build_chunks_candidate_chains.py`` uses) and prove the example is one
chunk, with FQN, Kind and the whole program, for a README and for an authored example, and that an
over-long example is truncated by the size cap, never split. The last tests read the committed source
of ``infra/build_chunks.py`` to prove the single-chunk path is the one the entrypoint calls.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest
import yaml

import infra.build_chunks as build_chunks
from foss_mcp.indexing.authored_examples import AUTHORED_LABEL
from foss_mcp.indexing.example_candidates import CandidateExample
from foss_mcp.indexing.example_verifier import VerificationResult
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_CHUNKS_SOURCE = REPO_ROOT / "infra" / "build_chunks.py"
API_SURFACE = REPO_ROOT / "tests/fixtures/pdf_net/api_surface.json"

REPOSITORY = "example-org/reference-library"
COMMIT = "3" * 40
TOOLCHAIN = "toolchain 1.0"

# Two column-0 ``#`` comment lines: the chunker reads each as a markdown heading.
COMMENTED_PROGRAM = (
    "import foo\n# first comment at column 0\nthing = foo.Thing()\n# second comment at column 0\nprint(thing)"
)
AUTHORED_PROGRAM = (
    "# Authored: open the thing, then print it\nimport foo\n# a second comment\nprint(foo.Thing())"
)


def _write_page(path: Path, title: str, code: str) -> Path:
    front_matter = {
        "title": "Never split fixture",
        "single": {
            "block": [{"title": title, "content": f"Prose for {title}.\n\n```python\n{code}\n```\n"}],
        },
    }
    path.write_text("---\n" + yaml.safe_dump(front_matter, sort_keys=False) + "---\n", encoding="utf-8")
    return path


def _stub_python_toolchain(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_verify(candidate: CandidateExample, **kwargs: object) -> VerificationResult:
        return VerificationResult(candidate=candidate, verified=True, output="ok")

    monkeypatch.setattr(build_chunks, "prepare_python_library", lambda *a, **k: Path("prepared") / "lib")
    monkeypatch.setitem(build_chunks._PLATFORM_DISPATCH["python"], "verify", fake_verify)
    monkeypatch.setattr(build_chunks, "toolchain_id", lambda name: TOOLCHAIN)


def _build_example_chunks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    page_path: Path,
    *,
    authored: tuple[CandidateExample, ...] = (),
) -> list[dict]:
    """Run the real ``main`` and return every chunk of content type ``example`` it wrote."""
    _stub_python_toolchain(monkeypatch)
    monkeypatch.setattr(build_chunks, "_load_authored_candidates", lambda args: authored)
    out = tmp_path / "chunks.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build_chunks.py",
            "--api-surface",
            str(API_SURFACE),
            "--title",
            "Never split fixture",
            "--out",
            str(out),
            "--furnished-page",
            str(page_path),
            "--library-repository",
            REPOSITORY,
            "--library-commit",
            COMMIT,
            "--library-platform",
            "python",
        ],
    )
    build_chunks.main()
    chunks = json.loads(out.read_text(encoding="utf-8"))["chunks"]
    return [chunk for chunk in chunks if chunk["content_type"] == "example"]


def _authored_candidate(title: str, code: str) -> CandidateExample:
    return CandidateExample(
        title=title,
        description="An authored example, written against the real API.",
        language="python",
        code=code,
        page_code=(code,),
        code_sha256=build_chunks.normalised_code_sha256(code),
        source="sidecar",
        starts_example=True,
        imports_only=False,
        import_prefix=(),
        provenance="authored",
    )


# --- the reproduction case: a README example with column-0 comments ----------------------------------------


def test_a_readme_example_with_column_0_comments_is_exactly_one_chunk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page_path = _write_page(tmp_path / "_index.md", "Comment Heavy", COMMENTED_PROGRAM)

    example_chunks = _build_example_chunks(monkeypatch, tmp_path, page_path)

    # Before TC-800 this was 3 chunks: the first with the header and `import foo`, the others headerless.
    assert len(example_chunks) == 1, [chunk["text"] for chunk in example_chunks]
    (chunk,) = example_chunks
    lines = chunk["text"].splitlines()
    assert lines[0] == "FQN: Example: Comment Heavy"
    assert lines[1] == "Kind: verified_example"
    assert "Example:\n" + COMMENTED_PROGRAM + "\nSource-Commit: " + COMMIT in chunk["text"]
    assert chunk["section_title"] == "Example: Comment Heavy"
    assert chunk["source_kind"] == "furnished"


def test_an_authored_example_with_column_0_comments_is_exactly_one_chunk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A page whose only fence is another language: the authored example is the one example published.
    page_path = tmp_path / "_index.md"
    page_path.write_text("---\ntitle: Never split fixture\n---\n", encoding="utf-8")
    authored = (_authored_candidate("Authored One", AUTHORED_PROGRAM),)

    example_chunks = _build_example_chunks(monkeypatch, tmp_path, page_path, authored=authored)

    assert len(example_chunks) == 1, [chunk["text"] for chunk in example_chunks]
    (chunk,) = example_chunks
    lines = chunk["text"].splitlines()
    assert lines[0] == "FQN: Example: Authored One"
    assert lines[1] == "Kind: verified_example"
    assert lines[2:4] == ["Provenance: authored", "Verification: passed"]
    assert "\n".join(lines[4 : 4 + len(AUTHORED_LABEL.splitlines())]) == AUTHORED_LABEL
    assert "Example:\n" + AUTHORED_PROGRAM + "\nSource-Commit: " + COMMIT in chunk["text"]


def test_a_readme_and_an_authored_example_together_are_one_chunk_each(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    page_path = _write_page(tmp_path / "_index.md", "Comment Heavy", COMMENTED_PROGRAM)
    authored = (_authored_candidate("Authored One", AUTHORED_PROGRAM),)

    example_chunks = _build_example_chunks(monkeypatch, tmp_path, page_path, authored=authored)

    fqns = sorted(chunk["text"].splitlines()[0] for chunk in example_chunks)
    assert fqns == ["FQN: Example: Authored One", "FQN: Example: Comment Heavy"]
    assert all(chunk["text"].count("Kind: verified_example") == 1 for chunk in example_chunks)


def test_an_example_without_comments_keeps_the_text_the_chunker_always_gave_it() -> None:
    """The change is invisible to an example the chunker never split: same section title, same text."""
    doc = make_document(
        source_kind=SourceKind.FURNISHED,
        content_type="example",
        provenance=Provenance(repository=REPOSITORY, commit=COMMIT, path="page.md"),
        evidence_refs=(f"{REPOSITORY}@{COMMIT}",),
        title="Plain",
        body="# Example: Plain\n\nFQN: Example: Plain\nKind: verified_example\nProse.\n\nExample:\nimport foo\nprint(foo)",
    )
    (via_chunker,) = chunk_document(doc)

    assert build_chunks._single_example_chunk(doc) == via_chunker


# --- the size cap still applies to the one chunk -------------------------------------------------------------


def test_a_very_long_example_is_one_truncated_chunk_never_split(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = "\n".join(f"# step {n} at column 0\nvalue_{n} = foo.step({n})" for n in range(1500))
    program = "import foo\n" + body
    assert len(program) > build_chunks.MAX_CHUNK_TEXT_CHARS
    page_path = _write_page(tmp_path / "_index.md", "Very Long", program)

    example_chunks = _build_example_chunks(monkeypatch, tmp_path, page_path)

    assert len(example_chunks) == 1, len(example_chunks)
    (chunk,) = example_chunks
    text = chunk["text"]
    assert text.startswith("FQN: Example: Very Long\nKind: verified_example\n")
    assert "import foo\n# step 0 at column 0\nvalue_0 = foo.step(0)\n" in text
    assert any(
        line.startswith("Truncated: ") and line.endswith(" characters omitted") for line in text.splitlines()
    )
    assert text.splitlines()[-1] == f"Source-Commit: {COMMIT}"
    assert len(text) < build_chunks.MAX_CHUNK_TEXT_CHARS + 200


# --- wiring: the entrypoint really builds examples through the single-chunk path -------------------------------


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    (function,) = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name]
    return function


def _called_names(function: ast.FunctionDef) -> list[str]:
    names = []
    for node in ast.walk(function):
        if isinstance(node, ast.Call):
            target = node.func
            names.append(target.id if isinstance(target, ast.Name) else getattr(target, "attr", ""))
    return names


def test_the_entrypoint_builds_examples_through_the_single_chunk_path() -> None:
    tree = ast.parse(BUILD_CHUNKS_SOURCE.read_text(encoding="utf-8"))

    # main() calls the example-building function ...
    assert "_build_verified_example_chunks" in _called_names(_function(tree, "main"))

    # ... which hands every document to _single_example_chunk and never to the section-splitting chunker.
    builder_calls = _called_names(_function(tree, "_build_verified_example_chunks"))
    assert "_single_example_chunk" in builder_calls
    assert "chunk_document" not in builder_calls

    # The single-chunk helper itself builds exactly one Chunk and does not split.
    helper_calls = _called_names(_function(tree, "_single_example_chunk"))
    assert helper_calls.count("Chunk") == 1
    assert "chunk_document" not in helper_calls


def test_there_is_no_provenance_special_case_left_in_the_example_builder() -> None:
    """One path for every provenance: the authored-only collapse of TC-460 is gone, not duplicated."""
    source = _function(
        ast.parse(BUILD_CHUNKS_SOURCE.read_text(encoding="utf-8")), "_build_verified_example_chunks"
    )
    for node in ast.walk(source):
        if isinstance(node, ast.If):
            test_source = ast.unparse(node.test)
            assert "len(example_chunks)" not in test_source, test_source
    assert "example_chunks" not in {node.id for node in ast.walk(source) if isinstance(node, ast.Name)}
