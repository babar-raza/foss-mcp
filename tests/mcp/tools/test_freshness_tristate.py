"""G3/TC-519: report_index_freshness distinguishes unknown from stale and says how it decided.

Offline. Synthetic generations are published through the real ``publish_generation`` with the helpers
``tests/mcp/tools/test_freshness_provenance.py`` builds them with.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from foss_mcp.extraction.github_release_reader import Release
from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_product_reference import ProductReferenceInputs
from foss_mcp.mcp.tools.report_index_freshness import (
    CommitCount,
    FreshnessReport,
    read_indexed_commits,
    report_index_freshness,
)
from tests.mcp.tools.test_freshness_provenance import (
    PINNED_COMMIT,
    SYMBOL_COMMIT,
    _doc,
    _mixed_texts,
    _publish,
    _symbol,
)

SCOPE = Scope(family="pdf", platform="net")
LEGACY_UNKNOWN_REASON = "current source commit is unknown; staleness cannot be ruled out"
_OTHER_COMMIT = "c" * 40
_PYTHON_26_7 = ProductReferenceInputs(
    manifest_text='[project]\nname = "x"\nversion = "26.7.0"\n',
    platform="python",
    source_repository="aspose-cells-foss/Aspose.Cells-FOSS-for-Python",
)


def _single(tmp_path: Path) -> GenerationManifestStore:
    return _publish(tmp_path / "m", [_symbol("Alpha", SYMBOL_COMMIT), _symbol("Beta", SYMBOL_COMMIT)])


def _report(store: GenerationManifestStore, current: str | None, **kwargs: object) -> FreshnessReport:
    return report_index_freshness(store, SCOPE, "self_extracted", current, **kwargs)  # type: ignore[arg-type]


def _release(tag: str, commit: str | None = None) -> Release:
    return Release(tag, "", "none", target_commitish=commit)


def test_caller_commit_match_is_fresh_with_basis_caller_commit(tmp_path: Path) -> None:
    report = _report(_single(tmp_path), SYMBOL_COMMIT)

    assert report.freshness_state == "fresh"
    assert report.freshness_basis == "caller_commit"
    assert report.stale is False
    assert report.reason == "index matches the current source commit"
    assert report.compared_against_commit is None


def test_caller_commit_mismatch_is_stale(tmp_path: Path) -> None:
    report = _report(_single(tmp_path), _OTHER_COMMIT)

    assert report.freshness_state == "stale"
    assert report.freshness_basis == "caller_commit"
    assert report.stale is True
    assert report.reason == f"indexed {SYMBOL_COMMIT}, current source is {_OTHER_COMMIT}"


def test_caller_commit_beats_the_latest_release(tmp_path: Path) -> None:
    store = _single(tmp_path)
    report = _report(store, SYMBOL_COMMIT, releases=[_release("v9.0.0", _OTHER_COMMIT)])

    assert report.freshness_state == "fresh"
    assert report.freshness_basis == "caller_commit"
    assert report.compared_against_commit is None


def test_omitted_commit_without_release_data_keeps_the_legacy_reason_and_is_unknown_and_stale_true(
    tmp_path: Path,
) -> None:
    report = _report(_single(tmp_path), None)

    assert report.reason == LEGACY_UNKNOWN_REASON
    assert report.freshness_state == "unknown"
    assert report.freshness_basis == "none"
    assert report.stale is True
    assert report.latest_release_tag is None
    assert report.latest_release_in_index is None


def test_omitted_commit_on_a_mixed_index_keeps_the_legacy_mixed_reason(tmp_path: Path) -> None:
    store = _publish(tmp_path / "m", _mixed_texts())
    report = _report(store, None)

    assert report.reason.startswith("index is mixed: ")
    assert report.reason.endswith("; " + LEGACY_UNKNOWN_REASON)
    assert report.freshness_state == "unknown"
    assert report.stale is True


def test_omitted_commit_uses_the_latest_tag_commit_when_known(tmp_path: Path) -> None:
    store = _single(tmp_path)

    fresh = _report(store, None, releases=[_release("v2.0.0", SYMBOL_COMMIT), _release("v1.0.0")])
    stale = _report(store, None, releases=[_release("v2.0.0", _OTHER_COMMIT)])

    assert fresh.freshness_state == "fresh"
    assert fresh.freshness_basis == "latest_release_tag_commit"
    assert fresh.stale is False
    assert fresh.compared_against_commit == SYMBOL_COMMIT
    assert fresh.current_source_commit is None
    assert fresh.reason == (
        "index matches the current source commit; compared against the latest release tag v2.0.0 "
        f"({SYMBOL_COMMIT[:12]}) because no current_source_commit was given"
    )
    assert stale.freshness_state == "stale"
    assert stale.freshness_basis == "latest_release_tag_commit"
    assert stale.compared_against_commit == _OTHER_COMMIT
    assert stale.reason.endswith(
        f"; compared against the latest release tag v2.0.0 ({_OTHER_COMMIT[:12]}) because no "
        "current_source_commit was given"
    )


def test_the_latest_tag_commit_is_compared_with_every_indexed_commit(tmp_path: Path) -> None:
    store = _publish(tmp_path / "m", _mixed_texts())

    only_symbols = _report(store, None, releases=[_release("v1.0.0", SYMBOL_COMMIT)])
    assert only_symbols.freshness_state == "stale"
    assert only_symbols.reason.startswith("index is mixed: ")
    assert "matches API symbols only" in only_symbols.reason


def test_omitted_commit_with_a_newer_release_is_stale_by_version(tmp_path: Path) -> None:
    report = _report(
        _single(tmp_path),
        None,
        product_reference=_PYTHON_26_7,
        releases=[_release("V26.10.0"), _release("V26.7.0")],
    )

    assert report.freshness_state == "stale"
    assert report.freshness_basis == "latest_release_version"
    assert report.stale is True
    assert report.reason == (
        "latest release V26.10.0 is newer than the indexed package version 26.7.0; "
        "no current_source_commit was given"
    )
    assert report.latest_release_tag == "V26.10.0"
    assert report.latest_release_in_index is False
    assert report.compared_against_commit is None


def test_omitted_commit_with_the_release_in_the_index_stays_unknown(tmp_path: Path) -> None:
    report = _report(_single(tmp_path), None, product_reference=_PYTHON_26_7, releases=[_release("V26.7.0")])

    assert report.freshness_state == "unknown"
    assert report.freshness_basis == "none"
    assert report.stale is True
    assert report.reason == LEGACY_UNKNOWN_REASON
    assert report.latest_release_tag == "V26.7.0"
    assert report.latest_release_in_index is True


def test_an_unversioned_manifest_leaves_the_verdict_unknown(tmp_path: Path) -> None:
    go = ProductReferenceInputs(manifest_text="module example.com/x\n\ngo 1.21\n", platform="go")
    report = _report(_single(tmp_path), None, product_reference=go, releases=[_release("v0.11.0")])

    assert report.freshness_state == "unknown"
    assert report.latest_release_in_index is None
    assert report.reason == LEGACY_UNKNOWN_REASON


@pytest.mark.parametrize(
    ("current", "releases", "expected_state"),
    [
        (SYMBOL_COMMIT, [], "fresh"),
        (_OTHER_COMMIT, [], "stale"),
        (None, [], "unknown"),
        (None, [_release("v1", SYMBOL_COMMIT)], "fresh"),
        (None, [_release("v1", _OTHER_COMMIT)], "stale"),
        (None, [_release("v1")], "unknown"),
    ],
)
def test_the_boolean_stale_is_true_unless_fresh(
    tmp_path: Path, current: str | None, releases: list[Release], expected_state: str
) -> None:
    report = _report(_single(tmp_path), current, releases=releases)

    assert report.freshness_state == expected_state
    assert report.stale is (expected_state != "fresh")


def test_abbreviated_commit_adds_a_warning_and_a_full_one_does_not(tmp_path: Path) -> None:
    store = _single(tmp_path)

    short = _report(store, SYMBOL_COMMIT[:7])
    full = _report(store, SYMBOL_COMMIT)
    omitted = _report(store, None)

    assert short.freshness_state == "fresh"
    assert short.warnings == (
        "current_source_commit is abbreviated (7 characters): it is matched as a prefix of the "
        "indexed commit; pass the full commit for an exact comparison.",
    )
    assert full.warnings == ()
    assert omitted.warnings == ()
    assert (
        _report(store, "  " + SYMBOL_COMMIT[:12] + " ")
        .warnings[0]
        .startswith("current_source_commit is abbreviated (12 characters)")
    )
    assert _report(store, "a" * 64).warnings == ()


def test_indexed_at_source_repository_and_latest_release_are_reported(tmp_path: Path) -> None:
    store = _single(tmp_path)
    inputs = ProductReferenceInputs(source_repository="aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET")

    report = _report(store, None, product_reference=inputs, releases=[_release("v3.0.0"), _release("v2.0.0")])

    assert report.indexed_at is not None
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", report.indexed_at)
    generation_stamp = (report.indexed_generation_id or "").split("::")[-1].split("-")[0]
    assert report.indexed_at.replace("-", "").replace(":", "") == generation_stamp[:15] + "Z"
    assert report.source_repository == "aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET"
    assert report.latest_release_tag == "v3.0.0"


def test_without_product_reference_the_source_repository_is_none(tmp_path: Path) -> None:
    assert _report(_single(tmp_path), None).source_repository is None


def test_never_published_has_state_unknown(tmp_path: Path) -> None:
    report = _report(GenerationManifestStore(tmp_path / "empty"), None)

    assert report.reason == "never published"
    assert report.stale is True
    assert report.freshness_state == "unknown"
    assert report.freshness_basis == "none"
    assert report.indexed_generation_id is None
    assert report.indexed_at is None
    assert report.warnings == ()


def test_a_generation_without_a_recorded_commit_is_unknown(tmp_path: Path) -> None:
    store = _publish(tmp_path / "m", [_symbol("Alpha", None), _doc("Install", None)])

    report = _report(store, SYMBOL_COMMIT)

    assert report.reason == "indexed generation does not record its source commit"
    assert report.stale is True
    assert report.freshness_state == "unknown"


def test_existing_fields_are_unchanged(tmp_path: Path) -> None:
    store = _publish(tmp_path / "m", _mixed_texts())

    report = _report(store, PINNED_COMMIT, pinned_library_commit=PINNED_COMMIT)

    assert report.stale is True
    assert report.indexed_source_commit == SYMBOL_COMMIT
    assert report.current_source_commit == PINNED_COMMIT
    assert report.source_commits == (
        CommitCount(SYMBOL_COMMIT, 3, symbol_chunks=3),
        CommitCount(PINNED_COMMIT, 3, example_chunks=1, documentation_chunks=2),
    )
    assert report.chunks_without_commit == 0
    assert report.pinned_library_commit == PINNED_COMMIT
    assert report.pinned_matches_symbols is False
    assert report.reason.startswith("index is mixed: API symbols from ")
    assert "matches documentation and examples only" in report.reason
    assert "API symbols were not extracted at the pinned library commit" in report.reason
    assert report.example_search == "lexical-only"


def test_read_indexed_commits_lists_the_distinct_commits(tmp_path: Path) -> None:
    store = _publish(tmp_path / "m", _mixed_texts())

    assert set(read_indexed_commits(store, SCOPE)) == {SYMBOL_COMMIT, PINNED_COMMIT}
    assert len(read_indexed_commits(store, SCOPE)) == 2
    assert read_indexed_commits(GenerationManifestStore(tmp_path / "none"), SCOPE) == ()
