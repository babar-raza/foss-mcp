"""Clone a pinned product repository, run the tree-sitter engine, emit a reproducible fixture.

Not itself exercised at check time: ``gatectl`` verifies TC-011 with the network proxied to a
dead port, so its tests run only against the fixture this script already produced
(``tests/fixtures/pdf_net/api_surface.json``). This module is the tool that produced it - run by
hand, with real network access, never by the offline test suite itself.
"""

from __future__ import annotations

import argparse
import ast
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from tree_sitter_language_pack import get_parser

from foss_mcp.extraction.tree_sitter_engine import (
    api_surface,
    package_root,
    python_surface,
    tree_helpers,
)

# Platform name (as written in a product manifest) to the tree-sitter-language-pack spelling
# extract_api_surface expects. package_root.detect_package_root takes the manifest spelling
# directly (it already accepts both "net" and "dotnet"). "python" is deliberately absent: it
# never reaches get_parser/extract_api_surface at all (see extract_from_clone) - it is routed
# to python_surface.inspect_public_surface, the independent pure-``ast`` reader, instead.
_LANGUAGE_BY_PLATFORM = {
    "net": "csharp",
    "dotnet": "csharp",
    "java": "java",
    "cpp": "cpp",
    "go": "go",
    "rust": "rust",
    "typescript": "typescript",
    "javascript": "typescript",
    "nodejs": "javascript",
}


# The version of the artifact/fixture data contract this module writes. A fixture without the key
# is version 1 (before TC-520). Later extraction cards bump it.
EXTRACTOR_VERSION = 5

# The ``--max-types`` each pilot was re-extracted with so its committed fixture holds its WHOLE public API
# (kept == total, ``truncated`` false): the values are the totals the committed fixtures were extracted
# with (TC-530, TC-550), consumed by the extraction CLI (``main``) when no ``--max-types`` is given. The
# helm ``maxTypes`` and the docker-compose ``--max-types`` carry the same numbers. The extraction cap and
# the ingestion ``--max-types`` must be EQUAL: otherwise the second slice in ``infra/build_chunks.py``
# cuts what the first one kept. A pilot whose total is below the default of 300 keeps 300.
# A higher cap raises ingestion memory, so a re-ingest must re-measure Job memory and image size.
RECOMMENDED_MAX_TYPES: dict[str, int] = {
    "pdf/net": 901,
    "pdf/java": 1239,
    "words/net": 618,
    "pdf/python": 867,
    "pdf/cpp": 359,
    "cells/cpp": 300,
    "slides/cpp": 355,
    "html/python": 323,
    "3d/python": 697,
    "slides/python": 1023,
    "words/python": 391,
    "pdf/go": 357,
    "pdf/typescript": 4393,
}

# The share of the cap that README-named types may take (after the example-used types).
README_KEEP_FRACTION = 0.7
GO_FUNCTION_QUOTA_PERCENT = 40

_PARSE_EXAMPLE_LIMIT = 5


def load_manifest(manifest_path: Path) -> dict[str, Any]:
    return yaml.safe_load(manifest_path.read_text(encoding="utf-8"))


# Repo-discovery variables that would redirect a `git init`/`fetch`/`checkout` away from the
# directory it was pointed at (the 2026-10-10 leaked-GIT_DIR incident, see docs/DECISION_LOG.md).
_GIT_DISCOVERY_ENV_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)


def repository_url(repository: str) -> str:
    """The clone URL for a manifest's ``owner/name``. The one place the host is named, so a
    test can point extraction at a local repository without touching the network."""
    return f"https://github.com/{repository}.git"


def _git_env() -> dict[str, str]:
    """The caller's environment minus the repo-discovery variables; passed to git, never read."""
    return {key: value for key, value in os.environ.items() if key not in _GIT_DISCOVERY_ENV_VARS}


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True, env=_git_env())


def _clone_at_commit(url: str, dest: Path, commit: str) -> None:
    """Fetch exactly *commit* (``git init``, ``fetch --depth 1 <url> <sha>``, ``checkout
    FETCH_HEAD``) into *dest*; raise ``RuntimeError`` unless the checked-out HEAD is that SHA."""
    if not commit or commit.startswith("-"):
        raise RuntimeError(f"refusing to fetch {commit!r}: not a commit SHA")
    try:
        _git("init", "--quiet", str(dest))
        _git("-C", str(dest), "fetch", "--depth", "1", "--quiet", url, commit)
        _git("-C", str(dest), "checkout", "--quiet", "FETCH_HEAD")
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or "").strip().splitlines()[-1:] or [""]
        raise RuntimeError(f"could not fetch commit {commit} from {url}: {detail[0]}") from None
    head = _git("-C", str(dest), "rev-parse", "HEAD").stdout.strip()
    if head.lower() != commit.lower():
        raise RuntimeError(
            f"fetched HEAD {head} differs from the requested commit {commit}; "
            "pass the full 40-character commit SHA"
        )


def clone_shallow(repository: str, dest: Path, *, commit: str | None = None) -> str:
    """Shallow-clone *repository* ("owner/name") into *dest*; return the exact commit SHA.

    Without *commit* this clones the default branch HEAD. With *commit* it fetches exactly that
    commit (see ``_clone_at_commit``) so extraction runs at the commit a pilot pins, not at
    whatever the default branch has moved on to.

    Read-only: this never writes into the clone and never pushes to it. Cleanup is the
    caller's responsibility.
    """
    url = repository_url(repository)
    if commit is not None:
        _clone_at_commit(url, dest, commit)
        return commit.lower()
    _git("clone", "--depth", "1", "--quiet", url, str(dest))
    return _git("-C", str(dest), "rev-parse", "HEAD").stdout.strip()


