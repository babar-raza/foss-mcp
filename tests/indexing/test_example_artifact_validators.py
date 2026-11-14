"""G3/TC-808 (REQ-G3-014): a produced artefact must be a valid file, not merely a file.

A run that exits 0 and leaves a 0-byte PDF, a truncated PNG or a corrupt zip used to count as "produced
output". ``example_artifacts`` judges the container formats the pilots write (OOXML, zip, PDF, PNG/JPEG, EML,
MSG) by their own bytes, and ``example_verifier`` calls it: such a file counts for nothing in
``artefact_payload_bytes`` and makes the run ``failed`` in ``execute_in_sandbox``.

Offline and deterministic: no process is started (a fake runner writes the files). The valid samples are the
TC-701 fixture generators (``example_fixtures``) plus a few bytes written here from the formats'
specifications, so the validators are not graded against their own writer.

Negative control: reverse-applying the production part of this card's commit removes the module and its call
sites, so every test below fails.
"""

from __future__ import annotations

import io
import re
import struct
import zipfile
import zlib
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest

import foss_mcp.indexing.example_verifier as ev
from foss_mcp.indexing import example_artifacts as ea
from foss_mcp.indexing import example_fixtures as ef
from foss_mcp.indexing.example_verifier import (
    RUNTIME_FAILED,
    RUNTIME_NO_OUTPUT,
    RUNTIME_RAN_OK,
    RunOutcome,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFIER_SOURCE = REPO_ROOT / "src" / "foss_mcp" / "indexing" / "example_verifier.py"
ARTIFACTS_SOURCE = REPO_ROOT / "src" / "foss_mcp" / "indexing" / "example_artifacts.py"

OLE_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")


def _fixture(extension: str) -> bytes:
    produced = ef.generated_fixture(extension)
    assert produced is not None
    return produced[1]


def _png_chunk(tag: bytes, body: bytes, *, crc: int | None = None) -> bytes:
    checksum = zlib.crc32(tag + body) & 0xFFFFFFFF if crc is None else crc
    return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", checksum)


def _png(*, idat: bytes | None = None) -> bytes:
    ihdr = struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)
    raw = (b"\x00" + bytes((255, 0, 0, 0, 255, 0))) * 2
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(raw) if idat is None else idat)
        + _png_chunk(b"IEND", b"")
    )


JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    + b"\xff\xdb\x00\x04\x00\x00"
    + (b"\xff\xd9")
)
PDF = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
EML = b"From: a@example.com\r\nTo: b@example.com\r\nSubject: hi\r\n\r\nbody\r\n"
MSG = OLE_MAGIC + bytes(1024 - len(OLE_MAGIC))


def _zip(parts: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    return buffer.getvalue()


# -- valid samples are accepted -------------------------------------------------------------------------------


@pytest.mark.parametrize("extension", ["docx", "xlsx", "pptx"])
def test_the_tc701_ooxml_generators_are_valid(extension: str) -> None:
    assert ea.artifact_problem(f"out.{extension}", _fixture(extension)) is None


def test_the_tc701_zip_png_pdf_and_eml_generators_are_valid() -> None:
    for extension in ("zip", "png", "pdf", "eml"):
        assert ea.artifact_problem(f"out.{extension}", _fixture(extension)) is None, extension


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("report.pdf", PDF),
        ("report.PDF", PDF),
        ("pic.png", _png()),
        ("pic.jpg", JPEG),
        ("pic.jpeg", JPEG),
        ("pic.jpg", JPEG + b"\x00\x00\x00"),
        ("mail.eml", EML),
        ("mail.eml", EML.replace(b"\r\n", b"\n")),
        ("mail.msg", MSG),
        ("book.xlsm", _zip({"[Content_Types].xml": b"<Types/>", "xl/workbook.xml": b"<w/>"})),
        ("sub/dir/deck.pptx", _fixture("pptx")),
        ("a.zip", _zip({"readme.txt": b"hello"})),
    ],
)
def test_valid_samples_are_accepted(name: str, data: bytes) -> None:
    assert ea.artifact_problem(name, data) is None
    assert ea.artifact_is_valid(name, data)


def test_a_password_protected_ooxml_package_is_an_ole_container_and_is_accepted() -> None:
    assert ea.artifact_problem("secret.xlsx", MSG) is None
    assert ea.artifact_problem("secret.docx", MSG) is None


def test_a_pdf_with_trailing_whitespace_after_eof_is_accepted() -> None:
    assert ea.artifact_problem("a.pdf", PDF + b"\r\n\r\n") is None


