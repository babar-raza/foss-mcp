"""G3/TC-657 (REQ-G3-012): 3 real round-3 audit regressions in report_index_freshness's
``registry_served_version`` fallback branch (docs/round3/issues.json: EML-NET-007, NOTE-PY-010,
SLIDES-JAVA-004), all one shared root cause - the branch this program built as I7/I8
(TC-621/TC-622):

1. SLIDES-JAVA-004: a confident fresh/stale verdict from this branch did not populate
   drift/published_version/index_ahead_of_package/package_ahead_of_index the way the sibling
   git_ancestry branch already does for an equivalent verdict. Fixed by deriving the same
   evidence shape from the version comparison itself (``_registry_version_drift``), since no
   git-ancestry data exists at this branch (drift_state is always DRIFT_UNKNOWN by the time it is
   reached - any non-unknown ancestry drift would already have returned via git_ancestry above).

2. NOTE-PY-010: the tool's self-description (both ``report_index_freshness``'s own docstring and
   ``server.py``'s real MCP-facing description) claimed "it never guesses" unconditionally, stale
   since this confidence-graded fallback branch was added.

3. EML-NET-007: ``indexed_version`` comes from the packaging manifest's own declared version,
   which for some pilots (confirmed: email/net's real .csproj declares a never-bumped placeholder
   ``0.1.0`` while the real served version is ``26.7.0``) cannot be trusted to mean anything.
   Fixed with a major-version-agreement guard before trusting the comparison - the SAME guard
   ``release_alignment.release_in_index`` already applies to a release tag against the manifest
   version (a calendar-style tag against a ``0.1.0`` manifest has a different major and is never
   compared there either). A major mismatch here now falls through to the honest "unknown"
   fallback instead of a confident-but-meaningless verdict; a pilot whose manifest version has the
   SAME major as the registry's (confirmed common - 29 of 32 demo pilots with a manifest version
   at all use this project's real 26.x calendar scheme) still gets a real, confident verdict.

Offline, no network - reuses the existing freshness test fixtures/helpers
(``tests/mcp/tools/test_freshness_provenance.py``'s ``_publish``/``_symbol``,
``test_freshness_tristate.py``'s ``_PYTHON_26_7``) plus a real end-to-end load of email/net's own
committed sidecars under ``infra/demo-manifests/`` for the EML-NET-007 case specifically.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import foss_mcp.mcp.tools.report_index_freshness as report_index_freshness_module
from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import DeploymentConfig, Scope
from foss_mcp.mcp.server import TOOL_DESCRIPTIONS
from foss_mcp.mcp.tools.get_product_reference import ProductReferenceInputs
from foss_mcp.mcp.tools.release_alignment import (
    DRIFT_IN_SYNC,
    INDEX_AHEAD,
    PACKAGE_AHEAD,
    package_version,
    parse_release_version,
)
from foss_mcp.mcp.tools.report_index_freshness import report_index_freshness
from tests.mcp.tools.test_freshness_provenance import SYMBOL_COMMIT, _publish, _symbol
from tests.mcp.tools.test_freshness_tristate import _PYTHON_26_7

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"

SCOPE = Scope(family="pdf", platform="net")

sys.path.insert(0, str(REPO_ROOT / "infra"))
import serve_http  # noqa: E402


def _single(tmp_path: Path) -> GenerationManifestStore:
    return _publish(tmp_path / "m", [_symbol("Alpha", SYMBOL_COMMIT), _symbol("Beta", SYMBOL_COMMIT)])


def _report(store: GenerationManifestStore, **kwargs: object):
    return report_index_freshness(store, SCOPE, "self_extracted", None, **kwargs)  # type: ignore[arg-type]


# --- (1) SLIDES-JAVA-004: the registry_served_version branch's confident verdicts now carry ------
# --- the same evidence fields the sibling git_ancestry branch's confident verdicts already carry -


def test_slides_java_004_confident_stale_verdict_populates_drift_evidence(tmp_path: Path) -> None:
    store = _single(tmp_path)

    report = _report(store, product_reference=_PYTHON_26_7, registry_served_version=(26, 8, 0))

    assert report.freshness_state == "stale"
    assert report.freshness_basis == "registry_served_version"
    # This was the real gap: these fields previously stayed at their all-unknown defaults
    # (DRIFT_UNKNOWN / None / None / None) for every verdict from this branch.
    assert report.drift == PACKAGE_AHEAD
    assert report.index_ahead_of_package is False
    assert report.package_ahead_of_index is True
    assert report.published_version == "26.8.0"
    assert report.drift_message is not None
    assert "26.8.0" in report.drift_message


def test_slides_java_004_confident_fresh_in_sync_verdict_populates_drift_evidence(tmp_path: Path) -> None:
    store = _single(tmp_path)

    report = _report(store, product_reference=_PYTHON_26_7, registry_served_version=(26, 7, 0))

    assert report.freshness_state == "fresh"
    assert report.freshness_basis == "registry_served_version"
    assert report.drift == DRIFT_IN_SYNC
    assert report.index_ahead_of_package is False
    assert report.package_ahead_of_index is False
    assert report.drift_commit_distance == 0
    assert report.published_version == "26.7.0"


def test_slides_java_004_confident_fresh_index_ahead_verdict_populates_drift_evidence(
    tmp_path: Path,
) -> None:
    """The exact SLIDES-JAVA-004 shape: the registry serves an OLDER version than the manifest
    declares, so the verdict is a confident "fresh" - but previously with no supporting evidence
    at all, which is what the audit's "confident-but-unsupported" wording describes."""
    store = _single(tmp_path)

    report = _report(store, product_reference=_PYTHON_26_7, registry_served_version=(26, 6, 0))

    assert report.freshness_state == "fresh"
    assert report.freshness_basis == "registry_served_version"
    assert report.drift == INDEX_AHEAD
    assert report.index_ahead_of_package is True
    assert report.package_ahead_of_index is False
    assert report.published_version == "26.6.0"


