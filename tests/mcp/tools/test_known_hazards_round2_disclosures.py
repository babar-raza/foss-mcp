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

G3/TC-720 adds an 8th round-2 finding, ``PAGE-PYTHON-003`` (config/products/page/python.yaml),
through the identical mechanism: a real, undocumented behavior of the actually-installed
``aspose-page-foss`` package (``PsDocument.create()`` silently starts with one page already
present) that this project's own served content never disclosed. Its ``verified_at`` is its
own real verification date (2026-11-07, by direct reproduction against the real installed
package), distinct from the other 7 entries' shared ``ROUND2_VERIFIED_AT`` (2026-10-29, the
original round-2 audit's date) - so it is kept out of the shared ``PILOTS``/``ALL_ISSUE_IDS``
tuples that assume that one shared date, and is instead layered on top via ``ALL_PILOTS``.

G3/TC-721 adds a 9th round-2 finding, ``CELLS-PY-004`` (config/products/cells/python.yaml),
through the identical mechanism: a real, undocumented gap in the actually-installed
``aspose-cells-foss`` package's ``FormulaEvaluator.evaluate()`` (SUM, raw arithmetic like
``B2+B3``, and AVERAGE all silently return ``None`` with no exception, while the real supported
set is only CONCATENATE/CONCAT/TEXT/IF/AND/OR/NOT/LEN/TRIM/UPPER/LOWER) that this project's own
served content never disclosed. Its ``verified_at`` is also its own real verification date
(2026-11-07, by direct reproduction against the real installed package), distinct from the
other 7 entries' shared ``ROUND2_VERIFIED_AT`` (2026-10-29) - so, like ``PAGE-PYTHON-003``
before it, it is layered on top via ``ALL_PILOTS`` rather than merged into ``PILOTS``.

G3/TC-722 adds a 10th round-2 finding, ``EMAIL-CPP-004`` (config/products/email/cpp.yaml),
through the identical mechanism: a real reference-invalidation hazard confirmed by reading the
real pinned commit's ``src/msg/mapi_message.cpp`` and header directly - ``add_attachment``,
``add_recipient``, and ``add_embedded_message_attachment`` each ``push_back`` onto an internal
``std::vector`` member and return a reference to its ``.back()``, with no ``reserve()`` call
anywhere in the class, so any further ``add_*`` call on the same message can reallocate the
vector and silently invalidate every reference an earlier call returned - that this project's
own served content never disclosed. Its ``verified_at`` is also its own real verification date
(2026-11-07, by direct reading of the real pinned source), distinct from the other 7 entries'
shared ``ROUND2_VERIFIED_AT`` (2026-10-29) - so, like ``PAGE-PYTHON-003`` and ``CELLS-PY-004``
before it, it is layered on top via ``ALL_PILOTS`` rather than merged into ``PILOTS``.

G3/TC-723 adds an 11th round-2 finding, ``SLIDES-CPP-003`` (config/products/slides/cpp.yaml),
through the identical mechanism: a real, confirmed-still-open gap in this project's own served
``search_docs`` "Key Capabilities and API Reference" text (``content_type=developer_guide``) -
``ShapeCollection::add_table()`` genuinely adds a table and it is genuinely saved to the slide
XML, but the table is silently excluded from the uniform ``shapes()`` collection forever
(``shapes().size()``/``operator[]`` never grow to include it, unlike ``add_auto_shape()``,
``add_connector()``, and ``add_picture_frame()``, which genuinely do), with no error and no
warning; the real correct accessor pair is ``table_count()``/``table_at()``. Its ``verified_at``
is also its own real verification date (2026-11-07), distinct from the other 7 entries' shared
``ROUND2_VERIFIED_AT`` (2026-10-29) - so, like the three findings before it, it is layered on top
via ``ALL_PILOTS`` rather than merged into ``PILOTS``.

