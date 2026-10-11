#!/usr/bin/env python3
"""Generate docker-compose.demo.yml: one service per chart pilot for the standalone demo image (TC-350).

Usage:
    python scripts/release/generate_demo_compose.py [--values FILE] [--output FILE] [--check]

The pilot list is the Helm chart's own ``ingestion.pilots`` (infra/helm/foss-mcp/values.yaml, read through
scripts/deploy/pilots.py), in chart order, so there is no second list to keep in sync by hand.

Every service runs the SAME image (``${FOSS_MCP_DEMO_IMAGE}``, required) and differs only in
``FOSS_MCP_PILOT``, which infra/demo_entrypoint.py turns into the server's identity. A peer starts only the
pilots they want, by profile (``--profile pdf``, ``--profile all``) or by service name, and each container
is held to a small budget and hardened: 192 MiB, a quarter of a CPU, 256 processes, a read-only root
filesystem with a ``/tmp`` tmpfs, no capabilities, no privilege escalation.

Host ports: pilot number N (1-based, in chart order) is published on ``127.0.0.1:(8100 + N)``, and each can
be moved with ``FOSS_MCP_DEMO_PORT_<FAMILY>_<PLATFORM>``. Ports bind to loopback only.

``--check`` writes nothing and exits 1 if the committed file differs from what the chart would generate.
Exit codes: 0 written (or, with --check, up to date); 1 --check found drift; 2 the pilot list is unusable.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
_DEPLOY_DIR = REPO_ROOT / "scripts" / "deploy"
if str(_DEPLOY_DIR) not in sys.path:
    # scripts/deploy is not a package; its pilots module owns reading the chart's pilot list.
    sys.path.insert(0, str(_DEPLOY_DIR))

import pilots as chart  # noqa: E402

DEFAULT_OUTPUT = REPO_ROOT / "docker-compose.demo.yml"
_IDENTITY_PART = re.compile(r"[a-z0-9]+")
PROJECT_NAME = "foss-mcp-demo"
IMAGE_ENV = "FOSS_MCP_DEMO_IMAGE"
IMAGE_VALUE = f"${{{IMAGE_ENV}:?set {IMAGE_ENV} to the demo image reference}}"
PORT_ENV_PREFIX = "FOSS_MCP_DEMO_PORT_"
BASE_PORT = 8101
CONTAINER_PORT = 8080
ALL_PROFILE = "all"
MEM_LIMIT = "192m"
CPUS = "0.25"
PIDS_LIMIT = 256
# The same probe Dockerfile.serving's HEALTHCHECK runs: 200 from /readyz only when the active generation
# carries queryable content.
HEALTHCHECK_COMMAND = [
    "CMD",
    "python",
    "-c",
    "import urllib.request,sys; "
    "sys.exit(0 if urllib.request.urlopen('http://localhost:8080/readyz', timeout=3).status==200 else 1)",
]


class ComposeError(Exception):
    """The chart's pilot list cannot be turned into a compose file."""


def service_name(pilot: dict[str, Any]) -> str:
    return f"{pilot['family']}-{pilot['platform']}"


def port_env(pilot: dict[str, Any]) -> str:
    return f"{PORT_ENV_PREFIX}{pilot['family']}_{pilot['platform']}".upper()


