"""G3/TC-512: chart lifecycle and hardening, rendered offline with the real helm binary.

Pins: the bounded and non-empty-aware serving init gate (rendered AND run in a real shell), the
Job deadline and TTL, the serving startupProbe, the kept manifests claim, the pod hardening shared
by serving, ingestion and demo pods, the pdf/cpp memory limit, the accepted-shrink environment and the
gateway egress policy. A missing helm binary FAILS these tests; it never skips them. No cluster, no
network and no wall-clock assertions.
"""

from __future__ import annotations

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
DEPLOYMENT_MD = REPO_ROOT / "docs" / "DEPLOYMENT.md"
INSTALL_PILOTS = REPO_ROOT / "scripts" / "deploy" / "install_pilots.py"
TAG = "tc512-test-tag"
INIT_NAME = "wait-for-product-reference-and-recent-releases"
PRODUCT_REFERENCE = "product_reference_pdf_net.json"
RECENT_RELEASES = "recent_releases_pdf_net.json"


def _helm() -> str:
    helm = shutil.which("helm")
    if helm is None:
        raise AssertionError(
            "helm is not on PATH; these tests fail rather than skip. Source "
            "scripts/toolchain/activate.ps1 in the same command as pytest."
        )
    return helm


def _helm_template(*extra: str) -> subprocess.CompletedProcess[str]:
    cmd = [_helm(), "template", "foss-mcp", str(CHART), "--set", f"image.tag={TAG}", *extra]
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", check=False)


