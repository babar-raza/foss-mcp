"""Deterministic input-file supply for README examples (TC-459, REQ-G3-007).

A Python or C++ README example that opens an input file fails with the same shape of
error whether the README is broken or the file simply was never supplied. This module
finds the file names a candidate's code actually names, supplies each one - from the
pinned clone when a real sample exists, from a small deterministic fixture generated
here when the format allows it, or leaves it alone when the candidate already wrote it
itself (an "output" literal) or when it is already present (an earlier block's own
output, or a file ``example_verifier``'s own staging already copied in) - and records
every file it supplied with its origin and sha256. It never imports ``example_verifier``
and never changes a verdict: a supplied fixture can only change what a run finds when it
actually opens the file, never the exit code comparison that decides ``verified``.
"""

from __future__ import annotations

import hashlib
import io
import re
import shutil
import struct
import zipfile
import zlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

FIXTURE_UNAVAILABLE = "fixture_unavailable"

DOCUMENT_EXTENSIONS: frozenset[str] = frozenset(
    {
        "one",
        "pdf",
        "docx",
        "doc",
        "rtf",
        "odt",
        "xlsx",
        "xls",
        "ods",
        "csv",
        "pptx",
        "ppt",
        "odp",
        "txt",
        "md",
        "json",
        "html",
        "htm",
        "xml",
        "eml",
        "msg",
        "mbox",
        "pst",
        "ics",
        "vcf",
        "obj",
        "stl",
        "fbx",
        "gltf",
        "glb",
        "dae",
        "3ds",
        "ply",
        "ps",
        "eps",
        "xps",
        "png",
        "jpg",
        "jpeg",
        "bmp",
        "gif",
        "tif",
        "tiff",
        "svg",
        "emf",
        "wmf",
        "tex",
        "epub",
        "zip",
    }
)

# --- string-literal extraction ------------------------------------------------

# Python: single- or double-quoted, an optional r/b/u prefix, never an f-string (the
# lookbehind rejects a match starting right after a letter, which also rejects any
# multi-character prefix such as ``rb"..."`` - unneeded for a README code block).
_PY_STRING_RE = re.compile(
    r'(?<![A-Za-z0-9_])[rRbBuU]?(?:"(?P<pd>(?:\\.|[^"\\])*)"|\'(?P<ps>(?:\\.|[^\'\\])*)\')'
)

# C++: double-quoted only. A raw string (``R"(...)"``) is rejected the same way: the
# lookbehind refuses a match starting right after the ``R``.
_CPP_STRING_RE = re.compile(r'(?<![A-Za-z0-9_])"(?P<cd>(?:\\.|[^"\\])*)"')

_DIRECTORY_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")
_BASENAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_. \-]*$")
_DRIVE_LETTER_RE = re.compile(r"^[A-Za-z]:")

_OUTPUT_BASENAME_PREFIXES = ("output", "out.", "out_", "out-", "result", "converted")
_OUTPUT_CONTEXT_RE = re.compile(r"(?i)save|write|export|to_file|dump|ofstream")
_OUTPUT_MODE_RE = re.compile(r'["\'](w|wb|w\+)["\']')


def _is_relative_document_literal(text: str) -> bool:
    """True when ``text`` is a relative file name whose extension is a document one.

    Never absolute, never a ``..`` segment, never a backslash, ``~`` or ``://``.
    """
    if not text or text.startswith("/") or "\\" in text or "~" in text or "://" in text:
        return False
    if _DRIVE_LETTER_RE.match(text):
        return False
    parts = text.split("/")
    basename = parts[-1]
    for segment in parts[:-1]:
        if not segment or segment == ".." or _DIRECTORY_SEGMENT_RE.match(segment) is None:
            return False
    if _BASENAME_RE.match(basename) is None or "." not in basename:
        return False
    extension = basename.rsplit(".", 1)[-1].lower()
    return extension in DOCUMENT_EXTENSIONS


