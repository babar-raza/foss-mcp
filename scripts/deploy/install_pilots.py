#!/usr/bin/env python3
"""Install the fourteen pilot releases of the foss-mcp chart (G2/TC-223).

Usage:
    python scripts/deploy/install_pilots.py --namespace foss-mcp --image-tag rev-1234567

For each selected pilot it writes a minimal values file (see pilots.py), then runs
    helm upgrade --install <release> <chart> -n <namespace> --set image.tag=<tag> -f <values> --wait --timeout <t>
with at most --parallel installs running at once. It creates the namespace if it is absent, prints a
result table, writes install-results.json into --values-dir, and exits non-zero if any install failed.
A failed install prints the pod states and the last 20 lines of that release's ingestion Job log.

This tool never removes anything: it runs no helm release removal and no resource deletion.
--dry-run prints each helm command and runs nothing, and writes nothing.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
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


@dataclass
class InstallResult:
    pilot: str
    release: str
    values_file: str
    command: list[str]
    rc: int
    duration_seconds: float
    error: str = ""


def helm_install_command(
    release: str, chart: Path, namespace: str, tag: str, values_file: Path, timeout: str
) -> list[str]:
    return [
        "helm",
        "upgrade",
        "--install",
        release,
        str(chart),
        "-n",
        namespace,
        "--set",
        f"image.tag={tag}",
        "-f",
        str(values_file),
        "--wait",
        "--timeout",
        timeout,
    ]


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
    pilot: dict[str, Any], chart: Path, namespace: str, tag: str, values_dir: Path, timeout: str
) -> InstallResult:
    release = pilots.release_name(pilot)
    values_file = values_dir / f"{pilots.pilot_key(pilot)}.yaml"
    cmd = helm_install_command(release, chart, namespace, tag, values_file, timeout)
    started = time.monotonic()
    try:
        proc = _run(cmd)
        rc, error = proc.returncode, proc.stderr.strip()
    except OSError as exc:  # helm is not installed or not on PATH
        rc, error = 127, f"{type(exc).__name__}: {exc}"
    return InstallResult(
        pilot=pilots.pilot_key(pilot),
        release=release,
        values_file=str(values_file),
        command=cmd,
        rc=rc,
        duration_seconds=round(time.monotonic() - started, 1),
        error=error,
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
) -> list[InstallResult]:
    """Run the installs, at most parallel at a time. Results keep the order of selected."""
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = [
            pool.submit(install_one, pilot, chart, namespace, tag, values_dir, timeout) for pilot in selected
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


def format_table(results: list[InstallResult]) -> str:
    width = max([len(r.release) for r in results] + [len("release")])
    rows = [f"{'release':<{width}}  rc   seconds  status"]
    for r in results:
        status = "installed" if r.rc == 0 else "FAILED"
        rows.append(f"{r.release:<{width}}  {r.rc:<3}  {r.duration_seconds:>7}  {status}")
    return "\n".join(rows)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install the fourteen foss-mcp pilot releases.")
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
    parser.add_argument("--dry-run", action="store_true", help="print the helm commands and run nothing")
    args = parser.parse_args(argv)
    if args.parallel < 1:
        parser.error("--parallel must be at least 1")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        selected = pilots.select_pilots(pilots.load_pilots(), args.only)
    except (ValueError, OSError) as exc:
        print(f"install_pilots: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        for pilot in selected:
            values_file = args.values_dir / f"{pilots.pilot_key(pilot)}.yaml"
            cmd = helm_install_command(
                pilots.release_name(pilot),
                args.chart,
                args.namespace,
                args.image_tag,
                values_file,
                args.timeout,
            )
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
    )
    results_path = args.values_dir / RESULTS_FILE
    results_path.write_text(json.dumps([asdict(r) for r in results], indent=2) + "\n", encoding="utf-8")

    print(format_table(results))
    failed = [r for r in results if r.rc != 0]
    for r in failed:
        print(f"\n{r.release} failed with rc {r.rc}: {r.error}")
        print(diagnostics(args.namespace, r.release))
    print(f"\n{len(results) - len(failed)} of {len(results)} installed; results: {results_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
