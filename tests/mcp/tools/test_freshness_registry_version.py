"""G3/TC-621 (REQ-G3-012): report_index_freshness's new registry_served_version verdict branch.

Offline, no network, no mocks beyond the synthetic generations the existing freshness test files
already build theirs with. This card is the pure-logic half only: the live registry fetch that
would produce a real ``registry_served_version`` is I8's separate job - here the parameter is
simply handed in, already parsed, by the test itself.
"""

from __future__ import annotations

from pathlib import Path

from foss_mcp.extraction.github_release_reader import Release
from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_product_reference import ProductReferenceInputs
from foss_mcp.mcp.tools.report_index_freshness import FreshnessReport, report_index_freshness
from tests.mcp.tools.test_freshness_provenance import SYMBOL_COMMIT, _publish, _symbol
from tests.mcp.tools.test_freshness_tristate import _OTHER_COMMIT, _PYTHON_26_7

SCOPE = Scope(family="pdf", platform="net")

# G3/TC-629: a manifest declaring a 2-segment version ("26.7"), to exercise the mixed-precision
# comparison against a 3-segment registry_served_version - the exact shape of the real bug
# (`(26, 7) < (26, 7, 0)` is True under a plain, unpadded tuple comparison, even though the two
# are the same version).
_PYTHON_26_7_SHORT = ProductReferenceInputs(
    manifest_text='[project]\nname = "x"\nversion = "26.7"\n',
    platform="python",
    source_repository="aspose-cells-foss/Aspose.Cells-FOSS-for-Python",
)


def _single(tmp_path: Path) -> GenerationManifestStore:
    return _publish(tmp_path / "m", [_symbol("Alpha", SYMBOL_COMMIT), _symbol("Beta", SYMBOL_COMMIT)])


def _report(store: GenerationManifestStore, current: str | None, **kwargs: object) -> FreshnessReport:
    return report_index_freshness(store, SCOPE, "self_extracted", current, **kwargs)  # type: ignore[arg-type]


def _release(tag: str, commit: str | None = None) -> Release:
    return Release(tag, "", "none", target_commitish=commit)


# --- (a) backward compatibility: the old signature is untouched -----------------------------


def test_old_signature_without_registry_served_version_is_unchanged(tmp_path: Path) -> None:
    store = _single(tmp_path)

    fresh = _report(store, SYMBOL_COMMIT)
    assert fresh.freshness_state == "fresh"
    assert fresh.freshness_basis == "caller_commit"

    stale = _report(store, _OTHER_COMMIT)
    assert stale.freshness_state == "stale"
    assert stale.freshness_basis == "caller_commit"

    unknown = _report(store, None)
    assert unknown.freshness_state == "unknown"
    assert unknown.freshness_basis == "none"
    assert unknown.reason == "current source commit is unknown; staleness cannot be ruled out"

    by_version = _report(
        store,
        None,
        product_reference=_PYTHON_26_7,
        releases=[_release("V26.10.0"), _release("V26.7.0")],
    )
    assert by_version.freshness_state == "stale"
    assert by_version.freshness_basis == "latest_release_version"


def test_registry_served_version_none_is_the_default_and_byte_identical(tmp_path: Path) -> None:
    store = _single(tmp_path)

    without = _report(store, None, product_reference=_PYTHON_26_7)
    with_explicit_none = _report(store, None, product_reference=_PYTHON_26_7, registry_served_version=None)

    assert without == with_explicit_none


# --- (b) registry strictly newer than the indexed version is stale --------------------------


def test_registry_served_version_newer_than_indexed_is_stale(tmp_path: Path) -> None:
    store = _single(tmp_path)

    report = _report(
        store,
        None,
        product_reference=_PYTHON_26_7,
        registry_served_version=(26, 8, 0),
    )

    assert report.freshness_state == "stale"
    assert report.freshness_basis == "registry_served_version"
    assert report.stale is True
    assert report.reason == (
        "the package registry serves version 26.8.0, newer than the indexed package version 26.7.0"
    )


# --- (c) registry equal to, or older than, the indexed version is fresh ---------------------


def test_registry_served_version_equal_to_indexed_is_fresh(tmp_path: Path) -> None:
    store = _single(tmp_path)

    report = _report(
        store,
        None,
        product_reference=_PYTHON_26_7,
        registry_served_version=(26, 7, 0),
    )

    assert report.freshness_state == "fresh"
    assert report.freshness_basis == "registry_served_version"
    assert report.stale is False
    assert report.reason == (
        "the package registry serves version 26.7.0, no newer than the indexed package version 26.7.0"
    )


def test_registry_served_version_older_than_indexed_is_fresh(tmp_path: Path) -> None:
    store = _single(tmp_path)

    report = _report(
        store,
        None,
        product_reference=_PYTHON_26_7,
        registry_served_version=(26, 6, 0),
    )

    assert report.freshness_state == "fresh"
    assert report.freshness_basis == "registry_served_version"
    assert report.stale is False
    assert report.reason == (
        "the package registry serves version 26.6.0, no newer than the indexed package version 26.7.0"
    )


# --- (c2) G3/TC-629: an equal version at mismatched precision must never read as "newer" -----


