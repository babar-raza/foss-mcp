"""fetch_recent_releases: a plain CLI, no message broker - the same shape as
``fetch_product_reference.py``.

``src/foss_mcp/mcp/server.py``'s ``create_server`` has always accepted a ``recent_releases``
argument, and ``src/foss_mcp/extraction/github_release_reader.py``'s ``fetch_releases`` has
always been a real, already-tested GitHub Releases reader - but, until this module, nothing in
this repo was the production caller that writes ``fetch_releases``' result somewhere serving can
read it back from. Every live deployment answered ``list_recent_changes`` with ``[]`` regardless
of real tagged upstream releases: the identical "wired but not called" shape
``fetch_product_reference.py`` already fixed for ``get_product_reference``.

This module is that caller. It live-fetches a repository's GitHub releases via
``fetch_releases`` and writes the first ``--limit`` of them to a JSON sidecar file,
``recent_releases_<family>_<platform>.json`` next to the existing ``product_reference_*.json``
sidecar in the same manifests claim. ``load_recent_releases_sidecar`` is the inverse:
``infra/serve_http.py`` reads the sidecar back at server start and passes the rebuilt releases to
``create_server``'s ``recent_releases`` argument. This module keeps ``infra/ingest.py``'s own
plain-print, argparse-based CLI convention.

Unlike ``fetch_product_reference.py``'s pin (a single immutable commit SHA, which never changes
once recorded), a release *list* has no single pinned ref to compare against - a repository can
genuinely publish a new tag at any time. So the skip-on-unchanged idea TC-243 added there becomes
a time-based cache here instead: ``main`` skips the live fetch only when ``--output`` already
exists, is well-formed JSON, and is less than ``_SIDECAR_FRESHNESS_SECONDS`` old (by its own
mtime) - never a permanent skip.

A failed fetch is never recorded as an answer (TC-388). ``main`` leaves a well-formed existing
sidecar untouched, writes ``[]`` only when there is nothing usable (so the Helm initContainer's
wait-for-the-file still ends), and in both cases writes a ``<output>.failed`` marker that makes
``_sidecar_is_fresh`` False, so the next run refetches instead of trusting an empty list. A
successful fetch deletes the marker. ``--force`` skips the freshness check entirely.

G3/TC-511: every write is atomic (``atomic_write_json``, this module's own copy of the one in
``fetch_product_reference.py``: a temp file in the same directory, fsync, ``os.replace``). The sidecar
stays a bare JSON list; its provenance lives in a sibling ``<output>.meta.json`` (``fetched_at``,
``family``, ``platform``, ``source_repository``, ``release_count``, ``etag``). The stored ETag turns a
refresh of an unchanged list into one conditional request (304: the list bytes are kept, only the file
mtime and the meta ``fetched_at`` move). A failed fetch also logs an ``ERROR`` line on stderr and, with
nothing usable on disk, leaves a meta whose ``state`` is ``fetch_failed``; the exit status stays 0.
Each release records its ``target_commitish`` and ``created_at`` when GitHub supplied them.

G3/TC-519: a sidecar keeps up to 20 releases by default (was 10). When a repository publishes no
GitHub Releases at all its tags are written instead: a tag entry records ``"source": "tag"``, no notes
(richness ``none``) and the tagged commit as ``target_commitish`` (the tags API gives the sha with no
extra call). Tags are fetched only when the releases list came back empty.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from foss_mcp.extraction.github_http import failure_reason, reset_default_budget
from foss_mcp.extraction.github_release_reader import Release, fetch_releases, fetch_tags

_IDENTITY_PART = re.compile(r"[a-z0-9]+")
_SIDECAR_FILE_NAME = re.compile(r"recent_releases_([a-z0-9]+)_([a-z0-9]+)\.json")
_META_SCHEMA = 1

# A release list can genuinely change (a new tag ships), unlike a pinned commit SHA, so this is a
# time-based cache, not a permanent skip: a sidecar older than this is always re-fetched live.
_SIDECAR_FRESHNESS_SECONDS = 24 * 60 * 60

# How many releases a sidecar keeps by default. G3/TC-519 raised it from 10 to 20: a repository such as
# pdf/go has 12 releases and the extra two were being dropped. `list_recent_changes` still returns
# 10 by default (its own `limit`), so this only widens what is available to ask for.
_DEFAULT_LIMIT = 20


def recent_releases_sidecar_name(family: str, platform: str) -> str:
    """The file name one identity's recent-releases sidecar lives under in the manifests
    directory - the same lowercase-letters-and-digits validation as
    ``fetch_product_reference.sidecar_name``, duplicated rather than imported so this module
    stays inside its own write scope. Every pilot has its own name, so the pilots never
    overwrite one another on the shared claim, and the name cannot leave the directory.
    """
    for part in (family, platform):
        if not _IDENTITY_PART.fullmatch(part):
            raise ValueError(f"sidecar identity part {part!r} must be lowercase letters and digits")
    return f"recent_releases_{family}_{platform}.json"


def atomic_write_json(path: Path, doc: object) -> None:
    """Write *doc* as JSON to *path* atomically.

    Serialises first (a serialisation error leaves the old file untouched), writes a temp file in
    the same directory, flushes and fsyncs it, then ``os.replace``s it over *path*. The temp file
    is removed when anything fails, and no other file is created. A sidecar is read by another
    container, so the file keeps the existing file's permissions (else 0644), not mkstemp's 0600.
    """
    payload = json.dumps(doc)
    path = Path(path)
    descriptor, temp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            mode = path.stat().st_mode & 0o777
        except OSError:
            mode = 0o644
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(temp_name)


def releases_meta_path(output: Path) -> Path:
    """The provenance file ``<output>.meta.json`` that sits beside the releases sidecar *output*."""
    return output.with_name(output.name + ".meta.json")


def _serialize_release(release: Release) -> dict:
    """A ``Release`` as a JSON-serializable dict: its three real fields, verbatim, plus
    ``published_at`` / ``url`` / ``target_commitish`` / ``created_at`` only when GitHub supplied
    them (older sidecars have none of them).
    """
    data: dict = {"tag_name": release.tag_name, "body": release.body, "richness": release.richness}
    if release.published_at is not None:
        data["published_at"] = release.published_at
    if release.url is not None:
        data["url"] = release.url
    if release.target_commitish is not None:
        data["target_commitish"] = release.target_commitish
    if release.created_at is not None:
        data["created_at"] = release.created_at
    if release.source != "release":
        data["source"] = release.source
    return data


def _deserialize_release(data: dict) -> Release:
    """The inverse of ``_serialize_release``: rebuild the exact ``Release`` it wrote. The two
    optional keys may be absent (every sidecar written before TC-388, and the two commit keys of
    every sidecar written before TC-511), which loads as None.
    """
    return Release(
        tag_name=data["tag_name"],
        body=data["body"],
        richness=data["richness"],
        published_at=data.get("published_at"),
        url=data.get("url"),
        target_commitish=data.get("target_commitish"),
        created_at=data.get("created_at"),
        source=data.get("source", "release"),
    )


def load_recent_releases_sidecar(path: Path) -> tuple[Release, ...] | None:
    """Read the JSON sidecar *path* back into the ``Release`` tuple it was built from.

    Returns None when the file is absent (no ingestion has written it yet) - an absence, not an
    error, mirroring ``fetch_product_reference.load_manifest_sidecar``'s own documented contract.
    A present but malformed sidecar raises, so serving fails at start rather than answering from a
    half-read file - the same contract ``load_manifest_sidecar`` already documents.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError(
            f"recent releases sidecar {path} must contain a JSON list, got {type(data).__name__}"
        )
    return tuple(_deserialize_release(entry) for entry in data)


