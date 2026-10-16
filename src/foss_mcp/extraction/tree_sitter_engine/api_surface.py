# Ported from repository-presenter at commit 16d75e95d4 (src/repository_presenter/components/readme/extractors/surface/_vendor/aspose_extraction/api_surface.py),
# itself vendored from aspose.org. Patch applied: import paths rewritten from
# `repository_presenter.components.readme.extractors.surface._vendor.aspose_extraction`
# to `foss_mcp.extraction.tree_sitter_engine`. No other changes.
"""extraction/api_surface.py â€” API surface extraction from source trees.

Extracted from scout.py (steps 4.7). Standalone functions usable without the
Scout class.

Public API::

    extract_api_surface(parser, language, pkg_root, repo, family,
                        *, excluded_package_segments=None)
        -> tuple[list[dict], list[dict], list[str], set[str], dict]
        # (classes, claims, scanned_files, packages, scout_report)

    _is_excluded_java_package(package, excluded_segments)
        -> bool  # True when package has an excluded segment

Constants::

    _JAVA_DEFAULT_EXCLUDED_PACKAGE_SEGMENTS  # frozenset({"internal", "impl"})
"""

from __future__ import annotations

import ast
import copy
import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

from foss_mcp.extraction.tree_sitter_engine import lang
from foss_mcp.extraction.tree_sitter_engine.tree_helpers import (
    _CLASS_TYPES,
    _DOC_COMMENT_STYLES,
    _FILE_EXTENSIONS,
    _FUNC_TYPES,
    _HEADER_EXTENSIONS,
    _MODULE_TYPES,
    _PY_ENUM_BASES,
    _collect_source_files,
    _extract_bases,
    _extract_enum_members,
    _extract_python_enum_members,
    _in_excluded_preproc_branch,
    _node_name,
    child_by_field,
    collect_nodes,
    deprecated_marker,
    find_child_by_type,
    find_children_by_type,
    is_public,
    node_text,
    synthesize_interface_members,
    visibility_tier,
)

# ---------------------------------------------------------------------------
# SYS-PKG-001: Java internal-package exclusion filter
# ---------------------------------------------------------------------------

#: Default package segments excluded for Java at the scout/extraction layer.
#: Classes whose ``class_package`` contains any of these as a dot-separated
#: segment are not emitted to api_surface.json, preventing them from ever
#: reaching batch_reference, enrichment, or site-plan steps.
#:
#: Segment matching is exact (split on ".") â€” "international" is NOT matched
#: by "internal".  To disable filtering pass
#: ``excluded_package_segments=frozenset()`` to ``extract_api_surface()``.
_JAVA_DEFAULT_EXCLUDED_PACKAGE_SEGMENTS: frozenset[str] = frozenset({"internal", "impl"})

# H-04d: Path segments that indicate vendor/internal code for Python/C++.
_VENDOR_PATH_SEGMENTS: frozenset[str] = frozenset(
    {
        "vendor",
        "vendored",
        "third_party",
        "thirdparty",
    }
)

# Private directory names that are exempt from filtering (contain real implementations)
_EXEMPT_PRIVATE_DIRS: frozenset[str] = frozenset({"_internal"})

# HARDEN-cpp-internal-collision (2026-07-28, MT012): C++-only vendor segment.
# pdf/cpp's real repo has a genuinely private `include/internal/` header tree â€”
# unlike Java/.NET's "internal" *package* convention (slides/java's
# org.aspose.slides.foss.internal ships real, already-published reference pages
# for PptxExporter/OpcPackage/etc. â€” those must NOT be affected), C++'s
# `internal/` headers are unreachable from the library's public
# `#include <aspose/pdf/...>` surface and routinely reuse the SAME short struct
# name (e.g. `DecodedImage`, `Options`, `Point`, `Rectangle`) across independent,
# unrelated per-codec files. batch_reference.py's collision-detection correctly
# refused to merge these into one page ("template_propagation_without_shared_
# grounding") rather than silently fabricating a page from the wrong file's
# methods -- the real fix is to never surface them as public API in the first
# place. Gated strictly to language=="cpp" so Java/.NET's own "internal"
# handling (see _JAVA_DEFAULT_EXCLUDED_PACKAGE_SEGMENTS above, a separate,
# package-segment-based mechanism) is completely untouched.
_CPP_ONLY_VENDOR_SEGMENTS: frozenset[str] = frozenset({"internal"})


def _detect_vendor_files(files: list[Path], pkg_root: Path, language: str = "") -> set[Path]:
    """Return the subset of files that are inside vendor/private directories.

    A directory is considered vendor if its name is in ``_VENDOR_PATH_SEGMENTS``
    (all languages) or, when *language* is ``"cpp"``, in
    ``_CPP_ONLY_VENDOR_SEGMENTS``. A directory is considered private if its name
    starts with ``_`` (but not ``__``) and is not in ``_EXEMPT_PRIVATE_DIRS``.

    These files will still be scanned (so class names appear in api_surface.json
    for diagnostic purposes) but their entries will have ``visibility: "internal"``.
    """
    vendor_segments = _VENDOR_PATH_SEGMENTS
    if language == "cpp":
        vendor_segments = vendor_segments | _CPP_ONLY_VENDOR_SEGMENTS
    vendor: set[Path] = set()
    for fpath in files:
        try:
            rel_parts = fpath.relative_to(pkg_root).parts
        except ValueError:
            continue
        for part in rel_parts[:-1]:
            if part.lower() in vendor_segments:
                vendor.add(fpath)
                break
            if part.startswith("_") and not part.startswith("__") and part not in _EXEMPT_PRIVATE_DIRS:
                vendor.add(fpath)
                break
    return vendor


# ---------------------------------------------------------------------------
# RC-W1-004 (MT026 blind-spot audit): reachability tagging
# ---------------------------------------------------------------------------
#
# is_public() (tree_helpers.py) already answers "is this declaration marked
# public at the syntax level," and for C#/Java that is sufficient to exclude
# `internal` types entirely -- they never reach api_surface.json in the first
# place (confirmed: cells/net's `internal class XmlParsingException` never
# appears in the output, because is_public() requires "public" among the
# accumulated modifier text). But two other languages positively INCLUDE
# items that are syntactically public/undecorated yet still unreachable by a
# real consumer importing the package:
#
#   Python: `class Foo` in a deeply-nested module is is_public()==True (no
#   leading underscore) and gets extracted -- but `from package import Foo`
#   only works if `package/__init__.py` actually re-exports it. Confirmed
#   live: words/python's DocumentReader/LdmDocxWriter are real, documented,
#   is_public()==True classes that are NOT in aspose/words_foss/__init__.py's
#   __all__ (only Document, SaveFormat, LoadFormat, saving are) --
#   `from aspose.words_foss import DocumentReader` raises ImportError for a
#   real reader of the generated docs page.
#
#   Rust: `pub struct Foo` inside a private `mod api;` is is_public()==True
#   (the struct itself carries a bare `pub`) but unreachable from outside the
#   crate unless `lib.rs` re-exports it via `pub use api::Foo;` (or the
#   enclosing module chain is itself `pub mod`). Confirmed live: cells/rust's
#   WorkbookModel is a `pub struct` inside `mod api;` (private) and is NOT in
#   lib.rs's `pub use api::{...}` list, so no consumer of the crate can ever
#   name it.
#
# Both helpers below are best-effort, single-file, regex-based scans (same
# spirit as the existing S-1 per-file __all__ extraction below) -- NOT full
# module-graph resolution. Any pattern they don't recognize (multi-level
# re-export chains, glob `pub use x::*`, conditional/computed __all__, etc.)
# makes them return None, and the caller then leaves every entry's
# `reachable` at the default True -- this is a strictly additive signal that
# only ever POSITIVELY marks an entry False; an unrecognized pattern must
# never manufacture a false negative.


# TC-MT040-42: the per-language algorithms formerly inlined here now live in
# extraction/lang/python.py and extraction/lang/rust.py (see extraction/lang/
# __init__.py for the shared adapter contract). These three names are kept
# as thin re-exporting wrappers -- unchanged names/signatures/behavior --
# because scout.py and several tests
# (test_api_surface_reachability.py, test_rust_extraction.py) import them
# directly from this module. No duplicate logic remains: each wrapper calls
# straight through to the single real implementation in lang/.


def _extract_all_from_init(init_path: Path) -> set[str] | None:
    """Return the name set from a single __init__.py's top-level __all__.

    See extraction/lang/python.py's ``_extract_all_from_init`` (the real,
    single implementation this delegates to) for the full docstring.
    """
    return lang.python._extract_all_from_init(init_path)


def _python_top_level_exports(pkg_root: Path) -> tuple[set[str] | None, Path | None]:
    """Return (export_names, export_root) for the package's real public surface.

    See extraction/lang/python.py's ``top_level_exports`` (the real, single
    implementation this delegates to) for the full docstring.
    """
    return lang.python.top_level_exports(pkg_root)


def _rust_reexported_names(pkg_root: Path) -> set[str] | None:
    """Return the set of names re-exported via `pub use` at the crate root.

    See extraction/lang/rust.py's ``reexported_names`` (the real, single
    implementation this delegates to) for the full docstring.
    """
    return lang.rust.reexported_names(pkg_root)


def _is_excluded_java_package(package: str, excluded_segments: frozenset[str]) -> bool:
    """Return True if any dot-separated segment of *package* is in *excluded_segments*.

    Uses exact segment matching so that "international" is NOT excluded by the
    "internal" rule.

    Args:
        package: Fully-qualified Java package string, e.g. "com.aspose.slides.internal.dx".
        excluded_segments: Set of segment strings to block, e.g. frozenset({"internal"}).

    Returns:
        True when the package should be excluded; False otherwise.
    """
    if not package or not excluded_segments:
        return False
    return any(seg in excluded_segments for seg in package.split("."))


# ---------------------------------------------------------------------------
# Internal claim helper
# ---------------------------------------------------------------------------


def _make_claim(family: str, kind: str, text: str, file: str = "", line: int = 0) -> dict:
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:6]
    evidence = [{"file": file, "line": line}] if file else []
    return {
        "claim_id": f"CLM-{family}-{h}",
        "kind": kind,
        "text": text,
        "confidence": 1.0,
        "claim_source": "scout",
        "evidence": evidence,
    }


# Task 16 (mission-plan-the-end-to-end-vast-hejlsberg.md, Â§9.11, closes half of
# G10 -- see enrich.py's _MEMBER_DOC_ANCHOR_RE / _assign_ids for the identical,
# already-shipped precedent this mirrors). Every member-level scout claim's
# text is template-generated by _make_claim's own call sites to begin with the
# exact "ClassName.MemberName" it describes (confirmed at all 15 real
# member-claim call sites in this file, e.g. f"{cname}.{mname}(...) -> {ret}");
# class-level ("Class X defined in...") and free-function ("Function foo(...)
# -> ...") claims never match this prefix and are correctly left untouched.
_SCOUT_MEMBER_ANCHOR_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b")


def _stabilize_scout_claim_ids(claims: list, family: str) -> list:
    """Re-assign stable, anchor-based IDs to scout-derived member claims.

    Root cause this fixes (G10, the "load-bearing" half): claim_id is a hash
    of full claim TEXT, so a real text change (e.g. a return-type formatting
    tweak, or added prose after re-extraction) produces a brand-new ID â€”
    indistinguishable from that member being removed and an unrelated one
    added. refresh_knowledge.py's modified_claims can only ever detect a
    "modified" claim if the SAME ID appears in both the old and new
    claims.json with different text; a text-hash ID can never satisfy that
    for a real text change, only for byte-identical re-extraction. Anchoring
    the ID to "ClassName.MemberName" instead makes a real modification
    visible as a real "modified" event for the first time.

    Mirrors enrich.py's _assign_ids() collision-resolution architecture
    exactly (see its own extensive docstring for the full rationale, measured
    against a real corpus): when more than one claim shares an anchor
    (overloaded methods -- Save(path) vs Save(stream) -- are common, not
    rare), only one deterministically-chosen claim (sorted by text, so the
    choice doesn't depend on extraction order) takes the anchor-based ID;
    every other claim sharing that anchor keeps the _make_claim-assigned
    full-text-hash ID it already has, rather than being dropped or silently
    colliding with the winner.

    Known, accepted, one-time transition cost (not a bug -- documented
    explicitly, matching this mission's own standard of surfacing tradeoffs
    rather than hiding them): the FIRST re-scout of an already-launched
    product after this change ships will assign a NEW anchor-based ID to
    each winning member claim, different from its old full-text-hash ID,
    even though nothing about the real API changed. Any already-published
    content page citing the old ID in its evidence.claims frontmatter will
    show that citation as orphaned until the page's evidence is next
    refreshed (S-20/page-update, or attach_evidence.py) -- exactly the class
    of event stale_detect.py's orphaned_claims detection and
    check_evidence_churn.py's citation-verification gate (Task 22, this same
    mission) already exist to correctly surface and govern, not a new,
    unhandled failure mode. From the SECOND re-scout onward, IDs are stable
    across rebuilds and this cost does not recur.
    """
    anchors: list = []
    for claim in claims:
        anchor = None
        if isinstance(claim, dict):
            match = _SCOUT_MEMBER_ANCHOR_RE.match(claim.get("text", ""))
            if match:
                anchor = f"{match.group(1)}.{match.group(2)}"
        anchors.append(anchor)

    anchor_groups: dict = {}
    for idx, anchor in enumerate(anchors):
        if anchor:
            anchor_groups.setdefault(anchor, []).append(idx)
    anchor_owner: dict = {}
    for anchor, idxs in anchor_groups.items():
        winner = idxs[0] if len(idxs) == 1 else min(idxs, key=lambda i: claims[i].get("text", ""))
        anchor_owner[winner] = True

    result = []
    for idx, claim in enumerate(claims):
        if not isinstance(claim, dict):
            result.append(claim)
            continue
        c = dict(claim)
        anchor = anchors[idx]
        if anchor and anchor_owner.get(idx):
            # [:8] (32 bits), not _make_claim's [:6] (24 bits) -- matches
            # enrich.py's own _assign_ids precedent exactly, and for the same
            # reason: found live against pdf/java's real 14,005-claim scout
            # corpus (Task 16 compatibility check) that [:6] over the anchor
            # space (not the full-text space _make_claim was tuned for)
            # produces real birthday-paradox collisions -- 4 among ~14k real
            # anchors, matching the ~5.9 expected by the math (n^2/2^25).
            # [:8] (n^2/2^33) drops the expected count for the same corpus to
            # ~0.02, verified against this exact real dataset before committing.
            stable = hashlib.sha256((anchor + "|" + c.get("kind", "")).encode("utf-8")).hexdigest()[:8]
            c["claim_id"] = f"CLM-{family}-{stable}"
        elif not c.get("claim_id"):
            # Defensive: normally every claim already has an ID from
            # _make_claim by the time this pass runs (its only real caller,
            # extract_api_surface's own final return) -- this mirrors that
            # exact fallback scheme so the function is correct standalone too,
            # never leaving a claim with no ID at all.
            stable = hashlib.sha256(
                (c.get("text", "") + "|" + c.get("kind", "")).encode("utf-8")
            ).hexdigest()[:6]
            c["claim_id"] = f"CLM-{family}-{stable}"
        result.append(c)
    return result


# ---------------------------------------------------------------------------
# Method parameter extraction
# ---------------------------------------------------------------------------


def _extract_method_params(node, language: str) -> list[dict[str, Any]]:
    """Extract parameters from a method/function node."""
    params: list[dict[str, Any]] = []
    param_list = (
        child_by_field(node, "parameters")
        or find_child_by_type(node, "formal_parameters")
        or find_child_by_type(node, "parameter_list")
        or find_child_by_type(node, "parameters")
    )
    if param_list is None:
        return params

    if language == "csharp":
        return _extract_csharp_params(param_list)

    if language == "cpp":
        # TC-524: a C++ parameter keeps its full declarator text (references, pointers, const, defaults).
        return _cpp_declarator_params(param_list)

    js_jsdoc: str | None = None
    js_destructured = 0
    ts_destructured = 0
    for ch in param_list.children:
        if language == "typescript" and ch.type in _TS_PARAM_TYPES:
            # TC-523: defaults, optional markers, rest and destructured TypeScript parameters.
            ts_param = _ts_param(ch, node)
            if ts_param is not None:
                ts_pattern = child_by_field(ch, "pattern")
                if ts_pattern is not None and ts_pattern.type in ("object_pattern", "array_pattern"):
                    if ts_destructured:
                        ts_param["name"] = f"{ts_param['name']}{ts_destructured + 1}"
                    ts_destructured += 1
                params.append(ts_param)
            continue
        if language == "javascript" and ch.type in _JS_PATTERN_PARAM_TYPES:
            # TC-521: defaulted, rest and destructured JavaScript parameters.
            if js_jsdoc is None:
                js_jsdoc = _js_function_jsdoc(node)
            js_param = _js_pattern_param(ch, js_jsdoc)
            if js_param is not None:
                if "destructured" in js_param:
                    if js_destructured:
                        js_param["name"] = f"{js_param['name']}{js_destructured + 1}"
                    js_destructured += 1
                params.append(js_param)
            continue
        if ch.type in (
            "formal_parameter",
            "parameter",
            "required_parameter",
            "optional_parameter",
            "typed_parameter",
            "default_parameter",
            "typed_default_parameter",
            "identifier",
            "parameter_declaration",
            "optional_parameter_declaration",
            "variadic_parameter_declaration",
        ):
            pnames: list[str] = []
            ptype = ""

            if language == "go" and ch.type in ("parameter_declaration", "variadic_parameter_declaration"):
                # Go allows comma-separated parameters sharing one declared
                # type in a single parameter_declaration node, e.g.
                # `func OpenWithPassword(path, password string)`. The node's
                # "name" field only ever resolves to one identifier even when
                # several are present, so every name after it silently
                # vanished. Scan every identifier child directly instead.
                pnames = [node_text(sub) for sub in ch.children if sub.type == "identifier"]

            if not pnames:
                name_node = child_by_field(ch, "name")
                if name_node:
                    pnames = [node_text(name_node)]
                elif ch.type == "identifier":
                    pnames = [node_text(ch)]
                else:
                    for sub in ch.children:
                        if sub.type == "identifier":
                            pnames = [node_text(sub)]
                            break

            # C++: parameter_declaration uses a "declarator" field for the name.
            # Handles default-arg forms: std::string x = {}, const T& y = T()
            if not pnames and ch.type == "parameter_declaration":
                decl = child_by_field(ch, "declarator")
                if decl is not None:
                    cur = decl
                    while cur is not None and cur.type != "identifier":
                        inner = child_by_field(cur, "declarator")
                        if inner is not None:
                            cur = inner
                        else:
                            found = None
                            for gc in cur.children:
                                if gc.type == "identifier":
                                    found = gc
                                    break
                            cur = found
                    if cur is not None and cur.type == "identifier":
                        pnames = [node_text(cur)]

            pnames = [p for p in pnames if p not in ("self", "this", "cls")]
            if not pnames:
                continue

            type_node = (
                child_by_field(ch, "type")
                or find_child_by_type(ch, "type_annotation")
                or find_child_by_type(ch, "type_identifier")
                or find_child_by_type(ch, "predefined_type")
            )
            if type_node:
                ptype = node_text(type_node).lstrip(":").strip()

            # TC-SYS-005: Go variadic params â€” preserve ... prefix in type
            if ch.type == "variadic_parameter_declaration" and ptype:
                ptype = f"...{ptype}"
            for pname in pnames:
                params.append({"name": pname, "type": ptype})
    return params


# ---------------------------------------------------------------------------
# TC-521: JavaScript defaulted, rest and destructured parameters
# ---------------------------------------------------------------------------

# Parameter node types the JavaScript grammar uses that the positional list above does not know.
_JS_PATTERN_PARAM_TYPES = ("assignment_pattern", "rest_pattern", "object_pattern")
_JS_DEFAULT_LIMIT = 120
_JS_TYPE_LIMIT = 300
_JS_DEFAULT_PARAM_NAME = "options"


def _js_clip(text: str, limit: int) -> str:
    """*text* with its whitespace collapsed and at most *limit* characters kept."""
    return " ".join(text.split())[:limit]


def _js_function_jsdoc(node) -> str:
    """The raw JSDoc block of a function node; for a function value of an object-literal
    ``pair`` it is the block that precedes the pair."""
    anchor = node
    parent = node.parent
    if (
        node.type in ("arrow_function", "function_expression")
        and parent is not None
        and parent.type == "pair"
    ):
        anchor = parent
    return _ts_raw_jsdoc_comment(anchor)


def _js_balanced(text: str, opening: str, closing: str) -> int:
    """The index of the bracket closing the *opening* that starts *text*, or -1 when unbalanced."""
    depth = 0
    for index, char in enumerate(text):
        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return index
    return -1


def _js_parse_param_tag(chunk: str) -> dict[str, Any] | None:
    """One ``@param {T} [name=default] - text`` tag as ``{type, name, default, optional, doc}``.

    The type braces and the optional-name brackets are matched brace-balanced, so a type such as
    ``{Object.<string, {a: number}>}`` is read whole. ``None`` when the tag is malformed.
    """
    rest = chunk[len("@param") :].lstrip()
    type_text = ""
    optional = False
    if rest.startswith("{"):
        end = _js_balanced(rest, "{", "}")
        if end < 0:
            return None
        type_text = " ".join(rest[1:end].split())
        rest = rest[end + 1 :].lstrip()
    default = ""
    if rest.startswith("["):
        end = _js_balanced(rest, "[", "]")
        if end < 0:
            return None
        name, _, default = rest[1:end].partition("=")
        name, default = name.strip(), default.strip()
        rest = rest[end + 1 :]
        optional = True
    else:
        match = re.match(r"\S+", rest)
        if match is None:
            return None
        name = match.group(0)
        rest = rest[match.end() :]
    if not name:
        return None
    if type_text.endswith("="):
        type_text = type_text[:-1].strip()
        optional = True
    type_text = re.sub(r"\s*\|\s*", " | ", type_text)
    doc = " ".join(rest.split()).lstrip("-").strip()
    return {"type": type_text or "any", "name": name, "default": default, "optional": optional, "doc": doc}


def _js_jsdoc_params(raw: str) -> list[dict[str, Any]]:
    """Every ``@param`` tag of a raw JSDoc block (``/** ... */``), in source order."""
    if not raw.startswith("/**"):
        return []
    body = raw[3:]
    if body.endswith("*/"):
        body = body[:-2]
    text = "\n".join(re.sub(r"^\s*\*\s?", "", line) for line in body.splitlines())
    tags: list[dict[str, Any]] = []
    for chunk in re.split(r"(?m)^\s*(?=@\w)", text):
        chunk = chunk.strip()
        if chunk.startswith("@param") and chunk[len("@param") : len("@param") + 1].isspace():
            tag = _js_parse_param_tag(chunk)
            if tag is not None:
                tags.append(tag)
    return tags


def _js_destructured_param_name(tags: list[dict[str, Any]]) -> str:
    """The name of the object parameter: the first JSDoc ``@param {object} <name>``, else ``options``."""
    for tag in tags:
        name = str(tag["name"])
        if (
            str(tag["type"]).lower() == "object"
            and "." not in name
            and re.fullmatch(r"[A-Za-z_$][\w$]*", name)
        ):
            return name
    return _JS_DEFAULT_PARAM_NAME


def _js_pattern_keys(pattern) -> list[tuple[str, str]]:
    """The ``(key, default text)`` of every key of an ``object_pattern``, in source order.

    Handles ``{ a }``, ``{ a = 1 }``, ``{ a: b }`` and ``{ a: b = 1 }``; a rest element or a
    computed key is not a key. The default is ``""`` when the key has none.
    """
    keys: list[tuple[str, str]] = []
    for ch in pattern.children:
        name = ""
        default = ""
        if ch.type == "shorthand_property_identifier_pattern":
            name = node_text(ch)
        elif ch.type == "object_assignment_pattern":
            left, right = child_by_field(ch, "left"), child_by_field(ch, "right")
            if left is not None:
                name = node_text(left)
            if right is not None:
                default = _js_clip(node_text(right), _JS_DEFAULT_LIMIT)
        elif ch.type == "pair_pattern":
            key, value = child_by_field(ch, "key"), child_by_field(ch, "value")
            if key is not None and key.type in ("property_identifier", "identifier"):
                name = node_text(key)
            if value is not None and value.type == "assignment_pattern":
                right = child_by_field(value, "right")
                if right is not None:
                    default = _js_clip(node_text(right), _JS_DEFAULT_LIMIT)
        if name and all(name != known for known, _ in keys):
            keys.append((name, default))
    return keys


def _js_object_type(items: list[dict[str, Any]]) -> str:
    """The synthesised object type ``{ id?: string; name: string }`` of the destructured keys."""
    if not items:
        return "{}"
    fields = "; ".join(f"{item['name']}{'?' if item['optional'] else ''}: {item['type']}" for item in items)
    text = "{ " + fields + " }"
    return text if len(text) <= _JS_TYPE_LIMIT else text[:_JS_TYPE_LIMIT] + "..."


def _destructured_param(pattern_node, default_node, jsdoc_raw: str) -> dict[str, Any]:
    """One object-typed parameter built from a destructuring *pattern_node* (TC-521).

    Language neutral on purpose: the ``object_pattern`` / ``= {}`` shapes are the same in the
    TypeScript grammar. The name is the first JSDoc ``@param {object} <name>``, else ``options``;
    each key takes its type, default, optionality and description from the pattern and from the
    JSDoc ``@param {T} [name.key=default] - text`` lines (type ``any`` when undocumented). The
    result is the usual ``{"name", "type"}`` plus ``"default"`` when the pattern had a default
    (``{}`` for ``= {}``) and the additive ``"destructured"`` list of
    ``{"name", "type", "default", "optional", "doc"}``.
    """
    tags = _js_jsdoc_params(jsdoc_raw)
    name = _js_destructured_param_name(tags)
    documented: dict[str, dict[str, Any]] = {}
    for tag in tags:
        prefix, dot, key = str(tag["name"]).partition(".")
        if dot and prefix == name and key and "." not in key:
            documented.setdefault(key, tag)
    items: list[dict[str, Any]] = []
    for key, pattern_default in _js_pattern_keys(pattern_node):
        tag = documented.get(key)
        items.append(
            {
                "name": key,
                "type": tag["type"] if tag else "any",
                "default": pattern_default or (tag["default"] if tag else ""),
                "optional": bool(pattern_default) or bool(tag and tag["optional"]),
                "doc": tag["doc"] if tag else "",
            }
        )
    param: dict[str, Any] = {"name": name, "type": _js_object_type(items)}
    if default_node is not None:
        default_text = _js_clip(node_text(default_node), _JS_DEFAULT_LIMIT)
        param["default"] = "{}" if re.fullmatch(r"\{\s*\}", default_text) else default_text
    param["destructured"] = items
    return param


