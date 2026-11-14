"""G3/TC-839 (REQ-G3-014): an offline simulation of the POST-REGENERATION state of every pilot.

Why this exists. The retrieval ratchets (``test_round3_retrieval_corpus.py``, ``test_forgiveness_acceptance.py``,
``test_find_examples_title_anchor.py``, ``test_find_examples_round2_recall_ratchet.py``) read the COMMITTED
``infra/demo-manifests``. The authored-example sidecars (``tests/fixtures/furnished/<pilot>/authored_examples.yaml``)
move faster than that data: a sidecar edit changes what the NEXT re-ingest will bake, never what is committed. A
retrieval regression caused by a sidecar is therefore invisible until the data is regenerated, and then it lands
in a release with no card to blame. This module builds the state that re-ingest will produce, offline, so the same
ratchets can be run against it BEFORE the regeneration.

What is simulated, per pilot that has an authored sidecar:

* every committed example chunk with ``Provenance: authored`` is dropped and replaced by one chunk per CURRENT
  sidecar entry, rendered in the real ``infra/build_chunks.py`` chunk format (the same ``# Example:`` heading body,
  ``_chunkable_description``, ``_single_example_chunk``, ``_sanitize_chunk_text`` and ``_with_source_commit_line``
  the ingestion job applies), carrying ``Provenance: authored``;
* the README-derived example chunks stay exactly as committed, except that - like every chunk the next ingestion
  writes (TC-802) - they gain the ``Verification-Level:`` / ``Verified-With:`` lines (``stamp_verification``);
* the lexical index (each document's tokens, ``doc_freq``) and the generation's per-kind ``stats`` are rebuilt the
  way ``lexical_index_writer.build_lexical_index`` / ``generation_manifest.payload_stats`` build them, so the
  retrieval code sees the same BM25 statistics a real ingestion would give it.

A pilot with no sidecar file (3d/java and tex/python carry their examples in code or have none) is copied as
committed. Nothing here verifies, compiles or runs an example (the real ingestion does that and may reject one);
the simulation assumes every sidecar example is published, which is the worst case for a ratchet. Nothing is ever
written to ``infra/demo-manifests``: the simulated tree lives in a directory the caller supplies.

Public API: ``build_post_regeneration_tree`` (the tree), ``simulated_pilot_counts`` (the per-pilot example counts),
and - so a test can repoint a ratchet module at the tree - ``repointed`` (a context manager that sets each given
module's ``DEMO_MANIFESTS`` and restores it).
"""

from __future__ import annotations

import contextlib
import dataclasses
import glob
import json
import shutil
from collections import Counter
from collections.abc import Iterable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml

import infra.build_chunks as build_chunks
from foss_mcp.indexing.authored_examples import AUTHORED_LABEL, SIDECAR_NAME
from foss_mcp.indexing.example_verifier import RuntimeRecord, legacy_verification_level
from foss_mcp.indexing.generation_manifest import payload_stats
from foss_mcp.indexing.lexical_index_writer import doc_id, tokenize
from foss_mcp.indexing.publisher import content_chunk_id
from foss_mcp.normalization.chunker import Chunk
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
FURNISHED = REPO_ROOT / "tests" / "fixtures" / "furnished"
VERIFIED_EXAMPLES = REPO_ROOT / "tests" / "fixtures" / "verified_examples"

_KIND_LINE = "Kind: verified_example"
_AUTHORED_LINE = "Provenance: authored"
# The header lines of an example chunk (``infra/build_chunks.py``), in the order it writes them after ``Kind:``.
_AUTHORED_HEADER_PREFIXES = ("Provenance:", "Verification:", "Authored example:")
_LEVEL_HEADER_PREFIXES = ("Verification-Level:", "Verified-With:")
_RUNTIME_HEADER_PREFIXES = ("Runtime-Status:", "Runtime-Produced-Bytes:", "Runtime-Error:", "Harness:")
_HEADER_PREFIXES = _AUTHORED_HEADER_PREFIXES + _LEVEL_HEADER_PREFIXES + _RUNTIME_HEADER_PREFIXES
# A java / dotnet authored example with no committed chunk to copy its run record from is simulated as one that
# ran and produced output (the only authored outcome that is published).
_DEFAULT_RUN_BYTES = 100
_RUN_PLATFORMS = frozenset({"java", "dotnet"})


