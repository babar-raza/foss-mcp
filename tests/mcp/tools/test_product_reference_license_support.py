"""G3/TC-427: get_product_reference answers license, support and agent guidance from the repository.

Before this card ``license`` was NotAvailable on 13 pilots and ``support`` on 16 although every
sidecar records its source repository: nothing ever fetched the repository's LICENSE file, the .NET
``Directory.Build.props`` was never read, and agent guidance was looked up by the exact,
case-sensitive path AGENTS.md (so ``Agents.md`` and ``agents.md`` answered "not present").

Fully offline and deterministic: the serving tests read the baked sidecars under
``infra/demo-manifests`` as plain JSON and build ``ProductReferenceInputs`` from the ORIGINAL keys
only, so they keep meaning "today's answers" after those sidecars are refetched; the fetch tests go
through a fake ``urlopen`` serving recorded Contents-API bodies. Nothing contacts GitHub.
"""

from __future__ import annotations

import json
import sys
import urllib.error
from base64 import b64encode
from pathlib import Path

import pytest

import foss_mcp.extraction.manifest_reader as manifest_reader
import foss_mcp.extraction.repo_native_reader as repo_native_reader
import infra.fetch_product_reference as fetch_product_reference
from foss_mcp.extraction.repo_native_reader import DocumentNotPresent, DocumentPresent
from foss_mcp.mcp.tools.get_product_reference import (
    SECTIONS,
    NotAvailable,
    ProductReferenceInputs,
    ReferenceContent,
    _spdx_from_license_text,
    get_product_reference,
)

_ROOT = Path(__file__).resolve().parents[3]
_MANIFEST_DIR = _ROOT / "infra" / "demo-manifests"
_FIXTURE_DIR = _ROOT / "tests" / "fixtures" / "product_reference_upstream"


def _fixture(name: str) -> str:
    return (_FIXTURE_DIR / name).read_text(encoding="utf-8")


def _sidecar(pilot: str) -> dict:
    return json.loads((_MANIFEST_DIR / f"product_reference_{pilot}.json").read_text(encoding="utf-8"))


def _document(raw: dict | None):
    if not raw or raw.get("status") != "present":
        return DocumentNotPresent(path=(raw or {}).get("path", ""))
    return DocumentPresent(path=raw["path"], sha=raw["sha"], size=raw["size"], content=raw["content"])


def _inputs(pilot: str, **extra: object) -> ProductReferenceInputs:
    """The baked pilot as it was BEFORE this card: only the original sidecar keys, plus *extra*."""
    data = _sidecar(pilot)
    return ProductReferenceInputs(
        manifest_text=data["manifest_text"],
        contributing=_document(data.get("contributing")),
        agent_guidance=_document(data.get("agent_guidance")),
        platform=data["platform"],
        **extra,
    )


def _license_document(name: str, path: str = "LICENSE") -> DocumentPresent:
    content = _fixture(name)
    return DocumentPresent(path=path, sha="1" * 40, size=len(content.encode("utf-8")), content=content)


# --- _spdx_from_license_text -------------------------------------------------------------------


def test_the_standard_mit_text_is_identified_as_mit() -> None:
    assert _spdx_from_license_text(_fixture("LICENSE_mit.txt")) == "MIT"


def test_the_mit_license_mit_variant_heading_is_identified_as_mit() -> None:
    assert _spdx_from_license_text(_fixture("LICENSE_mit_variant.txt")) == "MIT"


def test_the_apache_two_title_block_is_identified_as_apache_2_0() -> None:
    assert _spdx_from_license_text(_fixture("LICENSE_apache_excerpt.txt")) == "Apache-2.0"


def test_a_text_carrying_both_the_mit_and_apache_fingerprints_is_not_identified() -> None:
    assert _spdx_from_license_text(_fixture("LICENSE_dual.txt")) is None


def test_an_unknown_text_is_not_identified() -> None:
    assert _spdx_from_license_text("Copyright 2026 Example Co. All rights reserved.\n") is None
    assert _spdx_from_license_text("") is None


def test_case_and_whitespace_do_not_matter_to_the_fingerprints() -> None:
    mangled = " ".join(_fixture("LICENSE_mit.txt").upper().split())
    assert "\n" not in mangled
    assert _spdx_from_license_text(mangled) == "MIT"
    assert _spdx_from_license_text("\t" + _fixture("LICENSE_apache_excerpt.txt").lower()) == "Apache-2.0"