def _fetch_failed_marker(output: Path) -> Path:
    """The marker file ``<output>.failed`` that records the last fetch for *output* failed."""
    return output.with_name(output.name + ".failed")


def _sidecar_is_fresh(output: Path) -> bool:
    """True only when no failure marker exists and *output* exists, is well-formed JSON holding a
    list, and its own mtime is less than ``_SIDECAR_FRESHNESS_SECONDS`` old. Anything else - absent, unreadable, malformed,
    or merely stale - is never "fresh", so ``main`` always falls back to fetching live; this never
    raises, so a malformed existing file cannot block a re-run. A ``<output>.failed`` marker means
    the last fetch failed, so whatever the sidecar holds is not an answer to trust: never fresh.
    """
    if _fetch_failed_marker(output).exists():
        return False
    try:
        age_seconds = time.time() - output.stat().st_mtime
    except OSError:
        return False
    if age_seconds >= _SIDECAR_FRESHNESS_SECONDS:
        return False
    try:
        data = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, list)


_URL_PATTERN = re.compile(r"\w+://\S+")
_TOKEN_PATTERN = re.compile(r"\b(?:gh[pousr]_|github_pat_)\w+|\b[Bb]earer\s+\S+|\b[Tt]oken\s+\S+")
_MAX_DETAIL_CHARS = 200