G3/TC-724 adds a 12th round-2 finding, ``3D-TYPESCRIPT-006`` (config/products/3d/typescript.yaml),
through the identical mechanism: a real, confirmed-still-open barrel-export gap confirmed by a
direct ``gh api`` read of the real pinned commit's own source - ``Axis`` (declared in
``src/aspose/threed/Axis.ts``) and ``FbxElement``/``FbxProperty`` (both declared and exported
inside ``src/aspose/threed/formats/fbx/parser.ts``) are real, declared, compiled, public classes
with full, accurate ``get_symbol``/``search_symbols`` signatures, but none of the three is ever
re-exported by any module barrel (``index.ts``) file - not ``src/aspose/threed/index.ts``, and
not ``src/aspose/threed/formats/fbx/index.ts`` (which only re-exports
``FbxParser``/``FbxTokenizer``/``BinaryTokenizer``) - so importing any of the three from the
package's own declared public surface genuinely fails, with zero caveat anywhere this project
serves. Its ``verified_at`` is also its own real verification date (2026-11-07), distinct from
the other 7 entries' shared ``ROUND2_VERIFIED_AT`` (2026-10-29) - so, like the four findings
before it, it is layered on top via ``ALL_PILOTS`` rather than merged into ``PILOTS``.

G3/TC-725 adds a 13th round-2 finding, ``CELLS-RUST-003`` (config/products/cells/rust.yaml),
through the identical mechanism: a real, confirmed-still-open absence of any formula-calculation
engine anywhere in the real indexed API, confirmed by a direct search of the real committed
``tests/fixtures/cells_rust/api_surface.json`` fixture - zero of the 1470 real methods across all
122 indexed types are literally named ``recalculate``, ``evaluate``, or ``calculate_formula``;
``Workbook`` itself declares no member named ``calculate``; the only "calculation"-named members
anywhere are configuration settings on ``CalculationProperties``/``WorkbookProperties`` (flags,
never a compute engine); and ``put_formula_with_cached_value`` - the library's only
formula-writing mechanism - requires a pre-computed ``CellValue`` as its own parameter and never
recomputes anything, so a cached value never refreshes when the inputs it depends on change. Its
``verified_at`` is also its own real verification date (2026-11-07), distinct from the other 7
entries' shared ``ROUND2_VERIFIED_AT`` (2026-10-29) - so, like the five findings before it, it is
layered on top via ``ALL_PILOTS`` rather than merged into ``PILOTS``.

G3/TC-726 adds a 14th round-2 finding, ``PAGE-PYTHON-005`` (config/products/page/python.yaml),
through the identical mechanism, but as a SECOND, unrelated entry on the SAME pilot ``TC-720``
already added one for - a different class entirely: a real, confirmed-still-open silent
font-fallback behavior confirmed by direct reproduction against the real installed
``aspose-page-foss`` 26.9.0 package - ``XpsGlyphs.font_uri`` accepts any string, including a
path never registered as a real part in the document's own package; ``save()``/``to_pdf()``/
``to_image()`` all succeed with no exception or warning, pypdf confirms the resulting PDF's
extracted text is exactly ``"Hello XPS"``, and the rendered PNG has real, non-white glyph pixels
- the renderer silently substitutes a fallback font and still renders the requested text, never
the font actually named by ``font_uri``, with no signal anywhere that the requested font was
not the one used. Its ``verified_at`` is also its own real verification date (2026-11-07),
distinct from the other 7 entries' shared ``ROUND2_VERIFIED_AT`` (2026-10-29) - so, like the six
findings before it, it is layered on top of the existing ``PAGE_PYTHON_PILOT`` tuple's issue-id
list (not a new ``ALL_PILOTS`` entry, since ``page/python`` already has one from ``TC-720``).

