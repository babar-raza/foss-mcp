"""TC-453 (REQ-G3-007): the Python verifier installs libraries whose editable install fails,
stages the repository sample files a block names, rejects import-only blocks and bounds every
subprocess.

Offline and deterministic: the clone and the pip/venv commands are scripted fakes; the run and
``_run_bounded`` cases use the running interpreter and tiny throwaway scripts.
"""

from __future__ import annotations

import inspect
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

import infra.build_chunks as build_chunks
from foss_mcp.indexing import example_verifier as ev
from foss_mcp.indexing.example_candidates import CandidateExample
from foss_mcp.indexing.example_verifier import prepare_python_library, verify_python_example

REPO_ROOT = Path(__file__).resolve().parents[2]
NOTE_PAGE = REPO_ROOT / "tests" / "fixtures" / "furnished" / "note_python" / "pages" / "_index.md"

_BAD_BACKEND = (
    "BackendUnavailable: Cannot import 'setuptools.backends.legacy'\n"
    "ERROR: Cannot import 'setuptools.backends.legacy'"
)
_NO_VERSION_FILE = "FileNotFoundError: [Errno 2] No such file or directory: 'local/Version.txt'"


def _candidate(code: str) -> CandidateExample:
    return CandidateExample(title="block", description="", language="python", code=code)


def _done(
    args: list[str], code: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args, code, stdout=stdout, stderr=stderr)


# --- prepare_python_library --------------------------------------------------


