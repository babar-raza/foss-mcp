"""G3/TC-565 (REQ-G3-009, REQ-G3-008; rows RD3-140, RD3-141, RD3-U03, RD3-U04): EXAMPLE TRUTH.

The verifier used to stop at compilation, so ``find_examples`` labelled as "Compiled or run" an example that
throws (3d/net ``Save("o.obj", FileFormat.WavefrontOBJ)``), writes an empty file (3d/java "Build a Scene From
a Primitive": a 35-byte STL with no facet) or throws an IOException (3d/java "Quick Start (2)"). It now RUNS
what compiled, in a sandbox directory behind an injected runner, and records ``compiled``, ``ran``,
``exit_code`` and ``produced_output`` separately.

Offline and deterministic: no process is started (a fake runner stands in), no toolchain, no network. The
ORACLE for "the artefact has content" is a format reader written in this file from the formats' own
specifications (binary STL: 80-byte header, uint32 N, N 50-byte facets; glTF: JSON with asset.version), never
the verifier's exit code or its own reader. The classification of the real pins is asserted against the
committed verdict caches.

Negative control: ``RUNTIME_RUN_REQUIRED_FOR_VERIFIED = False`` makes a compiled-but-failing example count as
verified again, so the synthetic and the real-pin tests below fail at once.
"""

from __future__ import annotations

import json
import re
import struct
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
import yaml

