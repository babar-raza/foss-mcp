"""verify_package_registry: the ingestion-time registry-verification sidecar fetcher for C2
(fabricated installation coordinates, third independent audit, 2026-10-08) - the same shape as
``fetch_recent_releases.py``.

Before this module, ``get_product_reference(section="install")`` presented whatever package name
a product's own packaging manifest stated as if it were a verified, installable public package,
with no distinction anywhere in the system between "the manifest says this" and "this actually
resolves on the real registry". Live-confirmed against all 40 deployed pilots plus each claimed
coordinate's real registry: 15 of 33 install-claiming pilots serve a coordinate that does not
exist on its stated registry. Confirmed, by fetching the real upstream manifest directly for two
of these, that this is NOT an extraction bug - the manifest genuinely states exactly the name
served; the gap is architectural (see ``docs/DECISION_LOG.md``'s "C2" entry).

This module is the verification step only: given one package coordinate and the ecosystem its
registry lives on, it resolves that coordinate against the real registry - using each ecosystem's
own authoritative, unauthenticated, public endpoint - and writes the result to a JSON sidecar,
``package_registry_<family>_<platform>.json``, next to the existing ``product_reference_*.json``
and ``recent_releases_*.json`` sidecars in the same manifests claim. ``load_package_registry_sidecar``
is the inverse. Wiring this CLI into the ingestion Job chain, Helm/compose, and
``get_product_reference``'s own response shape is explicitly deferred to a follow-up card; nothing
in this module calls or is called by any of those yet.

Like ``fetch_recent_releases.py`` (and unlike ``fetch_product_reference.py``'s single immutable pin),
a registry's own contents can genuinely change - a package can be published after this ran, or
unpublished - so ``main`` uses the identical time-based cache: a sidecar newer than
``_SIDECAR_FRESHNESS_SECONDS`` is never re-checked live, but it is never a permanent skip either.

Each ecosystem checker returns True (the coordinate is confirmed to exist on its registry), False
(confirmed not to exist), or raises on a genuine network/transport failure. A transport failure is
never silently guessed at as either classification: ``main`` lets that exception propagate rather
than writing a sidecar claiming ``verified: false``, exactly mirroring the "|| true" tolerance
``fetch_recent_releases.py``'s own caller already applies for its own live-fetch failures - the
decision of how to handle a failed check belongs to that future caller, not to this module.

``go_latest_version``, ``resolve_tag_commit``, and the composing ``go_published_commit`` (G2/
TC-296) are a second, separate capability added later: not "does this coordinate exist" but "what
is the real commit SHA a fresh install would fetch right now". A round-4 independent audit
(2026-10-09) found this had never existed for any ecosystem anywhere in this codebase, and that
the gap was actively masking a real divergence - pdf/go's indexed source commit ran 66 commits
ahead of the only version ``go get`` actually fetches, silently hiding a merged security fix the
installable package still lacks. This card proves the resolution for ONE ecosystem (Go, matching
the audit's own worked example). Generalizing to every other ecosystem, and wiring the result into
the live ``report_index_freshness`` MCP tool so an agent is actually warned, are both explicitly
deferred to follow-up cards; nothing in this module calls or is called by that tool yet.
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from foss_mcp.extraction.github_http import urlopen_with_backoff, with_auth

_IDENTITY_PART = re.compile(r"[a-z0-9]+")

# A registry's contents can genuinely change (a package can be published or removed), unlike a
# pinned commit SHA, so this is a time-based cache, not a permanent skip - the same convention and
# the same window fetch_recent_releases.py already uses.
_SIDECAR_FRESHNESS_SECONDS = 24 * 60 * 60

# Generous but bounded - a hung registry request must not hang ingestion forever.
_REQUEST_TIMEOUT_SECONDS = 10

_REQUIRED_SIDECAR_KEYS = ("ecosystem", "coordinate", "verified", "checked_at")


def verify_package_registry_sidecar_name(family: str, platform: str) -> str:
    """The file name one identity's package-registry-verification sidecar lives under in the
    manifests directory - the same lowercase-letters-and-digits validation as
    ``fetch_recent_releases.recent_releases_sidecar_name`` (itself duplicated from
    ``fetch_product_reference.sidecar_name``), duplicated again here rather than imported so this
    module stays inside its own write scope. Every pilot has its own name, so the pilots never
    overwrite one another on the shared claim, and the name cannot leave the directory.
    """
    for part in (family, platform):
        if not _IDENTITY_PART.fullmatch(part):
            raise ValueError(f"sidecar identity part {part!r} must be lowercase letters and digits")
    return f"package_registry_{family}_{platform}.json"


def load_package_registry_sidecar(path: Path) -> dict | None:
    """Read the JSON sidecar *path* back into the verification result dict it was built from:
    ``{"ecosystem": str, "coordinate": str, "verified": bool, "checked_at": str (ISO-8601 UTC)}``.

    Returns None when the file is absent (no ingestion has verified this coordinate yet) - an
    absence, not an error, mirroring ``load_recent_releases_sidecar``'s own documented contract. A
    present but structurally invalid sidecar - not a JSON object, or missing a required key -
    raises ValueError, so serving fails at start rather than answering from a half-read file, the
    same contract ``load_recent_releases_sidecar`` and ``load_manifest_sidecar`` already document.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(
            f"package registry sidecar {path} must contain a JSON object, got {type(data).__name__}"
        )
    missing = [key for key in _REQUIRED_SIDECAR_KEYS if key not in data]
    if missing:
        raise ValueError(f"package registry sidecar {path} is missing required key(s): {missing}")
    return data


