"""What a peer reads in tools/list is true (TC-386, G3/REQ-G3-005).

The advertised input schemas carry the closed sets, bounds and defaults the models enforce; the
descriptions quote only identifiers that exist and say what the tools really do; a miss says it is
a miss and why; pseudo-symbols (examples, documentation) never answer get_symbol or list_members;
a misspelt bare type name still gets a suggestion; the unknown-tool error has a message; and the
pinned library commit reaches report_index_freshness. Offline: indexes are built in memory.
"""

from __future__ import annotations

import asyncio
import json
import re
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anyio
import pytest
from mcp import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from foss_mcp.indexing.lexical_index_writer import build_lexical_index
from foss_mcp.mcp.identity import read_deployment_facts
from foss_mcp.mcp.routing import DeploymentConfig, Scope
from foss_mcp.mcp.server import (
    _INPUT_MODELS,
    TOOL_DESCRIPTIONS,
    _assert_schema_matches_wrapper,
    create_server,
    schema_for,
    tool_description,
    tool_input_schema,
)
from foss_mcp.mcp.tools.find_examples import find_examples
from foss_mcp.mcp.tools.get_product_reference import SECTIONS, ProductReferenceInputs
from foss_mcp.mcp.tools.get_symbol import NotFound, SymbolSignature, get_symbol
from foss_mcp.mcp.tools.list_members import list_members
from foss_mcp.mcp.tools.search_docs import CONTENT_TYPES
from foss_mcp.mcp.tools.search_symbols import Miss, search_symbols, suggest_similar_fqns
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document

FIXTURE = Path(__file__).parents[1] / "fixtures" / "pdf_net" / "api_surface.json"
PDF_NET_SCOPE = Scope(family="pdf", platform="net")
PDF_NET_CONFIG = DeploymentConfig(family="pdf", platform="net")
TOOL_NAMES = sorted(TOOL_DESCRIPTIONS)
_GENERATION_ID = "gen-tc386"
_PINNED_COMMIT = "10a363830fea952c29df20fdb37d35ad3a881433"


# ---------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------


class _InMemoryStore:
    """One active generation of in-memory chunks, standing in for GenerationManifestStore."""

    def __init__(self, bodies: tuple[str, ...]) -> None:
        chunks = []
        for body in bodies:
            doc = make_document(
                source_kind=SourceKind.SELF_EXTRACTED,
                content_type="api_surface",
                provenance=Provenance(repository="Aspose/Aspose.PDF-for-.NET", commit="tc386"),
                evidence_refs=(),
                title="pdf/net API surface",
                body=body,
            )
            chunks.extend(chunk_document(doc))
        chunk_ids = [f"chunk-{index}" for index in range(len(chunks))]
        lexical = build_lexical_index(chunks, chunk_ids, _GENERATION_ID)
        self._manifest = SimpleNamespace(payload={"lexical_index": lexical})

    def read_active(self, scope: str) -> str:
        return _GENERATION_ID

    def read_generation(self, scope: str, generation_id: str) -> SimpleNamespace:
        return self._manifest


class _EmptyStore:
    """A store with no published generation at all."""

    def read_active(self, scope: str) -> None:
        return None


def _type_chunk(fqn: str, *, coverage: str | None = None) -> str:
    lines = [f"## {fqn}", "", f"FQN: {fqn}", "Kind: class_declaration"]
    if coverage is not None:
        lines.append(f"API-Coverage: {coverage}")
    lines += ["Methods:", "  - Save(path: string) -> void", "Properties:", "  - Title: string (writable)"]
    return "\n".join(lines) + "\n"


def _example_chunk(title: str) -> str:
    return (
        f"## Example: {title}\n\nFQN: Example: {title}\nKind: verified_example\nExample:\n"
        "document.Save(path);\n"
    )


def _doc_chunk(title: str) -> str:
    return f"## Doc: {title}\n\nFQN: Doc: {title}\nKind: documentation\nWhat this library does not cover.\n"