def _safe_failure_detail(exc: BaseException) -> str:
    """The exception's message with URLs and token-looking strings removed and its length capped,
    for the console line only. The marker file never carries any message text.
    """
    text = _URL_PATTERN.sub("<url>", str(exc))
    text = _TOKEN_PATTERN.sub("<redacted>", text)
    return " ".join(text.split())[:_MAX_DETAIL_CHARS]


def _sidecar_holds_a_list(output: Path) -> bool:
    """True when *output* is readable, well-formed JSON holding a list (good data to preserve)."""
    try:
        return isinstance(json.loads(output.read_text(encoding="utf-8")), list)
    except (OSError, ValueError):
        return False


def _write_failure_marker(output: Path, exc: BaseException) -> None:
    """Record only the exception class name and a UTC timestamp - no message, no URL."""
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    _fetch_failed_marker(output).write_text(f"{type(exc).__name__} {stamp}\n", encoding="utf-8")


def _utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _identity(args: argparse.Namespace) -> tuple[str | None, str | None]:
    """``(family, platform)``: ``--family``/``--platform`` when given, else parsed from an ``--output``
    file name of the form ``recent_releases_<family>_<platform>.json``, else None."""
    match = _SIDECAR_FILE_NAME.fullmatch(args.output.name)
    family = args.family or (match.group(1) if match else None)
    platform = args.platform or (match.group(2) if match else None)
    return family, platform


def _read_meta(output: Path) -> dict:
    """The parsed meta file beside *output*, or an empty dict when absent, unreadable or not an object."""
    try:
        data = json.loads(releases_meta_path(output).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _sidecar_holds_tag_entries(output: Path) -> bool:
    """True when the sidecar *output* is a good list with at least one tag-sourced entry."""
    try:
        data = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, list) and any(isinstance(e, dict) and e.get("source") == "tag" for e in data)


def _stored_etag(output: Path, repository: str) -> str | None:
    """The ETag to send as ``If-None-Match``: only when *output* holds a good list and its meta
    records a successful fetch of this repository together with an ETag."""
    if not _sidecar_holds_a_list(output):
        return None
    meta = _read_meta(output)
    etag = meta.get("etag")
    if meta.get("state") != "ok" or meta.get("source_repository") != repository:
        return None
    return etag if isinstance(etag, str) and etag else None


def _ok_meta(
    args: argparse.Namespace, release_count: int, etag: str | None, *, previous: dict | None = None
) -> dict:
    family, platform = _identity(args)
    previous = previous or {}
    return {
        "schema": _META_SCHEMA,
        "state": "ok",
        "fetched_at": _utc_stamp(),
        "family": family if family is not None else previous.get("family"),
        "platform": platform if platform is not None else previous.get("platform"),
        "source_repository": args.repository,
        "release_count": release_count,
        "etag": etag,
    }