def _js_pattern_param(ch, jsdoc_raw: str) -> dict[str, Any] | None:
    """The parameter dict of a JavaScript ``assignment_pattern`` / ``rest_pattern`` /
    ``object_pattern`` formal parameter, or ``None`` for a shape that is not modelled."""
    if ch.type == "rest_pattern":
        ident = find_child_by_type(ch, "identifier")
        return {"name": "..." + node_text(ident), "type": ""} if ident is not None else None
    if ch.type == "object_pattern":
        return _destructured_param(ch, None, jsdoc_raw)
    left, right = child_by_field(ch, "left"), child_by_field(ch, "right")
    if left is None or right is None:
        return None
    if left.type == "object_pattern":
        return _destructured_param(left, right, jsdoc_raw)
    if left.type == "identifier":
        return {"name": node_text(left), "type": "", "default": _js_clip(node_text(right), _JS_DEFAULT_LIMIT)}
    return None


# ---------------------------------------------------------------------------
# TC-523: TypeScript member marks, type alias values, parameters, @internal members
# ---------------------------------------------------------------------------

# Parameter node types of a TypeScript ``formal_parameters`` list (a rest parameter is a
# ``required_parameter`` whose ``pattern`` is a ``rest_pattern``).
_TS_PARAM_TYPES = ("required_parameter", "optional_parameter")
_TS_PARAM_DEFAULT_LIMIT = 120
_TS_ALIAS_TEXT_LIMIT = 300
_TS_HIDDEN_TAG_RE = re.compile(r"@(internal|hidden|ignore)\b")


def _ts_clip(text: str, limit: int) -> str:
    """*text* on one line (whitespace runs collapsed), at most *limit* characters, ``...`` when cut."""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _ts_member_flags(mnode) -> tuple[bool, bool, bool, bool]:
    """``(readonly, optional, is_static, is_async)`` of a TypeScript/JavaScript member, read from the
    ``readonly``, ``?``, ``static`` and ``async`` token children of its node (a ``property_signature``,
    ``method_signature``, ``public_field_definition`` or ``method_definition``)."""
    readonly = optional = is_static = is_async = False
    for child in mnode.children:
        if child.type == "readonly":
            readonly = True
        elif child.type == "?":
            optional = True
        elif child.type == "static":
            is_static = True
        elif child.type == "async":
            is_async = True
    return readonly, optional, is_static, is_async


def _ts_alias_text(cnode) -> str:
    """The right-hand side of a ``type_alias_declaration`` on one line, cut at ``_TS_ALIAS_TEXT_LIMIT``."""
    value = child_by_field(cnode, "value")
    return _ts_clip(node_text(value), _TS_ALIAS_TEXT_LIMIT) if value is not None else ""


def _ts_is_hidden(node) -> bool:
    """True when the JSDoc block that precedes *node* carries ``@internal``, ``@hidden`` or ``@ignore``
    as a whole tag (a word such as ``@internalized`` inside prose is not one)."""
    return _TS_HIDDEN_TAG_RE.search(_ts_raw_jsdoc_comment(node)) is not None


def _ts_param(ch, fn_node) -> dict[str, Any] | None:
    """The parameter dict of a TypeScript ``required_parameter`` / ``optional_parameter``, or ``None``.

    ``{"name", "type"}`` as ever, plus ``"default"`` (source text, at most 120 characters) only when
    the parameter has one and ``"optional"`` True only for ``x?: T``. A rest parameter is named
    ``...name``; an ``object_pattern`` / ``array_pattern`` goes through ``_destructured_param`` with the
    parameter's own type annotation, when it has one, as its type.
    """
    pattern = child_by_field(ch, "pattern")
    if pattern is None:
        return None
    type_node = child_by_field(ch, "type")
    ptype = node_text(type_node).lstrip(":").strip() if type_node is not None else ""
    value = child_by_field(ch, "value")
    param: dict[str, Any]
    if pattern.type in ("object_pattern", "array_pattern"):
        param = _destructured_param(pattern, value, _js_function_jsdoc(fn_node))
        if ptype:
            param["type"] = " ".join(ptype.split())
        elif pattern.type == "array_pattern":
            param["type"] = ""
        # The per-key list serves the JavaScript constructor-field pass only; a TypeScript parameter is
        # exactly ``name`` / ``type`` (+ ``default`` / ``optional``).
        param.pop("destructured", None)
    else:
        if pattern.type == "rest_pattern":
            ident = find_child_by_type(pattern, "identifier")
            if ident is None:
                return None
            name = "..." + node_text(ident)
        elif pattern.type == "identifier":
            name = node_text(pattern)
        else:
            return None
        if name in ("self", "this", "cls"):
            return None
        param = {"name": name, "type": ptype}
    if value is not None:
        default = _ts_clip(node_text(value), _TS_PARAM_DEFAULT_LIMIT)
        param["default"] = "{}" if re.fullmatch(r"\{\s*\}", default) else default
    if ch.type == "optional_parameter":
        param["optional"] = True
    return param


# C# parameter modifiers kept as a prefix of the parameter's type (TC-396).
_CSHARP_PARAM_MODIFIERS = ("ref", "out", "in", "this", "params")

# C# member modifiers recorded in the additive ``modifiers`` list (TC-396); access modifiers are
# deliberately absent.
_CSHARP_MEMBER_MODIFIERS = (
    "static",
    "abstract",
    "virtual",
    "override",
    "async",
    "sealed",
    "extern",
    "new",
    "readonly",
)


def _extract_csharp_params(param_list) -> list[dict[str, str]]:
    """Extract C# parameters from a ``parameter_list`` / ``bracketed_parameter_list`` node.

    ``parameter`` nodes carry ``type``/``name`` fields, optional ``modifier`` children
    (``ref``/``out``/``in``/``this``) and an optional default after ``=``. A ``params`` array
    parameter is NOT a ``parameter`` node: tree-sitter-c-sharp emits ``params``, the type and the
    name as loose children of the list, so it is reassembled here. Modifiers are kept as a prefix
    of the type (``"params Document[]"``); a default becomes the additive key ``"default"``.
    """
    params: list[dict[str, str]] = []
    in_params = False
    params_type = ""
    for i, ch in enumerate(param_list.children):
        field = param_list.field_name_for_child(i)
        if ch.type == "params":
            in_params = True
            params_type = ""
            continue
        if in_params:
            if field == "type":
                params_type = node_text(ch).strip()
            elif field == "name":
                params.append({"name": node_text(ch), "type": f"params {params_type}".strip()})
                in_params = False
            continue
        if ch.type != "parameter":
            continue
        name_node = child_by_field(ch, "name")
        if name_node is None:
            continue
        type_node = child_by_field(ch, "type")
        ptype = node_text(type_node).lstrip(":").strip() if type_node is not None else ""
        prefix = [
            node_text(sub).strip()
            for sub in ch.children
            if sub.type == "modifier" and node_text(sub).strip() in _CSHARP_PARAM_MODIFIERS
        ]
        if prefix:
            ptype = " ".join([*prefix, ptype]).strip()
        entry: dict[str, str] = {"name": node_text(name_node), "type": ptype}
        seen_equals = False
        for sub in ch.children:
            if sub.type == "=":
                seen_equals = True
            elif seen_equals:
                entry["default"] = node_text(sub).strip()
                break
        params.append(entry)
    return params


# ---------------------------------------------------------------------------
# Return type extraction
# ---------------------------------------------------------------------------


def _extract_return_type(node, language: str) -> str:
    """Extract the return type from a method/function node."""
    if language == "csharp":
        # tree-sitter-c-sharp names a method's return type field ``returns`` (TC-396); a
        # constructor/destructor has none and keeps "".
        returns = child_by_field(node, "returns")
        if returns is not None:
            return node_text(returns).strip()
    rt = child_by_field(node, "return_type")
    if rt:
        txt = node_text(rt).lstrip(":").strip()
        if txt.startswith("->"):
            txt = txt[2:].strip()
        return txt

    # type annotation after parameters (TS/Python)
    ta = find_child_by_type(node, "type_annotation")
    if ta:
        return node_text(ta).lstrip(":").strip()

    # C#/Java/C++: type before method name
    if language in ("csharp", "java", "cpp"):
        type_node = child_by_field(node, "type")
        if type_node:
            return node_text(type_node)

    # Go: use "result" field for method_declaration / function_declaration
    if language == "go":
        result = child_by_field(node, "result")
        if result:
            return node_text(result)
    return ""


def _csharp_modifiers(node) -> list[str]:
    """The C# member modifiers worth recording, in source order (TC-396).

    Only ``static``, ``abstract``, ``virtual``, ``override``, ``async``, ``sealed``, ``extern``,
    ``new`` and ``readonly``; access modifiers are never included.
    """
    found: list[str] = []
    for ch in node.children:
        if ch.type == "modifier":
            text = node_text(ch).strip()
            if text in _CSHARP_MEMBER_MODIFIERS:
                found.append(text)
    return found


def _csharp_indexer_is_public(inode) -> bool:
    """True for a ``public`` indexer, or any indexer declared directly inside an interface."""
    for ch in inode.children:
        if ch.type == "modifier" and node_text(ch).strip() == "public":
            return True
    parent = inode.parent
    return (
        parent is not None
        and parent.type == "declaration_list"
        and parent.parent is not None
        and parent.parent.type == "interface_declaration"
    )


