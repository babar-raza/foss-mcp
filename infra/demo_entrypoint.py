#!/usr/bin/env python3
"""Entrypoint of the standalone demo image (Dockerfile.serving, ``demo`` stage; TC-350 / REQ-G3-002).

The demo image bakes in every pilot's generation, one directory per scope under
``FOSS_MCP_MANIFESTS_DIR`` (default ``/data/manifests``) named ``<family>__<platform>__<source_kind>``.
The HTTP server (``serve_http.py``) serves exactly ONE identity and refuses to start without
``FOSS_MCP_FAMILY`` and ``FOSS_MCP_PLATFORM``, which a peer holding a bare image cannot know. This
entrypoint closes that gap without ever guessing:

* ``docker run IMAGE``                         prints the usage and the baked pilots to stderr, exits 2;
* ``docker run IMAGE --list``                  prints one line per baked pilot to stdout, exits 0;
* ``docker run -e FOSS_MCP_PILOT=pdf/go IMAGE`` selects the pilot, sets the identity, and replaces this
  process with the HTTP server (``os.execv``), so signals reach the server and it is the container's PID 1.

A pilot is spelled ``family/platform`` or ``family_platform`` (``--pilot VALUE`` wins over the
``FOSS_MCP_PILOT`` environment variable). An unknown pilot, or one whose scope has no readable active
generation, exits 2 and names what is baked. Nothing is ever served under an invented identity.

Standard library only. The only environment values this module reads are ``FOSS_MCP_PILOT`` and
``FOSS_MCP_MANIFESTS_DIR``; the only ones it writes are ``FOSS_MCP_FAMILY``, ``FOSS_MCP_PLATFORM`` and
``FOSS_MCP_SOURCE_KIND``. It never prints, logs or enumerates any other environment value.

Exit codes: 0 ``--list``; 2 usage, unknown pilot, or unreadable generation; 1 the exec itself failed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import MutableMapping, Sequence
from dataclasses import dataclass
from pathlib import Path

PILOT_ENV = "FOSS_MCP_PILOT"
MANIFESTS_DIR_ENV = "FOSS_MCP_MANIFESTS_DIR"
DEFAULT_MANIFESTS_DIR = "/data/manifests"
FAMILY_ENV = "FOSS_MCP_FAMILY"
PLATFORM_ENV = "FOSS_MCP_PLATFORM"
SOURCE_KIND_ENV = "FOSS_MCP_SOURCE_KIND"

EXIT_FAILED = 1
EXIT_USAGE = 2

ACTIVE_POINTER = "active_pointer.json"
SCOPE_SEPARATOR = "__"
GENERATION_ID_SEPARATOR = "::"
# The three per-pilot sidecars the live chart serves from the manifests root. The names are the same
# ones infra/fetch_product_reference.py, infra/fetch_recent_releases.py and
# infra/verify_package_registry.py write; this module is stdlib-only, so it repeats them (a test pins
# that the two agree).
SIDECAR_PREFIXES = ("product_reference", "recent_releases", "package_registry")

_PART = re.compile(r"[a-z0-9]+")
_SOURCE_KIND = re.compile(r"[a-z0-9]+(?:_[a-z0-9]+)*")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

# The program this process becomes once a pilot is selected. Keep it the LAST place this file names the
# HTTP server: every other mention above uses prose, and everything below uses this constant.
SERVER_SCRIPT = "/app/infra/serve_http.py"


class EntrypointError(Exception):
    """A pilot cannot be selected; the message is printed to stderr and the exit code is 2."""


@dataclass(frozen=True)
class Pilot:
    """One baked scope directory."""

    family: str
    platform: str
    source_kind: str
    scope_dir: Path

    @property
    def name(self) -> str:
        return f"{self.family}/{self.platform}"


def parse_pilot(value: str) -> tuple[str, str]:
    """``family/platform`` or ``family_platform`` to ``(family, platform)``.

    Family and platform are lowercase letters and digits only (the same rule the sidecar names use), so
    an underscore can only be the separator.
    """
    text = value.strip()
    parts = text.split("/") if "/" in text else text.split("_")
    if len(parts) != 2 or not all(_PART.fullmatch(part) for part in parts):
        raise EntrypointError(
            f"invalid pilot {value!r}: expected family/platform or family_platform "
            "(lowercase letters and digits)"
        )
    return parts[0], parts[1]


def discover_pilots(manifests_dir: Path) -> list[Pilot]:
    """Every scope directory under *manifests_dir*, sorted by directory name. Files at the root (the
    sidecars) and directories that are not named ``<family>__<platform>__<source_kind>`` are ignored."""
    if not manifests_dir.is_dir():
        return []
    found: list[Pilot] = []
    for entry in sorted(manifests_dir.iterdir(), key=lambda path: path.name):
        if not entry.is_dir():
            continue
        parts = entry.name.split(SCOPE_SEPARATOR)
        if len(parts) != 3:
            continue
        family, platform, source_kind = parts
        if not (
            _PART.fullmatch(family) and _PART.fullmatch(platform) and _SOURCE_KIND.fullmatch(source_kind)
        ):
            continue
        found.append(Pilot(family, platform, source_kind, entry))
    return found


def _load_json_object(path: Path, label: str) -> dict[str, object]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise EntrypointError(f"{label} is unreadable ({type(exc).__name__})") from exc
    if not isinstance(data, dict):
        raise EntrypointError(f"{label} is not a JSON object")
    return data


def read_lexical_document_count(scope_dir: Path) -> int:
    """The number of lexical documents in the scope's active generation.

    Raises ``EntrypointError`` when the pointer, the generation file it names, or the lexical index is
    missing or malformed. The generation file name is derived exactly as the manifest store does: the
    version is everything after the LAST ``::`` of the generation id.
    """
    scope = scope_dir.name
    pointer = _load_json_object(scope_dir / ACTIVE_POINTER, f"{scope}: {ACTIVE_POINTER}")
    generation_id = pointer.get("generation_id")
    if not isinstance(generation_id, str) or not generation_id:
        raise EntrypointError(f"{scope}: {ACTIVE_POINTER} names no generation")
    version = generation_id.rsplit(GENERATION_ID_SEPARATOR, 1)[-1]
    if not _VERSION.fullmatch(version):
        raise EntrypointError(f"{scope}: {ACTIVE_POINTER} names a malformed generation version")
    generation_name = f"generation__{version}.json"
    generation = _load_json_object(scope_dir / generation_name, f"{scope}: {generation_name}")
    payload = generation.get("payload")
    lexical = payload.get("lexical_index") if isinstance(payload, dict) else None
    documents = lexical.get("documents") if isinstance(lexical, dict) else None
    if not isinstance(documents, dict):
        raise EntrypointError(f"{scope}: {generation_name} has no lexical index documents")
    return len(documents)


def sidecar_presence(manifests_dir: Path, family: str, platform: str) -> dict[str, bool]:
    """For each of the three sidecars, whether its file exists in the manifests root."""
    return {
        prefix: (manifests_dir / f"{prefix}_{family}_{platform}.json").is_file()
        for prefix in SIDECAR_PREFIXES
    }


def format_pilot_line(manifests_dir: Path, pilot: Pilot) -> str:
    """One ``--list`` line: ``family/platform source_kind=K documents=N product_reference=yes|no ...``."""
    try:
        documents = str(read_lexical_document_count(pilot.scope_dir))
    except EntrypointError:
        documents = "unreadable"
    sidecars = sidecar_presence(manifests_dir, pilot.family, pilot.platform)
    flags = " ".join(f"{name}={'yes' if present else 'no'}" for name, present in sidecars.items())
    return f"{pilot.name} source_kind={pilot.source_kind} documents={documents} {flags}"


def select_pilot(pilots: Sequence[Pilot], family: str, platform: str) -> Pilot:
    matches = [pilot for pilot in pilots if (pilot.family, pilot.platform) == (family, platform)]
    baked = ", ".join(pilot.name for pilot in pilots) or "none"
    if not matches:
        raise EntrypointError(f"unknown pilot '{family}/{platform}'; baked pilots: {baked}")
    if len(matches) > 1:
        kinds = ", ".join(pilot.source_kind for pilot in matches)
        raise EntrypointError(
            f"pilot '{family}/{platform}' is ambiguous: baked under several source kinds ({kinds}); "
            "refusing to pick one"
        )
    return matches[0]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="demo_entrypoint",
        description="Select one baked pilot and serve it over HTTP on port 8080.",
        epilog=f"The pilot may also be given as the {PILOT_ENV} environment variable.",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--pilot", metavar="FAMILY/PLATFORM", help="the pilot to serve (family/platform or family_platform)"
    )
    parser.add_argument(
        "--list", action="store_true", help="print the baked pilots, one per line, and exit 0"
    )
    return parser


def _print_no_pilot(parser: argparse.ArgumentParser, manifests_dir: Path, pilots: Sequence[Pilot]) -> None:
    example = pilots[0].name if pilots else "<family>/<platform>"
    lines = [
        "demo_entrypoint: no pilot selected. This image holds several pilots and will not guess which one",
        "to serve.",
        parser.format_usage().rstrip(),
        f"Select one with {PILOT_ENV}=<family>/<platform> or --pilot, for example:",
        f"  docker run --rm -p 8080:8080 -e {PILOT_ENV}={example} IMAGE",
    ]
    if pilots:
        lines.append(f"Baked pilots ({len(pilots)}):")
        lines.extend(f"  {format_pilot_line(manifests_dir, pilot)}" for pilot in pilots)
    else:
        lines.append(f"No pilots are baked into this image (nothing under {manifests_dir}).")
    print("\n".join(lines), file=sys.stderr)


def main(argv: Sequence[str] | None = None, environ: MutableMapping[str, str] | None = None) -> int:
    env = os.environ if environ is None else environ
    parser = _build_parser()
    args = parser.parse_args(argv)  # a malformed command line exits 2 with usage on stderr

    manifests_dir = Path(env.get(MANIFESTS_DIR_ENV) or DEFAULT_MANIFESTS_DIR)
    pilots = discover_pilots(manifests_dir)

    if args.list:
        for pilot in pilots:
            print(format_pilot_line(manifests_dir, pilot))
        if not pilots:
            print(
                f"demo_entrypoint: no pilots are baked into this image (nothing under {manifests_dir})",
                file=sys.stderr,
            )
        return 0

    requested = args.pilot if args.pilot is not None else env.get(PILOT_ENV, "")
    if not requested.strip():
        _print_no_pilot(parser, manifests_dir, pilots)
        return EXIT_USAGE

    try:
        family, platform = parse_pilot(requested)
        pilot = select_pilot(pilots, family, platform)
        read_lexical_document_count(pilot.scope_dir)
    except EntrypointError as exc:
        print(f"demo_entrypoint: {exc}", file=sys.stderr)
        return EXIT_USAGE

    env[FAMILY_ENV] = pilot.family
    env[PLATFORM_ENV] = pilot.platform
    env[SOURCE_KIND_ENV] = pilot.source_kind
    print(f"demo_entrypoint: serving {pilot.name} ({pilot.source_kind}) on port 8080", file=sys.stderr)
    sys.stderr.flush()
    try:
        os.execv(sys.executable, [sys.executable, SERVER_SCRIPT])
    except OSError as exc:
        print(f"demo_entrypoint: cannot start the server ({type(exc).__name__})", file=sys.stderr)
        return EXIT_FAILED
    return 0


if __name__ == "__main__":
    sys.exit(main())