@dataclasses.dataclass(frozen=True)
class PilotCounts:
    """One pilot's example counts in the committed tree and in the simulated one."""

    pilot: str
    committed_examples: int
    simulated_examples: int
    committed_authored: int
    simulated_authored: int
    sidecar_examples: int | None  # None: the pilot has no sidecar and is copied as committed


def library_platform(platform: str) -> str:
    """The ``--library-platform`` value of a pilot's platform directory name (``net`` is ``dotnet``)."""
    return "dotnet" if platform == "net" else platform


def is_example_text(text: str) -> bool:
    lines = text.split("\n", 2)
    return len(lines) > 1 and lines[1] == _KIND_LINE


def is_authored_example_text(text: str) -> bool:
    lines = text.split("\n", 3)
    return is_example_text(text) and len(lines) > 2 and lines[2] == _AUTHORED_LINE


def example_title(text: str) -> str:
    return text.split("\n", 1)[0].removeprefix("FQN: Example: ")


def _generation_path(scope_dir: Path) -> Path:
    paths = sorted(glob.glob(str(scope_dir / "generation__*.json")))
    assert len(paths) == 1, (scope_dir, paths)
    return Path(paths[0])


def _verified_with(pilot: str) -> str | None:
    """The toolchain the committed verification cache recorded for *pilot* (None when it has no entry)."""
    path = VERIFIED_EXAMPLES / f"{pilot}.json"
    if not path.is_file():
        return None
    for entry in json.loads(path.read_text(encoding="utf-8")).get("entries", []):
        toolchain = entry.get("toolchain")
        if isinstance(toolchain, str) and toolchain.strip():
            return toolchain.strip()
    return None


def _header_end(lines: list[str]) -> int:
    """The index of the first line after the header block that follows ``Kind:`` (line 1)."""
    index = 2
    while index < len(lines) and lines[index].startswith(_HEADER_PREFIXES):
        index += 1
    return index


def _runtime_record(header: Iterable[str]) -> RuntimeRecord:
    """The run facts a chunk header states (``Runtime-Status`` / ``Runtime-Produced-Bytes``), as a RuntimeRecord."""
    status = ""
    produced: int | None = None
    for line in header:
        if line.startswith("Runtime-Status:"):
            status = line.split(":", 1)[1].strip()
        elif line.startswith("Runtime-Produced-Bytes:"):
            with contextlib.suppress(ValueError):
                produced = int(line.split(":", 1)[1])
    if not status:
        return RuntimeRecord(compiled=True)
    if status == "ran":
        return RuntimeRecord(compiled=True, ran=True, exit_code=0, produced_output=produced or 1)
    return RuntimeRecord(compiled=True, ran=True, exit_code=0, produced_output=0)


def stamp_verification(text: str, platform: str, toolchain: str | None) -> str:
    """*text*, an example chunk written before TC-802, with the ``Verification-Level:`` / ``Verified-With:`` lines
    the next ingestion adds to it, and the blank line that closes the header whenever they are written (see
    ``build_chunks._build_verified_example_chunks``). A chunk that already carries them is returned unchanged."""
    lines = text.split("\n")
    end = _header_end(lines)
    header = lines[2:end]
    if any(line.startswith("Verification-Level:") for line in header):
        return text
    level = legacy_verification_level(platform, _runtime_record(header))
    level_lines = build_chunks._verification_level_lines(level, toolchain if level else None)
    if not level_lines:
        return text
    authored = [line for line in header if line.startswith(_AUTHORED_HEADER_PREFIXES)]
    rest = [line for line in header if not line.startswith(_AUTHORED_HEADER_PREFIXES)]
    stamped = [*lines[:2], *authored, *level_lines.rstrip("\n").split("\n"), *rest, "", *lines[end:]]
    return "\n".join(stamped)


