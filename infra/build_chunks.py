"""Bridge TC-062's real chunk builder to infra/ingest.py's ``--chunks`` flag.

``foss_mcp.indexing.chunk_builder.build_chunks_from_api_surface`` (TC-062) returns real
``Chunk`` dataclass objects, not JSON - before this script existed, nothing in the repo ever
called it outside of tests (the 2026-09-25 audit's finding: a real production ingestion run had
no CLI step that could turn a raw ``api_surface.json`` extraction fixture into a
``--chunks PATH`` file ``infra/ingest.py``'s own ``_load_chunks()`` can read). This script is
that bridge: load ``--api-surface``, call ``build_chunks_from_api_surface`` with the exact
prescribed call shape, serialize the result to ``{"chunks": [...]}`` via ``dataclasses.asdict``
(never hand-duplicated field names), write it to ``--out``.

Each chunk's ``text`` also gets one appended ``Source-Commit: <sha>`` line here. This is this
project's own already-established convention for recovering a generation's indexed source
commit from chunk text (``foss_mcp.mcp.tools.get_symbol``'s ``FQN:`` line is the same pattern;
``foss_mcp.mcp.tools.report_index_freshness``'s own docstring names this exact convention,
because the lexical index persists each chunk's text but not its ``Provenance``).
``build_chunks_from_api_surface`` itself does not add this line - it is shared, generic
chunk-building logic reused verbatim by several pilots' inline builders, none of which add it
either - so it is added here, once, at the ingestion-adapter boundary this script owns, using
each chunk's own ``provenance.commit`` (already set by ``build_chunks_from_api_surface`` from
the fixture's ``source_commit``) so it can never drift from what the chunk was actually built
from.

REQ-G2-048 (TC-068) adds a second, optional path alongside the type-based one above: when
``--furnished-page`` (plus the ``--library-*`` coordinates) is given, candidate examples
are extracted from a furnished-content page (``foss_mcp.indexing.example_candidates``), each is
compiled for real in an isolated sandbox against the pinned reference library
(``foss_mcp.indexing.example_verifier``), and only a candidate that actually compiles becomes a
citable example chunk. An unverified candidate is silently excluded - a candidate failing real
compilation is the pipeline working correctly, not a defect. This whole path is skipped, and
output is byte-for-byte identical to before, when ``--furnished-page`` is omitted.

REQ-G2-048 (TC-091) generalizes that path with a new required ``--library-platform`` flag
(dotnet/python/rust/go/java/typescript) that dispatches to the matching real
``prepare_<lang>_library``/``verify_<lang>_example`` pair from
``foss_mcp.indexing.example_verifier`` (TC-081/082), via the ``_PLATFORM_DISPATCH`` table below.
``--library-platform dotnet`` (plus the pre-existing ``--library-csproj``) reproduces the
original pdf/net-only behavior byte-for-byte; the other five platforms were previously
unreachable from this CLI even though their prepare/verify functions already existed.

REQ-G3-007 (TC-458) widens that path's candidate supply (every same-language fence of the example
blocks and the README content blocks, de-duplicated) and makes it publish exactly the program the
toolchain compiled: ``_published_program`` names what the harness added in a ``Harness:`` line, an
import-only block is never an example on cpp, java and nodejs, and the verification cache is keyed on
the published program.

REQ-G2-047 (TC-112) adds a THIRD, additive path alongside the two above: real documentation
chunks built from ``--furnished-page``'s own ``overview``/``content``/``faq`` front-matter
sections (``foss_mcp.indexing.doc_candidates.extract_doc_sections``, TC-110). Unlike the
example path, no compile-verification happens here - prose is never compiled - so every
``DocCandidate`` the extractor returns becomes a chunk, no sandbox, no verified/unverified
split. This closes REQ-G2-047's confirmed gap: search_docs has never had any real content,
for any pilot, since this project's own inception.
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import json
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml

from foss_mcp.indexing.authored_examples import (
    AUTHORED_LABEL,
    SIDECAR_NAME,
    AuthoredExamplesError,
    has_builtin_authored_examples,
    load_authored_examples,
    load_builtin_authored_examples,
)
from foss_mcp.indexing.chunk_builder import build_chunks_from_api_surface, parse_failures_over_threshold
from foss_mcp.indexing.doc_candidates import extract_doc_sections
from foss_mcp.indexing.doc_chunking import chunk_doc_candidate
from foss_mcp.indexing.example_candidates import (
    CHAIN_PREFIX_PLATFORMS,
    IMPORT_ONLY_GUARD_PLATFORMS,
    CandidateExample,
    effective_candidate,
    extract_candidate_examples,
    extract_candidate_supply,
    import_like_lines,
    merge_authored_candidates,
)
from foss_mcp.indexing.example_candidates import normalised_code_sha256 as _candidate_code_sha256
from foss_mcp.indexing.example_fixtures import input_literals
from foss_mcp.indexing.example_verifier import (
    CPP_COMPAT_NAMES,
    RUNTIME_FAILED,
    RUNTIME_NO_OUTPUT,
    RUNTIME_RAN_OK,
    ExampleEnvironmentError,
    RuntimeRecord,
    _assemble_dotnet_source,
    _assemble_rust_source,
    _wrap_cpp_fragment_if_needed,
    counts_as_verified,
    cpp_compat_compile_options,
    entry_counts_as_verified,
    prepare_cpp_library_linux,
    prepare_go_library,
    prepare_java_library,
    prepare_nodejs_library,
    prepare_python_library,
    prepare_reference_library,
    prepare_rust_library,
    prepare_typescript_library,
    rejected_for_unimplemented_format,
    run_compiled_candidate,
    runtime_record_from_entry,
    scope_unimplemented_formats,
    verify_cpp_example_linux,
    verify_dotnet_example,
    verify_go_example,
    verify_java_example,
    verify_nodejs_example,
    verify_python_example,
    verify_rust_example,
    verify_typescript_example,
)
from foss_mcp.normalization.chunker import Chunk, chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document

# REQ-G2-048 (TC-184): exit status when the verifier hits an environment failure (a toolchain
# that could not reach its package registry). 75 is EX_TEMPFAIL from sysexits.h: a retryable
# failure that is not a defect, so the ingestion Job can tell it apart from the exit 1 a
# defect produces.
ENVIRONMENT_FAILURE_EXIT = 75


def _prepare_cpp(args: argparse.Namespace, workdir: Path) -> Path:
    """TC-361: prepare the C++ library. With no ``--library-cpp-*`` flag this is the exact
    three-positional-argument call pdf/cpp has always made; the keywords are added only when a
    consumer-project flag is set."""
    source_dir = getattr(args, "library_cpp_source_dir", None)
    target = getattr(args, "library_cpp_target", None)
    warnings = getattr(args, "library_cpp_warnings", None)
    compat = getattr(args, "library_cpp_compat", None)
    if source_dir is None and target is None and warnings is None:
        return prepare_cpp_library_linux(args.library_repository, args.library_commit, workdir)
    if compat:
        # TC-455: the compat keywords appear only when --library-cpp-compat is given.
        return prepare_cpp_library_linux(
            args.library_repository,
            args.library_commit,
            workdir,
            source_subdir=source_dir,
            target=target,
            relax_warnings=warnings == "relaxed",
            compile_options=cpp_compat_compile_options(compat),
            include_subdirs="include-subdirs" in compat,
        )
    return prepare_cpp_library_linux(
        args.library_repository,
        args.library_commit,
        workdir,
        source_subdir=source_dir,
        target=target,
        relax_warnings=warnings == "relaxed",
    )


# REQ-G2-048 (TC-091): the single, centrally-readable mapping from --library-platform to how
# to prepare and verify a candidate example for that pilot. Each entry is
# (prepare, verify, verify_kwargs) where:
#   - prepare(args, workdir) -> "prepared library" object (a Path in every case here, but its
#     meaning differs per platform: a built csproj file for dotnet, a venv python executable
#     for python, a cloned+built library directory for rust/go/typescript, a packaged jar for
#     java). Every real prepare_<lang>_library function takes the same (repository, commit,
#     workdir) positional shape except dotnet's prepare_reference_library, which also takes
#     csproj_relative_path - that one difference is handled explicitly here, inline, rather
#     than forcing a fake uniform signature onto it.
#   - verify is the real verify_<lang>_example function, called uniformly as
#     verify(candidate, workdir=candidate_workdir, **verify_kwargs(prepared, args)).
#   - verify_kwargs(prepared, args) -> dict of the extra keyword-only arguments verify needs
#     beyond candidate/workdir, built from the prepared library plus (for rust/go/typescript)
#     a platform-specific CLI flag.
#   - shared_page_workdir (TC-190) -> True when the platform's candidates of one page run in ONE
#     shared candidate workdir, in document order, so a later example can open a file an earlier
#     example of the same page wrote (real API docs are sequential tutorials). False keeps a
#     fresh, isolated candidate workdir per candidate. Only python is True.
_PLATFORM_DISPATCH = {
    "dotnet": {
        "prepare": lambda args, workdir: prepare_reference_library(
            args.library_repository, args.library_commit, args.library_csproj, workdir
        ),
        "verify": verify_dotnet_example,
        "verify_kwargs": lambda prepared, args: {"library_csproj": prepared},
        "shared_page_workdir": False,
    },
    "python": {
        "prepare": lambda args, workdir: prepare_python_library(
            args.library_repository, args.library_commit, workdir
        ),
        "verify": verify_python_example,
        "verify_kwargs": lambda prepared, args: {"venv_python": prepared},
        "shared_page_workdir": True,
    },
    "rust": {
        "prepare": lambda args, workdir: prepare_rust_library(
            args.library_repository, args.library_commit, workdir
        ),
        "verify": verify_rust_example,
        "verify_kwargs": lambda prepared, args: {
            "library_crate_name": args.library_crate_name,
            "library_dir": prepared,
        },
        "shared_page_workdir": False,
    },
    "go": {
        "prepare": lambda args, workdir: prepare_go_library(
            args.library_repository, args.library_commit, workdir
        ),
        "verify": verify_go_example,
        "verify_kwargs": lambda prepared, args: {
            "module_path": args.library_module_path,
            "library_dir": prepared,
        },
        "shared_page_workdir": False,
    },
    "java": {
        "prepare": lambda args, workdir: prepare_java_library(
            args.library_repository, args.library_commit, workdir
        ),
        "verify": verify_java_example,
        "verify_kwargs": lambda prepared, args: {
            "library_jar": prepared,
            "page_imports": _page_java_imports(getattr(args, "furnished_page", None)),
            # TC-360: pilot mode is opt-in; without --library-java-packages the dict is unchanged.
            **(
                {"java_packages": _java_packages_tuple(args.library_java_packages)}
                if getattr(args, "library_java_packages", None)
                else {}
            ),
        },
        "shared_page_workdir": False,
    },
    "typescript": {
        "prepare": lambda args, workdir: prepare_typescript_library(
            args.library_repository, args.library_commit, workdir
        ),
        "verify": verify_typescript_example,
        "verify_kwargs": lambda prepared, args: {
            "library_dir": prepared,
            "package_name": args.library_package_name,
        },
        "shared_page_workdir": False,
    },
    # TC-362: a plain ESM JavaScript pilot: the verifier checks syntax and export bindings.
    "nodejs": {
        "prepare": lambda args, workdir: prepare_nodejs_library(
            args.library_repository, args.library_commit, workdir
        ),
        "verify": verify_nodejs_example,
        "verify_kwargs": lambda prepared, args: {
            "library_dir": prepared,
            "package_name": args.library_package_name,
        },
        "shared_page_workdir": False,
    },
    # TC-222: pdf/cpp is compiled with g++ and CMake in the Linux ingestion image. prepared is the
    # build directory (holding libaspose_pdf_foss.a); the header root is its sibling include/.
    # verifier_name labels the Linux verifier pair; the test suite checks it by name.
    "cpp": {
        "prepare": lambda args, workdir: _prepare_cpp(args, workdir),
        "verify": verify_cpp_example_linux,
        "verify_kwargs": lambda prepared, args: {
            "library_dir": prepared,
            "include_dir": prepared.parent / "include",
            # TC-361: the consumer-project mode and the compile-only verdict are opt-in; with no
            # cpp flag the dict is exactly pdf/cpp's.
            **({"consumer": True} if getattr(args, "library_cpp_target", None) else {}),
            **({"run": False} if getattr(args, "library_cpp_verify_mode", None) == "compile" else {}),
            # TC-455: the page's own #include lines go in front of each candidate, opt-in per pilot.
            **(
                {"page_includes": _page_cpp_includes(getattr(args, "furnished_page", None))}
                if "page-includes" in (getattr(args, "library_cpp_compat", None) or ())
                else {}
            ),
        },
        "shared_page_workdir": False,
        "verifier_name": "cpp_verifier_linux",
    },
}

# TC-565 (REQ-G3-009): the platforms whose compiled candidate is RUN after the compile verdict, mapped to the
# real verify function that leaves the compiled project in the candidate workdir.
_RUNNABLE_VERIFIERS = {"java": verify_java_example, "dotnet": verify_dotnet_example}

# REQ-G2-048 (TC-091): which extra CLI flag (beyond --library-repository/--library-commit) is
# required for a given --library-platform, and its attribute name on the parsed args - keyed
# only for platforms that actually need one. python and java need no extra flag: their verify_*
# functions' extra kwarg (venv_python, library_jar) comes from their own prepare_*_library's
# return value, never a new CLI input.
_PLATFORM_REQUIRED_FLAGS: dict[str, tuple[str, str]] = {
    "dotnet": ("library_csproj", "--library-csproj"),
    "rust": ("library_crate_name", "--library-crate-name"),
    "go": ("library_module_path", "--library-module-path"),
    "typescript": ("library_package_name", "--library-package-name"),
    "nodejs": ("library_package_name", "--library-package-name"),
}


_JAVA_PACKAGE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")


def _java_packages_tuple(value: str | tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """The Java package names of ``--library-java-packages``, as a tuple. Accepts the parsed tuple
    or the raw comma-separated string.
    """
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(",") if part.strip())
    return tuple(value)


def _java_packages_argument(text: str) -> tuple[str, ...]:
    """argparse type for ``--library-java-packages``: comma-separated Java package names, each of
    which must be a valid dotted identifier; anything else is a usage error (exit 2).
    """
    packages = _java_packages_tuple(text)
    if not packages:
        raise argparse.ArgumentTypeError("expected at least one Java package name")
    for package in packages:
        if _JAVA_PACKAGE_RE.match(package) is None:
            raise argparse.ArgumentTypeError(f"{package!r} is not a valid Java package name")
    return packages


# TC-385 (REQ-G3-006): the invisible and bidirectional control characters stripped from every chunk.
# Built from code points, never typed literally: U+00AD, U+200B..U+200F, U+202A..U+202E,
# U+2060..U+2064, U+2066..U+2069 and U+FEFF.
_INVISIBLE_CODE_POINT_RANGES = (
    (0x00AD, 0x00AD),
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x2064),
    (0x2066, 0x2069),
    (0xFEFF, 0xFEFF),
)
_INVISIBLE_RE = re.compile(
    "[" + "".join(f"{chr(low)}-{chr(high)}" for low, high in _INVISIBLE_CODE_POINT_RANGES) + "]"
)
# A Hugo shortcode on one line, non-greedy, with the single space (if any) on each side captured so
# that removing it leaves one space where it would otherwise leave two.
_SHORTCODE_RE = re.compile(r"( ?)(?:\{\{<.*?>\}\}|\{\{%.*?%\}\})( ?)")
_EXAMPLE_MARKER_LINE = "Example:"
MAX_CHUNK_TEXT_CHARS = 24_000
_TRUNCATION_LINE_RE = re.compile(r"Truncated: \d+ characters omitted")


def _shortcode_replacement(match: re.Match[str]) -> str:
    before, after = match.group(1), match.group(2)
    return " " if before and after else before + after


def _strip_shortcodes_from_prose(prose_lines: list[str]) -> list[str]:
    kept: list[str] = []
    for line in prose_lines:
        stripped = _SHORTCODE_RE.sub(_shortcode_replacement, line)
        if stripped != line and not stripped.strip():
            continue  # the removal emptied the line, so the line goes with it
        kept.append(stripped)
    return kept


def _sanitize_chunk_text(text: str) -> str:
    """TC-385 (REQ-G3-006): the one clean-up step every chunk's text passes before it is written.

    In order: (1) remove invisible and bidirectional control characters everywhere; (2) remove
    Hugo shortcodes (``{{< ... >}}`` and ``{{% ... %}}``) from the prose only - the part before a
    line that is exactly ``Example:`` - never from the verified code after it; (3) cap the text at
    ``MAX_CHUNK_TEXT_CHARS`` by cutting at the last newline at or before that length and appending
    an explicit ``Truncated: <N> characters omitted`` line. A text with none of these is returned
    unchanged, and sanitising an already sanitised text changes nothing.
    """
    text = _INVISIBLE_RE.sub("", text)

    if "{{" in text:
        lines = text.split("\n")
        try:
            boundary = lines.index(_EXAMPLE_MARKER_LINE)
        except ValueError:
            boundary = len(lines)
        text = "\n".join(_strip_shortcodes_from_prose(lines[:boundary]) + lines[boundary:])

    if len(text) > MAX_CHUNK_TEXT_CHARS:
        head, _, last_line = text.rpartition("\n")
        if _TRUNCATION_LINE_RE.fullmatch(last_line) and len(head) <= MAX_CHUNK_TEXT_CHARS:
            return text  # already capped by this function: the cap is never applied twice
        cut = text.rfind("\n", 0, MAX_CHUNK_TEXT_CHARS + 1)
        if cut <= 0:
            cut = MAX_CHUNK_TEXT_CHARS  # no usable line break: cut at the limit itself
        text = f"{text[:cut]}\nTruncated: {len(text) - cut} characters omitted"
    return text


def _with_source_commit_line(chunk: Chunk) -> Chunk:
    return dataclasses.replace(chunk, text=f"{chunk.text}\nSource-Commit: {chunk.provenance.commit}")


def _load_furnished_page(path: Path) -> dict:
    """Parse a Hugo page-bundle's front matter - the exact real pattern TC-066's own
    real-fixture test uses (split on the first two ``---`` fences, ``yaml.safe_load``).
    """
    text = path.read_text(encoding="utf-8")
    front_matter = text.split("---", 2)[1]
    parsed = yaml.safe_load(front_matter)
    assert isinstance(parsed, dict)
    return parsed


def _load_authored_candidates(args: argparse.Namespace) -> tuple[CandidateExample, ...]:
    """TC-460 (REQ-G3-007): the authored examples of the pilot whose furnished page is ``--furnished-page``.

    A sidecar is looked for only when the page sits at ``<pilot dir>/pages/<page>``: then
    ``<pilot dir>/authored_examples.yaml`` is loaded (pilot = the directory's name; platform and
    commit from the arguments; api types from ``--api-surface``). No sidecar gives an empty tuple.
    An invalid or stale sidecar never stops the build and never publishes any authored example: one
    ``authored examples ignored: ...`` line is printed and the README candidates alone are used.
    """
    page_path = Path(args.furnished_page)
    if page_path.parent.name != "pages":
        return ()
    sidecar = page_path.parent.parent / SIDECAR_NAME
    builtin_pilot = page_path.parent.parent.name
    # TC-565: a pilot with no sidecar file may carry its authored examples in authored_examples.py itself.
    use_builtin = not sidecar.is_file() and has_builtin_authored_examples(builtin_pilot)
    if not sidecar.is_file() and not use_builtin:
        return ()
    try:
        fixture = json.loads(Path(args.api_surface).read_text(encoding="utf-8"))
        api_types = fixture["types"]
        if not isinstance(api_types, list):
            raise TypeError("the api_surface types entry is not a list")
        if use_builtin:
            return load_builtin_authored_examples(
                builtin_pilot,
                library_platform=args.library_platform,
                library_commit=args.library_commit,
                api_types=api_types,
            )
        return load_authored_examples(
            sidecar,
            pilot=page_path.parent.parent.name,
            library_platform=args.library_platform,
            library_commit=args.library_commit,
            api_types=api_types,
        )
    except (AuthoredExamplesError, OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        print(f"authored examples ignored: {str(exc)[:600]}")
        return ()


def _page_java_imports(path: Path | None) -> tuple[str, ...]:
    """Return the import lines of a furnished page, in document order: each stripped line that
    starts with ``import `` and ends with ``;``. Prose that merely mentions imports is not one of
    them. With no page (``None``) there are no imports.
    """
    if path is None:
        return ()
    imports: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("import ") and line.endswith(";"):
            imports.append(line)
    return tuple(imports)


_CPP_INCLUDE_LINE_RE = re.compile(r'^#\s*include\s*[<"][^>"]+[>"]$')


def _page_cpp_includes(path: Path | None) -> tuple[str, ...]:
    """Return the ``#include`` lines of the furnished page's extracted candidate examples, in
    document order and de-duplicated (TC-455). Each is a stripped whole line; an inline-code
    mention in prose is never one of them. With no page (``None``) there are none.
    """
    if path is None:
        return ()
    includes: list[str] = []
    for candidate in extract_candidate_examples(_load_furnished_page(path)):
        for raw_line in candidate.code.splitlines():
            line = raw_line.strip()
            if _CPP_INCLUDE_LINE_RE.match(line) and line not in includes:
                includes.append(line)
    return tuple(includes)


def _cpp_compat_argument(text: str) -> tuple[str, ...]:
    """argparse type for ``--library-cpp-compat``: a comma-separated list of names from
    ``CPP_COMPAT_NAMES``; an empty list or an unknown name is a usage error (exit 2)."""
    names = tuple(part.strip() for part in text.split(",") if part.strip())
    if not names:
        raise argparse.ArgumentTypeError("expected at least one C++ compat name")
    for name in names:
        if name not in CPP_COMPAT_NAMES:
            raise argparse.ArgumentTypeError(
                f"unknown C++ compat name {name!r}; expected one of {', '.join(CPP_COMPAT_NAMES)}"
            )
    return names


def _write_verification_report(path: Path, records: list[dict]) -> None:
    """Write one UTF-8 JSON array holding every candidate's verification result (TC-191).

    Verified and unverified candidates alike are recorded. ``output`` is the real compiler or
    interpreter output, written in full and never truncated, because a rejected candidate must be
    fixed from this evidence rather than from a guess.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")


