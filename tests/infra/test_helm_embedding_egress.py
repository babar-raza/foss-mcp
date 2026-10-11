"""TC-338: serving pods can reach the embedding gateway through an additive egress policy.

The always-rendered base NetworkPolicy lets serving pods reach only same-namespace pods and
kube-dns, so in a policy-enforcing cluster the query-time call to the embedding gateway (wired in
by TC-334) would be dropped. A third policy, `<fullname>-gateway-egress`, is therefore rendered
only when embeddingGateway.existingSecret is set. It grants TCP 443 to embeddingGateway.egressCidrs
for serving pods only, and the two base documents are never touched.

TC-344: that third policy lives in its own template, templates/networkpolicy-gateway-egress.yaml.
The always-on templates/networkpolicy.yaml must contain no values-driven Helm conditional (the
reference overlay's defect was a values toggle that removed isolation, and
tests/test_container_build.py guards it), so the conditional belongs in the separate file.

Offline. Renders the chart with the real helm binary (image.tag set) and parses the output with
PyYAML. A missing helm binary FAILS these tests; it never skips them.
"""

from __future__ import annotations

import functools
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CHART = REPO_ROOT / "infra" / "helm" / "foss-mcp"
VALUES = CHART / "values.yaml"
NETWORKPOLICY_TEMPLATE = CHART / "templates" / "networkpolicy.yaml"
GATEWAY_EGRESS_TEMPLATE = CHART / "templates" / "networkpolicy-gateway-egress.yaml"
SECRET_GUARD = "{{- if .Values.embeddingGateway.existingSecret }}"
TAG = "tc338-test-tag"
SECRET = "embedding-gateway-creds"
COMPONENT = "app.kubernetes.io/component"
CUSTOM_CIDRS = ("10.0.0.0/8", "192.168.1.0/24")


def _helm() -> str:
    helm = shutil.which("helm")
    if helm is None:
        pytest.fail(
            "helm is not on PATH. TC-338 requires it: source scripts/toolchain/activate.ps1, "
            "which adds C:\\dev-tools\\foss-mcp\\helm\\windows-amd64."
        )
    return helm


def _run(*extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_helm(), "template", "foss-mcp", str(CHART), "--set", f"image.tag={TAG}", *extra],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
        check=False,
    )


@functools.cache
def _render_text(*extra: str) -> str:
    result = _run(*extra)
    assert result.returncode == 0, result.stderr
    return result.stdout


def _docs(text: str) -> list[dict[str, Any]]:
    return [doc for doc in yaml.safe_load_all(text) if doc]


def _render(*extra: str) -> list[dict[str, Any]]:
    return _docs(_render_text(*extra))


def _values() -> dict[str, Any]:
    return yaml.safe_load(VALUES.read_text(encoding="utf-8"))


def _with_secret() -> tuple[str, ...]:
    return ("--set", f"embeddingGateway.existingSecret={SECRET}")


def _with_custom_cidrs() -> tuple[str, ...]:
    return (*_with_secret(), "--set", "embeddingGateway.egressCidrs={" + ",".join(CUSTOM_CIDRS) + "}")


