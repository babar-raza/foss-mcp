"""G3/TC-650: round-2 audit disclosures through the EXISTING known_hazards mechanism (G3/TC-582,
``config/products/cells/go.yaml``'s own template; extended into ``get_product_reference``'s
"limitations" rendering by G3/TC-641/KD4).

Closes 7 round-2 audit findings where this project's OWN served ``formats``/``limitations``/
``agent_guidance`` text - not an upstream defect - made a false capability claim the audit
disproved by live reproduction against the actually-installed/published package:
``3D-NET-001`` (config/products/3d/net.yaml), ``HTML-PY-001``/``HTML-PY-005``
(config/products/html/python.yaml), ``PDF-JAVA-003`` (config/products/pdf/java.yaml),
``SLIDES-JAVA-001``/``SLIDES-JAVA-002`` (config/products/slides/java.yaml), and
``SLIDES-PY-002`` (config/products/slides/python.yaml).

This file proves, per pilot: (1) the manifest's new ``known_hazards`` entries validate against
the real schema; (2) a real end-to-end ``get_product_reference(section="limitations")`` call
against the REAL baked sidecar (``infra/demo-manifests``, the exact way the live server loads a
pilot - the same pattern ``tests/mcp/tools/test_product_reference_known_hazards.py`` already
established for cells/go) surfaces the real hazard text, distinctly framed, never marked
``untrusted_upstream_content``; (3) every entry's ``text`` states a real failure mode, trigger,
and safe pattern traceable to its ``docs/round2/issues.json`` record; (4) every entry's
``verified_at`` is a real, valid calendar date (2026-10-29, the round-2 audit's own date, as
KD6-KD9's known_defects data cards for the same audit also record).
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_product_reference import (
    ReferenceContent,
    get_product_reference,
    known_hazards_from_manifest,
)
from foss_mcp.mcp.tools.readme_sections import ReadmeSections, read_readme_sections

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "infra"))
from fetch_product_reference import load_manifest_sidecar  # noqa: E402

MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
CONFIG_PRODUCTS = REPO_ROOT / "config" / "products"
SCHEMA_PATH = REPO_ROOT / "schemas" / "product" / "product-manifest.schema.json"
ROUND2_ISSUES_PATH = REPO_ROOT / "docs" / "round2" / "issues.json"
SOURCE_KIND = "self_extracted"
KNOWN_HAZARD_LEAD = "Known hazard (verified by this project)"
ROUND2_VERIFIED_AT = "2026-10-29"
_WHITESPACE_RE = re.compile(r"\s+")

# (family, platform, issue ids this pilot's manifest discloses, in manifest order).
PILOTS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("3d", "net", ("3D-NET-001",)),
    ("html", "python", ("HTML-PY-001", "HTML-PY-005")),
    ("pdf", "java", ("PDF-JAVA-003",)),
    ("slides", "java", ("SLIDES-JAVA-001", "SLIDES-JAVA-002")),
    ("slides", "python", ("SLIDES-PY-002",)),
)
ALL_ISSUE_IDS: tuple[str, ...] = tuple(issue_id for _, _, ids in PILOTS for issue_id in ids)
PILOT_IDS = [f"{family}/{platform}" for family, platform, _ in PILOTS]


def _schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _real_manifest(family: str, platform: str) -> dict[str, Any]:
    path = CONFIG_PRODUCTS / family / f"{platform}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _real_issue(issue_id: str) -> dict[str, Any]:
    """The real ``docs/round2/issues.json`` record for *issue_id* - the exact record this card's
    hazard text must be traceable to."""
    issues = json.loads(ROUND2_ISSUES_PATH.read_text(encoding="utf-8"))
    for issue in issues:
        if issue.get("id") == issue_id:
            return issue
    raise AssertionError(f"{issue_id} not found in {ROUND2_ISSUES_PATH}")


def _load_baked(tmp_path: Path, family: str, platform: str) -> tuple[Any, ReadmeSections]:
    """``(inputs, readme)`` for *family*/*platform*'s REAL baked pilot (infra/demo-manifests) -
    the exact way the live server loads it, copied to a temporary directory first so nothing
    under infra/demo-manifests is ever opened for writing. Identical pattern to
    ``tests/mcp/tools/test_product_reference_known_hazards.py``'s own ``_load_baked``."""
    scope_dir = f"{family}__{platform}__{SOURCE_KIND}"
    scopes_dir = tmp_path / "scopes"
    scopes_dir.mkdir(exist_ok=True)
    shutil.copytree(MANIFESTS / scope_dir, scopes_dir / scope_dir)
    sidecar = tmp_path / f"product_reference_{family}_{platform}.json"
    shutil.copyfile(MANIFESTS / f"product_reference_{family}_{platform}.json", sidecar)
    inputs = load_manifest_sidecar(sidecar)
    assert inputs is not None, (family, platform)
    store = GenerationManifestStore(scopes_dir)
    readme = read_readme_sections(store, Scope(family=family, platform=platform))
    return inputs, readme


# --------------------------------------------------------------------- (1) schema validation


@pytest.mark.parametrize("family,platform,issue_ids", PILOTS, ids=PILOT_IDS)
def test_manifest_known_hazards_entries_validate_against_the_real_schema(
    family: str, platform: str, issue_ids: tuple[str, ...]
) -> None:
    manifest = _real_manifest(family, platform)
    jsonschema.Draft202012Validator(_schema()).validate(manifest)
    hazards = known_hazards_from_manifest(manifest)
    assert len(hazards) == len(issue_ids), (family, platform, hazards)
    for hazard in hazards:
        assert hazard.text
        assert hazard.verified_at == ROUND2_VERIFIED_AT


def test_total_known_hazards_entries_match_the_seven_round2_findings() -> None:
    """Exactly 7 entries across the 5 pilots - one per round-2 issue id this card closes, no more,
    no fewer."""
    total = sum(
        len(known_hazards_from_manifest(_real_manifest(family, platform))) for family, platform, _ in PILOTS
    )
    assert total == 7
    assert len(ALL_ISSUE_IDS) == 7


# ------------------------------------------------------------- (2) end-to-end, real baked pilot


@pytest.mark.parametrize("family,platform,issue_ids", PILOTS, ids=PILOT_IDS)
def test_get_product_reference_limitations_surfaces_the_real_hazard_for_each_pilot(
    tmp_path: Path, family: str, platform: str, issue_ids: tuple[str, ...]
) -> None:
    """The REAL baked sidecar's "limitations" answer, with this pilot's manifest-sourced
    known_hazards merged in exactly as the live server does, discloses every one of this pilot's
    round-2 hazards - distinctly framed, never marked untrusted_upstream_content."""
    inputs, readme = _load_baked(tmp_path, family, platform)
    manifest = _real_manifest(family, platform)
    hazards = known_hazards_from_manifest(manifest)
    assert hazards, (family, platform)
    inputs = dataclasses.replace(inputs, known_hazards=hazards)

    result = get_product_reference(inputs, "limitations", readme=readme)

    assert isinstance(result, ReferenceContent)
    assert result.section == "limitations"
    assert result.untrusted_upstream_content is False
    assert result.text.count(KNOWN_HAZARD_LEAD) == len(issue_ids)
    assert ROUND2_VERIFIED_AT in result.text


# ------------------------------------------- (3) failure mode / trigger / safe pattern, per issue

_HAZARD_TRACE_TOKENS: dict[str, tuple[str, ...]] = {
    # Each token must appear (case-insensitively) in the matching manifest's hazard text, AND is
    # drawn directly from that issue's own `actual`/`expected` fields in docs/round2/issues.json.
    "3D-NET-001": ("rvmtext", "notimplementedexception", "aspose3dweb"),
    "HTML-PY-001": ("check_validity", "bf0f1e7a6d29", "_compute_validity_state"),
    "HTML-PY-005": ("focus-visible", "silent no-match", "notimplementederror"),
    "PDF-JAVA-003": ("saveformat.docx", "sdm", "docsaveoptions"),
    "SLIDES-JAVA-001": ("unsupportedoperationexception", "pptx, ppsx, potx", "export format"),
    "SLIDES-JAVA-002": ("sldidlst", "addemptyslide", "addclone"),
    "SLIDES-PY-002": ("pptm", "potx", "26.8.0"),
}


@pytest.mark.parametrize("issue_id", ALL_ISSUE_IDS)
def test_hazard_text_carries_the_real_issue_specific_trace_tokens(issue_id: str) -> None:
    """Each issue's distinctive technical tokens (drawn from its own real ``docs/round2/issues.json``
    record) appear BOTH in that real issue record's own text AND in the matching manifest's hazard
    text - proving the hazard text is not generic boilerplate, but is actually traceable to the
    real issue it claims to disclose."""
    issue = _real_issue(issue_id)
    issue_text = _WHITESPACE_RE.sub(" ", json.dumps(issue).lower())
    family, platform = issue["pilot"].split("/")
    # yaml.safe_dump line-folds long scalars, inserting a newline+indent where the real text has a
    # single space - collapse all whitespace runs to one space so a multi-word token still matches.
    manifest_text = _WHITESPACE_RE.sub(" ", yaml.safe_dump(_real_manifest(family, platform)).lower())
    for token in _HAZARD_TRACE_TOKENS[issue_id]:
        assert token.lower() in issue_text, (issue_id, token, "token not even in the real issue record")
        assert token.lower() in manifest_text, (
            issue_id,
            token,
            "token missing from the manifest's hazard text",
        )


# --------------------------------------------------------------------------- (4) verified_at date


@pytest.mark.parametrize("family,platform,issue_ids", PILOTS, ids=PILOT_IDS)
def test_known_hazards_verified_at_is_a_real_valid_calendar_date(
    family: str, platform: str, issue_ids: tuple[str, ...]
) -> None:
    manifest = _real_manifest(family, platform)
    hazards = known_hazards_from_manifest(manifest)
    assert hazards
    for hazard in hazards:
        parsed = datetime.date.fromisoformat(hazard.verified_at)
        assert parsed == datetime.date(2026, 10, 29)
        # Not in the future relative to today's real date (2026-11-01).
        assert parsed <= datetime.date(2026, 11, 1)


def test_round2_issues_json_actually_contains_the_seven_claimed_ids() -> None:
    """Every one of the 7 issue ids this card closes is a real record in
    docs/round2/issues.json - the source this card's hazard text must be traceable to."""
    issues = json.loads(ROUND2_ISSUES_PATH.read_text(encoding="utf-8"))
    ids_present = {issue["id"] for issue in issues}
    for issue_id in ALL_ISSUE_IDS:
        assert issue_id in ids_present, issue_id
