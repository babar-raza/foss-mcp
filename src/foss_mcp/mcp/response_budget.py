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

G3/TC-563 (RD3-110): the per-item cap of a DOCUMENTATION chunk is not fixed. ``ITEM_TEXT_CAP`` is its
floor; the cap is the equal share of what the other items leave of the total budget (``_item_cap``), so
a chunk in an otherwise small response is returned whole. A cut falls on a line boundary and never
inside a fenced code block, and its marker names the exact ``search_docs`` call (``chunk_id`` and
``offset``) that returns the rest. ``ADAPTIVE_ITEM_CAP = False`` restores the fixed cap.

Pure and deterministic: no I/O, no clock, no randomness; the input is never mutated. Rules, in order:
dedupe, per-field cap, total cap. A payload no rule touches is returned unchanged.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from foss_mcp.mcp.tools.relevance import DOCUMENTATION, EXAMPLE, SYMBOL, chunk_kind, query_terms

ITEM_TEXT_CAP = 1536
TOTAL_CAP_BYTES = 24576
RELEASE_BODY_CAP = 2048
RAW_TEXT_CAP = 2048

# G3/TC-563 (RD3-110). ``ITEM_TEXT_CAP`` is the FLOOR of the per-item cap of a documentation chunk; the
# cap itself is sized from the part of the total budget the response leaves free (``_item_cap``), so a
# lone 7.6 KB chunk is returned whole and ten of them share the budget. When False the budget falls back
# to the fixed cap and cuts without regard to a code fence, exactly as before.
ADAPTIVE_ITEM_CAP = True

# The key a continuation part (``search_docs`` with ``chunk_id``) carries: such an item was sized to fit
# by the tool itself and is never excerpted again.
_PART_KEY = "total_chars"

# What the adaptive cap leaves room for besides the texts: a few digits more in each marker than the
# floor-sized trial run had, and the response notes of a touched dict result.
_MARKER_SLACK = 24
_NOTES_RESERVE = 640

# The keys of a dict result whose value is a list that follows the rules of a list result.
_LIST_KEYS = ("doc_matches", "symbols")

# A line longer than this is split before a window is chosen (the fixed-cap behaviour only: the adaptive
# cap keeps a line whole unless it alone cannot fit), so one enormous line cannot make the excerpt miss
# its budget.
_MAX_LINE_CHARS = 400

_RESPONSE_NOTE = "response size cap of {cap} bytes reached; narrow the query or lower top_k"

# A markdown code fence: three or more backticks or tildes, optionally indented (a fence inside a list
# item is indented), optionally followed by an info string.
_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")


def _size(value: object) -> int:
    return len(json.dumps(value, default=str).encode("utf-8"))


def _json_len(text: str) -> int:
    """The bytes *text* takes as a JSON string value, quotes included."""
    return len(json.dumps(text).encode("utf-8"))


