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
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterable
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
        if symbol.kind == "enum":
            entry["enum_members"] = []
        entries[symbol.qualified_name] = entry
        order.append(symbol.qualified_name)

    for symbol in surface.symbols:
        if symbol.kind not in ("method", "property"):
            continue
        owner = entries.get(symbol.qualified_name.rsplit(".", 1)[0])
        if owner is None:
            continue  # the owning class was itself unresolved/excluded; never guess one up
        if symbol.kind == "property":
            owner["properties"].append(
                {
                    "name": symbol.name,
                    "type": symbol.return_type or "",
                    "writable": bool(symbol.writable),
                    "doc": symbol.docstring or "",
                    "file": symbol.source_path,
                    "line": symbol.line,
                }
            )
            continue
        # Every declared parameter (TC-393), an unannotated one with type "" - _reference_strings()
        # reads `or ""`, so an empty type never pollutes centrality. A symbol built without
        # `params` falls back to its annotated `param_types`, as before.
        declared = symbol.params or symbol.param_types
        params = [{"name": arg_name, "type": arg_type} for arg_name, arg_type in declared]
        owner["methods"].append(
            {
                "name": symbol.name,
                "doc": symbol.docstring or "",
                "file": symbol.source_path,
                "line": symbol.line,
                "params": params,
                "return_type": symbol.return_type or "",  # TC-265: real return_type
                "deprecated": False,
                "deprecated_reason": "",
                "is_constructor": symbol.name == "__init__",
            }
        )

    return [entries[key] for key in order]


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
    api_surface._flatten_inheritance(types)
    return types, list(surface.unresolved)


