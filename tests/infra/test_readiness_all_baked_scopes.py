"""G3/TC-506 (REQ-G3-008): the deeper readiness check passes for every baked pilot scope.

The stricter ``/readyz`` (scope identity, generation shape, composition floors) must not reject a single
real deployment. The pilots are read from ``infra/helm/foss-mcp/values.yaml`` (``ingestion.pilots``), never
hard-coded, and each one is checked against the committed ``infra/demo-manifests`` files in place: no copy,
no network, no container.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.health import (
    READINESS_DOCUMENTATION_FLOOR,
    READINESS_SYMBOL_FLOOR,
    DeploymentGenerationStore,
    readiness_report,
)
from foss_mcp.mcp.routing import Scope

REPO_ROOT = Path(__file__).resolve().parents[2]
VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"

# re-pinned by TC-551: 40 -> 32
MIN_PILOTS = 32
WARNING_FORMS = (
    re.compile(r"^no verified examples$"),
    re.compile(r"^generation mixes \d+ source commits$"),
)


def _pilots() -> list[dict[str, Any]]:
    data: dict[str, Any] = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
    return list(data["ingestion"]["pilots"])


PILOTS = _pilots()


def _scope_dir_name(pilot: dict[str, Any]) -> str:
    return f"{pilot['family']}__{pilot['platform']}__{pilot['sourceKind']}"


def _store_for(pilot: dict[str, Any]) -> DeploymentGenerationStore:
    return DeploymentGenerationStore.for_scope(
        GenerationManifestStore(DEMO_MANIFESTS),
        Scope(family=pilot["family"], platform=pilot["platform"]),
        pilot["sourceKind"],
    )


def test_there_are_at_least_thirty_two_pilots_each_with_a_baked_scope_directory() -> None:
    assert len(PILOTS) >= MIN_PILOTS
    missing = [
        _scope_dir_name(pilot) for pilot in PILOTS if not (DEMO_MANIFESTS / _scope_dir_name(pilot)).is_dir()
    ]
    assert missing == []


@pytest.mark.parametrize("pilot", PILOTS, ids=[f"{pilot['family']}-{pilot['platform']}" for pilot in PILOTS])
def test_every_baked_pilot_scope_is_ready(pilot: dict[str, Any]) -> None:
    report = readiness_report(_store_for(pilot))
    assert report.reasons == ()
    assert report.ready is True
    assert report.composition["symbol"] >= READINESS_SYMBOL_FLOOR
    assert report.composition["doc"] >= READINESS_DOCUMENTATION_FLOOR
    assert report.generation_id is not None
    assert report.generation_id.startswith(f"{pilot['family']}::{pilot['platform']}::{pilot['sourceKind']}::")


def test_warnings_if_any_are_only_the_two_documented_forms() -> None:
    for pilot in PILOTS:
        for warning in readiness_report(_store_for(pilot)).warnings:
            assert any(form.match(warning) for form in WARNING_FORMS), (_scope_dir_name(pilot), warning)
