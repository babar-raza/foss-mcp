"""G3/TC-514 (REQ-G3-008): every successful tool response is wrapped additively.

An independent review found three payloads with no scope at all (get_product_reference,
list_recent_changes and the found case of list_members) and third-party README and release text
returned unmarked by every tool but get_product_reference. The envelope adds a sibling ``scope`` to
``structuredContent``, an in-band ``scope`` on a dict result that lacks one, and the
``untrusted_upstream_content`` marker on every result that carries repository text. It never changes
a result's shape: a bare list stays a bare list.

Offline and deterministic: the pilot store is a COPY of the committed pdf/net scope directory and the
embedding gateway settings are removed from the environment.
"""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.types import CallToolRequestParams, CallToolResult

from foss_mcp.extraction.github_release_reader import Release
from foss_mcp.indexing.gateway_embedding_provider import _API_KEY_ENV, _ENDPOINT_ENV
from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.response_envelope import (
    CONTENT_ORIGIN_KEY,
    UNTRUSTED_KEY,
    UPSTREAM_TEXT_TOOLS,
    annotate_result,
    origin_of,
    scope_object,
)
from foss_mcp.mcp.routing import DeploymentConfig, Scope
from foss_mcp.mcp.server import create_server

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_PDF_NET = REPO_ROOT / "infra" / "demo-manifests" / "pdf__net__self_extracted"
SCOPE = Scope(family="pdf", platform="net")
SCOPE_JSON = {"family": "pdf", "platform": "net", "source_kind": "self_extracted"}
GUARD_SECONDS = 60
FQN = "Aspose.Pdf.Document"

RELEASES = (
    Release(tag_name="v2.0.0", body="Breaking: the foo API moved to bar.", richness="detailed"),
    Release(tag_name="v1.0.0", body="Initial release.", richness="none"),
)

VALID_ARGUMENTS: dict[str, dict[str, Any]] = {
    "lookup": {"query": "Document"},
    "search_docs": {"query": "install", "content_type": "getting_started"},
    "search_symbols": {"query": "Document"},
    "get_symbol": {"fqn": FQN},
    "list_members": {"fqn": FQN},
    "find_examples": {"query": "Document"},
    "get_product_reference": {"section": "license"},
    "list_recent_changes": {},
    "report_index_freshness": {},
}
EXPECTED_TYPE: dict[str, type] = {
    "lookup": list,
    "search_docs": list,
    "search_symbols": list,
    "get_symbol": dict,
    "list_members": list,
    "find_examples": list,
    "get_product_reference": dict,
    "list_recent_changes": list,
    "report_index_freshness": dict,
}


@pytest.fixture(autouse=True)
def _no_embedding_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_ENDPOINT_ENV, raising=False)
    monkeypatch.delenv(_API_KEY_ENV, raising=False)


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory) -> Any:
    root = tmp_path_factory.mktemp("envelope_pdf_net")
    shutil.copytree(DEMO_PDF_NET, root / DEMO_PDF_NET.name)
    return create_server(
        DeploymentConfig(family="pdf", platform="net"),
        GenerationManifestStore(root),
        recent_releases=RELEASES,
    )


def _call(server: Any, name: str, arguments: dict[str, Any]) -> CallToolResult:
    entry = server.get_request_handler("tools/call")
    assert entry is not None

    async def run() -> CallToolResult:
        with anyio.fail_after(GUARD_SECONDS):
            return await entry.handler(None, CallToolRequestParams(name=name, arguments=arguments))

    return anyio.run(run)


def _ok(server: Any, name: str, arguments: dict[str, Any] | None = None) -> Any:
    result = _call(server, name, VALID_ARGUMENTS[name] if arguments is None else arguments)
    assert result.is_error is False, result.content
    assert result.structured_content is not None
    return result


# ---------------------------------------------------------------------------------------------
# the sibling scope, on every tool
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("tool", sorted(VALID_ARGUMENTS))
def test_every_tool_response_carries_a_sibling_scope(server: Any, tool: str) -> None:
    result = _ok(server, tool)
    structured = result.structured_content
    assert structured["scope"] == SCOPE_JSON
    assert structured["content_origin"] == origin_of(tool)
    assert type(structured["result"]) is EXPECTED_TYPE[tool]
    # TC-515 adds the sibling "response_budget" notes beside the result.
    assert set(structured) == {"result", "scope", "content_origin", "response_budget"}


def test_dict_results_without_scope_gain_one_in_band(server: Any) -> None:
    result = _ok(server, "get_product_reference").structured_content["result"]
    assert result["scope"] == SCOPE_JSON
    assert result["section"] == "license"


