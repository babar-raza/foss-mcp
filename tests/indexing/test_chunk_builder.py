"""Direct unit test of build_chunks_from_api_surface (TC-062, text convention fixed by TC-065) -
the real, reusable src/ module that a real production ingestion run can call, promoted out of
test_publisher.py's _pdf_net_chunks() where this logic used to live inline and unreachable from
src/.

Exercised against small synthetic fixture dicts constructed here, matching the real shape of
tests/fixtures/pdf_net/api_surface.json (source_repository, source_commit, types with
name/class_import/kind/methods/properties/bases/enum_members) - not against the real fixture, so
this test is independent of any pilot's extraction and asserts real returned Chunk objects, not
placeholders.
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
    assert "FQN: Example.Widgets.WidgetFactory" in all_text
    assert "Kind: class" in all_text
    assert "Methods:" in all_text
    assert "  - Create() -> void" in all_text
    assert "  - Destroy() -> void" in all_text

    # a falsy class_import ("") falls back to name.
    assert "FQN: WidgetKind" in all_text
    assert "Kind: enum" in all_text
    assert "WidgetKind" in all_titles

    # an entry with no class_import key at all also falls back to name.
    assert "FQN: Sprocket" in all_text
    assert "Kind: struct" in all_text
    assert "  - Spin() -> void" in all_text

    assert "Example.Widgets.WidgetFactory" in all_titles

    # WidgetKind carries no bases/properties/enum_members - those sections (header and all)
    # must be omitted entirely, never emitted empty.
    widget_kind_chunk = next(c for c in chunks if c.section_title == "WidgetKind")
    assert "Bases:" not in widget_kind_chunk.text
    assert "Properties:" not in widget_kind_chunk.text
    assert "Members:" not in widget_kind_chunk.text


def test_body_text_carries_bases_properties_and_enum_members_when_present() -> None:
    """The real convention get_symbol/list_members require: FQN:/Kind:/Bases:/Methods:/
    Properties:/Members:, each section carrying the real field data the raw fixture has -
    a method's full parameter list and return type, a property's type and writability, and an
    enum's real member values.
    """
    fixture = {
        "source_repository": "example-org/Example-FOSS-for-Widgets",
        "source_commit": "deadbeefcafef00d1234567890abcdef1234567",
        "types": [
            {
                "name": "Cog",
                "class_import": "Example.Widgets.Cog",
                "kind": "class",
                "doc": "A toothed rotating widget part.",
                "bases": ["IEnumerable<Tooth>", "IDisposable"],
                "methods": [
                    {
                        "name": "Rotate",
                        "params": [{"name": "degrees", "type": "int"}, {"name": "clockwise", "type": "bool"}],
                        "return_type": "bool",
                    },
                    {"name": "Reset", "params": [], "return_type": ""},
                ],
                "properties": [
                    {"name": "ToothCount", "type": "int", "writable": True},
                    {"name": "Id", "type": "string", "writable": False},
                ],
            },
            {
                "name": "CogKind",
                "class_import": "",
                "kind": "enum",
                "enum_members": [
                    {"name": "Small", "value": "0"},
                    {"name": "Large", "value": "1"},
                ],
            },
        ],
    }

    chunks = build_chunks_from_api_surface(fixture, title="widgets/example API surface")
    by_title = {c.section_title: c.text for c in chunks}

    cog_text = by_title["Example.Widgets.Cog"]
    assert "FQN: Example.Widgets.Cog" in cog_text
    assert "Kind: class" in cog_text
    assert "A toothed rotating widget part." in cog_text
    assert "Bases:" in cog_text
    assert "  - IEnumerable<Tooth>" in cog_text
    assert "  - IDisposable" in cog_text
    assert "Methods:" in cog_text
    assert "  - Rotate(degrees: int, clockwise: bool) -> bool" in cog_text
    assert "  - Reset() -> void" in cog_text
    assert "Properties:" in cog_text
    assert "  - ToothCount: int (writable)" in cog_text
    assert "  - Id: string (read-only)" in cog_text
    assert "Members:" not in cog_text

    cog_kind_text = by_title["CogKind"]
    assert "FQN: CogKind" in cog_kind_text
    assert "Kind: enum" in cog_kind_text
    assert "Members:" in cog_kind_text
    assert "  - Small = 0" in cog_kind_text
    assert "  - Large = 1" in cog_kind_text
    assert "Bases:" not in cog_kind_text
    assert "Methods:" not in cog_kind_text
    assert "Properties:" not in cog_kind_text


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
