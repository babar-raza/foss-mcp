"""TC-337: the ingestion Jobs build each pilot's vector index through the SAME embedding gateway the
serving pods query with, but only when embeddingGateway.existingSecret is set.

Before this card ingestion-job.yaml hard-coded the hashing provider and gave the Job no gateway
environment, while find_examples' vector fallback embeds the QUERY through GatewayEmbeddingProvider
(4096-dim Qwen). A hashing-built index and a Qwen query vector are not comparable, so in a real chart
deployment the fallback silently returned nothing. TC-334 wired the gateway env into the serving pods
only; TC-337 closes the ingestion half.

Offline. Renders the chart with the real helm binary (image.tag set) and parses the output with
PyYAML. A missing helm binary FAILS these tests; it never skips them.
"""

from __future__ import annotations

import functools
import re
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
TEMPLATE = CHART / "templates" / "ingestion-job.yaml"
TAG = "tc337-test-tag"
SECRET = "embedding-gateway-creds"
ENDPOINT_KEY = "custom-endpoint-key"
API_KEY_KEY = "custom-api-key-key"
ENDPOINT_VAR = "FOSS_MCP_EMBEDDING_GATEWAY_ENDPOINT"
API_KEY_VAR = "FOSS_MCP_EMBEDDING_GATEWAY_API_KEY"
GATEWAY_PROVIDER = "foss_mcp.indexing.gateway_embedding_provider:GatewayEmbeddingProvider"
HASHING_PROVIDER = "foss_mcp.indexing.hashing_embedding_provider:HashingEmbeddingProvider"


