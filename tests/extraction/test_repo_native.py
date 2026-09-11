"""Offline tests against pdf/net's own real GitHub presence, pinned as fixtures.

``gatectl`` verifies TC-012 with the network proxied to a dead port (``network: false``), so
every network call the readers would make is replaced here with a pinned, real response - a
release listing, a .csproj, a real 404, and a real 200 - rather than invented data.
"""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path

from foss_mcp.extraction.github_release_reader import classify_richness
from foss_mcp.extraction.manifest_reader import read_dotnet_manifest
from foss_mcp.extraction.repo_native_reader import (
    DocumentNotPresent,
    DocumentPresent,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "repo_native"


def _releases_fixture() -> dict:
    return json.loads((FIXTURES / "pdf_net_releases.json").read_text(encoding="utf-8"))


def _font_python_fixture() -> dict:
    return json.loads((FIXTURES / "font_python_releases.json").read_text(encoding="utf-8"))


def test_all_three_richness_values_are_exercised() -> None:
    templated = (
        "## What's Changed\n"
        "* Bump lodash from 4.17.20 to 4.17.21 by @dependabot in "
        "https://github.com/acme/widget/pull/123\n\n"
        "**Full Changelog**: https://github.com/acme/widget/compare/v1.0.0...v1.1.0\n"
    )
    assert classify_richness(templated) == "templated"
    assert classify_richness("") == "none"
    assert classify_richness("   \n\n  ") == "none"
    assert classify_richness(None) == "none"
    # Real pdf/net release bodies: hand-written migration notes, not boilerplate.
    for release in _releases_fixture()["releases"]:
        assert classify_richness(release["body"]) == "detailed", release["tag_name"]


def test_a_real_migration_note_is_not_reported_the_same_as_boilerplate() -> None:
    """Honest degradation: the distinction the classifier exists for, checked directly."""
    real_note = _releases_fixture()["releases"][0]["body"]
    boilerplate = "## What's Changed\n* Fix typo by @octocat in https://github.com/x/y/pull/1\n"
    assert classify_richness(real_note) != classify_richness(boilerplate)
    assert classify_richness(real_note) == "detailed"


def test_a_filled_in_package_blurb_is_templated_not_detailed() -> None:
    """A hand-maintained release body can be headings, bullets and a fenced install command -
    fully-formatted markup - and still say nothing about the release itself. Real bodies from
    aspose-font-foss/Aspose.Font-FOSS-for-Python: 26.9.2 is byte-identical (458 chars) to at
    least five earlier consecutive releases apart from the version number; 2026.5.1 is an
    older, differently-worded mirror-deployment notice. Neither names an actual change, so
    classify_richness must not be fooled by their markup into calling either 'detailed'.
    """
    for release in _font_python_fixture()["releases"]:
        assert classify_richness(release["body"]) == "templated", release["tag_name"]


def test_fetch_releases_paginates_over_the_real_pinned_listing(monkeypatch) -> None:
    """Two pages of real data (per_page=2 over 3 real releases), replayed offline."""
    import foss_mcp.extraction.github_release_reader as reader

    all_releases = _releases_fixture()["releases"]
    pages = [all_releases[0:2], all_releases[2:3]]

    def fake_get_json(url, *, etag=None):
        page_number = int(url.rsplit("page=", 1)[1])
        batch = pages[page_number - 1] if page_number <= len(pages) else []
        return 200, batch, None

    monkeypatch.setattr(reader, "_get_json", fake_get_json)
    releases = reader.fetch_releases("aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET", per_page=2)
    assert [r.tag_name for r in releases] == [r["tag_name"] for r in all_releases]
    assert all(r.richness == "detailed" for r in releases)


def test_the_real_target_framework_is_surfaced_not_flattened() -> None:
    csproj_text = (FIXTURES / "pdf_net.csproj").read_text(encoding="utf-8")
    manifest = read_dotnet_manifest(csproj_text)
    # net8.0 - the exact moniker the manifest states, not "supports .NET" or ".NET 8".
    assert manifest.target_framework == "net8.0"
    assert manifest.package_id == "Aspose.PDF.FOSS"
    assert manifest.version == "26.9.0"


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


def test_a_missing_contributing_md_returns_an_explicit_not_present_result(monkeypatch) -> None:
    """A real 404 from pdf/net, replayed offline - never smoothed into a fabricated summary."""
    import foss_mcp.extraction.repo_native_reader as reader

    def fake_urlopen(request, timeout=30):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(reader.urllib.request, "urlopen", fake_urlopen)
    result = reader.read_repo_document("aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET", "CONTRIBUTING.md")
    assert result == DocumentNotPresent(path="CONTRIBUTING.md")
    assert not isinstance(result, DocumentPresent)


def test_a_missing_agents_md_also_returns_not_present(monkeypatch) -> None:
    import foss_mcp.extraction.repo_native_reader as reader

    def fake_urlopen(request, timeout=30):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(reader.urllib.request, "urlopen", fake_urlopen)
    result = reader.read_repo_document("aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET", "AGENTS.md")
    assert result == DocumentNotPresent(path="AGENTS.md")


def test_a_present_document_is_reported_with_its_real_content(monkeypatch) -> None:
    """The contrast case: README.md really is there, replayed from the real 200 response."""
    import foss_mcp.extraction.repo_native_reader as reader

    payload = (FIXTURES / "pdf_net_readme_present.json").read_bytes()

    def fake_urlopen(request, timeout=30):
        return _FakeResponse(payload)

    monkeypatch.setattr(reader.urllib.request, "urlopen", fake_urlopen)
    result = reader.read_repo_document("aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET", "README.md")
    assert isinstance(result, DocumentPresent)
    assert result.sha == "b10afb6063d78d6c2d42b08e97fae05c300f7a50"
    assert result.size == 104805
    assert "Aspose" in result.content
