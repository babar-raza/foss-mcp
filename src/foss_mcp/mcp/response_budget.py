"""A size budget for every tool response, applied to the result BEFORE it is serialised.

G3/TC-515 (REQ-G3-008). An independent review measured unbounded answers: a ``lookup`` of 60 KB and
of 186 KB (``top_k=100``), a ``list_recent_changes`` of 77 KB (verbatim release bodies), a
``search_docs`` answer of 98 KB, one README class-table chunk of 23.7 KB. The same object is sent in
``content[0].text`` and in ``structuredContent.result`` (the MCP specification recommends both), so
the saving comes from capping the object itself.

What is cut and what never is:

- only PROSE is excerpted: a documentation chunk (query-aware window), a symbol chunk in a search
  result (its head, with a pointer to ``get_symbol``), a release body and, only when the whole
  response is still over the cap, ``get_symbol``'s ``raw_text``;
- a type signature or a code example is never cut: ``get_symbol``'s ``bases``/``methods``/
  ``properties``/``members`` arrays, ``list_members``' names, a ``snippet`` and an example chunk are
  returned whole;
- lower-ranked list items are dropped last, with an explicit count on the last item kept;
- identical items of one list are removed once.

Pure and deterministic: no I/O, no clock, no randomness; the input is never mutated. Rules, in order:
dedupe, per-field cap, total cap. A payload no rule touches is returned unchanged.
"""

from __future__ import annotations

import json
from typing import Any

from foss_mcp.mcp.tools.relevance import DOCUMENTATION, EXAMPLE, SYMBOL, chunk_kind, query_terms

ITEM_TEXT_CAP = 1536
TOTAL_CAP_BYTES = 24576
RELEASE_BODY_CAP = 2048
RAW_TEXT_CAP = 2048

# The keys of a dict result whose value is a list that follows the rules of a list result.
_LIST_KEYS = ("doc_matches", "symbols")

# A line longer than this is split before a window is chosen, so one enormous line cannot make the
# excerpt miss its budget.
_MAX_LINE_CHARS = 400

_RESPONSE_NOTE = "response size cap of {cap} bytes reached; narrow the query or lower top_k"


def _size(value: object) -> int:
    return len(json.dumps(value, default=str).encode("utf-8"))


# ---------------------------------------------------------------------------------------------
# excerpts
# ---------------------------------------------------------------------------------------------


def _split_long_lines(lines: list[str]) -> list[str]:
    pieces: list[str] = []
    for line in lines:
        if len(line) <= _MAX_LINE_CHARS:
            pieces.append(line)
            continue
        start = 0
        while start < len(line):
            end = min(start + _MAX_LINE_CHARS, len(line))
            if end < len(line):
                space = line.rfind(" ", start, end)
                if space > start:
                    end = space + 1
            pieces.append(line[start:end])
            start = end
    return pieces


def _documentation_excerpt(text: str, terms: frozenset[str], family: str, marker_template: str) -> str:
    """The first line, then the window of consecutive lines holding the most distinct query terms
    (ties: the earliest window), cut at line boundaries, then the marker."""
    first, _newline, rest = text.partition("\n")
    lines = _split_long_lines(rest.split("\n")) if rest else []
    # The marker is at most ~170 characters; reserve it so header + window + marker stays near the cap.
    budget = max(ITEM_TEXT_CAP - len(first) - 1, 0)
    line_terms = [query_terms(line, family) & terms if terms else frozenset() for line in lines]

    best_start, best_end, best_score = 0, 0, -1
    for start in range(len(lines)):
        used = 0
        end = start
        while end < len(lines):
            cost = len(lines[end]) + (1 if end > start else 0)
            if used + cost > budget:
                break
            used += cost
            end += 1
        if end == start:
            continue
        score = len(frozenset().union(*line_terms[start:end])) if terms else 0
        if score > best_score:
            best_start, best_end, best_score = start, end, score
        if not terms:
            break
    window = "\n".join(lines[best_start:best_end])
    kept = first + ("\n" + window if window else "")
    return kept + "\n" + marker_template.format(shown=len(kept), total=len(text))


def _head_excerpt(text: str, marker_template: str) -> str:
    cut = text[:ITEM_TEXT_CAP]
    newline = cut.rfind("\n")
    if newline > 0:
        cut = cut[:newline]
    return cut + "\n" + marker_template.format(shown=len(cut), total=len(text))


