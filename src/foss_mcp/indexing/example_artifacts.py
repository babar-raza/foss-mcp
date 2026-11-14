"""Structural validators for the files a verified example's run produces (TC-808, REQ-G3-014).

"Ran and produced a file" has to mean a valid file. A run that exits 0 and leaves a 0-byte PDF, a truncated PNG,
or a zip container that is not a zip proves nothing about the example, yet reads as "produced output". This
module judges a produced file by its own bytes, in pure Python (no third-party reader, no toolchain), for the
container formats the Aspose pilots write:

* OOXML (``docx``/``xlsx``/``pptx`` and their macro/template variants): a readable zip archive with a
  ``[Content_Types].xml`` part. A password-protected package is an OLE compound file, not a zip, so the OLE
  magic is accepted for these extensions too. ``zip``: a readable zip archive.
* ``pdf``: a ``%PDF-`` header and a ``%%EOF`` trailer near the end.
* ``png``: the 8-byte signature, then well-formed chunks (every CRC correct) that start with ``IHDR`` and end
  with ``IEND``. ``jpg``/``jpeg``: ``FF D8 FF`` at the start and the ``FF D9`` end-of-image marker at the end.
* ``eml``: an RFC 5322 header block (at least one recognised header) ahead of the first blank line.
* ``msg``: the OLE compound-file magic and at least a full header sector.

A file whose extension is not one of these is never judged here (``artifact_problem`` returns ``None``): this
module only ever rejects, it never decides that an unknown kind of file is fine or broken. It imports nothing
from ``example_verifier``; the verifier calls it from ``artefact_payload_bytes`` and ``execute_in_sandbox``.
"""

from __future__ import annotations

import io
import re
import struct
import zipfile
import zlib
from collections.abc import Callable

__all__ = [
    "ARTIFACT_EXTENSIONS",
    "artifact_is_valid",
    "artifact_problem",
    "artifact_problems",
]

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_OLE_HEADER_BYTES = 512
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_PDF_TAIL_BYTES = 1024
_OOXML_EXTENSIONS = frozenset(
    {"docx", "docm", "dotx", "dotm", "xlsx", "xlsm", "xltx", "xltm", "pptx", "pptm", "potx", "potm", "ppsx"}
)
_EML_CORE_HEADERS = frozenset(
    {
        "from",
        "to",
        "cc",
        "bcc",
        "subject",
        "date",
        "message-id",
        "mime-version",
        "received",
        "return-path",
        "reply-to",
        "sender",
        "content-type",
    }
)
_EML_HEADER_RE = re.compile(rb"^([!-9;-~]+):")


def _extension(name: str) -> str:
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    return base.rsplit(".", 1)[-1].lower() if "." in base else ""


def _zip_problem(data: bytes, *, content_types: bool) -> str | None:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError, OSError):
        return "not a readable zip archive"
    with archive:
        try:
            bad_member = archive.testzip()
        except (zipfile.BadZipFile, ValueError, OSError, zlib.error, NotImplementedError):
            return "a zip member cannot be read"
        if bad_member is not None:
            return f"zip member {bad_member!r} is corrupt"
        if content_types and "[Content_Types].xml" not in archive.namelist():
            return "zip archive has no [Content_Types].xml part"
    return None


def _ooxml_problem(data: bytes) -> str | None:
    if data.startswith(_OLE_MAGIC):
        return None  # a password-protected package is an OLE container (EncryptedPackage), not a zip
    return _zip_problem(data, content_types=True)


def _zip_container_problem(data: bytes) -> str | None:
    return _zip_problem(data, content_types=False)


def _pdf_problem(data: bytes) -> str | None:
    if not data.startswith(b"%PDF-"):
        return "missing the %PDF- header"
    if b"%%EOF" not in data[-_PDF_TAIL_BYTES:]:
        return "missing the %%EOF trailer"
    return None


def _png_problem(data: bytes) -> str | None:
    if not data.startswith(_PNG_MAGIC):
        return "missing the PNG signature"
    position = len(_PNG_MAGIC)
    first = True
    while True:
        if position + 8 > len(data):
            return "truncated PNG: the IEND chunk is missing"
        (length,) = struct.unpack(">I", data[position : position + 4])
        tag = data[position + 4 : position + 8]
        end = position + 8 + length + 4
        if end > len(data):
            return f"truncated PNG: the {tag.decode('latin-1')} chunk is cut short"
        if first and tag != b"IHDR":
            return "the first PNG chunk is not IHDR"
        first = False
        body = data[position + 8 : position + 8 + length]
        (crc,) = struct.unpack(">I", data[end - 4 : end])
        if zlib.crc32(tag + body) & 0xFFFFFFFF != crc:
            return f"the {tag.decode('latin-1')} chunk fails its CRC"
        if tag == b"IEND":
            return None
        position = end


def _jpeg_problem(data: bytes) -> str | None:
    if not data.startswith(b"\xff\xd8\xff"):
        return "missing the JPEG start-of-image marker"
    if not data.rstrip(b"\x00").endswith(b"\xff\xd9"):
        return "truncated JPEG: the end-of-image marker is missing"
    return None


def _eml_problem(data: bytes) -> str | None:
    head = re.split(rb"\r?\n\r?\n", data, maxsplit=1)[0]
    names: set[str] = set()
    for line in head.splitlines():
        if line[:1] in (b" ", b"\t"):
            continue  # a folded continuation of the previous header
        match = _EML_HEADER_RE.match(line)
        if match is None:
            return "the header block holds a line that is not a header"
        names.add(match.group(1).decode("ascii").lower())
    if not names:
        return "no headers"
    if not names & _EML_CORE_HEADERS:
        return "none of the From/To/Subject/Date/Message-ID headers is present"
    return None


def _msg_problem(data: bytes) -> str | None:
    if not data.startswith(_OLE_MAGIC):
        return "missing the OLE compound-file signature"
    if len(data) < _OLE_HEADER_BYTES:
        return "truncated OLE compound file: shorter than its header"
    return None


_CHECKS: dict[str, Callable[[bytes], str | None]] = {
    **{extension: _ooxml_problem for extension in _OOXML_EXTENSIONS},
    "zip": _zip_container_problem,
    "pdf": _pdf_problem,
    "png": _png_problem,
    "jpg": _jpeg_problem,
    "jpeg": _jpeg_problem,
    "eml": _eml_problem,
    "msg": _msg_problem,
}

ARTIFACT_EXTENSIONS = frozenset(_CHECKS)


def artifact_problem(name: str, data: bytes) -> str | None:
    """Why ``data`` is not a valid ``name``-kind file, or ``None`` when it is valid or of a kind this module does
    not judge. A 0-byte file of a judged kind is invalid."""
    check = _CHECKS.get(_extension(name))
    if check is None:
        return None
    if not data:
        return "empty file"
    return check(data)


def artifact_is_valid(name: str, data: bytes) -> bool:
    """True unless ``name`` is a judged kind of file whose bytes are structurally invalid."""
    return artifact_problem(name, data) is None


def artifact_problems(files: dict[str, bytes]) -> list[str]:
    """One ``"<name>: <reason>"`` line per invalid file of a judged kind, in the order given."""
    lines: list[str] = []
    for name, data in files.items():
        problem = artifact_problem(name, data)
        if problem is not None:
            lines.append(f"{name}: {problem}")
    return lines