def _python_package_dirs(clone_root: Path) -> list[str]:
    """Repository-relative package directory name(s) for
    ``python_surface.inspect_public_surface``'s ``package_dirs`` argument.

    ``inspect_public_surface(repository_root, package_dirs)`` wants *names relative to the
    repository root* (see tests/extraction/tree_sitter_engine/test_lang_python_parity.py:
    ``inspect_public_surface(tmp_path, ["aspose"])``), but
    ``package_root._detect_python_root`` - already correct, and not re-derived here - returns
    a pre-resolved absolute ``Path`` to the package directory itself (e.g. ``repo/src/aspose``
    for a src-layout, or ``repo/aspose`` flat). This just re-expresses that same Path relative
    to *clone_root*, so both layouts route through the identical detection logic.
    """
    pkg_root = package_root.detect_package_root(clone_root, "python")
    try:
        relative = pkg_root.relative_to(clone_root).as_posix()
    except ValueError:
        relative = "."
    # "." means _detect_python_root fell back to the repo root itself (no marker file, or no
    # package directory found under it) - inspect_public_surface has no equivalent "scan
    # everything" mode, so there is nothing meaningful to pass; the caller then rightly gets
    # an empty surface rather than a mis-scanned one.
    return [] if relative == "." else [relative]


def _python_param_dicts(symbol: python_surface.PublicSymbol) -> list[dict[str, Any]]:
    """TC-522: the ``params`` list of a python function or method: ``{"name", "type"}`` per declared
    parameter, plus ``"default"`` (the default's source text) only when it has one and ``"kind"`` only
    for a positional-only or keyword-only parameter (``*args`` / ``**kw`` are visible from their stars)."""
    details = {name: (kind, default) for name, kind, default in symbol.param_details}
    params: list[dict[str, Any]] = []
    for name, annotation in symbol.params or symbol.param_types:
        param: dict[str, Any] = {"name": name, "type": annotation}
        kind, default = details.get(name, ("", ""))
        if default:
            param["default"] = default
        if kind in ("positional_only", "keyword_only"):
            param["kind"] = kind
        params.append(param)
    return params


def _python_types_from_surface(surface: python_surface.PublicSurface) -> list[dict[str, Any]]:
    """Adapt ``python_surface``'s ``PublicSymbol`` stream into the exact dict shape
    ``extract_api_surface`` produces (see tests/fixtures/pdf_net/api_surface.json), so
    downstream fixture/citation code needs no changes regardless of which reader ran.

    ``PublicSymbol`` now carries real base-class, return-type, and param-annotation data,
    read straight from the ``ast.ClassDef``/``ast.FunctionDef`` nodes the pure-``ast`` reader
    already walks (``python_surface._structured_fields``) - so ``bases``, ``methods[].
    return_type``, and ``methods[].params[].type`` are real for a python-sourced entry too,
    the same fields ``_centrality_scores()`` reads for every other language. TC-393: a method's
    ``params`` come from ``PublicSymbol.params`` (every declared parameter, ``type`` empty when
    unannotated, ``self``/``cls`` excluded), ``__init__`` is the constructor, and a ``property``
    symbol lands in its owner's ``properties`` with its annotated type and writability.
    ``enum_members`` is left empty here (this adapter sees symbols, not class bodies);
    ``fill_python_enum_members`` fills it from the source, and ``extract_pinned_repository``
    runs that for every python artifact it produces.
    """
    entries: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for symbol in surface.symbols:
        if symbol.kind not in ("class", "enum", "function"):
            continue
        entry: dict[str, Any] = {
            "name": symbol.name,
            "kind": symbol.kind,
            "file": symbol.source_path,
            "line": symbol.line,
            "doc": symbol.docstring or "",
            "visibility": "public",
            "deprecated": False,
            "deprecated_reason": "",
            "class_import": symbol.qualified_name,
            "canonical_namespace": symbol.module,
            "methods": [],
            "properties": [],
        }
        entry["bases"] = list(symbol.bases)  # TC-265: real bases
        if symbol.kind == "function":
            # TC-473: a module function carries its signature, as a method does.
            entry["params"] = _python_param_dicts(symbol)
            entry["return_type"] = symbol.return_type or ""
            if symbol.modifiers:
                entry["modifiers"] = list(symbol.modifiers)
        elif symbol.public_by == "reexport" and symbol.reexported_from:
            # TC-522: a class/enum re-export names the definition it re-exports, so a shell that
            # carries members of its own (inherited from a private base) still travels with it.
            entry["reexport_of"] = symbol.reexported_from
        if symbol.kind == "enum":
            # TC-532: the members come with the symbol (also for an enum reached through a private module).
            entry["enum_members"] = [{"name": name, "value": value} for name, value in symbol.enum_members]
        if symbol.shadows:
            entry["shadows"] = symbol.shadows  # TC-532: this exported name replaced that class
        if symbol.shadowed_by:
            entry["shadowed_by"] = symbol.shadowed_by  # TC-532: that exported name replaced this class
        entries[symbol.qualified_name] = entry
        order.append(symbol.qualified_name)

    for symbol in surface.symbols:
        if symbol.kind not in ("method", "property"):
            continue
        owner = entries.get(symbol.qualified_name.rsplit(".", 1)[0])
        if owner is None:
            continue  # the owning class was itself unresolved/excluded; never guess one up
        if symbol.kind == "property":
            prop: dict[str, Any] = {
                "name": symbol.name,
                "type": symbol.return_type or "",
                "writable": bool(symbol.writable),
                "doc": symbol.docstring or "",
                "file": symbol.source_path,
                "line": symbol.line,
            }
            if symbol.value is not None:
                prop["value"] = symbol.value  # TC-532: the literal the attribute is assigned
            if symbol.inherited_from:
                prop["inherited_from"] = symbol.inherited_from
            owner["properties"].append(prop)
            continue
        # Every declared parameter (TC-393), an unannotated one with type "" - _reference_strings()
        # reads `or ""`, so an empty type never pollutes centrality. A symbol built without
        # `params` falls back to its annotated `param_types`, as before.
        method: dict[str, Any] = {
            "name": symbol.name,
            "doc": symbol.docstring or "",
            "file": symbol.source_path,
            "line": symbol.line,
            "params": _python_param_dicts(symbol),
            "return_type": symbol.return_type or "",  # TC-265: real return_type
            "deprecated": False,
            "deprecated_reason": "",
            "is_constructor": symbol.name == "__init__",
        }
        if symbol.modifiers and symbol.name != "__init__":
            method["modifiers"] = list(symbol.modifiers)  # TC-522: static / classmethod / async
        if symbol.inherited_from:
            method["inherited_from"] = symbol.inherited_from  # TC-532: a member of a private base
        owner["methods"].append(method)

    return [entries[key] for key in order]


