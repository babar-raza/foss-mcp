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
"""

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path

from foss_mcp.extraction.github_release_reader import Release, fetch_releases

_IDENTITY_PART = re.compile(r"[a-z0-9]+")

# A release list can genuinely change (a new tag ships), unlike a pinned commit SHA, so this is a
# time-based cache, not a permanent skip: a sidecar older than this is always re-fetched live.
_SIDECAR_FRESHNESS_SECONDS = 24 * 60 * 60

# list_recent_changes' own default (src/foss_mcp/mcp/server.py's list_recent_changes_tool).
_DEFAULT_LIMIT = 10


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


def _serialize_release(release: Release) -> dict:
    """A ``Release`` as a JSON-serializable dict: its three real fields, verbatim, plus
    ``published_at`` / ``url`` only when GitHub supplied them (older sidecars have neither).
    """
    data: dict = {"tag_name": release.tag_name, "body": release.body, "richness": release.richness}
    if release.published_at is not None:
        data["published_at"] = release.published_at
    if release.url is not None:
        data["url"] = release.url
    return data


def _deserialize_release(data: dict) -> Release:
    """The inverse of ``_serialize_release``: rebuild the exact ``Release`` it wrote. The two
    optional keys may be absent (every sidecar written before TC-388), which loads as None.
    """
    return Release(
        tag_name=data["tag_name"],
        body=data["body"],
        richness=data["richness"],
        published_at=data.get("published_at"),
        url=data.get("url"),
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
    args = parser.parse_args()

    if not args.force and _sidecar_is_fresh(args.output):
        print(
            f"skipped fetch for {args.repository} -> {args.output}: "
            f"existing sidecar is less than {_SIDECAR_FRESHNESS_SECONDS} seconds old"
        )
        return

    try:
        releases = fetch_releases(args.repository)[: args.limit]
    except Exception as exc:  # noqa: BLE001 - deliberately broad: any failure is recorded as one.
        # A sidecar must always eventually exist once this step runs at all (see module docstring
        # and infra/helm/foss-mcp/templates/deployment.yaml's initContainer, which waits on this
        # file), so when there is nothing usable on disk an empty list is written. But a failure
        # is not an answer: good existing data is never replaced, and the marker makes the next
        # run refetch rather than trust either file.
        if _sidecar_holds_a_list(args.output):
            outcome = f"kept the existing sidecar {args.output}"
        else:
            args.output.write_text(json.dumps([]), encoding="utf-8")
            outcome = f"wrote an empty placeholder sidecar to {args.output}"
        _write_failure_marker(args.output, exc)
        detail = _safe_failure_detail(exc)
        print(
            f"failed to fetch releases for {args.repository}: {type(exc).__name__} -- {outcome}; "
            f"marked {_fetch_failed_marker(args.output)} for a retry"
            + (f" (detail: {detail})" if detail else "")
        )
        return

    args.output.write_text(
        json.dumps([_serialize_release(release) for release in releases]), encoding="utf-8"
    )
    _fetch_failed_marker(args.output).unlink(missing_ok=True)
    print(f"wrote {len(releases)} recent release(s) for {args.repository} -> {args.output}")


if __name__ == "__main__":
    main()