def _helm() -> str:
    helm = shutil.which("helm")
    if helm is None:
        pytest.fail(
            "helm is not on PATH. TC-337 requires it: source scripts/toolchain/activate.ps1, "
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


def _docs(text: str) -> list[dict[str, Any]]:
    return [doc for doc in yaml.safe_load_all(text) if doc]


def _render(*extra: str) -> list[dict[str, Any]]:
    return _docs(_render_text(*extra))


def _values() -> dict[str, Any]:
    return yaml.safe_load(VALUES.read_text(encoding="utf-8"))


def _pilots() -> list[dict[str, Any]]:
    return _values()["ingestion"]["pilots"]


def _pilot_id(pilot: dict[str, Any]) -> str:
    return f"{pilot['family']}-{pilot['platform']}"


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


def _all_jobs(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [doc for doc in docs if doc.get("kind") == "Job"]


def _ingestion_jobs(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        doc
        for doc in _all_jobs(docs)
        if doc["metadata"].get("labels", {}).get("app.kubernetes.io/component") == "ingestion"
    ]


def _job(docs: list[dict[str, Any]], pilot: dict[str, Any]) -> dict[str, Any]:
    wanted = f"-ingest-{pilot['family']}-{pilot['platform']}"
    matches = [job for job in _ingestion_jobs(docs) if job["metadata"]["name"].endswith(wanted)]
    assert len(matches) == 1, f"expected exactly one ingestion Job for {_pilot_id(pilot)}"
    return matches[0]


def _container(job: dict[str, Any]) -> dict[str, Any]:
    containers = job["spec"]["template"]["spec"]["containers"]
    assert len(containers) == 1
    return containers[0]


def _command(job: dict[str, Any]) -> str:
    args = _container(job)["args"]
    assert len(args) == 1
    return args[0]


def _env(job: dict[str, Any]) -> list[dict[str, Any]]:
    return _container(job)["env"]


def _gateway_entries(job: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {e["name"]: e for e in _env(job) if e.get("name") in {ENDPOINT_VAR, API_KEY_VAR}}


def _provider_token(command: str) -> str:
    tokens = shlex.split(command)
    assert tokens.count("--embedding-provider") == 1
    return tokens[tokens.index("--embedding-provider") + 1]


# --- the chart's real pilot list: derived, never hard-coded -----------------------------------


def test_the_chart_has_pilots_so_the_per_job_assertions_are_not_vacuous() -> None:
    assert _pilots(), "ingestion.pilots is empty; every per-Job assertion below would be vacuous"
    assert len(_ingestion_jobs(_render())) == len(_pilots())


# --- template text ----------------------------------------------------------------------------


def test_template_reads_the_dollar_scoped_values_not_bare_dot() -> None:
    # ingestion-job.yaml renders inside {{- range $p := .Values.ingestion.pilots }}, which rebinds
    # "." to the range variable. A bare ".Values.embeddingGateway" would silently read the wrong
    # scope, so every lookup must be written "$.Values.embeddingGateway".
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "$.Values.embeddingGateway.existingSecret" in text
    assert not re.search(r"(?<!\$)\.Values\.embeddingGateway", text)


# --- default render: exactly as before - the hashing provider, no gateway environment ----------


def test_default_render_mentions_no_gateway_env_var_anywhere() -> None:
    text = _render_text()
    assert ENDPOINT_VAR not in text
    assert API_KEY_VAR not in text


@pytest.mark.parametrize("pilot", _pilots(), ids=_pilot_id)
def test_default_every_ingestion_job_uses_the_hashing_provider(pilot: dict[str, Any]) -> None:
    command = _command(_job(_render(), pilot))
    assert HASHING_PROVIDER in command
    assert "GatewayEmbeddingProvider" not in command
    assert "gateway_embedding_provider" not in command
    assert _provider_token(command) == HASHING_PROVIDER


@pytest.mark.parametrize("pilot", _pilots(), ids=_pilot_id)
def test_default_every_ingestion_job_env_is_exactly_home_and_cargo_home(pilot: dict[str, Any]) -> None:
    names = [entry["name"] for entry in _env(_job(_render(), pilot))]
    assert names == ["HOME", "CARGO_HOME"]


def test_default_no_job_of_any_kind_carries_a_gateway_env_var() -> None:
    for job in _all_jobs(_render()):
        for container in job["spec"]["template"]["spec"]["containers"]:
            names = {entry["name"] for entry in container.get("env", [])}
            assert not names & {ENDPOINT_VAR, API_KEY_VAR}, job["metadata"]["name"]


def test_empty_existing_secret_keeps_the_hashing_provider_even_with_keys_set() -> None:
    docs = _render(
        "--set",
        f"embeddingGateway.endpointKey={ENDPOINT_KEY}",
        "--set",
        f"embeddingGateway.apiKeyKey={API_KEY_KEY}",
    )
    text = _render_text(
        "--set",
        f"embeddingGateway.endpointKey={ENDPOINT_KEY}",
        "--set",
        f"embeddingGateway.apiKeyKey={API_KEY_KEY}",
    )
    assert ENDPOINT_VAR not in text
    assert API_KEY_VAR not in text
    jobs = _ingestion_jobs(docs)
    assert jobs
    for job in jobs:
        assert _provider_token(_command(job)) == HASHING_PROVIDER


# --- configured render: the gateway provider and both required secretKeyRefs ------------------


@pytest.mark.parametrize("pilot", _pilots(), ids=_pilot_id)
def test_configured_every_ingestion_job_uses_the_gateway_provider(pilot: dict[str, Any]) -> None:
    command = _command(_job(_render(*_with_secret()), pilot))
    assert GATEWAY_PROVIDER in command
    assert "GatewayEmbeddingProvider" in command
    assert "HashingEmbeddingProvider" not in command
    assert "hashing_embedding_provider" not in command
    assert _provider_token(command) == GATEWAY_PROVIDER


@pytest.mark.parametrize("pilot", _pilots(), ids=_pilot_id)
def test_configured_every_ingestion_job_gets_both_gateway_env_vars_from_the_named_secret(
    pilot: dict[str, Any],
) -> None:
    entries = _gateway_entries(_job(_render(*_with_secret()), pilot))
    assert set(entries) == {ENDPOINT_VAR, API_KEY_VAR}
    endpoint_ref = entries[ENDPOINT_VAR]["valueFrom"]["secretKeyRef"]
    api_key_ref = entries[API_KEY_VAR]["valueFrom"]["secretKeyRef"]
    assert endpoint_ref["name"] == SECRET
    assert endpoint_ref["key"] == ENDPOINT_KEY
    assert api_key_ref["name"] == SECRET
    assert api_key_ref["key"] == API_KEY_KEY


@pytest.mark.parametrize("pilot", _pilots(), ids=_pilot_id)
def test_configured_gateway_refs_are_required_never_optional(pilot: dict[str, Any]) -> None:
    # Unlike the serving side (TC-334, optional: true) a Job that was asked for the gateway must fail
    # loudly (CreateContainerConfigError) on a missing key rather than silently build a hashing index.
    entries = _gateway_entries(_job(_render(*_with_secret()), pilot))
    assert set(entries) == {ENDPOINT_VAR, API_KEY_VAR}
    for name, entry in entries.items():
        ref = entry["valueFrom"]["secretKeyRef"]
        assert ref.get("optional", False) is False, name
        assert set(ref) <= {"name", "key", "optional"}, name


def test_configured_gateway_entries_follow_the_existing_ingestion_env() -> None:
    for job in _ingestion_jobs(_render(*_with_secret())):
        names = [entry["name"] for entry in _env(job)]
        assert names == ["HOME", "CARGO_HOME", ENDPOINT_VAR, API_KEY_VAR], job["metadata"]["name"]


def test_configured_gateway_refs_sit_beside_a_configured_github_token() -> None:
    docs = _render(
        *_with_secret(),
        "--set",
        "ingestion.githubToken.existingSecret=gh-token",
        "--set",
        "ingestion.githubToken.key=token",
    )
    jobs = _ingestion_jobs(docs)
    assert jobs
    for job in jobs:
        env = {entry["name"]: entry for entry in _env(job)}
        assert set(env) == {"HOME", "CARGO_HOME", "GITHUB_TOKEN", ENDPOINT_VAR, API_KEY_VAR}
        # The token keeps its own, deliberately optional, reference; the gateway pair does not.
        assert env["GITHUB_TOKEN"]["valueFrom"]["secretKeyRef"]["optional"] is True
        assert "optional" not in env[ENDPOINT_VAR]["valueFrom"]["secretKeyRef"]
        assert "optional" not in env[API_KEY_VAR]["valueFrom"]["secretKeyRef"]


# --- no literal value ever renders: these are secretKeyRefs, never literal values -------------


@pytest.mark.parametrize("pilot", _pilots(), ids=_pilot_id)
def test_configured_gateway_entries_have_no_literal_value_field(pilot: dict[str, Any]) -> None:
    for entry in _gateway_entries(_job(_render(*_with_secret()), pilot)).values():
        assert set(entry) == {"name", "valueFrom"}, entry["name"]


def test_the_secret_name_and_keys_never_appear_as_a_literal_env_value() -> None:
    # The secret name and its two keys legitimately appear inside valueFrom.secretKeyRef (that is the
    # whole point of the feature); what must never happen is any of them showing up as a literal
    # "value:" on an env entry, the way a real credential would if it leaked.
    for job in _all_jobs(_render(*_with_secret())):
        for container in job["spec"]["template"]["spec"]["containers"]:
            for entry in container.get("env", []):
                if "value" in entry:
                    assert entry["value"] not in {SECRET, ENDPOINT_KEY, API_KEY_KEY}, entry["name"]


# --- the folded command stays one well-formed shell line in BOTH modes ------------------------


@pytest.mark.parametrize("mode", ["default", "configured"])
def test_command_is_one_well_formed_shell_line_in_both_modes(mode: str) -> None:
    docs = _render(*_with_secret()) if mode == "configured" else _render()
    jobs = _ingestion_jobs(docs)
    assert jobs
    for job in jobs:
        command = _command(job)
        name = job["metadata"]["name"]
        assert "\n" not in command, name
        assert "  " not in command, name
        assert command == command.strip(), name
        # shlex rejects an unbalanced quote; the provider is exactly one whole token.
        assert shlex.split(command), name
        assert _provider_token(command) in {GATEWAY_PROVIDER, HASHING_PROVIDER}, name


@pytest.mark.parametrize("pilot", _pilots(), ids=_pilot_id)
def test_the_switch_changes_only_the_provider_token_in_the_command(pilot: dict[str, Any]) -> None:
    default = _command(_job(_render(), pilot))
    configured = _command(_job(_render(*_with_secret()), pilot))
    assert default != configured
    assert configured.replace(GATEWAY_PROVIDER, HASHING_PROVIDER) == default


# --- the switch changes content, never how many Jobs exist ------------------------------------


def test_the_rendered_job_count_is_the_same_in_both_modes() -> None:
    default = _all_jobs(_render())
    configured = _all_jobs(_render(*_with_secret()))
    assert default
    assert len(default) == len(configured)
    assert len(_ingestion_jobs(_render())) == len(_ingestion_jobs(_render(*_with_secret())))
    assert sorted(j["metadata"]["name"] for j in default) == sorted(j["metadata"]["name"] for j in configured)
