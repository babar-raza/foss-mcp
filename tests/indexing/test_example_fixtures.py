"""TC-459 (REQ-G3-007): deterministic input-file supply for README examples.

Offline, deterministic: no network, no g++, no cmake, no real subprocess beyond a couple of
tiny throwaway scripts run with the running interpreter. Covers ``example_fixtures.py`` on its
own, its wiring into ``verify_python_example`` and ``verify_cpp_example_linux``, and the
``fixture_unavailable`` classification.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

import infra.build_chunks as build_chunks
from foss_mcp.indexing import example_fixtures as ef
from foss_mcp.indexing import example_verifier as ev
from foss_mcp.indexing.example_candidates import CandidateExample
from foss_mcp.indexing.example_verifier import (
    VerificationResult,
    verify_cpp_example_linux,
    verify_python_example,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
NOTE_PAGE = REPO_ROOT / "tests" / "fixtures" / "furnished" / "note_python" / "pages" / "_index.md"
PAGE_PYTHON_PAGE = REPO_ROOT / "tests" / "fixtures" / "furnished" / "page_python" / "pages" / "_index.md"


def _py_candidate(code: str) -> CandidateExample:
    return CandidateExample(title="block", description="", language="python", code=code)


def _cpp_candidate(code: str) -> CandidateExample:
    return CandidateExample(title="block", description="", language="cpp", code=code)


def _completed(
    args: list[str], code: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args, code, stdout=stdout, stderr=stderr)


def _script_tools(monkeypatch: pytest.MonkeyPatch, present: set[str]) -> None:
    def fake_which(name: str, *args: object, **kwargs: object) -> str | None:
        return f"/usr/bin/{name}" if name in present else None

    monkeypatch.setattr(ev.shutil, "which", fake_which)


def _dedent_block(text: str) -> str:
    return "\n".join(line.removeprefix("      ") for line in text.splitlines())


def _note_quick_start_block() -> str:
    text = NOTE_PAGE.read_text(encoding="utf-8")
    for match in re.finditer(r"```python\n(.*?)```", text, re.DOTALL):
        if 'Document("SimpleTable.one")' in match.group(1) and "DisplayName" in match.group(1):
            return _dedent_block(match.group(1))
    raise AssertionError("the note_python Quick Start block was not found")


def _page_python_blocks() -> list[str]:
    text = PAGE_PYTHON_PAGE.read_text(encoding="utf-8")
    return [_dedent_block(match.group(1)) for match in re.finditer(r"```python\n(.*?)```", text, re.DOTALL)]


# --- input_literals ------------------------------------------------------------


def test_the_real_note_python_quick_start_block_names_simpletable() -> None:
    assert ef.input_literals(_note_quick_start_block(), "python") == ("SimpleTable.one",)


def test_the_real_page_python_blocks_name_input_ps_eps_xps_and_never_output_pdf() -> None:
    blocks = _page_python_blocks()
    assert blocks
    all_inputs: set[str] = set()
    for block in blocks:
        literals = ef.input_literals(block, "python")
        assert "output.pdf" not in literals
        assert "output.png" not in literals
        all_inputs.update(literals)
    assert all_inputs == {"input.ps", "input.eps", "input.xps"}


def test_a_literal_only_on_a_save_line_is_an_output() -> None:
    assert ef.input_literals('doc.save("report.pdf")\n', "python") == ()


def test_out_pdf_is_an_output() -> None:
    assert ef.input_literals('f = open("out.pdf")\n', "python") == ()


def test_a_testfiles_path_is_kept() -> None:
    assert ef.input_literals('open("testfiles/x.one")\n', "python") == ("testfiles/x.one",)


def test_a_parent_segment_is_ignored() -> None:
    assert ef.input_literals('open("../x.pdf")\n', "python") == ()


def test_an_absolute_path_is_ignored() -> None:
    assert ef.input_literals('open("/abs/x.pdf")\n', "python") == ()


def test_a_url_is_ignored() -> None:
    assert ef.input_literals('open("https://h/x.pdf")\n', "python") == ()


def test_an_fstring_is_ignored() -> None:
    assert ef.input_literals('open(f"x.pdf")\n', "python") == ()


def test_a_cpp_single_quoted_literal_is_ignored() -> None:
    assert ef.input_literals("char c = 'x';\n", "cpp") == ()


def test_input_pdf_uppercase_is_recognised() -> None:
    assert ef.input_literals('open("INPUT.PDF")\n', "python") == ("INPUT.PDF",)


def test_order_and_deduplication() -> None:
    code = 'open("a.pdf")\nopen("b.txt")\nopen("a.pdf")\n'
    assert ef.input_literals(code, "python") == ("a.pdf", "b.txt")


def test_a_cpp_raw_string_is_ignored() -> None:
    assert ef.input_literals('auto *p = R"(input.pdf)";\n', "cpp") == ()


def test_a_cpp_double_quoted_input_literal_is_recognised() -> None:
    assert ef.input_literals('open_file("model.obj");\n', "cpp") == ("model.obj",)


# --- generated_fixture -----------------------------------------------------------

_GENERATED_EXTENSIONS = (
    "txt",
    "md",
    "json",
    "csv",
    "html",
    "htm",
    "eml",
    "obj",
    "stl",
    "ps",
    "eps",
    "png",
    "pdf",
)


@pytest.mark.parametrize("extension", _GENERATED_EXTENSIONS)
def test_every_generated_fixture_is_at_most_1024_bytes_and_deterministic(extension: str) -> None:
    first = ef.generated_fixture(extension)
    second = ef.generated_fixture(extension)
    assert first is not None
    assert second is not None
    name1, data1 = first
    name2, data2 = second
    assert data1 == data2
    assert name1 == name2
    assert len(data1) <= 1024


def test_an_unknown_extension_gives_none() -> None:
    assert ef.generated_fixture("docx") is None
    assert ef.generated_fixture("xps") is None


def test_the_generated_png_has_a_valid_signature_ihdr_idat_and_crcs() -> None:
    import struct
    import zlib

    _, data = ef.generated_fixture("png")
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    offset = 8
    ihdr_length = struct.unpack(">I", data[offset : offset + 4])[0]
    ihdr_tag = data[offset + 4 : offset + 8]
    assert ihdr_tag == b"IHDR"
    ihdr_data = data[offset + 8 : offset + 8 + ihdr_length]
    width, height, bit_depth, color_type = struct.unpack(">IIBB", ihdr_data[:10])
    assert (width, height) == (1, 1)
    assert color_type == 6
    ihdr_crc = struct.unpack(">I", data[offset + 8 + ihdr_length : offset + 12 + ihdr_length])[0]
    assert ihdr_crc == zlib.crc32(ihdr_tag + ihdr_data) & 0xFFFFFFFF
    idat_offset = offset + 12 + ihdr_length
    idat_length = struct.unpack(">I", data[idat_offset : idat_offset + 4])[0]
    idat_tag = data[idat_offset + 4 : idat_offset + 8]
    assert idat_tag == b"IDAT"
    idat_data = data[idat_offset + 8 : idat_offset + 8 + idat_length]
    idat_crc = struct.unpack(">I", data[idat_offset + 8 + idat_length : idat_offset + 12 + idat_length])[0]
    assert idat_crc == zlib.crc32(idat_tag + idat_data) & 0xFFFFFFFF
    decompressed = zlib.decompress(idat_data)
    assert len(decompressed) == 1 + 4  # one filter byte + one RGBA pixel


def test_the_generated_pdf_has_correct_header_and_xref_offsets() -> None:
    _, data = ef.generated_fixture("pdf")
    assert data.startswith(b"%PDF-1.4")
    xref_byte_offset = data.index(b"xref\n")
    startxref_marker = b"startxref\n"
    start = data.index(startxref_marker) + len(startxref_marker)
    end = data.index(b"\n", start)
    declared_offset = int(data[start:end])
    assert declared_offset == xref_byte_offset

    xref_section = data[xref_byte_offset:]
    entries = re.findall(rb"(\d{10}) \d{5} n ", xref_section)
    assert len(entries) == 3
    for entry in entries:
        obj_offset = int(entry)
        marker = data[obj_offset : obj_offset + 20]
        assert re.match(rb"^\d+ 0 obj", marker)


def test_the_generated_eml_has_crlf_line_ends_headers_and_a_blank_line() -> None:
    _, data = ef.generated_fixture("eml")
    text = data.decode("ascii")
    assert "\r\n\r\n" in text
    assert "\n\n" not in text.replace("\r\n", "")
    headers, _, body = text.partition("\r\n\r\n")
    assert "From:" in headers
    assert "To:" in headers
    assert "Subject:" in headers
    assert "Date:" in headers
    assert "Message-ID:" in headers
    assert "MIME-Version:" in headers
    assert "Content-Type: text/plain" in headers
    assert body


def test_the_generated_json_parses() -> None:
    _, data = ef.generated_fixture("json")
    parsed = json.loads(data)
    assert isinstance(parsed, dict)


def test_the_generated_stl_obj_ps_and_eps_carry_their_markers() -> None:
    _, stl = ef.generated_fixture("stl")
    assert stl.startswith(b"solid")
    assert b"facet" in stl

    _, obj = ef.generated_fixture("obj")
    assert obj.count(b"\nv ") + (1 if obj.startswith(b"v ") else 0) == 3
    assert b"\nf " in obj or obj.splitlines()[-1].startswith(b"f ")

    _, ps = ef.generated_fixture("ps")
    assert ps.startswith(b"%!PS-Adobe-3.0\n")
    assert ps.rstrip().endswith(b"showpage")

    _, eps = ef.generated_fixture("eps")
    assert eps.startswith(b"%!PS-Adobe-3.0 EPSF-3.0")
    assert b"%%BoundingBox:" in eps


# --- provide_fixtures ------------------------------------------------------------


def test_an_existing_file_is_kept_present_and_unchanged(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "notes.txt").write_bytes(b"ALREADY-THERE")

    plan = ef.provide_fixtures('open("notes.txt")\n', "python", run_dir=run_dir, repo_dir=None, staged=())

    assert len(plan.records) == 1
    record = plan.records[0]
    assert record.origin == "present"
    assert record.source == ""
    assert (run_dir / "notes.txt").read_bytes() == b"ALREADY-THERE"
    assert record.sha256 == hashlib.sha256(b"ALREADY-THERE").hexdigest()


def test_a_staged_basename_gives_a_repository_record_with_hashlibs_sha256(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    data = b"ONE-CONTENT"
    (run_dir / "SimpleTable.one").write_bytes(data)

    plan = ef.provide_fixtures(
        'open("SimpleTable.one")\n',
        "python",
        run_dir=run_dir,
        repo_dir=None,
        staged=("testfiles/SimpleTable.one",),
    )

    assert len(plan.records) == 1
    record = plan.records[0]
    assert record.origin == "repository"
    assert record.source == "testfiles/SimpleTable.one"
    assert record.sha256 == hashlib.sha256(data).hexdigest()


def test_a_path_literal_is_copied_from_the_exact_repo_path(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    (repo_dir / "testfiles").mkdir(parents=True)
    (repo_dir / "testfiles" / "sample.pdf").write_bytes(b"PDF-BYTES")
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    plan = ef.provide_fixtures(
        'open("testfiles/sample.pdf")\n', "python", run_dir=run_dir, repo_dir=repo_dir, staged=()
    )

    assert plan.records == (
        ef.FixtureRecord(
            literal="testfiles/sample.pdf",
            destination="testfiles/sample.pdf",
            origin="repository",
            source="testfiles/sample.pdf",
            sha256=hashlib.sha256(b"PDF-BYTES").hexdigest(),
        ),
    )
    assert (run_dir / "testfiles" / "sample.pdf").read_bytes() == b"PDF-BYTES"


def test_a_py_file_is_never_recognised_as_an_input_and_so_is_never_copied(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    (repo_dir / "scripts").mkdir(parents=True)
    (repo_dir / "scripts" / "helper.py").write_text("X = 1\n", encoding="utf-8")
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    plan = ef.provide_fixtures(
        'runpy.run_path("scripts/helper.py")\n', "python", run_dir=run_dir, repo_dir=repo_dir, staged=()
    )

    assert plan.records == ()
    assert plan.missing == ()
    assert not (run_dir / "scripts").exists()


def test_a_file_under_git_is_never_copied_from_the_repository(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    (repo_dir / ".git").mkdir(parents=True)
    (repo_dir / ".git" / "hooked.pdf").write_bytes(b"SECRET")
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    plan = ef.provide_fixtures(
        'open(".git/hooked.pdf")\n', "python", run_dir=run_dir, repo_dir=repo_dir, staged=()
    )

    assert len(plan.records) == 1
    record = plan.records[0]
    assert record.origin == "generated"
    assert (run_dir / ".git" / "hooked.pdf").read_bytes() != b"SECRET"


def test_input_ps_is_generated_when_there_is_no_repository_match(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    plan = ef.provide_fixtures('open("input.ps")\n', "python", run_dir=run_dir, repo_dir=None, staged=())

    assert len(plan.records) == 1
    record = plan.records[0]
    assert record.literal == "input.ps"
    assert record.origin == "generated"
    assert record.source == "minimal-ps"
    assert (run_dir / "input.ps").read_bytes().startswith(b"%!PS-Adobe-3.0")


def test_input_xps_is_reported_missing(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    plan = ef.provide_fixtures('open("input.xps")\n', "python", run_dir=run_dir, repo_dir=None, staged=())

    assert plan.records == ()
    assert plan.missing == ("input.xps",)


def test_repo_dir_none_still_generates_a_literal_with_a_directory_part(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    plan = ef.provide_fixtures(
        'open("testfiles/notes.txt")\n', "python", run_dir=run_dir, repo_dir=None, staged=()
    )

    assert len(plan.records) == 1
    assert plan.records[0].origin == "generated"
    assert (run_dir / "testfiles" / "notes.txt").exists()


# --- fixture_unavailable -----------------------------------------------------------


def test_fixture_unavailable_true_for_the_python_filenotfounderror_marker() -> None:
    output = "Traceback (most recent call last):\nFileNotFoundError: [Errno 2] No such file or directory: 'input.xps'"
    assert ef.fixture_unavailable(output, ("input.xps",), "python") is True


def test_fixture_unavailable_true_for_the_python_no_such_file_text_without_the_class_name() -> None:
    output = "OSError: No such file or directory: 'input.xps'"
    assert ef.fixture_unavailable(output, ("input.xps",), "python") is True


@pytest.mark.parametrize(
    "marker",
    [
        "no such file",
        "cannot open",
        "can't open",
        "could not open",
        "failed to open",
        "not found",
        "does not exist",
    ],
)
def test_fixture_unavailable_true_for_every_cpp_marker(marker: str) -> None:
    output = f"error: {marker} sample.msg"
    assert ef.fixture_unavailable(output, ("sample.msg",), "cpp") is True


def test_fixture_unavailable_false_when_the_missing_name_is_absent() -> None:
    assert ef.fixture_unavailable("NameError: name 'foo' is not defined", ("input.xps",), "python") is False


def test_fixture_unavailable_false_when_nothing_is_missing() -> None:
    assert ef.fixture_unavailable("anything, even FileNotFoundError", (), "python") is False


# --- verify_python_example wiring ---------------------------------------------------


def test_python_block_opening_input_ps_sees_the_generated_file_and_is_verified(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_bounded(
        args: list[str], *, cwd: Path, timeout_seconds: float, env: object = None
    ) -> subprocess.CompletedProcess[str]:
        exists = (cwd / "input.ps").exists()
        return _completed(
            args,
            0 if exists else 1,
            stdout="ok" if exists else "",
            stderr="" if exists else "FileNotFoundError: [Errno 2] No such file or directory: 'input.ps'",
        )

    monkeypatch.setattr(ev, "_run_bounded", fake_bounded)
    candidate = _py_candidate('open("input.ps").read()\n')
    result = verify_python_example(candidate, venv_python=Path(sys.executable), workdir=tmp_path)

    assert result.verified is True
    assert result.status == ""
    assert result.candidate is candidate
    assert len(result.fixtures) == 1
    assert result.fixtures[0].literal == "input.ps"
    assert result.fixtures[0].origin == "generated"


def test_python_block_failing_with_filenotfounderror_for_input_xps_is_fixture_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_bounded(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return _completed(
            args,
            1,
            stderr="Traceback (most recent call last):\n"
            "FileNotFoundError: [Errno 2] No such file or directory: 'input.xps'",
        )

    monkeypatch.setattr(ev, "_run_bounded", fake_bounded)
    candidate = _py_candidate('open("input.xps").read()\n')
    result = verify_python_example(candidate, venv_python=Path(sys.executable), workdir=tmp_path)

    assert result.verified is False
    assert result.status == ev.FIXTURE_UNAVAILABLE
    assert "[foss-mcp] fixture unavailable: input.xps" in result.output


def test_python_nameerror_with_an_unmentioned_missing_name_keeps_status_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_bounded(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return _completed(args, 1, stderr="NameError: name 'foo' is not defined")

    monkeypatch.setattr(ev, "_run_bounded", fake_bounded)
    candidate = _py_candidate('open("input.xps").read()\n')
    result = verify_python_example(candidate, venv_python=Path(sys.executable), workdir=tmp_path)

    assert result.verified is False
    assert result.status == ""
    assert "[foss-mcp] fixture" not in result.output


def test_python_note_quick_start_gets_a_repository_record_with_the_staged_venv_layout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    code = _note_quick_start_block()
    workdir = tmp_path / "work"
    repo = workdir / "repo"
    (repo / "testfiles").mkdir(parents=True)
    (repo / "testfiles" / "SimpleTable.one").write_bytes(b"ONE")
    venv_python = workdir / "venv" / "bin" / "python"

    monkeypatch.setattr(
        ev, "_run_bounded", lambda args, **kwargs: _completed(args, 0, stdout="ok", stderr="")
    )

    result = verify_python_example(_py_candidate(code), venv_python=venv_python, workdir=workdir)

    assert result.verified is True
    record = next(r for r in result.fixtures if r.literal == "SimpleTable.one")
    assert record.origin == "repository"
    assert record.source == "testfiles/SimpleTable.one"


def test_python_block_naming_no_file_has_empty_fixtures_and_no_fixture_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        ev, "_run_bounded", lambda args, **kwargs: _completed(args, 0, stdout="2\n", stderr="")
    )
    candidate = _py_candidate("print(1 + 1)\n")
    result = verify_python_example(candidate, venv_python=Path(sys.executable), workdir=tmp_path)

    assert result.fixtures == ()
    assert "[foss-mcp] fixture" not in result.output


def test_python_real_run_prints_the_generated_notes_txt_and_is_verified(tmp_path: Path) -> None:
    code = 'with open("notes.txt") as f:\n    print(f.read())\n'
    candidate = _py_candidate(code)
    result = verify_python_example(candidate, venv_python=Path(sys.executable), workdir=tmp_path)

    assert result.verified is True
    assert "minimal fixture text line one" in result.output
    assert result.candidate is candidate


# --- verify_cpp_example_linux wiring --------------------------------------------------


def test_cpp_generated_hello_txt_is_present_when_the_executable_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    code = '#include <fstream>\nint main() {\n    std::ifstream in("hello.txt");\n    return in ? 0 : 1;\n}\n'
    candidate = _cpp_candidate(code)
    monkeypatch.setattr(ev, "_run", lambda args, *, cwd: _completed(args, 0))

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        cwd = kwargs.get("cwd")
        exists = (Path(cwd) / "hello.txt").exists()  # type: ignore[arg-type]
        return _completed(args, 0 if exists else 1)

    monkeypatch.setattr(ev.subprocess, "run", fake_run)
    _script_tools(monkeypatch, {"g++"})

    result = verify_cpp_example_linux(
        candidate, tmp_path / "cand", tmp_path / "lib" / "_build", tmp_path / "lib" / "include"
    )

    assert result.verified is True
    assert any(record.literal == "hello.txt" and record.origin == "generated" for record in result.fixtures)


def test_cpp_sample_msg_is_staged_from_the_clone_root_and_recorded_with_its_sha256(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    clone_root = tmp_path / "clone"
    (clone_root / ".git").mkdir(parents=True)
    (clone_root / "testdata").mkdir()
    sample_bytes = b"MSG-CONTENT"
    (clone_root / "testdata" / "sample.msg").write_bytes(sample_bytes)
    library_dir = clone_root / "lib" / "_build"
    include_dir = clone_root / "lib" / "include"

    code = 'int main() { const char *p = "sample.msg"; return p ? 0 : 1; }\n'
    candidate = _cpp_candidate(code)

    monkeypatch.setattr(ev, "_run", lambda args, *, cwd: _completed(args, 0))
    monkeypatch.setattr(ev.subprocess, "run", lambda args, **kwargs: _completed(args, 0, stdout="ok"))
    _script_tools(monkeypatch, {"g++"})

    result = verify_cpp_example_linux(candidate, tmp_path / "cand", library_dir, include_dir)

    assert result.verified is True
    assert len(result.fixtures) == 1
    record = result.fixtures[0]
    assert record.literal == "sample.msg"
    assert record.origin == "repository"
    assert record.source == "testdata/sample.msg"
    assert record.sha256 == hashlib.sha256(sample_bytes).hexdigest()


def test_cpp_run_failing_with_a_cannot_open_marker_gives_fixture_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    code = 'int main() { const char *p = "sample2.msg"; return p ? 0 : 1; }\n'
    candidate = _cpp_candidate(code)

    monkeypatch.setattr(ev, "_run", lambda args, *, cwd: _completed(args, 0))
    monkeypatch.setattr(
        ev.subprocess, "run", lambda args, **kwargs: _completed(args, 1, stderr="cannot open sample2.msg")
    )
    _script_tools(monkeypatch, {"g++"})

    result = verify_cpp_example_linux(
        candidate, tmp_path / "cand", tmp_path / "lib" / "_build", tmp_path / "lib" / "include"
    )

    assert result.verified is False
    assert result.status == ev.FIXTURE_UNAVAILABLE
    assert "[foss-mcp] fixture unavailable: sample2.msg" in result.output


def test_cpp_run_false_and_a_compile_failure_supply_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    candidate = _cpp_candidate("int main() { return 0; }\n")
    _script_tools(monkeypatch, {"g++"})

    monkeypatch.setattr(ev, "_run", lambda args, *, cwd: _completed(args, 0))
    no_run_result = verify_cpp_example_linux(
        candidate, tmp_path / "cand1", tmp_path / "lib", tmp_path / "inc", run=False
    )
    assert no_run_result.verified is True
    assert no_run_result.fixtures == ()
    assert no_run_result.status == ""

    monkeypatch.setattr(ev, "_run", lambda args, *, cwd: _completed(args, 1, stderr="boom"))
    compile_fail_result = verify_cpp_example_linux(
        candidate, tmp_path / "cand2", tmp_path / "lib", tmp_path / "inc"
    )
    assert compile_fail_result.verified is False
    assert compile_fail_result.fixtures == ()


# --- equality and wiring --------------------------------------------------------------


def test_verification_result_equality_ignores_fixtures() -> None:
    candidate = _py_candidate("print(1)\n")
    record = ef.FixtureRecord(
        literal="x.pdf", destination="x.pdf", origin="generated", source="minimal-pdf", sha256="a" * 64
    )
    assert VerificationResult(candidate, True, "x") == VerificationResult(
        candidate, True, "x", fixtures=(record,)
    )


def test_provide_fixtures_is_wired_into_both_verifiers_and_the_dispatch_entries_are_real() -> None:
    assert "provide_fixtures(" in inspect.getsource(verify_python_example)
    assert "provide_fixtures(" in inspect.getsource(verify_cpp_example_linux)
    assert build_chunks._PLATFORM_DISPATCH["python"]["verify"] is verify_python_example
    assert build_chunks._PLATFORM_DISPATCH["cpp"]["verify"] is verify_cpp_example_linux