def _python_base_origins(surface: python_surface.PublicSurface) -> dict[str, dict[str, str]]:
    """TC-532: ``{class_import: {base as written: qualified name of the class that defines it}}`` for every
    class symbol of *surface* whose bases the source resolves."""
    return {
        symbol.qualified_name: dict(symbol.base_origins)
        for symbol in surface.symbols
        if symbol.kind in ("class", "enum") and symbol.base_origins
    }


def _flatten_python_inheritance(types: list[dict[str, Any]], origins: dict[str, dict[str, str]]) -> None:
    """TC-532: copy inherited members into the python class entries, each base resolved the way the
    source resolves it, never by its bare name.

    ``api_surface._flatten_inheritance`` looks a base up by its short name, and the short name of a class is
    shared by its package-level re-export and its definition: it resolved the re-export (often empty) and
    never visited the definition, so ``GeometryShape(Shape)`` ended with no members. Here every entry is
    keyed by its ``class_import`` for the call, and every base written in the source is replaced by the
    qualified name of the class ``origins`` says it is (a class defined in a private module is stood for by
    the public entry that re-exports it). A base the source does not resolve (``ABC``, ``Exception``)
    stays as written and links to nothing. ``name`` and ``bases`` are restored afterwards.
    """
    classes = [
        entry for entry in types if entry.get("kind") in ("class", "enum") and entry.get("class_import")
    ]
    by_import = {entry["class_import"]: entry for entry in classes}
    stand_in: dict[str, str] = {}
    for entry in classes:
        target = entry.get("reexport_of")
        hops = 0
        while target in by_import and by_import[target].get("reexport_of") and hops < 16:
            target = by_import[target]["reexport_of"]
            hops += 1
        if target and target not in by_import:
            stand_in.setdefault(target, entry["class_import"])
    by_short: dict[str, list[dict[str, Any]]] = {}
    for entry in classes:
        by_short.setdefault(entry["name"], []).append(entry)

    def by_bare_name(base: str) -> str | None:
        """The entry a base the source does not resolve is taken for: the one class of that name, else the
        one that is not a re-export, else the first that has members (the rule of the short-name lookup)."""
        found = by_short.get(base) or []
        if len(found) > 1:
            found = [entry for entry in found if not entry.get("reexport_of")] or found
        if len(found) > 1:
            found = [entry for entry in found if entry.get("methods") or entry.get("properties")] or found
        return found[0]["class_import"] if found else None

    saved = [(entry, entry["name"], entry.get("bases")) for entry in classes]
    for entry in classes:
        mapping = origins.get(entry["class_import"], {})
        resolved: list[str] = []
        for base in entry.get("bases") or []:
            target = mapping.get(base)
            if target is not None and target not in by_import:
                target = stand_in.get(target)
            if target is None and "." not in base:
                target = by_bare_name(base)
            resolved.append(target if target is not None else base)
        entry["bases"] = resolved
        entry["name"] = entry["class_import"]
    try:
        api_surface._flatten_inheritance(classes)
    finally:
        for entry, name, bases in saved:
            entry["name"] = name
            if bases is None:
                entry.pop("bases", None)
            else:
                entry["bases"] = bases


def _drop_overridden_members(types: list[dict[str, Any]]) -> None:
    """TC-532: a class that defines a name itself does not also have the base's member of that name: Python
    binds the name once. ``_flatten_inheritance`` keys a method by its name AND its parameter types, so an
    override whose parameters differ from the base's was listed twice, its own and the base's, and the
    base's signature appeared as a second way to call it. Drop every inherited method or property whose
    name an own method or property of the same class already has."""
    for entry in types:
        if entry.get("kind") not in ("class", "enum"):
            continue
        own = {
            member["name"]
            for key in ("methods", "properties")
            for member in entry.get(key) or []
            if not member.get("inherited_from")
        }
        inherited_seen: set[str] = set()
        for key in ("methods", "properties"):
            members = entry.get(key) or []
            kept: list[dict[str, Any]] = []
            for member in members:
                if member.get("inherited_from"):
                    # An inherited name is the nearest base's, listed once.
                    if member["name"] in own or member["name"] in inherited_seen:
                        continue
                    inherited_seen.add(member["name"])
                kept.append(member)
            if len(kept) != len(members):
                entry[key] = kept


