"""Real, isolated compile/execute-verification sandbox for candidate
examples (REQ-G2-048).

This module never trusts a candidate example's plausibility. It clones the
exact pinned upstream commit already indexed, builds (or, for Python,
installs) the reference library once, then compiles or runs each
candidate's real code in a throwaway, disposable project/environment that
references that library. The verdict is the real toolchain's exit code,
nothing else.

Originally proven for pdf/net's .NET toolchain
(``prepare_reference_library``/``verify_dotnet_example``, untouched below).
Extended to three more pilots with the same git-fetch plumbing and the same
prepare/verify split: Python (slides/python), Rust (cells/rust), and Go
(pdf/go). Extended again here to the last two pilots with a real extraction
fixture: Java (pdf/java) and TypeScript (pdf/typescript) - completing 6 of 7.
Extended a final time to the 7th and last pilot, C++ (cells/cpp) - the only
pilot with no prior verifier at all, since it needs a real C/C++ compiler
toolchain rather than a language-native package manager. A sibling project
on this same machine (repository-presenter) already installed and verified
a working MSVC toolchain for this exact repository
(``C:\\tools\\rp-toolchains\\TOOLCHAIN_PATHS.txt``); GCC and Clang (also
available via that same registry) both FAIL on this repo's own source (a
trigraph literal in ``NumberFormat.cpp`` trips ``-Werror=trigraphs`` under
their non-MSVC compile flags), confirmed real and not a preference.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tomllib
import urllib.request
import xml.etree.ElementTree as ElementTree
import zipfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from foss_mcp.indexing.example_candidates import CandidateExample

__all__ = [
    "VerificationResult",
    "ExampleEnvironmentError",
    "prepare_reference_library",
    "verify_dotnet_example",
    "prepare_python_library",
    "verify_python_example",
    "prepare_rust_library",
    "verify_rust_example",
    "prepare_go_library",
    "verify_go_example",
    "prepare_java_library",
    "verify_java_example",
    "prepare_typescript_library",
    "verify_typescript_example",
    "prepare_nodejs_library",
    "verify_nodejs_example",
    "prepare_cpp_library",
    "verify_cpp_example",
    "prepare_cpp_library_linux",
    "verify_cpp_example_linux",
]

_OUTPUT_TRUNCATE_CHARS = 8000
# The part of a long toolchain output kept from its start; the rest of the budget keeps its end.
_OUTPUT_HEAD_CHARS = 1500

# Recorded as the output of a block that holds nothing but a preamble (for .NET, using
# directives). Nothing is compiled or run for such a block, so it can never count as verified.
_NO_STATEMENTS_MESSAGE = (
    "[foss-mcp] rejected: the block has no statements of its own "
    "(only {kind}, comments or blank lines); nothing was compiled or run"
)

# The target framework used when the library's own project files declare no usable one.
_DEFAULT_DOTNET_TARGET_FRAMEWORK = "net8.0"

_CANDIDATE_CSPROJ_TEMPLATE = """<Project Sdk="Microsoft.NET.Sdk">

  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>{target_framework}</TargetFramework>
    <ImplicitUsings>enable</ImplicitUsings>
  </PropertyGroup>

  <ItemGroup>
    <ProjectReference Include="{library_csproj}" />
  </ItemGroup>

</Project>
"""

_CANDIDATE_CARGO_TOML_TEMPLATE = """[package]
name = "candidate"
version = "0.0.0"
edition = "2021"

[dependencies]
{crate_name} = {{ path = "{library_dir}" }}
"""

_CANDIDATE_GO_MOD_TEMPLATE = """module candidate

go 1.24

require {module_path} {required_version}

