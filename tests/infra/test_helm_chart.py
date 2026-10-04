"""TC-177: the Helm chart renders a deployable serving pod, Service, PVC and ingestion Jobs.

Every check renders the chart with the real helm binary and parses the output with PyYAML. A
missing helm binary FAILS these tests; it never skips them.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CHART = REPO_ROOT / "infra" / "helm" / "foss-mcp"
COMPOSE = REPO_ROOT / "docker-compose.yml"
TAG = "tc177-test-tag"
SERVING_IMAGE = f"foss-mcp-serving:{TAG}"
INGESTION_IMAGE = f"foss-mcp-ingestion:{TAG}"

# The four pilots whose ingestion arguments are checked against docker-compose.yml. Each entry is
# copied from that file's ingest-<family>-<platform> service.
PILOTS: list[dict[str, Any]] = [
    {
        "family": "pdf",
        "platform": "net",
        "sourceKind": "self_extracted",
        "heldBy": "ingestion-pdf-net",
        "apiSurface": "/app/fixtures/pdf_net/api_surface.json",
        "title": "pdf/net API surface",
        "maxTypes": 20,
        "furnishedPage": "/app/fixtures/furnished/pdf_net/pages/_index.md",
        "library": {
            "repository": "aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET",
            "commit": "b7172877651413cff57a8bfe41fb8a8befb2406b",
            "platform": "dotnet",
            "csproj": "src/Aspose.Pdf.Foss.csproj",
        },
    },
    {
        "family": "pdf",
        "platform": "typescript",
        "sourceKind": "self_extracted",
        "heldBy": "ingestion-pdf-typescript",
        "apiSurface": "/app/fixtures/pdf_typescript/api_surface.json",
        "title": "pdf/typescript API surface",
        "maxTypes": 20,
        "furnishedPage": "/app/fixtures/furnished/pdf_typescript/pages/_index.md",
        "library": {
            "repository": "aspose-pdf-foss/Aspose.PDF-FOSS-for-TypeScript",
            "commit": "155bfc7a33f0ba23fb4252b6ba201828b02a5b9d",
            "platform": "typescript",
            "packageName": "@asposefoss/pdf",
        },
    },
    {
        "family": "pdf",
        "platform": "cpp",
        "sourceKind": "self_extracted",
        "heldBy": "ingestion-pdf-cpp",
        "apiSurface": "/app/fixtures/pdf_cpp/api_surface.json",
        "title": "pdf/cpp API surface",
        "maxTypes": 20,
    },
    {
        "family": "cells",
        "platform": "rust",
        "sourceKind": "self_extracted",
        "heldBy": "ingestion-cells-rust",
        "apiSurface": "/app/fixtures/cells_rust/api_surface.json",
        "title": "cells/rust API surface",
        "maxTypes": 20,
        "furnishedPage": "/app/fixtures/furnished/cells_rust/pages/_index.md",
        "library": {
            "repository": "aspose-cells-foss/Aspose.Cells-FOSS-for-Rust",
            "commit": "1a6004af47b1ef15385f9d36d381a8172428cc7e",
            "platform": "rust",
            "crateName": "aspose-cells-foss-rust",
        },
    },
]


def _helm() -> str:
    helm = shutil.which("helm")
    if helm is None:
        pytest.fail(
            "helm is not on PATH. TC-177 requires it: source scripts/toolchain/activate.ps1, "
            "which adds C:\\dev-tools\\foss-mcp\\helm\\windows-amd64."
        )
    return helm


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_helm(), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
        check=False,
    )


def _render(*extra: str, tag: str | None = TAG) -> list[dict[str, Any]]:
    args = ["template", "foss-mcp", str(CHART)]
    if tag is not None:
        args += ["--set", f"image.tag={tag}"]
    args += list(extra)
    result = _run(args)
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def _by_kind(docs: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [doc for doc in docs if doc.get("kind") == kind]


def _pod_specs(docs: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]:
    """(workload, pod spec, container) for every Deployment and Job container."""
    found = []
    for doc in docs:
        if doc.get("kind") in ("Deployment", "Job"):
            spec = doc["spec"]["template"]["spec"]
            for container in spec["containers"]:
                found.append((doc, spec, container))
    return found


def _serving(docs: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    deployments = _by_kind(docs, "Deployment")
    assert len(deployments) == 1, f"expected one serving Deployment, got {len(deployments)}"
    spec = deployments[0]["spec"]["template"]["spec"]
    return deployments[0], spec, spec["containers"][0]


def _pilot_values(tmp_path: Path) -> Path:
    values = tmp_path / "pilots.yaml"
    values.write_text(yaml.safe_dump({"ingestion": {"pilots": PILOTS}}), encoding="utf-8")
    return values


def _job_name(pilot: dict[str, Any]) -> str:
    return f"foss-mcp-pdf-net-ingest-{pilot['family']}-{pilot['platform']}"


def test_helm_binary_resolves_and_runs() -> None:
    result = _run(["version", "--short"])
    assert result.returncode == 0, result.stderr
    assert "v3." in result.stdout, result.stdout


def test_chart_lints_clean_with_a_required_tag() -> None:
    result = _run(["lint", str(CHART), "--set", f"image.tag={TAG}"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 chart(s) failed" in result.stdout, result.stdout


def test_render_refuses_to_run_without_an_image_tag() -> None:
    result = _run(["template", "foss-mcp", str(CHART)])
    assert result.returncode != 0, "rendering with no image.tag must fail"
    assert "image.tag must be set to a pinned version or digest" in result.stderr, result.stderr


def test_serving_deployment_runs_the_http_entrypoint() -> None:
    _, _, container = _serving(_render())
    assert container["command"] == ["python", "/app/infra/serve_http.py"]


def test_serving_pod_has_liveness_and_readiness_probes_on_8080() -> None:
    _, _, container = _serving(_render())
    liveness = container["livenessProbe"]["httpGet"]
    readiness = container["readinessProbe"]["httpGet"]
    assert liveness["path"] == "/healthz"
    assert liveness["port"] == 8080
    assert readiness["path"] == "/readyz"
    assert readiness["port"] == 8080
    assert container["readinessProbe"]["initialDelaySeconds"] > 0
    assert container["readinessProbe"]["periodSeconds"] > 0


def test_every_pod_runs_non_root_with_a_read_only_root_and_a_tmp_mount() -> None:
    pods = _pod_specs(_render())
    assert len(pods) >= 2, "expected the serving Deployment and at least one ingestion Job"
    for workload, spec, container in pods:
        name = workload["metadata"]["name"]
        assert spec["securityContext"]["runAsNonRoot"] is True, name
        assert spec["securityContext"]["runAsUser"] == 10001, name
        sc = container["securityContext"]
        assert sc["allowPrivilegeEscalation"] is False, name
        assert sc["readOnlyRootFilesystem"] is True, name
        assert sc["capabilities"]["drop"] == ["ALL"], name
        mount_paths = {mount["mountPath"] for mount in container["volumeMounts"]}
        assert "/tmp" in mount_paths, name


def test_no_image_is_latest_and_the_release_tag_is_applied() -> None:
    values = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
    assert values["image"]["tag"] != "latest"
    images = {container["image"] for _, _, container in _pod_specs(_render())}
    assert images == {SERVING_IMAGE, INGESTION_IMAGE}, images
    assert not any(image.endswith(":latest") for image in images)


def test_service_routes_port_80_to_the_http_port_for_serving_only() -> None:
    services = _by_kind(_render(), "Service")
    assert len(services) == 1, "expected exactly one Service"
    spec = services[0]["spec"]
    assert spec["type"] == "ClusterIP"
    assert spec["ports"][0]["port"] == 80
    assert spec["ports"][0]["targetPort"] == 8080
    assert spec["selector"]["app.kubernetes.io/component"] == "serving"


def test_manifests_claim_exists_and_serving_mounts_it_read_only_at_data_manifests() -> None:
    docs = _render()
    claims = _by_kind(docs, "PersistentVolumeClaim")
    assert len(claims) == 1, "expected exactly one PersistentVolumeClaim"
    claim_name = claims[0]["metadata"]["name"]
    _, spec, container = _serving(docs)
    volumes = {volume["name"]: volume for volume in spec["volumes"]}
    assert volumes["manifests"]["persistentVolumeClaim"]["claimName"] == claim_name
    mounts = {mount["mountPath"]: mount for mount in container["volumeMounts"]}
    assert mounts["/data/manifests"]["readOnly"] is True


def test_one_ingestion_job_is_rendered_per_listed_pilot_all_on_the_same_claim(tmp_path: Path) -> None:
    docs = _render("-f", str(_pilot_values(tmp_path)))
    jobs = _by_kind(docs, "Job")
    names = sorted(job["metadata"]["name"] for job in jobs)
    assert names == sorted(_job_name(pilot) for pilot in PILOTS)
    claim_name = _by_kind(docs, "PersistentVolumeClaim")[0]["metadata"]["name"]
    for job in jobs:
        spec = job["spec"]
        assert spec["backoffLimit"] == 2
        pod = spec["template"]["spec"]
        assert pod["restartPolicy"] == "Never"
        volumes = {volume["name"]: volume for volume in pod["volumes"]}
        assert volumes["manifests"]["persistentVolumeClaim"]["claimName"] == claim_name
        mounts = {mount["mountPath"]: mount for mount in pod["containers"][0]["volumeMounts"]}
        assert mounts["/data/manifests"].get("readOnly") is not True


def test_default_values_render_exactly_one_ingestion_job_for_the_deployment_identity() -> None:
    jobs = _by_kind(_render(), "Job")
    assert [job["metadata"]["name"] for job in jobs] == ["foss-mcp-pdf-net-ingest-pdf-net"]


def test_each_ingestion_job_runs_the_same_two_step_chain_as_docker_compose(tmp_path: Path) -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    jobs = {
        job["metadata"]["name"]: job for job in _by_kind(_render("-f", str(_pilot_values(tmp_path))), "Job")
    }
    for pilot in PILOTS:
        service = compose[f"ingest-{pilot['family']}-{pilot['platform']}"]
        job = jobs[_job_name(pilot)]
        container = job["spec"]["template"]["spec"]["containers"][0]
        assert container["command"] == service["entrypoint"], pilot
        assert shlex.split(container["args"][0]) == shlex.split(service["command"][0]), pilot


def test_every_container_declares_resource_requests_and_limits() -> None:
    for workload, _, container in _pod_specs(_render()):
        resources = container["resources"]
        for key in ("requests", "limits"):
            assert {"cpu", "memory"} <= set(resources[key]), (workload["metadata"]["name"], key)


def _covers_port_53(entry: dict[str, Any]) -> bool:
    port = entry.get("port")
    if port is None:
        return True
    return port <= 53 <= entry.get("endPort", port)


def _rule_permits_port_53(rule: dict[str, Any]) -> bool:
    if "ports" not in rule:
        return True  # no ports list means every port, including 53
    return any(_covers_port_53(entry) for entry in rule["ports"])


def _is_world_or_all_pods(peer: dict[str, Any]) -> bool:
    if peer.get("ipBlock", {}).get("cidr") in ("0.0.0.0/0", "::/0"):
        return True
    # An empty namespaceSelector selects every namespace; with no podSelector restriction that is
    # every pod in the cluster. A podSelector alone is scoped to the policy's own namespace.
    return peer.get("namespaceSelector") == {} and peer.get("podSelector", {}) == {}


def _opens_port_53_to_world_or_all_pods(rule: dict[str, Any]) -> bool:
    if not _rule_permits_port_53(rule):
        return False
    peers = rule.get("to")
    if not peers:
        return True  # no destination list means any destination
    return any(_is_world_or_all_pods(peer) for peer in peers)


def test_dns_egress_reaches_only_kube_dns_on_udp_and_tcp_53() -> None:
    docs = _render()
    serving, _, _ = _serving(docs)
    serving_labels = serving["spec"]["template"]["metadata"]["labels"]
    selecting = [policy for policy in _by_kind(docs, "NetworkPolicy") if _selects(policy, serving_labels)]
    assert len(selecting) == 1, f"expected one NetworkPolicy selecting the serving pods, got {len(selecting)}"
    egress = selecting[0]["spec"]["egress"]
    kube_dns_rules = [
        rule
        for rule in egress
        if {"protocol": "UDP", "port": 53} in rule.get("ports", [])
        and {"protocol": "TCP", "port": 53} in rule.get("ports", [])
        and any(
            peer.get("podSelector", {}).get("matchLabels") == {"k8s-app": "kube-dns"}
            and peer.get("namespaceSelector", {}).get("matchLabels")
            == {"kubernetes.io/metadata.name": "kube-system"}
            for peer in rule.get("to", [])
        )
    ]
    assert kube_dns_rules, "expected an egress rule allowing UDP and TCP 53 to kube-dns in kube-system"
    wide = [rule for rule in egress if _opens_port_53_to_world_or_all_pods(rule)]
    assert wide == [], f"port 53 must not open to 0.0.0.0/0 or to all pods: {wide}"


def _selects(policy: dict[str, Any], labels: dict[str, str]) -> bool:
    """True when the policy's podSelector matches pods carrying these labels (matchLabels only)."""
    selector = policy["spec"]["podSelector"]
    assert "matchExpressions" not in selector, "these checks read matchLabels only"
    return all(labels.get(key) == value for key, value in selector.get("matchLabels", {}).items())


