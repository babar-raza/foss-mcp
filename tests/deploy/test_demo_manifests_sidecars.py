"""G3/TC-354: the committed infra/demo-manifests/ tree is complete, well-formed, lean and free of credentials.

Offline: reads only the committed tree and the chart's values.yaml. No cluster, no docker, no network.

The standalone demo image bakes this tree in, so it must hold what the validated 32-pilot cluster serves: per
pilot the pointer, ONE generation the pointer names (with real lexical content), and the sidecars the server
reads from the manifests root. A C++ pilot has no package-registry sidecar by design (there is no registry
coordinate to verify, and the cluster never writes one), so that one sidecar is required for every other pilot
and must be absent for a cpp pilot - a stale file there would claim a verification that never ran.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE_DIR = REPO_ROOT / "scripts" / "release"
VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"

sys.path.insert(0, str(RELEASE_DIR))

import compact_demo_manifests as compact  # noqa: E402
import snapshot_demo_from_cluster as snapshot  # noqa: E402

from tests.deploy.test_compact_demo_manifests import (  # noqa: E402
    LEAN_CEILING_BYTES,
    budget_is_within_the_lean_ceiling,
)

# re-pinned by TC-551: 40 -> 32
EXPECTED_PILOTS = 32
# The platform key (as the chart names it) of the pilots that have no package-registry sidecar.
NO_REGISTRY_PLATFORM = "cpp"
# Measured 18,091,213 bytes (17.3 MiB) at TC-354: 40 generations (17,501,743 bytes) plus 115 sidecars
# (586,028 bytes) plus 40 pointers. The bound carries about 25% headroom.
# Re-pinned 2026-10-14 (TC-503, rev-b487453): total 28_069_555 bytes (median generation 577_817, largest 2_944_920), grown by complete
# API extraction plus quantised vectors for verified-example chunks. The bound carries about 5% headroom.
# Re-measured 2026-10-17 on the supervisor data commit 58ba5861 (cluster re-ingest of all 32 pilots on rev-e4a6ada): 41,687,973 bytes over 32 scopes. The tree grew
# because thirteen pilots now hold their WHOLE public surface (pdf/java 1,239 types, pdf/typescript 1,333 types and 3,060 functions, pdf/net 901
# types) instead of a 300-type cap. Measurement x 1.05 rounded up to the next 10,000, the same value as test_compact_demo_manifests.py.
# Re-measured 2026-10-21 (TC-536) on the committed tree after the supervisor's final re-ingest of all 32 pilots (commit
# ad8c2329, image revision rev-9993ea85, Round 3 cards TC-557 to TC-566): total 41_732_902 bytes. The tree grew further
# because Round 3 renders per-member documentation text for every pilot, not only the 13 pilots TC-553 already measured.
# Same rule (measurement x 1.05 rounded up to the next 10,000) and the same value as test_compact_demo_manifests.py.
# re-pinned by TC-536: 43_780_000 -> 43_820_000
TREE_BYTES_BUDGET = 43_820_000

# The same credential shapes ops/secretscan.py refuses in a commit.
CREDENTIAL_SHAPES: dict[str, re.Pattern[str]] = {
    "github_token": re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b"),
    "github_pat": re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "slack_token": re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b"),
}


def chart_pilots() -> list[dict[str, Any]]:
    data = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
    return [dict(p) for p in data["ingestion"]["pilots"]]


def scope_name(pilot: dict[str, Any]) -> str:
    return f"{pilot['family']}__{pilot['platform']}__{pilot['sourceKind']}"


def sidecars(pilot: dict[str, Any]) -> dict[str, str]:
    """label -> file name, from the same helpers the server and the snapshot tool use."""
    return snapshot.sidecar_names(pilot["family"], pilot["platform"])


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def has_registry_sidecar(pilot: dict[str, Any]) -> bool:
    return pilot["platform"] != NO_REGISTRY_PLATFORM


PILOTS = chart_pilots()
PILOT_IDS = [snapshot.pilots.pilot_key(p) for p in PILOTS]


def test_the_chart_names_thirty_two_pilots_and_some_have_no_registry_sidecar() -> None:
    assert len(PILOTS) == EXPECTED_PILOTS
    assert len(set(PILOT_IDS)) == EXPECTED_PILOTS
    # The absence assertions below are only meaningful while at least one pilot is a cpp pilot.
    assert any(not has_registry_sidecar(p) for p in PILOTS)
    assert any(has_registry_sidecar(p) for p in PILOTS)


def test_exactly_the_charts_scope_directories_exist_and_nothing_else_is_a_directory() -> None:
    found = sorted(p.name for p in DEMO_MANIFESTS.iterdir() if p.is_dir())
    assert found == sorted(scope_name(p) for p in PILOTS)
    assert len(found) == EXPECTED_PILOTS


@pytest.mark.parametrize("pilot", PILOTS, ids=PILOT_IDS)
def test_scope_has_its_pointer_one_generation_the_pointer_names_and_real_lexical_content(
    pilot: dict[str, Any],
) -> None:
    scope_dir = DEMO_MANIFESTS / scope_name(pilot)
    generation_id, generation_path = compact.active_generation_path(scope_dir)
    assert sorted(p.name for p in scope_dir.iterdir()) == sorted(
        [compact.ACTIVE_POINTER, generation_path.name]
    )
    generation = read_json(generation_path)
    assert generation["generation_id"] == generation_id
    documents = generation["payload"]["lexical_index"]["documents"]
    assert isinstance(documents, dict) and len(documents) > 0


@pytest.mark.parametrize("pilot", PILOTS, ids=PILOT_IDS)
def test_pilot_has_its_product_reference_and_recent_releases_sidecars_as_valid_json(
    pilot: dict[str, Any],
) -> None:
    names = sidecars(pilot)
    for label in ("product_reference", "recent_releases"):
        path = DEMO_MANIFESTS / names[label]
        assert path.is_file(), names[label]
        # A recent_releases sidecar may legitimately be a two-byte empty document (no releases): only the
        # shape is required, never content.
        read_json(path)


@pytest.mark.parametrize("pilot", PILOTS, ids=PILOT_IDS)
def test_product_reference_names_the_pilot_its_manifest_and_the_pinned_source(pilot: dict[str, Any]) -> None:
    data = read_json(DEMO_MANIFESTS / sidecars(pilot)["product_reference"])
    assert isinstance(data, dict)
    # The PILOT's platform key as the chart names it (net), not the library's own platform value (dotnet).
    assert data["platform"] == pilot["platform"]
    assert isinstance(data["manifest_text"], str)
    if pilot.get("manifestPath"):
        assert data["manifest_text"].strip(), pilot["manifestPath"]
    assert data["source_repository"] == pilot["library"]["repository"]
    assert data["source_ref"] == pilot["library"]["commit"]


@pytest.mark.parametrize("pilot", PILOTS, ids=PILOT_IDS)
def test_package_registry_sidecar_is_required_except_for_cpp_where_it_must_be_absent(
    pilot: dict[str, Any],
) -> None:
    path = DEMO_MANIFESTS / sidecars(pilot)["package_registry"]
    if not has_registry_sidecar(pilot):
        assert not path.exists(), f"{path.name} would claim a verification that never ran"
        return
    assert path.is_file(), path.name
    data = read_json(path)
    assert isinstance(data, dict)
    assert {"verified", "coordinate", "checked_at"} <= set(data), sorted(data)


def test_no_file_outside_the_expected_names_exists_in_the_tree() -> None:
    expected_root = {name for p in PILOTS for label, name in sidecars(p).items() if _expected(p, label)}
    root_files = {p.name for p in DEMO_MANIFESTS.iterdir() if p.is_file()}
    assert root_files == expected_root, (
        sorted(root_files - expected_root),
        sorted(expected_root - root_files),
    )
    for pilot in PILOTS:
        scope_dir = DEMO_MANIFESTS / scope_name(pilot)
        _, generation_path = compact.active_generation_path(scope_dir)
        assert {p.name for p in scope_dir.rglob("*")} == {compact.ACTIVE_POINTER, generation_path.name}


def _expected(pilot: dict[str, Any], label: str) -> bool:
    return label != "package_registry" or has_registry_sidecar(pilot)


def test_no_credential_shaped_value_appears_anywhere_in_the_tree() -> None:
    findings: list[str] = []
    for path in sorted(p for p in DEMO_MANIFESTS.rglob("*") if p.is_file()):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for kind, shape in CREDENTIAL_SHAPES.items():
            if shape.search(text):
                findings.append(f"{path.relative_to(DEMO_MANIFESTS).as_posix()}: {kind}")  # never the value
    assert findings == []


def test_the_whole_tree_is_within_the_size_budget() -> None:
    total = sum(p.stat().st_size for p in DEMO_MANIFESTS.rglob("*") if p.is_file())
    assert total <= TREE_BYTES_BUDGET, (total, TREE_BYTES_BUDGET)


def test_the_tree_budgets_stay_under_the_agreed_lean_ceiling() -> None:
    assert budget_is_within_the_lean_ceiling(TREE_BYTES_BUDGET), (TREE_BYTES_BUDGET, LEAN_CEILING_BYTES)
