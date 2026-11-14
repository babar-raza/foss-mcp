#!/usr/bin/env python3
"""Regenerate committed demo data locally, with no Kubernetes cluster (G3/TC-829 / REQ-G3-017).

Usage:
    python scripts/release/regenerate_local.py (--cohort C0[,C1...] | --only PILOT[,PILOT...]) [--image IMAGE]
        [--work-dir DIR] [--parallel N] [--source-date-epoch EPOCH] [--dry-run] [--no-copy-back] [--keep-work]
        [--accept-regression KIND:OLD->NEW ... --reason TEXT]

A cohort-driven replacement for the cluster path (install_pilots + snapshot_demo_from_cluster +
pull_verification_cache). For every selected pilot it:

  1. renders the pilot's ingestion Job with `helm template` (values from scripts/deploy/pilots.py write_values) and
     takes the Job container's `sh -c` argument string from it - the chart stays the only place the chain is spelled;
  2. keeps ONLY the `build_chunks.py ... && ingest.py ...` part of that string. The sidecar tail
     (fetch_product_reference / verify_product_reference_install / fetch_recent_releases) is DROPPED: sidecars are
     committed history (decision J) and are never regenerated here. `--content-derived-version
     --source-date-epoch N` is added to ingest.py, `--vector-scope examples --vector-decimals 5` are enforced;
  3. runs that part with `docker run --entrypoint sh` in the ingestion image against a per-pilot work directory
     OUTSIDE the repository and OneDrive, SEEDED with a copy of the committed infra/demo-manifests/<scope> so
     ingest.py's TC-510 shrink guard has a baseline. Memory caps come from values.yaml (default 4g; ingestionMemoryLimit
     overrides, e.g. 8g words/net, html/python, page/python; 10g pdf/cpp, slides/cpp), `--cpus 2`, `--parallel N` pilots
     at a time (default 2);
  4. REFUSES to publish a generation unless every vector index it wrote is gateway-dimension (4096): 64-dim hashing
     vectors make find_examples permanently lexical-only (the 2026-11 regression);
  5. copies back ONLY active_pointer.json and the active generation file (never a sidecar) into
     infra/demo-manifests/<scope>, through snapshot_demo_from_cluster's own `--from-dir` code path, and merges
     verification_cache_<pilot>.json into tests/fixtures/verified_examples/ through pull_verification_cache's
     `--from-dir` path (build_chunks.merge_verification_cache; failing entries are preserved);
  6. runs the TC-346 compaction over the manifest root and its --check.

Embeddings: the operator's laptop variables GPT_OSS_ENDPOINT / GPT_OSS_API_KEY are mapped, by NAME, to
FOSS_MCP_EMBEDDING_GATEWAY_ENDPOINT / FOSS_MCP_EMBEDDING_GATEWAY_API_KEY for the container (`docker run -e NAME`: the
command line never carries a value; the value travels in the docker client's environment). The dimension variable is
never set (default 4096). With neither pair present a real run exits 3 before running anything. GITHUB_TOKEN (or
GH_TOKEN) is forwarded the same way when present. No value of any of these is ever printed, logged or written.

--dry-run prints every command (credentials shown as variable names) and changes nothing in the repository or the work
directory; it still runs the read-only `helm template` when helm is on PATH (run scripts/toolchain/activate.ps1 first).
--no-copy-back stops after the ingestion and the dimension check, leaving the work directory for inspection.

Exit codes: 0 every selected pilot regenerated and compact; 1 at least one pilot (or the compaction) failed;
2 bad arguments; 3 refused before running anything (no gateway variables, helm/docker/image missing, unsafe work dir).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
_RELEASE_DIR = REPO_ROOT / "scripts" / "release"
_DEPLOY_DIR = REPO_ROOT / "scripts" / "deploy"
for _dir in (_RELEASE_DIR, _DEPLOY_DIR):
    if str(_dir) not in sys.path:
        sys.path.insert(0, str(_dir))

import compact_demo_manifests as compact  # noqa: E402  (also puts src/ on sys.path)
import pilots  # noqa: E402
import pull_verification_cache as pull  # noqa: E402  (also puts infra/ on sys.path)
import snapshot_demo_from_cluster as snap  # noqa: E402

CHART = REPO_ROOT / "infra" / "helm" / "foss-mcp"
DEFAULT_IMAGE = "foss-mcp-ingestion:regen"
# 2026-11-14T00:00:00Z. Fixed so that a re-run of byte-identical chunks mints the identical generation id.
DEFAULT_SOURCE_DATE_EPOCH = 1794614400
DEFAULT_PARALLEL = 2
DEFAULT_PILOT_TIMEOUT_MINUTES = 120
EXPECTED_VECTOR_DIMENSION = 4096
CONTAINER_MANIFESTS = "/data/manifests"
CONTAINER_HOME = "/tmp/h"
CPUS = "2"
HELM_RELEASE_SECRET = "gw"  # only makes the chart render the gateway provider; the name is never used

EXIT_FAILED = 1
EXIT_ARGS = 2
EXIT_REFUSED = 3

# Cohorts, smallest blast radius first. Every one of the 32 pilots is in exactly one cohort.
COHORTS: dict[str, tuple[str, ...]] = {
    "C0": ("imaging_net",),
    "C1": (
        "slides_python",
        "words_python",
        "cells_python",
        "pdf_python",
        "email_python",
        "barcode_python",
        "note_python",
        "html_python",
        "page_python",
        "3d_python",
        "tex_python",
    ),
    "C2": ("pdf_net", "slides_net", "words_net", "cells_net", "email_net", "3d_net"),
    "C3": ("pdf_java", "slides_java", "cells_java", "3d_java"),
    "C4": ("pdf_typescript", "cells_typescript", "3d_typescript"),
    "C5": ("pdf_go", "cells_go", "cells_rust", "pdf_cpp", "cells_cpp", "slides_cpp", "email_cpp"),
}

# (embedding-gateway env var the container reads) <- (operator laptop var), in order of preference.
GATEWAY_ENDPOINT_ENV = "FOSS_MCP_EMBEDDING_GATEWAY_ENDPOINT"
GATEWAY_API_KEY_ENV = "FOSS_MCP_EMBEDDING_GATEWAY_API_KEY"
GATEWAY_DIMENSION_ENV = "FOSS_MCP_EMBEDDING_GATEWAY_DIMENSION"
GATEWAY_SOURCES: tuple[tuple[str, str], ...] = (
    (GATEWAY_ENDPOINT_ENV, GATEWAY_API_KEY_ENV),
    ("GPT_OSS_ENDPOINT", "GPT_OSS_API_KEY"),
)
GITHUB_TOKEN_SOURCES = ("GITHUB_TOKEN", "GH_TOKEN")
SECRET_ENV_NAMES = (
    GATEWAY_ENDPOINT_ENV,
    GATEWAY_API_KEY_ENV,
    "GPT_OSS_ENDPOINT",
    "GPT_OSS_API_KEY",
    *GITHUB_TOKEN_SOURCES,
)
GATEWAY_PROVIDER = "foss_mcp.indexing.gateway_embedding_provider:GatewayEmbeddingProvider"
SIDECAR_STEPS = (
    "fetch_product_reference.py",
    "verify_product_reference_install.py",
    "fetch_recent_releases.py",
)

_ACCEPT_REGRESSION = re.compile(r"(symbols|docs|examples|total):\d+->\d+")
_MEMORY = re.compile(r"(\d+)(Gi|Mi|G|M|g|m)")
_SECRET_MIN_LENGTH = 4
OUTPUT_TAIL_LINES = 20

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class RegenError(Exception):
    """One pilot (or the whole run) cannot proceed; the message says why."""


class Refusal(Exception):
    """The run is refused before anything executes (exit 3)."""


# ---------------------------------------------------------------------------------------------------
# selection and static facts
# ---------------------------------------------------------------------------------------------------


def cohort_of(key: str) -> str:
    for name, members in COHORTS.items():
        if key in members:
            return name
    raise ValueError(f"pilot {key} is in no cohort")


def _split_names(values: Sequence[str]) -> list[str]:
    return [part.strip() for value in values for part in value.split(",") if part.strip()]


def select(only: Sequence[str], cohorts: Sequence[str]) -> list[dict[str, Any]]:
    """The chart's pilots named by --only or by --cohort, in the chart's own order. ValueError on a typo."""
    names = _split_names(only)
    for cohort in _split_names(cohorts):
        if cohort not in COHORTS:
            raise ValueError(f"unknown cohort {cohort!r}; known: {', '.join(COHORTS)}")
        names.extend(COHORTS[cohort])
    if not names:
        raise ValueError("select pilots with --cohort C0..C5 or --only PILOT[,PILOT...]")
    return pilots.select_pilots(pilots.load_pilots(), sorted(set(names)))


def memory_to_docker(quantity: str) -> str:
    """A Kubernetes quantity (4Gi, 512Mi) as a `docker run -m` value (4g, 512m)."""
    match = _MEMORY.fullmatch(str(quantity).strip())
    if match is None:
        raise RegenError(f"cannot read the memory quantity {quantity!r}")
    return f"{match.group(1)}{match.group(2)[0].lower()}"


def default_memory(values_path: Path = pilots.CHART_VALUES) -> str:
    values = yaml.safe_load(Path(values_path).read_text(encoding="utf-8")) or {}
    try:
        return str(values["ingestion"]["resources"]["limits"]["memory"])
    except (KeyError, TypeError) as exc:
        raise RegenError(f"{values_path} has no ingestion.resources.limits.memory") from exc


def memory_cap(pilot: dict[str, Any], values_path: Path = pilots.CHART_VALUES) -> str:
    """The container memory cap for a pilot, in docker syntax: its ingestionMemoryLimit, else the chart default."""
    return memory_to_docker(pilot.get("ingestionMemoryLimit") or default_memory(values_path))


# ---------------------------------------------------------------------------------------------------
# credentials - handled by name only
# ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Credentials:
    """Which operator variables feed the container's gateway pair, and the child-process environment that carries
    their values. ``source_names`` and ``child_env`` are the only places a value ever lives."""

    endpoint_source: str
    api_key_source: str
    github_source: str | None
    child_env: Mapping[str, str] = field(repr=False)

    def secret_values(self) -> dict[str, str]:
        return {name: value for name, value in self.child_env.items() if name in SECRET_ENV_NAMES}


def gateway_credentials(environ: Mapping[str, str]) -> Credentials:
    """Map the laptop's GPT_OSS_* (or already-correct FOSS_MCP_EMBEDDING_GATEWAY_*) variables onto the container's
    gateway pair. Raises Refusal naming the variables (never a value) when no complete pair exists."""
    chosen: tuple[str, str] | None = None
    for endpoint_name, key_name in GATEWAY_SOURCES:
        if environ.get(endpoint_name) and environ.get(key_name):
            chosen = (endpoint_name, key_name)
            break
    if chosen is None:
        present = [n for pair in GATEWAY_SOURCES for n in pair if environ.get(n)]
        raise Refusal(
            "refusing to ingest: no embedding gateway is configured. Set GPT_OSS_ENDPOINT and GPT_OSS_API_KEY "
            f"(or {GATEWAY_ENDPOINT_ENV} and {GATEWAY_API_KEY_ENV}). Without the gateway the ingestion would "
            "bake 64-dim hashing vectors, which leave find_examples permanently lexical-only. "
            f"Present variables: {', '.join(present) if present else 'none of them'}."
        )
    github = next((name for name in GITHUB_TOKEN_SOURCES if environ.get(name)), None)
    child_env = {k: v for k, v in environ.items() if k != GATEWAY_DIMENSION_ENV}
    child_env[GATEWAY_ENDPOINT_ENV] = environ[chosen[0]]
    child_env[GATEWAY_API_KEY_ENV] = environ[chosen[1]]
    if github is not None:
        child_env["GITHUB_TOKEN"] = environ[github]
    return Credentials(chosen[0], chosen[1], github, child_env)


def redact(text: str, secrets: Mapping[str, str]) -> str:
    """``text`` with every secret value replaced by its variable NAME."""
    for name, value in sorted(secrets.items(), key=lambda item: -len(item[1])):
        if len(value) >= _SECRET_MIN_LENGTH:
            text = text.replace(value, f"<{name}>")
    return text


# ---------------------------------------------------------------------------------------------------
# helm template -> the chain to run in the container
# ---------------------------------------------------------------------------------------------------


def helm_template_command(
    pilot: dict[str, Any], tag: str, values_file: Path, chart: Path = CHART
) -> list[str]:
    return [
        "helm",
        "template",
        pilots.release_name(pilot),
        str(chart),
        "--set",
        f"image.tag={tag}",
        "--set-string",
        f"embeddingGateway.existingSecret={HELM_RELEASE_SECRET}",
        "-f",
        str(values_file),
    ]


def extract_job_args(rendered: str) -> str:
    """The `sh -c` argument string of the single ingestion Job in ``rendered`` (helm template output)."""
    jobs = [d for d in yaml.safe_load_all(rendered) if isinstance(d, dict) and d.get("kind") == "Job"]
    if len(jobs) != 1:
        raise RegenError(f"helm template rendered {len(jobs)} Jobs, expected exactly 1")
    try:
        container = jobs[0]["spec"]["template"]["spec"]["containers"][0]
        command, args = container["command"], container["args"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RegenError("the rendered Job has no container command/args") from exc
    if (
        command != ["sh", "-c"]
        or not isinstance(args, list)
        or len(args) != 1
        or not isinstance(args[0], str)
    ):
        raise RegenError("the rendered Job's container is not `sh -c <one string>`")
    return args[0]


def _split_chain(tokens: list[str]) -> list[list[str]]:
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token == "&&":
            segments.append([])
        else:
            segments[-1].append(token)
    return segments


def _set_option(segment: list[str], flag: str, value: str) -> list[str]:
    out = list(segment)
    if flag in out:
        index = out.index(flag)
        if index + 1 >= len(out):
            raise RegenError(f"{flag} has no value in the rendered command")
        out[index + 1] = value
        return out
    return [*out, flag, value]


def _option_value(segment: list[str], flag: str) -> str | None:
    if flag in segment and segment.index(flag) + 1 < len(segment):
        return segment[segment.index(flag) + 1]
    return None


def build_chain(
    job_args: str,
    *,
    epoch: int,
    accept_regressions: Sequence[str] = (),
    reason: str | None = None,
) -> tuple[str, list[str]]:
    """(the shell chain to run in the container, the names of the sidecar steps dropped).

    The chain is `mkdir -p HOME && build_chunks.py ... && ingest.py ...`: the rendered build_chunks and ingest.py
    segments, with ingest.py forced onto the gateway provider and `--vector-scope examples --vector-decimals 5`, and
    `--content-derived-version --source-date-epoch EPOCH` added. Every later segment of the Job is dropped.
    """
    try:
        segments = _split_chain(shlex.split(job_args))
    except ValueError as exc:
        raise RegenError(f"cannot parse the rendered Job command ({exc})") from exc
    if len(segments) < 2 or segments[0][:2] != ["python", "/app/infra/build_chunks.py"]:
        raise RegenError("the rendered Job does not start with `python /app/infra/build_chunks.py`")
    if segments[1][:2] != ["python", "/app/infra/ingest.py"]:
        raise RegenError("the rendered Job's second step is not `python /app/infra/ingest.py`")
    build, ingest = segments[0], list(segments[1])
    if _option_value(ingest, "--embedding-provider") != GATEWAY_PROVIDER:
        raise RegenError(
            "the rendered ingest.py is not on the gateway embedding provider "
            f"({_option_value(ingest, '--embedding-provider')!r}): refusing to build hashing vectors"
        )
    dropped = [
        name for segment in segments[2:] for name in SIDECAR_STEPS if any(name in token for token in segment)
    ]
    ingest = _set_option(ingest, "--vector-scope", "examples")
    ingest = _set_option(ingest, "--vector-decimals", "5")
    if "--content-derived-version" not in ingest:
        ingest.append("--content-derived-version")
    ingest = _set_option(ingest, "--source-date-epoch", str(epoch))
    for item in accept_regressions:
        ingest += ["--accept-regression", item]
    if accept_regressions:
        if not reason:
            raise RegenError("--accept-regression needs --reason")
        ingest = _set_option(ingest, "--reason", reason)
    chain = " && ".join(
        [
            f"mkdir -p {CONTAINER_HOME}",
            shlex.join(build),
            shlex.join(ingest),
        ]
    )
    return chain, dropped


# ---------------------------------------------------------------------------------------------------
# docker
# ---------------------------------------------------------------------------------------------------


def container_name(key: str, run_id: str) -> str:
    return f"foss-mcp-regen-{key}-{run_id}"


def docker_run_command(
    *,
    image: str,
    memory: str,
    work_pilot_dir: Path,
    chain: str,
    name: str,
    credentials: Credentials | None,
) -> list[str]:
    """`docker run` for one pilot. Credentials appear as `-e NAME` only; the values ride in the client's environment.
    The dimension variable is never forwarded, so the provider keeps its 4096 default."""
    cmd = ["docker", "run", "--rm", "--name", name, "-m", memory, "--cpus", CPUS]
    cmd += ["-e", GATEWAY_ENDPOINT_ENV, "-e", GATEWAY_API_KEY_ENV]
    if credentials is None or credentials.github_source is not None:
        cmd += ["-e", "GITHUB_TOKEN"]
    cmd += ["-e", f"HOME={CONTAINER_HOME}", "-e", f"CARGO_HOME={CONTAINER_HOME}/.cargo"]
    cmd += ["-v", f"{work_pilot_dir}:{CONTAINER_MANIFESTS}", "--entrypoint", "sh", image, "-c", chain]
    return cmd


def image_tag(image: str) -> str:
    name = image.rsplit("/", 1)[-1]
    if ":" not in name or "@" in image:
        raise Refusal(
            f"--image {image!r} must be NAME:TAG (a pinned tag, never a digest or an implicit latest)"
        )
    tag = name.rsplit(":", 1)[1]
    if not tag or tag == "latest":
        raise Refusal(f"--image {image!r}: the tag must be explicit and never `latest`")
    return tag


# ---------------------------------------------------------------------------------------------------
# work directory
# ---------------------------------------------------------------------------------------------------


def default_work_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "foss-mcp-regen"


def check_work_dir(work_dir: Path) -> Path:
    """The resolved work directory, or Refusal if it sits inside the repository or under OneDrive (file locks)."""
    resolved = work_dir.resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise Refusal(f"--work-dir {resolved} is inside the repository; use a directory outside it")
    if any("onedrive" in part.lower() for part in resolved.parts):
        raise Refusal(f"--work-dir {resolved} is under OneDrive, whose file locks break the ingestion")
    return resolved


def seed_work_dir(pilot: dict[str, Any], manifests_root: Path, work_pilot_dir: Path) -> Path:
    """A fresh ``work_pilot_dir`` holding a copy of the committed scope directory, so ingest.py's shrink guard (TC-510)
    has a baseline. Returns the copied scope directory."""
    scope = "__".join((pilot["family"], pilot["platform"], pilot["sourceKind"]))
    committed = manifests_root / scope
    if not committed.is_dir():
        raise RegenError(
            f"{scope}: no committed baseline at {committed}; the shrink guard would have nothing to guard"
        )
    if work_pilot_dir.exists():
        shutil.rmtree(work_pilot_dir)
    work_pilot_dir.mkdir(parents=True)
    target = work_pilot_dir / scope
    shutil.copytree(committed, target)
    return target


def check_vector_dimension(doc: Mapping[str, Any], expected: int = EXPECTED_VECTOR_DIMENSION) -> int:
    """The number of vector points, after asserting the index and every point is ``expected``-dimensional.
    Raises RegenError otherwise - this is what keeps 64-dim hashing vectors out of the demo data."""
    payload = doc.get("payload") if isinstance(doc, Mapping) else None
    index = payload.get("vector_index") if isinstance(payload, Mapping) else None
    if not isinstance(index, Mapping):
        raise RegenError("the generation has no vector index to verify")
    if index.get("dimension") != expected:
        raise RegenError(
            f"vector index dimension is {index.get('dimension')!r}, not {expected}: "
            "these are not gateway vectors (a hashing provider would make find_examples lexical-only)"
        )
    points = index.get("points")
    if not isinstance(points, list):
        raise RegenError("the vector index has no points list")
    for point in points:
        vector = point.get("vector") if isinstance(point, Mapping) else None
        if not isinstance(vector, list) or len(vector) != expected:
            size = len(vector) if isinstance(vector, list) else None
            raise RegenError(f"a vector point has length {size!r}, not {expected}")
    return len(points)


def verify_work_output(work_pilot_dir: Path, scope: str) -> tuple[str, int]:
    """(generation file name, vector points) of the active generation the ingestion left in the work directory."""
    scope_dir = work_pilot_dir / scope
    try:
        generation_id, path = compact.active_generation_path(scope_dir)
    except compact.CompactionError as exc:
        raise RegenError(str(exc)) from exc
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RegenError(f"{scope}: {path.name} is unreadable ({type(exc).__name__})") from exc
    if doc.get("generation_id") != generation_id:
        raise RegenError(f"{scope}: {path.name} does not carry the generation the pointer names")
    return path.name, check_vector_dimension(doc)


# ---------------------------------------------------------------------------------------------------
# the plan and its execution
# ---------------------------------------------------------------------------------------------------


@dataclass
class Plan:
    pilot: dict[str, Any]
    key: str
    scope: str
    cohort: str
    helm_command: list[str]
    chain: str
    dropped: list[str]
    memory: str
    work_pilot_dir: Path
    docker_command: list[str]
    container: str


@dataclass
class Outcome:
    key: str
    ok: bool = False
    message: str = ""
    generation_file: str = ""
    vector_points: int = 0
    log_path: Path | None = None


@dataclass
class Context:
    image: str
    tag: str
    work_dir: Path
    manifests_root: Path
    cache_dest: Path
    epoch: int
    parallel: int
    timeout_seconds: int
    accept_regressions: list[str]
    reason: str | None
    dry_run: bool
    copy_back: bool
    keep_work: bool
    run: Runner
    credentials: Credentials | None
    run_id: str
    values_dir: Path
    chart: Path = CHART
    print_lock: threading.Lock = field(default_factory=threading.Lock)

    def secrets(self) -> dict[str, str]:
        return self.credentials.secret_values() if self.credentials else {}

    def say(self, text: str, *, err: bool = False) -> None:
        with self.print_lock:
            print(redact(text, self.secrets()), file=sys.stderr if err else sys.stdout, flush=True)


def build_plan(pilot: dict[str, Any], ctx: Context, *, helm_available: bool) -> Plan:
    key = pilots.pilot_key(pilot)
    scope = "__".join((pilot["family"], pilot["platform"], pilot["sourceKind"]))
    (values_file,) = pilots.write_values([pilot], ctx.values_dir)
    helm_command = helm_template_command(pilot, ctx.tag, values_file, ctx.chart)
    if helm_available:
        proc = ctx.run(helm_command, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RegenError(f"helm template failed (rc {proc.returncode}): {_tail(proc.stderr, ctx)}")
        job_args = extract_job_args(proc.stdout)
        chain, dropped = build_chain(
            job_args, epoch=ctx.epoch, accept_regressions=ctx.accept_regressions, reason=ctx.reason
        )
    else:
        chain, dropped = "<the build_chunks.py && ingest.py part of the helm-rendered Job args>", []
    work_pilot_dir = ctx.work_dir / key
    name = container_name(key, ctx.run_id)
    memory = memory_cap(pilot)
    return Plan(
        pilot=pilot,
        key=key,
        scope=scope,
        cohort=cohort_of(key),
        helm_command=helm_command,
        chain=chain,
        dropped=dropped,
        memory=memory,
        work_pilot_dir=work_pilot_dir,
        docker_command=docker_run_command(
            image=ctx.image,
            memory=memory,
            work_pilot_dir=work_pilot_dir,
            chain=chain,
            name=name,
            credentials=ctx.credentials,
        ),
        container=name,
    )


def _tail(text: str | None, ctx: Context, lines: int = OUTPUT_TAIL_LINES) -> str:
    body = redact(text or "", ctx.secrets()).strip().splitlines()
    return " | ".join(line.strip() for line in body[-lines:])


def describe(plan: Plan, ctx: Context) -> str:
    sidecars = (
        ", ".join(plan.dropped)
        if plan.dropped
        else "fetch_product_reference, fetch_recent_releases (dropped)"
    )
    return "\n".join(
        [
            f"== {plan.key} (cohort {plan.cohort}, memory {plan.memory}, cpus {CPUS}) ==",
            "  1. " + shlex.join(plan.helm_command),
            f"  2. seed {plan.work_pilot_dir / plan.scope} <- copy of {ctx.manifests_root / plan.scope}",
            "  3. " + shlex.join(plan.docker_command),
            f"     sidecar steps dropped from the Job: {sidecars}",
            f"  4. assert every vector in {plan.scope} is {EXPECTED_VECTOR_DIMENSION}-dimensional",
            f"  5. copy active_pointer.json + the active generation only -> {ctx.manifests_root / plan.scope}",
            f"     merge verification_cache_{plan.key}.json -> {ctx.cache_dest / (plan.key + '.json')}",
        ]
    )


def execute_pilot(plan: Plan, ctx: Context) -> Outcome:
    """Seed, ingest in the container and verify the vector dimension. Never touches the repository."""
    outcome = Outcome(key=plan.key)
    try:
        seed_work_dir(plan.pilot, ctx.manifests_root, plan.work_pilot_dir)
        log_path = ctx.work_dir / f"{plan.key}.log"
        outcome.log_path = log_path
        ctx.say(f"{plan.key}: ingesting in {ctx.image} (memory {plan.memory}) ...")
        if ctx.credentials is None:
            raise RegenError("no gateway credentials were resolved")
        try:
            proc = ctx.run(
                plan.docker_command,
                capture_output=True,
                text=True,
                check=False,
                env=dict(ctx.credentials.child_env),
                timeout=ctx.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            ctx.run(["docker", "rm", "-f", plan.container], capture_output=True, text=True, check=False)
            raise RegenError(
                f"the container did not finish within {ctx.timeout_seconds}s and was removed"
            ) from exc
        log_path.write_text(
            redact((proc.stdout or "") + "\n" + (proc.stderr or ""), ctx.secrets()), encoding="utf-8"
        )
        if proc.returncode != 0:
            raise RegenError(
                f"the ingestion container exited {proc.returncode}: "
                f"{_tail((proc.stderr or '') + chr(10) + (proc.stdout or ''), ctx)} (full log: {log_path})"
            )
        outcome.generation_file, outcome.vector_points = verify_work_output(plan.work_pilot_dir, plan.scope)
        outcome.ok = True
    except (RegenError, OSError, shutil.Error, subprocess.SubprocessError) as exc:
        outcome.ok = False
        outcome.message = str(exc) or type(exc).__name__
    return outcome


def copy_back(plan: Plan, ctx: Context) -> tuple[bool, str]:
    """Copy the verified output into the repository: pointer + active generation through the snapshot tool's
    --from-dir path (no sidecars), the verification cache through the pull tool's --from-dir path."""
    result = snap.snapshot_pilot(
        plan.pilot,
        "",
        ctx.manifests_root,
        allow_missing_sidecars=True,
        dry_run=False,
        from_dir=plan.work_pilot_dir,
        include_sidecars=False,
    )
    if not result.ok:
        return False, result.message
    pulled = pull.pull_pilot(plan.pilot, ctx.cache_dest, from_dir=plan.work_pilot_dir)
    if pulled.status == "failed":
        return False, f"verification cache: {pulled.message}"
    return True, f"{snap.format_result(result)}; verification cache {pulled.status}"


