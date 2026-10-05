"""TC-221: ingestion Jobs take a GitHub token from an operator Secret, never from values.

Offline. Renders the chart with the real helm binary (image.tag set) and parses the output with
PyYAML. A missing helm binary FAILS these tests; it never skips them.
"""

from __future__ import annotations

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
TAG = "tc221-test-tag"
SECRET = "gh-token"
# A recognisable fake. It is only ever placed in the process environment; the chart must never
# read it, and no rendered output may contain it.
FAKE_TOKEN = "ghp_TC221_FAKE_TOKEN_VALUE_do_not_render_0123456789"


def _helm() -> str:
    helm = shutil.which("helm")
    if helm is None:
        pytest.fail(
            "helm is not on PATH. TC-221 requires it: source scripts/toolchain/activate.ps1, "
            "which adds C:\\dev-tools\\foss-mcp\\helm\\windows-amd64."
        )
    return helm


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


def _ingestion_jobs(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        doc
        for doc in docs
        if doc.get("kind") == "Job"
        and doc["metadata"].get("labels", {}).get("app.kubernetes.io/component") == "ingestion"
    ]


def _job(docs: list[dict[str, Any]], pilot: dict[str, Any]) -> dict[str, Any]:
    suffix = f"-ingest-{pilot['family']}-{pilot['platform']}"
    matches = [job for job in _ingestion_jobs(docs) if job["metadata"]["name"].endswith(suffix)]
    assert len(matches) == 1, (
        f"expected one Job for {pilot['family']}/{pilot['platform']}, got {len(matches)}"
    )
    return matches[0]


def _env(job: dict[str, Any]) -> list[dict[str, Any]]:
    containers = job["spec"]["template"]["spec"]["containers"]
    assert len(containers) == 1
    return containers[0]["env"]


