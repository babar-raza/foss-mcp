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
import re
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

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
from foss_mcp.extraction.tree_sitter_engine import lang


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
_VENDOR_PATH_SEGMENTS: frozenset[str] = frozenset({
    "vendor", "vendored", "third_party", "thirdparty",
})

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
            if (part.startswith("_") and not part.startswith("__")
                    and part not in _EXEMPT_PRIVATE_DIRS):
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

def _extract_all_from_init(init_path: Path) -> "set[str] | None":
    """Return the name set from a single __init__.py's top-level __all__.

    See extraction/lang/python.py's ``_extract_all_from_init`` (the real,
    single implementation this delegates to) for the full docstring.
    """
    return lang.python._extract_all_from_init(init_path)


def _python_top_level_exports(pkg_root: Path) -> "tuple[set[str] | None, Path | None]":
    """Return (export_names, export_root) for the package's real public surface.

    See extraction/lang/python.py's ``top_level_exports`` (the real, single
    implementation this delegates to) for the full docstring.
    """
    return lang.python.top_level_exports(pkg_root)


def _rust_reexported_names(pkg_root: Path) -> "set[str] | None":
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

def _make_claim(family: str, kind: str, text: str,
                file: str = "", line: int = 0) -> dict:
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
        winner = idxs[0] if len(idxs) == 1 else min(
            idxs, key=lambda i: claims[i].get("text", "")
        )
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
            stable = hashlib.sha256(
                (anchor + "|" + c.get("kind", "")).encode("utf-8")
            ).hexdigest()[:8]
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

def _extract_method_params(node, language: str) -> list[dict[str, str]]:
    """Extract parameters from a method/function node."""
    params: list[dict[str, str]] = []
    param_list = (child_by_field(node, "parameters")
                  or find_child_by_type(node, "formal_parameters")
                  or find_child_by_type(node, "parameter_list")
                  or find_child_by_type(node, "parameters"))
    if param_list is None:
        return params

    for ch in param_list.children:
        if ch.type in ("formal_parameter", "parameter", "required_parameter",
                        "optional_parameter", "typed_parameter",
                        "default_parameter", "typed_default_parameter",
                        "identifier", "parameter_declaration",
                        "optional_parameter_declaration",
                        "variadic_parameter_declaration"):
            pnames: list[str] = []
            ptype = ""

            if language == "go" and ch.type in (
                "parameter_declaration", "variadic_parameter_declaration"
            ):
                # Go allows comma-separated parameters sharing one declared
                # type in a single parameter_declaration node, e.g.
                # `func OpenWithPassword(path, password string)`. The node's
                # "name" field only ever resolves to one identifier even when
                # several are present, so every name after it silently
                # vanished. Scan every identifier child directly instead.
                pnames = [
                    node_text(sub) for sub in ch.children
                    if sub.type == "identifier"
                ]

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

            type_node = (child_by_field(ch, "type")
                         or find_child_by_type(ch, "type_annotation")
                         or find_child_by_type(ch, "type_identifier")
                         or find_child_by_type(ch, "predefined_type"))
            if type_node:
                ptype = node_text(type_node).lstrip(":").strip()

            # TC-SYS-005: Go variadic params â€” preserve ... prefix in type
            if ch.type == "variadic_parameter_declaration" and ptype:
                ptype = f"...{ptype}"
            for pname in pnames:
                params.append({"name": pname, "type": ptype})
    return params


# ---------------------------------------------------------------------------
# Return type extraction
# ---------------------------------------------------------------------------

def _extract_return_type(node, language: str) -> str:
    """Extract the return type from a method/function node."""
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
        text = text[match.end():]
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
    lang_mod = lang.get(language)
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
            cleaned = re.sub(
                r'<(?:paramref|typeparamref)\s+name="([^"]+)"\s*/?>',
                r"\1", cleaned)
            cleaned = re.sub(
                r'<see\s+cref="([^"]+)"\s*/?>',
                lambda m: m.group(1).rsplit(".", 1)[-1], cleaned)
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
    cnode, cname: str, rel: str, family: str,
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
        properties.append({
            "name": pname,
            "type": ret,
            "writable": pname in setter_names,
            "doc": _extract_doc_comment(fn, "python"),
            "line": fn.start_point[0] + 1,
        })
        claims.append(_make_claim(
            family, "api_method",
            f"{cname}.{pname} property of type {ret}",
            rel, fn.start_point[0] + 1))

    return properties, claims


