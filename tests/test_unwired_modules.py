"""A real import-graph walk over ``src/foss_mcp/`` that names, explicitly, every top-level
public function or class genuinely unreachable from any production entrypoint - the exact
defect class AGENTS.md's "Integration and liveness" section describes: a well-tested unit that
sits completely unreferenced by any production entrypoint, silently, for dozens of accepted
cards, because every check that ran was scoped to the unit itself.

Method (real, structural, ``ast``-based - never a hand-maintained prose list):

1. Parse every ``.py`` file under ``src/``, ``infra/`` and ``tests/`` and build a file-level
   import graph (``import x``, ``from x import y``, ``from pkg import submodule``, relative
   imports, and the implicit edge every submodule import carries to each ancestor package's
   own ``__init__.py``).
2. BFS that graph from the eight real production entrypoints
   (``infra/serve_http.py``, ``infra/serve_stdio.py``, ``infra/ingest.py``,
   ``infra/build_chunks.py``, ``infra/rollback.py``, ``infra/fetch_product_reference.py``,
   ``infra/verify_product_reference_install.py``, ``src/foss_mcp/mcp/server.py``) to get every
   file genuinely on a production import path, and
   separately from every file under ``tests/`` to get every file a test genuinely exercises.
3. For every top-level public (non-underscore) function or class defined anywhere under
   ``src/foss_mcp/`` whose OWN FILE is on that production path, check whether the symbol itself
   - not just its file - is ever referenced (a ``from module import name`` match, a
   ``last_component.name`` attribute-chain reference, or a same-file call/reference from another
   already-production-reachable function in that file) anywhere in the production-reachable file
   set. A file being wired in does not make every function it defines wired in;
   ``vector_index_writer.py`` is genuinely on the production path (its own ``build_vector_index``
   is called from ``publisher.py``), yet nothing on that path ever calls its sibling
   ``query_vector_index`` - that gap is exactly as real as one whose whole file is unreached.
   (``foss_mcp.indexing.publisher.rollback_generation`` used to be exactly this kind of gap too -
   TC-140 added ``infra/rollback.py`` as a sixth entrypoint precisely because it now gives that
   function its first real caller, so it dropped out of ``_KNOWN_UNWIRED``.)
4. Historical note: ``foss_mcp.telemetry.usage_recorder`` used to sit in a file that was not on
   the production path at all, which once required a separate, hardcoded check here (outside
   step 3's scan, which is deliberately restricted to files already confirmed
   production-reachable) to confirm nothing under ``src/`` or ``infra/`` referenced it. TC-148
   ended that by making ``src/foss_mcp/mcp/server.py`` import ``foss_mcp.telemetry.usage_recorder``
   directly (``UsageRecorder``, ``build_event``, ``new_correlation_id``), putting that file on the
   production-reachable path for the first time. Step 3's ordinary scan now covers it like any
   other production-reachable file, so the separate special case was retired.

``_KNOWN_UNWIRED`` pins the resulting set exactly. Running this walk against the real, current
tree now finds FIVE such symbols: ``prepare_cpp_library``, ``verify_cpp_example``,
``query_vector_index``, ``document_schema.from_dict``, ``document_schema.to_dict`` - each
individually confirmed above the frozenset by manual citation of its own real callers (or lack
of them). ``_KNOWN_UNWIRED_AND_UNTESTED`` is currently empty: every symbol that once sat in that
worse ("reachable from NOTHING, not even a test") tier has since been wired in. (History: TC-140
shrank the set from its original fourteen - ``is_alive`` and ``round_trip_check`` were wired into
``infra/serve_http.py`` by TC-137, and ``rollback_generation`` got its first real caller once
``infra/rollback.py`` joined the entrypoint list above; TC-140 also deleted
``foss_mcp.mcp.health.is_ready`` outright - see that module's own docstring - rather than adding
it here, since ``round_trip_check`` is a confirmed strict superset of its guarantee. The set then
grew to eleven as this walk's own method matured, before TC-144 through TC-148's wiring fixes
(``resolve_anchor``, ``fetch_manifest_file``, ``read_repo_document``, ``build_manifest``,
``with_validation``, and ``usage_recorder``'s whole file via ``UsageRecorder``/``build_event``/
``new_correlation_id`` in ``server.py``) and TC-149's registration of
``infra/fetch_product_reference.py`` as a seventh entrypoint shrank it back down to the current
five, and emptied ``_KNOWN_UNWIRED_AND_UNTESTED`` entirely. TC-275 added
``infra/verify_product_reference_install.py`` as an eighth entrypoint - the first real caller of
``get_product_reference.install_coordinate_for_verification`` - which did not change the pinned
five: every file it newly makes production-reachable (itself, and ``infra/verify_package_registry.py``
transitively) lives under ``infra/``, outside this walk's ``SRC_FOSS``-only unwired scan, and
``get_product_reference.py`` was already production-reachable before this card.)
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
SRC_FOSS = SRC_ROOT / "foss_mcp"
INFRA_ROOT = REPO_ROOT / "infra"
TESTS_ROOT = REPO_ROOT / "tests"

ENTRYPOINTS: tuple[Path, ...] = (
    INFRA_ROOT / "serve_http.py",
    INFRA_ROOT / "serve_stdio.py",
    INFRA_ROOT / "ingest.py",
    INFRA_ROOT / "build_chunks.py",
    INFRA_ROOT / "rollback.py",
    INFRA_ROOT / "fetch_product_reference.py",
    INFRA_ROOT / "verify_product_reference_install.py",
    SRC_FOSS / "mcp" / "server.py",
)

# The pinned, current, genuinely-unreachable-from-any-production-entrypoint set (see module
# docstring for how this walk found each one). Renaming this binding (the negative control)
# leaves every comparison below referencing an undefined name - a NameError, not a silently
# vacuous pass - proving the checks below genuinely depend on this concrete, pinned set.
_KNOWN_UNWIRED: frozenset[str]
_KNOWN_UNWIRED = frozenset(
    {
        "foss_mcp.indexing.example_verifier.prepare_cpp_library",
        "foss_mcp.indexing.example_verifier.verify_cpp_example",
        "foss_mcp.indexing.vector_index_writer.query_vector_index",
        "foss_mcp.normalization.document_schema.from_dict",
        "foss_mcp.normalization.document_schema.to_dict",
    }
)

# The worse severity tier within the set above: not even a test references these two, so
# nothing anywhere - production or test - ever calls them. Currently empty: every symbol that
# used to be in this subset (fetch_manifest_file, with_validation) has since been wired in and
# dropped out of _KNOWN_UNWIRED entirely.
_KNOWN_UNWIRED_AND_UNTESTED: frozenset[str] = frozenset()

# A handful of symbols confirmed WIRED (real, live production callers), asserted below to make
# sure this walk can say "wired" as well as "unwired" - a check with only one reachable outcome
# would prove nothing about its own discriminating power.
_KNOWN_WIRED_CONTROL_SAMPLE: tuple[str, str] = (
    "foss_mcp.indexing.vector_index_writer",
    "build_vector_index",
)


_THIS_FILE = Path(__file__).resolve()


def _all_py_files() -> list[Path]:
    """Every ``.py`` file under ``src/``, ``infra/`` and ``tests/`` - excluding this file
    itself. This module's own source text necessarily contains every pinned dotted symbol name
    as a *string literal* (in ``_KNOWN_UNWIRED`` and its docstrings); a naive substring-based
    attribute-chain check (see ``referenced_via_attribute_chain``) would otherwise misread those
    literals as real code calling the very symbols this file is reporting as unreferenced.
    """
    files: list[Path] = []
    for root in (SRC_ROOT, INFRA_ROOT, TESTS_ROOT):
        files.extend(root.rglob("*.py"))
    return [f for f in files if f.resolve() != _THIS_FILE]


def _module_name_for(path: Path) -> str | None:
    try:
        rel = path.relative_to(SRC_ROOT)
    except ValueError:
        return None
    parts = list(rel.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _package_of(module: str, is_init: bool) -> str:
    if is_init:
        return module
    if "." not in module:
        return ""
    return module.rsplit(".", 1)[0]


class _ImportGraph:
    """A real, ast-parsed, file-level import graph over every ``.py`` file under ``src/``,
    ``infra/`` and ``tests/`` - the substrate every check in this module walks.
    """

    def __init__(self) -> None:
        self.files = _all_py_files()
        self.module_to_file: dict[str, Path] = {}
        self.file_to_module: dict[Path, str | None] = {}
        for f in self.files:
            m = _module_name_for(f)
            self.file_to_module[f] = m
            if m:
                self.module_to_file[m] = f

        self.sources: dict[Path, str] = {f: f.read_text(encoding="utf-8") for f in self.files}
        self.trees: dict[Path, ast.Module] = {
            f: ast.parse(src, filename=str(f)) for f, src in self.sources.items()
        }

        self.edges: dict[Path, set[Path]] = {f: set() for f in self.files}
        # (importing_file) -> list of (resolved_module_dotted_name, imported_name)
        self.from_imports: dict[Path, list[tuple[str, str]]] = {f: [] for f in self.files}

        for f, tree in self.trees.items():
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        target = self.module_to_file.get(alias.name)
                        if target is not None:
                            self.edges[f].add(target)
                elif isinstance(node, ast.ImportFrom):
                    mod = self._resolve_from_module(f, node)
                    if mod is None:
                        continue
                    for alias in node.names:
                        self.from_imports[f].append((mod, alias.name))
                        submodule = f"{mod}.{alias.name}"
                        submodule_file = self.module_to_file.get(submodule)
                        if submodule_file is not None:
                            self.edges[f].add(submodule_file)
                    target = self.module_to_file.get(mod)
                    if target is not None:
                        self.edges[f].add(target)

        # Implicit edge: importing any submodule always executes every ancestor package's own
        # __init__.py first - without this, a package __init__.py looks falsely unreachable.
        for f, mod in list(self.file_to_module.items()):
            if not mod:
                continue
            is_init = f.name == "__init__.py"
            ancestor_parts = mod.split(".")[:-1] if not is_init else mod.split(".")[:-1]
            for i in range(1, len(ancestor_parts) + 1):
                pkg_init = self.module_to_file.get(".".join(ancestor_parts[:i]))
                if pkg_init is not None and pkg_init != f:
                    self.edges[f].add(pkg_init)

    def _resolve_from_module(self, importing_file: Path, node: ast.ImportFrom) -> str | None:
        if not node.level:
            return node.module
        importing_module = self.file_to_module.get(importing_file)
        if importing_module is None:
            return None
        is_init = importing_file.name == "__init__.py"
        package = _package_of(importing_module, is_init)
        bits = package.split(".") if package else []
        if node.level > 1:
            bits = bits[: -(node.level - 1)] if len(bits) >= (node.level - 1) else []
        base = ".".join(bits)
        if node.module:
            return f"{base}.{node.module}" if base else node.module
        return base

    def reachable_from(self, starts: list[Path]) -> set[Path]:
        seen = set(starts)
        stack = list(starts)
        while stack:
            current = stack.pop()
            for nxt in self.edges.get(current, ()):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return seen

    def top_level_public_defs(self, path: Path) -> list[tuple[str, bool]]:
        """(name, is_class) for every top-level, non-underscore function/class def in *path*."""
        out: list[tuple[str, bool]] = []
        for node in self.trees[path].body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                if not node.name.startswith("_"):
                    out.append((node.name, isinstance(node, ast.ClassDef)))
        return out

    def references_symbol_in_file(self, path: Path, name: str) -> bool:
        """True if *name* is referenced (called, constructed, or simply named) anywhere in
        *path* other than inside its own top-level def/class statement of that exact name -
        i.e. does this file actually PUT the symbol to use, not merely define it.
        """
        found = False

        class Visitor(ast.NodeVisitor):
            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
                if node.name == name:
                    for child in ast.iter_child_nodes(node):
                        if child not in node.decorator_list:
                            self.visit(child)
                    return
                self.generic_visit(node)

            visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815

            def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
                if node.name == name:
                    for base in node.bases:
                        self.visit(base)
                    for stmt in node.body:
                        self.visit(stmt)
                    return
                self.generic_visit(node)

            def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
                nonlocal found
                if node.id == name:
                    found = True

            def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
                nonlocal found
                if node.attr == name:
                    found = True
                self.generic_visit(node)

        Visitor().visit(self.trees[path])
        return found

    def referenced_via_import(self, path: Path, defining_module: str, name: str) -> bool:
        return any(mod == defining_module and imported == name for mod, imported in self.from_imports[path])

    def referenced_via_attribute_chain(self, path: Path, defining_module: str, name: str) -> bool:
        """Catches duck-typed dispatch such as ``lang.python.export_surface(...)`` (called via
        a module object looked up at runtime, never bound to a literal ``python`` alias in the
        calling file) by checking for the defining module's own last path component immediately
        followed by ``.name`` anywhere in the candidate file's source text.
        """
        last_part = defining_module.rsplit(".", 1)[-1]
        return f"{last_part}.{name}" in self.sources[path]

    def has_external_reference(
        self, defining_file: Path, defining_module: str, name: str, candidates: set[Path]
    ) -> bool:
        for other in candidates:
            if other == defining_file or other not in self.sources:
                continue
            if self.referenced_via_import(
                other, defining_module, name
            ) or self.referenced_via_attribute_chain(other, defining_module, name):
                return True
        return False


_GRAPH = _ImportGraph()
_PROD_REACHABLE = _GRAPH.reachable_from(list(ENTRYPOINTS))
_TEST_FILES = [f for f in _GRAPH.files if TESTS_ROOT == f.parent or TESTS_ROOT in f.parents]
_TEST_REACHABLE = _GRAPH.reachable_from(_TEST_FILES)


def _is_wired(defining_file: Path, defining_module: str, name: str, universe: set[Path]) -> bool:
    """*name* (defined in *defining_file*) counts as reachable from *universe* (a
    production- or test-reachable file set) when either: (a) *defining_file* is itself in
    *universe* and something in *universe* - including *defining_file* itself - actually uses
    the symbol, or (b) some other file in *universe* references it directly.
    """
    if defining_file not in universe:
        return False
    if _GRAPH.references_symbol_in_file(defining_file, name):
        return True
    return _GRAPH.has_external_reference(defining_file, defining_module, name, universe)


def _compute_unwired_from_production_reachable_files() -> dict[str, tuple[Path, bool]]:
    """Every top-level public symbol whose OWN FILE is production-reachable, yet the symbol
    itself has zero real references anywhere in the production-reachable file set. Maps the
    dotted symbol name to (defining_file, is_class).
    """
    unwired: dict[str, tuple[Path, bool]] = {}
    for f in SRC_FOSS.rglob("*.py"):
        if f not in _PROD_REACHABLE:
            continue
        module = _GRAPH.file_to_module.get(f)
        if not module:
            continue
        for name, is_class in _GRAPH.top_level_public_defs(f):
            if not _is_wired(f, module, name, _PROD_REACHABLE):
                unwired[f"{module}.{name}"] = (f, is_class)
    return unwired


def test_the_real_import_graph_walk_finds_exactly_the_known_unwired_symbols() -> None:
    computed = set(_compute_unwired_from_production_reachable_files())

    assert computed == _KNOWN_UNWIRED, (
        f"the real import-graph walk's computed unwired set no longer matches the pinned "
        f"_KNOWN_UNWIRED. New in the walk (needs a decision - wire it, delete it, or add it "
        f"with a reason): {computed - _KNOWN_UNWIRED!r}. No longer unwired (the pin is now "
        f"stale and must shrink): {_KNOWN_UNWIRED - computed!r}."
    )


def test_each_known_unwired_symbol_genuinely_has_zero_production_callers() -> None:
    """Re-derives, per pinned symbol, that it is absent from the production-reachable file
    set's own reference graph - the concrete, per-symbol fact AGENTS.md's own text asserts
    ("both have zero callers anywhere under src/ or infra/ outside their own defining file").
    """
    for dotted in _KNOWN_UNWIRED:
        module, _, name = dotted.rpartition(".")
        defining_file = _GRAPH.module_to_file.get(module)
        assert defining_file is not None, f"{module} no longer resolves to a real file"
        candidates = {
            f
            for f in _GRAPH.files
            if (SRC_ROOT in f.parents or INFRA_ROOT in f.parents) and f != defining_file
        }
        assert not _GRAPH.has_external_reference(defining_file, module, name, candidates), (
            f"{dotted} now has a real caller under src/ or infra/ - it is no longer unwired "
            f"and must be removed from _KNOWN_UNWIRED."
        )


def test_the_untested_subset_is_pinned_separately_and_is_a_real_subset() -> None:
    """The worse severity tier: AGENTS.md distinguishes "reachable from tests only" (lower
    severity) from "reachable from NOTHING" (worse) and asks for the two to be reported
    separately. These two symbols are unreachable from production AND untouched by any test.
    """
    assert _KNOWN_UNWIRED_AND_UNTESTED <= _KNOWN_UNWIRED
    for dotted in _KNOWN_UNWIRED_AND_UNTESTED:
        module, _, name = dotted.rpartition(".")
        defining_file = _GRAPH.module_to_file[module]
        assert not _is_wired(defining_file, module, name, _TEST_REACHABLE), (
            f"{dotted} is now reachable from a test - move it out of "
            f"_KNOWN_UNWIRED_AND_UNTESTED (it is still fine to leave it in _KNOWN_UNWIRED)."
        )


def test_a_known_wired_symbol_is_not_flagged_unwired() -> None:
    """Discriminating-power sanity check: a real, live-called function must NOT show up as
    unwired, or this whole walk would be trivially satisfied by flagging everything (or
    nothing) regardless of the real graph.
    """
    module, name = _KNOWN_WIRED_CONTROL_SAMPLE
    defining_file = _GRAPH.module_to_file[module]
    assert defining_file in _PROD_REACHABLE
    assert _is_wired(defining_file, module, name, _PROD_REACHABLE)
    assert f"{module}.{name}" not in _KNOWN_UNWIRED


def test_no_real_src_module_is_reachable_from_neither_production_nor_tests() -> None:
    """The more severe file-level category AGENTS.md calls out distinctly: a whole file that
    NOTHING - not production, not even a test - ever imports. Scoped to files that actually
    define at least one top-level public symbol, since an empty placeholder package
    ``__init__.py`` (see docs/REPOSITORY_LAYOUT.md) reserving a namespace for not-yet-built code
    is a real, accepted, and entirely different situation from a built module going unnoticed.
    """
    orphaned = [
        f
        for f in SRC_FOSS.rglob("*.py")
        if f not in _PROD_REACHABLE and f not in _TEST_REACHABLE and _GRAPH.top_level_public_defs(f)
    ]
    assert orphaned == [], f"file(s) reachable from nothing at all: {[str(f) for f in orphaned]}"