def _grants_any_internet(rule: dict[str, Any]) -> bool:
    if "to" not in rule:
        return True  # an egress rule with no destination list allows every destination
    return any(peer.get("ipBlock", {}).get("cidr") in ("0.0.0.0/0", "::/0") for peer in rule["to"])


def _grants_tcp_443_to_any_destination(rule: dict[str, Any]) -> bool:
    return {"protocol": "TCP", "port": 443} in rule.get("ports", []) and rule.get("to") == [
        {"ipBlock": {"cidr": "0.0.0.0/0"}}
    ]


def test_ingestion_job_pod_template_carries_the_ingestion_component_label() -> None:
    jobs = _by_kind(_render(), "Job")
    assert jobs, "expected at least one ingestion Job"
    for job in jobs:
        labels = job["spec"]["template"]["metadata"]["labels"]
        assert labels.get("app.kubernetes.io/component") == "ingestion", job["metadata"]["name"]


def test_tcp_443_to_any_destination_is_granted_only_to_ingestion_pods() -> None:
    docs = _render()
    grants = [
        (policy, rule)
        for policy in _by_kind(docs, "NetworkPolicy")
        for rule in policy["spec"].get("egress", [])
        if _grants_tcp_443_to_any_destination(rule)
    ]
    assert len(grants) == 1, f"expected exactly one TCP 443 to 0.0.0.0/0 rule, got {len(grants)}"
    policy, _ = grants[0]
    selector = policy["spec"]["podSelector"]["matchLabels"]
    assert selector.get("app.kubernetes.io/component") == "ingestion", selector
    jobs = _by_kind(docs, "Job")
    assert jobs, "expected at least one ingestion Job"
    for job in jobs:
        labels = job["spec"]["template"]["metadata"]["labels"]
        assert _selects(policy, labels), f"the TCP 443 policy must select {job['metadata']['name']}"


def test_serving_pods_receive_no_internet_egress_rule() -> None:
    docs = _render()
    serving, _, _ = _serving(docs)
    serving_labels = serving["spec"]["template"]["metadata"]["labels"]
    assert serving_labels.get("app.kubernetes.io/component") == "serving"
    selecting = [policy for policy in _by_kind(docs, "NetworkPolicy") if _selects(policy, serving_labels)]
    assert selecting, "expected at least one NetworkPolicy to select the serving pods"
    for policy in selecting:
        for rule in policy["spec"].get("egress", []):
            assert not _grants_any_internet(rule), (policy["metadata"]["name"], rule)
