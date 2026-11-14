"""G3/TC-802 (REQ-G3-014): a stamped verification level, and a failed README example is never published.

Measured on the committed generations before this card: 119 of 280 served verified examples (all 99 python and
20 cpp) were RUN to exit 0 by their verifier yet carried no ``Runtime-Status`` line, so ``find_examples`` served
"compiled only (run not verified)" for them - safe but untrue; 4 README examples whose run FAILED (or produced
nothing) were still ``Kind: verified_example``; and a README fence holding a truncated literal
(``iVBORw0KGgo...``) was published (RD2-284).

What is pinned here, offline and deterministic (every toolchain call is stubbed, no network, no skip):

* each verifier returns the exact ``VerificationResult.level`` it achieved (python and cpp-run ``ran-exit0``;
  go, rust, cpp-compile, java and dotnet compile steps ``built``; typescript ``type-checked``; nodejs
  ``syntax-only``) and nothing on failure; the vocabulary and its order;
* the java / dotnet run record raises the level (``ran-produced-output`` / ``ran-exit0``); a verdict recorded
  before levels is read from its own facts (``legacy_verification_level``);
* ``counts_as_verified`` is UNCHANGED: it takes the run status only, an example whose run printed nothing but was
  never measured (python) still counts, and 17 such README python examples still publish;
* through the real ``build_chunks.main``: every published chunk carries ``Verification-Level:`` and
  ``Verified-With:``; a README example whose run failed or produced nothing is NOT published (it stays in the
  verification report as ``not_published``); the level is written to and read back from the verification cache;
* the placeholder-literal rule of ``example_candidates`` (the words/python case) and what it must never match.

Negative control: reverse-applying the production part of the commit removes the level, the stamped lines, the
publication rule and the placeholder rule, so the tests below fail.
"""

from __future__ import annotations

import inspect
import json
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import foss_mcp.indexing.example_verifier as ev
import infra.build_chunks as build_chunks
from foss_mcp.indexing.example_candidates import (
    CandidateExample,
    extract_candidate_supply,
    has_truncation_placeholder,
    merge_authored_candidates,
)
from foss_mcp.indexing.example_verifier import RunOutcome, RuntimeRecord, VerificationResult

REPO_ROOT = Path(__file__).resolve().parents[2]
API_SURFACE = REPO_ROOT / "tests/fixtures/pdf_net/api_surface.json"
BUILD_CHUNKS_SOURCE = REPO_ROOT / "infra" / "build_chunks.py"
WORDS_PYTHON_PAGE = REPO_ROOT / "tests" / "fixtures" / "furnished" / "words_python" / "pages" / "_index.md"

COMMIT = "5" * 40
REPO = "example-org/reference-lib"
TOOLCHAIN = "toolchain 9.9"
TITLE = "Save a box"
JAVA_CODE = 'Scene s = new Scene();\ns.save("o.stl");'
# a valid binary STL: 80-byte header, uint32 facet count 1, one 50-byte facet
_BINARY_STL_ONE_FACET = b"t".ljust(80, b" ") + (1).to_bytes(4, "little") + bytes(50)


def _candidate(code: str, title: str = TITLE) -> CandidateExample:
    return CandidateExample(title=title, description="", language="", code=code, page_code=(code,))