def _committed_program(text: str) -> str:
    """The program a committed example chunk publishes: the text after its last ``Example:`` marker line, without
    the trailing ``Source-Commit:`` line."""
    _, _, code = text.rpartition("\n" + build_chunks._EXAMPLE_MARKER_LINE + "\n")
    lines = code.split("\n")
    if lines and lines[-1].startswith("Source-Commit:"):
        lines = lines[:-1]
    return "\n".join(lines)


def render_authored_chunk(
    example: dict[str, Any],
    *,
    repository: str,
    commit: str,
    platform: str,
    toolchain: str | None,
    committed: str | None,
) -> Chunk:
    """The example chunk the ingestion job publishes for one sidecar entry.

    *committed* is the text of the committed authored chunk with the same title, when there is one: its run
    record (``Runtime-*`` lines) and harness line are what the verifier recorded for that very example, and when
    its published program still holds the sidecar's code (the dotnet / rust / cpp assemblies add lines around it)
    that program is reused. Without one a java / dotnet example is simulated as having run and produced output.
    """
    title = str(example["title"])
    code = str(example["code"]).strip()
    description = str(example["description"]).strip()
    runtime_lines = ""
    harness_line = ""
    program = code
    if committed is not None:
        header = committed.split("\n")[2 : _header_end(committed.split("\n"))]
        runtime_lines = "".join(f"{line}\n" for line in header if line.startswith("Runtime-"))
        harness_line = "".join(f"{line}\n" for line in header if line.startswith("Harness:"))
        published = _committed_program(committed)
        if code in published:
            program = published
    elif platform in _RUN_PLATFORMS:
        runtime_lines = f"Runtime-Status: ran\nRuntime-Produced-Bytes: {_DEFAULT_RUN_BYTES}\n"
    level = legacy_verification_level(
        platform, _runtime_record(line for line in runtime_lines.split("\n") if line)
    )
    level_lines = build_chunks._verification_level_lines(level, toolchain if level else None)
    header_end = "\n" if level_lines else ""
    authored_lines = f"Provenance: authored\nVerification: passed\n{AUTHORED_LABEL}\n"
    document = make_document(
        source_kind=SourceKind.FURNISHED,
        content_type="example",
        provenance=Provenance(repository=repository, commit=commit, path="simulated"),
        evidence_refs=(f"{repository}@{commit}",),
        title=title,
        body=(
            f"# Example: {title}\n\n"
            f"FQN: Example: {title}\n"
            f"Kind: verified_example\n"
            f"{authored_lines}"
            f"{level_lines}"
            f"{runtime_lines}"
            f"{harness_line}"
            f"{header_end}"
            f"{build_chunks._chunkable_description(description)}\n\n"
            f"Example:\n{program}"
        ),
    )
    chunk = build_chunks._single_example_chunk(document)
    chunk = dataclasses.replace(chunk, text=build_chunks._sanitize_chunk_text(chunk.text))
    return build_chunks._with_source_commit_line(chunk)


