"""TC-204: a serving pod waits for its own product-reference sidecar before it starts.

serve_http.py reads the packaging-manifest sidecar AND the recent-releases sidecar once, at
process start, with no refresh. The serving Deployment therefore carries an init container,
wait-for-product-reference-and-recent-releases, that loops until BOTH sidecars for the
Deployment's identity exist on the read-only manifests claim (G2/TC-251: the ingestion Job's own
chain runs fetch_product_reference.py strictly before fetch_recent_releases.py, so gating on the
first file alone let the serving container start before the second ever existed). Every check
renders the chart offline with the real helm binary. A missing helm binary FAILS these tests; it
never skips them.
"""

from __future__ import annotations

import importlib.util
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CHART = REPO_ROOT / "infra" / "helm" / "foss-mcp"
FETCH_SCRIPT = REPO_ROOT / "infra" / "fetch_product_reference.py"
FETCH_RECENT_RELEASES_SCRIPT = REPO_ROOT / "infra" / "fetch_recent_releases.py"
TAG = "tc204-test-tag"
INIT_NAME = "wait-for-product-reference-and-recent-releases"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sidecar_name(family: str, platform: str) -> str:
    """Load sidecar_name from infra/fetch_product_reference.py, the single source of the name."""
    return _load_module(FETCH_SCRIPT, "fetch_product_reference").sidecar_name(family, platform)


def _recent_releases_sidecar_name(family: str, platform: str) -> str:
    """Load recent_releases_sidecar_name from infra/fetch_recent_releases.py, the single source of
    this name (G2/TC-251)."""
    return _load_module(FETCH_RECENT_RELEASES_SCRIPT, "fetch_recent_releases").recent_releases_sidecar_name(
        family, platform
    )


def _helm_bin() -> str:
    helm = shutil.which("helm")
    if helm is None:
        raise AssertionError(
            "helm is not on PATH; these tests fail rather than skip. Source "
            "scripts/toolchain/activate.ps1 in the same command as pytest."
        )
    return helm


def _render_deployment(*sets: str) -> dict[str, Any]:
    cmd = [_helm_bin(), "template", "foss-mcp", str(CHART), "--set", f"image.tag={TAG}"]
    for item in sets:
        cmd += ["--set", item]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert result.returncode == 0, f"helm template failed: {shlex.join(cmd)}\n{result.stderr}"
    docs = [d for d in yaml.safe_load_all(result.stdout) if d]
    deployments = [d for d in docs if d.get("kind") == "Deployment"]
    assert len(deployments) == 1, f"expected one serving Deployment, found {len(deployments)}"
    return deployments[0]


def _init_container(deployment: dict[str, Any]) -> dict[str, Any]:
    inits = deployment["spec"]["template"]["spec"].get("initContainers") or []
    matches = [c for c in inits if c.get("name") == INIT_NAME]
    assert len(matches) == 1, f"expected one initContainer named {INIT_NAME}, found {len(matches)}"
    return matches[0]


def _serving_container(deployment: dict[str, Any]) -> dict[str, Any]:
    containers = deployment["spec"]["template"]["spec"]["containers"]
    assert len(containers) == 1 and containers[0]["name"] == "serving"
    return containers[0]


def test_serving_deployment_has_wait_for_product_reference_init_container() -> None:
    deployment = _render_deployment()
    init = _init_container(deployment)
    assert init["image"] == f"foss-mcp-serving:{TAG}", "init container must use the serving image"


def test_init_container_waits_for_both_the_product_reference_and_recent_releases_sidecar_paths() -> None:
    deployment = _render_deployment("deployment.family=pdf", "deployment.platform=net")
    init = _init_container(deployment)
    serving_mounts = {m["name"]: m for m in _serving_container(deployment)["volumeMounts"]}
    mount = serving_mounts["manifests"]["mountPath"]
    expected_product_reference_file = f"{mount}/{_sidecar_name('pdf', 'net')}"
    expected_recent_releases_file = f"{mount}/{_recent_releases_sidecar_name('pdf', 'net')}"
    assert init["command"][:2] == ["sh", "-c"], "the gate must be a single sh loop"
    script = init["command"][2]
    assert f"[ -f {expected_product_reference_file} ]" in script, script
    assert f"[ -f {expected_recent_releases_file} ]" in script, script
    assert "sleep" in script and "until" in script, script
    # The gate runs no other command: only the loop, the two existence tests and the sleep.
    assert "python" not in script and "serve_http" not in script, script
    assert len(init["command"]) == 3, init["command"]


def test_init_container_mounts_manifests_claim_read_only() -> None:
    deployment = _render_deployment()
    init = _init_container(deployment)
    mounts = {m["name"]: m for m in init["volumeMounts"]}
    assert set(mounts) == {"manifests"}, f"init container mounts: {sorted(mounts)}"
    assert mounts["manifests"]["mountPath"] == "/data/manifests"
    assert mounts["manifests"]["readOnly"] is True
    volumes = {v["name"]: v for v in deployment["spec"]["template"]["spec"]["volumes"]}
    assert "persistentVolumeClaim" in volumes["manifests"]


def test_serving_container_is_unchanged() -> None:
    deployment = _render_deployment()
    serving = _serving_container(deployment)
    assert serving["command"] == ["python", "/app/infra/serve_http.py"]
    assert serving["image"] == f"foss-mcp-serving:{TAG}"
    assert serving["ports"] == [{"name": "http", "containerPort": 8080, "protocol": "TCP"}]
    assert serving["livenessProbe"]["httpGet"]["path"] == "/healthz"
    assert serving["readinessProbe"]["httpGet"]["path"] == "/readyz"
    env = {e["name"]: e["value"] for e in serving["env"]}
    assert env["FOSS_MCP_FAMILY"] == "pdf"
    assert env["FOSS_MCP_PLATFORM"] == "net"
    assert env["FOSS_MCP_SOURCE_KIND"] == "self_extracted"
    mounts = {m["name"]: m for m in serving["volumeMounts"]}
    assert mounts["manifests"] == {"name": "manifests", "mountPath": "/data/manifests", "readOnly": True}
    assert mounts["tmp"]["mountPath"] == "/tmp"
    assert "initContainers" not in serving
