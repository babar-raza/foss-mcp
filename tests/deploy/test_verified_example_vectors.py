"""G3/TC-442 (REQ-G3-007): every verified example is part of the vector index baked into the image.

Offline and deterministic: reads the committed infra/demo-manifests tree and the chart's values.yaml,
runs the real compaction on copies in tmp_path, and runs the real ``infra.ingest.main`` against a
real ``GenerationManifestStore`` with the production hashing provider. No cluster, no docker, no
network, no embedding gateway call.

Part (1)-(2) pin the committed data (a verified example chunk has its vector point, and compaction
keeps it). Part (3) pins that 5-decimal quantisation keeps a real point small and keeps cosine
geometry intact. Part (4)-(5) pin the opt-in ``--vector-decimals`` knob and its chart wiring. Part
(6) pins that a verified-example chunk body is selected by ``is_example_chunk``.
"""

from __future__ import annotations

import json
import shutil
import sys
from functools import cache
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml

import infra.ingest as ingest
from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.hashing_embedding_provider import HashingEmbeddingProvider
from foss_mcp.indexing.publisher import content_chunk_id
from foss_mcp.indexing.vector_index_writer import (
    build_vector_index,
    is_example_chunk,
    point_id,
    quantize_vector,
)
from foss_mcp.normalization.chunker import Chunk, chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE_DIR = REPO_ROOT / "scripts" / "release"
VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"
INGESTION_JOB = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "templates" / "ingestion-job.yaml"
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"

sys.path.insert(0, str(RELEASE_DIR))

import compact_demo_manifests as compact  # noqa: E402

EXPECTED_PILOTS = 40
VERIFIED_KIND_LINE = "Kind: verified_example"
# Measured on 15 real 4096-dimension points: 34,100-34,396 bytes each when rounded to 5 decimals
# (87,928 bytes on average unrounded); the bound carries about 16% headroom.
MAX_QUANTISED_POINT_BYTES = 40_000
DECIMALS = 5
MIN_SELF_COSINE = 0.99999
MAX_PAIRWISE_COSINE_CHANGE = 1e-4
# 14 verified examples were measured in the committed tree on 2026-10-11; the data only grows.
MIN_COMMITTED_VERIFIED_EXAMPLES = 14
SCOPE = "pdf::net::self_extracted"


def chart_pilots() -> list[dict[str, Any]]:
    data = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
    return [dict(p) for p in data["ingestion"]["pilots"]]


def scope_name(pilot: dict[str, Any]) -> str:
    return f"{pilot['family']}__{pilot['platform']}__{pilot['sourceKind']}"


PILOTS = chart_pilots()
PILOT_IDS = [f"{p['family']}_{p['platform']}" for p in PILOTS]


def read_active_generation(scope_dir: Path) -> dict[str, Any]:
    """The active generation document of one scope, resolved as the serving code does."""
    generation_id, path = compact.active_generation_path(scope_dir)
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["generation_id"] == generation_id, scope_dir.name
    return doc


@cache
def load_active_generation(scope_dir_name: str) -> dict[str, Any]:
    return read_active_generation(DEMO_MANIFESTS / scope_dir_name)


