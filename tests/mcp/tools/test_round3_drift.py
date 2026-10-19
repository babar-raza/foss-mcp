"""G3/TC-560 (REQ-G3-009, rows RD3-070..073): index-versus-package drift from git ancestry.

Offline. The independent oracle is a throw-away LOCAL git repository built in the test (commits, a
branch, tags), so the ancestry is real: every commit distance below is what ``git rev-list --count``
says, never what the code under test says. The GitHub compare answer that production records is
reproduced from those git counts in the shape GitHub documents (``ahead_by``, ``behind_by``,
``status``, ``base_commit.sha``).
"""

from __future__ import annotations

import dataclasses
import re
import subprocess
from pathlib import Path

import pytest

from foss_mcp.extraction.github_release_reader import DriftRecord, Release
from foss_mcp.mcp import server as mcp_server
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools import release_alignment
from foss_mcp.mcp.tools.get_product_reference import (
    NotAvailable,
    ProductReferenceInputs,
    ReferenceContent,
    _install_pin_notice,
    get_product_reference,
)
from foss_mcp.mcp.tools.list_recent_changes import list_recent_changes
from foss_mcp.mcp.tools.release_alignment import (
    INDEX_AHEAD,
    PACKAGE_AHEAD,
    drift_from_record,
    drift_verdict,
)
from foss_mcp.mcp.tools.report_index_freshness import report_index_freshness
from tests.mcp.tools.test_freshness_provenance import _publish, _symbol

REPO_ROOT = Path(__file__).resolve().parents[3]
SCOPE = Scope(family="pdf", platform="net")
REPOSITORY = "aspose-slides-foss/Aspose.Slides-FOSS-for-Python"
REPOSITORY_URL = f"https://github.com/{REPOSITORY}"

_PYPROJECT_26_8 = '[project]\nname = "aspose-slides-foss"\nversion = "26.8.0"\n'
_GO_MOD = "module github.com/aspose-pdf-foss/aspose-pdf-foss-go\n\ngo 1.21\n"
_CMAKE = "cmake_minimum_required(VERSION 3.16)\nproject(aspose_3d_foss_cpp VERSION 26.8.0)\nset(CMAKE_CXX_STANDARD 17)\n"


# --- the oracle: a real git repository -------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        [
            "git",
            "-c",
            "user.name=oracle",
            "-c",
            "user.email=oracle@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "tag.gpgsign=false",
            *args,
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


class Oracle:
    """A local repository with ``c1..c5`` on main, tag ``v26.8.0`` at c2 and tag ``v26.9.0`` at c4, plus a
    side branch commit ``s1`` made on c2 (so c5 and s1 have diverged)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir()
        _git(root, "init", "-q", "-b", "main")
        self.commit: dict[str, str] = {}
        for index in range(1, 6):
            (root / "manifest.txt").write_text(f"version = 26.8.0\nrevision {index}\n", encoding="utf-8")
            _git(root, "add", "manifest.txt")
            _git(root, "commit", "-q", "-m", f"c{index}")
            self.commit[f"c{index}"] = _git(root, "rev-parse", "HEAD")
            if index == 2:
                _git(root, "tag", "v26.8.0")
            if index == 4:
                _git(root, "tag", "v26.9.0")
        _git(root, "checkout", "-q", "-b", "side", "v26.8.0")
        (root / "side.txt").write_text("side\n", encoding="utf-8")
        _git(root, "add", "side.txt")
        _git(root, "commit", "-q", "-m", "s1")
        self.commit["s1"] = _git(root, "rev-parse", "HEAD")

    def compare(self, tag: str, head: str) -> dict:
        """What GitHub's ``compare/<tag>...<head>`` answers, with the counts taken from git itself."""
        ahead = int(_git(self.root, "rev-list", "--count", f"{tag}..{head}"))
        behind = int(_git(self.root, "rev-list", "--count", f"{head}..{tag}"))
        status = {(0, 0): "identical", (1, 0): "ahead", (0, 1): "behind"}.get(
            (min(ahead, 1), min(behind, 1)), "diverged"
        )
        return {
            "status": status,
            "ahead_by": ahead,
            "behind_by": behind,
            "base_commit": {"sha": _git(self.root, "rev-parse", f"{tag}^{{commit}}")},
        }

    def record(self, tag: str, head: str) -> DriftRecord:
        body = self.compare(tag, self.commit[head])
        return DriftRecord(
            indexed_commit=self.commit[head],
            tag=tag,
            tag_commit=body["base_commit"]["sha"],
            ahead_by=body["ahead_by"],
            behind_by=body["behind_by"],
            status=body["status"],
        )


