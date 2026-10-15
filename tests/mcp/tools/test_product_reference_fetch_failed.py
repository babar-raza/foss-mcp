"""G3/TC-507: a sidecar that could not be fetched is a transient failure, never an absence.

``get_product_reference`` answers every sidecar-derived section of a ``fetch_failed`` product with one
distinct NotAvailable reason; ``formats`` and ``limitations`` never depend on a sidecar and keep theirs.
``list_recent_changes`` raises ``SidecarFetchFailedError`` for a failed releases fetch rather than returning
an empty list. A good sidecar and the absent-sidecar default are unchanged. Offline.
"""

from __future__ import annotations

import pytest

from foss_mcp.extraction.github_release_reader import Release
from foss_mcp.extraction.repo_native_reader import DocumentNotPresent, DocumentPresent
from foss_mcp.mcp.sidecar_state import (
    FETCH_FAILED_REASON,
    RELEASES_FETCH_FAILED_MESSAGE,
    SIDECAR_FETCH_FAILED,
    SIDECAR_NOT_PRESENT,
    SIDECAR_PRESENT,
    SidecarFetchFailedError,
)
from foss_mcp.mcp.tools.get_product_reference import (
    SECTIONS,
    NotAvailable,
    ProductReferenceInputs,
    ReferenceContent,
    get_product_reference,
)
from foss_mcp.mcp.tools.list_recent_changes import ChangeEntry, list_recent_changes

SIDECAR_SECTIONS = ("install", "compatibility", "license", "support", "contributing", "agent_guidance")
GO_MOD = "module github.com/example/pdf-for-go\n\ngo 1.24\n"
FAILED = ProductReferenceInputs(platform="go", sidecar_state=SIDECAR_FETCH_FAILED)
RELEASES = (
    Release(tag_name="v1.1.0", body="Real notes", richness="detailed"),
    Release(tag_name="v1.0.0", body="", richness="none"),
)


@pytest.mark.parametrize("section", SIDECAR_SECTIONS)
def test_every_sidecar_section_says_could_not_be_fetched(section: str) -> None:
    answer = get_product_reference(FAILED, section)  # type: ignore[arg-type]
    assert isinstance(answer, NotAvailable)
    assert answer.section == section
    assert answer.reason == FETCH_FAILED_REASON
    assert "could not be fetched" in answer.reason


def test_formats_and_limitations_keep_their_no_data_source_reason() -> None:
    for section in ("formats", "limitations"):
        answer = get_product_reference(FAILED, section)  # type: ignore[arg-type]
        assert answer == NotAvailable(section, f"{section} has no data source in this project yet")  # type: ignore[arg-type]


@pytest.mark.parametrize("section", SECTIONS)
def test_fetch_failed_never_says_not_present_in_this_repository_or_no_packaging_manifest(
    section: str,
) -> None:
    answer = get_product_reference(FAILED, section)
    assert isinstance(answer, NotAvailable)
    assert "is not present in this repository" not in answer.reason
    assert "no packaging manifest was provided" not in answer.reason


def test_the_reason_says_retry_later_and_not_a_statement_about_the_repository() -> None:
    answer = get_product_reference(FAILED, "install")
    assert isinstance(answer, NotAvailable)
    assert "retry later" in answer.reason
    assert "this is not a statement that the repository lacks it" in answer.reason


def test_fetched_at_is_appended_when_known() -> None:
    inputs = ProductReferenceInputs(
        platform="go", sidecar_state=SIDECAR_FETCH_FAILED, sidecar_fetched_at="2026-10-15T09:30:00Z"
    )
    answer = get_product_reference(inputs, "license")
    assert isinstance(answer, NotAvailable)
    assert answer.reason == f"{FETCH_FAILED_REASON} (last attempt 2026-10-15T09:30:00Z)"


def test_fetched_at_is_not_appended_to_formats_and_limitations() -> None:
    inputs = ProductReferenceInputs(
        platform="go", sidecar_state=SIDECAR_FETCH_FAILED, sidecar_fetched_at="2026-10-15T09:30:00Z"
    )
    answer = get_product_reference(inputs, "limitations")
    assert isinstance(answer, NotAvailable)
    assert "last attempt" not in answer.reason


def test_default_inputs_and_a_present_sidecar_are_unchanged() -> None:
    assert ProductReferenceInputs().sidecar_state == SIDECAR_PRESENT
    assert ProductReferenceInputs().sidecar_fetched_at is None
    default_install = get_product_reference(ProductReferenceInputs(), "install")
    assert default_install == NotAvailable("install", "no packaging manifest was provided")
    present = ProductReferenceInputs(
        manifest_text=GO_MOD,
        platform="go",
        contributing=DocumentNotPresent(path="CONTRIBUTING.md"),
        agent_guidance=DocumentPresent(path="AGENTS.md", sha="a" * 40, size=5, content="hello"),
        sidecar_state=SIDECAR_PRESENT,
        sidecar_fetched_at="2026-10-15T09:30:00Z",
    )
    install = get_product_reference(present, "install")
    assert isinstance(install, ReferenceContent)
    assert install.text == "go get github.com/example/pdf-for-go"
    assert get_product_reference(present, "compatibility") == ReferenceContent("compatibility", "1.24")
    assert get_product_reference(present, "contributing") == NotAvailable(
        "contributing", "contributing is not present in this repository"
    )
    guidance = get_product_reference(present, "agent_guidance")
    assert isinstance(guidance, ReferenceContent)
    assert guidance.text == "hello"
    assert get_product_reference(present, "limitations") == NotAvailable(
        "limitations", "limitations has no data source in this project yet"
    )


def test_the_absent_sidecar_state_name_is_distinct_from_the_other_two() -> None:
    assert len({SIDECAR_PRESENT, SIDECAR_NOT_PRESENT, SIDECAR_FETCH_FAILED}) == 3


def test_list_recent_changes_raises_sidecar_fetch_failed_error_on_the_failed_state() -> None:
    with pytest.raises(SidecarFetchFailedError) as raised:
        list_recent_changes((), state=SIDECAR_FETCH_FAILED)
    assert str(raised.value) == RELEASES_FETCH_FAILED_MESSAGE
    with pytest.raises(SidecarFetchFailedError):
        list_recent_changes(RELEASES, limit=1, state=SIDECAR_FETCH_FAILED)


def test_list_recent_changes_default_state_is_unchanged() -> None:
    entries = list_recent_changes(RELEASES, limit=1)
    assert entries == [ChangeEntry(tag_name="v1.1.0", richness="detailed", body="Real notes")]
    assert list_recent_changes(RELEASES) == list_recent_changes(RELEASES, state=SIDECAR_PRESENT)
    assert list_recent_changes(RELEASES, state=SIDECAR_NOT_PRESENT) == list_recent_changes(RELEASES)


def test_an_empty_present_list_is_an_empty_list() -> None:
    assert list_recent_changes((), state=SIDECAR_PRESENT) == []
    assert list_recent_changes((), state=SIDECAR_NOT_PRESENT) == []
    assert list_recent_changes(()) == []


def test_sidecar_fetch_failed_error_is_not_a_value_error() -> None:
    assert issubclass(SidecarFetchFailedError, RuntimeError)
    assert not issubclass(SidecarFetchFailedError, ValueError)