def test_registry_served_version_equal_at_higher_precision_than_indexed_is_fresh_not_stale(
    tmp_path: Path,
) -> None:
    """Regression for TC-629: indexed manifest version (26, 7), registry-served (26, 7, 0) - the
    exact same version, written at different precision. A plain, unpadded tuple comparison reads
    the shorter indexed tuple as older (`(26, 7) < (26, 7, 0)` is True in Python), which would
    falsely report "stale". Padding both to equal width before comparing must report "fresh"."""
    store = _single(tmp_path)

    report = _report(
        store,
        None,
        product_reference=_PYTHON_26_7_SHORT,
        registry_served_version=(26, 7, 0),
    )

    assert report.freshness_state == "fresh"
    assert report.freshness_basis == "registry_served_version"
    assert report.stale is False
    assert report.reason == (
        "the package registry serves version 26.7.0, no newer than the indexed package version 26.7"
    )


def test_registry_served_version_equal_at_lower_precision_than_indexed_is_fresh_not_stale(
    tmp_path: Path,
) -> None:
    """Symmetric case: indexed manifest version (26, 7, 0), registry-served (26, 7) - again the
    same version, just with the shorter tuple on the registry side this time. Must still be
    "fresh", not a false "shorter tuple is older" reading in the other direction."""
    store = _single(tmp_path)

    report = _report(
        store,
        None,
        product_reference=_PYTHON_26_7,
        registry_served_version=(26, 7),
    )

    assert report.freshness_state == "fresh"
    assert report.freshness_basis == "registry_served_version"
    assert report.stale is False
    assert report.reason == (
        "the package registry serves version 26.7, no newer than the indexed package version 26.7.0"
    )


def test_registry_served_version_genuinely_newer_at_different_precision_is_still_stale(
    tmp_path: Path,
) -> None:
    """A genuinely newer registry version at different precision (26, 8, 0) against an indexed
    (26, 7) must still correctly report "stale" - the padding fix must not blur a real difference."""
    store = _single(tmp_path)

    report = _report(
        store,
        None,
        product_reference=_PYTHON_26_7_SHORT,
        registry_served_version=(26, 8, 0),
    )

    assert report.freshness_state == "stale"
    assert report.freshness_basis == "registry_served_version"
    assert report.stale is True
    assert report.reason == (
        "the package registry serves version 26.8.0, newer than the indexed package version 26.7"
    )


def test_registry_served_version_genuinely_older_at_different_precision_is_still_fresh(
    tmp_path: Path,
) -> None:
    """A genuinely older registry version at different precision (26, 7) against an indexed
    (26, 8, 0) must still correctly report "fresh"."""
    store = _single(tmp_path)

    report = _report(
        store,
        None,
        product_reference=ProductReferenceInputs(
            manifest_text='[project]\nname = "x"\nversion = "26.8.0"\n',
            platform="python",
        ),
        registry_served_version=(26, 7),
    )

    assert report.freshness_state == "fresh"
    assert report.freshness_basis == "registry_served_version"
    assert report.stale is False
    assert report.reason == (
        "the package registry serves version 26.7, no newer than the indexed package version 26.8.0"
    )


# --- (d) no indexed_version to compare against falls through to unknown, unchanged ----------


def test_no_indexed_version_falls_through_to_unknown_even_with_a_registry_version(
    tmp_path: Path,
) -> None:
    store = _single(tmp_path)

    report = _report(store, None, registry_served_version=(26, 8, 0))

    assert report.freshness_state == "unknown"
    assert report.freshness_basis == "none"
    assert report.reason == "current source commit is unknown; staleness cannot be ruled out"


def test_an_unversioned_manifest_also_falls_through_to_unknown(tmp_path: Path) -> None:
    go = ProductReferenceInputs(manifest_text="module example.com/x\n\ngo 1.21\n", platform="go")
    store = _single(tmp_path)

    report = _report(store, None, product_reference=go, registry_served_version=(26, 8, 0))

    assert report.freshness_state == "unknown"
    assert report.freshness_basis == "none"


# --- (e) an earlier-resolving signal always wins over the registry version ------------------


def test_a_resolving_caller_commit_wins_over_a_disagreeing_registry_version(tmp_path: Path) -> None:
    store = _single(tmp_path)

    report = _report(
        store,
        SYMBOL_COMMIT,
        product_reference=_PYTHON_26_7,
        registry_served_version=(99, 0, 0),
    )

    assert report.freshness_state == "fresh"
    assert report.freshness_basis == "caller_commit"
    assert report.reason == "index matches the current source commit"


def test_a_resolving_latest_release_tag_commit_wins_over_a_disagreeing_registry_version(
    tmp_path: Path,
) -> None:
    store = _single(tmp_path)

    report = _report(
        store,
        None,
        releases=[_release("v2.0.0", SYMBOL_COMMIT)],
        product_reference=_PYTHON_26_7,
        registry_served_version=(99, 0, 0),
    )

    assert report.freshness_state == "fresh"
    assert report.freshness_basis == "latest_release_tag_commit"


def test_a_resolving_latest_release_version_wins_over_a_disagreeing_registry_version(
    tmp_path: Path,
) -> None:
    store = _single(tmp_path)

    report = _report(
        store,
        None,
        product_reference=_PYTHON_26_7,
        releases=[_release("V26.10.0"), _release("V26.7.0")],
        registry_served_version=(1, 0, 0),
    )

    assert report.freshness_state == "stale"
    assert report.freshness_basis == "latest_release_version"