@pytest.fixture(scope="module")
def oracle(tmp_path_factory: pytest.TempPathFactory) -> Oracle:
    return Oracle(tmp_path_factory.mktemp("oracle") / "repo")


def _release(tag: str, *, drift: DriftRecord | None = None, **kwargs: object) -> Release:
    return Release(tag, "Fixed `a`", "detailed", drift=drift, **kwargs)  # type: ignore[arg-type]


# --- the mandated names -----------------------------------------------------------------------


def test_the_mandated_constants_have_the_mandated_values() -> None:
    assert INDEX_AHEAD == "index_ahead_of_package"
    assert PACKAGE_AHEAD == "package_ahead_of_index"
    source = (REPO_ROOT / "src/foss_mcp/mcp/tools/release_alignment.py").read_text(encoding="utf-8")
    assert 'INDEX_AHEAD = "index_ahead_of_package"' in source
    assert 'PACKAGE_AHEAD = "package_ahead_of_index"' in source


# --- drift_verdict against the real ancestry ---------------------------------------------------


def test_the_oracle_itself_is_a_real_repository(oracle: Oracle) -> None:
    # The fixture's own ground truth: c5 is 3 commits past the v26.8.0 tag (c2) and the tag has nothing c5 lacks.
    assert _git(oracle.root, "rev-list", "--count", f"v26.8.0..{oracle.commit['c5']}") == "3"
    assert _git(oracle.root, "rev-list", "--count", f"{oracle.commit['c5']}..v26.8.0") == "0"


def test_index_ahead_of_the_tag_is_named_with_the_distance_and_the_published_version(oracle: Oracle) -> None:
    record = oracle.record("v26.8.0", "c5")

    verdict = drift_from_record(record, indexed_commit=oracle.commit["c5"], indexed_version="26.8.0")

    assert verdict["drift"] == "index_ahead_of_package"
    assert verdict["index_ahead_of_package"] is True
    assert verdict["package_ahead_of_index"] is False
    assert verdict["commit_distance"] == 3
    assert (verdict["commits_ahead"], verdict["commits_behind"]) == (3, 0)
    assert verdict["published_version"] == "26.8.0"
    assert verdict["indexed_version"] == "26.8.0"
    assert verdict["latest_release_tag"] == "v26.8.0"
    assert "3 commits ahead" in verdict["message"]
    assert "can lack types and members" in verdict["message"]


def test_package_ahead_of_the_index_is_named_with_the_distance(oracle: Oracle) -> None:
    record = oracle.record("v26.9.0", "c2")

    verdict = drift_from_record(record, indexed_commit=oracle.commit["c2"])

    assert verdict["drift"] == "package_ahead_of_index"
    assert verdict["package_ahead_of_index"] is True
    assert verdict["index_ahead_of_package"] is False
    assert verdict["commit_distance"] == 2
    assert verdict["published_version"] == "26.9.0"
    assert "2 commits ahead" in verdict["message"]
    assert "does not describe" in verdict["message"]


def test_diverged_histories_are_neither_ahead_nor_in_sync(oracle: Oracle) -> None:
    record = oracle.record("v26.9.0", "s1")

    verdict = drift_from_record(record, indexed_commit=oracle.commit["s1"])

    assert (record.ahead_by, record.behind_by) == (1, 2)
    assert verdict["drift"] == "diverged"
    assert verdict["index_ahead_of_package"] is False
    assert verdict["package_ahead_of_index"] is False
    assert verdict["commit_distance"] is None
    assert "diverged" in verdict["message"]