# REQ-G3-007 (TC-441): the persistent, committed VERIFICATION CACHE. Every real compile/run verdict
# a toolchain produces is thrown away today - the ingestion Job builds --out under /tmp (an
# emptyDir) and every re-ingestion clones, builds and compiles everything again, even a candidate
# whose code, library commit, verifier platform and toolchain have not changed since it was last
# verified true. The cache holds VERDICTS ONLY, never example code (the single source of authored
# example code is TC-460's own sidecar); a cached verdict is reused only when the normalised code
# hash, the library commit, the verifier platform and the toolchain id all match.
CACHE_SCHEMA_VERSION = 1
_CACHE_ENTRY_FIELDS = frozenset(
    {"provenance", "title", "code_sha256", "library_commit", "platform", "toolchain", "verified", "output"}
)
# TC-565 (REQ-G3-009): the run verdict, recorded separately from the compile verdict. ``verified`` keeps
# its meaning (the compile verdict), so an entry written before this card stays valid and is read as
# ``ran`` unknown; an entry written after it may carry these keys, each only when the fact is known.
_CACHE_RUNTIME_FIELDS = frozenset({"compiled", "ran", "exit_code", "produced_output", "runtime_error"})
# The last N characters of a verifier's output kept in a cache entry - the FULL output stays in the
# .verification.json report; the cache is meant to be read by a person skimming a diff, not to
# reproduce a rejection from scratch.
_CACHE_OUTPUT_TAIL_CHARS = 2000

