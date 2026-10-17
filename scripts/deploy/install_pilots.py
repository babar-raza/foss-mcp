#!/usr/bin/env python3
"""Install the thirty-two pilot releases of the foss-mcp chart (G2/TC-223).

Usage:
    python scripts/deploy/install_pilots.py --namespace foss-mcp --image-tag rev-1234567

For each selected pilot it first removes that release's own ingestion Job (job_delete_command, scoped
by label, --ignore-not-found so a first install with no existing Job is unaffected -- a Job's pod
template is immutable and helm cannot patch it), writes a minimal values file (see pilots.py), then runs
    helm upgrade --install <release> <chart> -n <namespace> --set image.tag=<tag> -f <values> --wait --timeout <t>
with at most --parallel installs running at once. The Job removal's exit code never gates the helm step;
a real failure there is recorded in job_delete_rc and never blocks the install. It creates the namespace
if it is absent, prints a result table, writes install-results.json into --values-dir, and exits non-zero
if any install failed. A failed install prints the pod states and the last 20 lines of that release's
ingestion Job log.

This tool never removes a helm release: the only resource it ever removes is that release's own scoped
ingestion Job, immediately before each upgrade.
--dry-run prints each pilot's Job removal command and helm command and runs neither, and writes nothing.

--wait-for-jobs (G3/TC-512, off by default, so the default helm command and output are unchanged) is the
recommended flag for any install that must not report a stale pilot as installed. `helm --wait` does not
wait for Jobs, so a pilot whose ingestion FAILED while its serving pod kept running the previous
generation would otherwise be reported `installed`. With the flag, helm gets `--wait-for-jobs` right
after `--wait`, and after a successful helm exit the tool reads that release's ingestion Jobs
(classify_job_status) and records succeeded, failed, running, missing or unknown in the results. A pilot
whose helm exit was 0 but whose ingestion failed prints `INGESTION FAILED`, prints the failure
diagnostics, counts as not installed and makes the exit status 1; running or unknown prints
`installed (ingestion unverified)` and does not fail.

Two optional flags name Secrets the operator created (G2/TC-339): --embedding-gateway-secret NAME and
--github-token-secret NAME. Each appends exactly one `--set-string <chart key>=NAME` pair to every helm
command and nothing when absent, so the default command is unchanged. It is --set-string, never --set:
plain --set would coerce a valid Secret name such as `null`, `true` or `123` into a non-string. A NAME is checked against the Kubernetes
DNS-1123 subdomain grammar before any command is built; an invalid one exits 2 without running anything.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pilots  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CHART = REPO_ROOT / "infra" / "helm" / "foss-mcp"
DEFAULT_VALUES_DIR = Path(tempfile.gettempdir()) / "foss-mcp-pilot-install"
LOG_TAIL_LINES = 20
INSTANCE_LABEL = "app.kubernetes.io/instance"
COMPONENT_LABEL = "app.kubernetes.io/component"
RESULTS_FILE = "install-results.json"

# Kubernetes DNS-1123 subdomain: the grammar a Secret name must satisfy. Checked before a name is placed in a
# helm --set-string argument, so a comma, equals sign or backslash can never smuggle a second value into it.
SECRET_NAME_PATTERN = re.compile(r"^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$")
SECRET_NAME_MAX_LENGTH = 253
# (CLI flag, argparse dest, chart values key it sets). The chart's own key names stay the defaults.
SECRET_FLAGS = (
    ("--embedding-gateway-secret", "embedding_gateway_secret", "embeddingGateway.existingSecret"),
    ("--github-token-secret", "github_token_secret", "ingestion.githubToken.existingSecret"),
)


@dataclass
class InstallResult:
    pilot: str
    release: str
    values_file: str
    command: list[str]
    rc: int
    job_delete_rc: int
    duration_seconds: float
    error: str = ""
    # G3/TC-512: succeeded, failed, running, missing or unknown; empty when --wait-for-jobs was not given.
    ingestion: str = ""


def secret_set_pairs(args: argparse.Namespace) -> list[str]:
    """The `key=NAME` helm --set-string pairs for the Secret flags that were given, in SECRET_FLAGS order.

    Raises ValueError, naming the flag, if a given NAME is not a valid DNS-1123 subdomain. An empty NAME
    means the flag was not given and contributes nothing.
    """
    pairs: list[str] = []
    for flag, dest, key in SECRET_FLAGS:
        name = getattr(args, dest)
        if not name:
            continue
        if len(name) > SECRET_NAME_MAX_LENGTH or SECRET_NAME_PATTERN.fullmatch(name) is None:
            raise ValueError(
                f"{flag}: {name[:60]!r} is not a valid Kubernetes Secret name "
                f"(lowercase letters, digits, '-' and '.', starting and ending alphanumeric, "
                f"at most {SECRET_NAME_MAX_LENGTH} characters)"
            )
        pairs.append(f"{key}={name}")
    return pairs


def helm_install_command(
    release: str,
    chart: Path,
    namespace: str,
    tag: str,
    values_file: Path,
    timeout: str,
    *,
    extra_sets: Sequence[str] = (),
    wait_for_jobs: bool = False,
) -> list[str]:
    cmd = [
        "helm",
        "upgrade",
        "--install",
        release,
        str(chart),
        "-n",
        namespace,
        "--set",
        f"image.tag={tag}",
    ]
    for pair in extra_sets:
        # --set-string, never --set: plain --set turns a name such as null, true or 123 into a non-string.
        cmd += ["--set-string", pair]
    cmd += ["-f", str(values_file), "--wait"]
    if wait_for_jobs:
        cmd.append("--wait-for-jobs")
    cmd += ["--timeout", timeout]
    return cmd


def job_delete_command(namespace: str, release: str) -> list[str]:
    """Remove this release's own ingestion Job, scoped by label, before every helm upgrade.

    A Job's pod template is immutable, so helm upgrade cannot patch one left over from a prior
    install; --ignore-not-found makes a first install (no existing Job) a no-op, never a failure.
    """
    selector = f"{INSTANCE_LABEL}={release},{COMPONENT_LABEL}=ingestion"
    return ["kubectl", "delete", "job", "-n", namespace, "-l", selector, "--ignore-not-found"]


def job_status_command(namespace: str, release: str) -> list[str]:
    """List this release's ingestion Jobs as JSON, scoped by the same labels job_delete_command uses."""
    selector = f"{INSTANCE_LABEL}={release},{COMPONENT_LABEL}=ingestion"
    return ["kubectl", "get", "job", "-n", namespace, "-l", selector, "-o", "json"]


