"""Tests for config/products/cells/rust.yaml (plan 19.2 / TC-031).

Mirrors tests/config/test_product_manifests.py's approach for pdf/net and
tests/config/test_slides_python_manifest.py's approach for slides/python:
the manifest pins the VERIFIED upstream repository name for the cells/rust
pilot. This org's naming is irregular (e.g. pdf/net uses
"Aspose.PDF-FOSS-for-.NET" with a dot before "NET"), so a pattern-derived
name is not trustworthy for every pilot. The test below therefore asserts
the verified literal string directly; it must NEVER reconstruct the expected
value from `family`/`platform`, because doing so would silently launder
exactly the bug this card exists to prevent.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA_PATH = REPO_ROOT / "schemas" / "product" / "product-manifest.schema.json"
MANIFEST_PATH = REPO_ROOT / "config" / "products" / "cells" / "rust.yaml"

# VERIFIED upstream `full_name`, re-checked via a fresh anonymous
# `gh api repos/aspose-cells-foss/Aspose.Cells-FOSS-for-Rust` read on
# 2026-09-24 (public, size ~245KB; default_branch is `master`, the only one
# of the 7 pilots where this differs from `main`). Pinned here literally on
# purpose.
EXPECTED_REPOSITORY = "aspose-cells-foss/Aspose.Cells-FOSS-for-Rust"


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
    guards against, since this org's naming is irregular and no pattern
    predicts every pilot repo's exact name."""
    manifest = _load_manifest()
    assert manifest["repository"] == EXPECTED_REPOSITORY


def test_family_and_platform_are_cells_and_rust():
    manifest = _load_manifest()
    assert manifest["family"] == "cells"
    assert manifest["platform"] == "rust"


def test_manifest_has_a_real_schedule_with_cron():
    manifest = _load_manifest()
    schedule = manifest["schedule"]
    assert isinstance(schedule, dict)
    assert isinstance(schedule.get("cron"), str) and len(schedule["cron"]) >= 9


def test_schedule_cron_hour_differs_from_pdf_net_and_slides_python():
    """pdf/net ingests at 04:00 UTC and slides/python at 06:00 UTC (see
    config/products/pdf/net.yaml and config/products/slides/python.yaml).
    This manifest must use a distinct hour so ingestion schedules do not
    collide."""
    manifest = _load_manifest()
    assert manifest["schedule"]["cron"] != "0 4 * * *"
    assert manifest["schedule"]["cron"] != "0 6 * * *"


def test_storage_topology_is_embedded():
    """storage_topology is ALREADY DECIDED (docs/DECISION_LOG.md's
    2026-09-24 'TC-004's topology rule generalized to all pilots' entry) -
    every pilot gets `embedded`. This is not a placeholder and must not be
    'undecided_pending_TC-004'."""
    manifest = _load_manifest()
    assert manifest["storage_topology"] == "embedded"