def _url_exists(url: str) -> bool:
    """GET *url* and classify by HTTP outcome alone: a response with no error means the resource
    exists (the registries this module targets answer 200 for a real coordinate), a 404 means it
    does not. Any other outcome - a different HTTP status, a connection failure, a timeout - is a
    genuine transport/registry failure, never a confirmed non-existence, so it propagates rather
    than being guessed at as either True or False (see module docstring).
    """
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS):
            return True
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        raise


def check_pypi(coordinate: str) -> bool:
    """PyPI JSON API - validated live during this session's own investigation."""
    return _url_exists(f"https://pypi.org/pypi/{coordinate}/json")


def check_npm(coordinate: str) -> bool:
    """npm registry API. A scoped package's own '/' (e.g. '@scope/name') must be percent-encoded
    as %2f or the registry treats it as a path separator instead of part of the package name.
    """
    encoded = coordinate.replace("/", "%2f")
    return _url_exists(f"https://registry.npmjs.org/{encoded}")


def _cargo_sparse_index_path(name: str) -> str:
    """crates.io's own documented sparse-index sharding, lowercased: 1 char -> '1/<name>'; 2 chars
    -> '2/<name>'; 3 chars -> '3/<first-char>/<name>'; 4+ chars ->
    '<first-two>/<next-two>/<name>'.
    """
    name = name.lower()
    length = len(name)
    if length == 1:
        return f"1/{name}"
    if length == 2:
        return f"2/{name}"
    if length == 3:
        return f"3/{name[0]}/{name}"
    return f"{name[0:2]}/{name[2:4]}/{name}"


def check_cargo(coordinate: str) -> bool:
    """index.crates.io's sparse index - NOT crates.io's own API host (crates.io/api/...), which
    was confirmed unreachable from this environment during this session's own investigation.
    """
    return _url_exists(f"https://index.crates.io/{_cargo_sparse_index_path(coordinate)}")


def check_maven(coordinate: str) -> bool:
    """repo1.maven.org's raw Maven repository layout - NOT search.maven.org's own API host, which
    was confirmed unreachable from this environment during this session's own investigation.
    *coordinate* is "groupId:artifactId"; existence is decided by the directory GET alone.
    """
    group_id, separator, artifact_id = coordinate.partition(":")
    if not separator or not group_id or not artifact_id:
        raise ValueError(f"maven coordinate {coordinate!r} must be 'groupId:artifactId'")
    group_path = group_id.replace(".", "/")
    return _url_exists(f"https://repo1.maven.org/maven2/{group_path}/{artifact_id}/")


def check_nuget(coordinate: str) -> bool:
    """NuGet's v3 flat container API."""
    return _url_exists(f"https://api.nuget.org/v3-flatcontainer/{coordinate.lower()}/index.json")


def check_go(coordinate: str) -> bool:
    """The Go module proxy's '@latest'. *coordinate* is a module path (e.g.
    'github.com/owner/repo'). A 200 response with a non-empty body means the module resolves -
    even via a pseudo-version with no tagged release, since `go get` without a version constraint
    genuinely works against that history. A 404, or a 200 with an empty body, means it does not;
    anything else (a different status, a transport failure) propagates rather than being guessed
    at, the same discipline ``_url_exists`` applies for every other ecosystem.
    """
    url = f"https://proxy.golang.org/{coordinate.lower()}/@latest"
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        raise
    return len(body) > 0


_GITHUB_API_ROOT = "https://api.github.com"
_GITHUB_HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "foss-mcp-infra"}