def _extract_python_surface(clone_root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Run the independent pure-``ast`` reader over a python checkout, adapted to
    ``extract_api_surface``'s dict shape. Returns ``(types, unresolved)`` - the second element
    is ``python_surface.PublicSurface.unresolved`` verbatim, so an unresolved re-export
    surfaces to the caller rather than being silently dropped.
    """
    package_dirs = _python_package_dirs(clone_root)
    surface = python_surface.inspect_public_surface(clone_root, package_dirs)
    types = _python_types_from_surface(surface)
    # TC-281: api_surface._flatten_inheritance() is fully language-agnostic (it operates
    # purely on class_import/name/bases/methods/properties dict keys, which the adapter above
    # already populates) but was never reached for any Python-sourced pilot - extract_from_clone
    # routes "python" around api_surface.extract_api_surface() entirely (see its own docstring),
    # the only other call site. Reusing it unmodified here, rather than writing a second
    # Python-specific flattening implementation, closes that gap for every Python pilot.
    # TC-532: bases are resolved as the source resolves them, and an override shows its own signature.
    _flatten_python_inheritance(types, _python_base_origins(surface))
    _drop_overridden_members(types)
    return types, list(surface.unresolved)


def _parses_as_written(path: Path) -> bool:
    """TC-561: True when *path* parses with the stdlib ``ast`` exactly as it is written, with no repair.

    This is what ``import`` does with the file, so a file that fails here makes the package unimportable
    even though ``python_surface`` could read its declarations after the indentation repair."""
    try:
        ast.parse(path.read_text(encoding="utf-8-sig", errors="replace"), str(path))
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return False
    return True


def python_parse_health(clone_root: Path) -> dict[str, Any]:
    """How many python source files of the package(s) do not parse (stdlib ``ast``).

    TC-555: a file counts as failed only if it still does not parse AFTER the bounded indentation
    repair of ``python_surface`` (a file that lost its indentation is repaired and read).

    TC-561: ``as_written_failed`` counts the files that do not parse BEFORE any repair (the files
    ``import`` would reject). It is at least ``failed``; when it is more than zero the package cannot be
    imported at this commit, however much of its API the repair recovered. The key is present only when
    that count is more than zero (an absent key reads as zero).

    Walks every ``*.py`` of each directory ``_python_package_dirs`` returns, with the exclusion
    ``python_surface`` uses, and parses ALL of them - including modules with a leading underscore
    and ``__init__.py``, which ``python_surface`` skips (or reaches only through a re-export)
    before it parses them. ``total`` is 0 when there is no package directory. Examples are the
    first five ``path:line: message`` strings in sorted path order, paths relative to *clone_root*.
    """
    total = 0
    failed = 0
    as_written_failed = 0
    examples: list[str] = []
    for package_dir in _python_package_dirs(clone_root):
        package_path = clone_root / package_dir
        for path in sorted(package_path.rglob("*.py")):
            if any(
                part in python_surface._EXCLUDED_PARTS for part in path.relative_to(package_path.parent).parts
            ):
                continue
            total += 1
            if not _parses_as_written(path):
                as_written_failed += 1
            try:
                # TC-555: a file whose lost indentation the extractor repairs is read, not a failure
                # (its repair is disclosed by the ``repaired-syntax`` entry of ``unresolved``).
                python_surface._parse_source_file(path)
            except (SyntaxError, ValueError, RecursionError, MemoryError) as error:
                failed += 1
                if len(examples) < _PARSE_EXAMPLE_LIMIT:
                    line = getattr(error, "lineno", None) or 0
                    message = getattr(error, "msg", None) or str(error)
                    examples.append(f"{path.relative_to(clone_root).as_posix()}:{line}: {message}")
    health: dict[str, Any] = {"engine": "ast", "failed": failed, "total": total, "examples": examples}
    if as_written_failed:
        # Present only when there is something to say: a package whose files all parse as written has the
        # same health as before the key existed, and a reader treats an absent count as zero.
        health["as_written_failed"] = as_written_failed
    return health


def tree_sitter_parse_health(scout_report: dict[str, Any]) -> dict[str, Any]:
    """The same health shape for a tree-sitter run, from ``extract_api_surface``'s scout report:
    ``parse_errors`` (files whose tree has an error node) and ``files_attempted``. Tree-sitter is
    error tolerant, so a flagged file may still have yielded symbols."""
    errors = sorted(str(item) for item in (scout_report.get("parse_errors") or []))
    attempted = scout_report.get("files_attempted")
    return {
        "engine": "tree-sitter",
        "failed": len(errors),
        "total": attempted if isinstance(attempted, int) and not isinstance(attempted, bool) else 0,
        "examples": [f"{path}: syntax error" for path in errors[:_PARSE_EXAMPLE_LIMIT]],
    }


def extract_with_health(
    clone_root: Path, manifest: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any] | None]:
    """``extract_from_clone`` plus the extraction's parse health: ``(types, unresolved, health)``.

    *health* is ``python_parse_health`` for a python manifest and ``tree_sitter_parse_health`` of
    the engine's scout report for every other platform; ``None`` when the engine could not count.
    """
    platform = manifest["platform"]
    if platform == "python":
        types, unresolved = _extract_python_surface(clone_root)
        return types, unresolved, python_parse_health(clone_root)
    language = _LANGUAGE_BY_PLATFORM[platform]
    pkg_root = package_root.detect_package_root(clone_root, platform)
    parser = get_parser(language)
    result = api_surface.extract_api_surface(parser, language, pkg_root, clone_root, manifest["family"])
    scout = result[4] if len(result) > 4 else None
    health = tree_sitter_parse_health(scout) if isinstance(scout, dict) else None
    return result[0], [], health


def extract_from_clone(clone_root: Path, manifest: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Run the engine over an already-cloned checkout, per the manifest's declared platform.

    Returns ``(types, unresolved)``. A python manifest never reaches ``_LANGUAGE_BY_PLATFORM``,
    ``get_parser``, or ``api_surface.extract_api_surface`` at all - it is routed to
    ``python_surface.inspect_public_surface`` instead, and ``unresolved`` is always empty for
    every other, tree-sitter-backed platform (that engine has no equivalent concept). A thin
    wrapper over ``extract_with_health``.
    """
    types, unresolved, _health = extract_with_health(clone_root, manifest)
    return types, unresolved


def _enum_members_from_class(node: ast.ClassDef) -> list[dict[str, str]]:
    """A python enum class body's members as ``{"name", "value"}`` (the shape the tree-sitter
    reader produces). ``value`` is the source text of the right-hand side when it is a literal
    (``1``, ``"png"``, ``(1, 2)``), the marker ``auto()`` for an ``auto()`` call and ``""`` otherwise
    (another call, a name). Private (``_x``) names and everything that is not a plain assignment
    are not members."""
    # TC-532: the reading of a class body lives in python_surface (an ``auto()`` member is recorded with the
    # marker value ``auto()``); this keeps the dict shape the tree-sitter reader produces.
    return [{"name": name, "value": value} for name, value in python_surface.enum_member_pairs(node)]


def _find_class(tree: ast.AST, name: str, line: int | None) -> ast.ClassDef | None:
    candidates = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == name]
    for node in candidates:
        if node.lineno == line:
            return node
    return candidates[0] if len(candidates) == 1 else None


def fill_python_enum_members(types: list[dict[str, Any]], clone_root: Path) -> None:
    """Fill ``enum_members`` in place for every python ``enum`` entry from its class body.

    The pure-``ast`` reader reports that a class is an Enum/IntEnum/Flag... but not what it
    contains, so every python enum used to come out with zero members. An entry whose class body
    is not in its own ``file`` (a re-export stub in an ``__init__.py``) is left as it was - its
    definition entry carries the members. Files outside *clone_root* are never read.
    """
    root = clone_root.resolve()
    parsed: dict[str, ast.AST | None] = {}
    for entry in types:
        if entry.get("kind") != "enum" or entry.get("enum_members"):
            continue
        file = entry.get("file") or ""
        if file not in parsed:
            tree: ast.AST | None = None
            path = (root / file).resolve()
            if file and path.is_relative_to(root) and path.is_file():
                try:
                    # TC-555: through the same indentation repair as the surface.
                    tree = python_surface._parse_source_file(path)
                except (SyntaxError, ValueError, OSError):
                    tree = None
            parsed[file] = tree
        tree = parsed[file]
        if tree is None:
            continue
        node = _find_class(tree, entry.get("name") or "", entry.get("line"))
        if node is not None:
            entry["enum_members"] = _enum_members_from_class(node)