def _rewrite_generation(scope_dir: Path, sidecar: Path | None, pilot: str, platform: str) -> PilotCounts:
    path = _generation_path(scope_dir)
    data = json.loads(path.read_text(encoding="utf-8"))
    lexical = data["payload"]["lexical_index"]
    generation_id = lexical["generation_id"]
    documents: dict[str, dict[str, Any]] = lexical["documents"]
    toolchain = _verified_with(pilot)

    def examples(docs: dict[str, dict[str, Any]]) -> list[str]:
        return [d["text"] for d in docs.values() if is_example_text(d["text"])]

    committed_examples = examples(documents)
    committed_authored = [t for t in committed_examples if is_authored_example_text(t)]
    sidecar_examples: int | None = None
    if sidecar is not None:
        sidecar_doc = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
        entries = sidecar_doc["examples"]
        sidecar_examples = len(entries)
        by_title = {example_title(text): text for text in committed_authored}
        kept = {key: doc for key, doc in documents.items() if not is_authored_example_text(doc["text"])}
        for key, doc in kept.items():
            if is_example_text(doc["text"]):
                doc["text"] = stamp_verification(doc["text"], platform, toolchain)
                doc["tokens"] = tokenize(doc["text"])
            doc["chunk_id"] = doc.get("chunk_id") or key.rsplit("::", 1)[-1]
        for entry in entries:
            chunk = render_authored_chunk(
                entry,
                repository=str(sidecar_doc["library_repository"]),
                commit=str(sidecar_doc["library_commit"]),
                platform=platform,
                toolchain=toolchain,
                committed=by_title.get(str(entry["title"])),
            )
            chunk_id = content_chunk_id(chunk)
            kept[doc_id(generation_id, chunk_id)] = {
                "chunk_id": chunk_id,
                "text": chunk.text,
                "tokens": tokenize(chunk.text),
            }
        documents = kept
        lexical["documents"] = documents
        frequency: Counter[str] = Counter()
        for doc in documents.values():
            frequency.update(set(doc["tokens"]))
        lexical["doc_freq"] = dict(frequency)
        data["payload"]["stats"] = {**data["payload"].get("stats", {}), **payload_stats(data["payload"])}
        path.write_text(json.dumps(data), encoding="utf-8")
    simulated_examples = examples(documents)
    return PilotCounts(
        pilot,
        len(committed_examples),
        len(simulated_examples),
        len(committed_authored),
        sum(1 for t in simulated_examples if is_authored_example_text(t)),
        sidecar_examples,
    )


def build_post_regeneration_tree(
    dest: Path,
    manifests_root: Path = DEMO_MANIFESTS,
    furnished_root: Path = FURNISHED,
) -> dict[str, PilotCounts]:
    """Copy every ``*__self_extracted`` pilot of *manifests_root* into *dest* and rewrite each pilot that has an
    authored sidecar under *furnished_root* to its post-regeneration state (see the module docstring). Returns the
    per-pilot example counts, keyed ``family__platform``. ``manifests_root`` is only read."""
    counts: dict[str, PilotCounts] = {}
    for scope_dir in sorted(manifests_root.glob("*__self_extracted")):
        if not scope_dir.is_dir():
            continue
        family, platform, _ = scope_dir.name.split("__")
        pilot = f"{family}_{platform}"
        target = dest / scope_dir.name
        shutil.copytree(scope_dir, target)
        sidecar = furnished_root / pilot / SIDECAR_NAME
        counts[f"{family}__{platform}"] = _rewrite_generation(
            target, sidecar if sidecar.is_file() else None, pilot, library_platform(platform)
        )
    return counts


def simulated_pilot_counts(counts: dict[str, PilotCounts]) -> dict[str, int]:
    """``family__platform -> simulated example count`` (the pilot-by-pilot example counts the card reports)."""
    return {pilot: value.simulated_examples for pilot, value in sorted(counts.items())}


@contextlib.contextmanager
def repointed(modules: Iterable[ModuleType], root: Path) -> Iterator[None]:
    """Set ``DEMO_MANIFESTS`` of every module in *modules* to *root* and restore the originals on exit."""
    saved = [(module, module.DEMO_MANIFESTS) for module in modules]
    try:
        for module in modules:
            module.DEMO_MANIFESTS = root
        yield
    finally:
        for module, original in saved:
            module.DEMO_MANIFESTS = original


__all__ = [
    "DEMO_MANIFESTS",
    "FURNISHED",
    "PilotCounts",
    "build_post_regeneration_tree",
    "example_title",
    "is_authored_example_text",
    "is_example_text",
    "library_platform",
    "render_authored_chunk",
    "repointed",
    "simulated_pilot_counts",
    "stamp_verification",
]
