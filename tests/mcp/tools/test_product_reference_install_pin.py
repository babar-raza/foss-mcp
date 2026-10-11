"""G3/TC-470 (REQ-G3-006): an install answer that names a registry command also says which commit the
index describes, and that the registry release may be older or newer.

The index and the installable package can describe different code. The answer's ``text`` stays the
bare command (``install_coordinate_for_verification`` and the verified-coordinate comparison depend on
it); the pin goes into the additive ``notice``. For Go the notice also carries the exact-commit form.

Offline and deterministic: synthetic manifests, the committed sidecars, and the in-process MCP
transport. No network, no wall clock.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml
from starlette.testclient import TestClient

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import DeploymentConfig
from foss_mcp.mcp.tools.get_product_reference import (
    SECTIONS,
    NotAvailable,
    ProductReferenceInputs,
    ReferenceContent,
    get_product_reference,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "infra"))
import serve_http  # noqa: E402

MANIFESTS_DIR = REPO_ROOT / "infra" / "demo-manifests"
VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"

CHECKED_AT = "2026-10-11T08:00:00+00:00"
REPOSITORY = "aspose-3d-foss/Aspose.3D-FOSS"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
REPOSITORY_URL = f"https://github.com/{REPOSITORY}"

CSPROJ_TEXT = "<Project><PropertyGroup><PackageId>Aspose.3D.FOSS</PackageId></PropertyGroup></Project>"
PYPROJECT_TEXT = '[project]\nname = "aspose-3d-foss"\nversion = "26.9.0"\n'
PACKAGE_JSON_TEXT = '{"name": "@aspose/3d", "version": "26.9.0"}'
GO_MOD_TEXT = "module github.com/aspose-3d-foss/aspose-3d-foss-for-go\n\ngo 1.21\n"
CARGO_TOML_TEXT = '[package]\nname = "aspose-3d-foss"\nversion = "26.9.0"\n'
POM_XML_TEXT = (
    "<project><groupId>com.aspose</groupId><artifactId>aspose-3d-foss</artifactId>"
    "<version>26.9.0</version></project>"
)
POM_XML_NO_VERSION_TEXT = (
    "<project><groupId>com.aspose</groupId><artifactId>aspose-3d-foss</artifactId></project>"
)
CMAKE_TEXT = (
    'cmake_minimum_required(VERSION 3.20)\nproject(aspose3d VERSION 1.0 HOMEPAGE_URL "https://example.com")\n'
)

# (platform, ecosystem the notice must name, manifest text, bare coordinate, the original install text)
ECOSYSTEMS = [
    pytest.param("typescript", "npm", PACKAGE_JSON_TEXT, "@aspose/3d", "npm install @aspose/3d", id="npm"),
    pytest.param("python", "pypi", PYPROJECT_TEXT, "aspose-3d-foss", "pip install aspose-3d-foss", id="pypi"),
    pytest.param(
        "net", "nuget", CSPROJ_TEXT, "Aspose.3D.FOSS", "dotnet add package Aspose.3D.FOSS", id="nuget"
    ),
    pytest.param(
        "go",
        "go",
        GO_MOD_TEXT,
        "github.com/aspose-3d-foss/aspose-3d-foss-for-go",
        "go get github.com/aspose-3d-foss/aspose-3d-foss-for-go",
        id="go",
    ),
    pytest.param("rust", "cargo", CARGO_TOML_TEXT, "aspose-3d-foss", "cargo add aspose-3d-foss", id="cargo"),
    pytest.param(
        "java", "maven", POM_XML_TEXT, "com.aspose:aspose-3d-foss", "com.aspose:aspose-3d-foss", id="maven"
    ),
]


def _inputs(
    platform: str,
    manifest_text: str | None,
    coordinate: str,
    *,
    verified: bool | None = True,
    source_repository: str | None = REPOSITORY,
    source_ref: str | None = COMMIT,
) -> ProductReferenceInputs:
    return ProductReferenceInputs(
        manifest_text=manifest_text,
        platform=platform,
        install_verified=verified,
        install_verified_coordinate=coordinate,
        install_verified_checked_at=CHECKED_AT,
        source_repository=source_repository,
        source_ref=source_ref,
    )


def _install(inputs: ProductReferenceInputs) -> ReferenceContent:
    result = get_product_reference(inputs, "install")
    assert isinstance(result, ReferenceContent)
    return result


# ---------------------------------------------------------------------
# (1) Every ecosystem, verified and never checked: text unchanged, notice carries the pin.
# ---------------------------------------------------------------------


@pytest.mark.parametrize("verified", [True, None], ids=["verified", "never-checked"])
@pytest.mark.parametrize(("platform", "ecosystem", "manifest", "coordinate", "original"), ECOSYSTEMS)
def test_the_install_text_is_the_original_command_exactly(
    platform: str, ecosystem: str, manifest: str, coordinate: str, original: str, verified: bool | None
) -> None:
    del ecosystem
    result = _install(_inputs(platform, manifest, coordinate, verified=verified))

    assert result.text == original
    assert COMMIT not in result.text
    assert result.section == "install"


@pytest.mark.parametrize("verified", [True, None], ids=["verified", "never-checked"])
@pytest.mark.parametrize(("platform", "ecosystem", "manifest", "coordinate", "original"), ECOSYSTEMS)
def test_the_notice_names_the_repository_the_commit_the_ecosystem_and_the_drift(
    platform: str, ecosystem: str, manifest: str, coordinate: str, original: str, verified: bool | None
) -> None:
    del original
    result = _install(_inputs(platform, manifest, coordinate, verified=verified))

    notice = result.notice
    assert notice is not None
    assert REPOSITORY_URL in notice
    assert COMMIT in notice
    assert f"the {ecosystem} registry" in notice
    assert "older or newer" in notice
    assert "build the library from that commit" in notice
    assert "```" not in notice
    assert result.untrusted_upstream_content is False


# ---------------------------------------------------------------------
# (2) Go gets the exact-commit form; nobody else does; Maven gets the pom version when there is one.
# ---------------------------------------------------------------------


def test_go_notice_carries_the_exact_commit_command() -> None:
    coordinate = "github.com/aspose-3d-foss/aspose-3d-foss-for-go"
    result = _install(_inputs("go", GO_MOD_TEXT, coordinate))

    assert result.notice is not None
    assert f"go get {coordinate}@{COMMIT}" in result.notice


@pytest.mark.parametrize(
    ("platform", "manifest", "coordinate"),
    [
        pytest.param("typescript", PACKAGE_JSON_TEXT, "@aspose/3d", id="npm"),
        pytest.param("python", PYPROJECT_TEXT, "aspose-3d-foss", id="pypi"),
        pytest.param("net", CSPROJ_TEXT, "Aspose.3D.FOSS", id="nuget"),
        pytest.param("rust", CARGO_TOML_TEXT, "aspose-3d-foss", id="cargo"),
        pytest.param("java", POM_XML_TEXT, "com.aspose:aspose-3d-foss", id="maven"),
    ],
)
def test_no_other_ecosystem_gets_an_at_commit_form(platform: str, manifest: str, coordinate: str) -> None:
    result = _install(_inputs(platform, manifest, coordinate))

    assert result.notice is not None
    assert f"@{COMMIT}" not in result.notice
    assert "go get" not in result.notice


def test_maven_notice_states_the_version_the_pom_declares_and_text_stays_the_bare_coordinate() -> None:
    result = _install(_inputs("java", POM_XML_TEXT, "com.aspose:aspose-3d-foss"))

    assert result.text == "com.aspose:aspose-3d-foss"
    assert result.notice is not None
    assert "declares version 26.9.0" in result.notice
    assert "check that the registry serves it" in result.notice


def test_maven_notice_has_no_version_sentence_when_the_pom_declares_none() -> None:
    result = _install(_inputs("java", POM_XML_NO_VERSION_TEXT, "com.aspose:aspose-3d-foss"))

    assert result.text == "com.aspose:aspose-3d-foss"
    assert result.notice is not None
    assert "declares version" not in result.notice
    assert "older or newer" in result.notice


def test_a_repository_that_is_already_a_url_is_used_as_given_in_the_notice() -> None:
    url = "https://github.com/aspose-3d-foss/Aspose.3D-FOSS-for-TypeScript"
    result = _install(_inputs("typescript", PACKAGE_JSON_TEXT, "@aspose/3d", source_repository=url))

    assert result.notice is not None
    assert url in result.notice
    assert "https://github.com/https://" not in result.notice


# ---------------------------------------------------------------------
# (3) Without a pin the answer is exactly today's, and the notice is None.
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source_repository", "source_ref"),
    [
        pytest.param(None, COMMIT, id="no-repository"),
        pytest.param(REPOSITORY, None, id="no-commit"),
        pytest.param(None, None, id="neither"),
        pytest.param("", COMMIT, id="empty-repository"),
        pytest.param(REPOSITORY, "", id="empty-commit"),
    ],
)
@pytest.mark.parametrize(("platform", "ecosystem", "manifest", "coordinate", "original"), ECOSYSTEMS)
def test_without_a_complete_pin_there_is_no_notice_and_the_text_is_unchanged(
    platform: str,
    ecosystem: str,
    manifest: str,
    coordinate: str,
    original: str,
    source_repository: str | None,
    source_ref: str | None,
) -> None:
    del ecosystem
    result = _install(
        _inputs(platform, manifest, coordinate, source_repository=source_repository, source_ref=source_ref)
    )

    assert result.notice is None
    assert result.text == original
    assert result == ReferenceContent("install", original)


# ---------------------------------------------------------------------
# (4) The unresolved answer is byte-identical to today's and carries no notice.
# ---------------------------------------------------------------------


@pytest.mark.parametrize(("platform", "ecosystem", "manifest", "coordinate", "original"), ECOSYSTEMS)
def test_the_unresolved_answer_keeps_its_text_and_has_no_notice(
    platform: str, ecosystem: str, manifest: str, coordinate: str, original: str
) -> None:
    del original
    result = _install(_inputs(platform, manifest, coordinate, verified=False))

    assert result.notice is None
    assert "not found" in result.text
    assert ecosystem in result.text
    assert REPOSITORY_URL in result.text
    assert COMMIT in result.text
    assert f"the exact commit {COMMIT}. " in result.text
    assert "older or newer" not in result.text


# ---------------------------------------------------------------------
# (5) cpp stays NotAvailable with today's reason.
# ---------------------------------------------------------------------


def test_cpp_stays_not_available_with_the_pin_present() -> None:
    result = get_product_reference(_inputs("cpp", CMAKE_TEXT, "", verified=None), "install")

    assert isinstance(result, NotAvailable)
    assert result.reason == (
        "the CMake manifest names no package-manager coordinate; "
        "list_recent_changes may describe binary distributions for this product"
    )


def test_cpp_answer_is_identical_with_and_without_the_pin() -> None:
    pinned = get_product_reference(_inputs("cpp", CMAKE_TEXT, "", verified=None), "install")
    unpinned = get_product_reference(
        _inputs("cpp", CMAKE_TEXT, "", verified=None, source_repository=None, source_ref=None), "install"
    )

    assert pinned == unpinned


def test_a_missing_manifest_stays_not_available_and_a_missing_field_stays_not_available() -> None:
    no_manifest = get_product_reference(_inputs("python", None, "x"), "install")
    no_name = get_product_reference(_inputs("python", "[project]\nversion = '1'\n", "x"), "install")

    assert isinstance(no_manifest, NotAvailable)
    assert isinstance(no_name, NotAvailable)
    assert no_name.reason == "manifest does not state a package name"


# ---------------------------------------------------------------------
# (6) The pin changes no other section.
# ---------------------------------------------------------------------


@pytest.mark.parametrize("section", [s for s in SECTIONS if s != "install"])
@pytest.mark.parametrize(("platform", "ecosystem", "manifest", "coordinate", "original"), ECOSYSTEMS)
def test_every_other_section_is_unchanged_by_the_pin(
    platform: str, ecosystem: str, manifest: str, coordinate: str, original: str, section: str
) -> None:
    del ecosystem, original
    pinned = get_product_reference(_inputs(platform, manifest, coordinate), section)  # type: ignore[arg-type]
    unpinned = get_product_reference(
        _inputs(platform, manifest, coordinate, source_repository=None, source_ref=None),
        section,  # type: ignore[arg-type]
    )

    assert pinned == unpinned


# ---------------------------------------------------------------------
# (7) The real committed sidecars of every pilot in the chart.
# ---------------------------------------------------------------------


def _chart_pilots() -> list[dict]:
    data = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
    return [dict(p) for p in data["ingestion"]["pilots"]]


PILOTS = _chart_pilots()


def _pilot_install(pilot: dict) -> ReferenceContent | NotAvailable:
    config = DeploymentConfig(family=pilot["family"], platform=pilot["platform"])
    inputs = serve_http._serving_product_reference_inputs(MANIFESTS_DIR, config)
    assert inputs is not None, f"no product-reference sidecar for {pilot['family']}/{pilot['platform']}"
    return get_product_reference(inputs, "install")


def _is_command(result: ReferenceContent | NotAvailable) -> bool:
    """A ReferenceContent whose text is a registry command or bare coordinate, not the unresolved prose."""
    return isinstance(result, ReferenceContent) and "was not found there" not in result.text


@pytest.mark.parametrize("pilot", PILOTS, ids=lambda p: f"{p['family']}-{p['platform']}")
def test_a_real_command_answer_names_the_pilots_indexed_commit(pilot: dict) -> None:
    result = _pilot_install(pilot)

    if _is_command(result):
        assert isinstance(result, ReferenceContent)
        assert result.notice is not None
        assert pilot["library"]["commit"] in result.notice
        assert "older or newer" in result.notice
        assert pilot["library"]["commit"] not in result.text
    elif isinstance(result, ReferenceContent):
        assert result.notice is None  # the unresolved prose already names the commit
    else:
        assert isinstance(result, NotAvailable)


def test_at_least_twenty_pilots_serve_a_command_answer_with_the_pinned_commit() -> None:
    assert len(PILOTS) >= 20
    pinned = 0
    for pilot in PILOTS:
        result = _pilot_install(pilot)
        if _is_command(result):
            assert isinstance(result, ReferenceContent)
            assert result.notice is not None
            assert pilot["library"]["commit"] in result.notice
            pinned += 1
    assert pinned >= 20


def test_pdf_go_notice_carries_the_indexed_commit_and_the_exact_commit_command() -> None:
    pilot = next(p for p in PILOTS if (p["family"], p["platform"]) == ("pdf", "go"))
    result = _pilot_install(pilot)

    assert isinstance(result, ReferenceContent)
    assert result.text == "go get github.com/aspose-pdf-foss/aspose-pdf-foss-for-go"
    assert result.notice is not None
    assert "cdf43df10c8c565ecaa978428b1fe66ad6685f8d" in result.notice
    assert "go get github.com/aspose-pdf-foss/aspose-pdf-foss-for-go@" in result.notice
    assert (
        "go get github.com/aspose-pdf-foss/aspose-pdf-foss-for-go@cdf43df10c8c565ecaa978428b1fe66ad6685f8d"
        in result.notice
    )


# ---------------------------------------------------------------------
# (8) Through the real tool dispatch of the served app.
# ---------------------------------------------------------------------

VALID_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "Origin": "https://example.com",
    "MCP-Protocol-Version": "2025-06-18",
}


def _sse_json(text: str) -> dict:
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:") :].strip())
    raise AssertionError(f"no SSE data line found in response: {text!r}")


def _call_install_over_transport(tmp_path: Path) -> dict:
    app = serve_http.build_app(
        DeploymentConfig(family="pdf", platform="go"),
        manifest_store=GenerationManifestStore(tmp_path / "manifests"),
        allowed_origins=["https://example.com"],
        manifests_dir=MANIFESTS_DIR,
    )
    with TestClient(app) as client:
        init = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "0.0.1"},
                },
            },
            headers=VALID_HEADERS,
        )
        assert init.status_code == 200, init.text
        headers = dict(VALID_HEADERS, **{"mcp-session-id": init.headers["mcp-session-id"]})
        client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=headers)
        response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "get_product_reference", "arguments": {"section": "install"}},
            },
            headers=headers,
        )
    assert response.status_code == 200
    return _sse_json(response.text)


def test_the_served_get_product_reference_install_carries_the_notice(tmp_path: Path) -> None:
    body = _call_install_over_transport(tmp_path)

    assert body["result"]["isError"] is False
    structured = body["result"]["structuredContent"]["result"]
    assert structured["section"] == "install"
    assert structured["text"] == "go get github.com/aspose-pdf-foss/aspose-pdf-foss-for-go"
    assert "cdf43df10c8c565ecaa978428b1fe66ad6685f8d" in structured["notice"]
    assert "older or newer" in structured["notice"]
    assert (
        "go get github.com/aspose-pdf-foss/aspose-pdf-foss-for-go@cdf43df10c8c565ecaa978428b1fe66ad6685f8d"
        in (structured["notice"])
    )
    # the plain-text channel agrees with the structured one
    assert json.loads(body["result"]["content"][0]["text"]) == structured