def _store(*, coverage: str | None = None) -> _InMemoryStore:
    return _InMemoryStore(
        (
            _type_chunk("Aspose.Pdf.Document", coverage=coverage),
            _type_chunk("Aspose.Pdf.Page", coverage=coverage),
            _example_chunk("Add a Watermark Annotation"),
            _doc_chunk("Scope and Limitations"),
        )
    )


@asynccontextmanager
async def _session(server: Any):
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(lambda: server.run(*server_streams, server.create_initialization_options()))
            async with ClientSession(*client_streams) as session:
                await session.initialize()
                yield session
                task_group.cancel_scope.cancel()


def _advertised(server: Any) -> dict[str, Any]:
    async def run() -> dict[str, Any]:
        async with _session(server) as session:
            return {tool.name: tool for tool in (await session.list_tools()).tools}

    return asyncio.run(run())


def _call(server: Any, name: str, arguments: dict[str, Any]) -> Any:
    async def run() -> Any:
        async with _session(server) as session:
            return await session.call_tool(name, arguments)

    return asyncio.run(run())


def _pdf_net_fqns() -> tuple[set[str], set[str]]:
    types = json.loads(FIXTURE.read_text(encoding="utf-8"))["types"]
    full = {f"{entry['canonical_namespace']}.{entry['name']}" for entry in types}
    return full, {entry["name"] for entry in types}


def _all_descriptions() -> list[tuple[str, str]]:
    """Every description a peer reads: each tool's own and each advertised property's."""
    found: list[tuple[str, str]] = []
    for name in TOOL_NAMES:
        found.append((name, tool_description(name)))
        for field, prop in tool_input_schema(name)["properties"].items():
            found.append((f"{name}.{field}", prop["description"]))
    return found


# ---------------------------------------------------------------------
# advertised schemas
# ---------------------------------------------------------------------


@pytest.mark.parametrize("tool_name", TOOL_NAMES)
def test_every_advertised_schema_is_flat_closed_and_typed(tool_name: str) -> None:
    schema = tool_input_schema(tool_name)
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == set(_INPUT_MODELS[tool_name].model_fields)
    for field, prop in schema["properties"].items():
        assert isinstance(prop.get("type"), str), f"{tool_name}.{field} has no flat type"
        assert prop.get("description"), f"{tool_name}.{field} has no description"
    rendered = json.dumps(schema)
    for forbidden in ("anyOf", "oneOf", "allOf", "$ref", "$defs"):
        assert forbidden not in rendered


@pytest.mark.parametrize("tool_name", TOOL_NAMES)
def test_required_is_exactly_the_models_required_fields(tool_name: str) -> None:
    model = _INPUT_MODELS[tool_name]
    expected = [name for name, field in model.model_fields.items() if field.is_required()]
    assert tool_input_schema(tool_name).get("required", []) == expected


def test_section_content_type_and_source_kind_advertise_the_enforced_closed_sets() -> None:
    assert tool_input_schema("get_product_reference")["properties"]["section"]["enum"] == list(SECTIONS)
    for tool_name in ("lookup", "search_docs"):
        assert tool_input_schema(tool_name)["properties"]["content_type"]["enum"] == list(CONTENT_TYPES)
    source_kinds = [kind.value for kind in SourceKind]
    assert tool_input_schema("report_index_freshness")["properties"]["source_kind"]["enum"] == source_kinds


def test_top_k_and_limit_advertise_bounds_and_the_models_default() -> None:
    for tool_name, field in (
        ("lookup", "top_k"),
        ("search_docs", "top_k"),
        ("search_symbols", "top_k"),
        ("find_examples", "top_k"),
        ("list_recent_changes", "limit"),
    ):
        prop = tool_input_schema(tool_name)["properties"][field]
        assert prop["type"] == "integer"
        assert prop["minimum"] == 1
        assert prop["maximum"] == 100
        assert prop["default"] == _INPUT_MODELS[tool_name].model_fields[field].default


def test_string_fields_advertise_their_length_and_pattern_limits() -> None:
    assert tool_input_schema("get_symbol")["properties"]["fqn"]["maxLength"] == 512
    assert tool_input_schema("search_symbols")["properties"]["query"]["maxLength"] == 1000
    commit = tool_input_schema("report_index_freshness")["properties"]["current_source_commit"]
    assert commit["maxLength"] == 128
    assert re.fullmatch(commit["pattern"], _PINNED_COMMIT)
    assert not re.fullmatch(commit["pattern"], "not a commit")