G3/TC-796 adds 5 more disclosures for the cells family's remaining undisclosed-behavior and
capability-gap rows, each re-verified on 2026-11-14 against the real upstream source at the
pinned/indexed commit (reading it, and running a small program built from it with the pilot's own
toolchain where cheap): ``CELLS-CPP-004`` (config/products/cells/cpp.yaml - formulas are never
calculated or cached), ``CELLS-TYPESCRIPT-001`` and ``CELLS-TYPESCRIPT-012``
(config/products/cells/typescript.yaml - write-only formulas; no defined-names API despite the
README claim), ``CELLS-GO-009`` (config/products/cells/go.yaml - missing capabilities) and
``CELLS-RUST-006`` (config/products/cells/rust.yaml - merge() takes counts, not end indices, and is
zero-based). The first three and ``CELLS-RUST-006`` are layered into ``ALL_PILOTS`` (cells/rust gains
a second id); ``CELLS-GO-009`` is covered by its own tests at the end of this module, because
cells/go's first, pre-existing hazard has no round-2 issue id.

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
from foss_mcp.indexing.known_defects import load_known_defects
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

# G3/TC-720: the 8th round-2-sourced known_hazards entry, PAGE-PYTHON-003 (page/python). Kept
# separate from PILOTS above (rather than appended to it) because its real verified_at
# (2026-11-07, this card's own direct reproduction) differs from the other 7 entries' shared
# ROUND2_VERIFIED_AT (2026-10-29) - tests that assume every PILOTS entry shares one verified_at
# date (the schema-validation and verified_at-date tests below) stay scoped to the original 7,
# unchanged; tests that check per-entry disclosure (the "for each pilot" and "trace tokens"
# tests) are extended to also cover this 8th entry via ALL_PILOTS/ALL_ISSUE_IDS below.
#
# G3/TC-726 adds a 14th round-2-sourced known_hazards entry, PAGE-PYTHON-005, as a SECOND entry
# on this SAME page/python manifest (a different, unrelated class - XpsGlyphs.font_uri's silent
# font fallback, not PAGE-PYTHON-003's page count) - so its issue id is appended directly into
# this tuple's own issue-id tuple below, the identical multi-id-per-pilot pattern PILOTS already
# uses for html/python's ("HTML-PY-001", "HTML-PY-005") and slides/java's
# ("SLIDES-JAVA-001", "SLIDES-JAVA-002"), rather than creating a second, duplicate ALL_PILOTS
# entry for the same (family, platform) pair.
PAGE_PYTHON_PILOT: tuple[str, str, tuple[str, ...]] = (
    "page",
    "python",
    ("PAGE-PYTHON-003", "PAGE-PYTHON-005"),
)
ALL_PILOTS: tuple[tuple[str, str, tuple[str, ...]], ...] = PILOTS + (PAGE_PYTHON_PILOT,)
ALL_PILOT_IDS = PILOT_IDS + ["page/python"]
ALL_ISSUE_IDS = ALL_ISSUE_IDS + PAGE_PYTHON_PILOT[2]

# G3/TC-721: the 9th round-2-sourced known_hazards entry, CELLS-PY-004 (cells/python). Layered
# on top of ALL_PILOTS the same way G3/TC-720's PAGE_PYTHON_PILOT was, for the identical reason:
# its real verified_at (2026-11-07, this card's own direct reproduction) differs from the other
# 7 entries' shared ROUND2_VERIFIED_AT (2026-10-29), so the schema-validation and
# verified_at-date tests below (which assume one shared date) stay scoped to the original 7,
# unchanged, while the per-entry disclosure tests ("for each pilot" and "trace tokens") extend
# to cover this 9th entry via ALL_PILOTS/ALL_ISSUE_IDS.
CELLS_PYTHON_PILOT: tuple[str, str, tuple[str, ...]] = ("cells", "python", ("CELLS-PY-004",))
ALL_PILOTS = ALL_PILOTS + (CELLS_PYTHON_PILOT,)
ALL_PILOT_IDS = ALL_PILOT_IDS + ["cells/python"]
ALL_ISSUE_IDS = ALL_ISSUE_IDS + CELLS_PYTHON_PILOT[2]