def _completed(returncode: int = 0, stdout: str = "ok", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------------------------------
# the vocabulary
# ---------------------------------------------------------------------------------------------------


def test_the_level_vocabulary_is_ordered_weakest_to_strongest() -> None:
    assert ev.VERIFICATION_LEVELS == (
        "syntax-only",
        "type-checked",
        "built",
        "ran-exit0",
        "ran-produced-output",
        "artifact-validated",
    )


def test_a_result_with_no_level_is_still_equal_to_one_with_a_level() -> None:
    """The level is a claim, not part of the verdict: it never changes what a result compares equal to."""
    candidate = _candidate("x = 1")
    bare = VerificationResult(candidate=candidate, verified=True, output="o")
    stamped = VerificationResult(candidate=candidate, verified=True, output="o", level=ev.LEVEL_RAN_EXIT0)
    assert bare.level == "" and stamped.level == "ran-exit0"
    assert bare == stamped


# ---------------------------------------------------------------------------------------------------
# the per-platform table: the exact level each verifier returns
# ---------------------------------------------------------------------------------------------------


def _verify_python(tmp: Path, mp: pytest.MonkeyPatch, returncode: int) -> VerificationResult:
    mp.setattr(ev, "_run_bounded", lambda args, **kw: _completed(returncode))
    return ev.verify_python_example(
        _candidate('print("hi")'),
        venv_python=tmp / "venv" / "bin" / "python",
        workdir=tmp / "work",
        repo_dir=None,
    )


def _verify_dotnet(tmp: Path, mp: pytest.MonkeyPatch, returncode: int) -> VerificationResult:
    mp.setattr(ev, "_run_bounded", lambda args, **kw: _completed(returncode))
    return ev.verify_dotnet_example(
        _candidate('System.Console.WriteLine("hi");'),
        library_csproj=tmp / "Lib.csproj",
        workdir=tmp / "work",
    )


def _verify_rust(tmp: Path, mp: pytest.MonkeyPatch, returncode: int) -> VerificationResult:
    mp.setattr(ev, "_run_cargo", lambda args, cwd: _completed(returncode))
    return ev.verify_rust_example(
        _candidate('fn main() { println!("hi"); }'),
        library_crate_name="lib",
        library_dir=tmp / "lib",
        workdir=tmp / "work",
    )


def _verify_go(tmp: Path, mp: pytest.MonkeyPatch, returncode: int) -> VerificationResult:
    mp.setattr(ev, "_run", lambda args, cwd: _completed(returncode))
    (tmp / "lib").mkdir(exist_ok=True)
    return ev.verify_go_example(
        _candidate('package main\n\nimport "fmt"\n\nfunc main() { fmt.Println("hi") }'),
        module_path="example.org/lib",
        library_dir=tmp / "lib",
        workdir=tmp / "work",
    )


def _verify_java(tmp: Path, mp: pytest.MonkeyPatch, returncode: int) -> VerificationResult:
    mp.setattr(ev, "_run", lambda args, cwd: _completed(returncode))
    return ev.verify_java_example(
        _candidate('public class Demo { public static void main(String[] a) { System.out.println("hi"); } }'),
        library_jar=tmp / "lib.jar",
        workdir=tmp / "work",
    )


def _verify_typescript(tmp: Path, mp: pytest.MonkeyPatch, returncode: int) -> VerificationResult:
    mp.setattr(ev, "_run", lambda args, cwd: _completed(returncode))
    library = tmp / "lib"
    (library / "dist").mkdir(parents=True, exist_ok=True)
    (library / "dist" / "index.d.ts").write_text("export {};\n", encoding="utf-8")
    return ev.verify_typescript_example(
        _candidate('const message: string = "hi";\nconsole.log(message);'),
        library_dir=library,
        package_name="lib",
        workdir=tmp / "work",
    )


def _verify_nodejs(tmp: Path, mp: pytest.MonkeyPatch, returncode: int) -> VerificationResult:
    mp.setattr(ev, "_run", lambda args, cwd: _completed(returncode))
    library = tmp / "lib"
    library.mkdir(exist_ok=True)
    (library / "index.js").write_text("export const A = 1;\n", encoding="utf-8")
    return ev.verify_nodejs_example(
        _candidate("import { A } from 'lib';\nconsole.log(A);"),
        library_dir=library,
        package_name="lib",
        workdir=tmp / "work",
    )


def _verify_cpp(tmp: Path, mp: pytest.MonkeyPatch, returncode: int, *, run: bool) -> VerificationResult:
    mp.setattr(ev.shutil, "which", lambda name, *a, **k: f"/usr/bin/{name}")
    mp.setattr(ev, "_run", lambda args, cwd: _completed(0 if run else returncode))
    mp.setattr(
        ev.subprocess,
        "run",
        lambda *a, **k: _completed(returncode),
    )
    library = tmp / "build"
    library.mkdir(exist_ok=True)
    return ev.verify_cpp_example_linux(
        _candidate("int main() { return 0; }"),
        tmp / "work",
        library,
        tmp / "include",
        run=run,
    )


def _verify_cpp_windows(tmp: Path, mp: pytest.MonkeyPatch, returncode: int) -> VerificationResult:
    mp.setattr(ev, "_msvc_environment", lambda workdir: SimpleNamespace(cl_exe=Path("cl.exe"), env={}))
    mp.setattr(ev.subprocess, "run", lambda *a, **k: _completed(returncode))
    return ev.verify_cpp_example(
        _candidate("int main() { return 0; }"),
        library_dir=tmp / "lib",
        include_dir=tmp / "include",
        workdir=tmp / "work",
    )


LEVEL_TABLE = [
    pytest.param(_verify_python, "ran-exit0", id="python-ran-exit0"),
    pytest.param(lambda t, m, r: _verify_cpp(t, m, r, run=True), "ran-exit0", id="cpp-run-ran-exit0"),
    pytest.param(lambda t, m, r: _verify_cpp(t, m, r, run=False), "built", id="cpp-compile-built"),
    pytest.param(_verify_cpp_windows, "built", id="cpp-msvc-built"),
    pytest.param(_verify_go, "built", id="go-built"),
    pytest.param(_verify_rust, "built", id="rust-built"),
    pytest.param(_verify_typescript, "type-checked", id="typescript-type-checked"),
    pytest.param(_verify_nodejs, "syntax-only", id="nodejs-syntax-only"),
    pytest.param(_verify_java, "built", id="java-compile-built"),
    pytest.param(_verify_dotnet, "built", id="dotnet-compile-built"),
]


@pytest.mark.parametrize(("verify", "expected"), LEVEL_TABLE)
def test_each_verifier_returns_the_level_it_achieved(
    verify, expected: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = verify(tmp_path, monkeypatch, 0)
    assert result.verified is True, result.output
    assert result.level == expected


@pytest.mark.parametrize(("verify", "expected"), LEVEL_TABLE)
def test_a_failed_verification_carries_no_level(
    verify, expected: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = verify(tmp_path, monkeypatch, 1)
    assert result.verified is False, result.output
    assert result.level == "", f"a failed verdict proved nothing; it must not claim {result.level!r}"


# ---------------------------------------------------------------------------------------------------
# the run record raises the level; a verdict recorded before levels is read from its own facts
# ---------------------------------------------------------------------------------------------------

_RAN_WITH_OUTPUT = RuntimeRecord(compiled=True, ran=True, exit_code=0, produced_output=35)
_RAN_NO_OUTPUT = RuntimeRecord(compiled=True, ran=True, exit_code=0, produced_output=0)
_RAN_FAILED = RuntimeRecord(compiled=True, ran=True, exit_code=1, produced_output=0, error="boom")
_NOT_RUN = RuntimeRecord(compiled=True)
_NOT_COMPILED = RuntimeRecord(compiled=False)


@pytest.mark.parametrize(
    ("level", "runtime", "expected"),
    [
        ("built", _RAN_WITH_OUTPUT, "ran-produced-output"),
        ("built", _RAN_NO_OUTPUT, "ran-exit0"),
        ("built", _RAN_FAILED, "built"),
        ("built", _NOT_RUN, "built"),
        ("ran-exit0", _NOT_RUN, "ran-exit0"),
        ("type-checked", _NOT_RUN, "type-checked"),
        ("", _NOT_RUN, ""),
        ("built", _NOT_COMPILED, ""),
    ],
)
def test_the_run_record_raises_the_level(level: str, runtime: RuntimeRecord, expected: str) -> None:
    assert ev.resolve_verification_level(level, runtime) == expected


@pytest.mark.parametrize(
    ("platform", "runtime", "cpp_run", "expected"),
    [
        ("python", _NOT_RUN, True, "ran-exit0"),
        ("go", _NOT_RUN, True, "built"),
        ("rust", _NOT_RUN, True, "built"),
        ("typescript", _NOT_RUN, True, "type-checked"),
        ("nodejs", _NOT_RUN, True, "syntax-only"),
        ("cpp", _NOT_RUN, True, "ran-exit0"),
        ("cpp", _NOT_RUN, False, "built"),
        ("java", _RAN_WITH_OUTPUT, True, "ran-produced-output"),
        ("java", _RAN_NO_OUTPUT, True, "ran-exit0"),
        ("dotnet", _RAN_FAILED, True, "built"),
        ("java", _NOT_RUN, True, "built"),
        ("python", _NOT_COMPILED, True, ""),
        ("cobol", _NOT_RUN, True, ""),
    ],
)
def test_a_legacy_verdict_is_read_from_its_own_facts(
    platform: str, runtime: RuntimeRecord, cpp_run: bool, expected: str
) -> None:
    assert ev.legacy_verification_level(platform, runtime, cpp_run=cpp_run) == expected


# ---------------------------------------------------------------------------------------------------
# HARD CONSTRAINT: the level never changes what counts as verified
# ---------------------------------------------------------------------------------------------------


def test_counts_as_verified_takes_the_run_status_only_and_is_unchanged() -> None:
    assert list(inspect.signature(ev.counts_as_verified).parameters) == ["status"]
    assert ev.counts_as_verified(ev.RUNTIME_UNKNOWN) is True
    assert ev.counts_as_verified(ev.RUNTIME_RAN_OK) is True
    assert ev.counts_as_verified(ev.RUNTIME_NO_OUTPUT) is False
    assert ev.counts_as_verified(ev.RUNTIME_FAILED) is False
    assert ev.counts_as_verified(ev.RUNTIME_NOT_COMPILED) is False
    # a python run is never measured for output (the record is "unknown"), so a program that prints nothing counts
    assert _NOT_RUN.status == ev.RUNTIME_UNKNOWN and ev.counts_as_verified(_NOT_RUN.status) is True


# ---------------------------------------------------------------------------------------------------
# through the real build_chunks.main
# ---------------------------------------------------------------------------------------------------


def _page(path: Path, language: str, codes: list[str]) -> Path:
    blocks = [
        {"title": f"{TITLE} {number}", "content": f"Prose.\n\n```{language}\n{code}\n```\n"}
        for number, code in enumerate(codes, start=1)
    ]
    front = {"title": "Level fixture", "single": {"block": blocks}}
    path.write_text("---\n" + yaml.safe_dump(front, sort_keys=False) + "---\n", encoding="utf-8")
    return path


def _argv(out_dir: Path, page: Path, platform: str, extra: list[str]) -> list[str]:
    return [
        "--api-surface",
        str(API_SURFACE),
        "--title",
        "Level",
        "--out",
        str(out_dir / "chunks.json"),
        "--furnished-page",
        str(page),
        "--library-repository",
        REPO,
        "--library-commit",
        COMMIT,
        "--library-platform",
        platform,
        *extra,
    ]


def _main(argv: list[str]) -> None:
    old = sys.argv
    sys.argv = ["build_chunks.py", *argv]
    try:
        build_chunks.main()
    finally:
        sys.argv = old


def _example_texts(out_dir: Path) -> list[str]:
    chunks = json.loads((out_dir / "chunks.json").read_text(encoding="utf-8"))["chunks"]
    return [chunk["text"] for chunk in chunks if "Kind: verified_example" in chunk["text"]]


def _report(out_dir: Path) -> list[dict]:
    return json.loads((out_dir / "chunks.verification.json").read_text(encoding="utf-8"))


def _entries(path: Path) -> list[dict]:
    return build_chunks.parse_verification_cache(path.read_text(encoding="utf-8"))


def _stub_python(
    monkeypatch: pytest.MonkeyPatch, *, level: str = "ran-exit0", real: bool = True
) -> list[str]:
    """Python with prepare and the verifier stubbed. ``real`` registers the stub as THE real python verifier (the way
    ``_RUNNABLE_VERIFIERS`` is patched for java), so its verdicts are stamped; a stand-in (``real=False``) is not."""
    verifies: list[str] = []
    monkeypatch.setattr(build_chunks, "toolchain_id", lambda platform: TOOLCHAIN)
    monkeypatch.setattr(
        build_chunks, "prepare_python_library", lambda repo, commit, workdir: Path("venv") / "python"
    )

    def fake_verify(candidate, *, venv_python: Path, workdir: Path) -> VerificationResult:
        verifies.append(candidate.title)
        return VerificationResult(candidate=candidate, verified=True, output="", level=level)

    monkeypatch.setitem(build_chunks._PLATFORM_DISPATCH["python"], "verify", fake_verify)
    if real:
        monkeypatch.setitem(build_chunks._REAL_VERIFIERS, "python", fake_verify)
    return verifies


def test_seventeen_python_examples_that_print_nothing_still_publish_with_level_ran_exit0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """17 of 75 README python examples print nothing. The level must not turn them into 'no_output'."""
    _stub_python(monkeypatch)
    codes = [f"value_{number} = {number}" for number in range(17)]
    page = _page(tmp_path / "_index.md", "python", codes)
    _main(_argv(tmp_path, page, "python", []))
    texts = _example_texts(tmp_path)
    assert len(texts) == 17, "an example whose run printed nothing (never measured) must still be published"
    assert all("Verification-Level: ran-exit0\n" in text for text in texts)
    assert all("Verified-With: " + TOOLCHAIN + "\n" in text for text in texts)
    assert all("Runtime-Status" not in text for text in texts)
    assert all(record["verified"] is True for record in _report(tmp_path))


def test_a_stand_in_verifier_publishes_no_stamp_fresh_or_from_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stand-in verifier (a test double, not the real python verifier) compiled and ran nothing, so its chunk
    claims no level and no toolchain - fresh or on a cache hit - and the two chunks are byte-identical (TC-441)."""
    _stub_python(monkeypatch, level="", real=False)
    cache = tmp_path / "cache.json"
    fresh = tmp_path / "fresh"
    cached = tmp_path / "cached"
    fresh.mkdir()
    cached.mkdir()
    page = _page(tmp_path / "_index.md", "python", ["x = 1"])
    _main(_argv(fresh, page, "python", ["--verification-cache-out", str(cache)]))
    (entry,) = _entries(cache)
    assert "verification_level" not in entry
    _main(_argv(cached, page, "python", ["--verification-cache", str(cache)]))
    assert _report(cached)[0]["cached"] is True
    assert _example_texts(fresh) == _example_texts(cached)
    (text,) = _example_texts(fresh)
    assert "Verification-Level" not in text and "Verified-With" not in text


def test_the_real_verifier_table_names_the_production_function_of_every_platform() -> None:
    assert set(build_chunks._REAL_VERIFIERS) == set(build_chunks._PLATFORM_DISPATCH)
    assert build_chunks._REAL_VERIFIERS["python"] is ev.verify_python_example
    assert build_chunks._REAL_VERIFIERS["java"] is ev.verify_java_example
    assert build_chunks._REAL_VERIFIERS["cpp"] is ev.verify_cpp_example_linux


class _Java:
    """Java with prepare and compile stubbed; the run goes through the real chain with a fake runner."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.exit_code = 0
        self.writes_artefact = True
        monkeypatch.setattr(build_chunks, "toolchain_id", lambda platform: TOOLCHAIN)
        monkeypatch.setattr(
            build_chunks, "prepare_java_library", lambda repo, commit, workdir: workdir / "lib.jar"
        )

        def fake_verify(cand, *, workdir, library_jar, page_imports, **_kw):
            (workdir / "candidate_project").mkdir(parents=True, exist_ok=True)
            return VerificationResult(candidate=cand, verified=True, output="compiled", level=ev.LEVEL_BUILT)

        monkeypatch.setitem(build_chunks._PLATFORM_DISPATCH["java"], "verify", fake_verify)
        monkeypatch.setitem(build_chunks._RUNNABLE_VERIFIERS, "java", fake_verify)
        monkeypatch.setitem(build_chunks._REAL_VERIFIERS, "java", fake_verify)

        def runner(argv: Sequence[str], cwd: Path, timeout_seconds: float) -> RunOutcome:
            if self.exit_code == 0 and self.writes_artefact:
                (cwd / "o.stl").write_bytes(_BINARY_STL_ONE_FACET)
            return RunOutcome(exit_code=self.exit_code, stderr="" if self.exit_code == 0 else "boom")

        monkeypatch.setattr(ev, "default_runtime_runner", runner)

    def build(self, tmp: Path, extra: list[str] | None = None) -> None:
        _main(_argv(tmp, _page(tmp / "_index.md", "java", [JAVA_CODE]), "java", extra or []))


def test_a_java_example_that_ran_with_output_is_published_as_ran_produced_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Java(monkeypatch).build(tmp_path)
    (text,) = _example_texts(tmp_path)
    assert "Verification-Level: ran-produced-output\n" in text
    assert "Runtime-Status: ran\n" in text


def test_a_readme_example_whose_run_failed_is_not_published_as_a_verified_example(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    java = _Java(monkeypatch)
    java.exit_code = 1
    java.build(tmp_path)
    assert _example_texts(tmp_path) == [], "a failed README example must not be Kind: verified_example"
    (record,) = _report(tmp_path)
    assert record["runtime_status"] == "failed" and record["verified"] is False
    assert record["not_published"] == "the run failed"
    assert record["verification_level"] == "built"


def test_a_readme_example_whose_run_produced_nothing_is_not_published_as_a_verified_example(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    java = _Java(monkeypatch)
    java.writes_artefact = False
    java.build(tmp_path)
    assert _example_texts(tmp_path) == []
    (record,) = _report(tmp_path)
    assert record["runtime_status"] == "no_output" and record["verified"] is False
    assert record["not_published"] == "the run produced no output"
    assert record["verification_level"] == "ran-exit0"


def test_the_level_is_written_to_the_cache_and_a_hit_serves_the_recorded_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    java = _Java(monkeypatch)
    cache = tmp_path / "cache.json"
    java.build(tmp_path, ["--verification-cache-out", str(cache)])
    (entry,) = _entries(cache)
    assert entry["verification_level"] == "ran-produced-output"
    first = _example_texts(tmp_path)
    java.exit_code = 1  # a hit must not run again, so the recorded verdict is what is served
    java.build(tmp_path, ["--verification-cache", str(cache)])
    assert _example_texts(tmp_path) == first
    assert _report(tmp_path)[0]["cached"] is True


def test_a_cache_hit_on_a_failed_run_is_not_published_either(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    java = _Java(monkeypatch)
    java.exit_code = 1
    cache = tmp_path / "cache.json"
    java.build(tmp_path, ["--verification-cache-out", str(cache)])
    java.build(tmp_path, ["--verification-cache", str(cache)])
    assert _report(tmp_path)[0]["cached"] is True
    assert _example_texts(tmp_path) == []


def test_a_legacy_python_entry_is_read_as_ran_exit0(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    verifies = _stub_python(monkeypatch)
    cache = tmp_path / "cache.json"
    page = _page(tmp_path / "_index.md", "python", ["x = 1"])
    _main(_argv(tmp_path, page, "python", ["--verification-cache-out", str(cache)]))
    entries = _entries(cache)
    for entry in entries:
        entry.pop("verification_level")
    cache.write_text(build_chunks.verification_cache_document(entries), encoding="utf-8")
    _main(_argv(tmp_path, page, "python", ["--verification-cache", str(cache)]))
    assert len(verifies) == 1, "the legacy entry must hit"
    (text,) = _example_texts(tmp_path)
    assert "Verification-Level: ran-exit0\n" in text
    assert "Verified-With: " + TOOLCHAIN + "\n" in text


def test_a_build_without_a_cache_still_names_the_toolchain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_python(monkeypatch)
    _main(_argv(tmp_path, _page(tmp_path / "_index.md", "python", ["x = 1"]), "python", []))
    (text,) = _example_texts(tmp_path)
    assert f"Verified-With: {TOOLCHAIN}\n" in text


def test_the_stamped_lines_sit_in_the_header_before_the_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_python(monkeypatch)
    _main(_argv(tmp_path, _page(tmp_path / "_index.md", "python", ["x = 1"]), "python", []))
    (text,) = _example_texts(tmp_path)
    header = text.split("\nExample:\n")[0]
    assert "Verification-Level: ran-exit0" in header and "Verified-With:" in header
    assert text.split("\nExample:\n")[1].startswith("x = 1")
    assert text.startswith(
        f"FQN: Example: {TITLE} 1\nKind: verified_example\nVerification-Level: ran-exit0\n"
    )


def test_the_stamped_lines_never_become_the_lead_in_of_a_generic_titled_example(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The prose above ``Example:`` is read back as the lead-in of a generic-titled README example (TC-797). The new
    header lines are not recognised header lines there, so the build closes the header with a blank line: measured
    on the 46 committed generic-titled examples, 26 lead-ins would otherwise start with 'Verification-Level: ...'."""
    from foss_mcp.mcp.tools.relevance import example_lead_in

    _stub_python(monkeypatch)
    prose = "Typical usage: load a file and convert it to another form."
    front = {
        "title": "Lead-in fixture",
        "single": {"block": [{"title": "Quick Start", "content": f"{prose}\n\n```python\nvalue = 1\n```\n"}]},
    }
    page = tmp_path / "_index.md"
    page.write_text("---\n" + yaml.safe_dump(front, sort_keys=False) + "---\n", encoding="utf-8")
    _main(_argv(tmp_path, page, "python", []))
    (text,) = _example_texts(tmp_path)
    assert "Verification-Level: ran-exit0\n" in text
    assert example_lead_in(text) == prose


def test_the_build_calls_the_level_and_the_publication_rule_in_the_real_entrypoint() -> None:
    """Wiring proof: the resolver, the line writer and the not-published rule are CALLED in build_chunks.py."""
    source = BUILD_CHUNKS_SOURCE.read_text(encoding="utf-8")
    assert 'resolve_verification_level(getattr(verification_result, "level", ""), runtime)' in source
    assert "legacy_verification_level(platform, runtime, cpp_run=cpp_run)" in source
    assert "_verification_level_lines(level, verified_with_toolchain() if level else None)" in source
    assert "real_verifier = verify is _REAL_VERIFIERS.get(platform)" in source
    assert 'f"{level_lines}"' in source
    assert re.search(r"compiled\s+and not rejected\s+and not not_published\s+and \(verified", source)


# ---------------------------------------------------------------------------------------------------
# the placeholder literal (RD2-284)
# ---------------------------------------------------------------------------------------------------

PLACEHOLDER_FENCES = [
    'md = "![logo](data:image/png;base64,iVBORw0KGgo...)"',
    'blob = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAfbLI3wAAAABJRU5ErkJggg=="\n'
    'short = "iVBORw0KGgo..."',
    "const image = 'data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD...';",
    'data = "JVBERi0xLjQKJcfsj6IKNSAwIG9iago8PC9MZW5ndGggNiAwIFIv\u2026"',
    'token = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVphYmNkZWZnaGlqa2xtbm9wcXJzdHV2d3h5ejAxMjM0NTY3ODk..."',
    'x = """\nhello\ndata:image/png;base64,iVBORw0KGgo...\n"""',
    "img := `data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw...`",
]
CLEAN_FENCES = [
    'print("Loading...")',
    'print("Processing your documents, please wait...")',
    "# the payload is base64,iVBORw0KGgo... (see the docs)\nrun()",
    'digest = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08..."',
    'blob = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAfbLI3wAAAABJRU5ErkJggg=="',
    "items = [1, 2, 3, ...]",
    "def f(*args): ...",
    'path = "C:/data/report.docx"',
]


@pytest.mark.parametrize("code", PLACEHOLDER_FENCES)
def test_a_literal_holding_a_truncated_blob_is_a_placeholder(code: str) -> None:
    assert has_truncation_placeholder(code) is True


@pytest.mark.parametrize("code", CLEAN_FENCES)
def test_ordinary_ellipses_and_complete_blobs_are_not_placeholders(code: str) -> None:
    assert has_truncation_placeholder(code) is False


def _readme_page(codes: list[str], language: str = "python") -> dict:
    return {
        "single": {
            "block": [
                {"title": f"Example {number}", "content": f"Prose.\n\n```{language}\n{code}\n```\n"}
                for number, code in enumerate(codes, start=1)
            ]
        }
    }


def test_a_fence_with_a_placeholder_literal_is_left_out_of_the_supply_and_counted() -> None:
    supply = extract_candidate_supply(
        _readme_page(['md = "data:image/png;base64,iVBORw0KGgo..."', "print('real')", "total = 1 + 1"]),
        "python",
    )
    assert [candidate.code for candidate in supply.candidates] == ["print('real')", "total = 1 + 1"]
    assert supply.placeholder_dropped == 1
    assert supply.fallback is False


def test_a_page_whose_only_python_fence_is_a_placeholder_does_not_fall_back_to_it() -> None:
    supply = extract_candidate_supply(_readme_page(['md = "data:image/png;base64,iVBORw0KGgo..."']), "python")
    assert supply.candidates == ()
    assert supply.placeholder_dropped == 1
    assert supply.fallback is False


def test_an_authored_example_is_never_subject_to_the_readme_rule() -> None:
    authored = _candidate('blob = "iVBORw0KGgo..."', "Authored with a short literal")
    merged, skipped = merge_authored_candidates([], [authored])
    assert [candidate.title for candidate in merged] == ["Authored with a short literal"] and skipped == []
    supply = extract_candidate_supply({"single": {"block": []}}, "python", authored=[authored])
    assert [candidate.title for candidate in supply.candidates] == ["Authored with a short literal"]


def test_the_words_python_markdown_example_with_the_truncated_image_is_no_longer_a_candidate() -> None:
    """RD2-284, reproduced on the committed furnished page: 'Load Markdown From In-Memory Content (2)'."""
    text = WORDS_PYTHON_PAGE.read_text(encoding="utf-8")
    page = yaml.safe_load(text.split("---\n")[1])
    supply = extract_candidate_supply(page, "python")
    assert supply.placeholder_dropped >= 1
    assert all("iVBORw0KGgo..." not in candidate.code for candidate in supply.candidates)
    assert "Load Markdown From In-Memory Content (2)" not in [
        candidate.title for candidate in supply.candidates
    ]
