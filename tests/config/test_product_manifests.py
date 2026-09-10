"""Tests for config/products/*.yaml product manifests (plan 19.2 / TC-003).

The manifest at config/products/pdf/net.yaml pins the FIRST real product's
exact upstream repository name. This org's naming is irregular - e.g.
pdf/go is "Aspose-PDF-FOSS-for-Go" (a dash) while pdf/net is
"Aspose.PDF-FOSS-for-.NET" (a dot before "NET") - so a name derived from a
family/platform pattern is wrong for 2 of the 7 pilot repos. The test below
therefore asserts the verified literal string directly; it must NEVER
reconstruct the expected value from `family`/`platform`, because doing so
would silently launder exactly the bug this card exists to prevent.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA_PATH = REPO_ROOT / "schemas" / "product" / "product-manifest.schema.json"
MANIFEST_PATH = REPO_ROOT / "config" / "products" / "pdf" / "net.yaml"

# VERIFIED upstream `full_name`, confirmed via `gh api` during planning
# (public, 7.4MB, default branch main). Pinned here literally on purpose.
EXPECTED_REPOSITORY = "aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET"


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
    guards against, since 2 of the 7 pilot repos use an irregular dash form
    that no pattern predicts."""
    manifest = _load_manifest()
    assert manifest["repository"] == EXPECTED_REPOSITORY


def test_family_and_platform_are_pdf_and_net():
    manifest = _load_manifest()
    assert manifest["family"] == "pdf"
    assert manifest["platform"] == "net"


def test_manifest_has_a_real_schedule_with_cron():
    manifest = _load_manifest()
    schedule = manifest["schedule"]
    assert isinstance(schedule, dict)
    assert isinstance(schedule.get("cron"), str) and len(schedule["cron"]) >= 9


def test_storage_topology_is_decided_not_placeholder():
    """TC-003a: the previous version of this test pinned the transient
    bootstrap value 'undecided_pending_TC-004', asserting a moment rather
    than a contract. TC-004 legitimately decided storage_topology (see
    docs/DECISION_LOG.md), so that placeholder assertion was removed. The
    real contract is that storage_topology is a real, non-empty decision -
    do not reintroduce the placeholder-literal assertion here."""
    manifest = _load_manifest()
    storage_topology = manifest["storage_topology"]
    assert isinstance(storage_topology, str) and storage_topology.strip()
    assert storage_topology != "undecided_pending_TC-004"