def summarize_symbols(types: list[dict[str, Any]]) -> dict[str, int]:
    """``{"types": N, "functions": M}`` over the non-test entries: ``functions`` counts entries
    whose ``kind`` is "function" (a Go/Rust/TypeScript free function or a python module function),
    ``types`` every other entry. The coverage line states the two separately."""
    entries = [entry for entry in types if not _is_test_entry(entry)]
    functions = sum(1 for entry in entries if entry.get("kind") == "function")
    return {"types": len(entries) - functions, "functions": functions}


def api_counts(types: list[dict[str, Any]]) -> dict[str, int]:
    """``{"types", "functions", "methods"}``: entries whose ``kind`` is not "function", entries whose
    ``kind`` is "function", and the total length of every entry's ``methods`` list. A method is
    counted as a method, never as a type."""
    functions = sum(1 for entry in types if entry.get("kind") == "function")
    methods = sum(len(entry.get("methods") or []) for entry in types)
    return {"types": len(types) - functions, "functions": functions, "methods": methods}


def extract_pinned_repository(manifest_path: Path, *, commit: str | None = None) -> dict[str, Any]:
    """Clone the manifest's pinned repository shallowly, run the engine over it, and return an
    api_surface-equivalent artifact recording the source repository and the exact commit
    extracted - the fixture is meaningless without that commit.

    With *commit* the clone is that exact commit (the pilot's pin) and ``source_commit`` is that
    SHA; without it the default branch HEAD is cloned, as before.
    """
    manifest = load_manifest(manifest_path)
    workdir = Path(tempfile.mkdtemp(prefix="foss-mcp-extract-"))
    try:
        if commit is None:
            commit_sha = clone_shallow(manifest["repository"], workdir)
        else:
            commit_sha = clone_shallow(manifest["repository"], workdir, commit=commit)
        types, unresolved, health = extract_with_health(workdir, manifest)
        platform = manifest["platform"]
        if platform == "python":
            fill_python_enum_members(types, workdir)
        # "python" is recorded literally here, never a tree-sitter grammar name, so downstream
        # code can tell which reader produced a given fixture.
        language = "python" if platform == "python" else _LANGUAGE_BY_PLATFORM[platform]
        artifact: dict[str, Any] = {
            "source_repository": manifest["repository"],
            "source_commit": commit_sha,
            "language": language,
            "type_count": len(types),
            "counts": api_counts(types),
            "types": types,
            "extractor_version": EXTRACTOR_VERSION,
            "symbol_counts": summarize_symbols(types),
        }
        if health is not None:
            artifact["parse_failures"] = health
        if unresolved:
            artifact["unresolved"] = unresolved
        return artifact
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _reference_strings(entry: dict[str, Any]) -> list[str]:
    """Every bare type-reference string *entry* itself makes to some OTHER type: its own
    ``bases`` entries, each method's ``return_type``/``params[].type``, and each property's
    ``type`` - the only per-type fields that name other types (may carry generic/array/nullable
    decoration, e.g. "List<Page>", "Page[]", "bool?")."""
    refs: list[str] = list(entry.get("bases") or [])
    for method in entry.get("methods") or []:
        refs.append(method.get("return_type") or "")
        for param in method.get("params") or []:
            refs.append(param.get("type") or "")
    for prop in entry.get("properties") or []:
        refs.append(prop.get("type") or "")
    return refs


def _centrality_scores(types: list[dict[str, Any]]) -> list[int]:
    """For each type in *types*, how many OTHER types in the same list reference its bare
    ``name`` as a whole word (``re.compile(rf"\\b{re.escape(name)}\\b")``) somewhere in their
    own bases/return_type/params/properties - so "List<Page>" counts as a reference to "Page"
    but "ExamplePage" or "PageSet" do not. Computed once, up front, over the FULL (pre-
    truncation) list, so the score reflects real centrality in the whole artifact.
    """
    haystacks = ["\n".join(_reference_strings(entry)) for entry in types]
    scores = [0] * len(types)
    for index, entry in enumerate(types):
        name = entry.get("name") or ""
        if not name:
            continue
        pattern = re.compile(rf"\b{re.escape(name)}\b")
        scores[index] = sum(
            1
            for other_index, haystack in enumerate(haystacks)
            if other_index != index and pattern.search(haystack)
        )
    return scores


_TEST_DIR_NAMES = frozenset({"test", "tests", "__tests__"})


def _is_test_entry(entry: dict[str, Any]) -> bool:
    """True when *entry* was read from a test source: a test-named file (the same rules the
    extraction engine applies when it walks a repository) or a ``test``/``tests``/``__tests__``
    directory anywhere in its path."""
    file = (entry.get("file") or "").replace("\\", "/")
    if not file:
        return False
    path = PurePosixPath(file)
    if any(part.lower() in _TEST_DIR_NAMES for part in path.parts[:-1]):
        return True
    return tree_helpers._is_test_source_file(path)  # type: ignore[arg-type]


def _has_members(entry: dict[str, Any]) -> bool:
    return bool(entry.get("methods") or entry.get("properties") or entry.get("enum_members"))


def _is_python_type(entry: dict[str, Any]) -> bool:
    return entry.get("kind") in ("class", "enum") and str(entry.get("file") or "").endswith(".py")


def _package_of(entry: dict[str, Any]) -> str:
    qualified = entry.get("class_import") or ""
    return qualified.rsplit(".", 1)[0] if "." in qualified else ""


_REEXPORT_CHAIN_LIMIT = 5