def test_a_text_with_only_half_of_the_mit_fingerprint_is_not_identified() -> None:
    first_half = _fixture("LICENSE_mit.txt").split("The above copyright notice", 1)[0]
    assert "Permission is hereby granted" in first_half
    assert _spdx_from_license_text(first_half) is None


_BSD_REDISTRIBUTION = (
    "Redistribution and use in source and binary forms, with or without\n"
    "modification, are permitted provided that the following conditions are met:\n"
)


def test_bsd_three_clause_is_told_from_bsd_two_clause_by_the_endorsement_clause() -> None:
    three = _BSD_REDISTRIBUTION + "3. Neither the name of the copyright holder nor the names of its\n"
    assert _spdx_from_license_text(three) == "BSD-3-Clause"
    assert _spdx_from_license_text(_BSD_REDISTRIBUTION) == "BSD-2-Clause"


def test_bsd_text_alongside_mit_text_is_ambiguous() -> None:
    assert _spdx_from_license_text(_BSD_REDISTRIBUTION + _fixture("LICENSE_mit.txt")) is None


# --- license: the repository's own license file ------------------------------------------------


@pytest.mark.parametrize("pilot", ["pdf_go", "pdf_cpp"])
def test_license_for_go_and_cpp_pilots_is_the_spdx_id_from_a_present_license_document(pilot: str) -> None:
    result = get_product_reference(
        _inputs(pilot, license_document=_license_document("LICENSE_mit.txt")), "license"
    )

    assert result == ReferenceContent(
        "license",
        "MIT",
        notice=(
            "Identified from the repository's LICENSE file at the pinned commit; "
            "the packaging manifest states no license."
        ),
    )


def test_the_notice_names_the_path_the_license_text_was_read_from() -> None:
    document = _license_document("LICENSE_mit.txt", path="LICENSE/LICENSE.txt")

    result = get_product_reference(_inputs("pdf_cpp", license_document=document), "license")

    assert isinstance(result, ReferenceContent)
    assert result.notice is not None
    assert "LICENSE/LICENSE.txt" in result.notice


def test_an_unidentified_license_answers_its_first_non_empty_line_with_a_notice() -> None:
    text = "\n\n  Example Source License 1.0  \n\nYou may do things.\n"
    document = DocumentPresent(path="LICENSE.md", sha="2" * 40, size=len(text), content=text)

    result = get_product_reference(_inputs("pdf_go", license_document=document), "license")

    assert isinstance(result, ReferenceContent)
    assert result.text == "Example Source License 1.0"
    assert result.notice == (
        "First line of the repository's LICENSE.md file; the license was not identified from its text."
    )


def test_a_dual_license_document_is_not_guessed_and_answers_its_first_line() -> None:
    document = _license_document("LICENSE_dual.txt")

    result = get_product_reference(_inputs("pdf_cpp", license_document=document), "license")

    assert isinstance(result, ReferenceContent)
    assert result.text == "MIT License"
    assert result.notice is not None
    assert result.notice.startswith("First line of the repository's LICENSE file")


def test_a_not_present_or_blank_license_document_keeps_the_go_and_cpp_reasons() -> None:
    blank = DocumentPresent(path="LICENSE", sha="3" * 40, size=3, content=" \n\n")
    for document in (DocumentNotPresent(path="LICENSE"), blank):
        go = get_product_reference(_inputs("pdf_go", license_document=document), "license")
        cpp = get_product_reference(_inputs("pdf_cpp", license_document=document), "license")
        assert go == NotAvailable("license", "go.mod has no license field; see the repository's LICENSE file")
        assert cpp == NotAvailable(
            "license", "CMakeLists.txt states no license; see the repository's LICENSE file"
        )


def test_a_manifest_license_still_wins_over_the_license_document() -> None:
    manifest = '[project]\nname = "x"\nlicense = "BSD-3-Clause"\n'
    inputs = ProductReferenceInputs(
        manifest_text=manifest,
        platform="python",
        license_document=_license_document("LICENSE_mit.txt"),
    )

    assert get_product_reference(inputs, "license") == ReferenceContent("license", "BSD-3-Clause")


def test_a_python_manifest_without_a_license_falls_back_to_the_license_document() -> None:
    inputs = ProductReferenceInputs(
        manifest_text='[project]\nname = "x"\n',
        platform="python",
        license_document=_license_document("LICENSE_mit.txt"),
    )

    result = get_product_reference(inputs, "license")

    assert isinstance(result, ReferenceContent)
    assert result.text == "MIT"


