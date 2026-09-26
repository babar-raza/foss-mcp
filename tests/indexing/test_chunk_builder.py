"""Direct unit test of build_chunks_from_api_surface (TC-062, text convention fixed by TC-065,
cross-pilot field-shape gaps fixed by TC-076) - the real, reusable src/ module that a real
production ingestion run can call, promoted out of test_publisher.py's _pdf_net_chunks() where
this logic used to live inline and unreachable from src/.

Exercised against small synthetic fixture dicts constructed here, matching the real shape of
tests/fixtures/pdf_net/api_surface.json (source_repository, source_commit, types with
name/class_import/kind/methods/properties/bases/enum_members) - not against the real fixture, so
this test is independent of any pilot's extraction and asserts real returned Chunk objects, not
placeholders.

TC-076 added the three tests below plus a real-fixture test at the bottom of this file. The
2026-09-26 six-fixture audit directly read tests/fixtures/{pdf_net,slides_python,cells_rust,
pdf_go,pdf_java,pdf_typescript}/api_surface.json and found: (1) pdf_go/cells_rust/pdf_typescript's
free-function entries carry params/return_type directly on the type entry (kind == "function",
methods == []) rather than under a "methods" list; (2) pdf_typescript's "constant" entries carry a
real type/value directly on the entry, previously dropped entirely; (3) property writability is
shaped three different, real ways across the six pilots - pdf_net's real bool "writable",
cells_rust/pdf_go's real string "access_mode" (observed value: "readwrite" only, in both real
fixtures), and pdf_java's real property dicts, most of which carry neither key at all.
"""

from __future__ import annotations

import json
from pathlib import Path

from foss_mcp.indexing.chunk_builder import build_chunks_from_api_surface
from foss_mcp.normalization.chunker import Chunk
from foss_mcp.normalization.document_schema import SourceKind, TrustTier

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"

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


def test_free_function_entry_is_synthesized_as_its_own_single_method() -> None:
    """Fix 1 (TC-076). Mirrors pdf_go's real AddFontFile entry (tests/fixtures/pdf_go/
    api_surface.json): a top-level entry with kind == "function", an empty "methods" list, and
    params/return_type carried directly on the entry itself. Before this fix, such an entry got a
    bare "Kind: function" header with no signature at all - every free function in pdf_go,
    cells_rust, and pdf_typescript was silently signature-less.
    """
    fixture = {
        "source_repository": "example-org/Example-FOSS-for-Widgets",
        "source_commit": "deadbeefcafef00d1234567890abcdef1234567",
        "types": [
            {
                "name": "AddFontFile",
                "kind": "function",
                "doc": "AddFontFile registers a single font file.",
                "methods": [],
                "params": [{"name": "path", "type": "string"}],
                "return_type": "",
                "properties": [],
                "bases": [],
            }
        ],
    }

    chunks = build_chunks_from_api_surface(fixture, title="pdf/go API surface")
    all_text = "\n".join(chunk.text for chunk in chunks)

    assert "FQN: AddFontFile" in all_text
    assert "Kind: function" in all_text
    assert "Methods:" in all_text
    assert "  - AddFontFile(path: string) -> void" in all_text


def test_constant_entry_gets_a_value_line() -> None:
    """Fix 2 (TC-076). Mirrors pdf_typescript's real AES_WRAP_OID entry (tests/fixtures/
    pdf_typescript/api_surface.json): kind == "constant" with real "type"/"value" fields directly
    on the entry. Before this fix, a constant's real type and value were silently dropped
    entirely - only "FQN:"/"Kind:" was ever emitted for it.
    """
    fixture = {
        "source_repository": "example-org/Example-FOSS-for-Widgets",
        "source_commit": "deadbeefcafef00d1234567890abcdef1234567",
        "types": [
            {
                "name": "AES_WRAP_OID",
                "kind": "constant",
                "doc": "",
                "type": "Record<number, string>",
                "value": "{\n  16: OID.aes128Wrap, 24: OID.aes192Wrap, 32: OID.aes256Wrap,\n}",
                "methods": [],
                "properties": [],
                "bases": [],
            }
        ],
    }

    chunks = build_chunks_from_api_surface(fixture, title="pdf/typescript API surface")
    all_text = "\n".join(chunk.text for chunk in chunks)

    assert "FQN: AES_WRAP_OID" in all_text
    assert "Kind: constant" in all_text
    assert "Value: Record<number, string> = {" in all_text
    assert "OID.aes128Wrap" in all_text


def test_constant_value_is_truncated_with_a_visible_marker_when_too_long() -> None:
    """A real constant's value can be a large multi-line object literal. The Value: line must
    cap its displayed length rather than dump the whole thing, and the truncation must be
    visible (a trailing "...") rather than silently pretending the shown value is complete.
    """
    long_value = "x" * 250
    fixture = {
        "source_repository": "example-org/Example-FOSS-for-Widgets",
        "source_commit": "deadbeefcafef00d1234567890abcdef1234567",
        "types": [
            {
                "name": "HUGE_TABLE",
                "kind": "constant",
                "type": "Record<number, string>",
                "value": long_value,
                "methods": [],
                "properties": [],
                "bases": [],
            }
        ],
    }

    chunks = build_chunks_from_api_surface(fixture, title="pdf/typescript API surface")
    all_text = "\n".join(chunk.text for chunk in chunks)

    assert f"Value: Record<number, string> = {long_value[:200]}..." in all_text
    # the full, untruncated value must never appear - truncation has to actually happen.
    assert long_value not in all_text


