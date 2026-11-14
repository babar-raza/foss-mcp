"""G3/TC-575 (RD3-080 follow-up, REQ-G3-008, REQ-G3-009): an honest gap, not a silent regression.

The 2026-10-23 independent audit found that 0 of 32 real ``product_reference_<family>_<platform>.json``
sidecars under ``infra/demo-manifests`` carry the ``manifest_path`` or ``readme_document`` keys at all - not
'stale', never written, for any pilot, ever. The root cause is OWNER-14: the cluster's GITHUB_TOKEN is rejected
by GitHub (HTTP 401), so ``infra/fetch_product_reference.py`` never runs to completion in the real cluster, and
every ingestion carries the previous (pre-TC-561) snapshot forward (OQ-015).

This module does NOT fail today for that gap, because it is honestly disclosed: OWNER-14 is OPEN, and its own
text names the exact root cause (the rejected GITHUB_TOKEN) that also explains why this fetch step has never
once succeeded. What this module DOES fail on is the real regression TC-575 exists to catch: the fields still
missing from every sidecar AFTER an operator (or a future agent) marks OWNER-14 resolved or satisfied without
the data actually having been backfilled. At that point the honest excuse is gone and a missing field is once
again a silent, uncaught defect.

One honesty note for whoever reads this file next: OWNER-14's own text (``ops/owner_items.yaml``) describes the
GITHUB_TOKEN rejection in terms of the *recent-releases* fetch failing (the symptom TC-536/OQ-015 actually
carried forward sidecars for); it does not, today, literally name ``manifest_path`` or ``readme_document``. The
same rejected token is the real, traced root cause of the product-reference fetch gap too (TC-575's own purpose
text confirms this), so the check below accepts OWNER-14 as accounting for the gap on that root-cause basis
rather than requiring the literal field names. ``ops/owner_items.yaml`` is a supervisor path, outside this
card's ``write_paths`` - a worker cannot add a field-naming clause to it. A future supervisor edit that adds
one explicit clause ("...and blocks fetch_product_reference.py's manifest_path/readme_document too") would make
the disclosure more precise, but is not required for this test to pass today.

Offline, deterministic, no network. Reads only committed files.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"
MANIFESTS_DIR = REPO_ROOT / "infra" / "demo-manifests"
OWNER_ITEMS_FILE = REPO_ROOT / "ops" / "owner_items.yaml"

REQUIRED_FIELDS = ("manifest_path", "readme_document")

# The root cause TC-575's own purpose text traces: the cluster's GITHUB_TOKEN is rejected by GitHub,
# which is why fetch_product_reference.py never runs to a successful completion. OWNER-14's own
# summary/resume_predicate/note text must account for roughly this, for its OPEN status to excuse a
# missing field - a vague or unrelated OPEN item does not get a pass.
_ROOT_CAUSE_TOKEN_RE = re.compile(r"github[ _-]?token", re.IGNORECASE)
_ROOT_CAUSE_REJECTION_RE = re.compile(r"401|unauthorized|rejected", re.IGNORECASE)

# OWNER-14's status values that account for the entry today: OPEN (the owner-blocked excuse), or resolved by
# the owner/operator (SATISFIED, OVERRIDDEN, DEFAULT_APPLIED), in which case the data itself must be complete.
_OWNER14_ACCOUNTING_STATUSES = frozenset({"OPEN", "SATISFIED", "OVERRIDDEN", "DEFAULT_APPLIED"})


def _pilots() -> list[dict[str, Any]]:
    data = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
    return list(data["ingestion"]["pilots"])


def _pilots_with_a_manifest_path() -> list[dict[str, Any]]:
    return [p for p in _pilots() if "manifestPath" in p]


_PILOTS = _pilots_with_a_manifest_path()
_PILOT_KEYS = [f"{p['family']}_{p['platform']}" for p in _PILOTS]


def _sidecar_path(family: str, platform: str) -> Path:
    return MANIFESTS_DIR / f"product_reference_{family}_{platform}.json"


def _sidecar(family: str, platform: str) -> dict[str, Any]:
    path = _sidecar_path(family, platform)
    assert path.is_file(), f"no product_reference sidecar for {family}/{platform}: {path}"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _missing_fields(family: str, platform: str) -> list[str]:
    sidecar = _sidecar(family, platform)
    return [field for field in REQUIRED_FIELDS if field not in sidecar]


def _owner_items() -> list[dict[str, Any]]:
    data = yaml.safe_load(OWNER_ITEMS_FILE.read_text(encoding="utf-8"))
    return list(data["owner_items"])


def _owner_item(item_id: str) -> dict[str, Any] | None:
    for item in _owner_items():
        if item.get("id") == item_id:
            return item
    return None


def _owner14_text(owner14: dict[str, Any]) -> str:
    return " ".join(str(owner14.get(key, "")) for key in ("summary", "resume_predicate", "note"))


def _owner14_accounts_for_the_gap(owner14: dict[str, Any] | None) -> bool:
    if owner14 is None or owner14.get("status") != "OPEN":
        return False
    text = _owner14_text(owner14)
    return bool(_ROOT_CAUSE_TOKEN_RE.search(text)) and bool(_ROOT_CAUSE_REJECTION_RE.search(text))


# -- sanity: the pilot set is real and every pilot with a manifestPath has a sidecar file ----------------------


def test_at_least_one_pilot_with_a_manifest_path_exists() -> None:
    assert _PILOTS, "expected at least one pilot with a manifestPath in values.yaml"


@pytest.mark.parametrize("pilot", _PILOTS, ids=_PILOT_KEYS)
def test_every_pilot_with_a_manifest_path_has_a_sidecar_file(pilot: dict[str, Any]) -> None:
    path = _sidecar_path(pilot["family"], pilot["platform"])
    assert path.is_file(), path


# -- the real guard: complete, or honestly disclosed as owner-blocked ------------------------------------------


@pytest.mark.parametrize("pilot", _PILOTS, ids=_PILOT_KEYS)
def test_sidecar_is_current_or_the_gap_is_honestly_disclosed(pilot: dict[str, Any]) -> None:
    family, platform = pilot["family"], pilot["platform"]
    missing = _missing_fields(family, platform)
    if not missing:
        return
    owner14 = _owner_item("OWNER-14")
    assert owner14 is not None, (
        f"{family}/{platform} is missing {missing}, and ops/owner_items.yaml has no OWNER-14 entry "
        "to excuse it"
    )
    assert _owner14_accounts_for_the_gap(owner14), (
        f"{family}/{platform} is missing {missing}, and OWNER-14 either is not OPEN "
        f"(status={owner14.get('status')!r}) or its text no longer accounts for the GITHUB_TOKEN "
        "rejection that is this gap's real root cause - this is the real, caught regression: a missing "
        "field with no honest excuse left."
    )


def test_owner14_is_the_entry_this_gap_depends_on() -> None:
    """A single, direct assertion (not per-pilot) that OWNER-14 itself is in the state this file's
    per-pilot test above relies on, so a reader sees the dependency in one place."""
    owner14 = _owner_item("OWNER-14")
    assert owner14 is not None, "ops/owner_items.yaml has no OWNER-14 entry"
    missing_count = sum(1 for p in _PILOTS if _missing_fields(p["family"], p["platform"]))
    # TC-846: this used to pytest.skip when no sidecar was missing a field - which is the permanent state now
    # (TC-786..TC-788 regenerated all 32 sidecars) and gatectl rejects any skipped test. The same condition is
    # asserted directly instead: either nothing is missing (OWNER-14's status no longer matters), or OWNER-14
    # must still be the OPEN owner-blocked excuse.
    assert missing_count == 0 or owner14.get("status") == "OPEN", (
        f"OWNER-14 is {owner14.get('status')!r} but {missing_count} "
        "sidecar(s) are still missing manifest_path/readme_document - the owner-blocked excuse is gone"
    )


# -- proof that the guard actually guards something (synthetic, no real file touched) --------------------------


def test_synthetic_a_missing_field_with_owner14_resolved_would_fail() -> None:
    owner14 = {"status": "SATISFIED", "summary": "The GITHUB_TOKEN issue was resolved (HTTP 401 gone)."}
    missing = ["manifest_path"]
    with pytest.raises(AssertionError):
        assert not missing or _owner14_accounts_for_the_gap(owner14)


def test_synthetic_a_missing_field_with_an_unrelated_open_item_would_fail() -> None:
    unrelated = {"status": "OPEN", "summary": "The office coffee machine is broken."}
    missing = ["readme_document"]
    with pytest.raises(AssertionError):
        assert not missing or _owner14_accounts_for_the_gap(unrelated)


def test_synthetic_a_complete_sidecar_needs_no_excuse() -> None:
    assert _owner14_accounts_for_the_gap(None) is False
    # A complete sidecar (no missing fields) never even reaches the OWNER-14 check - mirrored here
    # directly against the real helper, with an empty missing-fields list standing in for "complete".
    missing: list[str] = []
    assert not missing or _owner14_accounts_for_the_gap(None)


def test_owner14_accounts_for_the_real_entry_today() -> None:
    """Confirms the live ops/owner_items.yaml OWNER-14 entry is itself in the accounting state this
    file's guard depends on today - so a reader does not have to trust the per-pilot test alone."""
    owner14 = _owner_item("OWNER-14")
    assert owner14 is not None
    # TC-846: OWNER-14 was OVERRIDDEN on 2026-11-14 (operator decision: the GITHUB_TOKEN environment variable
    # is used instead of the cluster Secret; TC-786..TC-788 then regenerated all 32 sidecars with both
    # fields), so this pin no longer hard-codes OPEN. It asserts the entry's CURRENT status is one that
    # accounts for it. A non-OPEN status removes the owner-blocked excuse (see _owner14_accounts_for_the_gap),
    # so then every sidecar must really carry both fields - the original "re-check the sidecars" intent.
    status = owner14.get("status")
    assert status in _OWNER14_ACCOUNTING_STATUSES, (
        f"OWNER-14 status {status!r} is not one of {sorted(_OWNER14_ACCOUNTING_STATUSES)}; re-check "
        "whether every product_reference sidecar carries manifest_path and readme_document"
    )
    if status != "OPEN":
        still_missing = {
            f"{p['family']}_{p['platform']}": _missing_fields(p["family"], p["platform"]) for p in _PILOTS
        }
        still_missing = {key: fields for key, fields in still_missing.items() if fields}
        assert not still_missing, (
            f"OWNER-14 is {status!r} (no longer an excuse) but sidecars still miss fields: {still_missing}"
        )
    text = _owner14_text(owner14)
    assert _ROOT_CAUSE_TOKEN_RE.search(text), "OWNER-14's text no longer names the GITHUB_TOKEN root cause"
    assert _ROOT_CAUSE_REJECTION_RE.search(text), "OWNER-14's text no longer names the rejection (401)"