def _explicit_shell_owners(types: list[dict[str, Any]]) -> dict[int, int]:
    """TC-522: ``{shell index: definition index}`` for every entry that names its definition in
    ``reexport_of``, whatever members the shell has.

    The target is the entry whose ``class_import`` equals ``reexport_of`` (the first one in artifact
    order). A target that is itself a re-export is followed, up to ``_REEXPORT_CHAIN_LIMIT`` hops; the
    entry reached is the definition. A chain that loops back on itself, an entry that names itself and a
    target that is not an entry of *types* (a private origin) leave the entry out, to be paired by the
    member-less heuristic instead.
    """
    by_import: dict[str, int] = {}
    for index, entry in enumerate(types):
        key = entry.get("class_import")
        if key:
            by_import.setdefault(str(key), index)

    def follow(start: int) -> int | None:
        visited = {start}
        current = start
        for _ in range(_REEXPORT_CHAIN_LIMIT):
            target_name = types[current].get("reexport_of")
            if not target_name:
                break
            target = by_import.get(str(target_name))
            if target is None:
                break
            if target in visited:
                return None
            visited.add(target)
            current = target
        return current if current != start else None

    owner: dict[int, int] = {}
    for index, entry in enumerate(types):
        if entry.get("reexport_of"):
            definition = follow(index)
            if definition is not None:
                owner[index] = definition
    # A chain cut at the hop limit can end at an entry that is a shell itself: settle on its definition.
    for shell, definition in list(owner.items()):
        seen = {shell}
        while definition in owner and definition not in seen:
            seen.add(definition)
            definition = owner[definition]
        owner[shell] = definition
    return owner


def _alias_groups(types: list[dict[str, Any]]) -> list[list[int]]:
    """Partition the indices of *types* into selection groups.

    An entry that names its definition in ``reexport_of`` joins that definition's group as a shell
    (``_explicit_shell_owners``), even when the shell has members of its own - the methods a private
    base class gave it - and such a shell is never offered as a definition below.

    Otherwise a python class that carries no members but shares its ``name`` with one that does is a
    re-export shell of it (an ``__init__.py`` stub, or any other member-less alias): it joins its
    definition's group, so the definition and every shell are kept or dropped together and the
    group costs one slot. The definition is the member-bearing same-name class under the shell's
    own package when there is one, else any member-bearing same-name class; ties go to the
    smallest ``class_import``, so the pairing is deterministic. Every other entry is its own
    group. Each group lists its definition first, then its shells in artifact order; groups are
    ordered by their first member's artifact index.
    """
    owner = _explicit_shell_owners(types)
    definitions: dict[str, list[int]] = {}
    for index, entry in enumerate(types):
        if index in owner:
            continue
        if _is_python_type(entry) and _has_members(entry) and entry.get("name"):
            definitions.setdefault(entry["name"], []).append(index)

    def class_import(index: int) -> str:
        return types[index].get("class_import") or ""

    for index, entry in enumerate(types):
        if index in owner or not _is_python_type(entry) or _has_members(entry):
            continue
        candidates = definitions.get(entry.get("name") or "")
        if not candidates:
            continue
        package = _package_of(entry)
        under = [c for c in candidates if package and class_import(c).startswith(package + ".")]
        owner[index] = min(under or candidates, key=lambda c: (class_import(c), c))

    # TC-550: a member-less definition an explicit shell names can itself have been paired as a shell
    # just above (a name twin of a member-bearing class). The explicit shell then hung off an index that
    # is no group head and its group was never emitted: settle every owner on its final definition.
    for shell, definition in list(owner.items()):
        seen = {shell}
        while definition in owner and definition not in seen:
            seen.add(definition)
            definition = owner[definition]
        owner[shell] = definition

    members: dict[int, list[int]] = {}
    for shell, definition in sorted(owner.items()):
        members.setdefault(definition, []).append(shell)

    groups: list[list[int]] = []
    for index in range(len(types)):
        if index in owner:
            continue
        groups.append([index, *members.get(index, [])])
    return groups


def _group_centrality_scores(types: list[dict[str, Any]], groups: list[list[int]]) -> list[int]:
    """Per group, how many entries OUTSIDE the group reference the group's bare name as a whole
    word in their bases/return_type/params/properties.

    Nothing a group's own members say about the name counts: a real class's references to itself
    must not be credited to its re-export shells (which would let an empty shell outrank the
    class it merely re-exports). For a group of one this is exactly ``_centrality_scores``.
    """
    haystacks = ["\n".join(_reference_strings(entry)) for entry in types]
    scores = [0] * len(groups)
    for group_index, group in enumerate(groups):
        name = types[group[0]].get("name") or ""
        if not name:
            continue
        pattern = re.compile(rf"\b{re.escape(name)}\b")
        inside = set(group)
        scores[group_index] = sum(
            1 for other, haystack in enumerate(haystacks) if other not in inside and pattern.search(haystack)
        )
    return scores


_INTERNAL_MARKERS = frozenset(
    {
        "internal",
        "internals",
        "_internal",
        "impl",
        "engine",
        "generated",
        "vendor",
        "vendored",
        "third_party",
        "3rdparty",
        "node_modules",
        "detail",
    }
)
_PATH_SEPARATORS = re.compile(r"[/\\]")
_NAME_SEPARATORS = re.compile(r"\.|::|/")


def _is_internal_entry(entry: dict[str, Any]) -> bool:
    """True when *entry* lives in engine internals, generated or vendored code: a path segment of
    its ``file``, or a segment of its ``class_import`` / ``canonical_namespace``, equals one of
    ``_INTERNAL_MARKERS`` (case-insensitive), or its file is a ``.d.ts`` declaration file."""
    file = str(entry.get("file") or "")
    if file.lower().endswith(".d.ts"):
        return True
    segments = [segment.lower() for segment in _PATH_SEPARATORS.split(file)]
    for key in ("class_import", "canonical_namespace"):
        segments.extend(segment.lower() for segment in _NAME_SEPARATORS.split(str(entry.get(key) or "")))
    return any(segment in _INTERNAL_MARKERS for segment in segments)


def _is_internal_group(types: list[dict[str, Any]], group: list[int]) -> bool:
    """True only when EVERY entry of *group* is internal: a python definition whose public
    re-export shell sits in the package ``__init__.py`` is public."""
    return all(_is_internal_entry(types[index]) for index in group)


