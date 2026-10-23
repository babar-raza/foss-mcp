"""Serving answers get_product_reference from the product's own packaging manifest (G2/TC-200).

The sidecar is built by the real ``fetch_product_reference.build_sidecar`` and written as JSON, then
read back by the real ``load_manifest_sidecar`` and the real serving path
(``serve_http._serving_product_reference_inputs``) into ``create_server``. All three network
readers ``build_sidecar`` reaches (``fetch_manifest_file``, ``read_repo_document`` and, since
G3/TC-427, ``list_root_entries``) are replaced, with fixture text from
``tests/fixtures/repo_native/``, so every test is offline. The tool itself is the same closure
``create_server`` registers, taken from ``_build_tool_registry``.

G3/TC-577: ``list_root_entries`` went unmocked here for a while after G3/TC-427 added it to
``build_sidecar`` - a test-fixture gap, not a production one: the real MCP serving path
(``get_product_reference`` via ``_serve()``) never calls ``build_sidecar``; it only reads the
sidecar JSON that ingestion already wrote. The gap only bit when something made the environment
genuinely offline (gatectl's dead-proxy enforcement), because ``_write_sidecar`` quietly fell
through to a real ``api.github.com`` call that happened to still succeed, or fail silently enough
not to be noticed, whenever ambient network access is available.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

import pytest

import infra.fetch_product_reference as fetch_product_reference
import infra.fetch_recent_releases as fetch_recent_releases
from foss_mcp.extraction.github_release_reader import Release
from foss_mcp.extraction.repo_native_reader import DocumentNotPresent
from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import DeploymentConfig, resolve_scope
from foss_mcp.mcp.server import _build_tool_registry, create_server, schema_for
from foss_mcp.mcp.tools.get_product_reference import (
    NotAvailable,
    ProductReferenceInputs,
    ReferenceContent,
)

# infra/ is not a package; serve_http is imported from its path, as in tests/infra/test_serve_http.py.
sys.path.insert(0, str(Path(__file__).parents[2] / "infra"))
import serve_http  # noqa: E402

FIXTURES = Path(__file__).parents[1] / "fixtures" / "repo_native"
PDF_NET_CSPROJ = (FIXTURES / "pdf_net.csproj").read_text(encoding="utf-8")
TYPESCRIPT_PACKAGE_JSON = json.dumps({"name": "@aspose/pdf-foss", "engines": {"node": ">=18.0.0"}})
NET_CONFIG = DeploymentConfig(family="pdf", platform="net")


def _write_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    platform: str,
    manifest_text: str,
    family: str = "pdf",
) -> Path:
    """Build the sidecar through the real build_sidecar, with only its network readers faked, and
    write it to the file name serving reads."""
    monkeypatch.setattr(
        fetch_product_reference, "fetch_manifest_file", lambda repository, path, ref=None: manifest_text
    )
    monkeypatch.setattr(
        fetch_product_reference,
        "read_repo_document",
        lambda repository, path, ref=None: DocumentNotPresent(path=path),
    )
    # G3/TC-427 added this third network reader to build_sidecar (one root listing, used to
    # resolve CONTRIBUTING.md/AGENTS.md/README/LICENSE/Directory.Build.props spelling
    # case-insensitively); G3/TC-577: it must be faked too, or this fixture-construction helper
    # reaches the real api.github.com, same as the other two.
    monkeypatch.setattr(fetch_product_reference, "list_root_entries", lambda repository, ref=None: [])
    sidecar = fetch_product_reference.build_sidecar(
        repository="aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET",
        platform=platform,
        manifest_path="pdf_net.csproj",
        contributing_path="CONTRIBUTING.md",
        agent_guidance_path="AGENTS.md",
        ref=None,
    )
    (tmp_path / fetch_product_reference.sidecar_name(family, platform)).write_text(
        json.dumps(sidecar), encoding="utf-8"
    )
    return tmp_path


def _serve(tmp_path: Path, config: DeploymentConfig, manifests_dir: Path) -> dict:
    """The tool registry for the serving-side inputs read from *manifests_dir*. create_server is
    built with the same inputs first, so the registry is the one a real server would expose.

    G2/TC-244: also reads the real recent-releases sidecar through
    ``serve_http._serving_recent_releases`` and passes it to ``create_server`` the same way
    ``build_app`` now does - the one line that was previously missing in production.
    """
    inputs = serve_http._serving_product_reference_inputs(manifests_dir, config)
    releases = serve_http._serving_recent_releases(manifests_dir, config)
    store = GenerationManifestStore(tmp_path / "store")
    assert create_server(config, store, product_reference_inputs=inputs, recent_releases=releases) is not None
    scope = resolve_scope(config, request=None)
    return _build_tool_registry(store, scope, inputs or ProductReferenceInputs(), releases)


def _write_recent_releases_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    platform: str,
    releases: tuple[Release, ...],
    family: str = "pdf",
) -> Path:
    """Build the sidecar through the real ``fetch_recent_releases.main()``, with only
    ``fetch_releases`` faked, and write it to the file name serving reads - mirroring
    ``_write_sidecar`` above exactly, for the recent-releases sidecar instead."""
    monkeypatch.setattr(fetch_recent_releases, "fetch_releases", lambda repository, **kwargs: list(releases))
    output_path = tmp_path / fetch_recent_releases.recent_releases_sidecar_name(family, platform)
    argv = [
        "fetch_recent_releases.py",
        "--repository",
        "aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET",
        "--output",
        str(output_path),
    ]
    monkeypatch.setattr(sys, "argv", argv)
    fetch_recent_releases.main()
    return tmp_path


def test_serving_with_a_recent_releases_sidecar_reaches_list_recent_changes_with_real_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The live-content smoke test AGENTS.md's "Integration and liveness" section requires: real,
    # non-empty content actually reaching the tool, not merely a well-formed empty response.
    releases = (
        Release(tag_name="v2.1.0", body="Fixed a real bug in the parser", richness="detailed"),
        Release(tag_name="v2.0.0", body="", richness="none"),
    )
    manifests = _write_recent_releases_sidecar(tmp_path, monkeypatch, platform="net", releases=releases)

    registry = _serve(tmp_path, NET_CONFIG, manifests)
    answer = registry["list_recent_changes"]()

    assert len(answer) == 2
    assert answer[0].tag_name == "v2.1.0"
    assert answer[0].richness == "detailed"


def test_serving_with_no_recent_releases_sidecar_answers_an_empty_tuple_and_does_not_raise(
    tmp_path: Path,
) -> None:
    empty_manifests = tmp_path / "no-sidecar"
    empty_manifests.mkdir()

    registry = _serve(tmp_path, NET_CONFIG, empty_manifests)
    answer = registry["list_recent_changes"]()

    assert answer == []


def test_serving_with_the_pdf_java_identity_never_reads_the_pdf_net_recent_releases_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    net_releases = (Release(tag_name="v9.0.0", body="net-only release", richness="none"),)
    _write_recent_releases_sidecar(tmp_path, monkeypatch, platform="net", releases=net_releases)

    java = DeploymentConfig(family="pdf", platform="java")
    assert serve_http._serving_recent_releases(tmp_path, java) == ()
    net = DeploymentConfig(family="pdf", platform="net")
    assert serve_http._serving_recent_releases(tmp_path, net) == net_releases


def test_serving_with_a_sidecar_answers_the_exact_target_framework(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifests = _write_sidecar(tmp_path, monkeypatch, platform="net", manifest_text=PDF_NET_CSPROJ)

    registry = _serve(tmp_path, NET_CONFIG, manifests)
    answer = registry["get_product_reference"](section="compatibility")

    assert isinstance(answer, ReferenceContent), answer
    assert answer.text == "net8.0"


def test_serving_with_no_sidecar_answers_not_available_with_its_reason_and_does_not_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty_manifests = tmp_path / "no-sidecar"
    empty_manifests.mkdir()

    registry = _serve(tmp_path, NET_CONFIG, empty_manifests)
    answer = registry["get_product_reference"](section="compatibility")

    assert isinstance(answer, NotAvailable), answer
    assert answer.section == "compatibility"
    assert answer.reason == "no packaging manifest was provided"


def test_a_typescript_sidecar_reaches_the_engines_node_branch_not_the_dotnet_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifests = _write_sidecar(
        tmp_path, monkeypatch, platform="typescript", manifest_text=TYPESCRIPT_PACKAGE_JSON
    )
    ts_config = DeploymentConfig(family="pdf", platform="typescript")

    registry = _serve(tmp_path, ts_config, manifests)
    answer = registry["get_product_reference"](section="compatibility")

    # engines.node is the JS branch's answer; the .NET branch would have raised parsing package.json
    # as XML, or answered with a TargetFramework.
    assert isinstance(answer, ReferenceContent), answer
    assert answer.text == ">=18.0.0"


def test_the_tool_input_schema_exposes_only_section(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifests = _write_sidecar(tmp_path, monkeypatch, platform="net", manifest_text=PDF_NET_CSPROJ)

    registry = _serve(tmp_path, NET_CONFIG, manifests)
    schema = schema_for(registry["get_product_reference"])

    assert set(schema["properties"]) == {"section"}
    assert schema.get("required", []) == ["section"]


def test_a_present_sidecar_with_the_platform_unset_fails_at_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_sidecar(tmp_path, monkeypatch, platform="net", manifest_text=PDF_NET_CSPROJ)
    monkeypatch.setenv(serve_http.FAMILY_ENV, "pdf")
    monkeypatch.delenv(serve_http.PLATFORM_ENV, raising=False)

    with pytest.raises(RuntimeError, match="FOSS_MCP_PLATFORM"):
        serve_http.deployment_config_from_env()


def test_the_compatibility_answer_is_produced_with_zero_network_access_attempted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """G3/TC-577 regression: gatectl's offline-enforcement environment (HTTP_PROXY/HTTPS_PROXY
    pointed at a dead port, GATECTL_OFFLINE=1) turned this exact test file's own
    ``_write_sidecar`` helper into 4 failures with ``urllib.error.URLError`` - it quietly relied
    on a real, unmocked ``api.github.com`` call succeeding, reached from ``build_sidecar``'s
    G3/TC-427-added ``list_root_entries`` call, which this file's other two monkeypatches never
    covered. ``_write_sidecar`` now fakes all three network readers (see its own comment), so this
    asserts that directly: with ``urllib.request.urlopen`` itself replaced by a call counter that
    raises if invoked, building the sidecar fixture AND serving the real
    ``get_product_reference(section="compatibility")`` tool-registry entry through it make zero
    network attempts - not merely that the test happens to pass in an environment where a real
    network call would have succeeded.
    """
    call_count = 0

    def _forbidden_urlopen(*args: object, **kwargs: object) -> None:
        nonlocal call_count
        call_count += 1
        raise AssertionError("urllib.request.urlopen must not be called while serving from a baked sidecar")

    monkeypatch.setattr(urllib.request, "urlopen", _forbidden_urlopen)

    manifests = _write_sidecar(tmp_path, monkeypatch, platform="net", manifest_text=PDF_NET_CSPROJ)

    registry = _serve(tmp_path, NET_CONFIG, manifests)
    answer = registry["get_product_reference"](section="compatibility")

    assert isinstance(answer, ReferenceContent), answer
    assert answer.text == "net8.0"
    assert call_count == 0
