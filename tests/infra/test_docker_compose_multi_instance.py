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

# The 40 pilots and their exact (family, platform) pairs, pinned by the taskcard (TC-205 grew the
# original 7 to 14; TC-226 grew 14 to 20; TC-231 grew 20 to 28; TC-235 grew 28 to 35; TC-236 grew
# 35 to 40).
# pdf/net keeps host port 8080 (pre-existing); the other 39 get 8081-8119 in this order.
EXPECTED_PAIRS = [
    ("pdf", "net"),
    ("pdf", "typescript"),
    ("pdf", "cpp"),
    ("pdf", "java"),
    ("pdf", "go"),
    ("slides", "python"),
    ("cells", "rust"),
    ("words", "python"),
    ("words", "net"),
    ("slides", "java"),
    ("cells", "go"),
    ("cells", "typescript"),
    ("jmap", "rust"),
    ("cells", "cpp"),
    ("cells", "net"),
    ("cells", "python"),
    ("jmap", "python"),
    ("pdf", "python"),
    ("slides", "cpp"),
    ("slides", "net"),
    ("email", "python"),
    ("email", "cpp"),
    ("email", "net"),
    ("barcode", "python"),
    ("note", "python"),
    ("html", "python"),
    ("page", "python"),
    ("imaging", "net"),
    ("cells", "java"),
    ("jmap", "typescript"),
    ("jmap", "go"),
    ("jmap", "net"),
    ("jmap", "cpp"),
    ("jmap", "java"),
    ("jmap", "nodejs"),
    ("3d", "python"),
    ("3d", "typescript"),
    ("3d", "net"),
    ("3d", "java"),
    ("tex", "python"),
]

EXPECTED_HOST_PORTS = {
    8080,
    8081,
    8082,
    8083,
    8084,
    8085,
    8086,
    8087,
    8088,
    8089,
    8090,
    8091,
    8092,
    8093,
    8094,
    8095,
    8096,
    8097,
    8098,
    8099,
    8100,
    8101,
    8102,
    8103,
    8104,
    8105,
    8106,
    8107,
    8108,
    8109,
    8110,
    8111,
    8112,
    8113,
    8114,
    8115,
    8116,
    8117,
    8118,
    8119,
}


# A service with no "ports" key is a one-shot build/ingestion job (e.g. ingest-pdf-net),
# not a per-pilot serving instance, so it is excluded here rather than counted below.
def _load_services() -> dict:
    with COMPOSE_PATH.open("r", encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    assert "services" in doc, "docker-compose.yml has no top-level 'services' key"
    return {name: svc for name, svc in doc["services"].items() if "ports" in svc}


def _port_mapping(service: dict) -> tuple[str, str]:
    ports = service["ports"]
    assert len(ports) == 1, f"expected exactly one port mapping, got {ports!r}"
    host, container = str(ports[0]).split(":")
    return host, container


def test_exactly_seven_services():
    services = _load_services()
    assert len(services) == 40, (
        f"expected exactly 40 services (one per G2 pilot), found {len(services)}: {sorted(services)}"
    )


def test_family_platform_pairs_match_pinned_list_exactly():
    services = _load_services()
    found_pairs = []
    for _name, svc in services.items():
        env = svc["environment"]
        found_pairs.append((env["FOSS_MCP_FAMILY"], env["FOSS_MCP_PLATFORM"]))
    assert sorted(found_pairs) == sorted(EXPECTED_PAIRS), (
        f"service family/platform pairs {sorted(found_pairs)} do not match the pinned "
        f"40-pilot list {sorted(EXPECTED_PAIRS)}"
    )


def test_all_host_ports_are_distinct():
    services = _load_services()
    host_ports = [int(_port_mapping(svc)[0]) for svc in services.values()]
    assert len(host_ports) == 40
    assert len(set(host_ports)) == 40, f"host ports are not all distinct: {host_ports}"


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
