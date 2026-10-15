"""G3/TC-507: the identity rules must never degrade a real, baked pilot.

The serving side now refuses a sidecar that is not this deployment's: a platform, a family, a source
repository or a release url that does not belong. Those rules are only safe if every sidecar actually
shipped in ``infra/demo-manifests`` passes them. This test reads the pilot list from the Helm values (never
a hard-coded list) and proves each pilot's product-reference and releases sidecars load as ``present`` and
its registry sidecar merges without a warning.

Offline, data-driven, no copy: it reads the committed files in place.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from foss_mcp.mcp.routing import DeploymentConfig
from foss_mcp.mcp.sidecar_state import SIDECAR_PRESENT

REPO_ROOT = Path(__file__).parents[2]
INFRA_DIR = REPO_ROOT / "infra"
sys.path.insert(0, str(INFRA_DIR))
import serve_http  # noqa: E402

DEMO_MANIFESTS = INFRA_DIR / "demo-manifests"
VALUES = INFRA_DIR / "helm" / "foss-mcp" / "values.yaml"


def _pilots() -> list[dict[str, Any]]:
    values = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
    return list(values["ingestion"]["pilots"])


PILOTS = _pilots()
PILOT_IDS = [f"{pilot['family']}-{pilot['platform']}" for pilot in PILOTS]


def test_the_pilot_list_is_read_from_the_chart_and_is_not_trivially_small() -> None:
    assert len(PILOTS) >= 40
    assert len(set(PILOT_IDS)) == len(PILOT_IDS)


@pytest.mark.parametrize("pilot", PILOTS, ids=PILOT_IDS)
def test_every_baked_pilot_loads_as_present(pilot: dict[str, Any], caplog: pytest.LogCaptureFixture) -> None:
    config = DeploymentConfig(family=pilot["family"], platform=pilot["platform"])
    with caplog.at_level(logging.WARNING):
        inputs = serve_http._serving_product_reference_inputs(DEMO_MANIFESTS, config)
        assert inputs is not None, "the baked product-reference sidecar is missing"
        assert inputs.sidecar_state == SIDECAR_PRESENT
        assert inputs.platform == pilot["platform"]
        releases, state = serve_http._resolve_recent_releases(
            DEMO_MANIFESTS, config, source_repository=inputs.source_repository
        )
    assert state == SIDECAR_PRESENT, f"{len(releases)} releases"
    # The registry sidecar, where the file exists, merged with no WARNING (an unverified pilot has none).
    warnings = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert not warnings, [record.getMessage() for record in warnings]
    registry_file = DEMO_MANIFESTS / serve_http.verify_package_registry_sidecar_name(
        pilot["family"], pilot["platform"]
    )
    if registry_file.exists():
        assert inputs.install_verified is not None
        assert inputs.install_verified_coordinate
    else:
        assert inputs.install_verified is None


def test_the_family_is_part_of_the_source_repository_for_every_pilot_and_not_for_a_wrong_family() -> None:
    for pilot in PILOTS:
        config = DeploymentConfig(family=pilot["family"], platform=pilot["platform"])
        inputs = serve_http._serving_product_reference_inputs(DEMO_MANIFESTS, config)
        assert inputs is not None
        assert inputs.source_repository, PILOT_IDS
        assert serve_http._family_in_repository(pilot["family"], inputs.source_repository), pilot
        assert not serve_http._family_in_repository("notafamily", inputs.source_repository), pilot