def reduce_fixture(
    artifact: dict[str, Any],
    *,
    max_types: int = 300,
    must_keep: Iterable[str] = frozenset(),
    readme_names: Mapping[str, int] | Iterable[str] | None = None,
) -> dict[str, Any]:
    """A representative, deterministic subset of *artifact*, under the fixture size budget.

    The point of the fixture is a reproducible offline test, not a data dump. Entries read from
    test sources are dropped first and never scored or counted. The rest are grouped (a python
    re-export shell travels with its definition and the pair costs one slot, see
    ``_alias_groups``) and the groups ranked by a CENTRALITY SCORE - how many entries outside
    the group reference its bare name - descending, so a type everything else depends on
    survives a cut that a pure alphabetical slice would have dropped. Groups that are engine
    internals, generated or vendored code (``_is_internal_group``) rank after every public
    group, so they fill only slots nothing else wants. The qualified-name key is the
    deterministic tiebreaker for equal scores, so the same source commit always reduces to the
    same fixture, and the truncation is recorded rather than hidden.

    The order of choice is: (1) every group whose type ``name`` is in *must_keep* (the types a
    pilot's own examples use); (2) the groups whose name is in *readme_names* (name to mention
    count, from ``readme_keep_names``), most mentioned first, until ``README_KEEP_FRACTION`` of
    the cap is chosen; (3) the remaining slots by rank. Documentation beats the internal-code
    heuristic, so (1) and (2) keep a named group even when it is internal. ``max_types`` still
    bounds the number of groups. With no *must_keep*, no *readme_names*, no test entries, no
    shells and no internal groups the result is identical to selecting the top ``max_types`` by
    centrality.
    """
    keep_names = frozenset(must_keep)
    mentions: dict[str, int] = {}
    if readme_names:
        if isinstance(readme_names, Mapping):
            mentions = dict(readme_names)
        else:
            mentions = dict.fromkeys(readme_names, 1)
    all_types = artifact["types"]
    types = [entry for entry in all_types if not _is_test_entry(entry)]
    groups = _alias_groups(types)
    scores = _group_centrality_scores(types, groups)
    internal = [_is_internal_group(types, group) for group in groups]

    def key(group_index: int) -> tuple[bool, int, str]:
        rep = types[groups[group_index][0]]
        return (internal[group_index], -scores[group_index], rep.get("class_import") or rep.get("name") or "")

    ordered = sorted(range(len(groups)), key=key)
    rank = {group_index: position for position, group_index in enumerate(ordered)}

    def name_of(group_index: int) -> str:
        return types[groups[group_index][0]].get("name") or ""

    chosen: list[int] = []
    if keep_names:
        chosen = [g for g in ordered if name_of(g) in keep_names][:max_types]
    chosen_set = set(chosen)
    if artifact.get("language") == "go":
        # TC-525: a Go package function is referenced by no type's signature, so centrality
        # ranks it last and cuts it. Reserve a bounded share of the budget for them, by source order.
        group_of = {index: g for g, group in enumerate(groups) for index in group}
        package_functions = sorted(
            (
                index
                for index, entry in enumerate(types)
                if entry.get("kind") == "function" and not entry.get("receiver_type")
            ),
            key=lambda index: (types[index].get("file") or "", types[index].get("line") or 0, index),
        )
        for index in package_functions[
            : min(len(package_functions), max_types * GO_FUNCTION_QUOTA_PERCENT // 100)
        ]:
            group_index = group_of[index]
            if len(chosen) >= max_types:
                break
            if group_index not in chosen_set:
                chosen.append(group_index)
                chosen_set.add(group_index)
    if mentions:
        readme_budget = min(max_types, math.ceil(round(max_types * README_KEEP_FRACTION, 9)))
        named = sorted(
            (g for g in ordered if g not in chosen_set and name_of(g) in mentions),
            key=lambda g: (-mentions[name_of(g)], rank[g]),
        )
        for group_index in named:
            if len(chosen) >= readme_budget:
                break
            chosen.append(group_index)
            chosen_set.add(group_index)
    for group_index in ordered:
        if len(chosen) >= max_types:
            break
        if group_index not in chosen_set:
            chosen.append(group_index)
            chosen_set.add(group_index)
    chosen.sort(key=rank.__getitem__)

    reduced = [types[index] for group_index in chosen for index in groups[group_index]]
    return {
        **{key: value for key, value in artifact.items() if key != "types"},
        "reduced_type_count": len(reduced),
        "truncated": len(reduced) < len(all_types),
        "types": reduced,
    }


# Fenced-block languages that hold no class references: shell/console/markup/diagram text.
_NON_CODE_FENCE_LANGUAGES = frozenset(
    {
        "",
        "mermaid",
        "bash",
        "sh",
        "shell",
        "zsh",
        "console",
        "text",
        "txt",
        "plaintext",
        "output",
        "json",
        "yaml",
        "yml",
        "toml",
        "ini",
        "xml",
        "html",
        "markdown",
        "md",
        "bat",
        "cmd",
        "powershell",
        "ps1",
        "diff",
    }
)
_FENCE_RE = re.compile(r"```[ \t]*([^\n`]*)\n(.*?)```", re.DOTALL)
_CAMEL = r"[A-Z][A-Za-z0-9_]*[a-z][A-Za-z0-9_]*"
_CLASS_REFERENCE_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        rf"(?<![\w.])({_CAMEL})\s*\(",  # Name(
        rf"(?<![\w])({_CAMEL})\s*\.",  # Name.
        rf"\bnew\s+(?:\w+\.)*({_CAMEL})\b",  # new Name / new Ns.Name
        rf"(?<![\w])({_CAMEL})\s*::",  # Name::
        rf"::\s*({_CAMEL})\b",  # ::Name
        rf"(?<!:):(?!:)\s*({_CAMEL})\b",  # : Name
        rf"<\s*({_CAMEL})\s*[>,<]",  # <Name>
        rf",\s*({_CAMEL})\s*>",  # <A, Name>
    )
)
# `.Name(` after a dot is a method call in most languages but a class in `slides.Presentation(`;
# the first pattern above deliberately skips it, and the alias pattern below recovers the class
# case from the import that makes `slides` a module alias.
_PY_ALIAS_RE = re.compile(
    r"^[ \t]*(?:import[ \t]+([\w.]+)[ \t]+as[ \t]+(\w+)|import[ \t]+([\w.]+)|from[ \t]+[\w.]+[ \t]+import[ \t]+(.+))$",
    re.M,
)
_STRING_RE = re.compile(r"\"\"\".*?\"\"\"|'''.*?'''|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'", re.DOTALL)
_COMMENT_RE = re.compile(r"/\*.*?\*/|(?:^|[ \t])(?:#|//)[^\n]*", re.DOTALL | re.M)


