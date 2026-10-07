"""fetch_product_reference: a plain CLI, no message broker.

``get_product_reference.py``'s own docstring says it does no network I/O itself and expects
``foss_mcp.extraction``'s readers to do the live reads "elsewhere" - but before this module,
nothing anywhere in this repo was that "elsewhere": ``fetch_manifest_file``
(``foss_mcp.extraction.manifest_reader``) and ``read_repo_document``
(``foss_mcp.extraction.repo_native_reader``) were both real, already-implemented Contents-API
readers with zero production callers.

This module is that first real caller. It live-fetches a repository's packaging manifest text
(when a manifest path is given) plus its CONTRIBUTING.md and AGENTS.md via the GitHub Contents
API, and writes the results to a JSON sidecar file shaped to match
``get_product_reference.ProductReferenceInputs``'s own fields - ``manifest_text``, ``platform``,
``contributing``, ``agent_guidance``. Each ``DocumentResult`` serializes to an explicit
present/not_present shape; absence is never smoothed into a fabricated value.

``load_manifest_sidecar`` is the inverse: ``infra/serve_http.py`` reads the sidecar back at
server start and passes the rebuilt ``ProductReferenceInputs`` to ``create_server``. This module
keeps ``infra/ingest.py``'s own plain-print, argparse-based CLI convention.

``--ref`` is always a pinned, immutable commit SHA in production (never a branch), so a re-run
whose ``--repository``/``--ref`` exactly match what the existing ``--output`` sidecar already
records has nothing to re-verify: the sidecar also carries ``source_repository``/``source_ref``
(recorded verbatim from the arguments that produced it), and ``main`` skips the live fetch
entirely - zero network calls - when a re-run's pin is unchanged. ``source_repository``/
``source_ref`` are sidecar-file-only fields: ``ProductReferenceInputs`` (in
``get_product_reference.py``, outside this module's write scope) is not extended with them, and
``load_manifest_sidecar`` does not read them back; only ``main``'s own skip-check reads them,
directly off the raw JSON.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from foss_mcp.extraction.manifest_reader import fetch_manifest_file
from foss_mcp.extraction.repo_native_reader import (
    DocumentNotPresent,
    DocumentPresent,
    DocumentResult,
    read_repo_document,
)
from foss_mcp.mcp.tools.get_product_reference import ProductReferenceInputs

_IDENTITY_PART = re.compile(r"[a-z0-9]+")


def sidecar_name(family: str, platform: str) -> str:
    """The file name one identity's sidecar lives under in the manifests directory.

    The ingestion Job for a pilot writes this name, and serving for the same identity reads it.
    Every pilot has its own name, so the pilots never overwrite one another on the shared claim.
    The parts are restricted to lowercase letters and digits, so the name cannot leave the
    directory.
    """
    for part in (family, platform):
        if not _IDENTITY_PART.fullmatch(part):
            raise ValueError(f"sidecar identity part {part!r} must be lowercase letters and digits")
    return f"product_reference_{family}_{platform}.json"


def _serialize_document(document: DocumentResult) -> dict:
    """A ``DocumentResult`` as a JSON-serializable dict - the explicit present/not_present
    shape the sidecar file stores, never collapsing absence into a fabricated value."""
    if isinstance(document, DocumentPresent):
        return {
            "status": "present",
            "path": document.path,
            "sha": document.sha,
            "size": document.size,
            "content": document.content,
        }
    return {"status": "not_present", "path": document.path}


def _deserialize_document(data: dict) -> DocumentResult:
    """The inverse of ``_serialize_document``: rebuild the exact ``DocumentResult`` it wrote."""
    status = data.get("status")
    if status == "present":
        return DocumentPresent(path=data["path"], sha=data["sha"], size=data["size"], content=data["content"])
    if status == "not_present":
        return DocumentNotPresent(path=data["path"])
    raise ValueError(f"unknown document status {status!r} in product reference sidecar")


def load_manifest_sidecar(path: Path) -> ProductReferenceInputs | None:
    """Read the JSON sidecar *path* back into the ``ProductReferenceInputs`` it was built from.

    Returns None when the file is absent (no ingestion has written it yet) - an absence, not an
    error. A present but malformed sidecar raises, so serving fails at start rather than
    answering from a half-read file.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    data = json.loads(text)
    return ProductReferenceInputs(
        manifest_text=data["manifest_text"],
        platform=data["platform"],
        contributing=_deserialize_document(data["contributing"]),
        agent_guidance=_deserialize_document(data["agent_guidance"]),
    )


def build_sidecar(
    *,
    repository: str,
    platform: str,
    manifest_path: str | None,
    contributing_path: str,
    agent_guidance_path: str,
    ref: str | None,
) -> dict:
    """Live-fetch *repository*'s manifest text (only if *manifest_path* is given) and its
    contributing/agent-guidance documents, and return a JSON-serializable dict shaped to match
    ``get_product_reference.ProductReferenceInputs``'s own fields.
    """
    manifest_text: str | None = None
    if manifest_path is not None:
        manifest_text = fetch_manifest_file(repository, manifest_path, ref=ref)

    contributing = read_repo_document(repository, contributing_path, ref=ref)
    agent_guidance = read_repo_document(repository, agent_guidance_path, ref=ref)

    return {
        "manifest_text": manifest_text,
        "platform": platform,
        "contributing": _serialize_document(contributing),
        "agent_guidance": _serialize_document(agent_guidance),
        "source_repository": repository,
        "source_ref": ref,
    }


def _existing_sidecar_source(output: Path) -> tuple[str, str] | None:
    """The ``(source_repository, source_ref)`` pair recorded in *output*'s existing sidecar
    JSON, or ``None`` when the file is absent, unparseable, not a JSON object, or missing either
    field as a non-None value - every one of those is treated as absent, never as an error that
    blocks ingestion, so the caller always falls back to fetching live.
    """
    try:
        data = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    source_repository = data.get("source_repository")
    source_ref = data.get("source_ref")
    if source_repository is None or source_ref is None:
        return None
    return (source_repository, source_ref)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, help="'owner/name'")
    parser.add_argument("--platform", required=True)
    parser.add_argument(
        "--manifest-path",
        default=None,
        help="path to the packaging manifest in the repository; omit to skip the manifest fetch entirely",
    )
    parser.add_argument("--contributing-path", default="CONTRIBUTING.md")
    parser.add_argument("--agent-guidance-path", default="AGENTS.md")
    parser.add_argument("--ref", default=None)
    parser.add_argument("--output", type=Path, required=True, help="path to write the JSON sidecar")
    args = parser.parse_args()

    if _existing_sidecar_source(args.output) == (args.repository, args.ref):
        print(
            f"skipped fetch for {args.repository} ({args.platform}) -> {args.output}: "
            f"pin unchanged (repository={args.repository}, ref={args.ref})"
        )
        return

    sidecar = build_sidecar(
        repository=args.repository,
        platform=args.platform,
        manifest_path=args.manifest_path,
        contributing_path=args.contributing_path,
        agent_guidance_path=args.agent_guidance_path,
        ref=args.ref,
    )
    args.output.write_text(json.dumps(sidecar), encoding="utf-8")
    print(f"wrote product reference inputs for {args.repository} ({args.platform}) -> {args.output}")


if __name__ == "__main__":
    main()