def _render(*extra: str) -> list[dict[str, Any]]:
    result = _helm_template(*extra)
    assert result.returncode == 0, f"helm template failed: {shlex.join(extra)}\n{result.stderr}"
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def _by_kind(docs: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [d for d in docs if d.get("kind") == kind]


def _component(doc: dict[str, Any]) -> str:
    return doc["spec"]["template"]["metadata"]["labels"].get("app.kubernetes.io/component", "")


def _serving(docs: list[dict[str, Any]]) -> dict[str, Any]:
    found = [d for d in _by_kind(docs, "Deployment") if _component(d) == "serving"]
    assert len(found) == 1, f"expected one serving Deployment, found {len(found)}"
    return found[0]


def _gate(deployment: dict[str, Any]) -> dict[str, Any]:
    inits = deployment["spec"]["template"]["spec"].get("initContainers") or []
    matches = [c for c in inits if c.get("name") == INIT_NAME]
    assert len(matches) == 1
    return matches[0]


def _script(*extra: str) -> str:
    command = _gate(_serving(_render(*extra)))["command"]
    assert len(command) == 3
    return command[2]


def _values() -> dict[str, Any]:
    return yaml.safe_load(VALUES.read_text(encoding="utf-8"))


def _shell() -> str:
    found = shutil.which("sh") or shutil.which("bash")
    if found is None:
        raise AssertionError(
            "neither sh nor bash is on PATH; the real-shell gate tests fail rather than skip"
        )
    return found


# ---- the init gate ------------------------------------------------------------------------------


def test_init_gate_script_is_bounded_by_the_configured_timeout() -> None:
    script = _script("--set", "initGate.timeoutSeconds=77", "--set", "initGate.intervalSeconds=3")
    assert "-ge 77" in script
    assert "sleep 3" in script
    assert "waited=$((waited + 3))" in script
    assert script.count("exit 0; fi;") == 1
    assert script.startswith("waited=0; until ")


def test_default_gate_timeout_is_the_job_deadline_plus_five_minutes() -> None:
    values = _values()
    assert values["ingestion"]["activeDeadlineSeconds"] == 3600
    assert values["initGate"]["timeoutSeconds"] == 3900
    assert values["initGate"]["timeoutSeconds"] == values["ingestion"]["activeDeadlineSeconds"] + 300
    assert values["initGate"]["intervalSeconds"] == 2
    assert "-ge 3900" in _script()


def test_gate_still_tests_existence_and_non_emptiness_of_both_sidecars() -> None:
    script = _script()
    base = "/data/manifests"
    for name in (PRODUCT_REFERENCE, RECENT_RELEASES):
        assert f"[ -f {base}/{name} ]" in script
        assert f"[ -s {base}/{name} ]" in script
    assert script.index(f"[ -f {base}/{PRODUCT_REFERENCE} ]") < script.index(
        f"[ -f {base}/{RECENT_RELEASES} ]"
    )
    assert "until " in script and "sleep " in script


def test_gate_command_is_three_elements_and_runs_no_python() -> None:
    command = _gate(_serving(_render()))["command"]
    assert len(command) == 3
    assert command[:2] == ["sh", "-c"]
    assert "python" not in command[2]


@pytest.mark.parametrize("case", ["empty_directory", "both_present_and_non_empty", "one_file_zero_bytes"])
def test_rendered_gate_script_run_in_a_shell(tmp_path: Path, case: str) -> None:
    script = _script(
        "--set",
        "manifests.mountPath=.",
        "--set",
        "initGate.timeoutSeconds=2",
        "--set",
        "initGate.intervalSeconds=1",
    )
    if case == "both_present_and_non_empty":
        (tmp_path / PRODUCT_REFERENCE).write_text("{}", encoding="utf-8")
        (tmp_path / RECENT_RELEASES).write_text("[]", encoding="utf-8")
    elif case == "one_file_zero_bytes":
        (tmp_path / PRODUCT_REFERENCE).write_text("{}", encoding="utf-8")
        (tmp_path / RECENT_RELEASES).write_bytes(b"")
    before = sorted(p.name for p in tmp_path.iterdir())
    done = subprocess.run(
        [_shell(), "-c", script], cwd=tmp_path, capture_output=True, text=True, timeout=60, check=False
    )
    assert done.returncode == 0, done.stderr
    assert sorted(p.name for p in tmp_path.iterdir()) == before, "the gate must not create or remove a file"
    if case == "both_present_and_non_empty":
        assert done.stderr == ""
    else:
        assert PRODUCT_REFERENCE in done.stderr and RECENT_RELEASES in done.stderr
        assert "timed out after 2 seconds" in done.stderr
        assert "WITHOUT" in done.stderr
    if case == "empty_directory":
        assert before == []


def test_init_container_declares_requests_and_limits_from_values() -> None:
    assert _gate(_serving(_render()))["resources"] == {
        "requests": {"cpu": "10m", "memory": "16Mi"},
        "limits": {"cpu": "50m", "memory": "64Mi"},
    }
    docs = _render("--set", "initGate.resources.limits.memory=128Mi")
    assert _gate(_serving(docs))["resources"]["limits"]["memory"] == "128Mi"


# ---- Jobs, probes, claim -------------------------------------------------------------------------


def _job_specs(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    jobs = _by_kind(docs, "Job")
    assert jobs
    return [j["spec"] for j in jobs]


def test_job_gets_active_deadline_and_ttl_from_values() -> None:
    for spec in _job_specs(_render()):
        assert spec["activeDeadlineSeconds"] == 3600
        assert spec["ttlSecondsAfterFinished"] == 86400
    overridden = _render(
        "--set", "ingestion.activeDeadlineSeconds=120", "--set", "ingestion.ttlSecondsAfterFinished=60"
    )
    for spec in _job_specs(overridden):
        assert (spec["activeDeadlineSeconds"], spec["ttlSecondsAfterFinished"]) == (120, 60)
    for zero in ("--set", "--set-string"):
        off = _render(zero, "ingestion.activeDeadlineSeconds=0", zero, "ingestion.ttlSecondsAfterFinished=0")
        for spec in _job_specs(off):
            assert "activeDeadlineSeconds" not in spec and "ttlSecondsAfterFinished" not in spec
    empty = _render(
        "--set-string",
        "ingestion.activeDeadlineSeconds=",
        "--set-string",
        "ingestion.ttlSecondsAfterFinished=",
    )
    for spec in _job_specs(empty):
        assert "activeDeadlineSeconds" not in spec and "ttlSecondsAfterFinished" not in spec


def test_serving_has_a_startup_probe_on_healthz() -> None:
    serving = _serving(_render())["spec"]["template"]["spec"]["containers"][0]
    probe = serving["startupProbe"]
    assert probe["httpGet"] == {"path": "/healthz", "port": 8080}
    startup = _values()["probes"]["startup"]
    assert (probe["periodSeconds"], probe["timeoutSeconds"], probe["failureThreshold"]) == (
        startup["periodSeconds"],
        startup["timeoutSeconds"],
        startup["failureThreshold"],
    )
    assert startup == {"periodSeconds": 5, "timeoutSeconds": 3, "failureThreshold": 30}
    assert serving["livenessProbe"] == {
        "httpGet": {"path": "/healthz", "port": 8080},
        "initialDelaySeconds": 10,
        "periodSeconds": 10,
        "timeoutSeconds": 5,
        "failureThreshold": 3,
    }
    assert serving["readinessProbe"] == {
        "httpGet": {"path": "/readyz", "port": 8080},
        "initialDelaySeconds": 5,
        "periodSeconds": 10,
        "timeoutSeconds": 3,
        "failureThreshold": 3,
    }


def test_pvc_is_kept_on_uninstall_by_default_and_the_flag_turns_it_off() -> None:
    (claim,) = _by_kind(_render(), "PersistentVolumeClaim")
    assert claim["metadata"]["annotations"] == {"helm.sh/resource-policy": "keep"}
    (released,) = _by_kind(_render("--set", "manifests.keepOnUninstall=false"), "PersistentVolumeClaim")
    assert "annotations" not in released["metadata"]


# ---- pod hardening -------------------------------------------------------------------------------


def _pod_spec(kind: str) -> dict[str, Any]:
    docs = _render("--set", "demo.enabled=true", "--set", "demo.image.tag=t")
    if kind == "serving":
        return _serving(docs)["spec"]["template"]["spec"]
    if kind == "ingestion":
        return _by_kind(docs, "Job")[0]["spec"]["template"]["spec"]
    demos = [d for d in _by_kind(docs, "Deployment") if _component(d) == "demo-serving"]
    assert demos
    return demos[0]["spec"]["template"]["spec"]


@pytest.mark.parametrize("kind", ["serving", "ingestion", "demo"])
def test_every_pod_is_hardened(kind: str) -> None:
    spec = _pod_spec(kind)
    context = spec["securityContext"]
    assert context["seccompProfile"]["type"] == "RuntimeDefault"
    assert context["runAsGroup"] == 10001
    assert context["runAsUser"] == 10001
    assert context["runAsNonRoot"] is True
    assert spec["automountServiceAccountToken"] is False
    assert spec["enableServiceLinks"] is False


def test_pod_hardening_can_be_relaxed_by_values() -> None:
    docs = _render(
        "--set",
        "pod.enableServiceLinks=true",
        "--set",
        "pod.automountServiceAccountToken=true",
        "--set",
        "demo.enabled=true",
        "--set",
        "demo.image.tag=t",
    )
    specs = [_serving(docs)["spec"]["template"]["spec"], _by_kind(docs, "Job")[0]["spec"]["template"]["spec"]]
    specs += [
        d["spec"]["template"]["spec"] for d in _by_kind(docs, "Deployment") if _component(d) == "demo-serving"
    ]
    for spec in specs:
        assert spec["enableServiceLinks"] is True
        assert spec["automountServiceAccountToken"] is True


def test_no_code_reads_kubernetes_service_environment_variables() -> None:
    needles = ("KUBERNETES_", "_SERVICE_HOST", "_SERVICE_PORT")
    files = sorted((REPO_ROOT / "infra").glob("*.py")) + sorted((REPO_ROOT / "src").rglob("*.py"))
    assert files, "expected python sources under infra/ and src/"
    hits = [
        f"{path.relative_to(REPO_ROOT)}: {needle}"
        for path in files
        for needle in needles
        if needle in path.read_text(encoding="utf-8", errors="replace")
    ]
    assert hits == [], f"disabling service links is only safe while nothing reads these: {hits}"


# ---- memory, accepted shrink, egress -------------------------------------------------------------

FIVE_LARGE = {("html", "python"), ("page", "python"), ("words", "net"), ("slides", "cpp"), ("pdf", "cpp")}


def _job_memory(docs: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    for job in _by_kind(docs, "Job"):
        match = re.search(r"-ingest-([a-z0-9]+)-([a-z0-9]+)$", job["metadata"]["name"])
        assert match, job["metadata"]["name"]
        container = job["spec"]["template"]["spec"]["containers"][0]
        out[(match.group(1), match.group(2))] = container["resources"]["limits"]["memory"]
    return out


def test_pdf_cpp_job_renders_10gi_and_the_other_default_jobs_stay_4gi() -> None:
    memory = _job_memory(_render())
    assert memory[("pdf", "cpp")] == "10Gi"
    assert memory[("slides", "cpp")] == "10Gi"
    for key, limit in memory.items():
        if key not in FIVE_LARGE:
            assert limit == "4Gi", key
    pilot = next(
        p for p in _values()["ingestion"]["pilots"] if (p["family"], p["platform"]) == ("pdf", "cpp")
    )
    assert pilot["ingestionMemoryLimit"] == "10Gi"
    assert "ingestionMemoryLimit" not in pilot["library"]


def _job_envs(docs: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {e["name"]: e.get("value", "") for e in j["spec"]["template"]["spec"]["containers"][0]["env"]}
        for j in _by_kind(docs, "Job")
    ]


def _job_args(docs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        j["metadata"]["name"]: (
            j["spec"]["template"]["spec"]["containers"][0]["command"],
            j["spec"]["template"]["spec"]["containers"][0]["args"],
        )
        for j in _by_kind(docs, "Job")
    }


def test_accept_regression_env_is_rendered_only_when_values_set_it() -> None:
    default = _render()
    for env in _job_envs(default):
        assert "FOSS_MCP_ACCEPT_REGRESSION" not in env
        assert "FOSS_MCP_ACCEPT_REGRESSION_REASON" not in env
    accepted = _render(
        "--set-json",
        'ingestion.acceptRegression=["symbols:100->46","total:153->58"]',
        "--set",
        "ingestion.acceptRegressionReason=why",
    )
    envs = _job_envs(accepted)
    assert envs
    for env in envs:
        assert env["FOSS_MCP_ACCEPT_REGRESSION"] == "symbols:100->46;total:153->58"
        assert env["FOSS_MCP_ACCEPT_REGRESSION_REASON"] == "why"
    assert _job_args(accepted) == _job_args(default)
    refused = _helm_template("--set-json", 'ingestion.acceptRegression=["symbols:100->46"]')
    assert refused.returncode != 0
    assert "acceptRegressionReason" in refused.stderr


def _grants_443_to_the_world(rule: dict[str, Any]) -> bool:
    ports = rule.get("ports", [])
    destinations = rule.get("to")
    open_ports = any(p.get("port") == 443 for p in ports) or not ports
    open_world = destinations is None or any(
        peer.get("ipBlock", {}).get("cidr") in ("0.0.0.0/0", "::/0") for peer in destinations
    )
    return open_ports and open_world


def _selects(policy: dict[str, Any], labels: dict[str, str]) -> bool:
    selector = policy["spec"]["podSelector"]
    if any(labels.get(k) != v for k, v in selector.get("matchLabels", {}).items()):
        return False
    for expression in selector.get("matchExpressions", []):
        assert expression["operator"] == "In"
        if labels.get(expression["key"]) not in expression["values"]:
            return False
    return True


def test_gateway_egress_policy_exists_only_when_a_gateway_secret_is_configured() -> None:
    default = _render("--set", "demo.enabled=true", "--set", "demo.image.tag=t")
    assert not [
        p for p in _by_kind(default, "NetworkPolicy") if p["metadata"]["name"].endswith("-gateway-egress")
    ]
    serving_labels = _serving(default)["spec"]["template"]["metadata"]["labels"]
    for policy in _by_kind(default, "NetworkPolicy"):
        if _selects(policy, serving_labels):
            for rule in policy["spec"].get("egress", []):
                assert not _grants_443_to_the_world(rule), (policy["metadata"]["name"], rule)

    docs = _render(
        "--set",
        "embeddingGateway.existingSecret=gw",
        "--set",
        "demo.enabled=true",
        "--set",
        "demo.image.tag=t",
    )
    policies = [
        p for p in _by_kind(docs, "NetworkPolicy") if p["metadata"]["name"].endswith("-gateway-egress")
    ]
    assert len(policies) == 1
    gateway = policies[0]
    selected = {
        _component(d)
        for d in _by_kind(docs, "Deployment") + _by_kind(docs, "Job")
        if _selects(gateway, d["spec"]["template"]["metadata"]["labels"])
    }
    assert selected == {"serving", "demo-serving"}


# ---- documentation -------------------------------------------------------------------------------


def test_deployment_md_describes_the_bounded_gate_keep_and_wait_for_jobs() -> None:
    text = DEPLOYMENT_MD.read_text(encoding="utf-8")
    assert "initGate" in text
    assert "startupProbe" in text or "startup probe" in text
    assert "helm.sh/resource-policy" in text
    assert "--wait-for-jobs" in text
    assert "INGESTION FAILED" in text


def test_deployment_md_and_install_pilots_no_longer_say_fourteen() -> None:
    for path in (DEPLOYMENT_MD, INSTALL_PILOTS):
        text = path.read_text(encoding="utf-8")
        assert "fourteen" not in text.lower(), path.name
        assert "forty" in text.lower() or "40" in text, path.name
