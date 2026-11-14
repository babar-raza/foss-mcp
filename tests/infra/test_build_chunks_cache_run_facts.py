"""G3/TC-801 (REQ-G3-014): an HONEST verification-cache hit.

``infra/build_chunks.py::_cache_hit`` used to accept ANY ``verified: true`` entry. An entry written before
TC-565 has no run facts, classifies as RUNTIME_UNKNOWN, and ``counts_as_verified`` treats an unknown run as
verified - so 54 java/dotnet examples were served as 'compiled only (run not verified)' although the verifier
would now run them. The rules pinned here:

* on a platform whose compiled candidate is run (``_RUNNABLE_VERIFIERS``: java, dotnet) an entry without the
  run record, or without the current ``verifier_version``, is a MISS and the example is verified (and run) again;
* on every other platform an entry without ``verifier_version`` (legacy) still hits, so the committed caches
  keep working; an entry that names another version is stale and misses;
* every entry written carries ``verifier_version``;
* a timeout, an unavailable fixture or a run that could not be started is never written to the cache file.

Offline and deterministic: prepare and the compile step are stubbed, the run goes through the real chain with a
fake runner, ``toolchain_id`` is monkeypatched.

Negative control: reverse-applying the production part of the commit restores the any-``verified:true`` hit
test, so the legacy-java, old-version and environmental tests below fail.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest
import yaml

import foss_mcp.indexing.example_verifier as ev
import infra.build_chunks as build_chunks
from foss_mcp.indexing.example_fixtures import FIXTURE_UNAVAILABLE
from foss_mcp.indexing.example_verifier import RunOutcome, RuntimeRecord, VerificationResult

REPO_ROOT = Path(__file__).resolve().parents[2]
API_SURFACE = REPO_ROOT / "tests/fixtures/pdf_net/api_surface.json"
BUILD_CHUNKS_SOURCE = REPO_ROOT / "infra" / "build_chunks.py"

COMMIT = "4" * 40
REPO = "example-org/reference-lib"
TOOLCHAIN = "toolchain 1.0"
TITLE = "Save a box"
JAVA_CODE = 'Scene s = new Scene();\ns.save("o.stl");'
PY_CODE = "print('hello')"
# a valid binary STL: 80-byte header, uint32 facet count 1, one 50-byte facet
_BINARY_STL_ONE_FACET = b"t".ljust(80, b" ") + (1).to_bytes(4, "little") + bytes(50)
RUN_KEYS = ("compiled", "ran", "exit_code", "produced_output", "runtime_error")


# ---------------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------------


def _page(path: Path, language: str, code: str) -> Path:
    front = {
        "title": "Run facts fixture",
        "single": {"block": [{"title": TITLE, "content": f"Prose.\n\n```{language}\n{code}\n```\n"}]},
    }
    path.write_text("---\n" + yaml.safe_dump(front, sort_keys=False) + "---\n", encoding="utf-8")
    return path


def _argv(out_dir: Path, page: Path, platform: str, extra: list[str]) -> list[str]:
    return [
        "--api-surface",
        str(API_SURFACE),
        "--title",
        "Run facts",
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


def _entries(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return build_chunks.parse_verification_cache(path.read_text(encoding="utf-8"))


class _Java:
    """Java with prepare and compile stubbed; the run goes through the real chain with a fake runner."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.compiles: list[str] = []
        self.runs: list[list[str]] = []
        self.compile_output = "compiled"
        self.compile_verified = True
        self.exit_code = 0

        monkeypatch.setattr(build_chunks, "toolchain_id", lambda platform: TOOLCHAIN)
        monkeypatch.setattr(
            build_chunks, "prepare_java_library", lambda repo, commit, workdir: workdir / "lib.jar"
        )

        def fake_verify(cand, *, workdir, library_jar, page_imports, **_kw):
            self.compiles.append(cand.title)
            (workdir / "candidate_project").mkdir(parents=True, exist_ok=True)
            return VerificationResult(
                candidate=cand, verified=self.compile_verified, output=self.compile_output
            )

        monkeypatch.setitem(build_chunks._PLATFORM_DISPATCH["java"], "verify", fake_verify)
        monkeypatch.setitem(build_chunks._RUNNABLE_VERIFIERS, "java", fake_verify)

        def runner(argv: Sequence[str], cwd: Path, timeout_seconds: float) -> RunOutcome:
            self.runs.append(list(argv))
            if self.exit_code == 0:
                (cwd / "o.stl").write_bytes(_BINARY_STL_ONE_FACET)
            return RunOutcome(exit_code=self.exit_code, stderr="" if self.exit_code == 0 else "boom")

        monkeypatch.setattr(ev, "default_runtime_runner", runner)

    def build(self, tmp: Path, cache: Path | None, cache_out: Path | None, name: str = "out") -> Path:
        out = tmp / name
        out.mkdir(exist_ok=True)
        extra: list[str] = []
        if cache is not None:
            extra += ["--verification-cache", str(cache)]
        if cache_out is not None:
            extra += ["--verification-cache-out", str(cache_out)]
        _main(_argv(out, _page(tmp / "_index.md", "java", JAVA_CODE), "java", extra))
        return out


