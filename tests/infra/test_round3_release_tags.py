"""G3/TC-560 (REQ-G3-009, rows RD3-070..073): newer tags merged into the release list, and the drift record
written at ingestion.

Fully offline: ``fetch_releases`` / ``fetch_tags`` and the HTTP layer (``github_release_reader._get_json``)
are replaced by hand-written, upstream-shaped GitHub API answers (the documented JSON shapes of
``GET /repos/{r}/releases``, ``/tags`` and ``/compare/{base}...{head}``); no network, no token. The live pins
named by the card (slides/python 26.8.0, pdf/go v0.11.0, note/python v26.9.0, cells/go v26.7.1) are asserted
here against such recorded-shape answers; their baked sidecars are the closing step's.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

import infra.fetch_product_reference as fetch_product_reference
import infra.fetch_recent_releases as fetch_recent_releases
from foss_mcp.extraction import github_release_reader as reader
from foss_mcp.extraction.github_release_reader import (
    DriftRecord,
    Release,
    fetch_drift,
    merge_newer_tags,
    order_tags_newest_first,
)
from foss_mcp.mcp.tools.release_alignment import drift_from_record

SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40
SHA_INDEXED = "1234567890abcdef1234567890abcdef12345678"


def _release(tag: str) -> Release:
    return Release(tag, "Fixed `thing`", "detailed", url=f"https://github.com/o/r/releases/tag/{tag}")


def _tag(tag: str, sha: str = SHA_A) -> Release:
    return Release(
        tag, "", "none", url=f"https://github.com/o/r/releases/tag/{tag}", target_commitish=sha, source="tag"
    )


def _compare(ahead: int, behind: int, base_sha: str = SHA_A, status: str | None = None) -> dict:
    return {
        "status": status
        or ("ahead" if ahead and not behind else "behind" if behind and not ahead else "diverged"),
        "ahead_by": ahead,
        "behind_by": behind,
        "base_commit": {"sha": base_sha},
        "commits": [],
        "files": [],
    }


# --- merge_newer_tags (acceptance c) ------------------------------------------------------------


def test_a_tag_newer_than_the_newest_release_is_merged_in_front_and_marked_tag() -> None:
    # note/python: the newest GitHub release is v26.8.0 but the repository has a v26.9.0 tag.
    releases = [_release("v26.8.0"), _release("v26.7.0")]
    tags = [_tag("v26.9.0", SHA_B), _tag("v26.8.0"), _tag("v26.7.0")]

    merged = merge_newer_tags(releases, tags)

    assert [r.tag_name for r in merged] == ["v26.9.0", "v26.8.0", "v26.7.0"]
    assert merged[0].source == "tag"
    assert merged[0].target_commit == SHA_B
    assert [r.source for r in merged[1:]] == ["release", "release"]


def test_a_patch_tag_beyond_the_latest_release_is_merged_too() -> None:
    # cells/go: release v26.7.0, tag v26.7.1.
    merged = merge_newer_tags([_release("v26.7.0")], [_tag("v26.7.1", SHA_C), _tag("v26.7.0")])

    assert [r.tag_name for r in merged] == ["v26.7.1", "v26.7.0"]
    assert merged[0].source == "tag"


def test_older_unversioned_and_duplicate_tags_are_not_merged() -> None:
    releases = [_release("v26.8.0")]
    tags = [_tag("latest"), _tag("nightly"), _tag("v25.1.0"), _tag("26.8.0"), _tag("v26.8"), _tag("v26.8.0")]

    assert merge_newer_tags(releases, tags) == releases


def test_the_same_version_under_two_spellings_is_added_once_and_newest_first() -> None:
    merged = merge_newer_tags([_release("v1.0.0")], [_tag("v1.2.0"), _tag("1.2"), _tag("v1.1.0")])

    assert [r.tag_name for r in merged] == ["v1.2.0", "v1.1.0", "v1.0.0"]


def test_no_versioned_release_means_nothing_is_merged() -> None:
    releases = [Release("latest", "", "none")]

    assert merge_newer_tags(releases, [_tag("v9.0.0")]) == releases
    assert merge_newer_tags([], [_tag("v9.0.0")]) == []


def test_the_newest_release_is_the_greatest_version_not_the_first_listed() -> None:
    # A backport release published later sits first but is not the newest version.
    releases = [_release("v25.9.1"), _release("v26.8.0")]

    merged = merge_newer_tags(releases, [_tag("v26.1.0"), _tag("v26.9.0")])

    assert [r.tag_name for r in merged] == ["v26.9.0", "v25.9.1", "v26.8.0"]


def test_order_tags_newest_first_puts_the_highest_version_first_and_unversioned_last() -> None:
    tags = [_tag("v1.2.0"), _tag("latest"), _tag("v1.10.0"), _tag("v1.9.0")]

    assert [t.tag_name for t in order_tags_newest_first(tags)] == ["v1.10.0", "v1.9.0", "v1.2.0", "latest"]


# --- fetch_drift (recorded compare answers) -----------------------------------------------------


class _Http:
    def __init__(self, answers: dict[str, Any] | Exception) -> None:
        self.answers = answers
        self.urls: list[str] = []

    def __call__(self, url: str, *, etag: str | None = None) -> tuple[int, Any, str | None]:
        self.urls.append(url)
        if isinstance(self.answers, Exception):
            raise self.answers
        for fragment, body in self.answers.items():
            if fragment in url:
                return 200, body, None
        raise AssertionError(f"unexpected url {url}")


def test_fetch_drift_reads_one_compare_call_for_a_release_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    # slides/python: the index is past the 26.8.0 tag the package was built from.
    http = _Http({"/compare/26.8.0...": _compare(12, 0, base_sha=SHA_B)})
    monkeypatch.setattr(reader, "_get_json", http)

    record = fetch_drift("aspose-slides-foss/Aspose.Slides-FOSS-for-Python", _release("26.8.0"), SHA_INDEXED)

    assert record == DriftRecord(
        indexed_commit=SHA_INDEXED, tag="26.8.0", tag_commit=SHA_B, ahead_by=12, behind_by=0, status="ahead"
    )
    assert len(http.urls) == 1
    assert http.urls[0].startswith(
        f"https://api.github.com/repos/aspose-slides-foss/Aspose.Slides-FOSS-for-Python/compare/26.8.0...{SHA_INDEXED}"
    )
    verdict = drift_from_record(record, indexed_commit=SHA_INDEXED)
    assert verdict["drift"] == "index_ahead_of_package"
    assert verdict["commit_distance"] == 12
    assert verdict["published_version"] == "26.8.0"


def test_fetch_drift_uses_the_commit_of_a_tag_entry_as_the_base(monkeypatch: pytest.MonkeyPatch) -> None:
    http = _Http({f"/compare/{SHA_C}...": _compare(0, 3, base_sha=SHA_C)})
    monkeypatch.setattr(reader, "_get_json", http)

    record = fetch_drift("aspose-pdf-foss/aspose-pdf-foss-go", _tag("v0.11.0", SHA_C), SHA_INDEXED)

    # pdf/go: the module proxy resolves v0.11.0, which has commits the index lacks.
    assert (record.ahead_by, record.behind_by, record.tag_commit) == (0, 3, SHA_C)
    verdict = drift_from_record(record, indexed_commit=SHA_INDEXED)
    assert verdict["drift"] == "package_ahead_of_index"
    assert verdict["published_version"] == "0.11.0"


@pytest.mark.parametrize(
    "failure",
    [OSError("no route"), TimeoutError("slow"), RuntimeError("rate limited"), ValueError("bad json")],
)
def test_a_failed_comparison_is_recorded_as_unknown_not_raised(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    monkeypatch.setattr(reader, "_get_json", _Http(failure))

    record = fetch_drift("o/r", _release("v1.0.0"), SHA_INDEXED)

    assert record.error == type(failure).__name__
    assert record.ahead_by is None and record.behind_by is None
    assert drift_from_record(record, indexed_commit=SHA_INDEXED)["drift"] == "unknown"


@pytest.mark.parametrize("body", [None, [], {"status": "ahead"}, {"ahead_by": "3", "behind_by": 0}])
def test_an_unexpected_compare_answer_is_unknown(monkeypatch: pytest.MonkeyPatch, body: object) -> None:
    monkeypatch.setattr(reader, "_get_json", _Http({"/compare/": body}))

    record = fetch_drift("o/r", _release("v1.0.0"), SHA_INDEXED)

    assert record.error == "UnexpectedCompareAnswer"
    assert drift_from_record(record, indexed_commit=SHA_INDEXED)["drift"] == "unknown"


# --- DriftRecord JSON --------------------------------------------------------------------------


def test_a_drift_record_round_trips_and_an_unknown_one_stays_small() -> None:
    full = DriftRecord(SHA_INDEXED, "v1.0.0", SHA_A, 4, 0, "ahead")
    failed = DriftRecord(SHA_INDEXED, "v1.0.0", error="URLError")

    assert DriftRecord.from_json(full.to_json()) == full
    assert DriftRecord.from_json(failed.to_json()) == failed
    assert failed.to_json() == {"indexed_commit": SHA_INDEXED, "tag": "v1.0.0", "error": "URLError"}


@pytest.mark.parametrize(
    "data",
    [
        None,
        "x",
        [],
        {},
        {"tag": "v1"},
        {"indexed_commit": SHA_A},
        {"indexed_commit": 5, "tag": "v1"},
        {"indexed_commit": SHA_A, "tag": ""},
    ],
)
def test_a_malformed_record_reads_as_no_record(data: object) -> None:
    assert DriftRecord.from_json(data) is None


def test_bad_counts_in_a_record_read_as_unknown_counts() -> None:
    record = DriftRecord.from_json(
        {"indexed_commit": SHA_A, "tag": "v1", "ahead_by": -1, "behind_by": True, "status": 4}
    )

    assert record == DriftRecord(SHA_A, "v1")


# --- main(): the ingestion step ------------------------------------------------------------------

REPOSITORY = "aspose-note-foss/Aspose.Note-FOSS-for-Python"


def _setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    releases: list[Release],
    tags: list[Release] | Exception,
    compare: dict | Exception | None,
    source_ref: str | None = SHA_INDEXED,
    extra: tuple[str, ...] = (),
) -> tuple[Path, Path, dict[str, int], _Http]:
    calls = {"releases": 0, "tags": 0}

    def _fetch_releases(repository: str, **kwargs: object) -> list[Release]:
        calls["releases"] += 1
        return list(releases)

    def _fetch_tags(repository: str, **kwargs: object) -> list[Release]:
        calls["tags"] += 1
        if isinstance(tags, Exception):
            raise tags
        return list(tags)

    http = _Http(compare if compare is not None else {})
    monkeypatch.setattr(fetch_recent_releases, "fetch_releases", _fetch_releases)
    monkeypatch.setattr(fetch_recent_releases, "fetch_tags", _fetch_tags)
    monkeypatch.setattr(reader, "_get_json", http)
    output = tmp_path / "recent_releases_note_python.json"
    product = tmp_path / "product_reference_note_python.json"
    if source_ref is not None:
        product.write_text(
            json.dumps(
                {
                    "manifest_text": '[project]\nname = "aspose-note-foss"\nversion = "26.8.0"\n',
                    "platform": "python",
                    "contributing": {"status": "not_present", "path": "CONTRIBUTING.md"},
                    "agent_guidance": {"status": "not_present", "path": "AGENTS.md"},
                    "source_repository": REPOSITORY,
                    "source_ref": source_ref,
                }
            ),
            encoding="utf-8",
        )
    monkeypatch.setattr(
        sys, "argv", ["fetch_recent_releases.py", "--repository", REPOSITORY, "--output", str(output), *extra]
    )
    return output, product, calls, http


def test_main_merges_the_newer_tag_and_records_the_drift_in_both_sidecars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # note/python: the releases stop at v26.8.0 but the repository has the v26.9.0 tag. The newest entry is
    # that tag, and the recorded comparison is made against it (the index is 2 commits behind the tag).
    output, product, calls, http = _setup(
        tmp_path,
        monkeypatch,
        releases=[_release("v26.8.0"), _release("v26.7.0")],
        tags=[_tag("v26.9.0", SHA_B), _tag("v26.8.0", SHA_A)],
        compare={f"/compare/{SHA_B}...{SHA_INDEXED}": _compare(0, 2, base_sha=SHA_B)},
    )

    fetch_recent_releases.main()

    written = json.loads(output.read_text(encoding="utf-8"))
    assert [e["tag_name"] for e in written] == ["v26.9.0", "v26.8.0", "v26.7.0"]
    assert written[0]["source"] == "tag"
    assert written[0]["target_commitish"] == SHA_B
    assert written[0]["drift"] == {
        "indexed_commit": SHA_INDEXED,
        "tag": "v26.9.0",
        "tag_commit": SHA_B,
        "ahead_by": 0,
        "behind_by": 2,
        "status": "behind",
    }
    assert all("drift" not in entry for entry in written[1:])
    assert calls == {"releases": 1, "tags": 1}
    assert len(http.urls) == 1  # the compare API is asked once per pilot
    stored = json.loads(product.read_text(encoding="utf-8"))
    assert stored["drift"] == written[0]["drift"]
    assert stored["source_ref"] == SHA_INDEXED

    # The loaders read both back.
    loaded = fetch_recent_releases.load_recent_releases_sidecar(output)
    assert loaded is not None and loaded[0].drift is not None
    assert loaded[0].drift.behind_by == 2
    assert loaded[1].drift is None
    inputs = fetch_product_reference.load_manifest_sidecar(product)
    assert inputs is not None and inputs.drift == loaded[0].drift
    assert drift_from_record(inputs.drift, indexed_commit=SHA_INDEXED)["drift"] == "package_ahead_of_index"


def test_main_without_an_indexed_commit_does_nothing_new(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output, _product, calls, http = _setup(
        tmp_path,
        monkeypatch,
        releases=[_release("v26.8.0")],
        tags=[_tag("v26.9.0")],
        compare=None,
        source_ref=None,
    )

    fetch_recent_releases.main()

    assert calls == {"releases": 1, "tags": 0}
    assert http.urls == []
    written = json.loads(output.read_text(encoding="utf-8"))
    assert [e["tag_name"] for e in written] == ["v26.8.0"]
    assert "drift" not in written[0]


def test_main_takes_the_indexed_commit_from_the_flag_when_there_is_no_product_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output, product, _calls, http = _setup(
        tmp_path,
        monkeypatch,
        releases=[_release("v26.8.0")],
        tags=[],
        compare={"/compare/v26.8.0...": _compare(7, 0)},
        source_ref=None,
        extra=("--indexed-commit", SHA_INDEXED),
    )

    fetch_recent_releases.main()

    written = json.loads(output.read_text(encoding="utf-8"))
    assert written[0]["drift"]["ahead_by"] == 7
    assert written[0]["drift"]["indexed_commit"] == SHA_INDEXED
    assert len(http.urls) == 1
    assert not product.exists()  # nothing to add the record to, and none is created


def test_main_records_a_failed_comparison_as_unknown_and_still_writes_the_releases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output, product, _calls, _http = _setup(
        tmp_path,
        monkeypatch,
        releases=[_release("v26.8.0")],
        tags=[],
        compare=OSError("offline"),
    )

    fetch_recent_releases.main()

    written = json.loads(output.read_text(encoding="utf-8"))
    assert [e["tag_name"] for e in written] == ["v26.8.0"]
    assert written[0]["drift"] == {"indexed_commit": SHA_INDEXED, "tag": "v26.8.0", "error": "OSError"}
    meta = json.loads(fetch_recent_releases.releases_meta_path(output).read_text(encoding="utf-8"))
    assert meta["state"] == "ok"
    assert not fetch_recent_releases._fetch_failed_marker(output).exists()
    assert json.loads(product.read_text(encoding="utf-8"))["drift"]["error"] == "OSError"
    assert "error=OSError" in capsys.readouterr().out


def test_main_keeps_the_list_when_the_tags_read_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output, _product, calls, _http = _setup(
        tmp_path,
        monkeypatch,
        releases=[_release("v26.8.0")],
        tags=RuntimeError("boom"),
        compare={"/compare/v26.8.0...": _compare(1, 0)},
    )

    fetch_recent_releases.main()

    written = json.loads(output.read_text(encoding="utf-8"))
    assert [e["tag_name"] for e in written] == ["v26.8.0"]
    assert written[0]["drift"]["ahead_by"] == 1
    assert calls["tags"] == 1
    assert "tags read failed" in capsys.readouterr().err


def test_main_does_not_add_a_record_to_a_product_sidecar_for_another_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output, product, _calls, _http = _setup(
        tmp_path,
        monkeypatch,
        releases=[_release("v26.8.0")],
        tags=[],
        compare={"/compare/v26.8.0...": _compare(1, 0)},
        source_ref=SHA_C,
        extra=("--indexed-commit", SHA_INDEXED),
    )

    fetch_recent_releases.main()

    assert json.loads(output.read_text(encoding="utf-8"))[0]["drift"]["indexed_commit"] == SHA_INDEXED
    assert "drift" not in json.loads(product.read_text(encoding="utf-8"))


def test_main_ignores_a_failed_product_reference_placeholder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output, product, calls, http = _setup(
        tmp_path, monkeypatch, releases=[_release("v26.8.0")], tags=[], compare=None, source_ref=None
    )
    product.write_text(
        json.dumps({"state": "fetch_failed", "status": "fetch_failed", "source_ref": SHA_INDEXED})
    )

    fetch_recent_releases.main()

    assert calls["tags"] == 0 and http.urls == []
    assert "drift" not in json.loads(output.read_text(encoding="utf-8"))[0]


def test_a_tag_only_repository_gets_its_tags_newest_first_and_the_drift_on_the_highest_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # email/python and page/python publish tags only; the index is AHEAD of the newest tag.
    output, _product, calls, http = _setup(
        tmp_path,
        monkeypatch,
        releases=[],
        tags=[_tag("v1.2.0", SHA_A), _tag("v1.10.0", SHA_B), _tag("v1.9.0", SHA_C)],
        compare={f"/compare/{SHA_B}...": _compare(5, 0, base_sha=SHA_B)},
    )

    fetch_recent_releases.main()

    written = json.loads(output.read_text(encoding="utf-8"))
    assert [e["tag_name"] for e in written] == ["v1.10.0", "v1.9.0", "v1.2.0"]
    assert all(e["source"] == "tag" for e in written)
    assert written[0]["drift"]["ahead_by"] == 5
    assert calls == {"releases": 1, "tags": 1}  # the tags are read once, by the existing fallback
    assert len(http.urls) == 1
    assert (
        drift_from_record(
            fetch_recent_releases.load_recent_releases_sidecar(output)[0].drift,  # type: ignore[index]
            indexed_commit=SHA_INDEXED,
        )["drift"]
        == "index_ahead_of_package"
    )


def test_the_limit_applies_after_the_merge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output, _product, _calls, _http = _setup(
        tmp_path,
        monkeypatch,
        releases=[_release("v1.0.0"), _release("v0.9.0")],
        tags=[_tag("v1.1.0"), _tag("v1.2.0")],
        compare={"/compare/": _compare(0, 0)},
        extra=("--limit", "2"),
    )

    fetch_recent_releases.main()

    assert [e["tag_name"] for e in json.loads(output.read_text(encoding="utf-8"))] == ["v1.2.0", "v1.1.0"]


# --- a sidecar without the key, and the pin the product sidecar keeps -----------------------------


def test_a_sidecar_written_before_the_card_loads_with_no_drift(tmp_path: Path) -> None:
    output = tmp_path / "recent_releases_pdf_go.json"
    output.write_text(
        json.dumps([{"tag_name": "v0.11.0", "body": "", "richness": "none", "source": "tag"}]),
        encoding="utf-8",
    )

    loaded = fetch_recent_releases.load_recent_releases_sidecar(output)

    assert loaded is not None and loaded[0].drift is None
    assert drift_from_record(loaded[0].drift)["drift"] == "unknown"


def test_a_malformed_drift_key_loads_as_no_record_instead_of_failing(tmp_path: Path) -> None:
    output = tmp_path / "recent_releases_pdf_go.json"
    output.write_text(
        json.dumps([{"tag_name": "v1", "body": "", "richness": "none", "drift": {"ahead_by": "many"}}]),
        encoding="utf-8",
    )

    loaded = fetch_recent_releases.load_recent_releases_sidecar(output)

    assert loaded is not None and loaded[0].drift is None


def test_a_serialised_entry_has_a_drift_key_only_when_it_has_a_record() -> None:
    plain = fetch_recent_releases._serialize_release(_release("v1"))
    with_record = fetch_recent_releases._serialize_release(
        Release("v2", "", "none", drift=DriftRecord(SHA_A, "v2", ahead_by=1, behind_by=0))
    )

    assert "drift" not in plain
    assert with_record["drift"] == {"indexed_commit": SHA_A, "tag": "v2", "ahead_by": 1, "behind_by": 0}


def test_a_forced_refetch_of_an_unchanged_pin_keeps_its_drift_record(tmp_path: Path) -> None:
    sidecar = tmp_path / "product_reference_note_python.json"
    record = DriftRecord(SHA_INDEXED, "v26.8.0", ahead_by=3, behind_by=0)
    sidecar.write_text(json.dumps({"source_ref": SHA_INDEXED, "drift": record.to_json()}), encoding="utf-8")

    assert fetch_product_reference._existing_drift_for(sidecar, SHA_INDEXED) == record.to_json()
    assert fetch_product_reference._existing_drift_for(sidecar, SHA_C) is None
    assert fetch_product_reference._existing_drift_for(sidecar, None) is None
    assert fetch_product_reference._existing_drift_for(tmp_path / "absent.json", SHA_INDEXED) is None
    sidecar.write_text("{not json", encoding="utf-8")
    assert fetch_product_reference._existing_drift_for(sidecar, SHA_INDEXED) is None


def test_a_product_sidecar_without_drift_loads_with_none(tmp_path: Path) -> None:
    sidecar = tmp_path / "product_reference_note_python.json"
    sidecar.write_text(
        json.dumps(
            {
                "manifest_text": None,
                "platform": "python",
                "contributing": {"status": "not_present", "path": "CONTRIBUTING.md"},
                "agent_guidance": {"status": "not_present", "path": "AGENTS.md"},
            }
        ),
        encoding="utf-8",
    )

    inputs = fetch_product_reference.load_manifest_sidecar(sidecar)

    assert inputs is not None and inputs.drift is None


# --- the four live pins, as recorded-shape answers ------------------------------------------------


@pytest.mark.parametrize(
    ("repository", "tag", "compare", "expected_drift", "expected_published"),
    [
        (
            "aspose-slides-foss/Aspose.Slides-FOSS-for-Python",
            "26.8.0",
            _compare(12, 0),
            "index_ahead_of_package",
            "26.8.0",
        ),
        ("aspose-pdf-foss/aspose-pdf-foss-go", "v0.11.0", _compare(0, 6), "package_ahead_of_index", "0.11.0"),
        (
            "aspose-note-foss/Aspose.Note-FOSS-for-Python",
            "v26.9.0",
            _compare(0, 1),
            "package_ahead_of_index",
            "26.9.0",
        ),
        (
            "aspose-cells-foss/aspose-cells-foss-go",
            "v26.7.1",
            _compare(0, 2),
            "package_ahead_of_index",
            "26.7.1",
        ),
    ],
)
def test_the_named_live_pins_get_the_right_direction_and_published_version(
    monkeypatch: pytest.MonkeyPatch,
    repository: str,
    tag: str,
    compare: dict,
    expected_drift: str,
    expected_published: str,
) -> None:
    monkeypatch.setattr(reader, "_get_json", _Http({"/compare/": compare}))

    record = fetch_drift(repository, _tag(tag, SHA_C), SHA_INDEXED)
    verdict = drift_from_record(record, indexed_commit=SHA_INDEXED)

    assert verdict["drift"] == expected_drift
    assert verdict["published_version"] == expected_published
    assert verdict["latest_release_tag"] == tag