def _network_policies(docs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {d["metadata"]["name"]: d for d in docs if d.get("kind") == "NetworkPolicy"}


def _base_name() -> str:
    deployment = _values()["deployment"]
    return f"foss-mcp-{deployment['family']}-{deployment['platform']}"


def _gateway_policy(docs: list[dict[str, Any]]) -> dict[str, Any]:
    policies = _network_policies(docs)
    name = f"{_base_name()}-gateway-egress"
    assert name in policies, sorted(policies)
    return policies[name]


# --- values.yaml -------------------------------------------------------------------------------


def test_values_default_egress_cidrs_is_any_destination() -> None:
    assert _values()["embeddingGateway"]["egressCidrs"] == ["0.0.0.0/0"]


def test_values_leave_the_other_three_gateway_keys_unchanged() -> None:
    block = _values()["embeddingGateway"]
    assert block["existingSecret"] == ""
    assert block["endpointKey"] == "endpoint"
    assert block["apiKeyKey"] == "apiKey"
    assert sorted(block) == ["apiKeyKey", "egressCidrs", "endpointKey", "existingSecret"]


# --- template text -----------------------------------------------------------------------------


def test_gateway_egress_template_is_wholly_wrapped_in_the_secret_guard() -> None:
    text = GATEWAY_EGRESS_TEMPLATE.read_text(encoding="utf-8")
    assert text.count(SECRET_GUARD) == 1
    head, tail = text.split(SECRET_GUARD)
    # The guard is the very first thing in the file and its closing end is the very last, so with
    # the secret unset the whole file renders nothing. The file is a single document, so it has no
    # leading separator of its own.
    assert head.strip() == ""
    assert "\n---\n" not in text
    assert "gateway-egress" in tail
    assert tail.rstrip().endswith("{{- end }}")


def test_the_unconditional_networkpolicy_template_has_no_values_driven_conditional() -> None:
    # The contract TC-344 restores: the always-on base policy file renders unconditionally. Comment
    # lines are stripped first, since this file's header comment names the rule it enforces.
    text = NETWORKPOLICY_TEMPLATE.read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.strip().startswith("#"))
    assert not re.search(r"\{\{-?\s*if\b", code), "networkpolicy.yaml must render unconditionally"
    assert "gateway-egress" not in text
    assert "embeddingGateway" not in text


def test_the_secret_guard_lives_in_the_gateway_egress_template() -> None:
    text = GATEWAY_EGRESS_TEMPLATE.read_text(encoding="utf-8")
    assert SECRET_GUARD in text
    assert "kind: NetworkPolicy" in text
    assert "-gateway-egress" in text


# --- (1) default values: exactly today's two NetworkPolicy documents ----------------------------


def test_default_render_has_exactly_the_base_and_ingestion_egress_policies() -> None:
    policies = _network_policies(_render())
    assert sorted(policies) == sorted([_base_name(), f"{_base_name()}-ingestion-egress"])


def test_default_render_has_no_gateway_egress_policy() -> None:
    text = _render_text()
    assert "-gateway-egress" not in text
    assert not any(name.endswith("-gateway-egress") for name in _network_policies(_render()))


def test_unset_secret_renders_nothing_even_with_an_empty_cidr_list(tmp_path: Path) -> None:
    values_file = tmp_path / "empty-cidrs.yaml"
    values_file.write_text("embeddingGateway:\n  egressCidrs: []\n", encoding="utf-8")
    result = _run("-f", str(values_file))
    assert result.returncode == 0, result.stderr
    assert "-gateway-egress" not in result.stdout


# --- (2) with a secret: a serving-only, egress-only, TCP 443 policy ------------------------------


def test_configured_render_adds_the_gateway_egress_policy() -> None:
    policies = _network_policies(_render(*_with_secret()))
    assert sorted(policies) == sorted(
        [_base_name(), f"{_base_name()}-ingestion-egress", f"{_base_name()}-gateway-egress"]
    )


def test_gateway_policy_selector_names_both_serving_components_and_never_ingestion() -> None:
    selector = _gateway_policy(_render(*_with_secret()))["spec"]["podSelector"]
    assert selector["matchLabels"] == {
        "app.kubernetes.io/name": "foss-mcp",
        "app.kubernetes.io/instance": "foss-mcp",
    }
    assert selector["matchExpressions"] == [
        {"key": COMPONENT, "operator": "In", "values": ["serving", "demo-serving"]}
    ]
    assert "ingestion" not in yaml.safe_dump(selector)


def _pod_labels(docs: list[dict[str, Any]], kind: str, component: str) -> dict[str, str]:
    found = [
        d["spec"]["template"]["metadata"]["labels"]
        for d in docs
        if d.get("kind") == kind and d["spec"]["template"]["metadata"]["labels"].get(COMPONENT) == component
    ]
    assert found, f"expected a {kind} whose pods carry {COMPONENT}={component}"
    return found[0]


def _selects(selector: dict[str, Any], labels: dict[str, str]) -> bool:
    for key, value in selector.get("matchLabels", {}).items():
        if labels.get(key) != value:
            return False
    for expression in selector.get("matchExpressions", []):
        assert expression["operator"] == "In", expression
        if labels.get(expression["key"]) not in expression["values"]:
            return False
    return True