def run_compaction(ctx: Context) -> int:
    """The TC-346 compaction over the manifest root, then its --check. 0 when the root is compact."""
    snap.compact_root(ctx.manifests_root)
    return compact.run(ctx.manifests_root, check=True, dry_run=False)


# ---------------------------------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------------------------------


def regenerate(selected: list[dict[str, Any]], ctx: Context, *, helm_available: bool) -> int:
    try:
        plans = [build_plan(p, ctx, helm_available=helm_available) for p in selected]
    except RegenError as exc:
        ctx.say(f"regenerate_local: {exc}", err=True)
        return EXIT_FAILED
    if ctx.dry_run:
        for plan in plans:
            ctx.say(describe(plan, ctx))
        ctx.say(
            f"== afterwards ==\n  {sys.executable} scripts/release/compact_demo_manifests.py --root "
            f"{ctx.manifests_root}\n  {sys.executable} scripts/release/compact_demo_manifests.py --root "
            f"{ctx.manifests_root} --check\ndry run: nothing was run, written or copied"
        )
        return 0

    ctx.work_dir.mkdir(parents=True, exist_ok=True)
    failed: list[str] = []
    copied = 0
    with ThreadPoolExecutor(max_workers=max(1, ctx.parallel)) as pool:
        futures = [(plan, pool.submit(execute_pilot, plan, ctx)) for plan in plans]
        for plan, future in futures:
            outcome = future.result()
            if not outcome.ok:
                failed.append(plan.key)
                ctx.say(f"{plan.key}: FAILED {outcome.message}")
                continue
            if not ctx.copy_back:
                ctx.say(
                    f"{plan.key}: ingested, {outcome.vector_points} vector point(s) at 4096-dim (not copied back)"
                )
                continue
            ok, message = copy_back(plan, ctx)
            if not ok:
                failed.append(plan.key)
                ctx.say(f"{plan.key}: FAILED {message}")
                continue
            copied += 1
            ctx.say(f"{plan.key}: ok {message}")
            if not ctx.keep_work:
                shutil.rmtree(plan.work_pilot_dir, ignore_errors=True)

    compaction_rc = 0
    if copied:
        try:
            compaction_rc = run_compaction(ctx)
        except compact.CompactionError as exc:
            compaction_rc = 1
            ctx.say(f"regenerate_local: compaction failed: {exc}", err=True)
    ctx.say(
        f"total: {len(plans) - len(failed)} of {len(plans)} pilot(s) ok, {len(failed)} failed"
        f"{' (' + ', '.join(failed) + ')' if failed else ''}; compaction check rc {compaction_rc}"
    )
    return EXIT_FAILED if failed or compaction_rc else 0