replace {module_path} => {library_dir}
"""

# A Go module path whose last element is ``vN`` (N an integer of at least 2, no leading zero), or
# that ends in the gopkg.in style ``.vN``, is a major-version-suffixed path.
_GO_MAJOR_SUFFIX_RE = re.compile(r"(?:/|\.)v([1-9][0-9]*)$")


def _go_required_version(module_path: str) -> str:
    """Return the version the throwaway go.mod must ``require`` ``module_path`` at.

    Go refuses to require a module whose path ends in a major-version suffix at a version of a
    different major (``.../v26`` at ``v0.0.0`` fails with "should be v26, not v0"). A path ending
    in ``/vN`` or ``.vN`` with N an integer of at least 2 therefore gets ``vN.0.0``; every other
    path (no suffix, ``/v1``, ``/v0``, ``/version``, ``/v26x``, an uppercase ``/V26``) keeps
    ``v0.0.0``. A trailing slash is ignored.
    """
    match = _GO_MAJOR_SUFFIX_RE.search(module_path.rstrip("/"))
    if match is None:
        return "v0.0.0"
    major = int(match.group(1))
    if major < 2:
        return "v0.0.0"
    return f"v{major}.0.0"


# Every verifier subprocess is bounded: a README block that never returns (a server's ``run()``) or a
# stuck install must not hang the whole ingestion Job. A killed command is recorded as a timeout.
_RUN_TIMEOUT_SECONDS = 1800
_PYTHON_CANDIDATE_TIMEOUT_SECONDS = 120
_TIMEOUT_EXIT_CODE = 124
_KILL_GRACE_SECONDS = 10


def _kill_process_tree(proc: subprocess.Popen[str]) -> None:
    """Kill ``proc`` and every process it started."""
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=_KILL_GRACE_SECONDS,
            )
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            proc.kill()
        except OSError:
            pass
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _run_bounded(
    args: list[str],
    *,
    cwd: Path,
    timeout_seconds: float,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``args`` and give up after ``timeout_seconds``, killing the whole process tree.

    A command that overruns is killed and reported with return code ``_TIMEOUT_EXIT_CODE`` and a
    final stderr line ``[foss-mcp] TIMEOUT: ...``, so a hang can never read as a pass.
    """
    proc = subprocess.Popen(
        args,
        cwd=cwd,
        env=None if env is None else dict(env),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=sys.platform != "win32",
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)
        try:
            stdout, stderr = proc.communicate(timeout=_KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
        stdout = stdout or ""
        stderr = stderr or ""
        if stderr and not stderr.endswith("\n"):
            stderr += "\n"
        stderr += f"[foss-mcp] TIMEOUT: {Path(args[0]).name} was killed after {timeout_seconds} seconds"
        return subprocess.CompletedProcess(args, _TIMEOUT_EXIT_CODE, stdout=stdout, stderr=stderr)
    return subprocess.CompletedProcess(args, proc.returncode, stdout=stdout, stderr=stderr)


def _run(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return _run_bounded(args, cwd=cwd, timeout_seconds=_RUN_TIMEOUT_SECONDS)


# Cargo's crates.io index download is the one network step in the Rust pilot
# that can stall transiently (observed: "transfer too slow: failed to transfer
# more than 10 bytes in 30s"). Every cargo invocation goes through
# _run_cargo, which sets this policy in the subprocess environment so it cannot
# silently disappear from a call site.
_CARGO_NET_RETRY = "5"
_CARGO_HTTP_TIMEOUT = "120"


def _cargo_environment() -> dict[str, str]:
    """Return the environment for a cargo subprocess: this process's own
    environment plus the explicit retry and timeout policy.
    """
    env = dict(os.environ)
    env["CARGO_NET_RETRY"] = _CARGO_NET_RETRY
    env["CARGO_HTTP_TIMEOUT"] = _CARGO_HTTP_TIMEOUT
    return env


def _decode_partial_output(value: str | bytes | None) -> str:
    """Return the partial output a killed process carried: decoded if bytes, empty if None."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _run_cargo_subprocess(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run one cargo command bounded by ``_RUN_TIMEOUT_SECONDS``.

    A cargo that overruns is killed and reported with return code ``_TIMEOUT_EXIT_CODE``, the
    partial output it produced and a final ``[foss-mcp] TIMEOUT`` stderr line. That line is not a
    network marker, so a timed-out build is simply not verified.
    """
    try:
        return subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            env=_cargo_environment(),
            timeout=_RUN_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as expired:
        stdout = _decode_partial_output(expired.stdout)
        stderr = _decode_partial_output(expired.stderr)
        if stderr and not stderr.endswith("\n"):
            stderr += "\n"
        stderr += f"[foss-mcp] TIMEOUT: cargo was killed after {_RUN_TIMEOUT_SECONDS} seconds"
        return subprocess.CompletedProcess(args, _TIMEOUT_EXIT_CODE, stdout=stdout, stderr=stderr)


# Offline fallback. The reference crate has no Cargo.lock, so every fresh clone
# makes cargo refresh the crates.io index even when each crate is already in the
# local cache; a short registry outage then fails a build that needs no network.
# When a cargo run fails with a network marker and no rustc diagnostic, _run_cargo
# runs the same command once more with this flag. A run that carries a rustc
# diagnostic is a genuine compile failure and is never retried.
_CARGO_OFFLINE_FLAG = "--offline"


def _run_cargo(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run one cargo command, retrying it once offline after a network failure.

    This is the only place a cargo subprocess is started. The first run's result
    is returned unless it failed with a network marker and no rustc diagnostic
    (``error[E<digits>]``); only then is the same command run once more with the
    offline flag appended, and that second result is returned whether it
    succeeded or not. A cold cache therefore still fails on the second run, and
    the caller classifies that result exactly as it would any other.
    """
    first = _run_cargo_subprocess(args, cwd=cwd)
    if first.returncode == 0:
        return first
    output_text = f"{first.stdout}\n{first.stderr}"
    if _RUSTC_DIAGNOSTIC.search(output_text) is not None:
        return first
    if _match_network_failure_marker(output_text) is None:
        return first
    return _run_cargo_subprocess([*args, _CARGO_OFFLINE_FLAG], cwd=cwd)


def _truncate(text: str) -> str:
    """Keep at most ``_OUTPUT_TRUNCATE_CHARS`` characters of a toolchain output.

    Longer text keeps its first ``_OUTPUT_HEAD_CHARS`` characters, a line stating exactly how many
    characters were dropped, and its last ``_OUTPUT_TRUNCATE_CHARS - _OUTPUT_HEAD_CHARS``
    characters. The end of a compiler run carries its real error, so the end must survive the
    library warnings that precede it.
    """
    if len(text) <= _OUTPUT_TRUNCATE_CHARS:
        return text
    tail_chars = _OUTPUT_TRUNCATE_CHARS - _OUTPUT_HEAD_CHARS
    dropped = len(text) - _OUTPUT_HEAD_CHARS - tail_chars
    head = text[:_OUTPUT_HEAD_CHARS]
    tail = text[len(text) - tail_chars :]
    return f"{head}\n...[truncated {dropped} characters]...\n{tail}"


_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _has_own_statements(
    code: str, *, preamble_line: re.Pattern[str] | None, line_comment: str = "//"
) -> bool:
    """Return True when ``code`` holds a line that is neither a preamble line nor a comment.

    Block comments (``/* ... */``, across lines) are removed first when ``line_comment`` is ``//``.
    A line then counts as "not a statement" when, stripped, it is empty, starts with
    ``line_comment``, or (with any trailing line comment removed) fully matches ``preamble_line``
    (``None`` means there is no preamble). Any other line is a statement of the block's own.
    """
    if line_comment == "//":
        code = _BLOCK_COMMENT_RE.sub("", code)
    for raw_line in code.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(line_comment):
            continue
        if preamble_line is not None:
            without_comment = line.split(line_comment, 1)[0].strip()
            if preamble_line.fullmatch(without_comment) is not None:
                continue
        return True
    return False


# Cargo output that marks a failed run as an infrastructure failure (the
# registry or the network could not be reached), not a compile failure. Matched
# as plain substrings against a non-zero run's stdout+stderr. Kept in exactly
# one place: the classifier below reads it, and so do the tests.
_CARGO_NETWORK_FAILURE_MARKERS: tuple[str, ...] = (
    "spurious network error",
    "Could not resolve hostname",
    "SSL connect error",
    "download of config.json failed",
    "transfer too slow",
)


class ExampleEnvironmentError(RuntimeError):
    """A candidate could not be verified because its toolchain could not reach
    the package registry. This is an environment failure, never a verdict on
    the candidate's code, so it is raised rather than returned as
    ``VerificationResult(verified=False)``.
    """

    def __init__(self, tool: str, marker: str, output: str) -> None:
        self.tool = tool
        self.marker = marker
        self.output = output
        super().__init__(
            f"{tool} could not reach its registry (matched {marker!r}); this is an "
            "environment failure, not a verdict on the candidate"
        )


# A rustc diagnostic line, e.g. ``error[E0599]: no method named ...``. Cargo
# prints the network retry warning ("spurious network error (N tries
# remaining)") on real compile runs too, so a marker alone does not prove the
# run was infrastructure. A run that carries a rustc diagnostic is a compile
# failure, whatever markers it also prints.
_RUSTC_DIAGNOSTIC = re.compile(r"error\[E\d+\]")


def _match_network_failure_marker(output: str) -> str | None:
    """Return the first network-failure marker found in ``output``, else None."""
    for marker in _CARGO_NETWORK_FAILURE_MARKERS:
        if marker in output:
            return marker
    return None


@dataclass(frozen=True)
class VerificationResult:
    """The real, compiler-determined outcome of verifying one candidate."""

    candidate: CandidateExample
    verified: bool
    output: str


# TC-456 (REQ-G3-007): words/net's packages/ (Aspose.JavaAttributes, Aspose.CodePorting) are
# Git LFS objects, declared by `filter=lfs` in the repository's root .gitattributes. A clone
# without git-lfs - or one where the LFS host is unreachable - leaves 129-byte pointer files in
# their place, and `dotnet build` then fails with thousands of missing-type errors that say
# nothing about the real cause. Every failure below is a RuntimeError (never
# ExampleEnvironmentError: one pilot's missing binary packages must never cost it its
# documentation, the whole ingestion Job included) whose message starts with exactly this
# prefix, so build_chunks.py's degradation path records one clear reason instead.
LFS_UNAVAILABLE_REASON = "not verified (LFS unavailable)"
_LFS_INCLUDE_PATTERN = "packages/**"
_LFS_POINTER_PREFIX = "version https://git-lfs.github.com/spec/v1"
_LFS_POINTER_HEAD_BYTES = len(_LFS_POINTER_PREFIX) + 16


def _repository_declares_lfs(workdir: Path) -> bool:
    """Return True when ``<workdir>/.gitattributes`` declares an LFS filter.

    A repository without ``filter=lfs`` runs exactly today's five commands, in today's order -
    no .gitattributes file at all included.
    """
    gitattributes = workdir / ".gitattributes"
    if not gitattributes.exists():
        return False
    try:
        text = gitattributes.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "filter=lfs" in text


def _count_remaining_lfs_pointers(packages_dir: Path) -> int:
    """Return how many files under ``packages_dir`` are still LFS pointer files."""
    if not packages_dir.exists():
        return 0
    remaining = 0
    for path in sorted(packages_dir.rglob("*")):
        if not path.is_file():
            continue
        try:
            with path.open("rb") as handle:
                head = handle.read(_LFS_POINTER_HEAD_BYTES)
        except OSError:
            continue
        if head.decode("utf-8", errors="replace").startswith(_LFS_POINTER_PREFIX):
            remaining += 1
    return remaining


def _pull_lfs_packages(workdir: Path) -> None:
    """Fetch exactly the ``packages/**`` LFS objects into ``workdir``, after checkout.

    Runs ``git lfs install --local`` then ``git lfs pull --include packages/**``, both with
    ``cwd=workdir``. Raises ``RuntimeError`` (never ``ExampleEnvironmentError``), its message
    starting with ``LFS_UNAVAILABLE_REASON``, when: git-lfs is not installed (the install step
    fails); the pull itself fails (network, LFS host unreachable); or the pull exits zero but a
    pointer file is still present under ``<workdir>/packages/``.
    """
    install_result = _run(["git", "lfs", "install", "--local"], cwd=workdir)
    if install_result.returncode != 0:
        cause = f"{install_result.stdout}\n{install_result.stderr}".strip()
        raise RuntimeError(f"{LFS_UNAVAILABLE_REASON}: {cause}")

    pull_result = _run(["git", "lfs", "pull", "--include", _LFS_INCLUDE_PATTERN], cwd=workdir)
    if pull_result.returncode != 0:
        combined = f"{pull_result.stdout}\n{pull_result.stderr}"
        raise RuntimeError(f"{LFS_UNAVAILABLE_REASON}: {combined[-600:]}")

    remaining = _count_remaining_lfs_pointers(workdir / "packages")
    if remaining:
        raise RuntimeError(
            f"{LFS_UNAVAILABLE_REASON}: {remaining} file(s) under packages/ are still LFS "
            "pointer files after `git lfs pull`"
        )


def prepare_reference_library(
    repository: str,
    commit: str,
    csproj_relative_path: str,
    workdir: Path,
) -> Path:
    """Shallow-clone ``repository`` into ``workdir`` at exactly ``commit``,
    build the reference library once, and return the built csproj's path.

    A plain shallow clone only gets the default branch tip, so the specific
    historical commit is fetched explicitly: ``git init`` + ``git remote
    add`` + ``git fetch --depth 1 origin <commit>`` + ``git checkout
    <commit>``. When the checked-out repository declares LFS (``filter=lfs``
    in its root ``.gitattributes``), the ``packages/**`` LFS objects are
    pulled next (TC-456, REQ-G3-007) - a repository without LFS runs exactly
    today's five commands, in today's order.

    Raises ``RuntimeError`` on any real failure (clone, fetch, checkout,
    build, or an LFS pull) - the reference library itself must always build
    clean, and a failure here is a real, reportable defect, never silently
    swallowed.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    repo_url = f"https://github.com/{repository}.git"

    init_result = _run(["git", "init"], cwd=workdir)
    if init_result.returncode != 0:
        raise RuntimeError(f"git init failed in {workdir}:\n{init_result.stdout}\n{init_result.stderr}")

    remote_result = _run(["git", "remote", "add", "origin", repo_url], cwd=workdir)
    if remote_result.returncode != 0:
        raise RuntimeError(
            f"git remote add failed for {repo_url}:\n{remote_result.stdout}\n{remote_result.stderr}"
        )

    fetch_result = _run(["git", "fetch", "--depth", "1", "origin", commit], cwd=workdir)
    if fetch_result.returncode != 0:
        raise RuntimeError(
            f"git fetch of commit {commit} from {repo_url} failed:\n"
            f"{fetch_result.stdout}\n{fetch_result.stderr}"
        )

    checkout_result = _run(["git", "checkout", commit], cwd=workdir)
    if checkout_result.returncode != 0:
        raise RuntimeError(
            f"git checkout of commit {commit} failed:\n{checkout_result.stdout}\n{checkout_result.stderr}"
        )

    if _repository_declares_lfs(workdir):
        _pull_lfs_packages(workdir)

    csproj_path = workdir / csproj_relative_path
    build_result = _run(["dotnet", "build", str(csproj_path)], cwd=workdir)
    if build_result.returncode != 0:
        raise RuntimeError(
            f"reference library at {repository}@{commit} "
            f"({csproj_relative_path}) failed to build cleanly:\n"
            f"{build_result.stdout}\n{build_result.stderr}"
        )

    return csproj_path


# A C# using DIRECTIVE line (``using Ns;``, ``using static T;``, ``using Alias = A.B;``). The using
# STATEMENTS ``using var x = ...;``, ``using (var x = ...)`` and ``using T x = ...;`` do not match.
_DOTNET_USING_DIRECTIVE_RE = re.compile(
    r"^[ \t]*using[ \t]+(?:static[ \t]+)?(?:[A-Za-z_]\w*[ \t]*=[ \t]*)?[A-Za-z_][\w.]*[ \t]*;",
    re.MULTILINE,
)

_DOTNET_TFM_RE = re.compile(r"^net(\d+)\.(\d+)$")
_XML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_DOTNET_TFM_ELEMENT_RE = re.compile(r"<TargetFrameworks?>([^<]*)</TargetFrameworks?>")


def _tfms_declared_in(path: Path) -> list[str]:
    """The ``net<major>.<minor>`` values declared in one csproj or props file; unreadable files
    declare none. Property references, netstandard, netcoreapp and OS-suffixed values are ignored.
    """
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return []
    text = _XML_COMMENT_RE.sub("", text)
    found: list[str] = []
    for element_value in _DOTNET_TFM_ELEMENT_RE.findall(text):
        for part in element_value.split(";"):
            value = part.strip()
            if _DOTNET_TFM_RE.match(value) is not None:
                found.append(value)
    return found


def _library_target_framework(library_csproj: Path) -> tuple[str, str]:
    """Return ``(target framework, where it came from)`` for the library project.

    Reads ``<TargetFramework>``/``<TargetFrameworks>`` from the csproj and from every
    ``Directory.Build.props`` in its directory and parent directories, walking upward and stopping
    after the first directory that holds a ``.git`` entry (nothing above the clone root is read).
    The highest ``net<major>.<minor>`` by numeric version wins and is reported as ``"library"``;
    with none, ``(_DEFAULT_DOTNET_TARGET_FRAMEWORK, "default")``.
    """
    found: list[str] = _tfms_declared_in(library_csproj)
    directory = library_csproj.resolve().parent
    while True:
        found.extend(_tfms_declared_in(directory / "Directory.Build.props"))
        if (directory / ".git").exists() or directory.parent == directory:
            break
        directory = directory.parent
    if not found:
        return _DEFAULT_DOTNET_TARGET_FRAMEWORK, "default"

    def version(value: str) -> tuple[int, int]:
        match = _DOTNET_TFM_RE.match(value)
        assert match is not None
        return int(match.group(1)), int(match.group(2))

    return max(found, key=version), "library"


def _collapse_whitespace(text: str) -> str:
    return " ".join(text.split())


def _dotnet_page_usings(page_code: tuple[str, ...]) -> tuple[str, ...]:
    """Every using directive of every page block, in document order, de-duplicated.

    A directive is the matched directive text, stripped; a trailing comment is not part of it.
    """
    seen: set[str] = set()
    directives: list[str] = []
    for block in page_code:
        for match in _DOTNET_USING_DIRECTIVE_RE.finditer(block):
            directive = match.group(0).strip()
            key = _collapse_whitespace(directive)
            if key in seen:
                continue
            seen.add(key)
            directives.append(directive)
    return tuple(directives)


def _assemble_dotnet_source(code: str, page_code: tuple[str, ...]) -> tuple[str, tuple[str, ...]]:
    """Put the page's using directives that ``code`` lacks, one per line, in front of ``code``.

    Directives are compared with runs of whitespace collapsed. Returns the source and the
    directives that were added.
    """
    own = {_collapse_whitespace(directive) for directive in _dotnet_page_usings((code,))}
    added = tuple(
        directive
        for directive in _dotnet_page_usings(page_code)
        if _collapse_whitespace(directive) not in own
    )
    if not added:
        return code, ()
    return "\n".join(added) + "\n" + code, added


def verify_dotnet_example(
    candidate: CandidateExample,
    *,
    library_csproj: Path,
    workdir: Path,
) -> VerificationResult:
    """Compile ``candidate.code`` as top-level statements in a throwaway
    console project referencing ``library_csproj``, and report the real
    compiler outcome.

    The way a reader of the page would compile the block: the project targets the library's own
    target framework with implicit usings on, and the using directives the page wrote once
    (``candidate.page_code``) are put in front of a block that lacks them. A block that holds
    only using directives is rejected without compiling. Every assembly step is written as the
    first line of the output. The result carries the original ``candidate``.

    The literal comparison determining ``verified`` is intentionally exact
    (``verified = result.returncode == 0``) so that forcing it to always be
    true is a detectable, meaningful negative control.
    """
    if not _has_own_statements(candidate.code, preamble_line=_DOTNET_USING_DIRECTIVE_RE):
        return VerificationResult(
            candidate=candidate,
            verified=False,
            output=_NO_STATEMENTS_MESSAGE.format(kind="using directives"),
        )

    project_dir = workdir / "candidate_project"
    project_dir.mkdir(parents=True, exist_ok=True)

    target_framework, framework_source = _library_target_framework(library_csproj)
    source, added_usings = _assemble_dotnet_source(candidate.code, candidate.page_code)

    csproj_content = _CANDIDATE_CSPROJ_TEMPLATE.format(
        library_csproj=library_csproj, target_framework=target_framework
    )
    (project_dir / "candidate.csproj").write_text(csproj_content, encoding="utf-8")
    (project_dir / "Program.cs").write_text(source, encoding="utf-8")

    result = _run(["dotnet", "build"], cwd=project_dir)

    verified = result.returncode == 0
    prepended = ", ".join(added_usings) or "none"
    assembly_line = (
        f"[foss-mcp] assembly: dotnet target-framework={target_framework} ({framework_source}); "
        f"implicit-usings=enable; page-usings-prepended={prepended}"
    )
    combined = f"{result.stdout}\n{result.stderr}"
    output = f"{assembly_line}\n{_truncate(combined)}"

    return VerificationResult(candidate=candidate, verified=verified, output=output)


# --- cells/cpp ---------------------------------------------------------------
#
# Real, hands-on toolchain facts confirmed today by cloning
# aspose-cells-foss/Aspose.Cells-FOSS-for-Cpp at its pinned commit and
# actually running cmake -G Ninja + ninja against it with MSVC:
#
#   - The pinned repo has NO top-level CMakeLists.txt; the library's own
#     CMakeLists.txt (project `aspose_cells_foss_cpp`, library target
#     `aspose_cells_foss`, a STATIC library) lives in the
#     ``Aspose.Cells.Foss.Cpp`` subdirectory. Its own public headers live
#     under ``Aspose.Cells.Foss.Cpp/include/aspose/cells_foss/*.h`` - the
#     real, nested include layout TC-094's extraction run had already
#     confirmed.
#   - Configuring with no CMAKE_BUILD_TYPE set (Ninja's single-config
#     default) makes the project's own toolchain-detect.cmake select MSVC's
#     debug, dynamic runtime (``/MDd``). A candidate compiled with cl.exe's
#     own bare default (``/MT``, static release) then fails to LINK against
#     it with LNK2038 runtime-library mismatches - not a candidate code
#     defect. Building with ``-DCMAKE_BUILD_TYPE=Release`` (MSVC's dynamic
#     release runtime, ``/MD``) and compiling every candidate with ``/MD``
#     keeps both sides in agreement; confirmed by a real link that only
#     succeeds once both are ``/MD``.
#   - The real output artifact is a plain ``.lib`` file at the build
#     directory's own root (Ninja's single-config generator does not nest
#     output under a per-configuration subdirectory the way the Visual
#     Studio generator would): ``aspose_cells_foss.lib``.
#   - Building the bare ``aspose_cells_foss`` target (rather than Ninja's
#     default "all") builds only the library's own ~190 object/link steps,
#     skipping the project's own ``tests`` subdirectory the top-level
#     CMakeLists.txt also enables - keeping this fast.
#   - The real furnished content at
#     ``tests/fixtures/furnished/cells_cpp/pages/_index.md`` is a complete,
#     self-contained snippet with its own ``#include``s and ``int main()``
#     - not a bare statement block needing a wrapper, unlike pdf/java's own
#     furnished content. No wrapping is done here; a candidate is compiled
#     exactly as given. (That real snippet is itself missing a needed
#     ``#include "aspose/cells_foss/WorksheetCollection.h"`` - ``Workbook.h``
#     only forward-declares that class, so ``WorksheetCollection::operator[]``
#     is not visible from it alone - which a real MSVC compile of it
#     genuinely catches; this module does not paper over that, since a
#     candidate that fails to compile as furnished is exactly what
#     verification exists to catch.)

# Nothing below is guessed. vswhere (the Visual Studio Installer's own
# locator) finds the installation, and vcvarsall.bat is taken from that
# installation. ninja and cmake are taken from the toolchain root, which is
# FOSS_MCP_TOOLS if set, else _DEFAULT_FOSS_MCP_TOOLS. A tool that cannot be
# resolved raises RuntimeError naming the tool, the place searched, and the
# override variable - it is never returned as a path that does not exist.
_CPP_VSWHERE_DIR = Path(r"C:\Program Files (x86)\Microsoft Visual Studio\Installer")
_VSWHERE_EXE_NAME = "vswhere.exe"
_VS_REQUIRED_COMPONENT = "Microsoft.VisualStudio.Component.VC.Tools.x86.x64"
_VCVARSALL_RELATIVE_PATH = Path("VC") / "Auxiliary" / "Build" / "vcvarsall.bat"

_FOSS_MCP_TOOLS_ENV = "FOSS_MCP_TOOLS"
_DEFAULT_FOSS_MCP_TOOLS = Path(r"C:\dev-tools\foss-mcp")
_NINJA_EXE_NAME = "ninja.exe"
_NINJA_RELATIVE_DIR = Path("rp-toolchains") / "ninja"
_CMAKE_EXE_NAME = "cmake.exe"
_CMAKE_RELATIVE_DIR = Path("cmake") / "bin"

_CPP_LIBRARY_SUBDIR = "Aspose.Cells.Foss.Cpp"
_CPP_LIBRARY_TARGET = "aspose_cells_foss"
_CPP_LIBRARY_ARTIFACT_NAME = "aspose_cells_foss.lib"
_CPP_BUILD_DIRNAME = "_build"

_CL_EXE_PATH_RE = re.compile(r"^(.*\bcl\.exe)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class _MsvcEnvironment:
    """The real, subprocess-scoped MSVC x64 environment - never written to
    this machine's actual PATH/environment.
    """

    env: dict[str, str]
    cl_exe: Path


def _toolchain_root() -> Path:
    """Return the toolchain root: ``FOSS_MCP_TOOLS`` if set and non-empty,
    else ``_DEFAULT_FOSS_MCP_TOOLS``.
    """
    override = os.environ.get(_FOSS_MCP_TOOLS_ENV, "").strip()
    return Path(override) if override else _DEFAULT_FOSS_MCP_TOOLS


def _resolve_toolchain_exe(exe_name: str, relative_dir: Path, tool_label: str) -> Path:
    """Return ``<toolchain root>/<relative_dir>/<exe_name>`` if it is a real
    file; otherwise raise ``RuntimeError`` naming the tool, the searched
    path, and the override variable.
    """
    root = _toolchain_root()
    candidate = root / relative_dir / exe_name
    if not candidate.is_file():
        raise RuntimeError(
            f"{tool_label} ({exe_name}) not found at {candidate}; searched under toolchain "
            f"root {root}. Install it there or set {_FOSS_MCP_TOOLS_ENV} to the toolchain root."
        )
    return candidate


def _resolve_ninja() -> Path:
    return _resolve_toolchain_exe(_NINJA_EXE_NAME, _NINJA_RELATIVE_DIR, "ninja")


def _resolve_cmake() -> Path:
    return _resolve_toolchain_exe(_CMAKE_EXE_NAME, _CMAKE_RELATIVE_DIR, "cmake")


def _resolve_vcvarsall() -> Path:
    """Return the vcvarsall.bat of the Visual Studio installation that
    vswhere reports (latest, with the x64 C++ tools component).

    Raises ``RuntimeError`` naming vswhere, the searched folder, or the
    reported installation path if any step does not yield a real file.
    """
    vswhere = _CPP_VSWHERE_DIR / _VSWHERE_EXE_NAME
    if not vswhere.is_file():
        raise RuntimeError(
            f"vswhere ({_VSWHERE_EXE_NAME}) not found at {vswhere}; it is installed with the "
            "Visual Studio Installer. Install Visual Studio Build Tools to provide it."
        )

    query = _run(
        [
            str(vswhere),
            "-latest",
            "-products",
            "*",
            "-requires",
            _VS_REQUIRED_COMPONENT,
            "-property",
            "installationPath",
        ],
        cwd=_CPP_VSWHERE_DIR,
    )
    install_lines = [line.strip() for line in query.stdout.splitlines() if line.strip()]
    if query.returncode != 0 or not install_lines:
        raise RuntimeError(
            f"vswhere found no Visual Studio installation with {_VS_REQUIRED_COMPONENT} "
            f"(exit {query.returncode}):\n{query.stdout}\n{query.stderr}"
        )

    vcvarsall = Path(install_lines[0]) / _VCVARSALL_RELATIVE_PATH
    if not vcvarsall.is_file():
        raise RuntimeError(
            f"vcvarsall.bat (MSVC developer environment) not found at {vcvarsall}, "
            f"under the installation vswhere reported: {install_lines[0]}"
        )
    return vcvarsall


def _msvc_environment(workdir: Path) -> _MsvcEnvironment:
    """Activate the real MSVC x64 developer environment by running
    ``vcvarsall.bat x64`` (located through vswhere by ``_resolve_vcvarsall``)
    in a disposable ``cmd.exe`` subprocess and parsing its own ``set`` output.

    Returns a full environment dict (a copy of this process's own
    ``os.environ``, with ``INCLUDE``/``LIB``/``LIBPATH`` set from
    vcvarsall's own output and cl.exe's real resolved directory plus the
    real Windows SDK ``bin`` directory - needed for ``rc.exe``/``mt.exe``
    during linking, confirmed necessary by a real failed link without it -
    prepended to ``PATH``) for the caller to pass as
    ``subprocess.run(..., env=...)``. This machine's own PATH/environment is
    never modified; everything here is scoped to the returned dict.

    Raises ``RuntimeError`` if vcvarsall.bat fails, or if
    INCLUDE/LIB/LIBPATH/the Windows SDK bin directory/cl.exe's own path
    cannot be resolved from its real output.
    """
    vcvarsall = _resolve_vcvarsall()
    workdir.mkdir(parents=True, exist_ok=True)
    script_path = workdir / "_activate_msvc.bat"
    script_path.write_text(
        "@echo off\r\n"
        f'call "{vcvarsall}" x64\r\n'
        "echo ---MSVC-ENV-START---\r\n"
        "set\r\n"
        "echo ---MSVC-ENV-END---\r\n"
        "where cl.exe\r\n",
        encoding="utf-8",
    )

    launch_env = dict(os.environ)
    launch_env["PATH"] = f"{_CPP_VSWHERE_DIR};{launch_env.get('PATH', '')}"

    result = subprocess.run(
        ["cmd.exe", "/c", str(script_path)],
        cwd=workdir,
        capture_output=True,
        text=True,
        env=launch_env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"vcvarsall.bat x64 failed:\n{result.stdout}\n{result.stderr}")

    output = result.stdout
    if "---MSVC-ENV-START---" not in output or "---MSVC-ENV-END---" not in output:
        raise RuntimeError(f"vcvarsall.bat x64 produced unexpected output:\n{output}\n{result.stderr}")

    env_block = output.split("---MSVC-ENV-START---", 1)[1].split("---MSVC-ENV-END---", 1)[0]
    captured: dict[str, str] = {}
    for line in env_block.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            captured[key.strip()] = value

    if not captured.get("WindowsSdkVerBinPath"):
        raise RuntimeError(
            "the Windows SDK is not installed or not visible to vcvarsall.bat (WindowsSdkVerBinPath "
            "unset; rc.exe, mt.exe and kernel32.lib come from it). Install the Windows 10/11 SDK "
            f"through the Visual Studio Installer.\n{output}"
        )
    for required_key in ("INCLUDE", "LIB", "LIBPATH"):
        if not captured.get(required_key):
            raise RuntimeError(f"vcvarsall.bat x64 did not set {required_key}:\n{output}")

    after_env_block = output.split("---MSVC-ENV-END---", 1)[1]
    cl_match = _CL_EXE_PATH_RE.search(after_env_block)
    if cl_match is None:
        raise RuntimeError(f"could not resolve cl.exe from `where cl.exe`:\n{after_env_block}")
    cl_exe = Path(cl_match.group(1).strip())
    if not cl_exe.exists():
        raise RuntimeError(f"resolved cl.exe path does not exist: {cl_exe}")

    sdk_bin_dir = Path(captured["WindowsSdkVerBinPath"].strip().rstrip("\\")) / "x64"

    env = dict(os.environ)
    env["INCLUDE"] = captured["INCLUDE"]
    env["LIB"] = captured["LIB"]
    env["LIBPATH"] = captured["LIBPATH"]
    env["PATH"] = f"{cl_exe.parent};{sdk_bin_dir};{env.get('PATH', '')}"

    return _MsvcEnvironment(env=env, cl_exe=cl_exe)


def prepare_cpp_library(repository: str, commit: str, workdir: Path) -> Path:
    """Shallow-clone ``repository`` into ``workdir`` at exactly ``commit``,
    activate a real MSVC x64 environment, and build the real
    ``aspose_cells_foss`` static-library CMake target once with
    ``cmake -G Ninja`` + ``ninja`` (MSVC specifically - GCC/Clang both fail
    on this repo's own trigraph literal under their non-MSVC compile
    flags, confirmed real by
    ``C:\\tools\\rp-toolchains\\TOOLCHAIN_PATHS.txt``).

    Returns ``workdir`` - the cloned repo's own root, matching
    ``prepare_rust_library``'s/``prepare_go_library``'s own convention of
    returning the prepared root directly. Under it,
    ``Aspose.Cells.Foss.Cpp/include`` is the real header root a candidate's
    ``#include`` directives resolve against, and
    ``Aspose.Cells.Foss.Cpp/_build/aspose_cells_foss.lib`` is the real
    built static library - both real, hands-on-confirmed paths
    ``verify_cpp_example`` derives from ``library_dir``/``include_dir``.

    Raises ``RuntimeError`` on any real failure (clone, checkout, MSVC
    environment activation, cmake configure, or ninja build).
    """
    _clone_pinned_commit(repository, commit, workdir)

    ninja_executable = _resolve_ninja()
    cmake_executable = _resolve_cmake()

    library_source_dir = workdir / _CPP_LIBRARY_SUBDIR
    build_dir = library_source_dir / _CPP_BUILD_DIRNAME
    build_dir.mkdir(parents=True, exist_ok=True)

    msvc = _msvc_environment(workdir / "_msvc_env_prepare")
    build_env = dict(msvc.env)
    build_env["PATH"] = f"{ninja_executable.parent};{build_env.get('PATH', '')}"

    configure_result = subprocess.run(
        [
            str(cmake_executable),
            "-G",
            "Ninja",
            "-DCMAKE_BUILD_TYPE=Release",
            f"-DCMAKE_CXX_COMPILER={msvc.cl_exe}",
            str(library_source_dir),
        ],
        cwd=build_dir,
        capture_output=True,
        text=True,
        env=build_env,
    )
    if configure_result.returncode != 0:
        raise RuntimeError(
            f"cmake configure of {repository}@{commit} ({_CPP_LIBRARY_SUBDIR}) failed:\n"
            f"{configure_result.stdout}\n{configure_result.stderr}"
        )

    build_result = subprocess.run(
        [str(ninja_executable), _CPP_LIBRARY_TARGET],
        cwd=build_dir,
        capture_output=True,
        text=True,
        env=build_env,
    )
    if build_result.returncode != 0:
        raise RuntimeError(
            f"ninja build of {_CPP_LIBRARY_TARGET} for {repository}@{commit} failed:\n"
            f"{build_result.stdout}\n{build_result.stderr}"
        )

    library_artifact = build_dir / _CPP_LIBRARY_ARTIFACT_NAME
    if not library_artifact.exists():
        raise RuntimeError(
            f"ninja reported success building {_CPP_LIBRARY_TARGET} but {library_artifact} was not produced"
        )

    return workdir


def verify_cpp_example(
    candidate: CandidateExample,
    *,
    library_dir: Path,
    include_dir: Path,
    workdir: Path,
) -> VerificationResult:
    """Compile and link ``candidate.code`` as a real, throwaway ``.cpp``
    file against the pre-built static library under ``library_dir`` (a
    ``prepare_cpp_library`` root -
    ``Aspose.Cells.Foss.Cpp/_build/aspose_cells_foss.lib`` under it) and
    ``include_dir`` (the header root, e.g. ``Aspose.Cells.Foss.Cpp/include``
    under that same root), using the same real MSVC toolchain, and report
    the real ``cl.exe`` outcome.

    Compiled with ``/MD`` to match the reference library's own ``Release``
    build (dynamic, non-debug runtime) - confirmed by a real, hands-on
    reproduction that a mismatched runtime (the library's own CMake default
    of ``/MDd`` vs. cl.exe's own bare default of ``/MT``) fails at LINK time
    with LNK2038 errors, not at candidate compile time, so both sides must
    agree.

    The literal comparison determining ``verified`` is intentionally exact
    (``verified = result.returncode == 0``), matching every other pilot.
    """
    project_dir = workdir / "candidate_project"
    project_dir.mkdir(parents=True, exist_ok=True)
    source_path = project_dir / "candidate.cpp"
    source_path.write_text(candidate.code, encoding="utf-8")

    library_artifact = library_dir / _CPP_LIBRARY_SUBDIR / _CPP_BUILD_DIRNAME / _CPP_LIBRARY_ARTIFACT_NAME
    output_exe = project_dir / "candidate.exe"

    msvc = _msvc_environment(workdir / "_msvc_env_verify")

    result = subprocess.run(
        [
            str(msvc.cl_exe),
            "/nologo",
            "/EHsc",
            "/std:c++17",
            "/MD",
            f"/I{include_dir}",
            str(source_path),
            str(library_artifact),
            f"/Fe:{output_exe}",
        ],
        cwd=project_dir,
        capture_output=True,
        text=True,
        env=msvc.env,
    )

    verified = result.returncode == 0
    output = _truncate(f"{result.stdout}\n{result.stderr}")

    return VerificationResult(candidate=candidate, verified=verified, output=output)


def _clone_pinned_commit(repository: str, commit: str, workdir: Path) -> None:
    """Shallow-fetch exactly one pinned commit of ``repository`` into
    ``workdir``.

    Mirrors ``prepare_reference_library``'s own git plumbing exactly
    (``git init`` + ``git remote add`` + ``git fetch --depth 1`` + ``git
    checkout``), factored out so the three new pilots below share one
    real, tested clone path instead of three subtly-diverging copies.
    ``prepare_reference_library`` itself is untouched and does not call
    this helper.

    Raises ``RuntimeError`` on any real failure (init, remote add, fetch,
    or checkout).
    """
    workdir.mkdir(parents=True, exist_ok=True)
    repo_url = f"https://github.com/{repository}.git"

    init_result = _run(["git", "init"], cwd=workdir)
    if init_result.returncode != 0:
        raise RuntimeError(f"git init failed in {workdir}:\n{init_result.stdout}\n{init_result.stderr}")

    remote_result = _run(["git", "remote", "add", "origin", repo_url], cwd=workdir)
    if remote_result.returncode != 0:
        raise RuntimeError(
            f"git remote add failed for {repo_url}:\n{remote_result.stdout}\n{remote_result.stderr}"
        )

    fetch_result = _run(["git", "fetch", "--depth", "1", "origin", commit], cwd=workdir)
    if fetch_result.returncode != 0:
        raise RuntimeError(
            f"git fetch of commit {commit} from {repo_url} failed:\n"
            f"{fetch_result.stdout}\n{fetch_result.stderr}"
        )

    checkout_result = _run(["git", "checkout", commit], cwd=workdir)
    if checkout_result.returncode != 0:
        raise RuntimeError(
            f"git checkout of commit {commit} failed:\n{checkout_result.stdout}\n{checkout_result.stderr}"
        )


def _venv_python_executable(venv_dir: Path) -> Path:
    """Return the real python executable inside ``venv_dir``, on whichever
    of the two standard venv layouts the current platform actually used
    (``Scripts/python.exe`` on Windows, ``bin/python`` elsewhere).
    """
    windows_python = venv_dir / "Scripts" / "python.exe"
    if windows_python.exists():
        return windows_python
    return venv_dir / "bin" / "python"


# --- Python interpreter choice ------------------------------------------------
#
# A library's ``requires-python`` can exclude the interpreter that runs this
# verifier (the ingestion image runs 3.13; words/python needs ``<3.13``). The
# venv is therefore created with an interpreter that satisfies the library's
# own requirement. ``packaging`` is only a transitive pin here, not a declared
# dependency, so the specifier forms real projects use are parsed by hand.

_FALLBACK_INTERPRETER_NAMES = ("python3.13", "python3.12", "python3.11", "python3.10")
_SPECIFIER_PATTERN = re.compile(r"^(~=|===|==|!=|<=|>=|<|>)\s*(\d+(?:\.\d+)*)(\.\*)?")


def _release_tuple(text: str) -> tuple[int, ...] | None:
    """Parse the leading release segment of ``text`` ("3.13.0rc1" -> (3, 13, 0))."""
    match = re.match(r"^\s*(\d+(?:\.\d+)*)", text)
    if match is None:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def _padded(left: tuple[int, ...], right: tuple[int, ...]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)), right + (0,) * (width - len(right))


def _has_release_prefix(version: tuple[int, ...], prefix: tuple[int, ...]) -> bool:
    left, right = _padded(version[: len(prefix)], prefix)
    return left == right


def _clause_matches(
    version: tuple[int, ...], operator: str, release: tuple[int, ...], wildcard: bool
) -> bool:
    if wildcard:
        if operator not in ("==", "!="):
            raise ValueError(f"'.*' is only valid with == and !=, not {operator}")
        prefix_match = _has_release_prefix(version, release)
        return prefix_match if operator == "==" else not prefix_match
    left, right = _padded(version, release)
    if operator == "==":
        return left == right
    if operator == "!=":
        return left != right
    if operator == ">=":
        return left >= right
    if operator == ">":
        return left > right
    if operator == "<=":
        return left <= right
    if operator == "<":
        return left < right
    if operator == "~=":
        if len(release) < 2:
            raise ValueError("~= needs at least two release segments")
        return left >= right and _has_release_prefix(version, release[:-1])
    raise ValueError(f"unsupported specifier operator {operator}")


def _python_version_satisfies(version: tuple[int, ...], requires_python: str) -> bool:
    """Whether ``version`` satisfies the comma-separated specifier string.

    Raises ValueError for a specifier this parser does not understand.
    """
    for raw_clause in requires_python.split(","):
        clause = raw_clause.strip()
        if not clause:
            continue
        match = _SPECIFIER_PATTERN.match(clause)
        if match is None or clause[match.end() :].strip() or match.group(1) == "===":
            raise ValueError(f"unsupported requires-python specifier {clause!r}")
        release = tuple(int(part) for part in match.group(2).split("."))
        if not _clause_matches(version, match.group(1), release, match.group(3) is not None):
            return False
    return True


def _interpreter_version(executable: str) -> tuple[int, ...] | None:
    """The (major, minor, micro) of ``executable``, or None when it cannot be run."""
    if executable == sys.executable:
        return tuple(sys.version_info[:3])
    try:
        result = subprocess.run(
            [executable, "-c", "import sys;print('.'.join(map(str,sys.version_info[:3])))"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return _release_tuple(result.stdout)


def _candidate_interpreters() -> list[str]:
    """The running interpreter first, then python3.13..python3.10 found on PATH."""
    candidates = [sys.executable]
    seen = {os.path.normcase(os.path.realpath(sys.executable))}
    for name in _FALLBACK_INTERPRETER_NAMES:
        found = shutil.which(name)
        if found is None:
            continue
        identity = os.path.normcase(os.path.realpath(found))
        if identity in seen:
            continue
        seen.add(identity)
        candidates.append(found)
    return candidates


def _pick_python_interpreter(
    requires_python: str | None,
    versions: Mapping[str, tuple[int, ...] | None] | Callable[[str], tuple[int, ...] | None] | None = None,
) -> str:
    """Choose the interpreter to build a library's venv with.

    ``versions`` reports each candidate's version: a mapping (its keys, in
    order, are the candidates) or a callable applied to the default candidate
    list (None means probing each real interpreter). No (or an empty, or an unparseable) requirement keeps the first
    candidate, the running interpreter. Raises RuntimeError naming the
    requirement and every interpreter tried, with its version, when none
    satisfies the requirement.
    """
    if isinstance(versions, Mapping):
        candidates = list(versions)
        lookup: Callable[[str], tuple[int, ...] | None] = versions.get
    else:
        candidates = _candidate_interpreters()
        lookup = _interpreter_version if versions is None else versions
    requirement = (requires_python or "").strip()
    if not requirement:
        return candidates[0] if candidates else sys.executable
    tried: list[str] = []
    for candidate in candidates:
        version = lookup(candidate)
        if version is None:
            tried.append(f"{candidate} (version unavailable)")
            continue
        try:
            satisfied = _python_version_satisfies(tuple(version), requirement)
        except ValueError:
            # A specifier this parser cannot read must not newly break a library
            # that worked before: behave as if nothing was declared.
            return candidates[0]
        if satisfied:
            return candidate
        tried.append(f"{candidate} ({'.'.join(str(part) for part in version)})")
    raise RuntimeError(
        f"no available Python interpreter satisfies requires-python {requirement!r}; "
        f"tried: {', '.join(tried) if tried else 'none'}"
    )


def _read_requires_python(repo_dir: Path) -> str | None:
    """``[project].requires-python`` from the clone's pyproject.toml, or None
    when the file, the key, or valid TOML is missing."""
    try:
        data = tomllib.loads((repo_dir / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    project = data.get("project")
    if not isinstance(project, dict):
        return None
    value = project.get("requires-python")
    return value if isinstance(value, str) and value.strip() else None


# Optional-dependency groups that exist for the library's own development, never for running it.
_NON_RUNTIME_EXTRAS = frozenset(
    {
        "test",
        "tests",
        "testing",
        "dev",
        "develop",
        "development",
        "docs",
        "doc",
        "lint",
        "linting",
        "typing",
        "types",
        "mypy",
        "ci",
        "bench",
        "benchmark",
        "benchmarks",
        "all",
    }
)

_PTH_FILENAME = "_foss_mcp_source.pth"
_PURELIB_PROGRAM = "import sysconfig; print(sysconfig.get_paths()['purelib'])"


def _read_pyproject(repo_dir: Path) -> dict:
    """The clone's parsed pyproject.toml, or ``{}`` when it is missing, unreadable or invalid."""
    try:
        return tomllib.loads((repo_dir / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return {}


def _optional_dependency_groups(repo_dir: Path) -> dict[str, list[str]]:
    project = _read_pyproject(repo_dir).get("project")
    groups = project.get("optional-dependencies") if isinstance(project, dict) else None
    if not isinstance(groups, dict):
        return {}
    return {
        name: [item for item in value if isinstance(item, str)]
        for name, value in groups.items()
        if isinstance(name, str) and isinstance(value, list)
    }


def _python_runtime_extras(repo_dir: Path) -> tuple[str, ...]:
    """Sorted names of the library's optional-dependency groups that its runtime may need."""
    return tuple(sorted(set(_optional_dependency_groups(repo_dir)) - _NON_RUNTIME_EXTRAS))


def _declared_dependencies(repo_dir: Path, extras: tuple[str, ...]) -> list[str]:
    """The requirements to install by hand when ``pip install -e`` cannot build the library."""
    project = _read_pyproject(repo_dir).get("project")
    declared = project.get("dependencies") if isinstance(project, dict) else None
    requirements: list[str] = [item for item in declared if isinstance(item, str)] if declared else []
    groups = _optional_dependency_groups(repo_dir)
    for extra in extras:
        requirements.extend(groups.get(extra, []))
    if not requirements:
        try:
            lines = (repo_dir / "requirements.txt").read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            lines = []
        for raw in lines:
            line = raw.split(" #", 1)[0].strip()
            if not line or line.startswith("#") or line.startswith(("-r", "-e")):
                continue
            requirements.append(line)
    return list(dict.fromkeys(requirements))


def _install_failure(label: str, result: subprocess.CompletedProcess[str]) -> str:
    output = _truncate(f"{result.stdout}\n{result.stderr}")
    return f"{label} (exit {result.returncode}):\n{output}"


def prepare_python_library(
    repository: str,
    commit: str,
    workdir: Path,
) -> Path:
    """Shallow-clone ``repository`` into ``workdir`` at exactly ``commit``,
    create a fresh venv alongside it, and install the real library into
    that venv once. Returns the venv's own python executable, for
    ``verify_python_example`` to run candidates against.

    Python has no separate compile step, so this is the Python pilot's
    equivalent of ``prepare_reference_library`` building the reference
    library once: a real, disposable environment that genuinely has the
    real library installed and importable, never assumed.

    The install is ``pip install -e`` with the library's runtime extras; without the extras when
    that fails; and, when the library still cannot be built (a pyproject naming a missing build
    backend, a setup.py reading a git-ignored file), its declared dependencies plus a ``.pth`` path
    entry to the source tree.

    Raises ``RuntimeError`` on any real failure (clone, checkout, venv
    creation, or install).
    """
    repo_dir = workdir / "repo"
    _clone_pinned_commit(repository, commit, repo_dir)

    venv_dir = workdir / "venv"
    interpreter = _pick_python_interpreter(_read_requires_python(repo_dir))
    venv_result = _run([interpreter, "-m", "venv", str(venv_dir)], cwd=workdir)
    if venv_result.returncode != 0:
        raise RuntimeError(
            f"python -m venv failed for {venv_dir}:\n{venv_result.stdout}\n{venv_result.stderr}"
        )

    venv_python = _venv_python_executable(venv_dir)

    extras = _python_runtime_extras(repo_dir)
    attempts: list[str] = []
    targets = [f"{repo_dir}[{','.join(extras)}]", str(repo_dir)] if extras else [str(repo_dir)]
    for target in targets:
        install_result = _run([str(venv_python), "-m", "pip", "install", "-e", target], cwd=workdir)
        if install_result.returncode == 0:
            return venv_python
        attempts.append(
            _install_failure(f"pip install -e {target} into venv {venv_dir} failed", install_result)
        )
        if install_result.returncode == _TIMEOUT_EXIT_CODE:
            raise RuntimeError("\n".join(attempts))

    dependencies = _declared_dependencies(repo_dir, extras)
    if dependencies:
        deps_result = _run([str(venv_python), "-m", "pip", "install", *dependencies], cwd=workdir)
        if deps_result.returncode != 0:
            attempts.append(
                _install_failure(
                    f"pip install of the declared dependencies {dependencies} into venv {venv_dir} failed",
                    deps_result,
                )
            )
            raise RuntimeError("\n".join(attempts))

    purelib_result = _run([str(venv_python), "-c", _PURELIB_PROGRAM], cwd=workdir)
    purelib_text = purelib_result.stdout.strip()
    if purelib_result.returncode != 0 or not purelib_text or not Path(purelib_text).is_dir():
        attempts.append(_install_failure("querying the venv purelib directory failed", purelib_result))
        raise RuntimeError("\n".join(attempts))

    source_root = repo_dir / "src" if (repo_dir / "src").is_dir() else repo_dir
    (Path(purelib_text) / _PTH_FILENAME).write_text(f"{source_root.resolve()}\n", encoding="utf-8")
    print(
        f"[foss-mcp] pip install -e failed for {repo_dir}; installed its declared dependencies and "
        f"put {source_root} on sys.path through {_PTH_FILENAME}",
        file=sys.stderr,
    )
    return venv_python


_STAGED_NAME_RE = re.compile(r"""["']([\w.\- ]+\.\w{1,5})["']""")
_STAGING_SKIPPED_DIRS = frozenset({".git", "venv", ".venv", "node_modules", "__pycache__"})
_STAGING_REFUSED_EXTENSIONS = frozenset({"py", "pyc", "pyd", "pyi", "pth", "so", "dll", "exe"})
_STAGING_MAX_BYTES = 50 * 1024 * 1024


def _stage_named_repository_files(code: str, repo_dir: Path, script_dir: Path) -> list[str]:
    """Copy the repository files a block names by bare file name into ``script_dir``.

    A block that opens ``"SimpleTable.one"`` expects that sample next to it; the pinned repository
    keeps it somewhere below ``testfiles/``. For each quoted bare file name the first repository file
    (by repo-relative posix path) of exactly that name is copied: never a Python file or binary
    library, a symlink, a file under a VCS, environment or cache directory, or one above 50 MiB; and
    never over a file already in ``script_dir``. Returns the repo-relative paths copied.
    """
    names = [
        name
        for name in dict.fromkeys(_STAGED_NAME_RE.findall(code))
        if name.rsplit(".", 1)[-1].lower() not in _STAGING_REFUSED_EXTENSIONS
        and not (script_dir / name).exists()
    ]
    if not names:
        return []
    wanted = set(names)
    found: dict[str, list[str]] = {}
    for root, dirnames, filenames in os.walk(repo_dir):
        dirnames[:] = [d for d in dirnames if d not in _STAGING_SKIPPED_DIRS]
        for filename in filenames:
            if filename in wanted:
                relative = (Path(root) / filename).relative_to(repo_dir).as_posix()
                found.setdefault(filename, []).append(relative)
    copied: list[str] = []
    for name in names:
        for relative in sorted(found.get(name, [])):
            source = repo_dir / relative
            try:
                if source.is_symlink() or not source.is_file() or source.stat().st_size > _STAGING_MAX_BYTES:
                    continue
                shutil.copyfile(source, script_dir / name)
            except OSError:
                continue
            copied.append(relative)
            break
    return copied


def _is_import_only(code: str) -> bool:
    """True when ``code`` parses and holds only imports, ``pass`` and bare strings (or nothing)."""
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return False
    return all(
        isinstance(node, ast.Import | ast.ImportFrom | ast.Pass)
        or (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        )
        for node in tree.body
    )


def verify_python_example(
    candidate: CandidateExample,
    *,
    venv_python: Path,
    workdir: Path,
    repo_dir: Path | None = None,
) -> VerificationResult:
    """Run ``candidate.code`` as a real, disposable script with
    ``venv_python`` (a venv that already has the reference library
    installed), and report the real interpreter outcome.

    Python has no separate compile step: the candidate is actually
    executed in a real, isolated subprocess, and a real exception during
    that execution means ``verified=False``, exactly like a real compiler
    error for the other languages. ``verified`` is the literal exit-code
    comparison (``verified = result.returncode == 0``), so forcing it to
    always be true is a detectable, meaningful negative control.

    A block that only imports is rejected without running (an import proves nothing about use). The
    repository files the block names are staged beside it, and the run is bounded to
    ``_PYTHON_CANDIDATE_TIMEOUT_SECONDS``: a killed block is not verified.
    """
    if _is_import_only(candidate.code):
        return VerificationResult(
            candidate=candidate,
            verified=False,
            output=_NO_STATEMENTS_MESSAGE.format(kind="import statements"),
        )

    script_dir = workdir / "candidate_script"
    script_dir.mkdir(parents=True, exist_ok=True)
    script_path = script_dir / "candidate.py"
    script_path.write_text(candidate.code, encoding="utf-8")

    if repo_dir is None:
        default_repo = venv_python.parent.parent.parent / "repo"
        repo_dir = default_repo if default_repo.is_dir() else None
    staged = _stage_named_repository_files(candidate.code, repo_dir, script_dir) if repo_dir else []

    result = _run_bounded(
        [str(venv_python), str(script_path)],
        cwd=script_dir,
        timeout_seconds=_PYTHON_CANDIDATE_TIMEOUT_SECONDS,
    )

    verified = result.returncode == 0
    assembly = f"[foss-mcp] assembly: python staged-files={','.join(staged) if staged else 'none'}"
    run_output = _truncate(f"{result.stdout}\n{result.stderr}")
    output = f"{assembly}\n{run_output}"

    return VerificationResult(candidate=candidate, verified=verified, output=output)


def prepare_rust_library(
    repository: str,
    commit: str,
    workdir: Path,
) -> Path:
    """Shallow-clone ``repository`` into ``workdir`` at exactly ``commit``
    and build the reference crate once with ``cargo build``, proving the
    real library itself builds clean. Returns ``workdir`` - the cloned
    crate's own root, where its ``Cargo.toml`` lives - for
    ``verify_rust_example`` to depend on as a Cargo path dependency.

    Raises ``ExampleEnvironmentError`` when the failed build's output names a
    registry or network failure with no rustc diagnostic (see
    ``_reference_build_failure``). Raises ``RuntimeError`` on any other real
    failure (clone, checkout, or build).
    """
    _clone_pinned_commit(repository, commit, workdir)

    build_result = _run_cargo(["cargo", "build"], cwd=workdir)
    if build_result.returncode != 0:
        raise _reference_build_failure("cargo", repository, commit, build_result)

    return workdir


def _reference_build_failure(
    tool: str,
    repository: str,
    commit: str,
    result: subprocess.CompletedProcess[str],
) -> Exception:
    """Return the exception to raise for a failed reference build.

    A ``cargo`` build whose output names a registry or network failure (see
    ``_CARGO_NETWORK_FAILURE_MARKERS``) and carries no rustc diagnostic
    (``error[E<digits>]``) could not reach the registry. That is an
    environment failure, not a defect in the reference library, so it returns
    ``ExampleEnvironmentError`` carrying the matched marker. Every other failed
    reference build returns the plain ``RuntimeError``.
    """
    output_text = f"{result.stdout}\n{result.stderr}"
    if tool == "cargo" and _RUSTC_DIAGNOSTIC.search(output_text) is None:
        marker = _match_network_failure_marker(output_text)
        if marker is not None:
            return ExampleEnvironmentError(tool, marker, _truncate(output_text))
    return RuntimeError(
        f"reference crate at {repository}@{commit} failed to build cleanly:\n{result.stdout}\n{result.stderr}"
    )


_RUST_FRAGMENT_MAIN_HEADER = "fn main() -> Result<(), Box<dyn std::error::Error>> {"
_RUST_MAIN_RE = re.compile(r"(?m)^\s*(?:pub\s+)?(?:async\s+)?fn\s+main\s*\(")
_RUST_USE_RE = re.compile(r"(?m)^[ \t]*(?:pub(?:\([^)]*\))?[ \t]+)?use\s[^;]*;")
_RUST_INNER_ATTRIBUTE_RE = re.compile(r"(?m)^[ \t]*#!\[.*\][ \t]*$")
_RUST_USE_PREFIX_RE = re.compile(r"^(?:pub(?:\([^)]*\))?\s+)?use\s+")


def _rust_has_main(code: str) -> bool:
    """Return True when ``code`` defines its own ``fn main``."""
    return _RUST_MAIN_RE.search(code) is not None


def _rust_use_statements(code: str) -> list[str]:
    """Every ``use ...;`` statement (optionally ``pub``) of ``code``, in order, across lines."""
    return [match.group(0).strip() for match in _RUST_USE_RE.finditer(code)]


def _split_top_level_commas(text: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(text):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[start:index])
            start = index + 1
    parts.append(text[start:])
    return [part.strip() for part in parts if part.strip()]


def _rust_use_tree_names(tree: str, parent: str) -> set[str] | None:
    tree = tree.strip()
    if "{" in tree:
        prefix, rest = tree.split("{", 1)
        inner = rest[: rest.rindex("}")]
        segments = [segment.strip() for segment in prefix.strip().rstrip(":").split("::")]
        inner_parent = segments[-1] if segments and segments[-1] else parent
        names: set[str] = set()
        for item in _split_top_level_commas(inner):
            if item == "self":
                if inner_parent:
                    names.add(inner_parent)
                continue
            sub = _rust_use_tree_names(item, inner_parent)
            if sub is None:
                return None
            names |= sub
        return names
    if tree.endswith("*"):
        return None
    if re.search(r"\sas\s", tree):
        alias = re.split(r"\sas\s", tree)[-1].strip()
        return set() if alias == "_" else {alias}
    last = tree.split("::")[-1].strip()
    if last == "self":
        last = parent
    return {last} if last else set()


def _rust_use_names(statement: str) -> frozenset[str] | None:
    """The names a ``use`` statement brings into scope; None for a glob.

    That is the last segment of each leaf, or its ``as`` alias, through nested braces; ``self``
    inside braces counts as the parent segment.
    """
    body = _RUST_USE_PREFIX_RE.sub("", statement.strip()).rstrip().removesuffix(";").strip()
    names = _rust_use_tree_names(" ".join(body.split()), "")
    return None if names is None else frozenset(names)


def _rust_mentions(code: str, name: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", code) is not None


def _rust_page_uses(code: str, page_code: tuple[str, ...]) -> list[str]:
    """The page's ``use`` statements the block needs: globs, or those naming a word the block uses."""
    own = {_collapse_whitespace(statement) for statement in _rust_use_statements(code)}
    seen: set[str] = set()
    kept: list[str] = []
    for block in page_code:
        for statement in _rust_use_statements(block):
            key = _collapse_whitespace(statement)
            if key in seen:
                continue
            seen.add(key)
            if key in own:
                continue
            names = _rust_use_names(statement)
            if names is None or any(_rust_mentions(code, name) for name in names):
                kept.append(statement)
    return kept


def _assemble_rust_source(code: str, page_code: tuple[str, ...]) -> tuple[str, str, tuple[str, ...]]:
    """Return (source, mode, page uses added) for ``src/main.rs``.

    A block with its own ``fn main`` is compiled exactly as given. Any other block is a statement
    fragment: it is wrapped in a ``Result``-returning ``main`` so ``?`` works, preceded at crate
    level by the page ``use`` statements it needs. The block's own ``use`` lines stay inside
    ``main``, where a block-scoped import shadows a crate-level one instead of colliding.
    """
    if _rust_has_main(code):
        return code, "as-given", ()
    attributes = [match.group(0).strip() for match in _RUST_INNER_ATTRIBUTE_RE.finditer(code)]
    body = _RUST_INNER_ATTRIBUTE_RE.sub("", code).strip("\n") if attributes else code
    added = _rust_page_uses(code, page_code)
    lines = [*attributes, *added, _RUST_FRAGMENT_MAIN_HEADER, body, "    Ok(())", "}"]
    return "\n".join(lines) + "\n", "wrapped-fragment", tuple(added)


def _rust_serde_json_requirement(library_dir: Path) -> str:
    """The library's own ``serde_json`` requirement, else ``"1"``. Any error is ignored."""
    try:
        manifest = tomllib.loads((library_dir / "Cargo.toml").read_text(encoding="utf-8"))
        dependency = manifest["dependencies"]["serde_json"]
        if isinstance(dependency, Mapping):
            dependency = dependency["version"]
        if isinstance(dependency, str) and dependency.strip():
            return dependency.strip()
    except Exception:
        pass
    return "1"


def verify_rust_example(
    candidate: CandidateExample,
    *,
    library_crate_name: str,
    library_dir: Path,
    workdir: Path,
) -> VerificationResult:
    """Build ``candidate.code`` as ``src/main.rs`` in a throwaway crate
    that depends on ``library_dir`` (the built reference crate) via a
    Cargo path dependency named ``library_crate_name``, and report the
    real ``cargo build`` outcome.

    A block without its own ``fn main`` is a statement fragment: it is wrapped in a
    ``Result``-returning ``main`` with only the page ``use`` statements it names (see
    ``_assemble_rust_source``). A block that holds nothing but ``use`` declarations is rejected
    without calling cargo. ``serde_json`` is declared, at the library's own version requirement,
    only when the assembled source names it. The first line of the output records the assembly.

    Raises ``ExampleEnvironmentError`` when a failed build's output names a
    registry or network failure (see ``_CARGO_NETWORK_FAILURE_MARKERS``) AND
    carries no rustc diagnostic (``error[E<digits>]``). A failed build that
    carries a rustc diagnostic is a real compile failure and returns
    ``verified=False``, even if a network marker also appears.

    The literal comparison determining ``verified`` is intentionally exact
    (``verified = result.returncode == 0``), matching ``verify_dotnet_example``.
    """
    if not _rust_has_main(candidate.code):
        statements = _RUST_INNER_ATTRIBUTE_RE.sub("", _RUST_USE_RE.sub("", candidate.code))
        if not _has_own_statements(statements, preamble_line=None):
            return VerificationResult(
                candidate=candidate,
                verified=False,
                output=_NO_STATEMENTS_MESSAGE.format(kind="use declarations"),
            )

    project_dir = workdir / "candidate_project"
    (project_dir / "src").mkdir(parents=True, exist_ok=True)

    source, mode, added_uses = _assemble_rust_source(candidate.code, candidate.page_code)

    cargo_toml_content = _CANDIDATE_CARGO_TOML_TEMPLATE.format(
        crate_name=library_crate_name,
        library_dir=library_dir.resolve().as_posix(),
    )
    extra_dependencies = "none"
    if _rust_mentions(source, "serde_json"):
        extra_dependencies = f"serde_json = {json.dumps(_rust_serde_json_requirement(library_dir))}"
        cargo_toml_content += extra_dependencies + "\n"
    (project_dir / "Cargo.toml").write_text(cargo_toml_content, encoding="utf-8")
    (project_dir / "src" / "main.rs").write_text(source, encoding="utf-8")

    result = _run_cargo(["cargo", "build"], cwd=project_dir)
    output_text = f"{result.stdout}\n{result.stderr}"

    # A failed run whose output names a network failure and carries no rustc
    # diagnostic could not reach the registry: that is an environment failure,
    # not a compile verdict. Raise rather than return a not-verified result.
    if result.returncode != 0 and _RUSTC_DIAGNOSTIC.search(output_text) is None:
        marker = _match_network_failure_marker(output_text)
        if marker is not None:
            raise ExampleEnvironmentError("cargo", marker, _truncate(output_text))

    verified = result.returncode == 0
    added = ", ".join(_collapse_whitespace(statement) for statement in added_uses) or "none"
    assembly_line = (
        f"[foss-mcp] assembly: rust mode={mode}; page-uses-added={added}; "
        f"extra-dependencies={extra_dependencies}"
    )
    output = f"{assembly_line}\n{_truncate(output_text)}"

    return VerificationResult(candidate=candidate, verified=verified, output=output)


def prepare_go_library(
    repository: str,
    commit: str,
    workdir: Path,
) -> Path:
    """Shallow-clone ``repository`` into ``workdir`` at exactly ``commit``
    and confirm the reference module builds clean with ``go build ./...``.
    Returns ``workdir`` - the cloned module's own root, where its
    ``go.mod`` lives - for ``verify_go_example`` to ``replace`` in a
    throwaway module.

    Go modules resolve directly with no separate restore/build-once step
    the way .NET/Rust need, but this keeps the same two-function shape as
    the other pilots for consistency.

    Raises ``RuntimeError`` on any real failure (clone, checkout, or
    build).
    """
    _clone_pinned_commit(repository, commit, workdir)

    build_result = _run(["go", "build", "./..."], cwd=workdir)
    if build_result.returncode != 0:
        raise RuntimeError(
            f"reference module at {repository}@{commit} failed to build cleanly:\n"
            f"{build_result.stdout}\n{build_result.stderr}"
        )

    return workdir


_GO_PACKAGE_DECLARATION_RE = re.compile(r"(?m)^\s*package\s+\w+")
_GO_PACKAGE_CLAUSE_LINE_RE = re.compile(r"^[ \t]*package[ \t]+\w+[ \t]*(?://[^\n]*)?$\n?", re.MULTILINE)
_GO_PACKAGE_NAME_RE = re.compile(r"^[ \t]*package[ \t]+(\w+)", re.MULTILINE)
_GO_IMPORT_SINGLE_RE = re.compile(
    r'^[ \t]*import[ \t]+(?:([A-Za-z_]\w*|\.)[ \t]+)?"([^"\n]+)"[ \t]*(?://[^\n]*)?$\n?',
    re.MULTILINE,
)
_GO_IMPORT_GROUP_RE = re.compile(
    r"^[ \t]*import[ \t]*\((.*?)^[ \t]*\)[ \t]*(?://[^\n]*)?$\n?",
    re.MULTILINE | re.DOTALL,
)
_GO_IMPORT_SPEC_LINE_RE = re.compile(
    r'^[ \t]*(?:([A-Za-z_]\w*|\.)[ \t]+)?"([^"\n]+)"[ \t]*(?://[^\n]*)?$',
    re.MULTILINE,
)
_GO_MAIN_FUNCTION_RE = re.compile(r"^func\s+main\s*\(", re.MULTILINE)

# The standard-library packages a README fragment commonly uses without importing, by qualifier.
_GO_STDLIB_QUALIFIERS: dict[str, str] = {
    "fmt": "fmt",
    "os": "os",
    "strings": "strings",
    "strconv": "strconv",
    "time": "time",
    "log": "log",
    "errors": "errors",
    "io": "io",
    "bytes": "bytes",
    "bufio": "bufio",
    "math": "math",
    "sort": "sort",
    "context": "context",
    "sync": "sync",
    "filepath": "path/filepath",
    "json": "encoding/json",
    "http": "net/http",
}

# The legacy qualifier of pdf/go's furnished content: the package is really named ``asposepdf`` but
# every candidate writes ``pdf.``.
_GO_LEGACY_LIBRARY_QUALIFIER = "pdf"

# How many times a wrapped fragment is rebuilt after blanking variables the compiler reported unused.
_GO_BLANK_USE_MAX_RETRIES = 2

# Build output that asks for ``go mod tidy`` (a missing requirement or go.sum entry).
_GO_TIDY_HINTS = ("go mod tidy", "updates to go.mod needed", "missing go.sum entry")

_GO_MODE_AS_GIVEN = "as-given"
_GO_MODE_FILE_WITH_MAIN = "file-with-main"
_GO_MODE_WRAPPED_FRAGMENT = "wrapped-fragment"

_GO_MAIN_ERROR_LINE_RE = re.compile(r"^(?:\.[\\/])?main\.go:\d+:\d+: (.*)$")
_GO_ANY_ERROR_LINE_RE = re.compile(r"^\S+\.go:\d+:\d+: ")
_GO_UNUSED_NEW_RE = re.compile(r"^declared and not used: (\w+)$")
_GO_UNUSED_OLD_RE = re.compile(r"^(\w+) declared and not used$")
_GO_TOO_MANY_ERRORS = "too many errors"


def _go_import_specs(code: str) -> list[tuple[str | None, str]]:
    """Return every ``(alias, path)`` of the code's import declarations, in source order.

    Both single-line ``import [alias] "path"`` declarations and grouped ``import ( ... )`` blocks
    are read. The alias is a name, ``_`` or ``.``, or None when the import has none.
    """
    code = code.replace("\r\n", "\n")
    found: list[tuple[int, list[tuple[str | None, str]]]] = []
    for single in _GO_IMPORT_SINGLE_RE.finditer(code):
        found.append((single.start(), [(single.group(1), single.group(2))]))
    for group in _GO_IMPORT_GROUP_RE.finditer(code):
        specs = [(m.group(1), m.group(2)) for m in _GO_IMPORT_SPEC_LINE_RE.finditer(group.group(1))]
        found.append((group.start(), specs))
    found.sort(key=lambda item: item[0])
    return [spec for _, specs in found for spec in specs]


def _trim_blank_edge_lines(text: str) -> str:
    lines = text.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def _strip_go_preamble(code: str) -> str:
    """Return ``code`` without its package clause and without every import declaration."""
    code = code.replace("\r\n", "\n")
    code = _GO_IMPORT_GROUP_RE.sub("", code)
    code = _GO_IMPORT_SINGLE_RE.sub("", code)
    code = _GO_PACKAGE_CLAUSE_LINE_RE.sub("", code)
    return _trim_blank_edge_lines(code)


def _go_package_name(library_dir: Path, module_path: str, import_path: str) -> str | None:
    """Return the package name the library declares for ``import_path``, else None.

    Only an import path equal to ``module_path`` or under it names a directory of the library.
    The name is the ``package`` clause of the first (sorted) non-test ``.go`` file there.
    """
    if import_path == module_path:
        directory = library_dir
    elif import_path.startswith(module_path.rstrip("/") + "/"):
        directory = library_dir / import_path[len(module_path.rstrip("/")) + 1 :]
    else:
        return None
    if not directory.is_dir():
        return None
    for go_file in sorted(directory.glob("*.go")):
        if go_file.name.endswith("_test.go"):
            continue
        match = _GO_PACKAGE_NAME_RE.search(go_file.read_text(encoding="utf-8", errors="replace"))
        if match is not None:
            return match.group(1)
    return None


@dataclass(frozen=True)
class _GoPlan:
    """How one Go candidate is assembled into ``main.go``."""

    mode: str
    body: str
    # (qualifier, import path, rendered import line) for every import that is emitted.
    imports: tuple[tuple[str, str, str], ...]


def _is_go_stdlib_path(import_path: str) -> bool:
    return "." not in import_path.split("/", 1)[0]


def _plan_go_assembly(
    code: str,
    module_path: str,
    page_code: tuple[str, ...],
    library_dir: Path | None,
) -> _GoPlan:
    if _GO_PACKAGE_DECLARATION_RE.search(code) is not None:
        return _GoPlan(mode=_GO_MODE_AS_GIVEN, body=code, imports=())

    body = _strip_go_preamble(code)
    own_specs = _go_import_specs(code)

    def qualifier_of(alias: str | None, import_path: str) -> str:
        if alias is not None:
            return alias
        if library_dir is not None:
            name = _go_package_name(library_dir, module_path, import_path)
            if name is not None:
                return name
        return import_path.rstrip("/").rsplit("/", 1)[-1]

    # qualifier -> (import path, written with an explicit alias). The first source naming a
    # qualifier wins.
    table: dict[str, tuple[str, bool]] = {}

    def register(alias: str | None, import_path: str) -> None:
        if alias in ("_", "."):
            return
        qualifier = qualifier_of(alias, import_path)
        aliased = alias is not None or not _is_go_stdlib_path(import_path)
        table.setdefault(qualifier, (import_path, aliased))

    for alias, import_path in own_specs:
        register(alias, import_path)
    for block in page_code:
        for alias, import_path in _go_import_specs(block):
            register(alias, import_path)
    if library_dir is not None:
        root_name = _go_package_name(library_dir, module_path, module_path)
        if root_name is not None:
            table.setdefault(root_name, (module_path, True))
    table.setdefault(_GO_LEGACY_LIBRARY_QUALIFIER, (module_path, True))
    for qualifier, import_path in _GO_STDLIB_QUALIFIERS.items():
        table.setdefault(qualifier, (import_path, False))

    stdlib: list[tuple[str, str, str]] = []
    library: list[tuple[str, str, str]] = []
    for qualifier, (import_path, aliased) in table.items():
        if re.search(rf"(?<![\w.]){re.escape(qualifier)}\.", body) is None:
            continue
        if aliased:
            library.append((qualifier, import_path, f'\t{qualifier} "{import_path}"'))
        else:
            stdlib.append((qualifier, import_path, f'\t"{import_path}"'))
    stdlib.sort(key=lambda item: item[1])
    library.sort(key=lambda item: item[0])
    special = [
        (alias, import_path, f'\t{alias} "{import_path}"')
        for alias, import_path in own_specs
        if alias in ("_", ".")
    ]

    mode = (
        _GO_MODE_FILE_WITH_MAIN
        if _GO_MAIN_FUNCTION_RE.search(body) is not None
        else _GO_MODE_WRAPPED_FRAGMENT
    )
    return _GoPlan(mode=mode, body=body, imports=(*stdlib, *library, *special))


def _wrap_go_fragment_if_needed(
    code: str,
    module_path: str,
    *,
    page_code: tuple[str, ...] = (),
    library_dir: Path | None = None,
    blank_uses: tuple[str, ...] = (),
) -> str:
    """Return the ``main.go`` source for a Go candidate.

    A candidate with its own package clause is a complete file and is returned exactly as given.
    Otherwise the package clause and every import declaration are stripped from the body and the
    imports are rebuilt: a qualifier the body uses is resolved from the candidate's own import
    lines, then from every import line of the page (``page_code``), then from the library's root
    package name, then from the legacy ``pdf`` alias of pdf/go, then from a table of common
    standard-library packages. An import is emitted only for a qualifier the body uses, because Go
    rejects an unused import. Library imports are written aliased with the qualifier, so the
    library's real package name (``cells_foss``, ``jmap``) never has to match the module path.

    A body with its own top-level ``func main(`` is not wrapped again: it becomes
    ``package main`` + imports + the body. Any other body is indented inside ``func main() {``,
    followed by one ``_ = <name>`` line per entry of ``blank_uses`` (variables the compiler
    reported declared and not used in a fragment a reader would continue).
    """
    plan = _plan_go_assembly(code, module_path, page_code, library_dir)
    if plan.mode == _GO_MODE_AS_GIVEN:
        return code

    import_block = ""
    if plan.imports:
        import_block = "import (\n" + "\n".join(line for _, _, line in plan.imports) + "\n)\n\n"

    if plan.mode == _GO_MODE_FILE_WITH_MAIN:
        return f"package main\n\n{import_block}{plan.body}\n"

    lines = [f"\t{line}" if line.strip() else line for line in plan.body.splitlines()]
    lines.extend(f"\t_ = {name}" for name in blank_uses)
    indented = "\n".join(lines)
    return f"package main\n\n{import_block}func main() {{\n{indented}\n}}\n"


def _go_unused_variable_names(build_output: str) -> list[str]:
    """Return the variables a failed build reports unused, or [] unless ALL its errors say so.

    Every compile-error line must be a ``./main.go:<line>:<col>: ...`` line reporting an unused
    variable (``declared and not used: <name>``, or the older ``<name> declared and not used``).
    One other error (an undefined name, an unused import, an error in another file), or a
    truncated error list, returns [] so a real error is never retried away.
    """
    if _GO_TOO_MANY_ERRORS in build_output:
        return []
    names: list[str] = []
    for raw_line in build_output.splitlines():
        line = raw_line.strip()
        if _GO_ANY_ERROR_LINE_RE.match(line) is None:
            continue
        main_line = _GO_MAIN_ERROR_LINE_RE.match(line)
        if main_line is None:
            return []
        message = main_line.group(1).strip()
        unused = _GO_UNUSED_NEW_RE.match(message) or _GO_UNUSED_OLD_RE.match(message)
        if unused is None:
            return []
        if unused.group(1) not in names:
            names.append(unused.group(1))
    return names


def _go_assembly_line(plan: _GoPlan, blank_uses: list[str], tidy_returncode: int | None) -> str:
    imports = ",".join(f"{qualifier}={path}" for qualifier, path, _ in plan.imports) or "none"
    blanks = ",".join(blank_uses) or "none"
    tidy = "not-run" if tidy_returncode is None else f"rc={tidy_returncode}"
    return (
        f"[foss-mcp] assembly: go mode={plan.mode}; imports={imports}; "
        f"blank-uses={blanks}; go-mod-tidy={tidy}"
    )


def verify_go_example(
    candidate: CandidateExample,
    *,
    module_path: str,
    library_dir: Path,
    workdir: Path,
) -> VerificationResult:
    """Build ``candidate.code`` as ``main.go`` in a throwaway module that
    ``replace``s ``module_path`` with ``library_dir`` (the cloned
    reference module), and report the real ``go build`` and ``go vet``
    outcome. A candidate is verified only when both return 0; vet does not
    run when the build fails.

    A README Go block is rarely a complete file. ``_wrap_go_fragment_if_needed`` assembles
    ``main.go`` from the block, the page's own import lines (``candidate.page_code``) and the
    library's real package names; a block that is only a package clause and imports has no
    statements, so it is rejected without running anything. When the build asks for
    ``go mod tidy`` it is run once. When a wrapped fragment fails only because variables are
    declared and not used (a fragment a reader would continue), those variables are blanked and
    the build repeated, at most ``_GO_BLANK_USE_MAX_RETRIES`` times; any other compile error is a
    real failure and is never retried away. The first line of the output records how the file was
    assembled.

    The literal comparison determining ``verified`` is intentionally exact
    (``returncode == 0`` for each of build and vet), matching
    ``verify_dotnet_example``.
    """
    if not _has_own_statements(_strip_go_preamble(candidate.code), preamble_line=None):
        return VerificationResult(
            candidate=candidate,
            verified=False,
            output=_NO_STATEMENTS_MESSAGE.format(kind="package and import declarations"),
        )

    project_dir = workdir / "candidate_project"
    project_dir.mkdir(parents=True, exist_ok=True)

    go_mod_content = _CANDIDATE_GO_MOD_TEMPLATE.format(
        module_path=module_path,
        required_version=_go_required_version(module_path),
        library_dir=library_dir.resolve().as_posix(),
    )
    (project_dir / "go.mod").write_text(go_mod_content, encoding="utf-8")

    plan = _plan_go_assembly(candidate.code, module_path, candidate.page_code, library_dir)
    blank_uses: list[str] = []

    def write_main_go() -> None:
        main_go = _wrap_go_fragment_if_needed(
            candidate.code,
            module_path,
            page_code=candidate.page_code,
            library_dir=library_dir,
            blank_uses=tuple(blank_uses),
        )
        (project_dir / "main.go").write_text(main_go, encoding="utf-8")

    def finish(verified: bool, tidy_returncode: int | None, *outputs: str) -> VerificationResult:
        assembly = _go_assembly_line(plan, blank_uses, tidy_returncode)
        joined = "\n".join(outputs)
        return VerificationResult(
            candidate=candidate,
            verified=verified,
            output=f"{assembly}\n{_truncate(joined)}",
        )

    write_main_go()
    build_result = _run(["go", "build", "./..."], cwd=project_dir)
    tidy_returncode: int | None = None
    tidy_text = ""
    build_text = f"{build_result.stdout}\n{build_result.stderr}"

    if build_result.returncode != 0 and any(hint in build_text for hint in _GO_TIDY_HINTS):
        tidy_result = _run(["go", "mod", "tidy"], cwd=project_dir)
        tidy_returncode = tidy_result.returncode
        tidy_text = f"{tidy_result.stdout}\n{tidy_result.stderr}"
        if tidy_result.returncode != 0:
            return finish(False, tidy_returncode, tidy_text, build_text)
        build_result = _run(["go", "build", "./..."], cwd=project_dir)
        build_text = f"{build_result.stdout}\n{build_result.stderr}"

    retries = 0
    while (
        build_result.returncode != 0
        and plan.mode == _GO_MODE_WRAPPED_FRAGMENT
        and retries < _GO_BLANK_USE_MAX_RETRIES
    ):
        new_names = [name for name in _go_unused_variable_names(build_text) if name not in blank_uses]
        if not new_names:
            break
        blank_uses.extend(new_names)
        write_main_go()
        build_result = _run(["go", "build", "./..."], cwd=project_dir)
        build_text = f"{build_result.stdout}\n{build_result.stderr}"
        retries += 1

    tidy_outputs = (tidy_text,) if tidy_returncode is not None else ()
    if build_result.returncode != 0:
        return finish(False, tidy_returncode, *tidy_outputs, build_text)

    # A candidate that compiles but is statically wrong (an unreachable print,
    # a misused printf verb) is not verified. Vet runs in the same throwaway
    # module, so it sees the same replace directive the build used.
    vet_result = _run(["go", "vet", "./..."], cwd=project_dir)
    vet_text = f"{vet_result.stdout}\n{vet_result.stderr}"
    return finish(vet_result.returncode == 0, tidy_returncode, *tidy_outputs, build_text, vet_text)


# --- pdf/java --------------------------------------------------------------

_MAVEN_VERSION = "3.9.9"
_MAVEN_DOWNLOAD_URL = (
    "https://archive.apache.org/dist/maven/maven-3/3.9.9/binaries/apache-maven-3.9.9-bin.zip"
)

_JAVA_PUBLIC_CLASS_RE = re.compile(r"public\s+(?:final\s+|abstract\s+)?class\s+(\w+)")
_JAVA_DEPENDENCY_DIR = "foss-mcp-deps"
_JAVA_IMPORT_LINE_RE = re.compile(
    r"^import\s+(?:static\s+)?[A-Za-z_]\w*(?:\s*\.\s*(?:[A-Za-z_]\w*|\*))*\s*;$"
)


def _hoist_java_imports(code: str) -> tuple[tuple[str, ...], str]:
    """Split a Java statement block into its top-of-block imports and the remaining body.

    The imports are the ``import ...;`` and ``import static ...;`` lines at the top of ``code``, in
    order, stripped. Blank lines and comments between them are allowed; the first line that is
    anything else ends the import block, so an ``import`` word later in the code is never hoisted.
    The body is ``code`` minus the hoisted lines, with leading blank lines removed. A README code
    block often starts with its own imports, and Java accepts an import only above a class, never
    inside ``main``.
    """
    lines = code.splitlines()
    hoisted_indexes: set[int] = set()
    imports: list[str] = []
    in_block_comment = False
    for index, raw_line in enumerate(lines):
        line = raw_line.strip()
        if in_block_comment:
            if "*/" in line:
                in_block_comment = False
            continue
        if not line or line.startswith("//"):
            continue
        if line.startswith("/*"):
            in_block_comment = "*/" not in line
            continue
        if _JAVA_IMPORT_LINE_RE.match(line):
            hoisted_indexes.add(index)
            imports.append(line)
            continue
        break
    body_lines = [line for index, line in enumerate(lines) if index not in hoisted_indexes]
    while body_lines and not body_lines[0].strip():
        body_lines.pop(0)
    return tuple(imports), "\n".join(body_lines)


def _java_wrapper_source(
    candidate_code: str,
    class_name: str,
    page_imports: tuple[str, ...],
    base_packages: tuple[str, ...] | None = None,
) -> str:
    """Return the full Java source that wraps a candidate with no public class of its own.

    With ``base_packages`` left as ``None`` the source begins with the wildcard import
    ``org.aspose.pdf.*``, then each of ``page_imports`` in order (the import lines the furnished
    page itself carries, so classes in subpackages such as ``annotations`` or ``forms`` resolve),
    then a minimal ``class_name`` class whose ``main`` method holds the candidate's statements.
    With no page imports the text is identical to the wrapper used before page imports were
    honoured.

    A tuple for ``base_packages`` selects pilot mode (slides, cells, jmap, 3d): the header is
    ``import <package>.*;`` for each package in order, ``import java.io.*;``, ``import java.util.*;``,
    then the imports hoisted from the top of the candidate itself (de-duplicated, order kept). The
    page's imports are not added in this mode: a README page collects the imports of every code
    block, and two single-type imports of one simple name do not compile. The body is the candidate
    minus its hoisted import lines.
    """
    if base_packages is None:
        header = "\n".join(["import org.aspose.pdf.*;", *page_imports])
        body = candidate_code
    else:
        candidate_imports, body = _hoist_java_imports(candidate_code)
        header_lines = [f"import {package}.*;" for package in base_packages]
        header_lines += ["import java.io.*;", "import java.util.*;"]
        for import_line in candidate_imports:
            if import_line not in header_lines:
                header_lines.append(import_line)
        header = "\n".join(header_lines)
    indented = "\n".join(f"        {line}" if line.strip() else line for line in body.splitlines())
    return (
        f"{header}\n\n"
        f"public class {class_name} {{\n"
        "    public static void main(String[] args) throws Exception {\n"
        f"{indented}\n"
        "    }\n"
        "}\n"
    )


def _find_or_download_maven(workdir: Path) -> Path:
    """Return a real, invocable ``mvn`` executable: the one already on
    PATH if present, else a freshly downloaded-and-unzipped Maven
    distribution cached under ``workdir``.

    This machine has no ``mvn`` on PATH at all, confirmed by a real,
    hands-on check (``shutil.which("mvn")`` returns ``None``), so the
    download path is real, not theoretical. The versioned
    ``dlcdn.apache.org`` URL for this release 404s; only the
    ``archive.apache.org`` URL above was confirmed to actually serve the
    3.9.9 binary distribution.

    Raises ``RuntimeError`` if the download does not produce the expected
    executable.
    """
    on_path = shutil.which("mvn")
    if on_path is not None:
        return Path(on_path)

    maven_home = workdir / f"apache-maven-{_MAVEN_VERSION}"
    mvn_executable = maven_home / "bin" / ("mvn.cmd" if sys.platform == "win32" else "mvn")
    if mvn_executable.exists():
        return mvn_executable

    workdir.mkdir(parents=True, exist_ok=True)
    archive_path = workdir / "apache-maven.zip"
    with urllib.request.urlopen(_MAVEN_DOWNLOAD_URL) as response:
        archive_path.write_bytes(response.read())

    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(workdir)

    if not mvn_executable.exists():
        raise RuntimeError(
            f"Maven download from {_MAVEN_DOWNLOAD_URL} into {workdir} did not "
            f"produce the expected executable at {mvn_executable}"
        )

    return mvn_executable


def _select_built_jar(target_dir: Path) -> Path:
    """Return the real packaged jar in ``target_dir``, excluding the
    ``-sources.jar`` and ``-javadoc.jar`` side artifacts Maven also
    produces alongside it.
    """
    candidates = sorted(
        path
        for path in target_dir.glob("*.jar")
        if not path.name.endswith("-sources.jar") and not path.name.endswith("-javadoc.jar")
    )
    if not candidates:
        raise RuntimeError(f"no packaged jar (excluding sources/javadoc) found in {target_dir}")
    return candidates[0]


def prepare_java_library(
    repository: str,
    commit: str,
    workdir: Path,
) -> Path:
    """Shallow-clone ``repository`` into ``workdir`` at exactly ``commit``,
    ensure a real, invocable Maven is available (checking PATH first,
    downloading only if absent), build the reference library once, and
    return the real packaged jar's path.

    ``mvn package -DskipTests`` FAILS on this real repository: it skips
    test *execution* but not test *compilation*, and 283 test-source
    files fail to compile against the 943 main classes. This uses
    ``mvn package -Dmaven.test.skip=true`` instead, which skips compiling
    tests too and produces a real jar - confirmed working today by a real,
    hands-on build. The packaged jar (not the raw ``target/classes``
    directory) is what gets returned: referencing ``target/classes``
    directly as a classpath entry was found to fail from Git Bash on this
    machine even with a correct path.

    When the project's own ``<dependencies>`` carry a compile or runtime
    dependency, the dependency jars are copied to ``target/foss-mcp-deps``
    so ``verify_java_example`` can put them on the classpath. Only a Maven
    project with ``pom.xml`` at the repository root is supported.

    Raises ``RuntimeError`` on any real failure (clone, checkout, missing
    pom.xml, Maven acquisition, build, or dependency copy).
    """
    repo_dir = workdir / "repo"
    _clone_pinned_commit(repository, commit, repo_dir)

    pom_path = repo_dir / "pom.xml"
    if not pom_path.exists():
        raise RuntimeError(
            f"{repository}@{commit} has no pom.xml at its root: only root Maven projects are supported"
        )

    mvn_executable = _find_or_download_maven(workdir / "maven")

    build_result = _run(
        [str(mvn_executable), "package", "-Dmaven.test.skip=true"],
        cwd=repo_dir,
    )
    if build_result.returncode != 0:
        raise RuntimeError(
            f"reference library at {repository}@{commit} failed to build cleanly:\n"
            f"{build_result.stdout}\n{build_result.stderr}"
        )

    if _pom_has_runtime_dependencies(pom_path):
        copy_result = _run(
            [
                str(mvn_executable),
                "dependency:copy-dependencies",
                "-DincludeScope=runtime",
                f"-DoutputDirectory=target/{_JAVA_DEPENDENCY_DIR}",
            ],
            cwd=repo_dir,
        )
        if copy_result.returncode != 0:
            raise RuntimeError(
                f"runtime dependencies of {repository}@{commit} could not be copied:\n"
                f"{copy_result.stdout}\n{copy_result.stderr}"
            )

    return _select_built_jar(repo_dir / "target")


def _pom_has_runtime_dependencies(pom_path: Path) -> bool:
    """True when the project-level ``<dependencies>`` of ``pom_path`` holds a dependency the
    compiled library needs at run time: scope absent, ``compile`` or ``runtime``, and not optional.
    ``<dependencyManagement>`` only pins versions and is not read.
    """
    root = ElementTree.parse(pom_path).getroot()
    namespace = root.tag[: root.tag.index("}") + 1] if root.tag.startswith("{") else ""
    dependencies = root.find(f"{namespace}dependencies")
    if dependencies is None:
        return False
    for dependency in dependencies.findall(f"{namespace}dependency"):
        scope = (dependency.findtext(f"{namespace}scope") or "").strip()
        optional = (dependency.findtext(f"{namespace}optional") or "").strip().lower()
        if scope in ("", "compile", "runtime") and optional != "true":
            return True
    return False


def _java_classpath(library_jar: Path) -> str:
    """The ``javac -cp`` value: ``library_jar`` alone when no dependency directory sits beside it,
    else the jar followed by every dependency jar there, in sorted order.
    """
    dependency_dir = library_jar.parent / _JAVA_DEPENDENCY_DIR
    if not dependency_dir.exists():
        return str(library_jar)
    dependency_jars = sorted(dependency_dir.glob("*.jar"))
    return os.pathsep.join([str(library_jar), *(str(jar) for jar in dependency_jars)])


def verify_java_example(
    candidate: CandidateExample,
    *,
    library_jar: Path,
    workdir: Path,
    page_imports: tuple[str, ...] = (),
    java_packages: tuple[str, ...] | None = None,
) -> VerificationResult:
    """Compile ``candidate.code`` against ``library_jar`` (the real
    packaged reference jar) with ``javac``, and report the real compiler
    outcome.

    Real furnished pdf/java content is a complete, self-contained
    statement block already wrapped in a ``try (Document doc = ...)``
    block - not a full compilable Java file with its own class
    declaration (confirmed by reading the real
    ``tests/fixtures/furnished/pdf_java/pages/`` content). A candidate
    with no ``public class`` of its own is therefore wrapped here in a
    minimal ``Candidate`` class with a ``main`` method, a wildcard
    ``org.aspose.pdf`` import, and then ``page_imports`` (the page's own
    import lines, in order); a candidate that already declares its own
    public class is compiled as-is, named after that class, with no page
    imports added.

    ``java_packages`` selects the opt-in pilot mode (slides, cells, jmap, 3d): the wrapper imports
    those packages instead of ``org.aspose.pdf``, hoists the candidate's own import lines above the
    class, and ignores ``page_imports``. The classpath is ``library_jar`` plus any dependency jars
    ``prepare_java_library`` copied beside it.

    The literal comparison determining ``verified`` is intentionally
    exact (``verified = result.returncode == 0``), matching
    ``verify_dotnet_example``.
    """
    project_dir = workdir / "candidate_project"
    project_dir.mkdir(parents=True, exist_ok=True)

    match = _JAVA_PUBLIC_CLASS_RE.search(candidate.code)
    if match is not None:
        class_name = match.group(1)
        source = candidate.code
    else:
        class_name = "Candidate"
        source = _java_wrapper_source(candidate.code, class_name, page_imports, base_packages=java_packages)

    source_path = project_dir / f"{class_name}.java"
    source_path.write_text(source, encoding="utf-8")

    result = _run(
        ["javac", "-cp", _java_classpath(library_jar), source_path.name],
        cwd=project_dir,
    )

    verified = result.returncode == 0
    output = _truncate(f"{result.stdout}\n{result.stderr}")

    return VerificationResult(candidate=candidate, verified=verified, output=output)


# --- pdf/typescript ----------------------------------------------------------

_TS_TSCONFIG_TEMPLATE = """{{
  "compilerOptions": {{
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "strict": true,
    "skipLibCheck": true,
    "esModuleInterop": true,
    "noEmit": true,
    "baseUrl": ".",
    "paths": {{
      "{package_name}": ["{dist_index}"]
    }}
  }},
  "include": ["candidate.ts"]
}}
"""


_TS_ENTRY_SUFFIXES = (".d.ts", ".ts", ".js", ".mjs", ".cjs")
_TS_CANDIDATE_FILENAME = "__foss_mcp_candidate__.ts"
_TS_ROOT_TSCONFIG_FILENAME = "__foss_mcp_tsconfig__.json"
# The file names a tsc diagnostic can carry when it is about the candidate itself: the
# repository-root file, and the paths-mode file. Only this constant identifies the candidate in
# tsc output.
_TS_CANDIDATE_DIAGNOSTIC_FILES = (_TS_CANDIDATE_FILENAME, "candidate.ts")
# One tsc diagnostic line: ``file(line,col): error TSnnnn: message``. A message must follow the code.
_TS_DIAGNOSTIC_LINE_RE = re.compile(
    r"^(?P<file>[^\r\n(]+)\((?P<line>\d+),(?P<col>\d+)\): error (?P<code>TS\d+): ",
    re.MULTILINE,
)
_TS_ERROR_CODE_RE = re.compile(r"error TS\d+")

# A static import or export-from declaration's module specifier. A declaration starts at a line
# start or after a semicolon; the optional middle part spans the (possibly multi-line) binding
# list up to ``from``. A dynamic ``import("x")`` has a parenthesis before its quote, so it never
# matches.
_STATIC_MODULE_DECLARATION_RE = re.compile(
    r"""(?:^|(?<=;))[ \t\r\n]*(?P<decl>(?P<kw>import|export)\b(?:[^'"`;]*?\bfrom)?\s*(?P<q>['"])(?P<spec>[^'"\n]+)(?P=q)[ \t]*;?)""",
    re.MULTILINE,
)


def _read_package_json(library_dir: Path) -> dict:
    """``library_dir/package.json`` as a dict; ``{}`` when it is absent, unreadable or not an object."""
    try:
        data = json.loads((library_dir / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _typescript_types_entry(library_dir: Path) -> Path:
    """The path (without extension) a candidate's package import resolves to.

    Taken from ``library_dir/package.json``: ``types``, else ``typings``, else ``main``, with a
    trailing ``.d.ts``, ``.ts``, ``.js``, ``.mjs`` or ``.cjs`` stripped. With no package.json, or
    none of those keys, it is ``library_dir/dist/index`` (the pdf/typescript value).
    """
    package = _read_package_json(library_dir)
    for key in ("types", "typings", "main"):
        value = package.get(key)
        if isinstance(value, str) and value.strip():
            relative = value.strip()
            for suffix in _TS_ENTRY_SUFFIXES:
                if relative.endswith(suffix):
                    relative = relative[: -len(suffix)]
                    break
            return library_dir / relative
    return library_dir / "dist" / "index"


def _declared_types_file(library_dir: Path) -> Path | None:
    """The ``.d.ts`` file package.json declares in ``types``/``typings``, else None."""
    package = _read_package_json(library_dir)
    for key in ("types", "typings"):
        value = package.get(key)
        if isinstance(value, str) and value.strip().endswith(".d.ts"):
            return library_dir / value.strip()
    return None


def prepare_typescript_library(
    repository: str,
    commit: str,
    workdir: Path,
) -> Path:
    """Shallow-clone ``repository`` into ``workdir`` at exactly ``commit``,
    ``npm install`` its own dependencies, and build it once, producing real
    ``dist/*.js`` + ``*.d.ts`` output where the library has a build. Returns the
    cloned repo's own root - where its output and its already-installed
    ``node_modules/typescript`` live - for ``verify_typescript_example`` to
    depend on and type-check against.

    The build comes from the library's own files (TC-362): ``npx tsc -p
    tsconfig.build.json`` when that file exists (pdf/typescript, unchanged); else no
    build at all when the package entry (``_typescript_types_entry``) is an existing
    ``.ts`` source file (cells/typescript, ``main`` is ``index.ts``); else ``npx tsc
    -p tsconfig.json`` when that exists (jmap/typescript, 3d/typescript). After a
    build, the ``.d.ts`` file package.json declares must exist, else ``dist/`` must be
    non-empty.

    ``npm``/``npx`` are resolved via ``shutil.which`` rather than invoked
    by bare name: on this machine they are ``.CMD`` wrapper scripts, not
    ``.exe`` files, and a bare name in an argv list with ``shell=False``
    is not found by Windows' ``CreateProcess`` the way a real ``.exe`` on
    PATH is - confirmed by a real, hands-on reproduction.

    Raises ``RuntimeError`` on any real failure (clone, checkout, install,
    build, or a library with no way to build it).
    """
    repo_dir = workdir / "repo"
    _clone_pinned_commit(repository, commit, repo_dir)

    npm_executable = shutil.which("npm")
    if npm_executable is None:
        raise RuntimeError("npm is not available on PATH")
    npx_executable = shutil.which("npx")
    if npx_executable is None:
        raise RuntimeError("npx is not available on PATH")

    install_result = _run([npm_executable, "install"], cwd=repo_dir)
    if install_result.returncode != 0:
        raise RuntimeError(
            f"npm install for {repository}@{commit} failed:\n{install_result.stdout}\n{install_result.stderr}"
        )

    entry_source = Path(f"{_typescript_types_entry(repo_dir)}.ts")
    if (repo_dir / "tsconfig.build.json").is_file():
        build_config = "tsconfig.build.json"
    elif entry_source.is_file():
        return repo_dir
    elif (repo_dir / "tsconfig.json").is_file():
        build_config = "tsconfig.json"
    else:
        raise RuntimeError(
            f"{repository}@{commit} has no tsconfig.build.json, no tsconfig.json and no TypeScript "
            "source entry in package.json: there is no way to build or type-check it"
        )

    build_result = _run([npx_executable, "tsc", "-p", build_config], cwd=repo_dir)
    if build_result.returncode != 0:
        raise RuntimeError(
            f"reference library at {repository}@{commit} failed to build cleanly:\n"
            f"{build_result.stdout}\n{build_result.stderr}"
        )

    declared_types = _declared_types_file(repo_dir)
    if declared_types is not None:
        if not declared_types.is_file():
            declaration_files = sorted(
                path.relative_to(repo_dir).as_posix()
                for path in (repo_dir / "dist").rglob("*.d.ts")
                if path.is_file()
            )
            if not declaration_files:
                raise RuntimeError(
                    f"the build of {repository}@{commit} did not produce the declared types file {declared_types}"
                )
            print(
                f"[foss-mcp] the declared types file {declared_types} was not produced; "
                f"dist/ holds {len(declaration_files)} declaration files (first: {declaration_files[0]}); "
                "relative-path imports are checked against them, package-name imports will not resolve",
                file=sys.stderr,
            )
    else:
        dist_dir = repo_dir / "dist"
        if not dist_dir.is_dir() or not any(dist_dir.iterdir()):
            raise RuntimeError(f"npm run build did not produce dist/ output in {dist_dir}")

    return repo_dir


def _has_relative_module_specifier(code: str) -> bool:
    """True when a static import or export-from in ``code`` names a ``./`` or ``../`` module."""
    return any(
        match.group("spec").startswith(("./", "../"))
        for match in _STATIC_MODULE_DECLARATION_RE.finditer(code)
    )


def _typescript_verdict(returncode: int, output: str) -> tuple[bool, str]:
    """Decide a tsc run from its exit code and output; the note says which rule decided.

    Exit 0 is verified. A non-zero exit is verified only when every ``error TS<digits>`` in the
    output is a parsed ``file(line,col): error TSnnnn: message`` diagnostic (so a file-less config
    error or any unparsed mention fails), at least one parses, and none is in the candidate file
    (``_TS_CANDIDATE_DIAGNOSTIC_FILES``): then the candidate's own use of the library type-checks
    and only the library's own sources fail.
    """
    if returncode == 0:
        return True, "tsc exit 0"
    diagnostics = [match.group("file").strip() for match in _TS_DIAGNOSTIC_LINE_RE.finditer(output)]
    mentions = len(_TS_ERROR_CODE_RE.findall(output))
    if not diagnostics:
        return False, f"tsc exit {returncode}; no tsc diagnostic could be parsed from the output"
    if len(diagnostics) != mentions:
        return False, (
            f"tsc exit {returncode}; {mentions - len(diagnostics)} of {mentions} error mentions "
            "are not attributable to a file"
        )
    candidate_files = [
        name
        for name in diagnostics
        if name.replace("\\", "/").rsplit("/", 1)[-1] in _TS_CANDIDATE_DIAGNOSTIC_FILES
    ]
    if candidate_files:
        return False, (
            f"tsc exit {returncode}; {len(candidate_files)} of {len(diagnostics)} diagnostics "
            "are in the candidate file"
        )
    library_files = sorted(set(diagnostics))
    return True, (
        f"tsc exit {returncode}; all {len(diagnostics)} diagnostics are in library files "
        f"({', '.join(library_files)}) and none in the candidate; "
        "the candidate's own use of the library type-checks"
    )


def verify_typescript_example(
    candidate: CandidateExample,
    *,
    library_dir: Path,
    package_name: str,
    workdir: Path,
) -> VerificationResult:
    """Type-check ``candidate.code`` as a real ``.ts`` file importing
    ``package_name`` from ``library_dir``'s built output (or source), and
    report the real outcome.

    A minimal throwaway ``tsconfig.json`` maps ``package_name`` straight
    at the package entry (``_typescript_types_entry``: ``dist/index`` for
    pdf/typescript, ``dist/src/index`` for jmap, ``index`` for cells) via ``paths``
    (``moduleResolution: "bundler"``), so no second ``npm install`` is needed per
    candidate. Compiling is done with the reference library's own
    already-installed TypeScript compiler (``library_dir/node_modules/typescript``),
    invoked via ``node`` directly rather than through the ``.CMD`` wrapper, run
    with ``--noEmit`` so this is real type-checking, not code generation.

    Repository-root mode (TC-362): a candidate with a static import or export-from
    whose specifier starts with ``./`` or ``../`` (cells and 3d README blocks import
    by path from the repository root, which a ``paths`` mapping can never resolve)
    is written inside ``library_dir`` as ``__foss_mcp_candidate__.ts`` with a
    ``__foss_mcp_tsconfig__.json`` that extends the library's own tsconfig.json,
    and type-checked with ``library_dir`` as the working directory. Both temporary
    files are removed afterwards.

    ``verified`` comes from ``_typescript_verdict`` (TC-452): exit 0, or a non-zero exit whose
    every diagnostic is parsed and attributed to a library file and none to the candidate. The
    output starts with a ``[foss-mcp] verdict:`` line naming the rule that decided. A block that
    holds only import / export-from declarations is rejected without running tsc.
    """
    without_imports = _STATIC_MODULE_DECLARATION_RE.sub("", candidate.code)
    if not _has_own_statements(without_imports, preamble_line=None):
        return VerificationResult(
            candidate=candidate,
            verified=False,
            output=_NO_STATEMENTS_MESSAGE.format(kind="import and export-from declarations"),
        )

    project_dir = workdir / "candidate_project"
    project_dir.mkdir(parents=True, exist_ok=True)

    (project_dir / "candidate.ts").write_text(candidate.code, encoding="utf-8")
    tsc_entrypoint = library_dir / "node_modules" / "typescript" / "lib" / "tsc.js"

    if _has_relative_module_specifier(candidate.code):
        candidate_file = library_dir / _TS_CANDIDATE_FILENAME
        tsconfig_file = library_dir / _TS_ROOT_TSCONFIG_FILENAME
        config: dict = {}
        if (library_dir / "tsconfig.json").is_file():
            config["extends"] = "./tsconfig.json"
        config["compilerOptions"] = {
            "noEmit": True,
            "rootDir": ".",
            "noUnusedLocals": False,
            "noUnusedParameters": False,
            "skipLibCheck": True,
        }
        if "extends" not in config:
            config["compilerOptions"].update(
                {
                    "target": "ES2022",
                    "module": "ESNext",
                    "moduleResolution": "bundler",
                    "strict": True,
                    "esModuleInterop": True,
                }
            )
        config["files"] = [_TS_CANDIDATE_FILENAME]
        # The library's own tsconfig may include its whole tree; only the candidate is checked.
        config["include"] = []
        try:
            candidate_file.write_text(candidate.code, encoding="utf-8")
            tsconfig_file.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
            result = _run(
                ["node", str(tsc_entrypoint), "-p", _TS_ROOT_TSCONFIG_FILENAME],
                cwd=library_dir,
            )
        finally:
            candidate_file.unlink(missing_ok=True)
            tsconfig_file.unlink(missing_ok=True)
    else:
        dist_index = _typescript_types_entry(library_dir).resolve().as_posix()
        tsconfig_content = _TS_TSCONFIG_TEMPLATE.format(
            package_name=package_name,
            dist_index=dist_index,
        )
        (project_dir / "tsconfig.json").write_text(tsconfig_content, encoding="utf-8")

        result = _run(
            ["node", str(tsc_entrypoint), "-p", "tsconfig.json"],
            cwd=project_dir,
        )

    tsc_output = f"{result.stdout}\n{result.stderr}"
    verified, note = _typescript_verdict(result.returncode, tsc_output)
    output = f"[foss-mcp] verdict: {note}\n{_truncate(tsc_output)}"

    return VerificationResult(candidate=candidate, verified=verified, output=output)


# --- jmap/nodejs (TC-362) ----------------------------------------------------

_NODEJS_CANDIDATE_FILENAME = "candidate.mjs"
_NODEJS_LINK_CHECK_FILENAME = "__foss_mcp_link_check__.mjs"


def prepare_nodejs_library(repository: str, commit: str, workdir: Path) -> Path:
    """Shallow-clone ``repository`` at exactly ``commit`` into ``workdir/repo`` and return it.

    ``node`` must be on PATH (``ExampleEnvironmentError`` otherwise: a missing runtime is an
    environment failure, never a verdict). ``npm install --omit=dev`` runs only when package.json
    has a non-empty ``dependencies`` object, so a dependency-free library (jmap/nodejs) runs no npm
    at all. Raises ``RuntimeError`` on a clone or install failure.
    """
    _require_linux_tool("node")
    repo_dir = workdir / "repo"
    _clone_pinned_commit(repository, commit, repo_dir)

    dependencies = _read_package_json(repo_dir).get("dependencies")
    if isinstance(dependencies, dict) and dependencies:
        npm_executable = shutil.which("npm")
        if npm_executable is None:
            raise RuntimeError("npm is not available on PATH")
        install_result = _run([npm_executable, "install", "--omit=dev"], cwd=repo_dir)
        if install_result.returncode != 0:
            raise RuntimeError(
                f"npm install for {repository}@{commit} failed:\n"
                f"{install_result.stdout}\n{install_result.stderr}"
            )
    return repo_dir


def _nodejs_package_entry(library_dir: Path) -> Path:
    """The library's entry file: package.json ``exports["."]`` (string, or the import/default
    condition, nested), else ``main``, else ``index.js``."""
    package = _read_package_json(library_dir)

    def resolve_target(target: object) -> str | None:
        if isinstance(target, str):
            return target
        if isinstance(target, dict):
            for key in ("import", "default", "node", "require"):
                found = resolve_target(target.get(key))
                if found:
                    return found
        return None

    exports = package.get("exports")
    entry = None
    if isinstance(exports, dict):
        entry = resolve_target(exports.get(".")) if "." in exports else resolve_target(exports)
    elif isinstance(exports, str):
        entry = exports
    if not entry and isinstance(package.get("main"), str):
        entry = package["main"]
    return library_dir / (entry or "index.js")


def _link_package_into_project(project_dir: Path, library_dir: Path, package_name: str) -> None:
    """Make ``project_dir/node_modules/<package_name>`` resolve to ``library_dir``.

    A directory symlink when the OS allows one; otherwise (``OSError``, e.g. Windows without
    symlink privilege) a shim package whose ``index.js`` re-exports the library's entry file.
    """
    link = project_dir / "node_modules" / package_name
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink():
        link.unlink()
    elif link.exists():
        shutil.rmtree(link)
    try:
        os.symlink(library_dir.resolve(), link, target_is_directory=True)
    except OSError:
        link.mkdir(parents=True, exist_ok=True)
        (link / "package.json").write_text(json.dumps({"type": "module"}) + "\n", encoding="utf-8")
        entry_url = _nodejs_package_entry(library_dir).resolve().as_uri()
        (link / "index.js").write_text(f"export * from {json.dumps(entry_url)};\n", encoding="utf-8")


def _package_import_declarations(code: str, package_name: str) -> list[str]:
    """The candidate's static ``import`` declarations whose specifier is ``package_name`` or
    starts with ``package_name + "/"``, each as source text ending in a semicolon."""
    declarations = []
    for match in _STATIC_MODULE_DECLARATION_RE.finditer(code):
        spec = match.group("spec")
        if match.group("kw") == "import" and (spec == package_name or spec.startswith(package_name + "/")):
            declaration = match.group("decl").strip()
            declarations.append(declaration if declaration.endswith(";") else declaration + ";")
    return declarations


def verify_nodejs_example(
    candidate: CandidateExample,
    *,
    library_dir: Path,
    package_name: str,
    workdir: Path,
) -> VerificationResult:
    """Check ``candidate.code`` as plain ESM JavaScript against the library at ``library_dir``.

    This is a WEAKER bar than compilation: nothing is type-checked and the candidate is never
    executed. Two steps, both of which must exit 0 for ``verified``: (a) ``node --check`` on
    ``candidate.mjs`` (syntax), and (b) a link check - a generated module holding only the
    candidate's static imports of ``package_name`` (or ``package_name/...``), run with ``node``,
    so a named import the library does not export fails at module link time without running the
    candidate. A candidate with no such import is not verified (a fragment that uses names it
    never imports proves nothing) and its output says so.

    ``node_modules/<package_name>`` in ``workdir/candidate_project`` resolves to ``library_dir``
    (a symlink, or a re-exporting shim when symlinks are refused).
    """
    project_dir = workdir / "candidate_project"
    project_dir.mkdir(parents=True, exist_ok=True)
    candidate_file = project_dir / _NODEJS_CANDIDATE_FILENAME
    candidate_file.write_text(candidate.code, encoding="utf-8")
    _link_package_into_project(project_dir, library_dir, package_name)

    syntax = _run(["node", "--check", _NODEJS_CANDIDATE_FILENAME], cwd=project_dir)
    output = f"{syntax.stdout}\n{syntax.stderr}"
    if syntax.returncode != 0:
        return VerificationResult(candidate=candidate, verified=False, output=_truncate(output))

    declarations = _package_import_declarations(candidate.code, package_name)
    if not declarations:
        output += (
            f"\nnot verified: the candidate has no static import of {package_name!r}, so there is "
            "nothing to check against the library"
        )
        return VerificationResult(candidate=candidate, verified=False, output=_truncate(output))

    (project_dir / _NODEJS_LINK_CHECK_FILENAME).write_text("\n".join(declarations) + "\n", encoding="utf-8")
    link = _run(["node", _NODEJS_LINK_CHECK_FILENAME], cwd=project_dir)
    output += f"\n{link.stdout}\n{link.stderr}"
    verified = syntax.returncode == 0 and link.returncode == 0
    return VerificationResult(candidate=candidate, verified=verified, output=_truncate(output))


# --- pdf/cpp on Linux (TC-222) -----------------------------------------------
#
# The Linux ingestion image compiles C++ candidates with g++ and CMake. The
# pinned pdf/cpp library (aspose-pdf-foss/Aspose.PDF-FOSS-for-Cpp at
# 4b83c9fec1e37fd205156770161f6843bac00ceb) was confirmed on 2026-10-04 to
# configure and build with g++ 14 and CMake 3.31 on Linux, with twelve
# examples linking against the result (DECISION_LOG 2026-10-04).
#
# These functions sit beside the MSVC pair above and do not replace it: the
# MSVC pair serves cells/cpp on the Windows host and its tests import the
# same names, so the Linux pair carries a _linux suffix. The dispatch in
# infra/build_chunks.py selects this pair for --library-platform cpp.
#
# The library's CMakeLists.txt has no option that disables its examples or
# its gtest suite, so only the aspose_pdf_foss target is built. That gives
# the same effect: no example or test executable is compiled. The configure
# step still runs FetchContent for googletest (v1.14.0, from github.com)
# unconditionally, so configuring needs the same GitHub access as the pinned
# fetch; no separate host is involved.

_CPP_LINUX_COMPILER = "g++"
_CPP_LINUX_CMAKE = "cmake"
_CPP_LINUX_LIBRARY_TARGET = "aspose_pdf_foss"
_CPP_LINUX_ARTIFACT_NAME = "libaspose_pdf_foss.a"
_CPP_LINUX_BUILD_DIRNAME = "_build"
_CPP_LINUX_INCLUDE_DIRNAME = "include"
_CPP_LINUX_RUN_TIMEOUT_SECONDS = 30


def _require_linux_tool(tool: str) -> None:
    """Raise ``ExampleEnvironmentError`` when ``tool`` is not on PATH.

    A missing compiler is an environment failure, never a verdict on a
    candidate, so it is raised rather than returned as not verified.
    """
    if shutil.which(tool) is None:
        raise ExampleEnvironmentError(
            tool, "not found on PATH", f"{tool} is not installed in this environment"
        )


# TC-361 (REQ-G3-005): opt-in consumer-project mode. cells/cpp, slides/cpp, email/cpp and
# jmap/cpp are not one archive named like pdf/cpp's: their CMake targets differ, one is
# header-only (INTERFACE, no archive), and slides/cpp pulls its own dependencies in with
# FetchContent. A tiny CMake project that add_subdirectory()s the library and links a
# candidate executable to the library's target lets CMake supply includes, dependencies and
# the language level. pdf/cpp does not opt in and keeps its exact command lines.
_CPP_CONSUMER_DIRNAME = "_foss_mcp_consumer"
_CPP_CONSUMER_TARGET = "foss_mcp_candidate"
_CPP_TARGET_RE = re.compile(r"^[A-Za-z0-9_.+-]+$")
_CPP_MAIN_RE = re.compile(r"\bmain\s*\(")
_CPP_PREAMBLE_LINE_RE = re.compile(r"^\s*(?:#\s*include\b|using\b)")

# TC-455 (REQ-G3-007): opt-in per-pilot C++ compatibility switches for consumer mode.
# ``no-changes-meaning`` and ``include-limits`` become global compile options (so the
# library and the candidate both get them); ``include-subdirs`` puts every directory
# directly under ``<library>/include`` on the candidate's include path; ``page-includes``
# puts the page's own ``#include`` lines in front of each candidate. Names, never raw
# compiler flags, are what the deploy layer forwards (a flag value begins with a dash).
CPP_COMPAT_NAMES = ("no-changes-meaning", "include-limits", "include-subdirs", "page-includes")
_CPP_COMPAT_COMPILE_OPTIONS = {
    "no-changes-meaning": "-Wno-changes-meaning",
    "include-limits": "SHELL:-include limits",
}


def cpp_compat_compile_options(names: Iterable[str]) -> tuple[str, ...]:
    """Return the compile options for the C++ compat ``names``, in the order given.

    ``include-subdirs`` and ``page-includes`` add no compile option. Raises
    ``ValueError`` naming an unknown name.
    """
    options: list[str] = []
    for name in names:
        if name not in CPP_COMPAT_NAMES:
            raise ValueError(
                f"unknown C++ compat name {name!r}; expected one of {', '.join(CPP_COMPAT_NAMES)}"
            )
        option = _CPP_COMPAT_COMPILE_OPTIONS.get(name)
        if option is not None:
            options.append(option)
    return tuple(options)


def _validate_cpp_source_subdir(source_subdir: str) -> None:
    """Raise ``ValueError`` unless ``source_subdir`` is a relative path without ``..``."""
    parts = source_subdir.replace("\\", "/").split("/")
    if (
        not source_subdir
        or Path(source_subdir).is_absolute()
        or source_subdir.startswith(("/", "\\"))
        or re.match(r"^[A-Za-z]:", source_subdir)
        or ".." in parts
    ):
        raise ValueError(
            f"C++ library source directory must be a relative path without '..', got {source_subdir!r}"
        )


def _validate_cpp_target(target: str) -> None:
    if not _CPP_TARGET_RE.fullmatch(target):
        raise ValueError(f"C++ library target {target!r} does not match {_CPP_TARGET_RE.pattern}")


def _wrap_cpp_fragment_if_needed(code: str) -> str:
    """Wrap a C++ README fragment that has no ``main`` in ``int main() { ... return 0; }``.

    Code that matches ``\\bmain\\s*\\(`` is returned unchanged, byte for byte. Otherwise
    the leading ``#include`` and ``using`` lines (and the blank lines among them) stay at the
    top of the file, and everything after them is placed inside ``main``.
    """
    if _CPP_MAIN_RE.search(code) is not None:
        return code

    lines = code.splitlines()
    split = 0
    for index, line in enumerate(lines):
        if _CPP_PREAMBLE_LINE_RE.match(line):
            split = index + 1
        elif line.strip():
            break
    parts = [*lines[:split], "int main() {", *lines[split:], "    return 0;", "}"]
    return "\n".join(parts) + "\n"


def _write_cpp_consumer_project(
    consumer_dir: Path,
    library_source_dir: Path,
    target: str,
    relax_warnings: bool,
    *,
    compile_options: Sequence[str] = (),
    include_subdirs: bool = False,
) -> None:
    """Write the consumer ``CMakeLists.txt`` and a placeholder ``candidate.cpp`` into ``consumer_dir``.

    The project adds ``library_source_dir`` as a subdirectory and links the candidate
    executable to ``target``. ``relax_warnings`` appends ``-Wno-error`` to the target's
    private compile options after the library's own ``-Werror``, so it wins.
    TC-455: non-empty ``compile_options`` are written as one ``add_compile_options(...)``
    line before ``add_subdirectory``; ``include_subdirs`` adds every directory directly
    under ``<library_source_dir>/include`` to the candidate's include path.
    Raises ``ValueError`` for a malformed target or a library source directory that
    does not exist.
    """
    _validate_cpp_target(target)
    if not library_source_dir.is_dir():
        raise ValueError(f"C++ library source directory {library_source_dir} does not exist")

    consumer_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "cmake_minimum_required(VERSION 3.16)",
        "project(foss_mcp_consumer CXX)",
        "set(CMAKE_CXX_STANDARD 20)",
        "set(CMAKE_CXX_STANDARD_REQUIRED ON)",
    ]
    if compile_options:
        lines.append("add_compile_options(" + " ".join(f'"{option}"' for option in compile_options) + ")")
    lines += [
        f'add_subdirectory("{library_source_dir.as_posix()}" foss_mcp_library EXCLUDE_FROM_ALL)',
    ]
    if relax_warnings:
        lines.append(f"target_compile_options({target} PRIVATE -Wno-error)")
    lines.append(f"add_executable({_CPP_CONSUMER_TARGET} candidate.cpp)")
    include_root = library_source_dir / "include"
    if include_subdirs and include_root.is_dir():
        subdirs = sorted(entry.as_posix() for entry in include_root.iterdir() if entry.is_dir())
        if subdirs:
            quoted = " ".join(f'"{subdir}"' for subdir in subdirs)
            lines.append(f"target_include_directories({_CPP_CONSUMER_TARGET} PRIVATE {quoted})")
    lines += [
        f"target_link_libraries({_CPP_CONSUMER_TARGET} PRIVATE {target})",
    ]
    (consumer_dir / "CMakeLists.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (consumer_dir / "candidate.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")


def _prepare_cpp_consumer(
    repository: str,
    commit: str,
    workdir: Path,
    source_subdir: str | None,
    target: str,
    relax_warnings: bool,
    *,
    compile_options: Sequence[str] = (),
    include_subdirs: bool = False,
) -> Path:
    """Write, configure and build the consumer project; return its build directory."""
    library_source_dir = workdir / source_subdir if source_subdir else workdir
    consumer_dir = workdir / _CPP_CONSUMER_DIRNAME
    _write_cpp_consumer_project(
        consumer_dir,
        library_source_dir,
        target,
        relax_warnings,
        compile_options=compile_options,
        include_subdirs=include_subdirs,
    )
    build_dir = consumer_dir / _CPP_LINUX_BUILD_DIRNAME
    build_dir.mkdir(parents=True, exist_ok=True)

    configure_result = subprocess.run(
        [
            _CPP_LINUX_CMAKE,
            "-S",
            str(consumer_dir),
            "-B",
            str(build_dir),
            "-G",
            "Unix Makefiles",
            "-DCMAKE_BUILD_TYPE=Release",
            f"-DCMAKE_CXX_COMPILER={_CPP_LINUX_COMPILER}",
            "-DBUILD_TESTING=OFF",
        ],
        cwd=workdir,
        capture_output=True,
        text=True,
    )
    if configure_result.returncode != 0:
        raise RuntimeError(
            f"cmake configure of the consumer project for {repository}@{commit} failed:\n"
            f"{configure_result.stdout}\n{configure_result.stderr}"
        )

    build_result = subprocess.run(
        [_CPP_LINUX_CMAKE, "--build", str(build_dir), "--target", _CPP_CONSUMER_TARGET, "--parallel"],
        cwd=workdir,
        capture_output=True,
        text=True,
    )
    if build_result.returncode != 0:
        raise RuntimeError(
            f"build of {target} (through {_CPP_CONSUMER_TARGET}) for {repository}@{commit} failed:\n"
            f"{build_result.stdout}\n{build_result.stderr}"
        )
    return build_dir


def prepare_cpp_library_linux(
    repository: str,
    commit: str,
    workdir: Path,
    *,
    source_subdir: str | None = None,
    target: str | None = None,
    relax_warnings: bool = False,
    compile_options: Sequence[str] = (),
    include_subdirs: bool = False,
) -> Path:
    """Shallow-fetch ``repository`` at exactly ``commit`` into ``workdir``,
    configure it with CMake (Release, g++ as the C++ compiler), and build the
    ``aspose_pdf_foss`` static library. Returns the build directory, which
    holds ``libaspose_pdf_foss.a``. The header root is the sibling
    ``include`` directory of that build directory, which is what
    ``verify_cpp_example_linux`` takes as ``include_dir``.

    Raises ``ExampleEnvironmentError`` when g++ or cmake is missing, or when
    the pinned commit cannot be fetched. Raises ``RuntimeError`` when the
    library itself fails to configure or build, because the reference library
    must always build clean.

    TC-361: when ``target`` is given, the library is instead built through a
    generated consumer CMake project (see ``_write_cpp_consumer_project``) that
    add_subdirectory()s ``source_subdir`` (default: the repository root) and links a
    candidate executable to ``target``. The return value is then the consumer's
    build directory, ``workdir/_foss_mcp_consumer/_build``. ``relax_warnings``
    appends ``-Wno-error`` to ``target``. Raises ``ValueError`` for a malformed
    ``target``, or a ``source_subdir`` that is absolute, holds ``..``, or does not
    exist after the clone. TC-455: ``compile_options`` and ``include_subdirs`` are
    passed to the consumer project (consumer mode only).
    """
    if target is not None:
        _validate_cpp_target(target)
        if source_subdir:
            _validate_cpp_source_subdir(source_subdir)

    _require_linux_tool(_CPP_LINUX_COMPILER)
    _require_linux_tool(_CPP_LINUX_CMAKE)

    try:
        _clone_pinned_commit(repository, commit, workdir)
    except RuntimeError as exc:
        raise ExampleEnvironmentError("git", "fetch of the pinned commit failed", str(exc)) from exc

    if target is not None:
        return _prepare_cpp_consumer(
            repository,
            commit,
            workdir,
            source_subdir,
            target,
            relax_warnings,
            compile_options=compile_options,
            include_subdirs=include_subdirs,
        )

    build_dir = workdir / _CPP_LINUX_BUILD_DIRNAME
    build_dir.mkdir(parents=True, exist_ok=True)

    configure_result = subprocess.run(
        [
            _CPP_LINUX_CMAKE,
            "-S",
            str(workdir),
            "-B",
            str(build_dir),
            "-G",
            "Unix Makefiles",
            "-DCMAKE_BUILD_TYPE=Release",
            f"-DCMAKE_CXX_COMPILER={_CPP_LINUX_COMPILER}",
        ],
        cwd=workdir,
        capture_output=True,
        text=True,
    )
    if configure_result.returncode != 0:
        raise RuntimeError(
            f"cmake configure of {repository}@{commit} failed:\n"
            f"{configure_result.stdout}\n{configure_result.stderr}"
        )

    build_result = subprocess.run(
        [_CPP_LINUX_CMAKE, "--build", str(build_dir), "--target", _CPP_LINUX_LIBRARY_TARGET, "--parallel"],
        cwd=workdir,
        capture_output=True,
        text=True,
    )
    if build_result.returncode != 0:
        raise RuntimeError(
            f"build of {_CPP_LINUX_LIBRARY_TARGET} for {repository}@{commit} failed:\n"
            f"{build_result.stdout}\n{build_result.stderr}"
        )

    if not (build_dir / _CPP_LINUX_ARTIFACT_NAME).is_file():
        raise RuntimeError(
            f"cmake reported success building {_CPP_LINUX_LIBRARY_TARGET} but "
            f"{build_dir / _CPP_LINUX_ARTIFACT_NAME} was not produced"
        )

    return build_dir


def verify_cpp_example_linux(
    candidate: CandidateExample,
    workdir: Path,
    library_dir: Path,
    include_dir: Path,
    *,
    consumer: bool = False,
    run: bool = True,
    page_includes: Sequence[str] = (),
) -> VerificationResult:
    """Compile and link ``candidate.code`` as a throwaway ``candidate.cpp``
    against ``library_dir/libaspose_pdf_foss.a`` (``library_dir`` is the
    build directory from ``prepare_cpp_library_linux``) with ``include_dir``
    on the include path, then run the executable under a timeout.

    A candidate is verified only when it compiles, links and exits 0. A
    compile or link error, a non-zero exit, or a run that exceeds the timeout
    is a candidate failure and is returned as not verified. A missing g++ is
    an environment failure and raises ``ExampleEnvironmentError``.

    A candidate without ``main`` is first wrapped by ``_wrap_cpp_fragment_if_needed``.
    TC-361: with ``consumer=True`` ``library_dir`` is the consumer project's build
    directory; the wrapped code is written to the consumer's ``candidate.cpp`` and built
    through CMake as the ``foss_mcp_candidate`` target. With ``run=False`` a successful
    compile-and-link is the verdict and the executable is not run.
    TC-455: every ``page_includes`` line that is not already a line of the candidate's
    code is put in front of the code, in order, before the fragment wrapper runs. The
    returned result carries the ORIGINAL candidate (that is what is published) and its
    output begins with ``page includes prefixed: ...`` when anything was prefixed.
    """
    _require_linux_tool(_CPP_LINUX_COMPILER)
    if consumer:
        _require_linux_tool(_CPP_LINUX_CMAKE)

    project_dir = workdir / "candidate_project"
    project_dir.mkdir(parents=True, exist_ok=True)
    existing_lines = {line.strip() for line in candidate.code.splitlines()}
    prefixed: list[str] = []
    for include_line in page_includes:
        if include_line.strip() not in existing_lines and include_line not in prefixed:
            prefixed.append(include_line)
    source_code = "\n".join([*prefixed, candidate.code]) if prefixed else candidate.code
    code = _wrap_cpp_fragment_if_needed(source_code)

    if consumer:
        source_path = library_dir.parent / "candidate.cpp"
        output_exe = library_dir / _CPP_CONSUMER_TARGET
        compile_argv = [_CPP_LINUX_CMAKE, "--build", str(library_dir), "--target", _CPP_CONSUMER_TARGET]
    else:
        source_path = project_dir / "candidate.cpp"
        output_exe = project_dir / "candidate"
        compile_argv = [
            _CPP_LINUX_COMPILER,
            "-std=c++20",
            f"-I{include_dir}",
            str(source_path),
            str(library_dir / _CPP_LINUX_ARTIFACT_NAME),
            "-o",
            str(output_exe),
        ]
    source_path.write_text(code, encoding="utf-8")
    note = f"page includes prefixed: {' | '.join(prefixed)}\n" if prefixed else ""

    compile_result = _run(compile_argv, cwd=project_dir)
    if compile_result.returncode != 0:
        output = note + _truncate(f"{compile_result.stdout}\n{compile_result.stderr}")
        return VerificationResult(candidate=candidate, verified=False, output=output)

    if not run:
        output = note + _truncate(f"{compile_result.stdout}\n{compile_result.stderr}")
        return VerificationResult(candidate=candidate, verified=True, output=output)

    try:
        run_result = subprocess.run(
            [str(output_exe)],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=_CPP_LINUX_RUN_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        output = note + _truncate(
            f"run exceeded {_CPP_LINUX_RUN_TIMEOUT_SECONDS}s and was stopped\n"
            f"{exc.stdout or ''}\n{exc.stderr or ''}"
        )
        return VerificationResult(candidate=candidate, verified=False, output=output)

    verified = run_result.returncode == 0
    output = note + _truncate(
        f"{compile_result.stdout}\n{compile_result.stderr}\n{run_result.stdout}\n{run_result.stderr}"
    )
    return VerificationResult(candidate=candidate, verified=verified, output=output)