class _Prepare:
    """A fake clone plus a fake ``_run`` scripted per argv shape."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        files: dict[str, str],
        *,
        editable: Callable[[str], subprocess.CompletedProcess[str]],
        deps: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
        purelib: str | None = "default",
    ) -> None:
        self.workdir = tmp_path / "work"
        self.workdir.mkdir()
        self.purelib_dir = tmp_path / "site-packages"
        self.purelib_dir.mkdir()
        self.argvs: list[list[str]] = []
        self._editable = editable
        self._deps = deps
        self._purelib = str(self.purelib_dir) if purelib == "default" else (purelib or "")

        def fake_clone(repository: str, commit: str, repo_dir: Path) -> None:
            repo_dir.mkdir(parents=True)
            for name, text in files.items():
                (repo_dir / name).parent.mkdir(parents=True, exist_ok=True)
                (repo_dir / name).write_text(text, encoding="utf-8")

        monkeypatch.setattr(ev, "_clone_pinned_commit", fake_clone)
        monkeypatch.setattr(ev, "_run", self.fake_run)
        monkeypatch.setattr(ev, "_candidate_interpreters", lambda: [sys.executable])
        monkeypatch.setattr(ev, "_interpreter_version", lambda executable: tuple(sys.version_info[:3]))

    def fake_run(self, args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        self.argvs.append(list(args))
        if args[1:3] == ["-m", "venv"]:
            return _done(args)
        if args[1:5] == ["-m", "pip", "install", "-e"]:
            return self._editable(args[5])
        if args[1:4] == ["-m", "pip", "install"]:
            return self._deps(args[4:]) if self._deps else _done(args)
        if args[1] == "-c":
            return _done(args, stdout=self._purelib + "\n")
        raise AssertionError(f"unexpected command {args}")

    def pip_editable_targets(self) -> list[str]:
        return [argv[5] for argv in self.argvs if argv[1:5] == ["-m", "pip", "install", "-e"]]

    def dependency_installs(self) -> list[list[str]]:
        return [argv[4:] for argv in self.argvs if argv[1:4] == ["-m", "pip", "install"] and argv[4] != "-e"]

    def prepare(self) -> Path:
        return prepare_python_library("example-org/lib", "abc123", self.workdir)


def _failing(message: str) -> Callable[[str], subprocess.CompletedProcess[str]]:
    return lambda target: _done([target], 1, stderr=message)


_HTML_PYPROJECT = (
    '[build-system]\nrequires = ["setuptools"]\nbuild-backend = "setuptools.backends.legacy:build"\n'
    '[project]\nname = "aspose-html-foss"\ndependencies = ["lxml>=5", "pillow"]\n'
)


def test_runtime_extras_are_requested_and_test_extras_are_not(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pyproject = '[project]\nname = "x"\n[project.optional-dependencies]\npdf = ["reportlab"]\ntest = ["pytest"]\ndev = ["ruff"]\n'
    ctx = _Prepare(monkeypatch, tmp_path, {"pyproject.toml": pyproject}, editable=lambda t: _done([t]))
    ctx.prepare()
    targets = ctx.pip_editable_targets()
    assert len(targets) == 1
    assert targets[0].endswith("[pdf]")


def test_extras_are_sorted_and_comma_joined(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pyproject = (
        '[project]\nname = "x"\n[project.optional-dependencies]\nzeta = ["z"]\nalpha = ["a"]\ndocs = ["d"]\n'
    )
    ctx = _Prepare(monkeypatch, tmp_path, {"pyproject.toml": pyproject}, editable=lambda t: _done([t]))
    ctx.prepare()
    assert ctx.pip_editable_targets()[0].endswith("[alpha,zeta]")


def test_no_runtime_extras_means_a_plain_editable_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pyproject = '[project]\nname = "x"\n[project.optional-dependencies]\ntest = ["pytest"]\n'
    ctx = _Prepare(monkeypatch, tmp_path, {"pyproject.toml": pyproject}, editable=lambda t: _done([t]))
    ctx.prepare()
    assert ctx.pip_editable_targets() == [str(ctx.workdir / "repo")]


def test_failing_extras_then_plain_editable_install_passes_without_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pyproject = '[project]\nname = "x"\n[project.optional-dependencies]\npdf = ["reportlab"]\n'

    def editable(target: str) -> subprocess.CompletedProcess[str]:
        return _done([target], 1, stderr="no matching extra") if target.endswith("]") else _done([target])

    ctx = _Prepare(monkeypatch, tmp_path, {"pyproject.toml": pyproject}, editable=editable)
    ctx.prepare()
    targets = ctx.pip_editable_targets()
    assert len(targets) == 2
    assert targets[0].endswith("[pdf]")
    assert targets[1] == str(ctx.workdir / "repo")
    assert ctx.dependency_installs() == []
    assert list(ctx.purelib_dir.iterdir()) == []


def test_html_python_shape_installs_declared_dependencies_and_writes_the_pth_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    files = {"pyproject.toml": _HTML_PYPROJECT, "src/aspose/__init__.py": ""}
    ctx = _Prepare(monkeypatch, tmp_path, files, editable=_failing(_BAD_BACKEND))
    venv_python = ctx.prepare()
    assert venv_python.name in {"python", "python.exe"}
    assert ctx.dependency_installs() == [["lxml>=5", "pillow"]]
    assert any(argv[1] == "-c" and "purelib" in argv[2] for argv in ctx.argvs)
    pth = ctx.purelib_dir / "_foss_mcp_source.pth"
    assert pth.read_text(encoding="utf-8") == f"{(ctx.workdir / 'repo' / 'src').resolve()}\n"
    err = capsys.readouterr().err
    assert "[foss-mcp] pip install -e failed for" in err
    assert "_foss_mcp_source.pth" in err


def test_page_python_shape_takes_the_same_fallback_with_the_repository_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pyproject = '[project]\nname = "aspose-page-foss"\ndependencies = ["fonttools"]\n'
    ctx = _Prepare(monkeypatch, tmp_path, {"pyproject.toml": pyproject}, editable=_failing(_NO_VERSION_FILE))
    ctx.prepare()
    assert ctx.dependency_installs() == [["fonttools"]]
    pth = ctx.purelib_dir / "_foss_mcp_source.pth"
    assert pth.read_text(encoding="utf-8") == f"{(ctx.workdir / 'repo').resolve()}\n"


def test_fallback_also_installs_the_dependencies_of_the_runtime_extras(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pyproject = (
        '[project]\nname = "x"\ndependencies = ["core"]\n'
        '[project.optional-dependencies]\npdf = ["reportlab>=4"]\ntest = ["pytest"]\n'
    )
    ctx = _Prepare(monkeypatch, tmp_path, {"pyproject.toml": pyproject}, editable=_failing(_BAD_BACKEND))
    ctx.prepare()
    assert ctx.dependency_installs() == [["core", "reportlab>=4"]]
    assert len(ctx.pip_editable_targets()) == 2


def test_requirements_txt_is_used_when_pyproject_declares_no_dependencies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    files = {
        "pyproject.toml": '[project]\nname = "x"\n',
        "requirements.txt": "# comment\n\nrequests>=2\n-r other.txt\n-e .\nnumpy\n",
    }
    ctx = _Prepare(monkeypatch, tmp_path, files, editable=_failing(_NO_VERSION_FILE))
    ctx.prepare()
    assert ctx.dependency_installs() == [["requests>=2", "numpy"]]


def test_failing_dependency_install_raises_naming_both_attempts_and_writes_no_pth(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ctx = _Prepare(
        monkeypatch,
        tmp_path,
        {"pyproject.toml": _HTML_PYPROJECT},
        editable=_failing(_BAD_BACKEND),
        deps=lambda deps: _done(deps, 1, stderr="ERROR: No matching distribution found for lxml>=5"),
    )
    with pytest.raises(RuntimeError) as excinfo:
        ctx.prepare()
    message = str(excinfo.value)
    assert "pip install -e" in message
    assert "setuptools.backends.legacy" in message
    assert "declared dependencies" in message
    assert "No matching distribution" in message
    assert list(ctx.purelib_dir.iterdir()) == []


def test_empty_purelib_answer_raises_and_writes_nothing_in_the_working_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ctx = _Prepare(
        monkeypatch,
        tmp_path,
        {"pyproject.toml": _HTML_PYPROJECT},
        editable=_failing(_BAD_BACKEND),
        purelib=None,
    )
    before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    with pytest.raises(RuntimeError, match="purelib"):
        ctx.prepare()
    after = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    assert [p for p in after if p not in before and not p.startswith("work/repo")] == []
    assert not list(tmp_path.rglob("_foss_mcp_source.pth"))


def test_purelib_that_is_not_a_directory_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ctx = _Prepare(
        monkeypatch,
        tmp_path,
        {"pyproject.toml": _HTML_PYPROJECT},
        editable=_failing(_BAD_BACKEND),
        purelib=str(tmp_path / "does-not-exist"),
    )
    with pytest.raises(RuntimeError):
        ctx.prepare()
    assert not list(tmp_path.rglob("_foss_mcp_source.pth"))


def test_a_timed_out_editable_install_raises_instead_of_falling_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ctx = _Prepare(
        monkeypatch,
        tmp_path,
        {"pyproject.toml": _HTML_PYPROJECT},
        editable=lambda target: _done([target], 124, stderr="[foss-mcp] TIMEOUT: python was killed"),
    )
    with pytest.raises(RuntimeError, match="TIMEOUT"):
        ctx.prepare()
    assert ctx.dependency_installs() == []


def test_broken_toml_gives_no_extras_and_no_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project\nbroken", encoding="utf-8")
    assert ev._python_runtime_extras(tmp_path) == ()
    assert ev._python_runtime_extras(tmp_path / "missing") == ()


# --- staging -----------------------------------------------------------------


def _note_quick_start_block() -> str:
    text = NOTE_PAGE.read_text(encoding="utf-8")
    for match in re.finditer(r"```python\n(.*?)```", text, re.DOTALL):
        if 'Document("SimpleTable.one")' in match.group(1) and "DisplayName" in match.group(1):
            return "\n".join(line.removeprefix("      ") for line in match.group(1).splitlines())
    raise AssertionError("the note_python Quick Start block was not found")


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "testfiles").mkdir(parents=True)
    (repo / "testfiles" / "SimpleTable.one").write_bytes(b"ONE")
    return repo


def _script_dir(tmp_path: Path) -> Path:
    script_dir = tmp_path / "script"
    script_dir.mkdir()
    return script_dir


def test_the_real_note_python_quick_start_block_stages_simpletable(tmp_path: Path) -> None:
    code = _note_quick_start_block()
    repo, script_dir = _repo(tmp_path), _script_dir(tmp_path)
    staged = ev._stage_named_repository_files(code, repo, script_dir)
    assert staged == ["testfiles/SimpleTable.one"]
    assert (script_dir / "SimpleTable.one").read_bytes() == b"ONE"


def test_a_named_python_file_is_never_staged(tmp_path: Path) -> None:
    repo, script_dir = _repo(tmp_path), _script_dir(tmp_path)
    (repo / "helper.py").write_text("X = 1\n", encoding="utf-8")
    staged = ev._stage_named_repository_files('import runpy\nrunpy.run_path("helper.py")\n', repo, script_dir)
    assert staged == []
    assert not (script_dir / "helper.py").exists()


def test_a_file_already_in_the_script_directory_is_not_overwritten(tmp_path: Path) -> None:
    repo, script_dir = _repo(tmp_path), _script_dir(tmp_path)
    (script_dir / "SimpleTable.one").write_bytes(b"EARLIER")
    staged = ev._stage_named_repository_files('open("SimpleTable.one")', repo, script_dir)
    assert staged == []
    assert (script_dir / "SimpleTable.one").read_bytes() == b"EARLIER"


def test_a_file_under_git_is_ignored(tmp_path: Path) -> None:
    repo, script_dir = _repo(tmp_path), _script_dir(tmp_path)
    (repo / ".git").mkdir()
    (repo / ".git" / "hooked.txt").write_text("secret", encoding="utf-8")
    assert ev._stage_named_repository_files('open("hooked.txt")', repo, script_dir) == []
    assert not (script_dir / "hooked.txt").exists()


def test_a_quoted_path_with_a_separator_is_ignored(tmp_path: Path) -> None:
    repo, script_dir = _repo(tmp_path), _script_dir(tmp_path)
    code = 'open("testfiles/SimpleTable.one")\nopen("a\\\\SimpleTable.one")\n'
    assert ev._stage_named_repository_files(code, repo, script_dir) == []
    assert list(script_dir.iterdir()) == []


def test_the_first_file_by_repo_relative_path_wins(tmp_path: Path) -> None:
    repo, script_dir = _repo(tmp_path), _script_dir(tmp_path)
    (repo / "a").mkdir()
    (repo / "a" / "SimpleTable.one").write_bytes(b"FIRST")
    staged = ev._stage_named_repository_files('open("SimpleTable.one")', repo, script_dir)
    assert staged == ["a/SimpleTable.one"]
    assert (script_dir / "SimpleTable.one").read_bytes() == b"FIRST"


# --- verify_python_example ---------------------------------------------------


def test_a_block_that_opens_a_staged_file_and_prints_from_it_is_verified(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    candidate = _candidate('print(open("SimpleTable.one", "rb").read().decode())\n')
    result = verify_python_example(
        candidate, venv_python=Path(sys.executable), workdir=tmp_path, repo_dir=repo
    )
    assert result.verified is True
    assert result.candidate is candidate
    first, _, rest = result.output.partition("\n")
    assert first == "[foss-mcp] assembly: python staged-files=testfiles/SimpleTable.one"
    assert "ONE" in rest


def test_the_assembly_line_says_none_when_nothing_was_staged(tmp_path: Path) -> None:
    candidate = _candidate("print(1 + 1)\n")
    result = verify_python_example(
        candidate, venv_python=Path(sys.executable), workdir=tmp_path, repo_dir=_repo(tmp_path)
    )
    assert result.verified is True
    assert result.output.startswith("[foss-mcp] assembly: python staged-files=none\n")


def test_a_block_raising_an_exception_is_not_verified(tmp_path: Path) -> None:
    candidate = _candidate('raise RuntimeError("boom")\n')
    result = verify_python_example(
        candidate, venv_python=Path(sys.executable), workdir=tmp_path, repo_dir=_repo(tmp_path)
    )
    assert result.verified is False
    assert result.candidate is candidate
    assert "boom" in result.output


@pytest.mark.parametrize(
    "code",
    [
        "from aspose.note import Document\n",
        "import os, sys\nfrom a import b\n",
        '"""docstring"""\nimport os\npass\n',
        "",
        "# only a comment\n",
    ],
)
def test_import_only_blocks_are_rejected_and_nothing_is_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, code: str
) -> None:
    def must_not_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("nothing may be run for an import-only block")

    monkeypatch.setattr(ev, "_run_bounded", must_not_run)
    candidate = _candidate(code)
    result = verify_python_example(candidate, venv_python=Path(sys.executable), workdir=tmp_path)
    assert result.verified is False
    assert result.candidate is candidate
    assert result.output == ev._NO_STATEMENTS_MESSAGE.format(kind="import statements")
    assert "import statements" in result.output


def test_a_syntax_error_is_not_a_rejection_the_run_reports_it(tmp_path: Path) -> None:
    result = verify_python_example(
        _candidate("def broken(:\n"), venv_python=Path(sys.executable), workdir=tmp_path
    )
    assert result.verified is False
    assert "SyntaxError" in result.output
    assert "rejected" not in result.output


def test_a_block_that_never_returns_is_not_verified_and_records_the_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(ev, "_PYTHON_CANDIDATE_TIMEOUT_SECONDS", 1)
    candidate = _candidate("import time\ntime.sleep(30)\n")
    result = verify_python_example(
        candidate, venv_python=Path(sys.executable), workdir=tmp_path, repo_dir=_repo(tmp_path)
    )
    assert result.verified is False
    assert result.candidate is candidate
    assert "[foss-mcp] TIMEOUT:" in result.output
    assert "was killed after 1 seconds" in result.output


def test_repo_dir_defaults_to_the_repo_next_to_the_venv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    (repo / "data" / "sample.txt").write_text("hi", encoding="utf-8")
    venv_python = tmp_path / "venv" / "bin" / "python"
    seen: list[list[str]] = []

    def fake_bounded(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(args)
        return _done(args, stdout="ok")

    monkeypatch.setattr(ev, "_run_bounded", fake_bounded)
    result = verify_python_example(
        _candidate('print(open("sample.txt").read())\n'), venv_python=venv_python, workdir=tmp_path
    )
    assert result.verified is True
    assert result.output.startswith("[foss-mcp] assembly: python staged-files=data/sample.txt\n")
    assert seen[0][0] == str(venv_python)


def test_without_a_repo_directory_nothing_is_staged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ev, "_run_bounded", lambda args, **kwargs: _done(args))
    result = verify_python_example(
        _candidate('print(open("sample.txt").read())\n'),
        venv_python=tmp_path / "venv" / "bin" / "python",
        workdir=tmp_path,
    )
    assert result.output.startswith("[foss-mcp] assembly: python staged-files=none\n")


# --- _run_bounded ------------------------------------------------------------


def test_a_sleeping_command_is_killed_with_exit_124_and_the_timeout_line(tmp_path: Path) -> None:
    result = ev._run_bounded(
        [sys.executable, "-c", "import time; time.sleep(30)"], cwd=tmp_path, timeout_seconds=1
    )
    assert result.returncode == 124
    assert result.stderr.endswith(
        f"[foss-mcp] TIMEOUT: {Path(sys.executable).name} was killed after 1 seconds"
    )


def test_a_grandchild_holding_stdout_does_not_hang_the_run(tmp_path: Path) -> None:
    code = (
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "time.sleep(60)\n"
    )
    result = ev._run_bounded([sys.executable, "-c", code], cwd=tmp_path, timeout_seconds=1)
    assert result.returncode == 124
    assert "[foss-mcp] TIMEOUT:" in result.stderr


def test_a_normal_command_returns_its_own_exit_code_output_and_cwd(tmp_path: Path) -> None:
    code = "import os, sys; print(os.getcwd()); print('to-stderr', file=sys.stderr); sys.exit(3)"
    result = ev._run_bounded([sys.executable, "-c", code], cwd=tmp_path, timeout_seconds=60)
    assert result.returncode == 3
    assert Path(result.stdout.strip()).resolve() == tmp_path.resolve()
    assert result.stderr.strip() == "to-stderr"


def test_run_bounded_passes_the_environment_it_is_given(tmp_path: Path) -> None:
    env = {**os.environ, "TC453_MARKER": "marker-value"}
    result = ev._run_bounded(
        [sys.executable, "-c", "import os; print(os.environ['TC453_MARKER'])"],
        cwd=tmp_path,
        timeout_seconds=60,
        env=env,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "marker-value"


def test_run_uses_the_literal_1800_second_bound(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    recorded: dict[str, object] = {}

    def fake_bounded(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        recorded.update(kwargs)
        return _done(args)

    monkeypatch.setattr(ev, "_run_bounded", fake_bounded)
    ev._run(["tool"], cwd=tmp_path)
    assert recorded["timeout_seconds"] == 1800
    assert recorded["cwd"] == tmp_path


def test_the_bounds_are_the_documented_literals() -> None:
    assert ev._RUN_TIMEOUT_SECONDS == 1800
    assert ev._PYTHON_CANDIDATE_TIMEOUT_SECONDS == 120
    assert ev._TIMEOUT_EXIT_CODE == 124
    assert ev._KILL_GRACE_SECONDS == 10


# --- wiring ------------------------------------------------------------------


def test_the_python_dispatch_entry_uses_the_real_verifier_and_the_verifier_stages_and_bounds() -> None:
    assert build_chunks._PLATFORM_DISPATCH["python"]["verify"] is verify_python_example
    source = inspect.getsource(verify_python_example)
    assert "_stage_named_repository_files(" in source
    assert "_PYTHON_CANDIDATE_TIMEOUT_SECONDS" in source
