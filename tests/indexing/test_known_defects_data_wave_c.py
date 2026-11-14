"""G3/TC-832 (REQ-G3-015): disclosure wave C - three verified, previously undisclosed behaviours.

* ``3d/python`` (``aspose-3d-foss`` 26.1.0, indexed commit ``65b1f577``): a real format object is not
  enough to save with - ``Scene.save(path, FileFormat.FBX7400ASCII())`` raises ``NotImplementedError``
  just as ``Scene.save("x.fbx")`` / ``Scene.save("x.dae")`` do - and a node whose entity is a
  parametrised primitive (``Box()``) instead of its ``to_mesh()`` is silently skipped by the STL, glTF
  and 3MF exporters, so ``Scene.save("x.3mf")`` writes a 0-byte file. Both go on the existing
  ``known_hazards`` paragraph (``config/products/3d/python.yaml``); no registry entry is added, because
  the upstream README already says FBX/COLLADA export fails and the ``3D-PYTHON-009`` paragraph is the
  place agents read about format objects.
* ``cells/go`` (pinned ``fa4e890e``): ``CalculateFormula`` takes the formula text WITHOUT a leading
  ``=``; ``"=SUM(A1:A2)"`` returns ``unsupported function "=SUM"`` and ``"=IF(A1>0,1,2)"`` returns
  ``cell "A1>0" not found``. A third ``known_hazards`` paragraph (``config/products/cells/go.yaml``).
* ``pdf/python`` (ledger row RD2-210 / ``PDF-PYTHON-001``, pinned ``c391ee72``): ``class Permissions`` is
  ABSENT at the pin but present in the published ``aspose-pdf-foss-for-python`` 0.1.0 wheel, which is
  built from upstream commit ``71670a2a`` (78 commits later; the version string is ``0.1.0`` at both).
  The row is therefore REPRODUCED as an index-vs-package drift and disclosed as a ``known_hazards``
  paragraph (``config/products/pdf/python.yaml``). Nothing is patched (Decision A).

Every claim was re-run on 2026-11-14: ``aspose-3d-foss`` 26.1.0 in a throwaway venv (and the upstream
source at ``65b1f577``), a Go program against the library checked out at ``fa4e890e``, and the upstream
pdf/python repository cloned at ``c391ee72`` next to the real 0.1.0 wheel installed in a throwaway venv
(``from aspose_pdf import Permissions`` raised ``ImportError`` from the clone and worked from the wheel).

The tests exercise the real committed manifests and the real committed ``infra/demo-manifests`` corpus.
"""

from __future__ import annotations

import dataclasses
import json
import re
import shutil
import sys
from datetime import date
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml

from foss_mcp.indexing.generation_manifest import GenerationManifestStore
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_product_reference import (
    ProductReferenceInputs,
    ReferenceContent,
    get_product_reference,
    known_hazards_from_manifest,
)
from foss_mcp.mcp.tools.get_symbol import extract_fqn
from foss_mcp.mcp.tools.readme_sections import read_readme_sections
from tests.conformance.corpus import ConformanceCorpus, load_demo_manifests_corpus

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "infra"))
from fetch_product_reference import load_manifest_sidecar  # noqa: E402

MANIFEST_SCHEMA_PATH = REPO_ROOT / "schemas" / "product" / "product-manifest.schema.json"
CONFIG_PRODUCTS = REPO_ROOT / "config" / "products"
MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
ROUND2_ISSUES_PATH = REPO_ROOT / "docs" / "round2" / "issues.json"
ROUND2_REVIEW_PATH = REPO_ROOT / "docs" / "ROUND2_REVIEW.md"

VERIFIED_DATE = "2026-11-14"
KNOWN_HAZARD_LEAD = "Known hazard (verified by this project)"
SOURCE_KIND = "self_extracted"
_WHITESPACE_RE = re.compile(r"\s+")

# pilot -> (hazard entries the manifest carries today, pin the hazard text must be scoped to)
PILOTS: dict[tuple[str, str], tuple[int, str]] = {
    ("3d", "python"): (1, "65b1f577c0f16d0d9112bb6c1153d3024543ac02"),
    ("cells", "go"): (3, "fa4e890e46c509efd22d09b97411202c2b0b8a67"),
    ("pdf", "python"): (1, "c391ee72ca153070e5ba0c62d42484861070debc"),
}
PILOT_IDS = [f"{family}/{platform}" for family, platform in PILOTS]

