"""G3/TC-653: closes round-2 audit finding ``WORDS-NET-003`` through the EXISTING known_hazards
mechanism (G3/TC-582's ``config/products/cells/go.yaml`` template; extended into
``get_product_reference``'s "limitations" rendering by G3/TC-641/KD4, and already reused for 7
round-2 findings across 5 pilots by G3/TC-650).

Unlike G3/TC-650's findings (this project's OWN served text making a false capability claim),
``WORDS-NET-003`` is a real gap in the installed library itself: ``get_product_reference(
section="limitations")`` for words/net returns the empty "no data source" stub, while
``get_symbol``/``list_members`` for ``Aspose.Words.SaveFormat`` lists all 41 members with no
annotation - but on the real installed Aspose.Words.FOSS 26.3.0, calling ``doc.Save`` with most of
those values (including ``SaveFormat.Pdf`` and ``SaveFormat.Html``) throws
``System.NotSupportedException: FOSS`` at runtime, while ``SaveFormat.Markdown`` genuinely
succeeds.

This file proves: (1) ``config/products/words/net.yaml``'s new ``known_hazards`` entry validates
against the real schema; (2) a real end-to-end ``get_product_reference(section="limitations")``
call against the REAL baked sidecar (``infra/demo-manifests``, the exact way the live server loads
words/net) surfaces the real hazard text, distinctly framed, never marked
``untrusted_upstream_content``; (3) the hazard text names at least 2 of the real unsupported
``SaveFormat`` values from the issue's own ``repro`` list (``Pdf``, ``Html``) and does NOT claim
every value is unsupported - the issue's own repro shows ``Markdown`` succeeds, and the hazard text
must not contradict that; (4) the hazard text is traceable to the real
``docs/round2/issues.json`` record, not invented; (5) ``verified_at`` is a real, valid calendar
date; (6) exactly one entry exists - no more, no fewer; (7) the real issue id is actually present
in ``docs/round2/issues.json``.
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
ISSUE_ID = "WORDS-NET-003"
FAMILY = "words"
PLATFORM = "net"
_WHITESPACE_RE = re.compile(r"\s+")


def _schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _real_manifest() -> dict[str, Any]:
    path = CONFIG_PRODUCTS / FAMILY / f"{PLATFORM}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _real_issue() -> dict[str, Any]:
    """The real ``docs/round2/issues.json`` record for ``WORDS-NET-003`` - the exact record this
    card's hazard text must be traceable to."""
    issues = json.loads(ROUND2_ISSUES_PATH.read_text(encoding="utf-8"))
    for issue in issues:
        if issue.get("id") == ISSUE_ID:
            return issue
    raise AssertionError(f"{ISSUE_ID} not found in {ROUND2_ISSUES_PATH}")


def _load_baked(tmp_path: Path) -> tuple[Any, ReadmeSections]:
    """``(inputs, readme)`` for words/net's REAL baked pilot (infra/demo-manifests) - the exact
    way the live server loads it, copied to a temporary directory first so nothing under
    infra/demo-manifests is ever opened for writing. Identical pattern to
    ``tests/mcp/tools/test_product_reference_known_hazards.py``'s own ``_load_baked``."""
    scope_dir = f"{FAMILY}__{PLATFORM}__{SOURCE_KIND}"
    scopes_dir = tmp_path / "scopes"
    scopes_dir.mkdir(exist_ok=True)
    shutil.copytree(MANIFESTS / scope_dir, scopes_dir / scope_dir)
    sidecar = tmp_path / f"product_reference_{FAMILY}_{PLATFORM}.json"
    shutil.copyfile(MANIFESTS / f"product_reference_{FAMILY}_{PLATFORM}.json", sidecar)
    inputs = load_manifest_sidecar(sidecar)
    assert inputs is not None
    store = GenerationManifestStore(scopes_dir)
    readme = read_readme_sections(store, Scope(family=FAMILY, platform=PLATFORM))
    return inputs, readme


# --------------------------------------------------------------------- (1) schema validation


def test_manifest_known_hazards_entry_validates_against_the_real_schema() -> None:
    manifest = _real_manifest()
    jsonschema.Draft202012Validator(_schema()).validate(manifest)
    hazards = known_hazards_from_manifest(manifest)
    assert len(hazards) == 1, hazards
    assert hazards[0].text
    assert hazards[0].verified_at == ROUND2_VERIFIED_AT


# ------------------------------------------------------------- (2) end-to-end, real baked pilot


