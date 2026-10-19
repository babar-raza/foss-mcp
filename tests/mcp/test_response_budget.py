"""G3/TC-515 (REQ-G3-008): no tool response exceeds the size budget, and nothing but prose is cut.

Synthetic payloads of the measured sizes check each rule of ``apply_budget``; three runs against the
REAL baked data, through ``create_server`` and the SDK ``tools/call`` handler, check the worst
measured answers (a 60 KB, a 186 KB and a 77 KB response). Offline and deterministic: the scope
directories are COPIED from infra/demo-manifests into a temporary directory and the embedding
gateway settings are removed from the environment.
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
from foss_mcp.mcp.response_budget import (
    ITEM_TEXT_CAP,
    RAW_TEXT_CAP,
    RELEASE_BODY_CAP,
    TOTAL_CAP_BYTES,
    _item_cap,
    apply_budget,
)
from foss_mcp.mcp.routing import DeploymentConfig
from foss_mcp.mcp.server import create_server

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
GUARD_SECONDS = 120
# The cap is written out here, never read from the module under test: raising the module's constant must fail these tests.
CAP = 24576


def _bytes(value: object) -> int:
    return len(json.dumps(value, default=str).encode("utf-8"))


def _budget(
    payload: object, *, query: str | None = "render page image", family: str = "pdf"
) -> tuple[Any, dict]:
    return apply_budget("lookup", payload, query=query, family=family)


def _symbol_item(index: int, size: int = 3800) -> dict[str, Any]:
    head = f"FQN: Aspose.Pdf.Type{index}\nKind: class_declaration\nMethods:\n"
    body = "".join(f"  - Method{index}_{n}(value: int) -> void\n" for n in range(size // 30))
    return {
        "scope": {"family": "pdf", "platform": "net", "source_kind": "self_extracted"},
        "chunk_id": f"c{index}",
        "text": (head + body)[:size],
        "untrusted_upstream_content": True,
    }


def _doc_text(lines: int, marker_line: int | None = None) -> str:
    rows = [f"| Name{n} | Plain description number {n} of an unrelated facade class. |" for n in range(lines)]
    if marker_line is not None:
        rows[marker_line] = "| PngDevice | Renders a page to a PNG image with the given resolution. |"
    return "FQN: Doc: Key Capabilities\n" + "\n".join(rows)


def test_the_total_cap_is_24576_bytes() -> None:
    assert TOTAL_CAP_BYTES == CAP


def test_a_375_kilobyte_symbol_list_is_cut_to_the_total_cap_with_a_count() -> None:
    items = [_symbol_item(index) for index in range(100)]
    assert _bytes(items) > 375_000
    result, notes = _budget(items)
    assert _bytes(result) <= CAP
    kept = len(result)
    assert 1 <= kept < 100
    assert notes["omitted_items"] == 100 - kept
    assert result[-1]["omitted_items"] == 100 - kept
    assert str(CAP) in result[-1]["response_note"]
    assert all("omitted_items" not in item for item in result[:-1])
    assert [item["chunk_id"] for item in result] == [f"c{index}" for index in range(kept)]
    assert notes["original_bytes"] > 375_000 and notes["returned_bytes"] == _bytes(result)
    assert notes["total_cap_bytes"] == CAP


def test_ten_15_kilobyte_release_bodies_are_cut_to_2048_with_the_release_url() -> None:
    releases = [
        {
            "tag_name": f"v1.{index}.0",
            "body": "\n".join(f"- change {n} in release {index}" for n in range(900)),
            "richness": "detailed",
            "url": f"https://example.invalid/releases/tag/v1.{index}.0",
        }
        for index in range(10)
    ]
    assert all(len(release["body"]) > 15_000 for release in releases)
    result, notes = _budget(releases, query=None)
    assert _bytes(result) <= CAP
    assert [item["tag_name"] for item in result] == [release["tag_name"] for release in releases]
    for item, release in zip(result, releases, strict=True):
        assert len(item["body"]) <= RELEASE_BODY_CAP + 200
        assert item["body"].endswith(f"full notes: {release['url']}]")
        assert "[truncated: showing " in item["body"]
    assert notes["truncated_fields"] == 10


def test_a_release_without_a_url_points_at_the_release_page() -> None:
    result, _notes = _budget([{"tag_name": "v1", "body": "x\n" * 5000, "url": None}], query=None)
    assert result[0]["body"].endswith("full notes at the release page]")


def test_a_60_kilobyte_documentation_chunk_keeps_the_window_with_the_query_terms() -> None:
    text = _doc_text(900, marker_line=700)
    assert len(text) > 60_000
    result, notes = _budget([{"chunk_id": "d", "text": text}], query="render a page to png image")
    excerpt = result[0]["text"]
    assert excerpt.startswith("FQN: Doc: Key Capabilities\n")
    assert "PngDevice" in excerpt
    body, _separator, marker = excerpt.rpartition("\n")
    # G3/TC-563: the cap of a lone documentation chunk is the share of the total budget, not 1,536.
    assert len(body) <= _item_cap(CAP, 1)
    assert len(body) > ITEM_TEXT_CAP
    # The item has no chunk_id/content_type pair, so there is no call to name; the old marker promised
    # that a narrower query returns the passages not shown, which was never true.
    assert marker == f"[truncated: showing {len(body)} of {len(text)} characters]"
    assert notes["truncated_fields"] == 1


def test_a_symbol_chunk_is_cut_at_the_head_and_points_at_get_symbol() -> None:
    item = _symbol_item(7, size=9000)
    result, _notes = _budget([item], query="Type7")
    text = result[0]["text"]
    assert text.startswith(item["text"][:200])
    body, _separator, marker = text.rpartition("\n")
    assert len(body) <= ITEM_TEXT_CAP
    assert item["text"].startswith(body)
    assert "call get_symbol with fqn='Aspose.Pdf.Type7' for the full signature]" in marker


def test_identical_chunks_in_one_response_are_removed_once() -> None:
    first = {"chunk_id": "a", "text": "FQN: Doc: Same\nthe same passage"}
    twin = {"chunk_id": "b", "text": "FQN: Doc: Same\nthe same passage\n"}
    other = {"chunk_id": "c", "text": "FQN: Doc: Other\nanother passage"}
    result, notes = _budget([first, twin, other])
    assert [item["chunk_id"] for item in result] == ["a", "c"]
    assert notes["deduplicated_items"] == 1


def test_snippets_are_never_truncated() -> None:
    snippet = "document.Save(path);\n" * 4000
    payload = [{"fqn": "Example: big", "snippet": snippet, "source": "exact"}]
    result, notes = _budget(payload)
    assert result[0]["snippet"] == snippet
    assert notes["truncated_fields"] == 0
    answer = {"scope": {}, "doc_matches": [], "example": {"snippet": snippet}}
    result_answer, _notes = _budget(answer)
    assert result_answer["example"]["snippet"] == snippet


def test_get_symbol_arrays_and_list_members_names_are_never_dropped() -> None:
    members = [f"Member{n}(value: int) -> void" for n in range(3000)]
    symbol = {
        "fqn": "Aspose.Pdf.Huge",
        "bases": ["Aspose.Pdf.Base"],
        "methods": members,
        "properties": [f"Prop{n}: int" for n in range(50)],
        "members": [f"Item{n}" for n in range(50)],
        "raw_text": "FQN: Aspose.Pdf.Huge\n" + "\n".join(members),
    }
    result, notes = apply_budget("get_symbol", symbol, query=None, family="pdf")
    for key in ("bases", "methods", "properties", "members"):
        assert result[key] == symbol[key]
    assert len(result["raw_text"]) < RAW_TEXT_CAP + 200
    assert "the bases, methods, properties and members arrays are complete]" in result["raw_text"]
    assert result["response_notes"]
    assert notes["truncated_fields"] == 1 and notes["omitted_items"] == 0

    names = [f"Member{n}" for n in range(3000)]
    kept, names_notes = apply_budget("list_members", names, query=None, family="pdf")
    assert kept == names
    assert names_notes["omitted_items"] == 0


def test_an_untouched_payload_is_byte_identical_and_notes_are_zero() -> None:
    payload = [
        {"chunk_id": "a", "text": "FQN: Doc: Small\nshort"},
        {"chunk_id": "b", "text": "FQN: Doc: B\nx"},
    ]
    result, notes = _budget(payload)
    assert json.dumps(result, default=str) == json.dumps(payload, default=str)
    assert notes["truncated_fields"] == notes["deduplicated_items"] == notes["omitted_items"] == 0
    assert notes["original_bytes"] == notes["returned_bytes"] == _bytes(payload)
    miss = {"reason": "no symbol matches 'x'", "suggestions": []}
    miss_result, _ = _budget(miss)
    assert json.dumps(miss_result) == json.dumps(miss)
    assert "response_notes" not in miss_result


def test_apply_budget_does_not_mutate_its_input() -> None:
    items = [_symbol_item(index) for index in range(60)] + [_symbol_item(0)]
    items.append({"chunk_id": "doc", "text": _doc_text(900, 400)})
    answer = {"doc_matches": copy.deepcopy(items), "example": None}
    for payload in (items, answer):
        before = copy.deepcopy(payload)
        _budget(payload)
        assert payload == before


def test_the_excerpt_is_deterministic() -> None:
    items = [{"chunk_id": "d", "text": _doc_text(900, 123)}]
    first = _budget(copy.deepcopy(items), query="png image page")
    second = _budget(copy.deepcopy(items), query="png image page")
    assert json.dumps(first) == json.dumps(second)


def test_task_answer_doc_matches_and_symbols_lists_are_budgeted_like_lists() -> None:
    answer = {
        "scope": {"family": "pdf"},
        "doc_matches": [{"chunk_id": f"d{index}", "text": _doc_text(300 + index, 50)} for index in range(30)],
        "symbols": [_symbol_item(index) for index in range(30)],
        "example": {"fqn": "Example: x", "snippet": "document.Save(path);\n"},
    }
    result, notes = _budget(answer)
    assert _bytes(result) <= CAP
    assert result["example"] == answer["example"]
    assert len(result["doc_matches"]) >= 1 and len(result["symbols"]) >= 1
    assert notes["omitted_items"] > 0
    assert result["response_notes"]
    omitted_on_items = sum(result[key][-1].get("omitted_items", 0) for key in ("doc_matches", "symbols"))
    assert omitted_on_items == notes["omitted_items"]
    granted = _item_cap(CAP, len(answer["doc_matches"]))
    assert all(len(item["text"]) <= granted + 300 for item in result["doc_matches"])


# ---------------------------------------------------------------------------------------------
# the real baked data, through the SDK tools/call handler
# ---------------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_embedding_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_ENDPOINT_ENV, raising=False)
    monkeypatch.delenv(_API_KEY_ENV, raising=False)


def _server(tmp_path: Path, family: str, platform: str, releases: tuple[Release, ...] = ()) -> Any:
    directory = f"{family}__{platform}__self_extracted"
    shutil.copytree(DEMO_MANIFESTS / directory, tmp_path / directory)
    return create_server(
        DeploymentConfig(family=family, platform=platform),
        GenerationManifestStore(tmp_path),
        recent_releases=releases,
    )


def _call(server: Any, name: str, arguments: dict[str, Any]) -> CallToolResult:
    entry = server.get_request_handler("tools/call")
    assert entry is not None

    async def run() -> CallToolResult:
        with anyio.fail_after(GUARD_SECONDS):
            return await entry.handler(None, CallToolRequestParams(name=name, arguments=arguments))

    result = anyio.run(run)
    assert result.is_error is False, result.content
    assert result.structured_content is not None
    return result


def _assert_fits_in_both_channels(result: CallToolResult) -> Any:
    structured = result.structured_content["result"]
    assert _bytes(structured) <= CAP
    assert json.loads(result.content[0].text) == structured
    assert len(result.content[0].text.encode("utf-8")) <= CAP
    assert result.structured_content["response_budget"]["total_cap_bytes"] == CAP
    return structured


def test_pdf_net_lookup_render_a_page_to_png_image_fits_the_cap(tmp_path: Path) -> None:
    server = _server(tmp_path, "pdf", "net")
    result = _call(server, "lookup", {"query": "render a page to png image"})
    structured = _assert_fits_in_both_channels(result)
    assert isinstance(structured, dict) and "reason" not in structured  # not a Miss
    assert structured["doc_matches"] or structured["example"]
    budget = result.structured_content["response_budget"]
    assert budget["original_bytes"] > CAP and budget["returned_bytes"] <= CAP


def test_words_net_lookup_document_top_100_fits_the_cap(tmp_path: Path) -> None:
    server = _server(tmp_path, "words", "net")
    result = _call(server, "lookup", {"query": "Document", "top_k": 100})
    structured = _assert_fits_in_both_channels(result)
    assert isinstance(structured, list) and structured
    assert structured[-1]["omitted_items"] == result.structured_content["response_budget"]["omitted_items"]
    assert result.structured_content["response_budget"]["original_bytes"] > 150_000


def test_pdf_go_list_recent_changes_fits_the_cap(tmp_path: Path) -> None:
    raw = json.loads((DEMO_MANIFESTS / "recent_releases_pdf_go.json").read_text(encoding="utf-8"))
    releases = tuple(
        Release(
            tag_name=entry["tag_name"],
            body=entry["body"],
            richness=entry["richness"],
            published_at=entry.get("published_at"),
            url=entry.get("url"),
        )
        for entry in raw
    )
    server = _server(tmp_path, "pdf", "go", releases)
    result = _call(server, "list_recent_changes", {})
    structured = _assert_fits_in_both_channels(result)
    assert isinstance(structured, list) and structured
    assert structured[0]["tag_name"] == releases[0].tag_name
    for item, release in zip(structured, releases, strict=False):
        assert item["body"] == release.body or item["body"].rstrip().endswith("]")
        if item["body"] != release.body:
            assert "[truncated: showing " in item["body"].rpartition("\n")[2]
