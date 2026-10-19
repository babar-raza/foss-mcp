"""G3/TC-563 (REQ-G3-009, REQ-G3-008; Round-3 row RD3-110): documentation excerpts are as long as the
response budget allows, are never cut inside a code fence, and the rest of a cut chunk can be fetched.

Two layers, both offline and deterministic:

- synthetic payloads of the measured shapes check the budget rules of ``apply_budget``;
- the REAL baked pdf/python generation, served through ``create_server`` and the SDK ``tools/call``
  handler, checks the user-visible behaviour. INDEPENDENT ORACLE: the chunk text every expectation is
  compared with is read straight from the generation JSON under infra/demo-manifests, never from a tool
  response and never from the extractor.

The scope directories are COPIED into a temporary directory (a test that needs a synthetic 60 KB chunk
injects it into the copy) and the embedding gateway settings are removed from the environment.
"""

from __future__ import annotations

import glob
import json
import re
import shutil
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.types import CallToolRequestParams, CallToolResult

from foss_mcp.indexing.gateway_embedding_provider import _API_KEY_ENV, _ENDPOINT_ENV
from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp import response_budget
from foss_mcp.mcp.response_budget import ITEM_TEXT_CAP, TOTAL_CAP_BYTES, apply_budget
from foss_mcp.mcp.routing import DeploymentConfig
from foss_mcp.mcp.server import TOOL_DESCRIPTIONS, create_server, tool_input_schema
from foss_mcp.mcp.tools.relevance import DOCUMENTATION, chunk_kind
from foss_mcp.mcp.tools.search_docs import classify_content_type

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
PDF_PYTHON = "pdf__python__self_extracted"
GUARD_SECONDS = 120
# Written out here, never read from the module under test.
CAP = 24576

# The 7,604-character developer-guide chunk the Round-3 review measured as returned in 1,525 characters.
REVIEWED_CHUNK_ID = "94921787677f80fa"

_MARKER_CALL = re.compile(
    r"call search_docs with query=<[^>]+>, content_type='(?P<content_type>[a-z_]+)', "
    r"chunk_id='(?P<chunk_id>[0-9a-f]+)' and offset=(?P<offset>\d+)"
)


def _bytes(value: object) -> int:
    return len(json.dumps(value, default=str).encode("utf-8"))


def _budget(payload: object, *, query: str | None = None, family: str = "pdf") -> tuple[Any, dict]:
    return apply_budget("search_docs", payload, query=query, family=family)


# ---------------------------------------------------------------------------------------------
# the independent oracle: the baked generation JSON
# ---------------------------------------------------------------------------------------------


def _oracle_documents() -> dict[str, dict]:
    path = glob.glob(str(DEMO_MANIFESTS / PDF_PYTHON / "generation__*.json"))[0]
    generation = json.loads(Path(path).read_text(encoding="utf-8"))
    return generation["payload"]["lexical_index"]["documents"]


def _oracle_by_chunk_id() -> dict[str, str]:
    return {doc["chunk_id"]: doc["text"] for doc in _oracle_documents().values()}


def _oracle_doc_chunks() -> dict[str, str]:
    return {
        chunk_id: text
        for chunk_id, text in _oracle_by_chunk_id().items()
        if chunk_kind(text) == DOCUMENTATION
    }


# ---------------------------------------------------------------------------------------------
# synthetic documentation chunks
# ---------------------------------------------------------------------------------------------


def _prose(count: int, tag: str = "p") -> list[str]:
    return [
        f"Line {tag}{n:03d} describes one detail of the facade class in some plain words."
        for n in range(count)
    ]


def _fence(lines: int = 10, tag: str = "f") -> list[str]:
    return ["```python", *[f"document.Step{n}('{tag}')  # fenced code line {n}" for n in range(lines)], "```"]


def _doc(title: str, lines: list[str]) -> str:
    return f"FQN: Doc: {title}\n" + "\n".join(lines)


def _item(text: str, index: int = 0, content_type: str = "developer_guide") -> dict[str, Any]:
    return {
        "scope": {"family": "pdf", "platform": "python", "source_kind": "self_extracted"},
        "doc_id": f"d{index}",
        "chunk_id": f"{index:016x}",
        "content_type": content_type,
        "text": text,
    }


def _fence_lines(excerpt: str) -> int:
    return sum(1 for line in excerpt.split("\n") if line.lstrip().startswith("```"))


def _split_marker(shown_text: str) -> tuple[str, str]:
    body, _separator, marker = shown_text.rpartition("\n")
    assert marker.startswith("[truncated: showing "), marker
    return body, marker