def test_the_index_at_the_tag_commit_is_in_sync(oracle: Oracle) -> None:
    record = oracle.record("v26.9.0", "c4")

    verdict = drift_from_record(record, indexed_commit=oracle.commit["c4"])

    assert record.status == "identical"
    assert verdict["drift"] == "in_sync"
    assert verdict["commit_distance"] == 0
    assert verdict["index_ahead_of_package"] is False
    assert verdict["package_ahead_of_index"] is False


def test_drift_verdict_is_pure_and_takes_the_documented_arguments() -> None:
    verdict = drift_verdict("a" * 40, "v1.0.0", 45, 0, "1.0.0", "1.0.0")

    assert verdict["drift"] == "index_ahead_of_package"
    assert verdict["commit_distance"] == 45
    assert verdict["indexed_commit"] == "a" * 40
    assert "45 commits ahead" in verdict["message"]
    assert drift_verdict("a" * 40, "v1.0.0", 1, 0, "1.0.0", None)["message"].startswith(
        "The indexed commit aaaaaaaaaaaa is 1 commit ahead"
    )


@pytest.mark.parametrize(
    ("ahead", "behind"), [(None, None), (3, None), (None, 3), (-1, 0), (0, -2), (True, 0), ("3", 0)]
)
def test_absent_or_invalid_counts_are_unknown_never_an_error_and_never_no_drift(
    ahead: object, behind: object
) -> None:
    verdict = drift_verdict("a" * 40, "v1", ahead, behind, "1.0", None)  # type: ignore[arg-type]

    assert verdict["drift"] == "unknown"
    assert verdict["index_ahead_of_package"] is None
    assert verdict["package_ahead_of_index"] is None
    assert verdict["commit_distance"] is None
    assert verdict["message"] is None


def test_no_record_and_a_record_for_another_commit_are_unknown(oracle: Oracle) -> None:
    assert drift_from_record(None)["drift"] == "unknown"
    record = oracle.record("v26.8.0", "c5")
    assert drift_from_record(record, indexed_commit=oracle.commit["c3"])["drift"] == "unknown"
    # An abbreviated indexed commit of seven or more characters is the same commit.
    assert (
        drift_from_record(record, indexed_commit=oracle.commit["c5"][:9])["drift"] == "index_ahead_of_package"
    )
    # A failed comparison (no counts) is unknown, not in sync.
    failed = DriftRecord(indexed_commit=oracle.commit["c5"], tag="v26.8.0", error="URLError")
    assert drift_from_record(failed, indexed_commit=oracle.commit["c5"])["drift"] == "unknown"


# --- report_index_freshness ---------------------------------------------------------------------


def test_freshness_says_index_ahead_with_the_distance_and_the_published_version(
    tmp_path: Path, oracle: Oracle
) -> None:
    indexed = oracle.commit["c5"]
    store = _publish(tmp_path / "m", [_symbol("Alpha", indexed), _symbol("Beta", indexed)])
    inputs = ProductReferenceInputs(
        manifest_text=_PYPROJECT_26_8, platform="python", source_repository=REPOSITORY, source_ref=indexed
    )

    report = report_index_freshness(
        store,
        SCOPE,
        "self_extracted",
        None,
        product_reference=inputs,
        releases=[_release("v26.8.0", drift=oracle.record("v26.8.0", "c5"))],
    )

    assert report.drift == "index_ahead_of_package"
    assert report.index_ahead_of_package is True
    assert report.package_ahead_of_index is False
    assert report.drift_commit_distance == 3
    assert report.published_version == "26.8.0"
    assert report.freshness_basis == "git_ancestry"
    # The index is AHEAD of the latest tag: that is not stale.
    assert report.freshness_state == "fresh"
    assert report.stale is False
    assert "3 commits ahead" in report.reason
    assert report.drift_message in report.warnings


def test_freshness_says_package_ahead_for_a_go_manifest_with_no_version(
    tmp_path: Path, oracle: Oracle
) -> None:
    indexed = oracle.commit["c2"]
    store = _publish(tmp_path / "m", [_symbol("Alpha", indexed)])
    inputs = ProductReferenceInputs(
        manifest_text=_GO_MOD, platform="go", source_repository="aspose-pdf-foss/pdf-go", source_ref=indexed
    )

    report = report_index_freshness(
        store,
        SCOPE,
        "self_extracted",
        None,
        product_reference=inputs,
        releases=[_release("v26.9.0", drift=oracle.record("v26.9.0", "c2"))],
    )

    assert report.drift == "package_ahead_of_index"
    assert report.package_ahead_of_index is True
    assert report.index_ahead_of_package is False
    assert report.drift_commit_distance == 2
    assert report.published_version == "26.9.0"
    assert report.freshness_state == "stale"
    assert report.stale is True
    assert report.freshness_basis == "git_ancestry"