def _validated(pilot_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    for pilot in pilot_list:
        for key in ("family", "platform"):
            value = pilot.get(key)
            if not isinstance(value, str) or not _IDENTITY_PART.fullmatch(value):
                raise ComposeError(f"pilot {pilot!r}: {key} must be lowercase letters and digits")
        name = service_name(pilot)
        if name in seen:
            raise ComposeError(f"duplicate pilot {name!r} in the chart's pilot list")
        seen.add(name)
    if not pilot_list:
        raise ComposeError("the chart declares no pilots")
    return pilot_list


def render_service(pilot: dict[str, Any], index: int) -> list[str]:
    """The lines of one service block; *index* is its 0-based position in chart order."""
    port = BASE_PORT + index
    healthcheck = json.dumps(HEALTHCHECK_COMMAND)
    return [
        f"  {service_name(pilot)}:",
        f'    image: "{IMAGE_VALUE}"',
        "    environment:",
        f"      FOSS_MCP_PILOT: {pilot['family']}/{pilot['platform']}",
        "    ports:",
        f'      - "127.0.0.1:${{{port_env(pilot)}:-{port}}}:{CONTAINER_PORT}"',
        f"    profiles: [{pilot['family']}, {ALL_PROFILE}]",
        f"    mem_limit: {MEM_LIMIT}",
        f"    cpus: {CPUS}",
        f"    pids_limit: {PIDS_LIMIT}",
        "    read_only: true",
        "    tmpfs:",
        "      - /tmp",
        "    cap_drop:",
        "      - ALL",
        "    security_opt:",
        "      - no-new-privileges:true",
        "    restart: unless-stopped",
        "    healthcheck:",
        f"      test: {healthcheck}",
        "      interval: 30s",
        "      timeout: 5s",
        "      start_period: 10s",
        "      retries: 3",
    ]


def render(pilot_list: list[dict[str, Any]]) -> str:
    """The full file text for *pilot_list*, deterministic: LF newlines, UTF-8, one trailing newline."""
    pilot_list = _validated(pilot_list)
    count = len(pilot_list)
    families = sorted({pilot["family"] for pilot in pilot_list})
    first, second = pilot_list[0], pilot_list[min(1, count - 1)]
    header = [
        "# GENERATED by scripts/release/generate_demo_compose.py from infra/helm/foss-mcp/values.yaml",
        "# (ingestion.pilots). Do not edit by hand: regenerate with",
        "#     python scripts/release/generate_demo_compose.py",
        "# and verify with",
        "#     python scripts/release/generate_demo_compose.py --check",
        "#",
        f"# Standalone demo: {count} services, one per pilot, all running the same image ({IMAGE_ENV}, required).",
        "# Each service differs only in FOSS_MCP_PILOT, which the image's entrypoint turns into the server's",
        "# identity. Start only the pilots you want:",
        "#",
        f"#     export {IMAGE_ENV}=<registry>/foss-mcp-serving:<demo tag>",
        f"#     docker compose -f docker-compose.demo.yml up -d {service_name(first)} {service_name(second)}",
        f"#     docker compose -f docker-compose.demo.yml --profile {first['family']} up -d     # a whole family",
        f"#     docker compose -f docker-compose.demo.yml --profile {ALL_PROFILE} up -d     # all {count} pilots",
        "#",
        f"# Profiles: each service is in its family's profile ({', '.join(families)}) and in '{ALL_PROFILE}'.",
        f"# Limits per container: mem_limit {MEM_LIMIT}, cpus {CPUS}, pids_limit {PIDS_LIMIT}, read-only root",
        "# filesystem with a /tmp tmpfs, cap_drop ALL, no-new-privileges. Healthcheck: /readyz.",
        "#",
        f"# Host ports, loopback only. Pilot N (1-based, in chart order) is published on 127.0.0.1:{BASE_PORT - 1}+N;",
        f"# override one with {PORT_ENV_PREFIX}<FAMILY>_<PLATFORM>. The MCP endpoint is http://127.0.0.1:<port>/mcp,",
        "# readiness is /readyz.",
        "#",
    ]
    for index, pilot in enumerate(pilot_list):
        header.append(
            f"#   {BASE_PORT + index}  {pilot['family']}/{pilot['platform']}  (service {service_name(pilot)})"
        )
    lines = [*header, "", f"name: {PROJECT_NAME}", "", "services:"]
    for index, pilot in enumerate(pilot_list):
        if index:
            lines.append("")
        lines.extend(render_service(pilot, index))
    return "\n".join(lines) + "\n"


def _normalized(data: bytes) -> str:
    return data.decode("utf-8").replace("\r\n", "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate docker-compose.demo.yml from the chart's pilot list."
    )
    parser.add_argument(
        "--values", type=Path, default=chart.CHART_VALUES, help="chart values file (default: %(default)s)"
    )
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT, help="compose file (default: %(default)s)"
    )
    parser.add_argument("--check", action="store_true", help="write nothing; exit 1 if the file differs")
    args = parser.parse_args(argv)
    try:
        text = render(chart.load_pilots(args.values))
    except (ComposeError, ValueError) as exc:
        print(f"generate_demo_compose: {exc}", file=sys.stderr)
        return 2
    if args.check:
        try:
            committed = _normalized(args.output.read_bytes())
        except OSError as exc:
            print(f"generate_demo_compose: cannot read {args.output} ({type(exc).__name__})", file=sys.stderr)
            return 1
        if committed != text:
            print(
                f"generate_demo_compose: {args.output} differs from the generated file; "
                "run python scripts/release/generate_demo_compose.py",
                file=sys.stderr,
            )
            return 1
        print(f"generate_demo_compose: {args.output} is up to date")
        return 0
    args.output.write_bytes(text.encode("utf-8"))
    print(f"generate_demo_compose: wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