# ---------------------------------------------------------------------------------------------
# the mandated names
# ---------------------------------------------------------------------------------------------


def test_item_cap_is_the_floor_or_the_share_of_the_remaining_budget() -> None:
    assert ITEM_TEXT_CAP == 1536
    assert response_budget._item_cap(CAP, 1) == CAP
    assert response_budget._item_cap(CAP, 10) == CAP // 10
    assert response_budget._item_cap(CAP, 100) == ITEM_TEXT_CAP  # the share is below the floor
    assert response_budget._item_cap(0, 3) == ITEM_TEXT_CAP
    assert response_budget._item_cap(CAP, 0) == CAP  # no division by zero


def test_the_adaptive_flag_is_a_real_switch_and_the_literal_line_exists() -> None:
    source = (REPO_ROOT / "src" / "foss_mcp" / "mcp" / "response_budget.py").read_text(encoding="utf-8")
    assert source.count("\nADAPTIVE_ITEM_CAP = True\n") == 1


def test_with_the_flag_off_the_fixed_cap_comes_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(response_budget, "ADAPTIVE_ITEM_CAP", False)
    assert response_budget._item_cap(CAP, 1) == ITEM_TEXT_CAP
    text = _doc("Big", _prose(80))
    result, notes = _budget([_item(text)])
    body, _marker = _split_marker(result[0]["text"])
    assert len(body) <= ITEM_TEXT_CAP
    assert notes["truncated_fields"] == 1


# ---------------------------------------------------------------------------------------------
# the adaptive per-item cap
# ---------------------------------------------------------------------------------------------


def test_a_5_kilobyte_chunk_in_an_otherwise_small_response_is_returned_in_full() -> None:
    text = _doc("Key Capabilities", _prose(70))
    assert 5000 < len(text) < 6000
    payload = [_item(text)]
    result, notes = _budget(payload, query="detail facade class")
    assert result[0]["text"] == text
    assert notes["truncated_fields"] == 0 and notes["omitted_items"] == 0
    assert _bytes(result) <= CAP


def test_ten_5_kilobyte_chunks_each_get_more_than_the_legacy_cap_and_all_are_kept() -> None:
    payload = [_item(_doc(f"Section {n}", _prose(70, tag=f"s{n}")), index=n) for n in range(10)]
    assert sum(len(item["text"]) for item in payload) > 50_000
    result, notes = _budget(payload)
    assert _bytes(result) <= CAP
    assert [item["chunk_id"] for item in result] == [item["chunk_id"] for item in payload]
    assert notes["omitted_items"] == 0
    lengths = [len(_split_marker(item["text"])[0]) for item in result]
    # a fixed 1,536-character cap would leave ~9 KB of the 24 KB budget unused
    assert min(lengths) > ITEM_TEXT_CAP + 400
    assert _bytes(result) > 20_000  # the budget is actually used


def test_the_total_cap_still_holds_for_a_hundred_large_chunks() -> None:
    payload = [_item(_doc(f"Section {n}", _prose(90, tag=f"s{n}")), index=n) for n in range(100)]
    result, notes = _budget(payload)
    assert _bytes(result) <= CAP
    assert 1 <= len(result) < 100
    assert notes["omitted_items"] == 100 - len(result)
    assert notes["total_cap_bytes"] == CAP


def test_small_chunks_beside_big_ones_leave_their_unused_share_to_the_big_ones() -> None:
    small = [_item(_doc(f"Small {n}", _prose(8, tag=f"m{n}")), index=n) for n in range(4)]
    big = [_item(_doc("Big", _prose(500)), index=9)]
    result, _notes = _budget([*small, *big])
    assert [item["text"] for item in result[:4]] == [item["text"] for item in small]
    shown = len(_split_marker(result[4]["text"])[0])
    assert shown > 10_000
    assert _bytes(result) <= CAP


def test_a_part_returned_by_the_fetch_call_is_never_excerpted_again() -> None:
    text = "\n".join(_prose(240))  # about 19 KB, a continuation part
    part = {
        **_item(text),
        "offset": 1525,
        "total_chars": 40_000,
        "next_offset": 20_525,
        "part_note": "characters 1525-20525 of 40000",
    }
    result, notes = _budget([part])
    assert result[0]["text"] == text
    assert notes["truncated_fields"] == 0


# ---------------------------------------------------------------------------------------------
# where a cut falls
# ---------------------------------------------------------------------------------------------


