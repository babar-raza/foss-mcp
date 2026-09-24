"""Tests for config/products/pdf/go.yaml (plan / TC-037).

Mirrors tests/config/test_product_manifests.py's approach for pdf/net,
tests/config/test_slides_python_manifest.py's approach for slides/python,
and tests/config/test_cells_rust_manifest.py's approach for cells/rust: the
manifest pins the VERIFIED upstream repository name for the pdf/go pilot.
This org's naming is irregular - pdf/net uses "Aspose.PDF-FOSS-for-.NET"
(dot before "NET"), while pdf/go uses "Aspose-PDF-FOSS-for-Go" (a DASH
before "PDF"), unlike every other pdf/* pilot's dot naming - so a
pattern-derived name is not trustworthy. The test below therefore asserts
the verified literal string directly; it must NEVER reconstruct the
expected value from `family`/`platform`, because doing so would silently
launder exactly the bug this card exists to prevent.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA_PATH = REPO_ROOT / "schemas" / "product" / "product-manifest.schema.json"
MANIFEST_PATH = REPO_ROOT / "config" / "products" / "pdf" / "go.yaml"

# VERIFIED upstream `full_name`, re-checked via a fresh anonymous
# `gh api repos/aspose-pdf-foss/Aspose-PDF-FOSS-for-Go` read on 2026-09-24
# (public, default branch main, size ~49MB - by far the largest checkout of
# all 7 pilots). Pinned here literally on purpose.
EXPECTED_REPOSITORY = "aspose-pdf-foss/Aspose-PDF-FOSS-for-Go"


def _load_schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _load_manifest():
    return yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_manifest_file_exists():
    assert MANIFEST_PATH.is_file(), f"{MANIFEST_PATH} does not exist"


def test_manifest_validates_against_product_manifest_schema():
    schema = _load_schema()
    manifest = _load_manifest()
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(manifest))
    assert errors == [], f"manifest failed schema validation: {errors}"


def test_repository_is_the_exact_verified_upstream_full_name():
    """Asserts the LITERAL verified string. Do not derive this from
    `family`/`platform` - that reconstruction is precisely the bug this test
    guards against, since this org's naming is irregular (a dash before
    'PDF' here, unlike every other pdf/* pilot's dot naming) and no pattern
    predicts every pilot repo's exact name."""
    manifest = _load_manifest()
    assert manifest["repository"] == EXPECTED_REPOSITORY


def test_family_and_platform_are_pdf_and_go():
    manifest = _load_manifest()
    assert manifest["family"] == "pdf"
    assert manifest["platform"] == "go"


def test_manifest_has_a_real_schedule_with_cron():
    manifest = _load_manifest()
    schedule = manifest["schedule"]
    assert isinstance(schedule, dict)
    assert isinstance(schedule.get("cron"), str) and len(schedule["cron"]) >= 9


def test_schedule_cron_hour_differs_from_other_pilots():
    """pdf/net ingests at 04:00 UTC, slides/python at 06:00 UTC, and
    cells/rust at 08:00 UTC (see config/products/pdf/net.yaml,
    config/products/slides/python.yaml, and config/products/cells/rust.yaml).
    This manifest must use a distinct hour so ingestion schedules do not
    collide."""
    manifest = _load_manifest()
    assert manifest["schedule"]["cron"] != "0 4 * * *"
    assert manifest["schedule"]["cron"] != "0 6 * * *"
    assert manifest["schedule"]["cron"] != "0 8 * * *"


def test_storage_topology_is_embedded():
    """storage_topology is ALREADY DECIDED (docs/DECISION_LOG.md's
    2026-09-24 'TC-004's topology rule generalized to all pilots' entry) -
    every pilot gets `embedded`. This is not a placeholder and must not be
    'undecided_pending_TC-004'."""
    manifest = _load_manifest()
    assert manifest["storage_topology"] == "embedded"