def test_slides_net_with_the_recorded_directory_build_props_answers_license_mit() -> None:
    props = _fixture("slides_net_Directory.Build.props")

    result = get_product_reference(_inputs("slides_net", build_props_text=props), "license")

    assert result == ReferenceContent("license", "MIT")


def test_the_dotnet_build_props_expression_is_tried_before_the_license_document() -> None:
    props = _fixture("slides_net_Directory.Build.props")
    apache = _license_document("LICENSE_apache_excerpt.txt")

    result = get_product_reference(
        _inputs("slides_net", build_props_text=props, license_document=apache), "license"
    )

    assert result == ReferenceContent("license", "MIT")


def test_a_dotnet_manifest_expression_wins_over_build_props_and_the_license_document() -> None:
    manifest = (
        "<Project><PropertyGroup><PackageLicenseExpression>Apache-2.0</PackageLicenseExpression>"
        "</PropertyGroup></Project>"
    )
    inputs = ProductReferenceInputs(
        manifest_text=manifest,
        platform="net",
        build_props_text=_fixture("slides_net_Directory.Build.props"),
        license_document=_license_document("LICENSE_mit.txt"),
    )

    assert get_product_reference(inputs, "license") == ReferenceContent("license", "Apache-2.0")


def test_a_dotnet_pilot_without_build_props_uses_the_license_document() -> None:
    result = get_product_reference(
        _inputs("words_net", license_document=_license_document("LICENSE_mit.txt")), "license"
    )

    assert isinstance(result, ReferenceContent)
    assert result.text == "MIT"


def test_build_props_text_is_ignored_for_non_dotnet_platforms() -> None:
    result = get_product_reference(
        _inputs("pdf_go", build_props_text=_fixture("slides_net_Directory.Build.props")), "license"
    )

    assert isinstance(result, NotAvailable)


# --- support: Directory.Build.props, then the labelled source repository -----------------------

# A sidecar written by the TC-427 fetcher always records license_document (here: none found upstream).
_FETCHED = DocumentNotPresent(path="LICENSE")

_SUPPORT_NOTICE = "The packaging manifest states no project or support url; this is the source repository."


def test_slides_net_with_the_recorded_directory_build_props_answers_the_package_project_url() -> None:
    props = _fixture("slides_net_Directory.Build.props")

    result = get_product_reference(_inputs("slides_net", build_props_text=props), "support")

    assert result == ReferenceContent(
        "support", "https://github.com/aspose-slides-foss/Aspose.Slides-FOSS-for-.NET"
    )


@pytest.mark.parametrize("pilot", ["pdf_go", "tex_python"])
def test_support_without_a_manifest_url_is_the_labelled_source_repository(pilot: str) -> None:
    repository = _sidecar(pilot)["source_repository"]

    result = get_product_reference(
        _inputs(pilot, source_repository=repository, license_document=_FETCHED), "support"
    )

    assert result == ReferenceContent("support", f"https://github.com/{repository}", notice=_SUPPORT_NOTICE)


def test_a_full_source_repository_url_is_kept_as_given() -> None:
    url = "https://example.org/forge/owner/name"

    result = get_product_reference(
        _inputs("pdf_go", source_repository=url, license_document=_FETCHED), "support"
    )

    assert isinstance(result, ReferenceContent)
    assert result.text == url


@pytest.mark.parametrize("pilot", ["pdf_go", "tex_python", "slides_net"])
def test_a_sidecar_from_before_the_fetcher_change_keeps_its_support_answer_despite_the_pin(
    pilot: str,
) -> None:
    """The pin recorded for the install section (TC-353) must not change ``support`` on its own."""
    repository = _sidecar(pilot)["source_repository"]

    with_pin = _inputs(pilot, source_repository=repository, source_ref="a" * 40)
    without_pin = _inputs(pilot)

    assert get_product_reference(with_pin, "support") == get_product_reference(without_pin, "support")
    assert isinstance(get_product_reference(with_pin, "support"), NotAvailable)