def _is_output_literal(literal: str, code: str) -> bool:
    """True when ``literal`` is written by ``code`` rather than read from it.

    A literal whose basename starts with an output-ish prefix is always an output.
    Otherwise it is an output only when EVERY line of ``code`` that holds it looks like
    a write (a save/write/export/to_file/dump/ofstream call, or a quoted w/wb/w+ mode).
    """
    basename = literal.rsplit("/", 1)[-1].lower()
    if basename.startswith(_OUTPUT_BASENAME_PREFIXES):
        return True
    holding_lines = [line for line in code.splitlines() if literal in line]
    if not holding_lines:
        return False
    return all(_OUTPUT_CONTEXT_RE.search(line) or _OUTPUT_MODE_RE.search(line) for line in holding_lines)


def input_literals(code: str, platform: str) -> tuple[str, ...]:
    """The relative, document-extensioned string literals of ``code`` that are inputs.

    In first-appearance order, de-duplicated. An output literal (see
    ``_is_output_literal``) is left out.
    """
    pattern = _PY_STRING_RE if platform == "python" else _CPP_STRING_RE
    seen: set[str] = set()
    result: list[str] = []
    for match in pattern.finditer(code):
        content = next((value for value in match.groupdict().values() if value is not None), None)
        if content is None or "{" in content:
            continue
        if not _is_relative_document_literal(content):
            continue
        if _is_output_literal(content, code):
            continue
        if content in seen:
            continue
        seen.add(content)
        result.append(content)
    return tuple(result)


# --- generated fixtures --------------------------------------------------------

_TXT_BYTES = b"minimal fixture text line one\nminimal fixture text line two\n"
_MD_BYTES = b"# Minimal Fixture\n\nThis is a minimal fixture paragraph.\n"
_JSON_BYTES = b'{"name": "minimal-fixture", "value": 1}'
_CSV_BYTES = b"name,value\nminimal-fixture,1\n"
_HTML_BYTES = (
    b"<!DOCTYPE html>\n<html><head><title>Minimal Fixture</title></head>"
    b"<body><p>This is a minimal fixture paragraph.</p></body></html>\n"
)
_EML_BYTES = (
    b"From: sender@example.com\r\n"
    b"To: recipient@example.com\r\n"
    b"Subject: Minimal Fixture\r\n"
    b"Date: Mon, 01 Jan 2024 00:00:00 +0000\r\n"
    b"Message-ID: <minimal-fixture@example.com>\r\n"
    b"MIME-Version: 1.0\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"\r\n"
    b"This is a minimal fixture message body.\r\n"
)
_OBJ_BYTES = b"v 0.0 0.0 0.0\nv 1.0 0.0 0.0\nv 0.0 1.0 0.0\nf 1 2 3\n"
_STL_BYTES = (
    b"solid minimal\n"
    b"facet normal 0 0 1\n"
    b"outer loop\n"
    b"vertex 0 0 0\n"
    b"vertex 1 0 0\n"
    b"vertex 0 1 0\n"
    b"endloop\n"
    b"endfacet\n"
    b"endsolid minimal\n"
)
_PS_BYTES = (
    b"%!PS-Adobe-3.0\n"
    b"/Helvetica findfont 12 scalefont setfont\n"
    b"72 720 moveto\n"
    b"(minimal fixture) show\n"
    b"showpage\n"
)
_EPS_BYTES = (
    b"%!PS-Adobe-3.0 EPSF-3.0\n"
    b"%%BoundingBox: 0 0 100 100\n"
    b"/Helvetica findfont 12 scalefont setfont\n"
    b"10 10 moveto\n"
    b"(minimal fixture) show\n"
)


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def _generate_png() -> bytes:
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    raw = b"\x00" + bytes((0, 0, 0, 255))
    idat = zlib.compress(raw, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", idat)
        + _png_chunk(b"IEND", b"")
    )


def _generate_pdf() -> bytes:
    objects = (
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\nendobj\n",
    )
    body = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for obj in objects:
        offsets.append(len(body))
        body += obj
    xref_offset = len(body)
    xref = bytearray(b"xref\n0 4\n0000000000 65535 f \n")
    for offset in offsets:
        xref += f"{offset:010d} 00000 n \n".encode("ascii")
    trailer = (
        b"trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n" + str(xref_offset).encode("ascii") + b"\n%%EOF\n"
    )
    return bytes(body + xref + trailer)


