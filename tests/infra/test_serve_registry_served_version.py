"""G3/TC-622 (REQ-G3-012), part (a)/(b): the real ``served_version`` field infra/verify_package_registry.py's
``main()`` writes into the package-registry sidecar now reaches ``ProductReferenceInputs.install_verified_served_version``
through the real ``_merge_registry_verification`` function - the same merge TC-275's own
``test_serving_product_reference_inputs_merges_a_present_registry_verification_sidecar`` already exercises for
``verified``/``coordinate``/``checked_at``, extended to the field this card adds.

Reuses ``tests/infra/test_serve_http.py``'s own ``_write_manifest_sidecar`` fixture-building helper (a real
manifest sidecar on disk) and ``test_serve_sidecar_tolerance.py``'s own wrong-type rejection convention (one
WARNING log record, the answers fall back to "never verified"), rather than inventing new helpers.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPO_ROOT / "infra"))
import serve_http  # noqa: E402

from foss_mcp.mcp.routing import DeploymentConfig  # noqa: E402
from tests.infra.test_serve_http import _write_manifest_sidecar  # noqa: E402

CONFIG = DeploymentConfig(family="pdf", platform="net")
MANIFEST_TEXT = "<Project><PropertyGroup><PackageId>Aspose.PDF-FOSS</PackageId></PropertyGroup></Project>"


def _write_registry_sidecar(manifests_dir: Path, payload: dict) -> None:
    (manifests_dir / serve_http.verify_package_registry_sidecar_name("pdf", "net")).write_text(
        json.dumps(payload), encoding="utf-8"
    )


# --- (a) a real served_version string reaches install_verified_served_version exactly ------------


def test_a_served_version_string_reaches_install_verified_served_version(tmp_path: Path) -> None:
    _write_manifest_sidecar(tmp_path, "pdf", "net", MANIFEST_TEXT)
    _write_registry_sidecar(
        tmp_path,
        {
            "ecosystem": "nuget",
            "coordinate": "Aspose.PDF-FOSS",
            "verified": True,
            "checked_at": "2026-10-08T00:00:00+00:00",
            "served_version": "26.8.0",
        },
    )

    result = serve_http._serving_product_reference_inputs(tmp_path, CONFIG)

    assert result is not None
    assert result.install_verified_served_version == "26.8.0"
    # the 3 existing fields are unaffected by this card
    assert result.install_verified is True
    assert result.install_verified_coordinate == "Aspose.PDF-FOSS"
    assert result.install_verified_checked_at == "2026-10-08T00:00:00+00:00"


# --- (b) served_version: null (the verified=False shape main() itself writes) is None ------------


def test_a_null_served_version_is_none(tmp_path: Path) -> None:
    _write_manifest_sidecar(tmp_path, "pdf", "net", MANIFEST_TEXT)
    _write_registry_sidecar(
        tmp_path,
        {
            "ecosystem": "nuget",
            "coordinate": "Aspose.PDF-FOSS",
            "verified": False,
            "checked_at": "2026-10-08T00:00:00+00:00",
            "served_version": None,
        },
    )

    result = serve_http._serving_product_reference_inputs(tmp_path, CONFIG)

    assert result is not None
    assert result.install_verified_served_version is None
    assert result.install_verified is False
    assert result.install_verified_coordinate == "Aspose.PDF-FOSS"


# --- (c) a sidecar written before this card (no served_version key at all) is accepted exactly ---
# --- as before -------------------------------------------------------------------------------------


def test_a_sidecar_written_before_this_card_with_no_served_version_key_is_accepted_as_before(
    tmp_path: Path,
) -> None:
    _write_manifest_sidecar(tmp_path, "pdf", "net", MANIFEST_TEXT)
    # Constructed literally without the key, simulating a sidecar written before this card.
    _write_registry_sidecar(
        tmp_path,
        {
            "ecosystem": "nuget",
            "coordinate": "Aspose.PDF-FOSS",
            "verified": True,
            "checked_at": "2026-10-08T00:00:00+00:00",
        },
    )

    result = serve_http._serving_product_reference_inputs(tmp_path, CONFIG)

    assert result is not None
    assert result.install_verified_served_version is None
    # every OTHER field is unaffected
    assert result.install_verified is True
    assert result.install_verified_coordinate == "Aspose.PDF-FOSS"
    assert result.install_verified_checked_at == "2026-10-08T00:00:00+00:00"
    assert result.manifest_text == MANIFEST_TEXT


# --- (d) a wrong-typed served_version is rejected the same way the 3 existing fields already are -


@pytest.mark.parametrize("bad_served_version", [26, ["26.8.0"]])
def test_a_wrong_typed_served_version_is_rejected_like_the_existing_fields(
    bad_served_version: object, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_manifest_sidecar(tmp_path, "pdf", "net", MANIFEST_TEXT)
    _write_registry_sidecar(
        tmp_path,
        {
            "ecosystem": "nuget",
            "coordinate": "Aspose.PDF-FOSS",
            "verified": True,
            "checked_at": "2026-10-08T00:00:00+00:00",
            "served_version": bad_served_version,
        },
    )

    with caplog.at_level(logging.WARNING):
        result = serve_http._serving_product_reference_inputs(tmp_path, CONFIG)

    assert result is not None
    # Mirrors the existing wrong-typed-field fallback exactly: every registry-derived field,
    # not only served_version, falls back to "never verified" - the whole sidecar is ignored.
    assert result.install_verified is None
    assert result.install_verified_coordinate is None
    assert result.install_verified_checked_at is None
    assert result.install_verified_served_version is None
    records = [
        record
        for record in caplog.records
        if record.levelno >= logging.WARNING and hasattr(record, "sidecar")
    ]
    (record,) = records
    assert record.sidecar == "package_registry"  # type: ignore[attr-defined]
    assert record.levelno == logging.WARNING
