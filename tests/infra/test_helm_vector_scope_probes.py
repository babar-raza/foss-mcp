"""TC-341: the ingestion Jobs store vectors only for example chunks, and serving probes get timeouts.

Two production defects from the first real 40-pilot install:

1. Every ingestion Job embedded and stored a vector for every chunk, although only example-bearing
   chunks can ever be returned by find_examples' vector fallback. TC-340 added the opt-in
   ``infra/ingest.py --vector-scope examples``; the chart must now pass it, in the hashing mode and
   in the gateway mode alike (it is the production path, not a switch).
2. The chart never set a probe ``timeoutSeconds``, so Kubernetes used 1 second. On a busy node
   healthy serving pods failed readiness ("Client.Timeout exceeded while awaiting headers") and were
   then killed by the liveness probe. Both probes now carry an explicit, overridable timeout in the
   single-pilot Deployment and in every demo Deployment.

Offline. Renders the chart with the real helm binary and parses the output with PyYAML. A missing
helm binary FAILS these tests; it never skips them. Pilot counts are derived from the chart's own
values.yaml, never hard-coded.
"""

from __future__ import annotations

import functools
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CHART = REPO_ROOT / "infra" / "helm" / "foss-mcp"
VALUES = CHART / "values.yaml"
TAG = "tc341-test-tag"
DEMO_TAG = "tc341-demo-tag"
SECRET = "embedding-gateway-creds"
GATEWAY_PROVIDER = "foss_mcp.indexing.gateway_embedding_provider:GatewayEmbeddingProvider"
HASHING_PROVIDER = "foss_mcp.indexing.hashing_embedding_provider:HashingEmbeddingProvider"
DEFAULT_LIVENESS_TIMEOUT = 5
DEFAULT_READINESS_TIMEOUT = 3
OVERRIDE_LIVENESS_TIMEOUT = 9
OVERRIDE_READINESS_TIMEOUT = 7
DEMO_COMPONENT = "demo-serving"
INGESTION_COMPONENT = "ingestion"

GATEWAY = ("--set", f"embeddingGateway.existingSecret={SECRET}")
DEMO = ("--set", "demo.enabled=true", "--set", f"demo.image.tag={DEMO_TAG}")
OVERRIDES = (
    "--set",
    f"probes.liveness.timeoutSeconds={OVERRIDE_LIVENESS_TIMEOUT}",
    "--set",
    f"probes.readiness.timeoutSeconds={OVERRIDE_READINESS_TIMEOUT}",
)


def _helm() -> str:
    helm = shutil.which("helm")
    if helm is None:
        pytest.fail(
            "helm is not on PATH. TC-341 requires it: source scripts/toolchain/activate.ps1, "
            "which adds C:\\dev-tools\\foss-mcp\\helm\\windows-amd64."
        )
    return helm