_DOC_MARKER = (
    "[truncated: showing {shown} of {total} characters; narrow the query or call search_docs for the "
    "passages not shown]"
)
_SYMBOL_MARKER = (
    "[truncated: showing {shown} of {total} characters; call get_symbol with fqn='{fqn}' for the full "
    "signature]"
)


def _cap_text(item: dict[str, Any], terms: frozenset[str], family: str) -> bool:
    """Excerpt ``item['text']`` in place (the caller passes a copy); True when it was cut."""
    text = item.get("text")
    if not isinstance(text, str) or len(text) <= ITEM_TEXT_CAP:
        return False
    kind = chunk_kind(text)
    if kind == EXAMPLE:
        return False  # a code example is never cut
    if kind == DOCUMENTATION:
        item["text"] = _documentation_excerpt(text, terms, family, _DOC_MARKER)
        return True
    if kind == SYMBOL:
        first = text.partition("\n")[0]
        fqn = first.partition(":")[2].strip()
        item["text"] = _head_excerpt(
            text, _SYMBOL_MARKER.replace("{fqn}", fqn.replace("{", "{{").replace("}", "}}"))
        )
        return True
    return False


def _cap_body(item: dict[str, Any]) -> bool:
    """Cut a release ``body`` to RELEASE_BODY_CAP at a line boundary, in place; True when cut."""
    body = item.get("body")
    if not isinstance(body, str) or len(body) <= RELEASE_BODY_CAP:
        return False
    cut = body[:RELEASE_BODY_CAP]
    newline = cut.rfind("\n")
    if newline > 0:
        cut = cut[:newline]
    cut = cut.rstrip("\r")
    url = item.get("url")
    where = f"full notes: {url}" if isinstance(url, str) and url else "full notes at the release page"
    item["body"] = cut + f"\n[truncated: showing {len(cut)} of {len(body)} characters; {where}]"
    return True


# ---------------------------------------------------------------------------------------------
# the rules
# ---------------------------------------------------------------------------------------------


def _dedupe(items: list[Any]) -> tuple[list[Any], int]:
    seen: set[str] = set()
    kept: list[Any] = []
    removed = 0
    for item in items:
        key: str | None = None
        if isinstance(item, dict):
            for field in ("text", "snippet"):
                value = item.get(field)
                if isinstance(value, str):
                    key = f"{field}\0{value.strip()}"
                    break
        if key is not None:
            if key in seen:
                removed += 1
                continue
            seen.add(key)
        kept.append(item)
    return kept, removed


def _is_dict_list(items: list[Any]) -> bool:
    return bool(items) and all(isinstance(item, dict) for item in items)


def _with_omitted(items: list[Any], keep: int, omitted: int, cap: int) -> list[Any]:
    kept = [dict(item) if isinstance(item, dict) else item for item in items[:keep]]
    if omitted and kept and isinstance(kept[-1], dict):
        kept[-1]["omitted_items"] = omitted
        kept[-1]["response_note"] = _RESPONSE_NOTE.format(cap=cap)
    return kept


class _Counters:
    def __init__(self) -> None:
        self.truncated_fields = 0
        self.deduplicated_items = 0
        self.omitted_items = 0


def _prepare_list(items: list[Any], terms: frozenset[str], family: str, counters: _Counters) -> list[Any]:
    """Rules 1 and 2 for one list: dedupe, then the per-field caps (on copies)."""
    deduped, removed = _dedupe(items)
    counters.deduplicated_items += removed
    prepared: list[Any] = []
    for item in deduped:
        if isinstance(item, dict) and ("text" in item or "body" in item):
            copy = dict(item)
            if _cap_text(copy, terms, family):
                counters.truncated_fields += 1
            if _cap_body(copy):
                counters.truncated_fields += 1
            prepared.append(copy)
        else:
            prepared.append(item)
    return prepared


def _notes_sentences(counters: _Counters, raw_text_cut: bool) -> list[str]:
    sentences: list[str] = []
    if counters.deduplicated_items:
        sentences.append(f"{counters.deduplicated_items} identical item(s) were removed")
    if counters.truncated_fields:
        sentences.append(
            f"{counters.truncated_fields} long prose field(s) were excerpted; the marker at the end of "
            "each says how to get the rest"
        )
    if raw_text_cut:
        sentences.append(
            f"raw_text was cut to {RAW_TEXT_CAP} characters because the response exceeded "
            f"{TOTAL_CAP_BYTES} bytes; the bases, methods, properties and members arrays are complete"
        )
    if counters.omitted_items:
        sentences.append(
            f"{counters.omitted_items} lower-ranked item(s) were left out; "
            + _RESPONSE_NOTE.format(cap=TOTAL_CAP_BYTES)
        )
    return sentences