def verified_example_documents(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Every lexical document whose text carries the line ``Kind: verified_example``."""
    documents = doc["payload"]["lexical_index"]["documents"]
    return [d for d in documents.values() if VERIFIED_KIND_LINE in d["text"].splitlines()]


def points_by_chunk_id(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {p["chunk_id"]: p for p in doc["payload"]["vector_index"]["points"]}


def missing_example_points(doc: dict[str, Any]) -> list[str]:
    points = points_by_chunk_id(doc)
    return sorted(d["chunk_id"] for d in verified_example_documents(doc) if d["chunk_id"] not in points)


def cosine(a: Any, b: Any) -> float:
    left = np.asarray(a, dtype=np.float64)
    right = np.asarray(b, dtype=np.float64)
    return float(left @ right / (np.linalg.norm(left) * np.linalg.norm(right)))


def compact_json_bytes(value: Any) -> int:
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def all_real_points() -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for pilot in PILOTS:
        name = scope_name(pilot)
        for point in load_active_generation(name)["payload"]["vector_index"]["points"]:
            out.append((name, point))
    return out


# ---------------------------------------------------------------------------------------------------
# the chart names the forty pilots
# ---------------------------------------------------------------------------------------------------


def test_the_chart_names_forty_pilots_and_each_has_a_committed_scope() -> None:
    assert len(PILOTS) == EXPECTED_PILOTS
    assert len(set(PILOT_IDS)) == EXPECTED_PILOTS
    for pilot in PILOTS:
        assert (DEMO_MANIFESTS / scope_name(pilot) / "active_pointer.json").is_file(), scope_name(pilot)


def test_the_committed_tree_carries_verified_examples() -> None:
    total = sum(len(verified_example_documents(load_active_generation(scope_name(p)))) for p in PILOTS)
    assert total >= MIN_COMMITTED_VERIFIED_EXAMPLES


# ---------------------------------------------------------------------------------------------------
# (1) every verified example chunk has its vector point
# ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("pilot", PILOTS, ids=PILOT_IDS)
def test_every_verified_example_chunk_has_its_vector_point(pilot: dict[str, Any]) -> None:
    doc = load_active_generation(scope_name(pilot))
    vector_index = doc["payload"]["vector_index"]
    generation_id = doc["generation_id"]
    missing = missing_example_points(doc)
    assert not missing, (
        f"{pilot['family']}_{pilot['platform']}: verified examples without a vector point: {missing}"
    )
    points = points_by_chunk_id(doc)
    for lexical in verified_example_documents(doc):
        point = points[lexical["chunk_id"]]
        assert point["point_id"] == point_id(generation_id, lexical["chunk_id"])
        assert point["point_id"] == f"{generation_id}::point::{lexical['chunk_id']}"
        assert point["text"] == lexical["text"], lexical["chunk_id"]
        assert len(point["vector"]) == vector_index["dimension"], lexical["chunk_id"]


# ---------------------------------------------------------------------------------------------------
# (2) compaction keeps every verified example point unchanged
# ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("pilot", PILOTS, ids=PILOT_IDS)
def test_compaction_keeps_every_verified_example_point_unchanged(
    pilot: dict[str, Any], tmp_path: Path
) -> None:
    name = scope_name(pilot)
    before_doc = load_active_generation(name)
    before = points_by_chunk_id(before_doc)
    wanted = [d["chunk_id"] for d in verified_example_documents(before_doc)]

    copy = tmp_path / name
    shutil.copytree(DEMO_MANIFESTS / name, copy)
    compact.compact_scope(copy, apply=True)

    after_doc = read_active_generation(copy)
    after = points_by_chunk_id(after_doc)
    for chunk_id in wanted:
        assert chunk_id in after, f"{name}: compaction dropped verified example point {chunk_id}"
        assert after[chunk_id] == before[chunk_id], f"{name}: compaction changed point {chunk_id}"
    assert not missing_example_points(after_doc)


# ---------------------------------------------------------------------------------------------------
# (3) 5-decimal quantisation of the real committed points
# ---------------------------------------------------------------------------------------------------


def test_the_committed_tree_has_real_vector_points_to_quantise() -> None:
    assert len(all_real_points()) >= MIN_COMMITTED_VERIFIED_EXAMPLES


@pytest.mark.parametrize("pilot", PILOTS, ids=PILOT_IDS)
def test_quantised_real_points_are_small_and_keep_their_direction(pilot: dict[str, Any]) -> None:
    name = scope_name(pilot)
    for point in load_active_generation(name)["payload"]["vector_index"]["points"]:
        quantised = quantize_vector(point["vector"], DECIMALS)
        size = compact_json_bytes(list(quantised))
        assert size <= MAX_QUANTISED_POINT_BYTES, f"{name} {point['chunk_id']}: {size} bytes"
        assert cosine(quantised, point["vector"]) >= MIN_SELF_COSINE, f"{name} {point['chunk_id']}"


def test_quantisation_changes_no_pairwise_cosine_between_real_points_by_more_than_the_bound() -> None:
    by_dimension: dict[int, list[dict[str, Any]]] = {}
    for _, point in all_real_points():
        by_dimension.setdefault(len(point["vector"]), []).append(point)
    largest = 0.0
    for group in by_dimension.values():
        vectors = [p["vector"] for p in group]
        quantised = [quantize_vector(v, DECIMALS) for v in vectors]
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                change = abs(cosine(quantised[i], quantised[j]) - cosine(vectors[i], vectors[j]))
                largest = max(largest, change)
    assert largest <= MAX_PAIRWISE_COSINE_CHANGE, largest


@pytest.mark.parametrize("pilot", PILOTS, ids=PILOT_IDS)
def test_pairwise_cosine_change_within_one_pilot_is_bounded(pilot: dict[str, Any]) -> None:
    points = load_active_generation(scope_name(pilot))["payload"]["vector_index"]["points"]
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            a, b = points[i]["vector"], points[j]["vector"]
            qa, qb = quantize_vector(a, DECIMALS), quantize_vector(b, DECIMALS)
            assert abs(cosine(qa, qb) - cosine(a, b)) <= MAX_PAIRWISE_COSINE_CHANGE


# ---------------------------------------------------------------------------------------------------
# (4) quantize_vector, build_vector_index(decimals=...) and `ingest --vector-decimals`
# ---------------------------------------------------------------------------------------------------


def _chunk(text: str, title: str = "Section") -> Chunk:
    return Chunk(
        section_title=title,
        text=text,
        source_kind="self_extracted",
        content_type="api_surface",
        provenance=Provenance(repository="r/r", commit="c" * 40, path="p"),
        trust_tier="highest",
    )


EXAMPLE_TEXT = (
    "`Document.Open` opens a file and returns a document that the caller later saves again.\n"
    "Example:\n```csharp\nvar d = Document.Open(path);\nd.Save(path);\n```"
)
PLAIN_TEXT = "Document is the root object of a PDF file."


@pytest.mark.parametrize("decimals", [1, 2, 5, 8, 12])
def test_quantize_vector_rounds_every_component(decimals: int) -> None:
    vector = (1 / 3, -2 / 3, 0.123456789012345, 2.5e-07, 0.0, 1.0)
    result = quantize_vector(vector, decimals)
    assert isinstance(result, tuple)
    assert result == tuple(round(float(x), decimals) for x in vector)


def test_quantize_vector_accepts_any_sequence_and_keeps_length() -> None:
    assert quantize_vector([0.123456, 0.987654], 3) == (0.123, 0.988)
    assert quantize_vector((), 5) == ()
    assert len(quantize_vector([0.1] * 4096, 5)) == 4096


def test_build_vector_index_without_decimals_returns_exactly_the_providers_vectors() -> None:
    provider = HashingEmbeddingProvider()
    chunks = [_chunk(EXAMPLE_TEXT), _chunk(PLAIN_TEXT)]
    ids = [content_chunk_id(c) for c in chunks]
    payload = build_vector_index(chunks, ids, provider, "g1")
    expected = provider.embed([c.text for c in chunks])
    assert [p["vector"] for p in payload["points"]] == [list(v) for v in expected]
    default = build_vector_index(chunks, ids, provider, "g1", decimals=None)
    assert default == payload


def test_build_vector_index_with_decimals_returns_the_vectors_rounded() -> None:
    provider = HashingEmbeddingProvider()
    chunks = [_chunk(EXAMPLE_TEXT), _chunk(PLAIN_TEXT)]
    ids = [content_chunk_id(c) for c in chunks]
    payload = build_vector_index(chunks, ids, provider, "g1", decimals=DECIMALS)
    expected = provider.embed([c.text for c in chunks])
    assert [p["vector"] for p in payload["points"]] == [list(quantize_vector(v, DECIMALS)) for v in expected]
    assert any(p["vector"] != list(v) for p, v in zip(payload["points"], expected, strict=True)), (
        "rounding changed nothing; the test vectors are not informative"
    )


def test_build_vector_index_keeps_exactly_the_documented_point_and_payload_keys() -> None:
    provider = HashingEmbeddingProvider()
    chunks = [_chunk(EXAMPLE_TEXT)]
    payload = build_vector_index(chunks, [content_chunk_id(chunks[0])], provider, "g1", decimals=DECIMALS)
    assert set(payload) == {"generation_id", "dimension", "points"}
    assert set(payload["points"][0]) == {"chunk_id", "point_id", "text", "vector"}
    assert payload["points"][0]["point_id"] == point_id("g1", payload["points"][0]["chunk_id"])


def test_build_vector_index_with_decimals_and_scope_quantises_only_the_selected_points() -> None:
    provider = HashingEmbeddingProvider()
    chunks = [_chunk(PLAIN_TEXT), _chunk(EXAMPLE_TEXT)]
    ids = [content_chunk_id(c) for c in chunks]
    payload = build_vector_index(chunks, ids, provider, "g1", include=is_example_chunk, decimals=DECIMALS)
    assert [p["text"] for p in payload["points"]] == [EXAMPLE_TEXT]
    assert payload["points"][0]["vector"] == list(
        quantize_vector(provider.embed([EXAMPLE_TEXT])[0], DECIMALS)
    )


def _write_chunks(path: Path) -> None:
    provenance = {"repository": "r/r", "commit": "c" * 40, "path": "api_surface.json"}
    entries = [
        {
            "section_title": title,
            "text": text,
            "source_kind": "self_extracted",
            "content_type": "api_surface",
            "provenance": provenance,
            "trust_tier": "highest",
            "evidence_refs": [],
        }
        for title, text in (("Document", PLAIN_TEXT), ("Document.Open", EXAMPLE_TEXT))
    ]
    path.write_text(json.dumps({"chunks": entries}), encoding="utf-8")


def _ingest_argv(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "ingest.py",
        "--family",
        "pdf",
        "--platform",
        "net",
        "--source-kind",
        "self_extracted",
        "--chunks",
        str(tmp_path / "chunks.json"),
        "--manifest-store",
        str(tmp_path / "manifests"),
        "--embedding-provider",
        "foss_mcp.indexing.hashing_embedding_provider:HashingEmbeddingProvider",
        "--vector-scope",
        "examples",
        "--min-symbols",
        "0",
        "--min-docs",
        "0",
        *extra,
    ]


def _run_ingest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *extra: str) -> dict[str, Any]:
    _write_chunks(tmp_path / "chunks.json")
    monkeypatch.setattr(sys, "argv", _ingest_argv(tmp_path, *extra))
    ingest.main()
    store = GenerationManifestStore(tmp_path / "manifests")
    generation_id = store.read_active(SCOPE)
    assert generation_id is not None
    return store.read_generation(SCOPE, generation_id).payload["vector_index"]


def test_ingest_with_vector_decimals_stores_rounded_components(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vector_index = _run_ingest(monkeypatch, tmp_path, "--vector-decimals", "5")
    assert [p["text"] for p in vector_index["points"]] == [EXAMPLE_TEXT]
    vector = vector_index["points"][0]["vector"]
    assert len(vector) == vector_index["dimension"]
    assert all(component == round(component, 5) for component in vector)
    expected = quantize_vector(HashingEmbeddingProvider().embed([EXAMPLE_TEXT])[0], 5)
    assert tuple(vector) == expected


def test_ingest_without_the_flag_stores_unrounded_components(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vector_index = _run_ingest(monkeypatch, tmp_path)
    vector = vector_index["points"][0]["vector"]
    assert tuple(vector) == tuple(HashingEmbeddingProvider().embed([EXAMPLE_TEXT])[0])
    assert any(component != round(component, 5) for component in vector)


@pytest.mark.parametrize("decimals", [1, 3, 12])
def test_ingest_accepts_the_documented_decimal_range(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, decimals: int
) -> None:
    vector = _run_ingest(monkeypatch, tmp_path, "--vector-decimals", str(decimals))["points"][0]["vector"]
    assert all(component == round(component, decimals) for component in vector)


@pytest.mark.parametrize("bad", ["0", "13", "-1", "100", "abc", "1.5", "", "five"])
def test_ingest_rejects_an_out_of_range_or_non_integer_decimals_with_exit_code_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    _write_chunks(tmp_path / "chunks.json")
    monkeypatch.setattr(sys, "argv", _ingest_argv(tmp_path, "--vector-decimals", bad))
    with pytest.raises(SystemExit) as raised:
        ingest.main()
    assert raised.value.code == 2


# ---------------------------------------------------------------------------------------------------
# (5) wiring on committed source: the chart's ingestion Job passes --vector-decimals 5
# ---------------------------------------------------------------------------------------------------


def test_the_ingestion_job_passes_vector_decimals_five_next_to_the_examples_scope() -> None:
    lines = [line.strip() for line in INGESTION_JOB.read_text(encoding="utf-8").splitlines()]
    assert lines.count("--vector-scope examples") == 1
    scope_at = lines.index("--vector-scope examples")
    assert lines[scope_at + 1] == "--vector-decimals 5"


def test_the_ingestion_job_passes_vector_decimals_exactly_once() -> None:
    assert INGESTION_JOB.read_text(encoding="utf-8").count("--vector-decimals") == 1


def test_infra_ingest_declares_the_vector_decimals_flag_and_passes_it_to_publish_generation() -> None:
    source = (REPO_ROOT / "infra" / "ingest.py").read_text(encoding="utf-8")
    assert '"--vector-decimals"' in source
    assert "vector_decimals=args.vector_decimals" in source
    publisher = (REPO_ROOT / "src" / "foss_mcp" / "indexing" / "publisher.py").read_text(encoding="utf-8")
    assert "decimals=vector_decimals" in publisher


# ---------------------------------------------------------------------------------------------------
# (6) a verified-example chunk body is selected by is_example_chunk
# ---------------------------------------------------------------------------------------------------

BODY_VERIFIED = (
    "# Example: Save a PDF\n\n"
    "FQN: Example: Save a PDF\n"
    "Kind: verified_example\n"
    "Saves a document to disk.\n\n"
    "Example:\nDocument d = new Document();\nd.Save(path);"
)
BODY_AUTHORED = (
    "# Example: Save a PDF\n\n"
    "FQN: Example: Save a PDF\n"
    "Kind: verified_example\n"
    "Provenance: authored\n"
    "Verification: passed\n"
    "Saves a document to disk.\n\n"
    "Example:\nDocument d = new Document();\nd.Save(path);"
)


def _chunks_of(body: str) -> list[Chunk]:
    document = make_document(
        source_kind=SourceKind.FURNISHED,
        content_type="example",
        provenance=Provenance(repository="r/r", commit="c" * 40, path="page.json"),
        evidence_refs=("r/r@" + "c" * 40,),
        title="Save a PDF",
        body=body,
    )
    return chunk_document(document)


@pytest.mark.parametrize("body", [BODY_VERIFIED, BODY_AUTHORED], ids=["verified", "authored"])
def test_a_verified_example_body_is_selected_by_is_example_chunk(body: str) -> None:
    chunks = _chunks_of(body)
    assert chunks
    carrying = [c for c in chunks if VERIFIED_KIND_LINE in c.text.splitlines()]
    assert carrying, "the chunker lost the Kind line"
    assert all(is_example_chunk(c) for c in carrying)


@pytest.mark.parametrize("body", [BODY_VERIFIED, BODY_AUTHORED], ids=["verified", "authored"])
def test_a_verified_example_chunk_gets_a_vector_point_under_the_examples_scope(body: str) -> None:
    chunks = _chunks_of(body)
    ids = [content_chunk_id(c) for c in chunks]
    payload = build_vector_index(
        chunks, ids, HashingEmbeddingProvider(), "g1", include=is_example_chunk, decimals=DECIMALS
    )
    carrying = {content_chunk_id(c) for c in chunks if VERIFIED_KIND_LINE in c.text.splitlines()}
    assert carrying <= {p["chunk_id"] for p in payload["points"]}


def test_a_chunk_without_an_example_block_is_not_selected() -> None:
    assert not is_example_chunk(_chunk(PLAIN_TEXT))