# A distinctive fragment of each wave-C hazard, used to prove it appears on its own pilot's manifest and
# on no other config/products manifest.
FRAGMENTS: dict[tuple[str, str], tuple[str, ...]] = {
    ("3d", "python"): (
        "Scene.save(path, FileFormat.FBX7400ASCII())",
        "returned by its to_mesh() is silently skipped",
    ),
    ("cells", "go"): ("takes the formula text WITHOUT a leading",),
    ("pdf", "python"): ("which was built from a",),
}


def _real_manifest(family: str, platform: str) -> dict[str, Any]:
    return yaml.safe_load((CONFIG_PRODUCTS / family / f"{platform}.yaml").read_text(encoding="utf-8"))


def _hazards(family: str, platform: str):
    return known_hazards_from_manifest(_real_manifest(family, platform))


def _flat(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text)


def _new_hazard_text(family: str, platform: str) -> str:
    """The wave-C hazard text of *family*/*platform* (3d/python: the one paragraph; cells/go: the third
    entry; pdf/python: the one entry), whitespace-flattened."""
    hazards = _hazards(family, platform)
    return _flat(hazards[-1].text)


def _all_other_pilots() -> list[tuple[str, str]]:
    return sorted(
        (path.parent.name, path.stem)
        for path in CONFIG_PRODUCTS.glob("*/*.yaml")
        if (path.parent.name, path.stem) not in PILOTS
    )


def _load_baked(tmp_path: Path, family: str, platform: str) -> tuple[ProductReferenceInputs, Any]:
    """The REAL baked sidecar and README sections for *family*/*platform*, copied to a temporary
    directory first so nothing under infra/demo-manifests is ever opened for writing."""
    scope_dir = f"{family}__{platform}__{SOURCE_KIND}"
    scopes_dir = tmp_path / "scopes"
    scopes_dir.mkdir(exist_ok=True)
    shutil.copytree(MANIFESTS / scope_dir, scopes_dir / scope_dir)
    sidecar = tmp_path / f"product_reference_{family}_{platform}.json"
    shutil.copyfile(MANIFESTS / f"product_reference_{family}_{platform}.json", sidecar)
    inputs = load_manifest_sidecar(sidecar)
    assert inputs is not None, (family, platform)
    readme = read_readme_sections(
        GenerationManifestStore(scopes_dir), Scope(family=family, platform=platform)
    )
    return inputs, readme


def _limitations(tmp_path: Path, family: str, platform: str) -> ReferenceContent:
    inputs, readme = _load_baked(tmp_path, family, platform)
    inputs = dataclasses.replace(inputs, known_hazards=_hazards(family, platform))
    result = get_product_reference(inputs, "limitations", readme=readme)
    assert isinstance(result, ReferenceContent), (family, platform, result)
    return result


