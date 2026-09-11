"""HOLDOUT oracle for TC-015 — supervisor-authored, not named by the card.

The defect this card exists to avoid inheriting is specific and was self-admitted
in the reference system's own code: its Qdrant point id was `sha256(chunk_id)`
alone, so identical content across two generations produced identical points and
rollback could not actually restore anything.

A suite can look thorough about that and still miss it. `point_id` might mix the
generation in somewhere without it being *load-bearing*; rollback might be
asserted only through the publisher's own return value rather than by reading
what a consumer would read; and the case that actually bites — republishing
BYTE-IDENTICAL content — is the easiest one to forget precisely because nothing
appears to change.

So this oracle asserts the consumer-visible consequences, reading the stored
payload back out of the store rather than trusting a return value, and it
includes an anti-vacuity check that identical inputs inside ONE generation still
collide (otherwise "ids differ" could be satisfied by a random nonce, which would
break caching and dedup while passing every identity test).
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

WT = pathlib.Path(__file__).resolve().parents[3]
SRC = WT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from foss_mcp.indexing.generation_manifest import (  # noqa: E402
    GenerationKey,
    GenerationManifestStore,
)
from foss_mcp.indexing.publisher import publish_generation, rollback_generation  # noqa: E402
from foss_mcp.indexing.vector_index_writer import point_id  # noqa: E402
from foss_mcp.normalization.chunker import chunk_document  # noqa: E402
from foss_mcp.normalization.document_schema import (  # noqa: E402
    Provenance,
    SourceKind,
    make_document,
)

FIXTURE = WT / "tests" / "fixtures" / "pdf_net" / "api_surface.json"

# Derived from the card's own key type rather than hardcoded. A guessed scope
# string silently addressed a DIFFERENT scope, so leases were acquired somewhere
# the publisher never looked - which holdout-check caught before it could reject
# a correct card.
SCOPE = GenerationKey(
    family="pdf", platform="net", source_kind="self_extracted", version="probe"
).scope


class _FixedProvider:
    """Deterministic and offline. A provider that varied per call would make every
    identity assertion below pass for the wrong reason."""

    dimension = 8

    def embed(self, texts):
        return [[float((len(t) + i) % 7) for i in range(self.dimension)] for t in texts]

    def embed_one(self, text):
        return self.embed([text])[0]


@pytest.fixture(scope="module")
def chunks():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    sections = []
    for entry in fixture["types"][:12]:
        name = entry.get("class_import") or entry.get("name", "")
        sections.append(f"## {name}\n\n{name} is a {entry.get('kind', '')}.")
    doc = make_document(
        source_kind=SourceKind.SELF_EXTRACTED,
        content_type="api_surface",
        provenance=Provenance(
            repository=fixture["source_repository"],
            commit=fixture["source_commit"],
            path="api_surface.json",
        ),
        evidence_refs=(f"{fixture['source_repository']}@{fixture['source_commit']}",),
        title="pdf/net API surface",
        body="\n\n".join(sections),
    )
    return chunk_document(doc)


def _publish(store, chunks, expected_active, version=None):
    lease = store.acquire_lease(SCOPE, "holdout", "pending")
    return publish_generation(
        store,
        family="pdf",
        platform="net",
        source_kind="self_extracted",
        expected_active=expected_active,
        chunks=chunks,
        embedding_provider=_FixedProvider(),
        lease=lease,
        version=version,
    )


def _stored_points(store, generation_id):
    payload = store.read_generation(SCOPE, generation_id).payload
    return {p["chunk_id"]: p["point_id"] for p in payload["vector_index"]["points"]}


def test_point_id_is_generation_qualified_not_content_only(tmp_path, chunks):
    """The exact defect: sha256(chunk_id) alone makes rollback impossible."""
    a = point_id("pdf/net/self_extracted@1", "chunk-x")
    b = point_id("pdf/net/self_extracted@2", "chunk-x")
    assert a != b, (
        "identical content in two generations produced the same point id - this is the "
        "content-only scheme that made rollback self-admittedly broken upstream"
    )
    assert point_id("g1", "chunk-x") == point_id("g1", "chunk-x"), (
        "point_id is not deterministic; a nonce would satisfy the inequality above while "
        "destroying caching, dedup and any stable reference to a point"
    )
    assert point_id("g1", "chunk-x") != point_id("g1", "chunk-y"), "distinct chunks must not collide"


def test_byte_identical_republish_still_yields_a_new_generation(tmp_path, chunks):
    """The forgettable case: nothing about the content changed, so it is tempting
    to reuse the identity — and reusing it is what breaks rollback."""
    store = GenerationManifestStore(tmp_path / "m")
    gen1 = _publish(store, chunks, None)
    gen2 = _publish(store, chunks, gen1)

    assert gen1 != gen2, "republishing identical content must mint a NEW generation identity"
    p1, p2 = _stored_points(store, gen1), _stored_points(store, gen2)
    assert set(p1) == set(p2), "precondition: the same chunks were published both times"
    assert all(p1[c] != p2[c] for c in p1), (
        "identical content across two generations shares point ids; publishing the second would "
        "overwrite the first in a shared collection and rollback would restore nothing"
    )


def test_rollback_is_observable_in_the_store_not_just_the_return_value(tmp_path, chunks):
    """Read what a consumer reads. A publisher can return the right string while
    the active pointer never moved."""
    store = GenerationManifestStore(tmp_path / "m")
    gen1 = _publish(store, chunks, None)
    gen2 = _publish(store, chunks, gen1)
    assert store.read_active(SCOPE) == gen2

    lease = store.acquire_lease(SCOPE, "holdout-rollback", gen1)
    rollback_generation(store, SCOPE, gen2, gen1, lease)

    assert store.read_active(SCOPE) == gen1, "rollback did not move the active pointer"
    assert _stored_points(store, gen1), "the rolled-back generation's index is not readable"
    assert _stored_points(store, gen2), (
        "rolling back destroyed the newer generation; a rollback must be a pointer move, not a "
        "deletion, or rolling forward again is impossible"
    )


def test_a_stale_lease_cannot_publish_after_someone_else_took_over(tmp_path, chunks):
    """Fencing, exercised through the publisher rather than the manifest layer -
    the path a real ingestion job actually takes."""
    store = GenerationManifestStore(tmp_path / "m")
    gen1 = _publish(store, chunks, None)

    stale = store.acquire_lease(SCOPE, "slow-worker", "pending")
    store.acquire_lease(SCOPE, "newer-worker", "pending")  # fences `stale` out

    with pytest.raises(Exception) as exc:
        publish_generation(
            store,
            family="pdf",
            platform="net",
            source_kind="self_extracted",
            expected_active=gen1,
            chunks=chunks,
            embedding_provider=_FixedProvider(),
            lease=stale,
        )
    assert "token" in str(exc.value).lower() or "stale" in str(exc.value).lower(), (
        f"a fenced-out worker was not rejected for staleness: {exc.value!r}"
    )
    assert store.read_active(SCOPE) == gen1, "a fenced publish still moved the active pointer"