def go_latest_version(coordinate: str) -> str | None:
    """The Go module proxy's '@latest' resolved to its real published version string (e.g.
    'v0.9.0', or a pseudo-version like 'v0.0.0-20260101000000-abcdef123456' when there is no
    tagged release) - the same endpoint ``check_go`` already calls, but parsing the JSON body's
    own ``"Version"`` field instead of merely checking its length.

    Returns None on a 404 (the module has never been published to the proxy). Any other failure -
    a different HTTP status, a transport failure, or a 200 whose body is not the expected JSON
    shape - propagates rather than being guessed at, the same discipline every checker in this
    module already follows.
    """
    url = f"https://proxy.golang.org/{coordinate.lower()}/@latest"
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    return json.loads(body)["Version"]


def resolve_tag_commit(repository: str, ref: str) -> str | None:
    """Resolve a GitHub tag OR branch name *ref* on *repository* ("owner/name") to the real commit
    SHA it currently points at, via ``GET /repos/{repository}/commits/{ref}`` - this single
    endpoint resolves a lightweight tag, an annotated tag, or a branch name directly to a commit,
    unlike the lower-level ``git/refs/tags`` endpoint, which needs an extra indirection step for an
    annotated tag.

    Reuses ``foss_mcp.extraction.github_http``'s existing GitHub-calling plumbing
    (``with_auth`` for an optional token, ``urlopen_with_backoff`` for bounded rate-limit
    tolerance) rather than reimplementing GitHub-API calling here.

    Returns None on a 404 (*ref* does not exist on *repository*). Any other failure propagates.
    """
    url = f"{_GITHUB_API_ROOT}/repos/{repository}/commits/{ref}"
    request = urllib.request.Request(url, headers=with_auth(_GITHUB_HEADERS), method="GET")
    try:
        with urlopen_with_backoff(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    return json.loads(body)["sha"]


def go_published_commit(coordinate: str, repository: str) -> str | None:
    """The real commit SHA a fresh, unconstrained ``go get`` of *coordinate* would fetch RIGHT NOW
    - composing ``go_latest_version`` (what version the module proxy actually serves) with
    ``resolve_tag_commit`` (what commit that version's own git tag actually points at on
    *repository*). Go module version strings and their corresponding git tags are the same string
    in the overwhelming common case, so the resolved version is passed to ``resolve_tag_commit``
    as *ref* unchanged - no transform is applied.

    Returns None when *coordinate* has never been published (``go_latest_version`` returns None),
    or when the resolved version string is not actually a ref that exists on *repository*
    (``resolve_tag_commit`` returns None - e.g. the version/tag convention does not hold for this
    particular repository). Any transport failure from either step propagates unchanged.
    """
    version = go_latest_version(coordinate)
    if version is None:
        return None
    return resolve_tag_commit(repository, version)


_CHECKERS: dict[str, Callable[[str], bool]] = {
    "pypi": check_pypi,
    "npm": check_npm,
    "cargo": check_cargo,
    "maven": check_maven,
    "nuget": check_nuget,
    "go": check_go,
}


def _sidecar_is_fresh(output: Path) -> bool:
    """True only when *output* exists, is well-formed JSON holding an object, and its own mtime
    is less than ``_SIDECAR_FRESHNESS_SECONDS`` old. Anything else - absent, unreadable,
    malformed, or merely stale - is never "fresh", so ``main`` always falls back to checking live;
    this never raises, so a malformed existing file cannot block a re-run.
    """
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
    return isinstance(data, dict)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--ecosystem", required=True, choices=sorted(_CHECKERS))
    parser.add_argument("--coordinate", required=True)
    parser.add_argument("--output", type=Path, required=True, help="path to write the JSON sidecar")
    args = parser.parse_args()

    # Validated the same way the sidecar name itself is - a bad --family/--platform must fail
    # loudly here rather than silently producing a sidecar nothing can ever look up by name.
    verify_package_registry_sidecar_name(args.family, args.platform)

    if _sidecar_is_fresh(args.output):
        print(
            f"skipped verification for {args.coordinate} ({args.ecosystem}) -> {args.output}: "
            f"existing sidecar is less than {_SIDECAR_FRESHNESS_SECONDS} seconds old"
        )
        return

    checker = _CHECKERS[args.ecosystem]
    # Deliberately NOT wrapped in try/except: a genuine transport/registry failure must propagate
    # rather than being written as a false "verified: false" (see module docstring). The caller -
    # a future ingestion step, not built in this card - decides how to tolerate that failure,
    # exactly as fetch_recent_releases.py's own caller already tolerates its live-fetch failures.
    verified = checker(args.coordinate)

    sidecar = {
        "ecosystem": args.ecosystem,
        "coordinate": args.coordinate,
        "verified": verified,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    args.output.write_text(json.dumps(sidecar), encoding="utf-8")
    print(
        f"wrote verification result for {args.coordinate} ({args.ecosystem}) -> {args.output}: "
        f"verified={verified}"
    )


if __name__ == "__main__":
    main()
