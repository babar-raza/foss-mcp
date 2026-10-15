"""Shared fixtures for the readiness tests (G3/TC-506); this module holds no tests.

``ready_chunks`` builds the smallest set of chunks that clears the readiness floors
(``foss_mcp.mcp.health.READINESS_SYMBOL_FLOOR`` symbol chunks and
``READINESS_DOCUMENTATION_FLOOR`` documentation chunk): three distinct symbol chunks and one
documentation chunk, plus one verified example when asked. A ``Chunk`` is built exactly as
``tests/infra/test_serve_http.py`` builds one.
"""

from __future__ import annotations

from foss_mcp.normalization.chunker import Chunk
from foss_mcp.normalization.document_schema import NOT_CHECKED, Provenance

SOURCE_COMMIT = "b717287b717287b717287b717287b717287b7172"

SYMBOL_ALPHA_TEXT = (
    f"FQN: Aspose.Pdf.Alpha\nKind: class_declaration\nSummary of Alpha.\nSource-Commit: {SOURCE_COMMIT}"
)
SYMBOL_BETA_TEXT = (
    f"FQN: Aspose.Pdf.Beta\nKind: class_declaration\nSummary of Beta.\nSource-Commit: {SOURCE_COMMIT}"
)
SYMBOL_GAMMA_TEXT = (
    f"FQN: Aspose.Pdf.Gamma\nKind: class_declaration\nSummary of Gamma.\nSource-Commit: {SOURCE_COMMIT}"
)
DOCUMENTATION_TEXT = (
    "FQN: Doc: Getting started\nKind: documentation\n"
    f"How to install and open a first file.\nSource-Commit: {SOURCE_COMMIT}"
)
EXAMPLE_TEXT = (
    "FQN: Example: sign a file\nKind: verified_example\n"
    f"Example:\ndoc.Sign(certificate, signature)\nSource-Commit: {SOURCE_COMMIT}"
)

# What a test that already publishes one or two chunks of its own appends to clear the floors:
# two extra symbol chunks and one documentation chunk, none of them an example.
FLOOR_FILLER_TEXTS: tuple[str, ...] = (SYMBOL_BETA_TEXT, SYMBOL_GAMMA_TEXT, DOCUMENTATION_TEXT)


def make_chunk(title: str, text: str, provenance: Provenance) -> Chunk:
    """One ``Chunk``, built exactly as tests/infra/test_serve_http.py builds one."""
    return Chunk(title, text, "self_extracted", "api_surface", provenance, "highest", (), NOT_CHECKED)


def ready_chunks(provenance: Provenance, *, with_example: bool = False) -> list[Chunk]:
    """Three symbol chunks, one documentation chunk and (when asked) one verified example."""
    chunks = [
        make_chunk("Alpha", SYMBOL_ALPHA_TEXT, provenance),
        make_chunk("Beta", SYMBOL_BETA_TEXT, provenance),
        make_chunk("Gamma", SYMBOL_GAMMA_TEXT, provenance),
        make_chunk("Getting started", DOCUMENTATION_TEXT, provenance),
    ]
    if with_example:
        chunks.append(make_chunk("sign a file", EXAMPLE_TEXT, provenance))
    return chunks
