"""Pilot manual export of furnished content, until Aspose's own CI produces this bundle.

This script is a PILOT WORKAROUND, not the real thing: the eventual production path is a
public export bundle Aspose's own CI produces from the private ``Aspose/aspose.org`` repo.
Until that exists, this tool authenticates with ``gh`` (the access ``OWNER-01`` records as
credential-gated) and exports one content subtree by hand, at a pinned commit, with a
per-file SHA-256 manifest so the bundle can be verified before ingestion. Every run - and
every exported page - announces itself as NON-PRODUCTION; nothing downstream should mistake
this for the real export.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

NON_PRODUCTION_BANNER = (
    "=" * 70 + "\nNON-PRODUCTION PILOT EXPORT\n"
    "This bundle is a manual stand-in for the public export bundle Aspose's own CI\n"
    "must eventually produce. It is not the real thing - do not ingest it as one.\n" + "=" * 70
)


def print_banner() -> None:
    print(NON_PRODUCTION_BANNER)


def _gh_api(path: str) -> Any:
    result = subprocess.run(["gh", "api", path], check=True, capture_output=True, text=True, encoding="utf-8")
    return json.loads(result.stdout)


def resolve_commit(repository: str, ref: str) -> str:
    """The exact commit SHA *ref* names in *repository* right now - never assumed, always read."""
    return _gh_api(f"repos/{repository}/commits/{ref}")["sha"]


def _list_tree(repository: str, commit_sha: str, subtree: str) -> list[dict[str, Any]]:
    tree = _gh_api(f"repos/{repository}/git/trees/{commit_sha}?recursive=1")["tree"]
    prefix = subtree.rstrip("/") + "/"
    return [entry for entry in tree if entry["type"] == "blob" and entry["path"].startswith(prefix)]


def _fetch_blob(repository: str, blob_sha: str) -> bytes:
    blob = _gh_api(f"repos/{repository}/git/blobs/{blob_sha}")
    return base64.b64decode(blob["content"])


@dataclass(frozen=True)
class ExportedFile:
    """One exported page: its checksum and the exact commit it came from.

    ``source_commit`` is carried per file, not only once at the manifest's top level, so a
    file record can never be separated from the commit that proves it and still look complete.
    """

    path: str
    sha256: str
    size: int
    source_commit: str


def _write_manifest(
    output_dir: Path, repository: str, subtree: str, commit_sha: str, files: list[ExportedFile]
) -> dict[str, Any]:
    """Build and write the checksum manifest for an already-materialized bundle.

    Shared tail of both export paths (GitHub-API and local-clone) so the manifest shape -
    banner, provenance fields, per-file records - can never drift between them.
    """
    manifest = {
        "banner": NON_PRODUCTION_BANNER,
        "source_repository": repository,
        "source_subtree": subtree,
        "source_commit": commit_sha,
        "files": [asdict(f) for f in files],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def export_subtree(repository: str, subtree: str, ref: str, output_dir: Path) -> dict[str, Any]:
    """Export every file under *subtree* in *repository* at *ref*, pinned to its exact commit.

    Requires credentialed ``gh`` access to *repository* (private for ``Aspose/aspose.org``,
    per ``OWNER-01``). Writes each file under ``<output_dir>/pages/`` and a checksum manifest
    at ``<output_dir>/manifest.json``.
    """
    commit_sha = resolve_commit(repository, ref)
    entries = _list_tree(repository, commit_sha, subtree)
    prefix = subtree.rstrip("/") + "/"
    pages_dir = output_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    files: list[ExportedFile] = []
    for entry in entries:
        relative = entry["path"][len(prefix) :]
        content = _fetch_blob(repository, entry["sha"])
        destination = pages_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        files.append(
            ExportedFile(
                path=relative,
                sha256=hashlib.sha256(content).hexdigest(),
                size=len(content),
                source_commit=commit_sha,
            )
        )
    return _write_manifest(output_dir, repository, subtree, commit_sha, files)


def resolve_local_commit(clone_path: Path | str, ref: str) -> str:
    """The exact commit SHA *ref* names in the local clone at *clone_path* right now.

    Read-only git plumbing (``git -C <clone_path> rev-parse <ref>``) - never touches the
    clone's working tree, index, or refs. Mirrors ``resolve_commit``, backed by a local
    clone instead of the GitHub API.
    """
    result = subprocess.run(
        ["git", "-C", str(clone_path), "rev-parse", ref],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _list_local_tree(clone_path: Path | str, commit_sha: str, subtree: str) -> list[dict[str, str]]:
    """Every blob under *subtree* at *commit_sha* in the local clone at *clone_path*.

    Read-only git plumbing (``git -C <clone_path> ls-tree -r <commit_sha> -- <subtree>``),
    parsing the ``<mode> <type> <sha>\\t<path>`` output format. Mirrors ``_list_tree``.
    """
    result = subprocess.run(
        ["git", "-C", str(clone_path), "ls-tree", "-r", commit_sha, "--", subtree],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    entries: list[dict[str, str]] = []
    for line in result.stdout.splitlines():
        if not line:
            continue
        meta, _, path = line.partition("\t")
        mode, entry_type, blob_sha = meta.split(" ")
        if entry_type != "blob":
            continue
        entries.append({"path": path, "sha": blob_sha})
    return entries


def _fetch_local_blob(clone_path: Path | str, blob_sha: str) -> bytes:
    """The raw bytes of blob *blob_sha* in the local clone at *clone_path*.

    Read-only git plumbing (``git -C <clone_path> cat-file -p <blob_sha>``). Captured as raw
    bytes (no ``text=True``) so binary or non-UTF-8 content is never corrupted by decoding.
    Mirrors ``_fetch_blob``.
    """
    result = subprocess.run(
        ["git", "-C", str(clone_path), "cat-file", "-p", blob_sha],
        check=True,
        capture_output=True,
    )
    return result.stdout


def export_local_subtree(
    clone_path: Path | str, repository: str, subtree: str, ref: str, output_dir: Path
) -> dict[str, Any]:
    """Export every file under *subtree* at *ref*, read from a local read-only git clone.

    Parallel to ``export_subtree``, but never calls the GitHub API and needs no credentials:
    every read is read-only git plumbing (``rev-parse``, ``ls-tree -r``, ``cat-file -p``)
    against *clone_path*, pinned to the exact commit *ref* resolves to - never the clone's
    working tree directly, so an uncommitted local change in *clone_path* cannot leak in.
    *repository* is recorded in the manifest as provenance metadata (e.g. ``Aspose/aspose.org``);
    it does not have to match how *clone_path* is reached. Writes each file under
    ``<output_dir>/pages/`` and a checksum manifest at ``<output_dir>/manifest.json``, in the
    exact same shape ``export_subtree`` produces.
    """
    commit_sha = resolve_local_commit(clone_path, ref)
    entries = _list_local_tree(clone_path, commit_sha, subtree)
    prefix = subtree.rstrip("/") + "/"
    pages_dir = output_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    files: list[ExportedFile] = []
    for entry in entries:
        relative = entry["path"][len(prefix) :]
        content = _fetch_local_blob(clone_path, entry["sha"])
        destination = pages_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        files.append(
            ExportedFile(
                path=relative,
                sha256=hashlib.sha256(content).hexdigest(),
                size=len(content),
                source_commit=commit_sha,
            )
        )
    return _write_manifest(output_dir, repository, subtree, commit_sha, files)


def verify_bundle(bundle_dir: Path) -> list[str]:
    """Every problem found checking *bundle_dir* against its own manifest; empty means clean.

    A page whose manifest entry has no ``source_commit`` is rejected outright - a checksum
    alone proves a file was not altered, never where it came from.
    """
    manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    problems: list[str] = []
    for entry in manifest.get("files", []):
        path = entry.get("path", "<unnamed>")
        if not entry.get("source_commit"):
            problems.append(f"{path}: missing source_commit")
            continue
        page_path = bundle_dir / "pages" / path
        if not page_path.is_file():
            problems.append(f"{path}: file missing from bundle")
            continue
        actual = hashlib.sha256(page_path.read_bytes()).hexdigest()
        if actual != entry.get("sha256"):
            problems.append(f"{path}: checksum mismatch (tampered or corrupt)")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default="Aspose/aspose.org")
    parser.add_argument("--subtree", default="content/products.aspose.org/en/pdf/net")
    parser.add_argument("--ref", default="main")
    parser.add_argument(
        "--local-clone",
        type=Path,
        default=None,
        help=(
            "Path to a local, read-only git clone of --repository. When given, export via "
            "read-only git plumbing (rev-parse/ls-tree/cat-file) against this clone instead "
            "of the GitHub API - no `gh` call, no credentials needed. Alternative to relying "
            "on --repository/--ref reaching a live, credentialed GitHub API."
        ),
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    print_banner()
    if args.local_clone is not None:
        manifest = export_local_subtree(args.local_clone, args.repository, args.subtree, args.ref, args.output)
    else:
        manifest = export_subtree(args.repository, args.subtree, args.ref, args.output)
    print(
        f"exported {len(manifest['files'])} file(s) from "
        f"{args.repository}@{manifest['source_commit']} to {args.output}"
    )


if __name__ == "__main__":
    main()