def classify_job_status(stdout: str) -> str:
    """Reduce `kubectl get job -o json` output to succeeded, failed, running, missing or unknown.

    failed: any Job has a Failed condition with status True, or status.failed > 0 without
    status.succeeded. running: a Job has status.active (and none failed). succeeded: every Job has
    status.succeeded of at least 1. missing: the list is empty. unknown: anything unparseable or
    in none of those states.
    """
    try:
        document = json.loads(stdout)
    except (ValueError, TypeError):
        return "unknown"
    if not isinstance(document, dict):
        return "unknown"
    items = document.get("items") if "items" in document else [document] if "status" in document else None
    if not isinstance(items, list):
        return "unknown"
    if not items:
        return "missing"
    states: list[str] = []
    for item in items:
        status = item.get("status") if isinstance(item, dict) else None
        if not isinstance(status, dict):
            states.append("unknown")
            continue
        conditions = status.get("conditions")
        failed_condition = isinstance(conditions, list) and any(
            isinstance(c, dict) and c.get("type") == "Failed" and str(c.get("status")) == "True"
            for c in conditions
        )
        succeeded = _count(status.get("succeeded"))
        if failed_condition or (_count(status.get("failed")) > 0 and succeeded < 1):
            states.append("failed")
        elif succeeded >= 1:
            states.append("succeeded")
        elif _count(status.get("active")) > 0:
            states.append("running")
        else:
            states.append("unknown")
    for state in ("failed", "running", "unknown"):
        if state in states:
            return state
    return "succeeded"


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )


