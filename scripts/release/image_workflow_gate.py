#!/usr/bin/env python3
"""Run the exact Docker image that would ship, one container per pilot, under a memory and CPU budget (TC-345).

Usage:
    python scripts/release/image_workflow_gate.py --image NAME:TAG [--only FAMILY_PLATFORM ...] [--fixtures DIR]
        [--report-dir DIR] [--memory 256m] [--cpus 0.5] [--ready-timeout 120] [--parallel 2]
        [--with-embedding-gateway] [--dry-run]

Requirements met here: REQ-G3-001 (test the MCP with actual workflows before deployment) and the measuring
half of REQ-G3-002 (lean, must not overwhelm the cluster).

For every selected pilot (the chart's ``ingestion.pilots``, read through scripts/deploy/pilots.py, so there is no
second list) the gate:

  1. starts ONE container from the image, exactly as a peer will: ``docker run -d --rm --read-only --tmpfs /tmp
     --memory M --cpus C --pids-limit 256 --publish 127.0.0.1::8080 -e FOSS_MCP_PILOT=<family>/<platform> IMAGE``,
     with no volume mount and NO command override, so the image's own entrypoint (infra/demo_entrypoint.py)
     selects the pilot and starts the HTTP server;
  2. finds the host port with ``docker port`` and polls ``/readyz`` until it answers 200 or --ready-timeout runs out;
  3. runs the eleven-check proof of scripts/poc/pilot_workflow_proof.py over a real MCP session, then a short
     task-oriented workflow recorded as its own entries (W1 tools/list, W2 a natural question to lookup, W3
     find_examples with a verified example's title, which is not_applicable when the proof found none);
  4. samples ``docker stats --no-stream`` memory before and after and keeps the larger;
  5. ALWAYS ``docker rm -f`` the container, on success, failure, timeout or Ctrl-C.

A pilot fails when any applicable check or workflow entry fails, when its container never became ready, when it was
OOM-killed or used more memory than the budget, when its memory could not be measured, or when ``docker run`` itself
failed. A not_applicable entry is never counted as a pass, and a pilot with no passing entry does not pass.

Report: ``<pilot_key>.json`` per pilot plus ``summary.json`` in --report-dir (default: a temporary directory, so
nothing lands in the repository unless asked). summary.json is the file ``gatectl release-record --gate-report``
reads; its keys are fixed (see ``build_summary``). Exit 0 only when every selected pilot passed; 1 when any failed
(or after an interrupt); 2 when the request was refused before anything ran (bad image reference, unknown --only
name, bad number, --with-embedding-gateway without both variables set).

Secrets: the optional embedding-gateway variables are passed to a container only with --with-embedding-gateway, by
NAME (``-e NAME``), so docker reads the value itself and it never appears in an argv. This tool reads the two values
only to scrub them from anything it prints or writes; it never prints any environment variable's value.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
_DEPLOY_DIR = REPO_ROOT / "scripts" / "deploy"
PROOF_SCRIPT = REPO_ROOT / "scripts" / "poc" / "pilot_workflow_proof.py"
DEFAULT_FIXTURES = REPO_ROOT / "tests" / "fixtures"
DEFAULT_REPORT_DIR = Path(tempfile.gettempdir()) / "foss-mcp-image-workflow-gate"

if str(_DEPLOY_DIR) not in sys.path:
    # scripts/deploy is not a package; its pilots module owns reading the chart's pilot list.
    sys.path.insert(0, str(_DEPLOY_DIR))

import pilots as chart  # noqa: E402

SCHEMA_VERSION = 1
SUMMARY_NAME = "summary.json"
CONTAINER_PORT = 8080
PIDS_LIMIT = 256
PILOT_ENV = "FOSS_MCP_PILOT"
# Forwarded by NAME only, and only with --with-embedding-gateway.
GATEWAY_ENV_NAMES = ("FOSS_MCP_EMBEDDING_GATEWAY_ENDPOINT", "FOSS_MCP_EMBEDDING_GATEWAY_API_KEY")
# The path infra/serve_http.py answers 200 on only when the active generation carries queryable content.
READY_PATH = "/readyz"

DEFAULT_MEMORY = "256m"
DEFAULT_CPUS = 0.5
DEFAULT_READY_TIMEOUT = 120.0
DEFAULT_PARALLEL = 2

POLL_INTERVAL_S = 1.0
HTTP_TIMEOUT_S = 3.0
DOCKER_TIMEOUT_S = 60.0
DOCKER_RUN_TIMEOUT_S = 180.0
LOG_TAIL_LINES = 6
LOG_LINE_LIMIT = 200
EXCERPT_LIMIT = 200

EXIT_FAILED = 1
EXIT_REFUSED = 2
EXIT_INTERRUPTED = 130

# The tools the workflow insists on finding in tools/list (the proof's check 1 separately wants all nine).
REQUIRED_TOOLS = ("lookup", "search_symbols", "get_symbol", "list_members", "find_examples")
# A natural question built from the probe symbol's short name. lookup answers a question with documentation
# first (REQ-G2-049), so it is asked in this form first and by the bare name second (see workflow_lookup_question).
QUESTION_TEMPLATE = "How do I use {symbol}?"
EXAMPLE_TITLE_PREFIX = "Example: "

WORKFLOW_TITLES: dict[str, str] = {
    "W1": "tools/list returns a non-empty tool registry with lookup, search_symbols, get_symbol, "
    "list_members and find_examples",
    "W2": "lookup answers a natural question about the pilot's probe symbol with a non-empty answer that names it",
    "W3": "find_examples returns a match with a non-empty snippet for the title of the verified example "
    "the proof found",
}

# Image reference: an optional registry HOST[:PORT]/, lowercase path components, then an explicit :TAG. The
# first character is alphanumeric, so a value can never read as a docker option.
_COMPONENT = r"[a-z0-9]+(?:[._-]+[a-z0-9]+)*"
_HOST = r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?(?::[0-9]{1,5})?"
IMAGE_PATTERN = re.compile(
    rf"(?:{_HOST}/)?{_COMPONENT}(?:/{_COMPONENT})*:(?P<tag>[A-Za-z0-9_][A-Za-z0-9_.-]{{0,127}})"
)
MAX_IMAGE_LENGTH = 255
IMAGE_ID_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")

_MEMORY_LIMIT = re.compile(r"([1-9][0-9]*)([bkmg]?)", re.IGNORECASE)
_LIMIT_UNIT_BYTES = {"": 1, "b": 1, "k": 1024, "m": 1024**2, "g": 1024**3}
_STATS_USAGE = re.compile(r"\s*([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]+)")
_STATS_UNIT_BYTES = {
    "B": 1,
    "kB": 1000,
    "KB": 1000,
    "KiB": 1024,
    "MB": 1000**2,
    "MiB": 1024**2,
    "GB": 1000**3,
    "GiB": 1024**3,
    "TB": 1000**4,
    "TiB": 1024**4,
}
_HOST_PORT = re.compile(r"127\.0\.0\.1:([0-9]{1,5})\s*$", re.MULTILINE)


class Refused(Exception):
    """The request cannot be run as asked. Nothing has been started; the exit code is 2."""


class Stopped(Exception):
    """The run was interrupted while this pilot was still starting."""


# ---------------------------------------------------------------------------------------------------------------
# The proof module: reused, never copied.
# ---------------------------------------------------------------------------------------------------------------


def _load_proof_module() -> Any:
    """scripts/poc/pilot_workflow_proof.py, imported by file path (it is not a package)."""
    name = "_image_gate_pilot_workflow_proof"
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(name, PROOF_SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {PROOF_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # its dataclass needs the module registered while it executes
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


# ---------------------------------------------------------------------------------------------------------------
# Dependencies the tests replace: subprocess, the HTTP probe, the MCP client, time.
# ---------------------------------------------------------------------------------------------------------------


@dataclass
class Deps:
    """Everything the gate touches outside this process. None means the real thing, resolved at call time."""

    run: Callable[..., Any] | None = None  # subprocess.run
    http_get: Callable[[str, float], int | None] | None = None  # status code, or None for no connection
    client_factory: Callable[[str], Any] | None = None  # base URL -> MCP client (proof.open_live_client)
    proof_runner: Callable[[Any, Any], list[dict[str, Any]]] | None = None  # (client, pilot) -> checks
    pilot_loader: Callable[..., Any] | None = None  # proof.load_pilot
    sleep: Callable[[float], None] | None = None
    clock: Callable[[], float] | None = None
    environ: Mapping[str, str] | None = None
    run_id: str | None = None


def http_status(url: str, timeout: float) -> int | None:
    """The HTTP status of a GET, or None when nothing answered."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except (urllib.error.URLError, OSError, ValueError):
        return None


