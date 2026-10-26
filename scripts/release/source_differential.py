#!/usr/bin/env python3
"""Independent source-differential verifier (TC-566 / REQ-G3-009, RD3-150).

Round-3's own headline finding: 14,347 of 14,348 chunks of the shipped image rebuild byte-identically
from the committed fixtures, so every prior "fidelity" test only proved the extractor agrees with
itself. This script is the independent oracle that finding demanded: for one pilot's upstream library
at its pinned commit, it builds the REAL public API with the language's own tooling (never this
project's own tree-sitter engine, never a committed fixture read as truth) and diffs it against the
baked ``api_surface.json`` fixture, by category:

    missing     a real, public member the fixture never serves
    extra       a served member the independent oracle never found (a possible fabrication)
    signature   a served parameter list or return type that disagrees with the real one
    static      a served static/instance flag that disagrees with the real one
    default     a served "this parameter has a default" flag that disagrees with the real one
    enum_value  a served enum member's value that disagrees with the real one

Usage (the real, network-and-toolchain run the supervisor performs at closing time, never at check
time -- see ``main``'s docstring below for why):

    python scripts/release/source_differential.py [--fixtures DIR] [--only FAMILY_PLATFORM ...]
        [--ceilings-file FILE] [--library-root DIR] [--no-require-extractor-version]
        [--report-dir DIR]

Enumerators (``Enumerator``, ``(repository, commit, library_dir) -> Sequence[OracleSymbol]``) are
injected through ``Deps.enumerators``, copying ``scripts/release/verify_pushed_image.py``'s own
injection style, so ``diff_symbol``/``diff_language``/``verify_pilot`` and this module's CLI wiring
are exercised offline, with hand-written per-language micro-libraries, by every unit test -- no
compiler, interpreter or network call is ever made by ``tests/release/test_source_differential.py``.

``enumerate_python``, ``enumerate_go``, ``enumerate_rust``, ``enumerate_typescript``,
``enumerate_java``, ``enumerate_dotnet`` and ``enumerate_cpp`` are wired to real tooling here
(Python's own ``ast`` module, Go's own ``go/parser``+``go/ast`` via a small companion program,
Rust's ``syn`` crate via a small companion crate, TypeScript's own Compiler API --
``ts.createProgram`` plus its real ``TypeChecker``, never a regex, never this project's own
tree-sitter grammar, which would make the oracle circular -- via a small companion Node script,
the JDK's own Compiler Tree API (``com.sun.source.tree``/``com.sun.source.util``) at the PARSE
phase only via a small companion Java program, Roslyn's own parser
(``Microsoft.CodeAnalysis.CSharp``, ``CSharpSyntaxTree.ParseText``) at the SYNTAX/PARSE phase only
via a small companion .NET console project, and Clang's own AST (``-Xclang -ast-dump=json
-fsyntax-only``, the PARSE-and-SEMA phase only) via a direct ``clang.exe`` subprocess, respectively
-- all seven reading the real cloned source, no network beyond the clone, no external compiler for
Python, a pinned ``golang:1.26.4`` Docker container for Go (G3/TC-596), a pinned ``rust:1.98.1``
Docker container for Rust (G3/TC-590) -- both companion programs are built AND run entirely inside
their own container, never directly on the host, because this host's Windows Application Control
policy blocks execution of a freshly-built, unsigned native .exe; see ``_run_go_enumerator``'s and
``_run_rust_enumerator``'s own docstrings -- and, for TypeScript, Java, .NET and C++, the
already-installed ``typescript`` npm package / the JDK's own single-file source-launcher / the
``dotnet`` host's own ``dotnet run`` / this host's own already-installed LLVM 19.1.7 ``clang.exe``,
all run directly by the already-trusted ``node``/``java``/``dotnet``/``clang`` host binaries
(G3/TC-591, G3/TC-592, G3/TC-593, G3/TC-594): none of executing a ``.js`` text file, a ``.java``
text file, JIT-running managed IL, or running a pre-existing, already-installed native compiler
binary against a text header is a FRESHLY-BUILT, unsigned native binary, so WDAC never applies
and no container is needed; see ``_run_typescript_enumerator``'s, ``_run_java_enumerator``'s,
``_run_dotnet_enumerator``'s and ``_run_clang_ast_dump``'s own docstrings, which also record why
this is confirmed rather than merely assumed -- ``enumerate_cpp``'s own docstring also records why
this host's real ``clang.exe`` is used directly rather than the ``libclang`` PyPI binding the
card that wired it in originally named (that binding's own ``import clang.cindex`` fails in this
worktree, and a real system clang was found to already exist here, contrary to that card's own
dispatch-time premise). ``javascript``/``nodejs`` are the only platforms this project's own chart
never names a pilot for; ``DEFAULT_ENUMERATORS`` still maps them to ``_unimplemented_enumerator``,
which raises a precise, named ``NotImplementedError`` -- never a silent pass, never a fabricated
oracle -- and the ``Deps.enumerators`` injection point is exactly where a real one would be wired
in if a pilot for either platform is ever added, the same way track A's extraction fix landed as
eight separate per-language cards.

``REQUIRE_EXTRACTOR_VERSION`` mirrors the identically-named switch in ``image_workflow_gate.py``: a
fixture whose ``extractor_version`` is below the current schema (``run_extraction.EXTRACTOR_VERSION``)
fails outright, before any diff runs -- the fixture is known-stale on its face.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = REPO_ROOT / "src"
_DEPLOY_DIR = REPO_ROOT / "scripts" / "deploy"
DEFAULT_FIXTURES = REPO_ROOT / "tests" / "fixtures"
# Temp directories, never the repository, so a run with no --report-dir/--library-root never leaves
# output behind in the working tree (matches image_workflow_gate.py's own DEFAULT_REPORT_DIR).
DEFAULT_REPORT_DIR = Path(tempfile.gettempdir()) / "foss-mcp-source-differential"
DEFAULT_LIBRARY_ROOT = Path(tempfile.gettempdir()) / "foss-mcp-source-differential-libraries"

if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_DEPLOY_DIR) not in sys.path:
    # scripts/deploy is not a package; its pilots module owns reading the chart's pilot list.
    sys.path.insert(0, str(_DEPLOY_DIR))

import pilots as chart  # noqa: E402

from foss_mcp.extraction import run_extraction  # noqa: E402

SCHEMA_VERSION = 1
REPORT_NAME = "source_differential.json"
EXIT_FAILED = 1
EXIT_REFUSED = 2
GIT_FETCH_TIMEOUT_S = 180.0

CATEGORIES: tuple[str, ...] = ("missing", "extra", "signature", "static", "default", "enum_value")

# A fixture type is matched against an oracle symbol of the same bare ``name``. These are the kinds
# this engine ever emits for a class-like/enum-like container -- see tests/fixtures/*/api_surface.json.
CLASS_KINDS = frozenset(
    {
        "class",
        "class_declaration",
        "abstract_class_declaration",
        "struct_declaration",
        "struct_item",
        "struct_specifier",
        "record_declaration",
        "type_spec",
        "interface_declaration",
        "trait_item",
    }
)
ENUM_KINDS = frozenset({"enum", "enum_declaration", "enum_item", "enum_specifier"})


class Refused(Exception):
    """The request cannot be run as asked. Nothing was diffed; the exit code is 2."""


# ---------------------------------------------------------------------------------------------------------------
# The oracle shape: what an independent enumerator reports about one real symbol.
# ---------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class OracleParam:
    name: str
    has_default: bool = False


@dataclass(frozen=True)
class OracleMember:
    """One real method, property or enum value, as the independent tool actually read it."""

    name: str
    kind: str  # "method" | "property" | "enum_value"
    params: tuple[OracleParam, ...] = ()
    return_type: str | None = None  # None means "not checked" (the enumerator did not read it)
    is_static: bool = False
    enum_value: str | None = None  # None means "not checked"


@dataclass(frozen=True)
class OracleSymbol:
    """One real class/struct/enum/interface, matched against a baked fixture type by bare name."""

    name: str
    kind: str
    members: tuple[OracleMember, ...] = ()


# (repository, commit, library_dir) -> every real public symbol the independent tool found. library_dir
# is where the enumerator may clone/build the pinned commit; it is created if it does not exist.
Enumerator = Callable[[str, str, Path], Sequence[OracleSymbol]]


@dataclass
class Deps:
    enumerators: Mapping[str, Enumerator] = field(default_factory=dict)
    run: Callable[..., Any] | None = None  # subprocess.run, for the real git clone helper


# ---------------------------------------------------------------------------------------------------------------
# The diff engine: pure, offline, the same code path for every language.
# ---------------------------------------------------------------------------------------------------------------


@dataclass
class DiffCounts:
    missing: int = 0
    extra: int = 0
    signature: int = 0
    static: int = 0
    default: int = 0
    enum_value: int = 0
    checked: int = 0

    def add(self, other: DiffCounts) -> None:
        for category in (*CATEGORIES, "checked"):
            setattr(self, category, getattr(self, category) + getattr(other, category))

    def as_dict(self) -> dict[str, int]:
        return {category: getattr(self, category) for category in (*CATEGORIES, "checked")}


@dataclass
class Finding:
    symbol: str
    member: str
    category: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {
            "symbol": self.symbol,
            "member": self.member,
            "category": self.category,
            "detail": self.detail,
        }


def _baked_is_static(entry: Mapping[str, Any]) -> bool:
    modifiers = entry.get("modifiers") or []
    return "static" in modifiers or "classmethod" in modifiers


def _baked_params(entry: Mapping[str, Any]) -> list[tuple[str, bool]]:
    return [(str(param.get("name", "")), "default" in param) for param in entry.get("params") or []]


def diff_symbol(oracle: OracleSymbol, baked: Mapping[str, Any] | None) -> tuple[DiffCounts, list[Finding]]:
    """Compare one real symbol against its baked fixture type (or ``None`` when the fixture has none)."""
    counts = DiffCounts()
    findings: list[Finding] = []

    if baked is None:
        for member in oracle.members:
            counts.checked += 1
            counts.missing += 1
            findings.append(
                Finding(
                    oracle.name, member.name, "missing", "the whole symbol is absent from the baked fixture"
                )
            )
        return counts, findings

    baked_methods = {str(m.get("name")): m for m in baked.get("methods") or []}
    baked_props = {str(p.get("name")): p for p in baked.get("properties") or []}
    baked_enum = {str(e.get("name")): e for e in baked.get("enum_members") or []}

    oracle_method_names: set[str] = set()
    oracle_prop_names: set[str] = set()
    oracle_enum_names: set[str] = set()

    for member in oracle.members:
        counts.checked += 1
        if member.kind == "enum_value":
            oracle_enum_names.add(member.name)
            entry = baked_enum.get(member.name)
            if entry is None:
                counts.missing += 1
                findings.append(
                    Finding(oracle.name, member.name, "missing", "enum member absent from the baked fixture")
                )
                continue
            if member.enum_value is not None and str(entry.get("value") or "") != member.enum_value:
                counts.enum_value += 1
                findings.append(
                    Finding(
                        oracle.name,
                        member.name,
                        "enum_value",
                        f"expected value {member.enum_value!r}, baked {entry.get('value')!r}",
                    )
                )
            continue

        table = baked_methods if member.kind == "method" else baked_props
        (oracle_method_names if member.kind == "method" else oracle_prop_names).add(member.name)
        entry = table.get(member.name)
        if entry is None:
            counts.missing += 1
            findings.append(
                Finding(oracle.name, member.name, "missing", f"{member.kind} absent from the baked fixture")
            )
            continue

        if _baked_is_static(entry) != member.is_static:
            counts.static += 1
            findings.append(
                Finding(
                    oracle.name,
                    member.name,
                    "static",
                    f"expected is_static={member.is_static}, baked modifiers={entry.get('modifiers')!r}",
                )
            )

        if member.kind == "method":
            baked_params = _baked_params(entry)
            oracle_names = [param.name for param in member.params]
            baked_names = [name for name, _ in baked_params]
            if oracle_names != baked_names:
                counts.signature += 1
                findings.append(
                    Finding(
                        oracle.name,
                        member.name,
                        "signature",
                        f"expected params {oracle_names}, baked {baked_names}",
                    )
                )
            else:
                oracle_defaults = [param.has_default for param in member.params]
                baked_defaults = [has_default for _, has_default in baked_params]
                if oracle_defaults != baked_defaults:
                    counts.default += 1
                    findings.append(
                        Finding(
                            oracle.name,
                            member.name,
                            "default",
                            f"expected defaults {oracle_defaults}, baked {baked_defaults}",
                        )
                    )

            if member.return_type is not None:
                baked_return = str(entry.get("return_type") or "")
                if baked_return != member.return_type:
                    counts.signature += 1
                    findings.append(
                        Finding(
                            oracle.name,
                            member.name,
                            "signature",
                            f"expected return type {member.return_type!r}, baked {baked_return!r}",
                        )
                    )

    for name in baked_methods:
        if name not in oracle_method_names:
            counts.extra += 1
            findings.append(
                Finding(oracle.name, name, "extra", "method served that the independent oracle never found")
            )
    for name in baked_props:
        if name not in oracle_prop_names:
            counts.extra += 1
            findings.append(
                Finding(oracle.name, name, "extra", "property served that the independent oracle never found")
            )
    for name in baked_enum:
        if name not in oracle_enum_names:
            counts.extra += 1
            findings.append(
                Finding(
                    oracle.name, name, "extra", "enum member served that the independent oracle never found"
                )
            )

    return counts, findings


def diff_language(
    symbols: Sequence[OracleSymbol], baked_types: Sequence[Mapping[str, Any]]
) -> tuple[DiffCounts, list[Finding]]:
    """Diff every oracle symbol against the baked fixture's types of the same bare name."""
    baked_by_name: dict[str, Mapping[str, Any]] = {str(t.get("name")): t for t in baked_types}
    total = DiffCounts()
    findings: list[Finding] = []
    for symbol in symbols:
        counts, found = diff_symbol(symbol, baked_by_name.get(symbol.name))
        total.add(counts)
        findings.extend(found)
    return total, findings