# ---------------------------------------------------------------------------
# Python annotated field extraction (dataclass fields, TypedDict members, etc.)
# ---------------------------------------------------------------------------

def _extract_python_annotated_fields(
    cnode, cname: str, rel: str, family: str,
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

        fields.append({
            "name": fname,
            "type": ftype,
            "writable": is_dataclass and not is_frozen,
            "doc": "",
            "line": stmt.start_point[0] + 1,
        })
        claims.append(_make_claim(
            family, "api_method",
            f"{cname}.{fname} field of type {ftype}",
            rel, stmt.start_point[0] + 1))

    return fields, claims


# ---------------------------------------------------------------------------
# Python __init__ instance attribute extraction
# ---------------------------------------------------------------------------

def _extract_python_init_attributes(
    cnode, cname: str, rel: str, family: str,
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

        props.append({
            "name": attr_name,
            "type": attr_type,
            "doc": "",
            "line": target.start_point[0] + 1,
        })
        claims.append(_make_claim(
            family, "api_method",
            f"{cname}.{attr_name} field of type {attr_type or 'unknown'}",
            rel, target.start_point[0] + 1))

    return props, claims


# ---------------------------------------------------------------------------
# Java property synthesis
# ---------------------------------------------------------------------------

_JAVA_OBJECT_METHODS: frozenset[str] = frozenset({
    "getClass", "hashCode", "equals", "toString",
    "notify", "notifyAll", "wait", "clone", "finalize",
})


def _synthesize_java_properties(
    methods: list[dict[str, Any]], cname: str, rel: str, family: str,
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
        if (name.startswith("get") and len(name) > 3
                and name[3].isupper() and len(params) == 0):
            prop_name = name[3].lower() + name[4:]
            getters[prop_name] = m
        elif (name.startswith("is") and len(name) > 2
              and name[2].isupper() and len(params) == 0):
            prop_name = name[2].lower() + name[3:]
            getters[prop_name] = m

    for prop_name, getter in getters.items():
        ptype = getter.get("return_type", "")
        properties.append({
            "name": prop_name,
            "type": ptype,
            "doc": getter.get("doc", ""),
            "line": getter["line"],
        })
        claims.append(_make_claim(
            family, "api_method",
            f"{cname}.{prop_name} property of type {ptype}",
            rel, getter["line"]))

    return properties, claims


# ---------------------------------------------------------------------------
# C# / Java const and static-final field extraction (Track A-1)
# ---------------------------------------------------------------------------

def _extract_const_fields(
    cnode, cname: str, rel: str, family: str, language: str,
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
    body = (find_child_by_type(cnode, "declaration_list")
            or find_child_by_type(cnode, "class_body"))
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
                fields.append({
                    "name": fname,
                    "type": ftype,
                    "kind": kind,
                    "doc": _extract_doc_comment(member, language),
                    "line": member.start_point[0] + 1,
                    "writable": False,
                })
                claims.append(_make_claim(
                    family, "api_field",
                    f"{cname}.{fname} {kind} of type {ftype}",
                    rel, member.start_point[0] + 1,
                ))

    return fields, claims


# ---------------------------------------------------------------------------
# TypeScript module-level `export const` extraction (TC-MT040-13)
# ---------------------------------------------------------------------------

_TS_LITERALISH_TYPES: frozenset[str] = frozenset({
    "string", "number", "true", "false", "null",
    "template_string", "array", "object",
})


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
    root, rel: str, fpath: Path, family: str, compute_reachable,
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
                "arrow_function", "function_expression",
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
                claims.append(_make_claim(
                    family, "api_method",
                    f"Function {cname}({', '.join(p['name'] for p in params)}) -> {ret}",
                    rel, vd.start_point[0] + 1))
                continue

            type_node = child_by_field(vd, "type")
            ctype = (node_text(type_node).lstrip(":").strip() if type_node is not None
                     else _infer_ts_literal_type(value_node))
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
            claims.append(_make_claim(
                family, "api_field",
                f"{cname} constant of type {ctype or 'unknown'}",
                rel, vd.start_point[0] + 1,
            ))

    return entries, claims


# ---------------------------------------------------------------------------
# C++ property synthesis
# ---------------------------------------------------------------------------

def _synthesize_cpp_properties(
    methods: list[dict[str, Any]], cname: str, rel: str, family: str,
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

        properties.append({
            "name": name,
            "type": ret,
            "doc": m.get("doc", ""),
            "line": m["line"],
            "writable": name in setter_names,
        })
        claims.append(_make_claim(
            family, "api_method",
            f"{cname}.{name} property of type {ret}",
            rel, m["line"]))

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
        if (first.type == "expression_statement"
                and second.type == "raise_statement"
                and "NotImplementedError" in node_text(second)):
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
    """Walk up the AST from a C++ class node to collect all enclosing namespace names.

    Returns namespace names joined with '::' in outermost-to-innermost order.
    Example: class inside 'namespace Aspose { namespace Slides { ... } }' returns
    'Aspose::Slides'.
    """
    parts: list[str] = []
    parent = cnode.parent
    while parent is not None:
        if parent.type == "namespace_definition":
            name_node = child_by_field(parent, "name")
            if name_node:
                parts.append(node_text(name_node))
        parent = parent.parent
    parts.reverse()
    return "::".join(parts)


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
    """Walk up from a C# class node to find the enclosing namespace.

    Handles block-scoped (``namespace X { class Y {} }``) and file-scoped
    (``namespace X; class Y {}``) forms.
    """
    # Block-scoped: walk up through namespace_declaration ancestors
    parts: list[str] = []
    parent = cnode.parent
    while parent is not None:
        if parent.type == "namespace_declaration":
            for ch in parent.children:
                if ch.type in ("qualified_name", "identifier"):
                    parts.append(node_text(ch))
                    break
        parent = parent.parent
    if parts:
        parts.reverse()
        return ".".join(parts)
    # File-scoped: find sibling file_scoped_namespace_declaration at root
    cu = cnode
    while cu.parent is not None:
        cu = cu.parent
    for ch in cu.children:
        if ch.type == "file_scoped_namespace_declaration":
            for sub in ch.children:
                if sub.type in ("qualified_name", "identifier"):
                    return node_text(sub)
    return ""


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
            properties.append({
                "name": fname,
                "type": ftype,
                "kind": "property",
                "access_mode": "readwrite",
                "doc": "",
                "line": field_decl.start_point[0] + 1,
            })
            claims.append(_make_claim(
                family, "api_method",
                f"{cname}.{fname} field of type {ftype}",
                rel, field_decl.start_point[0] + 1,
            ))

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
        methods.append({
            "name": mname,
            "params": params,
            "return_type": ret,
            "doc": mdoc,
            "line": member.start_point[0] + 1,
            "is_constructor": False,
        })
        claims.append(_make_claim(
            family, "api_method",
            f"{cname}.{mname}({', '.join(p['name'] for p in params)}) -> {ret}",
            rel, member.start_point[0] + 1,
        ))

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
        vis = [node_text(c).strip() for c in field_decl.children
               if c.type == "visibility_modifier"]
        if "pub" not in vis:
            continue
        name_node = child_by_field(field_decl, "name")
        fname = node_text(name_node) if name_node else ""
        if not fname:
            continue
        type_node = child_by_field(field_decl, "type")
        ftype = node_text(type_node) if type_node else ""
        properties.append({
            "name": fname,
            "type": ftype,
            "kind": "property",
            "access_mode": "readwrite",
            "doc": _extract_rustdoc_comment(field_decl),
            "line": field_decl.start_point[0] + 1,
        })
        claims.append(_make_claim(
            family, "api_method",
            f"{cname}.{fname} field of type {ftype}",
            rel, field_decl.start_point[0] + 1,
        ))

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

    return (_bare(child_by_field(parent, "type")),
            _bare(child_by_field(parent, "trait")))


_RUST_TYPE_KINDS: frozenset[str] = frozenset({
    "struct_item", "enum_item", "union_item", "trait_item",
})


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

    LOG.debug("Rust impl association: moved %d impl methods into %d types",
              moved, len(type_map))
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

    LOG.debug("SFX-1: moved %d receiver methods into %d Go types",
              len(to_remove), len(type_map))
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
        (m.get("name", ""), str(m.get("params", "")))
        for m in target.get("methods", [])
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


def consolidate_classes(classes: list[dict], language: str) -> list[dict]:
    """Merge partial classes, disambiguate same-name-different-namespace, dedup enums.

    Must run BEFORE ``_flatten_inheritance`` so the by-name dict used for
    inheritance resolution is correct.

    Categories handled:
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
                LOG.debug("MERGE-PARTIAL: %s â€” %d fragments â†’ 1",
                          name, len(ns_indices))
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
                        LOG.debug("DISCARD-SHIM: %s â€” %s inherits from same-name type",
                                  name, classes[i].get("file", ""))
                        break
            if shim_found:
                continue

            # Category 2: stub vs full (one has bases, others don't)
            with_bases = [i for i in ns_indices if classes[i].get("bases")]
            without_bases = [i for i in ns_indices if not classes[i].get("bases")]
            if with_bases and without_bases:
                for i in without_bases:
                    discard.add(i)
                LOG.debug("DISCARD-STUB: %s â€” kept %d with bases, discarded %d stubs",
                          name, len(with_bases), len(without_bases))
                continue

            # Category 6: fallback â€” merge on same visibility (backward compat)
            vis_groups: dict[int, list[int]] = defaultdict(list)
            for i in ns_indices:
                rank = _VIS_RANK.get(classes[i].get("visibility", "conventional"), 1)
                vis_groups[rank].append(i)
            for rank, vis_indices in vis_groups.items():
                if len(vis_indices) < 2:
                    continue
                primary_idx = max(vis_indices,
                                  key=lambda i: (len(classes[i].get("methods", []))
                                                 + len(classes[i].get("properties", []))))
                primary = classes[primary_idx]
                for i in vis_indices:
                    if i == primary_idx:
                        continue
                    _merge_members(primary, classes[i])
                    discard.add(i)
                    merge_count += 1
                LOG.debug("MERGE-FALLBACK: %s â€” %d entries with rank %d â†’ 1",
                          name, len(vis_indices), rank)

    if merge_count:
        LOG.info("consolidate_classes: merged %d partial-class fragment(s)", merge_count)
    if discard:
        LOG.info("consolidate_classes: discarded %d duplicate(s)", len(discard) - merge_count)

    return [c for i, c in enumerate(classes) if i not in discard]


# ---------------------------------------------------------------------------
# Core API surface extraction
# ---------------------------------------------------------------------------


def _flatten_inheritance(classes: list[dict]) -> None:
    """Copy inherited methods/properties into child classes (in-place).

    Walks the full inheritance tree (not just direct parents) by resolving
    classes in topological order so parents are populated before children.
    Child definitions take precedence over inherited ones.
    Handles cycles via visited-set guard.
    """
    # Use class_import as key when available for namespace-aware resolution;
    # also index by short name as fallback for base-class lookup.
    by_name: dict[str, dict] = {}
    for c in classes:
        key = c.get("class_import") or c.get("name", "")
        if key:
            by_name[key] = c
        short = c.get("name", "")
        if short and short not in by_name:
            by_name[short] = c

    resolved: set[str] = set()

    def _resolve(cls_name: str, visiting: set[str]) -> None:
        if cls_name in resolved or cls_name not in by_name:
            return
        if cls_name in visiting:  # cycle guard
            return
        visiting.add(cls_name)
        cls = by_name[cls_name]
        # Resolve parents first so their inherited members are populated
        for base_name in cls.get("bases", []):
            _resolve(base_name, visiting)
        # Copy from each parent (now includes grandparent members)
        for base_name in cls.get("bases", []):
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

            child_method_keys = {_method_key(m) for m in cls.get("methods", [])}
            for m in parent.get("methods", []):
                key = _method_key(m)
                if key not in child_method_keys:
                    cls.setdefault("methods", []).append(copy.deepcopy(m))
                    child_method_keys.add(key)
            child_prop_names = {p["name"] for p in cls.get("properties", [])}
            for p in parent.get("properties", []):
                if p["name"] not in child_prop_names:
                    cls.setdefault("properties", []).append(copy.deepcopy(p))
                    child_prop_names.add(p["name"])
        visiting.discard(cls_name)
        resolved.add(cls_name)

    for cls in classes:
        name = cls.get("name")
        if name:
            _resolve(name, set())


def _mark_package_init_exports(
    classes: list[dict], source_files: list[Path], pkg_root: "Path | None" = None,
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
        if any(p.startswith("_") and not p.startswith("__")
               for p in src_file.parts):
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
                    if isinstance(target, ast.Name) and target.id == "__all__" \
                            and isinstance(node.value, (ast.List, ast.Tuple)):
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


_PY_RELATIVE_IMPORT_RE = re.compile(
    r"^\s*from\s+(\.+)([\w.]*)\s+import\s+([^\n(]+)$", re.MULTILINE
)
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
_PY_RELATIVE_IMPORT_PAREN_RE = re.compile(
    r"^\s*from\s+(\.+)([\w.]*)\s+import\s+\(([^)]*)\)", re.MULTILINE
)
# Absolute-form counterparts ("from aspose_pdf.images import Rectangle"),
# used only when scanning the repo's own test files (below) for direct
# submodule imports â€” the leading `\w` (not `[\w.]`) is deliberate so
# these never also match a relative "from .forms import X" statement's
# dot-prefixed module path.
_PY_ABSOLUTE_IMPORT_RE = re.compile(
    r"^\s*from\s+(\w[\w.]*)\s+import\s+([^\n(]+)$", re.MULTILINE
)
_PY_ABSOLUTE_IMPORT_PAREN_RE = re.compile(
    r"^\s*from\s+(\w[\w.]*)\s+import\s+\(([^)]*)\)", re.MULTILINE
)


def _resolve_python_shim_collisions(
    classes: list[dict], source_files: list[Path], repo: Path
) -> list[dict]:
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
        for dots, modpath, names_part in (
            list(_PY_RELATIVE_IMPORT_RE.findall(content))
            + list(_PY_RELATIVE_IMPORT_PAREN_RE.findall(content))
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
        return any(
            (name, file) in unaliased_imports.get(g, ())
            for g in reachable_files
        )

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
                name, len(without_bases),
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
                name, classes[reachable_idx[0]].get("file", ""), len(indices) - 1,
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
                i for i, e in entries
                if _symbol_transitively_reachable(name, e.get("file", ""))
            ]
            if len(file_reachable_idx) == 1:
                for i in indices:
                    if i not in file_reachable_idx:
                        discard.add(i)
                LOG.info(
                    "PY-SHIM: %s â€” kept transitively-reachable %s, discarded "
                    "%d duplicate(s) with no import chain to any __init__.py",
                    name, classes[file_reachable_idx[0]].get("file", ""),
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
            entry_dirs = {
                str(Path(e.get("file", "")).parent).replace("\\", "/")
                for _, e in entries
            }
            if entry_dirs and entry_dirs.issubset(governed_dirs):
                for i in indices:
                    discard.add(i)
                LOG.warning(
                    "PY-SHIM: %s â€” none of %d duplicate(s) reachable, and every "
                    "containing package has a curating __init__.py that omits "
                    "them; treating as internal, discarding all: %s",
                    name, len(indices),
                    ", ".join(classes[i].get("file", "") for i in indices),
                )
        # else (>1 reachable, or 0 reachable without full __init__.py
        # coverage): not a confident signal either way â€” leave the group
        # untouched for consolidate_classes' existing different-namespace
        # handling and downstream disambiguation.

    if not discard:
        return list(classes)
    return [c for i, c in enumerate(classes) if i not in discard]


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
    _python_exports: "set[str] | None" = None
    _python_export_root: "Path | None" = None
    _python_export_init_rel = ""
    _rust_reexports: "set[str] | None" = None
    # TC-MT040-11: TypeScript/JavaScript reachability signal (real
    # implementation -- was an unconditional (None, None) placeholder from
    # extraction/lang/typescript.py before this card).
    _ts_exports: "set[str] | None" = None
    _ts_entry_point: "Path | None" = None
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
                _ts_entry_point_rel = str(
                    _ts_resolved_entry.relative_to(repo)
                ).replace("\\", "/")
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
                    pkg_text = pkg_text[len(kw):].strip()
            if pkg_text:
                packages.add(pkg_text)

        # FR-15: capture Java file-level package declaration once per file
        java_file_package = ""
        if language == "java":
            for pkg_node in collect_nodes(root, {"package_declaration"}):
                pkg_txt = node_text(pkg_node).strip().rstrip(";")
                if pkg_txt.startswith("package"):
                    java_file_package = pkg_txt[len("package"):].strip()
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
                        module_all_names = set(
                            re.findall(r'["\'](\w+)["\']', rhs_text)
                        )
                        break

        for cnode in class_nodes:
            if not is_public(cnode, language):
                continue

            cname = _node_name(cnode, language)
            if not cname:
                continue

            # SYS-PKG-001: exclude Java classes in internal/impl packages.
            # Check uses the file-level java_file_package captured above the loop.
            if (language == "java"
                    and _effective_excluded_segments
                    and _is_excluded_java_package(java_file_package, _effective_excluded_segments)):
                continue

            # Skip C++ forward declarations (class Foo;) â€” no body
            if (language == "cpp"
                    and cnode.type in ("class_specifier", "struct_specifier")
                    and find_child_by_type(cnode, "field_declaration_list") is None):
                continue

            is_enum = cnode.type in ("enum_declaration", "enum_definition",
                                     "enum_specifier", "enum_item")
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
                if candidates and all(
                    m["name"].replace("_", "").isupper()
                    for m in candidates
                ):
                    enum_members = candidates
                    is_enum = True
                else:
                    enum_members = []
            else:
                enum_members = []

            methods: list[dict[str, Any]] = []
            properties: list[dict[str, Any]] = []

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
                        type_node = (child_by_field(mnode, "return_type")
                                     or find_child_by_type(mnode, "type_annotation"))
                        if type_node:
                            ptype = node_text(type_node).lstrip(":").strip()
                        # Merge with existing same-name property if present
                        existing_prop = next(
                            (p for p in properties
                             if p.get("name") == mname and p.get("kind") == "property"),
                            None,
                        )
                        if existing_prop is not None:
                            existing_prop["access_mode"] = "readwrite"
                        else:
                            properties.append({
                                "name": mname,
                                "type": ptype,
                                "kind": "property",
                                "access_mode": acc_mode,
                                "doc": "",
                                "line": mnode.start_point[0] + 1,
                            })
                            claims.append(_make_claim(
                                family, "api_method",
                                f"{cname}.{mname} property of type {ptype}",
                                rel, mnode.start_point[0] + 1))
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
                                type_node = (child_by_field(mnode, "return_type")
                                             or find_child_by_type(mnode, "type_annotation"))
                                if type_node:
                                    ptype = node_text(type_node).lstrip(":").strip()
                            existing_prop = next(
                                (p for p in properties
                                 if p.get("name") == mname and p.get("kind") == "property"),
                                None,
                            )
                            if existing_prop is not None:
                                existing_prop["access_mode"] = "readwrite"
                                if is_getter and ptype and not existing_prop.get("type"):
                                    existing_prop["type"] = ptype
                            else:
                                properties.append({
                                    "name": mname,
                                    "type": ptype,
                                    "kind": "property",
                                    "access_mode": acc_mode,
                                    "doc": "",
                                    "line": mnode.start_point[0] + 1,
                                })
                                claims.append(_make_claim(
                                    family, "api_method",
                                    f"{cname}.{mname} property of type {ptype}",
                                    rel, mnode.start_point[0] + 1))
                            continue

                if mnode.type in ("property_declaration",
                                  "public_field_definition",
                                  "property_signature"):
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
                    if language == "typescript" and mnode.type == "public_field_definition":
                        _ts_is_static = any(ch.type == "static" for ch in mnode.children)
                        _ts_is_readonly = any(ch.type == "readonly" for ch in mnode.children)
                        prop_entry["writable"] = not _ts_is_readonly
                        if _ts_is_static:
                            prop_entry["is_static"] = True
                        _ts_value_node = child_by_field(mnode, "value")
                        _ts_is_literalish = (
                            _ts_value_node is not None
                            and (_ts_value_node.type in _TS_LITERALISH_TYPES
                                 or _ts_value_node.type == "unary_expression")
                        )
                        if (_ts_is_static and _ts_is_readonly) or (
                            _ts_is_static and _ts_is_literalish
                        ):
                            prop_entry["kind"] = "constant"
                            if _ts_value_node is not None:
                                prop_entry["value"] = node_text(_ts_value_node)[:80]

                    properties.append(prop_entry)
                    claims.append(_make_claim(
                        family, "api_method",
                        f"{cname}.{mname} property of type {ptype}",
                        rel, mnode.start_point[0] + 1))
                    continue

                params = _extract_method_params(mnode, language)
                ret = _extract_return_type(mnode, language)
                mdoc = _extract_doc_comment(mnode, language)
                m_deprecated, m_deprecated_reason = deprecated_marker(mnode, language)

                is_ctor = (mnode.type == "constructor_declaration"
                           or (language == "python" and mname == "__init__")
                           or (language in ("typescript", "javascript")
                               and mname == "constructor"))

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

                methods.append(method_entry)
                claims.append(_make_claim(
                    family, "api_method",
                    f"{cname}.{mname}({', '.join(p['name'] for p in params)}) -> {ret}",
                    rel, mnode.start_point[0] + 1))

            # Python properties via decorators
            if language == "python":
                extra_props, extra_claims = _extract_python_properties(
                    cnode, cname, rel, family)
                properties.extend(extra_props)
                claims.extend(extra_claims)

            # Python annotated fields (dataclass fields, TypedDict members, etc.)
            if language == "python":
                extra_fields, extra_claims = _extract_python_annotated_fields(
                    cnode, cname, rel, family)
                properties.extend(extra_fields)
                claims.extend(extra_claims)

            # Python __init__ instance attributes (self.xxx = value)
            if language == "python":
                existing_prop_names = {p["name"] for p in properties}
                init_props, init_claims = _extract_python_init_attributes(
                    cnode, cname, rel, family, existing_prop_names)
                properties.extend(init_props)
                claims.extend(init_claims)

            # Java properties via getter/setter synthesis
            if language == "java":
                extra_props, extra_claims = _synthesize_java_properties(
                    methods, cname, rel, family)
                properties.extend(extra_props)
                claims.extend(extra_claims)

            # C++ properties via getter/setter synthesis
            if language == "cpp":
                extra_props, extra_claims = _synthesize_cpp_properties(
                    methods, cname, rel, family)
                properties.extend(extra_props)
                claims.extend(extra_claims)

            # C# public const / static readonly fields + Java public static final fields
            # These are accessed like properties in content (e.g. CfbConstants.RootStreamId)
            # but are NOT captured by the method/property_declaration pass above.
            if language in ("csharp", "java"):
                extra_props, extra_claims = _extract_const_fields(
                    cnode, cname, rel, family, language)
                properties.extend(extra_props)
                claims.extend(extra_claims)

            # C++ field_declaration: method declarations (no body) AND public data members
            if language == "cpp":
                body = find_child_by_type(cnode, "field_declaration_list")
                if body:
                    access = "public" if cnode.type == "struct_specifier" else "private"
                    for member in body.children:
                        if member.type == "access_specifier":
                            access = node_text(member).strip().rstrip(":")
                        elif member.type == "function_definition" and access == "public":
                            # Inline method definition (has body): void SetBold(bool v) { ... }
                            fdecl = find_child_by_type(member, "function_declarator")
                            if fdecl is None:
                                for _wrapper_type in ("reference_declarator", "pointer_declarator"):
                                    _wrapper = find_child_by_type(member, _wrapper_type)
                                    if _wrapper:
                                        fdecl = find_child_by_type(_wrapper, "function_declarator")
                                        if fdecl:
                                            break
                            if fdecl is not None:
                                mname = ""
                                for ch in fdecl.children:
                                    if ch.type in ("identifier", "field_identifier", "destructor_name"):
                                        mname = node_text(ch)
                                        break
                                if mname and not mname.startswith("_"):
                                    params = _extract_method_params(fdecl, language)
                                    ret = _extract_return_type(member, language)
                                    mdoc = _extract_doc_comment(member, language)
                                    methods.append({
                                        "name": mname,
                                        "params": params,
                                        "return_type": ret,
                                        "doc": mdoc,
                                        "line": member.start_point[0] + 1,
                                        "is_constructor": False,
                                    })
                                    claims.append(_make_claim(
                                        family, "api_method",
                                        f"{cname}.{mname}({', '.join(p['name'] for p in params)}) -> {ret}",
                                        rel, member.start_point[0] + 1))
                        elif member.type == "field_declaration" and access == "public":
                            fdecl = find_child_by_type(member, "function_declarator")
                            # Support reference/pointer return types: T& GetFoo() or T* GetFoo()
                            # tree-sitter wraps function_declarator inside reference_declarator
                            # or pointer_declarator in these cases, so direct search misses them.
                            if fdecl is None:
                                for _wrapper_type in ("reference_declarator", "pointer_declarator"):
                                    _wrapper = find_child_by_type(member, _wrapper_type)
                                    if _wrapper:
                                        fdecl = find_child_by_type(_wrapper, "function_declarator")
                                        if fdecl:
                                            break
                            if fdecl is not None:
                                # Method declaration (no body) â€” common in header files
                                mname = ""
                                for ch in fdecl.children:
                                    if ch.type in ("identifier", "field_identifier"):
                                        mname = node_text(ch)
                                        break
                                if not mname:
                                    continue
                                params = _extract_method_params(fdecl, language)
                                ret = _extract_return_type(member, language)
                                mdoc = _extract_doc_comment(member, language)
                                methods.append({
                                    "name": mname,
                                    "params": params,
                                    "return_type": ret,
                                    "doc": mdoc,
                                    "line": member.start_point[0] + 1,
                                    "is_constructor": False,
                                })
                                claims.append(_make_claim(
                                    family, "api_method",
                                    f"{cname}.{mname}({', '.join(p['name'] for p in params)}) -> {ret}",
                                    rel, member.start_point[0] + 1))
                            else:
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
                                mdoc = _extract_doc_comment(member, language)
                                properties.append({
                                    "name": fname,
                                    "type": ftype,
                                    "doc": mdoc,
                                    "line": member.start_point[0] + 1,
                                    "writable": True,  # public data members are read-write
                                })
                                claims.append(_make_claim(
                                    family, "api_method",
                                    f"{cname}.{fname} data member of type {ftype}",
                                    rel, member.start_point[0] + 1))

            # Go exported struct fields (e.g. RenderOptions.DPI, Rectangle.LLX)
            if language == "go":
                extra_props, extra_claims = _extract_go_struct_fields(
                    cnode, cname, rel, family)
                properties.extend(extra_props)
                claims.extend(extra_claims)

            # Go interface method signatures (e.g. Annotation.Rect/SetColor/
            # Flatten) -- interface bodies have no struct_type, so the block
            # above is a no-op for them; this is the dedicated path.
            if language == "go":
                extra_methods, extra_iface_claims = _extract_go_interface_methods(
                    cnode, cname, rel, family)
                methods.extend(extra_methods)
                claims.extend(extra_iface_claims)

            # Rust pub struct fields (e.g. Workbook.path)
            if language == "rust":
                extra_props, extra_claims = _extract_rust_struct_fields(
                    cnode, cname, rel, family)
                properties.extend(extra_props)
                claims.extend(extra_claims)

            vis = visibility_tier(
                cnode, language,
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
                bases, methods, properties, language)
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
                if java_file_package:
                    cls_record["class_package"] = java_file_package
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
                    cls_record["canonical_namespace"] = java_file_package
                else:
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
                    if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", p)
                           for p in parts):
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
            claims.append(_make_claim(
                family, "api_class",
                f"Class {cname} defined in {rel}",
                rel, cnode.start_point[0] + 1))

        # top-level functions (not inside classes)
        top_funcs = collect_nodes(root, func_types)
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
            claims.append(_make_claim(
                family, "api_method",
                f"Function {fname}({', '.join(p['name'] for p in params)}) -> {ret}",
                rel, fnode.start_point[0] + 1))

        # TC-MT040-13: module-level `export const` declarations -- classes,
        # functions, and top-level functions above are the only three shapes
        # func_types/class_types nodes cover; a bare `export const X = ...`
        # (constant OR arrow-function-valued) is neither and was previously
        # invisible to extraction entirely.
        if language == "typescript":
            _ts_const_entries, _ts_const_claims = _extract_ts_module_constants(
                root, rel, fpath, family, _compute_reachable)
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
