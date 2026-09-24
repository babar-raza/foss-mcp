"""Tests for docker-compose.yml's multi-instance service definitions (G2/TC-023).

docker-compose.yml must define one independently-runnable service per G2 pilot, each
publishing a distinct host port mapped to the container's fixed port 8080 (never
overridable - see infra/serve_http.py's DEFAULT_PORT), with the deployment-identity env
vars (FOSS_MCP_FAMILY, FOSS_MCP_PLATFORM, FOSS_MCP_SOURCE_KIND) set correctly per pilot.
These are parsed straight out of the compose file with yaml.safe_load (which resolves
YAML anchors/aliases automatically), never re-derived or assumed.
"""

from __future__ import annotations

import pathlib

import yaml

COMPOSE_PATH = pathlib.Path(__file__).resolve().parents[2] / "docker-compose.yml"

# The 7 pilots and their exact (family, platform) pairs, pinned by the taskcard.
# pdf/net keeps host port 8080 (pre-existing); the other 6 get 8081-8086 in this order.
EXPECTED_PAIRS = [
    ("pdf", "net"),
    ("pdf", "typescript"),
    ("pdf", "cpp"),
    ("pdf", "java"),
    ("pdf", "go"),
    ("slides", "python"),
    ("cells", "rust"),
]

EXPECTED_HOST_PORTS = {8080, 8081, 8082, 8083, 8084, 8085, 8086}


def _load_services() -> dict:
    with COMPOSE_PATH.open("r", encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    assert "services" in doc, "docker-compose.yml has no top-level 'services' key"
    return doc["services"]


def _port_mapping(service: dict) -> tuple[str, str]:
    ports = service["ports"]
    assert len(ports) == 1, f"expected exactly one port mapping, got {ports!r}"
    host, container = str(ports[0]).split(":")
    return host, container


def test_exactly_seven_services():
    services = _load_services()
    assert len(services) == 7, (
        f"expected exactly 7 services (one per G2 pilot), found {len(services)}: "
        f"{sorted(services)}"
    )


def test_family_platform_pairs_match_pinned_list_exactly():
    services = _load_services()
    found_pairs = []
    for name, svc in services.items():
        env = svc["environment"]
        found_pairs.append((env["FOSS_MCP_FAMILY"], env["FOSS_MCP_PLATFORM"]))
    assert sorted(found_pairs) == sorted(EXPECTED_PAIRS), (
        f"service family/platform pairs {sorted(found_pairs)} do not match the pinned "
        f"7-pilot list {sorted(EXPECTED_PAIRS)}"
    )


def test_all_host_ports_are_distinct():
    services = _load_services()
    host_ports = [int(_port_mapping(svc)[0]) for svc in services.values()]
    assert len(host_ports) == 7
    assert len(set(host_ports)) == 7, f"host ports are not all distinct: {host_ports}"


def test_host_ports_are_exactly_8080_through_8086():
    services = _load_services()
    host_ports = {int(_port_mapping(svc)[0]) for svc in services.values()}
    assert host_ports == EXPECTED_HOST_PORTS, (
        f"expected host ports {sorted(EXPECTED_HOST_PORTS)}, got {sorted(host_ports)}"
    )


def test_every_service_maps_to_container_port_8080():
    services = _load_services()
    for name, svc in services.items():
        _, container = _port_mapping(svc)
        assert container == "8080", (
            f"{name}: container port must stay 8080 (infra/serve_http.py's fixed "
            f"internal port), got {container!r}"
        )


def test_source_kind_is_self_extracted_for_all_seven():
    services = _load_services()
    for name, svc in services.items():
        assert svc["environment"]["FOSS_MCP_SOURCE_KIND"] == "self_extracted", (
            f"{name}: FOSS_MCP_SOURCE_KIND must be self_extracted for every pilot"
        )


def test_pdf_net_service_unchanged_on_port_8080():
    services = _load_services()
    assert "serving" in services, "the original pdf/net service ('serving') must remain"
    svc = services["serving"]
    host, container = _port_mapping(svc)
    assert host == "8080"
    assert container == "8080"
    env = svc["environment"]
    assert env["FOSS_MCP_FAMILY"] == "pdf"
    assert env["FOSS_MCP_PLATFORM"] == "net"
    assert env["FOSS_MCP_SOURCE_KIND"] == "self_extracted"