@functools.cache
def _render_text(*extra: str) -> str:
    result = subprocess.run(
        [_helm(), "template", "foss-mcp", str(CHART), "--set", f"image.tag={TAG}", *extra],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _render(*extra: str) -> list[dict[str, Any]]:
    return [doc for doc in yaml.safe_load_all(_render_text(*extra)) if doc]


def _pilots() -> list[dict[str, Any]]:
    return yaml.safe_load(VALUES.read_text(encoding="utf-8"))["ingestion"]["pilots"]


def _component(doc: dict[str, Any]) -> str | None:
    return doc["metadata"].get("labels", {}).get("app.kubernetes.io/component")


def _ingestion_jobs(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [d for d in docs if d.get("kind") == "Job" and _component(d) == INGESTION_COMPONENT]


def _serving_deployments(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # The single-pilot serving Deployment: every Deployment that is not a demo one.
    return [d for d in docs if d.get("kind") == "Deployment" and _component(d) != DEMO_COMPONENT]


def _demo_deployments(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [d for d in docs if d.get("kind") == "Deployment" and _component(d) == DEMO_COMPONENT]


def _command(job: dict[str, Any]) -> str:
    containers = job["spec"]["template"]["spec"]["containers"]
    assert len(containers) == 1
    args = containers[0]["args"]
    assert len(args) == 1
    return args[0]


def _serving_container(deployment: dict[str, Any]) -> dict[str, Any]:
    containers = deployment["spec"]["template"]["spec"]["containers"]
    serving = [c for c in containers if c["name"] == "serving"]
    assert len(serving) == 1, deployment["metadata"]["name"]
    return serving[0]


def _timeouts(deployment: dict[str, Any]) -> tuple[Any, Any]:
    container = _serving_container(deployment)
    return container["livenessProbe"]["timeoutSeconds"], container["readinessProbe"]["timeoutSeconds"]


def _ingest_segment(command: str) -> list[str]:
    """The tokens of the ``python /app/infra/ingest.py`` step, up to the next ``&&`` or ``{``."""
    tokens = shlex.split(command)
    start = tokens.index("/app/infra/ingest.py")
    end = start + 1
    while end < len(tokens) and tokens[end] not in {"&&", "{"}:
        end += 1
    return tokens[start:end]


# --- the chart has pilots, so the per-Job assertions are not vacuous ---------------------------


def test_the_chart_has_pilots_and_one_ingestion_job_per_pilot() -> None:
    assert _pilots(), "ingestion.pilots is empty; every per-Job assertion below would be vacuous"
    assert len(_ingestion_jobs(_render())) == len(_pilots())


# --- (1) the ingestion command passes --vector-scope examples exactly once, in both modes ------


@pytest.mark.parametrize("mode", ["default", "gateway"])
def test_every_ingestion_command_has_vector_scope_examples_exactly_once(mode: str) -> None:
    jobs = _ingestion_jobs(_render(*GATEWAY) if mode == "gateway" else _render())
    assert len(jobs) == len(_pilots())
    for job in jobs:
        command = _command(job)
        name = job["metadata"]["name"]
        assert command.count("--vector-scope examples") == 1, name
        tokens = shlex.split(command)
        assert tokens.count("--vector-scope") == 1, name
        assert tokens[tokens.index("--vector-scope") + 1] == "examples", name


@pytest.mark.parametrize("mode", ["default", "gateway"])
def test_vector_scope_sits_in_the_ingest_step_right_after_the_embedding_provider(mode: str) -> None:
    jobs = _ingestion_jobs(_render(*GATEWAY) if mode == "gateway" else _render())
    assert jobs
    expected_provider = GATEWAY_PROVIDER if mode == "gateway" else HASHING_PROVIDER
    for job in jobs:
        step = _ingest_segment(_command(job))
        name = job["metadata"]["name"]
        assert step.count("--vector-scope") == 1, name
        index = step.index("--embedding-provider")
        assert step[index + 1] == expected_provider, name
        assert step[index + 2 : index + 4] == ["--vector-scope", "examples"], name


@pytest.mark.parametrize("mode", ["default", "gateway"])
def test_the_command_is_still_one_well_formed_shell_line(mode: str) -> None:
    jobs = _ingestion_jobs(_render(*GATEWAY) if mode == "gateway" else _render())
    assert jobs
    for job in jobs:
        command = _command(job)
        name = job["metadata"]["name"]
        assert "\n" not in command, name
        assert "  " not in command, name
        assert command == command.strip(), name
        # shlex rejects an unbalanced quote.
        assert shlex.split(command), name


def test_the_job_count_is_the_same_with_and_without_the_gateway() -> None:
    default = _ingestion_jobs(_render())
    gateway = _ingestion_jobs(_render(*GATEWAY))
    assert default
    assert len(default) == len(gateway) == len(_pilots())
    assert sorted(j["metadata"]["name"] for j in default) == sorted(j["metadata"]["name"] for j in gateway)


def test_the_gateway_switch_still_changes_only_the_provider_token() -> None:
    # --vector-scope is unconditional: it must not differ between the two modes.
    default = {j["metadata"]["name"]: _command(j) for j in _ingestion_jobs(_render())}
    gateway = {j["metadata"]["name"]: _command(j) for j in _ingestion_jobs(_render(*GATEWAY))}
    assert default.keys() == gateway.keys()
    for name, command in gateway.items():
        assert command.replace(GATEWAY_PROVIDER, HASHING_PROVIDER) == default[name], name


# --- (2) the serving Deployment's probes carry explicit timeouts by default --------------------


def test_the_serving_deployment_has_the_default_probe_timeouts() -> None:
    deployments = _serving_deployments(_render())
    assert len(deployments) == 1
    assert _timeouts(deployments[0]) == (DEFAULT_LIVENESS_TIMEOUT, DEFAULT_READINESS_TIMEOUT)


def test_the_default_timeouts_come_from_values_yaml() -> None:
    probes = yaml.safe_load(VALUES.read_text(encoding="utf-8"))["probes"]
    assert probes["liveness"]["timeoutSeconds"] == DEFAULT_LIVENESS_TIMEOUT
    assert probes["readiness"]["timeoutSeconds"] == DEFAULT_READINESS_TIMEOUT


# --- (3) with demo.enabled every demo Deployment carries the same two values -------------------


def test_every_demo_deployment_has_the_default_probe_timeouts() -> None:
    docs = _render(*DEMO)
    demos = _demo_deployments(docs)
    assert demos, "demo.enabled=true rendered no demo Deployment; the per-Deployment loop would be vacuous"
    for deployment in demos:
        assert _timeouts(deployment) == (
            DEFAULT_LIVENESS_TIMEOUT,
            DEFAULT_READINESS_TIMEOUT,
        ), deployment["metadata"]["name"]


def test_the_demo_deployment_count_is_derived_from_the_chart_pilots() -> None:
    assert len(_demo_deployments(_render(*DEMO))) == len(_pilots())


def test_enabling_the_demo_leaves_the_serving_deployment_timeouts_unchanged() -> None:
    deployments = _serving_deployments(_render(*DEMO))
    assert len(deployments) == 1
    assert _timeouts(deployments[0]) == (DEFAULT_LIVENESS_TIMEOUT, DEFAULT_READINESS_TIMEOUT)


# --- (4) both timeouts are overridable, in both templates --------------------------------------


def test_the_overrides_are_honoured_by_the_serving_deployment() -> None:
    deployments = _serving_deployments(_render(*OVERRIDES))
    assert len(deployments) == 1
    assert _timeouts(deployments[0]) == (OVERRIDE_LIVENESS_TIMEOUT, OVERRIDE_READINESS_TIMEOUT)


def test_the_overrides_are_honoured_by_every_demo_deployment() -> None:
    demos = _demo_deployments(_render(*DEMO, *OVERRIDES))
    assert demos
    for deployment in demos:
        assert _timeouts(deployment) == (
            OVERRIDE_LIVENESS_TIMEOUT,
            OVERRIDE_READINESS_TIMEOUT,
        ), deployment["metadata"]["name"]


def test_each_override_moves_only_its_own_probe() -> None:
    liveness_only = _serving_deployments(
        _render("--set", f"probes.liveness.timeoutSeconds={OVERRIDE_LIVENESS_TIMEOUT}")
    )
    readiness_only = _serving_deployments(
        _render("--set", f"probes.readiness.timeoutSeconds={OVERRIDE_READINESS_TIMEOUT}")
    )
    assert _timeouts(liveness_only[0]) == (OVERRIDE_LIVENESS_TIMEOUT, DEFAULT_READINESS_TIMEOUT)
    assert _timeouts(readiness_only[0]) == (DEFAULT_LIVENESS_TIMEOUT, OVERRIDE_READINESS_TIMEOUT)