def test_every_advertised_default_validates_against_its_own_model() -> None:
    for tool_name in TOOL_NAMES:
        model = _INPUT_MODELS[tool_name]
        schema = tool_input_schema(tool_name)
        base = {
            "query": "x",
            "fqn": "x",
            "content_type": CONTENT_TYPES[0],
            "section": SECTIONS[0],
        }
        for field, prop in schema["properties"].items():
            if "default" not in prop:
                continue
            arguments = {name: base[name] for name in schema.get("required", [])}
            arguments[field] = prop["default"]
            assert getattr(model.model_validate(arguments), field) == prop["default"]


def test_an_optional_field_is_absent_from_required_and_never_null_typed() -> None:
    schema = tool_input_schema("report_index_freshness")
    assert "required" not in schema
    assert schema["properties"]["current_source_commit"]["type"] == "string"
    assert "default" not in schema["properties"]["current_source_commit"]
    lookup = tool_input_schema("lookup")
    assert lookup["required"] == ["query"]
    assert "content_type" in lookup["properties"]


def test_find_examples_schema_has_no_fqn_parameter() -> None:
    assert set(tool_input_schema("find_examples")["properties"]) == {"query", "top_k"}


def test_the_query_description_differs_per_tool_and_names_only_its_own_tool() -> None:
    descriptions = {
        tool_name: tool_input_schema(tool_name)["properties"]["query"]["description"]
        for tool_name in ("lookup", "search_docs", "search_symbols", "find_examples")
    }
    assert len(set(descriptions.values())) == len(descriptions)
    for tool_name, text in descriptions.items():
        for other in TOOL_NAMES:
            if other != tool_name:
                assert other not in text, f"{tool_name}'s query description talks about {other}"


def test_tools_list_advertises_the_built_schemas_and_read_only_annotations() -> None:
    store = _store()
    advertised = _advertised(create_server(PDF_NET_CONFIG, store))
    assert set(advertised) == set(TOOL_NAMES)
    # The field descriptions are rendered from the deployment's own generation (G3/TC-514).
    facts = read_deployment_facts(store, PDF_NET_SCOPE)
    for name, tool in advertised.items():
        assert tool.input_schema == tool_input_schema(name, facts)
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False
        assert tool.annotations.idempotent_hint is True
        assert tool.annotations.open_world_hint is False


# ---------------------------------------------------------------------
# descriptions
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "banned", ["Document.save", "PdfFileSignature", "AFRelationship", "pip install aspose-pdf"]
)
def test_no_description_cites_an_identifier_or_package_that_is_not_true(banned: str) -> None:
    for where, text in _all_descriptions():
        assert banned not in text, f"{where} still mentions {banned}"


def test_every_quoted_identifier_in_a_description_is_a_pdf_net_type() -> None:
    full, short = _pdf_net_fqns()
    quoted = re.compile(r"'([A-Za-z_][\w.:]*)'")
    checked = 0
    for where, text in _all_descriptions():
        for token in quoted.findall(text):
            identifier_shaped = "." in token or token[0].isupper()
            if not identifier_shaped:
                continue
            checked += 1
            assert token in full or token in short, f"{where} quotes {token!r}, not a pdf/net type"
    assert checked >= 3


def test_get_symbol_and_list_members_say_only_types_have_an_fqn() -> None:
    for tool_name in ("get_symbol", "list_members"):
        text = tool_description(tool_name)
        assert "no FQN of its own" in text or "has none of its own" in text
    assert "search_symbols" in tool_description("get_symbol")


def test_find_examples_description_matches_its_two_parameters() -> None:
    text = tool_description("find_examples")
    assert "only query and top_k" in text
    assert "fqn" not in text