def _item_cap(remaining_bytes: int, item_count: int) -> int:
    """The per-item character cap for *item_count* documentation chunks that share *remaining_bytes* of
    the total budget: never below ``ITEM_TEXT_CAP``, and the equal share when that is larger. With
    ``ADAPTIVE_ITEM_CAP`` False it is always ``ITEM_TEXT_CAP``."""
    if not ADAPTIVE_ITEM_CAP:
        return ITEM_TEXT_CAP
    return max(ITEM_TEXT_CAP, remaining_bytes // max(item_count, 1))


# ---------------------------------------------------------------------------------------------
# code fences
# ---------------------------------------------------------------------------------------------


def fence_blocks(lines: list[str]) -> list[tuple[int, int] | None]:
    """For each of *lines*, the ``(first, last)`` line indexes of the fenced code block it belongs to
    (the opening and closing fence lines included), or ``None`` outside a fence. A fence closes with a
    line of the same character and at least the opening length; one never closed runs to the end."""
    blocks: list[tuple[int, int] | None] = [None] * len(lines)
    opening: tuple[int, str, int] | None = None
    for index, line in enumerate(lines):
        match = _FENCE_RE.match(line)
        if opening is None:
            if match is not None:
                char = match.group(1)[0]
                if char == "~" or "`" not in match.group(2):
                    opening = (index, char, len(match.group(1)))
            continue
        start, char, length = opening
        if match is not None and match.group(1)[0] == char and len(match.group(1)) >= length:
            if not match.group(2).strip():
                for member in range(start, index + 1):
                    blocks[member] = (start, index)
                opening = None
    if opening is not None:
        for member in range(opening[0], len(lines)):
            blocks[member] = (opening[0], len(lines) - 1)
    return blocks


# ---------------------------------------------------------------------------------------------
# excerpts
# ---------------------------------------------------------------------------------------------


class _Piece:
    """A window unit: a whole line of the chunk, or a slice of a line too long to be one unit."""

    __slots__ = ("end", "line", "start")

    def __init__(self, start: int, end: int, line: int) -> None:
        self.start = start
        self.end = end
        self.line = line


def _pieces(text: str, base: int, max_line: int) -> tuple[list[_Piece], list[str]]:
    """The pieces of ``text[base:]`` (offsets are into *text*) and its lines. A line longer than
    *max_line* is split at spaces into pieces of at most *max_line* characters."""
    pieces: list[_Piece] = []
    lines: list[str] = []
    position = base
    while position <= len(text):
        newline = text.find("\n", position)
        end = len(text) if newline < 0 else newline
        line_index = len(lines)
        lines.append(text[position:end])
        if end - position <= max_line:
            pieces.append(_Piece(position, end, line_index))
        else:
            start = position
            while start < end:
                stop = min(start + max_line, end)
                if stop < end:
                    space = text.rfind(" ", start, stop)
                    if space > start:
                        stop = space + 1
                pieces.append(_Piece(start, stop, line_index))
                start = stop
        if newline < 0:
            break
        position = newline + 1
    return pieces, lines


def _doc_marker(
    item: dict[str, Any], shown: int, total: int, window: tuple[int, int] | None, head: bool
) -> str:
    """The marker that ends a documentation excerpt. When the item carries the ids that name its chunk
    it names the exact ``search_docs`` call that returns the rest; the offsets are into the full chunk.
    *window* is the ``(first, next)`` offsets of the passage shown after the title line, ``None`` when
    only the title line is shown; *head* says the passage follows the title directly."""
    chunk_id = item.get("chunk_id")
    content_type = item.get("content_type")
    if not isinstance(chunk_id, str) or not chunk_id or not isinstance(content_type, str):
        return f"[truncated: showing {shown} of {total} characters]"
    call = f"search_docs with query=<the same query>, content_type='{content_type}', chunk_id='{chunk_id}'"
    if window is None:
        return f"[truncated: showing {shown} of {total} characters; call {call} and offset=0 for the chunk]"
    start, end = window
    if head:
        return (
            f"[truncated: showing {shown} of {total} characters; call {call} and offset={end} for the rest]"
        )
    return (
        f"[truncated: showing {shown} of {total} characters (the title line and characters {start}-{end}); "
        f"call {call} and offset=0 for the chunk from its start, or offset={end} for the part after this "
        "passage]"
    )


def _documentation_excerpt(item: dict[str, Any], terms: frozenset[str], family: str, cap: int) -> str:
    """The first line, then the window of consecutive lines holding the most distinct query terms (ties:
    the earliest window), cut at line boundaries and, with ``ADAPTIVE_ITEM_CAP``, never inside a fenced
    code block, then the marker. The window is a verbatim slice of the chunk."""
    text = item["text"]
    first, _newline, _rest = text.partition("\n")
    if len(first) > cap // 2 or "\n" not in text:
        first, base = "", 0  # no usable title line: the whole text is window material
    else:
        base = len(first) + 1
    fence_aware = ADAPTIVE_ITEM_CAP
    budget = max(cap - len(first) - 1, 0)
    pieces, lines = _pieces(text, base, budget if fence_aware else _MAX_LINE_CHARS)
    blocks = fence_blocks(lines) if fence_aware else [None] * len(lines)
    piece_terms = [
        query_terms(text[piece.start : piece.end], family) & terms if terms else frozenset()
        for piece in pieces
    ]
    # the first piece of each line, to tell an opening fence line from the middle of a block
    first_piece_of_line: dict[int, int] = {}
    for index, piece in enumerate(pieces):
        first_piece_of_line.setdefault(piece.line, index)

    def starts_inside_block(index: int) -> bool:
        block = blocks[pieces[index].line]
        return block is not None and not (
            pieces[index].line == block[0] and first_piece_of_line[block[0]] == index
        )

    def snap_end(start: int, end: int) -> int:
        """*end* (exclusive) moved up to the start of the fenced block it would otherwise cut."""
        if end >= len(pieces) or end <= start:
            return end
        block = blocks[pieces[end - 1].line]
        if block is None or blocks[pieces[end].line] != block:
            return end
        return max(start, first_piece_of_line[block[0]])

    best: tuple[int, int] = (0, 0)
    best_score = -1
    end = 0
    for start in range(len(pieces)):
        if starts_inside_block(start):
            continue
        end = max(end, start)
        while end < len(pieces) and pieces[end].end - pieces[start].start <= budget:
            end += 1
        stop = snap_end(start, end) if fence_aware else end
        while stop > start + 1 and not text[pieces[stop - 1].start : pieces[stop - 1].end].strip():
            stop -= 1
        if stop == start:
            continue
        score = len(frozenset().union(*piece_terms[start:stop])) if terms else 0
        if score > best_score:
            best, best_score = (start, stop), score
        if not terms:
            break
    start_piece, stop_piece = best
    if stop_piece > start_piece:
        window_start, window_end = pieces[start_piece].start, pieces[stop_piece - 1].end
        window = text[window_start:window_end]
        kept = first + ("\n" if first else "") + window
        head = window_start == base
        next_offset = window_end + (1 if text.startswith("\n", window_end) else 0)
        marker = _doc_marker(item, len(kept), len(text), (window_start, next_offset), head)
    else:
        kept = first
        marker = _doc_marker(item, len(kept), len(text), None, False)
    return kept + "\n" + marker


def _head_excerpt(text: str, marker_template: str) -> str:
    cut = text[:ITEM_TEXT_CAP]
    newline = cut.rfind("\n")
    if newline > 0:
        cut = cut[:newline]
    return cut + "\n" + marker_template.format(shown=len(cut), total=len(text))


_SYMBOL_MARKER = (
    "[truncated: showing {shown} of {total} characters; call get_symbol with fqn='{fqn}' for the full "
    "signature]"
)


def _cap_text(item: dict[str, Any], terms: frozenset[str], family: str, doc_cap: int) -> str | None:
    """Excerpt ``item['text']`` in place (the caller passes a copy). Returns ``"documentation"`` or
    ``"symbol"`` for what was cut, ``None`` when nothing was."""
    text = item.get("text")
    if not isinstance(text, str) or len(text) <= ITEM_TEXT_CAP:
        return None
    if _PART_KEY in item:
        return None  # a continuation part: search_docs sized it to fit
    kind = chunk_kind(text)
    if kind == EXAMPLE:
        return None  # a code example is never cut
    if kind == DOCUMENTATION:
        if len(text) <= doc_cap:
            return None
        item["text"] = _documentation_excerpt(item, terms, family, doc_cap)
        return DOCUMENTATION
    if kind == SYMBOL:
        first = text.partition("\n")[0]
        fqn = first.partition(":")[2].strip()
        item["text"] = _head_excerpt(
            text, _SYMBOL_MARKER.replace("{fqn}", fqn.replace("{", "{{").replace("}", "}}"))
        )
        return SYMBOL
    return None


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
        # (JSON bytes of the whole chunk, of its excerpt, of the marker ending the excerpt) for each
        # documentation chunk cut
        self.doc_cuts: list[tuple[int, int, int]] = []


def _prepare_list(
    items: list[Any], terms: frozenset[str], family: str, counters: _Counters, doc_cap: int
) -> list[Any]:
    """Rules 1 and 2 for one list: dedupe, then the per-field caps (on copies)."""
    deduped, removed = _dedupe(items)
    counters.deduplicated_items += removed
    prepared: list[Any] = []
    for item in deduped:
        if isinstance(item, dict) and ("text" in item or "body" in item):
            copy = dict(item)
            original = copy.get("text")
            cut = _cap_text(copy, terms, family, doc_cap)
            if cut is not None:
                counters.truncated_fields += 1
                if cut == DOCUMENTATION and isinstance(original, str):
                    marker = copy["text"].rpartition("\n")[2]
                    counters.doc_cuts.append((_json_len(original), _json_len(copy["text"]), len(marker)))
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


def _fill_cap(remaining_bytes: int, text_sizes: list[int]) -> int:
    """The per-item cap for documentation chunks whose JSON text sizes are *text_sizes*, sharing
    *remaining_bytes*: a chunk smaller than its equal share keeps all of itself and leaves the rest of
    the share to the others (water-filling), so ``_item_cap`` is asked with what is really left."""
    left = len(text_sizes)
    for size in sorted(text_sizes):
        if size > _item_cap(remaining_bytes, left):
            break
        remaining_bytes -= size
        left -= 1
    return _item_cap(remaining_bytes, left) if left else max(text_sizes, default=ITEM_TEXT_CAP)


def _choose_doc_cap(build: Callable[[int], tuple[Any, _Counters]], measure: Callable[[Any], int]) -> int:
    """The per-item cap for the documentation chunks of one result (G3/TC-563).

    *build* prepares the result with a given cap and *measure* is its size in bytes. The result is
    first prepared with the floor to learn what the documentation chunks cost and what everything else
    costs; the remaining budget is then shared among the chunks, and the cap is lowered until the
    prepared result fits the total cap (UTF-8 and JSON escaping make a character cost more than a byte).
    The floor is returned when there is nothing to share or the flag is off."""
    if not ADAPTIVE_ITEM_CAP:
        return ITEM_TEXT_CAP
    state, counters = build(ITEM_TEXT_CAP)
    cuts = counters.doc_cuts
    if not cuts:
        return ITEM_TEXT_CAP
    others = measure(state) - sum(excerpt - 2 for _whole, excerpt, _marker in cuts)
    markers = sum(marker + _MARKER_SLACK for _whole, _excerpt, marker in cuts)
    remaining = TOTAL_CAP_BYTES - others - markers - _NOTES_RESERVE
    cap = _fill_cap(remaining, [whole - 2 for whole, _excerpt, _marker in cuts])
    for _attempt in range(6):
        if cap <= ITEM_TEXT_CAP:
            break
        state, counters = build(cap)
        overshoot = measure(state) - TOTAL_CAP_BYTES
        if overshoot <= 0:
            return cap
        cap = min(cap - 1, cap - overshoot // max(len(counters.doc_cuts), 1) - 16)
    return ITEM_TEXT_CAP


def _budget_list(items: list[Any], terms: frozenset[str], family: str) -> tuple[list[Any], _Counters]:
    def build(cap: int) -> tuple[list[Any], _Counters]:
        counters = _Counters()
        return _prepare_list(items, terms, family, counters, cap), counters

    prepared, counters = build(_choose_doc_cap(build, _size))
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

    def build(cap: int) -> tuple[tuple[dict[str, Any], dict[str, list[Any]]], _Counters]:
        built_counters = _Counters()
        built = dict(payload)
        built_lists: dict[str, list[Any]] = {}
        for key in _LIST_KEYS:
            value = built.get(key)
            if isinstance(value, list):
                built_lists[key] = _prepare_list(value, terms, family, built_counters, cap)
                built[key] = built_lists[key]
        return (built, built_lists), built_counters

    (prepared, lists), counters = build(_choose_doc_cap(build, lambda state: _size(state[0])))
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
