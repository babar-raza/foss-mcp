"""TC-195: every pilot in the chart matches its docker-compose.yml ingestion service.

Offline. Reads infra/helm/foss-mcp/values.yaml and docker-compose.yml as text and parses them. For
each pilot it finds the ingest-<family>-<platform> service and compares the arguments the chart
passes to build_chunks.py and ingest.py with the arguments compose passes. No helm, no docker, no
network.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"
COMPOSE = REPO_ROOT / "docker-compose.yml"

# TC-205: fourteen pilots, two per platform across seven platforms, from different teams.
# TC-226: six more self-extracted pilots, growing the set from fourteen to twenty.
# TC-231: eight more self-extracted pilots, growing the set from twenty to twenty-eight.
# TC-235: seven more self-extracted pilots, growing the set from twenty-eight to thirty-five.
# TC-236: the last five self-extracted pilots, growing the set from thirty-five to forty - every
# combo of the real forty-combo portfolio is now a live pilot.
FORTY_PILOTS = {
    ("pdf", "net"),
    ("slides", "python"),
    ("pdf", "typescript"),
    ("pdf", "go"),
    ("pdf", "java"),
    ("cells", "rust"),
    ("pdf", "cpp"),
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
}

# build_chunks.py flag for each library field the chart template can pass, and the key it has in
# values.yaml's pilot library block.
LIBRARY_FLAGS = {
    "--library-repository": "repository",
    "--library-commit": "commit",
    "--library-platform": "platform",
    "--library-csproj": "csproj",
    "--library-package-name": "packageName",
    "--library-module-path": "modulePath",
    "--library-crate-name": "crateName",
}

# Self-extracted pilots whose compose services pass no library flags. Their library block is checked
# against the fixture's source_repository and source_commit instead of against compose flags.
FIXTURE_CHECKED_PILOTS = {
    ("pdf", "cpp"),
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
}


def _pilots() -> list[dict[str, Any]]:
    values = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
    return values["ingestion"]["pilots"]


def _pilot(family: str, platform: str) -> dict[str, Any]:
    matches = [p for p in _pilots() if p["family"] == family and p["platform"] == platform]
    assert len(matches) == 1, f"expected exactly one {family}/{platform} pilot, got {len(matches)}"
    return matches[0]


def _compose_service(family: str, platform: str) -> dict[str, Any]:
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    return services[f"ingest-{family}-{platform}"]


def _flags(tokens: list[str]) -> dict[str, str]:
    flags: dict[str, str] = {}
    index = 0
    while index < len(tokens):
        if tokens[index].startswith("--"):
            flags[tokens[index]] = tokens[index + 1]
            index += 2
        else:
            index += 1
    return flags


def _compose_arguments(family: str, platform: str) -> tuple[dict[str, str], dict[str, str]]:
    """The build_chunks.py and ingest.py flags of one ingest service, split on its && chain."""
    tokens = shlex.split(_compose_service(family, platform)["command"][0])
    assert "&&" in tokens, f"ingest-{family}-{platform} is not the two-step chain"
    split_at = tokens.index("&&")
    build, ingest = tokens[:split_at], tokens[split_at + 1 :]
    assert build[:2] == ["python", "/app/infra/build_chunks.py"], build[:2]
    assert ingest[:2] == ["python", "/app/infra/ingest.py"], ingest[:2]
    return _flags(build), _flags(ingest)


def _assert_pilot_matches_compose(family: str, platform: str) -> None:
    pilot = _pilot(family, platform)
    build, ingest = _compose_arguments(family, platform)

    assert ingest["--family"] == family, family
    assert ingest["--platform"] == platform, platform
    assert pilot["family"] == family and pilot["platform"] == platform

    # A self-extracted pilot has an empty library block and no --library-commit flag: both None.
    # The FIXTURE_CHECKED_PILOTS pilots have compose services that define no library flags, so their
    # library is checked against their fixture's source_repository and source_commit instead.
    if (family, platform) in FIXTURE_CHECKED_PILOTS:
        fixture = json.loads(
            (REPO_ROOT / "tests" / "fixtures" / f"{family}_{platform}" / "api_surface.json").read_text(
                encoding="utf-8"
            )
        )
        assert pilot["library"]["repository"] == fixture["source_repository"], "library repository"
        assert pilot["library"]["commit"] == fixture["source_commit"], "library commit"
    else:
        assert pilot["library"].get("commit") == build.get("--library-commit"), "library commit"
    assert pilot["apiSurface"] == build["--api-surface"], "apiSurface (build_chunks)"
    assert pilot["apiSurface"] == ingest["--api-surface"], "apiSurface (ingest)"
    # A self-extracted pilot has no furnished page: the key is absent in values.yaml and the flag
    # is absent in compose, so both sides are None and still compare equal.
    assert pilot.get("furnishedPage") == build.get("--furnished-page"), "furnished page"
    assert pilot["maxTypes"] == int(build["--max-types"]), "maxTypes"

    assert pilot["title"] == build["--title"], "title"
    assert pilot["sourceKind"] == ingest["--source-kind"], "sourceKind"
    assert pilot["heldBy"] == ingest["--held-by"], "heldBy"

    if (family, platform) not in FIXTURE_CHECKED_PILOTS:
        compose_library = {key: build[flag] for flag, key in LIBRARY_FLAGS.items() if flag in build}
        assert pilot["library"] == compose_library, "library block"


def test_pdf_net_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("pdf", "net")


def test_slides_python_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("slides", "python")


def test_pdf_typescript_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("pdf", "typescript")


def test_pdf_go_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("pdf", "go")


def test_pdf_java_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("pdf", "java")


def test_cells_rust_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("cells", "rust")


def test_pdf_cpp_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("pdf", "cpp")


def test_words_python_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("words", "python")


def test_words_net_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("words", "net")


def test_slides_java_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("slides", "java")


def test_cells_go_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("cells", "go")


def test_cells_typescript_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("cells", "typescript")


def test_jmap_rust_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("jmap", "rust")


def test_cells_cpp_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("cells", "cpp")


def test_cells_net_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("cells", "net")


def test_cells_python_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("cells", "python")


def test_jmap_python_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("jmap", "python")


def test_pdf_python_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("pdf", "python")


def test_slides_cpp_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("slides", "cpp")


def test_slides_net_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("slides", "net")


def test_email_python_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("email", "python")


def test_email_cpp_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("email", "cpp")


def test_email_net_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("email", "net")


def test_barcode_python_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("barcode", "python")


def test_note_python_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("note", "python")


def test_html_python_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("html", "python")


def test_page_python_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("page", "python")


def test_imaging_net_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("imaging", "net")


def test_cells_java_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("cells", "java")


def test_jmap_typescript_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("jmap", "typescript")


def test_jmap_go_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("jmap", "go")


def test_jmap_net_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("jmap", "net")


def test_jmap_cpp_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("jmap", "cpp")


def test_jmap_java_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("jmap", "java")


def test_jmap_nodejs_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("jmap", "nodejs")


def test_3d_python_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("3d", "python")


def test_3d_typescript_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("3d", "typescript")


def test_3d_net_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("3d", "net")


def test_3d_java_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("3d", "java")


def test_tex_python_pilot_matches_compose() -> None:
    _assert_pilot_matches_compose("tex", "python")


def _serving_name(family: str, platform: str) -> str:
    # The pdf/net serving service predates the naming pattern and is called plain "serving".
    return "serving" if (family, platform) == ("pdf", "net") else f"serving-{family}-{platform}"


def test_every_pilot_has_a_serving_service_with_its_identity() -> None:
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    for family, platform in sorted(FORTY_PILOTS):
        serving = services[_serving_name(family, platform)]
        assert serving["environment"]["FOSS_MCP_FAMILY"] == family, (family, platform)
        assert serving["environment"]["FOSS_MCP_PLATFORM"] == platform, (family, platform)
        assert serving["environment"]["FOSS_MCP_SOURCE_KIND"] == _pilot(family, platform)["sourceKind"]
    host_ports = [
        services[_serving_name(family, platform)]["ports"][0].split(":")[0]
        for family, platform in sorted(FORTY_PILOTS)
    ]
    assert len(set(host_ports)) == len(host_ports), host_ports


def test_chart_has_exactly_six_pilots_one_per_platform() -> None:
    keys = [(p["family"], p["platform"]) for p in _pilots()]
    assert len(keys) == 40, keys
    assert len(set(keys)) == len(keys), f"duplicate pilot platform: {keys}"
    assert set(keys) == FORTY_PILOTS, keys
