"""KD5 (G3/TC-643, REQ-G3-015): wiring TC-636's known-defects registry into
``report_index_freshness``, giving ``defect_is_stale`` its first real production caller.

No real defect data exists yet (KD6-KD9's job) - every test here proves the WIRING itself is
correct against a real, synthetic, ``tmp_path``-scoped registry file, the same mechanism
``test_known_defects_in_get_symbol.py`` (KD2/TC-639) and ``test_known_defects_in_search_symbols.py``
(KD3/TC-642) already established: ``known_defects.REGISTRY_DIR`` is monkeypatched to a
``tmp_path`` directory for the duration of each test, never the real, committed
``config/known_defects/`` directory.

Offline and deterministic: synthetic generations published through the real
``publish_generation``, reusing ``tests/mcp/tools/test_freshness_provenance.py``'s own
``_publish``/``_symbol``/``SYMBOL_COMMIT`` helpers (the same ones
``test_freshness_registry_version.py`` and ``test_freshness_tristate.py`` already reuse).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from foss_mcp.indexing import known_defects as known_defects_module
from foss_mcp.indexing.known_defects import registry_path_for
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.report_index_freshness import FreshnessReport, report_index_freshness
from tests.mcp.tools.test_freshness_provenance import SYMBOL_COMMIT, _publish, _symbol
from tests.mcp.tools.test_freshness_tristate import _OTHER_COMMIT

SCOPE = Scope(family="pdf", platform="net")


def _single(tmp_path: Path) -> Any:
    return _publish(tmp_path / "m", [_symbol("Alpha", SYMBOL_COMMIT), _symbol("Beta", SYMBOL_COMMIT)])


def _report(store: Any, current: str | None = None, **kwargs: object) -> FreshnessReport:
    return report_index_freshness(store, SCOPE, "self_extracted", current, **kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------------
# registry fixture (the same mechanism KD2/KD3's own test files already use)
# --------------------------------------------------------------------------------------------


@pytest.fixture
def registry_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A ``tmp_path``-scoped stand-in for the real, committed ``config/known_defects/`` directory -
    never the real one. ``REGISTRY_DIR`` is read fresh on every ``load_known_defects`` call, so
    monkeypatching the module attribute is enough; no file exists here unless a test writes one."""
    directory = tmp_path / "known_defects"
    directory.mkdir()
    monkeypatch.setattr(known_defects_module, "REGISTRY_DIR", directory)
    return directory


def _defect(
    defect_id: str,
    *,
    indexed_commit: str | None = None,
    package_versions: list[str] | None = None,
    verified_version: str = "26.7.0",
) -> dict[str, Any]:
    applies_to: dict[str, Any] = {}
    if indexed_commit is not None:
        applies_to["indexed_commit"] = indexed_commit
    if package_versions is not None:
        applies_to["package_versions"] = package_versions
    return {
        "id": defect_id,
        "subject": {"kind": "package", "refs": ["Aspose.Pdf"]},
        "applies_to": applies_to,
        "summary": f"Summary describing {defect_id}.",
        "user_action": f"Recommended action for {defect_id}.",
        "evidence": {
            "repro": f"Repro steps for {defect_id}.",
            "verified_on": {"package": "Aspose.Pdf", "version": verified_version, "date": "2026-10-15"},
        },
        "status": "open",
    }


def _write_registry(
    registry_dir: Path, defects: list[dict[str, Any]], *, family: str = "pdf", platform: str = "net"
) -> None:
    document = {"schema_version": 1, "pilot": f"{family}_{platform}", "defects": defects}
    path = registry_path_for(family, platform, base_dir=registry_dir)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


# --------------------------------------------------------------------------------------------
# commit scoping - exact match against FreshnessReport.indexed_source_commit
# --------------------------------------------------------------------------------------------


def test_commit_scoped_defect_surfaces_when_indexed_commit_matches(
    tmp_path: Path, registry_dir: Path
) -> None:
    store = _single(tmp_path)
    _write_registry(registry_dir, [_defect("PDF-NET-001", indexed_commit=SYMBOL_COMMIT)])

    report = _report(store, SYMBOL_COMMIT)

    assert report.indexed_source_commit == SYMBOL_COMMIT
    assert len(report.known_defects) == 1
    line = report.known_defects[0]
    assert "Summary describing PDF-NET-001." in line
    assert "Recommended action for PDF-NET-001." in line
    assert "Repro steps for PDF-NET-001." in line
    assert "last verified broken on" not in line


def test_commit_scoped_defect_does_not_surface_when_indexed_commit_differs(
    tmp_path: Path, registry_dir: Path
) -> None:
    store = _single(tmp_path)
    _write_registry(registry_dir, [_defect("PDF-NET-002", indexed_commit=_OTHER_COMMIT)])

    report = _report(store, SYMBOL_COMMIT)

    assert report.indexed_source_commit == SYMBOL_COMMIT
    assert report.known_defects == ()


def test_commit_scoped_defect_with_package_versions_also_set_still_requires_commit_match(
    tmp_path: Path, registry_dir: Path
) -> None:
    """A defect with BOTH ``indexed_commit`` and ``package_versions`` set is commit-scoped: it is
    governed only by the commit comparison, never surfacing on package-version grounds alone when
    the commit does not match (the card's own "COMMIT-scoped ... OR a package/commit-scoped
    defect with applies_to.indexed_commit set" rule)."""
    store = _single(tmp_path)
    _write_registry(
        registry_dir,
        [_defect("PDF-NET-003", indexed_commit=_OTHER_COMMIT, package_versions=["26.7.0"])],
    )

    report = _report(
        store,
        SYMBOL_COMMIT,
        registry_served_version=(26, 9, 0),  # would make it "stale" if treated as package-scoped
    )

    assert report.known_defects == ()