def test_property_writability_resolved_honestly_across_three_real_shapes() -> None:
    """Fix 3 (TC-076). One property per real shape the six-fixture audit found: pdf_net's real
    bool "writable" (tests/fixtures/pdf_net), cells_rust/pdf_go's real string "access_mode" ==
    "readwrite" (tests/fixtures/cells_rust, tests/fixtures/pdf_go), and pdf_java's real property
    dicts that carry neither key at all (tests/fixtures/pdf_java). The absent case must produce no
    parenthetical whatsoever - never a silently guessed "(read-only)".
    """
    fixture = {
        "source_repository": "example-org/Example-FOSS-for-Widgets",
        "source_commit": "deadbeefcafef00d1234567890abcdef1234567",
        "types": [
            {
                "name": "Mixed",
                "kind": "class",
                "methods": [],
                "bases": [],
                "properties": [
                    {"name": "ToothCount", "type": "int", "writable": True},
                    {"name": "Id", "type": "string", "writable": False},
                    {"name": "StartRow", "type": "i32", "access_mode": "readwrite"},
                    {"name": "artifactType", "type": "ArtifactType"},
                ],
            }
        ],
    }

    chunks = build_chunks_from_api_surface(fixture, title="mixed API surface")
    all_text = "\n".join(chunk.text for chunk in chunks)

    assert "  - ToothCount: int (writable)" in all_text
    assert "  - Id: string (read-only)" in all_text
    assert "  - StartRow: i32 (writable)" in all_text
    # neither "writable" nor "access_mode" is present on this property (the real pdf_java
    # shape) - the writability information is genuinely absent, so no parenthetical at all.
    assert "  - artifactType: ArtifactType\n" in all_text or all_text.endswith(
        "  - artifactType: ArtifactType"
    )
    assert "artifactType: ArtifactType (" not in all_text


def test_real_free_functions_get_real_signatures_across_the_three_affected_pilots() -> None:
    """Runs build_chunks_from_api_surface against the ACTUAL committed pdf_go, cells_rust, and
    pdf_typescript fixtures (real JSON, not a synthetic stand-in) and asserts a real free
    function's real name and parameters appear correctly in the output chunk text. This is the
    test the negative control (reverting `methods = [entry]` to `methods = []`) must break.
    """
    # pdf_go's real AddFontFile: {kind: "function", methods: [], params: [{name: "path", type:
    # "string"}], return_type: ""} - confirmed directly against the committed fixture.
    pdf_go_fixture = json.loads((FIXTURES_DIR / "pdf_go" / "api_surface.json").read_text(encoding="utf-8"))
    pdf_go_chunks = build_chunks_from_api_surface(
        pdf_go_fixture, title="pdf/go API surface", max_types=len(pdf_go_fixture["types"])
    )
    pdf_go_text = "\n".join(chunk.text for chunk in pdf_go_chunks)
    assert "  - AddFontFile(path: string) -> void" in pdf_go_text

    # cells_rust's real standalone "fmt" free-function entry (receiver_type: CellAddress,
    # trait_impl: std::fmt::Display, methods: [], params/return_type on the entry itself) -
    # confirmed directly against the committed fixture.
    cells_rust_fixture = json.loads(
        (FIXTURES_DIR / "cells_rust" / "api_surface.json").read_text(encoding="utf-8")
    )
    cells_rust_chunks = build_chunks_from_api_surface(
        cells_rust_fixture, title="cells/rust API surface", max_types=len(cells_rust_fixture["types"])
    )
    cells_rust_text = "\n".join(chunk.text for chunk in cells_rust_chunks)
    assert "  - fmt(f: &mut Formatter<'_>) -> std::fmt::Result" in cells_rust_text

    # pdf_typescript's real "A" free function: {kind: "function", methods: [], params: [{name:
    # "a", type: "Float32Array"}, ...], return_type: "number"} - confirmed directly against the
    # committed fixture.
    pdf_typescript_fixture = json.loads(
        (FIXTURES_DIR / "pdf_typescript" / "api_surface.json").read_text(encoding="utf-8")
    )
    pdf_typescript_chunks = build_chunks_from_api_surface(
        pdf_typescript_fixture,
        title="pdf/typescript API surface",
        max_types=len(pdf_typescript_fixture["types"]),
    )
    pdf_typescript_text = "\n".join(chunk.text for chunk in pdf_typescript_chunks)
    assert "  - A(a: Float32Array, w: number, h: number, x: number, y: number) -> number" in pdf_typescript_text

    # pdf_typescript's real AES_WRAP_OID constant must also carry its real value now.
    assert "Value: Record<number, string> = {" in pdf_typescript_text
    assert "OID.aes128Wrap" in pdf_typescript_text


def test_real_pdf_net_property_writability_bool_shape_is_unaffected() -> None:
    """pdf/net's own real fixture uses a bool "writable" field and has no free functions at all
    (kind is exclusively class/interface/struct/enum declarations). This must remain completely
    unaffected by the cross-pilot fixes above."""
    pdf_net_fixture = json.loads((FIXTURES_DIR / "pdf_net" / "api_surface.json").read_text(encoding="utf-8"))
    chunks = build_chunks_from_api_surface(
        pdf_net_fixture, title="pdf/net API surface", max_types=len(pdf_net_fixture["types"])
    )
    all_text = "\n".join(chunk.text for chunk in chunks)
    assert "(writable)" in all_text
    assert "(read-only)" in all_text
    # pdf/net never has a bare "Kind: function" entry with a synthesized single-method list -
    # its own real fixture has no kind == "function" entries at all.
    assert not any(entry.get("kind") == "function" for entry in pdf_net_fixture["types"])