def ensure_namespace(namespace: str) -> None:
    """Create the namespace if kubectl cannot find it. Raises RuntimeError if creation fails."""
    if _run(["kubectl", "get", "namespace", namespace]).returncode == 0:
        return
    created = _run(["kubectl", "create", "namespace", namespace])
    if created.returncode != 0:
        raise RuntimeError(f"could not create namespace {namespace}: {created.stderr.strip()}")


def install_one(
    pilot: dict[str, Any],
    chart: Path,
    namespace: str,
    tag: str,
    values_dir: Path,
    timeout: str,
    extra_sets: Sequence[str] = (),
    wait_for_jobs: bool = False,
) -> InstallResult:
    release = pilots.release_name(pilot)
    values_file = values_dir / f"{pilots.pilot_key(pilot)}.yaml"
    try:
        job_delete_rc = _run(job_delete_command(namespace, release)).returncode
    except OSError:  # kubectl is not installed or not on PATH; never gates the helm step
        job_delete_rc = 127
    cmd = helm_install_command(
        release,
        chart,
        namespace,
        tag,
        values_file,
        timeout,
        extra_sets=extra_sets,
        wait_for_jobs=wait_for_jobs,
    )
    started = time.monotonic()
    try:
        proc = _run(cmd)
        rc, error = proc.returncode, proc.stderr.strip()
    except OSError as exc:  # helm is not installed or not on PATH
        rc, error = 127, f"{type(exc).__name__}: {exc}"
    ingestion = ""
    if wait_for_jobs and rc == 0:
        try:
            status = _run(job_status_command(namespace, release))
            ingestion = classify_job_status(status.stdout) if status.returncode == 0 else "unknown"
        except OSError:  # kubectl is not installed or not on PATH
            ingestion = "unknown"
    return InstallResult(
        pilot=pilots.pilot_key(pilot),
        release=release,
        values_file=str(values_file),
        command=cmd,
        rc=rc,
        job_delete_rc=job_delete_rc,
        duration_seconds=round(time.monotonic() - started, 1),
        error=error,
        ingestion=ingestion,
    )


def install_all(
    selected: list[dict[str, Any]],
    *,
    chart: Path,
    namespace: str,
    tag: str,
    values_dir: Path,
    parallel: int,
    timeout: str,
    extra_sets: Sequence[str] = (),
    wait_for_jobs: bool = False,
) -> list[InstallResult]:
    """Run the installs, at most parallel at a time. Results keep the order of selected."""
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = [
            pool.submit(
                install_one, pilot, chart, namespace, tag, values_dir, timeout, extra_sets, wait_for_jobs
            )
            for pilot in selected
        ]
        return [future.result() for future in futures]


def diagnostics(namespace: str, release: str) -> str:
    """Pod states and the last lines of the release's ingestion Job log, for a failed install."""
    selector = f"{INSTANCE_LABEL}={release}"
    pods = _run(["kubectl", "get", "pods", "-n", namespace, "-l", selector, "-o", "wide"])
    job_selector = f"{selector},{COMPONENT_LABEL}=ingestion"
    logs = _run(
        ["kubectl", "logs", "-n", namespace, "-l", job_selector, f"--tail={LOG_TAIL_LINES}", "--prefix"]
    )
    lines = [f"--- {release}: pod states", pods.stdout.strip() or pods.stderr.strip() or "(no pods)"]
    lines.append(f"--- {release}: last {LOG_TAIL_LINES} lines of the ingestion Job log")
    lines.append(logs.stdout.strip() or logs.stderr.strip() or "(no ingestion pod log)")
    return "\n".join(lines)


def is_failed(result: InstallResult) -> bool:
    """A pilot is failed when helm failed OR its ingestion Job failed (G3/TC-512)."""
    return result.rc != 0 or result.ingestion == "failed"