# ---------------------------------------------------------------------------------------------------------------
# The extractor-version gate: a fixture below the current schema fails outright, independent of the diff.
# ---------------------------------------------------------------------------------------------------------------


def check_extractor_version(fixture: Mapping[str, Any], *, current_version: int) -> str | None:
    """None when the fixture's own ``extractor_version`` is at least ``current_version``; else the reason."""
    version = fixture.get("extractor_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < current_version:
        return f"extractor_version {version!r} is below the current schema {current_version}"
    return None


# ---------------------------------------------------------------------------------------------------------------
# Ceilings: per-language, per-category, absolute error-count ceilings. 0 by default -- any real
# disagreement fails -- exactly the bar a hand-written offline micro-library must clear.
# ---------------------------------------------------------------------------------------------------------------

LANGUAGE_ERROR_CEILINGS: dict[str, dict[str, int]] = {
    language: {category: 0 for category in CATEGORIES}
    for language in ("python", "typescript", "javascript", "go", "rust", "java", "net", "dotnet", "cpp")
}


def load_ceilings(path: Path | None) -> dict[str, dict[str, int]]:
    """``LANGUAGE_ERROR_CEILINGS``, overridden per language/category by ``path`` (a JSON object), when given."""
    ceilings = {language: dict(rates) for language, rates in LANGUAGE_ERROR_CEILINGS.items()}
    if path is not None:
        overrides = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(overrides, dict):
            raise Refused(f"{path} is not a JSON object of language -> {{category: ceiling}}")
        for language, rates in overrides.items():
            if not isinstance(rates, dict):
                raise Refused(f"{path}: {language!r} is not an object of category -> ceiling")
            bucket = ceilings.setdefault(language, {category: 0 for category in CATEGORIES})
            bucket.update({str(k): int(v) for k, v in rates.items()})
    return ceilings


def evaluate_ceilings(counts: DiffCounts, ceilings: Mapping[str, int]) -> list[str]:
    """The reasons ``counts`` exceeds ``ceilings``, or ``[]`` when every category is within its ceiling."""
    reasons: list[str] = []
    for category in CATEGORIES:
        count = getattr(counts, category)
        ceiling = ceilings.get(category, 0)
        if count > ceiling:
            reasons.append(f"{category} count {count} exceeds the ceiling {ceiling}")
    return reasons


# ---------------------------------------------------------------------------------------------------------------
# One pilot
# ---------------------------------------------------------------------------------------------------------------


@dataclass
class PilotReport:
    pilot: str
    language: str
    extractor_version_reason: str | None
    counts: dict[str, int]
    reasons: list[str]
    findings: list[dict[str, str]]

    @property
    def passed(self) -> bool:
        return self.extractor_version_reason is None and not self.reasons

    def as_dict(self) -> dict[str, Any]:
        return {
            "pilot": self.pilot,
            "language": self.language,
            "status": "pass" if self.passed else "fail",
            "extractor_version_reason": self.extractor_version_reason,
            "counts": self.counts,
            "reasons": self.reasons,
            "findings": self.findings,
        }


def verify_pilot(
    *,
    pilot: str,
    language: str,
    fixture: Mapping[str, Any],
    symbols: Sequence[OracleSymbol],
    ceilings: Mapping[str, Mapping[str, int]],
    require_extractor_version: bool,
    current_extractor_version: int,
) -> PilotReport:
    """Diff ``symbols`` (the independent oracle) against ``fixture`` (the baked data) and gate the result."""
    version_reason = (
        check_extractor_version(fixture, current_version=current_extractor_version)
        if require_extractor_version
        else None
    )
    total, findings = diff_language(symbols, fixture.get("types") or [])
    reasons = evaluate_ceilings(total, ceilings.get(language, {category: 0 for category in CATEGORIES}))
    return PilotReport(
        pilot=pilot,
        language=language,
        extractor_version_reason=version_reason,
        counts=total.as_dict(),
        reasons=reasons,
        findings=[finding.as_dict() for finding in findings],
    )


# ---------------------------------------------------------------------------------------------------------------
# Enumerators. Only Python is real tooling; every other platform is a named, honest refusal (see the
# module docstring) until a follow-up card wires its own real compiler/reflection/AST reader in.
# ---------------------------------------------------------------------------------------------------------------


def _run_git(
    args: list[str], *, cwd: Path, run: Callable[..., Any] | None = None
) -> subprocess.CompletedProcess[str]:
    runner = run or subprocess.run
    return runner(
        args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )


def clone_pinned_commit(
    repository: str, commit: str, library_dir: Path, *, run: Callable[..., Any] | None = None
) -> None:
    """Shallow-fetch exactly one pinned commit of ``repository`` into ``library_dir``.

    Mirrors ``src/foss_mcp/indexing/example_verifier.py``'s own ``_clone_pinned_commit`` git plumbing
    (init, remote add, fetch --depth 1, checkout) -- that helper is private to its own module, so this
    is a small, deliberate duplication rather than an import across an unrelated package boundary.
    """
    library_dir.mkdir(parents=True, exist_ok=True)
    repo_url = f"https://github.com/{repository}.git"

    init = _run_git(["git", "init"], cwd=library_dir, run=run)
    if init.returncode != 0:
        raise RuntimeError(f"git init failed in {library_dir}:\n{init.stdout}\n{init.stderr}")

    remote = _run_git(["git", "remote", "add", "origin", repo_url], cwd=library_dir, run=run)
    if remote.returncode != 0:
        raise RuntimeError(f"git remote add failed for {repo_url}:\n{remote.stdout}\n{remote.stderr}")

    fetch = _run_git(["git", "fetch", "--depth", "1", "origin", commit], cwd=library_dir, run=run)
    if fetch.returncode != 0:
        raise RuntimeError(
            f"git fetch of commit {commit} from {repo_url} failed:\n{fetch.stdout}\n{fetch.stderr}"
        )

    checkout = _run_git(["git", "checkout", commit], cwd=library_dir, run=run)
    if checkout.returncode != 0:
        raise RuntimeError(f"git checkout of commit {commit} failed:\n{checkout.stdout}\n{checkout.stderr}")


def _is_public_python_name(name: str) -> bool:
    return not name.startswith("_") or (name.startswith("__") and name.endswith("__"))


def _python_enum_bases(bases: list[Any]) -> bool:
    import ast as _ast

    names = {node.id for node in bases if isinstance(node, _ast.Name)}
    names |= {node.attr for node in bases if isinstance(node, _ast.Attribute)}
    return bool(names & {"Enum", "IntEnum", "Flag", "IntFlag", "StrEnum"})


def _python_symbol_from_classdef(node: Any) -> OracleSymbol:
    import ast as _ast

    is_enum = _python_enum_bases(node.bases)
    members: list[OracleMember] = []
    for item in node.body:
        if is_enum and isinstance(item, _ast.Assign):
            for target in item.targets:
                if isinstance(target, _ast.Name) and _is_public_python_name(target.id):
                    value = None
                    try:
                        value = _ast.literal_eval(item.value)
                    except Exception:
                        value = None
                    members.append(
                        OracleMember(
                            name=target.id,
                            kind="enum_value",
                            enum_value=(str(value) if value is not None else None),
                        )
                    )
            continue
        if isinstance(item, _ast.FunctionDef | _ast.AsyncFunctionDef) and _is_public_python_name(item.name):
            decorators = {
                decorator.id if isinstance(decorator, _ast.Name) else getattr(decorator, "attr", "")
                for decorator in item.decorator_list
            }
            is_static = "staticmethod" in decorators or "classmethod" in decorators
            params = [arg.arg for arg in item.args.args if arg.arg != "self" and arg.arg != "cls"]
            defaulted = set(params[len(params) - len(item.args.defaults) :]) if item.args.defaults else set()
            members.append(
                OracleMember(
                    name=item.name,
                    kind="method",
                    params=tuple(OracleParam(name=p, has_default=p in defaulted) for p in params),
                    is_static=is_static,
                )
            )
    return OracleSymbol(name=node.name, kind="enum" if is_enum else "class", members=tuple(members))


def enumerate_python(
    repository: str, commit: str, library_dir: Path, *, run: Callable[..., Any] | None = None
) -> list[OracleSymbol]:
    """The real public API of a Python library, read with the standard library's own ``ast`` module.

    Clones ``repository`` at ``commit`` into ``library_dir`` (unless it already looks checked out: a
    ``.git`` directory already present), then parses every ``*.py`` file under it with ``ast.parse``
    and collects every public, module-level ``ClassDef`` (and the ``Enum``/``IntEnum``/``Flag``
    subclasses among them) as one ``OracleSymbol`` each. A file that fails to parse is skipped, never
    silently merged into another file's symbols.
    """
    import ast as _ast

    if not (library_dir / ".git").is_dir():
        clone_pinned_commit(repository, commit, library_dir, run=run)

    symbols: list[OracleSymbol] = []
    for path in sorted(library_dir.rglob("*.py")):
        if any(part in {"tests", "test", "examples", "docs"} for part in path.parts):
            continue
        try:
            tree = _ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, ValueError, UnicodeDecodeError):
            continue
        for node in tree.body:
            if isinstance(node, _ast.ClassDef) and _is_public_python_name(node.name):
                symbols.append(_python_symbol_from_classdef(node))
    return symbols