def test_a_tag_only_repository_is_not_reported_stale_when_the_index_is_ahead_of_its_tag(
    tmp_path: Path, oracle: Oracle
) -> None:
    indexed = oracle.commit["c5"]
    store = _publish(tmp_path / "m", [_symbol("Alpha", indexed)])
    tag_commit = oracle.commit["c4"]
    without_drift = Release("v26.9.0", "", "none", target_commitish=tag_commit, source="tag")
    with_drift = dataclasses.replace(without_drift, drift=oracle.record("v26.9.0", "c5"))

    legacy = report_index_freshness(store, SCOPE, "self_extracted", None, releases=[without_drift])
    fixed = report_index_freshness(store, SCOPE, "self_extracted", None, releases=[with_drift])

    # Before the card the differing commits read as "stale"; the ancestry shows the index is the newer side.
    assert legacy.freshness_state == "stale"
    assert legacy.drift == "unknown"
    assert fixed.freshness_state == "fresh"
    assert fixed.drift == "index_ahead_of_package"
    assert fixed.drift_commit_distance == 1


def test_a_caller_supplied_commit_keeps_its_basis_and_the_drift_is_still_disclosed(
    tmp_path: Path, oracle: Oracle
) -> None:
    indexed = oracle.commit["c5"]
    store = _publish(tmp_path / "m", [_symbol("Alpha", indexed)])

    report = report_index_freshness(
        store,
        SCOPE,
        "self_extracted",
        indexed,
        releases=[_release("v26.8.0", drift=oracle.record("v26.8.0", "c5"))],
    )

    assert report.freshness_basis == "caller_commit"
    assert report.freshness_state == "fresh"
    assert report.drift == "index_ahead_of_package"
    assert report.drift_commit_distance == 3


def test_absent_drift_data_keeps_the_existing_behaviour_and_says_unknown(
    tmp_path: Path, oracle: Oracle
) -> None:
    indexed = oracle.commit["c5"]
    store = _publish(tmp_path / "m", [_symbol("Alpha", indexed)])

    report = report_index_freshness(store, SCOPE, "self_extracted", None, releases=[_release("v26.8.0")])

    assert report.drift == "unknown"
    assert report.index_ahead_of_package is None
    assert report.package_ahead_of_index is None
    assert report.drift_commit_distance is None
    assert report.published_version is None
    assert report.drift_message is None
    assert report.freshness_state == "unknown"
    assert report.freshness_basis == "none"
    assert report.reason == "current source commit is unknown; staleness cannot be ruled out"
    assert report.warnings == ()


def test_a_record_taken_for_another_commit_is_not_applied_to_this_index(
    tmp_path: Path, oracle: Oracle
) -> None:
    store = _publish(tmp_path / "m", [_symbol("Alpha", oracle.commit["c5"])])

    report = report_index_freshness(
        store,
        SCOPE,
        "self_extracted",
        None,
        releases=[_release("v26.8.0", drift=oracle.record("v26.8.0", "c3"))],
    )

    assert report.drift == "unknown"
    assert report.freshness_basis == "none"


def test_the_product_reference_sidecar_record_is_used_when_the_releases_carry_none(
    tmp_path: Path, oracle: Oracle
) -> None:
    indexed = oracle.commit["c5"]
    store = _publish(tmp_path / "m", [_symbol("Alpha", indexed)])
    inputs = ProductReferenceInputs(
        manifest_text=_PYPROJECT_26_8,
        platform="python",
        source_repository=REPOSITORY,
        source_ref=indexed,
        drift=oracle.record("v26.8.0", "c5"),
    )

    report = report_index_freshness(
        store, SCOPE, "self_extracted", None, product_reference=inputs, releases=[_release("v26.8.0")]
    )

    assert report.drift == "index_ahead_of_package"
    assert report.drift_commit_distance == 3