class _Python:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.verifies: list[str] = []
        self.status = ""
        self.verified = True
        self.output = "python output"
        monkeypatch.setattr(build_chunks, "toolchain_id", lambda platform: TOOLCHAIN)
        monkeypatch.setattr(
            build_chunks, "prepare_python_library", lambda repo, commit, workdir: Path("venv") / "python"
        )

        def fake_verify(candidate, *, venv_python: Path, workdir: Path) -> VerificationResult:
            self.verifies.append(candidate.title)
            return VerificationResult(
                candidate=candidate, verified=self.verified, output=self.output, status=self.status
            )

        monkeypatch.setitem(build_chunks._PLATFORM_DISPATCH["python"], "verify", fake_verify)

    def build(self, tmp: Path, cache: Path | None, cache_out: Path | None) -> None:
        extra: list[str] = []
        if cache is not None:
            extra += ["--verification-cache", str(cache)]
        if cache_out is not None:
            extra += ["--verification-cache-out", str(cache_out)]
        _main(_argv(tmp, _page(tmp / "_index.md", "python", PY_CODE), "python", extra))


def _legacy(path: Path, *, keep_run_facts: bool = False) -> None:
    """Rewrite the cache at ``path`` as an entry written before TC-801: no ``verifier_version`` and, unless
    ``keep_run_facts``, none of the TC-565 run keys either (the pre-TC-565 shape)."""
    entries = _entries(path)
    for entry in entries:
        entry.pop("verifier_version", None)
        if not keep_run_facts:
            for key in RUN_KEYS:
                entry.pop(key, None)
    path.write_text(build_chunks.verification_cache_document(entries), encoding="utf-8")


