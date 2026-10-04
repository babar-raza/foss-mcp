"""Offline dispatch coverage for the Go and Java platforms (REQ-G2-048, TC-188).

tests/infra/test_build_chunks.py covers the dotnet and python entries of infra/build_chunks.py's
_PLATFORM_DISPATCH table, through real networked end-to-end runs. Nothing covered the go or java
entries, so a wrong entry (for example the go verify pointing at the java function) would pass
every test. These tests read the real table and check each entry's keys, the function it names,
the kwargs it produces, and the _PLATFORM_REQUIRED_FLAGS table.

They never call a prepare or verify function, so they need no network, git, Go, JDK or Maven.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

import infra.build_chunks as build_chunks
from foss_mcp.indexing.example_verifier import verify_go_example, verify_java_example

GO_REPOSITORY = "example-org/reference-go"
GO_COMMIT = "0123456789abcdef0123456789abcdef01234567"
GO_MODULE_PATH = "github.com/example-org/reference-go"
JAVA_REPOSITORY = "example-org/reference-java"
JAVA_COMMIT = "89abcdef0123456789abcdef0123456789abcdef"


def _record_prepare(monkeypatch: pytest.MonkeyPatch, name: str, sentinel: object) -> list[tuple]:
    """Replace a real prepare_<lang>_library by name inside build_chunks, so the dispatch entry's
    prepare lambda can be called without any network or toolchain. Returns the call log.
    """
    calls: list[tuple] = []

    def _recorder(repository: str, commit: str, workdir: Path) -> object:
        calls.append((repository, commit, workdir))
        return sentinel

    monkeypatch.setattr(build_chunks, name, _recorder)
    return calls


def test_go_entry_has_prepare_verify_and_verify_kwargs_and_verify_is_verify_go_example(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The go entry of _PLATFORM_DISPATCH has exactly the three documented keys, its verify is the
    real verify_go_example from example_verifier, and its prepare calls prepare_go_library with
    the repository, commit and workdir it is given.
    """
    entry = build_chunks._PLATFORM_DISPATCH["go"]

    assert set(entry) == {"prepare", "verify", "verify_kwargs", "shared_page_workdir"}
    assert entry["verify"] is verify_go_example
    assert entry["shared_page_workdir"] is False

    sentinel = object()
    calls = _record_prepare(monkeypatch, "prepare_go_library", sentinel)
    args = argparse.Namespace(library_repository=GO_REPOSITORY, library_commit=GO_COMMIT)

    assert entry["prepare"](args, tmp_path) is sentinel
    assert calls == [(GO_REPOSITORY, GO_COMMIT, tmp_path)]


def test_go_verify_kwargs_gives_module_path_and_library_dir_from_the_arguments() -> None:
    """The go verify_kwargs builds module_path from --library-module-path and library_dir from the
    prepared library, and reads no other CLI attribute.
    """
    args = argparse.Namespace(library_module_path=GO_MODULE_PATH)
    prepared = Path("workdir") / "go-library"

    kwargs = build_chunks._PLATFORM_DISPATCH["go"]["verify_kwargs"](prepared, args)

    assert kwargs == {"module_path": GO_MODULE_PATH, "library_dir": prepared}


def test_java_entry_has_prepare_verify_and_verify_kwargs_and_verify_is_verify_java_example(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The java entry of _PLATFORM_DISPATCH has exactly the three documented keys, its verify is the
    real verify_java_example from example_verifier, and its prepare calls prepare_java_library with
    the repository, commit and workdir it is given.
    """
    entry = build_chunks._PLATFORM_DISPATCH["java"]

    assert set(entry) == {"prepare", "verify", "verify_kwargs", "shared_page_workdir"}
    assert entry["verify"] is verify_java_example
    assert entry["shared_page_workdir"] is False

    sentinel = object()
    calls = _record_prepare(monkeypatch, "prepare_java_library", sentinel)
    args = argparse.Namespace(library_repository=JAVA_REPOSITORY, library_commit=JAVA_COMMIT)

    assert entry["prepare"](args, tmp_path) is sentinel
    assert calls == [(JAVA_REPOSITORY, JAVA_COMMIT, tmp_path)]


def test_python_entry_shares_one_page_workdir() -> None:
    """The python entry of _PLATFORM_DISPATCH has the same four keys as the go and java entries, and
    its shared_page_workdir is True, so the candidates of one page run in one shared workdir in
    document order. Turning that flag off must fail this test.
    """
    entry = build_chunks._PLATFORM_DISPATCH["python"]

    assert set(entry) == {"prepare", "verify", "verify_kwargs", "shared_page_workdir"}
    assert entry["shared_page_workdir"] is True


def test_java_verify_kwargs_gives_library_jar() -> None:
    """The java verify_kwargs passes the prepared jar as library_jar. It reads no CLI attribute,
    so an empty argument namespace must be accepted.
    """
    prepared = Path("workdir") / "reference-java.jar"

    kwargs = build_chunks._PLATFORM_DISPATCH["java"]["verify_kwargs"](prepared, argparse.Namespace())

    assert kwargs == {"library_jar": prepared, "page_imports": ()}


def test_required_flags_table_matches_what_go_and_java_entries_use(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """_PLATFORM_REQUIRED_FLAGS lists go with exactly the CLI attribute its verify_kwargs reads, and
    does not list java, whose verify_kwargs needs no CLI flag. The go flag is also enforced by the
    real CLI: --library-platform go without --library-module-path is a usage error that names it.
    """
    required = build_chunks._PLATFORM_REQUIRED_FLAGS

    assert "java" not in required

    attr_name, flag_name = required["go"]
    assert flag_name == "--" + attr_name.replace("_", "-")
    go_kwargs = build_chunks._PLATFORM_DISPATCH["go"]["verify_kwargs"](
        Path("workdir"), argparse.Namespace(**{attr_name: GO_MODULE_PATH})
    )
    assert go_kwargs["module_path"] == GO_MODULE_PATH

    out_path = tmp_path / "chunks.json"
    old_argv = sys.argv
    sys.argv = [
        "build_chunks.py",
        "--api-surface",
        "tests/fixtures/pdf_net/api_surface.json",
        "--title",
        "Reference Go",
        "--out",
        str(out_path),
        "--furnished-page",
        "tests/fixtures/furnished/pdf_net/pages/_index.md",
        "--library-repository",
        GO_REPOSITORY,
        "--library-commit",
        GO_COMMIT,
        "--library-platform",
        "go",
        # --library-module-path deliberately omitted
    ]
    try:
        with pytest.raises(SystemExit) as excinfo:
            build_chunks.main()
    finally:
        sys.argv = old_argv

    assert excinfo.value.code == 2
    assert "--library-platform go requires --library-module-path" in capsys.readouterr().err
    assert not out_path.exists()