def format_table(results: list[InstallResult]) -> str:
    width = max([len(r.release) for r in results] + [len("release")])
    rows = [f"{'release':<{width}}  rc   seconds  status"]
    for r in results:
        if r.rc != 0:
            status = "FAILED"
        elif r.ingestion == "failed":
            status = "INGESTION FAILED"
        elif r.ingestion in ("running", "unknown"):
            status = "installed (ingestion unverified)"
        else:
            status = "installed"
        rows.append(f"{r.release:<{width}}  {r.rc:<3}  {r.duration_seconds:>7}  {status}")
    return "\n".join(rows)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install the thirty-two foss-mcp pilot releases.")
    parser.add_argument("--namespace", required=True, help="Kubernetes namespace (created if absent)")
    parser.add_argument("--image-tag", required=True, help="tag of foss-mcp-serving and foss-mcp-ingestion")
    parser.add_argument("--chart", type=Path, default=DEFAULT_CHART, help="chart directory")
    parser.add_argument(
        "--values-dir",
        type=Path,
        default=DEFAULT_VALUES_DIR,
        help="where the per-pilot values files and install-results.json are written",
    )
    parser.add_argument("--parallel", type=int, default=2, help="most installs running at once")
    parser.add_argument("--timeout", default="30m", help="helm --timeout for each install")
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="FAMILY_PLATFORM",
        help="install only this pilot, e.g. pdf_net (repeatable)",
    )
    parser.add_argument(
        "--embedding-gateway-secret",
        default="",
        metavar="NAME",
        help="name of the operator-created Secret holding the embedding gateway endpoint and apiKey",
    )
    parser.add_argument(
        "--github-token-secret",
        default="",
        metavar="NAME",
        help="name of the operator-created Secret holding the GitHub token (key: token)",
    )
    parser.add_argument(
        "--wait-for-jobs",
        action="store_true",
        help=(
            "pass --wait-for-jobs to helm and check each release's ingestion Job afterwards, so a pilot "
            "whose ingestion failed is reported INGESTION FAILED instead of installed (recommended)"
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="print the helm commands and run nothing")
    args = parser.parse_args(argv)
    if args.parallel < 1:
        parser.error("--parallel must be at least 1")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        extra_sets = secret_set_pairs(args)
        selected = pilots.select_pilots(pilots.load_pilots(), args.only)
    except (ValueError, OSError) as exc:
        print(f"install_pilots: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        for pilot in selected:
            release = pilots.release_name(pilot)
            values_file = args.values_dir / f"{pilots.pilot_key(pilot)}.yaml"
            delete_cmd = job_delete_command(args.namespace, release)
            cmd = helm_install_command(
                release,
                args.chart,
                args.namespace,
                args.image_tag,
                values_file,
                args.timeout,
                extra_sets=extra_sets,
                wait_for_jobs=args.wait_for_jobs,
            )
            print(" ".join(delete_cmd))
            print(" ".join(cmd))
        return 0

    try:
        ensure_namespace(args.namespace)
    except RuntimeError as exc:
        print(f"install_pilots: {exc}", file=sys.stderr)
        return 1

    args.values_dir.mkdir(parents=True, exist_ok=True)
    pilots.write_values(selected, args.values_dir)
    results = install_all(
        selected,
        chart=args.chart,
        namespace=args.namespace,
        tag=args.image_tag,
        values_dir=args.values_dir,
        parallel=args.parallel,
        timeout=args.timeout,
        extra_sets=extra_sets,
        wait_for_jobs=args.wait_for_jobs,
    )
    results_path = args.values_dir / RESULTS_FILE
    results_path.write_text(json.dumps([asdict(r) for r in results], indent=2) + "\n", encoding="utf-8")

    print(format_table(results))
    failed = [r for r in results if is_failed(r)]
    for r in failed:
        if r.rc != 0:
            print(f"\n{r.release} failed with rc {r.rc}: {r.error}")
        else:
            print(
                f"\n{r.release}: helm succeeded but its ingestion Job FAILED; "
                "the serving pod is still running the previous generation"
            )
        print(diagnostics(args.namespace, r.release))
    print(f"\n{len(results) - len(failed)} of {len(results)} installed; results: {results_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