def _seed_java(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[_Java, Path]:
    """A java cache file holding one genuinely written (current-version, run-record) entry."""
    java = _Java(monkeypatch)
    cache = tmp_path / "cache.json"
    java.build(tmp_path, None, cache, "seed")
    assert len(java.compiles) == 1 and len(java.runs) == 1
    (entry,) = _entries(cache)
    assert entry["verifier_version"] == ev.VERIFIER_VERSION and entry["ran"] is True
    return java, cache


# ---------------------------------------------------------------------------------------------------
# runnable platform (java): a stale or unrun entry is a miss
# ---------------------------------------------------------------------------------------------------


def test_a_legacy_java_entry_without_run_facts_is_a_miss_and_is_run_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    java, cache = _seed_java(monkeypatch, tmp_path)
    _legacy(cache)
    out = java.build(tmp_path, cache, cache, "again")
    assert len(java.compiles) == 2 and len(java.runs) == 2
    (entry,) = _entries(cache)
    assert entry["ran"] is True and entry["verifier_version"] == ev.VERIFIER_VERSION
    text = json.loads((out / "chunks.json").read_text(encoding="utf-8"))["chunks"]
    assert any("Runtime-Status: ran" in chunk["text"] for chunk in text)
    report = json.loads((out / "chunks.verification.json").read_text(encoding="utf-8"))
    assert report[0]["cached"] is False


def test_a_java_entry_with_run_facts_but_no_verifier_version_is_a_miss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    java, cache = _seed_java(monkeypatch, tmp_path)
    _legacy(cache, keep_run_facts=True)
    java.build(tmp_path, cache, cache, "again")
    assert len(java.compiles) == 2 and len(java.runs) == 2


def test_a_java_entry_with_the_run_record_and_the_current_version_is_a_hit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    java, cache = _seed_java(monkeypatch, tmp_path)
    out = java.build(tmp_path, cache, None, "again")
    assert len(java.compiles) == 1 and len(java.runs) == 1, (
        "a current entry must not be verified or run again"
    )
    report = json.loads((out / "chunks.verification.json").read_text(encoding="utf-8"))
    assert report[0]["cached"] is True and report[0]["verified"] is True


def test_a_java_entry_with_an_older_verifier_version_is_a_miss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    java, cache = _seed_java(monkeypatch, tmp_path)
    monkeypatch.setattr(build_chunks, "VERIFIER_VERSION", ev.VERIFIER_VERSION + 1)
    java.build(tmp_path, cache, cache, "again")
    assert len(java.compiles) == 2 and len(java.runs) == 2
    (entry,) = _entries(cache)
    assert entry["verifier_version"] == ev.VERIFIER_VERSION + 1, "the re-run entry replaces the stale one"


def test_a_runnable_verified_true_entry_whose_run_failed_is_still_a_hit_when_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A genuine run verdict (the example throws) is a real, durable verdict: not re-run every build."""
    java = _Java(monkeypatch)
    java.exit_code = 1
    cache = tmp_path / "cache.json"
    java.build(tmp_path, None, cache, "seed")
    (entry,) = _entries(cache)
    assert entry["ran"] is True and entry["exit_code"] == 1
    java.build(tmp_path, cache, None, "again")
    assert len(java.compiles) == 1 and len(java.runs) == 1


# ---------------------------------------------------------------------------------------------------
# non-runnable platforms: legacy still hits (the committed caches keep validating)
# ---------------------------------------------------------------------------------------------------


def test_a_legacy_python_entry_is_still_a_hit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    python = _Python(monkeypatch)
    cache = tmp_path / "cache.json"
    python.build(tmp_path, None, cache)
    assert python.verifies == [TITLE]
    _legacy(cache)
    python.build(tmp_path, cache, None)
    assert python.verifies == [TITLE], "a legacy entry on a platform that is not run must still hit"


def test_a_legacy_rust_entry_is_still_a_hit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build_chunks, "toolchain_id", lambda platform: TOOLCHAIN)
    verifies: list[str] = []

    def fake_verify(candidate, *, workdir, library_crate_name, library_dir):
        verifies.append(candidate.title)
        return VerificationResult(candidate=candidate, verified=True, output="ok")

    monkeypatch.setattr(build_chunks, "prepare_rust_library", lambda repo, commit, workdir: workdir)
    monkeypatch.setitem(build_chunks._PLATFORM_DISPATCH["rust"], "verify", fake_verify)
    page = _page(tmp_path / "_index.md", "rust", 'fn main() { println!("ok"); }')
    extra = ["--library-crate-name", "example_crate"]
    cache = tmp_path / "cache.json"
    _main(_argv(tmp_path, page, "rust", [*extra, "--verification-cache-out", str(cache)]))
    assert verifies == [TITLE]
    _legacy(cache)
    _main(_argv(tmp_path, page, "rust", [*extra, "--verification-cache", str(cache)]))
    assert verifies == [TITLE]


def test_a_python_entry_that_names_an_older_verifier_version_is_a_miss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    python = _Python(monkeypatch)
    cache = tmp_path / "cache.json"
    python.build(tmp_path, None, cache)
    (entry,) = _entries(cache)
    assert entry["verifier_version"] == ev.VERIFIER_VERSION
    monkeypatch.setattr(build_chunks, "VERIFIER_VERSION", ev.VERIFIER_VERSION + 1)
    python.build(tmp_path, cache, None)
    assert python.verifies == [TITLE, TITLE]


# ---------------------------------------------------------------------------------------------------
# the hit rule itself, without a build
# ---------------------------------------------------------------------------------------------------

_V = ev.VERIFIER_VERSION
_BASE = {"verified": True}


@pytest.mark.parametrize(
    ("platform", "entry", "expected"),
    [
        ("java", {**_BASE}, False),
        ("dotnet", {**_BASE}, False),
        ("java", {**_BASE, "compiled": True, "ran": True, "exit_code": 0, "produced_output": 4}, False),
        ("java", {**_BASE, "verifier_version": _V}, False),
        ("java", {**_BASE, "ran": True, "verifier_version": _V}, True),
        ("dotnet", {**_BASE, "ran": True, "exit_code": 1, "verifier_version": _V}, True),
        ("java", {**_BASE, "ran": True, "verifier_version": _V - 1}, False),
        ("java", {**_BASE, "ran": True, "verifier_version": _V + 1}, False),
        ("java", {**_BASE, "ran": True, "verifier_version": True}, False),
        ("go", {**_BASE}, False),  # TC-805: go is run, so an entry with no run record is a miss
        ("go", {**_BASE, "ran": True, "verifier_version": _V}, True),
        ("python", {**_BASE}, True),
        ("rust", {**_BASE, "verifier_version": _V}, False),  # TC-806: rust is run, so no run record is a miss
        ("rust", {**_BASE, "ran": True, "verifier_version": _V}, True),
        ("python", {**_BASE, "verifier_version": _V - 1}, False),
        ("cpp", {**_BASE, "verifier_version": "1"}, False),
    ],
)
def test_cache_entry_is_current_rule(platform: str, entry: dict, expected: bool) -> None:
    assert build_chunks.cache_entry_is_current(entry, platform) is expected


def test_a_stand_in_verifier_that_is_not_the_runnable_one_is_not_held_to_a_run_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run record is expected exactly where the build would produce one: the verifier in use IS the real
    runnable one. A test double compiles nothing, runs nothing, so its current-version entry still hits."""
    java = _Java(monkeypatch)
    stand_in = build_chunks._PLATFORM_DISPATCH["java"]["verify"]
    monkeypatch.setitem(build_chunks._RUNNABLE_VERIFIERS, "java", object())
    monkeypatch.setitem(build_chunks._PLATFORM_DISPATCH["java"], "verify", stand_in)
    cache = tmp_path / "cache.json"
    java.build(tmp_path, None, cache, "seed")
    (entry,) = _entries(cache)
    assert "ran" not in entry and entry["verifier_version"] == ev.VERIFIER_VERSION
    java.build(tmp_path, cache, None, "again")
    assert len(java.compiles) == 1 and len(java.runs) == 0


def test_the_runnable_set_the_hit_rule_uses_is_the_one_that_runs_candidates() -> None:
    assert set(build_chunks._RUNNABLE_VERIFIERS) == set(ev.RUNNABLE_PLATFORMS)


# ---------------------------------------------------------------------------------------------------
# entry shape: additive and backward compatible
# ---------------------------------------------------------------------------------------------------


def _base_entry(**overrides: object) -> dict:
    entry: dict = {
        "provenance": "readme",
        "title": TITLE,
        "code_sha256": "a" * 64,
        "library_commit": COMMIT,
        "platform": "java",
        "toolchain": TOOLCHAIN,
        "verified": True,
        "output": "",
    }
    entry.update(overrides)
    return entry


def test_parse_accepts_a_legacy_entry_and_an_entry_with_the_version() -> None:
    legacy = _base_entry()
    current = _base_entry(ran=True, verifier_version=ev.VERIFIER_VERSION)
    text = build_chunks.verification_cache_document([legacy, current])
    assert build_chunks.parse_verification_cache(text) == [legacy, current]


def test_every_entry_written_carries_the_verifier_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    java = _Java(monkeypatch)
    java.compile_verified = False  # a compile failure is a durable verdict and is written, versioned
    java.compile_output = "error: cannot find symbol"
    cache = tmp_path / "cache.json"
    java.build(tmp_path, None, cache)
    (entry,) = _entries(cache)
    assert entry["verified"] is False and entry["verifier_version"] == ev.VERIFIER_VERSION


# ---------------------------------------------------------------------------------------------------
# environmental verdicts are never persisted
# ---------------------------------------------------------------------------------------------------


def test_a_compile_timeout_is_not_written_to_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    java = _Java(monkeypatch)
    java.compile_verified = False
    java.compile_output = "partial\n[foss-mcp] TIMEOUT: javac was killed after 60 seconds"
    cache = tmp_path / "cache.json"
    java.build(tmp_path, None, cache)
    assert len(java.compiles) == 1
    assert _entries(cache) == []


def test_a_run_timeout_is_not_written_to_the_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    java = _Java(monkeypatch)
    java.exit_code = 124
    cache = tmp_path / "cache.json"
    java.build(tmp_path, None, cache)
    assert len(java.runs) == 1
    assert _entries(cache) == []


def test_a_run_that_could_not_be_started_is_not_written_to_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    java = _Java(monkeypatch)

    def cannot_start(argv: Sequence[str], cwd: Path, timeout_seconds: float) -> RunOutcome:
        raise OSError("java not found")

    monkeypatch.setattr(ev, "default_runtime_runner", cannot_start)
    cache = tmp_path / "cache.json"
    java.build(tmp_path, None, cache)
    assert _entries(cache) == []


def test_a_fixture_unavailable_verdict_is_not_written_to_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    python = _Python(monkeypatch)
    python.verified = False
    python.status = FIXTURE_UNAVAILABLE
    cache = tmp_path / "cache.json"
    python.build(tmp_path, None, cache)
    assert python.verifies == [TITLE]
    assert _entries(cache) == []


def test_a_genuine_failure_is_still_written_to_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control for the three tests above: only the environmental verdicts are withheld."""
    python = _Python(monkeypatch)
    python.verified = False
    python.output = "NameError: name 'x' is not defined"
    cache = tmp_path / "cache.json"
    python.build(tmp_path, None, cache)
    (entry,) = _entries(cache)
    assert entry["verified"] is False and entry["verifier_version"] == ev.VERIFIER_VERSION


def test_a_cached_environmental_run_does_not_overwrite_an_existing_good_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale entry is re-run; when that re-run times out nothing is written, so the file keeps what it had."""
    java, cache = _seed_java(monkeypatch, tmp_path)
    _legacy(cache, keep_run_facts=True)
    before = cache.read_text(encoding="utf-8")
    java.exit_code = 124
    java.build(tmp_path, cache, cache, "again")
    assert len(java.runs) == 2
    assert cache.read_text(encoding="utf-8") == before


@pytest.mark.parametrize(
    ("result", "runtime", "expected"),
    [
        (
            VerificationResult(None, False, "x\n[foss-mcp] TIMEOUT: dotnet was killed after 60 seconds"),
            None,
            True,
        ),
        (VerificationResult(None, False, "run exceeded 60s and was stopped\nout"), None, True),
        (VerificationResult(None, False, "unavailable", status=FIXTURE_UNAVAILABLE), None, True),
        (VerificationResult(None, True, "ok"), RuntimeRecord(compiled=True, ran=False, error="x"), True),
        (
            VerificationResult(None, True, "ok"),
            RuntimeRecord(compiled=True, ran=True, exit_code=124, error="timed out after 60 seconds"),
            True,
        ),
        (VerificationResult(None, False, "error: cannot find symbol"), None, False),
        (
            VerificationResult(None, True, "ok"),
            RuntimeRecord(compiled=True, ran=True, exit_code=1, error="boom"),
            False,
        ),
        (
            VerificationResult(None, True, "ok"),
            RuntimeRecord(compiled=True, ran=True, exit_code=0, produced_output=3),
            False,
        ),
        (VerificationResult(None, True, "ok"), RuntimeRecord(compiled=True), False),
        (None, None, False),
    ],
)
def test_is_environmental_verdict(result: object, runtime: RuntimeRecord | None, expected: bool) -> None:
    assert ev.is_environmental_verdict(result, runtime) is expected


# ---------------------------------------------------------------------------------------------------
# liveness: the production entrypoint CALLS both new rules (AGENTS.md: integration and liveness)
# ---------------------------------------------------------------------------------------------------


def test_the_production_entrypoint_calls_the_hit_rule_and_the_environmental_guard() -> None:
    source = BUILD_CHUNKS_SOURCE.read_text(encoding="utf-8")
    start = source.index("def _build_verified_example_chunks(")
    body = source[start : source.index("\ndef ", start + 1)]
    assert "cache_entry_is_current(entry, platform, run_expected)" in body
    assert "is_environmental_verdict(verification_result, runtime)" in body
    assert '"verifier_version": VERIFIER_VERSION' in body