# G3/TC-722: the 10th round-2-sourced known_hazards entry, EMAIL-CPP-004 (email/cpp). Layered
# on top of ALL_PILOTS the same way G3/TC-720's PAGE_PYTHON_PILOT and G3/TC-721's
# CELLS_PYTHON_PILOT were, for the identical reason: its real verified_at (2026-11-07, this
# card's own direct reading of the real pinned source) differs from the other 7 entries' shared
# ROUND2_VERIFIED_AT (2026-10-29), so the schema-validation and verified_at-date tests below
# (which assume one shared date) stay scoped to the original 7, unchanged, while the per-entry
# disclosure tests ("for each pilot" and "trace tokens") extend to cover this 10th entry via
# ALL_PILOTS/ALL_ISSUE_IDS.
EMAIL_CPP_PILOT: tuple[str, str, tuple[str, ...]] = ("email", "cpp", ("EMAIL-CPP-004",))
ALL_PILOTS = ALL_PILOTS + (EMAIL_CPP_PILOT,)
ALL_PILOT_IDS = ALL_PILOT_IDS + ["email/cpp"]
ALL_ISSUE_IDS = ALL_ISSUE_IDS + EMAIL_CPP_PILOT[2]

# G3/TC-723: the 11th round-2-sourced known_hazards entry, SLIDES-CPP-003 (slides/cpp). Layered
# on top of ALL_PILOTS the same way G3/TC-720's PAGE_PYTHON_PILOT, G3/TC-721's CELLS_PYTHON_PILOT,
# and G3/TC-722's EMAIL_CPP_PILOT were, for the identical reason: its real verified_at
# (2026-11-07, this card's own real verification date) differs from the other 7 entries' shared
# ROUND2_VERIFIED_AT (2026-10-29), so the schema-validation and verified_at-date tests below
# (which assume one shared date) stay scoped to the original 7, unchanged, while the per-entry
# disclosure tests ("for each pilot" and "trace tokens") extend to cover this 11th entry via
# ALL_PILOTS/ALL_ISSUE_IDS.
SLIDES_CPP_PILOT: tuple[str, str, tuple[str, ...]] = ("slides", "cpp", ("SLIDES-CPP-003",))
ALL_PILOTS = ALL_PILOTS + (SLIDES_CPP_PILOT,)
ALL_PILOT_IDS = ALL_PILOT_IDS + ["slides/cpp"]
ALL_ISSUE_IDS = ALL_ISSUE_IDS + SLIDES_CPP_PILOT[2]

# G3/TC-724: the 12th round-2-sourced known_hazards entry, 3D-TYPESCRIPT-006 (3d/typescript).
# Layered on top of ALL_PILOTS the same way G3/TC-720's PAGE_PYTHON_PILOT, G3/TC-721's
# CELLS_PYTHON_PILOT, G3/TC-722's EMAIL_CPP_PILOT, and G3/TC-723's SLIDES_CPP_PILOT were, for the
# identical reason: its real verified_at (2026-11-07, this card's own real verification date)
# differs from the other 7 entries' shared ROUND2_VERIFIED_AT (2026-10-29), so the
# schema-validation and verified_at-date tests below (which assume one shared date) stay scoped
# to the original 7, unchanged, while the per-entry disclosure tests ("for each pilot" and
# "trace tokens") extend to cover this 12th entry via ALL_PILOTS/ALL_ISSUE_IDS.
THREED_TYPESCRIPT_PILOT: tuple[str, str, tuple[str, ...]] = ("3d", "typescript", ("3D-TYPESCRIPT-006",))
ALL_PILOTS = ALL_PILOTS + (THREED_TYPESCRIPT_PILOT,)
ALL_PILOT_IDS = ALL_PILOT_IDS + ["3d/typescript"]
ALL_ISSUE_IDS = ALL_ISSUE_IDS + THREED_TYPESCRIPT_PILOT[2]