def test_the_two_caveats_are_each_stated_exactly_once() -> None:
    partial = [name for name in TOOL_NAMES if "hold only part of a library" in tool_description(name)]
    imports = [name for name in TOOL_NAMES if "is not an import path" in tool_description(name)]
    assert len(partial) == 1 and len(imports) == 1
    assert "report_index_freshness" in tool_description(partial[0])
    assert "get_product_reference" in tool_description(imports[0])


def test_cross_references_stay_and_the_nine_descriptions_stay_distinct() -> None:
    assert "search_symbols" in tool_description("lookup")
    assert "get_symbol" in tool_description("search_symbols")
    assert "search_symbols" in tool_description("get_symbol")
    assert len({tool_description(name) for name in TOOL_NAMES}) == len(TOOL_NAMES) == 9


# ---------------------------------------------------------------------
# misses
# ---------------------------------------------------------------------


@pytest.mark.parametrize("tool", [get_symbol, list_members], ids=["get_symbol", "list_members"])
@pytest.mark.parametrize("fqn", ["Example: Add a Watermark Annotation", "Doc: Scope and Limitations"])
def test_pseudo_symbols_are_not_found_by_exact_matching(tool: Any, fqn: str) -> None:
    result = tool(_store(), PDF_NET_SCOPE, fqn)
    assert isinstance(result, NotFound)
    assert result.found is False
    assert result.reason


def test_a_real_type_is_still_answered_unchanged() -> None:
    signature = get_symbol(_store(), PDF_NET_SCOPE, "Aspose.Pdf.Document")
    assert isinstance(signature, SymbolSignature)
    assert signature.found is True
    assert signature.fqn == "Aspose.Pdf.Document"
    assert signature.kind == "class_declaration"
    assert signature.methods == ("Save(path: string) -> void",)
    assert list_members(_store(), PDF_NET_SCOPE, "Aspose.Pdf.Document") == (
        "Save(path: string) -> void",
        "Title: string (writable)",
    )


def test_suggestions_never_offer_a_pseudo_symbol() -> None:
    result = get_symbol(_store(), PDF_NET_SCOPE, "Example: Add a Watermark Annotatio")
    assert isinstance(result, NotFound)
    assert all(not s.startswith(("Example: ", "Doc: ")) for s in result.suggestions)


def test_a_not_found_carries_found_false_and_a_reason() -> None:
    result = get_symbol(_store(), PDF_NET_SCOPE, "Aspose.Pdf.Nothing")
    assert isinstance(result, NotFound)
    assert result.found is False
    assert result.reason == "no symbol with this exact FQN is in this index"
    assert result.coverage_note is None


def test_a_not_found_without_any_generation_says_so() -> None:
    result = get_symbol(_EmptyStore(), PDF_NET_SCOPE, "Aspose.Pdf.Document")  # type: ignore[arg-type]
    assert isinstance(result, NotFound)
    assert result.reason == "no published generation for this scope"


def test_a_partial_api_index_adds_a_coverage_note_to_a_miss() -> None:
    result = get_symbol(_store(coverage="300 of 971 types"), PDF_NET_SCOPE, "Aspose.Pdf.Missing")
    assert isinstance(result, NotFound)
    assert result.coverage_note == (
        "the API index holds 300 of 971 types; a type missing from it may still exist upstream"
    )
    members = list_members(_store(coverage="300 of 971 types"), PDF_NET_SCOPE, "Aspose.Pdf.Missing")
    assert isinstance(members, NotFound) and members.coverage_note == result.coverage_note


def test_a_complete_api_index_adds_no_coverage_note() -> None:
    result = get_symbol(_store(coverage="971 of 971 types"), PDF_NET_SCOPE, "Aspose.Pdf.Missing")
    assert isinstance(result, NotFound) and result.coverage_note is None


def test_a_misspelt_bare_type_name_gets_a_suggestion_of_the_namespaced_type() -> None:
    store = _InMemoryStore((_type_chunk("Aspose.Cells.Workbook"), _type_chunk("Aspose.Pdf.Document")))
    workbook = get_symbol(store, PDF_NET_SCOPE, "Workbok")
    document = get_symbol(store, PDF_NET_SCOPE, "Documnet")
    assert isinstance(workbook, NotFound) and "Aspose.Cells.Workbook" in workbook.suggestions
    assert isinstance(document, NotFound) and "Aspose.Pdf.Document" in document.suggestions
    miss = search_symbols(store, PDF_NET_SCOPE, "Workbok")
    assert isinstance(miss, Miss) and "Aspose.Cells.Workbook" in miss.suggestions