def _record_failure(args: argparse.Namespace, exc: BaseException) -> str:
    """Handle a failed fetch and return the outcome phrase. Good data is never replaced; with nothing
    usable on disk an empty list and a ``fetch_failed`` meta are written. The marker is always written."""
    output: Path = args.output
    if _sidecar_holds_a_list(output):
        outcome = f"kept the existing sidecar {output}"
    else:
        family, platform = _identity(args)
        atomic_write_json(output, [])
        atomic_write_json(
            releases_meta_path(output),
            {
                "schema": _META_SCHEMA,
                "state": "fetch_failed",
                "reason": failure_reason(exc),
                "error_class": type(exc).__name__,
                "fetched_at": _utc_stamp(),
                "family": family,
                "platform": platform,
                "source_repository": args.repository,
            },
        )
        outcome = f"wrote an empty placeholder sidecar to {output}"
    _write_failure_marker(output, exc)
    return outcome


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, help="'owner/name'")
    parser.add_argument("--limit", type=int, default=_DEFAULT_LIMIT)
    parser.add_argument("--output", type=Path, required=True, help="path to write the JSON sidecar")
    parser.add_argument(
        "--force",
        action="store_true",
        help="refetch even when the existing sidecar looks fresh",
    )
    parser.add_argument(
        "--family", default=None, help="recorded in the meta file; default: parsed from --output"
    )
    parser.add_argument(
        "--platform", default=None, help="recorded in the meta file; default: parsed from --output"
    )
    args = parser.parse_args()
    # A run starts with a clean request budget, whatever an earlier in-process run left behind.
    reset_default_budget()

    if not args.force and _sidecar_is_fresh(args.output):
        print(
            f"skipped fetch for {args.repository} -> {args.output}: "
            f"existing sidecar is less than {_SIDECAR_FRESHNESS_SECONDS} seconds old"
        )
        return

    stored_etag = _stored_etag(args.output, args.repository)
    if stored_etag is not None and _sidecar_holds_tag_entries(args.output):
        # The releases list of a tag-only repository never changes (it is empty), so a 304 would
        # keep stale tags for ever: send no ETag and re-read the tags instead.
        stored_etag = None
    fetch_meta: dict = {}
    try:
        if stored_etag is not None:
            fetched = fetch_releases(args.repository, etag=stored_etag, meta=fetch_meta)
        else:
            fetched = fetch_releases(args.repository, meta=fetch_meta)
        if not fetched and not fetch_meta.get("not_modified"):
            # G3/TC-519: a repository without GitHub Releases is described by its tags instead. Tags
            # are fetched ONLY here; a failure follows the same path as a failed releases fetch.
            fetched = fetch_tags(args.repository)
        releases = fetched[: args.limit]
    except Exception as exc:  # noqa: BLE001 - deliberately broad: any failure is recorded as one.
        # A sidecar must always eventually exist once this step runs at all (see module docstring
        # and infra/helm/foss-mcp/templates/deployment.yaml's initContainer, which waits on this
        # file), so when there is nothing usable on disk an empty list is written. But a failure
        # is not an answer: good existing data is never replaced, and the marker makes the next
        # run refetch rather than trust either file.
        outcome = _record_failure(args, exc)
        detail = _safe_failure_detail(exc)
        print(
            f"failed to fetch releases for {args.repository}: {type(exc).__name__} -- {outcome}; "
            f"marked {_fetch_failed_marker(args.output)} for a retry"
            + (f" (detail: {detail})" if detail else "")
        )
        print(
            f"ERROR releases fetch failed: class={type(exc).__name__} reason={failure_reason(exc)} "
            f"repository={args.repository}; {outcome}",
            file=sys.stderr,
        )
        return

    if stored_etag is not None and fetch_meta.get("not_modified"):
        # The list is unchanged upstream: keep its bytes, only move the mtime and the meta's fetched_at.
        os.utime(args.output)
        previous = _read_meta(args.output)
        atomic_write_json(
            releases_meta_path(args.output),
            _ok_meta(args, int(previous.get("release_count") or 0), stored_etag, previous=previous),
        )
        _fetch_failed_marker(args.output).unlink(missing_ok=True)
        print(f"not modified: {args.repository} -> {args.output}: the existing sidecar is current")
        return

    atomic_write_json(args.output, [_serialize_release(release) for release in releases])
    etag = fetch_meta.get("etag")
    atomic_write_json(
        releases_meta_path(args.output),
        _ok_meta(args, len(releases), etag if isinstance(etag, str) else None),
    )
    _fetch_failed_marker(args.output).unlink(missing_ok=True)
    print(f"wrote {len(releases)} recent release(s) for {args.repository} -> {args.output}")


if __name__ == "__main__":
    main()