def default_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    kwargs.setdefault("encoding", "utf-8")
    kwargs.setdefault("errors", "replace")
    return subprocess.run(cmd, **kwargs)


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Regenerate committed demo data locally (helm template + docker run), with no cluster.",
        epilog="example: python scripts/release/regenerate_local.py --cohort C0 --dry-run",
    )
    parser.add_argument(
        "--cohort",
        action="append",
        default=[],
        metavar="C0..C5",
        help=(
            "cohort(s) to regenerate, comma separated or repeated: "
            + "; ".join(f"{k} {len(v)} pilot(s)" for k, v in COHORTS.items())
        ),
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="PILOT[,PILOT...]",
        help="pilots in family_platform form, e.g. pdf_net (comma separated or repeated)",
    )
    parser.add_argument(
        "--image", default=DEFAULT_IMAGE, help="ingestion image NAME:TAG (default: %(default)s)"
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=None,
        help="scratch directory outside the repo and OneDrive (default: %%LOCALAPPDATA%%/foss-mcp-regen)",
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=DEFAULT_PARALLEL,
        help="pilots ingesting at once (default: %(default)s)",
    )
    parser.add_argument(
        "--source-date-epoch",
        type=int,
        default=DEFAULT_SOURCE_DATE_EPOCH,
        help="fixed epoch stamped into content-derived generation versions (default: %(default)s)",
    )
    parser.add_argument(
        "--pilot-timeout-minutes",
        type=int,
        default=DEFAULT_PILOT_TIMEOUT_MINUTES,
        help="per-pilot container limit (default: %(default)s)",
    )
    parser.add_argument(
        "--root", type=Path, default=compact.DEFAULT_ROOT, help="demo manifest root (default: %(default)s)"
    )
    parser.add_argument(
        "--cache-dest",
        type=Path,
        default=pull.DEFAULT_DEST,
        help="verification cache directory (default: %(default)s)",
    )
    parser.add_argument(
        "--accept-regression",
        action="append",
        default=[],
        metavar="KIND:OLD->NEW",
        help="pass a deliberate shrink through to ingest.py's TC-510 guard; repeatable, needs --reason",
    )
    parser.add_argument(
        "--reason", default=None, help="why the shrink is accepted (with --accept-regression)"
    )
    parser.add_argument("--dry-run", action="store_true", help="print every command, change nothing")
    parser.add_argument(
        "--no-copy-back",
        action="store_true",
        help="stop after ingest + dimension check; leave the repo untouched",
    )
    parser.add_argument(
        "--keep-work",
        action="store_true",
        help="keep each pilot's work directory after a successful copy-back",
    )
    args = parser.parse_args(argv)
    if args.parallel < 1:
        parser.error("--parallel must be at least 1")
    if args.pilot_timeout_minutes < 1:
        parser.error("--pilot-timeout-minutes must be at least 1")
    for item in args.accept_regression:
        if _ACCEPT_REGRESSION.fullmatch(item) is None:
            parser.error(f"--accept-regression {item!r} is not KIND:OLD->NEW (symbols|docs|examples|total)")
    if args.accept_regression and not args.reason:
        parser.error("--accept-regression needs --reason")
    return args


