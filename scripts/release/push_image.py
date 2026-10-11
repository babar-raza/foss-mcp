#!/usr/bin/env python3
"""Tag a locally built image for the operator's internal registry, push it, capture the digest (TC-347 / REQ-G3-003).

Usage:
    python scripts/release/push_image.py --image NAME:TAG --registry HOST[:PORT][/PATH] [--dry-run] [--record FILE]

Steps, stopping with a non-zero exit at the first failure:
  1. Refuse unless --image names a local image with an explicit tag that is not `latest`, and a registry is
     named, by --registry or by the environment variable FOSS_MCP_REGISTRY, and is a plain HOST[:PORT][/PATH].
     Both refusals exit 2 and run nothing.
  2. Confirm the local image exists (`docker image inspect`).
  3. `docker tag <image> <registry>/<name>:<tag>`, then `docker push <registry>/<name>:<tag>`.
  4. Parse the `digest: sha256:<64 hex> size: <n>` line from the push output. A push whose output carries no
     digest is not a success and exits non-zero.
  5. Print one JSON object with exactly the keys image, pushed_reference, digest, local_image_id and timestamp
     (the shape `gatectl release-record --push-result` reads) and, with --record, write the same object to
     that file.

The registry host is the operator's to supply. This tool never handles a credential: the operator authenticates
to the registry themselves before running it, it reads no Docker client configuration, and it prints no
environment variable. With --dry-run it prints the tag and push commands and runs nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REGISTRY_ENV = "FOSS_MCP_REGISTRY"
MAX_REGISTRY_LENGTH = 255
FAILURE_TAIL_LINES = 20

# Registry grammar: lowercase DNS host labels, an optional :port, optional path segments. No scheme, no spaces
# and no user:pass@ prefix can match. A path segment starts and ends alphanumeric, so `.` and `..` are refused.
_LABEL = r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?"
_SEGMENT = r"[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?"
REGISTRY_PATTERN = re.compile(rf"{_LABEL}(?:\.{_LABEL})*(?::(?P<port>[0-9]{{1,5}}))?(?:/{_SEGMENT})*")

# A local image reference: NAME:TAG. The name has no host or port, so the only colon is the tag separator.
_NAME_COMPONENT = r"[a-z0-9]+(?:[._-]+[a-z0-9]+)*"
IMAGE_PATTERN = re.compile(
    rf"(?P<name>{_NAME_COMPONENT}(?:/{_NAME_COMPONENT})*):(?P<tag>[A-Za-z0-9_][A-Za-z0-9_.-]{{0,127}})"
)

DIGEST_PATTERN = re.compile(r"digest:\s*(sha256:[0-9a-f]{64})\s+size:\s*([0-9]+)")
IMAGE_ID_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
RESULT_KEYS = ("image", "pushed_reference", "digest", "local_image_id", "timestamp")


def fail(message: str, code: int = 1) -> None:
    print(f"push_image: {message}", file=sys.stderr)
    sys.exit(code)


def resolve_registry(flag_value: str | None) -> str:
    """The registry string from --registry, else from the environment, else exit 2. Never echoes the value."""
    registry = flag_value if flag_value is not None else os.environ.get(REGISTRY_ENV, "")
    if not registry:
        fail(
            f"no registry named. Give --registry HOST[:PORT][/PATH] or set {REGISTRY_ENV}. "
            "Nothing was tagged or pushed.",
            2,
        )
    return registry


def validate_registry(registry: str) -> str:
    """Return the registry unchanged if it is a plain HOST[:PORT][/PATH], else exit 2 without echoing it.

    The value is never printed back: it may have come from a mistyped command line or the environment, and a
    value such as user:pass@host must not reach any output.
    """
    reason = ""
    if len(registry) > MAX_REGISTRY_LENGTH:
        reason = f"longer than {MAX_REGISTRY_LENGTH} characters"
    elif "://" in registry:
        reason = "it must not include a scheme such as https://"
    elif "@" in registry:
        reason = "it must not include credentials"
    elif any(ch.isspace() for ch in registry):
        reason = "it must not contain whitespace"
    else:
        match = REGISTRY_PATTERN.fullmatch(registry)
        if match is None:
            reason = (
                "it must be lowercase host labels, an optional :port and optional path segments "
                "of lowercase letters, digits, '.', '_' and '-', each starting and ending alphanumeric"
            )
        elif match.group("port") is not None and not 1 <= int(match.group("port")) <= 65535:
            reason = "the port must be 1 to 65535"
    if reason:
        fail(f"the registry is not a valid HOST[:PORT][/PATH]: {reason}. Nothing was tagged or pushed.", 2)
    return registry


def validate_image(image: str) -> tuple[str, str]:
    """Return (name, tag) for a local NAME:TAG reference, else exit 2. `latest` and a missing tag are refused."""
    shown = image[:60]
    if ":" not in image:
        fail(f"--image {shown!r} has no tag; name an explicit NAME:TAG. Nothing was tagged or pushed.", 2)
    match = IMAGE_PATTERN.fullmatch(image)
    if match is None:
        fail(
            f"--image {shown!r} is not a local NAME:TAG reference (lowercase name, no registry host, no digest). "
            "Nothing was tagged or pushed.",
            2,
        )
    assert match is not None
    name, tag = match.group("name"), match.group("tag")
    if tag.lower() == "latest":
        fail("--image must carry an explicit release tag, never latest. Nothing was tagged or pushed.", 2)
    return name, tag


def inspect_command(image: str) -> list[str]:
    return ["docker", "image", "inspect", "--format", "{{.Id}}", image]


def tag_command(image: str, reference: str) -> list[str]:
    return ["docker", "tag", image, reference]


def push_command(reference: str) -> list[str]:
    return ["docker", "push", reference]


def run_docker(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        fail(f"could not run {cmd[0]}: {type(exc).__name__}; is docker on PATH? Nothing more was run.")
        raise  # unreachable: fail() exits


def output_of(proc: subprocess.CompletedProcess[str]) -> str:
    parts = []
    for stream in (proc.stdout, proc.stderr):
        if isinstance(stream, bytes):
            stream = stream.decode("utf-8", "replace")
        if stream:
            parts.append(stream)
    return "\n".join(parts)


def tail(text: str, lines: int = FAILURE_TAIL_LINES) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def parse_digest(push_output: str) -> str | None:
    """The sha256 digest from the last `digest: sha256:<64 hex> size: <n>` line of the push output, or None."""
    found = DIGEST_PATTERN.findall(push_output)
    if not found:
        return None
    return found[-1][0]


def parse_image_id(inspect_output: str) -> str | None:
    """The `sha256:<64 hex>` image id from `docker image inspect --format {{.Id}}` output, or None."""
    lines = inspect_output.strip().splitlines()
    candidate = lines[0].strip() if lines else ""
    return candidate if IMAGE_ID_PATTERN.fullmatch(candidate) else None


def utc_timestamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_record(path: Path, result: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Tag a local image for the internal registry, push it, and capture the pushed digest.",
    )
    parser.add_argument("--image", required=True, help="local image NAME:TAG (an explicit tag, never latest)")
    parser.add_argument(
        "--registry",
        help=f"registry HOST[:PORT][/PATH], no scheme and no credentials (default: environment {REGISTRY_ENV})",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the tag and push commands and run nothing"
    )
    parser.add_argument("--record", type=Path, help="write the JSON result to this file")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    registry = validate_registry(resolve_registry(args.registry))
    name, tag = validate_image(args.image)
    reference = f"{registry}/{name}:{tag}"

    if args.dry_run:
        print(shlex.join(tag_command(args.image, reference)))
        print(shlex.join(push_command(reference)))
        print("push_image: dry run; nothing was run and nothing was written.", file=sys.stderr)
        return 0

    inspected = run_docker(inspect_command(args.image))
    if inspected.returncode != 0:
        fail(f"local image {args.image} was not found by docker image inspect; build it first.")
    local_image_id = parse_image_id(output_of(inspected))
    if local_image_id is None:
        fail(f"docker image inspect of {args.image} did not return a sha256:<64 hex> image id.")
    assert local_image_id is not None

    print(f"push_image: tagging {args.image} as {reference}", file=sys.stderr, flush=True)
    tagged = run_docker(tag_command(args.image, reference))
    if tagged.returncode != 0:
        fail(f"docker tag failed with exit {tagged.returncode}: {tail(output_of(tagged))}")

    print(f"push_image: pushing {reference}", file=sys.stderr, flush=True)
    pushed = run_docker(push_command(reference))
    push_output = output_of(pushed)
    if pushed.returncode != 0:
        fail(f"docker push of {reference} failed with exit {pushed.returncode}: {tail(push_output)}")
    digest = parse_digest(push_output)
    if digest is None:
        fail(
            f"docker push of {reference} exited 0 but its output carries no 'digest: sha256:<64 hex> size: <n>' "
            "line; a push with no captured digest is not a success."
        )
    assert digest is not None

    result: dict[str, str] = {
        "image": args.image,
        "pushed_reference": reference,
        "digest": digest,
        "local_image_id": local_image_id,
        "timestamp": utc_timestamp(),
    }
    assert tuple(result) == RESULT_KEYS
    if args.record is not None:
        write_record(args.record, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
