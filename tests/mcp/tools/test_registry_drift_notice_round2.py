"""G3/TC-816 (REQ-G3-012): a registry-served version that differs from the indexed one is never silent.

Round-2 rows RD2-091 / RD2-083 / RD2-084 / RD2-170: ``report_index_freshness`` said ``stale`` (or ``fresh``
by git ancestry) while ``drift`` stayed ``unknown`` / said nothing about the registry, and
``get_product_reference``'s compatibility / formats / limitations / install notices never mentioned that
the registry serves a version other than the manifest's.

Offline and synthetic: every input is built here and handed straight to the code path under test, so none of
this depends on the committed ``infra/demo-manifests`` generations having been regenerated.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from foss_mcp.extraction.github_release_reader import DriftRecord, Release
from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_product_reference import (
    DocumentPresent,
    ProductReferenceInputs,
    ReferenceContent,
    Section,
    _drift_notice,
    _registry_version_notice,
    get_product_reference,
)
from foss_mcp.mcp.tools.readme_sections import ReadmeSection, ReadmeSections
from foss_mcp.mcp.tools.release_alignment import parse_release_version
from foss_mcp.mcp.tools.report_index_freshness import FreshnessReport, report_index_freshness
from tests.mcp.tools.test_freshness_provenance import SYMBOL_COMMIT, _publish, _symbol

# The synthetic generation ``_publish`` builds is pdf/net; the manifest/sidecar inputs below are Python ones.
SCOPE = Scope(family="pdf", platform="net")
COMMIT = SYMBOL_COMMIT
REPOSITORY = "aspose-cells-foss/Aspose.Cells-FOSS-for-Python"

_RATE_LIMITED = DriftRecord(indexed_commit=COMMIT, tag="V26.10.0", error="RateLimited")
_AHEAD_OF_26_7 = DriftRecord(
    indexed_commit=COMMIT, tag="26.7.0", tag_commit="d" * 40, ahead_by=5, behind_by=0, status="ahead"
)


def _manifest(version: str | None) -> str:
    version_line = f'version = "{version}"\n' if version else 'dynamic = ["version"]\n'
    return f'[project]\nname = "demo"\nrequires-python = ">=3.7"\n{version_line}'


def _inputs(
    manifest_version: str | None,
    *,
    served: str | None = None,
    verified_served: str | None = None,
    drift: DriftRecord | None = None,
) -> ProductReferenceInputs:
    return ProductReferenceInputs(
        manifest_text=_manifest(manifest_version),
        platform="python",
        source_repository=REPOSITORY,
        source_ref=COMMIT,
        drift=drift,
        install_served_version=served,
        install_verified_served_version=verified_served,
    )


def _release(tag: str, drift: DriftRecord | None = None) -> Release:
    return Release(tag, "", "none", drift=drift)


def _freshness(
    tmp_path: Path,
    inputs: ProductReferenceInputs,
    releases: list[Release],
    registry: str | None,
) -> FreshnessReport:
    # One generation per test, reused by every call in it, so two reports of the same inputs can be compared
    # whole (``indexed_generation_id`` included).
    root = tmp_path / "m"
    store = (
        GenerationManifestStore(root)
        if root.exists()
        else _publish(root, [_symbol("Alpha", COMMIT), _symbol("Beta", COMMIT)])
    )
    return report_index_freshness(
        store,
        SCOPE,
        "self_extracted",
        None,
        product_reference=inputs,
        releases=releases,
        registry_served_version=parse_release_version(registry) if registry else None,
    )


# --- report_index_freshness: the latest_release_version branch (RD2-091) -------------------------


def test_latest_release_version_branch_states_a_newer_registry_version(tmp_path: Path) -> None:
    report = _freshness(
        tmp_path,
        _inputs("26.7.0", served="26.10.0", drift=_RATE_LIMITED),
        [_release("V26.10.0", _RATE_LIMITED), _release("V26.7.0")],
        "26.10.0",
    )

    # The verdict is exactly what it was.
    assert (report.freshness_state, report.freshness_basis, report.stale) == (
        "stale",
        "latest_release_version",
        True,
    )
    assert report.reason == (
        "latest release V26.10.0 is newer than the indexed package version 26.7.0; "
        "no current_source_commit was given"
    )
    # And the drift fields now carry the registry's version instead of "unknown" / None.
    assert report.drift == "package_ahead_of_index"
    assert report.package_ahead_of_index is True
    assert report.index_ahead_of_package is False
    assert report.published_version == "26.10.0"
    assert report.drift_message is not None
    assert "26.10.0" in report.drift_message and "26.7.0" in report.drift_message


def test_latest_release_version_branch_is_unchanged_when_the_registry_equals_the_manifest(
    tmp_path: Path,
) -> None:
    inputs = _inputs("26.7.0", served="26.7.0", drift=_RATE_LIMITED)
    releases = [_release("V26.10.0", _RATE_LIMITED), _release("V26.7.0")]

    with_registry = _freshness(tmp_path, inputs, releases, "26.7.0")
    without_registry = _freshness(tmp_path, inputs, releases, None)

    assert with_registry.drift == "unknown"
    assert with_registry.drift_message is None
    assert with_registry == without_registry


def test_a_placeholder_manifest_major_never_reaches_the_latest_release_version_drift(tmp_path: Path) -> None:
    # TC-657's guard: a manifest declaring 0.1.0 next to a registry's 26.10.0 is not comparable, so no
    # drift is built from that pair - the verdict is TC-712's major-mismatch "unknown" and drift stays unknown.
    inputs = _inputs("0.1.0", served="26.10.0", drift=_RATE_LIMITED)
    releases = [_release("V26.10.0", _RATE_LIMITED)]

    report = _freshness(tmp_path, inputs, releases, "26.10.0")

    assert report.freshness_state == "unknown"
    assert report.freshness_basis == "registry_served_version_major_mismatch"
    assert report.drift == "unknown"
    assert report.drift_message is None


def test_latest_release_version_branch_does_not_rewrite_the_drift_for_an_older_registry(
    tmp_path: Path,
) -> None:
    # Registry 26.5.0 is OLDER than the manifest's 26.7.0 while a newer release exists: the stale verdict
    # is about the release, so the drift fields stay what they were rather than claim an index-ahead state.
    inputs = _inputs("26.7.0", served="26.5.0", drift=_RATE_LIMITED)
    releases = [_release("V26.10.0", _RATE_LIMITED)]

    older = _freshness(tmp_path, inputs, releases, "26.5.0")
    absent = _freshness(tmp_path, inputs, releases, None)

    assert older == absent


# --- report_index_freshness: the git_ancestry branch (RD2-170) -----------------------------------


def test_git_ancestry_branch_says_the_registry_is_ahead_of_every_tag(tmp_path: Path) -> None:
    # No manifest version at all (a dynamic version), as for page/python.
    report = _freshness(
        tmp_path,
        _inputs(None, served="26.9.0", drift=_AHEAD_OF_26_7),
        [_release("26.7.0", _AHEAD_OF_26_7), _release("26.5.0")],
        "26.9.0",
    )

    assert (report.freshness_state, report.freshness_basis, report.stale) == ("fresh", "git_ancestry", False)
    assert report.drift == "index_ahead_of_package"
    sentence = (
        "The package registry serves version 26.9.0, which has no release tag and cannot be compared "
        "to the indexed commit."
    )
    assert report.drift_message is not None and report.drift_message.endswith(sentence)
    assert sentence in report.reason
    assert "Nothing newer than the index was found." not in report.reason
    assert report.drift_message in report.warnings
    # The ancestry facts themselves are untouched.
    assert report.drift_commit_distance == 5
    assert report.published_version == "26.7.0"


def test_git_ancestry_branch_is_unchanged_when_the_registry_serves_the_latest_tag(tmp_path: Path) -> None:
    inputs = _inputs(None, served="26.7.0", drift=_AHEAD_OF_26_7)
    releases = [_release("26.7.0", _AHEAD_OF_26_7)]

    served = _freshness(tmp_path, inputs, releases, "26.7.0")
    absent = _freshness(tmp_path, inputs, releases, None)

    assert served == absent
    assert served.reason.endswith("Nothing newer than the index was found.")


def test_git_ancestry_branch_is_unchanged_when_the_registry_is_older_than_the_latest_tag(
    tmp_path: Path,
) -> None:
    inputs = _inputs(None, served="26.5.0", drift=_AHEAD_OF_26_7)
    releases = [_release("26.7.0", _AHEAD_OF_26_7)]

    older = _freshness(tmp_path, inputs, releases, "26.5.0")
    absent = _freshness(tmp_path, inputs, releases, None)

    assert older == absent


def test_git_ancestry_branch_is_unchanged_when_the_registry_equals_the_manifest_version(
    tmp_path: Path,
) -> None:
    # The registry serves exactly the manifest's version: nothing differs, so nothing is added even though
    # that version happens to carry no tag.
    inputs = _inputs("26.9.0", served="26.9.0", drift=_AHEAD_OF_26_7)
    releases = [_release("26.7.0", _AHEAD_OF_26_7)]

    same = _freshness(tmp_path, inputs, releases, "26.9.0")
    absent = _freshness(tmp_path, inputs, releases, None)

    assert same == absent


def test_git_ancestry_branch_is_unchanged_for_a_placeholder_manifest_major(tmp_path: Path) -> None:
    inputs = _inputs("0.1.0", served="26.9.0", drift=_AHEAD_OF_26_7)
    releases = [_release("26.7.0", _AHEAD_OF_26_7)]

    guarded = _freshness(tmp_path, inputs, releases, "26.9.0")
    absent = _freshness(tmp_path, inputs, releases, None)

    assert guarded == absent


# --- get_product_reference: compatibility / formats / limitations / install (RD2-083/084/091) -----


def _readme() -> ReadmeSections:
    return ReadmeSections(
        limitations=ReadmeSection(
            title="Scope and Limitations",
            text="- Only .xlsx is supported for native load/save.",
            commit=COMMIT,
            ordered=True,
            parts=1,
        ),
        install=ReadmeSection(
            title="Installation", text="Requires Python 3.9.", commit=COMMIT, ordered=True, parts=1
        ),
        capabilities=ReadmeSection(
            title="Key Capabilities",
            text="- Load and save .xlsx and export CSV, JSON and Markdown.",
            commit=COMMIT,
            ordered=True,
            parts=1,
        ),
    )


def _sentence(inputs: ProductReferenceInputs) -> str:
    sentence = _drift_notice(inputs, "pypi")
    assert sentence is not None
    return sentence


def _registry_sentence(inputs: ProductReferenceInputs) -> str:
    sentence = _registry_version_notice(inputs)
    assert sentence is not None
    return sentence


def _notice(inputs: ProductReferenceInputs, section: Section) -> str | None:
    result = get_product_reference(inputs, section, readme=_readme())
    assert isinstance(result, ReferenceContent), result
    return result.notice


def test_every_answer_states_a_registry_version_that_differs_from_the_manifest() -> None:
    inputs = _inputs("26.7.0", served="26.10.0", drift=_RATE_LIMITED)
    sentence = _sentence(inputs)
    assert "26.10.0" in sentence and "26.7.0" in sentence

    for section in ("compatibility", "formats", "limitations", "install"):
        notice = _notice(inputs, section)
        assert notice is not None and sentence in notice, section


def test_the_answers_texts_are_unchanged_by_the_notice() -> None:
    differing = _inputs("26.7.0", served="26.10.0", drift=_RATE_LIMITED)
    same = _inputs("26.7.0", served="26.7.0", drift=_RATE_LIMITED)

    for section in ("compatibility", "formats", "limitations", "install"):
        with_notice = get_product_reference(differing, section, readme=_readme())
        without = get_product_reference(same, section, readme=_readme())
        assert isinstance(with_notice, ReferenceContent) and isinstance(without, ReferenceContent)
        assert with_notice.text == without.text, section


def test_answers_are_byte_identical_when_the_registry_serves_the_manifest_version() -> None:
    same = _inputs("26.7.0", served="26.7.0", drift=_RATE_LIMITED)
    never_checked = _inputs("26.7.0", served=None, drift=_RATE_LIMITED)

    for section in ("compatibility", "formats", "limitations", "install"):
        assert get_product_reference(same, section, readme=_readme()) == get_product_reference(
            never_checked,
            section,
            readme=_readme(),
        ), section
    # Versions are compared padded with trailing zeros: "26.7" and "26.7.0" are the same version.
    padded = _inputs("26.7", served="26.7.0", drift=_RATE_LIMITED)
    assert _drift_notice(padded, "pypi") is None


def test_the_registry_verification_sidecars_served_version_is_used_when_the_other_is_absent() -> None:
    inputs = _inputs("26.7.0", served=None, verified_served="26.10.0", drift=_RATE_LIMITED)

    notice = _notice(inputs, "compatibility")

    assert notice is not None and "26.10.0" in notice and "26.7.0" in notice


_MANIFESTS_BY_PLATFORM = {
    "python": '[project]\nname = "demo"\nversion = "26.7.0"\nrequires-python = ">=3.9"\n',
    "dotnet-net": (
        "<Project><PropertyGroup><TargetFramework>net8.0</TargetFramework>"
        "<PackageId>Demo</PackageId><Version>26.7.0</Version></PropertyGroup></Project>"
    ),
    "java": (
        "<project><groupId>com.demo</groupId><artifactId>demo</artifactId><version>26.7.0</version>"
        "<properties><maven.compiler.release>17</maven.compiler.release></properties></project>"
    ),
    "typescript": '{"name": "demo", "version": "26.7.0", "engines": {"node": ">=18"}}',
    "rust": '[package]\nname = "demo"\nversion = "26.7.0"\nrust-version = "1.70"\n',
}


@pytest.mark.parametrize("platform", sorted(_MANIFESTS_BY_PLATFORM))
def test_the_sentence_reaches_every_section_on_every_platform_with_a_manifest_version(platform: str) -> None:
    manifest = _MANIFESTS_BY_PLATFORM[platform]
    inputs = ProductReferenceInputs(
        manifest_text=manifest,
        platform="net" if platform == "dotnet-net" else platform,
        source_repository=REPOSITORY,
        source_ref=COMMIT,
        install_served_version="26.10.0",
    )
    sentence = _registry_sentence(inputs)
    assert "26.10.0" in sentence and "26.7.0" in sentence

    for section in ("compatibility", "formats", "limitations", "install"):
        notice = _notice(inputs, section)
        assert notice is not None and sentence in notice, (platform, section)


def test_a_go_manifest_declares_no_version_so_a_registry_version_alone_adds_nothing() -> None:
    inputs = ProductReferenceInputs(
        manifest_text="module example.com/demo\n\ngo 1.22\n",
        platform="go",
        source_repository=REPOSITORY,
        source_ref=COMMIT,
        install_served_version="v26.7.1",
    )

    assert _registry_version_notice(inputs) is None


def test_a_drift_record_tag_that_carries_no_version_adds_nothing() -> None:
    inputs = _inputs(
        None, served="26.9.0", drift=DriftRecord(indexed_commit=COMMIT, tag="latest", ahead_by=1, behind_by=0)
    )

    assert _registry_version_notice(inputs) is None


def test_a_placeholder_manifest_version_never_yields_the_sentence() -> None:
    # TC-657's guard: 0.1.0 next to a registry's 26.7.0 is not a version the registry can be set against.
    inputs = _inputs("0.1.0", served="26.7.0", drift=_RATE_LIMITED)

    assert _drift_notice(inputs, "pypi") is None
    for section in ("compatibility", "formats", "limitations"):
        notice = _notice(inputs, section)
        assert notice is None or "registry serves" not in notice, section


def test_a_manifest_with_no_version_says_the_registry_version_has_no_tag() -> None:
    inputs = _inputs(None, served="26.9.0", drift=_AHEAD_OF_26_7)

    sentence = _sentence(inputs)

    assert "26.9.0" in sentence and "26.7.0" in sentence and "no tag" in sentence
    # It rides next to the existing ancestry notice (compatibility reads it through _drift_notice).
    compatibility = _notice(inputs, "compatibility")
    assert compatibility is not None
    assert "5 commits ahead" in compatibility and sentence in compatibility
    assert _registry_sentence(inputs) in (_notice(inputs, "formats") or "")


def test_a_manifest_with_no_version_and_a_registry_at_or_below_the_latest_tag_adds_nothing() -> None:
    for served in ("26.7.0", "26.5.0"):
        inputs = _inputs(None, served=served, drift=_AHEAD_OF_26_7)
        assert "registry serves" not in (_notice(inputs, "compatibility") or ""), served
    no_record = _inputs(None, served="26.9.0", drift=None)
    assert _drift_notice(no_record, "pypi") is None


def test_an_answer_that_is_not_content_is_not_given_a_notice() -> None:
    inputs = ProductReferenceInputs(
        manifest_text=_manifest("26.7.0"),
        platform="python",
        source_repository=REPOSITORY,
        source_ref=COMMIT,
        install_served_version="26.10.0",
        readme_document=DocumentPresent(path="README.md", sha="0" * 40, size=7, content="# Demo\n"),
    )

    # An empty README holds no formats/limitations section: the explicit absence is served as before.
    for section in ("formats", "limitations"):
        result = get_product_reference(inputs, section, readme=ReadmeSections(None, None, None))
        assert not isinstance(result, ReferenceContent), section