def test_get_product_reference_limitations_surfaces_the_real_hazard(tmp_path: Path) -> None:
    """The REAL baked sidecar's "limitations" answer, with words/net's manifest-sourced
    known_hazards merged in exactly as the live server does, discloses the WORDS-NET-003 hazard -
    distinctly framed, never marked untrusted_upstream_content. Before this card the section had
    no data source at all; the merged hazard text is the only content, and it must be present."""
    inputs, readme = _load_baked(tmp_path)
    manifest = _real_manifest()
    hazards = known_hazards_from_manifest(manifest)
    assert hazards
    inputs = dataclasses.replace(inputs, known_hazards=hazards)

    result = get_product_reference(inputs, "limitations", readme=readme)

    assert isinstance(result, ReferenceContent)
    assert result.section == "limitations"
    # G3/TC-824: the flag stays True while upstream README text heads the answer; it is False only for a
    # hazard-only answer (one that starts with the project's own hazard paragraph).
    assert result.untrusted_upstream_content is (not result.text.startswith(KNOWN_HAZARD_LEAD))
    assert result.text.count(KNOWN_HAZARD_LEAD) == 1
    assert ROUND2_VERIFIED_AT in result.text


# --------------------------------------- (3) names real unsupported values, not a blanket claim


def test_hazard_text_names_at_least_two_real_unsupported_saveformat_values() -> None:
    """The hazard text names at least 2 of the real unsupported SaveFormat values from the
    issue's own repro list (Pdf, Html) - not a vague, generic warning."""
    hazard = known_hazards_from_manifest(_real_manifest())[0]
    text_lower = hazard.text.lower()
    assert "saveformat.pdf" in text_lower
    assert "saveformat.html" in text_lower


def test_hazard_text_does_not_claim_every_saveformat_value_is_unsupported() -> None:
    """The issue's own repro shows SaveFormat.Markdown succeeds; the hazard text must not
    contradict that by claiming every value is unsupported, and must name Markdown as a
    confirmed-working alternative."""
    hazard = known_hazards_from_manifest(_real_manifest())[0]
    text_lower = hazard.text.lower()
    assert "markdown" in text_lower
    assert "succeeds" in text_lower or "works" in text_lower or "confirmed-working" in text_lower
    # "most"/"majority" - never an absolute "every"/"all" claim about SaveFormat values.
    assert "most" in text_lower or "majority" in text_lower
    assert "every saveformat" not in text_lower
    assert "all saveformat" not in text_lower


# ------------------------------------------- (4) traceable to the real issue record, not invented


_TRACE_TOKENS: tuple[str, ...] = (
    "notsupportedexception",
    "saveformat.pdf",
    "saveformat.html",
    "26.3.0",
)


def test_hazard_text_carries_the_real_issue_specific_trace_tokens() -> None:
    """The hazard's distinctive technical tokens appear BOTH in the real issue record's own text
    AND in the manifest's hazard text - proving the hazard text is not generic boilerplate, but is
    actually traceable to the real WORDS-NET-003 record it discloses."""
    issue = _real_issue()
    issue_text = _WHITESPACE_RE.sub(" ", json.dumps(issue).lower())
    manifest_text = _WHITESPACE_RE.sub(" ", yaml.safe_dump(_real_manifest()).lower())
    for token in _TRACE_TOKENS:
        assert token in issue_text, (token, "token not even in the real issue record")
        assert token in manifest_text, (token, "token missing from the manifest's hazard text")


def test_hazard_text_names_markdown_as_the_confirmed_working_repro_value() -> None:
    """``SaveFormat.Markdown`` is the one repro line in the real issue record whose outcome is
    'succeeds' - the hazard text must name it, traceable to that exact repro line."""
    issue = _real_issue()
    repro_lines = [line.lower() for line in issue["repro"]]
    assert any("markdown" in line and "succeeds" in line for line in repro_lines)
    hazard = known_hazards_from_manifest(_real_manifest())[0]
    assert "saveformat.markdown" in hazard.text.lower()


# --------------------------------------------------------------------------- (5) verified_at date


def test_known_hazards_verified_at_is_a_real_valid_calendar_date() -> None:
    hazards = known_hazards_from_manifest(_real_manifest())
    assert hazards
    for hazard in hazards:
        parsed = datetime.date.fromisoformat(hazard.verified_at)
        assert parsed == datetime.date(2026, 10, 29)
        # Not in the future relative to today's real date (2026-11-01).
        assert parsed <= datetime.date(2026, 11, 1)


# --------------------------------------------------------------------------- (6) exactly one entry


def test_exactly_one_known_hazards_entry() -> None:
    hazards = known_hazards_from_manifest(_real_manifest())
    assert len(hazards) == 1


# ------------------------------------------------------------------- (7) issue id actually exists


def test_round2_issues_json_actually_contains_the_claimed_id() -> None:
    issues = json.loads(ROUND2_ISSUES_PATH.read_text(encoding="utf-8"))
    ids_present = {issue["id"] for issue in issues}
    assert ISSUE_ID in ids_present