def test_an_eml_with_a_folded_header_is_accepted() -> None:
    folded = b"Subject: a very\r\n long subject\r\nFrom: a@example.com\r\n\r\nbody"
    assert ea.artifact_problem("m.eml", folded) is None


# -- invalid files are rejected -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["a.docx", "a.xlsx", "a.pptx", "a.zip", "a.pdf", "a.png", "a.jpg", "a.jpeg", "a.eml", "a.msg"]
)
def test_a_zero_byte_file_of_every_judged_kind_is_invalid(name: str) -> None:
    assert ea.artifact_problem(name, b"") == "empty file"


def test_garbage_bytes_are_not_a_zip_container() -> None:
    for name in ("a.docx", "a.xlsx", "a.pptx", "a.zip"):
        assert "zip" in (ea.artifact_problem(name, b"this is not a zip at all") or ""), name


def test_a_truncated_ooxml_package_is_a_corrupt_zip() -> None:
    whole = _fixture("docx")
    assert ea.artifact_problem("a.docx", whole[: len(whole) // 2]) is not None


def test_a_zip_with_a_flipped_payload_byte_is_a_corrupt_zip() -> None:
    whole = bytearray(_zip({"[Content_Types].xml": b"<Types/>" * 50, "word/document.xml": b"x" * 400}))
    stored = zipfile.ZipFile(io.BytesIO(bytes(whole))).infolist()[1]
    whole[stored.header_offset + 30 + len(stored.filename) + 2] ^= 0xFF
    assert ea.artifact_problem("a.docx", bytes(whole)) is not None


def test_an_ooxml_zip_without_content_types_is_invalid_but_a_plain_zip_is_not() -> None:
    plain = _zip({"word/document.xml": b"<d/>"})
    assert ea.artifact_problem("a.docx", plain) == "zip archive has no [Content_Types].xml part"
    assert ea.artifact_problem("a.xlsx", plain) == "zip archive has no [Content_Types].xml part"
    assert ea.artifact_problem("a.zip", plain) is None


def test_a_pdf_without_its_header_or_trailer_is_invalid() -> None:
    assert "header" in (ea.artifact_problem("a.pdf", b"hello world %%EOF") or "")
    assert "%%EOF" in (ea.artifact_problem("a.pdf", PDF[: len(PDF) - 8]) or "")
    assert "%%EOF" in (
        ea.artifact_problem("a.pdf", b"%PDF-1.4\n" + b"x" * 5000 + b"\n%%EOF\n" + b"y" * 3000) or ""
    )


def test_a_truncated_png_is_invalid_at_every_cut_point() -> None:
    whole = _png()
    assert ea.artifact_problem("a.png", whole) is None
    for cut in (8, 9, 16, 20, 33, len(whole) - 13, len(whole) - 1):
        assert ea.artifact_problem("a.png", whole[:cut]) is not None, cut


def test_a_png_with_a_bad_crc_or_a_wrong_first_chunk_is_invalid() -> None:
    body = struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)
    bad_crc = b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", body, crc=0) + _png_chunk(b"IEND", b"")
    assert "CRC" in (ea.artifact_problem("a.png", bad_crc) or "")
    wrong_first = b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IDAT", b"x") + _png_chunk(b"IEND", b"")
    assert "IHDR" in (ea.artifact_problem("a.png", wrong_first) or "")


def test_a_png_without_its_signature_is_invalid() -> None:
    assert ea.artifact_problem("a.png", _png()[1:]) == "missing the PNG signature"
    assert ea.artifact_problem("a.png", b"GIF89a....") == "missing the PNG signature"


def test_a_jpeg_without_its_markers_is_invalid() -> None:
    assert ea.artifact_problem("a.jpg", JPEG[:-2]) is not None
    assert ea.artifact_problem("a.jpg", b"\x89PNG....") is not None
    assert ea.artifact_problem("a.jpeg", JPEG[:20]) is not None


def test_an_eml_without_headers_is_invalid() -> None:
    assert ea.artifact_problem("a.eml", b"just a body, no headers\r\n") is not None
    assert ea.artifact_problem("a.eml", b"X-Custom: 1\r\n\r\nbody") is not None
    assert ea.artifact_problem("a.eml", b"\r\n\r\nFrom: a@example.com\r\n") is not None
    assert ea.artifact_problem("a.eml", b"\x00\x01\x02\x03") is not None


def test_a_msg_without_the_ole_magic_or_a_header_is_invalid() -> None:
    assert ea.artifact_problem("a.msg", b"From: a@example.com\r\n\r\nbody") is not None
    assert ea.artifact_problem("a.msg", MSG[:100]) is not None
    assert ea.artifact_problem("a.msg", MSG[:8]) is not None


def test_a_kind_this_module_does_not_judge_is_never_rejected() -> None:
    for name in ("a.txt", "a.bin", "a.json", "noextension", "a.stl", "a.obj", "a.gltf", "dir.pdf/file", "x."):
        assert ea.artifact_problem(name, b"") is None, name
        assert ea.artifact_problem(name, b"anything") is None, name


def test_the_judged_extensions_are_the_card_formats() -> None:
    assert {
        "docx",
        "xlsx",
        "pptx",
        "zip",
        "pdf",
        "png",
        "jpg",
        "jpeg",
        "eml",
        "msg",
    } <= ea.ARTIFACT_EXTENSIONS
    assert not {"stl", "obj", "gltf", "glb", "txt"} & ea.ARTIFACT_EXTENSIONS


def test_artifact_problems_names_each_invalid_file_and_skips_valid_ones() -> None:
    lines = ea.artifact_problems(
        {"ok.pdf": PDF, "bad.pdf": b"", "x.txt": b"", "bad.png": b"nope", "ok.png": _png()}
    )
    assert [line.split(":")[0] for line in lines] == ["bad.pdf", "bad.png"]
    assert lines[0] == "bad.pdf: empty file"


# -- artefact_payload_bytes ---------------------------------------------------------------------------------


def test_artefact_payload_bytes_counts_a_valid_artefact_and_nothing_for_an_invalid_one() -> None:
    assert ev.artefact_payload_bytes("o.pdf", PDF) == len(PDF)
    assert ev.artefact_payload_bytes("o.pdf", b"") == 0
    assert ev.artefact_payload_bytes("o.pdf", b"%PDF-1.4 truncated") == 0
    png = _png()
    assert ev.artefact_payload_bytes("o.png", png) == len(png)
    assert ev.artefact_payload_bytes("o.png", png[:-5]) == 0
    docx = _fixture("docx")
    assert ev.artefact_payload_bytes("o.docx", docx) == len(docx)
    assert ev.artefact_payload_bytes("o.docx", b"PK\x03\x04garbage") == 0
    assert ev.artefact_payload_bytes("o.eml", EML) == len(EML)
    assert ev.artefact_payload_bytes("o.msg", MSG) == len(MSG)
    assert ev.artefact_payload_bytes("o.msg", b"") == 0


def test_the_existing_formats_and_unknown_kinds_count_as_before() -> None:
    assert ev.artefact_payload_bytes("m.gltf", b"{}") == 0
    assert ev.artefact_payload_bytes("m.gltf", b'{"asset":{"version":"2.0"}}') == 27
    assert ev.artefact_payload_bytes("m.obj", b"# only a comment\n") == 0
    assert ev.artefact_payload_bytes("m.txt", b"abc") == 3
    assert ev.artefact_payload_bytes("m.bin", b"") == 0
    assert ev.artefact_payload_bytes("m.csv", b"a,b\n1,2\n") == 8


# -- the sandboxed run --------------------------------------------------------------------------------------


def _runner(writes: dict[str, bytes], *, exit_code: int = 0, stdout: str = "", stderr: str = ""):
    def runner(argv: Sequence[str], cwd: Path, timeout_seconds: float) -> RunOutcome:
        for name, data in writes.items():
            target = cwd / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return RunOutcome(exit_code=exit_code, stdout=stdout, stderr=stderr)

    return runner


def _run(writes: dict[str, bytes], *, platform: str = "java", **kwargs: object) -> ev.RuntimeRecord:
    record = ev.execute_in_sandbox(["x"], runner=_runner(writes, **kwargs), platform=platform)  # type: ignore[arg-type]
    return replace(record, output_optional=platform in ev.OUTPUT_OPTIONAL_PLATFORMS)


@pytest.mark.parametrize("platform", ["java", "dotnet", "go", "rust", "typescript"])
@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("out.pdf", b""),
        ("out.pdf", b"%PDF-1.4 cut off"),
        ("outputfiles/out.png", _png()[:-12]),
        ("out.docx", b"not a zip"),
        ("out.xlsx", b""),
        ("out.msg", b"not ole"),
    ],
)
def test_an_exit_zero_run_that_leaves_an_invalid_artefact_is_failed(
    platform: str, name: str, data: bytes
) -> None:
    record = _run({name: data}, platform=platform)
    assert record.status == RUNTIME_FAILED
    assert not ev.counts_as_verified(record.status)
    assert record.exit_code == 0
    assert record.error.startswith("invalid artefact: ")
    assert name in record.error


