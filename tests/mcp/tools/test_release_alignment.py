"""G3/TC-519: which served release does the indexed API include? Pure rules, fully offline.

``release_alignment`` marks each release ``in_index`` True / False / None by one deterministic rule
with a stated basis: an indexed source commit equal to the tag's commit, else the release version
against the package manifest's version when both share a major number, else unknown.
"""

from __future__ import annotations

import pytest

from foss_mcp.extraction.github_release_reader import Release
from foss_mcp.mcp.tools.get_product_reference import ProductReferenceInputs
from foss_mcp.mcp.tools.list_recent_changes import ChangeEntry, list_recent_changes
from foss_mcp.mcp.tools.release_alignment import (
    align_releases,
    generation_indexed_at,
    package_version,
    parse_release_version,
    release_in_index,
)

COMMIT_A = "a" * 40
COMMIT_B = "b" * 40


def _release(tag: str, *, commit: str | None = None, source: str = "release") -> Release:
    return Release(tag, "", "none", target_commitish=commit, source=source)


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("V26.10.0", (26, 10, 0)),
        ("v26.09", (26, 9)),
        ("release-1.2", (1, 2)),
        ("26.8.0", (26, 8, 0)),
        ("latest", None),
        ("v1", None),
    ],
)
def test_parse_release_version(tag: str, expected: tuple[int, ...] | None) -> None:
    assert parse_release_version(tag) == expected


_NET_MANIFEST = "<Project><PropertyGroup><PackageId>Aspose.X</PackageId><Version>26.9.0.0</Version></PropertyGroup></Project>"
_PYTHON_MANIFEST = '[project]\nname = "x"\nversion = "26.7.0"\n'
_JAVA_MANIFEST = "<project><groupId>g</groupId><artifactId>a</artifactId><version>1.4.2</version></project>"
_JS_MANIFEST = '{"name": "x", "version": "2.3.1"}'
_RUST_MANIFEST = '[package]\nname = "x"\nversion = "0.8.1"\n'
_CPP_MANIFEST = "cmake_minimum_required(VERSION 3.20)\nproject(X VERSION 1.0.1 LANGUAGES CXX)\n"


@pytest.mark.parametrize(
    ("platform", "manifest", "expected"),
    [
        ("net", _NET_MANIFEST, (26, 9, 0, 0)),
        ("python", _PYTHON_MANIFEST, (26, 7, 0)),
        ("java", _JAVA_MANIFEST, (1, 4, 2)),
        ("typescript", _JS_MANIFEST, (2, 3, 1)),
        ("rust", _RUST_MANIFEST, (0, 8, 1)),
        ("cpp", _CPP_MANIFEST, (1, 0, 1)),
        ("go", "module example.com/x\n\ngo 1.21\n", None),
        ("python", "", None),
    ],
)
def test_package_version_per_platform(platform: str, manifest: str, expected: tuple[int, ...] | None) -> None:
    inputs = ProductReferenceInputs(manifest_text=manifest, platform=platform)
    assert package_version(inputs) == expected


def test_package_version_is_none_without_inputs_or_manifest() -> None:
    assert package_version(None) is None
    assert package_version(ProductReferenceInputs()) is None


@pytest.mark.parametrize("tag", ["V26.7.0", "26.7.0", "v26.6.9", "V26.3.1", "v26.7"])
def test_equal_and_lower_versions_are_in_the_index(tag: str) -> None:
    result = release_in_index(_release(tag), indexed_commits=(), indexed_version=(26, 7, 0))
    assert result == (True, "version_not_newer")


@pytest.mark.parametrize("tag", ["V26.10.0", "v26.7.1", "26.8.0", "v26.7.0.1"])
def test_a_higher_minor_is_newer_and_not_in_the_index(tag: str) -> None:
    result = release_in_index(_release(tag), indexed_commits=(), indexed_version=(26, 7, 0))
    assert result == (False, "version_newer")


def test_padding_26_9_0_0_equals_26_9_0() -> None:
    assert release_in_index(_release("V26.9.0"), indexed_commits=(), indexed_version=(26, 9, 0, 0)) == (
        True,
        "version_not_newer",
    )
    assert release_in_index(_release("v26.9.0.0"), indexed_commits=(), indexed_version=(26, 9, 0)) == (
        True,
        "version_not_newer",
    )
    assert release_in_index(_release("v26.9"), indexed_commits=(), indexed_version=(26, 9, 0, 0)) == (
        True,
        "version_not_newer",
    )


