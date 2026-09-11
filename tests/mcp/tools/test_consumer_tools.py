"""The three consumer-facing tools (get_product_reference, list_recent_changes,
report_index_freshness) against real pdf/net data from TC-011/TC-012's committed fixtures.
"""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path

import pytest

from foss_mcp.extraction.repo_native_reader import read_repo_document
from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.indexing.publisher import publish_generation
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_product_reference import (
    NotAvailable,
    ProductReferenceInputs,
    ReferenceContent,
    get_product_reference,
)
from foss_mcp.mcp.tools.list_recent_changes import list_recent_changes
from foss_mcp.mcp.tools.report_index_freshness import report_index_freshness
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document
from tests.indexing.test_index_writers import DeterministicEmbeddingProvider

FIXTURES = Path(__file__).parents[2] / "fixtures" / "repo_native"
API_SURFACE_FIXTURE = Path(__file__).parents[2] / "fixtures" / "pdf_net" / "api_surface.json"
PDF_NET_SCOPE = Scope(family="pdf", platform="net")


def _read_document_replaying_fixture(monkeypatch: pytest.MonkeyPatch, path: str, fixture_name: str):
    """Replay a real, pinned GitHub Contents API response through the real reader - offline,
    but exercising the actual read_repo_document code path, not a hand-built stand-in."""
    import foss_mcp.extraction.repo_native_reader as reader_module

    payload = (FIXTURES / fixture_name).read_bytes()

    def fake_urlopen(request, timeout=30):
        data = json.loads(payload)
        if "message" in data and data.get("status") == "404":
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

        class _FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *exc_info):
                return None

            def read(self):
                return payload

        return _FakeResponse()

    monkeypatch.setattr(reader_module.urllib.request, "urlopen", fake_urlopen)
    return read_repo_document("aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET", path)


def _inputs(monkeypatch: pytest.MonkeyPatch) -> ProductReferenceInputs:
    manifest_text = (FIXTURES / "pdf_net.csproj").read_text(encoding="utf-8")
    contributing = _read_document_replaying_fixture(monkeypatch, "CONTRIBUTING.md", "pdf_net_contributing_404.json")
    agent_guidance = _read_document_replaying_fixture(monkeypatch, "AGENTS.md", "pdf_net_agents_404.json")
    return ProductReferenceInputs(manifest_text=manifest_text, contributing=contributing, agent_guidance=agent_guidance)


def test_compatibility_surfaces_the_real_target_framework_not_flattened(monkeypatch: pytest.MonkeyPatch) -> None:
    result = get_product_reference(_inputs(monkeypatch), "compatibility")
    assert isinstance(result, ReferenceContent)
    assert result.text == "net8.0"  # the exact manifest value, not "supports .NET"


def test_license_and_install_and_support_are_real_manifest_derived_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = _inputs(monkeypatch)
    license_result = get_product_reference(inputs, "license")
    install_result = get_product_reference(inputs, "install")
    support_result = get_product_reference(inputs, "support")

    assert isinstance(license_result, ReferenceContent) and license_result.text == "MIT"
    assert isinstance(install_result, ReferenceContent) and install_result.text == "dotnet add package Aspose.PDF.FOSS"
    assert isinstance(support_result, ReferenceContent)
    assert support_result.text == "https://github.com/aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET"


def test_a_genuinely_absent_contributing_file_is_an_explicit_not_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real data: pdf/net genuinely has no CONTRIBUTING.md (TC-012's real 404 fixture)."""
    result = get_product_reference(_inputs(monkeypatch), "contributing")
    assert isinstance(result, NotAvailable)


def test_a_genuinely_absent_agent_guidance_file_is_an_explicit_not_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = get_product_reference(_inputs(monkeypatch), "agent_guidance")
    assert isinstance(result, NotAvailable)


def test_sections_with_no_data_source_are_honestly_not_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """formats and limitations are not fabricated just because a manifest happens to exist."""
    inputs = _inputs(monkeypatch)
    assert isinstance(get_product_reference(inputs, "formats"), NotAvailable)
    assert isinstance(get_product_reference(inputs, "limitations"), NotAvailable)


def test_every_section_selector_is_exercised(monkeypatch: pytest.MonkeyPatch) -> None:
    from foss_mcp.mcp.tools.get_product_reference import SECTIONS

    inputs = _inputs(monkeypatch)
    for section in SECTIONS:
        result = get_product_reference(inputs, section)
        assert isinstance(result, ReferenceContent | NotAvailable)


def test_an_unrecognized_section_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError):
        get_product_reference(_inputs(monkeypatch), "changelog")  # type: ignore[arg-type]