import foss_mcp.indexing.example_verifier as ev
import infra.build_chunks as build_chunks
from foss_mcp.indexing.example_candidates import CandidateExample
from foss_mcp.indexing.example_verifier import (
    RUNTIME_FAILED,
    RUNTIME_NO_OUTPUT,
    RUNTIME_NOT_COMPILED,
    RUNTIME_RAN_OK,
    RUNTIME_UNKNOWN,
    RunOutcome,
    RuntimeRecord,
    VerificationResult,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = REPO_ROOT / "tests" / "fixtures" / "verified_examples"
FURNISHED = REPO_ROOT / "tests" / "fixtures" / "furnished"
VERIFIER_SOURCE = REPO_ROOT / "src" / "foss_mcp" / "indexing" / "example_verifier.py"

JAVA_CODE = '// Authored example\nScene scene = new Scene();\nscene.save("box.stl");'


# -- the independent oracle: format readers written from the formats' specifications -------------------------


def oracle_binary_stl(facets: int) -> bytes:
    """A binary STL: 80-byte header, little-endian uint32 count, then ``facets`` 50-byte facets."""
    facet = struct.pack("<12fH", 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0)
    return b"foss-mcp test".ljust(80, b" ") + struct.pack("<I", facets) + facet * facets


def oracle_reads_stl_facets(data: bytes) -> int:
    """How many facets an STL file really holds (-1: not an STL), by the specification."""
    if len(data) >= 84:
        (count,) = struct.unpack("<I", data[80:84])
        if len(data) == 84 + 50 * count:
            return count
    if data.lstrip().startswith(b"solid") and b"endsolid" in data:
        return len(re.findall(rb"facet\s+normal", data))
    return -1


EMPTY_ASCII_STL = b"solid empty\nendsolid empty\n"
ASCII_STL = (
    b"solid tri\n facet normal 0 0 1\n  outer loop\n   vertex 0 0 0\n   vertex 1 0 0\n   vertex 0 1 0\n"
    b"  endloop\n endfacet\nendsolid tri\n"
)


def oracle_glb(document: dict) -> bytes:
    """A GLB container: 12-byte header, one JSON chunk (padded with spaces to 4 bytes)."""
    payload = json.dumps(document).encode("utf-8")
    payload += b" " * (-len(payload) % 4)
    return (
        b"glTF"
        + struct.pack("<II", 2, 12 + 8 + len(payload))
        + struct.pack("<I", len(payload))
        + b"JSON"
        + payload
    )


# -- a fake runner ----------------------------------------------------------------------------------------


Runner = Callable[[Sequence[str], Path, float], RunOutcome]


def fake_runner(
    *,
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
    writes: dict[str, bytes] | None = None,
    seen: list | None = None,
    raises: Exception | None = None,
) -> Runner:
    def runner(argv: Sequence[str], cwd: Path, timeout_seconds: float) -> RunOutcome:
        if seen is not None:
            seen.append(
                (list(argv), cwd, timeout_seconds, sorted(p.name for p in cwd.rglob("*") if p.is_file()))
            )
        if raises is not None:
            raise raises
        for name, data in (writes or {}).items():
            target = cwd / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return RunOutcome(exit_code=exit_code, stdout=stdout, stderr=stderr)

    return runner


def run(runner: Runner, code: str = "", platform: str = "java") -> RuntimeRecord:
    return ev.execute_in_sandbox(["java", "Candidate"], runner=runner, code=code, platform=platform)


def candidate(code: str = JAVA_CODE) -> CandidateExample:
    return CandidateExample(
        title="t",
        description="d",
        language="java",
        code=code,
        page_code=(code,),
        code_sha256=build_chunks.normalised_code_sha256(code),
    )


# -- the three synthetic programs: throws, empty, working ----------------------------------------------------


def test_a_program_that_compiles_and_throws_is_a_runtime_failure_not_a_verified_example() -> None:
    stderr = (
        "Unhandled exception. System.NotImplementedException: The method or operation is not implemented.\n"
        "   at Aspose.ThreeD.Scene.Save(String fileName, FileFormat format)\n"
    )
    record = run(fake_runner(exit_code=134, stderr=stderr))
    assert (record.compiled, record.ran, record.exit_code) == (True, True, 134)
    assert record.status == RUNTIME_FAILED
    assert "NotImplementedException" in record.error
    assert not record.error.startswith("at ")
    assert ev.counts_as_verified(record.status) is False


def test_a_program_that_compiles_and_writes_an_empty_artefact_produces_no_output() -> None:
    # The 3d/java pin: a 35-byte STL. The ORACLE confirms the bytes hold no facet.
    stl = b"solid scene\nendsolid scene\n"[:35].ljust(35, b" ")
    assert len(stl) == 35 and oracle_reads_stl_facets(stl) == 0
    record = run(fake_runner(exit_code=0, writes={"box.stl": stl}))
    assert (record.ran, record.exit_code, record.produced_output) == (True, 0, 0)
    assert record.status == RUNTIME_NO_OUTPUT
    assert record.artefact_extensions == ("stl",)
    assert ev.counts_as_verified(record.status) is False


def test_a_working_program_is_verified_and_its_bytes_are_counted() -> None:
    stl = oracle_binary_stl(12)
    assert oracle_reads_stl_facets(stl) == 12
    record = run(fake_runner(exit_code=0, writes={"box.stl": stl}))
    assert record.status == RUNTIME_RAN_OK
    assert record.produced_output == len(stl) == 84 + 50 * 12
    assert ev.counts_as_verified(record.status) is True


def test_stdout_counts_as_output_only_when_no_file_was_written() -> None:
    printed = run(fake_runner(stdout="42\n"))
    assert (printed.status, printed.produced_output) == (RUNTIME_RAN_OK, 3)
    silent = run(fake_runner())
    assert (silent.status, silent.produced_output) == (RUNTIME_NO_OUTPUT, 0)
    # printing a line does not make an empty artefact a result
    empty_but_chatty = run(fake_runner(stdout="saved\n", writes={"a.stl": EMPTY_ASCII_STL}))
    assert empty_but_chatty.status == RUNTIME_NO_OUTPUT


# -- the format readers against the oracle ----------------------------------------------------------------------


@pytest.mark.parametrize("facets", [0, 1, 2, 12, 100])
def test_the_stl_reader_agrees_with_the_specification_on_binary_files(facets: int) -> None:
    data = oracle_binary_stl(facets)
    assert ev.stl_facet_count(data) == oracle_reads_stl_facets(data) == facets


def test_the_stl_reader_reads_ascii_files_and_rejects_garbage() -> None:
    assert ev.stl_facet_count(ASCII_STL) == oracle_reads_stl_facets(ASCII_STL) == 1
    assert ev.stl_facet_count(EMPTY_ASCII_STL) == oracle_reads_stl_facets(EMPTY_ASCII_STL) == 0
    assert ev.stl_facet_count(b"") is None
    assert ev.stl_facet_count(b"not an stl at all") is None
    truncated = oracle_binary_stl(3)[:-10]
    assert ev.stl_facet_count(truncated) is None


def test_the_gltf_reader_accepts_json_and_glb_and_rejects_the_rest() -> None:
    document = {"asset": {"version": "2.0"}, "scenes": [{"nodes": [0]}]}
    assert ev.gltf_is_valid(json.dumps(document).encode()) is True
    assert ev.gltf_is_valid(oracle_glb(document)) is True
    assert ev.gltf_is_valid(b"") is False
    assert ev.gltf_is_valid(b"{not json") is False
    assert ev.gltf_is_valid(json.dumps({"scenes": []}).encode()) is False
    assert ev.gltf_is_valid(json.dumps({"asset": {}}).encode()) is False
    assert ev.gltf_is_valid(b"glTF\x02\x00\x00\x00") is False


def test_an_artefact_the_reader_calls_empty_counts_for_nothing_whatever_its_size() -> None:
    assert ev.artefact_payload_bytes("m.stl", EMPTY_ASCII_STL) == 0
    assert ev.artefact_payload_bytes("m.stl", oracle_binary_stl(0)) == 0
    assert ev.artefact_payload_bytes("m.stl", oracle_binary_stl(2)) == 184
    assert ev.artefact_payload_bytes("m.gltf", b"{}") == 0
    assert ev.artefact_payload_bytes("m.gltf", b'{"asset":{"version":"2.0"}}') == 27
    assert ev.artefact_payload_bytes("m.obj", b"# only a comment\n") == 0
    assert ev.artefact_payload_bytes("m.obj", b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n") == 32
    assert ev.artefact_payload_bytes("m.bin", b"abc") == 3
    assert ev.artefact_payload_bytes("m.bin", b"") == 0


def test_a_glb_written_by_the_program_is_judged_by_its_own_content() -> None:
    good = oracle_glb({"asset": {"version": "2.0"}})
    bad = b"glTF" + b"\x00" * 30
    assert run(fake_runner(writes={"o.glb": good})).status == RUNTIME_RAN_OK
    assert run(fake_runner(writes={"o.glb": bad})).status == RUNTIME_NO_OUTPUT


# -- the sandbox ---------------------------------------------------------------------------------------------


def test_the_program_runs_in_a_fresh_directory_that_is_removed_afterwards() -> None:
    seen: list = []
    run(fake_runner(seen=seen, writes={"x.bin": b"1"}))
    (argv, cwd, timeout, _files) = seen[0]
    assert argv == ["java", "Candidate"]
    assert timeout > 0
    assert cwd.name.startswith("foss_mcp_run_")
    assert not cwd.exists()


def test_the_input_files_the_code_names_are_staged_before_the_run() -> None:
    seen: list = []
    code = 'Scene scene = Scene.fromFile("input/cube.stl");\nscene.save("output.stl");'
    run(fake_runner(seen=seen), code=code)
    assert "cube.stl" in seen[0][3]
    assert "output.stl" not in seen[0][3]


def test_a_staged_input_is_not_counted_as_a_produced_artefact() -> None:
    record = run(fake_runner(), code='Scene.fromFile("input/cube.stl");')
    assert record.status == RUNTIME_NO_OUTPUT
    assert record.artefact_extensions == ()


def test_a_run_that_overruns_is_a_failure_with_a_timeout_message() -> None:
    record = run(fake_runner(exit_code=124, stderr="[foss-mcp] TIMEOUT: java was killed after 60 seconds"))
    assert record.status == RUNTIME_FAILED
    assert record.error.startswith("timed out after")


def test_a_process_that_cannot_be_started_is_ran_false_and_failed() -> None:
    record = run(fake_runner(raises=FileNotFoundError("no such file: java")))
    assert (record.compiled, record.ran, record.exit_code) == (True, False, None)
    assert record.status == RUNTIME_FAILED
    assert "could not start java" in record.error


def test_the_error_line_is_the_first_exception_line_collapsed_and_bounded() -> None:
    stderr = (
        'noise\nException in thread "main"   java.io.IOException:   disk\tfull '
        + "x" * 600
        + "\n\tat A.b(A.java:1)\n"
    )
    record = run(fake_runner(exit_code=1, stderr=stderr))
    assert record.error.startswith('Exception in thread "main" java.io.IOException: disk full')
    assert len(record.error) <= 300
    assert "\n" not in record.error and "\t" not in record.error


# -- classification and the cache fields ----------------------------------------------------------------------------


def test_the_four_facts_are_separate_in_the_cache_fields_and_unknowns_are_left_out() -> None:
    full = RuntimeRecord(compiled=True, ran=True, exit_code=1, produced_output=0, error="boom")
    assert full.as_cache_fields() == {
        "compiled": True,
        "ran": True,
        "exit_code": 1,
        "produced_output": 0,
        "runtime_error": "boom",
    }
    assert RuntimeRecord(compiled=True).as_cache_fields() == {"compiled": True}


@pytest.mark.parametrize(
    ("record", "status"),
    [
        (RuntimeRecord(compiled=False), RUNTIME_NOT_COMPILED),
        (RuntimeRecord(compiled=True), RUNTIME_UNKNOWN),
        (RuntimeRecord(compiled=True, ran=False), RUNTIME_FAILED),
        (RuntimeRecord(compiled=True, ran=True, exit_code=2, produced_output=10), RUNTIME_FAILED),
        (RuntimeRecord(compiled=True, ran=True, error="NotImplementedException"), RUNTIME_FAILED),
        (RuntimeRecord(compiled=True, ran=True, exit_code=0, produced_output=0), RUNTIME_NO_OUTPUT),
        (RuntimeRecord(compiled=True, ran=True, exit_code=0, produced_output=35), RUNTIME_RAN_OK),
    ],
)
def test_classification_of_the_recorded_facts(record: RuntimeRecord, status: str) -> None:
    assert ev.classify_runtime(record) == status


def test_an_entry_written_before_the_card_is_read_as_ran_unknown_never_as_ran() -> None:
    legacy = {"verified": True, "output": "Build succeeded."}
    record = ev.runtime_record_from_entry(legacy)
    assert (record.compiled, record.ran) == (True, None)
    assert record.status == RUNTIME_UNKNOWN
    assert ev.runtime_record_from_entry({"verified": False}).status == RUNTIME_NOT_COMPILED


def test_a_compile_only_example_stays_verified_when_its_run_was_never_attempted() -> None:
    assert ev.entry_counts_as_verified({"verified": True}) is True
    assert ev.entry_counts_as_verified({"verified": False}) is False


def test_an_unsupported_platform_is_not_run_and_stays_ran_unknown() -> None:
    seen: list = []
    record = ev.run_compiled_candidate("rust", candidate(), workdir=Path("w"), runner=fake_runner(seen=seen))
    assert record == RuntimeRecord(compiled=True)
    assert seen == []


def test_java_is_started_with_the_candidate_class_on_the_class_path(tmp_path: Path) -> None:
    seen: list = []
    jar = tmp_path / "lib.jar"
    wrapper = candidate()
    ev.run_compiled_candidate("java", wrapper, workdir=tmp_path, library=jar, runner=fake_runner(seen=seen))
    argv = seen[0][0]
    assert argv[:2] == ["java", "-cp"] and argv[-1] == "Candidate"
    assert str(tmp_path / "candidate_project") in argv[2] and str(jar) in argv[2]
    own_class = candidate("public class Demo { public static void main(String[] a) {} }")
    ev.run_compiled_candidate("java", own_class, workdir=tmp_path, library=jar, runner=fake_runner(seen=seen))
    assert seen[1][0][-1] == "Demo"


def test_dotnet_is_started_without_building_again(tmp_path: Path) -> None:
    seen: list = []
    ev.run_compiled_candidate("dotnet", candidate(), workdir=tmp_path, runner=fake_runner(seen=seen))
    argv = seen[0][0]
    assert argv[:3] == ["dotnet", "run", "--no-build"]
    assert argv[-1].endswith("candidate.csproj")


# -- the switch ---------------------------------------------------------------------------------------------------


def test_the_switch_is_the_one_mandated_line_in_the_verifier() -> None:
    source = VERIFIER_SOURCE.read_text(encoding="utf-8")
    assert source.count("RUNTIME_RUN_REQUIRED_FOR_VERIFIED = True") == 1
    assert ev.RUNTIME_RUN_REQUIRED_FOR_VERIFIED is True


def test_the_switch_off_restores_the_compile_only_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ev, "RUNTIME_RUN_REQUIRED_FOR_VERIFIED", False)
    assert ev.counts_as_verified(RUNTIME_FAILED) is True
    assert ev.counts_as_verified(RUNTIME_NO_OUTPUT) is True
    assert ev.counts_as_verified(RUNTIME_NOT_COMPILED) is False
    seen: list = []
    record = ev.run_compiled_candidate(
        "java", candidate(), workdir=Path("w"), library=Path("l.jar"), runner=fake_runner(seen=seen)
    )
    assert record == RuntimeRecord(compiled=True) and seen == []


# -- the real pins, classified from the committed verdict caches -----------------------------------------------------

PINS = [
    ("3d_net", "Additional Examples", RUNTIME_FAILED),
    ("3d_java", "Build a Scene From a Primitive", RUNTIME_NO_OUTPUT),
    ("3d_java", "Quick Start (2)", RUNTIME_FAILED),
    ("pdf_cpp", "Open a Password-Protected Document", RUNTIME_FAILED),
]


def _entries(pilot: str, title: str) -> list[dict]:
    text = (CACHE_DIR / f"{pilot}.json").read_text(encoding="utf-8")
    return [
        e
        for e in build_chunks.parse_verification_cache(text)
        if e["title"] == title and e["provenance"] == "readme"
    ]


@pytest.mark.parametrize(("pilot", "title", "status"), PINS, ids=[f"{p}:{t}" for p, t, _ in PINS])
def test_the_real_pin_is_classified_from_the_committed_cache_and_is_not_verified(
    pilot: str, title: str, status: str
) -> None:
    entries = _entries(pilot, title)
    assert entries, f"{pilot} has no readme entry titled {title!r}"
    for entry in entries:
        assert entry["verified"] is True, "the compile verdict keeps its meaning"
        assert entry["compiled"] is True and entry["ran"] is True
        assert ev.runtime_record_from_entry(entry).status == status
        assert ev.entry_counts_as_verified(entry) is False


def test_the_empty_stl_pin_records_exit_zero_and_zero_bytes() -> None:
    for entry in _entries("3d_java", "Build a Scene From a Primitive"):
        assert (entry["exit_code"], entry["produced_output"]) == (0, 0)


def test_the_failing_pins_carry_the_error_that_the_label_will_quote() -> None:
    assert "NotImplementedException" in _entries("3d_net", "Additional Examples")[0]["runtime_error"]
    assert "IOException" in _entries("3d_java", "Quick Start (2)")[0]["runtime_error"]
    assert "zlib" in _entries("pdf_cpp", "Open a Password-Protected Document")[0]["runtime_error"]


def test_every_other_committed_entry_is_unchanged_and_ran_unknown() -> None:
    pinned = {(pilot, title) for pilot, title, _ in PINS}
    seen_other = 0
    for path in sorted(CACHE_DIR.glob("*.json")):
        for entry in build_chunks.parse_verification_cache(path.read_text(encoding="utf-8")):
            if (path.stem, entry["title"]) in pinned and entry["provenance"] == "readme":
                continue
            assert "ran" not in entry, (path.stem, entry["title"])
            assert ev.runtime_record_from_entry(entry).status in (RUNTIME_UNKNOWN, RUNTIME_NOT_COMPILED)
            seen_other += 1
    assert seen_other > 100


def test_the_committed_caches_are_still_canonical_documents() -> None:
    for name in ("3d_net", "3d_java", "pdf_cpp"):
        text = (CACHE_DIR / f"{name}.json").read_text(encoding="utf-8")
        assert build_chunks.verification_cache_document(build_chunks.parse_verification_cache(text)) == text


# -- scope documentation: formats that are not implemented ----------------------------------------------------------


def _page(pilot: str) -> dict:
    text = (FURNISHED / pilot / "pages" / "_index.md").read_text(encoding="utf-8")
    return yaml.safe_load(text.split("---", 2)[1])


def test_3d_java_scope_says_stl_is_the_only_working_format() -> None:
    unimplemented = ev.scope_unimplemented_formats(_page("3d_java"))
    assert "stl" not in unimplemented
    assert {"obj", "fbx", "gltf", "ply"} <= unimplemented


def test_3d_net_scope_names_draco_and_pdf_but_not_the_formats_that_work() -> None:
    unimplemented = ev.scope_unimplemented_formats(_page("3d_net"))
    assert "drc" in unimplemented
    assert not {"stl", "ply"} & unimplemented


def test_a_page_without_a_scope_section_rejects_nothing() -> None:
    assert ev.scope_unimplemented_formats({"title": "x"}) == frozenset()
    assert (
        ev.scope_unimplemented_formats({"content": {"block": [{"title_left": "Quick Start"}]}}) == frozenset()
    )


def test_an_empty_artefact_in_an_unimplemented_format_is_rejected_but_not_one_in_a_working_format() -> None:
    empty = RuntimeRecord(compiled=True, ran=True, exit_code=0, produced_output=0)
    unimplemented = frozenset({"obj", "fbx"})
    assert ev.rejected_for_unimplemented_format(empty, 'scene.save("a.obj");', unimplemented) is True
    assert ev.rejected_for_unimplemented_format(empty, 'scene.save("a.stl");', unimplemented) is False
    failed = RuntimeRecord(compiled=True, ran=True, exit_code=1, error="boom")
    assert ev.rejected_for_unimplemented_format(failed, 'scene.save("a.obj");', unimplemented) is False
    working = RuntimeRecord(compiled=True, ran=True, exit_code=0, produced_output=9)
    assert ev.rejected_for_unimplemented_format(working, 'scene.save("a.obj");', unimplemented) is False


# -- the wiring: build_chunks runs what compiled and records the run ------------------------------------------------

LIB_COMMIT = "3" * 40
LIB_REPO = "example-org/reference-java"
API_SURFACE = REPO_ROOT / "tests/fixtures/pdf_net/api_surface.json"
TOOLCHAIN = "javac 21.0.0"


def _java_page(path: Path, blocks: list[tuple[str, str]]) -> Path:
    front = {
        "title": "Java runtime fixture",
        "single": {
            "block": [
                {"title": title, "content": f"Prose for {title}.\n\n```java\n{code}\n```\n"}
                for title, code in blocks
            ]
        },
    }
    path.write_text("---\n" + yaml.safe_dump(front, sort_keys=False) + "---\n", encoding="utf-8")
    return path


def _argv(out_dir: Path, page: Path, extra: list[str] | None = None) -> list[str]:
    return [
        "--api-surface",
        str(API_SURFACE),
        "--title",
        "Runtime fixture",
        "--out",
        str(out_dir / "chunks.json"),
        "--furnished-page",
        str(page),
        "--library-repository",
        LIB_REPO,
        "--library-commit",
        LIB_COMMIT,
        "--library-platform",
        "java",
        *(extra or []),
    ]


def _main(argv: list[str]) -> None:
    old = build_chunks.sys.argv
    build_chunks.sys.argv = ["build_chunks.py", *argv]
    try:
        build_chunks.main()
    finally:
        build_chunks.sys.argv = old


def _example_texts(out_dir: Path) -> dict[str, str]:
    chunks = json.loads((out_dir / "chunks.json").read_text(encoding="utf-8"))["chunks"]
    texts = {}
    for chunk in chunks:
        match = re.search(r"^FQN: Example: (.+)$", chunk["text"], re.MULTILINE)
        if match and "Kind: verified_example" in chunk["text"]:
            texts[match.group(1)] = chunk["text"]
    return texts


@pytest.fixture
def java_pipeline(monkeypatch: pytest.MonkeyPatch):
    """Java prepare and compile stubbed (every candidate compiles); the RUN goes through the real chain with
    a fake runner chosen per example by its code."""
    monkeypatch.setattr(build_chunks, "toolchain_id", lambda platform: TOOLCHAIN)
    monkeypatch.setattr(
        build_chunks, "prepare_java_library", lambda repo, commit, workdir: workdir / "lib.jar"
    )
    compile_calls: list[str] = []

    def fake_verify(cand, *, workdir, library_jar, page_imports, **_kw):
        compile_calls.append(cand.title)
        (workdir / "candidate_project").mkdir(parents=True, exist_ok=True)
        return VerificationResult(candidate=cand, verified=True, output="compiled")

    monkeypatch.setitem(build_chunks._PLATFORM_DISPATCH["java"], "verify", fake_verify)
    # the stand-in plays the real compiler, which leaves the compiled project in the workdir
    monkeypatch.setitem(build_chunks._RUNNABLE_VERIFIERS, "java", fake_verify)
    runs: list[list[str]] = []

    def dispatching_runner(argv: Sequence[str], cwd: Path, timeout_seconds: float) -> RunOutcome:
        runs.append(list(argv))
        marker = _CURRENT["mode"]
        if marker == "throw":
            return RunOutcome(exit_code=1, stderr='Exception in thread "main" java.io.IOException: nope')
        if marker == "empty":
            (cwd / "o.stl").write_bytes(EMPTY_ASCII_STL)
            return RunOutcome(exit_code=0)
        (cwd / "o.stl").write_bytes(oracle_binary_stl(4))
        return RunOutcome(exit_code=0)

    monkeypatch.setattr(ev, "default_runtime_runner", dispatching_runner)
    return compile_calls, runs


_CURRENT = {"mode": "ok"}


def test_build_chunks_runs_what_compiled_and_publishes_the_run_in_the_chunk(
    tmp_path: Path, java_pipeline
) -> None:
    compile_calls, runs = java_pipeline
    page = _java_page(tmp_path / "_index.md", [("Save a box", 'Scene s = new Scene();\ns.save("o.stl");')])
    for mode, status, produced, error in [
        ("ok", "ran", len(oracle_binary_stl(4)), None),
        ("empty", "no_output", 0, None),
        ("throw", "failed", None, "java.io.IOException: nope"),
    ]:
        _CURRENT["mode"] = mode
        out = tmp_path / mode
        out.mkdir()
        _main(_argv(out, page))
        text = _example_texts(out)["Save a box"]
        assert f"Runtime-Status: {status}\n" in text
        assert (f"Runtime-Produced-Bytes: {produced}\n" in text) is (produced is not None)
        assert ("Runtime-Error: Exception in thread" in text) is (error is not None)
        assert (
            text.index("Kind: verified_example") < text.index("Runtime-Status:") < text.index("\nExample:\n")
        )
    assert len(runs) == 3 and all(argv[0] == "java" for argv in runs)


def test_the_report_and_the_cache_record_compiled_ran_exit_code_and_produced_output(
    tmp_path: Path, java_pipeline
) -> None:
    _CURRENT["mode"] = "empty"
    page = _java_page(tmp_path / "_index.md", [("Save a box", 'Scene s = new Scene();\ns.save("o.stl");')])
    cache_out = tmp_path / "cache.json"
    _main(
        _argv(
            tmp_path,
            page,
            ["--verification-cache", str(tmp_path / "none.json"), "--verification-cache-out", str(cache_out)],
        )
    )
    (entry,) = build_chunks.parse_verification_cache(cache_out.read_text(encoding="utf-8"))
    assert entry["verified"] is True and entry["compiled"] is True and entry["ran"] is True
    assert (entry["exit_code"], entry["produced_output"]) == (0, 0)
    report = json.loads((tmp_path / "chunks.verification.json").read_text(encoding="utf-8"))
    assert report[0]["verified"] is False and report[0]["runtime_status"] == "no_output"
    assert ev.entry_counts_as_verified(entry) is False


def test_a_cached_failing_example_is_not_run_again_and_publishes_the_same_chunk(
    tmp_path: Path, java_pipeline
) -> None:
    _CURRENT["mode"] = "throw"
    page = _java_page(tmp_path / "_index.md", [("Save a box", 'Scene s = new Scene();\ns.save("o.stl");')])
    cache = tmp_path / "cache.json"
    first = tmp_path / "first"
    first.mkdir()
    _main(_argv(first, page, ["--verification-cache", str(cache), "--verification-cache-out", str(cache)]))
    compile_calls, runs = java_pipeline
    assert len(runs) == 1
    second = tmp_path / "second"
    second.mkdir()
    _main(_argv(second, page, ["--verification-cache", str(cache)]))
    assert len(runs) == 1 and len(compile_calls) == 1
    assert _example_texts(first) == _example_texts(second)
    assert "Runtime-Status: failed" in _example_texts(second)["Save a box"]


@pytest.mark.parametrize(("mode", "published"), [("throw", False), ("empty", False), ("ok", True)])
def test_an_authored_example_is_published_only_when_it_ran_and_produced_output(
    tmp_path: Path, java_pipeline, mode: str, published: bool
) -> None:
    """The 3d/java pilot carries its authored examples in authored_examples.py: they go through the same
    compile-and-RUN verifier, and one that fails or writes nothing is never labelled as verified."""
    _CURRENT["mode"] = mode
    pilot = tmp_path / "3d_java"
    (pilot / "pages").mkdir(parents=True)
    page = _java_page(
        pilot / "pages" / "_index.md", [("Save a box", 'Scene s = new Scene();\ns.save("o.stl");')]
    )
    surface = REPO_ROOT / "tests" / "fixtures" / "3d_java" / "api_surface.json"
    argv = _argv(tmp_path, page)
    argv[argv.index("--api-surface") + 1] = str(surface)
    argv[argv.index("--library-commit") + 1] = "3d2ed6be91f5abdd1c52ecbcfa192719883cb1b3"
    _main(argv)
    texts = _example_texts(tmp_path)
    assert "Save a box" in texts
    authored = {title: text for title, text in texts.items() if "Provenance: authored" in text}
    assert bool(authored) is published
    for text in authored.values():
        assert "Verification: passed" in text and "Runtime-Status: ran" in text


# -- liveness: the run step is CALLED by the production entrypoint, not only correct in isolation ---------------------------

BUILD_CHUNKS_SOURCE = REPO_ROOT / "infra" / "build_chunks.py"
FIND_EXAMPLES_SOURCE = REPO_ROOT / "src" / "foss_mcp" / "mcp" / "tools" / "find_examples.py"


def test_the_production_entrypoint_calls_the_run_step_and_the_classification() -> None:
    """AGENTS.md (integration and liveness): a green unit test does not prove the function is called."""
    source = BUILD_CHUNKS_SOURCE.read_text(encoding="utf-8")
    for call in (
        "run_compiled_candidate(",
        "counts_as_verified(",
        "runtime_record_from_entry(",
        "scope_unimplemented_formats(",
        "rejected_for_unimplemented_format(",
        "_runtime_chunk_lines(",
    ):
        assert call in source, f"infra/build_chunks.py never calls {call}"
    assert "from foss_mcp.indexing.example_verifier import" in source


def test_the_server_tool_reads_the_runtime_lines_the_entrypoint_writes() -> None:
    source = FIND_EXAMPLES_SOURCE.read_text(encoding="utf-8")
    for line in ("Runtime-Status:", "Runtime-Produced-Bytes:", "Runtime-Error:"):
        assert line in source
        assert line in BUILD_CHUNKS_SOURCE.read_text(encoding="utf-8")
    assert "_runtime_rank(" in source and "_same_example(" in source


def test_the_run_step_cannot_be_reached_from_a_platform_it_does_not_know() -> None:
    assert ev.RUNNABLE_PLATFORMS == frozenset({"java", "dotnet"})
    for platform in set(build_chunks._PLATFORM_DISPATCH) - ev.RUNNABLE_PLATFORMS:
        assert ev.runtime_command(platform, candidate(), workdir=Path("w"), library=Path("l")) is None


def test_a_stand_in_verifier_compiled_nothing_so_nothing_is_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, java_pipeline
) -> None:
    """Only the real compiler step leaves a compiled project to run; a test double must never start a process."""
    _compile_calls, runs = java_pipeline
    monkeypatch.setitem(build_chunks._RUNNABLE_VERIFIERS, "java", ev.verify_java_example)
    _CURRENT["mode"] = "ok"
    page = _java_page(tmp_path / "_index.md", [("Save a box", 'Scene s = new Scene();\ns.save("o.stl");')])
    _main(_argv(tmp_path, page))
    assert runs == []
    text = _example_texts(tmp_path)["Save a box"]
    assert "Runtime-Status:" not in text, "ran unknown: no runtime line, read as compiled only"


def test_the_runnable_verifiers_are_the_real_java_and_dotnet_compilers() -> None:
    assert build_chunks._RUNNABLE_VERIFIERS == {
        "java": ev.verify_java_example,
        "dotnet": ev.verify_dotnet_example,
    }
