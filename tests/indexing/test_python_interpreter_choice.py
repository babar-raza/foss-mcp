"""Offline tests for the Python example verifier's interpreter choice.

``prepare_python_library`` must build the library's venv with an interpreter
that satisfies the library's own ``requires-python``, not blindly with the one
running the verifier. Every test here is offline: version lookups are injected
and the clone/venv/pip subprocesses are replaced by recorders.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from foss_mcp.indexing import example_verifier
from foss_mcp.indexing.example_verifier import (
    _pick_python_interpreter,
    _python_version_satisfies,
    _read_requires_python,
    prepare_python_library,
)

RUNNING = "/opt/running/python"
PY313 = "/usr/bin/python3.13"
PY312 = "/usr/bin/python3.12"
PY311 = "/usr/bin/python3.11"
PY310 = "/usr/bin/python3.10"


def _versions(**present: tuple[int, ...] | None) -> dict[str, tuple[int, ...] | None]:
    """Ordered candidates as the real chooser sees them: running first."""
    table: dict[str, tuple[int, ...] | None] = {RUNNING: (3, 13, 1)}
    for key, path in (("p313", PY313), ("p312", PY312), ("p311", PY311), ("p310", PY310)):
        if key in present:
            table[path] = present[key]
    return table


# --- _pick_python_interpreter ------------------------------------------------


@pytest.mark.parametrize("requirement", [None, "", "   "])
def test_no_requirement_keeps_the_running_interpreter(requirement: str | None) -> None:
    table = _versions(p312=(3, 12, 7))
    assert _pick_python_interpreter(requirement, table) == RUNNING


def test_requirement_the_running_interpreter_satisfies_keeps_it() -> None:
    table = _versions(p312=(3, 12, 7))
    assert _pick_python_interpreter(">=3.10", table) == RUNNING


def test_upper_bound_below_running_picks_python312_when_present() -> None:
    table = _versions(p312=(3, 12, 7), p311=(3, 11, 9), p310=(3, 10, 14))
    assert _pick_python_interpreter("<3.13,>=3.10", table) == PY312


def test_upper_bound_below_running_falls_back_to_python311() -> None:
    table = _versions(p311=(3, 11, 9), p310=(3, 10, 14))
    assert _pick_python_interpreter("<3.13,>=3.10", table) == PY311


def test_upper_bound_below_running_falls_back_to_python310() -> None:
    table = _versions(p310=(3, 10, 14))
    assert _pick_python_interpreter("<3.13,>=3.10", table) == PY310


def test_nothing_satisfying_raises_naming_requirement_and_every_interpreter() -> None:
    table = _versions(p312=(3, 12, 7), p311=(3, 11, 9))
    with pytest.raises(RuntimeError) as excinfo:
        _pick_python_interpreter(">=3.14", table)
    message = str(excinfo.value)
    assert ">=3.14" in message
    for path, version in ((RUNNING, "3.13.1"), (PY312, "3.12.7"), (PY311, "3.11.9")):
        assert path in message
        assert version in message


def test_unrunnable_interpreter_is_reported_as_tried_and_skipped() -> None:
    table = _versions(p312=None, p311=(3, 11, 9))
    assert _pick_python_interpreter("<3.12", table) == PY311
    with pytest.raises(RuntimeError, match="version unavailable"):
        _pick_python_interpreter(">=3.14", table)


def test_only_the_running_interpreter_and_it_fails_the_requirement() -> None:
    with pytest.raises(RuntimeError, match=r"<3\.13"):
        _pick_python_interpreter("<3.13", _versions())


def test_exact_wildcard_requirement_picks_that_minor() -> None:
    table = _versions(p312=(3, 12, 7), p311=(3, 11, 9))
    assert _pick_python_interpreter("==3.12.*", table) == PY312


def test_compatible_release_accepts_311_and_312_but_not_310() -> None:
    assert _pick_python_interpreter("~=3.11", _versions(p311=(3, 11, 9))) == RUNNING
    assert _pick_python_interpreter("~=3.11,<3.13", _versions(p312=(3, 12, 7))) == PY312
    assert _pick_python_interpreter("~=3.11,<3.12", _versions(p311=(3, 11, 9))) == PY311
    with pytest.raises(RuntimeError):
        _pick_python_interpreter("~=3.11,<3.11", _versions(p310=(3, 10, 14)))
    assert not _python_version_satisfies((3, 10, 14), "~=3.11")
    assert _python_version_satisfies((3, 11, 0), "~=3.11")
    assert _python_version_satisfies((3, 12, 7), "~=3.11")


def test_exclusion_clause_skips_python313() -> None:
    table = _versions(p312=(3, 12, 7))
    assert _pick_python_interpreter("!=3.13.*,>=3.10", table) == PY312


def test_callable_lookup_is_applied_to_the_default_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(example_verifier, "_candidate_interpreters", lambda: [RUNNING, PY312])
    lookup = {RUNNING: (3, 13, 0), PY312: (3, 12, 0)}.get
    assert _pick_python_interpreter("<3.13", lookup) == PY312


def test_unparseable_requirement_behaves_as_if_nothing_declared() -> None:
    assert _pick_python_interpreter("python >= banana", _versions(p312=(3, 12, 7))) == RUNNING


@pytest.mark.parametrize(
    ("version", "spec", "expected"),
    [
        ((3, 13, 1), "<3.13", False),
        ((3, 12, 9), "<3.13", True),
        ((3, 13, 1), "<=3.13", False),
        ((3, 13, 0), "<=3.13", True),
        ((3, 13, 1), ">3.13", True),
        ((3, 13, 0), ">3.13", False),
        ((3, 13, 1), ">=3.13", True),
        ((3, 12, 9), "==3.12", False),
        ((3, 12, 0), "==3.12", True),
        ((3, 12, 9), "==3.12.*", True),
        ((3, 13, 0), "==3.12.*", False),
        ((3, 12, 9), "!=3.12.*", False),
        ((3, 11, 9), "!=3.12.*", True),
        ((3, 11, 4), "~=3.11.2", True),
        ((3, 12, 0), "~=3.11.2", False),
        ((3, 11, 1), "~=3.11.2", False),
        ((3, 12, 5), ">=3.9, <3.13", True),
        ((3, 12, 5), " >= 3.9 , < 3.12 ", False),
        ((3, 12, 5), ">=3.9,", True),
    ],
)
def test_specifier_table(version: tuple[int, ...], spec: str, expected: bool) -> None:
    assert _python_version_satisfies(version, spec) is expected


@pytest.mark.parametrize("spec", ["banana", ">=", "===3.12", ">3.12.*", "~=3", ">=3.12 extra"])
def test_unsupported_specifiers_are_rejected(spec: str) -> None:
    with pytest.raises(ValueError):
        _python_version_satisfies((3, 12, 0), spec)


# --- reading requires-python from the clone ---------------------------------


def test_read_requires_python_variants(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nrequires-python = "<3.13,>=3.10"\n', encoding="utf-8"
    )
    assert _read_requires_python(tmp_path) == "<3.13,>=3.10"
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
    assert _read_requires_python(tmp_path) is None
    (tmp_path / "pyproject.toml").write_text("[project\nbroken", encoding="utf-8")
    assert _read_requires_python(tmp_path) is None
    (tmp_path / "pyproject.toml").write_text('[tool.x]\nrequires-python = ">=3.10"\n', encoding="utf-8")
    assert _read_requires_python(tmp_path) is None
    (tmp_path / "pyproject.toml").unlink()
    assert _read_requires_python(tmp_path) is None


# --- prepare_python_library integration ------------------------------------


class _Recorder:
    def __init__(self) -> None:
        self.argvs: list[list[str]] = []

    def fake_run(self, args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        self.argvs.append(list(args))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")


def _stage_prepare(
    monkeypatch: pytest.MonkeyPatch,
    pyproject_text: str | None,
    versions: dict[str, tuple[int, ...] | None],
) -> _Recorder:
    recorder = _Recorder()

    def fake_clone(repository: str, commit: str, repo_dir: Path) -> None:
        repo_dir.mkdir(parents=True)
        if pyproject_text is not None:
            (repo_dir / "pyproject.toml").write_text(pyproject_text, encoding="utf-8")

    monkeypatch.setattr(example_verifier, "_clone_pinned_commit", fake_clone)
    monkeypatch.setattr(example_verifier, "_run", recorder.fake_run)
    monkeypatch.setattr(example_verifier, "_candidate_interpreters", lambda: list(versions))
    monkeypatch.setattr(example_verifier, "_interpreter_version", versions.get)
    return recorder


def test_prepare_creates_the_venv_with_the_interpreter_the_library_requires(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pyproject = '[project]\nname = "words"\nrequires-python = "<3.13,>=3.10"\n'
    recorder = _stage_prepare(monkeypatch, pyproject, _versions(p312=(3, 12, 7)))
    prepare_python_library("https://example.invalid/words", "abc123", tmp_path)
    venv_argv = recorder.argvs[0]
    assert venv_argv[0] == PY312
    assert venv_argv[1:3] == ["-m", "venv"]
    pip_argv = recorder.argvs[1]
    assert pip_argv[1:5] == ["-m", "pip", "install", "-e"]


def test_prepare_without_requires_python_keeps_sys_executable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    table = {sys.executable: tuple(sys.version_info[:3]), PY312: (3, 12, 7)}
    recorder = _stage_prepare(monkeypatch, '[project]\nname = "plain"\n', table)
    prepare_python_library("https://example.invalid/plain", "abc123", tmp_path)
    assert recorder.argvs[0][0] == sys.executable


def test_prepare_with_broken_toml_keeps_sys_executable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    table = {sys.executable: tuple(sys.version_info[:3]), PY312: (3, 12, 7)}
    recorder = _stage_prepare(monkeypatch, "[project\nnot toml", table)
    prepare_python_library("https://example.invalid/broken", "abc123", tmp_path)
    assert recorder.argvs[0][0] == sys.executable


def test_prepare_without_pyproject_keeps_sys_executable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    table = {sys.executable: tuple(sys.version_info[:3]), PY312: (3, 12, 7)}
    recorder = _stage_prepare(monkeypatch, None, table)
    prepare_python_library("https://example.invalid/none", "abc123", tmp_path)
    assert recorder.argvs[0][0] == sys.executable


def test_prepare_fails_clearly_when_no_interpreter_satisfies_the_requirement(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recorder = _stage_prepare(
        monkeypatch, '[project]\nrequires-python = ">=3.99"\n', _versions(p312=(3, 12, 7))
    )
    with pytest.raises(RuntimeError, match=r">=3\.99"):
        prepare_python_library("https://example.invalid/future", "abc123", tmp_path)
    assert recorder.argvs == []