def test_different_majors_are_not_compared() -> None:
    assert release_in_index(_release("v26.09"), indexed_commits=(), indexed_version=(0, 1, 0)) == (
        None,
        "unknown",
    )
    assert release_in_index(_release("v26.10.0"), indexed_commits=(), indexed_version=(1, 0, 1)) == (
        None,
        "unknown",
    )


def test_a_release_without_a_version_or_without_a_manifest_version_is_unknown() -> None:
    assert release_in_index(_release("latest"), indexed_commits=(), indexed_version=(1, 0)) == (
        None,
        "unknown",
    )
    assert release_in_index(_release("v1.2.3"), indexed_commits=(), indexed_version=None) == (None, "unknown")


def test_commit_equality_wins_over_version() -> None:
    # The version rule alone would say "newer than the index" (False); the equal commit says it IS indexed.
    release = _release("V26.10.0", commit=COMMIT_A)
    assert release_in_index(release, indexed_commits=(COMMIT_B, COMMIT_A), indexed_version=(26, 7, 0)) == (
        True,
        "commit_equal",
    )
    # A different commit falls back to the version rule.
    assert release_in_index(release, indexed_commits=(COMMIT_B,), indexed_version=(26, 7, 0)) == (
        False,
        "version_newer",
    )
    # Equal commit works with no version information at all.
    assert release_in_index(
        _release("nightly", commit=COMMIT_A), indexed_commits=(COMMIT_A,), indexed_version=None
    ) == (
        True,
        "commit_equal",
    )


def test_a_branch_name_is_not_a_commit() -> None:
    release = _release("V26.10.0", commit="main")
    assert release.target_commit is None
    assert release_in_index(release, indexed_commits=("main",), indexed_version=None) == (None, "unknown")


def test_tag_entries_follow_the_same_rules() -> None:
    by_commit = _release("nightly", commit=COMMIT_A, source="tag")
    by_version = _release("v26.10.0", source="tag")
    assert release_in_index(by_commit, indexed_commits=(COMMIT_A,), indexed_version=None) == (
        True,
        "commit_equal",
    )
    assert release_in_index(by_version, indexed_commits=(), indexed_version=(26, 9, 0)) == (
        False,
        "version_newer",
    )
    assert release_in_index(by_version, indexed_commits=(), indexed_version=None) == (None, "unknown")


def test_align_releases_is_keyed_by_tag_and_the_first_occurrence_wins() -> None:
    releases = [_release("V26.10.0"), _release("V26.7.0"), _release("V26.10.0", commit=COMMIT_A)]
    aligned = align_releases(releases, indexed_commits=(COMMIT_A,), indexed_version=(26, 7, 0))
    assert aligned == {"V26.10.0": (False, "version_newer"), "V26.7.0": (True, "version_not_newer")}


@pytest.mark.parametrize(
    ("generation_id", "expected"),
    [
        ("pdf::net::self_extracted::20261014T131352217893Z-c8c1dcd9", "2026-10-14T13:13:52Z"),
        ("20261014T131352Z", "2026-10-14T13:13:52Z"),
        ("garbage", None),
        ("pdf::net::self_extracted::99999999T999999Z-x", None),
        ("", None),
        (None, None),
    ],
)
def test_generation_indexed_at_parses_the_utc_stamp(generation_id: str | None, expected: str | None) -> None:
    assert generation_indexed_at(generation_id) == expected


def test_list_recent_changes_without_alignment_keeps_the_defaults_and_the_list_shape() -> None:
    releases = [Release("v2", "Fixed `a`", "detailed"), Release("v1", "", "none")]

    entries = list_recent_changes(releases)

    assert isinstance(entries, list)
    assert [entry.tag_name for entry in entries] == ["v2", "v1"]
    assert all(isinstance(entry, ChangeEntry) for entry in entries)
    assert all(entry.in_index is None for entry in entries)
    assert all(entry.in_index_basis == "unknown" for entry in entries)
    assert all(entry.source == "release" for entry in entries)


def test_list_recent_changes_fills_in_index_basis_and_source() -> None:
    releases = [
        _release("V26.10.0"),
        _release("V26.7.0"),
        _release("old-tag", source="tag"),
    ]
    alignment = align_releases(releases, indexed_commits=(), indexed_version=(26, 7, 0))

    entries = list_recent_changes(releases, limit=3, alignment=alignment)

    assert [(e.tag_name, e.in_index, e.in_index_basis, e.source) for e in entries] == [
        ("V26.10.0", False, "version_newer", "release"),
        ("V26.7.0", True, "version_not_newer", "release"),
        ("old-tag", None, "unknown", "tag"),
    ]
    # The limit still bounds the list, and the order is the input order.
    assert [e.tag_name for e in list_recent_changes(releases, limit=1, alignment=alignment)] == ["V26.10.0"]