# --- list_recent_changes ------------------------------------------------------------------------


def test_list_recent_changes_carries_the_drift_on_the_entry_it_was_recorded_for(oracle: Oracle) -> None:
    newest_tag = Release(
        "v26.9.0",
        "",
        "none",
        target_commitish=oracle.commit["c4"],
        source="tag",
        drift=oracle.record("v26.9.0", "c5"),
    )
    releases = [newest_tag, _release("v26.8.0"), _release("v26.7.0")]

    entries = list_recent_changes(releases)

    assert [e.tag_name for e in entries] == ["v26.9.0", "v26.8.0", "v26.7.0"]
    assert entries[0].source == "tag"
    assert entries[0].drift == "index_ahead_of_package"
    assert entries[0].commits_index_ahead == 1
    assert entries[0].commits_package_ahead == 0
    assert entries[0].published_version == "26.9.0"
    assert entries[0].drift_message
    assert [e.drift for e in entries[1:]] == ["unknown", "unknown"]
    assert all(e.drift_message is None for e in entries[1:])


def test_list_recent_changes_without_any_record_is_as_before_plus_drift_unknown() -> None:
    entries = list_recent_changes([_release("v2"), _release("v1")])

    assert [e.drift for e in entries] == ["unknown", "unknown"]
    assert all(e.commits_index_ahead is None and e.commits_package_ahead is None for e in entries)
    assert all(e.in_index is None and e.source == "release" for e in entries)


# --- the install notice ------------------------------------------------------------------------


def _notice(inputs: ProductReferenceInputs) -> tuple[str, str | None]:
    content = get_product_reference(inputs, "install")
    assert isinstance(content, ReferenceContent)
    return content.text, content.notice


def test_the_install_notice_says_the_package_can_lack_members_and_spells_out_git_clone(
    oracle: Oracle,
) -> None:
    indexed = oracle.commit["c5"]
    inputs = ProductReferenceInputs(
        manifest_text=_PYPROJECT_26_8,
        platform="python",
        source_repository=REPOSITORY,
        source_ref=indexed,
        drift=oracle.record("v26.8.0", "c5"),
    )

    text, notice = _notice(inputs)

    assert text == "pip install aspose-slides-foss"
    assert notice is not None
    assert "can lack types and members" in notice
    assert "3 commits ahead" in notice
    assert f"git clone {REPOSITORY_URL}" in notice
    assert f"git checkout {indexed}" in notice
    # The existing pin notice is still there, first.
    assert notice.startswith(_install_pin_notice("pypi", "aspose-slides-foss", REPOSITORY, indexed))


def test_the_go_notice_spells_out_go_get_at_the_commit(oracle: Oracle) -> None:
    indexed = oracle.commit["c5"]
    inputs = ProductReferenceInputs(
        manifest_text=_GO_MOD,
        platform="go",
        source_repository="aspose-pdf-foss/aspose-pdf-foss-go",
        source_ref=indexed,
        drift=oracle.record("v26.8.0", "c5"),
    )

    _text, notice = _notice(inputs)

    assert notice is not None
    assert f"go get github.com/aspose-pdf-foss/aspose-pdf-foss-go@{indexed}" in notice
    assert "can lack types and members" in notice
    assert f"git checkout {indexed}" in notice


def test_a_package_ahead_notice_warns_that_the_index_lacks_members_without_a_build_recipe(
    oracle: Oracle,
) -> None:
    indexed = oracle.commit["c2"]
    inputs = ProductReferenceInputs(
        manifest_text=_GO_MOD,
        platform="go",
        source_repository="aspose-pdf-foss/aspose-pdf-foss-go",
        source_ref=indexed,
        drift=oracle.record("v26.9.0", "c2"),
    )

    _text, notice = _notice(inputs)

    assert notice is not None
    assert "2 commits ahead of the indexed commit" in notice
    assert "does not describe" in notice
    assert "git clone" not in notice