# G3/TC-725: the 13th round-2-sourced known_hazards entry, CELLS-RUST-003 (cells/rust). Layered
# on top of ALL_PILOTS the same way the six findings before it were, for the identical reason:
# its real verified_at (2026-11-07, this card's own direct reproduction against the real
# committed fixture) differs from the other 7 entries' shared ROUND2_VERIFIED_AT (2026-10-29), so
# the schema-validation and verified_at-date tests below (which assume one shared date) stay
# scoped to the original 7, unchanged, while the per-entry disclosure tests ("for each pilot" and
# "trace tokens") extend to cover this 13th entry via ALL_PILOTS/ALL_ISSUE_IDS.
#
# G3/TC-796 adds a SECOND entry on the same pilot, CELLS-RUST-006 (merge() counts vs end indices),
# via the identical multi-id-per-pilot pattern PAGE_PYTHON_PILOT already uses.
CELLS_RUST_PILOT: tuple[str, str, tuple[str, ...]] = (
    "cells",
    "rust",
    ("CELLS-RUST-003", "CELLS-RUST-006"),
)
ALL_PILOTS = ALL_PILOTS + (CELLS_RUST_PILOT,)
ALL_PILOT_IDS = ALL_PILOT_IDS + ["cells/rust"]
ALL_ISSUE_IDS = ALL_ISSUE_IDS + CELLS_RUST_PILOT[2]

# G3/TC-796: three more round-2-sourced entries on two more pilots - CELLS-CPP-004 (cells/cpp) and
# CELLS-TYPESCRIPT-001 + CELLS-TYPESCRIPT-012 (cells/typescript) - layered on top of ALL_PILOTS
# the same way, for the identical reason: their real verified_at (2026-11-14, this card's own
# direct verification against the real pinned source) differs from the 7 original entries' shared
# ROUND2_VERIFIED_AT (2026-10-29).
CELLS_CPP_PILOT: tuple[str, str, tuple[str, ...]] = ("cells", "cpp", ("CELLS-CPP-004",))
CELLS_TYPESCRIPT_PILOT: tuple[str, str, tuple[str, ...]] = (
    "cells",
    "typescript",
    ("CELLS-TYPESCRIPT-001", "CELLS-TYPESCRIPT-012"),
)
ALL_PILOTS = ALL_PILOTS + (CELLS_CPP_PILOT, CELLS_TYPESCRIPT_PILOT)
ALL_PILOT_IDS = ALL_PILOT_IDS + ["cells/cpp", "cells/typescript"]
ALL_ISSUE_IDS = ALL_ISSUE_IDS + CELLS_CPP_PILOT[2] + CELLS_TYPESCRIPT_PILOT[2]


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


def _library_level_defect_count(family: str, platform: str) -> int:
    """How many of this pilot's real ``known_defects`` registry entries ``get_product_reference``
    merges into "limitations" (``subject.kind`` "package" or "commit" - method/class-scoped
    entries surface through get_symbol instead)."""
    return sum(
        1 for defect in load_known_defects(family, platform) if defect.subject.kind in ("package", "commit")
    )


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


def test_total_known_hazards_entries_match_the_eighteen_round2_findings() -> None:
    """Exactly 18 entries across the 13 pilots (G3/TC-796 added 4 more: CELLS-RUST-006,
    CELLS-CPP-004, CELLS-TYPESCRIPT-001, CELLS-TYPESCRIPT-012; CELLS-GO-009 is on cells/go, whose
    first hazard has no round-2 issue id, and is counted by its own test below) - one per round-2
    issue id this card (plus G3/TC-720's
    PAGE-PYTHON-003, G3/TC-721's CELLS-PY-004, G3/TC-722's EMAIL-CPP-004, G3/TC-723's
    SLIDES-CPP-003, G3/TC-724's 3D-TYPESCRIPT-006, G3/TC-725's CELLS-RUST-003, and G3/TC-726's
    PAGE-PYTHON-005, the latter a second entry on page/python alongside PAGE-PYTHON-003) closes,
    no more, no fewer."""
    total = sum(
        len(known_hazards_from_manifest(_real_manifest(family, platform)))
        for family, platform, _ in ALL_PILOTS
    )
    assert total == 18
    assert len(ALL_ISSUE_IDS) == 18