def test_a_url_the_manifest_states_is_not_replaced_by_the_source_repository() -> None:
    manifest = '[project]\nname = "x"\n[project.urls]\nHomepage = "https://example.org/home"\n'
    inputs = ProductReferenceInputs(manifest_text=manifest, platform="python", source_repository="owner/name")

    result = get_product_reference(inputs, "support")

    assert result == ReferenceContent("support", "https://example.org/home")


def test_the_manifests_project_url_wins_over_build_props_for_dotnet() -> None:
    manifest = (
        "<Project><PropertyGroup><PackageProjectUrl>https://example.org/manifest</PackageProjectUrl>"
        "</PropertyGroup></Project>"
    )
    inputs = ProductReferenceInputs(
        manifest_text=manifest,
        platform="net",
        build_props_text=_fixture("slides_net_Directory.Build.props"),
        source_repository="owner/name",
    )

    assert get_product_reference(inputs, "support") == ReferenceContent(
        "support", "https://example.org/manifest"
    )


def test_build_props_url_is_tried_before_the_source_repository() -> None:
    result = get_product_reference(
        _inputs(
            "slides_net",
            build_props_text=_fixture("slides_net_Directory.Build.props"),
            source_repository="aspose-slides-foss/Aspose.Slides-FOSS-for-.NET",
            license_document=_FETCHED,
        ),
        "support",
    )

    assert isinstance(result, ReferenceContent)
    assert result.notice is None
    assert result.text == "https://github.com/aspose-slides-foss/Aspose.Slides-FOSS-for-.NET"


def test_without_a_manifest_the_license_document_and_source_repository_still_answer() -> None:
    inputs = ProductReferenceInputs(
        manifest_text=None,
        platform="go",
        license_document=_license_document("LICENSE_mit.txt"),
        source_repository="owner/name",
    )

    license_result = get_product_reference(inputs, "license")
    support_result = get_product_reference(inputs, "support")

    assert isinstance(license_result, ReferenceContent)
    assert license_result.text == "MIT"
    assert isinstance(support_result, ReferenceContent)
    assert support_result.text == "https://github.com/owner/name"
    assert get_product_reference(ProductReferenceInputs(manifest_text=None), "license") == NotAvailable(
        "license", "no packaging manifest was provided"
    )


# --- every answer is today's when the new fields are None -------------------------------------

_TODAY = {
    "pdf_go": {
        "license": "go.mod has no license field; see the repository's LICENSE file",
        "support": "manifest does not state a project url",
    },
    "pdf_cpp": {
        "license": "CMakeLists.txt states no license; see the repository's LICENSE file",
        "support": "manifest does not state a project url",
    },
    "slides_net": {
        "license": "manifest does not state a license expression",
        "support": "manifest does not state a project url",
    },
    "words_net": {
        "license": "manifest does not state a license expression",
        "support": "manifest does not state a project url",
    },
}


@pytest.mark.parametrize("pilot", sorted(_TODAY))
def test_with_the_new_fields_none_license_and_support_answer_exactly_as_before(pilot: str) -> None:
    inputs = _inputs(pilot, license_document=None, build_props_text=None, source_repository=None)

    for section in ("license", "support"):
        assert get_product_reference(inputs, section) == NotAvailable(section, _TODAY[pilot][section])


@pytest.mark.parametrize("pilot", sorted(_TODAY))
def test_with_the_new_fields_none_no_section_changes_against_an_inputs_built_without_them(
    pilot: str,
) -> None:
    data = _sidecar(pilot)
    legacy = ProductReferenceInputs(
        manifest_text=data["manifest_text"],
        contributing=_document(data.get("contributing")),
        agent_guidance=_document(data.get("agent_guidance")),
        platform=data["platform"],
    )
    explicit = _inputs(pilot, license_document=None, build_props_text=None)

    for section in SECTIONS:
        assert get_product_reference(explicit, section) == get_product_reference(legacy, section)
    assert legacy.license_document is None
    assert legacy.build_props_text is None


# --- the fetch side, through a fake urlopen ---------------------------------------------------

_REPO = "aspose-example-foss/Aspose.Example-FOSS-for-Python"
_REF = "e" * 40


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


def _file_body(path: str, content: str) -> bytes:
    encoded = b64encode(content.encode("utf-8")).decode("ascii")
    return json.dumps(
        {"path": path, "sha": "5" * 40, "size": len(content.encode("utf-8")), "content": encoded}
    ).encode("utf-8")


def _listing(*entries: tuple[str, str]) -> bytes:
    return json.dumps([{"name": name, "type": kind} for name, kind in entries]).encode("utf-8")