_ORACLE_HELPERS_DIR = Path(__file__).resolve().parent / "oracle_helpers"
GO_ENUMERATOR_SOURCE = _ORACLE_HELPERS_DIR / "go_enumerator.go"
GO_DOCKER_IMAGE = "golang:1.26.4"  # matches this host's installed go version go1.26.4; see plans/TC-596.yaml


def _run_go_enumerator(
    library_dir: Path, *, run: Callable[..., Any] | None = None, image: str = GO_DOCKER_IMAGE
) -> list[dict[str, Any]]:
    """Run ``go_enumerator.go`` (Go's own ``go/parser``+``go/ast``, no new dependency) and parse
    its JSON stdout. Built AND executed entirely inside a pinned Docker container -- never
    directly on the host (G3/TC-596).

    This host's Windows Application Control policy blocks execution of a freshly-built, unsigned
    native ``.exe`` -- confirmed directly against this exact function (``go_enumerator.exe``,
    "Enterprise signing level requirements") when TC-590's worker independently reproduced the
    same condition while reworking the Rust enumerator (OWNER-16); TC-589 itself only landed
    because its own live run happened to complete before this condition re-triggered here, a
    fragile timing accident, never a durable guarantee. WDAC governs native Windows PE execution
    only -- a Linux binary executed inside Docker Desktop's own Linux VM is completely outside
    its scope, so ``go run`` moves inside the container too, eliminating the dependency on the
    host's own WDAC posture entirely, mirroring ``_run_rust_enumerator``'s own fix (G3/TC-590).

    The directory holding ``go_enumerator.go`` is mounted read-only (the companion program is
    never written to); ``library_dir`` is mounted read-write, since Go's own module/build cache
    may want to write under the directory tree it is reading from. ``go_enumerator.go`` has no
    non-stdlib imports (see its own module docstring), so this never reaches the network -- this
    function, and therefore every test that calls ``enumerate_go``, stays compatible with this
    card's own ``network: false`` requirement even though it shells out to Docker, because the
    pinned image is already cached locally before the checks ever run.
    """
    runner = run or subprocess.run
    result = runner(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{_ORACLE_HELPERS_DIR}:/work:ro",
            "-v",
            f"{library_dir}:/library",
            "-w",
            "/work",
            image,
            "go",
            "run",
            "go_enumerator.go",
            "/library",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"go_enumerator.go (Docker image {image}) failed against {library_dir}:\n"
            f"{result.stdout}\n{result.stderr}"
        )
    parsed = json.loads(result.stdout)
    if not isinstance(parsed, list):
        raise RuntimeError(f"go_enumerator.go printed a non-array JSON document: {result.stdout!r}")
    return parsed


def enumerate_go(
    repository: str, commit: str, library_dir: Path, *, run: Callable[..., Any] | None = None
) -> list[OracleSymbol]:
    """The real public API of a Go library, read with Go's own ``go/parser``+``go/ast`` (stdlib,
    no new dependency) through a small companion program
    (``scripts/release/oracle_helpers/go_enumerator.go``), built and run entirely inside a pinned
    Docker container (never directly on the host -- see ``_run_go_enumerator``'s own docstring
    for why, G3/TC-596).

    Clones ``repository`` at ``commit`` into ``library_dir`` (unless it already looks checked out:
    a ``.git`` directory already present), runs the companion program with ``go run`` inside the
    container against that directory, and maps its JSON output onto
    ``OracleSymbol``/``OracleMember``/``OracleParam``. Go has no default parameter values, so
    every ``OracleParam.has_default`` stays ``False``; a Go method always needs a receiver (there
    is no Go analogue of ``@staticmethod``), so every member's ``is_static`` stays ``False``.
    """
    if not (library_dir / ".git").is_dir():
        clone_pinned_commit(repository, commit, library_dir, run=run)

    raw_symbols = _run_go_enumerator(library_dir, run=run)
    symbols: list[OracleSymbol] = []
    for raw in raw_symbols:
        members = tuple(
            OracleMember(
                name=str(raw_member["name"]),
                kind=str(raw_member["kind"]),
                params=tuple(OracleParam(name=str(p["name"])) for p in raw_member.get("params") or []),
                is_static=bool(raw_member.get("is_static", False)),
            )
            for raw_member in raw.get("members") or []
        )
        symbols.append(OracleSymbol(name=str(raw["name"]), kind=str(raw["kind"]), members=members))
    return symbols


RUST_ENUMERATOR_CRATE_DIR = _ORACLE_HELPERS_DIR / "rust_enumerator"
RUST_DOCKER_IMAGE = "rust:1.98.1"  # matches this host's installed rustc exactly; see plans/TC-590.yaml