# ------------------------------------------------------------- (2) end-to-end, real baked pilot


@pytest.mark.parametrize("family,platform,issue_ids", ALL_PILOTS, ids=ALL_PILOT_IDS)
def test_get_product_reference_limitations_surfaces_the_real_hazard_for_each_pilot(
    tmp_path: Path, family: str, platform: str, issue_ids: tuple[str, ...]
) -> None:
    """The REAL baked sidecar's "limitations" answer, with this pilot's manifest-sourced
    known_hazards merged in exactly as the live server does, discloses every one of this pilot's
    round-2 hazards - distinctly framed, never marked untrusted_upstream_content.

    Asserts each hazard's OWN verified_at appears in the text (rather than the shared
    ROUND2_VERIFIED_AT constant) because G3/TC-720's page/python entry (added via ALL_PILOTS)
    carries its own real, later verification date."""
    inputs, readme = _load_baked(tmp_path, family, platform)
    manifest = _real_manifest(family, platform)
    hazards = known_hazards_from_manifest(manifest)
    assert hazards, (family, platform)
    inputs = dataclasses.replace(inputs, known_hazards=hazards)

    result = get_product_reference(inputs, "limitations", readme=readme)

    assert isinstance(result, ReferenceContent)
    assert result.section == "limitations"
    # G3/TC-824: the flag stays True while upstream README text heads the answer; it is False only for a
    # hazard-only answer (one that starts with the project's own hazard paragraph).
    assert result.untrusted_upstream_content is (not result.text.startswith(KNOWN_HAZARD_LEAD))
    # get_product_reference merges a pilot's library-level (package/commit-kind) known_defects
    # registry entries into "limitations" through the SAME paragraph mechanism and lead as
    # known_hazards (G3/TC-641), so the real expected lead count is this pilot's hazards PLUS
    # those registry entries - computed from the real registry, never hardcoded.
    assert result.text.count(KNOWN_HAZARD_LEAD) == len(issue_ids) + _library_level_defect_count(
        family, platform
    )
    for hazard in hazards:
        assert hazard.verified_at in result.text


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
    "PAGE-PYTHON-003": (
        "psdocument.create(is_eps=true)",
        "xpsdocument.create()",
        "returns a document that already contains one page",
    ),
    "CELLS-PY-004": (
        "formulaevaluator.evaluate",
        "_evaluate_function",
        "b2+b3",
    ),
    "EMAIL-CPP-004": (
        "add_attachment/add_recipient/add_embedded_message_attachment",
        "on the same mapi_message.",
        "silently-corrupted",
    ),
    "SLIDES-CPP-003": (
        "shapecollection::add_table()",
        "table_count()/table_at()",
        "shapes().size()/operator[]",
    ),
    "3D-TYPESCRIPT-006": (
        "fbxelement or fbxproperty",
        "axis.ts is never imported/re-exported",
        "public entry point",
    ),
    "CELLS-RUST-003": (
        "put_formula_with_cached_value) requires a pre-computed value",
        "recalculate/evaluate/calculate_formula",
        "declares no member named calculate",
    ),
    # G3/TC-796: each token below is present both in the real docs/round2/issues.json record and in
    # the new manifest hazard text.
    "CELLS-RUST-006": ("total_columns", "merge(0, 0, 1, 3)", "a1:c1"),
    "CELLS-CPP-004": ("setformula()", "getformula()", "calculation engine"),
    "CELLS-TYPESCRIPT-001": ("loadworksheetdata", "toxml", "write-only"),
    "CELLS-TYPESCRIPT-012": ("generateworkbook", "defined names", "definednames"),
    "PAGE-PYTHON-005": (
        "font_uri accepts any string, including a path",
        "font_uri: str (writable)",
        "silently substitutes a fallback font",
    ),
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