@pytest.mark.parametrize("platform", ["java", "dotnet", "go", "rust", "typescript"])
def test_a_valid_artefact_is_still_a_verified_run(platform: str) -> None:
    record = _run({"out.pdf": PDF, "pic.png": _png(), "book.xlsx": _fixture("xlsx")}, platform=platform)
    assert record.status == RUNTIME_RAN_OK
    assert record.error == ""
    assert record.produced_output == len(PDF) + len(_png()) + len(_fixture("xlsx"))
    assert ev.counts_as_verified(record.status)


def test_one_invalid_artefact_among_valid_ones_fails_the_run_and_is_named() -> None:
    record = _run({"good.pdf": PDF, "bad.png": b"\x89PNG\r\n\x1a\n", "good.eml": EML})
    assert record.status == RUNTIME_FAILED
    assert "bad.png" in record.error and "good.pdf" not in record.error and "good.eml" not in record.error
    assert record.produced_output == len(PDF) + len(EML)


def test_the_error_is_bounded() -> None:
    record = _run({f"file{index}.pdf": b"" for index in range(60)})
    assert record.status == RUNTIME_FAILED
    assert len(record.error) <= 300


def test_a_failing_exit_code_keeps_its_own_error_text() -> None:
    record = _run({"out.pdf": b""}, exit_code=1, stderr="Error: boom\n")
    assert record.status == RUNTIME_FAILED
    assert "boom" in record.error and "invalid artefact" not in record.error