def test_richness_is_carried_through_list_recent_changes_honestly() -> None:
    """Real releases with genuinely different richness verdicts (TC-012's own fixtures):
    pdf/net's are all 'detailed', font-python's are all 'templated'."""
    pdf_net = json.loads((FIXTURES / "pdf_net_releases.json").read_text(encoding="utf-8"))
    font_python = json.loads((FIXTURES / "font_python_releases.json").read_text(encoding="utf-8"))

    from foss_mcp.extraction.github_release_reader import classify_richness, Release

    releases = [
        Release(r["tag_name"], r["body"], classify_richness(r["body"])) for r in pdf_net["releases"]
    ] + [
        Release(r["tag_name"], r["body"], classify_richness(r["body"])) for r in font_python["releases"]
    ]

    entries = list_recent_changes(releases)

    assert {e.richness for e in entries if e.tag_name in {r["tag_name"] for r in pdf_net["releases"]}} == {"detailed"}
    assert {e.richness for e in entries if e.tag_name in {r["tag_name"] for r in font_python["releases"]}} == {
        "templated"
    }


def test_list_recent_changes_respects_the_limit() -> None:
    from foss_mcp.extraction.github_release_reader import Release

    releases = [Release(f"v{i}", "", "none") for i in range(5)]
    assert len(list_recent_changes(releases, limit=2)) == 2


def _store(tmp_path: Path) -> GenerationManifestStore:
    return GenerationManifestStore(tmp_path / "manifests")


def _publish_pdf_net_symbols_with_source_commit(store: GenerationManifestStore, source_commit: str) -> str:
    fixture = json.loads(API_SURFACE_FIXTURE.read_text(encoding="utf-8"))
    sections = []
    for entry in fixture["types"][:5]:
        name = entry.get("class_import") or entry.get("name", "")
        sections.append(f"## {name}\n\nFQN: {name}\nKind: {entry.get('kind', '')}\nSource-Commit: {source_commit}")
    doc = make_document(
        source_kind=SourceKind.SELF_EXTRACTED,
        content_type="api_surface",
        provenance=Provenance(repository=fixture["source_repository"], commit=source_commit),
        evidence_refs=(),
        title="pdf/net API surface",
        body="\n\n".join(sections),
    )
    lease = store.acquire_lease("pdf::net::self_extracted", "worker-1", "pending")
    return publish_generation(
        store,
        family="pdf",
        platform="net",
        source_kind="self_extracted",
        expected_active=None,
        chunks=chunk_document(doc),
        embedding_provider=DeterministicEmbeddingProvider(),
        lease=lease,
    )


def test_freshness_reports_a_fresh_index_when_commits_match(tmp_path: Path) -> None:
    store = _store(tmp_path)
    real_commit = json.loads(API_SURFACE_FIXTURE.read_text(encoding="utf-8"))["source_commit"]
    _publish_pdf_net_symbols_with_source_commit(store, real_commit)

    report = report_index_freshness(store, PDF_NET_SCOPE, "self_extracted", real_commit)

    assert report.stale is False
    assert report.indexed_source_commit == real_commit
    assert report.current_source_commit == real_commit


def test_freshness_reports_stale_when_the_source_has_moved_on(tmp_path: Path) -> None:
    store = _store(tmp_path)
    real_commit = json.loads(API_SURFACE_FIXTURE.read_text(encoding="utf-8"))["source_commit"]
    _publish_pdf_net_symbols_with_source_commit(store, real_commit)

    newer_commit = "a" * 40
    report = report_index_freshness(store, PDF_NET_SCOPE, "self_extracted", newer_commit)

    assert report.stale is True
    assert report.indexed_source_commit == real_commit
    assert "current source is" in report.reason


def test_freshness_reports_never_published_for_an_untouched_scope(tmp_path: Path) -> None:
    store = _store(tmp_path)
    report = report_index_freshness(store, Scope(family="cells", platform="python"), "self_extracted", "x")
    assert report.stale is True
    assert report.indexed_generation_id is None
    assert report.reason == "never published"