# --------------------------------------------------------------------------------------------
# manifests: schema validity, counts, dates, key order
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("family", "platform"), list(PILOTS), ids=PILOT_IDS)
def test_manifest_validates_against_the_real_schema_with_the_expected_hazard_count(
    family: str, platform: str
) -> None:
    manifest = _real_manifest(family, platform)
    schema = json.loads(MANIFEST_SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(manifest)
    hazards = known_hazards_from_manifest(manifest)
    assert len(hazards) == PILOTS[(family, platform)][0], hazards
    assert list(manifest)[-1] == "known_hazards", "known_hazards must stay the LAST top-level key"
    assert hazards[-1].verified_at == VERIFIED_DATE
    assert date.fromisoformat(hazards[-1].verified_at).isoformat() == VERIFIED_DATE


def test_cells_go_keeps_its_two_earlier_hazards_first_and_unchanged() -> None:
    hazards = _hazards("cells", "go")
    assert [hazard.verified_at for hazard in hazards] == ["2026-10-24", "2026-11-14", "2026-11-14"]
    assert "goroutine" in hazards[0].text.lower() and "workbook" in hazards[0].text.lower()
    assert "has no API for several commonly expected spreadsheet" in _flat(hazards[1].text)
    assert "CalculateFormula" not in hazards[0].text and "CalculateFormula" not in hazards[1].text


def test_3d_python_keeps_one_paragraph_and_its_original_file_format_text() -> None:
    (hazard,) = _hazards("3d", "python")
    text = _flat(hazard.text)
    # the TC-815 text is still there, in front of the new sentences
    assert text.index("48 of the FileFormat class attributes") < text.index(
        "Scene.save(path, FileFormat.FBX7400ASCII())"
    )
    assert "The safe pattern" in text and "the real trigger" in text.lower()


@pytest.mark.parametrize(("family", "platform"), list(PILOTS), ids=PILOT_IDS)
def test_each_new_hazard_is_scoped_to_the_pinned_or_indexed_version(family: str, platform: str) -> None:
    pin = PILOTS[(family, platform)][1]
    sidecar = json.loads(
        (MANIFESTS / f"product_reference_{family}_{platform}.json").read_text(encoding="utf-8")
    )
    # the pin the hazard talks about is the pin the served corpus is built from
    assert sidecar["source_ref"] == pin
    text = _new_hazard_text(family, platform)
    assert pin[:8] in text or "26.1.0" in text, (
        "the hazard must name the version or commit it was verified on"
    )


# --------------------------------------------------------------------------------------------
# 3d/python: what the text claims
# --------------------------------------------------------------------------------------------


def test_3d_python_hazard_states_the_fbx7400ascii_save_failure_and_the_two_working_factories() -> None:
    text = _new_hazard_text("3d", "python")
    assert "Scene.save(path, FileFormat.FBX7400ASCII())" in text
    assert "Scene.save(stream, FileFormat.FBX7400ASCII())" in text
    assert 'NotImplementedError ("supports_format is not implemented")' in text
    assert 'Scene.save("x.fbx")' in text and 'Scene.save("x.dae")' in text
    assert "only GLTF2() and MICROSOFT_3MF_FORMAT() can be used to save" in text
    # it does not repeat the audit's unreproduced claim that every factory is usable
    assert "all four" not in text.lower()


def test_3d_python_hazard_states_the_primitive_skipping_and_the_zero_byte_3mf() -> None:
    text = _new_hazard_text("3d", "python")
    for primitive in ("Box()", "Sphere()", "Cylinder()"):
        assert primitive in text, primitive
    assert "to_mesh()" in text and "Box().to_mesh()" in text
    assert "header-only file with no facets" in text
    assert '"meshes" array is empty' in text
    assert 'Scene.save("x.3mf") writes a 0-byte file with no error' in text
    assert "returns without writing anything when the scene holds no Mesh" in text
    assert "check the file size" in text


def test_the_served_3d_python_corpus_really_lists_the_factories_the_hazard_names(
    corpus: ConformanceCorpus,
) -> None:
    """The hazard names FBX7400ASCII() as a static factory the index shows; a changed index cannot leave
    the claim pointing at nothing."""
    chunks = {
        extract_fqn(chunk["text"]): chunk["text"] for chunk in corpus.pilots["3d__python"].symbol_chunks()
    }
    text = chunks["aspose.threed.FileFormat.FileFormat"]
    for factory in ("FBX7400ASCII()", "GLTF2()", "MICROSOFT_3MF_FORMAT()", "WAVEFRONT_OBJ()"):
        assert f"- {factory} [static]" in text, factory
    assert "aspose.threed.Scene.Scene" in chunks


# --------------------------------------------------------------------------------------------
# cells/go: what the text claims
# --------------------------------------------------------------------------------------------


def test_cells_go_hazard_states_the_leading_equals_failure_and_the_working_spelling() -> None:
    text = _new_hazard_text("cells", "go")
    assert "takes the formula text WITHOUT a leading" in text
    assert "fa4e890e" in text
    for function in ("SUM", "AVERAGE", "MAX", "MIN"):
        assert f'"={function}' in text, function
    assert 'unsupported function "=SUM"' in text
    # the exact reproduced results, not a vague statement
    assert 'CalculateFormula("SUM(A1:A2)", ws) returns 7' in text
    assert '"AVERAGE(A1:A2)" returns 3.5' in text and '"MAX(A1:A2)" returns 4' in text
    assert '"MIN(A1:A2)" returns 3' in text
    assert "store and evaluate formulas without the leading" in text
    assert "SetFormula" in text and "GetFormula" in text


def test_cells_go_hazard_states_the_if_error_and_does_not_blame_the_equals_for_it() -> None:
    text = _new_hazard_text("cells", "go")
    assert 'CalculateFormula("IF(A1>0,1,2)", ws) (with or without the "=")' in text
    assert 'cell "A1>0" not found' in text


def test_the_cells_go_readme_never_mentions_a_leading_equals_for_calculateformula(tmp_path: Path) -> None:
    """The hazard's premise: the README's CalculateFormula limitation says only which functions are
    supported, never that the formula text must omit the leading '='."""
    inputs, _readme = _load_baked(tmp_path, "cells", "go")
    document = inputs.readme_document
    content = getattr(document, "content", "")
    assert "`CalculateFormula` supports only `SUM`, `AVERAGE`, `MAX`, and `MIN`" in _flat(content)
    assert "leading" not in content.lower()


def test_the_served_cells_go_corpus_really_has_the_calculateformula_symbol(corpus: ConformanceCorpus) -> None:
    fqns = {extract_fqn(chunk["text"]) for chunk in corpus.pilots["cells__go"].symbol_chunks()}
    assert "CalculateFormula" in fqns


# --------------------------------------------------------------------------------------------
# pdf/python: the RD2-210 row is REPRODUCED as an index-vs-package drift
# --------------------------------------------------------------------------------------------


def test_the_ledger_row_rd2_210_is_the_real_pdf_python_permissions_finding() -> None:
    issues = {issue["id"]: issue for issue in json.loads(ROUND2_ISSUES_PATH.read_text(encoding="utf-8"))}
    issue = issues["PDF-PYTHON-001"]
    assert issue["pilot"] == "pdf/python"
    assert "Permissions" in issue["title"]
    row = [
        line for line in ROUND2_REVIEW_PATH.read_text(encoding="utf-8").splitlines() if "| RD2-210 |" in line
    ]
    assert len(row) == 1 and "| pdf/python |" in row[0] and "Permissions" in row[0]
    # tokens present both in the real ledger record and in the manifest hazard text
    issue_text = _flat(json.dumps(issue).lower())
    manifest_text = _flat(yaml.safe_dump(_real_manifest("pdf", "python")).lower())
    assert "permissions" in issue_text and "int-subclass" in issue_text
    assert "permissions" in manifest_text and "int subclass" in manifest_text
    assert "aspose_pdf.permissions.permissions" in manifest_text


def test_pdf_python_hazard_states_the_pin_the_wheel_and_the_exact_measured_drift() -> None:
    text = _new_hazard_text("pdf", "python")
    assert "commit c391ee72 (2026-10-08)" in text
    assert "pip install aspose-pdf-foss-for-python" in text
    assert "0.1.0 wheel" in text
    assert "230 Python modules is byte-identical to upstream commit 71670a2a (2026-10-28)" in text
    assert "78 commits after the indexed one" in text
    assert 'version string is "0.1.0" in both' in text
    assert "65 additional modules (13 top-level and 52 under aspose_pdf/engine) and 26 modified ones" in text
    assert "none of the 65 is in this index" in text


def test_pdf_python_hazard_states_the_permissions_symbol_and_the_safe_pattern() -> None:
    text = _new_hazard_text("pdf", "python")
    assert "aspose_pdf.permissions.Permissions" in text
    assert "int subclass" in text
    assert "`from aspose_pdf import Permissions` raises ImportError" in text
    assert "imports and works from the installed 0.1.0 package" in text
    assert "raw integer /P word" in text
    assert '"not found in the index" does not mean "absent from the package you installed"' in text
    assert 'python -c "from aspose_pdf import X"' in text


def test_the_served_pdf_python_corpus_really_has_no_permissions_symbol(corpus: ConformanceCorpus) -> None:
    """The index side of the drift: no indexed symbol is a Permissions class or lives in a permissions
    module, so get_symbol('aspose_pdf.permissions.Permissions') cannot resolve."""
    fqns = {extract_fqn(chunk["text"]) for chunk in corpus.pilots["pdf__python"].symbol_chunks()}
    fqns.discard(None)
    assert fqns, "the pdf/python corpus must serve symbols"
    assert "aspose_pdf.permissions.Permissions" not in fqns
    assert "aspose_pdf.Permissions" not in fqns
    assert not [fqn for fqn in fqns if fqn.rsplit(".", 1)[-1] == "Permissions"]
    assert not [fqn for fqn in fqns if ".permissions." in fqn or fqn.endswith(".permissions")]


def test_the_baked_pdf_python_sidecar_names_the_pin_and_the_wheel_version() -> None:
    sidecar = json.loads((MANIFESTS / "product_reference_pdf_python.json").read_text(encoding="utf-8"))
    assert sidecar["source_ref"].startswith("c391ee72")
    assert sidecar["install_served_version"] == "0.1.0"


# --------------------------------------------------------------------------------------------
# the real limitations answer carries each hazard, distinctly framed, after the README text
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("family", "platform"), list(PILOTS), ids=PILOT_IDS)
def test_the_real_limitations_answer_carries_the_new_hazard_distinctly_framed(
    tmp_path: Path, family: str, platform: str
) -> None:
    result = _limitations(tmp_path, family, platform)
    text = _flat(result.text)
    expected = _new_hazard_text(family, platform)
    assert expected in text, (family, platform)
    assert VERIFIED_DATE in text
    assert text.count(KNOWN_HAZARD_LEAD) >= PILOTS[(family, platform)][0]
    assert result.notice is not None and "Disclosed by this project itself" in result.notice
    # G3/TC-824: untrusted only while upstream README text heads the answer
    assert result.untrusted_upstream_content is (not result.text.startswith(KNOWN_HAZARD_LEAD))
    # the hazard is appended to the answer, never replacing the README text that heads it
    if result.untrusted_upstream_content:
        assert text.index(KNOWN_HAZARD_LEAD) > 0