def test_gateway_policy_selects_real_serving_and_demo_serving_pods_but_not_ingestion_pods() -> None:
    # Applies the rendered selector to the labels the chart really puts on each kind of pod, so a
    # selector that silently misses demo-serving pods (the lean demo topology) or catches
    # ingestion pods fails here rather than in a cluster.
    docs = _render(*_with_secret(), "--set", "demo.enabled=true", "--set", "demo.image.tag=tc338-demo-tag")
    selector = _gateway_policy(docs)["spec"]["podSelector"]
    assert _selects(selector, _pod_labels(docs, "Deployment", "serving"))
    assert _selects(selector, _pod_labels(docs, "Deployment", "demo-serving"))
    assert not _selects(selector, _pod_labels(docs, "Job", "ingestion"))


def test_gateway_policy_is_egress_only() -> None:
    assert _gateway_policy(_render(*_with_secret()))["spec"]["policyTypes"] == ["Egress"]


def test_gateway_policy_allows_tcp_443_to_any_address_by_default() -> None:
    spec = _gateway_policy(_render(*_with_secret()))["spec"]
    assert spec["egress"] == [
        {
            "ports": [{"protocol": "TCP", "port": 443}],
            "to": [{"ipBlock": {"cidr": "0.0.0.0/0"}}],
        }
    ]


def test_gateway_policy_does_not_duplicate_dns() -> None:
    spec = _gateway_policy(_render(*_with_secret()))["spec"]
    ports = [port["port"] for rule in spec["egress"] for port in rule["ports"]]
    assert ports == [443]


def test_gateway_policy_carries_the_chart_labels() -> None:
    labels = _gateway_policy(_render(*_with_secret()))["metadata"]["labels"]
    assert labels["app.kubernetes.io/name"] == "foss-mcp"
    assert labels["app.kubernetes.io/managed-by"] == "Helm"


# --- (3) custom CIDRs ------------------------------------------------------------------------------


def test_custom_egress_cidrs_are_listed_exactly_and_not_the_default() -> None:
    spec = _gateway_policy(_render(*_with_custom_cidrs()))["spec"]
    assert len(spec["egress"]) == 1
    rule = spec["egress"][0]
    assert rule["ports"] == [{"protocol": "TCP", "port": 443}]
    assert [peer["ipBlock"]["cidr"] for peer in rule["to"]] == list(CUSTOM_CIDRS)
    assert "0.0.0.0/0" not in yaml.safe_dump(spec)


def test_empty_egress_cidrs_with_a_secret_fails_instead_of_allowing_every_destination(
    tmp_path: Path,
) -> None:
    # An egress rule with ports and no "to" allows EVERY destination on those ports, so an empty
    # list must stop the render rather than silently widen the policy.
    values_file = tmp_path / "empty-cidrs.yaml"
    values_file.write_text("embeddingGateway:\n  egressCidrs: []\n", encoding="utf-8")
    result = _run(*_with_secret(), "-f", str(values_file))
    assert result.returncode != 0
    assert "egressCidrs must list at least one CIDR" in result.stderr


# --- (4) the first two documents are untouched -------------------------------------------------------


@pytest.mark.parametrize("suffix", ["", "-ingestion-egress"])
def test_existing_policies_are_identical_between_default_and_configured_renders(suffix: str) -> None:
    name = f"{_base_name()}{suffix}"
    default = _network_policies(_render())[name]
    configured = _network_policies(_render(*_with_secret()))[name]
    custom = _network_policies(_render(*_with_custom_cidrs()))[name]
    assert default == configured
    assert default == custom


def test_base_policy_still_selects_both_serving_and_ingestion_pods() -> None:
    selector = _network_policies(_render(*_with_secret()))[_base_name()]["spec"]["podSelector"]["matchLabels"]
    assert COMPONENT not in selector


def test_ingestion_egress_policy_still_selects_ingestion_pods_only() -> None:
    policy = _network_policies(_render(*_with_secret()))[f"{_base_name()}-ingestion-egress"]
    assert policy["spec"]["podSelector"]["matchLabels"][COMPONENT] == "ingestion"