def test_a_cut_falls_on_a_line_boundary_and_the_marker_names_the_exact_call() -> None:
    lines = _prose(500)
    text = _doc("Long Reference", lines)
    item = _item(text, index=7)
    result, notes = _budget([item])
    assert notes["truncated_fields"] == 1
    body, marker = _split_marker(result[0]["text"])
    title, _newline, window = body.partition("\n")
    assert title == "FQN: Doc: Long Reference"
    start = text.index(window)
    assert text[start - 1] == "\n"  # starts on a line
    end = start + len(window)
    assert text[end] == "\n"  # ends on a line
    call = _MARKER_CALL.search(marker)
    assert call is not None, marker
    assert call.group("chunk_id") == item["chunk_id"]
    assert call.group("content_type") == "developer_guide"
    offset = int(call.group("offset"))
    assert offset == end + 1  # the first character after the shown passage
    assert text[offset:].startswith(lines[len(window.split("\n"))])
    assert f"showing {len(body)} of {len(text)} characters" in marker
    assert "\n" not in marker


@pytest.mark.parametrize("pad", [3, 9, 14, 20, 26, 31])
def test_a_cut_never_falls_inside_a_code_fence(pad: int) -> None:
    # Twelve chunks share the budget, so each is cut at roughly 1.9 KB; the fenced block starts at a
    # different place in every chunk and straddles the cut in several of them.
    chunks = []
    for index in range(12):
        lines = _prose(pad + index * 2, tag="a") + _fence(11, tag=f"c{index}") + _prose(120, tag="b")
        chunks.append(_item(_doc(f"Section {index}", lines), index=index))
    result, _notes = _budget(chunks)
    assert _bytes(result) <= CAP
    cut = 0
    for original, shown in zip(chunks, result, strict=True):
        if shown["text"] == original["text"]:
            continue
        cut += 1
        body, _marker = _split_marker(shown["text"])
        assert _fence_lines(body) % 2 == 0, body
    assert cut >= 6


def test_a_code_fence_larger_than_the_budget_is_left_out_rather_than_cut() -> None:
    lines = _prose(10) + _fence(2000, tag="huge") + _prose(10)
    text = _doc("Huge Fence", lines)
    result, _notes = _budget([_item(text)])
    body, marker = _split_marker(result[0]["text"])
    assert _fence_lines(body) == 0
    assert "document.Step" not in body
    assert _MARKER_CALL.search(marker) is not None
    assert _bytes(result) <= CAP


def test_a_fence_in_the_part_that_follows_the_query_window_is_kept_whole() -> None:
    lines = _prose(100, tag="a") + _fence(8, tag="needle") + _prose(100, tag="b")
    text = _doc("Windowed", lines)
    chunks = [_item(text, index=0)] + [
        _item(_doc(f"Other {n}", _prose(90, tag=f"o{n}")), index=n + 1) for n in range(9)
    ]
    result, _notes = _budget(chunks, query="fenced code line needle Step3")
    body, _marker = _split_marker(result[0]["text"])
    assert _fence_lines(body) == 2
    assert "document.Step3('needle')" in body


# ---------------------------------------------------------------------------------------------
# the real baked pdf/python data, through the SDK tools/call handler
# ---------------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_embedding_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_ENDPOINT_ENV, raising=False)
    monkeypatch.delenv(_API_KEY_ENV, raising=False)


def _server(tmp_path: Path, extra_documents: dict[str, str] | None = None) -> Any:
    shutil.copytree(DEMO_MANIFESTS / PDF_PYTHON, tmp_path / PDF_PYTHON)
    if extra_documents:
        generation_file = glob.glob(str(tmp_path / PDF_PYTHON / "generation__*.json"))[0]
        generation = json.loads(Path(generation_file).read_text(encoding="utf-8"))
        documents = generation["payload"]["lexical_index"]["documents"]
        for chunk_id, text in extra_documents.items():
            documents[f"injected::{chunk_id}"] = {"chunk_id": chunk_id, "text": text, "tokens": ["injected"]}
        Path(generation_file).write_text(json.dumps(generation), encoding="utf-8")
    return create_server(DeploymentConfig(family="pdf", platform="python"), GenerationManifestStore(tmp_path))


def _call_raw(server: Any, name: str, arguments: dict[str, Any]) -> CallToolResult:
    entry = server.get_request_handler("tools/call")
    assert entry is not None

    async def run() -> CallToolResult:
        with anyio.fail_after(GUARD_SECONDS):
            return await entry.handler(None, CallToolRequestParams(name=name, arguments=arguments))

    return anyio.run(run)


