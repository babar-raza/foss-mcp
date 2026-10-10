"""TC-334: the embedding gateway's endpoint/API key are read from an operator Secret, never from
values, into BOTH serving paths - the live per-pilot deployment (deployment.yaml) and the lean
demo path (demo-deployment.yaml) - mirroring ingestion-job.yaml's own githubToken convention.

Offline. Renders the chart with the real helm binary (image.tag set) and parses the output with
PyYAML. A missing helm binary FAILS these tests; it never skips them.
"""

from __future__ import annotations

import functools
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CHART = REPO_ROOT / "infra" / "helm" / "foss-mcp"
VALUES = CHART / "values.yaml"
DEPLOYMENT_TEMPLATE = CHART / "templates" / "deployment.yaml"
DEMO_DEPLOYMENT_TEMPLATE = CHART / "templates" / "demo-deployment.yaml"
TAG = "tc334-test-tag"
DEMO_TAG = "tc334-demo-test-tag"
SECRET = "embedding-gateway-creds"
ENDPOINT_KEY = "endpoint"
API_KEY_KEY = "apiKey"
ENDPOINT_VAR = "FOSS_MCP_EMBEDDING_GATEWAY_ENDPOINT"
API_KEY_VAR = "FOSS_MCP_EMBEDDING_GATEWAY_API_KEY"


def _helm() -> str:
    helm = shutil.which("helm")
    if helm is None:
        pytest.fail(
            "helm is not on PATH. TC-334 requires it: source scripts/toolchain/activate.ps1, "
            "which adds C:\\dev-tools\\foss-mcp\\helm\\windows-amd64."
        )
    return helm


@functools.lru_cache(maxsize=None)
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


def _docs(text: str) -> list[dict[str, Any]]:
    return [doc for doc in yaml.safe_load_all(text) if doc]


def _render(*extra: str) -> list[dict[str, Any]]:
    return _docs(_render_text(*extra))


def _render_demo_text(*extra: str) -> str:
    return _render_text("--set", "demo.enabled=true", "--set", f"demo.image.tag={DEMO_TAG}", *extra)


def _render_demo(*extra: str) -> list[dict[str, Any]]:
    return _docs(_render_demo_text(*extra))


def _values() -> dict[str, Any]:
    return yaml.safe_load(VALUES.read_text(encoding="utf-8"))


def _pilots() -> list[dict[str, Any]]:
    return _values()["ingestion"]["pilots"]


def _with_secret(
    secret: str = SECRET, endpoint_key: str = ENDPOINT_KEY, api_key_key: str = API_KEY_KEY
) -> tuple[str, ...]:
    return (
        "--set",
        f"embeddingGateway.existingSecret={secret}",
        "--set",
        f"embeddingGateway.endpointKey={endpoint_key}",
        "--set",
        f"embeddingGateway.apiKeyKey={api_key_key}",
    )


def _is_demo(deployment: dict[str, Any]) -> bool:
    labels = deployment["spec"]["template"]["metadata"]["labels"]
    return labels.get("app.kubernetes.io/component") == "demo-serving"


def _main_deployment(docs: list[dict[str, Any]]) -> dict[str, Any]:
    deployments = [d for d in docs if d.get("kind") == "Deployment" and not _is_demo(d)]
    assert len(deployments) == 1
    return deployments[0]