def test_cells_go_limitations_answer_lists_the_three_hazards_in_order(tmp_path: Path) -> None:
    result = _limitations(tmp_path, "cells", "go")
    text = _flat(result.text)
    first = text.index("goroutine")
    second = text.index("no API for several commonly expected")
    third = text.index("takes the formula text WITHOUT a leading")
    assert first < second < third
    # README-sourced CalculateFormula limitation still precedes the project's own paragraph on it
    assert text.index("CalculateFormula` supports only") < third


def test_3d_python_limitations_answer_still_carries_the_filefmt_text_and_the_new_sentences(
    tmp_path: Path,
) -> None:
    text = _flat(_limitations(tmp_path, "3d", "python").text)
    assert "48 of the FileFormat class attributes" in text
    assert "Scene.save(path, FileFormat.FBX7400ASCII())" in text
    assert 'Scene.save("x.3mf") writes a 0-byte file' in text


def test_pdf_python_limitations_answer_carries_the_drift_disclosure(tmp_path: Path) -> None:
    text = _flat(_limitations(tmp_path, "pdf", "python").text)
    assert "Permissions" in text and "78 commits after the indexed one" in text


# --------------------------------------------------------------------------------------------
# no other pilot's served disclosure changed
# --------------------------------------------------------------------------------------------


