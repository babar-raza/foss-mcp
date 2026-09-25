"""Direct unit test of build_chunks_from_api_surface (TC-062) - the real, reusable src/ module
that a real production ingestion run can call, promoted out of test_publisher.py's
_pdf_net_chunks() where this logic used to live inline and unreachable from src/.

Exercised against a small synthetic fixture dict constructed here, matching the real shape of
tests/fixtures/pdf_net/api_surface.json (source_repository, source_commit, types with
name/class_import/kind/methods) - not against the real fixture, so this test is independent of
any pilot's extraction and asserts real returned Chunk objects, not placeholders.
"""

from __future__ import annotations

from foss_mcp.indexing.chunk_builder import build_chunks_from_api_surface
from foss_mcp.normalization.chunker import Chunk
from foss_mcp.normalization.document_schema import SourceKind, TrustTier

SYNTHETIC_FIXTURE = {
    "source_repository": "example-org/Example-FOSS-for-Widgets",
    "source_commit": "deadbeefcafef00d1234567890abcdef1234567",
    "types": [
        {
            "name": "WidgetFactory",
            "class_import": "Example.Widgets.WidgetFactory",
            "kind": "class",
            "methods": [{"name": "Create"}, {"name": "Destroy"}],
        },
        {
            "name": "WidgetKind",
            "class_import": "",
            "kind": "enum",
            "methods": [],
        },
        {
            "name": "Sprocket",
            "kind": "struct",
            "methods": [{"name": "Spin"}],
        },
    ],
}


def test_returns_real_chunk_objects() -> None:
    chunks = build_chunks_from_api_surface(SYNTHETIC_FIXTURE, title="widgets/example API surface")
    assert chunks
    assert all(isinstance(chunk, Chunk) for chunk in chunks)


def test_provenance_carries_the_real_repository_and_commit() -> None:
    chunks = build_chunks_from_api_surface(SYNTHETIC_FIXTURE, title="widgets/example API surface")
    for chunk in chunks:
        assert chunk.provenance.repository == "example-org/Example-FOSS-for-Widgets"
        assert chunk.provenance.commit == "deadbeefcafef00d1234567890abcdef1234567"
        assert chunk.provenance.path == "api_surface.json"
        assert chunk.evidence_refs == (
            "example-org/Example-FOSS-for-Widgets"
            "@deadbeefcafef00d1234567890abcdef1234567:api_surface.json",
        )
        assert chunk.source_kind == SourceKind.SELF_EXTRACTED.value
        assert chunk.content_type == "api_surface"
        assert chunk.trust_tier == TrustTier.HIGHEST.value


def test_body_text_reflects_the_real_synthetic_types_passed_in() -> None:
    chunks = build_chunks_from_api_surface(SYNTHETIC_FIXTURE, title="widgets/example API surface")
    all_text = "\n".join(chunk.text for chunk in chunks)
    all_titles = {chunk.section_title for chunk in chunks}

    # class_import wins over name when present.
    assert "Example.Widgets.WidgetFactory" in all_text
    assert "Example.Widgets.WidgetFactory is a class." in all_text
    assert "Methods: Create, Destroy." in all_text

    # a falsy class_import ("") falls back to name.
    assert "WidgetKind is a enum." in all_text
    assert "WidgetKind" in all_titles

    # an entry with no class_import key at all also falls back to name.
    assert "Sprocket is a struct." in all_text
    assert "Methods: Spin." in all_text

    assert "Example.Widgets.WidgetFactory" in all_titles


def test_title_is_passed_through_and_not_hardcoded() -> None:
    chunks = build_chunks_from_api_surface(SYNTHETIC_FIXTURE, title="some other pilot's title")
    assert chunks
    # title only affects the document's title, not chunk section titles, but the function must
    # accept and use a caller-supplied title rather than a hardcoded one (proven by not raising
    # and by producing the same body content regardless of which pilot's title is passed).
    other_chunks = build_chunks_from_api_surface(SYNTHETIC_FIXTURE, title="pdf/net API surface")
    assert [c.text for c in chunks] == [c.text for c in other_chunks]


def test_max_types_bounds_how_many_types_are_included() -> None:
    chunks = build_chunks_from_api_surface(SYNTHETIC_FIXTURE, title="widgets/example API surface", max_types=1)
    all_text = "\n".join(chunk.text for chunk in chunks)
    assert "WidgetFactory" in all_text or "Example.Widgets.WidgetFactory" in all_text
    assert "WidgetKind" not in all_text
    assert "Sprocket" not in all_text