def _demo_deployments(docs: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for d in docs:
        if d.get("kind") == "Deployment" and _is_demo(d):
            labels = d["metadata"]["labels"]
            key = (labels["foss-mcp.aspose.org/demo-family"], labels["foss-mcp.aspose.org/demo-platform"])
            out[key] = d
    return out


def _env(deployment: dict[str, Any]) -> list[dict[str, Any]]:
    containers = deployment["spec"]["template"]["spec"]["containers"]
    assert len(containers) == 1
    return containers[0]["env"]


def _gateway_entries(deployment: dict[str, Any]) -> list[dict[str, Any]]:
    return [entry for entry in _env(deployment) if entry.get("name") in {ENDPOINT_VAR, API_KEY_VAR}]


# --- values.yaml -----------------------------------------------------------------------------


def test_values_name_the_embedding_gateway_block_at_the_top_level() -> None:
    values = _values()
    assert "embeddingGateway" in values
    assert "embeddingGateway" not in values["ingestion"]
    assert "embeddingGateway" not in values["demo"]


def test_values_embedding_gateway_block_has_exactly_the_three_documented_keys() -> None:
    block = _values()["embeddingGateway"]
    assert sorted(block) == ["apiKeyKey", "endpointKey", "existingSecret"]


def test_values_default_existing_secret_is_empty() -> None:
    assert _values()["embeddingGateway"]["existingSecret"] == ""


def test_values_default_endpoint_key_is_endpoint() -> None:
    assert _values()["embeddingGateway"]["endpointKey"] == "endpoint"


def test_values_default_api_key_key_is_api_key() -> None:
    assert _values()["embeddingGateway"]["apiKeyKey"] == "apiKey"


# --- template text ---------------------------------------------------------------------------


def test_deployment_template_gates_the_entries_on_existing_secret() -> None:
    text = DEPLOYMENT_TEMPLATE.read_text(encoding="utf-8")
    assert "{{- with .Values.embeddingGateway }}" in text
    assert "{{- if .existingSecret }}" in text


def test_demo_deployment_template_reads_the_dollar_scoped_values_not_bare_dot() -> None:
    # G2/TC-334's own documented gotcha: demo-deployment.yaml is inside a {{- range $p := $pilots
    # }} loop, which rebinds "." to the range variable. A bare ".Values.embeddingGateway" would
    # silently read the wrong scope, so the guard must use "$.Values.embeddingGateway".
    text = DEMO_DEPLOYMENT_TEMPLATE.read_text(encoding="utf-8")
    assert "{{- with $.Values.embeddingGateway }}" in text
    assert "{{- with .Values.embeddingGateway }}" not in text


# --- default render: nothing is emitted, in either serving path ------------------------------


def test_default_render_mentions_no_gateway_env_var_anywhere() -> None:
    text = _render_text()
    assert ENDPOINT_VAR not in text
    assert API_KEY_VAR not in text


def test_default_demo_render_mentions_no_gateway_env_var_anywhere() -> None:
    text = _render_demo_text()
    assert ENDPOINT_VAR not in text
    assert API_KEY_VAR not in text


def test_default_main_deployment_env_is_unchanged_by_the_gateway_feature() -> None:
    deployment = _main_deployment(_render())
    names = [entry["name"] for entry in _env(deployment)]
    assert names == ["FOSS_MCP_FAMILY", "FOSS_MCP_PLATFORM", "FOSS_MCP_SOURCE_KIND"]


def test_default_demo_deployment_env_is_unchanged_by_the_gateway_feature() -> None:
    deployments = _demo_deployments(_render_demo())
    assert deployments, "expected at least one demo Deployment"
    for deployment in deployments.values():
        names = [entry["name"] for entry in _env(deployment)]
        assert names == ["FOSS_MCP_FAMILY", "FOSS_MCP_PLATFORM", "FOSS_MCP_SOURCE_KIND"]


def test_empty_existing_secret_emits_nothing_even_with_keys_set() -> None:
    text = _render_text(
        "--set", f"embeddingGateway.endpointKey={ENDPOINT_KEY}", "--set", f"embeddingGateway.apiKeyKey={API_KEY_KEY}"
    )
    assert ENDPOINT_VAR not in text
    assert API_KEY_VAR not in text


# --- with a secret: both serving paths get both entries ---------------------------------------


def test_main_deployment_gets_both_gateway_env_vars_from_the_named_secret() -> None:
    deployment = _main_deployment(_render(*_with_secret()))
    entries = {e["name"]: e for e in _gateway_entries(deployment)}
    assert set(entries) == {ENDPOINT_VAR, API_KEY_VAR}
    assert entries[ENDPOINT_VAR]["valueFrom"]["secretKeyRef"] == {
        "name": SECRET,
        "key": ENDPOINT_KEY,
        "optional": True,
    }
    assert entries[API_KEY_VAR]["valueFrom"]["secretKeyRef"] == {
        "name": SECRET,
        "key": API_KEY_KEY,
        "optional": True,
    }


def test_main_deployment_gateway_entries_are_inserted_between_platform_and_source_kind() -> None:
    deployment = _main_deployment(_render(*_with_secret()))
    names = [entry["name"] for entry in _env(deployment)]
    assert names == [
        "FOSS_MCP_FAMILY",
        "FOSS_MCP_PLATFORM",
        ENDPOINT_VAR,
        API_KEY_VAR,
        "FOSS_MCP_SOURCE_KIND",
    ]


@pytest.mark.parametrize("pilot", _pilots(), ids=lambda p: f"{p['family']}-{p['platform']}")
def test_every_demo_pilot_gets_both_gateway_env_vars_from_the_named_secret(pilot: dict[str, Any]) -> None:
    deployments = _demo_deployments(_render_demo(*_with_secret()))
    key = (pilot["family"], pilot["platform"])
    assert key in deployments, f"expected a demo Deployment for {pilot['family']}/{pilot['platform']}"
    entries = {e["name"]: e for e in _gateway_entries(deployments[key])}
    assert set(entries) == {ENDPOINT_VAR, API_KEY_VAR}
    assert entries[ENDPOINT_VAR]["valueFrom"]["secretKeyRef"] == {
        "name": SECRET,
        "key": ENDPOINT_KEY,
        "optional": True,
    }
    assert entries[API_KEY_VAR]["valueFrom"]["secretKeyRef"] == {
        "name": SECRET,
        "key": API_KEY_KEY,
        "optional": True,
    }


def test_every_demo_pilot_gateway_entries_are_appended_after_source_kind() -> None:
    deployments = _demo_deployments(_render_demo(*_with_secret()))
    for key, deployment in deployments.items():
        names = [entry["name"] for entry in _env(deployment)]
        assert names == [
            "FOSS_MCP_FAMILY",
            "FOSS_MCP_PLATFORM",
            "FOSS_MCP_SOURCE_KIND",
            ENDPOINT_VAR,
            API_KEY_VAR,
        ], key


# --- no literal value ever renders - these are secretKeyRefs, never literal values ------------


def test_main_deployment_gateway_entries_have_no_literal_value_field() -> None:
    deployment = _main_deployment(_render(*_with_secret()))
    for entry in _gateway_entries(deployment):
        assert set(entry) == {"name", "valueFrom"}, entry["name"]


def test_every_demo_pilot_gateway_entries_have_no_literal_value_field() -> None:
    deployments = _demo_deployments(_render_demo(*_with_secret()))
    for deployment in deployments.values():
        for entry in _gateway_entries(deployment):
            assert set(entry) == {"name", "valueFrom"}, entry["name"]


def test_the_fake_secret_name_and_keys_never_appear_as_a_literal_env_value() -> None:
    # The secret name and its two keys legitimately appear inside valueFrom.secretKeyRef (that is
    # the whole point of the feature); what must never happen is any of them showing up as a
    # literal "value:" on an env entry, the way a real credential would if it leaked.
    docs = _render(*_with_secret())
    for doc in docs:
        if doc.get("kind") != "Deployment":
            continue
        for container in doc["spec"]["template"]["spec"]["containers"]:
            for entry in container.get("env", []):
                if "value" in entry:
                    assert entry["value"] not in {SECRET, ENDPOINT_KEY, API_KEY_KEY}


# --- untouched surfaces ------------------------------------------------------------------------


def test_the_serving_deployment_env_is_unaffected_without_a_secret_configured() -> None:
    # Re-stating the default-render assertions against the live (non-demo) Deployment
    # specifically, the same shape test_helm_github_token.py asserts for ingestion Jobs.
    deployment = _main_deployment(_render())
    assert "secretKeyRef" not in yaml.safe_dump(deployment)