def _extract_csharp_indexers(
    cnode, class_types: set[str], cname: str, rel: str, family: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract the public ``indexer_declaration`` members of a C# type as PROPERTY entries (TC-396).

    An indexer is named ``this[<parameter types, comma-separated>]``; it is writable when it has a
    ``set``/``init`` accessor; its bracketed parameters are kept under the additive ``"params"`` key.
    """
    props: list[dict[str, Any]] = []
    claims: list[dict[str, Any]] = []
    for inode in collect_nodes(cnode, {"indexer_declaration"}):
        if _in_excluded_preproc_branch(inode, "csharp") or not _csharp_indexer_is_public(inode):
            continue
        parent = inode.parent
        nested = False
        while parent is not None and parent != cnode:
            if parent.type in class_types:
                nested = True
                break
            parent = parent.parent
        if nested:
            continue
        params = _extract_method_params(inode, "csharp")
        name = f"this[{', '.join(p.get('type', '') for p in params)}]"
        type_node = child_by_field(inode, "type")
        ptype = node_text(type_node).strip() if type_node is not None else ""
        writable = False
        acc_list = find_child_by_type(inode, "accessor_list")
        if acc_list is not None:
            for acc in acc_list.children:
                if acc.type == "accessor_declaration" and node_text(acc).lstrip().startswith(("set", "init")):
                    writable = True
                    break
        entry: dict[str, Any] = {
            "name": name,
            "type": ptype,
            "doc": _extract_doc_comment(inode, "csharp"),
            "line": inode.start_point[0] + 1,
            "writable": writable,
            "params": params,
        }
        modifiers = _csharp_modifiers(inode)
        if modifiers:
            entry["modifiers"] = modifiers
        props.append(entry)
        claims.append(
            _make_claim(
                family,
                "api_method",
                f"{cname}.{name} property of type {ptype}",
                rel,
                inode.start_point[0] + 1,
            )
        )
    return props, claims


def _extract_go_receiver_type(fnode) -> str:
    """Extract the receiver type name from a Go method_declaration node.

    Returns the bare type name (without pointer prefix) or "" if none.
    E.g. for ``func (d *Document) Save(...)``, returns ``"Document"``.
    """
    if fnode.type != "method_declaration":
        return ""
    # The receiver is the first parameter_list child of the method node.
    # tree-sitter-go field name is "receiver".
    recv_list = child_by_field(fnode, "receiver")
    if recv_list is None:
        # Fallback: first parameter_list child
        for ch in fnode.children:
            if ch.type == "parameter_list":
                recv_list = ch
                break
    if recv_list is None:
        return ""
    for ch in recv_list.children:
        if ch.type == "parameter_declaration":
            for pch in ch.children:
                if pch.type == "pointer_type":
                    for ppch in pch.children:
                        if ppch.type == "type_identifier":
                            return node_text(ppch)
                elif pch.type == "type_identifier":
                    return node_text(pch)
    return ""


# ---------------------------------------------------------------------------
# Doc comment extraction
# ---------------------------------------------------------------------------


def _first_sentence(text: str) -> str:
    """Return the first sentence (up to first period-space or newline)."""
    text = text.strip()
    m = re.match(r"^(.+?[.!?])(?:\s|$)", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # no sentence-ending punctuation; return first line
    return text.split("\n")[0].strip()[:200]


def _extract_rustdoc_comment(node) -> str:
    """Extract the first sentence of a Rust `///` doc comment preceding *node*.

    Rust doc comments are per-line ``line_comment`` nodes preceding the item,
    optionally separated from it by ``attribute_item`` nodes
    (``#[derive(...)]``, ``#[doc = "..."]``). Walk back over attributes first
    (collecting any ``#[doc = "..."]`` text as a fallback doc source), then
    gather the contiguous run of ``///`` line-comment nodes using the same
    line-adjacency rule as the godoc/xml_doc extractors. ``//!`` inner docs
    document the *enclosing* module, never the following item, and are
    excluded by the ``///`` prefix check.
    """
    attr_doc_parts: list[str] = []
    cur = node.prev_named_sibling
    while cur is not None and cur.type == "attribute_item":
        m = re.search(r'#\s*\[\s*doc\s*=\s*"([^"]*)"\s*\]', node_text(cur))
        if m:
            attr_doc_parts.insert(0, m.group(1))
        cur = cur.prev_named_sibling

    lines_with_row: list[tuple[int, str]] = []
    while cur is not None and cur.type == "line_comment":
        cur_txt = node_text(cur)
        if not cur_txt.lstrip().startswith("///"):
            break
        cur_row = cur.start_point[0]
        if lines_with_row and lines_with_row[0][0] - cur_row > 1:
            break  # line gap â€” belongs to a different comment block
        lines_with_row.insert(0, (cur_row, cur_txt))
        cur = cur.prev_named_sibling

    para_lines: list[str] = []
    for _, ln in lines_with_row:
        bare = ln.strip()
        if bare.startswith("///"):
            bare = bare[3:]
        bare = bare.strip()
        if not bare and para_lines:
            break  # blank `///` line ends the summary paragraph
        if bare:
            para_lines.append(bare)

    cleaned = " ".join(para_lines) or " ".join(attr_doc_parts)
    if cleaned:
        return _first_sentence(cleaned)
    return ""


_STRING_PREFIX_RE = re.compile(r'^[a-zA-Z]{1,2}(?=["\'])')


def _strip_string_prefix_and_quotes(text: str) -> str:
    """Strip a Python string-literal prefix (r/R/u/U, b/f combinations) and
    surrounding quote/whitespace characters from raw docstring source text.

    RC-tex-python-04 (MT037-2026-08-24-FOSS-PORTFOLIO-FORENSIC-HEAL): plain
    .strip("\\"' \\n\\r") cannot remove a leading raw-string prefix (e.g.
    r\"\"\"...\"\"\") because the prefix character itself is not in the strip
    set, so it blocks .strip() from ever reaching the quote marks that
    follow -- the prefix and all quote characters then leak into the
    extracted doc field verbatim and flow into every downstream consumer.
    """
    match = _STRING_PREFIX_RE.match(text)
    if match:
        text = text[match.end() :]
    return text.strip("\"' \n\r")


def _extract_doc_comment(node, language: str) -> str:
    """Extract the first sentence of a doc comment preceding *node*.

    TC-MT040-42: redirects *node* through extraction/lang/<language>.py's
    doc_anchor() first -- the seam a language's adapter uses when its
    grammar wraps an exported declaration in a container node whose own
    prev_named_sibling doesn't hold the doc comment (today this only
    changes behavior for Go's `type_spec` -> parent `type_declaration`
    case, SFX-2; every other language's doc_anchor is the identity
    function, so this redirection is a no-op for them).
    """
    # TC-521: ``lang.LANGUAGES`` has no "javascript" key (only "typescript"), so an ``export class``
    # in a JavaScript tree never reached the redirect to its ``export_statement`` and lost its
    # description. JavaScript uses the TypeScript adapter (the grammars wrap exports the same way).
    lang_mod = lang.get("typescript" if language == "javascript" else language)
    if lang_mod is not None:
        node = lang_mod.doc_anchor(node)
    style = _DOC_COMMENT_STYLES.get(language, "javadoc")

    if style == "rustdoc":
        return _extract_rustdoc_comment(node)

    if style == "docstring" and language == "python":
        body = child_by_field(node, "body") or find_child_by_type(node, "block")
        if body:
            for child in body.children:
                if not child.is_named:
                    continue  # skip anonymous tokens (newlines, indent, etc.)
                if child.type == "expression_statement":
                    # Some tree-sitter-python versions wrap the docstring
                    if child.children:
                        s = child.children[0]
                        if s.type in ("string", "concatenated_string"):
                            raw = _strip_string_prefix_and_quotes(node_text(s))
                            return _first_sentence(raw)
                    break  # first named statement isn't a docstring
                elif child.type in ("string", "concatenated_string"):
                    # Other versions expose the docstring as a bare string node
                    raw = _strip_string_prefix_and_quotes(node_text(child))
                    return _first_sentence(raw)
                else:
                    break  # first named child is not a docstring
        return ""

    # Look at previous sibling for comment
    prev = node.prev_named_sibling
    if prev is None:
        prev = node.prev_sibling
    if prev is None:
        return ""

    txt = node_text(prev)
    # TC-524: a C++ ``///`` comment (including ``/// <summary>`` blocks) documents like a C# XML doc comment;
    # ``///<`` is a trailing comment on the member before, never a doc of the next one.
    if language == "cpp" and txt.strip().startswith("///"):
        if txt.strip().startswith("///<"):
            return ""
        style = "xml_doc"
    if style == "javadoc" or style == "jsdoc":
        if txt.startswith("/**"):
            cleaned = re.sub(r"/\*\*|\*/|\n\s*\*\s?", " ", txt).strip()
            return _first_sentence(cleaned)
    elif style == "xml_doc":
        if txt.strip().startswith("///"):
            # C# XML doc comments are per-line nodes in tree-sitter-c-sharp.
            # Walk back through consecutive adjacent-line comment nodes to
            # collect the full doc block (same pattern as godoc below).
            xml_lines_with_row: list[tuple[int, str]] = [(prev.start_point[0], txt)]
            cur = prev.prev_named_sibling
            while cur is not None and cur.type == "comment":
                cur_txt = node_text(cur)
                if not cur_txt.strip().startswith("///"):
                    break  # not an XML doc comment line
                cur_row = cur.start_point[0]
                last_row = xml_lines_with_row[0][0]
                if last_row - cur_row > 1:
                    break  # line gap â€” belongs to a different comment block
                xml_lines_with_row.insert(0, (cur_row, cur_txt))
                cur = cur.prev_named_sibling
            # Verify the comment block is immediately above the node
            # (last comment line must be on the line before the declaration).
            last_comment_row = xml_lines_with_row[-1][0]
            if node.start_point[0] - last_comment_row > 1:
                return ""  # gap between comment and declaration â€” not our doc
            xml_comment_lines = [ln for _, ln in xml_lines_with_row]
            cleaned = " ".join(l.lstrip("/ ").strip() for l in xml_comment_lines)
            # TC-SYS-007: preserve names from <paramref>, <typeparamref>,
            # and <see> tags before stripping remaining XML markup.
            cleaned = re.sub(r'<(?:paramref|typeparamref)\s+name="([^"]+)"\s*/?>', r"\1", cleaned)
            cleaned = re.sub(r'<see\s+cref="([^"]+)"\s*/?>', lambda m: m.group(1).rsplit(".", 1)[-1], cleaned)
            # strip remaining XML tags
            cleaned = re.sub(r"<[^>]+>", "", cleaned).strip()
            return _first_sentence(cleaned)
    elif style == "godoc":
        if txt.strip().startswith("//"):
            # Go doc comments are per-line nodes in tree-sitter-go.
            # Walk back through consecutive adjacent-line comment nodes to collect
            # the full doc block (stop at a gap indicating a different comment block).
            lines_with_row: list[tuple[int, str]] = [(prev.start_point[0], txt)]
            cur = prev.prev_named_sibling
            while cur is not None and cur.type == "comment":
                cur_row = cur.start_point[0]
                last_row = lines_with_row[0][0]
                if last_row - cur_row > 1:
                    break  # line gap â€” belongs to a different comment block
                lines_with_row.insert(0, (cur_row, node_text(cur)))
                cur = cur.prev_named_sibling
            comment_lines = [ln for _, ln in lines_with_row]
            # Extract the summary paragraph: lines before the first blank `//`
            para_lines: list[str] = []
            for ln in comment_lines:
                bare = ln.strip().lstrip("/").strip()
                if not bare:  # blank `//` line ends the summary paragraph
                    break
                para_lines.append(bare)
            cleaned = " ".join(para_lines)
            if cleaned:
                return _first_sentence(cleaned)

    return ""


# ---------------------------------------------------------------------------
# TC-MT040-12: TSDoc block-tag enrichment (additive -- see
# lang/typescript.py's parse_doc() for the actual tag parser)
# ---------------------------------------------------------------------------


def _ts_raw_jsdoc_comment(node) -> str:
    """Return the raw JSDoc comment text (including ``/**``/``*/``
    delimiters) immediately preceding *node*, after applying TypeScript's
    doc_anchor() redirection -- the same anchor resolution
    _extract_doc_comment uses, but returning the full raw block instead of
    just a first-sentence summary, for lang.typescript.parse_doc()'s richer
    TSDoc block-tag parsing. Returns "" when no such comment is found.
    """
    anchored = lang.typescript.doc_anchor(node)
    prev = anchored.prev_named_sibling
    if prev is None:
        prev = anchored.prev_sibling
    if prev is None:
        return ""
    txt = node_text(prev)
    return txt if txt.startswith("/**") else ""


def _is_real_typescript_property_signature(node) -> bool:
    """TC-051: True iff a TypeScript ``property_signature`` node is a genuine
    direct member of an ``interface_declaration`` or ``type_alias_declaration``
    body -- never a property key of an INLINE object-type literal used as a
    parameter/variable/return-type annotation elsewhere.

    ``property_signature`` is in ``_FUNC_TYPES["typescript"]`` (tree_helpers.py)
    because genuine interface/type-alias members legitimately need to be
    collectible that way. But the top-level-function scan in this module
    (``collect_nodes(root, func_types)``) is an UNSCOPED whole-file search that
    only excludes a match whose ancestor chain reaches a ``class_types`` node
    (``interface_declaration``/``type_alias_declaration``) -- an inline
    object-type literal used as e.g. a parameter's type annotation
    (``function f(w: { A: number })``) has an ancestor chain of
    ``property_signature -> object_type -> type_annotation ->
    required_parameter -> formal_parameters -> function_declaration`` that
    never reaches either, so its property keys ('A', ...) leaked through as
    fabricated top-level "function" entries. Confirmed by direct reproduction
    against the real pdf/typescript repository (src/metrics.ts:81 and others).

    Verified via a live tree-sitter-typescript parse probe (this project's
    established discipline for exactly this kind of grammar ambiguity, per
    the TC-MT040-10 precedent elsewhere in this file/tree_helpers.py):
      - Genuine interface member (``interface Foo { bar: number }``):
        property_signature's DIRECT parent is ``interface_body``.
      - Genuine type-alias member (``type Baz = { qux: string }``):
        property_signature's DIRECT parent is ``object_type``, and THAT
        object_type's own direct parent is ``type_alias_declaration``.
      - Inline object-type literal anywhere else (a parameter's type
        annotation, a variable's type annotation, a function's return-type
        annotation, or nested inside another inline object-type literal):
        property_signature's DIRECT parent is also ``object_type``, but that
        object_type's own parent is ``type_annotation`` (or another
        property_signature's type_annotation when nested deeper) -- never
        ``type_alias_declaration``. Checking only the immediate object_type's
        own parent (not walking further up to a function/variable ancestor)
        is exactly what distinguishes the two cases, and correctly narrows
        even for a type-alias member that itself has an inline nested object
        type (only the outer property_signature is genuine; the nested one
        is correctly excluded here too, matching its true role).
    """
    parent = node.parent
    if parent is None:
        return False
    if parent.type == "interface_body":
        return True
    if parent.type == "object_type":
        grandparent = parent.parent
        return grandparent is not None and grandparent.type == "type_alias_declaration"
    return False


def _apply_ts_tsdoc_enrichment(entry: dict, doc_node) -> None:
    """TC-MT040-12: additive TSDoc enrichment for a TypeScript/JavaScript
    method/function/module-constant entry.

    Populates each existing params[] item's "doc" (matched by name, after
    stripping a leading rest-param "..." marker and/or leading "_"), plus
    "return_doc"/"remarks"/"doc_examples" on *entry* itself, whenever
    lang.typescript.parse_doc()'s richer TSDoc block-tag parser finds them.
    Purely additive: never touches *entry*'s existing "doc" field (still
    exactly what _extract_doc_comment computed) or removes/renames any
    existing key. No-op when there is no JSDoc comment to parse.
    """
    raw = _ts_raw_jsdoc_comment(doc_node)
    if not raw:
        return
    parsed = lang.typescript.parse_doc(raw)

    tag_params = parsed.get("params")
    if tag_params:
        for p in entry.get("params", []):
            lookup = p.get("name", "")
            if lookup.startswith("..."):
                lookup = lookup[3:]
            lookup = lookup.lstrip("_")
            if lookup in tag_params and tag_params[lookup]:
                p["doc"] = tag_params[lookup]

    if parsed.get("returns"):
        entry["return_doc"] = parsed["returns"]
    if parsed.get("remarks"):
        entry["remarks"] = parsed["remarks"]
    if parsed.get("examples"):
        entry["doc_examples"] = parsed["examples"]


# ---------------------------------------------------------------------------
# Python property extraction
# ---------------------------------------------------------------------------


def _all_decorator_text(fn) -> str:
    """Return the combined text of every decorator attached to *fn*.

    A Python function can carry multiple stacked decorators, e.g.::

        @property
        @abstractmethod
        def font_name(self) -> str: ...

    Checking only the single nearest decorator (a direct child of *fn*, or
    its one immediate previous sibling) misses this: ``@abstractmethod`` is
    the nearest sibling to the function, so a check for ``@property`` alone
    against that one node never finds it. This walks the full run of
    consecutive ``decorator`` nodes â€” as direct children of *fn* (some
    tree-sitter grammars attach them there) and as consecutive preceding
    siblings (the more common form) â€” and returns their concatenated text
    so callers can search across the whole stack, not just one layer of it.

    Root cause of ST-014 (font/python truth audit, 2026-07-24): 56 of 57
    FAILs traced to this single-decorator blind spot classifying stacked
    ``@property @abstractmethod`` members as plain callable methods.
    """
    parts = [node_text(d) for d in find_children_by_type(fn, "decorator")]

    node = fn.prev_named_sibling
    while node is not None and node.type == "decorator":
        parts.append(node_text(node))
        node = node.prev_named_sibling

    return " ".join(parts)


def _extract_python_properties(
    cnode,
    cname: str,
    rel: str,
    family: str,
) -> tuple[list[dict[str, Any]], list[dict]]:
    """Extract @property decorated methods in Python classes.

    Returns (properties, claims).
    """
    properties: list[dict[str, Any]] = []
    claims: list[dict] = []

    func_nodes = collect_nodes(cnode, {"function_definition"})

    # TC-SYS-019: pre-scan for @name.setter decorators to detect writable properties.
    setter_names: set[str] = set()
    for fn in func_nodes:
        dec_text = _all_decorator_text(fn)
        # Match @prop_name.setter patterns
        if ".setter" in dec_text:
            fn_name = _node_name(fn, "python")
            if fn_name:
                setter_names.add(fn_name)

    for fn in func_nodes:
        # check for @property anywhere in the decorator stack (e.g. stacked
        # with @abstractmethod â€” see _all_decorator_text)
        dec_text = _all_decorator_text(fn)
        if "@property" not in dec_text:
            continue
        pname = _node_name(fn, "python")
        if not pname or pname.startswith("_"):
            continue
        ret = _extract_return_type(fn, "python")
        properties.append(
            {
                "name": pname,
                "type": ret,
                "writable": pname in setter_names,
                "doc": _extract_doc_comment(fn, "python"),
                "line": fn.start_point[0] + 1,
            }
        )
        claims.append(
            _make_claim(
                family, "api_method", f"{cname}.{pname} property of type {ret}", rel, fn.start_point[0] + 1
            )
        )

    return properties, claims


# ---------------------------------------------------------------------------
# Python annotated field extraction (dataclass fields, TypedDict members, etc.)
# ---------------------------------------------------------------------------


def _extract_python_annotated_fields(
    cnode,
    cname: str,
    rel: str,
    family: str,
) -> tuple[list[dict[str, Any]], list[dict]]:
    """Extract annotated class-level field declarations from Python classes.

    Captures plain annotated assignments in the class body such as::

        name: str
        count: int = 0
        items: list[str] = field(default_factory=list)

    These patterns are common in ``@dataclass``, ``TypedDict``, ``NamedTuple``,
    and similar declarations.  The function inspects only **direct children**
    of the class body block so that assignments inside method bodies are never
    collected by accident.

    Returns (fields, claims).
    """
    fields: list[dict[str, Any]] = []
    claims: list[dict] = []

    body = child_by_field(cnode, "body") or find_child_by_type(cnode, "block")
    if body is None:
        return fields, claims

    # TC-SYS-019: detect @dataclass decorator on the class â€” if present and not
    # frozen=True, all annotated fields are writable by default.
    is_dataclass = False
    is_frozen = False
    dec = find_child_by_type(cnode, "decorator")
    if dec is None:
        prev = cnode.prev_named_sibling
        if prev and prev.type == "decorator":
            dec = prev
    if dec is not None:
        dec_text = node_text(dec)
        if "dataclass" in dec_text:
            is_dataclass = True
            if "frozen=True" in dec_text or "frozen = True" in dec_text:
                is_frozen = True

    for stmt in body.children:
        if stmt.type != "assignment":
            continue

        # Annotated assignments carry a named "type" child (the annotation after
        # the colon).  Plain assignments  (x = 5)  have no such child.
        type_node = child_by_field(stmt, "type")
        if type_node is None:
            continue

        # Field name comes from the first identifier child of the assignment.
        fname = ""
        for ch in stmt.children:
            if ch.type == "identifier":
                fname = node_text(ch)
                break
        if not fname or fname.startswith("_"):
            continue

        ftype = node_text(type_node).strip()

        # ClassVar annotations mark class-level variables, not instance fields.
        if ftype.startswith("ClassVar"):
            continue

        fields.append(
            {
                "name": fname,
                "type": ftype,
                "writable": is_dataclass and not is_frozen,
                "doc": "",
                "line": stmt.start_point[0] + 1,
            }
        )
        claims.append(
            _make_claim(
                family, "api_method", f"{cname}.{fname} field of type {ftype}", rel, stmt.start_point[0] + 1
            )
        )

    return fields, claims


# ---------------------------------------------------------------------------
# Python __init__ instance attribute extraction
# ---------------------------------------------------------------------------


def _extract_python_init_attributes(
    cnode,
    cname: str,
    rel: str,
    family: str,
    existing_names: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict]]:
    """Extract instance attributes assigned in ``__init__`` from Python classes.

    Captures patterns like::

        def __init__(self):
            self.jpeg_quality: int = 100
            self.compliance = PdfCompliance.PDF17

    Only public attributes (not starting with ``_``) are captured.
    Attributes whose names already appear in *existing_names* (from
    ``@property`` or class-level fields) are skipped to avoid duplication.

    Returns (properties, claims).
    """
    props: list[dict[str, Any]] = []
    claims: list[dict] = []
    seen = set(existing_names or ())

    body = child_by_field(cnode, "body") or find_child_by_type(cnode, "block")
    if body is None:
        return props, claims

    # Find the __init__ method
    init_fn = None
    for child in body.children:
        if child.type == "function_definition":
            fn_name = _node_name(child, "python")
            if fn_name == "__init__":
                init_fn = child
                break
    if init_fn is None:
        return props, claims

    init_body = child_by_field(init_fn, "body") or find_child_by_type(init_fn, "block")
    if init_body is None:
        return props, claims

    # Walk all statements (including nested in if/else/try blocks)
    stmts = collect_nodes(init_body, {"assignment", "expression_statement"})
    for stmt in stmts:
        # Handle expression_statement wrapping an assignment
        target = stmt
        if stmt.type == "expression_statement":
            inner = find_child_by_type(stmt, "assignment")
            if inner is None:
                continue
            target = inner

        if target.type != "assignment":
            continue

        # Left side must be an attribute access on self: self.xxx
        left = child_by_field(target, "left")
        if left is None:
            # For some tree-sitter representations, iterate children
            for ch in target.children:
                if ch.type == "attribute":
                    left = ch
                    break
            if left is None:
                continue

        if left.type != "attribute":
            continue

        # Object must be "self"
        obj = child_by_field(left, "object")
        if obj is None or node_text(obj) != "self":
            continue

        # Attribute name
        attr_node = child_by_field(left, "attribute")
        if attr_node is None:
            continue
        attr_name = node_text(attr_node)

        # Skip private/protected attributes â€” underscore-prefixed backing fields
        # must not appear in the public API surface even when a matching
        # @property exists (private fields are implementation detail, not API).
        if attr_name.startswith("_"):
            continue

        # Skip duplicates (already found via @property or class-level annotation)
        if attr_name in seen:
            continue
        seen.add(attr_name)

        # Try to get type annotation
        type_node = child_by_field(target, "type")
        attr_type = node_text(type_node).strip() if type_node else ""

        props.append(
            {
                "name": attr_name,
                "type": attr_type,
                "doc": "",
                "line": target.start_point[0] + 1,
            }
        )
        claims.append(
            _make_claim(
                family,
                "api_method",
                f"{cname}.{attr_name} field of type {attr_type or 'unknown'}",
                rel,
                target.start_point[0] + 1,
            )
        )

    return props, claims


# ---------------------------------------------------------------------------
# Java property synthesis
# ---------------------------------------------------------------------------

_JAVA_OBJECT_METHODS: frozenset[str] = frozenset(
    {
        "getClass",
        "hashCode",
        "equals",
        "toString",
        "notify",
        "notifyAll",
        "wait",
        "clone",
        "finalize",
    }
)


def _synthesize_java_properties(
    methods: list[dict[str, Any]],
    cname: str,
    rel: str,
    family: str,
) -> tuple[list[dict[str, Any]], list[dict]]:
    """Synthesize property entries from Java getter/setter method pairs.

    Returns (properties, claims).
    """
    properties: list[dict[str, Any]] = []
    claims: list[dict] = []

    getters: dict[str, dict[str, Any]] = {}
    for m in methods:
        name = m["name"]
        if name in _JAVA_OBJECT_METHODS:
            continue
        params = m.get("params", [])
        if name.startswith("get") and len(name) > 3 and name[3].isupper() and len(params) == 0:
            prop_name = name[3].lower() + name[4:]
            getters[prop_name] = m
        elif name.startswith("is") and len(name) > 2 and name[2].isupper() and len(params) == 0:
            prop_name = name[2].lower() + name[3:]
            getters[prop_name] = m

    for prop_name, getter in getters.items():
        ptype = getter.get("return_type", "")
        properties.append(
            {
                "name": prop_name,
                "type": ptype,
                "doc": getter.get("doc", ""),
                "line": getter["line"],
            }
        )
        claims.append(
            _make_claim(
                family, "api_method", f"{cname}.{prop_name} property of type {ptype}", rel, getter["line"]
            )
        )

    return properties, claims


# ---------------------------------------------------------------------------
# C# / Java const and static-final field extraction (Track A-1)
# ---------------------------------------------------------------------------


def _extract_const_fields(
    cnode,
    cname: str,
    rel: str,
    family: str,
    language: str,
) -> tuple[list[dict[str, Any]], list[dict]]:
    """Extract public const/static-readonly (C#) and public static final (Java) fields.

    These are accessed in content like properties (``CfbConstants.RootStreamId``)
    but are field_declaration nodes, not method_declaration or property_declaration
    nodes, so they are missed by the main extraction pass.

    Returns (properties, claims).
    """
    fields: list[dict[str, Any]] = []
    claims: list[dict] = []

    # C# class body is a declaration_list; Java class body is a class_body
    body = find_child_by_type(cnode, "declaration_list") or find_child_by_type(cnode, "class_body")
    if body is None:
        return fields, claims

    for member in body.children:
        if member.type != "field_declaration":
            continue

        # Gather ALL modifier texts (C# has one `modifier` node per keyword)
        mod_parts: list[str] = []
        var_decl_node = None
        for ch in member.children:
            if ch.type in ("modifiers", "modifier"):
                mod_parts.append(node_text(ch))
            elif ch.type == "variable_declaration":
                var_decl_node = ch
        mods = " ".join(mod_parts)

        if "public" not in mods:
            continue

        # C#: must have `const` or `readonly` (catches `static readonly`)
        if language == "csharp" and "const" not in mods and "readonly" not in mods:
            continue
        # Java: must have `final` (catches `static final`)
        if language == "java" and "final" not in mods:
            continue

        # Extract field type â€” C# puts it inside variable_declaration
        ftype = ""
        if var_decl_node is not None:
            type_node = child_by_field(var_decl_node, "type")
            if type_node is None:
                # fallback: first non-declarator child
                for sub in var_decl_node.children:
                    if sub.type not in ("variable_declarator", ",", ";"):
                        ftype = node_text(sub)
                        break
            else:
                ftype = node_text(type_node)
        else:
            type_node = child_by_field(member, "type")
            if type_node:
                ftype = node_text(type_node)

        # Find variable_declarator nodes â€” C# nests them inside variable_declaration
        declarator_parent = var_decl_node if var_decl_node is not None else member
        for ch in declarator_parent.children:
            if ch.type == "variable_declarator":
                fname_node = child_by_field(ch, "name")
                fname = node_text(fname_node) if fname_node else ""
                if not fname:
                    # fallback: first identifier in the declarator
                    for sub in ch.children:
                        if sub.type == "identifier":
                            fname = node_text(sub)
                            break
                if not fname:
                    continue

                kind = "constant" if "const" in mods else "static_field"
                fields.append(
                    {
                        "name": fname,
                        "type": ftype,
                        "kind": kind,
                        "doc": _extract_doc_comment(member, language),
                        "line": member.start_point[0] + 1,
                        "writable": False,
                    }
                )
                claims.append(
                    _make_claim(
                        family,
                        "api_field",
                        f"{cname}.{fname} {kind} of type {ftype}",
                        rel,
                        member.start_point[0] + 1,
                    )
                )

    return fields, claims


# ---------------------------------------------------------------------------
# TypeScript module-level `export const` extraction (TC-MT040-13)
# ---------------------------------------------------------------------------

_TS_LITERALISH_TYPES: frozenset[str] = frozenset(
    {
        "string",
        "number",
        "true",
        "false",
        "null",
        "template_string",
        "array",
        "object",
    }
)


def _infer_ts_literal_type(value_node) -> str:
    """Best-effort type inference from a TS initializer literal's own node
    shape, used only when no explicit type annotation is present.
    """
    if value_node is None:
        return ""
    t = value_node.type
    if t in ("string", "template_string"):
        return "string"
    if t == "number":
        return "number"
    if t in ("true", "false"):
        return "boolean"
    if t == "array":
        return "array"
    if t == "object":
        return "object"
    if t == "new_expression":
        ctor = child_by_field(value_node, "constructor")
        return node_text(ctor) if ctor is not None else ""
    if t == "unary_expression":
        # e.g. `-1` -- a unary_expression wrapping a number literal.
        arg = child_by_field(value_node, "argument")
        if arg is not None:
            return _infer_ts_literal_type(arg)
    return ""


def _extract_ts_module_constants(
    root,
    rel: str,
    fpath: Path,
    family: str,
    compute_reachable,
) -> tuple[list[dict[str, Any]], list[dict]]:
    """Scan a TypeScript source file's top-level `export const` declarations
    and emit module-level entries for them.

    Root cause this closes: class-member fields (public_field_definition)
    and top-level func_types nodes (function_declaration, arrow_function,
    ...) were already scanned, but a bare module-level ``export const
    MAX_PAGE_SIZE = 100;`` is neither a class nor a class member nor (from
    the top-level-function loop's perspective) a func_types node --
    lexical_declaration was never collected at all, so every such constant
    was invisible to extraction entirely regardless of how public/documented
    it was.

    Only directly-exported (`export const ...`) top-level `const` bindings
    are in scope: `let`/`var` (mutable, not constant) and non-exported
    top-level `const` (never reachable from outside the module) are both
    skipped. A destructuring-pattern declarator (`export const { a, b } =
    obj;`) is skipped too -- out of scope, no single meaningful name to
    attach a claim to.

    An arrow-function or plain `function` expression initializer is emitted
    as a `kind: "function"` entry instead of `kind: "constant"` (reusing
    _extract_method_params/_extract_return_type directly -- arrow functions
    carry the same `parameters`/`return_type` field shape as a regular
    function declaration in this grammar, verified via parse probe).

    Returns (entries, claims) to be extended onto the caller's own
    classes/claims lists.
    """
    entries: list[dict[str, Any]] = []
    claims: list[dict] = []

    for stmt in root.children:
        if stmt.type != "export_statement":
            continue
        decl = child_by_field(stmt, "declaration")
        if decl is None or decl.type != "lexical_declaration":
            continue
        kind_node = child_by_field(decl, "kind")
        if kind_node is None or node_text(kind_node) != "const":
            continue  # skip `let`/`var` -- mutable, out of scope

        doc = _extract_doc_comment(decl, "typescript")

        for vd in find_children_by_type(decl, "variable_declarator"):
            name_node = child_by_field(vd, "name")
            if name_node is None or name_node.type != "identifier":
                continue  # destructuring pattern -- out of scope
            cname = node_text(name_node)
            if not cname or cname.startswith("_"):
                continue
            value_node = child_by_field(vd, "value")

            if value_node is not None and value_node.type in (
                "arrow_function",
                "function_expression",
            ):
                params = _extract_method_params(value_node, "typescript")
                ret = _extract_return_type(value_node, "typescript")
                func_entry: dict[str, Any] = {
                    "name": cname,
                    "kind": "function",
                    "doc": doc,
                    "file": rel,
                    "line": vd.start_point[0] + 1,
                    "bases": [],
                    "methods": [],
                    "properties": [],
                    "params": params,
                    "return_type": ret,
                    "reachable": compute_reachable(cname, fpath, rel),
                }
                _apply_ts_tsdoc_enrichment(func_entry, decl)
                entries.append(func_entry)
                claims.append(
                    _make_claim(
                        family,
                        "api_method",
                        f"Function {cname}({', '.join(p['name'] for p in params)}) -> {ret}",
                        rel,
                        vd.start_point[0] + 1,
                    )
                )
                continue

            type_node = child_by_field(vd, "type")
            ctype = (
                node_text(type_node).lstrip(":").strip()
                if type_node is not None
                else _infer_ts_literal_type(value_node)
            )
            cvalue = node_text(value_node)[:80] if value_node is not None else ""

            const_entry: dict[str, Any] = {
                "name": cname,
                "kind": "constant",
                "doc": doc,
                "file": rel,
                "line": vd.start_point[0] + 1,
                "type": ctype,
                "value": cvalue,
                "bases": [],
                "methods": [],
                "properties": [],
                "reachable": compute_reachable(cname, fpath, rel),
            }
            entries.append(const_entry)
            claims.append(
                _make_claim(
                    family,
                    "api_field",
                    f"{cname} constant of type {ctype or 'unknown'}",
                    rel,
                    vd.start_point[0] + 1,
                )
            )

    return entries, claims


# ---------------------------------------------------------------------------
# C++ property synthesis
# ---------------------------------------------------------------------------


def _synthesize_cpp_properties(
    methods: list[dict[str, Any]],
    cname: str,
    rel: str,
    family: str,
) -> tuple[list[dict[str, Any]], list[dict]]:
    """Synthesize property entries from C++ getter/setter method pairs.

    The Aspose FOSS C++ headers use snake_case conventions:
      - Getter: a public method ``<name>(...)`` with 0 params and a non-void
        return type that does NOT begin with ``set_``.
      - Setter: a public method ``set_<name>(T val)`` with exactly 1 param.

    A virtual property ``<name>`` is emitted for every getter.  If a matching
    ``set_<name>`` setter exists the property is considered read-write;
    otherwise it is read-only (no effect on the emitted record â€” callers can
    inspect the setter list if needed).

    Returns (properties, claims).
    """
    properties: list[dict[str, Any]] = []
    claims: list[dict] = []

    # Collect setters first so we can annotate read-write properties.
    setter_names: set[str] = set()
    for m in methods:
        name = m["name"]
        params = m.get("params", [])
        if name.startswith("set_") and len(name) > 4 and len(params) == 1:
            setter_names.add(name[4:])  # strip "set_" prefix

    seen: set[str] = set()
    for m in methods:
        name = m["name"]
        params = m.get("params", [])
        ret = m.get("return_type", "")

        # Skip setters (they are not independent properties)
        if name.startswith("set_"):
            continue
        # TC-524: a constructor, destructor, operator or static method is never a property
        if (
            m.get("is_constructor")
            or name.startswith("~")
            or name.startswith("operator")
            or "static" in (m.get("modifiers") or [])
        ):
            continue
        # Must have 0 parameters and a non-empty, non-void return type
        if len(params) != 0:
            continue
        if not ret or ret.strip() in ("void", ""):
            continue
        # Skip private/internal helpers (leading underscore convention)
        if name.startswith("_"):
            continue
        # De-duplicate (e.g. const and non-const overloads share the same name)
        if name in seen:
            continue
        seen.add(name)

        properties.append(
            {
                "name": name,
                "type": ret,
                "doc": m.get("doc", ""),
                "line": m["line"],
                "writable": name in setter_names,
            }
        )
        claims.append(
            _make_claim(family, "api_method", f"{cname}.{name} property of type {ret}", rel, m["line"])
        )

    return properties, claims


# ---------------------------------------------------------------------------
# FR-16: Stub method detection helpers
# ---------------------------------------------------------------------------


def _is_python_stub(mnode) -> bool:
    """Return True if a Python function body is a single raise NotImplementedError."""
    body = child_by_field(mnode, "body") or find_child_by_type(mnode, "block")
    if body is None:
        return False
    stmts = [ch for ch in body.children if ch.type not in (",", "\n", "comment")]
    if len(stmts) != 1:
        return False
    stmt = stmts[0]
    # Allow docstring + raise pattern: 2 children where first is expression_statement(string)
    if stmt.type == "expression_statement" and stmt.children:
        s = stmt.children[0]
        if s.type in ("string", "concatenated_string"):
            # This is a docstring â€” not a raise; look at siblings
            return False
    # raise_statement: raise NotImplementedError / raise NotImplementedError(...)
    if stmt.type == "raise_statement":
        txt = node_text(stmt)
        return "NotImplementedError" in txt
    # expression_statement containing raise: not common, but skip
    return False


def _is_python_stub_with_docstring(mnode) -> bool:
    """Return True if a Python function body is docstring + raise NotImplementedError."""
    body = child_by_field(mnode, "body") or find_child_by_type(mnode, "block")
    if body is None:
        return False
    stmts = [ch for ch in body.children if ch.type not in (",", "\n", "comment")]
    # pattern: expression_statement(string), raise_statement
    if len(stmts) == 2:
        first, second = stmts
        if (
            first.type == "expression_statement"
            and second.type == "raise_statement"
            and "NotImplementedError" in node_text(second)
        ):
            return True
    return False


def _is_python_stub_any(mnode) -> bool:
    """Check both pure-raise and docstring+raise stub patterns for Python."""
    return _is_python_stub(mnode) or _is_python_stub_with_docstring(mnode)


# ---------------------------------------------------------------------------
# Generalized stub detection: catches both well-known exception types AND
# domain-specific exceptions with "not implemented"/"not supported" messages
# (e.g. ExportException("FBX export is not implemented in FOSS version")).
# ---------------------------------------------------------------------------

_STUB_EXCEPTION_RE = re.compile(
    r"(UnsupportedOperationException|NotImplementedException"
    r"|NotSupportedOperationException|NotSupportedException)",
    re.IGNORECASE,
)
_STUB_MESSAGE_RE = re.compile(
    r"not\s+(implemented|supported)|is\s+not\s+(implemented|supported)",
    re.IGNORECASE,
)


def _is_stub_throw(txt: str) -> bool:
    """True if a throw statement indicates unimplemented functionality.

    Matches both well-known exception types (UnsupportedOperationException,
    NotImplementedException) and domain-specific exceptions whose message
    contains "not implemented" or "not supported".
    """
    return bool(_STUB_EXCEPTION_RE.search(txt) or _STUB_MESSAGE_RE.search(txt))


def _is_csharp_stub(mnode) -> bool:
    """Return True if a C# method body is a single throw indicating unimplemented."""
    body = find_child_by_type(mnode, "block")
    if body is None:
        return False
    stmts = [ch for ch in body.children if ch.type not in ("{", "}", "\n", "comment")]
    if len(stmts) != 1:
        return False
    stmt = stmts[0]
    if stmt.type == "throw_statement":
        txt = node_text(stmt)
        return _is_stub_throw(txt)
    return False


def _is_java_stub(mnode) -> bool:
    """Return True if a Java method body is a single throw indicating unimplemented."""
    body = find_child_by_type(mnode, "block")
    if body is None:
        return False
    stmts = [ch for ch in body.children if ch.type not in ("{", "}", "\n", "comment")]
    if len(stmts) != 1:
        return False
    stmt = stmts[0]
    if stmt.type in ("throw_statement", "expression_statement"):
        txt = node_text(stmt)
        return _is_stub_throw(txt)
    return False


def _is_typescript_stub(mnode) -> bool:
    """Return True if a TypeScript/JavaScript method body is a single throw indicating unimplemented."""
    body = find_child_by_type(mnode, "statement_block")
    if body is None:
        return False
    stmts = [ch for ch in body.children if ch.type not in ("{", "}", "\n", "comment")]
    if len(stmts) != 1:
        return False
    stmt = stmts[0]
    if stmt.type in ("throw_statement", "expression_statement"):
        txt = node_text(stmt)
        return _is_stub_throw(txt)
    return False


def _is_cpp_stub(mnode) -> bool:
    """Return True if a C++ function body is a single throw indicating unimplemented."""
    body = find_child_by_type(mnode, "compound_statement")
    if body is None:
        body = find_child_by_type(mnode, "field_declaration_list")
    if body is None:
        return False
    stmts = [ch for ch in body.children if ch.type not in ("{", "}", "\n", "comment")]
    if len(stmts) != 1:
        return False
    stmt = stmts[0]
    if stmt.type in ("throw_statement", "expression_statement"):
        txt = node_text(stmt)
        return _is_stub_throw(txt)
    return False


# ---------------------------------------------------------------------------
# FR-15: C++ canonical namespace helper
# ---------------------------------------------------------------------------


def _cpp_canonical_namespace(cnode) -> str:
    """Walk up the AST from a C++ class node to collect all enclosing namespace
    AND class/struct-type ancestor names (TC-241: a nested class must carry
    its enclosing class in its FQN, not just its enclosing namespace).

    Both namespace and class ancestors are collected in one single bottom-up
    walk, in actual nesting order (innermost first, then reversed before
    joining) -- a namespace can contain a class that contains a nested
    class, and the real FQN must reflect that exact order regardless of
    which kind of ancestor each level is.

    Returns names joined with '::' in outermost-to-innermost order.
    Example: class inside 'namespace Aspose { namespace Slides { ... } }' returns
    'Aspose::Slides'. A class 'Builder' nested inside class 'Outer' returns
    'Outer' for Builder's own canonical_namespace.
    """
    parts: list[str] = []
    parent = cnode.parent
    while parent is not None:
        if parent.type == "namespace_definition" or parent.type in _CLASS_TYPES["cpp"]:
            name_node = child_by_field(parent, "name")
            if name_node:
                parts.append(node_text(name_node))
        parent = parent.parent
    parts.reverse()
    return "::".join(parts)


# ---------------------------------------------------------------------------
# TC-524: C++ fidelity -- full declarator text, constructors, operators, modifiers and
# free functions published only from public headers
# ---------------------------------------------------------------------------

_CPP_PARAM_NODE_TYPES = (
    "parameter_declaration",
    "optional_parameter_declaration",
    "variadic_parameter_declaration",
)
# Declarator wrappers followed (in order) from a parameter's ``declarator`` field down to its name.
_CPP_DECLARATOR_NODE_TYPES = (
    "identifier",
    "field_identifier",
    "pointer_declarator",
    "reference_declarator",
    "array_declarator",
    "parenthesized_declarator",
    "function_declarator",
    "variadic_declarator",
)
_CPP_FUNCTION_NAME_NODE_TYPES = ("identifier", "field_identifier", "destructor_name", "operator_name")
# A leading run of specifier words and attributes that is not part of a return type.
_CPP_LEADING_SPECIFIER_RE = re.compile(
    r"^(?:(?:static|virtual|inline|explicit|constexpr|consteval|friend|extern)\b(?:\s*\([^)]*\))?\s*|\[\[.*?\]\]\s*)+"
)
_CPP_MODIFIER_ORDER = (
    "static",
    "virtual",
    "explicit",
    "constexpr",
    "const",
    "noexcept",
    "override",
    "final",
    "pure",
    "deleted",
    "defaulted",
)
# A free function declared in a header under one of these directories is implementation detail.
_CPP_PRIVATE_DIRECTORY_NAMES = frozenset({"internal", "detail", "details", "impl", "private", "src"})
_CPP_HIDDEN_NAMESPACE_NAMES = frozenset({"detail", "internal"})
_CPP_SCOPE_CONTAINER_TYPES = (
    "preproc_if",
    "preproc_ifdef",
    "preproc_else",
    "preproc_elif",
    "preproc_elifdef",
    "linkage_specification",
    "declaration_list",
)


def _cpp_collapse(text: str) -> str:
    return " ".join(text.split())


def _cpp_declarator_name_node(decl):
    """The identifier at the end of a C++ declarator chain (``* & name``, ``(*name)(int)``, ``name[3]``),
    or ``None`` when the declarator is abstract (an unnamed parameter)."""
    cur = decl
    for _ in range(16):
        if cur is None:
            return None
        if cur.type in ("identifier", "field_identifier"):
            return cur
        nxt = child_by_field(cur, "declarator")
        if nxt is None:
            nxt = next((c for c in cur.children if c.type in _CPP_DECLARATOR_NODE_TYPES), None)
        cur = nxt
    return None


def _cpp_param_type_text(param, name_node, default_node) -> str:
    """The full type text of one C++ parameter: the source from the start of the parameter to the start of
    its name (so ``const std::vector<int>& data`` gives ``const std::vector<int>&`` and ``const char*
    password = nullptr`` gives ``const char*``), plus any array or function-pointer suffix that follows the
    name. An unnamed parameter's type is its source up to the default value or the end."""
    raw = param.text
    base = param.start_byte
    decl = child_by_field(param, "declarator")
    if name_node is not None and decl is not None:
        before = raw[: name_node.start_byte - base].decode("utf-8", errors="replace")
        after = raw[name_node.end_byte - base : decl.end_byte - base].decode("utf-8", errors="replace")
        return _cpp_collapse(before) + _cpp_collapse(after)
    end = default_node.start_byte - base if default_node is not None else len(raw)
    text = _cpp_collapse(raw[:end].decode("utf-8", errors="replace"))
    if default_node is not None and text.endswith("="):
        text = text[:-1].rstrip()
    return text


def _cpp_declarator_params(param_list) -> list[dict[str, Any]]:
    """The parameters of a C++ ``parameter_list`` as ``{"name", "type"[, "default"]}``. An unnamed
    parameter is kept with an empty name; a C varargs ``...`` is kept as an unnamed ``...``; a parameter
    list that is exactly ``(void)`` has no parameters."""
    params: list[dict[str, Any]] = []
    for ch in param_list.children:
        if ch.type == "...":
            params.append({"name": "", "type": "..."})
            continue
        if ch.type not in _CPP_PARAM_NODE_TYPES:
            continue
        decl = child_by_field(ch, "declarator")
        default_node = child_by_field(ch, "default_value")
        name_node = _cpp_declarator_name_node(decl) if decl is not None else None
        entry: dict[str, Any] = {
            "name": node_text(name_node) if name_node is not None else "",
            "type": _cpp_param_type_text(ch, name_node, default_node),
        }
        if default_node is not None:
            entry["default"] = _cpp_collapse(node_text(default_node))
        params.append(entry)
    if len(params) == 1 and not params[0]["name"] and params[0]["type"] == "void":
        return []
    return params


def _cpp_function_declarator(member):
    """The ``function_declarator`` of a C++ member or free-function node, looking through the reference and
    pointer declarators that wrap it for a reference or pointer return type. A conversion operator
    (``operator bool() const``) has an ``abstract_function_declarator`` inside an ``operator_cast`` instead.
    ``None`` when the node declares no function."""
    cur = member
    for _ in range(8):
        direct = find_child_by_type(cur, "function_declarator")
        if direct is not None:
            return direct
        cast = find_child_by_type(cur, "operator_cast")
        if cast is not None:
            return find_child_by_type(cast, "abstract_function_declarator")
        wrapper = None
        for wrapper_type in ("reference_declarator", "pointer_declarator"):
            wrapper = find_child_by_type(cur, wrapper_type)
            if wrapper is not None:
                break
        if wrapper is None:
            return None
        cur = wrapper
    return None


def _cpp_function_name_node(fdecl):
    """The node that names the function *fdecl* declares (identifier, field_identifier, destructor_name,
    operator_name, or the ``operator_cast`` of a conversion operator); ``None`` for a qualified name
    (an out-of-line member) or a function-pointer declarator."""
    if fdecl.type == "abstract_function_declarator":
        return fdecl.parent
    name_node = child_by_field(fdecl, "declarator")
    if name_node is not None and name_node.type in _CPP_FUNCTION_NAME_NODE_TYPES:
        return name_node
    return None


def _cpp_function_name(name_node, fdecl) -> str:
    if name_node.type == "operator_cast":
        return _cpp_collapse(
            name_node.text[: fdecl.start_byte - name_node.start_byte].decode("utf-8", "replace")
        )
    return node_text(name_node).strip()


def _cpp_return_type_text(member, fdecl) -> str:
    """The return type of a C++ function, reference or pointer and leading ``const`` included: the source
    text from the start of the member to the start of the function name, whitespace-collapsed, with a leading
    run of ``static``/``virtual``/``inline``/``explicit``/``constexpr``/``consteval``/``friend``/``extern`` and
    ``[[...]]`` attributes removed. Empty for a constructor, destructor or conversion operator."""
    name_node = _cpp_function_name_node(fdecl)
    if name_node is None:
        return ""
    trailing = find_child_by_type(fdecl, "trailing_return_type")
    if trailing is not None:
        text = node_text(trailing).strip()
        return _cpp_collapse(text[2:] if text.startswith("->") else text)
    head = member.text[: name_node.start_byte - member.start_byte].decode("utf-8", errors="replace")
    return _CPP_LEADING_SPECIFIER_RE.sub("", _cpp_collapse(head)).strip()


def _cpp_member_modifiers(member, fdecl) -> list[str]:
    """The modifiers of a C++ function that apply, in the fixed order of ``_CPP_MODIFIER_ORDER``."""
    found: set[str] = set()
    for ch in member.children:
        text = node_text(ch).strip()
        if ch.type == "storage_class_specifier" and text == "static":
            found.add("static")
        elif ch.type == "virtual":
            found.add("virtual")
        elif ch.type == "explicit_function_specifier":
            found.add("explicit")
        elif ch.type == "type_qualifier" and text == "constexpr":
            found.add("constexpr")
        elif ch.type == "delete_method_clause":
            found.add("deleted")
        elif ch.type == "default_method_clause":
            found.add("defaulted")
    for ch in fdecl.children:
        text = node_text(ch).strip()
        if ch.type == "type_qualifier" and text == "const":
            found.add("const")
        elif ch.type == "noexcept" and "false" not in text:
            found.add("noexcept")
        elif ch.type == "virtual_specifier" and text in ("override", "final"):
            found.add(text)
    default_value = child_by_field(member, "default_value")
    if default_value is not None and node_text(default_value).strip() == "0":
        found.add("pure")
    return [name for name in _CPP_MODIFIER_ORDER if name in found]


def _cpp_signature_claim_names(params: list[dict[str, Any]]) -> str:
    return ", ".join(p["name"] or p["type"] for p in params)


def _extract_cpp_members(cnode, cname: str, rel: str, family: str):
    """The public members of the C++ class or struct *cnode*, as ``(methods, properties, claims)``.

    Walks the class body tracking ``access_specifier`` (a ``class`` starts private, a ``struct`` public).
    Methods come from a ``field_declaration`` with a function declarator, a ``declaration`` with a function
    declarator (constructors, destructors, explicit constructors) and a ``function_definition`` (inline
    methods and the deleted or defaulted special members); a ``field_declaration`` without a function
    declarator is a public data member. A member named like the class is a constructor (``is_constructor``);
    ``~Name`` is a destructor and is not. Nested types and templates are not collected.
    """
    methods: list[dict[str, Any]] = []
    properties: list[dict[str, Any]] = []
    claims: list[dict] = []
    body = find_child_by_type(cnode, "field_declaration_list")
    if body is None:
        return methods, properties, claims
    access = "public" if cnode.type == "struct_specifier" else "private"
    for member in body.children:
        if member.type == "access_specifier":
            access = node_text(member).strip().rstrip(":")
            continue
        if access != "public" or member.type not in (
            "field_declaration",
            "declaration",
            "function_definition",
        ):
            continue
        fdecl = _cpp_function_declarator(member)
        if fdecl is not None:
            name_node = _cpp_function_name_node(fdecl)
            if name_node is None:
                continue
            mname = _cpp_function_name(name_node, fdecl)
            if not mname:
                continue
            if member.type == "function_definition" and mname.startswith("_"):
                continue
            params = _extract_method_params(fdecl, "cpp")
            ret = _cpp_return_type_text(member, fdecl)
            modifiers = _cpp_member_modifiers(member, fdecl)
            method: dict[str, Any] = {
                "name": mname,
                "params": params,
                "return_type": ret,
                "doc": _extract_doc_comment(member, "cpp"),
                "line": member.start_point[0] + 1,
                "is_constructor": mname == cname,
            }
            if modifiers:
                method["modifiers"] = modifiers
            methods.append(method)
            claims.append(
                _make_claim(
                    family,
                    "api_method",
                    f"{cname}.{mname}({_cpp_signature_claim_names(params)}) -> {ret}",
                    rel,
                    member.start_point[0] + 1,
                )
            )
        elif member.type == "field_declaration":
            # Public data member (e.g., std::string name;)
            fname = ""
            for ch in member.children:
                if ch.type in ("identifier", "field_identifier"):
                    fname = node_text(ch)
                    break
            if not fname or fname.startswith("_"):
                continue
            ftype = ""
            type_node = child_by_field(member, "type")
            if type_node:
                ftype = node_text(type_node)
            properties.append(
                {
                    "name": fname,
                    "type": ftype,
                    "doc": _extract_doc_comment(member, "cpp"),
                    "line": member.start_point[0] + 1,
                    "writable": True,  # public data members are read-write
                }
            )
            claims.append(
                _make_claim(
                    family,
                    "api_method",
                    f"{cname}.{fname} data member of type {ftype}",
                    rel,
                    member.start_point[0] + 1,
                )
            )
    return methods, properties, claims


def _cpp_is_public_header(fpath: Path, pkg_root: Path) -> bool:
    """True when *fpath* is a ``.h``/``.hpp`` file outside every ``internal``/``detail``/``details``/``impl``/
    ``private``/``src`` (or ``internal*``) directory below *pkg_root*."""
    if fpath.suffix.lower() not in (".h", ".hpp"):
        return False
    try:
        parts = fpath.relative_to(pkg_root).parts
    except ValueError:
        parts = fpath.parts[-1:]
    for part in parts[:-1]:
        low = part.lower()
        if low in _CPP_PRIVATE_DIRECTORY_NAMES or low.startswith("internal"):
            return False
    return not parts[-1].lower().startswith("internal")


def _cpp_free_function_candidates(container) -> list:
    """The ``declaration`` and ``function_definition`` nodes at namespace or global scope below *container*,
    descending named namespaces (never an anonymous one or one named ``detail``/``internal``), preprocessor
    conditionals (include guards wrap whole headers) and linkage blocks."""
    found: list = []
    for node in container.children:
        if node.type in ("declaration", "function_definition"):
            found.append(node)
        elif node.type == "namespace_definition":
            name_node = child_by_field(node, "name")
            if name_node is None:
                continue
            if any(seg.strip() in _CPP_HIDDEN_NAMESPACE_NAMES for seg in node_text(name_node).split("::")):
                continue
            body = child_by_field(node, "body")
            if body is not None:
                found.extend(_cpp_free_function_candidates(body))
        elif node.type in _CPP_SCOPE_CONTAINER_TYPES:
            found.extend(_cpp_free_function_candidates(node))
    return found


def _extract_cpp_free_functions(root, fpath: Path, pkg_root: Path, rel: str, family: str, reachable):
    """The free functions of one C++ file as ``(entries, claims)``.

    A function is published only when it is declared or defined inline in a PUBLIC header: a ``.cpp`` file
    contributes none, and neither does a header under an ``internal``/``detail`` directory. Excluded: a
    ``static`` function, anything in an anonymous or ``detail``/``internal`` namespace, an out-of-line member
    definition (a qualified name), a template, a friend declaration and anything without a return type.
    *reachable* is the extraction's ``(name, path, rel) -> bool`` reachability callback.
    """
    entries: list[dict[str, Any]] = []
    claims: list[dict] = []
    if not _cpp_is_public_header(fpath, pkg_root):
        return entries, claims
    for node in _cpp_free_function_candidates(root):
        fdecl = _cpp_function_declarator(node)
        if fdecl is None or fdecl.type != "function_declarator":
            continue
        name_node = _cpp_function_name_node(fdecl)
        if name_node is None or name_node.type not in ("identifier", "operator_name"):
            continue
        if any(
            (ch.type == "storage_class_specifier" and node_text(ch).strip() == "static")
            or ch.type == "friend"
            for ch in node.children
        ):
            continue
        fname = node_text(name_node).strip()
        ret = _cpp_return_type_text(node, fdecl)
        if not fname or not ret:
            continue
        params = _extract_method_params(fdecl, "cpp")
        entry: dict[str, Any] = {
            "name": fname,
            "kind": "function",
            "doc": _extract_doc_comment(node, "cpp"),
            "file": rel,
            "line": node.start_point[0] + 1,
            "bases": [],
            "methods": [],
            "properties": [],
            "params": params,
            "return_type": ret,
            "reachable": reachable(fname, fpath, rel),
        }
        modifiers = _cpp_member_modifiers(node, fdecl)
        if modifiers:
            entry["modifiers"] = modifiers
        canonical_ns = _cpp_canonical_namespace(node)
        if canonical_ns:
            entry["canonical_namespace"] = canonical_ns
            entry["class_import"] = f"{canonical_ns}::{fname}"
        else:
            entry["class_import"] = fname
        entries.append(entry)
        claims.append(
            _make_claim(
                family,
                "api_method",
                f"Function {fname}({_cpp_signature_claim_names(params)}) -> {ret}",
                rel,
                node.start_point[0] + 1,
            )
        )
    return entries, claims


def _ts_namespace_chain(cnode) -> str:
    """Walk up the AST from a TypeScript class/interface/enum node collecting
    every enclosing `internal_module`/`module` (namespace) name, outer-to-
    inner, skipping `export_statement`/`statement_block` wrapper nodes in
    between. Returns "" when *cnode* is not nested inside any namespace
    block. A dotted namespace name (`namespace Outer.Inner2 {}`) parses as a
    single `nested_identifier` name field whose own text already contains
    the dots (verified via parse probe) -- no extra join step needed for
    that case, it falls out of the same node_text() call.

    TC-MT040-15.
    """
    parts: list[str] = []
    parent = cnode.parent
    while parent is not None:
        if parent.type in ("internal_module", "module"):
            name_node = child_by_field(parent, "name")
            if name_node is not None:
                parts.append(node_text(name_node))
        parent = parent.parent
    parts.reverse()
    return ".".join(parts)


def _csharp_canonical_namespace(cnode) -> str:
    """Walk up from a C# class node to find the enclosing namespace AND any
    enclosing class/struct/interface/record ancestor (TC-241: a nested class
    must carry its enclosing class in its FQN, not just its enclosing
    namespace).

    Handles block-scoped (``namespace X { class Y {} }``) and file-scoped
    (``namespace X; class Y {}``) forms. Namespace and class-type ancestors
    are collected in one single bottom-up walk, in actual nesting order
    (innermost first, then reversed before joining) -- a namespace can
    contain a class that contains a nested class, and the real FQN must
    reflect that exact order regardless of which kind of ancestor each
    level is.
    """
    # Block-scoped: walk up through namespace_declaration AND class-type
    # ancestors in one pass.
    parts: list[str] = []
    saw_block_namespace = False
    parent = cnode.parent
    while parent is not None:
        if parent.type == "namespace_declaration":
            saw_block_namespace = True
            for ch in parent.children:
                if ch.type in ("qualified_name", "identifier"):
                    parts.append(node_text(ch))
                    break
        elif parent.type in _CLASS_TYPES["csharp"]:
            name_node = child_by_field(parent, "name")
            if name_node:
                parts.append(node_text(name_node))
        parent = parent.parent
    if saw_block_namespace:
        parts.reverse()
        return ".".join(parts)
    # File-scoped: find sibling file_scoped_namespace_declaration at root,
    # then prepend it to any class-type ancestor chain collected above (the
    # walk above still ran and may have collected enclosing class names even
    # though no namespace_declaration ancestor was found).
    parts.reverse()
    cu = cnode
    while cu.parent is not None:
        cu = cu.parent
    for ch in cu.children:
        if ch.type == "file_scoped_namespace_declaration":
            for sub in ch.children:
                if sub.type in ("qualified_name", "identifier"):
                    parts.insert(0, node_text(sub))
                    break
            break
    return ".".join(parts)


def _java_enclosing_class_chain(cnode) -> str:
    """Walk up from a Java class/interface/enum/record/annotation-type node
    collecting every enclosing class-type ancestor's own name (TC-241: a
    nested class must carry its enclosing class in its FQN; Java previously
    had no ancestor walk for this at all).

    Mirrors the C++/C# ancestor-walk shape above: a single bottom-up walk
    over ``cnode.parent``, collecting the name of every ancestor whose type
    is a member of ``_CLASS_TYPES["java"]``, in actual nesting order
    (innermost first, then reversed before joining with '.').

    Returns "" when *cnode* has no class-type ancestor (the common,
    non-nested case) -- callers must leave class_import/canonical_namespace
    exactly as before this fix in that case.
    """
    parts: list[str] = []
    parent = cnode.parent
    while parent is not None:
        if parent.type in _CLASS_TYPES["java"]:
            name_node = child_by_field(parent, "name")
            if name_node:
                parts.append(node_text(name_node))
        parent = parent.parent
    parts.reverse()
    return ".".join(parts)


# ---------------------------------------------------------------------------
# Go struct field extraction (GO-011)
# ---------------------------------------------------------------------------


def _extract_go_struct_fields(
    cnode,
    cname: str,
    rel: str,
    family: str,
) -> tuple[list[dict], list[dict]]:
    """Extract exported fields from a Go struct type_spec node.

    Walks type_spec â†’ struct_type â†’ field_declaration_list â†’ field_declaration
    and captures field_identifier nodes whose name begins with an uppercase letter
    (Go convention for exported identifiers).

    Returns (properties, claims).
    """
    properties: list[dict] = []
    claims: list[dict] = []

    # Go struct body: struct_type â†’ field_declaration_list â†’ field_declaration*
    struct_body = find_child_by_type(cnode, "struct_type")
    if struct_body is None:
        return properties, claims
    field_list = find_child_by_type(struct_body, "field_declaration_list")
    if field_list is None:
        return properties, claims

    for field_decl in field_list.children:
        if field_decl.type != "field_declaration":
            continue
        # field_declaration children: field_identifier* type_node
        field_names: list[str] = []
        type_node = None
        for ch in field_decl.children:
            if ch.type == "field_identifier":
                field_names.append(node_text(ch))
            else:
                # Last non-identifier child is the type
                type_node = ch

        ftype = node_text(type_node) if type_node else ""
        for fname in field_names:
            if not fname or not fname[0].isupper():
                continue  # skip unexported fields
            properties.append(
                {
                    "name": fname,
                    "type": ftype,
                    "kind": "property",
                    "access_mode": "readwrite",
                    "doc": "",
                    "line": field_decl.start_point[0] + 1,
                }
            )
            claims.append(
                _make_claim(
                    family,
                    "api_method",
                    f"{cname}.{fname} field of type {ftype}",
                    rel,
                    field_decl.start_point[0] + 1,
                )
            )

    return properties, claims


# ---------------------------------------------------------------------------
# Go interface method extraction
# ---------------------------------------------------------------------------


def _extract_go_interface_methods(
    cnode,
    cname: str,
    rel: str,
    family: str,
) -> tuple[list[dict], list[dict]]:
    """Extract method signatures from a Go interface type_spec node.

    Walks type_spec -> interface_type -> method_elem* and captures each
    method's name/params/result. Unlike struct receiver methods (which are
    top-level function_declaration/method_declaration nodes relocated by
    _associate_go_methods), interface method signatures live directly inside
    the interface_type body and were never visited by any extraction path --
    confirmed live: pdf/go's real `Annotation` interface (12 methods) was
    captured with methods:[] before this fix, since collect_nodes(cnode,
    func_types) finds 0 function_declaration/method_declaration nodes inside
    an interface body.

    Embedded interfaces (type_elem children, e.g. `interface { Reader }`) are
    handled by _extract_bases, not here -- they contribute to `bases`, not
    `methods`, matching how struct embedding is handled.

    Returns (methods, claims).
    """
    methods: list[dict] = []
    claims: list[dict] = []

    interface_body = find_child_by_type(cnode, "interface_type")
    if interface_body is None:
        return methods, claims

    for member in interface_body.children:
        if member.type != "method_elem":
            continue
        name_node = child_by_field(member, "name")
        mname = node_text(name_node) if name_node else ""
        if not mname or not mname[0].isupper():
            continue  # skip unexported interface methods (package-private contract)
        params = _extract_method_params(member, "go")
        ret = _extract_return_type(member, "go")
        mdoc = _extract_doc_comment(member, "go")
        methods.append(
            {
                "name": mname,
                "params": params,
                "return_type": ret,
                "doc": mdoc,
                "line": member.start_point[0] + 1,
                "is_constructor": False,
            }
        )
        claims.append(
            _make_claim(
                family,
                "api_method",
                f"{cname}.{mname}({', '.join(p['name'] for p in params)}) -> {ret}",
                rel,
                member.start_point[0] + 1,
            )
        )

    return methods, claims


# ---------------------------------------------------------------------------
# Rust struct field extraction + impl-block method association
# ---------------------------------------------------------------------------


def _extract_rust_struct_fields(
    cnode,
    cname: str,
    rel: str,
    family: str,
) -> tuple[list[dict], list[dict]]:
    """Extract `pub` fields from a Rust struct_item node.

    Walks struct_item â†’ field_declaration_list â†’ field_declaration and keeps
    only fields carrying a bare ``pub`` visibility modifier â€” private and
    ``pub(crate)``/``pub(super)`` fields are implementation detail, not public
    API. Tuple structs (ordered_field_declaration_list) and unit structs have
    no named fields and yield nothing.

    Returns (properties, claims).
    """
    properties: list[dict] = []
    claims: list[dict] = []

    body = child_by_field(cnode, "body")
    if body is None or body.type != "field_declaration_list":
        return properties, claims

    for field_decl in body.children:
        if field_decl.type != "field_declaration":
            continue
        vis = [node_text(c).strip() for c in field_decl.children if c.type == "visibility_modifier"]
        if "pub" not in vis:
            continue
        name_node = child_by_field(field_decl, "name")
        fname = node_text(name_node) if name_node else ""
        if not fname:
            continue
        type_node = child_by_field(field_decl, "type")
        ftype = node_text(type_node) if type_node else ""
        properties.append(
            {
                "name": fname,
                "type": ftype,
                "kind": "property",
                "access_mode": "readwrite",
                "doc": _extract_rustdoc_comment(field_decl),
                "line": field_decl.start_point[0] + 1,
            }
        )
        claims.append(
            _make_claim(
                family,
                "api_method",
                f"{cname}.{fname} field of type {ftype}",
                rel,
                field_decl.start_point[0] + 1,
            )
        )

    return properties, claims


def _extract_rust_impl_context(fnode) -> tuple[str, str]:
    """Return (implementing_type, trait_name) for a Rust function_item.

    Walks up from *fnode* to the enclosing ``impl_item`` (if any) and reads
    its ``type`` field (the implementing type) and optional ``trait`` field.
    Generic arguments and reference sigils are stripped so the bare type name
    matches the extracted struct/enum entry (``Workbook<'a, T>`` â†’ ``Workbook``).
    Returns ("", "") for functions not inside an impl block.
    """
    parent = fnode.parent
    while parent is not None and parent.type != "impl_item":
        parent = parent.parent
    if parent is None:
        return "", ""

    def _bare(n) -> str:
        if n is None:
            return ""
        txt = node_text(n)
        return txt.split("<", 1)[0].strip().lstrip("&").strip()

    return (_bare(child_by_field(parent, "type")), _bare(child_by_field(parent, "trait")))


_RUST_TYPE_KINDS: frozenset[str] = frozenset(
    {
        "struct_item",
        "enum_item",
        "union_item",
        "trait_item",
    }
)


def _associate_rust_impl_methods(classes: list[dict]) -> list[dict]:
    """Post-process Rust entries: move impl-block methods into their type.

    Rust methods live in ``impl Type { ... }`` / ``impl Trait for Type { ... }``
    blocks, which are top-level AST nodes â€” the same shape as Go receiver
    methods (SFX-1). The main extraction loop records them as
    ``kind="function"`` entries carrying ``receiver_type`` (the impl target
    type) and ``trait_impl`` (the trait name, for trait impls). This pass
    relocates each such entry into the matching type's ``methods[]`` list and
    records implemented traits in the type's ``bases``. Functions whose
    receiver type was not extracted (e.g. an impl for a private type) are
    left in place, matching the Go behavior.
    """
    type_map: dict[str, int] = {}
    for i, entry in enumerate(classes):
        if entry.get("kind") in _RUST_TYPE_KINDS:
            type_map.setdefault(entry["name"], i)

    to_remove: set[int] = set()
    moved = 0
    for i, entry in enumerate(classes):
        if entry.get("kind") != "function":
            continue
        receiver = entry.get("receiver_type", "")
        if not receiver:
            continue
        idx = type_map.get(receiver)
        if idx is None:
            continue
        parent = classes[idx]
        method_entry = {
            "name": entry["name"],
            "doc": entry.get("doc", ""),
            "params": entry.get("params", []),
            "return_type": entry.get("return_type", ""),
            "line": entry.get("line", 0),
            "file": entry.get("file", ""),
            # Rust convention: `new` is the canonical associated constructor.
            "is_constructor": entry["name"] == "new",
        }
        if not isinstance(parent.get("methods"), list):
            parent["methods"] = []
        parent["methods"].append(method_entry)
        trait_name = entry.get("trait_impl", "")
        if trait_name and trait_name not in parent.get("bases", []):
            parent.setdefault("bases", []).append(trait_name)
        to_remove.add(i)
        moved += 1

    LOG.debug("Rust impl association: moved %d impl methods into %d types", moved, len(type_map))
    return [e for i, e in enumerate(classes) if i not in to_remove]


# ---------------------------------------------------------------------------
# Go receiver method association (SFX-1)
# ---------------------------------------------------------------------------


def _associate_go_methods(classes: list[dict]) -> list[dict]:
    """Post-process Go entries: move receiver methods into their parent type.

    In Go, method declarations are top-level AST nodes with a receiver
    parameter that names the type they belong to (e.g. ``func (d *Document)
    Save(...) error``).  The main extraction loop cannot find them as children
    of the type node, so it records them as ``kind="function"`` entries with
    a ``receiver_type`` field.  This pass relocates each such entry into the
    ``methods[]`` list of the matching type entry and removes it from the
    top-level list.
    """
    # Map type name â†’ index in classes list
    type_map: dict[str, int] = {}
    for i, entry in enumerate(classes):
        if entry.get("kind") in ("type_spec", "type_declaration"):
            type_map[entry["name"]] = i

    to_remove: set[int] = set()
    for i, entry in enumerate(classes):
        if entry.get("kind") != "function":
            continue
        receiver = entry.get("receiver_type", "")
        if not receiver:
            continue
        recv_name = receiver.lstrip("*").strip()
        if recv_name not in type_map:
            continue
        parent = classes[type_map[recv_name]]
        method_entry = {
            "name": entry["name"],
            "doc": entry.get("doc", ""),
            "params": entry.get("params", []),
            "return_type": entry.get("return_type", ""),
            "line": entry.get("line", 0),
            "file": entry.get("file", ""),
            "is_constructor": False,
        }
        if not isinstance(parent.get("methods"), list):
            parent["methods"] = []
        parent["methods"].append(method_entry)
        to_remove.add(i)

    LOG.debug("SFX-1: moved %d receiver methods into %d Go types", len(to_remove), len(type_map))
    return [e for i, e in enumerate(classes) if i not in to_remove]


# ---------------------------------------------------------------------------
# Partial-class consolidation
# ---------------------------------------------------------------------------


_VIS_RANK = {"exported": 3, "public": 2, "conventional": 1, "internal": 0}


def _merge_members(target: dict, source: dict) -> None:
    """Merge methods, properties, and bases from *source* into *target*.

    HARDEN-A11 (2026-07-22): members are deepcopy'd before appending, matching
    the ST-013 fix in _flatten_inheritance(). This wasn't exploitable as a live
    bug today ONLY because every `source` merged here is unconditionally
    discarded by the caller (consolidate_classes()) right after â€” no other
    surviving class holds the same object. That invariant lives entirely in
    caller discipline, not in this function's own contract, and is exactly the
    shape of assumption ST-013 violated once already. Copying here removes the
    dependency on that invariant rather than trusting it to hold forever.
    """
    # Methods: dedup by (name, params_str)
    existing_sigs: set[tuple[str, str]] = {
        (m.get("name", ""), str(m.get("params", ""))) for m in target.get("methods", [])
    }
    for m in source.get("methods", []):
        sig = (m.get("name", ""), str(m.get("params", "")))
        if sig not in existing_sigs:
            target.setdefault("methods", []).append(copy.deepcopy(m))
            existing_sigs.add(sig)
    # Properties: dedup by name
    existing_pnames: set[str] = {p.get("name", "") for p in target.get("properties", [])}
    for p in source.get("properties", []):
        if p.get("name", "") not in existing_pnames:
            target.setdefault("properties", []).append(copy.deepcopy(p))
            existing_pnames.add(p.get("name", ""))
    # Bases: dedup
    existing_bases = set(target.get("bases", []))
    for b in source.get("bases", []):
        if b not in existing_bases:
            target.setdefault("bases", []).append(b)
            existing_bases.add(b)
    # Doc: prefer entry with more content
    if not target.get("doc") and source.get("doc"):
        target["doc"] = source["doc"]
    # Provenance
    src_file = source.get("file", "")
    if src_file:
        target.setdefault("_merged_files", [target.get("file", "")])
        if src_file not in target["_merged_files"]:
            target["_merged_files"].append(src_file)
    # Class-level modifiers: union (e.g., is_static from one fragment)
    for mod_key in ("is_static", "is_abstract", "is_sealed", "is_partial"):
        if source.get(mod_key):
            target[mod_key] = True


def _dedupe_same_name_functions_by_file(ns_entries: list[dict], ns_indices: list[int]) -> set[int]:
    """Discard genuine same-file duplicate free-function entries only.

    Free-function entries (``kind == "function"``) never get ``canonical_namespace``
    populated (only class/struct/enum entries do). Because ``consolidate_classes``
    sub-groups same-named entries by ``canonical_namespace`` (falling back to ``""``),
    every same-named free function in an ENTIRE repository lands in the same
    namespace-group, regardless of which file it was defined in. None of categories
    1-5 apply to a plain function (no ``is_partial``, no ``bases``, not an enum), so
    they always fall through to category 6's fallback merge, which picks one entry
    as "primary" (by member count -- always 0 for a function) and silently discards
    every other same-named entry, even when they are genuinely distinct, unrelated
    symbols. This is a real, common C++ pattern: ``static`` helpers with internal
    linkage independently redefined per translation unit (e.g. a real
    ``IsNullOrWhiteSpace`` helper defined separately in 10 different .cpp files of
    cells/cpp's real source is 10 distinct symbols, not one duplicated 10 times).

    Every function-kind entry already carries a real ``file`` field (a
    repository-relative path). That field -- not ``canonical_namespace``, which
    file-local C++ functions never have -- is the correct signal to distinguish
    genuinely separate free functions from genuine same-file duplicates (e.g. a
    declaration and its out-of-line definition both recorded as separate entries
    for the same file). Two entries are only real duplicates of each other when
    they share the same ``file``; entries in different files are NEVER discarded
    against each other, regardless of sharing the same name.

    Returns the subset of ``ns_indices`` to discard, keeping exactly one entry per
    file group.
    """
    from collections import defaultdict

    # TC-524: two overloads declared in one header (``ToString(int)`` and ``ToString(double)``) are distinct
    # symbols; entries are duplicates only when the file AND the parameter type list are equal.
    by_file: dict[tuple[str, tuple[str, ...]], list[int]] = defaultdict(list)
    for idx, e in zip(ns_indices, ns_entries):
        signature = tuple(str(p.get("type", "")) for p in (e.get("params") or []))
        by_file[(e.get("file", ""), signature)].append(idx)

    discard: set[int] = set()
    for file_indices in by_file.values():
        if len(file_indices) < 2:
            continue
        # Genuine same-file duplicate (e.g. declaration + out-of-line definition
        # recorded twice) -- keep the first, discard the rest.
        for i in file_indices[1:]:
            discard.add(i)
    return discard


def consolidate_classes(classes: list[dict], language: str) -> list[dict]:
    """Merge partial classes, disambiguate same-name-different-namespace, dedup enums.

    Must run BEFORE ``_flatten_inheritance`` so the by-name dict used for
    inheritance resolution is correct.

    Categories handled:
      0. All entries are free functions (``kind == "function"``) â†’ dedupe by
         ``file`` only (they lack ``canonical_namespace``; see
         ``_dedupe_same_name_functions_by_file``)
      1. Same namespace + ``is_partial`` â†’ MERGE members
      2. Same namespace + stub (no bases, fewer members) â†’ KEEP full, discard stub
      3. Same namespace + compat shim (bases reference the other) â†’ DISCARD shim
      4. Different namespace â†’ KEEP BOTH (distinct ``class_import``)
      5. Identical enums (same enum_members or both empty) â†’ DEDUP
      6. Fallback (no namespace, same visibility) â†’ MERGE (backward compat)

    Returns a new list.
    """
    from collections import Counter, defaultdict

    name_counts = Counter(c.get("name", "") for c in classes)
    # Fast path: if no duplicates at all, return as-is
    dup_names = {n for n, cnt in name_counts.items() if cnt > 1 and n}
    if not dup_names:
        return list(classes)

    groups: dict[str, list[int]] = defaultdict(list)
    for i, c in enumerate(classes):
        n = c.get("name", "")
        if n in dup_names:
            groups[n].append(i)

    discard: set[int] = set()
    merge_count = 0

    for name, indices in groups.items():
        entries = [classes[i] for i in indices]

        # Sub-group by canonical_namespace (or class_import minus name)
        ns_groups: dict[str, list[int]] = defaultdict(list)
        for idx in indices:
            e = classes[idx]
            ns = e.get("canonical_namespace", "")
            if not ns:
                ci = e.get("class_import", "")
                if ci and ci != e.get("name", ""):
                    ns = ci.rsplit(".", 1)[0] if "." in ci else ""
            ns_groups[ns].append(idx)

        # If all entries share the same namespace (or all empty), handle as same-ns
        # If they differ, keep all (different namespaces = different types)
        for ns, ns_indices in ns_groups.items():
            if len(ns_indices) < 2:
                continue

            ns_entries = [classes[i] for i in ns_indices]
            kinds = [e.get("kind", "") for e in ns_entries]

            # Category 0: all entries are free functions. Function-kind entries
            # never carry canonical_namespace, so this ns-group may contain
            # entries from many different files that are NOT duplicates of each
            # other (see _dedupe_same_name_functions_by_file). Handle them here,
            # additively, BEFORE falling through to categories 1-6 below, which
            # remain unchanged for every non-function (class/struct/enum) entry.
            if all(k == "function" for k in kinds):
                discard |= _dedupe_same_name_functions_by_file(ns_entries, ns_indices)
                continue

            # Category 5: identical enums
            if all("enum" in k for k in kinds):
                # Keep first, discard rest
                for i in ns_indices[1:]:
                    discard.add(i)
                continue

            # Category 1: partial classes (at least one has is_partial)
            any_partial = any(e.get("is_partial") for e in ns_entries)
            if any_partial:
                # Pick the entry with the most total members as primary
                def _member_count(e: dict) -> int:
                    return len(e.get("methods", [])) + len(e.get("properties", []))

                primary_idx = max(ns_indices, key=lambda i: _member_count(classes[i]))
                primary = classes[primary_idx]
                for i in ns_indices:
                    if i == primary_idx:
                        continue
                    _merge_members(primary, classes[i])
                    discard.add(i)
                    merge_count += 1
                LOG.debug("MERGE-PARTIAL: %s â€” %d fragments â†’ 1", name, len(ns_indices))
                continue

            # Category 3: compat shim (one's bases list references the other by name)
            # Must run BEFORE stub-discard: a shim has bases (referencing the
            # real class), so the stub heuristic would wrongly keep the shim.
            shim_found = False
            for i in ns_indices:
                bases = classes[i].get("bases", [])
                for b in bases:
                    short_base = b.rsplit(".", 1)[-1] if "." in b else b
                    if short_base == name:
                        discard.add(i)
                        shim_found = True
                        LOG.debug(
                            "DISCARD-SHIM: %s â€” %s inherits from same-name type",
                            name,
                            classes[i].get("file", ""),
                        )
                        break
            if shim_found:
                continue

            # Category 2: stub vs full (one has bases, others don't)
            with_bases = [i for i in ns_indices if classes[i].get("bases")]
            without_bases = [i for i in ns_indices if not classes[i].get("bases")]
            if with_bases and without_bases:
                for i in without_bases:
                    discard.add(i)
                LOG.debug(
                    "DISCARD-STUB: %s â€” kept %d with bases, discarded %d stubs",
                    name,
                    len(with_bases),
                    len(without_bases),
                )
                continue

            # Category 6: fallback â€” merge on same visibility (backward compat)
            vis_groups: dict[int, list[int]] = defaultdict(list)
            for i in ns_indices:
                rank = _VIS_RANK.get(classes[i].get("visibility", "conventional"), 1)
                vis_groups[rank].append(i)
            for rank, vis_indices in vis_groups.items():
                if len(vis_indices) < 2:
                    continue
                primary_idx = max(
                    vis_indices,
                    key=lambda i: len(classes[i].get("methods", [])) + len(classes[i].get("properties", [])),
                )
                primary = classes[primary_idx]
                for i in vis_indices:
                    if i == primary_idx:
                        continue
                    _merge_members(primary, classes[i])
                    discard.add(i)
                    merge_count += 1
                LOG.debug(
                    "MERGE-FALLBACK: %s â€” %d entries with rank %d â†’ 1", name, len(vis_indices), rank
                )

    if merge_count:
        LOG.info("consolidate_classes: merged %d partial-class fragment(s)", merge_count)
    if discard:
        LOG.info("consolidate_classes: discarded %d duplicate(s)", len(discard) - merge_count)

    return [c for i, c in enumerate(classes) if i not in discard]


# ---------------------------------------------------------------------------
# Core API surface extraction
# ---------------------------------------------------------------------------


def _prefers_structured_data(existing: dict, candidate: dict) -> bool:
    """True if *candidate* should displace *existing* in the short-name index.

    TC-293 (found while verifying TC-281/TC-288 against 3d/python's real Python
    extraction): a Python package following the common "class defined in its own
    submodule, re-exported at the package level" convention produces two entries
    sharing the same bare ``name`` -- a re-export shell (``class_import`` e.g.
    ``pkg.Base``, ``methods=[]``, ``properties=[]``) and the real definition
    (``class_import`` e.g. ``pkg.Base.Base``, real methods/properties). Because
    the shell's shorter ``class_import`` sorts first in the type list, it used to
    claim the short-name slot before the real definition was ever processed --
    and since source almost always references a base class by its bare,
    unqualified name, every subclass's ``bases`` entry resolved through the
    short-name index straight to the empty shell, never the real definition.
    Live-confirmed against 3d/python's real extraction: every one of 281
    short-name-resolved base references in that pilot hit an empty shell, and
    ``_flatten_inheritance()`` copied zero members anywhere in the pilot.

    This prefers whichever candidate actually carries structured data (a
    non-empty ``methods`` or ``properties`` list) over an empty shell. If BOTH
    candidates are non-empty, that is a genuine ambiguity this function does not
    resolve -- it returns ``False`` and the existing first-writer-wins entry is
    kept, exactly as before this fix. Mirrors the same "prefer the richer, more
    specific data" principle TC-261's own ``m.get("inherited_from") or
    parent_identity`` already applies for provenance rooting.
    """
    existing_has_data = bool(existing.get("methods")) or bool(existing.get("properties"))
    candidate_has_data = bool(candidate.get("methods")) or bool(candidate.get("properties"))
    return candidate_has_data and not existing_has_data


def _is_constructor_like(method: dict, class_short_name: str) -> bool:
    """True if *method* is a constructor or destructor of the class named *class_short_name*.

    TC-392: constructors and destructors are not inherited in C#, Java, C++ or TypeScript
    (nor is Python's ``__init__`` a member a caller sees on the subclass as the base's), so
    ``_flatten_inheritance`` must not copy them from a parent into a subclass. A parent's
    ``BaseParagraph()`` / ``~BaseParagraph()`` listed on ``PopupAnnotation`` is fabricated
    API. Recognised shapes: an explicit ``is_constructor`` flag, a name equal to the class's
    short name, ``~`` plus the short name, or the language keywords ``constructor`` /
    ``__init__``. An ordinary method that merely RETURNS the class type is not matched.
    """
    if method.get("is_constructor"):
        return True
    name = method.get("name", "")
    if name in ("constructor", "__init__"):
        return True
    if not class_short_name:
        return False
    return name == class_short_name or name == "~" + class_short_name


def _flatten_inheritance(classes: list[dict]) -> None:
    """Copy inherited methods/properties into child classes (in-place).

    Walks the full inheritance tree (not just direct parents) by resolving
    classes in topological order so parents are populated before children.
    Child definitions take precedence over inherited ones.
    Handles cycles via visited-set guard.

    TC-261 (C3 of the third independent recon, 2026-10-08): every copied
    method/property gains an explicit ``"inherited_from"`` key recording the
    real, ORIGINAL declaring class -- never a genuinely locally-declared
    entry, which must never gain this key at all. For a multi-level chain
    (confirmed live: pdf/cpp's real PopupAnnotation -> Annotation ->
    BaseParagraph), a member copied transitively through an intermediate
    parent must still be rooted to the class that actually declares it
    (BaseParagraph), not the intermediate parent it was copied through
    (Annotation) -- because parents are resolved before children, a parent's
    own already-copied entries already carry the correct original
    "inherited_from"; copying from the parent into the child preserves that
    existing value instead of overwriting it with the parent's own identity,
    and only a genuinely parent-declared entry (no existing "inherited_from")
    gets the parent's own identity. Before this fix, a copied member carried
    no ownership marker at all, silently misrepresenting an inherited member
    as if it were locally declared -- the entry's own file/line metadata
    stayed correctly pointed at the real ancestor, but nothing in the data
    itself told a caller the member was not the child's own.

    TC-293: the short-name index below now prefers a structured-data-bearing
    candidate over an empty shell on a short-name collision (see
    ``_prefers_structured_data``'s own docstring). The full-class_import-keyed
    resolution path (``by_name[key] = c`` immediately below) is untouched --
    that path was already unambiguous and correct.
    """
    # Use class_import as key when available for namespace-aware resolution;
    # also index by short name as fallback for base-class lookup.
    by_name: dict[str, dict] = {}
    for c in classes:
        key = c.get("class_import") or c.get("name", "")
        if key:
            by_name[key] = c
        short = c.get("name", "")
        if short:
            if short not in by_name:
                by_name[short] = c
            elif _prefers_structured_data(by_name[short], c):
                by_name[short] = c

    resolved: set[str] = set()

    def _resolve(cls_name: str, visiting: set[str]) -> None:
        if cls_name in resolved or cls_name not in by_name:
            return
        if cls_name in visiting:  # cycle guard
            return
        visiting.add(cls_name)
        cls = by_name[cls_name]
        # TC-521: an aliased JavaScript import base is followed to the class it names.
        base_names = _effective_bases(cls)
        # Resolve parents first so their inherited members are populated
        for base_name in base_names:
            _resolve(base_name, visiting)
        # Copy from each parent (now includes grandparent members)
        for base_name in base_names:
            parent = by_name.get(base_name)
            if parent is None:
                continue

            # Deep-copy each inherited member â€” the parent's method/property
            # dicts must never be shared by reference across classes. Without
            # this, any later pass that mutates one class's copy of an
            # inherited member (e.g. a doc backfill) silently corrupts the
            # parent's own entry and every other class that inherited the
            # same member, since they all pointed at the identical object.
            # Confirmed root cause of ST-013 (3d/java truth audit 2026-07-14):
            # identical wrong doc text appeared on ~37 unrelated pages because
            # they all rendered the same shared, mutated dict.
            # Dedup by (name, param-type-signature), not name alone -- a
            # name-only key silently drops real overloads sharing a name
            # with different parameter types (confirmed: Node.ToString
            # declares both ToString(SaveFormat) and ToString(SaveOptions);
            # flattening kept only whichever the parent's own list listed
            # first, for every subclass). Two methods can't share both name
            # AND identical param types in one class, so this key is exact,
            # not a heuristic.
            def _method_key(entry: dict) -> tuple:
                return (
                    entry["name"],
                    tuple(p.get("type", "") for p in entry.get("params", [])),
                )

            # Provenance root for a member genuinely declared by `parent` itself
            # (not already carrying its own "inherited_from" from a deeper
            # ancestor -- see the docstring above for why that case is
            # preserved instead of overwritten).
            parent_identity = parent.get("class_import") or parent.get("name", "")

            child_method_keys = {_method_key(m) for m in cls.get("methods", [])}
            parent_short_name = parent.get("name", "")
            for m in parent.get("methods", []):
                # TC-392: constructors/destructors are never inherited.
                if _is_constructor_like(m, parent_short_name):
                    continue
                key = _method_key(m)
                if key not in child_method_keys:
                    copied_method = copy.deepcopy(m)
                    copied_method["inherited_from"] = m.get("inherited_from") or parent_identity
                    cls.setdefault("methods", []).append(copied_method)
                    child_method_keys.add(key)
            child_prop_names = {p["name"] for p in cls.get("properties", [])}
            for p in parent.get("properties", []):
                if p["name"] not in child_prop_names:
                    copied_prop = copy.deepcopy(p)
                    copied_prop["inherited_from"] = p.get("inherited_from") or parent_identity
                    cls.setdefault("properties", []).append(copied_prop)
                    child_prop_names.add(p["name"])
        visiting.discard(cls_name)
        resolved.add(cls_name)

    for cls in classes:
        name = cls.get("name")
        if name:
            _resolve(name, set())


def _mark_package_init_exports(
    classes: list[dict],
    source_files: list[Path],
    pkg_root: Path | None = None,
) -> None:
    """Mark Python classes re-exported via any package __init__.py.__all__.

    PY-INIT-001: Packages that define classes in _internal/ and re-export them
    from __init__.py use a legitimate "implementation hiding" pattern.  The
    per-file __all__ extraction (S-1) only sets visibility="exported" when the
    class definition and __all__ are in the SAME file.  Cross-file re-exports
    from __init__.py are invisible to that check.

    This post-pass unions all __all__ names from every __init__.py in the
    source tree and sets exported_via_package_init=True on matching classes.
    index.py._is_public_api_entry() checks this flag before applying the
    /_internal/ path filter, so re-exported classes are counted as public API.

    PY-INIT-002 (MT028-2026-08-09-FONT-PYTHON-BLIND-SPOT-HEAL, RC-font-python-02):
    an __all__ entry is only a real re-export if the name is ALSO actually bound
    in that same __init__.py (via an import, or a local def/class/assignment) --
    not merely present as a string literal inside __all__'s list. Upstream
    packages can and do carry an __all__ entry with no corresponding import (a
    real, live-verified example: aspose-font-foss-for-python's `BinaryReader`/
    `BinaryWriter` are listed in __all__ but never imported into the package
    namespace, so `from aspose_font import BinaryReader` genuinely raises
    ImportError). Treating such a name as public API mis-informs generated
    reference content into citing a class that is not actually importable.
    Verified via AST (ast.parse over the __init__.py source), not another
    regex, so relative-import forms, parenthesized multi-name imports, and
    `as`-aliases are all handled correctly, matching how Python itself
    resolves names -- the previous name-only-in-__all__ regex match is the bug
    this fixes, not the pattern to preserve. Do not fix the upstream repo
    itself (runs/.clone_cache/ is read-only per AGENTS.md 2a); this pipeline
    now correctly reports what the package's own __all__ inaccurately claims.

    PY-INIT-003 (MT028-2026-08-09-FONT-PYTHON-BLIND-SPOT-HEAL, RC-font-python-01):
    a boolean "is it exported somewhere" is not enough to construct a correct
    import statement -- a class can be re-exported by a SUBPACKAGE's
    __init__.py (e.g. aspose_font/cff/__init__.py re-exporting CffCharset)
    without being re-exported by the top-level package __init__.py at all.
    Live-verified against aspose-font-foss-for-python: `from aspose_font import
    CffCharset` raises ImportError, but `from aspose_font.cff import
    CffCharset` succeeds -- the class_import field ("cff.charset.CffCharset",
    the class's OWN defining module) is a different fact from where the class
    is genuinely re-exported and cannot substitute for it. When *pkg_root* is
    given, each entry also gets public_import_module: the dotted path (from
    pkg_root) of the __init__.py that genuinely re-exports it -- "" for the
    top-level package root, "cff" for a subpackage, etc. -- so a consumer can
    build `from {package}[.{public_import_module}] import {name}` exactly,
    instead of guessing from source-file location. When the same name is
    re-exported by more than one __init__.py, the shallowest (closest to
    pkg_root) wins, matching normal "prefer the top-level public path" intent.
    """
    package_all_names: set[str] = set()
    public_import_module: dict[str, str] = {}
    for src_file in source_files:
        if src_file.name != "__init__.py":
            continue
        # Only read __init__.py files that are NOT inside a private implementation
        # subtree (a directory starting with a single "_", e.g. _internal/).
        # This ensures we only aggregate exports from the package's public interface
        # modules, not from internal subpackage init files.
        if any(p.startswith("_") and not p.startswith("__") for p in src_file.parts):
            continue
        try:
            content = src_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        try:
            tree = ast.parse(content)
        except SyntaxError:
            # An __init__.py that doesn't even parse cannot be trusted for either
            # its __all__ list or its bindings -- absence of evidence, not
            # evidence of absence (same principle _resolve_python_shim_collisions
            # already applies elsewhere in this file). Contribute nothing rather
            # than falling back to the less-correct name-only match.
            continue

        declared_all: set[str] = set()
        bound_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name != "*":
                        bound_names.add(alias.asname or alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    bound_names.add(alias.asname or alias.name.split(".")[0])
            elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                bound_names.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Name)
                        and target.id == "__all__"
                        and isinstance(node.value, (ast.List, ast.Tuple))
                    ):
                        for elt in node.value.elts:
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                declared_all.add(elt.value)
                    elif isinstance(target, ast.Name):
                        bound_names.add(target.id)

        # Only names that are BOTH declared in __all__ AND actually bound in this
        # file count as genuine re-exports -- an __all__ entry with no matching
        # binding is the upstream bug PY-INIT-002 exists to stop trusting.
        genuine_reexports = declared_all & bound_names
        package_all_names.update(genuine_reexports)

        if pkg_root is not None and genuine_reexports:
            try:
                rel_dir = src_file.parent.relative_to(pkg_root)
                module_path = "" if rel_dir == Path(".") else ".".join(rel_dir.parts)
            except ValueError:
                continue
            for name in genuine_reexports:
                existing = public_import_module.get(name)
                # Shallower (fewer dotted segments) wins -- "" (top-level) beats
                # any subpackage path, matching "prefer the most public route".
                if existing is None or len(module_path) < len(existing):
                    public_import_module[name] = module_path

    if not package_all_names:
        return

    for entry in classes:
        name = entry.get("name")
        if name in package_all_names:
            entry["exported_via_package_init"] = True
            if name in public_import_module:
                entry["public_import_module"] = public_import_module[name]


_PY_RELATIVE_IMPORT_RE = re.compile(r"^\s*from\s+(\.+)([\w.]*)\s+import\s+([^\n(]+)$", re.MULTILINE)
# Companion pattern for the parenthesized multi-line form of the same
# statement (`from .forms import (\n    Field,\n    ...\n)`), which
# _PY_RELATIVE_IMPORT_RE's own trailing `[^\n(]+$` deliberately excludes
# (it stops at the first "(" or newline) and therefore never matches at
# all. This is the standard style for a from-import listing more than a
# handful of names, so real packages nearly always use it for exactly the
# imports this module cares about (confirmed live 2026-08-17, pdf/python:
# `__init__.py`'s own `from .forms import (Field, ..., UnsignedContent,
# UnsignedContentAbsorber)` block was invisible to the single-line-only
# regex, wrongly making UnsignedContentAbsorber read as "not re-exported
# anywhere" and triggering _resolve_python_shim_collisions' whole-group
# discard on a class the package's own __init__.py genuinely exports).
_PY_RELATIVE_IMPORT_PAREN_RE = re.compile(r"^\s*from\s+(\.+)([\w.]*)\s+import\s+\(([^)]*)\)", re.MULTILINE)
# Absolute-form counterparts ("from aspose_pdf.images import Rectangle"),
# used only when scanning the repo's own test files (below) for direct
# submodule imports â€” the leading `\w` (not `[\w.]`) is deliberate so
# these never also match a relative "from .forms import X" statement's
# dot-prefixed module path.
_PY_ABSOLUTE_IMPORT_RE = re.compile(r"^\s*from\s+(\w[\w.]*)\s+import\s+([^\n(]+)$", re.MULTILINE)
_PY_ABSOLUTE_IMPORT_PAREN_RE = re.compile(r"^\s*from\s+(\w[\w.]*)\s+import\s+\(([^)]*)\)", re.MULTILINE)


def _resolve_python_shim_collisions(classes: list[dict], source_files: list[Path], repo: Path) -> list[dict]:
    """Drop same-name Python class duplicates that are abandoned top-level
    compatibility shims shadowed by a real submodule implementation, or
    an unreferenced duplicate sitting alongside a confirmed-public sibling.

    Upstream packages sometimes leave a thin placeholder file at the
    package root (``formats/FbxLoadOptions.py``) after moving the real
    implementation into a per-format submodule
    (``formats/fbx/FbxLoadOptions.py``), without deleting the old file.
    Both are picked up as distinct classes by file-based extraction, and
    ``consolidate_classes`` treats differing ``canonical_namespace``
    values as genuinely different types (correct in general â€” this pass
    only removes duplicates it can positively resolve, before that
    generic logic runs).

    Two ordered signals, both deliberately conservative â€” a group is
    only narrowed to zero when there is positive evidence of curation
    that omits every candidate â€” a real, intentional same-name-
    different-module pattern exists in this codebase (e.g. pdf/python's
    ``Document`` + a separate ``generated/Document`` compatibility-shim
    *class*, HARDEN-py-canonical-namespace/MT013) and must not be
    mistaken for dead code just because no ``__init__.py`` curates the
    package's exports:
      1. Base-class presence: a class with zero bases next to a
         same-name class with real bases is almost always the abandoned
         placeholder â€” discard it.
      2. Package-init reachability: if bases don't disambiguate, keep
         only the entry whose defining file is actually the source of a
         ``from <path> import <Name>`` statement in some ``__init__.py``
         â€” but only when exactly one candidate is reachable.
      3. Whole-group drop: if none are reachable, only discard the
         entire group when EVERY candidate's own directory has a real,
         non-private ``__init__.py`` that had the chance to export it
         and didn't (e.g. all Token/TokenType duplicates sit in
         formats/fbx/, whose ``__init__.py`` curates other classes but
         never these). If even one candidate's directory has no
         ``__init__.py`` to check, that is an absence of evidence, not
         evidence of absence â€” leave the group untouched.

    If no signal cleanly resolves a group, it is left untouched for
    ``consolidate_classes``'s existing "different namespace, keep both"
    handling and downstream batch_reference disambiguation.
    """
    from collections import Counter, defaultdict

    name_counts = Counter(c.get("name", "") for c in classes)
    dup_names = {n for n, cnt in name_counts.items() if cnt > 1 and n}
    if not dup_names:
        return list(classes)

    # Build the reachability set: (imported name, resolved module file
    # relative to repo, POSIX-style) for every "from <relpath> import
    # <Name>" statement found in any non-private __init__.py. Also track
    # which directories have a real (non-private) __init__.py at all â€”
    # a package that demonstrably curates its exports there is much
    # stronger evidence than a package with no __init__.py to check.
    reachable: set[tuple[str, str]] = set()
    governed_dirs: set[str] = set()

    def _record_relative_imports(src_file: Path, import_stmts) -> None:
        for dots, modpath, names_part in import_stmts:
            if not modpath:
                continue  # "from . import X" â€” not a specific module file
            base_dir = src_file.parent
            for _ in range(len(dots) - 1):
                base_dir = base_dir.parent
            target = base_dir.joinpath(*modpath.split(".")).with_suffix(".py")
            try:
                target_rel = str(target.relative_to(repo)).replace("\\", "/")
            except ValueError:
                continue
            for raw_name in names_part.split(","):
                imported_name = raw_name.strip().split(" as ")[0].strip()
                if imported_name:
                    reachable.add((imported_name, target_rel))

    for src_file in source_files:
        if src_file.name != "__init__.py":
            continue
        if any(p.startswith("_") and not p.startswith("__") for p in src_file.parts):
            continue
        try:
            governed_dirs.add(str(src_file.parent.relative_to(repo)).replace("\\", "/"))
        except ValueError:
            pass
        try:
            content = src_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        import_stmts = list(_PY_RELATIVE_IMPORT_RE.findall(content))
        import_stmts.extend(_PY_RELATIVE_IMPORT_PAREN_RE.findall(content))
        _record_relative_imports(src_file, import_stmts)

    # Also treat a class as reachable when the repo's own test suite
    # imports it directly from its defining module (e.g. `from
    # aspose_pdf.images import Rectangle`) -- real, sanctioned usage a
    # human test author relied on, as opposed to reachability only from
    # ANOTHER internal implementation file (which just proves internal
    # code reuse, not public status: confirmed live 2026-08-17, pdf/python
    # vs. 3d/python's own prior Token/TokenType finding -- Token/TokenType
    # is imported only from formats/fbx/parser.py, a sibling internal
    # implementation file, never from any test, and correctly stays
    # excluded; Rectangle/TextFragmentAbsorber/TextFragmentCollection are
    # imported directly in pdf/python's real test_image_extraction.py /
    # equivalent test files and must not be discarded as dead code).
    tests_dir = repo / "tests"
    if tests_dir.is_dir():
        # Map every real source file to the dotted module path(s) it is
        # importable as, so "from aspose_pdf.images import Rectangle" can
        # be resolved to the file that actually defines it regardless of
        # a src/-layout root (e.g. real file "src/aspose_pdf/images.py"
        # is importable as both "src.aspose_pdf.images" and, once "src"
        # is on sys.path per the package's own build config, simply
        # "aspose_pdf.images" â€” index both so either form resolves).
        module_path_index: dict[str, str] = {}
        for sf in source_files:
            try:
                sf_rel = str(sf.relative_to(repo)).replace("\\", "/")
            except ValueError:
                continue
            if not sf_rel.endswith(".py"):
                continue
            dotted = sf_rel[: -len(".py")].replace("/", ".")
            if dotted.endswith(".__init__"):
                dotted = dotted[: -len(".__init__")]
            module_path_index.setdefault(dotted, sf_rel)
            if dotted.startswith("src."):
                module_path_index.setdefault(dotted[len("src.") :], sf_rel)

        for test_file in tests_dir.rglob("*.py"):
            try:
                content = test_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            import_stmts = list(_PY_RELATIVE_IMPORT_RE.findall(content))
            import_stmts.extend(_PY_RELATIVE_IMPORT_PAREN_RE.findall(content))
            _record_relative_imports(test_file, import_stmts)

            abs_import_stmts = list(_PY_ABSOLUTE_IMPORT_RE.findall(content))
            abs_import_stmts.extend(_PY_ABSOLUTE_IMPORT_PAREN_RE.findall(content))
            for modpath, names_part in abs_import_stmts:
                target_rel = module_path_index.get(modpath)
                if target_rel is None:
                    continue
                for raw_name in names_part.split(","):
                    imported_name = raw_name.strip().split(" as ")[0].strip()
                    if imported_name:
                        reachable.add((imported_name, target_rel))

    # RPR-ST090-01: transitive reachability. The direct `reachable` set above
    # only captures a class imported ONE hop from a governed __init__.py (or
    # a test). That correctly excludes Token/TokenType (imported only by a
    # sibling internal file, formats/fbx/parser.py, which is itself never
    # reached from any __init__.py or test â€” genuinely dead-adjacent code).
    # But it also wrongly excluded a real, live case: cells/python's
    # `CFBWriter` (cfb_handler.py) is never re-exported by name, yet is
    # imported and instantiated by xlsx_encryptor.py, whose own
    # `encrypt_xlsx`/`decrypt_xlsx` functions ARE directly imported by
    # __init__.py (`from .xlsx_encryptor import encrypt_xlsx, decrypt_xlsx`)
    # -- a real public entry point's implementation chain passes through
    # cfb_handler.py two hops out, which is strong evidence of genuine use,
    # not shim/dead code. Found live 2026-08-18, cells/python S-84
    # remediation: the whole-group-drop below discarded BOTH same-named
    # CFBWriter classes (this one and cfb_writer.py's unrelated low-level
    # MS-CFB writer), which cascaded into knowledge_delta.json wrongly
    # reporting a real, actively-used class as `removed_apis`.
    #
    # Fix: build TWO graphs from every source file (not just __init__.py/
    # tests): `file_targets` (which files each file imports FROM, alias-
    # agnostic -- importing a module executes it regardless of what name
    # the imported symbol is bound to locally) and `unaliased_imports`
    # (name, target_file) pairs recorded ONLY when the import is NOT
    # aliased -- an aliased import (`from .cfb_writer import CFBWriter as
    # CFBWriterImpl`) deliberately does NOT count as evidence that
    # "CFBWriter" itself is reachable by that name, since nothing
    # downstream can ever refer to it as "CFBWriter" again. This
    # alias-awareness is what correctly separates cfb_writer.py's
    # CFBWriter (only ever referenced under the CFBWriterImpl alias --
    # stays excluded) from cfb_handler.py's CFBWriter (imported bare,
    # `from .cfb_handler import CFBReader, CFBWriter, is_encrypted_file`,
    # by xlsx_encryptor.py -- correctly recognized as reachable). Without
    # this distinction, a first version of this fix propagated file-level
    # reachability through the alias edge too and wrongly re-included
    # BOTH same-named classes again.
    #
    # `reachable_files` is then the set of files transitively imported
    # (any name, alias or not) starting from files already proven
    # reachable by the existing direct signals -- this generalizes signal
    # 2 without weakening the Token/TokenType exclusion, since
    # Token/TokenType's own importer (parser.py) has no import chain back
    # to any governed __init__.py either, so it never enters this set.
    file_targets: dict[str, set[str]] = defaultdict(set)
    unaliased_imports: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for src_file in source_files:
        if not src_file.name.endswith(".py"):
            continue
        try:
            src_rel = str(src_file.relative_to(repo)).replace("\\", "/")
        except ValueError:
            continue
        try:
            content = src_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for dots, modpath, names_part in list(_PY_RELATIVE_IMPORT_RE.findall(content)) + list(
            _PY_RELATIVE_IMPORT_PAREN_RE.findall(content)
        ):
            if not modpath:
                continue
            base_dir = src_file.parent
            for _ in range(len(dots) - 1):
                base_dir = base_dir.parent
            target = base_dir.joinpath(*modpath.split(".")).with_suffix(".py")
            try:
                target_rel = str(target.relative_to(repo)).replace("\\", "/")
            except ValueError:
                continue
            file_targets[src_rel].add(target_rel)
            for raw_name in names_part.split(","):
                raw_name = raw_name.strip()
                if not raw_name:
                    continue
                if " as " in raw_name:
                    continue  # aliased -- does not count for name-level reachability
                unaliased_imports[src_rel].add((raw_name, target_rel))

    reachable_files: set[str] = {target_rel for _name, target_rel in reachable}
    frontier = list(reachable_files)
    while frontier:
        current = frontier.pop()
        for target_rel in file_targets.get(current, ()):
            if target_rel not in reachable_files:
                reachable_files.add(target_rel)
                frontier.append(target_rel)

    def _symbol_transitively_reachable(name: str, file: str) -> bool:
        if (name, file) in reachable:
            return True
        return any((name, file) in unaliased_imports.get(g, ()) for g in reachable_files)

    groups: dict[str, list[int]] = defaultdict(list)
    for i, c in enumerate(classes):
        n = c.get("name", "")
        if n in dup_names:
            groups[n].append(i)

    discard: set[int] = set()
    for name, indices in groups.items():
        if len(indices) < 2:
            continue
        entries = [(i, classes[i]) for i in indices]

        with_bases = [i for i, e in entries if e.get("bases")]
        without_bases = [i for i, e in entries if not e.get("bases")]
        if with_bases and without_bases:
            for i in without_bases:
                discard.add(i)
            LOG.info(
                "PY-SHIM: %s â€” discarded %d baseless shim(s) (kept %s)",
                name,
                len(without_bases),
                ", ".join(classes[j].get("file", "") for j in with_bases),
            )
            continue

        reachable_idx = [i for i, e in entries if (name, e.get("file", "")) in reachable]
        if len(reachable_idx) == 1:
            for i in indices:
                if i not in reachable_idx:
                    discard.add(i)
            LOG.info(
                "PY-SHIM: %s â€” kept reachable %s, discarded %d unreachable duplicate(s)",
                name,
                classes[reachable_idx[0]].get("file", ""),
                len(indices) - 1,
            )
            continue

        if not reachable_idx:
            # RPR-ST090-01: before falling back to the directory-curation
            # heuristic below, check transitive SYMBOL reachability -- if
            # exactly one candidate's own name is imported, unaliased, by
            # some file that is itself reached (through any number of
            # internal hops) from a governed __init__.py, that one is
            # genuinely live code and the others are the actual shim/dead
            # duplicates.
            file_reachable_idx = [
                i for i, e in entries if _symbol_transitively_reachable(name, e.get("file", ""))
            ]
            if len(file_reachable_idx) == 1:
                for i in indices:
                    if i not in file_reachable_idx:
                        discard.add(i)
                LOG.info(
                    "PY-SHIM: %s â€” kept transitively-reachable %s, discarded "
                    "%d duplicate(s) with no import chain to any __init__.py",
                    name,
                    classes[file_reachable_idx[0]].get("file", ""),
                    len(indices) - 1,
                )
                continue

            # Zero (transitively) reachable is only meaningful when EVERY
            # entry's own directory has a real, curating __init__.py that
            # had the chance to export it and didn't -- e.g. all
            # Token/TokenType duplicates sit in formats/fbx/, which has a
            # real __init__.py that imports other classes but never these,
            # AND (confirmed live 2026-08-17) neither Token nor TokenType is
            # ever imported from any test file either, nor does their own
            # importer (parser.py) chain back to any governed __init__.py --
            # genuine evidence of non-public status. A file no __init__.py
            # imports anything from at all AND that is never imported from
            # any test file (namespace package, or genuinely distinct
            # same-name classes users import directly from their own
            # submodule -- e.g. pdf/python's real, live `from
            # aspose_pdf.images import Rectangle`, confirmed by
            # test_reachable_from_tests below finding it in
            # test_image_extraction.py) gives no such evidence and must
            # not be treated as dead code by default.
            entry_dirs = {str(Path(e.get("file", "")).parent).replace("\\", "/") for _, e in entries}
            if entry_dirs and entry_dirs.issubset(governed_dirs):
                for i in indices:
                    discard.add(i)
                LOG.warning(
                    "PY-SHIM: %s â€” none of %d duplicate(s) reachable, and every "
                    "containing package has a curating __init__.py that omits "
                    "them; treating as internal, discarding all: %s",
                    name,
                    len(indices),
                    ", ".join(classes[i].get("file", "") for i in indices),
                )
        # else (>1 reachable, or 0 reachable without full __init__.py
        # coverage): not a confident signal either way â€” leave the group
        # untouched for consolidate_classes' existing different-namespace
        # handling and downstream disambiguation.

    if not discard:
        return list(classes)
    return [c for i, c in enumerate(classes) if i not in discard]


# ---------------------------------------------------------------------------
# TC-521: JavaScript object-literal mixins and aliased bases
# ---------------------------------------------------------------------------
#
# A JavaScript library may compose a class from object-literal "mixins":
#
#     export const MailClientMixin = { async listMailboxes(accountId) { ... } };
#     class JmapClient extends CoreClient {}
#     Object.assign(JmapClient.prototype, MailClientMixin);
#
# The class model above only sees ``class`` declarations, and an object-literal method fails
# ``is_public`` (its parent is an ``object``, not a ``class_body``), so the 13 public methods of
# the real jmap/nodejs client were invisible. Per file the extractor now records the mixin
# constants, the ``Object.assign(<Class>.prototype, ...)`` applications and the relative named
# imports; after the file loop ``_attach_js_mixins`` gives each applying class the methods.

_JS_MIXIN_ASSIGN_CALLEE = "Object.assign"
_JS_INLINE_MIXIN = "<inline>"


def _js_member_name(member) -> str:
    """The plain name of an object-literal member (``""`` for a computed, private or numeric key)."""
    key = child_by_field(member, "key" if member.type == "pair" else "name")
    if key is None:
        return ""
    if key.type == "property_identifier":
        return node_text(key)
    if key.type == "string":
        text = node_text(key).strip("\"'`")
        return text if re.fullmatch(r"[A-Za-z_$][\w$]*", text) else ""
    return ""


def _js_member_params(fn) -> list[dict[str, Any]]:
    """Parameters of an object-literal function, including a bare ``x => ...`` arrow parameter."""
    params = _extract_method_params(fn, "javascript")
    if not params:
        single = child_by_field(fn, "parameter")
        if single is not None and single.type == "identifier":
            params = [{"name": node_text(single), "type": ""}]
    return params


def _js_object_literal_members(obj, mixin: str, rel: str) -> list[dict[str, Any]]:
    """The public methods of an object literal as method entries tagged with *mixin*.

    A member is a ``method_definition`` (``async`` and generators included) or a ``pair`` whose
    value is an arrow function or function expression. Names starting with ``_`` or ``#``,
    getters, setters and ``constructor`` are skipped. An entry has the keys of an ordinary method
    entry plus ``"mixin"`` and, for an ``async`` member, ``"modifiers": ["async"]``.
    """
    members: list[dict[str, Any]] = []
    seen: set[str] = set()
    for member in obj.children:
        if member.type == "method_definition":
            fn = member
            if any(ch.type in ("get", "set") for ch in member.children if not ch.is_named):
                continue
        elif member.type == "pair":
            fn = child_by_field(member, "value")
            if fn is None or fn.type not in ("arrow_function", "function_expression"):
                continue
        else:
            continue
        name = _js_member_name(member)
        if not name or name[0] in "_#" or name == "constructor" or name in seen:
            continue
        seen.add(name)
        deprecated, deprecated_reason = deprecated_marker(member, "javascript")
        entry: dict[str, Any] = {
            "name": name,
            "params": _js_member_params(fn),
            "return_type": _extract_return_type(fn, "javascript"),
            "doc": _extract_doc_comment(member, "javascript"),
            "line": member.start_point[0] + 1,
            "file": rel,
            "is_constructor": False,
            "deprecated": deprecated,
            "deprecated_reason": deprecated_reason,
            "mixin": mixin,
        }
        if any(ch.type == "async" for ch in fn.children):
            entry["modifiers"] = ["async"]
        if _is_typescript_stub(fn):
            entry["stub"] = True
        _apply_ts_tsdoc_enrichment(entry, member)
        members.append(entry)
    return members


def _js_collect_mixin_consts(root, rel: str) -> dict[str, list[dict[str, Any]]]:
    """``{const name: member entries}`` of the object-literal constants declared at the top level
    of a file (``const`` / ``let`` / ``var``, with or without ``export``)."""
    found: dict[str, list[dict[str, Any]]] = {}
    for stmt in root.children:
        decl = child_by_field(stmt, "declaration") if stmt.type == "export_statement" else stmt
        if decl is None or decl.type not in ("lexical_declaration", "variable_declaration"):
            continue
        for declarator in find_children_by_type(decl, "variable_declarator"):
            name_node = child_by_field(declarator, "name")
            value = child_by_field(declarator, "value")
            if name_node is None or value is None:
                continue
            if name_node.type == "identifier" and value.type == "object":
                const_name = node_text(name_node)
                found[const_name] = _js_object_literal_members(value, const_name, rel)
    return found


def _js_import_target(
    importing: Path, spec: str, files: set[Path], repo: Path, pkg_root: Path
) -> tuple[str, str]:
    """``(repo-relative file, dotted module path)`` of a relative import specifier, or ``("", "")``
    when it names no scanned file.

    The dotted path is derived exactly like ``class_import``: relative to the package root, ``.js``
    removed, ``/`` replaced by ``.`` and a trailing ``index`` dropped.
    """
    base = Path(os.path.normpath(importing.parent / spec))
    target = next(
        (cand for cand in (base, base.with_name(base.name + ".js"), base / "index.js") if cand in files), None
    )
    if target is None:
        return "", ""
    try:
        rel = target.relative_to(repo).as_posix()
    except ValueError:
        return "", ""
    try:
        parts = list(target.relative_to(pkg_root).parts)
    except ValueError:
        return rel, ""
    if parts and parts[-1].endswith(".js"):
        parts[-1] = parts[-1][:-3]
    if parts and parts[-1] == "index":
        parts = parts[:-1]
    return rel, ".".join(parts)


def _js_collect_imports(
    root, fpath: Path, files: set[Path], repo: Path, pkg_root: Path
) -> dict[str, dict[str, Any]]:
    """The relative named imports of a file as ``{local name: {name, alias, file, module}}``.

    ``import { A as B } from "./x.js"`` gives ``B -> {name: A, alias: True, ...}``; a package
    (non-relative) specifier cannot be resolved and is not recorded.
    """
    found: dict[str, dict[str, Any]] = {}
    for stmt in root.children:
        if stmt.type != "import_statement":
            continue
        source = child_by_field(stmt, "source")
        clause = find_child_by_type(stmt, "import_clause")
        named = find_child_by_type(clause, "named_imports") if clause is not None else None
        spec = node_text(source).strip("\"'`") if source is not None else ""
        if named is None or not spec.startswith("."):
            continue
        target_file, module = _js_import_target(fpath, spec, files, repo, pkg_root)
        for specifier in find_children_by_type(named, "import_specifier"):
            name_node = child_by_field(specifier, "name")
            alias_node = child_by_field(specifier, "alias")
            if name_node is None:
                continue
            original = node_text(name_node)
            local = node_text(alias_node) if alias_node is not None else original
            found[local] = {
                "name": original,
                "alias": alias_node is not None,
                "file": target_file,
                "module": module,
            }
    return found


def _js_collect_mixin_applications(
    root, rel: str, callee: str, imports: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Every ``<callee>(<Class>.prototype, <Mixin>, { inline() {} }, ...)`` call of a file.

    An application is ``{file, class, items, imports}``; each item is ``{"name": <mixin name>}``
    or ``{"inline": <member entries>}``. Other argument shapes are ignored.
    """
    applications: list[dict[str, Any]] = []
    for call in collect_nodes(root, {"call_expression"}):
        function = child_by_field(call, "function")
        arguments = child_by_field(call, "arguments")
        if function is None or arguments is None or node_text(function).strip() != callee:
            continue
        args = [arg for arg in arguments.children if arg.is_named and arg.type != "comment"]
        if len(args) < 2 or args[0].type != "member_expression":
            continue
        owner = child_by_field(args[0], "object")
        prop = child_by_field(args[0], "property")
        if owner is None or prop is None or owner.type != "identifier" or node_text(prop) != "prototype":
            continue
        items: list[dict[str, Any]] = []
        for arg in args[1:]:
            if arg.type == "identifier":
                items.append({"name": node_text(arg)})
            elif arg.type == "object":
                items.append({"inline": _js_object_literal_members(arg, _JS_INLINE_MIXIN, rel)})
        if items:
            applications.append({"file": rel, "class": node_text(owner), "items": items, "imports": imports})
    return applications


def _js_resolve_mixin(
    name: str, application: dict[str, Any], mixins: dict[str, dict[str, list[dict[str, Any]]]]
) -> list[dict[str, Any]]:
    """The member entries of the mixin *name* as an application refers to it: the definition in the
    module its import names, else one in the applying file, else the unique definition of that
    name in the package, else nothing."""
    imported = application.get("imports", {}).get(name)
    if imported is not None and imported["name"] in mixins.get(imported["file"], {}):
        return mixins[imported["file"]][imported["name"]]
    own = mixins.get(application["file"], {})
    if name in own:
        return own[name]
    matches = [definitions[name] for definitions in mixins.values() if name in definitions]
    return matches[0] if len(matches) == 1 else []


def _js_application_target(classes: list[dict], application: dict[str, Any]) -> dict | None:
    """The class an application extends: the class record of that name declared in the applying
    file, else the unique class record of that name, else ``None``."""
    named = [c for c in classes if c.get("name") == application["class"] and c.get("kind") != "function"]
    in_file = [c for c in named if c.get("file") == application["file"]]
    if in_file:
        return in_file[0]
    return named[0] if len(named) == 1 else None


def _attach_js_mixins(
    classes: list[dict],
    mixins: dict[str, dict[str, list[dict[str, Any]]]],
    applications: list[dict[str, Any]],
) -> list[tuple[dict, dict]]:
    """Append the methods of the applied mixins to the classes that receive them (TC-521).

    For each ``Object.assign(<Class>.prototype, ...)`` application: find the class (see
    ``_js_application_target``), resolve each mixin name (see ``_js_resolve_mixin``; an unresolvable
    one is skipped silently) and append deep copies of its methods, an existing member name never
    replaced, in mixin-argument order and then declaration order. A mixin member keeps the ``file``
    and ``line`` of its own definition. Returns ``(class, appended entry)`` pairs.
    """
    attached: list[tuple[dict, dict]] = []
    for application in applications:
        target = _js_application_target(classes, application)
        if target is None:
            continue
        methods = target.setdefault("methods", [])
        known = {method.get("name") for method in methods}
        for item in application["items"]:
            entries = (
                item["inline"] if "inline" in item else _js_resolve_mixin(item["name"], application, mixins)
            )
            for entry in entries:
                if entry["name"] in known:
                    continue
                copied = copy.deepcopy(entry)
                methods.append(copied)
                known.add(entry["name"])
                attached.append((target, copied))
    return attached


def _resolve_js_alias_bases(
    classes: list[dict], imports_by_file: dict[str, dict[str, dict[str, Any]]]
) -> None:
    """Resolve an aliased import base to the class it names (in place).

    ``import { JmapClient as CoreClient } from "./client-core.js"`` followed by
    ``class JmapClient extends CoreClient {}`` leaves the base ``CoreClient``, which no class
    carries. When the import's module path plus its original name equals the ``class_import`` of a
    known class (``client-core.JmapClient``) the class gains the additive
    ``"base_imports": {"CoreClient": "client-core.JmapClient"}``, which ``_flatten_inheritance``
    follows so the wrapper inherits the core class's members. ``bases`` keeps the name as written
    (TC-315 pins ``["CoreClient"]``); a base that does not resolve gets no ``base_imports`` entry.
    """
    known = {c["class_import"] for c in classes if c.get("class_import")}
    for cls in classes:
        imports = imports_by_file.get(cls.get("file", ""), {})
        resolved: dict[str, str] = {}
        for base in cls.get("bases", []):
            imported = imports.get(base)
            if imported is None or not imported["alias"] or not imported["module"]:
                continue
            candidate = f"{imported['module']}.{imported['name']}"
            if candidate in known:
                resolved[base] = candidate
        if resolved:
            cls["base_imports"] = resolved


def _effective_bases(cls: dict) -> list[str]:
    """The bases of *cls* with each aliased import base replaced by the ``class_import`` it names
    (the ``base_imports`` recorded by ``_resolve_js_alias_bases``; absent for every other class)."""
    base_imports = cls.get("base_imports") or {}
    return [base_imports.get(base, base) for base in cls.get("bases", [])]


def extract_api_surface(
    parser,
    language: str,
    pkg_root: Path,
    repo: Path,
    family: str,
    *,
    excluded_package_segments: frozenset[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict], list[str], set[str], dict[str, Any]]:
    """Extract API classes, methods, and properties from source files.

    Args:
        parser: tree-sitter parser for *language*.
        language: Language key (e.g. "java", "python").
        pkg_root: Root directory of the package source tree.
        repo: Root of the FOSS repository clone.
        family: Product family identifier (e.g. "slides").
        excluded_package_segments: For Java, a frozenset of dot-separated
            package segment strings whose matching classes should be excluded
            from the output.  When ``None`` (default) the Java-specific
            defaults :data:`_JAVA_DEFAULT_EXCLUDED_PACKAGE_SEGMENTS` are used
            (``{"internal", "impl"}``).  Pass ``frozenset()`` to disable all
            package filtering.  Ignored for non-Java languages.

    Returns:
        Tuple of (classes, claims, scanned_files, packages, scout_report).
        *scout_report* is a dict with keys ``files_attempted``,
        ``files_parsed``, ``files_skipped``, and ``skip_reasons``.
    """
    classes: list[dict[str, Any]] = []
    claims: list[dict] = []
    scanned_files: list[str] = []
    packages: set[str] = set()

    # Completeness tracking (S1-6)
    files_attempted: int = 0
    files_skipped: list[str] = []
    skip_reasons: dict[str, int] = {}
    parse_errors: list[str] = []

    # SYS-PKG-001: resolve effective Java package exclusion segments once.
    # None â†’ apply language default; frozenset() â†’ disable filtering.
    if language == "java":
        _effective_excluded_segments: frozenset[str] = (
            _JAVA_DEFAULT_EXCLUDED_PACKAGE_SEGMENTS
            if excluded_package_segments is None
            else excluded_package_segments
        )
    else:
        _effective_excluded_segments = frozenset()

    ext = _FILE_EXTENSIONS.get(language, ".py")
    header_ext = _HEADER_EXTENSIONS.get(language, "")
    _collection_stats: dict[str, Any] = {}
    files = _collect_source_files(pkg_root, ext, header_ext, stats=_collection_stats)

    # H-04d: Pre-compute set of files in vendor/private directories.
    # Classes from these files will be marked visibility="internal".
    _vendor_files = _detect_vendor_files(files, pkg_root, language=language)

    # RC-W1-004: compute the reachability signal once per extraction run
    # (pkg_root-level, not per-file) instead of once per class.
    # TC-MT040-42: dispatches through the extraction/lang/ adapters instead
    # of inline per-language logic. Dispatch scope is unchanged from before
    # this card (python/rust only) -- see extraction/lang/__init__.py's
    # module docstring for why csharp's new export_surface() is not wired
    # in here yet.
    _python_exports: set[str] | None = None
    _python_export_root: Path | None = None
    _python_export_init_rel = ""
    _rust_reexports: set[str] | None = None
    # TC-MT040-11: TypeScript/JavaScript reachability signal (real
    # implementation -- was an unconditional (None, None) placeholder from
    # extraction/lang/typescript.py before this card).
    _ts_exports: set[str] | None = None
    _ts_entry_point: Path | None = None
    _ts_entry_point_rel = ""
    if language == "python":
        _python_exports, _python_export_root = lang.python.export_surface(repo, pkg_root)
        if _python_export_root is not None:
            try:
                _python_export_init_rel = str(
                    (_python_export_root / "__init__.py").relative_to(repo)
                ).replace("\\", "/")
            except ValueError:
                _python_export_init_rel = ""
    elif language == "rust":
        _rust_reexports, _ = lang.rust.export_surface(repo, pkg_root)
    elif language in ("typescript", "javascript"):
        _ts_exports, _ts_entry_point = lang.typescript.export_surface(repo, pkg_root)
        # resolve_entry_point() is called separately (not derived from
        # _ts_entry_point above) so scout_report's ts_entry_point stays
        # populated even when export_surface() had to return (None, None)
        # for the whole result because the barrel's exports could not be
        # safely enumerated -- see lang/typescript.py's export_surface()
        # docstring for why that case still loses the entry path there.
        _ts_resolved_entry = lang.typescript.resolve_entry_point(repo, pkg_root)
        if _ts_resolved_entry is not None:
            try:
                _ts_entry_point_rel = str(_ts_resolved_entry.relative_to(repo)).replace("\\", "/")
            except ValueError:
                _ts_entry_point_rel = ""

    def _compute_reachable(item_name: str, item_fpath: Path, item_rel: str) -> bool:
        """RC-W1-004: reachable=True unless a language-specific check above
        can POSITIVELY determine the item is unreachable from outside the
        package/crate despite being syntactically public. Shared by both the
        class loop and the top-level-function loop below. See the helper
        docstrings above _is_excluded_java_package for the full rationale
        and known limitations of each check.
        """
        if language == "python" and _python_exports is not None:
            # Only items under the resolved export root are in scope for
            # this signal -- an item in a sibling subtree the export root
            # doesn't cover is simply unknown, not unreachable.
            try:
                item_fpath.relative_to(_python_export_root)
            except ValueError:
                return True
            # An item defined directly IN the exporting __init__.py is
            # trivially reachable regardless of __all__ -- __all__ only
            # gates `from pkg import *`; `from pkg import Name` works for
            # any name bound in pkg's own namespace.
            if item_rel == _python_export_init_rel:
                return True
            return item_name in _python_exports
        if language == "rust" and _rust_reexports is not None:
            return item_name in _rust_reexports
        if language in ("typescript", "javascript") and _ts_exports is not None:
            # An item literally declared in the entry/barrel file itself is
            # trivially reachable -- mirrors the python __init__.py bypass
            # above. In practice every such item is already captured by
            # export_surface()'s own entry-file bare-declaration scan, but
            # this direct check stays as a second, independent safety net
            # (never a source of a false "confirmed unreachable").
            if _ts_entry_point is not None and item_fpath == _ts_entry_point:
                return True
            return item_name in _ts_exports
        return True

    class_types = _CLASS_TYPES.get(language, set())
    func_types = _FUNC_TYPES.get(language, set())

    # TC-521: per-file facts a JavaScript post-pass needs (object-literal mixins, the
    # ``Object.assign(<Class>.prototype, ...)`` applications and the relative named imports).
    js_file_set: set[Path] = set(files) if language == "javascript" else set()
    js_imports: dict[str, dict[str, dict[str, Any]]] = {}
    js_mixin_defs: dict[str, dict[str, list[dict[str, Any]]]] = {}
    js_mixin_applications: list[dict[str, Any]] = []

    for fpath in files:
        rel = str(fpath.relative_to(repo)).replace("\\", "/")
        files_attempted += 1
        try:
            src = fpath.read_bytes()
        except OSError as e:
            files_skipped.append(rel)
            reason = type(e).__name__
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
            continue

        tree = parser.parse(src)
        root = tree.root_node
        if root.has_error:
            parse_errors.append(rel)
            LOG.debug("tree-sitter parse error in %s â€” extraction may be incomplete", rel)
        scanned_files.append(rel)

        if language == "javascript":
            js_imports[rel] = _js_collect_imports(root, fpath, js_file_set, repo, pkg_root)
            js_mixin_defs[rel] = _js_collect_mixin_consts(root, rel)
            js_mixin_applications.extend(
                _js_collect_mixin_applications(root, rel, _JS_MIXIN_ASSIGN_CALLEE, js_imports[rel])
            )

        # Track packages/namespaces
        module_types = _MODULE_TYPES.get(language, set())
        for mnode in collect_nodes(root, module_types):
            # C++ namespace_definition: extract name field instead of full text
            if language == "cpp" and mnode.type == "namespace_definition":
                name_node = child_by_field(mnode, "name")
                if name_node:
                    packages.add(node_text(name_node))
                continue
            # Rust mod_item: extract the name field â€” the node text of an
            # inline module (`mod foo { ... }`) spans the entire module body.
            if language == "rust" and mnode.type == "mod_item":
                name_node = child_by_field(mnode, "name")
                if name_node:
                    packages.add(node_text(name_node))
                continue
            # TC-MT040-15: TypeScript namespace/module blocks
            # (internal_module / module) -- extract the name field instead
            # of full text, same reason as cpp/rust above: node_text() on
            # the whole block would return the entire namespace BODY, not
            # just its name.
            if language == "typescript" and mnode.type in ("internal_module", "module"):
                name_node = child_by_field(mnode, "name")
                if name_node:
                    packages.add(node_text(name_node))
                continue
            pkg_text = node_text(mnode).strip().rstrip(";").rstrip("{").strip()
            for kw in ("package", "namespace"):
                if pkg_text.startswith(kw):
                    pkg_text = pkg_text[len(kw) :].strip()
            if pkg_text:
                packages.add(pkg_text)

        # FR-15: capture Java file-level package declaration once per file
        java_file_package = ""
        if language == "java":
            for pkg_node in collect_nodes(root, {"package_declaration"}):
                pkg_txt = node_text(pkg_node).strip().rstrip(";")
                if pkg_txt.startswith("package"):
                    java_file_package = pkg_txt[len("package") :].strip()
                break

        class_nodes = collect_nodes(root, class_types)

        # S-1: Extract __all__ names for Python visibility tier.
        # Only handles simple top-level assignment (`__all__ = [...]`).
        # Intentionally does NOT handle `__all__ += [...]`, conditional
        # assignments, or __init__.py re-export aggregation â€” these are
        # rare in the Aspose FOSS repos and would require control-flow
        # analysis.  Unrecognized patterns fall back gracefully to
        # "public" or "conventional" visibility, which is safe.
        module_all_names: set[str] = set()
        if language == "python":
            for stmt in root.children:
                if stmt.type == "assignment":
                    lhs = child_by_field(stmt, "left")
                    rhs = child_by_field(stmt, "right")
                    if lhs and node_text(lhs).strip() == "__all__" and rhs:
                        rhs_text = node_text(rhs)
                        module_all_names = set(re.findall(r'["\'](\w+)["\']', rhs_text))
                        break

        for cnode in class_nodes:
            if not is_public(cnode, language):
                continue

            cname = _node_name(cnode, language)
            if not cname:
                continue

            # TC-523: a type whose JSDoc says @internal / @hidden / @ignore is not emitted at all.
            if language in ("typescript", "javascript") and _ts_is_hidden(cnode):
                continue

            # SYS-PKG-001: exclude Java classes in internal/impl packages.
            # Check uses the file-level java_file_package captured above the loop.
            if (
                language == "java"
                and _effective_excluded_segments
                and _is_excluded_java_package(java_file_package, _effective_excluded_segments)
            ):
                continue

            # Skip C++ forward declarations (class Foo;) â€” no body
            if (
                language == "cpp"
                and cnode.type in ("class_specifier", "struct_specifier")
                and find_child_by_type(cnode, "field_declaration_list") is None
            ):
                continue

            is_enum = cnode.type in ("enum_declaration", "enum_definition", "enum_specifier", "enum_item")
            # SFX-2 (TC-MT040-42: now handled inside _extract_doc_comment via
            # extraction/lang/go.py's doc_anchor() -- for Go type_spec nodes,
            # the godoc comment precedes the parent type_declaration node,
            # not the type_spec itself; doc_anchor() redirects to the parent
            # so this call site no longer needs its own special case).
            cdoc = _extract_doc_comment(cnode, language)
            bases = _extract_bases(cnode, language)

            # Python: detect enums by base class inheritance
            if not is_enum and language == "python":
                if set(bases) & _PY_ENUM_BASES:
                    is_enum = True

            if is_enum:
                if language == "python":
                    enum_members = _extract_python_enum_members(cnode)
                else:
                    enum_members = _extract_enum_members(cnode, language)
            elif language == "python":
                # Convention-based enum detection: plain classes whose
                # body consists entirely of ALL_CAPS assignments are
                # treated as enum-like (common in 3D/Python libraries).
                candidates = _extract_python_enum_members(cnode)
                if candidates and all(m["name"].replace("_", "").isupper() for m in candidates):
                    enum_members = candidates
                    is_enum = True
                else:
                    enum_members = []
            else:
                enum_members = []

            methods: list[dict[str, Any]] = []
            properties: list[dict[str, Any]] = []
            # TC-521: the keys a JavaScript constructor destructures are the fields the class takes.
            ctor_keys: list[tuple[dict[str, Any], int]] = []

            method_nodes = collect_nodes(cnode, func_types)
            for mnode in method_nodes:
                # Skip members whose declaration lives inside a C#
                # `#if DEBUG` / `#elif JAVA` / `#if CPLUSPLUS` / etc. branch
                # that is unreachable in a real .NET Release build -- this
                # source is shared across the Java/C++/Python ports of the
                # same product, and Debug-only helpers or other-language-only
                # overrides were leaking into api_surface.json and being
                # rendered as real public .NET API. Found 2026-07-23 via an
                # independent review of the words/net launch (Node.cs's
                # `dd()` and its `#elif JAVA`-only `ToString()` override).
                if _in_excluded_preproc_branch(mnode, language):
                    continue
                if not is_public(mnode, language):
                    continue
                mname = _node_name(mnode, language)
                if not mname:
                    continue

                # TC-523: a member whose JSDoc says @internal / @hidden / @ignore is not public API.
                if language in ("typescript", "javascript") and _ts_is_hidden(mnode):
                    continue

                # FIX-02: Skip methods that belong to nested classes.
                # collect_nodes does a deep DFS, so methods inside nested
                # private/internal classes appear in the parent's list.
                # Check if mnode has a class ancestor that is NOT cnode itself.
                if language in ("csharp", "java"):
                    _parent = mnode.parent
                    _in_nested = False
                    while _parent is not None and _parent != cnode:
                        if _parent.type in class_types:
                            _in_nested = True
                            break
                        _parent = _parent.parent
                    if _in_nested:
                        continue

                # Skip Python @property and setter/deleter â€” handled by _extract_python_properties.
                # Checks the full decorator stack (see _all_decorator_text), not just the
                # single nearest decorator â€” a bare @abstractmethod check would otherwise
                # miss @property stacked with another decorator (root cause of ST-014).
                if language == "python" and mnode.type == "function_definition":
                    dec_text = _all_decorator_text(mnode)
                    if "@property" in dec_text or ".setter" in dec_text or ".deleter" in dec_text:
                        continue

                # FR-14: TypeScript getter/setter accessors â†’ kind: property, not callable
                if language in ("typescript", "javascript"):
                    if mnode.type in ("get_signature", "set_signature"):
                        # Explicit TS accessor signature nodes
                        acc_mode = "readonly" if mnode.type == "get_signature" else "writeonly"
                        ptype = ""
                        type_node = child_by_field(mnode, "return_type") or find_child_by_type(
                            mnode, "type_annotation"
                        )
                        if type_node:
                            ptype = node_text(type_node).lstrip(":").strip()
                        # Merge with existing same-name property if present
                        existing_prop = next(
                            (p for p in properties if p.get("name") == mname and p.get("kind") == "property"),
                            None,
                        )
                        if existing_prop is not None:
                            existing_prop["access_mode"] = "readwrite"
                        else:
                            properties.append(
                                {
                                    "name": mname,
                                    "type": ptype,
                                    "kind": "property",
                                    "access_mode": acc_mode,
                                    "doc": "",
                                    "line": mnode.start_point[0] + 1,
                                }
                            )
                            claims.append(
                                _make_claim(
                                    family,
                                    "api_method",
                                    f"{cname}.{mname} property of type {ptype}",
                                    rel,
                                    mnode.start_point[0] + 1,
                                )
                            )
                        continue
                    # method_definition that begins with 'get ' or 'set '
                    if mnode.type == "method_definition":
                        raw_txt = node_text(mnode).lstrip()
                        is_getter = raw_txt.startswith("get ")
                        is_setter = raw_txt.startswith("set ")
                        if is_getter or is_setter:
                            acc_mode = "readonly" if is_getter else "writeonly"
                            ptype = ""
                            if is_getter:
                                type_node = child_by_field(mnode, "return_type") or find_child_by_type(
                                    mnode, "type_annotation"
                                )
                                if type_node:
                                    ptype = node_text(type_node).lstrip(":").strip()
                            existing_prop = next(
                                (
                                    p
                                    for p in properties
                                    if p.get("name") == mname and p.get("kind") == "property"
                                ),
                                None,
                            )
                            if existing_prop is not None:
                                existing_prop["access_mode"] = "readwrite"
                                if is_getter and ptype and not existing_prop.get("type"):
                                    existing_prop["type"] = ptype
                            else:
                                properties.append(
                                    {
                                        "name": mname,
                                        "type": ptype,
                                        "kind": "property",
                                        "access_mode": acc_mode,
                                        "doc": "",
                                        "line": mnode.start_point[0] + 1,
                                    }
                                )
                                claims.append(
                                    _make_claim(
                                        family,
                                        "api_method",
                                        f"{cname}.{mname} property of type {ptype}",
                                        rel,
                                        mnode.start_point[0] + 1,
                                    )
                                )
                            continue

                if mnode.type in ("property_declaration", "public_field_definition", "property_signature"):
                    ptype = ""
                    type_node = child_by_field(mnode, "type")
                    if type_node:
                        ptype = node_text(type_node)
                    # Detect setter â€” C# accessor_list with "set" or "init"
                    writable = False
                    acc_list = find_child_by_type(mnode, "accessor_list")
                    if acc_list is not None:
                        for acc in acc_list.children:
                            if acc.type == "accessor_declaration":
                                acc_text = node_text(acc).lstrip()
                                if acc_text.startswith(("set", "init")):
                                    writable = True
                                    break
                    prop_entry: dict[str, Any] = {
                        "name": mname,
                        "type": ptype,
                        "doc": _extract_doc_comment(mnode, language),
                        "line": mnode.start_point[0] + 1,
                        "writable": writable,
                    }

                    # TC-MT040-13: TypeScript class fields carry static/
                    # readonly as direct modifier-TOKEN children (verified
                    # via parse probe -- `static`/`readonly` are their own
                    # child nodes, not bundled into a "modifiers" node the
                    # way C#/Java do it) -- a wholly separate mechanism from
                    # the accessor_list check above (which is C#-only).
                    # Previously every TS public_field_definition was
                    # hardcoded writable: False regardless of whether it was
                    # actually a plain mutable field.
                    if language in ("typescript", "javascript"):
                        # TC-523: an interface / object-type member is writable unless ``readonly``;
                        # ``static`` and ``?`` are the additive ``modifiers``.
                        _ts_is_readonly, _ts_is_optional, _ts_is_static, _ = _ts_member_flags(mnode)
                        prop_entry["writable"] = not _ts_is_readonly
                        _ts_prop_modifiers = [
                            mark
                            for mark, applies in (("static", _ts_is_static), ("optional", _ts_is_optional))
                            if applies
                        ]
                        if _ts_prop_modifiers:
                            prop_entry["modifiers"] = _ts_prop_modifiers
                    if language == "typescript" and mnode.type == "public_field_definition":
                        if _ts_is_static:
                            prop_entry["is_static"] = True
                        _ts_value_node = child_by_field(mnode, "value")
                        _ts_is_literalish = _ts_value_node is not None and (
                            _ts_value_node.type in _TS_LITERALISH_TYPES
                            or _ts_value_node.type == "unary_expression"
                        )
                        if (_ts_is_static and _ts_is_readonly) or (_ts_is_static and _ts_is_literalish):
                            prop_entry["kind"] = "constant"
                            if _ts_value_node is not None:
                                prop_entry["value"] = node_text(_ts_value_node)[:80]

                    if language == "csharp":
                        prop_modifiers = _csharp_modifiers(mnode)
                        if prop_modifiers:
                            prop_entry["modifiers"] = prop_modifiers

                    properties.append(prop_entry)
                    claims.append(
                        _make_claim(
                            family,
                            "api_method",
                            f"{cname}.{mname} property of type {ptype}",
                            rel,
                            mnode.start_point[0] + 1,
                        )
                    )
                    continue

                params = _extract_method_params(mnode, language)
                ret = _extract_return_type(mnode, language)
                mdoc = _extract_doc_comment(mnode, language)
                m_deprecated, m_deprecated_reason = deprecated_marker(mnode, language)

                is_ctor = (
                    mnode.type == "constructor_declaration"
                    or (language == "python" and mname == "__init__")
                    or (language in ("typescript", "javascript") and mname == "constructor")
                )

                # FR-16: stub detection â€” methods that only raise/throw NotImplemented*
                method_entry: dict[str, Any] = {
                    "name": mname,
                    "params": params,
                    "return_type": ret,
                    "doc": mdoc,
                    "line": mnode.start_point[0] + 1,
                    "is_constructor": is_ctor,
                    "deprecated": m_deprecated,
                    "deprecated_reason": m_deprecated_reason,
                }
                if language == "csharp":
                    method_modifiers = _csharp_modifiers(mnode)
                    if method_modifiers:
                        method_entry["modifiers"] = method_modifiers
                if language in ("typescript", "javascript"):
                    # TC-523: ``static``, ``async`` and an optional interface method (``m?()``).
                    _, _ts_m_optional, _ts_m_static, _ts_m_async = _ts_member_flags(mnode)
                    ts_method_modifiers = [
                        mark
                        for mark, applies in (
                            ("static", _ts_m_static),
                            ("async", _ts_m_async),
                            ("optional", _ts_m_optional),
                        )
                        if applies
                    ]
                    if ts_method_modifiers:
                        method_entry["modifiers"] = ts_method_modifiers
                if language == "python" and _is_python_stub_any(mnode):
                    method_entry["stub"] = True
                elif language == "csharp" and _is_csharp_stub(mnode):
                    method_entry["stub"] = True
                elif language == "java" and _is_java_stub(mnode):
                    method_entry["stub"] = True
                elif language in ("typescript", "javascript") and _is_typescript_stub(mnode):
                    method_entry["stub"] = True
                elif language == "cpp" and _is_cpp_stub(mnode):
                    method_entry["stub"] = True

                if language in ("typescript", "javascript"):
                    _apply_ts_tsdoc_enrichment(method_entry, mnode)

                if language == "javascript" and is_ctor:
                    for ctor_param in params:
                        ctor_keys.extend(
                            (key, mnode.start_point[0] + 1) for key in ctor_param.get("destructured", [])
                        )

                methods.append(method_entry)
                claims.append(
                    _make_claim(
                        family,
                        "api_method",
                        f"{cname}.{mname}({', '.join(p['name'] for p in params)}) -> {ret}",
                        rel,
                        mnode.start_point[0] + 1,
                    )
                )

            # TC-521: every destructured constructor key not already a property becomes one.
            property_names = {prop["name"] for prop in properties}
            for key, key_line in ctor_keys:
                if key["name"] in property_names:
                    continue
                property_names.add(key["name"])
                properties.append(
                    {"name": key["name"], "type": key["type"], "writable": True, "doc": key["doc"]}
                )
                claims.append(
                    _make_claim(
                        family,
                        "api_method",
                        f"{cname}.{key['name']} property of type {key['type']}",
                        rel,
                        key_line,
                    )
                )

            # C# indexers (`this[...]`) are PROPERTY entries (TC-396)
            if language == "csharp":
                extra_props, extra_claims = _extract_csharp_indexers(cnode, class_types, cname, rel, family)
                properties.extend(extra_props)
                claims.extend(extra_claims)

            # Python properties via decorators
            if language == "python":
                extra_props, extra_claims = _extract_python_properties(cnode, cname, rel, family)
                properties.extend(extra_props)
                claims.extend(extra_claims)

            # Python annotated fields (dataclass fields, TypedDict members, etc.)
            if language == "python":
                extra_fields, extra_claims = _extract_python_annotated_fields(cnode, cname, rel, family)
                properties.extend(extra_fields)
                claims.extend(extra_claims)

            # Python __init__ instance attributes (self.xxx = value)
            if language == "python":
                existing_prop_names = {p["name"] for p in properties}
                init_props, init_claims = _extract_python_init_attributes(
                    cnode, cname, rel, family, existing_prop_names
                )
                properties.extend(init_props)
                claims.extend(init_claims)

            # Java properties via getter/setter synthesis
            if language == "java":
                extra_props, extra_claims = _synthesize_java_properties(methods, cname, rel, family)
                properties.extend(extra_props)
                claims.extend(extra_claims)

            # C++ properties via getter/setter synthesis
            if language == "cpp":
                extra_props, extra_claims = _synthesize_cpp_properties(methods, cname, rel, family)
                properties.extend(extra_props)
                claims.extend(extra_claims)

            # C# public const / static readonly fields + Java public static final fields
            # These are accessed like properties in content (e.g. CfbConstants.RootStreamId)
            # but are NOT captured by the method/property_declaration pass above.
            if language in ("csharp", "java"):
                extra_props, extra_claims = _extract_const_fields(cnode, cname, rel, family, language)
                properties.extend(extra_props)
                claims.extend(extra_claims)

            # C++ members: constructors, destructors, operators, methods (with full declarator text and
            # modifiers) AND public data members (TC-524)
            if language == "cpp":
                cpp_methods, cpp_properties, cpp_claims = _extract_cpp_members(cnode, cname, rel, family)
                methods.extend(cpp_methods)
                properties.extend(cpp_properties)
                claims.extend(cpp_claims)

            # Go exported struct fields (e.g. RenderOptions.DPI, Rectangle.LLX)
            if language == "go":
                extra_props, extra_claims = _extract_go_struct_fields(cnode, cname, rel, family)
                properties.extend(extra_props)
                claims.extend(extra_claims)

            # Go interface method signatures (e.g. Annotation.Rect/SetColor/
            # Flatten) -- interface bodies have no struct_type, so the block
            # above is a no-op for them; this is the dedicated path.
            if language == "go":
                extra_methods, extra_iface_claims = _extract_go_interface_methods(cnode, cname, rel, family)
                methods.extend(extra_methods)
                claims.extend(extra_iface_claims)

            # Rust pub struct fields (e.g. Workbook.path)
            if language == "rust":
                extra_props, extra_claims = _extract_rust_struct_fields(cnode, cname, rel, family)
                properties.extend(extra_props)
                claims.extend(extra_claims)

            vis = visibility_tier(
                cnode,
                language,
                has_docstring=bool(cdoc),
                in_module_all=(cname in module_all_names),
            )
            # H-04d: Override visibility for vendor/private directory classes
            if fpath in _vendor_files:
                vis = "internal"
            # OBS-03: Remove self-referential bases (tree-sitter extraction bug)
            bases = [b for b in bases if b != cname]

            # TC-MT007-01 (G-40): synthesize standard interface-contract members
            # (Dispose, MoveNext/Reset/Current, GetEnumerator, CompareTo) for
            # classes implementing a known BCL interface without an explicit
            # same-file override. C#-only; see synthesize_interface_members's
            # own docstring for the full finding and scope rationale.
            _synth_methods, _synth_properties = synthesize_interface_members(
                bases, methods, properties, language
            )
            methods = methods + _synth_methods
            properties = properties + _synth_properties

            # G-21: every method/property dict built above (13+ append sites
            # across this function and the per-language helpers it calls --
            # _extract_python_properties, _synthesize_java_properties,
            # _synthesize_cpp_properties, _extract_go_struct_fields,
            # _extract_go_interface_methods, _extract_rust_struct_fields, and
            # the inline appends in this loop) carries "line" but never
            # "file" -- only the class-level record does (cls_record["file"]
            # below). _flatten_inheritance() deep-copies these dicts verbatim
            # into every subclass, so a consumer pairing a subclass's own
            # cls["file"] with an INHERITED member's "line" gets a bogus
            # (subclass-file, base-class-line) citation -- confirmed on
            # words/net: 94/617 classes (every direct Field subclass) cite a
            # base-class method's real line number against the subclass's own
            # file. Single backfill point here (after every append/extend for
            # this class has already happened, before cls_record is built)
            # is comprehensive by construction: every method/property in
            # `methods`/`properties` by this point, regardless of which
            # helper produced it, gets its true declaring file recorded once,
            # before any inheritance copying can happen.
            for _m in methods:
                _m.setdefault("file", rel)
            for _p in properties:
                _p.setdefault("file", rel)

            # Extract class-level modifiers (C#/Java: static, abstract, sealed)
            # Uses the same tree-sitter pattern as _extract_const_fields (L664).
            _cls_mods: set[str] = set()
            if language in ("csharp", "java"):
                for _ch in cnode.children:
                    if _ch.type in ("modifiers", "modifier"):
                        _cls_mods.update(node_text(_ch).lower().split())

            # RC-W1-004: additive reachability signal (see _compute_reachable).
            reachable = _compute_reachable(cname, fpath, rel)
            c_deprecated, c_deprecated_reason = deprecated_marker(cnode, language)

            cls_record: dict[str, Any] = {
                "name": cname,
                "kind": cnode.type,
                "doc": cdoc,
                "file": rel,
                "line": cnode.start_point[0] + 1,
                "bases": bases,
                "methods": methods,
                "properties": properties,
                "visibility": vis,
                "reachable": reachable,
                "deprecated": c_deprecated,
                "deprecated_reason": c_deprecated_reason,
            }
            if "static" in _cls_mods:
                cls_record["is_static"] = True
            if "abstract" in _cls_mods:
                cls_record["is_abstract"] = True
            if "sealed" in _cls_mods:
                cls_record["is_sealed"] = True
            if "partial" in _cls_mods:
                cls_record["is_partial"] = True
            # TC-MT040-10: TypeScript expresses `abstract` as a distinct
            # grammar-level node type (abstract_class_declaration), not a
            # modifier keyword token the way C#/Java do -- no _cls_mods
            # membership check applies here.
            if language == "typescript" and cnode.type == "abstract_class_declaration":
                cls_record["is_abstract"] = True
            if is_enum:
                cls_record["enum_members"] = enum_members
            # TC-523: a TypeScript type alias keeps its right-hand side (one line, cut at the limit).
            if language == "typescript" and cnode.type == "type_alias_declaration":
                alias_text = _ts_alias_text(cnode)
                if alias_text:
                    cls_record["alias_of"] = alias_text

            # FR-15: canonical_namespace (C++, C#) and class_package (Java)
            if language == "cpp":
                canonical_ns = _cpp_canonical_namespace(cnode)
                if canonical_ns:
                    cls_record["canonical_namespace"] = canonical_ns
                    cls_record["class_import"] = f"{canonical_ns}::{cname}"
                else:
                    cls_record["class_import"] = cname
            elif language == "java":
                # java_file_package is captured at file level above the class loop
                #
                # TC-241: a nested class (declared inside another
                # class/interface/enum/record/annotation-type) must carry its
                # enclosing type in class_import/canonical_namespace -- Java
                # previously had no ancestor walk at all here, so e.g.
                # jmap/java's real JmapClientOptions.Builder recorded
                # class_import="com.aspose.jmap.Builder", silently dropping
                # "JmapClientOptions" and producing a non-importable name.
                # _java_enclosing_class_chain() mirrors the C++/C#
                # ancestor-walk fix above and returns "" for the overwhelming
                # non-nested case, so behavior there is completely unchanged.
                enclosing_chain = _java_enclosing_class_chain(cnode)
                if java_file_package:
                    cls_record["class_package"] = java_file_package
                    if enclosing_chain:
                        cls_record["class_import"] = f"{java_file_package}.{enclosing_chain}.{cname}"
                    else:
                        cls_record["class_import"] = f"{java_file_package}.{cname}"
                    # TC-DUPIDX-06 (2026-08-12, ST-059): also mirror into
                    # canonical_namespace -- the field _assign_namespace_slug_
                    # suffixes() in batch_reference.py reads for same-name
                    # collision disambiguation when generating grouped reference
                    # indexes. Java's class_package always carried this exact
                    # information, but it was never surfaced under the field name
                    # the disambiguator actually looks for, so every Java product's
                    # same-package-name collisions went undisambiguated regardless
                    # of how many times the index was regenerated (real incident:
                    # pdf/java alone reached 28 distinct undisambiguated collisions
                    # after a knowledge refresh grew its class count from 527 to
                    # 1027). This completes, for Java, the same rollout already done
                    # for C++ (day one), C# (2026-06-13), Rust (day one), and Python
                    # (2026-07-29, MT013, fde65ade26) for this exact purpose.
                    #
                    # TC-241: fold the enclosing-class chain in here too, the
                    # same way C++/C#'s canonical_namespace already does via
                    # their own ancestor walk, so a nested class's
                    # same-name-collision disambiguation key is the real
                    # enclosing scope, not just the file's package.
                    if enclosing_chain:
                        cls_record["canonical_namespace"] = f"{java_file_package}.{enclosing_chain}"
                    else:
                        cls_record["canonical_namespace"] = java_file_package
                else:
                    # No java_file_package captured for this file (default
                    # package) -- out of this card's contract (which keys
                    # exclusively off java_file_package), so left exactly as
                    # before this fix.
                    cls_record["class_import"] = cname
            elif language == "csharp":
                canonical_ns = _csharp_canonical_namespace(cnode)
                if canonical_ns:
                    cls_record["canonical_namespace"] = canonical_ns
                    cls_record["class_import"] = f"{canonical_ns}.{cname}"
                else:
                    cls_record["class_import"] = cname
            elif language == "python":
                # Derive from file path relative to pkg_root
                #
                # HARDEN-py-canonical-namespace (2026-07-28, MT013): this
                # branch computed module_path for class_import but never
                # exposed it as canonical_namespace -- the field the existing
                # same-name disambiguation mechanism in batch_reference.py /
                # api_completeness.py reads (already proven for C++/Rust).
                # Without it, two genuinely distinct Python classes sharing a
                # short name across different modules (e.g. pdf/python's
                # `Document` in both aspose_pdf/document.py -- the primary
                # implementation -- and aspose_pdf/generated/document.py -- a
                # real, separate compatibility-shim class, not a stub) could
                # never be disambiguated and both failed page generation
                # entirely ("within-batch case-collision"). Populating this
                # mirrors the Rust branch below.
                try:
                    py_rel = fpath.relative_to(pkg_root)
                    parts = list(py_rel.parts)
                    if parts and parts[-1].endswith(".py"):
                        parts[-1] = parts[-1][:-3]
                    if parts and parts[-1] == "__init__":
                        parts = parts[:-1]
                    if parts:
                        module_path = ".".join(parts)
                        cls_record["canonical_namespace"] = module_path
                        cls_record["class_import"] = f"{module_path}.{cname}"
                except ValueError:
                    pass
            elif language == "typescript":
                # TC-DUPIDX-06 (2026-08-12, ST-059): TypeScript had NO namespace-
                # equivalent field at all before this branch -- not canonical_
                # namespace, not class_import, nothing. Confirmed live on
                # cells/typescript: 7 genuine same-name collisions (verified against
                # real upstream source, not a scout artifact -- 3 are byte-identical
                # type aliases independently declared in two files, 4 are genuine
                # `interface X` + `class X` pairs sharing a name via deliberate
                # `import ... as XType` aliasing), none disambiguable without a
                # namespace signal to key on. Derive module_path from file location
                # relative to pkg_root, mirroring the Python branch above exactly
                # (TypeScript's one-file-per-module convention resembles Python's
                # far more than Java's package-per-directory or C++'s namespace-per-
                # declaration model).
                try:
                    ts_rel = fpath.relative_to(pkg_root)
                    parts = list(ts_rel.parts)
                    if parts and (parts[-1].endswith(".ts") or parts[-1].endswith(".tsx")):
                        parts[-1] = re.sub(r"\.tsx?$", "", parts[-1])
                    if parts and parts[-1] == "index":
                        parts = parts[:-1]
                    # TC-MT040-15: prefix with the enclosing namespace/module
                    # chain (if any), IN ADDITION to the file-path-derived
                    # segments above -- a class declared inside `namespace
                    # Foo { ... }` is imported as `Foo.ClassName`, not merely
                    # under its physical file's own module segment.
                    _ts_ns_chain = _ts_namespace_chain(cnode)
                    if _ts_ns_chain:
                        parts = _ts_ns_chain.split(".") + parts
                    if parts:
                        module_path = ".".join(parts)
                        cls_record["canonical_namespace"] = module_path
                        cls_record["class_import"] = f"{module_path}.{cname}"
                except ValueError:
                    pass
            elif language == "javascript":
                # TC-315: javascript had NO namespace-equivalent field at all
                # before this branch -- not canonical_namespace, not
                # class_import, nothing -- exactly the same gap TC-DUPIDX-06
                # already closed for typescript above, but javascript is the
                # language this project's own "nodejs" pilots actually
                # extract under (confirmed live: a fresh jmap/nodejs
                # extraction reports `"language": "javascript"`), not
                # typescript, so the gap was never actually closed for any
                # real nodejs pilot. Without a namespace signal, two
                # same-bare-name classes from different files collide into
                # one consolidate_classes() dedup group as if they were the
                # same type -- live-confirmed on the real, pinned jmap/nodejs
                # repository (aspose-email-foss/Aspose.JMAP-FOSS-for-Node.js):
                # src/client-core.js declares the real, full `JmapClient`
                # (bases=[]), and src/index.js separately declares a
                # near-empty wrapper also named `JmapClient`
                # (`class JmapClient extends CoreClient {}` via
                # `import { JmapClient as CoreClient } from "./client-core.js"`,
                # an alias that defeats consolidate_classes' own Category 3
                # shim-detection, which only matches a base literally named
                # after the dropped duplicate). Both landed in the same ns=""
                # group and hit Category 2 ("stub vs full": a class with
                # `bases` is assumed more complete than one without) --
                # backwards here, so the real, full implementation was
                # discarded in favor of the near-empty wrapper.
                #
                # Derive module_path from file location relative to pkg_root,
                # mirroring the typescript branch above exactly -- EXCEPT for
                # its _ts_namespace_chain() call, which reads a TypeScript-
                # only `namespace { }` construct with no JavaScript
                # equivalent (the corresponding tree-sitter node type does
                # not exist in a JS parse tree at all, so that call is
                # deliberately omitted here).
                #
                # Extension handling: live-checked against this project's one
                # real nodejs pilot (jmap/nodejs, pinned to
                # aspose-email-foss/Aspose.JMAP-FOSS-for-Node.js) -- its
                # entire src/ tree uses only `.js`, never `.mjs`/`.cjs`/
                # `.jsx`. Only `.js` is stripped here rather than guessing the
                # full set blindly; revisit if a future nodejs pilot is
                # pinned to a repository that actually uses one of the
                # others.
                try:
                    js_rel = fpath.relative_to(pkg_root)
                    parts = list(js_rel.parts)
                    if parts and parts[-1].endswith(".js"):
                        parts[-1] = parts[-1][:-3]
                    if parts and parts[-1] == "index":
                        parts = parts[:-1]
                    if parts:
                        module_path = ".".join(parts)
                        cls_record["canonical_namespace"] = module_path
                        cls_record["class_import"] = f"{module_path}.{cname}"
                except ValueError:
                    pass
            elif language == "rust":
                # Module path derived from file location relative to pkg_root
                # (src/): src/worksheet.rs â†’ "worksheet", src/foo/mod.rs â†’
                # "foo", src/lib.rs â†’ crate root (no module segment).
                # Segments that are not valid Rust module identifiers are
                # dropped: crates like Aspose.Cells-FOSS-for-Rust keep source
                # under a dotted directory (src/Aspose.Cells_FOSS/) wired via
                # `#[path = "..."] mod api;` and re-export every type at the
                # crate root â€” a path like `Aspose.Cells_FOSS::Workbook` is
                # not a real Rust path, so such types get the bare type name
                # (crate-root import), the Rust analog of PY-INIT-001.
                try:
                    rs_rel = fpath.relative_to(pkg_root)
                    parts = list(rs_rel.parts)
                    if parts and parts[-1].endswith(".rs"):
                        parts[-1] = parts[-1][:-3]
                    if parts and parts[-1] in ("lib", "main", "mod"):
                        parts = parts[:-1]
                    # ANY invalid segment means the layout is #[path]-mapped â€”
                    # the whole derived path is unreliable, not just that
                    # segment (the file-stem "module" under a dotted dir is
                    # not a real module either).
                    if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", p) for p in parts):
                        parts = []
                    if parts:
                        module_path = "::".join(parts)
                        cls_record["canonical_namespace"] = module_path
                        cls_record["class_import"] = f"{module_path}::{cname}"
                    else:
                        cls_record["class_import"] = cname
                except ValueError:
                    cls_record["class_import"] = cname

            classes.append(cls_record)
            claims.append(
                _make_claim(
                    family, "api_class", f"Class {cname} defined in {rel}", rel, cnode.start_point[0] + 1
                )
            )

        # top-level functions (not inside classes)
        # TC-524: C++ free functions come from public headers only (declared or defined inline), never from
        # the generic loop below, which would publish every file-static helper a .cpp file defines.
        if language == "cpp":
            cpp_funcs, cpp_func_claims = _extract_cpp_free_functions(
                root, fpath, pkg_root, rel, family, _compute_reachable
            )
            classes.extend(cpp_funcs)
            claims.extend(cpp_func_claims)
        top_funcs = [] if language == "cpp" else collect_nodes(root, func_types)
        for fnode in top_funcs:
            # skip if nested inside a class
            parent = fnode.parent
            in_class = False
            while parent:
                if parent.type in class_types:
                    in_class = True
                    break
                parent = parent.parent
            if in_class:
                continue
            # TC-051: a 'property_signature' reaching this unscoped top-level
            # scan is only a genuine construct when it is a direct member of
            # an interface_declaration/type_alias_declaration body -- never
            # when it's a property key of an inline object-type literal used
            # as a parameter/variable/return-type annotation elsewhere (whose
            # ancestor chain never passes through class_types, so the in_class
            # check above cannot catch it). See _is_real_typescript_property_signature.
            if fnode.type == "property_signature" and not _is_real_typescript_property_signature(fnode):
                continue
            if not is_public(fnode, language):
                continue
            fname = _node_name(fnode, language)
            if not fname:
                continue
            params = _extract_method_params(fnode, language)
            ret = _extract_return_type(fnode, language)
            fdoc = _extract_doc_comment(fnode, language)

            # SFX-1: For Go methods, capture receiver type so _associate_go_methods
            # can move them into their parent type's methods[] list later.
            receiver_type = ""
            rust_trait = ""
            if language == "go":
                receiver_type = _extract_go_receiver_type(fnode)
            elif language == "rust":
                # Rust impl-block methods surface here (impl_item is not a
                # class-type node); capture the impl target + trait so
                # _associate_rust_impl_methods can relocate them.
                receiver_type, rust_trait = _extract_rust_impl_context(fnode)

            func_entry: dict[str, Any] = {
                "name": fname,
                "kind": "function",
                "doc": fdoc,
                "file": rel,
                "line": fnode.start_point[0] + 1,
                "bases": [],
                "methods": [],
                "properties": [],
                "params": params,
                "return_type": ret,
                # RC-W1-004: for Rust/Go this entry is usually transient (it
                # gets relocated into its parent type's methods[] list by
                # _associate_rust_impl_methods / _associate_go_methods and
                # dropped from the top-level list below); the field is set
                # here mainly so genuinely top-level Python module functions
                # get the same additive signal as classes.
                "reachable": _compute_reachable(fname, fpath, rel),
            }
            if receiver_type:
                func_entry["receiver_type"] = receiver_type
            if rust_trait:
                func_entry["trait_impl"] = rust_trait
            if language in ("typescript", "javascript"):
                _apply_ts_tsdoc_enrichment(func_entry, fnode)
            classes.append(func_entry)
            claims.append(
                _make_claim(
                    family,
                    "api_method",
                    f"Function {fname}({', '.join(p['name'] for p in params)}) -> {ret}",
                    rel,
                    fnode.start_point[0] + 1,
                )
            )

        # TC-MT040-13: module-level `export const` declarations -- classes,
        # functions, and top-level functions above are the only three shapes
        # func_types/class_types nodes cover; a bare `export const X = ...`
        # (constant OR arrow-function-valued) is neither and was previously
        # invisible to extraction entirely.
        if language == "typescript":
            _ts_const_entries, _ts_const_claims = _extract_ts_module_constants(
                root, rel, fpath, family, _compute_reachable
            )
            classes.extend(_ts_const_entries)
            claims.extend(_ts_const_claims)

    # SFX-1: Associate Go receiver methods with their parent type entries.
    # Go methods are top-level nodes (not children of type nodes), so the
    # collect_nodes pass inside the class loop finds 0 methods. This pass
    # moves them into the correct type's methods[] list.
    if language == "go":
        classes = _associate_go_methods(classes)

    # Rust: same shape as Go â€” impl-block methods are top-level nodes, so
    # relocate them into their owning struct/enum/trait entries.
    if language == "rust":
        classes = _associate_rust_impl_methods(classes)

    # Drop abandoned top-level compatibility shims and unreferenced
    # internal duplicates before the generic different-namespace-keeps-both
    # consolidation logic runs (see _resolve_python_shim_collisions).
    if language == "python":
        classes = _resolve_python_shim_collisions(classes, files, repo)

    # TC-521: JavaScript composes classes from object-literal mixins and aliased imports. Resolve
    # the aliased bases and attach the mixin methods BEFORE consolidate_classes and
    # _flatten_inheritance, which must see the wrapper class with its real bases and members.
    if language == "javascript":
        _resolve_js_alias_bases(classes, js_imports)
        for mixin_class, mixin_entry in _attach_js_mixins(classes, js_mixin_defs, js_mixin_applications):
            mixin_params = ", ".join(p["name"] for p in mixin_entry["params"])
            claims.append(
                _make_claim(
                    family,
                    "api_method",
                    f"{mixin_class['name']}.{mixin_entry['name']}({mixin_params}) -> {mixin_entry['return_type']}",
                    mixin_entry["file"],
                    mixin_entry["line"],
                )
            )

    # Consolidate partial classes, deduplicate enums, disambiguate same-name
    # types in different namespaces.  Must run BEFORE _flatten_inheritance so
    # the by-name dict used for inheritance resolution is correct.
    classes = consolidate_classes(classes, language)

    # TC-HCR-001: Flatten inherited methods/properties into child classes
    # so that evaluators can verify inherited member access without walking
    # the inheritance chain at evaluation time.
    _flatten_inheritance(classes)

    # PY-INIT-001: Mark classes re-exported via any package __init__.py.__all__.
    # The per-file __all__ extraction (S-1) only fires when __all__ and class
    # definitions share the same file.  Packages that define classes in _internal/
    # and re-export them from __init__.py have their public API understated by
    # _is_public_api_entry() in index.py.  This post-pass adds the field
    # exported_via_package_init: true so index.py can override the path filter.
    if language == "python":
        _mark_package_init_exports(classes, files, pkg_root=pkg_root)

    scout_report: dict[str, Any] = {
        "files_attempted": files_attempted,
        "files_parsed": files_attempted - len(files_skipped),
        "files_skipped": files_skipped,
        "skip_reasons": skip_reasons,
        "parse_errors": parse_errors,
        "parse_error_rate": len(parse_errors) / max(files_attempted, 1),
        # HARDEN-words-net: files_attempted above already reflects the
        # post-MAX_FILES-cap count, so it alone can't reveal truncation (a
        # capped product and one that fit comfortably look identical). These
        # two fields make that fact durable in the artifact itself.
        "total_source_files_found": _collection_stats.get("candidates_found", files_attempted),
        "files_capped": _collection_stats.get("files_capped", False),
    }
    # TC-MT040-11: operator-visible evidence of what the TS/JS barrel
    # resolution resolved to. Deliberately only added to the report for
    # typescript/javascript (never an always-present, empty-for-everyone-
    # else key) -- extraction_goldens.py's byte-for-byte report parity
    # check for python/rust caught exactly this when it was first added
    # unconditionally; this constant's own file-scope docstring is explicit
    # that non-TS/JS extraction behavior must not change in any way a
    # regression test would catch.
    if language in ("typescript", "javascript"):
        scout_report["ts_entry_point"] = _ts_entry_point_rel
    # Task 16 (Â§9.11, closes half of G10): stabilize member-claim IDs against
    # a "ClassName.MemberName" anchor instead of the full claim text -- see
    # _stabilize_scout_claim_ids' own docstring for the full rationale and the
    # documented, one-time transition cost this incurs on next re-scout.
    claims = _stabilize_scout_claim_ids(claims, family)
    return classes, claims, scanned_files, packages, scout_report