def _github_entries(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [entry for entry in _env(job) if entry.get("name") == "GITHUB_TOKEN"]


def _with_secret(secret: str = SECRET, key: str = "token") -> tuple[str, ...]:
    return (
        "--set",
        f"ingestion.githubToken.existingSecret={secret}",
        "--set",
        f"ingestion.githubToken.key={key}",
    )


# --- values.yaml -----------------------------------------------------------------------------


def test_values_name_the_github_token_block_under_ingestion() -> None:
    assert "githubToken" in _values()["ingestion"]


def test_values_github_token_block_has_exactly_the_two_documented_keys() -> None:
    block = _values()["ingestion"]["githubToken"]
    assert sorted(block) == ["existingSecret", "key"]


def test_values_default_existing_secret_is_empty() -> None:
    assert _values()["ingestion"]["githubToken"]["existingSecret"] == ""


def test_values_default_key_is_token() -> None:
    assert _values()["ingestion"]["githubToken"]["key"] == "token"


def test_values_file_contains_no_token_like_value() -> None:
    text = VALUES.read_text(encoding="utf-8")
    assert "ghp_" not in text
    assert "github_pat_" not in text
    assert FAKE_TOKEN not in text


def test_values_comment_says_the_operator_creates_the_secret() -> None:
    text = VALUES.read_text(encoding="utf-8")
    assert "operator" in text
    assert "never stored in the chart" in text or "never in the chart" in text


# --- template text ---------------------------------------------------------------------------


def test_template_names_secret_key_ref_exactly_once() -> None:
    # The negative control rewrites this exact text; one occurrence means one env entry carries it.
    assert TEMPLATE.read_text(encoding="utf-8").count("secretKeyRef") == 1


def test_template_gates_the_entry_on_existing_secret() -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "{{- if .existingSecret }}" in text


def test_template_has_no_literal_github_token_value() -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "ghp_" not in text
    assert "github_pat_" not in text


# --- default render: nothing is emitted ------------------------------------------------------


def test_default_render_mentions_no_github_token_anywhere() -> None:
    assert "GITHUB_TOKEN" not in _render_text()


def test_default_render_has_no_secret_key_ref() -> None:
    assert "secretKeyRef" not in _render_text()


@pytest.mark.parametrize("pilot", _pilots(), ids=_pilot_id)
def test_default_job_env_is_unchanged_by_the_token_feature(pilot: dict[str, Any]) -> None:
    job = _job(_render(), pilot)
    names = [entry["name"] for entry in _env(job)]
    assert names == ["HOME", "CARGO_HOME"]


def test_empty_existing_secret_emits_nothing_even_with_a_key_set() -> None:
    text = _render_text("--set", "ingestion.githubToken.key=other-key")
    assert "GITHUB_TOKEN" not in text
    assert "secretKeyRef" not in text


# --- with a secret: every pilot Job gets the entry -------------------------------------------


def test_with_a_secret_there_is_one_ingestion_job_per_pilot() -> None:
    docs = _render(*_with_secret())
    assert len(_ingestion_jobs(docs)) == len(_pilots())


@pytest.mark.parametrize("pilot", _pilots(), ids=_pilot_id)
def test_every_pilot_job_has_github_token_from_the_named_secret(pilot: dict[str, Any]) -> None:
    job = _job(_render(*_with_secret()), pilot)
    entries = _github_entries(job)
    assert len(entries) == 1, _pilot_id(pilot)
    ref = entries[0]["valueFrom"]["secretKeyRef"]
    assert ref == {"name": SECRET, "key": "token", "optional": True}


@pytest.mark.parametrize("pilot", _pilots(), ids=_pilot_id)
def test_every_pilot_github_token_ref_is_optional(pilot: dict[str, Any]) -> None:
    job = _job(_render(*_with_secret()), pilot)
    ref = _github_entries(job)[0]["valueFrom"]["secretKeyRef"]
    assert ref["optional"] is True


@pytest.mark.parametrize("pilot", _pilots(), ids=_pilot_id)
def test_github_token_entry_has_no_literal_value_field(pilot: dict[str, Any]) -> None:
    job = _job(_render(*_with_secret()), pilot)
    entry = _github_entries(job)[0]
    assert set(entry) == {"name", "valueFrom"}


@pytest.mark.parametrize("pilot", _pilots(), ids=_pilot_id)
def test_home_and_cargo_home_are_still_set_beside_the_token(pilot: dict[str, Any]) -> None:
    job = _job(_render(*_with_secret()), pilot)
    env = {entry["name"]: entry for entry in _env(job)}
    assert env["HOME"]["value"] == "/home/foss-mcp"
    assert env["CARGO_HOME"]["value"] == "/home/foss-mcp/.cargo"


@pytest.mark.parametrize("pilot", _pilots(), ids=_pilot_id)
def test_github_token_is_appended_after_the_existing_env(pilot: dict[str, Any]) -> None:
    job = _job(_render(*_with_secret()), pilot)
    assert [entry["name"] for entry in _env(job)] == ["HOME", "CARGO_HOME", "GITHUB_TOKEN"]


@pytest.mark.parametrize("pilot", _pilots(), ids=_pilot_id)
def test_the_job_command_is_unchanged_by_the_token(pilot: dict[str, Any]) -> None:
    baseline = _job(_render(), pilot)["spec"]["template"]["spec"]["containers"][0]["args"]
    with_token = _job(_render(*_with_secret()), pilot)["spec"]["template"]["spec"]["containers"][0]
    assert with_token["args"] == baseline


def test_the_secret_ref_appears_once_per_pilot_job_and_nowhere_else() -> None:
    docs = _render(*_with_secret())
    assert _render_text(*_with_secret()).count("secretKeyRef") == len(_pilots())
    for doc in docs:
        if doc.get("kind") != "Job":
            assert "GITHUB_TOKEN" not in yaml.safe_dump(doc)


def test_the_serving_deployment_gets_no_github_token() -> None:
    docs = _render(*_with_secret())
    deployments = [doc for doc in docs if doc.get("kind") == "Deployment"]
    assert len(deployments) == 1
    assert "GITHUB_TOKEN" not in yaml.safe_dump(deployments[0])
    assert "secretKeyRef" not in yaml.safe_dump(deployments[0])


# --- honoured overrides ----------------------------------------------------------------------


@pytest.mark.parametrize("pilot", _pilots(), ids=_pilot_id)
def test_a_custom_key_is_honoured(pilot: dict[str, Any]) -> None:
    job = _job(_render(*_with_secret(key="github-pat")), pilot)
    ref = _github_entries(job)[0]["valueFrom"]["secretKeyRef"]
    assert ref == {"name": SECRET, "key": "github-pat", "optional": True}


def test_a_custom_secret_name_is_honoured_on_every_job() -> None:
    docs = _render(*_with_secret(secret="ops-github-credentials"))
    for job in _ingestion_jobs(docs):
        ref = _github_entries(job)[0]["valueFrom"]["secretKeyRef"]
        assert ref["name"] == "ops-github-credentials"


def test_a_secret_name_is_rendered_as_a_string_even_when_numeric() -> None:
    docs = _render("--set-string", "ingestion.githubToken.existingSecret=12345")
    for job in _ingestion_jobs(docs):
        ref = _github_entries(job)[0]["valueFrom"]["secretKeyRef"]
        assert ref["name"] == "12345"


def test_a_values_file_override_of_the_block_is_honoured(tmp_path: Path) -> None:
    override = tmp_path / "token.yaml"
    override.write_text(
        yaml.safe_dump({"ingestion": {"githubToken": {"existingSecret": "from-file", "key": "k"}}}),
        encoding="utf-8",
    )
    docs = _render("-f", str(override))
    for job in _ingestion_jobs(docs):
        ref = _github_entries(job)[0]["valueFrom"]["secretKeyRef"]
        assert ref == {"name": "from-file", "key": "k", "optional": True}


def test_a_values_file_with_no_token_block_renders_no_token(tmp_path: Path) -> None:
    override = tmp_path / "pilots.yaml"
    override.write_text(yaml.safe_dump({"replicaCount": 1}), encoding="utf-8")
    assert "GITHUB_TOKEN" not in _render_text("-f", str(override))


# --- no token value ever renders -------------------------------------------------------------


@pytest.mark.parametrize("extra", [(), _with_secret(), _with_secret(key="github-pat")])
def test_no_rendered_output_contains_a_token_value(
    monkeypatch: pytest.MonkeyPatch, extra: tuple[str, ...]
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", FAKE_TOKEN)
    assert FAKE_TOKEN not in _render_text(*extra)


def test_the_fake_token_is_not_rendered_even_when_the_secret_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", FAKE_TOKEN)
    text = _render_text(*_with_secret())
    assert FAKE_TOKEN not in text
    assert "ghp_" not in text


def test_the_fake_token_cannot_be_set_through_helm_values(tmp_path: Path) -> None:
    # A token placed under a key the chart does not define must not change the render.
    override = tmp_path / "leak.yaml"
    override.write_text(
        yaml.safe_dump({"ingestion": {"githubToken": {"value": FAKE_TOKEN}}}), encoding="utf-8"
    )
    assert FAKE_TOKEN not in _render_text("-f", str(override))