@dataclass
class DockerResult:
    rc: int
    stdout: str
    stderr: str


def run_docker(deps: Deps, args: list[str], *, timeout: float = DOCKER_TIMEOUT_S) -> DockerResult:
    """Run ``docker <args>`` and capture its output. A missing docker or a timeout is a non-zero result."""
    cmd = ["docker", *args]
    runner = deps.run if deps.run is not None else subprocess.run
    try:
        proc = runner(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return DockerResult(-1, "", f"docker {args[0]} timed out after {timeout:g} s")
    except OSError as exc:
        return DockerResult(-1, "", f"could not run docker ({type(exc).__name__}); is it on PATH?")
    return DockerResult(int(proc.returncode), _text(proc.stdout), _text(proc.stderr))


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value if isinstance(value, str) else ""


# ---------------------------------------------------------------------------------------------------------------
# Secret hygiene
# ---------------------------------------------------------------------------------------------------------------


class Redactor:
    """Replaces the values of the optional gateway variables wherever they might leak into text.

    The values are read only to be removed. They are never printed, never passed to docker in an argument
    (docker reads them itself through ``-e NAME``) and never written to a report.
    """

    def __init__(self, values: Iterable[str]) -> None:
        self._values = sorted({value for value in values if value}, key=len, reverse=True)

    def text(self, text: str) -> str:
        for value in self._values:
            text = text.replace(value, "[redacted]")
        return text

    def scrub(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list | tuple):
            return [self.scrub(item) for item in value]
        if isinstance(value, dict):
            return {key: self.scrub(item) for key, item in value.items()}
        return value


def ascii_safe(text: str) -> str:
    """Printable on any console: a cp1252 terminal must not crash on a product document's characters."""
    return text.encode("ascii", "replace").decode("ascii")


# ---------------------------------------------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------------------------------------------


def validate_image(image: str) -> str:
    """The image reference, unchanged, if it is NAME:TAG with an explicit tag that is not ``latest``."""
    shown = image[:60]
    if not image or len(image) > MAX_IMAGE_LENGTH or any(ch.isspace() for ch in image):
        raise Refused(f"--image {shown!r} is not an image reference. Nothing was run.")
    match = IMAGE_PATTERN.fullmatch(image)
    if match is None:
        raise Refused(
            f"--image {shown!r} must be NAME:TAG with an explicit tag (lowercase name, optional registry host). "
            "Nothing was run."
        )
    if match.group("tag").lower() == "latest":
        raise Refused("--image must carry an explicit release tag, never latest. Nothing was run.")
    return image


def parse_memory_limit_mib(text: str) -> float:
    """MiB for a ``--memory`` value such as 256m, 1g, 512M, 524288k or a plain byte count."""
    match = _MEMORY_LIMIT.fullmatch(text.strip())
    if match is None:
        raise ValueError(f"--memory {text!r} must be a whole number with an optional b, k, m or g suffix")
    return int(match.group(1)) * _LIMIT_UNIT_BYTES[match.group(2).lower()] / 1024**2


def parse_stats_mib(output: str) -> float | None:
    """MiB from the used half of ``docker stats --format {{.MemUsage}}`` ('45.3MiB / 256MiB'), or None."""
    match = _STATS_USAGE.match(output.split("/", 1)[0])
    if match is None:
        return None
    unit = _STATS_UNIT_BYTES.get(match.group(2))
    if unit is None:
        return None
    return float(match.group(1)) * unit / 1024**2


def _memory_arg(text: str) -> str:
    try:
        parse_memory_limit_mib(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return text


def _positive_float(text: str) -> float:
    try:
        value = float(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number") from exc
    if not value > 0 or value == float("inf"):
        raise argparse.ArgumentTypeError(f"{text!r} must be a finite number greater than 0")
    return value


def _at_least_one(text: str) -> int:
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number") from exc
    if value < 1:
        raise argparse.ArgumentTypeError(f"{text!r} must be at least 1")
    return value


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the exact shipped image, one container per pilot, and prove each with real workflows.",
    )
    parser.add_argument(
        "--image", required=True, help="image NAME:TAG to test (an explicit tag, never latest)"
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="FAMILY_PLATFORM",
        help="test only this pilot, e.g. pdf_net (repeatable); an unknown name exits 2 and runs nothing",
    )
    parser.add_argument(
        "--fixtures", type=Path, default=DEFAULT_FIXTURES, help="fixtures root (default tests/fixtures)"
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=DEFAULT_REPORT_DIR,
        help="where <pilot_key>.json and summary.json are written (default: a temporary directory)",
    )
    parser.add_argument(
        "--memory",
        type=_memory_arg,
        default=DEFAULT_MEMORY,
        help=f"per-container memory limit (default {DEFAULT_MEMORY})",
    )
    parser.add_argument(
        "--cpus",
        type=_positive_float,
        default=DEFAULT_CPUS,
        help=f"per-container CPU limit (default {DEFAULT_CPUS:g})",
    )
    parser.add_argument(
        "--ready-timeout",
        type=_positive_float,
        default=DEFAULT_READY_TIMEOUT,
        help=f"seconds to wait for each container's readiness probe (default {DEFAULT_READY_TIMEOUT:g})",
    )
    parser.add_argument(
        "--parallel",
        type=_at_least_one,
        default=DEFAULT_PARALLEL,
        help=f"containers starting at once, at least 1 (default {DEFAULT_PARALLEL})",
    )
    parser.add_argument(
        "--with-embedding-gateway",
        action="store_true",
        help="pass the two embedding-gateway variables from this environment to each container, by name only",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the docker commands each pilot would get and run nothing",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------------------------------------------
# docker commands
# ---------------------------------------------------------------------------------------------------------------


def container_name(entry: dict[str, Any], run_id: str) -> str:
    return f"foss-mcp-gate-{entry['family']}-{entry['platform']}-{run_id}"


def run_command(
    image: str, name: str, entry: dict[str, Any], memory: str, cpus: float, *, with_gateway: bool
) -> list[str]:
    """The ``docker run`` argv for one pilot: budgeted, read-only, loopback-only, no mounts, no command."""
    cmd = [
        "docker",
        "run",
        "-d",
        "--rm",
        "--read-only",
        "--tmpfs",
        "/tmp",
        "--memory",
        memory,
        "--cpus",
        f"{cpus:g}",
        "--pids-limit",
        str(PIDS_LIMIT),
        "--publish",
        f"127.0.0.1::{CONTAINER_PORT}",
        "--name",
        name,
        "-e",
        f"{PILOT_ENV}={entry['family']}/{entry['platform']}",
    ]
    if with_gateway:
        for variable in GATEWAY_ENV_NAMES:
            cmd += ["-e", variable]  # the name alone: docker reads the value from this process's environment
    cmd.append(image)
    return cmd


def port_command(name: str) -> list[str]:
    return ["docker", "port", name, f"{CONTAINER_PORT}/tcp"]


def stats_command(name: str) -> list[str]:
    return ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", name]


def state_command(name: str) -> list[str]:
    return ["docker", "inspect", "--format", "{{.State.Running}} {{.State.OOMKilled}}", name]


def logs_command(name: str) -> list[str]:
    return ["docker", "logs", "--tail", str(LOG_TAIL_LINES), name]


def remove_command(name: str) -> list[str]:
    return ["docker", "rm", "-f", name]


def image_id_command(image: str) -> list[str]:
    return ["docker", "image", "inspect", "--format", "{{.Id}}", image]


def image_size_command(image: str) -> list[str]:
    return ["docker", "image", "inspect", "--format", "{{.Size}}", image]


def host_port(output: str) -> int | None:
    """The loopback host port from ``docker port`` output, or None."""
    match = _HOST_PORT.search(output)
    if match is None:
        return None
    port = int(match.group(1))
    return port if 1 <= port <= 65535 else None


# ---------------------------------------------------------------------------------------------------------------
# The context one run shares, and guaranteed container cleanup
# ---------------------------------------------------------------------------------------------------------------


class Containers:
    """The containers this run has started and not yet removed. Safe to use from several threads."""

    def __init__(self, deps: Deps) -> None:
        self._deps = deps
        self._names: set[str] = set()
        self._lock = threading.Lock()

    def add(self, name: str) -> None:
        with self._lock:
            self._names.add(name)

    def remove(self, name: str) -> None:
        """``docker rm -f`` the container. The name is forgotten only once the removal has been attempted."""
        run_docker(self._deps, remove_command(name)[1:])
        with self._lock:
            self._names.discard(name)

    def remove_all(self) -> None:
        with self._lock:
            pending = sorted(self._names)
        for name in pending:
            self.remove(name)


@dataclass
class Context:
    image: str
    memory: str
    memory_mib: float
    cpus: float
    ready_timeout: float
    fixtures: Path
    report_dir: Path
    with_gateway: bool
    run_id: str
    deps: Deps
    proof: Any
    containers: Containers
    redact: Redactor
    stop: threading.Event = field(default_factory=threading.Event)
    # tests/e2e's MCP client keeps its base URL in a module global, so two sessions at once would send one
    # pilot's requests to another pilot's container. Containers start in parallel; sessions never overlap.
    session_lock: threading.Lock = field(default_factory=threading.Lock)
    print_lock: threading.Lock = field(default_factory=threading.Lock)

    def clock(self) -> float:
        return (self.deps.clock or time.monotonic)()

    def sleep(self, seconds: float) -> None:
        (self.deps.sleep or time.sleep)(seconds)

    def http_get(self, url: str, timeout: float) -> int | None:
        return (self.deps.http_get or http_status)(url, timeout)

    def load_pilot(self, key: str) -> Any:
        loader = self.deps.pilot_loader or self.proof.load_pilot
        return loader(key, self.fixtures, chart_values=chart.CHART_VALUES)

    def open_client(self, url: str) -> Any:
        factory = self.deps.client_factory or self.proof.open_live_client
        return factory(url)

    def run_proof(self, client: Any, pilot: Any) -> list[dict[str, Any]]:
        runner = self.deps.proof_runner or self.proof.run_checks
        return runner(client, pilot)


# ---------------------------------------------------------------------------------------------------------------
# Container state, readiness and memory
# ---------------------------------------------------------------------------------------------------------------


@dataclass
class ContainerState:
    found: bool
    running: bool = False
    oom_killed: bool = False


def container_state(ctx: Context, name: str) -> ContainerState:
    result = run_docker(ctx.deps, state_command(name)[1:])
    parts = result.stdout.split()
    if result.rc != 0 or len(parts) < 2:
        return ContainerState(found=False)
    return ContainerState(found=True, running=parts[0] == "true", oom_killed=parts[1] == "true")


def describe_gone(state: ContainerState, memory: str) -> str | None:
    """Why a container is no longer usable, or None while it is running and was not OOM-killed."""
    if not state.found:
        return (
            "the container is gone (it exited and --rm removed it, so an out-of-memory kill cannot be told "
            f"apart from another exit; budget {memory})"
        )
    if state.oom_killed:
        return f"the container was OOM-killed (memory budget {memory})"
    if not state.running:
        return "the container is no longer running"
    return None


def log_tail(ctx: Context, name: str) -> str:
    result = run_docker(ctx.deps, logs_command(name)[1:])
    lines = [line.strip() for line in (result.stdout + "\n" + result.stderr).splitlines() if line.strip()]
    return " | ".join(line[:LOG_LINE_LIMIT] for line in lines[-LOG_TAIL_LINES:])


def wait_until_ready(ctx: Context, name: str, base_url: str) -> str | None:
    """None once the readiness probe answers 200; otherwise the reason it never did."""
    deadline = ctx.clock() + ctx.ready_timeout
    last = "no connection"
    while True:
        if ctx.stop.is_set():
            raise Stopped("interrupted")
        status = ctx.http_get(f"{base_url}{READY_PATH}", HTTP_TIMEOUT_S)
        if status == 200:
            return None
        last = f"HTTP {status}" if status is not None else "no connection"
        gone = describe_gone(container_state(ctx, name), ctx.memory)
        if gone is not None:
            return f"{gone}; the last {READY_PATH} answer was {last}"
        if ctx.clock() >= deadline:
            return f"{READY_PATH} did not answer 200 within {ctx.ready_timeout:g} s (last answer: {last})"
        ctx.sleep(POLL_INTERVAL_S)


def sample_memory(ctx: Context, name: str) -> float | None:
    result = run_docker(ctx.deps, stats_command(name)[1:])
    if result.rc != 0:
        return None
    return parse_stats_mib(result.stdout)


# ---------------------------------------------------------------------------------------------------------------
# The task-oriented workflow, run over the same MCP session as the proof
# ---------------------------------------------------------------------------------------------------------------


def _entry(
    number: str, status: str, evidence: dict[str, Any] | None = None, reason: str | None = None
) -> dict[str, Any]:
    return {
        "id": number,
        "name": WORKFLOW_TITLES[number],
        "status": status,
        "evidence": evidence or {},
        "reason": reason,
    }


def names_symbol(symbol: str, text: str) -> bool:
    """True when the symbol appears in text as a whole identifier (not as part of a longer one)."""
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(symbol)}(?![A-Za-z0-9_])", text) is not None


def excerpt(text: str, symbol: str | None = None) -> str:
    """A short single-line piece of text; around the symbol's first appearance when one is given."""
    flat = " ".join(text.split())
    start = 0
    if symbol:
        found = re.search(rf"(?<![A-Za-z0-9_]){re.escape(symbol)}(?![A-Za-z0-9_])", flat)
        if found:
            start = max(0, found.start() - 60)
    return flat[start : start + EXCERPT_LIMIT]


def describe_lookup(answer: Any, symbol: str) -> dict[str, Any]:
    """What a lookup answer is, and whether any text in it names the symbol. A miss never counts."""
    texts: list[str]
    if isinstance(answer, list):
        kind = "matches"
        texts = [str(item.get("text") or "") for item in answer if isinstance(item, dict)]
    elif isinstance(answer, dict) and "doc_matches" in answer:
        kind = "task_answer"
        docs = answer.get("doc_matches") or []
        texts = [str(item.get("text") or "") for item in docs if isinstance(item, dict)]
        example = answer.get("example")
        if isinstance(example, dict):
            texts += [str(example.get("fqn") or ""), str(example.get("snippet") or "")]
    elif isinstance(answer, dict) and "reason" in answer:
        return {
            "kind": "miss",
            "items": 0,
            "names_symbol": False,
            "excerpt": "",
            "miss_reason": str(answer["reason"])[:200],
        }
    else:
        return {"kind": type(answer).__name__, "items": 0, "names_symbol": False, "excerpt": ""}
    hit = next((text for text in texts if names_symbol(symbol, text)), None)
    return {
        "kind": kind,
        "items": len([text for text in texts if text.strip()]),
        "names_symbol": hit is not None,
        "excerpt": excerpt(hit, symbol) if hit is not None else "",
    }


def workflow_tools(client: Any) -> dict[str, Any]:
    tools = client.list_tools()
    names = sorted(
        str(tool["name"]) for tool in tools if isinstance(tool, dict) and isinstance(tool.get("name"), str)
    )
    evidence: dict[str, Any] = {"tool_count": len(names), "tools": names}
    if not names:
        return _entry("W1", "fail", evidence, "tools/list returned an empty tool registry")
    missing = sorted(set(REQUIRED_TOOLS) - set(names))
    if missing:
        return _entry("W1", "fail", evidence, f"tools/list lacks {missing}")
    return _entry("W1", "pass", evidence)


def workflow_lookup_question(client: Any, pilot: Any) -> dict[str, Any]:
    """lookup, asked as a natural question about the probe symbol, must answer with content that names it.

    The question goes first because that is how a task-oriented client asks, and lookup answers a question
    with documentation first. A documentation answer for some symbols never contains the identifier itself, so
    the bare name, which lookup's own tool description says to use for an identifier, is asked second. Both
    attempts are recorded, and ``satisfied_by`` says which one named the symbol. A miss never satisfies either.
    """
    symbol = pilot.symbol
    if symbol is None:
        return _entry("W2", "fail", {}, pilot.probe_error or f"{pilot.name} has no probe symbol")
    attempts: list[dict[str, Any]] = []
    for form, query in (("question", QUESTION_TEMPLATE.format(symbol=symbol)), ("identifier", symbol)):
        answer = client.invoke("lookup", {"query": query})
        attempts.append({"form": form, "query": query, **describe_lookup(answer, symbol)})
        if attempts[-1]["names_symbol"]:
            evidence = {
                "symbol": symbol,
                "satisfied_by": form,
                "excerpt": attempts[-1]["excerpt"],
                "attempts": attempts,
            }
            return _entry("W2", "pass", evidence)
    return _entry(
        "W2",
        "fail",
        {"symbol": symbol, "attempts": attempts},
        f"lookup returned no non-empty answer that names {symbol!r}, for the question or for the bare name",
    )


def workflow_find_examples(client: Any, checks: list[dict[str, Any]]) -> dict[str, Any]:
    """find_examples with the title of the verified example the proof's check 6 found, when it found one."""
    check = next((item for item in checks if item.get("id") == 6), None)
    evidence = (check or {}).get("evidence") or {}
    if check is None or check.get("status") != "pass":
        status = "missing" if check is None else str(check.get("status"))
        return _entry(
            "W3",
            "not_applicable",
            {},
            f"the proof's check 6 did not establish a verified example (it is {status})",
        )
    if evidence.get("answer") != "verified example" or not evidence.get("example_fqn"):
        return _entry(
            "W3",
            "not_applicable",
            {},
            "the proof's check 6 found no verified example for this pilot (an honest no-example answer)",
        )
    fqn = str(evidence["example_fqn"])
    title = fqn[len(EXAMPLE_TITLE_PREFIX) :] if fqn.startswith(EXAMPLE_TITLE_PREFIX) else fqn
    results = client.invoke("find_examples", {"query": title})
    detail: dict[str, Any] = {"title": title, "example_fqn": fqn}
    if not isinstance(results, list) or not results:
        detail["answer"] = "empty list" if isinstance(results, list) else "no example"
        return _entry("W3", "fail", detail, f"find_examples returned no example for the title {title!r}")
    with_snippet = [
        item for item in results if isinstance(item, dict) and str(item.get("snippet") or "").strip()
    ]
    detail["match_count"] = len(results)
    if not with_snippet:
        return _entry(
            "W3", "fail", detail, f"find_examples matched {title!r} but every match has an empty snippet"
        )
    detail.update(
        first_fqn=with_snippet[0].get("fqn"), snippet_excerpt=excerpt(str(with_snippet[0]["snippet"]))
    )
    return _entry("W3", "pass", detail)


def run_workflow(client: Any, pilot: Any, checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """W1 to W3. An entry that cannot run is a fail with its reason, never a silent skip."""
    steps: list[tuple[str, Callable[[], dict[str, Any]]]] = [
        ("W1", lambda: workflow_tools(client)),
        ("W2", lambda: workflow_lookup_question(client, pilot)),
        ("W3", lambda: workflow_find_examples(client, checks)),
    ]
    entries = []
    for number, step in steps:
        try:
            entries.append(step())
        except Exception as exc:
            entries.append(_entry(number, "fail", {}, f"could not run: {type(exc).__name__}: {exc}"))
    return entries


def unrunnable_workflow(reason: str) -> list[dict[str, Any]]:
    return [_entry(number, "fail", {}, reason) for number in WORKFLOW_TITLES]


# ---------------------------------------------------------------------------------------------------------------
# One pilot
# ---------------------------------------------------------------------------------------------------------------


@dataclass
class PilotRun:
    key: str
    container: str
    reasons: list[str] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    workflow: list[dict[str, Any]] = field(default_factory=list)
    samples_mib: list[float] = field(default_factory=list)
    startup_seconds: float = 0.0
    url: str | None = None
    started: bool = False


def count_statuses(entries: list[dict[str, Any]]) -> dict[str, int]:
    """pass, fail and not_applicable counts. A not_applicable entry is counted as itself, never as a pass."""
    return {
        "pass": sum(1 for entry in entries if entry.get("status") == "pass"),
        "fail": sum(1 for entry in entries if entry.get("status") == "fail"),
        "not_applicable": sum(1 for entry in entries if entry.get("status") == "not_applicable"),
    }


def _drive(entry: dict[str, Any], ctx: Context, run: PilotRun) -> None:
    try:
        pilot = ctx.load_pilot(run.key)
    except Exception as exc:
        run.reasons.append(f"the pilot's fixtures could not be loaded: {type(exc).__name__}: {exc}")
        return

    # Recorded before `docker run`: a failed or interrupted run may still leave a container behind.
    ctx.containers.add(run.container)
    run.started = True
    started = ctx.clock()
    command = run_command(
        ctx.image, run.container, entry, ctx.memory, ctx.cpus, with_gateway=ctx.with_gateway
    )
    launched = run_docker(ctx.deps, command[1:], timeout=DOCKER_RUN_TIMEOUT_S)
    if launched.rc != 0:
        detail = " ".join((launched.stderr or launched.stdout).split())[:300]
        run.reasons.append(f"docker run failed (exit {launched.rc}): {detail}")
        return

    mapped = run_docker(ctx.deps, port_command(run.container)[1:])
    port = host_port(mapped.stdout) if mapped.rc == 0 else None
    if port is None:
        run.reasons.append("docker port did not report a loopback host port for the container")
        return
    run.url = f"http://127.0.0.1:{port}"

    not_ready = wait_until_ready(ctx, run.container, run.url)
    run.startup_seconds = round(ctx.clock() - started, 2)
    if not_ready is not None:
        tail = log_tail(ctx, run.container)
        run.reasons.append(
            f"the container never became ready: {not_ready}" + (f"; last log lines: {tail}" if tail else "")
        )
        return

    before = sample_memory(ctx, run.container)
    if before is not None:
        run.samples_mib.append(before)

    with ctx.session_lock:
        try:
            client = ctx.open_client(run.url)
        except Exception as exc:
            reason = f"the MCP session could not be opened at the container: {type(exc).__name__}: {exc}"
            run.checks = ctx.proof.unrunnable_checks(reason)
            run.workflow = unrunnable_workflow(reason)
        else:
            run.checks = ctx.run_proof(client, pilot)
            run.workflow = run_workflow(client, pilot, run.checks)

    after = sample_memory(ctx, run.container)
    if after is not None:
        run.samples_mib.append(after)
    gone = describe_gone(container_state(ctx, run.container), ctx.memory)
    if gone is not None:
        run.reasons.append(gone)
    if not run.samples_mib and gone is None:
        run.reasons.append("memory use could not be measured with docker stats")


def run_pilot(entry: dict[str, Any], ctx: Context) -> dict[str, Any]:
    """Run one pilot start to finish and return {"summary": ..., "detail": ...}. Always removes the container."""
    key = chart.pilot_key(entry)
    run = PilotRun(key=key, container=container_name(entry, ctx.run_id))
    try:
        _drive(entry, ctx, run)
    except Stopped:
        run.reasons.append("the run was interrupted before this pilot finished")
    except Exception as exc:
        run.reasons.append(f"gate error: {type(exc).__name__}: {exc}")
    finally:
        if run.started:
            ctx.containers.remove(run.container)
    outcome = finalize(entry, run, ctx)
    write_json(ctx.report_dir / f"{key}.json", outcome["detail"])
    return outcome


def finalize(entry: dict[str, Any], run: PilotRun, ctx: Context) -> dict[str, Any]:
    entries = [*run.checks, *run.workflow]
    counts = count_statuses(entries)
    reasons = list(run.reasons)
    for item in run.checks:
        if item.get("status") == "fail":
            reasons.append(f"check {item.get('id')} failed: {item.get('reason')}")
    for item in run.workflow:
        if item.get("status") == "fail":
            reasons.append(f"workflow {item.get('id')} failed: {item.get('reason')}")
    peak = round(max(run.samples_mib), 2) if run.samples_mib else 0.0
    if peak > ctx.memory_mib:
        reasons.append(f"peak memory {peak:g} MiB exceeds the {ctx.memory} budget ({ctx.memory_mib:g} MiB)")
    if not reasons and counts["pass"] == 0:
        reasons.append("no applicable check or workflow entry passed")
    status = "fail" if reasons else "pass"
    summary = {
        "key": run.key,
        "status": status,
        "checks": counts,
        "peak_memory_mb": peak,
        "startup_seconds": run.startup_seconds,
        "reasons": reasons,
    }
    detail = {
        **summary,
        "family": entry["family"],
        "platform": entry["platform"],
        "image": ctx.image,
        "container": run.container,
        "budget": {"memory": ctx.memory, "cpus": ctx.cpus},
        "memory_samples_mib": [round(sample, 2) for sample in run.samples_mib],
        "proof_checks": run.checks,
        "workflow": run.workflow,
    }
    return {"summary": ctx.redact.scrub(summary), "detail": ctx.redact.scrub(detail)}


# ---------------------------------------------------------------------------------------------------------------
# Reports and output
# ---------------------------------------------------------------------------------------------------------------


def write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def utc_timestamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_summary(
    *,
    image: str,
    image_id: str,
    image_size_bytes: int,
    memory: str,
    cpus: float,
    pilots: list[dict[str, Any]],
) -> dict[str, Any]:
    """The summary.json document. ``gatectl release-record`` reads these keys; do not add or rename any."""
    passed = sum(1 for pilot in pilots if pilot["status"] == "pass")
    return {
        "schema_version": SCHEMA_VERSION,
        "image": image,
        "image_id": image_id,
        "image_size_bytes": image_size_bytes,
        "budget": {"memory": memory, "cpus": cpus},
        "generated_at": utc_timestamp(),
        "verdict": "PASS" if pilots and passed == len(pilots) else "FAIL",
        "pilots_total": len(pilots),
        "pilots_passed": passed,
        "pilots": pilots,
    }


def format_row(pilot: dict[str, Any]) -> str:
    counts = pilot["checks"]
    return (
        f"{pilot['key']:<24} {pilot['status'].upper():<4}  startup {pilot['startup_seconds']:>6.1f} s  "
        f"peak {pilot['peak_memory_mb']:>7.1f} MiB  pass {counts['pass']:>2}  fail {counts['fail']:>2}  "
        f"n/a {counts['not_applicable']:>2}"
    )


def emit(ctx: Context | None, text: str, *, stream: Any = None) -> None:
    line = ascii_safe(ctx.redact.text(text) if ctx is not None else text)
    lock = ctx.print_lock if ctx is not None else threading.Lock()
    with lock:
        print(line, file=stream if stream is not None else sys.stdout, flush=True)


def inspect_image(deps: Deps, image: str) -> tuple[str, int]:
    """(image id, size in bytes) from docker, or a Refused when docker cannot describe the image."""
    identified = run_docker(deps, image_id_command(image)[1:])
    image_id = identified.stdout.strip().splitlines()[0].strip() if identified.stdout.strip() else ""
    if identified.rc != 0 or IMAGE_ID_PATTERN.fullmatch(image_id) is None:
        raise Refused(
            f"docker image inspect could not report a sha256:<64 hex> id for {image[:60]!r}; build or pull it first."
        )
    sized = run_docker(deps, image_size_command(image)[1:])
    try:
        size = int(sized.stdout.strip())
    except ValueError:
        size = -1
    if sized.rc != 0 or size < 0:
        raise Refused(f"docker image inspect could not report the size of {image[:60]!r}.")
    return image_id, size


def gateway_values(environ: Mapping[str, str]) -> list[str]:
    return [environ.get(name, "") for name in GATEWAY_ENV_NAMES]


def print_dry_run(ctx_args: argparse.Namespace, selected: list[dict[str, Any]], run_id: str) -> None:
    print(shlex.join(image_id_command(ctx_args.image)))
    print(shlex.join(image_size_command(ctx_args.image)))
    for entry in selected:
        name = container_name(entry, run_id)
        print(
            shlex.join(
                run_command(
                    ctx_args.image,
                    name,
                    entry,
                    ctx_args.memory,
                    ctx_args.cpus,
                    with_gateway=ctx_args.with_embedding_gateway,
                )
            )
        )
        print(shlex.join(port_command(name)))
        print(shlex.join(stats_command(name)))
        print(shlex.join(remove_command(name)))
    print(
        f"image_workflow_gate: dry run for {len(selected)} pilot(s); nothing was run and nothing was written.",
        file=sys.stderr,
    )


def main(argv: list[str] | None = None, deps: Deps | None = None) -> int:
    deps = deps or Deps()
    args = parse_args(argv)
    environ = deps.environ if deps.environ is not None else os.environ
    try:
        validate_image(args.image)
        try:
            selected = chart.select_pilots(chart.load_pilots(), args.only)
        except (ValueError, OSError) as exc:
            raise Refused(str(exc)) from exc
        if args.with_embedding_gateway:
            unset = [name for name in GATEWAY_ENV_NAMES if not environ.get(name)]
            if unset:
                raise Refused(
                    f"--with-embedding-gateway needs these variables set in this environment: {', '.join(unset)}. "
                    "Nothing was run."
                )
    except Refused as exc:
        print(f"image_workflow_gate: {exc}", file=sys.stderr)
        return EXIT_REFUSED

    run_id = deps.run_id or uuid.uuid4().hex[:8]
    if args.dry_run:
        print_dry_run(args, selected, run_id)
        return 0

    redact = Redactor(gateway_values(environ))
    try:
        image_id, image_size = inspect_image(deps, args.image)
    except Refused as exc:
        print(f"image_workflow_gate: {exc}", file=sys.stderr)
        return EXIT_FAILED

    containers = Containers(deps)
    ctx = Context(
        image=args.image,
        memory=args.memory,
        memory_mib=parse_memory_limit_mib(args.memory),
        cpus=args.cpus,
        ready_timeout=args.ready_timeout,
        fixtures=args.fixtures,
        report_dir=args.report_dir,
        with_gateway=args.with_embedding_gateway,
        run_id=run_id,
        deps=deps,
        proof=_load_proof_module(),
        containers=containers,
        redact=redact,
    )
    args.report_dir.mkdir(parents=True, exist_ok=True)

    outcomes: list[dict[str, Any]] = []
    futures: list[Any] = []
    executor = ThreadPoolExecutor(max_workers=max(1, min(args.parallel, len(selected))))
    try:
        futures = [executor.submit(run_pilot, entry, ctx) for entry in selected]
        for future in futures:
            outcome = future.result()
            outcomes.append(outcome)
            summary = outcome["summary"]
            emit(
                ctx, f"image_workflow_gate: {summary['key']} finished: {summary['status']}", stream=sys.stderr
            )
    except KeyboardInterrupt:
        ctx.stop.set()
        for future in futures:
            future.cancel()
        emit(
            ctx,
            "image_workflow_gate: interrupted; removing every container this run started",
            stream=sys.stderr,
        )
        containers.remove_all()  # first, so a pilot still running fails fast instead of holding the exit
        return EXIT_INTERRUPTED
    finally:
        ctx.stop.set()
        executor.shutdown(wait=True, cancel_futures=True)
        containers.remove_all()

    pilots = [outcome["summary"] for outcome in outcomes]
    summary_doc = redact.scrub(
        build_summary(
            image=args.image,
            image_id=image_id,
            image_size_bytes=image_size,
            memory=args.memory,
            cpus=args.cpus,
            pilots=pilots,
        )
    )
    write_json(args.report_dir / SUMMARY_NAME, summary_doc)

    for pilot in pilots:
        emit(ctx, format_row(pilot))
        for reason in pilot["reasons"]:
            emit(ctx, f"    reason: {reason}")
    emit(
        ctx,
        f"verdict: {summary_doc['verdict']} ({summary_doc['pilots_passed']} of {summary_doc['pilots_total']} pilots "
        f"passed); image {image_id}; reports: {args.report_dir}",
    )
    return 0 if summary_doc["verdict"] == "PASS" else EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