def _serve(monkeypatch, routes: dict[str, bytes | int]) -> list[str]:
    """Install a fake urlopen routed by the Contents-API path (the part after ``/contents/``, before
    any query). A path with no route is a test failure - never an implicit 404, never a catch-all.
    Returns the list of requested paths, in order."""
    requested: list[str] = []

    def fake_urlopen(request, timeout=30):
        path = request.full_url.rsplit("/contents/", 1)[1].split("?", 1)[0]
        requested.append(path)
        assert path in routes, f"unexpected Contents-API request for {path!r}"
        outcome = routes[path]
        if isinstance(outcome, int):
            raise urllib.error.HTTPError(request.full_url, outcome, "Not Found", {}, None)
        return _FakeResponse(outcome)

    monkeypatch.setattr(manifest_reader.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(repo_native_reader.urllib.request, "urlopen", fake_urlopen)
    return requested


def _build(platform: str = "python", ref: str | None = _REF) -> dict:
    return fetch_product_reference.build_sidecar(
        repository=_REPO,
        platform=platform,
        manifest_path=None,
        contributing_path="CONTRIBUTING.md",
        agent_guidance_path="AGENTS.md",
        ref=ref,
    )


def test_agents_md_in_title_case_is_fetched_for_the_agents_md_request(monkeypatch) -> None:
    requested = _serve(
        monkeypatch,
        {
            "": _listing(("Agents.md", "file"), ("README.md", "file")),
            "Agents.md": _file_body("Agents.md", "# Guidance\n"),
            "README.md": _file_body("README.md", "# Demo\n"),  # TC-561: the README is fetched whole too
            "CONTRIBUTING.md": 404,
        },
    )

    sidecar = _build()

    assert sidecar["agent_guidance"]["status"] == "present"
    assert sidecar["agent_guidance"]["path"] == "Agents.md"
    assert sidecar["agent_guidance"]["content"] == "# Guidance\n"
    assert "AGENTS.md" not in requested
    assert sidecar["contributing"] == {"status": "not_present", "path": "CONTRIBUTING.md"}


def test_agents_md_in_lower_case_is_fetched_for_the_agents_md_request(monkeypatch) -> None:
    _serve(
        monkeypatch,
        {
            "": _listing(("agents.md", "file")),
            "agents.md": _file_body("agents.md", "# lower\n"),
            "CONTRIBUTING.md": 404,
        },
    )

    sidecar = _build()

    assert sidecar["agent_guidance"]["path"] == "agents.md"
    assert sidecar["agent_guidance"]["content"] == "# lower\n"


def test_an_exact_case_agents_md_wins_over_a_differently_cased_one(monkeypatch) -> None:
    requested = _serve(
        monkeypatch,
        {
            "": _listing(("agents.md", "file"), ("AGENTS.md", "file")),
            "AGENTS.md": _file_body("AGENTS.md", "# exact\n"),
            "CONTRIBUTING.md": 404,
        },
    )

    sidecar = _build()

    assert sidecar["agent_guidance"]["path"] == "AGENTS.md"
    assert "agents.md" not in requested


def test_contributing_is_resolved_case_insensitively_too(monkeypatch) -> None:
    _serve(
        monkeypatch,
        {
            "": _listing(("Contributing.md", "file")),
            "Contributing.md": _file_body("Contributing.md", "# Help\n"),
            "AGENTS.md": 404,
        },
    )

    sidecar = _build()

    assert sidecar["contributing"]["path"] == "Contributing.md"
    assert sidecar["agent_guidance"] == {"status": "not_present", "path": "AGENTS.md"}


def test_a_directory_named_like_a_document_is_not_fetched_as_that_document(monkeypatch) -> None:
    _serve(
        monkeypatch,
        {"": _listing(("agents.md", "dir")), "AGENTS.md": 404, "CONTRIBUTING.md": 404},
    )

    sidecar = _build()

    assert sidecar["agent_guidance"] == {"status": "not_present", "path": "AGENTS.md"}


def test_the_root_listing_is_requested_once_and_with_the_pinned_ref(monkeypatch) -> None:
    urls: list[str] = []

    def fake_urlopen(request, timeout=30):
        urls.append(request.full_url)
        path = request.full_url.rsplit("/contents/", 1)[1].split("?", 1)[0]
        if path == "":
            return _FakeResponse(_listing())
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(repo_native_reader.urllib.request, "urlopen", fake_urlopen)

    _build(ref=_REF)

    listings = [url for url in urls if url.rsplit("/contents/", 1)[1].split("?", 1)[0] == ""]
    assert listings == [f"https://api.github.com/repos/{_REPO}/contents/?ref={_REF}"]


def test_list_root_entries_returns_name_and_type_pairs_and_a_404_returns_empty(monkeypatch) -> None:
    _serve(monkeypatch, {"": _listing(("LICENSE", "file"), ("src", "dir"))})
    assert repo_native_reader.list_root_entries(_REPO, ref=_REF) == [("LICENSE", "file"), ("src", "dir")]

    _serve(monkeypatch, {"": 404})
    assert repo_native_reader.list_root_entries(_REPO) == []


def test_a_root_license_file_is_fetched_and_recorded_as_license_document(monkeypatch) -> None:
    mit = _fixture("LICENSE_mit.txt")
    _serve(
        monkeypatch,
        {
            "": _listing(("LICENSE", "file")),
            "LICENSE": _file_body("LICENSE", mit),
            "AGENTS.md": 404,
            "CONTRIBUTING.md": 404,
        },
    )

    sidecar = _build()

    assert sidecar["license_document"]["status"] == "present"
    assert sidecar["license_document"]["path"] == "LICENSE"
    assert sidecar["license_document"]["content"] == mit


def test_a_license_file_name_is_matched_case_insensitively_and_by_priority(monkeypatch) -> None:
    _serve(
        monkeypatch,
        {
            "": _listing(("COPYING", "file"), ("license.md", "file")),
            "license.md": _file_body("license.md", "text\n"),
            "AGENTS.md": 404,
            "CONTRIBUTING.md": 404,
        },
    )

    sidecar = _build()

    assert sidecar["license_document"]["path"] == "license.md"


def test_a_root_license_directory_is_listed_once_more_and_its_license_txt_is_found(monkeypatch) -> None:
    mit = _fixture("LICENSE_mit.txt")
    requested = _serve(
        monkeypatch,
        {
            "": _listing(("LICENSE", "dir"), ("README.md", "file")),
            "LICENSE": _listing(("NOTICE", "file"), ("LICENSE.txt", "file")),
            "LICENSE/LICENSE.txt": _file_body("LICENSE/LICENSE.txt", mit),
            "README.md": _file_body("README.md", "# Demo\n"),  # TC-561: the README is fetched whole too
            "AGENTS.md": 404,
            "CONTRIBUTING.md": 404,
        },
    )

    sidecar = _build()

    assert sidecar["license_document"]["status"] == "present"
    assert sidecar["license_document"]["path"] == "LICENSE/LICENSE.txt"
    assert sidecar["license_document"]["content"] == mit
    assert requested.count("LICENSE") == 1


def test_a_repository_with_no_license_records_not_present_and_fetches_nothing_for_it(monkeypatch) -> None:
    requested = _serve(
        monkeypatch,
        {
            "": _listing(("README.md", "file"), ("docs", "dir")),
            "README.md": _file_body("README.md", "# Demo\n"),
            "AGENTS.md": 404,
            "CONTRIBUTING.md": 404,
        },
    )

    sidecar = _build()

    assert sidecar["license_document"] == {"status": "not_present", "path": "LICENSE"}
    # TC-561: README.md is now fetched too (listed above); nothing is requested for a license that is not there.
    assert sorted(requested) == ["", "AGENTS.md", "CONTRIBUTING.md", "README.md"]


def test_a_license_directory_without_a_license_file_records_not_present(monkeypatch) -> None:
    _serve(
        monkeypatch,
        {
            "": _listing(("LICENSE", "dir")),
            "LICENSE": _listing(("NOTICE", "file")),
            "AGENTS.md": 404,
            "CONTRIBUTING.md": 404,
        },
    )

    assert _build()["license_document"] == {"status": "not_present", "path": "LICENSE"}


def test_directory_build_props_is_fetched_for_net_and_recorded_as_text(monkeypatch) -> None:
    props = _fixture("slides_net_Directory.Build.props")
    requested = _serve(
        monkeypatch,
        {
            "": _listing(("directory.build.props", "file")),
            "directory.build.props": _file_body("directory.build.props", props),
            "AGENTS.md": 404,
            "CONTRIBUTING.md": 404,
        },
    )

    sidecar = _build(platform="net")

    assert sidecar["build_props_text"] == props
    assert "directory.build.props" in requested


@pytest.mark.parametrize("platform", ["python", "go", "cpp", "java", "typescript", "rust"])
def test_directory_build_props_is_never_fetched_for_a_non_dotnet_platform(monkeypatch, platform: str) -> None:
    requested = _serve(
        monkeypatch,
        {"": _listing(("Directory.Build.props", "file")), "AGENTS.md": 404, "CONTRIBUTING.md": 404},
    )

    sidecar = _build(platform=platform)

    assert sidecar["build_props_text"] is None
    assert "Directory.Build.props" not in requested


def test_a_dotnet_repository_without_build_props_records_none(monkeypatch) -> None:
    _serve(monkeypatch, {"": _listing(), "AGENTS.md": 404, "CONTRIBUTING.md": 404})

    assert _build(platform="net")["build_props_text"] is None


# --- the sidecar round trip, an old sidecar, and --force --------------------------------------


def _old_sidecar_dict() -> dict:
    return {
        "manifest_text": "module example.org/x\n\ngo 1.24\n",
        "platform": "go",
        "contributing": {"status": "not_present", "path": "CONTRIBUTING.md"},
        "agent_guidance": {"status": "not_present", "path": "AGENTS.md"},
        "source_repository": _REPO,
        "source_ref": _REF,
    }


def test_an_old_sidecar_without_the_new_keys_loads_with_none(tmp_path: Path) -> None:
    path = tmp_path / "old.json"
    path.write_text(json.dumps(_old_sidecar_dict()), encoding="utf-8")

    inputs = fetch_product_reference.load_manifest_sidecar(path)

    assert inputs is not None
    assert inputs.license_document is None
    assert inputs.build_props_text is None
    assert inputs.source_repository == _REPO


def test_the_new_keys_round_trip_through_the_sidecar(tmp_path: Path) -> None:
    sidecar = _old_sidecar_dict()
    sidecar["license_document"] = {
        "status": "present",
        "path": "LICENSE.txt",
        "sha": "6" * 40,
        "size": 4,
        "content": "MIT\n",
    }
    sidecar["build_props_text"] = "<Project />"
    path = tmp_path / "new.json"
    path.write_text(json.dumps(sidecar), encoding="utf-8")

    inputs = fetch_product_reference.load_manifest_sidecar(path)

    assert inputs is not None
    assert inputs.license_document == DocumentPresent(
        path="LICENSE.txt", sha="6" * 40, size=4, content="MIT\n"
    )
    assert inputs.build_props_text == "<Project />"


def test_a_not_present_license_document_loads_as_not_present(tmp_path: Path) -> None:
    sidecar = _old_sidecar_dict()
    sidecar["license_document"] = {"status": "not_present", "path": "LICENSE"}
    sidecar["build_props_text"] = None
    path = tmp_path / "absent.json"
    path.write_text(json.dumps(sidecar), encoding="utf-8")

    inputs = fetch_product_reference.load_manifest_sidecar(path)

    assert inputs is not None
    assert inputs.license_document == DocumentNotPresent(path="LICENSE")
    assert inputs.build_props_text is None


def _run_main(monkeypatch, output: Path, *extra: str) -> None:
    argv = [
        "fetch_product_reference.py",
        "--repository",
        _REPO,
        "--platform",
        "python",
        "--ref",
        _REF,
        "--output",
        str(output),
        *extra,
    ]
    monkeypatch.setattr(sys, "argv", argv)
    fetch_product_reference.main()


def test_an_unchanged_pin_is_skipped_without_force_and_refetched_with_it(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    output = tmp_path / "product_reference.json"
    output.write_text(json.dumps(_old_sidecar_dict()), encoding="utf-8")
    requested = _serve(
        monkeypatch,
        {
            "": _listing(("LICENSE", "file"), ("Agents.md", "file")),
            "LICENSE": _file_body("LICENSE", _fixture("LICENSE_mit.txt")),
            "Agents.md": _file_body("Agents.md", "# Guidance\n"),
            "CONTRIBUTING.md": 404,
        },
    )

    _run_main(monkeypatch, output)

    assert requested == []
    assert "skip" in capsys.readouterr().out.lower()
    assert "license_document" not in json.loads(output.read_text(encoding="utf-8"))

    _run_main(monkeypatch, output, "--force")

    written = json.loads(output.read_text(encoding="utf-8"))
    assert requested[0] == ""
    assert written["license_document"]["status"] == "present"
    assert written["agent_guidance"]["path"] == "Agents.md"
    assert written["source_repository"] == _REPO
    assert written["source_ref"] == _REF
    assert "wrote product reference inputs" in capsys.readouterr().out


def test_force_on_a_changed_pin_behaves_like_a_normal_fetch(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "product_reference.json"
    stale = _old_sidecar_dict()
    stale["source_ref"] = "d" * 40
    output.write_text(json.dumps(stale), encoding="utf-8")
    _serve(monkeypatch, {"": _listing(), "AGENTS.md": 404, "CONTRIBUTING.md": 404})

    _run_main(monkeypatch, output, "--force")

    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["source_ref"] == _REF
    assert written["license_document"] == {"status": "not_present", "path": "LICENSE"}


# --- G3/TC-561: the README, the manifest path and the CLAUDE.md fallback ---------------------------------------


def test_the_readme_is_recorded_whole_whatever_its_case_and_extension(monkeypatch) -> None:
    body = "# Demo\n\n## Installation\n\npip install demo\n"
    _serve(
        monkeypatch,
        {
            "": _listing(("Readme.MD", "file"), ("docs", "dir")),
            "Readme.MD": _file_body("Readme.MD", body),
            "AGENTS.md": 404,
            "CONTRIBUTING.md": 404,
        },
    )

    sidecar = _build()

    assert sidecar["readme_document"]["status"] == "present"
    assert sidecar["readme_document"]["path"] == "Readme.MD"
    assert sidecar["readme_document"]["content"] == body


def test_a_repository_without_a_readme_records_none_and_requests_none(monkeypatch) -> None:
    requested = _serve(monkeypatch, {"": _listing(("src", "dir")), "AGENTS.md": 404, "CONTRIBUTING.md": 404})

    sidecar = _build()

    assert sidecar["readme_document"] is None
    assert not [path for path in requested if path.lower().startswith("readme")]


def test_the_manifest_path_is_recorded_as_given(monkeypatch) -> None:
    _serve(monkeypatch, {"": _listing(), "AGENTS.md": 404, "CONTRIBUTING.md": 404})
    monkeypatch.setattr(
        fetch_product_reference,
        "fetch_manifest_file",
        lambda repository, path, ref=None: f"manifest of {path}",
    )

    sidecar = fetch_product_reference.build_sidecar(
        repository=_REPO,
        platform="cpp",
        manifest_path="Lib.Cpp/CMakeLists.txt",
        contributing_path="CONTRIBUTING.md",
        agent_guidance_path="AGENTS.md",
        ref=_REF,
    )

    assert sidecar["manifest_path"] == "Lib.Cpp/CMakeLists.txt"
    assert sidecar["manifest_text"] == "manifest of Lib.Cpp/CMakeLists.txt"
    assert _build()["manifest_path"] is None


def test_claude_md_is_the_guidance_when_the_repository_has_no_agents_md(monkeypatch) -> None:
    _serve(
        monkeypatch,
        {
            "": _listing(("CLAUDE.md", "file")),
            "AGENTS.md": 404,
            "CLAUDE.md": _file_body("CLAUDE.md", "# CLAUDE.md\n"),
            "CONTRIBUTING.md": 404,
        },
    )

    sidecar = _build()

    assert sidecar["agent_guidance"]["status"] == "present"
    assert sidecar["agent_guidance"]["path"] == "CLAUDE.md"


def test_agents_md_wins_over_claude_md(monkeypatch) -> None:
    requested = _serve(
        monkeypatch,
        {
            "": _listing(("CLAUDE.md", "file"), ("AGENTS.md", "file")),
            "AGENTS.md": _file_body("AGENTS.md", "# agents\n"),
            "CONTRIBUTING.md": 404,
        },
    )

    sidecar = _build()

    assert sidecar["agent_guidance"]["path"] == "AGENTS.md"
    assert "CLAUDE.md" not in requested


def test_neither_guidance_file_is_an_absence_that_names_agents_md(monkeypatch) -> None:
    _serve(monkeypatch, {"": _listing(), "AGENTS.md": 404, "CONTRIBUTING.md": 404})

    assert _build()["agent_guidance"] == {"status": "not_present", "path": "AGENTS.md"}