def test_the_final_segment_fallback_is_deterministic_deduplicated_and_capped() -> None:
    known = [
        "Aspose.Pdf.Drawing.Color",
        "Aspose.Pdf.Color",
        "Aspose.Pdf.Color",
        "Aspose.Pdf.Colour",
        "Aspose.Pdf.Rectangle",
    ]
    first = suggest_similar_fqns(known, "Colr")
    assert first == suggest_similar_fqns(list(reversed(known)), "Colr")
    assert len(first) == len(set(first)) <= 3
    assert "Aspose.Pdf.Color" in first and "Aspose.Pdf.Rectangle" not in first
    assert suggest_similar_fqns(known, "Colr", limit=1) == (first[0],)


def test_a_non_empty_full_fqn_suggestion_result_is_unchanged() -> None:
    known = ["Aspose.Pdf.Document", "Aspose.Pdf.Documents", "Aspose.Cells.Workbook"]
    assert suggest_similar_fqns(known, "Aspose.Pdf.Documnt") == (
        "Aspose.Pdf.Document",
        "Aspose.Pdf.Documents",
    )


def test_an_unrelated_name_still_gets_no_suggestion() -> None:
    assert suggest_similar_fqns(["Aspose.Cells.Workbook", "Aspose.Pdf.Document"], "Zzzzqq") == ()


def test_find_examples_exact_branch_is_unaffected_by_the_symbol_filter() -> None:
    result = find_examples(_store(), PDF_NET_SCOPE, "Example: Add a Watermark Annotation")
    assert isinstance(result, list)
    assert result[0].source == "exact"
    assert "document.Save(path);" in result[0].snippet


# ---------------------------------------------------------------------
# the served server
# ---------------------------------------------------------------------


def test_the_unknown_tool_error_has_a_message_and_does_not_echo_a_long_name() -> None:
    server = create_server(PDF_NET_CONFIG, _store())
    result = _call(server, "x" * 500, {})
    assert result.is_error is True
    assert result.structured_content["code"] == "not_found"
    assert result.structured_content["message"] == "unknown tool"
    assert "x" * 65 not in result.content[0].text


def test_pseudo_symbols_are_not_found_through_the_server_either() -> None:
    server = create_server(PDF_NET_CONFIG, _store())
    for name in ("get_symbol", "list_members"):
        result = _call(server, name, {"fqn": "Doc: Scope and Limitations"})
        assert result.is_error is False
        payload = result.structured_content["result"]
        assert payload["found"] is False and payload["reason"]


def test_report_index_freshness_reports_the_pinned_commit_of_the_sidecar() -> None:
    inputs = ProductReferenceInputs(source_ref=_PINNED_COMMIT)
    server = create_server(PDF_NET_CONFIG, _store(), product_reference_inputs=inputs)
    result = _call(server, "report_index_freshness", {})
    assert result.is_error is False
    assert result.structured_content["result"]["pinned_library_commit"] == _PINNED_COMMIT


def test_report_index_freshness_has_no_pinned_commit_without_a_sidecar() -> None:
    server = create_server(PDF_NET_CONFIG, _store())
    result = _call(server, "report_index_freshness", {})
    assert result.is_error is False
    assert result.structured_content["result"]["pinned_library_commit"] is None


def test_server_construction_rejects_a_schema_that_disagrees_with_its_wrapper() -> None:
    def wrapper(*, query: str, top_k: int = 10) -> None:
        pass

    advertised = tool_input_schema("search_symbols")
    _assert_schema_matches_wrapper("search_symbols", advertised, schema_for(wrapper))
    drifted = {**advertised, "required": ["query", "top_k"]}
    with pytest.raises(RuntimeError, match="search_symbols"):
        _assert_schema_matches_wrapper("search_symbols", drifted, schema_for(wrapper))
