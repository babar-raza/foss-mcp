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

Consuming that sidecar from ``infra/serve_http.py`` at server-start time is intentionally OUT
of scope here (a separate follow-up card): this module only produces the sidecar file, mirroring
``infra/ingest.py``'s own plain-print, argparse-based CLI convention exactly rather than
inventing a different one.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from foss_mcp.extraction.manifest_reader import fetch_manifest_file
from foss_mcp.extraction.repo_native_reader import DocumentPresent, DocumentResult, read_repo_document


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
    }


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