def test_bare_lists_stay_bare_lists(server: Any) -> None:
    members = _ok(server, "list_members").structured_content
    assert type(members["result"]) is list and members["result"]
    assert all(type(name) is str for name in members["result"])
    assert members["scope"] == SCOPE_JSON

    changes = _ok(server, "list_recent_changes").structured_content
    assert type(changes["result"]) is list
    assert [item["tag_name"] for item in changes["result"]] == ["v2.0.0", "v1.0.0"]
    assert all("scope" not in item for item in changes["result"])
    assert changes["scope"] == SCOPE_JSON


# ---------------------------------------------------------------------------------------------
# the untrusted marker
# ---------------------------------------------------------------------------------------------


def _carriers(value: Any) -> list[dict[str, Any]]:
    return [value] if isinstance(value, dict) else [item for item in value if isinstance(item, dict)]


def test_upstream_text_results_are_marked_untrusted(server: Any) -> None:
    calls = {
        "search_symbols": VALID_ARGUMENTS["search_symbols"],
        "search_docs": VALID_ARGUMENTS["search_docs"],
        "lookup": {"query": "how do I add a watermark to a PDF"},
        "find_examples": {"query": "Example: Add a Watermark Annotation"},
        "get_symbol": VALID_ARGUMENTS["get_symbol"],
        "list_recent_changes": {},
    }
    assert set(calls) == UPSTREAM_TEXT_TOOLS
    for tool, arguments in calls.items():
        result = _ok(server, tool, arguments).structured_content["result"]
        carriers = _carriers(result)
        assert carriers, f"{tool} returned nothing to mark"
        assert any(item.get(UNTRUSTED_KEY) is True for item in carriers), tool
        assert origin_of(tool) == "upstream_repository"
        if isinstance(result, dict):
            assert result[CONTENT_ORIGIN_KEY] == "upstream_repository"


def test_every_text_carrying_item_of_a_list_is_marked(server: Any) -> None:
    for tool in ("search_symbols", "list_recent_changes"):
        result = _ok(server, tool).structured_content["result"]
        assert len(result) >= 2
        assert all(item[UNTRUSTED_KEY] is True for item in result)


def test_index_metadata_results_are_not_marked(server: Any) -> None:
    freshness = _ok(server, "report_index_freshness").structured_content["result"]
    assert UNTRUSTED_KEY not in freshness and "text" not in freshness
    assert origin_of("report_index_freshness") == "index_metadata"

    members = _ok(server, "list_members").structured_content["result"]
    assert all(type(name) is str for name in members)
    assert origin_of("list_members") == "index_metadata"

    miss = _ok(server, "search_symbols", {"query": "send a fax via modem"}).structured_content["result"]
    assert isinstance(miss, dict) and miss["reason"]
    assert UNTRUSTED_KEY not in miss


def test_get_product_reference_origin_is_mixed(server: Any) -> None:
    assert origin_of("get_product_reference") == "mixed"
    assert _ok(server, "get_product_reference").structured_content["content_origin"] == "mixed"


# ---------------------------------------------------------------------------------------------
# the text channel
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("tool", sorted(VALID_ARGUMENTS))
def test_text_channel_equals_structured_result(server: Any, tool: str) -> None:
    result = _ok(server, tool)
    text = result.content[0].text
    assert json.loads(text) == result.structured_content["result"]
    assert text == json.dumps(result.structured_content["result"], default=str)


def test_the_text_channel_of_a_bare_list_carries_the_marker_on_each_dict_item(server: Any) -> None:
    items = json.loads(_ok(server, "search_symbols").content[0].text)
    assert type(items) is list and items
    assert all(item[UNTRUSTED_KEY] is True for item in items)


# ---------------------------------------------------------------------------------------------
# the pure functions
# ---------------------------------------------------------------------------------------------


def test_scope_object_is_the_three_scope_fields() -> None:
    assert scope_object(Scope(family="cells", platform="rust", source_kind="furnished")) == {
        "family": "cells",
        "platform": "rust",
        "source_kind": "furnished",
    }


def test_origin_of_is_closed_over_the_nine_tools() -> None:
    assert {origin_of(tool) for tool in VALID_ARGUMENTS} == {"upstream_repository", "mixed", "index_metadata"}
    assert [tool for tool in VALID_ARGUMENTS if origin_of(tool) == "upstream_repository"] == [
        tool for tool in VALID_ARGUMENTS if tool in UPSTREAM_TEXT_TOOLS
    ]