def test_a_cpp_product_gets_fetchcontent_at_the_commit(oracle: Oracle) -> None:
    indexed = oracle.commit["c5"]
    inputs = ProductReferenceInputs(
        manifest_text=_CMAKE,
        platform="cpp",
        source_repository="aspose-3d-foss/Aspose.3D-FOSS-for-Cpp",
        source_ref=indexed,
        drift=oracle.record("v26.8.0", "c5"),
    )

    content = get_product_reference(inputs, "install")

    assert isinstance(content, NotAvailable)
    assert "FetchContent_Declare" in content.reason
    assert f"GIT_TAG {indexed}" in content.reason
    assert f"git checkout {indexed}" in content.reason


def test_no_record_an_unknown_record_or_a_record_for_another_pin_leave_the_notice_unchanged(
    oracle: Oracle,
) -> None:
    indexed = oracle.commit["c5"]
    base = dict(
        manifest_text=_PYPROJECT_26_8, platform="python", source_repository=REPOSITORY, source_ref=indexed
    )
    legacy = _install_pin_notice("pypi", "aspose-slides-foss", REPOSITORY, indexed)

    for drift in (
        None,
        DriftRecord(indexed_commit=indexed, tag="v26.8.0", error="URLError"),
        oracle.record("v26.8.0", "c3"),
        oracle.record("v26.9.0", "c4"),  # in sync: nothing to disclose
    ):
        _text, notice = _notice(ProductReferenceInputs(**base, drift=drift))  # type: ignore[arg-type]
        assert notice == legacy


# --- the real entrypoints -----------------------------------------------------------------------


def test_the_server_wrappers_pass_the_drift_through_to_the_tool_answers(
    tmp_path: Path, oracle: Oracle
) -> None:
    indexed = oracle.commit["c5"]
    store = _publish(tmp_path / "m", [_symbol("Alpha", indexed)])
    record = oracle.record("v26.8.0", "c5")
    inputs = ProductReferenceInputs(
        manifest_text=_PYPROJECT_26_8,
        platform="python",
        source_repository=REPOSITORY,
        source_ref=indexed,
        drift=record,
    )
    releases = [_release("v26.8.0", drift=record)]

    freshness = mcp_server._to_jsonable(
        mcp_server._wrap_report_index_freshness(store, SCOPE, inputs, releases)()
    )
    changes = mcp_server._to_jsonable(
        mcp_server._wrap_list_recent_changes(releases, "present", store, SCOPE, inputs)(limit=5)
    )
    install = mcp_server._to_jsonable(
        mcp_server._wrap_get_product_reference(inputs, store, SCOPE)(section="install")
    )

    assert freshness["drift"] == "index_ahead_of_package"
    assert freshness["index_ahead_of_package"] is True
    assert freshness["drift_commit_distance"] == 3
    assert freshness["published_version"] == "26.8.0"
    assert changes[0]["drift"] == "index_ahead_of_package"
    assert changes[0]["commits_index_ahead"] == 3
    assert "git clone" in install["notice"]


def test_every_emitter_calls_the_one_verdict_function_in_its_production_file() -> None:
    def source(path: str) -> str:
        return (REPO_ROOT / path).read_text(encoding="utf-8")

    freshness = source("src/foss_mcp/mcp/tools/report_index_freshness.py")
    changes = source("src/foss_mcp/mcp/tools/list_recent_changes.py")
    reference = source("src/foss_mcp/mcp/tools/get_product_reference.py")
    writer = source("infra/fetch_recent_releases.py")

    assert re.search(r"\bdrift_from_record\(", freshness)
    assert re.search(r"\bdrift_from_record\(", changes)
    assert re.search(r"\b_drift_notice\(inputs, ecosystem\)", reference)
    assert re.search(r"\bdrift_from_record\(", reference)
    assert "drift_verdict" in source("src/foss_mcp/mcp/tools/release_alignment.py")
    # drift_from_record is the thin wrapper that calls drift_verdict.
    assert re.search(r"return drift_verdict\(", source("src/foss_mcp/mcp/tools/release_alignment.py"))
    # The writer's production main() calls the ancestry step.
    assert re.search(r"releases = _with_ancestry\(args, fetched, indexed_commit\)", writer)
    assert re.search(r"\bfetch_drift\(args\.repository", writer)
    assert release_alignment.drift_verdict is drift_verdict
