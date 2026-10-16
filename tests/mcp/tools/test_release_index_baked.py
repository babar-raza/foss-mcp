"""G3/TC-519 on the BAKED data: the committed demo generations and sidecars under infra/demo-manifests.

Offline and data-driven. For each pilot the scope directory is copied, the product-reference sidecar
is loaded with ``load_manifest_sidecar`` and the releases are rebuilt from the releases sidecar JSON.
The expectations were measured on the committed sidecars with the rule in ``release_alignment``.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from foss_mcp.extraction.github_release_reader import Release
from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp import server as server_module
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_product_reference import ProductReferenceInputs
from foss_mcp.mcp.tools.release_alignment import align_releases, package_version
from foss_mcp.mcp.tools.report_index_freshness import (
    FreshnessReport,
    read_indexed_commits,
    report_index_freshness,
)

# infra/ is not a package - import its module the way tests/infra/test_serve_http.py does.
sys.path.insert(0, str(Path(__file__).parents[3] / "infra"))
from fetch_product_reference import load_manifest_sidecar  # noqa: E402

DEMO_MANIFESTS = Path(__file__).resolve().parents[3] / "infra" / "demo-manifests"

PILOTS = sorted(
    tuple(path.name.split("__")[:2])
    for path in DEMO_MANIFESTS.iterdir()
    if path.name.endswith("__self_extracted")
)


def _inputs(family: str, platform: str) -> ProductReferenceInputs:
    inputs = load_manifest_sidecar(DEMO_MANIFESTS / f"product_reference_{family}_{platform}.json")
    assert inputs is not None
    return inputs


def _releases(family: str, platform: str) -> list[Release]:
    data = json.loads(
        (DEMO_MANIFESTS / f"recent_releases_{family}_{platform}.json").read_text(encoding="utf-8")
    )
    return [
        Release(
            entry["tag_name"], entry["body"], entry["richness"], entry.get("published_at"), entry.get("url")
        )
        for entry in data
    ]


def _store(tmp_path: Path, family: str, platform: str) -> GenerationManifestStore:
    directory = f"{family}__{platform}__self_extracted"
    manifests = tmp_path / "manifests"
    shutil.copytree(DEMO_MANIFESTS / directory, manifests / directory)
    return GenerationManifestStore(manifests)


def _aligned(
    store: GenerationManifestStore, family: str, platform: str
) -> dict[str, tuple[bool | None, str]]:
    return align_releases(
        _releases(family, platform),
        indexed_commits=read_indexed_commits(store, Scope(family=family, platform=platform)),
        indexed_version=package_version(_inputs(family, platform)),
    )


def _freshness(store: GenerationManifestStore, family: str, platform: str) -> FreshnessReport:
    return report_index_freshness(
        store,
        Scope(family=family, platform=platform),
        "self_extracted",
        None,
        pinned_library_commit=_inputs(family, platform).source_ref,
        product_reference=_inputs(family, platform),
        releases=_releases(family, platform),
    )


def test_cells_python_release_26_10_0_is_not_in_the_index_and_26_7_0_is(tmp_path: Path) -> None:
    aligned = _aligned(_store(tmp_path, "cells", "python"), "cells", "python")

    assert aligned["V26.10.0"] == (False, "version_newer")
    assert aligned["V26.7.0"] == (True, "version_not_newer")
    assert aligned["V26.3.1"] == (True, "version_not_newer")


def test_pdf_net_release_26_10_0_is_not_in_the_index(tmp_path: Path) -> None:
    aligned = _aligned(_store(tmp_path, "pdf", "net"), "pdf", "net")

    assert aligned["v26.10.0"] == (False, "version_newer")
    assert aligned["v26.9.0"] == (True, "version_not_newer")
    assert aligned["v26.8.0"] == (True, "version_not_newer")


def test_cells_net_and_pdf_cpp_latest_releases_are_in_the_index(tmp_path: Path) -> None:
    cells_net = _aligned(_store(tmp_path / "a", "cells", "net"), "cells", "net")
    pdf_cpp = _aligned(_store(tmp_path / "b", "pdf", "cpp"), "pdf", "cpp")

    assert cells_net["V26.9.0"] == (True, "version_not_newer")
    assert pdf_cpp["v1.0.1"] == (True, "version_not_newer")


def test_jmap_pilots_and_pdf_go_are_unknown(tmp_path: Path) -> None:
    jmap = [pilot for pilot in PILOTS if pilot[0] == "jmap"]
    assert len(jmap) == 8
    go_pilots = [pilot for pilot in PILOTS if pilot[1] == "go"]
    assert ("pdf", "go") in go_pilots
    for index, (family, platform) in enumerate([*jmap, *go_pilots]):
        releases = _releases(family, platform)
        aligned = _aligned(_store(tmp_path / str(index), family, platform), family, platform)
        assert aligned == {release.tag_name: (None, "unknown") for release in releases}, (family, platform)


@pytest.mark.parametrize(("family", "platform"), PILOTS)
def test_every_baked_pilot_aligns_without_raising(tmp_path: Path, family: str, platform: str) -> None:
    store = _store(tmp_path, family, platform)

    aligned = _aligned(store, family, platform)
    report = _freshness(store, family, platform)

    assert set(aligned) == {release.tag_name for release in _releases(family, platform)}
    for value, basis in aligned.values():
        assert value in (True, False, None)
        assert basis in {"commit_equal", "version_not_newer", "version_newer", "unknown"}
    assert report.freshness_state in {"fresh", "stale", "unknown"}
    assert report.stale is (report.freshness_state != "fresh")
    assert report.indexed_at is not None


def test_there_are_at_least_40_baked_pilots() -> None:
    assert len(PILOTS) >= 40


@pytest.mark.parametrize(
    ("family", "platform", "tag", "indexed_version"),
    [("cells", "python", "V26.10.0", "26.7.0"), ("pdf", "net", "v26.10.0", "26.9.0")],
)
def test_freshness_of_cells_python_and_pdf_net_without_a_commit_is_stale_by_version(
    tmp_path: Path, family: str, platform: str, tag: str, indexed_version: str
) -> None:
    report = _freshness(_store(tmp_path, family, platform), family, platform)

    assert report.freshness_state == "stale"
    assert report.freshness_basis == "latest_release_version"
    assert report.stale is True
    assert report.latest_release_tag == tag
    assert report.latest_release_in_index is False
    assert report.reason.startswith(
        f"latest release {tag} is newer than the indexed package version {indexed_version}; "
        "no current_source_commit was given"
    )
    assert report.source_repository


def test_freshness_of_cells_net_without_a_commit_is_unknown(tmp_path: Path) -> None:
    report = _freshness(_store(tmp_path, "cells", "net"), "cells", "net")

    assert report.freshness_state == "unknown"
    assert report.freshness_basis == "none"
    assert report.stale is True
    assert report.latest_release_tag == "V26.9.0"
    assert report.latest_release_in_index is True
    assert "current source commit is unknown; staleness cannot be ruled out" in report.reason


def test_the_production_registry_serves_in_index_and_the_freshness_state(tmp_path: Path) -> None:
    store = _store(tmp_path, "cells", "python")
    registry = server_module._build_tool_registry(
        store,
        Scope(family="cells", platform="python"),
        _inputs("cells", "python"),
        _releases("cells", "python"),
    )

    entries = registry["list_recent_changes"](limit=20)
    report = registry["report_index_freshness"]()

    by_tag = {entry.tag_name: entry for entry in entries}
    assert by_tag["V26.10.0"].in_index is False
    assert by_tag["V26.7.0"].in_index is True
    assert by_tag["V26.7.0"].in_index_basis == "version_not_newer"
    assert report.freshness_state == "stale"
    assert report.freshness_basis == "latest_release_version"


def test_the_call_sites_exist_in_the_server_entrypoint() -> None:
    source = (Path(__file__).resolve().parents[3] / "src" / "foss_mcp" / "mcp" / "server.py").read_text(
        encoding="utf-8"
    )
    assert "alignment=alignment" in source
    assert "align_releases(" in source
    assert "product_reference=inputs" in source
    assert "releases=releases" in source