def _call(server: Any, name: str, arguments: dict[str, Any]) -> Any:
    result = _call_raw(server, name, arguments)
    assert result.is_error is False, result.content
    assert result.structured_content is not None
    structured = result.structured_content["result"]
    assert _bytes(structured) <= CAP
    assert len(result.content[0].text.encode("utf-8")) <= CAP
    assert json.loads(result.content[0].text) == structured
    return structured


def _fetch(server: Any, chunk_id: str, offset: int, content_type: str = "developer_guide") -> Any:
    return _call(
        server,
        "search_docs",
        {"query": "the same query", "content_type": content_type, "chunk_id": chunk_id, "offset": offset},
    )


def test_a_large_chunk_that_is_alone_is_returned_in_full_and_the_reviewed_chunk_is_fetchable(
    tmp_path: Path,
) -> None:
    oracle = _oracle_by_chunk_id()[REVIEWED_CHUNK_ID]
    assert len(oracle) == 7604
    server = _server(tmp_path)
    hits = _call(server, "search_docs", {"query": "watermark", "content_type": "developer_guide", "top_k": 1})
    # the single hit of this query is another large chunk; it is returned complete as well
    assert len(hits) == 1
    assert hits[0]["text"] == _oracle_by_chunk_id()[hits[0]["chunk_id"]]
    assert len(hits[0]["text"]) > ITEM_TEXT_CAP
    # and the reviewed chunk, fetched by id, is exactly the oracle
    part = _fetch(server, REVIEWED_CHUNK_ID, 0)
    assert len(part) == 1
    assert part[0]["text"] == oracle
    assert part[0]["next_offset"] is None
    assert part[0]["total_chars"] == 7604 and part[0]["offset"] == 0
    assert part[0]["chunk_id"] == REVIEWED_CHUNK_ID


def test_every_cut_hit_of_a_real_query_is_recoverable_and_fewer_are_cut_than_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    oracle = _oracle_by_chunk_id()
    server = _server(tmp_path)
    arguments = {"query": "convert pdf", "content_type": "developer_guide"}

    def cut_hits(structured: list[dict]) -> list[dict]:
        return [hit for hit in structured if hit["text"] != oracle[hit["chunk_id"]]]

    now = _call(server, "search_docs", arguments)
    with monkeypatch.context() as legacy:
        legacy.setattr(response_budget, "ADAPTIVE_ITEM_CAP", False)
        before = _call(server, "search_docs", arguments)
    assert len(cut_hits(now)) < len(cut_hits(before))
    assert len(cut_hits(before)) >= 3  # the four large chunks were all cut to ~1.5 KB
    for hit in cut_hits(now):
        body, marker = _split_marker(hit["text"])
        call = _MARKER_CALL.search(marker)
        assert call is not None, marker
        assert call.group("chunk_id") == hit["chunk_id"]
        assert call.group("content_type") == "developer_guide"
        offset = int(call.group("offset"))
        original = oracle[hit["chunk_id"]]
        part = _fetch(server, hit["chunk_id"], offset)[0]
        assert part["text"] == original[offset : offset + len(part["text"])]
        if part["next_offset"] is None:
            assert part["text"] == original[offset:]
        # the title plus the passage shown, then the fetched remainder: nothing of the chunk is lost
        assert body.partition("\n")[2] in original
        assert len(body) < len(original)


