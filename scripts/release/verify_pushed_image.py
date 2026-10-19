#!/usr/bin/env python3
"""Pull a pushed image back from the registry by digest and prove it is the image that passed the gate (TC-569).

Usage:
    python scripts/release/verify_pushed_image.py --push-result FILE --record FILE [--report-dir DIR] [--dry-run]

Requirement met here: REQ-G3-009 (row RD3-161) and REQ-G3-003. scripts/release/push_image.py tags, pushes and
captures the digest, then stops: nothing pulls the image back, so a successful push proves nothing about what a
peer will pull. This script closes that gap.

Inputs:
  --push-result  the JSON push_image.py writes (image, pushed_reference, digest, local_image_id, timestamp).
  --record       the release record `gatectl release-record` writes (image.image_id, image.source_commit).

Steps, stopping at the first failure (later steps are recorded as skipped):
  1. pull        `docker pull <name>@<digest>`: the reference is built from the pushed reference WITHOUT its tag
                 plus the recorded digest, never the mutable tag a registry operator can move.
  2. inspect     `docker image inspect` of that digest reference.
  3. manifest    the recorded digest must be among the pulled image's repository digests.
  4. image_id    the pulled image id must equal the release record's image id (and the push result's local id).
  5. revision    the org.opencontainers.image.revision label must exist and equal the record's source commit.
  6. gate        scripts/release/image_workflow_gate.py, every pilot, against the digest reference.
  7. adversarial scripts/release/adversarial_suite.py against the digest reference.

Every failure has its own message and exit code 1. A request that cannot be run as asked (missing keys, a digest
that is not sha256:<64 hex>, a record that disagrees with the push result) exits 2 and runs nothing. Exit 0 only
when all seven steps passed. A report with every step's result is written to --report-dir; its ``verified`` field
is true only on exit 0.

The docker side is injected (``Deps``), so the whole flow is unit-tested without docker or a registry. This tool
handles no secret: the operator authenticates to the registry themselves beforehand, and this script passes only
image references and file paths to the commands it runs.

The two suites accept only NAME:TAG. The default gate runner therefore gives the digest-pulled image a throw-away
local alias, confirms the alias resolves to the pulled image id, runs the suite on the alias and removes the
alias; the reference the flow itself handles, reports and hands to ``Deps.run_gate`` is always the digest form.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# When True the image is pulled and gated only by `<pushed reference without tag>@<digest>`; a push result
# that cannot yield one is refused. A mutable tag proves nothing about the bytes a peer receives.
REQUIRE_DIGEST_REFERENCE = True

REVISION_LABEL = "org.opencontainers.image.revision"
SCHEMA_VERSION = 1
REPORT_NAME = "verify_pushed_image.json"
DEFAULT_REPORT_DIR = Path(tempfile.gettempdir()) / "foss-mcp-verify-pushed-image"
EXIT_FAILED = 1
EXIT_REFUSED = 2
FAILURE_TAIL_LINES = 12

DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
PUSH_RESULT_KEYS = ("image", "pushed_reference", "digest", "local_image_id")

STEP_NAMES = ("pull", "inspect", "manifest", "image_id", "revision", "gate", "adversarial")

FAILURE_MESSAGES: dict[str, str] = {
    "PULL_FAILED": "docker could not pull the pushed image back by digest",
    "INSPECT_FAILED": "docker could not describe the image that was pulled",
    "DIGEST_MISMATCH": "the pulled manifest digest differs from the digest recorded at push",
    "IMAGE_ID_MISMATCH": "the pulled image id differs from the recorded local image id",
    "LABEL_MISSING": f"the pulled image carries no {REVISION_LABEL} label",
    "REVISION_MISMATCH": f"the pulled image's {REVISION_LABEL} label differs from the release record's source commit",
    "GATE_FAILED": "the workflow gate failed against the pulled image",
    "ADVERSARIAL_FAILED": "the adversarial suite failed against the pulled image",
}


class Refused(Exception):
    """The request cannot be run as asked. Nothing was pulled; the exit code is 2."""


# ---------------------------------------------------------------------------------------------------------------
# Injection
# ---------------------------------------------------------------------------------------------------------------


@dataclass
class Deps:
    """The four things the flow does outside this process. Each takes the digest-form reference.

    docker_pull(ref) -> (exit code, output)
    docker_inspect(ref) -> {"image_id": str, "repo_digests": [str], "labels": {str: str}} or None
    run_gate(ref) -> exit code of the 32-of-32 workflow gate
    run_adversarial(ref) -> exit code of the adversarial suite
    """

    docker_pull: Callable[[str], tuple[int, str]]
    docker_inspect: Callable[[str], Mapping[str, Any] | None]
    run_gate: Callable[[str], int]
    run_adversarial: Callable[[str], int]


# ---------------------------------------------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------------------------------------------


@dataclass
class Step:
    name: str
    status: str = "skipped"  # pass, fail or skipped
    message: str = ""


@dataclass
class Report:
    reference: str
    digest: str
    image: str
    expected_image_id: str
    expected_revision: str
    steps: list[Step] = field(default_factory=lambda: [Step(name) for name in STEP_NAMES])
    failure: str | None = None
    generated_at: str = ""

    @property
    def verified(self) -> bool:
        return self.failure is None and all(step.status == "pass" for step in self.steps)

    def step(self, name: str) -> Step:
        return next(step for step in self.steps if step.name == name)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at": self.generated_at,
            "image": self.image,
            "reference": self.reference,
            "digest": self.digest,
            "expected_image_id": self.expected_image_id,
            "expected_revision": self.expected_revision,
            "verified": self.verified,
            "verdict": "PASS" if self.verified else "FAIL",
            "failure": self.failure,
            "steps": [{"name": s.name, "status": s.status, "message": s.message} for s in self.steps],
        }


# ---------------------------------------------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------------------------------------------


def digest_reference(pushed_reference: str, digest: str) -> str:
    """``<repository>@<digest>``: the pushed reference with any tag or digest removed, plus the recorded digest."""
    repository = pushed_reference.split("@", 1)[0]
    last = repository.rsplit("/", 1)[-1]
    if ":" in last:
        repository = repository[: len(repository) - len(last)] + last.split(":", 1)[0]
    return f"{repository}@{digest}"


def gate_reference(push_result: Mapping[str, Any]) -> str:
    """The reference the image is pulled and gated by. A digest form unless the switch above is turned off."""
    pushed = str(push_result["pushed_reference"])
    digest = str(push_result["digest"])
    if REQUIRE_DIGEST_REFERENCE:
        return digest_reference(pushed, digest)
    return pushed


def _record_field(record: Mapping[str, Any], key: str) -> str:
    image = record.get("image")
    value = image.get(key) if isinstance(image, Mapping) else None
    if not isinstance(value, str):
        raise Refused(f"the release record has no image.{key}. Nothing was pulled.")
    return value


def check_inputs(push_result: Mapping[str, Any], record: Mapping[str, Any]) -> tuple[str, str]:
    """(expected image id, expected source commit), or a Refused. Runs before anything is pulled."""
    for key in PUSH_RESULT_KEYS:
        if not isinstance(push_result.get(key), str) or not push_result[key]:
            raise Refused(f"the push result has no {key}. Nothing was pulled.")
    if DIGEST_PATTERN.fullmatch(push_result["digest"]) is None:
        raise Refused(
            "the push result digest is not sha256:<64 hex>; a tag alone is not verified. Nothing was pulled."
        )
    image_id = _record_field(record, "image_id")
    commit = _record_field(record, "source_commit")
    if DIGEST_PATTERN.fullmatch(image_id) is None:
        raise Refused("the release record image_id is not sha256:<64 hex>. Nothing was pulled.")
    if COMMIT_PATTERN.fullmatch(commit) is None:
        raise Refused("the release record source_commit is not a 40-hex commit. Nothing was pulled.")
    if push_result["local_image_id"] != image_id:
        raise Refused(
            "the push result's local_image_id differs from the release record's image_id. Nothing was pulled."
        )
    registry = record.get("registry")
    if isinstance(registry, Mapping) and registry.get("digest") != push_result["digest"]:
        raise Refused(
            "the release record's registry digest differs from the push result's. Nothing was pulled."
        )
    if "@" not in gate_reference(push_result) and REQUIRE_DIGEST_REFERENCE:
        raise Refused("no digest reference can be built from the push result. Nothing was pulled.")
    return image_id, commit


# ---------------------------------------------------------------------------------------------------------------
# The flow
# ---------------------------------------------------------------------------------------------------------------


def _tail(text: str, lines: int = FAILURE_TAIL_LINES) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def verify(push_result: Mapping[str, Any], record: Mapping[str, Any], deps: Deps) -> Report:
    """Run the seven steps against the registry copy. Raises Refused for an unusable request, else returns a Report."""
    expected_id, expected_commit = check_inputs(push_result, record)
    reference = gate_reference(push_result)
    report = Report(
        reference=reference,
        digest=str(push_result["digest"]),
        image=str(push_result["image"]),
        expected_image_id=expected_id,
        expected_revision=expected_commit,
        generated_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

    def fail(step: str, code: str, detail: str = "") -> Report:
        report.failure = code
        message = FAILURE_MESSAGES[code] + (f": {detail}" if detail else "")
        report.step(step).status = "fail"
        report.step(step).message = message
        return report

    def passed(step: str, message: str) -> None:
        report.step(step).status = "pass"
        report.step(step).message = message

    rc, output = deps.docker_pull(reference)
    if rc != 0:
        return fail("pull", "PULL_FAILED", f"exit {rc}: {_tail(output)}")
    passed("pull", f"pulled {reference}")

    inspected = deps.docker_inspect(reference)
    if not inspected:
        return fail("inspect", "INSPECT_FAILED")
    passed("inspect", "described the pulled image")

    repo_digests = [str(item).rsplit("@", 1)[-1] for item in inspected.get("repo_digests") or []]
    if report.digest not in repo_digests:
        return fail(
            "manifest",
            "DIGEST_MISMATCH",
            f"recorded {report.digest}, pulled {sorted(repo_digests) or 'none'}",
        )
    passed("manifest", f"manifest digest is {report.digest}")

    pulled_id = str(inspected.get("image_id") or "")
    if pulled_id != expected_id:
        return fail("image_id", "IMAGE_ID_MISMATCH", f"recorded {expected_id}, pulled {pulled_id or 'none'}")
    passed("image_id", f"image id is {pulled_id}")

    labels = inspected.get("labels") or {}
    revision = labels.get(REVISION_LABEL) if isinstance(labels, Mapping) else None
    if not revision:
        return fail("revision", "LABEL_MISSING")
    if revision != expected_commit:
        return fail("revision", "REVISION_MISMATCH", f"record {expected_commit}, image {revision}")
    passed("revision", f"revision label is {revision}")

    if deps.run_gate(reference) != 0:
        return fail("gate", "GATE_FAILED")
    passed("gate", "workflow gate passed on every pilot")

    if deps.run_adversarial(reference) != 0:
        return fail("adversarial", "ADVERSARIAL_FAILED")
    passed("adversarial", "adversarial suite passed")
    return report


# ---------------------------------------------------------------------------------------------------------------
# The real docker side (never reached by a unit test; those inject Deps or a fake ``run``)
# ---------------------------------------------------------------------------------------------------------------

Runner = Callable[..., Any]


def _run(run: Runner, cmd: Sequence[str]) -> tuple[int, str]:
    try:
        proc = run(list(cmd), capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    except OSError as exc:
        return 127, f"could not run {cmd[0]}: {type(exc).__name__}"
    return int(proc.returncode), "\n".join(part for part in (proc.stdout, proc.stderr) if part)


def pull_command(reference: str) -> list[str]:
    return ["docker", "pull", reference]


def inspect_command(reference: str) -> list[str]:
    return ["docker", "image", "inspect", reference]


def real_pull(run: Runner, reference: str) -> tuple[int, str]:
    return _run(run, pull_command(reference))


def real_inspect(run: Runner, reference: str) -> Mapping[str, Any] | None:
    rc, output = _run(run, inspect_command(reference))
    if rc != 0:
        return None
    try:
        data = json.loads(output)[0]
        return {
            "image_id": data["Id"],
            "repo_digests": list(data.get("RepoDigests") or []),
            "labels": dict((data.get("Config") or {}).get("Labels") or {}),
        }
    except (ValueError, KeyError, IndexError, TypeError):
        return None


def local_alias(digest: str) -> str:
    return f"foss-mcp-verify-pushed:{digest.split(':', 1)[-1][:12]}"


def real_suite(run: Runner, reference: str, suite_main: Callable[[list[str]], int], report_dir: Path) -> int:
    """Run one suite (it accepts only NAME:TAG) on a local alias of the digest-pulled image, then drop the alias."""
    alias = local_alias(reference.rsplit("@", 1)[-1])
    rc, _ = _run(run, ["docker", "tag", reference, alias])
    if rc != 0:
        return rc
    try:
        return int(suite_main(["--image", alias, "--report-dir", str(report_dir)]))
    finally:
        _run(run, ["docker", "rmi", alias])


def real_deps(report_dir: Path, run: Runner = subprocess.run) -> Deps:
    release_dir = str(Path(__file__).resolve().parent)
    if release_dir not in sys.path:
        sys.path.insert(0, release_dir)

    def gate_main(argv: list[str]) -> int:
        import image_workflow_gate

        return int(image_workflow_gate.main(argv))

    def adversarial_main(argv: list[str]) -> int:
        import adversarial_suite

        return int(adversarial_suite.main(argv))

    return Deps(
        docker_pull=lambda ref: real_pull(run, ref),
        docker_inspect=lambda ref: real_inspect(run, ref),
        run_gate=lambda ref: real_suite(run, ref, gate_main, report_dir / "gate"),
        run_adversarial=lambda ref: real_suite(run, ref, adversarial_main, report_dir / "adversarial"),
    )


# ---------------------------------------------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------------------------------------------


def _load_json(path: Path, what: str) -> Mapping[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Refused(
            f"cannot read the {what} {path.name!r} as JSON ({type(exc).__name__}). Nothing was pulled."
        ) from exc
    if not isinstance(data, dict):
        raise Refused(f"the {what} {path.name!r} is not a JSON object. Nothing was pulled.")
    return data


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pull a pushed image back by digest and prove it is the image that passed the gate.",
    )
    parser.add_argument("--push-result", type=Path, required=True, help="the JSON push_image.py wrote")
    parser.add_argument("--record", type=Path, required=True, help="the release record gatectl wrote")
    parser.add_argument(
        "--report-dir", type=Path, default=DEFAULT_REPORT_DIR, help=f"where {REPORT_NAME} is written"
    )
    parser.add_argument("--dry-run", action="store_true", help="print the commands and run nothing")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, deps: Deps | None = None) -> int:
    args = parse_args(argv)
    try:
        push_result = _load_json(args.push_result, "push result")
        record = _load_json(args.record, "release record")
        check_inputs(push_result, record)
        reference = gate_reference(push_result)
        if args.dry_run:
            print(" ".join(pull_command(reference)))
            print(
                f"verify_pushed_image: dry run; would gate and attack {reference}; nothing was run.",
                file=sys.stderr,
            )
            return 0
        report = verify(push_result, record, deps or real_deps(args.report_dir))
    except Refused as exc:
        print(f"verify_pushed_image: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    args.report_dir.mkdir(parents=True, exist_ok=True)
    (args.report_dir / REPORT_NAME).write_text(
        json.dumps(report.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    for step in report.steps:
        print(f"verify_pushed_image: {step.name}: {step.status} {step.message}".rstrip(), file=sys.stderr)
    if not report.verified:
        print(
            f"verify_pushed_image: FAILED ({report.failure}): {FAILURE_MESSAGES[str(report.failure)]}",
            file=sys.stderr,
        )
        return EXIT_FAILED
    print(f"verify_pushed_image: VERIFIED {report.reference}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