def _page_texts(path: Path) -> list[str]:
    """Every markdown string of a furnished page: the body after the front matter and each
    string value inside the front matter (examples live in ``single.block[].content`` etc.)."""
    text = path.read_text(encoding="utf-8")
    match = re.match(r"\A---\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)(.*)\Z", text, re.DOTALL)
    if match is None:
        return [text]
    texts = [match.group(2)]
    try:
        front = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return [text]
    stack: list[Any] = [front]
    while stack:
        value = stack.pop()
        if isinstance(value, str):
            texts.append(value)
        elif isinstance(value, dict):
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    return texts


def _class_names_in_code(code: str) -> set[str]:
    code = _COMMENT_RE.sub(" ", _STRING_RE.sub('""', code))
    names: set[str] = set()
    aliases: set[str] = set()
    for alias_match in _PY_ALIAS_RE.finditer(code):
        if alias_match.group(2):  # import a.b as x
            aliases.add(alias_match.group(2))
        elif alias_match.group(3):  # import a.b - used fully qualified as a.b.Name
            aliases.add(alias_match.group(3))
        else:  # from a import b, c as d - module-style (lowercase) names become aliases
            for item in (alias_match.group(4) or "").strip("() ").split(","):
                parts = item.split()
                if parts and parts[-1].isidentifier() and parts[-1][0].islower():
                    aliases.add(parts[-1])
    for pattern in _CLASS_REFERENCE_PATTERNS:
        names.update(pattern.findall(code))
    for alias in sorted(aliases):
        names.update(re.findall(rf"(?<![\w.]){re.escape(alias)}\.({_CAMEL})\b", code))
    return names


def names_used_in_examples(page_path: Path | str) -> frozenset[str]:
    """The CamelCase identifiers a furnished page's fenced code blocks use as class references.

    Reads the same front-matter-plus-markdown page file the indexer reads and scans every fenced
    block of a code language (shell, mermaid, JSON... are skipped; comments and string literals
    are blanked first) for ``Name(``, ``Name.``, ``new Name``, ``Name::``, ``: Name``,
    ``<Name>`` and ``alias.Name`` where *alias* is a python module alias. A method called
    through ``obj.Name(`` is not a class reference and is not matched; a CamelCase name that is
    merely the receiver of a call (``Name.``) is, which may over-include harmlessly because
    ``reduce_fixture`` only keeps names the artifact actually defines. The result is a set of
    names - deterministic, never lowercase.
    """
    found: set[str] = set()
    for text in _page_texts(Path(page_path)):
        for fence in _FENCE_RE.finditer(text):
            language = fence.group(1).strip().split()[0].lower() if fence.group(1).strip() else ""
            if language in _NON_CODE_FENCE_LANGUAGES:
                continue
            found.update(_class_names_in_code(fence.group(2)))
    return frozenset(found)


_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}")
_INLINE_SPAN_RE = re.compile(r"(?<!`)`([^`\n]{1,120})`(?!`)")


def readme_keep_names(page_path: Path | str) -> dict[str, int]:
    """Identifiers a furnished page (the library's README) presents as its API, with a mention count.

    Per identifier, the count is the number of fenced code blocks of a code language (the
    ``_NON_CODE_FENCE_LANGUAGES`` rule of ``names_used_in_examples``, comments and string
    literals blanked first) that contain it plus the number of inline code spans (a single
    backtick span of at most 120 characters on one line, outside fences) that contain it. Only
    identifiers that occur in at least one code fence, or in at least two spans/fences, are kept:
    a name mentioned once in passing inside a span is not evidence. Prose words are never counted.
    Deterministic. ``reduce_fixture`` only keeps names the artifact actually defines.
    """
    in_fences: dict[str, int] = {}
    in_spans: dict[str, int] = {}
    for text in _page_texts(Path(page_path)):
        for fence in _FENCE_RE.finditer(text):
            language = fence.group(1).strip().split()[0].lower() if fence.group(1).strip() else ""
            if language in _NON_CODE_FENCE_LANGUAGES:
                continue
            code = _COMMENT_RE.sub(" ", _STRING_RE.sub('""', fence.group(2)))
            for name in set(_IDENTIFIER_RE.findall(code)):
                in_fences[name] = in_fences.get(name, 0) + 1
        prose = _FENCE_RE.sub(" ", text)
        for span in _INLINE_SPAN_RE.finditer(prose):
            for name in set(_IDENTIFIER_RE.findall(span.group(1))):
                in_spans[name] = in_spans.get(name, 0) + 1
    counts: dict[str, int] = {}
    for name in sorted(set(in_fences) | set(in_spans)):
        fenced, spanned = in_fences.get(name, 0), in_spans.get(name, 0)
        if fenced >= 1 or fenced + spanned >= 2:
            counts[name] = fenced + spanned
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="product manifest, e.g. config/products/pdf/net.yaml")
    parser.add_argument(
        "output", type=Path, help="fixture path, e.g. tests/fixtures/pdf_net/api_surface.json"
    )
    parser.add_argument(
        "--max-types",
        type=int,
        default=None,
        help="cap on kept types; default is RECOMMENDED_MAX_TYPES for the manifest's pilot, else 300",
    )
    parser.add_argument(
        "--commit", default=None, help="extract at exactly this commit SHA (the pilot's pin), not HEAD"
    )
    parser.add_argument(
        "--keep-names-from",
        type=Path,
        default=None,
        metavar="PAGE",
        help="furnished page whose example code and README-named types must survive the cut",
    )
    args = parser.parse_args()

    manifest = load_manifest(args.manifest)
    max_types = args.max_types
    if max_types is None:
        max_types = RECOMMENDED_MAX_TYPES.get(f"{manifest['family']}/{manifest['platform']}", 300)
    artifact = extract_pinned_repository(args.manifest, commit=args.commit)
    must_keep = names_used_in_examples(args.keep_names_from) if args.keep_names_from else frozenset()
    readme_names = readme_keep_names(args.keep_names_from) if args.keep_names_from else None
    fixture = reduce_fixture(artifact, max_types=max_types, must_keep=must_keep, readme_names=readme_names)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(fixture, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"wrote {args.output} ({args.output.stat().st_size} bytes); "
        f"{fixture['reduced_type_count']}/{artifact['type_count']} types; "
        f"commit {artifact['source_commit']}"
    )


if __name__ == "__main__":
    main()