# Which real executable(s) each --library-platform's verify function invokes (example_verifier.py):
# dotnet build -> dotnet; the venv python verify_python_example runs candidates with -> this
# interpreter (the running one, matching _pick_python_interpreter's own default with no
# requires-python narrowing it); cargo build -> cargo; go build/mod tidy/vet -> go; javac -> javac;
# node running the library's own bundled tsc.js -> node (typescript and nodejs alike); g++ -> g++
# (the cpp consumer-project mode's extra cmake invocation is opt-in per pilot, not probed here).
_TOOLCHAIN_VERSION_COMMAND: dict[str, tuple[str, ...]] = {
    "dotnet": ("dotnet", "--version"),
    "python": (sys.executable, "--version"),
    "rust": ("cargo", "--version"),
    "go": ("go", "version"),
    "java": ("javac", "-version"),
    "typescript": ("node", "--version"),
    "nodejs": ("node", "--version"),
    "cpp": ("g++", "--version"),
}
_TOOLCHAIN_PROBE_TIMEOUT_SECONDS = 30


def toolchain_id(platform: str) -> str | None:
    """One string identifying the real toolchain ``--library-platform platform``'s verify function
    invokes (TC-441): the first line of its executable's version output (``--version``, or the
    platform's own equivalent, e.g. ``go version``, ``javac -version``). Run with subprocess, a
    timeout, no shell and no network.

    Returns ``None`` for an unknown platform, a missing executable, a command that fails or times
    out, or output with no usable line - a run where this returns ``None`` reuses nothing and
    writes nothing to the cache. Tests monkeypatch this function; it never runs a real toolchain
    under test.
    """
    command = _TOOLCHAIN_VERSION_COMMAND.get(platform)
    if command is None:
        return None
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=_TOOLCHAIN_PROBE_TIMEOUT_SECONDS,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    for line in f"{result.stdout}\n{result.stderr}".splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def normalised_code_sha256(code: str) -> str:
    """sha256 (lowercase hex) of ``code`` after normalising incidental whitespace (TC-441), so a
    CRLF/CR save, a trailing-space edit, or a surrounding blank line never changes a verified
    verdict's cache key, while a changed statement always does.

    In order: CRLF and CR become LF; trailing whitespace is stripped from every line; leading and
    trailing blank lines are dropped; then the UTF-8 bytes of what remains are hashed.

    TC-458: the rule now lives in ``foss_mcp.indexing.example_candidates`` (a candidate records its
    own hash there); this function returns that module's result, so there is exactly one rule.
    """
    return _candidate_code_sha256(code)