def test_round2_issues_json_actually_contains_the_claimed_ids() -> None:
    """Every one of the issue ids in ALL_ISSUE_IDS (18 today) this card (plus G3/TC-720's PAGE-PYTHON-003, G3/TC-721's
    CELLS-PY-004, G3/TC-722's EMAIL-CPP-004, G3/TC-723's SLIDES-CPP-003, G3/TC-724's
    3D-TYPESCRIPT-006, G3/TC-725's CELLS-RUST-003, G3/TC-726's PAGE-PYTHON-005, and G3/TC-796's
    CELLS-RUST-006, CELLS-CPP-004, CELLS-TYPESCRIPT-001 and CELLS-TYPESCRIPT-012) closes is a
    real record in docs/round2/issues.json - the source this card's hazard text must be
    traceable to."""
    issues = json.loads(ROUND2_ISSUES_PATH.read_text(encoding="utf-8"))
    ids_present = {issue["id"] for issue in issues}
    for issue_id in ALL_ISSUE_IDS:
        assert issue_id in ids_present, issue_id


# ---------------------------------------------------------------------------------------------
# G3/TC-796: CELLS-GO-009 (cells/go), the 5th TC-796 disclosure, plus "nothing else changed"
# ---------------------------------------------------------------------------------------------

TC796_VERIFIED_AT = "2026-11-14"
# The TC-582 concurrency hazard stays first and unchanged; CELLS-GO-009 is appended second.
GO_TC582_VERIFIED_AT = "2026-10-24"
# Each token appears in BOTH the real docs/round2/issues.json CELLS-GO-009 record and the cells/go
# manifest's hazard text.
GO_009_TRACE_TOKENS = ("importfromcsv", "addworksheet", "numfmtid")
# A distinctive fragment of each TC-796 hazard, used to prove each text appears only on its own
# pilot's manifest and nowhere else.
TC796_HAZARD_FRAGMENTS: dict[tuple[str, str], tuple[str, ...]] = {
    ("cells", "cpp"): ("has no formula-calculation engine",),
    ("cells", "typescript"): ("Formulas are write-only in excel-cells", "has no defined-name or"),
    ("cells", "go"): ("has no API for several commonly expected spreadsheet",),
    ("cells", "rust"): ("the last two are COUNTS of rows and columns",),
}


def _manifest_text(family: str, platform: str) -> str:
    return _WHITESPACE_RE.sub(
        " ", (CONFIG_PRODUCTS / family / f"{platform}.yaml").read_text(encoding="utf-8")
    )


def test_cells_go_carries_the_original_hazard_first_and_the_new_cells_go_009_hazard_second() -> None:
    hazards = known_hazards_from_manifest(_real_manifest("cells", "go"))
    # G3/TC-832 appended a THIRD entry (the CalculateFormula leading-"=" behaviour) after these two.
    assert len(hazards) == 3
    assert hazards[0].verified_at == GO_TC582_VERIFIED_AT
    assert "goroutine" in hazards[0].text.lower()
    assert hazards[1].verified_at == TC796_VERIFIED_AT
    text = hazards[1].text
    # Only gaps confirmed in the pinned source are stated: merge, defined names, number format,
    # PDF, chart, and the lack of an AddWorksheet.
    for fragment in ("merged-cells", "defined-name", "number-format", "PDF export", "chart", "ImportFromCSV"):
        assert fragment in text, fragment
    assert "numFmtId 0" in text
    assert "fa4e890e" in text  # scoped to the pinned commit, never presented as current upstream


