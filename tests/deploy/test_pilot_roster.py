"""G3/TC-551: the shipped roster is the chart's 32 pilots, everywhere (REQ-G3-001, REQ-G3-002, REQ-G3-005, REQ-G3-007, REQ-G3-008).

The operator removed one whole product family from the product on 2026-10-16: its eight upstream repositories no longer
exist, so none of its pilots can be extracted, ingested or verified any more (OWNER-15). The chart's ``ingestion.pilots``
is the one list of shipped pilots; this module proves that every other place that carries a pilot roster (the two compose
files, the baked demo manifests and their sidecars, the verification caches, the fixtures, the product manifests, the
ingestion image's COPY lines, the deploy tools and the three documents a peer reads) agrees with it, and that nothing of the
removed family is left in any of them. Offline and deterministic: it reads committed files only.

The removed family's name is built from two halves on purpose, so that this file never contains the string that
tests/deploy/test_removed_family_absent.py (TC-552) forbids in every tracked file.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"
COMPOSE = REPO_ROOT / "docker-compose.yml"
DEMO_COMPOSE = REPO_ROOT / "docker-compose.demo.yml"
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
FIXTURES = REPO_ROOT / "tests" / "fixtures"
CONFIG_PRODUCTS = REPO_ROOT / "config" / "products"
DOCKERFILE_INGESTION = REPO_ROOT / "Dockerfile.ingestion"

sys.path.insert(0, str(REPO_ROOT / "scripts" / "deploy"))

import pilots as deploy_pilots  # noqa: E402

REMOVED_FAMILY = "j" + "map"
EXPECTED_PILOTS = 32
EXPECTED_FAMILIES = 12
SIDECAR_PREFIXES = ("product_reference_", "recent_releases_", "package_registry_", "verification_cache_")


def chart_pilots() -> list[dict[str, Any]]:
    data = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
    return [dict(p) for p in data["ingestion"]["pilots"]]


def key_of(pilot: dict[str, Any]) -> str:
    return f"{pilot['family']}_{pilot['platform']}"


def scope_of(pilot: dict[str, Any]) -> str:
    return f"{pilot['family']}__{pilot['platform']}__{pilot['sourceKind']}"


PILOTS = chart_pilots()
KEYS = sorted(key_of(p) for p in PILOTS)
SCOPES = sorted(scope_of(p) for p in PILOTS)


def mentions_removed_family(text: str) -> bool:
    return REMOVED_FAMILY in text.casefold()


def sidecar_key(name: str) -> str | None:
    """The pilot key a root-level sidecar file name carries, or None when it is not a sidecar name."""
    for prefix in SIDECAR_PREFIXES:
        if name.startswith(prefix) and name.endswith(".json"):
            return name[len(prefix) : -len(".json")]
    return None


def stray_sidecars(names: list[str], keys: set[str]) -> list[str]:
    """Root-level files of the demo manifests that belong to no chart pilot."""
    return sorted(n for n in names if sidecar_key(n) not in keys)


# -- the chart --------------------------------------------------------------------------------------------------------


def test_the_chart_lists_exactly_32_pilots_with_unique_keys() -> None:
    assert len(PILOTS) == EXPECTED_PILOTS
    assert len(set(KEYS)) == EXPECTED_PILOTS


def test_the_chart_has_12_families_and_none_is_the_removed_one() -> None:
    families = {p["family"] for p in PILOTS}
    assert len(families) == EXPECTED_FAMILIES
    assert REMOVED_FAMILY not in families


def test_no_chart_pilot_names_the_removed_family_anywhere() -> None:
    text = VALUES.read_text(encoding="utf-8")
    assert not mentions_removed_family(text)
    for pilot in PILOTS:
        assert not mentions_removed_family(yaml.safe_dump(pilot)), key_of(pilot)


def test_the_deploy_tools_count_the_chart() -> None:
    assert (
        deploy_pilots.PILOT_COUNT
        == EXPECTED_PILOTS
        == len(deploy_pilots.load_pilots(deploy_pilots.CHART_VALUES))
    )


# -- the two compose files --------------------------------------------------------------------------------------------


def test_docker_compose_ingest_and_serving_services_are_exactly_the_charts_pilots() -> None:
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    names = {f"{p['family']}-{p['platform']}" for p in PILOTS}
    ingest = {n[len("ingest-") :] for n in services if n.startswith("ingest-")}
    # pdf/net is served by the service named plain "serving" (the original single-pilot service); the other 31 by serving-<f>-<p>.
    serving = {n[len("serving-") :] for n in services if n.startswith("serving-")} | {"pdf-net"}
    assert "serving" in services
    assert ingest == names
    assert serving == names


def test_docker_compose_volumes_are_exactly_the_charts_pilots() -> None:
    volumes = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["volumes"]
    manifest_volumes = {v[len("manifests-") :] for v in volumes if v.startswith("manifests-")}
    assert manifest_volumes == {f"{p['family']}-{p['platform']}" for p in PILOTS}


def test_docker_compose_serving_services_carry_the_charts_identity() -> None:
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    for pilot in PILOTS:
        name = f"serving-{pilot['family']}-{pilot['platform']}"
        env = services["serving" if key_of(pilot) == "pdf_net" else name]["environment"]
        assert (env["FOSS_MCP_FAMILY"], env["FOSS_MCP_PLATFORM"]) == (pilot["family"], pilot["platform"])


def test_the_demo_compose_has_one_service_per_chart_pilot_in_chart_order() -> None:
    services = list(yaml.safe_load(DEMO_COMPOSE.read_text(encoding="utf-8"))["services"])
    assert services == [f"{p['family']}-{p['platform']}" for p in PILOTS]
    assert len(services) == EXPECTED_PILOTS


def test_the_demo_compose_header_states_32_services_and_12_profiles() -> None:
    header = "\n".join(
        line for line in DEMO_COMPOSE.read_text(encoding="utf-8").splitlines() if line.startswith("#")
    )
    assert "32 services" in header
    assert "all 32 pilots" in header
    assert not mentions_removed_family(header)


# -- the baked data ---------------------------------------------------------------------------------------------------


def test_the_demo_manifests_hold_exactly_the_charts_32_scope_directories() -> None:
    found = sorted(p.name for p in DEMO_MANIFESTS.iterdir() if p.is_dir())
    assert found == SCOPES
    assert len(found) == EXPECTED_PILOTS


def test_the_demo_manifests_hold_no_sidecar_of_a_pilot_outside_the_chart() -> None:
    names = [p.name for p in DEMO_MANIFESTS.iterdir() if p.is_file()]
    assert stray_sidecars(names, set(KEYS)) == []
    assert not [n for n in names if mentions_removed_family(n)]


def test_every_root_level_sidecar_file_is_a_known_kind() -> None:
    names = [p.name for p in DEMO_MANIFESTS.iterdir() if p.is_file()]
    assert names, "the demo manifests hold no sidecar at all"
    assert [n for n in names if sidecar_key(n) is None] == []


def test_the_verification_caches_exist_for_exactly_the_charts_32_pilots() -> None:
    found = sorted(p.stem for p in (FIXTURES / "verified_examples").glob("*.json"))
    assert found == KEYS


# -- the fixtures and the product manifests ---------------------------------------------------------------------------


def test_the_api_surface_fixtures_are_exactly_the_charts_32_pilots() -> None:
    found = sorted(p.parent.name for p in FIXTURES.glob("*/api_surface.json") if p.parent.name != "furnished")
    assert found == KEYS


def test_the_furnished_page_directories_belong_to_chart_pilots() -> None:
    found = sorted(p.name for p in (FIXTURES / "furnished").iterdir() if p.is_dir())
    assert [name for name in found if name not in KEYS and name != "sealed_current"] == []


def test_no_fixture_directory_of_the_removed_family_is_left() -> None:
    leftovers = [
        str(p.relative_to(REPO_ROOT)) for p in FIXTURES.rglob("*") if mentions_removed_family(p.name)
    ]
    assert leftovers == []


def test_the_product_manifests_are_exactly_the_charts_32_pilots() -> None:
    found = sorted(f"{p.parent.name}_{p.stem}" for p in CONFIG_PRODUCTS.glob("*/*.yaml"))
    assert found == KEYS
    assert not (CONFIG_PRODUCTS / REMOVED_FAMILY).exists()


def test_the_ingestion_image_copies_only_fixtures_of_chart_pilots() -> None:
    copied = re.findall(
        r"^COPY tests/fixtures/([a-z0-9_]+)/api_surface\.json ",
        DOCKERFILE_INGESTION.read_text(encoding="utf-8"),
        re.M,
    )
    assert copied
    assert sorted(set(copied)) == KEYS


# -- the three documents a peer reads ---------------------------------------------------------------------------------


def _text(*parts: str) -> str:
    return (REPO_ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def test_the_readme_says_32_pilots_and_12_families_and_no_longer_40() -> None:
    readme = " ".join(_text("README.md").split())
    assert "serves 32 pilots" in readme
    assert "across 12 product families" in readme
    assert "all 32" in readme
    assert re.search(r"\b40\b", readme) is None
    assert "forty" not in readme.casefold()


def test_the_release_document_says_32_pilots_and_states_the_removal_exactly_once() -> None:
    release = _text("docs", "RELEASE.md")
    assert "currently 32" in release
    assert "The image serves 32 pilots." in release
    lines = [line for line in release.splitlines() if mentions_removed_family(line)]
    assert len(lines) == 1, lines
    assert "upstream GitHub repositories no longer exist" in lines[0]
    assert re.search(r"\b40\b", release) is None
    assert "forty" not in release.casefold()


def test_the_deployment_runbook_says_thirty_two_and_32_of_32() -> None:
    deployment = _text("docs", "DEPLOYMENT.md")
    assert "thirty-two" in deployment.casefold()
    assert "32-of-32" in deployment
    assert "forty" not in deployment.casefold()
    assert re.search(r"\b40-of-40\b", deployment) is None
    assert not mentions_removed_family(deployment)


def test_the_deploy_tools_say_thirty_two_and_not_forty() -> None:
    for name in ("install_pilots.py", "prove_pilots.py", "pilots.py"):
        text = _text("scripts", "deploy", name)
        assert "forty" not in text.casefold(), name
        assert not mentions_removed_family(text), name


# -- anti-vacuity: the helpers can fail -------------------------------------------------------------------------------


def test_the_sidecar_key_parser_reads_all_four_kinds() -> None:
    assert sidecar_key("product_reference_pdf_net.json") == "pdf_net"
    assert sidecar_key("recent_releases_cells_go.json") == "cells_go"
    assert sidecar_key("package_registry_pdf_java.json") == "pdf_java"
    assert sidecar_key("verification_cache_pdf_go.json") == "pdf_go"
    assert sidecar_key("README.md") is None


def test_the_stray_sidecar_check_flags_a_pilot_that_is_not_in_the_chart() -> None:
    keys = {"pdf_net"}
    assert stray_sidecars(["product_reference_pdf_net.json"], keys) == []
    assert stray_sidecars(["product_reference_" + REMOVED_FAMILY + "_go.json"], keys) == [
        "product_reference_" + REMOVED_FAMILY + "_go.json"
    ]
    assert stray_sidecars(["notes.txt"], keys) == ["notes.txt"]


@pytest.mark.parametrize(
    "text",
    ["a " + REMOVED_FAMILY.title() + " b", REMOVED_FAMILY.upper() + "/rust", "x_" + REMOVED_FAMILY + "_y"],
)
def test_the_removed_family_detector_is_case_insensitive(text: str) -> None:
    assert mentions_removed_family(text)


def test_the_removed_family_detector_does_not_flag_other_families() -> None:
    assert not mentions_removed_family("pdf cells slides")