def verification_cache_key(
    candidate: Any, library_commit: str, platform: str, toolchain: str
) -> tuple[str, str, str, str, str]:
    """The lookup/write key for one candidate's verification verdict (TC-441): ``(provenance,
    code_sha256, library_commit, platform, toolchain)``. Title plays no part in matching, only in
    the record kept for history.

    ``provenance`` is ``getattr(candidate, "provenance", "readme")``, so a candidate predating
    TC-460's own field (or a test-local frozen-dataclass stand-in without it) keys as ``"readme"``:
    an authored candidate's verdict is never mistaken for a README candidate's, even with
    identical code, and the reverse.
    """
    provenance = getattr(candidate, "provenance", "readme")
    return (provenance, normalised_code_sha256(candidate.code), library_commit, platform, toolchain)


def _cache_entry_key(entry: dict) -> tuple[str, str, str, str, str]:
    """The same 5-field key as ``verification_cache_key``, read back from a written cache entry."""
    return (
        entry["provenance"],
        entry["code_sha256"],
        entry["library_commit"],
        entry["platform"],
        entry["toolchain"],
    )


def parse_verification_cache(text: str) -> list[dict]:
    """Parse a verification cache document's TEXT into its ``entries`` list (TC-441).

    Raises ``ValueError``, with a one-line message naming what is wrong (never a traceback), for
    anything that is not exactly ``{"schema_version": 1, "entries": [...]}`` with every entry
    carrying the eight documented fields and, optionally, the TC-565 run fields (``compiled``, ``ran``,
    ``exit_code``, ``produced_output``, ``runtime_error``) - an ``"authored"`` key, any other extra
    top-level key, or a wrong ``schema_version``, included.
    """
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ValueError(f"not valid JSON ({exc})") from exc
    if not isinstance(data, dict) or set(data) != {"schema_version", "entries"}:
        raise ValueError("must have exactly the top-level keys schema_version and entries")
    if data.get("schema_version") != CACHE_SCHEMA_VERSION:
        raise ValueError(f"schema_version is {data.get('schema_version')!r}, expected {CACHE_SCHEMA_VERSION}")
    entries = data["entries"]
    if not isinstance(entries, list):
        raise ValueError("entries is not a list")
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or not _CACHE_ENTRY_FIELDS <= set(entry)
            or not set(entry) <= _CACHE_ENTRY_FIELDS | _CACHE_RUNTIME_FIELDS
        ):
            raise ValueError("an entry does not have exactly the eight documented fields")
    return entries


def load_verification_cache(path: Path) -> list[dict]:
    """The ``entries`` of the verification cache at ``path`` (TC-441); ``[]`` (with one printed
    line) when ``path`` does not exist. Raises ``ValueError``, with a one-line message naming
    ``path``, for anything malformed - ``main()`` turns that into exit code 2.
    """
    if not path.exists():
        print(f"verification cache: no file at {path}; starting with an empty cache")
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"verification cache {path} could not be read: {exc}") from exc
    try:
        return parse_verification_cache(text)
    except ValueError as exc:
        raise ValueError(f"verification cache {path} is malformed: {exc}") from exc


def merge_verification_cache(existing: list[dict], new: list[dict]) -> list[dict]:
    """Merge ``new`` onto ``existing`` (TC-441): every entry of ``existing`` is kept (other commits
    and toolchains included, so history is never lost), and ``new``'s verdict replaces any entry
    with the identical ``(provenance, code_sha256, library_commit, platform, toolchain)`` key.

    Returned sorted by ``(provenance, title, code_sha256, library_commit, platform, toolchain)`` -
    the cache's own committed, deterministic order, so an unchanged run writes identical bytes.
    """
    merged: dict[tuple[str, str, str, str, str], dict] = {
        _cache_entry_key(entry): entry for entry in existing
    }
    for entry in new:
        merged[_cache_entry_key(entry)] = entry
    return sorted(
        merged.values(),
        key=lambda e: (
            e["provenance"],
            e["title"],
            e["code_sha256"],
            e["library_commit"],
            e["platform"],
            e["toolchain"],
        ),
    )