def _run_rust_enumerator(
    library_dir: Path, *, run: Callable[..., Any] | None = None, image: str = RUST_DOCKER_IMAGE
) -> list[dict[str, Any]]:
    """Run ``rust_enumerator`` (the ``syn`` crate,
    ``scripts/release/oracle_helpers/rust_enumerator/``) and parse its JSON stdout. Built AND
    executed entirely inside a pinned Docker container -- never directly on the host.

    This host's Windows Application Control policy blocks execution of a freshly-built, unsigned
    native ``.exe`` (confirmed via the Code Integrity event log; see plans/TC-590.yaml's REWORK
    note). WDAC governs native Windows PE execution only -- a Linux binary executed inside Docker
    Desktop's own Linux VM is completely outside its scope, so the crate's own build (which would
    otherwise compile and run a transitive dependency's ``build-script-build.exe`` on the host)
    and its resulting binary both run inside the container instead, eliminating the dependency on
    the host's own WDAC posture entirely.

    The crate's own ``.cargo/config.toml`` redirects every dependency to its vendored copy next to
    this file (``cargo vendor``), so ``cargo run --offline`` inside the container never reaches
    the network either -- this function, and therefore every test that calls ``enumerate_rust``,
    stays compatible with this card's own ``network: false`` requirement even though it shells
    out to Docker, because the pinned image and every crate dependency are already cached/vendored
    locally before the checks ever run.
    """
    runner = run or subprocess.run
    result = runner(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{RUST_ENUMERATOR_CRATE_DIR}:/work",
            "-v",
            f"{library_dir}:/library:ro",
            "-w",
            "/work",
            image,
            "cargo",
            "run",
            "--release",
            "--offline",
            "--quiet",
            "--",
            "/library",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"rust_enumerator (Docker image {image}) failed against {library_dir}:\n"
            f"{result.stdout}\n{result.stderr}"
        )
    try:
        parsed = json.loads(result.stdout)
    except ValueError as exc:
        raise RuntimeError(f"rust_enumerator printed non-JSON stdout: {result.stdout!r}") from exc
    if not isinstance(parsed, list):
        raise RuntimeError(f"rust_enumerator printed a non-array JSON document: {result.stdout!r}")
    return parsed


def enumerate_rust(
    repository: str, commit: str, library_dir: Path, *, run: Callable[..., Any] | None = None
) -> list[OracleSymbol]:
    """The real public API of a Rust library, read with the ``syn`` crate (real syntax, never
    macro-expanded output -- the design table's own "Full (macro-expansion-blind, disclosed)"
    fidelity note, tested by ``tests/release/test_source_differential.py``) through a small
    companion crate (``scripts/release/oracle_helpers/rust_enumerator/``), built and run entirely
    inside a pinned Docker container (never directly on the host -- see
    ``_run_rust_enumerator``'s own docstring for why).

    Clones ``repository`` at ``commit`` into ``library_dir`` (unless it already looks checked
    out: a ``.git`` directory already present), runs the companion crate against that directory,
    and maps its JSON output onto ``OracleSymbol``/``OracleMember``/``OracleParam``. Rust has no
    default parameter values, so every ``OracleParam.has_default`` stays ``False``. A ``pub
    struct`` and a ``pub trait`` both map to ``OracleSymbol.kind == "class"`` (a trait's own fn
    items become "method" members, matching this module's existing class-like convention); a
    ``pub enum`` maps to ``kind == "enum"`` (its variants as "enum_value" members, each carrying
    its exact explicit discriminant when the source gives one, never merely a count).
    """
    if not (library_dir / ".git").is_dir():
        clone_pinned_commit(repository, commit, library_dir, run=run)

    raw_symbols = _run_rust_enumerator(library_dir, run=run)
    symbols: list[OracleSymbol] = []
    for raw in raw_symbols:
        members = tuple(
            OracleMember(
                name=str(raw_member["name"]),
                kind=str(raw_member["kind"]),
                params=tuple(OracleParam(name=str(p["name"])) for p in raw_member.get("params") or []),
                is_static=bool(raw_member.get("is_static", False)),
                enum_value=(
                    str(raw_member["enum_value"]) if raw_member.get("enum_value") is not None else None
                ),
            )
            for raw_member in raw.get("members") or []
        )
        raw_kind = str(raw["kind"])
        kind = "enum" if raw_kind == "enum" else "class"  # struct/trait -> class (see docstring)
        symbols.append(OracleSymbol(name=str(raw["name"]), kind=kind, members=members))
    return symbols


TS_ENUMERATOR_SOURCE = _ORACLE_HELPERS_DIR / "ts_enumerator.js"
NODE_BINARY = "node"
# The already-installed `typescript` npm package's own directory on this host, confirmed
# directly (`node -e "console.log(require.resolve('typescript'))"` under the activated
# toolchain's NODE_PATH) rather than assumed from this card's own purpose text -- see
# plans/TC-591.yaml. Pinned the same way GO_DOCKER_IMAGE/RUST_DOCKER_IMAGE pin this host's own
# installed Go/Rust versions: a visible, diagnosable error if a future host ever moves it,
# never a silent wrong answer.
TYPESCRIPT_NPM_PACKAGE_DIR = Path(r"C:\dev-tools\foss-mcp\rp-toolchains\npm\prefix\node_modules\typescript")


