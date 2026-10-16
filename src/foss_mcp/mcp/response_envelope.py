"""How every successful tool response is wrapped: its scope, and a marker on repository text.

G3/TC-514 (REQ-G3-008). An independent review found three payloads that carried no scope at all
(``get_product_reference``, ``list_recent_changes`` and the found case of ``list_members``, a bare
list of names) and found that only ``get_product_reference`` marked the third-party README and
release text it returns as untrusted; ``search_docs``, ``lookup``, ``find_examples``,
``search_symbols``, ``get_symbol`` and ``list_recent_changes`` returned it unmarked.

The wrapping is ADDITIVE and pure. A payload's shape never changes: a bare list stays a bare list,
no key is removed or renamed, and new keys go last. For a bare list the scope lives only in the
sibling ``structuredContent.scope`` the server adds beside ``result``; a dict result that lacks a
``scope`` gains one in band.
"""

from __future__ import annotations

from typing import Any

from foss_mcp.mcp.routing import Scope

UNTRUSTED_KEY = "untrusted_upstream_content"
CONTENT_ORIGIN_KEY = "content_origin"

# The tools whose results quote text written by a third party (a README, a documentation page,
# release notes, a code example). ``get_product_reference`` keeps its own per-section marker;
# ``list_members`` returns identifiers only; ``report_index_freshness`` is index metadata.
UPSTREAM_TEXT_TOOLS = frozenset(
    {"search_docs", "lookup", "search_symbols", "get_symbol", "find_examples", "list_recent_changes"}
)

_ORIGIN_UPSTREAM = "upstream_repository"
_ORIGIN_MIXED = "mixed"
_ORIGIN_METADATA = "index_metadata"

# A dict (or a dict item of a list) carrying any of these keys holds repository text.
_TEXT_KEYS = ("text", "snippet", "raw_text", "body", "doc_matches", "example")


NOTE_KEY = "note"

# G3/TC-542: an empty release list is a complete answer, but a bare ``[]`` cannot be told from a
# failed fetch or a missing capability. The list stays a bare list; this sentence rides beside it.
NO_RELEASES_NOTE = (
    "This repository has no published GitHub releases, so there is nothing to list. "
    "An empty list is a complete answer, not a fetch failure (a failed fetch is reported as an error). "
    "Use search_docs for changelog or upgrade notes in the documentation."
)


def empty_result_note(tool: str, payload: Any) -> str | None:
    """The explanation that belongs beside an empty answer, or ``None`` when nothing needs saying.

    Only a successful, empty ``list_recent_changes`` answer has one; any other tool and any
    non-empty or non-list payload gains nothing."""
    if tool == "list_recent_changes" and isinstance(payload, list) and not payload:
        return NO_RELEASES_NOTE
    return None


def scope_object(scope: Scope) -> dict[str, str]:
    """The scope a response was answered for, as a plain JSON object."""
    return {"family": scope.family, "platform": scope.platform, "source_kind": scope.source_kind}


def origin_of(tool: str) -> str:
    """Where a tool's content comes from: ``upstream_repository`` for the six tools in
    ``UPSTREAM_TEXT_TOOLS``, ``mixed`` for ``get_product_reference`` (some sections quote the
    repository, some come from the manifest) and ``index_metadata`` for everything else."""
    if tool in UPSTREAM_TEXT_TOOLS:
        return _ORIGIN_UPSTREAM
    if tool == "get_product_reference":
        return _ORIGIN_MIXED
    return _ORIGIN_METADATA


def _mark(item: Any) -> Any:
    """A copy of *item* with the untrusted marker when it is a dict carrying repository text; any
    other value is returned unchanged. A marker already present is kept."""
    if isinstance(item, dict) and any(key in item for key in _TEXT_KEYS):
        return {**item} if UNTRUSTED_KEY in item else {**item, UNTRUSTED_KEY: True}
    return item


def annotate_result(tool: str, payload: Any, scope: Scope) -> Any:
    """*payload* (the jsonable result of *tool*) wrapped additively; the input is never mutated.

    - a dict without a ``scope`` key gains ``scope``;
    - for an ``UPSTREAM_TEXT_TOOLS`` tool, a dict (or each dict item of a list) carrying repository
      text gains ``untrusted_upstream_content: true``, and a dict result also gains
      ``content_origin: "upstream_repository"``;
    - an empty list, a list of strings and a dict without those keys are otherwise unchanged.
    """
    upstream = tool in UPSTREAM_TEXT_TOOLS
    if isinstance(payload, list):
        return [_mark(item) for item in payload] if upstream else list(payload)
    if not isinstance(payload, dict):
        return payload
    annotated: dict[str, Any] = _mark(payload) if upstream else {**payload}
    if "scope" not in annotated:
        annotated["scope"] = scope_object(scope)
    if upstream and CONTENT_ORIGIN_KEY not in annotated:
        annotated[CONTENT_ORIGIN_KEY] = _ORIGIN_UPSTREAM
    return annotated