def verification_cache_document(entries: list[dict]) -> str:
    """The exact committed text of a verification cache holding ``entries`` (TC-441): UTF-8 JSON,
    ``schema_version`` 1, ``sort_keys=True``, ``ensure_ascii=False``, one trailing newline.
    """
    return (
        json.dumps(
            {"schema_version": CACHE_SCHEMA_VERSION, "entries": entries},
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n"
    )


def write_verification_cache_if_changed(path: Path, entries: list[dict]) -> bool:
    """Write ``entries`` to ``path`` as a verification cache, atomically (a sibling ``.tmp`` file
    then renamed over ``path``), creating parent directories as needed, only when the resulting
    bytes differ from what is already there (TC-441). Returns ``True`` iff ``path``'s content
    changed, so a caller can print ``written``/``unchanged``.
    """
    payload = verification_cache_document(entries)
    if path.exists():
        try:
            if path.read_text(encoding="utf-8") == payload:
                return False
        except OSError:
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(payload, encoding="utf-8")
    tmp_path.replace(path)
    return True


def _display_units(units: Iterable[str]) -> str:
    """Import units for a Harness part: each LF inside a unit is shown as a space, units joined by ' | '."""
    return " | ".join(unit.replace("\n", " ") for unit in units)


def _published_program(
    effective: CandidateExample,
    args: argparse.Namespace,
    applied_prefix: tuple[str, ...] = (),
) -> tuple[str, list[str]]:
    """TC-458 (REQ-G3-007): the program the toolchain compiled for ``effective``, and what was added.

    Returns ``(program, harness_parts)``. ``program`` is the text the example chunk publishes and
    the verification cache is keyed on; each part names one thing the harness added around the
    README block. ``applied_prefix`` is the chain import units ``effective_candidate`` put in front
    of the code (``effective`` itself has none left). The result depends only on the candidate, the
    page and the arguments, so a cached and a fresh verdict publish identical text.

    - python, typescript, nodejs: ``effective.code`` (its chain imports already in it).
    - dotnet: the verifier's assembled source (the page's using directives the block lacks).
    - rust: the verifier's assembled source (``fn main`` wrapper, the page's ``use`` statements).
    - cpp: the page ``#include`` lines the block lacks in front, then the ``int main`` wrapper.
    - go, java: ``effective.code``; their assembly needs the built library and stays in the
      verification report.
    """
    platform = args.library_platform
    code = effective.code
    program = code
    parts: list[str] = []

    if platform in CHAIN_PREFIX_PLATFORMS:
        if applied_prefix:
            parts.append(
                f"import lines prepended from earlier examples on this page: {_display_units(applied_prefix)}"
            )
    elif platform == "dotnet":
        program, added_usings = _assemble_dotnet_source(code, effective.page_code)
        if added_usings:
            parts.append(
                "using directives from other blocks on this page added by the verifier: "
                f"{_display_units(added_usings)}"
            )
    elif platform == "rust":
        program, mode, added_uses = _assemble_rust_source(code, effective.page_code)
        if mode == "wrapped-fragment":
            parts.append("wrapped by the verifier in fn main() -> Result<(), Box<dyn std::error::Error>>")
        if added_uses:
            parts.append(
                "use statements from other blocks on this page added by the verifier: "
                f"{_display_units(added_uses)}"
            )
    elif platform == "cpp":
        page_includes: tuple[str, ...] = ()
        if "page-includes" in (getattr(args, "library_cpp_compat", None) or ()):
            page_includes = _page_cpp_includes(getattr(args, "furnished_page", None))
        existing_lines = {line.strip() for line in code.splitlines()}
        prefixed: list[str] = []
        for include_line in page_includes:
            if include_line.strip() not in existing_lines and include_line not in prefixed:
                prefixed.append(include_line)
        source = "\n".join([*prefixed, code]) if prefixed else code
        program = _wrap_cpp_fragment_if_needed(source)
        if prefixed:
            parts.append(
                f"#include lines from other blocks on this page added by the verifier: {_display_units(prefixed)}"
            )
        if program != source:
            parts.append("wrapped by the verifier in int main()")

    if platform == "python" or (
        platform == "cpp" and getattr(args, "library_cpp_verify_mode", None) != "compile"
    ):
        names = input_literals(program, platform)
        if names:
            parts.append(
                "input files named in the code are placed in the working directory by the verifier when it "
                "can (kept if already there, otherwise copied by name from the pinned repository or "
                f"generated as a minimal file): {', '.join(names)}"
            )
    return program, parts


_MARKDOWN_HEADING_RE = re.compile(r"^#{1,6}[ \t]+", re.MULTILINE)


def _chunkable_description(description: str) -> str:
    """TC-458: ``description`` without markdown heading syntax. A README content block's prose before
    its first fence is a whole section (``### Installation``, ``### Quick Start``): left as is, the
    chunker splits the example body at every such heading and the code lands in a chunk without the
    ``FQN:`` and ``Kind: verified_example`` lines, so the example could no longer be recognised. The
    text of each heading is kept; a description with no heading line is returned unchanged."""
    return _MARKDOWN_HEADING_RE.sub("", description)


_RUNTIME_ERROR_LINE_CHARS = 300


def _runtime_chunk_lines(runtime: RuntimeRecord) -> str:
    """TC-565: the lines of an example chunk that say what running it proved, directly after ``Kind``.

    ``Runtime-Status`` is ``ran`` (exit 0, produced output), ``failed`` or ``no_output``, with the bytes
    produced and, for a failure, one line of its error. Nothing is written when the run was never
    attempted (``ran`` unknown): a chunk without these lines is read as compiled-only, which is all that
    was proven. ``find_examples`` turns the lines into its label.
    """
    status = runtime.status
    if status not in (RUNTIME_RAN_OK, RUNTIME_FAILED, RUNTIME_NO_OUTPUT):
        return ""
    lines = f"Runtime-Status: {status}\n"
    if runtime.produced_output is not None:
        lines += f"Runtime-Produced-Bytes: {runtime.produced_output}\n"
    error = " ".join(runtime.error.split())[:_RUNTIME_ERROR_LINE_CHARS].replace("Example:", "Example :")
    if status == RUNTIME_FAILED and error:
        lines += f"Runtime-Error: {error}\n"
    return lines


def _cache_key_text(effective: CandidateExample, program: str, platform: str) -> str:
    """TC-458: the text a verdict's cache key is the hash of. The published program, except for go
    and java, whose assembly needs the built library: there it is the candidate's code plus the
    page's import units (de-duplicated, sorted), so a changed import elsewhere on the page
    invalidates a recorded verdict."""
    if platform in ("go", "java"):
        units = sorted({unit for entry in effective.page_code for unit in import_like_lines(entry, platform)})
        return effective.code + "\n" + "\n".join(units)
    return program


@dataclasses.dataclass(frozen=True)
class _CandidatePlan:
    """Everything decided about one candidate before any toolchain runs (TC-458)."""

    index: int
    candidate: CandidateExample
    skipped: bool
    effective: CandidateExample
    program: str
    harness: str
    key_candidate: CandidateExample


def _build_verified_example_chunks(
    args: argparse.Namespace,
    verification_records: list[dict] | None = None,
    verification_cache: dict[tuple[str, str, str, str, str], dict] | None = None,
    toolchain: str | None = None,
    new_cache_entries: list[dict] | None = None,
) -> list[Chunk]:
    """Extract, really compile-verify, and chunk every candidate example from
    ``--furnished-page`` - an unverified candidate is silently excluded, never published,
    never logged as an error.

    Dispatches to the real prepare/verify pair for ``args.library_platform`` via
    ``_PLATFORM_DISPATCH`` (REQ-G2-048/TC-091). For ``--library-platform dotnet`` this calls
    ``prepare_reference_library``/``verify_dotnet_example`` with exactly the same arguments as
    before this dispatch layer existed - byte-identical behavior for the real, currently-running
    pdf/net production path.

    When ``verification_records`` is a list, one dict is appended per candidate as its verifier
    returns, verified or not: ``title``, ``platform``, ``verified``, ``output`` (in full) and
    ``index`` (the candidate's 0-based position in document order on its page) and ``kind``
    (``candidate``).

    TC-379: when the platform's ``prepare`` raises a ``RuntimeError`` that is not an
    ``ExampleEnvironmentError``, one record of ``kind`` ``library_unavailable`` (``index`` -1, the
    full message as ``output``) is appended instead and an empty list is returned.

    TC-441 (REQ-G3-007): when ``verification_cache`` is given (not ``None``, possibly empty), a
    candidate is a cache HIT only when it has a ``verified: true`` entry with the identical
    ``verification_cache_key``; a hit is not verified again, and its record (and the published
    chunk, byte-identical either way) comes from the cache. A platform with
    ``shared_page_workdir`` True reuses the whole page only when EVERY candidate is a hit -
    otherwise every candidate on that page is verified for real and none is marked cached. The
    platform's ``prepare`` runs only when at least one candidate on the page must really be
    verified, so a fully cached page publishes with no network at all. Every real (non-cached)
    verdict is appended to ``new_cache_entries`` (when given) for the caller to merge and write
    out; a ``library_unavailable`` record or an environment failure is never written as an entry.

    TC-458 (REQ-G3-007): the candidates are ``extract_candidate_supply(page, platform)`` - every
    same-language fence of the example blocks and the README content blocks, de-duplicated - or, on a
    page with no fence in the pilot's language, the legacy first fence of each example block. On cpp,
    java and nodejs an import-only block is neither verified nor published (record ``kind``
    ``imports_only``). The verifier is handed ``effective_candidate`` (a continuation gets the import
    lines of its chain where no verifier borrows page imports); the chunk publishes
    ``_published_program`` - what the toolchain compiled - with a ``Harness:`` line naming what was
    added, and the verification cache is keyed on that program.
    """
    page = _load_furnished_page(args.furnished_page)
    platform = args.library_platform
    authored = _load_authored_candidates(args)
    supply = extract_candidate_supply(page, platform, authored=authored)
    if supply.fallback:
        # The legacy first fence of every example block, then the authored ones (the same list
        # ``supply.candidates`` holds, which ``authored=`` above already merged).
        candidates, _ = merge_authored_candidates(extract_candidate_examples(page), authored)
    else:
        candidates = list(supply.candidates)
    if supply.fallback:
        print(
            f"candidate supply: no {platform} code fences on the page; "
            "using the first fence of every example block unfiltered"
        )
    else:
        print(
            f"candidate supply: {len(supply.candidates)} candidates for {platform} "
            f"({supply.from_single_blocks} from example blocks, {supply.from_content_blocks} from content "
            f"blocks; dropped {supply.duplicates_dropped} duplicates and {supply.other_language_dropped} "
            f"fences in other languages; {supply.imports_only} import-only blocks)"
        )

    platform_dispatch = _PLATFORM_DISPATCH[platform]
    library_commit = args.library_commit

    plans: list[_CandidatePlan] = []
    for index, candidate in enumerate(candidates):
        if platform in IMPORT_ONLY_GUARD_PLATFORMS and candidate.imports_only:
            plans.append(_CandidatePlan(index, candidate, True, candidate, candidate.code, "", candidate))
            continue
        effective = effective_candidate(candidate, platform)
        program, parts = _published_program(
            effective, args, candidate.import_prefix if effective is not candidate else ()
        )
        key_text = _cache_key_text(effective, program, platform)
        key_candidate = dataclasses.replace(
            effective, code=key_text, code_sha256=normalised_code_sha256(key_text)
        )
        harness = "; ".join(parts).replace("Example:", "Example :")
        plans.append(_CandidatePlan(index, candidate, False, effective, program, harness, key_candidate))
    active = [plan for plan in plans if not plan.skipped]

    cache_active = verification_cache is not None
    hit_entries: list[dict | None] = [None] * len(plans)
    if cache_active:
        # Both are guaranteed together by the caller (main()): a non-None verification_cache is
        # passed only alongside a toolchain_id that itself returned non-None.
        assert verification_cache is not None
        assert toolchain is not None
        cache = verification_cache
        resolved_toolchain = toolchain

        def _cache_hit(candidate: Any) -> dict | None:
            key = verification_cache_key(candidate, library_commit, platform, resolved_toolchain)
            entry = cache.get(key)
            return entry if entry is not None and entry.get("verified") is True else None

        for plan in active:
            hit_entries[plan.index] = _cache_hit(plan.key_candidate)
        if platform_dispatch["shared_page_workdir"] and not (
            active and all(hit_entries[plan.index] is not None for plan in active)
        ):
            # TC-441: a shared-workdir page (python) is reused only as a whole; a partial hit
            # re-verifies every candidate for real, in document order, and marks none cached.
            hit_entries = [None] * len(plans)

    needs_real_verification = any(hit_entries[plan.index] is None for plan in active)

    prepared_library = None
    verify_kwargs: dict = {}
    if needs_real_verification:
        library_workdir = Path(tempfile.mkdtemp(prefix="build_chunks_library_"))
        try:
            prepared_library = platform_dispatch["prepare"](args, library_workdir)
        except ExampleEnvironmentError:
            # An environment failure stays fatal: main() ends the build with ENVIRONMENT_FAILURE_EXIT.
            raise
        except RuntimeError as exc:
            # TC-379 (REQ-G3-005): a reference library that cannot be built is a reason to publish no
            # verified examples, not a reason to publish no documentation. Only the platform's prepare
            # call is covered; the caller still adds the documentation chunks and writes the output.
            message = str(exc)
            print(
                f"reference library unavailable: {message[:600]}; "
                "publishing documentation without verified examples",
                file=sys.stderr,
            )
            if verification_records is not None:
                verification_records.append(
                    {
                        "title": "(reference library preparation)",
                        "platform": platform,
                        "verified": False,
                        "output": message,
                        "index": -1,
                        "kind": "library_unavailable",
                    }
                )
            return []
        verify_kwargs = platform_dispatch["verify_kwargs"](prepared_library, args)
    verify = platform_dispatch["verify"]

    # TC-190: a platform whose candidates are sequential tutorials shares one candidate workdir
    # across every candidate of the page (created once, before the loop, in document order).
    # Every other platform gets a fresh, isolated workdir per candidate, as before.
    shared_candidate_workdir: Path | None = None
    if platform_dispatch["shared_page_workdir"] and needs_real_verification:
        shared_candidate_workdir = Path(tempfile.mkdtemp(prefix="build_chunks_page_"))
    # TC-565: the formats the pilot's own Scope and Limitations section says are not implemented.
    unimplemented_formats = scope_unimplemented_formats(page)

    verified_chunks: list[Chunk] = []
    verified_count = 0
    authored_verified = 0
    cached_count = 0
    for plan in plans:
        candidate = plan.candidate
        index = plan.index

        if plan.skipped:
            # TC-458: a block that holds nothing but imports is not an example on a platform whose
            # verifier would compile and count it. It is recorded, never verified or published.
            if verification_records is not None:
                skipped_record: dict = {
                    "title": candidate.title,
                    "platform": platform,
                    "verified": False,
                    "output": "import-only block: not an example by itself",
                    "index": index,
                    "kind": "imports_only",
                    "cached": False,
                    "source": candidate.source,
                    "starts_example": candidate.starts_example,
                    "harness": "",
                    "status": "",
                    "fixtures": [],
                }
                verification_records.append(skipped_record)
            continue

        hit_entry = hit_entries[index]
        verification_result = None
        if hit_entry is not None:
            compiled = True
            output = hit_entry.get("output", "")
            cached = True
            cached_count += 1
            runtime = runtime_record_from_entry(hit_entry)
        else:
            if shared_candidate_workdir is not None:
                candidate_workdir = shared_candidate_workdir
            else:
                candidate_workdir = Path(tempfile.mkdtemp(prefix="build_chunks_candidate_"))
            verification_result = verify(plan.effective, workdir=candidate_workdir, **verify_kwargs)
            compiled = verification_result.verified
            output = verification_result.output
            cached = False
            if compiled and verify is _RUNNABLE_VERIFIERS.get(platform):
                # TC-565: a compile-only pass proves nothing about the example working. Run what compiled.
                # Only what the REAL compiler step left in the workdir can be run: a stand-in verifier
                # (a test double) compiled nothing, so its candidate is recorded as compiled only.
                runtime = run_compiled_candidate(
                    platform,
                    plan.effective,
                    workdir=candidate_workdir,
                    library=prepared_library if isinstance(prepared_library, Path) else None,
                    runner=getattr(args, "runtime_runner", None),
                )
            else:
                runtime = RuntimeRecord(compiled=compiled)
        # TC-565: verified now means compiled AND (ran with exit 0 and produced output, or run not attempted).
        verified = (
            entry_counts_as_verified(hit_entry)
            if hit_entry is not None
            else counts_as_verified(runtime.status)
        )
        rejected = compiled and rejected_for_unimplemented_format(
            runtime, candidate.code, unimplemented_formats
        )

        if verification_records is not None:
            record = {
                "title": candidate.title,
                "platform": platform,
                "verified": verified,
                "output": output,
                "index": index,
                "kind": "authored" if candidate.provenance == "authored" else "candidate",
            }
            if runtime.ran is not None:
                record.update(runtime.as_cache_fields())
                record["runtime_status"] = runtime.status
            if rejected:
                record["rejected"] = "output is empty and the format is documented as not implemented"
            if cache_active:
                record["cached"] = cached
                record["code_sha256"] = plan.key_candidate.code_sha256
                record["toolchain"] = toolchain
            record["source"] = candidate.source
            record["starts_example"] = candidate.starts_example
            record["harness"] = plan.harness
            record["status"] = getattr(verification_result, "status", "")
            record["fixtures"] = [
                fixture.as_dict() for fixture in getattr(verification_result, "fixtures", ())
            ]
            verification_records.append(record)

        if cache_active and new_cache_entries is not None and not cached:
            new_cache_entries.append(
                {
                    "provenance": candidate.provenance,
                    "title": candidate.title,
                    "code_sha256": plan.key_candidate.code_sha256,
                    "library_commit": library_commit,
                    "platform": platform,
                    "toolchain": toolchain,
                    # The compile verdict, unchanged in meaning; the run verdict is the keys below.
                    "verified": compiled,
                    "output": output[-_CACHE_OUTPUT_TAIL_CHARS:],
                    **(runtime.as_cache_fields() if runtime.ran is not None else {}),
                }
            )

        if verified:
            verified_count += 1
        if compiled and not rejected and (verified or candidate.provenance != "authored"):
            # TC-565: an example that compiled but fails at runtime (or produced nothing) is published
            # only with its honest runtime lines, never as verified; an authored one only when verified.
            harness_line = f"Harness: {plan.harness}\n" if plan.harness else ""
            authored_lines = ""
            runtime_lines = _runtime_chunk_lines(runtime)
            if candidate.provenance == "authored":
                authored_verified += 1
                # TC-460: a verified authored example says so, in three lines directly after Kind.
                authored_lines = f"Provenance: authored\nVerification: passed\n{AUTHORED_LABEL}\n"
            doc = make_document(
                source_kind=SourceKind.FURNISHED,
                content_type="example",
                provenance=Provenance(
                    repository=args.library_repository,
                    commit=args.library_commit,
                    path=str(args.furnished_page),
                ),
                evidence_refs=(f"{args.library_repository}@{args.library_commit}",),
                title=candidate.title,
                body=(
                    f"# Example: {candidate.title}\n\n"
                    f"FQN: Example: {candidate.title}\n"
                    f"Kind: verified_example\n"
                    f"{authored_lines}"
                    f"{runtime_lines}"
                    f"{harness_line}"
                    f"{_chunkable_description(candidate.description)}\n\n"
                    f"Example:\n{plan.program}"
                ),
            )
            example_chunks = chunk_document(doc)
            if candidate.provenance == "authored" and len(example_chunks) > 1:
                # TC-460: a Python authored example must open with a ``#`` comment (sidecar rule 7), and
                # the chunker reads a column-0 ``#`` line as a markdown heading: it would cut the
                # code away from the FQN / Kind / Provenance lines and drop that comment. An authored
                # example is published as ONE chunk, the whole body after its own title heading. A
                # README example is never touched, so its chunk text and id do not move.
                _, _, section_text = doc.body.partition("\n")
                example_chunks = [dataclasses.replace(example_chunks[0], text=section_text.strip())]
            verified_chunks.extend(example_chunks)

    cache_suffix = f" ({cached_count} from cache)" if cache_active else ""
    print(f"verified {verified_count}/{len(active)} candidate examples{cache_suffix}")
    if authored:
        print(f"authored examples: {authored_verified} of {len(authored)} verified")
    return verified_chunks


def _build_doc_chunks(page: dict, args: argparse.Namespace) -> list[Chunk]:
    """Extract and chunk every real documentation candidate from ``--furnished-page``'s own
    ``overview``/``content``/``faq`` sections (REQ-G2-047, TC-110's ``extract_doc_sections``).

    Genuinely simpler than ``_build_verified_example_chunks``: prose is never compiled, so
    there is no compile-verification sandbox and no verified/unverified split - every
    ``DocCandidate`` becomes a chunk. For each candidate, a real ``Document`` is built via
    ``make_document`` mirroring ``_build_verified_example_chunks``'s own real call shape
    exactly (same argument names/order), then ``chunk_document`` splits it into one or more
    section-granularity ``Chunk``s.

    ``content_type="doc"`` is this CHUNK's own build-time ``content_type`` field
    (``document_schema.py``'s ``Document``/``Chunk`` dataclass field) - nothing downstream
    currently reads it back, but it must NOT be the literal string ``"example"``:
    ``citation.py``'s ``_is_real_compile_verified_example`` checks exactly
    ``chunk.content_type == "example"`` and would wrongly exempt this real, unverified doc
    prose from citation checking if it collided.

    Every returned chunk's ``text`` is prefixed with a pseudo-FQN line of the exact shape
    ``FQN: Doc: {candidate.title}\\n``, mirroring ``_build_verified_example_chunks``'s own
    ``FQN: Example: {candidate.title}\\n`` convention exactly. This is applied to each chunk
    AFTER ``chunk_document`` splits the candidate's body - not baked into the body passed to
    ``make_document`` - because a ``content``-origin candidate's own real markdown headings
    (e.g. ``### Installation``) make ``chunk_document`` split one candidate into several
    chunks, and any text placed before a document's first heading is dropped entirely by
    ``chunker._heading_sections`` (text before the first heading match is never captured).
    Post-processing every emitted chunk is the only way to guarantee EVERY chunk this function
    publishes carries the marker, not just the first one.

    Reason the marker is required at all, discovered live during TC-109's own execution:
    ``search_symbols.py``'s own exclusion of non-symbol chunks
    (``get_symbol.extract_fqn(text).startswith("Example: ")``) is the ONLY thing stopping a
    non-symbol chunk in this shared generation from being wrongly returned by
    ``search_symbols`` (and therefore by ``lookup()``'s bare-query dispatch, which tries
    ``search_symbols`` first) as if it were a real API symbol. Without this marker, every doc
    chunk this function publishes would silently corrupt ``search_symbols``/``lookup`` for
    every pilot. A separate, already-authored follow-up card (TC-120) extends
    ``search_symbols.py``'s own exclusion to also drop ``Doc: `` - it depends on this exact
    marker shape existing.
    """
    candidates = extract_doc_sections(page)

    doc_chunks: list[Chunk] = []
    for candidate in candidates:
        # TC-517: a FAQ keeps its answer, a lead-in keeps its list, tiny neighbours merge and anything
        # over the size limit is split with a ``Part: i of n`` marker instead of being cut.
        for chunk in chunk_doc_candidate(
            candidate,
            source_kind=SourceKind.FURNISHED,
            content_type="doc",
            provenance=Provenance(
                repository=args.library_repository,
                commit=args.library_commit,
                path=str(args.furnished_page),
            ),
            evidence_refs=(f"{args.library_repository}@{args.library_commit}",),
        ):
            doc_chunks.append(dataclasses.replace(chunk, text=f"FQN: Doc: {candidate.title}\n{chunk.text}"))

    print(f"built {len(doc_chunks)} doc chunks from {len(candidates)} doc candidates")
    return doc_chunks


_DEPRECATION_LINK_RE = re.compile(r"\{@link(?:plain)?\s+#?([\w.]+(?:#\w+)?(?:\([^)]*\))?)[^}]*\}")
_DEPRECATION_USE_RE = re.compile(
    r"\b(?:use|see|replaced by|replace with|prefer)\s+`?([A-Za-z_][\w.]*(?:\([^)]*\))?)`?", re.IGNORECASE
)


def _deprecation_replacement(doc: object) -> str:
    """TC-561: the name a deprecated member's doc comment points at (``{@link #getInfo()}``, "Use Bar
    instead"), or ``""`` when it names none."""
    if not isinstance(doc, str):
        return ""
    for pattern in (_DEPRECATION_LINK_RE, _DEPRECATION_USE_RE):
        match = pattern.search(doc)
        if match is not None:
            return match.group(1)
    return ""


def name_deprecation_replacements(fixture: dict) -> dict:
    """TC-561 (RD3-091): a deprecated member whose recorded reason is empty gets ``use <name>`` when its doc
    comment names the replacement (pdf/java ``Document.getOrCreateInfo`` is documented "Deprecated alias for
    {@link #getInfo()}"), so the chunk says what to call instead of only "(deprecated)". Reasons the extractor
    already recorded are never touched. Returns *fixture* itself when nothing changes, else a copy."""
    changed = False
    result = copy.deepcopy(fixture)
    for entry in result.get("types") or []:
        if not isinstance(entry, dict):
            continue
        for member in [entry, *(entry.get("methods") or []), *(entry.get("properties") or [])]:
            if not isinstance(member, dict) or member.get("deprecated") is not True:
                continue
            if member.get("deprecated_reason"):
                continue
            replacement = _deprecation_replacement(member.get("doc"))
            if replacement:
                member["deprecated_reason"] = f"use {replacement}"
                changed = True
    return result if changed else fixture


def _as_written_failures(fixture: Mapping[str, Any]) -> int:
    """TC-561: the number of source files that do not parse as written (before the indentation repair),
    from the fixture's ``parse_failures.as_written_failed``; 0 when it is absent, malformed or zero."""
    failures = fixture.get("parse_failures")
    if not isinstance(failures, Mapping):
        return 0
    count, total = failures.get("as_written_failed"), failures.get("total")
    if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
        return 0
    if not isinstance(total, int) or isinstance(total, bool) or total <= 0:
        return 0
    return count


def as_written_import_statement(as_written: int, total: int) -> str:
    """TC-561: the plain statement that a Python library cannot be imported at the indexed commit, with the
    real count of files that do not parse as written. One sentence group, no backtick, no blank line."""
    return (
        f"{as_written} of {total} source files do not parse as written; the API was recovered by a "
        "textual repair of the lost indentation; the library cannot be imported at this commit."
    )


def _coverage_fixture(fixture: dict) -> dict:
    """TC-561: the fixture the symbol chunks' coverage line is computed from. When files do not parse as
    written, the line counts those (35 of 45 for tex/python), not only the files the repair could not read
    (11 of 45); every other fixture is passed through unchanged."""
    as_written = _as_written_failures(fixture)
    if not as_written:
        return fixture
    return {**fixture, "parse_failures": {**fixture["parse_failures"], "failed": as_written}}


def _extraction_notice_chunks(fixture: dict) -> list[Chunk]:
    """TC-426 (REQ-G3-005): one explicit, searchable notice when the extractor could not read the source.

    A pilot whose pinned source does not parse silently looks like a small library: the symbols
    the extractor could not reach are simply absent. When the fixture's ``unresolved`` list holds
    a ``:syntax-error:`` entry, this returns exactly one ``doc`` chunk that names each unparseable
    file with its line and message and every public name that could not be extracted (the last
    segment of each ``unresolved-reexport`` target), and says that lookups for those names will
    answer not found. A fixture with no syntax-error entry yields ``[]``. The text carries no
    backticks and no blank line, so the chunker keeps it as one chunk and no renderer rewrites it.

    TC-555: a ``:repaired-syntax:`` entry means the file parsed only after its collapsed indentation
    was repaired; the notice says so, and says what that costs (the declarations and signatures are
    indexed, the function bodies and the docstrings of functions are not).
    """
    unresolved = [entry for entry in fixture.get("unresolved", []) if isinstance(entry, str)]
    syntax_errors = sorted({entry for entry in unresolved if ":syntax-error:" in entry})
    repaired = sorted({entry for entry in unresolved if ":repaired-syntax:" in entry})
    parse_failures: dict[str, Any] = fixture.get("parse_failures") or {}
    over_threshold = parse_failures_over_threshold(parse_failures)
    as_written = _as_written_failures(fixture)
    if not syntax_errors and not repaired and not over_threshold and not as_written:
        return []
    files: list[str] = []
    for entry in syntax_errors:
        before, _, after = entry.partition(":syntax-error:")
        line = before.rpartition(":")[2]
        path, _, message = after.partition(":")
        files.append(f"{path} line {line}: {message}")
    repaired_files: list[str] = []
    for entry in repaired:
        before, _, after = entry.partition(":repaired-syntax:")
        line = before.rpartition(":")[2]
        path, _, detail = after.partition(":")
        repaired_files.append(f"{path} line {line}: {detail}")
    names = sorted(
        {
            entry.partition(":unresolved-reexport:")[2].rsplit(".", 1)[-1]
            for entry in unresolved
            if ":unresolved-reexport:" in entry
        }
        - {""}
    )
    repository, commit = fixture["source_repository"], fixture["source_commit"]
    lines: list[str] = []
    if as_written:
        # TC-561: the files Python itself rejects, before any repair. The recovered API is real, but the
        # package cannot be imported, so the notice must not read as "a few files were skipped".
        lines.append(as_written_import_statement(as_written, parse_failures["total"]))
    if over_threshold:
        # No backtick and no blank line, so the chunker keeps the notice as ONE chunk.
        examples = [
            " ".join(str(item).replace("`", "'").split())
            for item in (parse_failures.get("examples") or [])[:5]
        ]
        if as_written:
            headline = (
                f"{parse_failures['failed']} of {parse_failures['total']} source files could not be read "
                "even after the repair"
            )
        else:
            headline = (
                f"{parse_failures['failed']} of {parse_failures['total']} source files could not be parsed"
            )
        lines.append(f"{headline} (examples: {', '.join(examples)})." if examples else f"{headline}.")
    if syntax_errors or over_threshold:
        lines.append(f"The source at {repository}@{commit} could not be fully read by the extractor.")
    else:
        lines.append(f"The source at {repository}@{commit} was read by the extractor with a repair.")
    if repaired_files:
        lines.append(
            "These files parsed only after their lost indentation was repaired: the declarations and "
            "signatures are indexed, the function bodies and the docstrings of functions are not:"
        )
        lines.extend(repaired_files)
    if files:
        lines.append("These files do not parse as valid source and were skipped:")
        lines.extend(files)
    if names:
        lines.append("Public names that could not be extracted: " + ", ".join(names) + ".")
        lines.append("Lookups for these names will answer not found.")
    doc = make_document(
        source_kind=SourceKind.SELF_EXTRACTED,
        content_type="doc",
        provenance=Provenance(repository=repository, commit=commit, path="api_surface.json"),
        evidence_refs=(f"{repository}@{commit}:api_surface.json",),
        title="Extraction notice",
        body="\n".join(lines),
    )
    return [
        dataclasses.replace(
            chunk,
            section_title="Extraction notice",
            text=f"FQN: Doc: Extraction notice\n{chunk.text}",
        )
        for chunk in chunk_document(doc)
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-surface", type=Path, required=True, help="raw extraction fixture JSON")
    parser.add_argument("--title", required=True)
    parser.add_argument("--max-types", type=int, default=300)
    parser.add_argument("--out", type=Path, required=True, help="where to write the --chunks JSON")
    parser.add_argument(
        "--furnished-page",
        type=Path,
        default=None,
        help="optional Hugo-bundle YAML page (e.g. tests/fixtures/furnished/pdf_net/pages/_index.md) "
        "to extract and real-compile-verify candidate examples from (REQ-G2-048)",
    )
    parser.add_argument("--library-repository", default=None, help="pinned reference library's repository")
    parser.add_argument("--library-commit", default=None, help="pinned reference library's commit")
    parser.add_argument(
        "--library-platform",
        choices=sorted(_PLATFORM_DISPATCH),
        default=None,
        help="which pilot's real prepare/verify pair (REQ-G2-048) to dispatch --furnished-page "
        "verification to",
    )
    parser.add_argument(
        "--library-csproj",
        default=None,
        help="pinned reference library's repository-relative csproj path (dotnet only)",
    )
    parser.add_argument(
        "--library-crate-name", default=None, help="pinned reference crate's package name (rust only)"
    )
    parser.add_argument(
        "--library-module-path", default=None, help="pinned reference module's Go module path (go only)"
    )
    parser.add_argument(
        "--library-package-name",
        default=None,
        help="pinned reference package's npm package name (typescript and nodejs only)",
    )
    parser.add_argument(
        "--library-java-packages",
        type=_java_packages_argument,
        default=None,
        help="comma-separated Java packages whose classes the README examples use "
        "(java only; opts in to the pilot mode of the Java verifier, TC-360)",
    )
    parser.add_argument(
        "--library-cpp-source-dir",
        default=None,
        help="repository-relative directory holding the C++ library's CMake project "
        "(cpp only; needs --library-cpp-target, TC-361)",
    )
    parser.add_argument(
        "--library-cpp-target",
        default=None,
        help="the C++ library's CMake target; opts in to the consumer-project mode (cpp only, TC-361)",
    )
    parser.add_argument(
        "--library-cpp-warnings",
        choices=("strict", "relaxed"),
        default=None,
        help="relaxed appends -Wno-error to the library target (cpp only; needs --library-cpp-target)",
    )
    parser.add_argument(
        "--library-cpp-verify-mode",
        choices=("run", "compile"),
        default=None,
        help="compile makes compile-and-link success the verdict and skips running (cpp only)",
    )
    parser.add_argument(
        "--library-cpp-compat",
        type=_cpp_compat_argument,
        default=None,
        help="comma-separated C++ compatibility switches (" + ", ".join(CPP_COMPAT_NAMES) + "); "
        "cpp only; needs --library-cpp-target (TC-455)",
    )
    parser.add_argument(
        "--verification-report",
        type=Path,
        default=None,
        help="where to write every candidate's verification result (verified or not, output in full) "
        "as a JSON array; defaults to --out with the suffix .verification.json (REQ-G2-048, TC-191)",
    )
    parser.add_argument(
        "--verification-cache",
        type=Path,
        default=None,
        help="a committed per-pilot verification cache (REQ-G3-007, TC-441) to reuse a matching "
        "verified: true verdict from, keyed by code hash, library commit, platform and toolchain; "
        "a missing file is an empty cache",
    )
    parser.add_argument(
        "--no-verification-cache",
        action="store_true",
        help="ignore every recorded verdict in --verification-cache: every candidate is really "
        "verified (TC-441)",
    )
    parser.add_argument(
        "--verification-cache-out",
        type=Path,
        default=None,
        help="write the verification cache merged with this run's own verdicts here (TC-441); "
        "written atomically and only when its bytes change",
    )
    args = parser.parse_args()

    if args.library_cpp_target is None and (
        args.library_cpp_source_dir is not None
        or args.library_cpp_warnings is not None
        or args.library_cpp_compat is not None
    ):
        parser.error(
            "--library-cpp-source-dir, --library-cpp-warnings and --library-cpp-compat "
            "require --library-cpp-target"
        )

    furnished_args = (
        args.furnished_page,
        args.library_repository,
        args.library_commit,
        args.library_platform,
    )
    if any(furnished_args) and not all(furnished_args):
        parser.error(
            "--furnished-page, --library-repository, --library-commit, and --library-platform "
            "must all be given together"
        )

    if args.furnished_page is not None:
        required_flag = _PLATFORM_REQUIRED_FLAGS.get(args.library_platform)
        if required_flag is not None:
            attr_name, flag_name = required_flag
            if getattr(args, attr_name) is None:
                parser.error(f"--library-platform {args.library_platform} requires {flag_name}")

    fixture = json.loads(args.api_surface.read_text(encoding="utf-8"))
    chunks = build_chunks_from_api_surface(
        _coverage_fixture(name_deprecation_replacements(fixture)), title=args.title, max_types=args.max_types
    )
    chunks = chunks + _extraction_notice_chunks(fixture)

    verification_records: list[dict] = []
    if args.furnished_page is not None:
        # Note: _build_verified_example_chunks loads --furnished-page itself internally, and
        # it is explicitly out of scope for this card to touch (REQ-G2-047/TC-112) - so this
        # is a real, deliberately-accepted duplicate parse, not an oversight.
        page = _load_furnished_page(args.furnished_page)
        report_path = args.verification_report or args.out.with_suffix(".verification.json")

        # REQ-G3-007 (TC-441): both cache flags are optional and inert when absent - with neither
        # given, everything below is skipped and behaviour is byte-identical to before this cache
        # existed.
        cache_enabled = args.verification_cache is not None or args.verification_cache_out is not None
        existing_cache_entries: list[dict] = []
        cache_lookup: dict[tuple[str, str, str, str, str], dict] | None = None
        toolchain: str | None = None
        new_cache_entries: list[dict] = []
        if cache_enabled:
            if args.verification_cache is not None:
                try:
                    existing_cache_entries = load_verification_cache(args.verification_cache)
                except ValueError as exc:
                    print(str(exc), file=sys.stderr)
                    sys.exit(2)
            toolchain = toolchain_id(args.library_platform)
            if toolchain is None:
                print(
                    f"verification cache: no {args.library_platform} toolchain detected; "
                    "nothing will be reused or recorded this run",
                    file=sys.stderr,
                )
            else:
                cache_lookup = {}
                if not args.no_verification_cache:
                    cache_lookup = {
                        _cache_entry_key(entry): entry
                        for entry in existing_cache_entries
                        if entry.get("verified") is True
                    }

        try:
            try:
                chunks = (
                    chunks
                    + _build_verified_example_chunks(
                        args,
                        verification_records,
                        verification_cache=cache_lookup,
                        toolchain=toolchain,
                        new_cache_entries=new_cache_entries,
                    )
                    + _build_doc_chunks(page, args)
                )
            except ExampleEnvironmentError as exc:
                # TC-184: an environment failure is not a verdict on any candidate. End the build
                # here with a distinct exit code and write no chunks, so the candidate is neither
                # published nor recorded as not verified.
                print(
                    f"environment failure: {exc.tool} could not reach its registry "
                    f"(marker {exc.marker!r}); build stopped, no chunks written",
                    file=sys.stderr,
                )
                sys.exit(ENVIRONMENT_FAILURE_EXIT)
        finally:
            # TC-191: the report is written on every exit from this block (success, environment
            # failure, or any other error), so the verdicts already reached are never lost.
            _write_verification_report(report_path, verification_records)
            # TC-441: likewise, the merged cache is written on every exit, so a run that ends with
            # an environment failure or a configuration error never loses a verdict a previous run
            # already recorded.
            if cache_enabled and args.verification_cache_out is not None:
                merged_cache = merge_verification_cache(existing_cache_entries, new_cache_entries)
                write_verification_cache_if_changed(args.verification_cache_out, merged_cache)

    # TC-385: sanitise first, so the Source-Commit line is always the last line and is never cut.
    chunks = [
        _with_source_commit_line(dataclasses.replace(chunk, text=_sanitize_chunk_text(chunk.text)))
        for chunk in chunks
    ]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps({"chunks": [dataclasses.asdict(chunk) for chunk in chunks]}, indent=2),
        encoding="utf-8",
    )
    print(f"wrote {len(chunks)} chunks to {args.out}")


if __name__ == "__main__":
    main()