def test_a_run_that_writes_no_artefact_is_classified_exactly_as_before() -> None:
    printed = _run({}, stdout="hello\n")
    assert printed.status == RUNTIME_RAN_OK and printed.error == ""
    silent_java = _run({})
    assert silent_java.status == RUNTIME_NO_OUTPUT
    silent_go = _run({}, platform="go")
    assert silent_go.status == RUNTIME_RAN_OK
    other = _run({"notes.txt": b"abc", "data.csv": b""})
    assert other.status == RUNTIME_RAN_OK and other.error == ""


def test_the_existing_empty_3d_artefact_rule_still_reads_no_output_not_failed() -> None:
    record = _run({"m.stl": b"solid empty\nendsolid empty\n"})
    assert record.status == RUNTIME_NO_OUTPUT


def test_the_failed_verdict_survives_the_verification_cache_round_trip() -> None:
    record = _run({"out.pdf": b""}, platform="go")
    entry = {**record.as_cache_fields(), "platform": "go"}
    restored = ev.runtime_record_from_entry(entry)
    assert restored.status == RUNTIME_FAILED
    assert not ev.entry_counts_as_verified(entry)


def test_a_zero_exit_record_without_an_error_is_unchanged() -> None:
    assert ev.RuntimeRecord(compiled=True, ran=True, exit_code=0, produced_output=5).status == RUNTIME_RAN_OK
    assert (
        ev.RuntimeRecord(compiled=True, ran=True, exit_code=0, produced_output=0).status == RUNTIME_NO_OUTPUT
    )


# -- the wiring proof ---------------------------------------------------------------------------------------


def _function_source(source: str, name: str) -> str:
    match = re.search(rf"^def {name}\(.*?(?=^def |\Z)", source, re.DOTALL | re.MULTILINE)
    assert match is not None, name
    return match.group(0)


def test_artefact_payload_bytes_calls_the_new_module() -> None:
    source = VERIFIER_SOURCE.read_text(encoding="utf-8")
    assert re.search(r"^from foss_mcp\.indexing\.example_artifacts import .*artifact_is_valid", source, re.M)
    assert "artifact_is_valid(name, data)" in _function_source(source, "artefact_payload_bytes")


def test_execute_in_sandbox_records_the_invalid_artefacts() -> None:
    source = VERIFIER_SOURCE.read_text(encoding="utf-8")
    body = _function_source(source, "execute_in_sandbox")
    assert "artifact_problems(" in body
    assert "invalid artefact:" in body


def test_the_new_module_is_pure_and_imports_nothing_from_the_verifier() -> None:
    text = ARTIFACTS_SOURCE.read_text(encoding="utf-8")
    imports = re.findall(r"^(?:from|import) (\S+)", text, re.MULTILINE)
    assert not [name for name in imports if name.startswith("foss_mcp") or name.startswith("infra")]