def test_slides_java_004_mixed_precision_verdict_still_populates_drift_evidence(tmp_path: Path) -> None:
    """TC-629's own mixed-precision regression (indexed "26.7", registry (26, 7, 0) - the same
    version at different precision) must also come out "fresh" AND carry drift evidence now."""
    store = _single(tmp_path)
    short = ProductReferenceInputs(
        manifest_text='[project]\nname = "x"\nversion = "26.7"\n', platform="python"
    )

    report = _report(store, product_reference=short, registry_served_version=(26, 7, 0))

    assert report.freshness_state == "fresh"
    assert report.drift == DRIFT_IN_SYNC
    assert report.index_ahead_of_package is False
    assert report.package_ahead_of_index is False


# --- (2) NOTE-PY-010: the tool's self-description no longer claims "never guesses" ----------------
# --- unconditionally, in either of the two real places it was said --------------------------------


def test_note_py_010_server_tool_description_no_longer_claims_never_guesses_unconditionally() -> None:
    description = TOOL_DESCRIPTIONS["report_index_freshness"]

    assert "it never guesses" not in description
    # The honest replacement must actually name the graded fallback this card fixes, not just
    # delete the old claim.
    assert "registry" in description.lower()
    assert "placeholder" in description.lower() or "cannot be trusted" in description.lower()


def test_note_py_010_module_docstring_no_longer_claims_never_guesses_unconditionally() -> None:
    function_doc = report_index_freshness.__doc__ or ""
    module_doc = report_index_freshness_module.__doc__ or ""

    assert "never guesses freshness" not in function_doc
    assert "never guesses freshness" not in module_doc
    assert "registry_served_version" in function_doc


# --- (3) EML-NET-007: a manifest version with a different major than the registry's own served ---
# --- version is now reported "unknown", never a confident-but-meaningless verdict -----------------


def test_eml_net_007_real_baked_sidecar_now_reports_unknown_not_a_false_stale(tmp_path: Path) -> None:
    """The real, reproducible EML-NET-007 case: email/net's own committed sidecars
    (infra/demo-manifests/product_reference_email_net.json declares manifest version "0.1.0";
    infra/demo-manifests/package_registry_email_net.json's real served_version is "26.7.0").
    Before this fix, the registry_served_version branch compared these directly and produced a
    confident, wrong "stale". It must now report "unknown"."""
    config = DeploymentConfig(family="email", platform="net")
    inputs = serve_http._serving_product_reference_inputs(DEMO_MANIFESTS, config)
    assert inputs is not None
    # Confirm the real data shape this card's own diagnosis describes, so this test fails loudly
    # (not silently passing for the wrong reason) if the committed sidecars ever change.
    assert inputs.install_verified_served_version == "26.7.0"
    assert package_version(inputs) == (0, 1, 0)

    manifests = tmp_path / "manifests"
    shutil.copytree(DEMO_MANIFESTS / "email__net__self_extracted", manifests / "email__net__self_extracted")
    store = GenerationManifestStore(manifests)
    registry_served_version = parse_release_version(inputs.install_verified_served_version)

    report = report_index_freshness(
        store,
        Scope(family="email", platform="net"),
        "self_extracted",
        None,
        pinned_library_commit=inputs.source_ref,
        product_reference=inputs,
        releases=(),
        registry_served_version=registry_served_version,
    )

    assert report.freshness_state == "unknown"
    assert report.stale is True
    assert "0.1.0" not in report.reason or "stale" not in report.reason.lower()


# --- (3b) G3/TC-712, EML-NET-005: the real registry-served version is disclosed even though -----
# --- EML-NET-007's own major-mismatch guard above correctly keeps the verdict "unknown" ----------