# --- zip-container fixtures (zip/docx/xlsx/pptx) ---------------------------------
#
# docx/xlsx/pptx are OOXML: a zip archive of XML parts. Each generator below builds
# the real minimum set of parts its format needs ([Content_Types].xml, the package's
# root relationship, the format's own main part, and - for xlsx/pptx only, since a
# workbook/presentation's real content lives in a separate worksheet/slide part - that
# part's own relationship and the part itself), so the result is genuinely openable,
# not merely a zip that happens to carry the right extension. ``msg`` (compound
# binary/OLE, not a zip container) is deliberately left unsupported - a documented
# follow-up, not an oversight.

_ZIP_FIXTURE_DATE_TIME = (1980, 1, 1, 0, 0, 0)


def _zip_entry_size(name: str, data: bytes, compress_type: int) -> int:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        info = zipfile.ZipInfo(name, date_time=_ZIP_FIXTURE_DATE_TIME)
        info.compress_type = compress_type
        archive.writestr(info, data, compresslevel=9)
    return len(buffer.getvalue())


def _build_zip_fixture(parts: Sequence[tuple[str, bytes]]) -> bytes:
    """A deterministic zip archive containing exactly ``parts``, in order.

    Every entry gets a fixed ``date_time`` (so the bytes never depend on the wall
    clock a generator happens to run at) and whichever of stored/deflated compression
    is smaller for that entry's own content, chosen per entry to keep the archive as
    small as it can be.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in parts:
            stored = _zip_entry_size(name, data, zipfile.ZIP_STORED)
            deflated = _zip_entry_size(name, data, zipfile.ZIP_DEFLATED)
            method = zipfile.ZIP_DEFLATED if deflated < stored else zipfile.ZIP_STORED
            info = zipfile.ZipInfo(name, date_time=_ZIP_FIXTURE_DATE_TIME)
            info.compress_type = method
            info.external_attr = 0o600 << 16
            archive.writestr(info, data, compresslevel=9)
    return buffer.getvalue()


_OPC_CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_OPC_RELATIONSHIPS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_OPC_OFFICE_DOCUMENT_REL = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
)
_XML_DECLARATION = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'


def _opc_relationships_xml(target: str, rel_type: str) -> bytes:
    return _XML_DECLARATION + (
        f'<Relationships xmlns="{_OPC_RELATIONSHIPS_NS}">'
        f'<Relationship Id="rId1" Type="{rel_type}" Target="{target}"/></Relationships>'
    ).encode("ascii")


def _generate_zip() -> bytes:
    """A real, non-empty zip archive with one small member."""
    return _build_zip_fixture([("minimal.txt", b"minimal fixture\n")])


def _generate_docx() -> bytes:
    """A minimal, real, openable ``.docx``: Word's own OOXML wordprocessing package.

    Three parts, the real minimum a wordprocessing package needs: the content-types
    declaration, the package's root relationship (pointing at the document part), and
    ``word/document.xml`` itself - one paragraph of real text, no styles/theme parts,
    which a wordprocessing document does not require to be genuinely openable.
    """
    content_types = _XML_DECLARATION + (
        f'<Types xmlns="{_OPC_CONTENT_TYPES_NS}">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    ).encode("ascii")
    document_xml = _XML_DECLARATION + (
        b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        b"<w:body><w:p><w:r><w:t>minimal fixture</w:t></w:r></w:p></w:body></w:document>"
    )
    return _build_zip_fixture(
        [
            ("[Content_Types].xml", content_types),
            ("_rels/.rels", _opc_relationships_xml("word/document.xml", _OPC_OFFICE_DOCUMENT_REL)),
            ("word/document.xml", document_xml),
        ]
    )


def _generate_xlsx() -> bytes:
    """A minimal, real, openable ``.xlsx``: one workbook with one empty worksheet.

    Five parts, the real minimum a spreadsheet package needs: the content-types
    declaration, the package's root relationship, ``xl/workbook.xml`` itself, that
    workbook's own relationship to its one worksheet (a ``<sheet>`` entry's ``r:id``
    is a required reference, not decoration - omitting it would make the sheet
    unreachable), and the worksheet part the workbook actually names.
    """
    content_types = _XML_DECLARATION + (
        f'<Types xmlns="{_OPC_CONTENT_TYPES_NS}">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    ).encode("ascii")
    workbook_xml = _XML_DECLARATION + (
        b'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        b'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        b'<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    workbook_rels = _opc_relationships_xml(
        "worksheets/sheet1.xml",
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet",
    )
    sheet1_xml = _XML_DECLARATION + (
        b'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        b"<sheetData/></worksheet>"
    )
    return _build_zip_fixture(
        [
            ("[Content_Types].xml", content_types),
            ("_rels/.rels", _opc_relationships_xml("xl/workbook.xml", _OPC_OFFICE_DOCUMENT_REL)),
            ("xl/workbook.xml", workbook_xml),
            ("xl/_rels/workbook.xml.rels", workbook_rels),
            ("xl/worksheets/sheet1.xml", sheet1_xml),
        ]
    )


def _generate_pptx() -> bytes:
    """A minimal, real, openable ``.pptx``: one presentation with one empty slide.

    Five parts, the real minimum a presentation package needs: the content-types
    declaration, the package's root relationship, ``ppt/presentation.xml`` itself
    (naming its one slide via ``<p:sldId>`` and declaring a real slide size), that
    presentation's own relationship to its one slide, and the slide part itself (an
    empty-but-real shape tree, the minimum ``<p:sld>`` body the schema allows).
    """
    content_types = _XML_DECLARATION + (
        f'<Types xmlns="{_OPC_CONTENT_TYPES_NS}">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Override PartName="/ppt/presentation.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
        '<Override PartName="/ppt/slides/slide1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
        "</Types>"
    ).encode("ascii")
    presentation_xml = _XML_DECLARATION + (
        b'<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        b'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        b'<p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst>'
        b'<p:sldSz cx="9144000" cy="6858000"/></p:presentation>'
    )
    presentation_rels = _opc_relationships_xml(
        "slides/slide1.xml", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"
    )
    slide1_xml = _XML_DECLARATION + (
        b'<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
        b'<p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/>'
        b"</p:nvGrpSpPr><p:grpSpPr/></p:spTree></p:cSld></p:sld>"
    )
    return _build_zip_fixture(
        [
            ("[Content_Types].xml", content_types),
            ("_rels/.rels", _opc_relationships_xml("ppt/presentation.xml", _OPC_OFFICE_DOCUMENT_REL)),
            ("ppt/presentation.xml", presentation_xml),
            ("ppt/_rels/presentation.xml.rels", presentation_rels),
            ("ppt/slides/slide1.xml", slide1_xml),
        ]
    )


_GENERATORS: dict[str, Callable[[], bytes]] = {
    "txt": lambda: _TXT_BYTES,
    "md": lambda: _MD_BYTES,
    "json": lambda: _JSON_BYTES,
    "csv": lambda: _CSV_BYTES,
    "html": lambda: _HTML_BYTES,
    "htm": lambda: _HTML_BYTES,
    "eml": lambda: _EML_BYTES,
    "obj": lambda: _OBJ_BYTES,
    "stl": lambda: _STL_BYTES,
    "ps": lambda: _PS_BYTES,
    "eps": lambda: _EPS_BYTES,
    "png": _generate_png,
    "pdf": _generate_pdf,
    "zip": _generate_zip,
    "docx": _generate_docx,
    "xlsx": _generate_xlsx,
    "pptx": _generate_pptx,
}


def generated_fixture(extension: str) -> tuple[str, bytes] | None:
    """A deterministic, at-most-1024-byte ``(name, bytes)`` fixture for ``extension``.

    Returns ``None`` when no generator for that extension exists.
    """
    generator = _GENERATORS.get(extension.lower())
    if generator is None:
        return None
    ext = extension.lower()
    return f"minimal-{ext}", generator()


# --- records and the plan -------------------------------------------------------


@dataclass(frozen=True)
class FixtureRecord:
    """One file supplied for a candidate's run: what it was, where it went, and how."""

    literal: str
    destination: str
    origin: str
    source: str
    sha256: str

    def as_dict(self) -> dict[str, str]:
        return {
            "literal": self.literal,
            "destination": self.destination,
            "origin": self.origin,
            "source": self.source,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class FixturePlan:
    """What ``provide_fixtures`` did: every file it supplied, and every one it could not."""

    records: tuple[FixtureRecord, ...]
    missing: tuple[str, ...]


_REFUSED_REPOSITORY_EXTENSIONS = frozenset({"py", "pyc", "pyd", "pyi", "pth", "so", "dll", "exe"})
_MAX_REPOSITORY_FIXTURE_BYTES = 50 * 1024 * 1024


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _record(literal: str, destination: Path, origin: str, source: str) -> FixtureRecord:
    return FixtureRecord(
        literal=literal, destination=literal, origin=origin, source=source, sha256=_hash_file(destination)
    )


def provide_fixtures(
    code: str,
    platform: str,
    *,
    run_dir: Path,
    repo_dir: Path | None,
    staged: Sequence[str] = (),
) -> FixturePlan:
    """Supply every input file ``code`` names, under ``run_dir``, and report what happened.

    For each input literal (``input_literals(code, platform)``, in order): a file already at
    ``run_dir/<literal>`` is left untouched and recorded (``"repository"`` when a staged path's
    basename matches it, else ``"present"``); otherwise, when the literal has a directory part
    and ``repo_dir/<literal>`` is a safe, real, small-enough file, it is copied there
    (``"repository"``); otherwise a known extension is generated in code (``"generated"``);
    otherwise the literal is reported missing. ``repo_dir`` of ``None`` skips the repository-path
    copy step entirely.
    """
    staged_by_basename: dict[str, str] = {}
    for path in staged:
        staged_by_basename.setdefault(path.rsplit("/", 1)[-1], path)

    records: list[FixtureRecord] = []
    missing: list[str] = []

    for literal in input_literals(code, platform):
        destination = run_dir / literal
        if destination.exists():
            basename = literal.rsplit("/", 1)[-1]
            staged_source = staged_by_basename.get(basename)
            if staged_source is not None:
                records.append(_record(literal, destination, "repository", staged_source))
            else:
                records.append(_record(literal, destination, "present", ""))
            continue

        copied = False
        if repo_dir is not None and "/" in literal:
            segments = literal.split("/")
            extension = segments[-1].rsplit(".", 1)[-1].lower()
            if ".git" not in segments[:-1] and extension not in _REFUSED_REPOSITORY_EXTENSIONS:
                repo_path = repo_dir / literal
                try:
                    is_safe = (
                        repo_path.is_file()
                        and not repo_path.is_symlink()
                        and repo_path.stat().st_size <= _MAX_REPOSITORY_FIXTURE_BYTES
                    )
                except OSError:
                    is_safe = False
                if is_safe:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(repo_path, destination)
                    records.append(_record(literal, destination, "repository", literal))
                    copied = True
        if copied:
            continue

        extension = literal.rsplit(".", 1)[-1].lower()
        generated = generated_fixture(extension)
        if generated is not None:
            name, data = generated
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            records.append(_record(literal, destination, "generated", name))
            continue

        missing.append(literal)

    return FixturePlan(records=tuple(records), missing=tuple(missing))


# --- classification --------------------------------------------------------------

_CPP_MISSING_MARKERS = (
    "no such file",
    "cannot open",
    "can't open",
    "could not open",
    "failed to open",
    "not found",
    "does not exist",
)


def fixture_unavailable(output: str, missing: Sequence[str], platform: str) -> bool:
    """True when ``output`` shows a run that failed because a missing input could not be opened."""
    if not missing:
        return False
    lowered_output = output.lower()
    for literal in missing:
        basename = literal.rsplit("/", 1)[-1]
        if basename not in output:
            continue
        if platform == "python":
            if "FileNotFoundError" in output or "No such file or directory" in output:
                return True
        else:
            if any(marker in lowered_output for marker in _CPP_MISSING_MARKERS):
                return True
    return False
