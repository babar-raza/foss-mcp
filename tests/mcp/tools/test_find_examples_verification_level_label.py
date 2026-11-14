"""G3/TC-802 (REQ-G3-014): the served label is derived from the stamped verification level.

Before this card ``find_examples.runtime_label`` could only say what a ``Runtime-Status`` line said (java and
dotnet runs); a python or cpp example that was RUN to exit 0 by its verifier carried no such line and was served as
"compiled only (run not verified)" - safe but untrue. A chunk now carries ``Verification-Level:`` (and
``Verified-With:``) and the label's sentence comes from the level, never from the platform.

Pinned here, offline and deterministic (synthetic generations through the real ``publish_generation``; a copy of the
committed generations is only READ; no gateway, no network, no clock, no skip):

* one sentence per level, and no sentence claims more than its level proves;
* a ``Runtime-Status`` line (the example was started) still wins over the level, exactly as before;
* a chunk with no ``Verification-Level`` (every committed generation until the coordinated regeneration), or with a
  level this server does not know, renders EXACTLY as before - the legacy fallback;
* ``Verified-With`` names the toolchain in the served note;
* the lines ``infra/build_chunks.py`` writes are the lines the server reads (writer and reader agree);
* the three answer branches (exact, lexical, vector) all carry the derived note.

Negative control: reverse-applying the production part of the commit removes the level reader, so the derived
sentences disappear and the tests below fail.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import infra.build_chunks as build_chunks
from foss_mcp.indexing.example_verifier import VERIFICATION_LEVELS
from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.publisher import publish_generation
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools import find_examples as find_examples_module
from foss_mcp.mcp.tools.find_examples import (
    AUTHORED_NOTE_TAIL,
    AUTHORED_VERIFICATION_NOTE,
    LEVEL_LABELS,
    README_NOTE_TAIL,
    README_VERIFICATION_NOTE,
    RUNTIME_LABEL_COMPILED_ONLY,
    RUNTIME_LABEL_NO_OUTPUT,
    _runtime_rank,
    _verification_fields,
    _verification_note,
    find_examples,
    is_verified_example_text,
    runtime_label,
)
from foss_mcp.mcp.tools.vector_guard import _set_guard
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document
from tests.indexing.test_index_writers import DeterministicEmbeddingProvider

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
FIND_EXAMPLES_SOURCE = REPO_ROOT / "src" / "foss_mcp" / "mcp" / "tools" / "find_examples.py"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
TOOL = "Python 3.12.4"


def _chunk(title: str, header: str = "", provenance: str = "readme") -> str:
    authored = "Provenance: authored\nVerification: passed\n" if provenance == "authored" else ""
    return (
        f"FQN: Example: {title}\nKind: verified_example\n{authored}{header}"
        f"Does one thing.\n\nExample:\nrun()\nSource-Commit: {COMMIT}"
    )


class _NoGatewayEmbeddingProvider:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("no embedding gateway in this deployment")


@pytest.fixture(autouse=True)
def _no_embedding_gateway(monkeypatch: pytest.MonkeyPatch) -> object:
    monkeypatch.setattr(find_examples_module, "GatewayEmbeddingProvider", _NoGatewayEmbeddingProvider)
    _set_guard(None)
    yield None
    _set_guard(None)


def _publish(root: Path, texts: list[str], platform: str = "python") -> GenerationManifestStore:
    store = GenerationManifestStore(root)
    body = "\n\n".join(f"## section {index}\n\n{text}" for index, text in enumerate(texts))
    document = make_document(
        source_kind=SourceKind.SELF_EXTRACTED,
        content_type="api_surface",
        provenance=Provenance(repository="https://example.invalid/repo", commit=COMMIT),
        evidence_refs=(),
        title=f"pdf/{platform} synthetic",
        body=body,
    )
    chunks = chunk_document(document)
    assert [chunk.text for chunk in chunks] == texts
    lease = store.acquire_lease(f"pdf::{platform}::self_extracted", "worker-1", "pending")
    publish_generation(
        store,
        family="pdf",
        platform=platform,
        source_kind="self_extracted",
        expected_active=None,
        chunks=chunks,
        embedding_provider=DeterministicEmbeddingProvider(),
        lease=lease,
    )
    return store


# ---------------------------------------------------------------------------------------------------
# one sentence per level
# ---------------------------------------------------------------------------------------------------

EXPECTED_SENTENCES = {
    "syntax-only": "syntax-checked only; not compiled or run",
    "type-checked": "type-checked against the library; not run",
    "built": "built against the library; not run",
    "ran-exit0": "built and run to exit 0; its output was not checked",
    "ran-produced-output": "built and run to exit 0 and produced output",
    "artifact-validated": "built and run to exit 0 and produced an artifact that was validated",
}


def test_every_level_of_the_vocabulary_has_exactly_one_sentence() -> None:
    assert set(LEVEL_LABELS) == set(VERIFICATION_LEVELS)
    assert LEVEL_LABELS == EXPECTED_SENTENCES


@pytest.mark.parametrize("level", VERIFICATION_LEVELS)
def test_the_label_is_the_sentence_of_the_chunks_level(level: str) -> None:
    text = _chunk("Level", f"Verification-Level: {level}\n")
    assert runtime_label(text) == EXPECTED_SENTENCES[level]
    assert _verification_note(text) == f"{EXPECTED_SENTENCES[level]}; {README_NOTE_TAIL}"


@pytest.mark.parametrize("level", VERIFICATION_LEVELS)
def test_a_label_never_says_the_example_was_run_unless_its_level_is_a_run_level(level: str) -> None:
    label = runtime_label(_chunk("Level", f"Verification-Level: {level}\n"))
    claims_run = "run to exit 0" in label
    assert claims_run is (level in ("ran-exit0", "ran-produced-output", "artifact-validated"))
    if level in ("syntax-only", "type-checked", "built"):
        assert "not run" in label or "not compiled or run" in label


def test_a_python_example_run_to_exit_zero_is_no_longer_served_as_compiled_only() -> None:
    legacy = _chunk("Run")
    stamped = _chunk("Run", "Verification-Level: ran-exit0\nVerified-With: Python 3.12.4\n")
    assert runtime_label(legacy) == RUNTIME_LABEL_COMPILED_ONLY
    assert runtime_label(stamped) != RUNTIME_LABEL_COMPILED_ONLY
    assert runtime_label(stamped) == EXPECTED_SENTENCES["ran-exit0"]


# ---------------------------------------------------------------------------------------------------
# Runtime-Status still wins; the legacy fallback is unchanged
# ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Runtime-Status: ran\nRuntime-Produced-Bytes: 35\n", "compiled and ran, produced 35 bytes"),
        ("Runtime-Status: no_output\nRuntime-Produced-Bytes: 0\n", RUNTIME_LABEL_NO_OUTPUT),
        (
            "Runtime-Status: failed\nRuntime-Error: IOException: nope\n",
            "compiles; fails at runtime: IOException: nope",
        ),
    ],
)
def test_a_runtime_status_line_wins_over_the_level(header: str, expected: str) -> None:
    text = _chunk("Run", f"Verification-Level: built\n{header}")
    assert runtime_label(text) == expected


def test_a_java_chunk_with_the_level_and_the_run_lines_keeps_the_run_sentence() -> None:
    text = _chunk(
        "Run",
        "Verification-Level: ran-produced-output\nVerified-With: javac 21.0.11\n"
        "Runtime-Status: ran\nRuntime-Produced-Bytes: 35\n",
    )
    assert runtime_label(text) == "compiled and ran, produced 35 bytes"
    assert (
        _verification_note(text)
        == f"compiled and ran, produced 35 bytes (verified with javac 21.0.11); {README_NOTE_TAIL}"
    )


@pytest.mark.parametrize("header", ["", "Harness: wrapped by the verifier in int main()\n"])
def test_a_legacy_chunk_renders_exactly_as_before(header: str) -> None:
    readme = _chunk("Legacy", header)
    authored = _chunk("Legacy", header, provenance="authored")
    assert runtime_label(readme) == RUNTIME_LABEL_COMPILED_ONLY
    assert _verification_note(readme) == README_VERIFICATION_NOTE
    assert _verification_note(authored) == AUTHORED_VERIFICATION_NOTE
    assert _verification_fields(readme) == ("", "")


@pytest.mark.parametrize("level", ["", "ran-forever", "RAN-EXIT0", "unknown", "ran-exit0-and-more"])
def test_a_level_this_server_does_not_know_falls_back_to_the_legacy_label(level: str) -> None:
    text = _chunk("Odd", f"Verification-Level: {level}\n")
    assert runtime_label(text) == RUNTIME_LABEL_COMPILED_ONLY
    assert _verification_note(text) == README_VERIFICATION_NOTE


def test_the_first_level_line_is_the_one_read_and_the_code_below_the_marker_is_never_read() -> None:
    text = (
        "FQN: Example: Odd\nKind: verified_example\nVerification-Level: built\nVerification-Level: ran-exit0\n"
        "Does one thing.\n\nExample:\nVerification-Level: artifact-validated\nrun()\n"
        f"Source-Commit: {COMMIT}"
    )
    assert runtime_label(text) == EXPECTED_SENTENCES["built"]


# ---------------------------------------------------------------------------------------------------
# Verified-With
# ---------------------------------------------------------------------------------------------------


def test_verified_with_is_named_in_the_note_for_readme_and_authored_examples() -> None:
    header = f"Verification-Level: ran-exit0\nVerified-With: {TOOL}\n"
    readme = _verification_note(_chunk("Tool", header))
    authored = _verification_note(_chunk("Tool", header, provenance="authored"))
    sentence = f"{EXPECTED_SENTENCES['ran-exit0']} (verified with {TOOL})"
    assert readme == f"{sentence}; {README_NOTE_TAIL}"
    assert authored == f"Authored for this index; {sentence}; {AUTHORED_NOTE_TAIL}"
    assert _verification_fields(_chunk("Tool", header)) == ("ran-exit0", TOOL)


def test_a_stamped_chunk_is_still_a_verified_example_and_ranks_first() -> None:
    text = _chunk("Tool", f"Verification-Level: built\nVerified-With: {TOOL}\n")
    assert is_verified_example_text(text) is True
    assert _runtime_rank(text) == 0


# ---------------------------------------------------------------------------------------------------
# the three answer branches carry the derived note
# ---------------------------------------------------------------------------------------------------


def test_exact_and_lexical_answers_carry_the_note_derived_from_the_level(tmp_path: Path) -> None:
    stamped = _chunk("Merge Two Documents", f"Verification-Level: ran-exit0\nVerified-With: {TOOL}\n")
    legacy = _chunk("Split A File")
    store = _publish(tmp_path / "m", [stamped, legacy])
    scope = Scope("pdf", "python")
    expected = f"{EXPECTED_SENTENCES['ran-exit0']} (verified with {TOOL}); {README_NOTE_TAIL}"

    for query, source in (("Example: Merge Two Documents", "exact"), ("merge two documents", "lexical")):
        result = find_examples(store, scope, query)
        assert isinstance(result, list) and len(result) == 1
        assert (result[0].source, result[0].verification) == (source, expected)

    legacy_result = find_examples(store, scope, "Example: Split A File")
    assert isinstance(legacy_result, list) and len(legacy_result) == 1
    assert legacy_result[0].verification == README_VERIFICATION_NOTE


# ---------------------------------------------------------------------------------------------------
# writer and reader agree
# ---------------------------------------------------------------------------------------------------


def test_the_lines_build_chunks_writes_are_the_lines_the_server_reads() -> None:
    lines = build_chunks._verification_level_lines("ran-exit0", TOOL)
    assert lines == f"Verification-Level: ran-exit0\nVerified-With: {TOOL}\n"
    text = _chunk("Agree", lines)
    assert _verification_fields(text) == ("ran-exit0", TOOL)
    assert runtime_label(text) == EXPECTED_SENTENCES["ran-exit0"]
    assert build_chunks._verification_level_lines("", None) == ""
    assert build_chunks._verification_level_lines("built", None) == "Verification-Level: built\n"
    # the toolchain text can never smuggle in the marker the code is split at, or a second line
    smuggled = build_chunks._verification_level_lines("built", "tool\nExample:\nx = 1")
    assert smuggled.count("\n") == 2 and "\nExample:\n" not in smuggled


def test_the_server_source_reads_the_prefixes_the_entrypoint_writes() -> None:
    source = FIND_EXAMPLES_SOURCE.read_text(encoding="utf-8")
    written = (REPO_ROOT / "infra" / "build_chunks.py").read_text(encoding="utf-8")
    for line in ("Verification-Level:", "Verified-With:"):
        assert line in source
        assert line in written
    assert "_verification_fields(" in source and "LEVEL_LABELS.get(" in source


# ---------------------------------------------------------------------------------------------------
# the committed generations are legacy chunks and must render unchanged
# ---------------------------------------------------------------------------------------------------

LEGACY_PILOTS = ["words__python", "cells__cpp", "3d__java", "pdf__go"]


@pytest.mark.parametrize("pilot", LEGACY_PILOTS)
def test_a_committed_generation_has_no_level_lines_and_keeps_its_old_labels(pilot: str) -> None:
    directory = DEMO_MANIFESTS / f"{pilot}__self_extracted"
    pointer = json.loads((directory / "active_pointer.json").read_text(encoding="utf-8"))["generation_id"]
    (path,) = [
        candidate
        for candidate in directory.glob("generation__*.json")
        if json.loads(candidate.read_text(encoding="utf-8"))["generation_id"] == pointer
    ]
    documents = json.loads(path.read_text(encoding="utf-8"))["payload"]["lexical_index"]["documents"]
    examples = [doc["text"] for doc in documents.values() if is_verified_example_text(doc["text"])]
    assert examples, pilot
    for text in examples:
        assert _verification_fields(text) == ("", ""), "a committed generation is not regenerated by TC-802"
        label = runtime_label(text)
        assert label not in EXPECTED_SENTENCES.values()
        legacy = (
            label == RUNTIME_LABEL_COMPILED_ONLY
            or label == RUNTIME_LABEL_NO_OUTPUT
            or label.startswith(("compiled and ran, produced ", "compiles; fails at runtime: "))
        )
        assert legacy, label
        assert _verification_note(text) == (
            f"{label}; {README_NOTE_TAIL}"
            if "Provenance: authored" not in text
            else f"Authored for this index; {label}; {AUTHORED_NOTE_TAIL}"
        )