def test_annotate_result_does_not_mutate_its_input() -> None:
    dict_payload = {"text": "readme", "nested": {"text": "x"}}
    list_payload = [{"snippet": "code"}, {"other": 1}, "plain"]
    before = copy.deepcopy((dict_payload, list_payload))
    annotated_dict = annotate_result("lookup", dict_payload, SCOPE)
    annotated_list = annotate_result("find_examples", list_payload, SCOPE)
    assert (dict_payload, list_payload) == before
    assert annotated_dict is not dict_payload and annotated_list is not list_payload
    assert UNTRUSTED_KEY in annotated_dict and UNTRUSTED_KEY not in dict_payload


def test_annotate_result_shapes() -> None:
    assert annotate_result("search_docs", [], SCOPE) == []
    assert annotate_result("list_members", ["A", "B"], SCOPE) == ["A", "B"]
    assert annotate_result("search_docs", ["a string"], SCOPE) == ["a string"]
    # A dict item without a text key is left alone, and a non-dict item is never given a key.
    assert annotate_result("search_docs", [{"reason": "r"}, 3], SCOPE) == [{"reason": "r"}, 3]
    # A marker already present is kept, whatever its value.
    kept = annotate_result("lookup", {"text": "t", UNTRUSTED_KEY: False, "scope": {"x": 1}}, SCOPE)
    assert kept[UNTRUSTED_KEY] is False and kept["scope"] == {"x": 1}
    # New keys go last; existing keys keep their order.
    ordered = annotate_result("get_symbol", {"b": 1, "raw_text": "t", "a": 2}, SCOPE)
    assert list(ordered) == ["b", "raw_text", "a", UNTRUSTED_KEY, "scope", CONTENT_ORIGIN_KEY]
    # A non-upstream dict gains a scope only.
    assert annotate_result("report_index_freshness", {"text": "t"}, SCOPE) == {
        "text": "t",
        "scope": SCOPE_JSON,
    }
    assert annotate_result("report_index_freshness", 5, SCOPE) == 5


# ---------------------------------------------------------------------------------------------
# the unknown-argument message
# ---------------------------------------------------------------------------------------------


def _error_message(result: CallToolResult) -> str:
    assert result.is_error is True
    assert result.structured_content["code"] == "invalid_argument"
    message = result.structured_content["message"]
    assert result.content[0].text == message
    return message


def test_an_unknown_argument_error_names_the_argument_and_lists_the_valid_ones(server: Any) -> None:
    message = _error_message(_call(server, "search_symbols", {"query": "x", "bogus": 1}))
    assert "unexpected argument 'bogus'" in message
    assert "query, top_k" in message
    assert message == "unexpected argument 'bogus'; valid arguments: query, top_k"


def test_every_tool_lists_its_own_valid_arguments(server: Any) -> None:
    expected = {
        "lookup": "query, content_type, top_k",
        "list_recent_changes": "limit",
        "report_index_freshness": "source_kind, current_source_commit",
        "get_symbol": "fqn",
    }
    for tool, names in expected.items():
        arguments = dict(VALID_ARGUMENTS[tool], bogus=1)
        assert _error_message(_call(server, tool, arguments)).endswith(f"valid arguments: {names}")


def test_a_hostile_argument_name_is_sanitised_and_cut(server: Any) -> None:
    hostile = "bad\nname\x00\x1b[31m" + "x" * 5000
    message = _error_message(_call(server, "search_symbols", {"query": "x", hostile: 1}))
    assert len(message) <= 300
    assert "\n" not in message and "\r" not in message
    assert all(ord(ch) >= 32 and ord(ch) != 127 for ch in message)
    assert "xxxxx" not in message
    assert "query, top_k" in message

    short = _error_message(_call(server, "search_symbols", {"query": "x", "a b\n'c\"": 1}))
    assert "unexpected argument 'a?b??c?'" in short
    assert "\n" not in short


def test_a_name_of_exactly_forty_characters_is_named_and_a_longer_one_is_not(server: Any) -> None:
    at_limit = "n" * 40
    assert f"unexpected argument '{at_limit}'" in _error_message(
        _call(server, "search_symbols", {"query": "x", at_limit: 1})
    )
    over = _error_message(_call(server, "search_symbols", {"query": "x", "n" * 41: 1}))
    assert "nnnn" not in over and "unexpected argument" in over and "query, top_k" in over


def test_several_unknown_arguments_are_each_named(server: Any) -> None:
    message = _error_message(_call(server, "search_symbols", {"query": "x", "one": 1, "two": 2}))
    assert "unexpected argument 'one'" in message and "unexpected argument 'two'" in message


def test_other_validation_messages_are_unchanged(server: Any) -> None:
    assert _error_message(_call(server, "search_symbols", {"query": ""})) == (
        "query: Value error, query must not be empty"
    )
    assert _error_message(_call(server, "search_symbols", {"query": "x", "top_k": 0})) == (
        "top_k: Input should be greater than 0"
    )
