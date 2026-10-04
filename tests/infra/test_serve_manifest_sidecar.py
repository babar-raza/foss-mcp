"""Serving answers get_product_reference from the product's own packaging manifest (G2/TC-200).

The sidecar is built by the real ``fetch_product_reference.build_sidecar`` and written as JSON, then
read back by the real ``load_manifest_sidecar`` and the real serving path
(``serve_http._serving_product_reference_inputs``) into ``create_server``. Only the two network
readers are replaced, with fixture text from ``tests/fixtures/repo_native/``, so every test is
offline. The tool itself is the same closure ``create_server`` registers, taken from
``_build_tool_registry``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import infra.fetch_product_reference as fetch_product_reference
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
    sidecar = fetch_product_reference.build_sidecar(
        repository="aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET",
        platform=platform,
        manifest_path="pdf_net.csproj",
        contributing_path="CONTRIBUTING.md",
        agent_guidance_path="AGENTS.md",
        ref=None,
    )
    (tmp_path / fetch_product_reference.PRODUCT_REFERENCE_SIDECAR_NAME).write_text(
        json.dumps(sidecar), encoding="utf-8"
    )
    return tmp_path


def _serve(tmp_path: Path, config: DeploymentConfig, manifests_dir: Path) -> dict:
    """The tool registry for the serving-side inputs read from *manifests_dir*. create_server is
    built with the same inputs first, so the registry is the one a real server would expose."""
    inputs = serve_http._serving_product_reference_inputs(manifests_dir)
    store = GenerationManifestStore(tmp_path / "store")
    assert create_server(config, store, product_reference_inputs=inputs) is not None
    scope = resolve_scope(config, request=None)
    return _build_tool_registry(store, scope, inputs or ProductReferenceInputs(), ())


def test_serving_with_a_sidecar_answers_the_exact_target_framework(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifests = _write_sidecar(tmp_path, monkeypatch, platform="net", manifest_text=PDF_NET_CSPROJ)
    monkeypatch.setenv(serve_http.PLATFORM_ENV, "net")

    registry = _serve(tmp_path, NET_CONFIG, manifests)
    answer = registry["get_product_reference"](section="compatibility")

    assert isinstance(answer, ReferenceContent), answer
    assert answer.text == "net8.0"


def test_serving_with_no_sidecar_answers_not_available_with_its_reason_and_does_not_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(serve_http.PLATFORM_ENV, raising=False)
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
    monkeypatch.setenv(serve_http.PLATFORM_ENV, "typescript")
    ts_config = DeploymentConfig(family="pdf", platform="typescript")

    registry = _serve(tmp_path, ts_config, manifests)
    answer = registry["get_product_reference"](section="compatibility")

    # engines.node is the JS branch's answer; the .NET branch would have raised parsing package.json
    # as XML, or answered with a TargetFramework.
    assert isinstance(answer, ReferenceContent), answer
    assert answer.text == ">=18.0.0"


def test_the_tool_input_schema_exposes_only_section(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifests = _write_sidecar(tmp_path, monkeypatch, platform="net", manifest_text=PDF_NET_CSPROJ)
    monkeypatch.setenv(serve_http.PLATFORM_ENV, "net")

    registry = _serve(tmp_path, NET_CONFIG, manifests)
    schema = schema_for(registry["get_product_reference"])

    assert set(schema["properties"]) == {"section"}
    assert schema.get("required", []) == ["section"]


def test_a_present_sidecar_with_the_platform_unset_fails_at_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_sidecar(tmp_path, monkeypatch, platform="net", manifest_text=PDF_NET_CSPROJ)
    monkeypatch.delenv(serve_http.PLATFORM_ENV, raising=False)

    with pytest.raises(RuntimeError, match="FOSS_MCP_PLATFORM"):
        serve_http._serving_product_reference_inputs(tmp_path)