def extract_from_clone(clone_root: Path, manifest: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Run the engine over an already-cloned checkout, per the manifest's declared platform.

    Returns ``(types, unresolved)``. A python manifest never reaches ``_LANGUAGE_BY_PLATFORM``,
    ``get_parser``, or ``api_surface.extract_api_surface`` at all - it is routed to
    ``python_surface.inspect_public_surface`` instead, and ``unresolved`` is always empty for
    every other, tree-sitter-backed platform (that engine has no equivalent concept).
    """
    platform = manifest["platform"]
    if platform == "python":
        return _extract_python_surface(clone_root)
    language = _LANGUAGE_BY_PLATFORM[platform]
    pkg_root = package_root.detect_package_root(clone_root, platform)
    parser = get_parser(language)
    types, *_rest = api_surface.extract_api_surface(
        parser, language, pkg_root, clone_root, manifest["family"]
    )
    return types, []


def _enum_members_from_class(node: ast.ClassDef) -> list[dict[str, str]]:
    """A python enum class body's members as ``{"name", "value"}`` (the shape the tree-sitter
    reader produces). ``value`` is the source text of the right-hand side when it is a literal
    (``1``, ``"png"``, ``(1, 2)``) and ``""`` otherwise (``auto()``, a call, a name). Private
    (``_x``) names and everything that is not a plain assignment are not members."""
    members: list[dict[str, str]] = []
    for statement in node.body:
        if isinstance(statement, ast.Assign):
            targets, value = statement.targets, statement.value
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            targets, value = [statement.target], statement.value
        else:
            continue
        try:
            ast.literal_eval(value)
            text = ast.unparse(value)
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
            text = ""
        for target in targets:
            if isinstance(target, ast.Name) and not target.id.startswith("_"):
                members.append({"name": target.id, "value": text})
    return members


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
                    tree = ast.parse(path.read_text(encoding="utf-8-sig", errors="replace"))
                except (SyntaxError, ValueError, OSError):
                    tree = None
            parsed[file] = tree
        tree = parsed[file]
        if tree is None:
            continue
        node = _find_class(tree, entry.get("name") or "", entry.get("line"))
        if node is not None:
            entry["enum_members"] = _enum_members_from_class(node)


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
        types, unresolved = extract_from_clone(workdir, manifest)
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
            "types": types,
        }
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


def _alias_groups(types: list[dict[str, Any]]) -> list[list[int]]:
    """Partition the indices of *types* into selection groups.

    A python class that carries no members but shares its ``name`` with one that does is a
    re-export shell of it (an ``__init__.py`` stub, or any other member-less alias): it joins its
    definition's group, so the definition and every shell are kept or dropped together and the
    group costs one slot. The definition is the member-bearing same-name class under the shell's
    own package when there is one, else any member-bearing same-name class; ties go to the
    smallest ``class_import``, so the pairing is deterministic. Every other entry is its own
    group. Each group lists its definition first, then its shells in artifact order; groups are
    ordered by their first member's artifact index.
    """
    definitions: dict[str, list[int]] = {}
    for index, entry in enumerate(types):
        if _is_python_type(entry) and _has_members(entry) and entry.get("name"):
            definitions.setdefault(entry["name"], []).append(index)

    def class_import(index: int) -> str:
        return types[index].get("class_import") or ""

    owner: dict[int, int] = {}
    for index, entry in enumerate(types):
        if not _is_python_type(entry) or _has_members(entry):
            continue
        candidates = definitions.get(entry.get("name") or "")
        if not candidates:
            continue
        package = _package_of(entry)
        under = [c for c in candidates if package and class_import(c).startswith(package + ".")]
        owner[index] = min(under or candidates, key=lambda c: (class_import(c), c))

    members: dict[int, list[int]] = {}
    for shell, definition in owner.items():
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


def reduce_fixture(
    artifact: dict[str, Any], *, max_types: int = 300, must_keep: Iterable[str] = frozenset()
) -> dict[str, Any]:
    """A representative, deterministic subset of *artifact*, under the fixture size budget.

    The point of the fixture is a reproducible offline test, not a data dump. Entries read from
    test sources are dropped first and never scored or counted. The rest are grouped (a python
    re-export shell travels with its definition and the pair costs one slot, see
    ``_alias_groups``) and the groups chosen by a CENTRALITY SCORE - how many entries outside
    the group reference its bare name - sorted descending, so a type everything else depends on
    survives a cut that a pure alphabetical slice would have dropped. The qualified-name key is
    the deterministic tiebreaker for equal scores, so the same source commit always reduces to
    the same fixture, and the truncation is recorded rather than hidden.

    Every group whose type ``name`` is in *must_keep* (the types a pilot's own examples use) is
    chosen first, in the same order, then the remaining budget is filled by centrality.
    ``max_types`` still bounds the number of groups. With no *must_keep*, no test entries and no
    shells the result is identical to selecting the top ``max_types`` by centrality.
    """
    keep_names = frozenset(must_keep)
    all_types = artifact["types"]
    types = [entry for entry in all_types if not _is_test_entry(entry)]
    groups = _alias_groups(types)
    scores = _group_centrality_scores(types, groups)

    def key(group_index: int) -> tuple[int, str]:
        rep = types[groups[group_index][0]]
        return (-scores[group_index], rep.get("class_import") or rep.get("name") or "")

    ordered = sorted(range(len(groups)), key=key)
    chosen: list[int] = []
    if keep_names:
        chosen = [g for g in ordered if (types[groups[g][0]].get("name") or "") in keep_names][:max_types]
    chosen_set = set(chosen)
    for group_index in ordered:
        if len(chosen) >= max_types:
            break
        if group_index not in chosen_set:
            chosen.append(group_index)
            chosen_set.add(group_index)
    rank = {group_index: position for position, group_index in enumerate(ordered)}
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="product manifest, e.g. config/products/pdf/net.yaml")
    parser.add_argument(
        "output", type=Path, help="fixture path, e.g. tests/fixtures/pdf_net/api_surface.json"
    )
    parser.add_argument("--max-types", type=int, default=300)
    parser.add_argument(
        "--commit", default=None, help="extract at exactly this commit SHA (the pilot's pin), not HEAD"
    )
    parser.add_argument(
        "--keep-names-from",
        type=Path,
        default=None,
        metavar="PAGE",
        help="furnished page whose example code names types that must survive the cut",
    )
    args = parser.parse_args()

    artifact = extract_pinned_repository(args.manifest, commit=args.commit)
    must_keep = names_used_in_examples(args.keep_names_from) if args.keep_names_from else frozenset()
    fixture = reduce_fixture(artifact, max_types=args.max_types, must_keep=must_keep)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(fixture, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"wrote {args.output} ({args.output.stat().st_size} bytes); "
        f"{fixture['reduced_type_count']}/{artifact['type_count']} types; "
        f"commit {artifact['source_commit']}"
    )


if __name__ == "__main__":
    main()