def test_eml_net_005_real_baked_sidecar_now_populates_published_version(tmp_path: Path) -> None:
    """The real, reproducible EML-NET-005 case, against the same committed email/net sidecars as
    EML-NET-007 above: before this card, ``published_version`` stayed ``None`` even though the
    real registry-served version (26.7.0) was resolved and in scope the whole time - a pure
    missing-fact gap, not a wrong-verdict bug (round-2's own finding: "zero practical harm", the
    indexed API matched the installed 26.7.0 package exactly). The verdict stays "unknown",
    unchanged; only published_version is now populated with the real registry text."""
    config = DeploymentConfig(family="email", platform="net")
    inputs = serve_http._serving_product_reference_inputs(DEMO_MANIFESTS, config)
    assert inputs is not None
    assert inputs.install_verified_served_version == "26.7.0"
    assert package_version(inputs) == (0, 1, 0)

    manifests = tmp_path / "manifests"
    shutil.copytree(DEMO_MANIFESTS / "email__net__self_extracted", manifests / "email__net__self_extracted")
    store = GenerationManifestStore(manifests)
    registry_served_version = parse_release_version(inputs.install_verified_served_version)

    report = report_index_freshness(
        store,
        Scope(family="email", platform="net"),
        "self_extracted",
        None,
        pinned_library_commit=inputs.source_ref,
        product_reference=inputs,
        releases=(),
        registry_served_version=registry_served_version,
    )

    # The verdict EML-NET-007 established is untouched.
    assert report.freshness_state == "unknown"
    assert report.stale is True
    # The real gap this card closes: published_version is no longer null.
    assert report.freshness_basis == "registry_served_version_major_mismatch"
    assert report.published_version == "26.7.0"
    # No other evidence field is fabricated - the legacy DRIFT_UNKNOWN shape is kept verbatim
    # apart from the one overridden key.
    assert report.drift == "unknown"
    assert report.index_ahead_of_package is None
    assert report.package_ahead_of_index is None
    assert report.drift_commit_distance is None
    assert report.drift_message is None


def test_eml_net_007_major_mismatch_registry_higher_falls_through_to_unknown(tmp_path: Path) -> None:
    store = _single(tmp_path)
    placeholder = ProductReferenceInputs(
        manifest_text='[project]\nname = "x"\nversion = "0.1.0"\n', platform="python"
    )

    report = _report(store, product_reference=placeholder, registry_served_version=(26, 7, 0))

    assert report.freshness_state == "unknown"
    # G3/TC-712 (EML-NET-005): the verdict itself is still the honest "unknown" fallback this card
    # (EML-NET-007) established - but the real registry-served version is now disclosed in
    # published_version rather than silently staying null, via its own dedicated basis.
    assert report.freshness_basis == "registry_served_version_major_mismatch"
    assert report.published_version == "26.7.0"
    # No confident-but-meaningless verdict leaks through, and no fabricated drift evidence either.
    assert report.drift == "unknown"
    assert report.index_ahead_of_package is None
    assert report.package_ahead_of_index is None


def test_eml_net_007_major_mismatch_registry_lower_also_falls_through_to_unknown(tmp_path: Path) -> None:
    """The guard is symmetric: it does not matter which side has the "placeholder-looking"
    major - a major mismatch in EITHER direction is untrustworthy, not just the direction the
    real email/net case happens to exercise."""
    store = _single(tmp_path)
    inflated = ProductReferenceInputs(
        manifest_text='[project]\nname = "x"\nversion = "99.0.0"\n', platform="python"
    )

    report = _report(store, product_reference=inflated, registry_served_version=(26, 7, 0))

    assert report.freshness_state == "unknown"
    # G3/TC-712: symmetric with the "registry lower" case above - the real registry version is
    # still disclosed in published_version even when the mismatch runs in the other direction.
    assert report.freshness_basis == "registry_served_version_major_mismatch"
    assert report.published_version == "26.7.0"


# --- (4) the mechanism is NOT blinded for pilots whose manifest version is real -------------------


def test_a_pilot_with_a_meaningful_matching_major_manifest_version_still_gets_a_confident_verdict(
    tmp_path: Path,
) -> None:
    """The fix narrows the branch only for a major-version mismatch; a manifest declaring a real
    version that tracks the registry's own numbering (the common case - this project's own real
    pilots overwhelmingly use a shared 26.x calendar scheme) must keep getting a real, confident
    verdict from this branch, exactly as before this card."""
    store = _single(tmp_path)

    stale = _report(store, product_reference=_PYTHON_26_7, registry_served_version=(26, 9, 0))
    fresh = _report(store, product_reference=_PYTHON_26_7, registry_served_version=(26, 7, 0))

    assert stale.freshness_state == "stale"
    assert stale.freshness_basis == "registry_served_version"
    assert fresh.freshness_state == "fresh"
    assert fresh.freshness_basis == "registry_served_version"


def test_no_indexed_version_still_falls_through_to_unknown_unchanged(tmp_path: Path) -> None:
    """Pre-existing behavior (TC-621): no manifest version at all is still the legacy unknown
    fallback, untouched by this card's major-version guard (there is no major to compare)."""
    store = _single(tmp_path)

    report = _report(store, registry_served_version=(26, 8, 0))

    assert report.freshness_state == "unknown"
    assert report.freshness_basis == "none"