def test_cells_go_009_text_is_traceable_to_the_real_round2_record() -> None:
    issue = _real_issue("CELLS-GO-009")
    assert issue["pilot"] == "cells/go"
    issue_text = _WHITESPACE_RE.sub(" ", json.dumps(issue).lower())
    manifest_text = _WHITESPACE_RE.sub(" ", yaml.safe_dump(_real_manifest("cells", "go")).lower())
    for token in GO_009_TRACE_TOKENS:
        assert token in issue_text, (token, "token not even in the real issue record")
        assert token in manifest_text, (token, "token missing from the manifest's hazard text")


def test_cells_go_manifest_still_validates_against_the_real_schema() -> None:
    jsonschema.Draft202012Validator(_schema()).validate(_real_manifest("cells", "go"))


def test_get_product_reference_limitations_for_cells_go_surfaces_both_hazards(tmp_path: Path) -> None:
    inputs, readme = _load_baked(tmp_path, "cells", "go")
    hazards = known_hazards_from_manifest(_real_manifest("cells", "go"))
    inputs = dataclasses.replace(inputs, known_hazards=hazards)

    result = get_product_reference(inputs, "limitations", readme=readme)

    assert isinstance(result, ReferenceContent)
    # G3/TC-824: the flag stays True while upstream README text heads the answer; it is False only for a
    # hazard-only answer (one that starts with the project's own hazard paragraph).
    assert result.untrusted_upstream_content is (not result.text.startswith(KNOWN_HAZARD_LEAD))
    assert result.text.count(KNOWN_HAZARD_LEAD) == 3 + _library_level_defect_count("cells", "go")
    assert "ImportFromCSV" in result.text
    assert "numFmtId 0" in result.text
    for hazard in hazards:
        assert hazard.verified_at in result.text
    # The original concurrency hazard is still there, and still comes before the new one.
    assert result.text.index("goroutine") < result.text.index("no API for several commonly expected")


def test_every_tc796_hazard_text_appears_only_on_its_own_pilots_manifest() -> None:
    """Each TC-796 hazard fragment is present on its own pilot's manifest and on NO other
    config/products manifest - proving no other pilot's served disclosure changed."""
    for (family, platform), fragments in TC796_HAZARD_FRAGMENTS.items():
        own = _manifest_text(family, platform)
        for fragment in fragments:
            assert fragment in own, (family, platform, fragment)
    for path in sorted(CONFIG_PRODUCTS.glob("*/*.yaml")):
        key = (path.parent.name, path.stem)
        other = _WHITESPACE_RE.sub(" ", path.read_text(encoding="utf-8"))
        for owner, fragments in TC796_HAZARD_FRAGMENTS.items():
            if owner == key:
                continue
            for fragment in fragments:
                assert fragment not in other, (key, "carries a TC-796 hazard belonging to", owner)


@pytest.mark.parametrize(
    "family,platform",
    [("cells", "java"), ("cells", "net"), ("cells", "python")],
    ids=["cells/java", "cells/net", "cells/python"],
)
def test_other_cells_pilots_limitations_carry_none_of_the_tc796_text(
    tmp_path: Path, family: str, platform: str
) -> None:
    """The three cells siblings TC-796 did not touch: their real limitations answers (own
    manifest hazards merged exactly as the live server does) contain none of the new hazard text,
    and cells/python's own CELLS-PY-004 hazard is byte-for-byte what its manifest says."""
    inputs, readme = _load_baked(tmp_path, family, platform)
    hazards = known_hazards_from_manifest(_real_manifest(family, platform))
    result = get_product_reference(
        dataclasses.replace(inputs, known_hazards=hazards), "limitations", readme=readme
    )
    text = result.text if isinstance(result, ReferenceContent) else ""
    for fragments in TC796_HAZARD_FRAGMENTS.values():
        for fragment in fragments:
            assert fragment not in _WHITESPACE_RE.sub(" ", text), (family, platform, fragment)
    assert text.count(KNOWN_HAZARD_LEAD) == len(hazards) + _library_level_defect_count(family, platform)
    assert all(hazard.verified_at != TC796_VERIFIED_AT for hazard in hazards)