def _run_typescript_enumerator(
    library_dir: Path,
    *,
    run: Callable[..., Any] | None = None,
    node_binary: str = NODE_BINARY,
    typescript_package_dir: Path = TYPESCRIPT_NPM_PACKAGE_DIR,
) -> list[dict[str, Any]]:
    """Run ``ts_enumerator.js`` (the REAL TypeScript Compiler API --
    ``scripts/release/oracle_helpers/ts_enumerator.js``) and parse its JSON stdout.

    Unlike ``_run_go_enumerator``/``_run_rust_enumerator``, this never shells out to Docker.
    TypeScript execution goes through ``node`` running an already-trusted, already-installed
    interpreter over a plain ``.js`` text file -- never a freshly-built, unsigned native
    binary -- so this host's Windows Application Control policy (which blocks exactly that
    native-binary case; see the Go/Rust docstrings above) simply does not apply here. This was
    confirmed directly rather than assumed before this function was written (G3/TC-591): every
    test in ``tests/release/test_source_differential.py`` that exercises this function (and the
    one real, live, network-using run against the pinned pdf/typescript pilot performed once at
    this card's own closing time) runs ``node`` directly against the host, with no container
    anywhere in the invocation below.

    ``typescript_package_dir`` must already exist (the package is pre-installed; this function
    never ``npm install``s anything, keeping this card's own ``network: false`` requirement for
    every unit test true even though ``enumerate_typescript`` itself clones over the network).
    """
    if not typescript_package_dir.is_dir():
        raise RuntimeError(
            f"the typescript npm package was not found at {typescript_package_dir} -- this path is "
            "pinned in source_differential.py (TYPESCRIPT_NPM_PACKAGE_DIR); if the toolchain moved, "
            "update that constant to the real, installed location on this host"
        )
    runner = run or subprocess.run
    result = runner(
        [node_binary, str(TS_ENUMERATOR_SOURCE), str(library_dir), str(typescript_package_dir)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"ts_enumerator.js failed against {library_dir}:\n{result.stdout}\n{result.stderr}"
        )
    try:
        parsed = json.loads(result.stdout)
    except ValueError as exc:
        raise RuntimeError(f"ts_enumerator.js printed non-JSON stdout: {result.stdout!r}") from exc
    if not isinstance(parsed, list):
        raise RuntimeError(f"ts_enumerator.js printed a non-array JSON document: {result.stdout!r}")
    return parsed


def enumerate_typescript(
    repository: str, commit: str, library_dir: Path, *, run: Callable[..., Any] | None = None
) -> list[OracleSymbol]:
    """The real public API of a TypeScript library, read with the TypeScript Compiler API
    itself (``ts.createProgram`` plus its real ``TypeChecker`` -- never a regex, never this
    project's own tree-sitter grammar, which would make the oracle circular) through a small
    companion Node script (``scripts/release/oracle_helpers/ts_enumerator.js``), run directly
    by the already-trusted ``node`` host interpreter (no Docker -- see
    ``_run_typescript_enumerator``'s own docstring for why that is safe here, unlike
    ``enumerate_go``/``enumerate_rust``).

    Clones ``repository`` at ``commit`` into ``library_dir`` (unless it already looks checked
    out: a ``.git`` directory already present), runs the companion script against that
    directory, and maps its JSON output onto ``OracleSymbol``/``OracleMember``/``OracleParam``.
    Both a TypeScript ``class`` and an ``interface`` map to ``OracleSymbol.kind == "class"``
    (an interface's own members become "method"/"property" members using this module's
    existing class-like convention, matching how ``enumerate_rust`` already folds a Rust
    ``trait`` the same way). A class member's ``is_static`` is read from the Compiler API's own
    "static side" type, never guessed; a method parameter's ``has_default`` is ``True`` only
    when its own AST node carries a real initializer, never merely a ``?`` optional marker --
    see ``ts_enumerator.js``'s own "SCOPE" docstring for the one most important rule this
    enumerator applies (a class member with no visibility modifier is public by TypeScript's
    own default; only an explicit ``private`` excludes a member, and TypeScript interfaces
    cannot declare ``private`` members at all, so every interface member is always public) and
    for why this enumerator scopes to symbols reachable from the package's own declared entry
    point rather than merely every file's own ``export`` keyword.
    """
    if not (library_dir / ".git").is_dir():
        clone_pinned_commit(repository, commit, library_dir, run=run)

    raw_symbols = _run_typescript_enumerator(library_dir, run=run)
    symbols: list[OracleSymbol] = []
    for raw in raw_symbols:
        members = tuple(
            OracleMember(
                name=str(raw_member["name"]),
                kind=str(raw_member["kind"]),
                params=tuple(
                    OracleParam(name=str(p["name"]), has_default=bool(p.get("has_default", False)))
                    for p in raw_member.get("params") or []
                ),
                is_static=bool(raw_member.get("is_static", False)),
            )
            for raw_member in raw.get("members") or []
        )
        symbols.append(OracleSymbol(name=str(raw["name"]), kind="class", members=members))
    return symbols


JAVA_ENUMERATOR_SOURCE = _ORACLE_HELPERS_DIR / "JavaEnumerator.java"
JAVA_BINARY = "java"


def _run_java_enumerator(
    library_dir: Path, *, run: Callable[..., Any] | None = None, java_binary: str = JAVA_BINARY
) -> list[dict[str, Any]]:
    """Run ``JavaEnumerator.java`` (the JDK's own Compiler Tree API,
    ``scripts/release/oracle_helpers/JavaEnumerator.java``) and parse its JSON stdout.

    Unlike ``_run_go_enumerator``/``_run_rust_enumerator``, this never shells out to Docker.
    ``java`` running the JDK's own single-file source-launcher (``java
    JavaEnumerator.java <dir>``, supported since Java 11) compiles the companion program
    in-memory and runs it with the already-trusted ``java`` host interpreter -- never a
    freshly-built, unsigned native binary written to disk -- so this host's Windows
    Application Control policy (which blocks exactly that native-binary case; see the Go/Rust
    docstrings above) simply does not apply here. This was confirmed directly rather than
    assumed before this function was written (G3/TC-592), the same way TC-591 confirmed it for
    ``node``/TypeScript: every test in ``tests/release/test_source_differential.py`` that
    exercises this function (and the one real, live, network-using run against the pinned
    pdf/java pilot performed once at this card's own closing time) runs ``java`` directly
    against the host, with no container anywhere in the invocation below. The companion
    program's own Compiler Tree API usage (``com.sun.source.tree``/``com.sun.source.util``)
    needed no ``--add-exports`` flag either -- confirmed directly, see
    ``JavaEnumerator.java``'s own module docstring.
    """
    runner = run or subprocess.run
    result = runner(
        [java_binary, str(JAVA_ENUMERATOR_SOURCE), str(library_dir)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"JavaEnumerator.java failed against {library_dir}:\n{result.stdout}\n{result.stderr}"
        )
    try:
        parsed = json.loads(result.stdout)
    except ValueError as exc:
        raise RuntimeError(f"JavaEnumerator.java printed non-JSON stdout: {result.stdout!r}") from exc
    if not isinstance(parsed, list):
        raise RuntimeError(f"JavaEnumerator.java printed a non-array JSON document: {result.stdout!r}")
    return parsed


def enumerate_java(
    repository: str, commit: str, library_dir: Path, *, run: Callable[..., Any] | None = None
) -> list[OracleSymbol]:
    """The real public API of a Java library, read with the JDK's own Compiler Tree API at the
    PARSE phase only (``JavacTask.parse()`` -- never ``.analyze()``/``.generate()``, so a real
    library's own third-party dependencies, routinely absent from this function's classpath,
    are never needed) through a small companion program
    (``scripts/release/oracle_helpers/JavaEnumerator.java``), run directly by the already-
    trusted ``java`` host interpreter's own single-file source-launcher (no Docker -- see
    ``_run_java_enumerator``'s own docstring for why that is safe here, unlike
    ``enumerate_go``/``enumerate_rust``).

    Clones ``repository`` at ``commit`` into ``library_dir`` (unless it already looks checked
    out: a ``.git`` directory already present), runs the companion program against that
    directory, and maps its JSON output onto ``OracleSymbol``/``OracleMember``/``OracleParam``.
    Java has no default parameter values, so every ``OracleParam.has_default`` stays ``False``.
    Both a Java ``class`` and an ``interface`` map to ``OracleSymbol.kind == "class"`` (an
    interface's own members become "method"/"property" members using this module's existing
    class-like convention, matching how ``enumerate_rust``/``enumerate_typescript`` already fold
    a Rust ``trait``/TypeScript ``interface`` the same way); a Java ``enum`` maps to ``kind ==
    "enum"`` (its constants as "enum_value" members, each carrying its exact constructor
    argument literal(s) when the source gives any, never merely a count -- see
    ``JavaEnumerator.java``'s own "SCOPE" docstring for the one most important rule this
    enumerator applies: a ``protected`` or package-private class/enum member is NOT public and
    must be excluded, while an interface member is public unless explicitly ``private``). A
    public NESTED class/interface/enum (whose own enclosing chain is itself all public) is
    flattened into the same symbol list by its own simple name, exactly like a top-level one --
    unlike ``enumerate_go``/``enumerate_rust``, which are top-level-only; this was confirmed
    necessary, not merely assumed, against the real pinned pdf/java pilot (a top-level-only
    reading silently produced zero symbol for roughly a fifth of its own distinct public type
    names).
    """
    if not (library_dir / ".git").is_dir():
        clone_pinned_commit(repository, commit, library_dir, run=run)

    raw_symbols = _run_java_enumerator(library_dir, run=run)
    symbols: list[OracleSymbol] = []
    for raw in raw_symbols:
        members = tuple(
            OracleMember(
                name=str(raw_member["name"]),
                kind=str(raw_member["kind"]),
                params=tuple(OracleParam(name=str(p["name"])) for p in raw_member.get("params") or []),
                is_static=bool(raw_member.get("is_static", False)),
                enum_value=(
                    str(raw_member["enum_value"]) if raw_member.get("enum_value") is not None else None
                ),
            )
            for raw_member in raw.get("members") or []
        )
        symbols.append(OracleSymbol(name=str(raw["name"]), kind=str(raw["kind"]), members=members))
    return symbols


DOTNET_ENUMERATOR_PROJECT = _ORACLE_HELPERS_DIR / "dotnet_enumerator" / "DotNetEnumerator.csproj"
DOTNET_BINARY = "dotnet"


def _run_dotnet_enumerator(
    library_dir: Path,
    *,
    run: Callable[..., Any] | None = None,
    dotnet_binary: str = DOTNET_BINARY,
    project_path: Path = DOTNET_ENUMERATOR_PROJECT,
) -> list[dict[str, Any]]:
    """Run ``DotNetEnumerator`` (Roslyn's own ``CSharpSyntaxTree.ParseText``,
    ``scripts/release/oracle_helpers/dotnet_enumerator/Program.cs``) and parse its JSON stdout.

    Unlike ``_run_go_enumerator``/``_run_rust_enumerator``, this never shells out to Docker.
    ``dotnet run`` JIT-compiles and executes MANAGED IL through the already-trusted ``dotnet``
    host -- it never writes a freshly-built, unsigned NATIVE binary to disk (exactly the case
    this host's Windows Application Control policy blocks; see the Go/Rust docstrings above).
    This was confirmed directly rather than assumed before this function was written (G3/TC-593),
    the same way TC-591/TC-592 confirmed it for ``node``/``java``: every test in
    ``tests/release/test_source_differential.py`` that exercises this function (and the one real,
    live, network-using run against the pinned pdf/net pilot performed once at this card's own
    closing time) runs ``dotnet`` directly against the host, with no container anywhere in the
    invocation below.

    ``--no-restore`` is deliberately NOT passed. ``dotnet run``/``dotnet build`` always perform
    an implicit restore first, and that is exactly what this function relies on: unlike Rust's
    crate registry, NuGet's restore writes into a machine-wide, version-keyed package cache
    (``~/.nuget/packages``) that persists across every checkout. Once the companion project's
    ``Microsoft.CodeAnalysis.CSharp`` dependency has been restored once (with network, exactly
    like ``cargo vendor``'s one-time network use before ``enumerate_rust``'s own
    ``cargo run --offline`` -- see ``_run_rust_enumerator``'s own docstring), every subsequent
    implicit restore -- real or test, in this worktree or a brand-new one -- resolves entirely
    from that already-warm global cache, with no further network call of any kind and no
    dependency on a project-local, gitignored ``obj/project.assets.json`` existing already.
    ``--no-restore`` was tried first (G3/TC-593) and skips even that implicit, cache-only
    restore -- it instead requires ``obj/project.assets.json`` to already exist on disk, which
    it only does in a worktree where ``dotnet restore``/``build``/``run`` has personally been
    run before; a genuinely fresh worktree (every ``gatectl review`` throwaway worktree among
    them) has no such file and fails outright, even with a warm global cache. Omitting the flag
    costs a few seconds per invocation (a cache lookup, not a network round trip) in exchange
    for working correctly in any worktree, which is the whole point.
    """
    runner = run or subprocess.run
    result = runner(
        [
            dotnet_binary,
            "run",
            "--project",
            str(project_path),
            "-c",
            "Release",
            "--",
            str(library_dir),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"DotNetEnumerator failed against {library_dir}:\n{result.stdout}\n{result.stderr}"
        )
    try:
        parsed = json.loads(result.stdout)
    except ValueError as exc:
        raise RuntimeError(f"DotNetEnumerator printed non-JSON stdout: {result.stdout!r}") from exc
    if not isinstance(parsed, list):
        raise RuntimeError(f"DotNetEnumerator printed a non-array JSON document: {result.stdout!r}")
    return parsed


def enumerate_dotnet(
    repository: str, commit: str, library_dir: Path, *, run: Callable[..., Any] | None = None
) -> list[OracleSymbol]:
    """The real public API of a .NET/C# library, read with Roslyn's own parser
    (``Microsoft.CodeAnalysis.CSharp``, ``CSharpSyntaxTree.ParseText`` -- SYNTAX/PARSE phase only,
    never a ``SemanticModel``/``Compilation``, never symbol resolution against the library's own
    external references) through a small companion console project
    (``scripts/release/oracle_helpers/dotnet_enumerator/``), run directly by the already-trusted
    ``dotnet`` host (no Docker -- see ``_run_dotnet_enumerator``'s own docstring for why that is
    safe here, unlike ``enumerate_go``/``enumerate_rust``).

    Clones ``repository`` at ``commit`` into ``library_dir`` (unless it already looks checked out:
    a ``.git`` directory already present), runs the companion program against that directory, and
    maps its JSON output onto ``OracleSymbol``/``OracleMember``/``OracleParam``. A C# ``class``,
    ``struct``, ``record`` and ``interface`` all map to ``OracleSymbol.kind == "class"`` (an
    interface's own members become "method"/"property" members using this module's existing
    class-like convention, matching how ``enumerate_rust``/``enumerate_typescript``/
    ``enumerate_java`` already fold a Rust ``trait``/TypeScript ``interface``/Java ``interface``
    the same way); a C# ``enum`` maps to ``kind == "enum"`` (its members as "enum_value" members,
    carrying an explicit numeric literal when the source gives one, never merely a count -- see
    ``Program.cs``'s own "SCOPE" docstring for the one most important rule this enumerator
    applies: a class/struct/record member with no explicit access modifier is implicitly PRIVATE,
    while an interface member with no explicit modifier is implicitly PUBLIC -- the exact opposite
    default, and the card's own named defect case). A parameter's ``has_default`` comes straight
    from the syntax tree's own ``EqualsValueClause``, never a nullable ``?`` marker.

    A public NESTED class/interface/struct/record/enum (whose own enclosing chain is itself all
    public) is flattened into the same symbol list by its own simple name, exactly like
    ``enumerate_java``'s own nested-type handling (public static nested types are a routine
    pattern in these wrapper APIs too). Unlike every other language this module enumerates, C#
    method/property names are routinely NOT unique within one type (overloading is pervasive in
    these Aspose wrapper APIs); ``Program.cs``'s own "SCOPE" docstring records the deterministic,
    first-declaration-wins rule this enumerator applies for both a genuine overload set and a
    ``partial`` type split across several files -- a pre-existing limitation of this whole oracle
    system's own bare-name keying (``diff_symbol``/``diff_language`` key the baked fixture's own
    ``methods``/``properties`` by bare name too), not something this card introduces or must fix.
    """
    if not (library_dir / ".git").is_dir():
        clone_pinned_commit(repository, commit, library_dir, run=run)

    raw_symbols = _run_dotnet_enumerator(library_dir, run=run)
    symbols: list[OracleSymbol] = []
    for raw in raw_symbols:
        members = tuple(
            OracleMember(
                name=str(raw_member["name"]),
                kind=str(raw_member["kind"]),
                params=tuple(
                    OracleParam(name=str(p["name"]), has_default=bool(p.get("has_default", False)))
                    for p in raw_member.get("params") or []
                ),
                is_static=bool(raw_member.get("is_static", False)),
                enum_value=(
                    str(raw_member["enum_value"]) if raw_member.get("enum_value") is not None else None
                ),
            )
            for raw_member in raw.get("members") or []
        )
        symbols.append(OracleSymbol(name=str(raw["name"]), kind=str(raw["kind"]), members=members))
    return symbols


CPP_CLANG_BINARY = Path(
    r"C:\dev-tools\foss-mcp\rp-toolchains\llvm-19.1.7\clang+llvm-19.1.7-x86_64-pc-windows-msvc\bin\clang.exe"
)
# TC-594: this host's own already-installed LLVM 19.1.7 toolchain, confirmed directly (searched
# C:\dev-tools and PATH) rather than assumed from either the Round-3 audit's design table ("None
# (already in toolchain)") or this card's own dispatch premise ("no system clang/LLVM install exists
# anywhere under C:\dev-tools") -- BOTH were wrong for this host: clang.exe/libclang.dll have sat
# under this exact path since September 2026, well before this card was ever authored. Separately,
# and regardless of that finding, `import clang.cindex` (the `libclang` PyPI binding this card's own
# dispatch instructions say the supervisor pinned in requirements.in/requirements.lock before
# dispatch) fails in this worktree's venv -- the pin is present in requirements.lock
# (`libclang==18.1.1`) but the package is not actually installed, and this card's own contract
# forbids trying to fix that ("if `import clang.cindex` fails ... stop and report rather than trying
# to fix the dependency yourself"). The card's own fallback clause covers exactly this situation
# ("if a later toolchain update did add a real clang.exe, a bare `clang -Xclang -ast-dump=json
# -fsyntax-only` invocation ... is simpler and preferred -- use whichever is actually available"), so
# this module uses the real, already-installed ``clang.exe`` directly via subprocess, never the
# unavailable Python binding. Pinned the same way TYPESCRIPT_NPM_PACKAGE_DIR/GO_DOCKER_IMAGE/
# RUST_DOCKER_IMAGE pin this host's own installed tools: a visible, diagnosable error if a future
# host ever moves it, never a silent wrong answer.
CPP_STD = "-std=c++20"  # the real pdf/cpp pilot's own public headers use std::span (C++20);
# confirmed directly against the real pinned commit before picking this flag, not assumed.
_CPP_PRIVATE_DIR_NAMES = frozenset({"internal", "detail", "details", "impl", "private", "src"})
_CPP_HIDDEN_NAMESPACE_NAMES = frozenset({"internal", "detail"})
# CXXConstructorDecl/CXXDestructorDecl/CXXConversionDecl are Clang's own distinct AST node kinds for
# a constructor, destructor and conversion operator (each derives from CXXMethodDecl in Clang's own
# class hierarchy, but the -ast-dump=json "kind" string names the concrete subclass, never the base).
_CPP_METHOD_KINDS = frozenset(
    {"CXXMethodDecl", "CXXConstructorDecl", "CXXDestructorDecl", "CXXConversionDecl"}
)


def _cpp_is_public_header(path: Path, include_root: Path) -> bool:
    """True when *path* is a ``.h``/``.hpp`` file outside every ``internal``/``detail``/``details``/
    ``impl``/``private``/``src`` directory (case-insensitive, leading underscores ignored --
    ``_internal`` is an internal directory too) below *include_root*, and whose own file name does
    not itself start with ``internal``. Re-derived independently here from the SAME public/private
    rule TC-534 already established for this project's own tree-sitter C++ extractor
    (``api_surface.py``'s ``_cpp_is_public_header``) -- never imported from it, so this stays a
    genuinely independent oracle even though a correct fixture should agree with it.
    """
    if path.suffix.lower() not in (".h", ".hpp"):
        return False
    try:
        parts = path.relative_to(include_root).parts
    except ValueError:
        parts = (path.name,)
    for part in parts[:-1]:
        low = part.lower().lstrip("_")
        if low in _CPP_PRIVATE_DIR_NAMES or low.startswith("internal"):
            return False
    return not parts[-1].lower().startswith("internal")


def _cpp_namespace_is_hidden(name: str | None) -> bool:
    """True when a C++ namespace named *name* (``None`` for an anonymous namespace) holds
    implementation detail, never public API: TC-534's own rule (an anonymous namespace, or one
    named -- any ``::``-separated segment, case-insensitive, leading underscores ignored --
    ``internal`` or ``detail``) re-derived independently here, never imported from
    ``api_surface.py``.
    """
    if name is None:
        return True
    return any(seg.strip().lstrip("_").lower() in _CPP_HIDDEN_NAMESPACE_NAMES for seg in name.split("::"))


def _cpp_public_include_dir(library_dir: Path) -> Path:
    """The library's own public include root: the directory literally named ``include`` directly
    under *library_dir* when one exists (the layout every ``*-FOSS-for-Cpp`` repository this
    project ingests already uses), else *library_dir* itself.
    """
    candidate = library_dir / "include"
    return candidate if candidate.is_dir() else library_dir


def _cpp_public_headers(include_root: Path) -> list[Path]:
    return sorted(
        path
        for path in include_root.rglob("*")
        if path.is_file() and _cpp_is_public_header(path, include_root)
    )


def _cpp_resolve_node_files(root: Mapping[str, Any], main_file: str) -> dict[int, str]:
    """``{id(node): the source file that node is actually declared in}`` for every node under
    *root* (a ``TranslationUnitDecl``), found by walking the WHOLE tree, unconditionally, in exact
    document order.

    Clang's own ``-ast-dump=json`` delta-encodes each declaration's own file: a node's ``"loc"``
    carries an explicit ``"file"`` only when it differs from the one immediately before it (in
    document order) -- omitted, a declaration is in the SAME file as the one before it, starting
    from *main_file* itself (the header actually passed on the command line) before any transition
    is ever seen. Confirmed directly against a real two-file case (a header that ``#include``s
    ``<string>``): the header's OWN declarations that come after the ``#include`` carry an explicit
    ``"file"`` again, re-asserting *main_file*, once control returns from the included header's own
    subtree. Without this, every declaration pulled in transitively from an included system/CRT
    header (``__crt_locale_data_public`` and thousands like it, confirmed directly against the real
    pdf/cpp pilot before this function was written) would be walked exactly like one the library's
    own public header actually declares, wildly inflating "missing" -- this independent oracle must
    read only what the header being enumerated ACTUALLY declares, nothing an unrelated, transitively
    included header happens to declare too.
    """
    resolved: dict[int, str] = {}
    current: list[str] = [main_file]

    def walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        loc = node.get("loc")
        if isinstance(loc, dict) and "file" in loc:
            current[0] = str(loc["file"])
        resolved[id(node)] = current[0]
        for child in node.get("inner") or []:
            walk(child)

    walk(root)
    return resolved


def _cpp_explicit_enum_value(node: Mapping[str, Any]) -> str | None:
    """The explicit literal value of one ``EnumConstantDecl`` *node* (its own ``ConstantExpr``
    child's folded ``"value"``), or ``None`` when the source gave no initializer -- an implicit,
    "not checked" value, matching ``enumerate_rust``/``enumerate_java``/``enumerate_dotnet``'s own
    convention of never reporting a mere position count as a real discriminant.
    """
    for child in node.get("inner") or []:
        if child.get("kind") == "ConstantExpr" and "value" in child:
            return str(child["value"])
    return None


def _cpp_enum_members(node: Mapping[str, Any]) -> tuple[OracleMember, ...]:
    members: list[OracleMember] = []
    for child in node.get("inner") or []:
        if child.get("kind") != "EnumConstantDecl":
            continue
        name = str(child.get("name") or "")
        if not name:
            continue
        members.append(OracleMember(name=name, kind="enum_value", enum_value=_cpp_explicit_enum_value(child)))
    return tuple(members)


def _cpp_method_member(node: Mapping[str, Any]) -> OracleMember | None:
    name = str(node.get("name") or "")
    if not name:
        return None
    params = tuple(
        OracleParam(name=str(p.get("name") or ""), has_default="init" in p)
        for p in node.get("inner") or []
        if p.get("kind") == "ParmVarDecl"
    )
    return OracleMember(
        name=name, kind="method", params=params, is_static=node.get("storageClass") == "static"
    )


def _cpp_extract_record(
    node: Mapping[str, Any], qualified_name: str
) -> tuple[OracleSymbol, list[OracleSymbol]]:
    """The ``OracleSymbol`` for one ``CXXRecordDecl`` *node* (already confirmed public and complete
    by the caller), named *qualified_name* (its own simple name, or ``Outer::Inner`` for a nested
    type -- TC-534's own nested-type naming convention, re-derived independently here from Clang's
    AST: a public nested class/struct/enum is simply a normal child of its own enclosing record's
    body, walked the same way any other public member is), plus every public nested class/struct/
    enum found inside it (recursively, each named ``Outer::Inner``/``Outer::Inner::Innermost`` off
    of *qualified_name*, never a separate top-level symbol).

    Walks the record body tracking the current ``AccessSpecDecl`` (a ``class`` starts private, a
    ``struct``/``union`` starts public, matching TC-534's own rule) -- Clang's own ``-ast-dump=json``
    never annotates a member's effective access on the member itself, only on the
    ``AccessSpecDecl`` that introduces each section, exactly like a tree-sitter parse of the same
    source would see. A member with ``"isImplicit": true`` (a compiler-synthesized default/copy/
    move constructor, destructor or assignment operator never written in the source) is skipped,
    never fabricated as a "real" public member.

    C++ method overloading is pervasive in these Aspose wrapper APIs (a getter/setter pair sharing
    one name -- ``bool Margin() const;``/``void Margin(bool value);`` -- is a routine pattern, not
    an edge case): ``diff_symbol``/``diff_language`` key the baked fixture's own ``methods`` by
    bare name too (a pre-existing limit of this whole oracle system, not something this card
    introduces), so an overload set must collapse to exactly one member, deterministically (the
    first declaration encountered, in source order, wins), never one per overload -- confirmed
    necessary, not merely assumed, against the real pdf/cpp pilot, and matching
    ``enumerate_dotnet``'s own identical rule for the same reason (G3/TC-593).
    """
    tag_used = node.get("tagUsed")
    access = "public" if tag_used in ("struct", "union") else "private"
    members: list[OracleMember] = []
    nested: list[OracleSymbol] = []
    seen_method_names: set[str] = set()
    for child in node.get("inner") or []:
        kind = child.get("kind")
        if kind == "AccessSpecDecl":
            access = str(child.get("access") or access)
            continue
        if access != "public" or child.get("isImplicit"):
            continue
        if kind in _CPP_METHOD_KINDS:
            member = _cpp_method_member(child)
            if member is not None and member.name not in seen_method_names:
                seen_method_names.add(member.name)
                members.append(member)
        elif kind == "FieldDecl":
            name = str(child.get("name") or "")
            if name:
                members.append(OracleMember(name=name, kind="property", is_static=False))
        elif kind == "VarDecl":
            # TC-534's own rule, re-derived here: a public static data member (a `static const`/
            # `static constexpr` class member is a VarDecl, never a FieldDecl, in Clang's own AST)
            # keeps its static-ness -- never silently folded into an ordinary instance field.
            name = str(child.get("name") or "")
            if name:
                members.append(
                    OracleMember(name=name, kind="property", is_static=child.get("storageClass") == "static")
                )
        elif kind == "CXXRecordDecl" and child.get("completeDefinition"):
            inner_name = str(child.get("name") or "")
            if not inner_name:
                continue
            full_name = f"{qualified_name}::{inner_name}"
            nested_symbol, deeper = _cpp_extract_record(child, full_name)
            nested.append(nested_symbol)
            nested.extend(deeper)
        elif kind == "EnumDecl" and child.get("inner"):  # an EnumDecl with no "inner" is a forward
            # declaration (`enum State : int;`), never a definition -- skipped so it never produces
            # a phantom duplicate, zero-member symbol alongside the real, later definition.
            inner_name = str(child.get("name") or "")
            if not inner_name:
                continue
            full_name = f"{qualified_name}::{inner_name}"
            nested.append(OracleSymbol(name=full_name, kind="enum", members=_cpp_enum_members(child)))
        # Everything else (a FunctionTemplateDecl/ClassTemplateDecl, UsingDecl, FriendDecl, an
        # IndirectFieldDecl of an anonymous union, ...) is not a class/struct/enum member this
        # oracle enumerates -- skipped, never fabricated.
    return OracleSymbol(name=qualified_name, kind="class", members=tuple(members)), nested


def _cpp_walk_container(
    nodes: Sequence[Mapping[str, Any]], *, file_map: Mapping[int, str], main_file: str
) -> list[OracleSymbol]:
    """Every public, top-level-or-namespace-nested (never class-nested -- that is
    ``_cpp_extract_record``'s own job) class/struct/enum found directly inside *nodes* (a
    ``TranslationUnitDecl``'s or ``NamespaceDecl``'s own ``"inner"`` list), skipping a namespace
    that ``_cpp_namespace_is_hidden`` (TC-534's own implementation-namespace rule, re-derived
    independently here from Clang's AST), skipping any top-level declaration that ``file_map``
    (``_cpp_resolve_node_files``) says is NOT actually declared in *main_file* itself (a type
    pulled in transitively from an included system/CRT/other-library header), and descending into
    an ``extern "C" { ... }`` linkage block transparently (it carries no visibility, and no file
    boundary, of its own).
    """
    found: list[OracleSymbol] = []
    for node in nodes:
        kind = node.get("kind")
        if kind == "NamespaceDecl":
            if _cpp_namespace_is_hidden(node.get("name")):
                continue
            found.extend(_cpp_walk_container(node.get("inner") or [], file_map=file_map, main_file=main_file))
        elif kind == "LinkageSpecDecl":
            found.extend(_cpp_walk_container(node.get("inner") or [], file_map=file_map, main_file=main_file))
        elif kind == "CXXRecordDecl":
            if node.get("isImplicit") or not node.get("completeDefinition"):
                continue
            if file_map.get(id(node)) != main_file:
                continue  # declared in a transitively-included header, never this one
            name = str(node.get("name") or "")
            if not name:
                continue
            symbol, nested = _cpp_extract_record(node, name)
            found.append(symbol)
            found.extend(nested)
        elif kind == "EnumDecl" and node.get("inner"):
            if file_map.get(id(node)) != main_file:
                continue
            name = str(node.get("name") or "")
            if not name:
                continue
            found.append(OracleSymbol(name=name, kind="enum", members=_cpp_enum_members(node)))
    return found


def _run_clang_ast_dump(
    header: Path,
    include_root: Path,
    *,
    run: Callable[..., Any] | None = None,
    clang_binary: Path = CPP_CLANG_BINARY,
) -> dict[str, Any]:
    """Run clang's own ``-Xclang -ast-dump=json -fsyntax-only`` (PARSE-and-SEMA phase only, never a
    link, never a compiled object) against *header* in isolation and return its parsed JSON AST.

    Raises ``RuntimeError``, naming *header* itself in the message, on a nonzero clang exit -- a
    missing ``#include``, a macro-heavy header clang cannot parse standalone outside its library's
    own build system, or any other real resolution failure. ``enumerate_cpp`` catches this per
    header (never per pilot) so one unresolvable header never silently empties the whole result;
    see its own docstring for the per-pilot disclosure this card requires.
    """
    if not clang_binary.is_file():
        raise RuntimeError(
            f"clang.exe was not found at {clang_binary} -- this path is pinned in "
            "source_differential.py (CPP_CLANG_BINARY); if the toolchain moved, update that "
            "constant to the real, installed location on this host"
        )
    runner = run or subprocess.run
    result = runner(
        [
            str(clang_binary),
            "-Xclang",
            "-ast-dump=json",
            "-fsyntax-only",
            CPP_STD,
            "-x",
            "c++",
            f"-I{include_root}",
            str(header),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        # Never include result.stdout here: on a resolution failure clang still dumps whatever
        # partial AST it built before hitting the fatal error, which is noise, not a reason --
        # result.stderr alone carries the real diagnostic ("fatal error: 'x.h' file not found").
        raise RuntimeError(f"clang failed to parse {header}:\n{result.stderr}")
    try:
        parsed = json.loads(result.stdout)
    except ValueError as exc:
        raise RuntimeError(f"clang printed non-JSON stdout for {header}: {result.stdout!r}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"clang printed a non-object JSON document for {header}: {result.stdout!r}")
    return parsed


def enumerate_cpp(
    repository: str, commit: str, library_dir: Path, *, run: Callable[..., Any] | None = None
) -> list[OracleSymbol]:
    """The real public API of a C++ library, read with Clang's own AST (``-Xclang
    -ast-dump=json -fsyntax-only``, the PARSE-and-SEMA phase only -- never a link, never generated
    code) run directly against the already-installed, real ``clang.exe`` on this host (see
    ``CPP_CLANG_BINARY``'s own module-level comment for why this is a bare subprocess rather than
    the ``libclang`` pip binding this card's own dispatch instructions named: that binding's
    ``import clang.cindex`` fails in this worktree, and a real system clang turned out to already
    exist here -- both confirmed directly, never assumed, before this function was written).

    Clones ``repository`` at ``commit`` into ``library_dir`` (unless it already looks checked out:
    a ``.git`` directory already present), finds its public include root (``_cpp_public_include_dir``
    -- the ``include/`` directory directly under the library when one exists, else the library root
    itself), parses every public header under it (``_cpp_public_headers`` -- ``_cpp_is_public_header``
    re-derives TC-534's own public/private directory-naming rule independently from Clang's AST,
    never by importing ``api_surface.py``) ONE HEADER AT A TIME, in isolation -- each header must
    resolve its own ``#include``s on its own; a header that cannot (a missing include, a macro-heavy
    header no standalone parse can expand) raises inside ``_run_clang_ast_dump``, is caught here, and
    is recorded in the raised ``RuntimeError``'s own message naming every such header by path, never
    silently folded into an empty result for the whole pilot -- the caller (this card's own one real,
    live run, and ``main``'s per-pilot reporting) must disclose that message, not swallow it.

    Every resolved header's own public class/struct/enum and their own public methods/fields/enum
    values are collected by ``_cpp_walk_container``/``_cpp_extract_record``, applying the SAME
    public/private, hidden-namespace and nested-type-naming rules TC-534 already established and
    tested for this project's own tree-sitter C++ extractor (re-derived independently here from
    Clang's own AST, never by calling that extractor): a type under an ``internal``/``detail``
    namespace (or an anonymous one) is excluded entirely; a ``class`` starts private and a
    ``struct``/``union`` starts public, re-evaluated per ``AccessSpecDecl`` encountered in source
    order; a public nested type is named ``Outer::Inner`` (``Outer::Inner::Innermost`` for a type
    nested two levels deep), never given its own top-level symbol; a public ``static const``/
    ``static constexpr`` data member is read as a property with ``is_static=True`` (a plain ``const``
    instance member stays ``is_static=False``), never silently folded into an ordinary instance
    field. A method's ``return_type`` is left ``None`` ("not checked"), matching
    ``enumerate_go``/``enumerate_rust``/``enumerate_java``/``enumerate_dotnet``'s own convention;
    a parameter's ``has_default`` is read from Clang's own ``"init"`` marker on its ``ParmVarDecl``,
    never guessed from the type text. Both a C++ ``class``, ``struct`` and ``union`` map to
    ``OracleSymbol.kind == "class"``, matching this module's existing class-like convention for
    every other language's own record-like declaration. C++ method overloading (a getter/setter
    pair sharing one name) is pervasive in these Aspose wrapper APIs; ``_cpp_extract_record``'s own
    docstring records the deterministic, first-declaration-wins collapse rule this enumerator
    applies, matching ``enumerate_dotnet``'s own identical rule for the same pre-existing bare-name
    keying limit (G3/TC-593).

    Crucially, only a top-level declaration ``_cpp_resolve_node_files`` says is ACTUALLY declared
    in the header being enumerated is ever turned into a symbol -- confirmed necessary, not merely
    assumed, against the real pdf/cpp pilot: a header's own ``#include`` pulls in the whole
    transitive closure of the C++ standard library AND the MSVC CRT's own internal types
    (``__crt_locale_data_public`` and thousands like it), and Clang's single JSON AST dump for one
    header mixes every one of those declarations in with the header's own, with no other signal
    telling them apart.
    """
    if not (library_dir / ".git").is_dir():
        clone_pinned_commit(repository, commit, library_dir, run=run)

    include_root = _cpp_public_include_dir(library_dir)
    symbols: list[OracleSymbol] = []
    failures: list[str] = []
    for header in _cpp_public_headers(include_root):
        try:
            ast = _run_clang_ast_dump(header, include_root, run=run)
        except RuntimeError as exc:
            failures.append(str(exc))
            print(f"enumerate_cpp: header did not resolve, skipped: {exc}", file=sys.stderr)
            continue
        main_file = str(header)
        file_map = _cpp_resolve_node_files(ast, main_file)
        symbols.extend(_cpp_walk_container(ast.get("inner") or [], file_map=file_map, main_file=main_file))
    if failures:
        print(
            f"enumerate_cpp: {len(failures)} of "
            f"{len(_cpp_public_headers(include_root))} public header(s) under {include_root} did not "
            "resolve; see the warnings above for each one by name -- this run still used every "
            "header that DID resolve, never an empty result",
            file=sys.stderr,
        )
    return symbols


def _unimplemented_enumerator(platform: str) -> Enumerator:
    def _enumerator(repository: str, commit: str, library_dir: Path) -> list[OracleSymbol]:
        raise NotImplementedError(
            f"no real {platform} enumerator is wired yet (TC-566 only wires Python's ast-based reader); "
            f"supply one via Deps.enumerators[{platform!r}] before verifying a {platform} pilot"
        )

    return _enumerator


DEFAULT_ENUMERATORS: dict[str, Enumerator] = {
    "python": enumerate_python,
    "go": enumerate_go,
    "rust": enumerate_rust,
    "typescript": enumerate_typescript,
    "java": enumerate_java,
    # Both keys are registered to the same real enumerator -- this project's own platform naming
    # uses both spellings for the identical .NET/C# ecosystem (confirmed directly, not assumed:
    # the pdf/net pilot's own deployment-identity "platform" field -- this module's main() own
    # lookup key via chart.pilot_key/entry["platform"] -- is spelled "net", while that exact same
    # pilot's own library/source-of-truth block spells it "dotnet"; run_extraction.py's own
    # _LANGUAGE_BY_PLATFORM dict and package_root.detect_package_root already treat "net"/"dotnet"
    # as the identical case for exactly this reason -- see run_extraction.py's own comment above
    # that dict).
    "net": enumerate_dotnet,
    "dotnet": enumerate_dotnet,
    "cpp": enumerate_cpp,
    **{platform: _unimplemented_enumerator(platform) for platform in ("javascript", "nodejs")},
}


# ---------------------------------------------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------------------------------------------


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Diff every pilot's baked fixture against an independent, per-language oracle.",
    )
    parser.add_argument(
        "--fixtures", type=Path, default=DEFAULT_FIXTURES, help="fixtures root (default tests/fixtures)"
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="FAMILY_PLATFORM",
        help="verify only this pilot (repeatable)",
    )
    parser.add_argument(
        "--ceilings-file", type=Path, default=None, help="a JSON override of LANGUAGE_ERROR_CEILINGS"
    )
    parser.add_argument(
        "--library-root", type=Path, default=DEFAULT_LIBRARY_ROOT, help="where each pilot's library is cloned"
    )
    parser.add_argument(
        "--report-dir", type=Path, default=DEFAULT_REPORT_DIR, help=f"where {REPORT_NAME} is written"
    )
    parser.add_argument(
        "--no-require-extractor-version",
        dest="require_extractor_version",
        action="store_false",
        default=True,
        help="skip the extractor_version gate (never use this for a real release decision)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, deps: Deps | None = None) -> int:
    deps = deps or Deps(enumerators=DEFAULT_ENUMERATORS)
    args = parse_args(argv)
    try:
        ceilings = load_ceilings(args.ceilings_file)
        entries = chart.select_pilots(chart.load_pilots(), args.only)
    except (Refused, ValueError, OSError) as exc:
        print(f"source_differential: {exc}", file=sys.stderr)
        return EXIT_REFUSED

    reports: list[PilotReport] = []
    for entry in entries:
        key = chart.pilot_key(entry)
        platform = str(entry["platform"])
        fixture_path = args.fixtures / key / "api_surface.json"
        try:
            fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            reports.append(
                PilotReport(
                    pilot=key,
                    language=platform,
                    extractor_version_reason=None,
                    counts=DiffCounts().as_dict(),
                    reasons=[f"could not read {fixture_path}: {type(exc).__name__}: {exc}"],
                    findings=[],
                )
            )
            continue

        enumerator = deps.enumerators.get(platform)
        if enumerator is None:
            reports.append(
                PilotReport(
                    pilot=key,
                    language=platform,
                    extractor_version_reason=None,
                    counts=DiffCounts().as_dict(),
                    reasons=[f"no enumerator registered for platform {platform!r}"],
                    findings=[],
                )
            )
            continue

        library = entry.get("library") if isinstance(entry.get("library"), dict) else {}
        repository = str(library.get("repository", ""))
        commit = str(library.get("commit", ""))
        library_dir = args.library_root / key
        try:
            symbols = enumerator(repository, commit, library_dir)
        except NotImplementedError as exc:
            reports.append(
                PilotReport(
                    pilot=key,
                    language=platform,
                    extractor_version_reason=None,
                    counts=DiffCounts().as_dict(),
                    reasons=[str(exc)],
                    findings=[],
                )
            )
            continue

        reports.append(
            verify_pilot(
                pilot=key,
                language=platform,
                fixture=fixture,
                symbols=symbols,
                ceilings=ceilings,
                require_extractor_version=args.require_extractor_version,
                current_extractor_version=run_extraction.EXTRACTOR_VERSION,
            )
        )

    args.report_dir.mkdir(parents=True, exist_ok=True)
    document = {
        "schema_version": SCHEMA_VERSION,
        "verdict": "PASS" if reports and all(report.passed for report in reports) else "FAIL",
        "pilots": [report.as_dict() for report in reports],
    }
    (args.report_dir / REPORT_NAME).write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    for report in reports:
        status = "pass" if report.passed else "fail"
        print(f"source_differential: {report.pilot} ({report.language}): {status}")
        if report.extractor_version_reason:
            print(f"    reason: {report.extractor_version_reason}")
        for reason in report.reasons:
            print(f"    reason: {reason}")
    print(
        f"verdict: {document['verdict']} ({sum(1 for r in reports if r.passed)} of {len(reports)} pilots passed)"
    )
    return 0 if document["verdict"] == "PASS" else EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
