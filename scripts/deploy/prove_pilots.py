#!/usr/bin/env python3
"""Prove the fourteen pilot releases of the foss-mcp chart (G2/TC-223).

Usage:
    python scripts/deploy/prove_pilots.py --namespace foss-mcp [--base-port 8401] [--only pdf_net]

For each selected pilot, one at a time, it starts `kubectl port-forward` to that release's Service on
its own local port (base-port plus the pilot's position), waits up to 30 s for /healthz and /readyz to
answer 200, runs scripts/poc/pilot_workflow_proof.py against the forwarded endpoint, and always stops
the port-forward. It prints one row per pilot with the healthz, readyz, passed, failed and not
applicable counts. It exits 0 only if every pilot passed every applicable check. A pilot with no
applicable check is not a pass, and is named in the output.

Per-pilot logs and proof reports go to --report-dir. The default is a temporary directory, so no
evidence is written into the repository.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pilots  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
PROOF_SCRIPT = REPO_ROOT / "scripts" / "poc" / "pilot_workflow_proof.py"
FIXTURES = REPO_ROOT / "tests" / "fixtures"
DEFAULT_BASE_PORT = 8401
DEFAULT_REPORT_DIR = Path(tempfile.gettempdir()) / "foss-mcp-pilot-proofs"
SERVICE_PORT = 80
HEALTH_TIMEOUT_S = 30.0
HEALTH_POLL_S = 0.5
HEALTH_REQUEST_TIMEOUT_S = 2.0
STOP_TIMEOUT_S = 5.0


@dataclass
class ProofRow:
    pilot: str
    release: str
    port: int
    healthz: bool = False
    readyz: bool = False
    applicable: int = 0
    passed: int = 0
    failed: int = 0
    not_applicable: int = 0
    proof_exit: int | None = None
    error: str = ""
    ok: bool = False


def _answers_200(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=HEALTH_REQUEST_TIMEOUT_S) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


def wait_for_200(url: str, proc: Any, timeout: float) -> bool:
    """True once url answers 200 within timeout seconds. False early if the port-forward has died."""
    deadline = time.monotonic() + timeout
    while True:
        if proc.poll() is not None:
            return False
        if _answers_200(url):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(HEALTH_POLL_S)


def stop_port_forward(proc: Any) -> None:
    """Terminate the port-forward, killing it if it does not exit promptly. Safe to call twice."""
    try:
        proc.terminate()
        proc.wait(timeout=STOP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    except OSError:
        pass


def run_proof(
    pilot: dict[str, Any], url: str, report_path: Path, log_path: Path
) -> tuple[int, dict[str, Any]]:
    """Run the eleven-check proof for one pilot. Returns its exit code and the report's summary."""
    cmd = [
        sys.executable,
        str(PROOF_SCRIPT),
        "--pilot",
        pilots.pilot_key(pilot),
        "--url",
        url,
        "--fixtures",
        str(FIXTURES),
        "--report",
        str(report_path),
    ]
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, check=False)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    return proc.returncode, report["summary"]


def prove_one(pilot: dict[str, Any], namespace: str, port: int, report_dir: Path) -> ProofRow:
    key = pilots.pilot_key(pilot)
    release = pilots.release_name(pilot)
    row = ProofRow(pilot=key, release=release, port=port)
    # The Service is named after the release: the chart's fullname helper yields the same text.
    forward_cmd = [
        "kubectl",
        "port-forward",
        "-n",
        namespace,
        f"svc/{release}",
        f"{port}:{SERVICE_PORT}",
    ]
    proc: Any = None
    try:
        log_path = report_dir / f"{key}.port-forward.log"
        with log_path.open("w", encoding="utf-8") as log:
            proc = subprocess.Popen(forward_cmd, stdout=log, stderr=subprocess.STDOUT)
        base = f"http://127.0.0.1:{port}"
        row.healthz = wait_for_200(f"{base}/healthz", proc, HEALTH_TIMEOUT_S)
        row.readyz = wait_for_200(f"{base}/readyz", proc, HEALTH_TIMEOUT_S)
        if not (row.healthz and row.readyz):
            row.error = (
                f"not ready after {HEALTH_TIMEOUT_S:g} s (healthz {'200' if row.healthz else 'not 200'}, "
                f"readyz {'200' if row.readyz else 'not 200'}); see {log_path}"
            )
            return row
        rc, summary = run_proof(
            pilot,
            base,
            report_dir / f"{key}.json",
            report_dir / f"{key}.proof.log",
        )
        row.proof_exit = rc
        row.applicable = int(summary["applicable"])
        row.passed = int(summary["pass"])
        row.failed = int(summary["fail"])
        row.not_applicable = int(summary["not_applicable"])
        row.ok = rc == 0 and row.failed == 0 and row.applicable > 0
    except Exception as exc:  # a proof that raises is a failed pilot, and the others still run
        row.error = f"{type(exc).__name__}: {exc}"
    finally:
        if proc is not None:
            stop_port_forward(proc)
    return row


def format_table(rows: list[ProofRow]) -> str:
    width = max([len(r.release) for r in rows] + [len("release")])
    header = f"{'release':<{width}}  port  healthz  readyz  passed  failed  n/a  result"
    lines = [header]
    for r in rows:
        result = "PASS" if r.ok else "FAIL"
        lines.append(
            f"{r.release:<{width}}  {r.port:<4}  {'200' if r.healthz else '-':<7}  "
            f"{'200' if r.readyz else '-':<6}  {r.passed:<6}  {r.failed:<6}  {r.not_applicable:<3}  {result}"
        )
    return "\n".join(lines)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prove the fourteen foss-mcp pilot releases.")
    parser.add_argument("--namespace", required=True, help="Kubernetes namespace of the releases")
    parser.add_argument(
        "--base-port", type=int, default=DEFAULT_BASE_PORT, help="first local port for the port-forwards"
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="FAMILY_PLATFORM",
        help="prove only this pilot, e.g. pdf_net (repeatable)",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=DEFAULT_REPORT_DIR,
        help="where the per-pilot proof reports and logs are written",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        selected = pilots.select_pilots(pilots.load_pilots(), args.only)
    except (ValueError, OSError) as exc:
        print(f"prove_pilots: {exc}", file=sys.stderr)
        return 2
    args.report_dir.mkdir(parents=True, exist_ok=True)

    rows = [
        prove_one(pilot, args.namespace, args.base_port + index, args.report_dir)
        for index, pilot in enumerate(selected)
    ]

    print(format_table(rows))
    for r in rows:
        if r.error:
            print(f"{r.release}: {r.error}")
    no_applicable = [r.release for r in rows if r.applicable == 0]
    if no_applicable:
        print(f"no applicable checks ran for: {', '.join(no_applicable)}")
    passed = sum(1 for r in rows if r.ok)
    print(f"{passed} of {len(rows)} pilots passed every applicable check; reports: {args.report_dir}")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