def _cut_raw_text(raw_text: str) -> str:
    cut = raw_text[:RAW_TEXT_CAP]
    newline = cut.rfind("\n")
    if newline > 0:
        cut = cut[:newline]
    return (
        cut + f"\n[truncated: showing {len(cut)} of {len(raw_text)} characters; the bases, methods, "
        "properties and members arrays are complete]"
    )


def _budget_list(items: list[Any], terms: frozenset[str], family: str) -> tuple[list[Any], _Counters]:
    counters = _Counters()
    prepared = _prepare_list(items, terms, family, counters)
    if _size(prepared) > TOTAL_CAP_BYTES and _is_dict_list(prepared):
        keep = len(prepared)
        while keep > 1:
            candidate = _with_omitted(prepared, keep, len(prepared) - keep, TOTAL_CAP_BYTES)
            if _size(candidate) <= TOTAL_CAP_BYTES:
                break
            keep -= 1
        counters.omitted_items = len(prepared) - keep
        prepared = _with_omitted(prepared, keep, counters.omitted_items, TOTAL_CAP_BYTES)
    return prepared, counters


def _budget_dict(
    payload: dict[str, Any], terms: frozenset[str], family: str
) -> tuple[dict[str, Any], _Counters]:
    counters = _Counters()
    prepared = dict(payload)
    lists: dict[str, list[Any]] = {}
    for key in _LIST_KEYS:
        value = prepared.get(key)
        if isinstance(value, list):
            lists[key] = _prepare_list(value, terms, family, counters)
            prepared[key] = lists[key]
    raw_text = prepared.get("raw_text")
    touched = bool(counters.truncated_fields or counters.deduplicated_items)

    def assemble(keeps: dict[str, int], cut_raw: bool) -> dict[str, Any]:
        built = dict(prepared)
        omitted_total = 0
        for key, items in lists.items():
            omitted = len(items) - keeps[key]
            omitted_total += omitted
            built[key] = _with_omitted(items, keeps[key], omitted, TOTAL_CAP_BYTES)
        if cut_raw and isinstance(raw_text, str):
            built["raw_text"] = _cut_raw_text(raw_text)
        counters.omitted_items = omitted_total
        sentences = _notes_sentences(counters, cut_raw and isinstance(raw_text, str))
        if sentences:
            built["response_notes"] = sentences
        return built

    keeps = {key: len(items) for key, items in lists.items()}
    built = assemble(keeps, False)
    if _size(built) <= TOTAL_CAP_BYTES:
        return (built if touched else payload), counters if touched else _Counters()

    cut_raw = False
    if isinstance(raw_text, str) and len(raw_text) > RAW_TEXT_CAP:
        cut_raw = True
        counters.truncated_fields += 1
        built = assemble(keeps, cut_raw)
    while _size(built) > TOTAL_CAP_BYTES:
        trimmable = [key for key in lists if keeps[key] > 1 and _is_dict_list(lists[key])]
        if not trimmable:
            break
        largest = max(trimmable, key=lambda key: (_size(lists[key][: keeps[key]]), -list(lists).index(key)))
        keeps[largest] -= 1
        built = assemble(keeps, cut_raw)
    return built, counters


def apply_budget(
    tool: str, payload: object, *, query: str | None, family: str
) -> tuple[object, dict[str, int]]:
    """*payload* (the annotated jsonable result of *tool*) within the response budget, and a notes
    dict ``{truncated_fields, deduplicated_items, omitted_items, original_bytes, returned_bytes,
    total_cap_bytes}``. The input is never mutated; a payload no rule touches is returned as is."""
    del tool  # the rules key off the payload's own shape, never the tool name
    original_bytes = _size(payload)
    terms = query_terms(query, family) if query else frozenset()
    result: object = payload
    counters = _Counters()
    if isinstance(payload, list):
        result, counters = _budget_list(payload, terms, family)
    elif isinstance(payload, dict):
        result, counters = _budget_dict(payload, terms, family)
    untouched = not (counters.truncated_fields or counters.deduplicated_items or counters.omitted_items)
    if untouched:
        result = payload
        returned_bytes = original_bytes
    else:
        returned_bytes = _size(result)
    return result, {
        "truncated_fields": counters.truncated_fields,
        "deduplicated_items": counters.deduplicated_items,
        "omitted_items": counters.omitted_items,
        "original_bytes": original_bytes,
        "returned_bytes": returned_bytes,
        "total_cap_bytes": TOTAL_CAP_BYTES,
    }
