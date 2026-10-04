"""TC-201: each ingestion Job writes its pilot's packaging-manifest sidecar; serving reads its own.

Offline. Renders the chart with the real helm binary (image.tag set) and parses the output with
PyYAML, then reads the Dockerfiles as text and calls the real sidecar functions. A missing helm
binary FAILS these tests; it never skips them.
"""

from __future__ import annotations

import copy
import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

import infra.fetch_product_reference as fetch_product_reference
from foss_mcp.mcp.routing import DeploymentConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
CHART = REPO_ROOT / "infra" / "helm" / "foss-mcp"
VALUES = CHART / "values.yaml"
DOCKERFILE_SERVING = REPO_ROOT / "Dockerfile.serving"
DOCKERFILE_INGESTION = REPO_ROOT / "Dockerfile.ingestion"
TAG = "tc201-test-tag"
FETCH_COMMAND = "/app/infra/fetch_product_reference.py"

# infra/ is not a package; serve_http is imported from its path, as in tests/infra/test_serve_manifest_sidecar.py.
sys.path.insert(0, str(REPO_ROOT / "infra"))
import serve_http  # noqa: E402


def _helm() -> str:
    helm = shutil.which("helm")
    if helm is None:
        pytest.fail(
            "helm is not on PATH. TC-201 requires it: source scripts/toolchain/activate.ps1, "
            "which adds C:\\dev-tools\\foss-mcp\\helm\\windows-amd64."
        )
    return helm