def test_the_fetch_call_returns_the_whole_remainder_of_every_large_baked_chunk(tmp_path: Path) -> None:
    server = _server(tmp_path)
    large = {cid: text for cid, text in _oracle_doc_chunks().items() if len(text) > ITEM_TEXT_CAP}
    assert len(large) == 9
    for chunk_id, original in large.items():
        content_type = classify_content_type(original)
        assembled = ""
        offset = 0
        while True:
            part = _fetch(server, chunk_id, offset, content_type)[0]
            assert part["offset"] == offset
            assembled += part["text"]
            if part["next_offset"] is None:
                break
            offset = part["next_offset"]
        assert assembled == original
        # a fetch from the middle returns exactly the rest of the oracle text
        middle = original.index("\n", len(original) // 2) + 1
        assert _fetch(server, chunk_id, middle, content_type)[0]["text"] == original[middle:]


def test_a_multi_part_chunk_is_returned_in_line_aligned_parts_that_never_split_a_fence(
    tmp_path: Path,
) -> None:
    lines: list[str] = []
    for block in range(60):
        lines += _prose(8, tag=f"b{block}") + _fence(7, tag=f"x{block}")
    original = _doc("Synthetic Reference", lines)
    assert len(original) > 40_000
    server = _server(tmp_path, {"feedfacecafe0001": original})
    content_type = classify_content_type(original)
    assembled = ""
    offset = 0
    parts = 0
    while True:
        part = _fetch(server, "feedfacecafe0001", offset, content_type)[0]
        parts += 1
        text = part["text"]
        assert part["offset"] == offset and part["total_chars"] == len(original)
        assert _fence_lines(text) % 2 == 0
        assembled += text
        if part["next_offset"] is None:
            assert "end of the chunk" in part["part_note"]
            break
        assert part["next_offset"] == offset + len(text)
        assert original[part["next_offset"] - 1] == "\n"
        assert f"offset={part['next_offset']}" in part["part_note"]
        offset = part["next_offset"]
    assert parts >= 2
    assembled_ok = assembled == original
    assert assembled_ok


def test_an_unknown_chunk_is_a_miss_and_a_bad_offset_is_an_invalid_argument(tmp_path: Path) -> None:
    server = _server(tmp_path)
    miss = _fetch(server, "ffffffffffffffff", 0)
    assert isinstance(miss, dict) and "reason" in miss and "chunk_id" in miss["reason"]
    # a chunk of another content type is not returned as if it matched
    wrong_type = _fetch(server, REVIEWED_CHUNK_ID, 0, content_type="faq")
    assert isinstance(wrong_type, dict) and "reason" in wrong_type
    past_the_end = _call_raw(
        server,
        "search_docs",
        {"query": "q", "content_type": "developer_guide", "chunk_id": REVIEWED_CHUNK_ID, "offset": 7604},
    )
    assert past_the_end.is_error is True
    assert past_the_end.structured_content["code"] == "invalid_argument"
    negative = _call_raw(
        server,
        "search_docs",
        {"query": "q", "content_type": "developer_guide", "chunk_id": REVIEWED_CHUNK_ID, "offset": -1},
    )
    assert negative.is_error is True
    assert negative.structured_content["code"] == "invalid_argument"
    without_id = _call_raw(
        server, "search_docs", {"query": "q", "content_type": "developer_guide", "offset": 5}
    )
    assert without_id.is_error is False  # an offset without a chunk_id is ignored: the normal search runs


def test_lookup_with_a_content_type_marks_cut_chunks_with_a_working_search_docs_call(tmp_path: Path) -> None:
    oracle = _oracle_by_chunk_id()
    server = _server(tmp_path)
    hits = _call(server, "lookup", {"query": "convert pdf", "content_type": "developer_guide"})
    cut = [hit for hit in hits if hit["text"] != oracle[hit["chunk_id"]]]
    assert cut
    for hit in cut:
        call = _MARKER_CALL.search(hit["text"].rpartition("\n")[2])
        assert call is not None
        offset = int(call.group("offset"))
        part = _fetch(server, call.group("chunk_id"), offset, call.group("content_type"))[0]
        assert part["text"] == oracle[hit["chunk_id"]][offset : offset + len(part["text"])]


def test_the_tool_schema_and_description_document_the_new_arguments() -> None:
    schema = tool_input_schema("search_docs")
    properties = schema["properties"]
    assert list(properties)[:3] == ["query", "content_type", "top_k"]
    assert properties["chunk_id"]["type"] == "string"
    assert properties["offset"]["type"] == "integer" and properties["offset"]["minimum"] == 0
    assert properties["offset"]["default"] == 0
    for name in ("chunk_id", "offset"):
        description = properties[name]["description"]
        assert "truncated" in description or "cut" in description
    assert schema["required"] == ["query", "content_type"]  # the new arguments are optional
    assert "chunk_id" in TOOL_DESCRIPTIONS["search_docs"] and "offset" in TOOL_DESCRIPTIONS["search_docs"]
    # every argument named by a marker is an argument the schema advertises
    for argument in ("query", "content_type", "chunk_id", "offset"):
        assert argument in properties


def test_the_old_marker_that_promised_a_narrower_query_is_gone() -> None:
    source = (REPO_ROOT / "src" / "foss_mcp" / "mcp" / "response_budget.py").read_text(encoding="utf-8")
    assert "narrow the query or call search_docs for the passages not shown" not in source


def test_the_total_response_cap_constant_is_unchanged() -> None:
    assert TOTAL_CAP_BYTES == CAP