def main(
    argv: Sequence[str] | None = None,
    *,
    run: Runner | None = None,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> int:
    args = parse_args(argv)
    environ = os.environ if environ is None else environ
    runner = run or default_run
    try:
        selected = select(args.only, args.cohort)
    except ValueError as exc:
        print(f"regenerate_local: {exc}", file=sys.stderr)
        return EXIT_ARGS

    try:
        tag = image_tag(args.image)
        work_dir = check_work_dir(args.work_dir or default_work_dir())
        credentials: Credentials | None = None
        try:
            credentials = gateway_credentials(environ)
        except Refusal:
            if not args.dry_run:
                raise
        helm_available = which("helm") is not None
        if not args.dry_run:
            if not helm_available:
                raise Refusal(
                    "helm is not on PATH; run scripts/toolchain/activate.ps1 in the same shell command first"
                )
            if which("docker") is None:
                raise Refusal("docker is not on PATH")
            probe = runner(
                ["docker", "image", "inspect", args.image], capture_output=True, text=True, check=False
            )
            if probe.returncode != 0:
                raise Refusal(
                    f"the ingestion image {args.image} does not exist; build it first: "
                    f"docker build -f Dockerfile.ingestion -t {args.image} ."
                )
    except Refusal as exc:
        print(f"regenerate_local: {exc}", file=sys.stderr)
        return EXIT_REFUSED

    with tempfile.TemporaryDirectory(prefix="foss-mcp-regen-values-") as values_dir:
        ctx = Context(
            image=args.image,
            tag=tag,
            work_dir=work_dir,
            manifests_root=args.root,
            cache_dest=args.cache_dest,
            epoch=args.source_date_epoch,
            parallel=args.parallel,
            timeout_seconds=args.pilot_timeout_minutes * 60,
            accept_regressions=list(args.accept_regression),
            reason=args.reason,
            dry_run=args.dry_run,
            copy_back=not args.no_copy_back,
            keep_work=args.keep_work,
            run=runner,
            credentials=credentials,
            run_id=f"{os.getpid()}",
            values_dir=Path(values_dir),
        )
        if args.dry_run:
            gateway = (
                f"{credentials.endpoint_source}/{credentials.api_key_source} -> {GATEWAY_ENDPOINT_ENV}/{GATEWAY_API_KEY_ENV}"
                if credentials
                else "MISSING (a real run would refuse with exit 3)"
            )
            ctx.say(
                f"regenerate_local dry run: {len(selected)} pilot(s); gateway {gateway}; "
                f"helm {'found' if helm_available else 'NOT on PATH (command plan shown without the rendered chain)'}"
            )
        return regenerate(selected, ctx, helm_available=helm_available)


if __name__ == "__main__":
    sys.exit(main())