def _render(*extra: str) -> list[dict[str, Any]]:
    result = subprocess.run(
        [_helm(), "template", "foss-mcp", str(CHART), "--set", f"image.tag={TAG}", *extra],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def _values() -> dict[str, Any]:
    return yaml.safe_load(VALUES.read_text(encoding="utf-8"))


def _kind(docs: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [doc for doc in docs if doc.get("kind") == kind]


def _job(docs: list[dict[str, Any]], pilot: dict[str, Any]) -> dict[str, Any]:
    suffix = f"-ingest-{pilot['family']}-{pilot['platform']}"
    matches = [job for job in _kind(docs, "Job") if job["metadata"]["name"].endswith(suffix)]
    assert len(matches) == 1, (
        f"expected one Job for {pilot['family']}/{pilot['platform']}, got {len(matches)}"
    )
    return matches[0]


def _job_tokens(job: dict[str, Any]) -> list[str]:
    container = job["spec"]["template"]["spec"]["containers"][0]
    return shlex.split(container["args"][0])


def _flag_after(tokens: list[str], start: int, flag: str) -> str:
    index = tokens.index(flag, start)
    return tokens[index + 1]


def _fetch_tokens(tokens: list[str]) -> list[str]:
    """The tokens of the fetch step only: from its command to the end of the chain."""
    return tokens[tokens.index(FETCH_COMMAND) - 1 :]


def _claim_name(docs: list[dict[str, Any]]) -> str:
    claims = _kind(docs, "PersistentVolumeClaim")
    assert len(claims) == 1, f"expected exactly one PersistentVolumeClaim, got {len(claims)}"
    return claims[0]["metadata"]["name"]


def _pilots_with_a_manifest_path() -> list[dict[str, Any]]:
    return [p for p in _values()["ingestion"]["pilots"] if "manifestPath" in p]


def test_every_pilot_with_a_manifest_path_gets_the_fetch_step_on_the_read_write_claim() -> None:
    values = _values()
    pilots = _pilots_with_a_manifest_path()
    assert pilots, "expected at least one pilot with a manifestPath in values.yaml"
    docs = _render()
    claim = _claim_name(docs)
    mount_path = values["manifests"]["mountPath"]
    for pilot in pilots:
        job = _job(docs, pilot)
        tokens = _job_tokens(job)
        assert FETCH_COMMAND in tokens, pilot["family"] + "/" + pilot["platform"]
        assert tokens.index(FETCH_COMMAND) > tokens.index("/app/infra/ingest.py"), (
            "the fetch step must run after the build and ingest steps"
        )
        fetch = _fetch_tokens(tokens)
        assert _flag_after(fetch, 0, "--manifest-path") == pilot["manifestPath"]
        assert _flag_after(fetch, 0, "--repository") == pilot["library"]["repository"]
        assert _flag_after(fetch, 0, "--ref") == pilot["library"]["commit"]
        pod = job["spec"]["template"]["spec"]
        volumes = {volume["name"]: volume for volume in pod["volumes"]}
        assert volumes["manifests"]["persistentVolumeClaim"]["claimName"] == claim
        mounts = {mount["mountPath"]: mount for mount in pod["containers"][0]["volumeMounts"]}
        assert mounts[mount_path].get("readOnly") is not True, (
            "the fetch step writes, so the claim is read-write"
        )


def test_the_rendered_pdf_cpp_fetch_step_passes_its_manifest_path_CMakeLists_txt() -> None:
    # The expected value is a literal, not read from values.yaml. Editing manifestPath in values.yaml
    # changes the rendered command and the value together, so the per-pilot equality above cannot
    # catch it; this literal does.
    pilot = next(
        p for p in _values()["ingestion"]["pilots"] if p["family"] == "pdf" and p["platform"] == "cpp"
    )
    docs = _render()
    fetch = _fetch_tokens(_job_tokens(_job(docs, pilot)))
    assert _flag_after(fetch, 0, "--manifest-path") == "CMakeLists.txt"


def test_a_pilot_without_a_manifest_path_gets_no_fetch_step(tmp_path: Path) -> None:
    pilots = copy.deepcopy(_values()["ingestion"]["pilots"])
    for pilot in pilots:
        pilot.pop("manifestPath", None)
    # One pilot keeps its manifest path, so one render shows the step on one Job and none on the rest.
    pilots[0]["manifestPath"] = _values()["ingestion"]["pilots"][0]["manifestPath"]
    path = tmp_path / "pilots.yaml"
    path.write_text(yaml.safe_dump({"ingestion": {"pilots": pilots}}), encoding="utf-8")
    docs = _render("-f", str(path))
    assert FETCH_COMMAND in _job_tokens(_job(docs, pilots[0]))
    for pilot in pilots[1:]:
        tokens = _job_tokens(_job(docs, pilot))
        assert FETCH_COMMAND not in tokens, pilot["family"] + "/" + pilot["platform"]
        assert "fetch_product_reference" not in " ".join(tokens), pilot["family"] + "/" + pilot["platform"]


def test_the_serving_deployment_mounts_the_manifests_claim_read_only() -> None:
    values = _values()
    docs = _render()
    deployments = _kind(docs, "Deployment")
    assert len(deployments) == 1, f"expected one serving Deployment, got {len(deployments)}"
    spec = deployments[0]["spec"]["template"]["spec"]
    volumes = {volume["name"]: volume for volume in spec["volumes"]}
    assert volumes["manifests"]["persistentVolumeClaim"]["claimName"] == _claim_name(docs)
    mounts = {mount["mountPath"]: mount for mount in spec["containers"][0]["volumeMounts"]}
    assert mounts[values["manifests"]["mountPath"]]["readOnly"] is True


def test_each_pilot_sidecar_name_is_distinct_and_is_the_name_its_own_pilot_writes() -> None:
    names = []
    for pilot in _pilots_with_a_manifest_path():
        names.append(fetch_product_reference.sidecar_name(pilot["family"], pilot["platform"]))
    assert len(names) == len(set(names)) == len(_pilots_with_a_manifest_path()) == 7, names
    assert "product_reference_pdf_net.json" in names
    assert "product_reference_pdf_java.json" in names


def test_the_job_step_writes_the_sidecar_name_sidecar_name_returns_for_its_pilot() -> None:
    values = _values()
    docs = _render()
    for pilot in _pilots_with_a_manifest_path():
        fetch = _fetch_tokens(_job_tokens(_job(docs, pilot)))
        expected = f"{values['manifests']['mountPath']}/" + fetch_product_reference.sidecar_name(
            pilot["family"], pilot["platform"]
        )
        assert _flag_after(fetch, 0, "--output") == expected, pilot["family"] + "/" + pilot["platform"]


def test_the_rendered_job_command_invokes_the_fetch_script_with_the_sidecar_name_for_its_pilot() -> None:
    # Reads the command from helm template output, not from the template text. Editing the fetch
    # command in templates/ingestion-job.yaml changes this rendered command and fails here.
    values = _values()
    docs = _render()
    for pilot in _pilots_with_a_manifest_path():
        identity = pilot["family"] + "/" + pilot["platform"]
        tokens = _job_tokens(_job(docs, pilot))
        assert FETCH_COMMAND in tokens, identity
        fetch = _fetch_tokens(tokens)
        assert fetch[:2] == ["python", FETCH_COMMAND], identity
        sidecar = fetch_product_reference.sidecar_name(pilot["family"], pilot["platform"])
        assert _flag_after(fetch, 0, "--output") == f"{values['manifests']['mountPath']}/{sidecar}", identity


def test_serving_with_the_pdf_java_identity_never_reads_the_pdf_net_sidecar(tmp_path: Path) -> None:
    net_sidecar = {
        "manifest_text": '<Project Sdk="Microsoft.NET.Sdk"/>',
        "platform": "net",
        "contributing": {"status": "not_present", "path": "CONTRIBUTING.md"},
        "agent_guidance": {"status": "not_present", "path": "AGENTS.md"},
    }
    (tmp_path / fetch_product_reference.sidecar_name("pdf", "net")).write_text(
        json.dumps(net_sidecar), encoding="utf-8"
    )
    # Only the pdf/net file exists, so the pdf/java identity has no sidecar and answers NotAvailable.
    java = DeploymentConfig(family="pdf", platform="java")
    assert serve_http._serving_product_reference_inputs(tmp_path, java) is None
    net = DeploymentConfig(family="pdf", platform="net")
    assert serve_http._serving_product_reference_inputs(tmp_path, net).platform == "net"


def test_serving_fails_at_start_when_its_identity_env_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(serve_http.FAMILY_ENV, raising=False)
    with pytest.raises(RuntimeError, match="FOSS_MCP_FAMILY"):
        serve_http._required_env(serve_http.FAMILY_ENV)


def test_dockerfile_serving_copies_the_fetch_module_that_serve_http_imports() -> None:
    lines = DOCKERFILE_SERVING.read_text(encoding="utf-8").splitlines()
    copies = [line for line in lines if line.startswith("COPY ") and "fetch_product_reference.py" in line]
    assert copies == ["COPY infra/fetch_product_reference.py ./infra/fetch_product_reference.py"], copies


def test_dockerfile_ingestion_copies_the_fetch_module_the_job_step_runs() -> None:
    lines = DOCKERFILE_INGESTION.read_text(encoding="utf-8").splitlines()
    copies = [line for line in lines if line.startswith("COPY ") and "fetch_product_reference.py" in line]
    assert copies == ["COPY infra/fetch_product_reference.py ./infra/fetch_product_reference.py"], copies