# --------------------------------------------------------------------------------------------
# package-version scoping - defect_is_stale's first real production caller
# --------------------------------------------------------------------------------------------


def test_package_version_scoped_defect_surfaces_plain_when_no_served_version_available(
    tmp_path: Path, registry_dir: Path
) -> None:
    store = _single(tmp_path)
    _write_registry(registry_dir, [_defect("PDF-NET-004", package_versions=["26.7.0"])])

    report = _report(store, SYMBOL_COMMIT)  # no registry_served_version given at all

    assert len(report.known_defects) == 1
    line = report.known_defects[0]
    assert "Summary describing PDF-NET-004." in line
    assert "last verified broken on" not in line
    assert "not re-verified on" not in line


def test_package_version_scoped_defect_surfaces_plain_when_served_version_not_stale(
    tmp_path: Path, registry_dir: Path
) -> None:
    store = _single(tmp_path)
    _write_registry(
        registry_dir, [_defect("PDF-NET-005", package_versions=["26.7.0"], verified_version="26.7.0")]
    )

    # Served version equal to the verified-on version: defect_is_stale is False.
    report = _report(store, SYMBOL_COMMIT, registry_served_version=(26, 7, 0))

    assert len(report.known_defects) == 1
    line = report.known_defects[0]
    assert "Summary describing PDF-NET-005." in line
    assert "last verified broken on" not in line


def test_package_version_scoped_defect_surfaces_decision_c_downgrade_when_stale(
    tmp_path: Path, registry_dir: Path
) -> None:
    store = _single(tmp_path)
    _write_registry(
        registry_dir, [_defect("PDF-NET-006", package_versions=["26.7.0"], verified_version="26.7.0")]
    )

    # Served version strictly newer than the verified-on version: defect_is_stale is True.
    report = _report(store, SYMBOL_COMMIT, registry_served_version=(26, 8, 0))

    assert len(report.known_defects) == 1
    line = report.known_defects[0]
    assert "last verified broken on 26.7.0; not re-verified on 26.8.0" in line
    assert "Summary describing PDF-NET-006." in line
    assert "Recommended action for PDF-NET-006." in line
    assert "Repro steps for PDF-NET-006." in line


# --------------------------------------------------------------------------------------------
# no registry file - byte-identical to before this card
# --------------------------------------------------------------------------------------------


def test_no_registry_file_leaves_known_defects_empty_and_report_otherwise_unaffected(
    tmp_path: Path, registry_dir: Path
) -> None:
    # registry_dir exists (fixture creates it) but no file is written for this pilot - valid,
    # not an error (load_known_defects returns []).
    store = _single(tmp_path)

    report = _report(store, SYMBOL_COMMIT)

    assert report.known_defects == ()
    assert report.freshness_state == "fresh"
    assert report.freshness_basis == "caller_commit"
    assert report.indexed_source_commit == SYMBOL_COMMIT


# --------------------------------------------------------------------------------------------
# never-published generation: commit-scoped defects cannot match (no indexed commit at all);
# a package-version-scoped defect still applies, per the established "always applies" default.
# --------------------------------------------------------------------------------------------


def test_never_published_generation_never_surfaces_a_commit_scoped_defect(
    tmp_path: Path, registry_dir: Path
) -> None:
    from foss_mcp.indexing.generation_manifest import GenerationManifestStore

    store = GenerationManifestStore(tmp_path / "empty")
    _write_registry(registry_dir, [_defect("PDF-NET-007", indexed_commit=SYMBOL_COMMIT)])

    report = _report(store, SYMBOL_COMMIT)

    assert report.indexed_source_commit is None
    assert report.known_defects == ()


def test_never_published_generation_still_surfaces_package_version_scoped_defect(
    tmp_path: Path, registry_dir: Path
) -> None:
    from foss_mcp.indexing.generation_manifest import GenerationManifestStore

    store = GenerationManifestStore(tmp_path / "empty")
    _write_registry(registry_dir, [_defect("PDF-NET-008", package_versions=["26.7.0"])])

    report = _report(store, SYMBOL_COMMIT)

    assert report.indexed_source_commit is None
    assert len(report.known_defects) == 1
    assert "Summary describing PDF-NET-008." in report.known_defects[0]


# --------------------------------------------------------------------------------------------
# a commit-scoped defect that matches neither the given current commit never surfaces, while a
# co-resident package-version-scoped defect (which always applies) still does - proving the
# commit mismatch suppresses only that one defect, not the whole disclosure
# --------------------------------------------------------------------------------------------


def test_defect_matching_neither_scoping_condition_never_surfaces(tmp_path: Path, registry_dir: Path) -> None:
    store = _single(tmp_path)
    _write_registry(
        registry_dir,
        [
            _defect("PDF-NET-009", indexed_commit=_OTHER_COMMIT),
            _defect("PDF-NET-010", package_versions=["26.7.0"], verified_version="99.0.0"),
        ],
    )

    # PDF-NET-009's indexed_commit never matches SYMBOL_COMMIT, so it never surfaces. PDF-NET-010
    # is verified_on 99.0.0; a served 26.8.0 is not newer, so defect_is_stale is False and it
    # surfaces with the plain wording.
    report = _report(store, SYMBOL_COMMIT, registry_served_version=(26, 8, 0))

    assert len(report.known_defects) == 1
    line = report.known_defects[0]
    assert "PDF-NET-010" in line
    assert "PDF-NET-009" not in line
    assert "last verified broken on" not in line