def test_each_wave_c_fragment_appears_only_on_its_own_pilots_manifest() -> None:
    for owner, fragments in FRAGMENTS.items():
        own = _flat((CONFIG_PRODUCTS / owner[0] / f"{owner[1]}.yaml").read_text(encoding="utf-8"))
        for fragment in fragments:
            assert fragment in own, (owner, fragment)
    for path in sorted(CONFIG_PRODUCTS.glob("*/*.yaml")):
        key = (path.parent.name, path.stem)
        other = _flat(path.read_text(encoding="utf-8"))
        for owner, fragments in FRAGMENTS.items():
            if owner == key:
                continue
            for fragment in fragments:
                assert fragment not in other, (key, "carries a wave-C hazard belonging to", owner)


@pytest.mark.parametrize(("family", "platform"), _all_other_pilots(), ids=lambda v: str(v))
def test_no_other_pilots_limitations_answer_mentions_a_wave_c_hazard(
    tmp_path: Path, family: str, platform: str
) -> None:
    inputs, readme = _load_baked(tmp_path, family, platform)
    hazards = _hazards(family, platform)
    result = get_product_reference(
        dataclasses.replace(inputs, known_hazards=hazards), "limitations", readme=readme
    )
    text = _flat(result.text) if isinstance(result, ReferenceContent) else ""
    for fragments in FRAGMENTS.values():
        for fragment in fragments:
            assert fragment not in text, (family, platform, fragment)
    assert "78 commits after the indexed one" not in text
    assert "FBX7400ASCII())" not in text
    assert 'CalculateFormula("=SUM(A1:A2)"' not in text


def test_no_registry_entry_was_added_for_the_wave_c_hazards() -> None:
    """The disclosures are manifest hazards, not registry entries: the 3d/python registry keeps exactly
    its seven pre-existing ids and no pdf/python or other new registry file appears."""
    ids = [
        defect["id"]
        for defect in yaml.safe_load(
            (REPO_ROOT / "config" / "known_defects" / "3d__python.yaml").read_text(encoding="utf-8")
        )["defects"]
    ]
    assert ids == [
        "3D-PYTHON-001",
        "3D-PYTHON-002",
        "3D-PYTHON-006",
        "3D-PYTHON-008",
        "3D-PYTHON-010",
        "3D-PYTHON-013",
        "3D-PYTHON-009",
    ]
    assert not (REPO_ROOT / "config" / "known_defects" / "pdf__python.yaml").exists()


# --------------------------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> ConformanceCorpus:
    return load_demo_manifests_corpus(tmp_path_factory.mktemp("tc832-corpus"))
