"""G3/TC-815 (REQ-G3-015): disclosure wave B - three round-2 rows that nothing served told an agent about.

* ``3D-PYTHON-009`` (ledger row RD2-034, ``3d/python``): ``aspose.threed.FileFormat.FileFormat`` is
  served with 48 class-level constants rendered ``X = None (read-only)`` beside 4 static factory
  methods; the constants really are ``None`` at runtime, so using one as a format raises
  ``AttributeError`` or is silently ignored. Symbol-scoped, so it is a registry entry
  (``config/known_defects/3d__python.yaml``) PLUS a ``known_hazards`` paragraph
  (``config/products/3d/python.yaml``) so ``get_product_reference(section="limitations")`` says it too.
* ``WORDS-NET-005`` (ledger row RD2-269, ``words/net``): ``Document.Save(HttpResponse, ...)`` is served
  with no framework qualifier but is compiled only into the net462 build of the published package
  (``#if NETFRAMEWORK`` upstream). A registry entry (``config/known_defects/words__net.yaml``).
* ``WORDS-PY-009`` (ledger row RD2-283, ``words/python``): ``Paragraph(children=[Run(...)])`` works only
  through a private pydantic wrap-validator the library's own docstring calls "intentionally not part of
  the public attribute surface". A capability/limitation statement, so a ``known_hazards`` paragraph
  (``config/products/words/python.yaml``).

Every claim was re-verified on 2026-11-14 against the real upstream source and the real installed or
published package (a throwaway venv with ``aspose-3d-foss`` 26.1.0; the real ``Aspose.Words.FOSS`` 26.3.0
NuGet package's three per-framework assemblies plus ``Document.cs``/``Aspose.Words.csproj`` at the
indexed commit; ``aspose-words-foss`` 26.7.0 built from the indexed commit ``2d2efee2`` - it is not on
PyPI). Nothing is patched: the findings are disclosed, never worked around (Decision A).

``3D-PYTHON-009`` and ``WORDS-NET-005`` are round-2 ids whose category is not ``upstream-library-bug``,
so both are in ``tests/indexing/test_known_defects_completeness.py``'s ``_ADDITIONAL_DISCLOSABLE_IDS``
(the TC-706/707/711/728/796 precedent).

These tests exercise the real committed registry/manifest files and the real committed
``infra/demo-manifests`` corpus; none of them rely on any generation having been regenerated.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import jsonschema
import pytest
import yaml

from foss_mcp.indexing.known_defects import KnownDefect, load_known_defects
from foss_mcp.indexing.lexical_index_writer import build_lexical_index
from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.get_product_reference import (
    ProductReferenceInputs,
    ReferenceContent,
    get_product_reference,
    known_hazards_from_manifest,
)
from foss_mcp.mcp.tools.get_symbol import ResolvedSignature, SymbolSignature, extract_fqn, get_symbol
from foss_mcp.mcp.tools.search_symbols import SymbolMatch, search_symbols
from foss_mcp.normalization.chunker import chunk_document
from foss_mcp.normalization.document_schema import Provenance, SourceKind, make_document
from tests.conformance.corpus import ConformanceCorpus, load_demo_manifests_corpus

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "infra"))
from fetch_product_reference import load_manifest_sidecar  # noqa: E402

DEFECT_SCHEMA_PATH = REPO_ROOT / "schemas" / "product" / "known_defects.schema.json"
MANIFEST_SCHEMA_PATH = REPO_ROOT / "schemas" / "product" / "product-manifest.schema.json"
REGISTRY_DIR = REPO_ROOT / "config" / "known_defects"
CONFIG_PRODUCTS = REPO_ROOT / "config" / "products"
MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
ROUND2_ISSUES_PATH = REPO_ROOT / "docs" / "round2" / "issues.json"
ROUND2_REVIEW_PATH = REPO_ROOT / "docs" / "ROUND2_REVIEW.md"

VERIFIED_DATE = "2026-11-14"
KNOWN_HAZARD_LEAD = "Known hazard (verified by this project)"
SOURCE_KIND = "self_extracted"

FILE_FORMAT_FQN = "aspose.threed.FileFormat.FileFormat"
FILE_FORMAT_ALIAS_FQN = "aspose.threed.FileFormat"
DOCUMENT_FQN = "Aspose.Words.Document"

# (registry id, family, platform, ledger row, expected round-2 pilot string, expected package)
DEFECTS = (
    ("3D-PYTHON-009", "3d", "python", "RD2-034", "3d/python", "aspose-3d-foss", "26.1.0"),
    ("WORDS-NET-005", "words", "net", "RD2-269", "words/net", "Aspose.Words.FOSS", "26.3.0"),
)
DEFECT_IDS = tuple(row[0] for row in DEFECTS)
HAZARD_PILOTS = (("words", "python"), ("3d", "python"))


def _registry_document(family: str, platform: str) -> dict[str, Any]:
    return yaml.safe_load((REGISTRY_DIR / f"{family}__{platform}.yaml").read_text(encoding="utf-8"))


def _defect(defect_id: str) -> KnownDefect:
    for _id, family, platform, *_rest in DEFECTS:
        if _id == defect_id:
            (match,) = [d for d in load_known_defects(family, platform) if d.id == defect_id]
            return match
    raise AssertionError(defect_id)


def _full_text(defect: KnownDefect) -> str:
    return " ".join((defect.summary, defect.user_action, defect.evidence.repro)).lower()


def _real_manifest(family: str, platform: str) -> dict[str, Any]:
    return yaml.safe_load((CONFIG_PRODUCTS / family / f"{platform}.yaml").read_text(encoding="utf-8"))


def _all_other_pilots() -> list[tuple[str, str]]:
    return sorted(
        (path.parent.name, path.stem)
        for path in CONFIG_PRODUCTS.glob("*/*.yaml")
        if (path.parent.name, path.stem) not in HAZARD_PILOTS
    )


# --------------------------------------------------------------------------------------------
# registry: schema validity, ids, ledger rows
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("defect_id", "family", "platform"), [row[:3] for row in DEFECTS])
def test_real_registry_file_validates_against_the_real_schema(
    defect_id: str, family: str, platform: str
) -> None:
    document = _registry_document(family, platform)
    schema = json.loads(DEFECT_SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(document))
    assert errors == [], f"{family}/{platform} registry failed schema validation: {errors}"
    assert document["pilot"] == f"{family}_{platform}"
    assert defect_id in [d["id"] for d in document["defects"]]


@pytest.mark.parametrize(("defect_id", "family", "platform", "row", "ledger_pilot"), [r[:5] for r in DEFECTS])
def test_each_id_equals_a_real_ledger_issue_id_and_appears_exactly_once(
    defect_id: str, family: str, platform: str, row: str, ledger_pilot: str
) -> None:
    issues = json.loads(ROUND2_ISSUES_PATH.read_text(encoding="utf-8"))
    ledger = [issue for issue in issues if issue["id"] == defect_id]
    assert len(ledger) == 1, f"{defect_id} is not a real docs/round2/issues.json id"
    assert ledger[0]["pilot"] == ledger_pilot
    # The ledger row this card closes is a real, still-open row of the committed register.
    row_lines = [
        line for line in ROUND2_REVIEW_PATH.read_text(encoding="utf-8").splitlines() if f"| {row} |" in line
    ]
    assert len(row_lines) == 1
    assert f"| {ledger_pilot} |" in row_lines[0]

    ids = [defect["id"] for defect in _registry_document(family, platform)["defects"]]
    assert ids.count(defect_id) == 1
    assert len(ids) == len(set(ids))


def test_the_ledger_rows_title_matches_the_registry_entrys_subject() -> None:
    issues = {issue["id"]: issue for issue in json.loads(ROUND2_ISSUES_PATH.read_text(encoding="utf-8"))}
    assert "FileFormat" in issues["3D-PYTHON-009"]["title"] and "= None" in issues["3D-PYTHON-009"]["title"]
    assert "HttpResponse" in issues["WORDS-NET-005"]["title"] and "net462" in issues["WORDS-NET-005"]["title"]
    assert "children" in issues["WORDS-PY-009"]["title"]
    assert "intentionally not part of the public attribute surface" in issues["WORDS-PY-009"]["title"]


@pytest.mark.parametrize(
    ("defect_id", "family", "platform", "row", "ledger_pilot", "package", "version"), DEFECTS
)
def test_each_entry_is_open_version_scoped_with_a_real_date_and_a_user_action(
    defect_id: str, family: str, platform: str, row: str, ledger_pilot: str, package: str, version: str
) -> None:
    defect = _defect(defect_id)
    assert defect.status == "open"
    assert defect.applies_to.package_versions == (version,)
    assert defect.evidence.verified_on.package == package
    assert defect.evidence.verified_on.version == version
    assert defect.evidence.verified_on.date == VERIFIED_DATE
    assert date.fromisoformat(defect.evidence.verified_on.date).isoformat() == VERIFIED_DATE
    assert len(defect.summary) <= 240
    assert defect.user_action.strip(), "user_action must be present"
    assert row in defect.evidence.repro, "the evidence must cite the ledger row it closes"


def test_subject_kinds() -> None:
    assert _defect("3D-PYTHON-009").subject.kind == "class"
    assert _defect("WORDS-NET-005").subject.kind == "method"


# --------------------------------------------------------------------------------------------
# refs resolve in the REAL corpus
# --------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> ConformanceCorpus:
    return load_demo_manifests_corpus(tmp_path_factory.mktemp("tc815-corpus"))


def _symbol_chunks(corpus: ConformanceCorpus, scope: str) -> dict[str, str]:
    pilot = corpus.pilots[scope]
    chunks: dict[str, str] = {}
    for chunk in pilot.symbol_chunks():
        fqn = extract_fqn(chunk["text"])
        if fqn is not None:
            chunks.setdefault(fqn, chunk["text"])
    return chunks


@pytest.mark.parametrize(("defect_id", "family", "platform"), [row[:3] for row in DEFECTS])
def test_every_ref_resolves_to_a_real_indexed_fqn_in_the_real_corpus(
    corpus: ConformanceCorpus, defect_id: str, family: str, platform: str
) -> None:
    real_fqns = set(_symbol_chunks(corpus, f"{family}__{platform}"))
    refs = _defect(defect_id).subject.refs
    assert refs
    for ref in refs:
        assert ref in real_fqns, f"{defect_id}: ref {ref!r} is not a real indexed {family}/{platform} FQN"


def test_the_served_file_format_chunk_really_has_48_none_constants_and_4_static_factories(
    corpus: ConformanceCorpus,
) -> None:
    """The disclosure's own numbers are checked against what the real committed corpus serves, so a
    changed index cannot leave the count silently wrong."""
    text = _symbol_chunks(corpus, "3d__python")[FILE_FORMAT_FQN]
    none_constants = [line for line in text.splitlines() if line.strip().endswith("= None (read-only)")]
    assert len(none_constants) == 48
    static_methods = [line.strip() for line in text.splitlines() if line.strip().endswith("() [static]")]
    assert static_methods == [
        "- FBX7400ASCII() [static]",
        "- GLTF2() [static]",
        "- MICROSOFT_3MF_FORMAT() [static]",
        "- WAVEFRONT_OBJ() [static]",
    ]
    names = {line.strip().split()[1] for line in none_constants}
    for named in ("GLTF", "STL_BINARY", "STLASCII", "PLY", "COLLADA", "DISCREET_3DS", "PDF", "USD"):
        assert named in names, named
    assert "FBX7400ASCII" not in names


def test_the_served_words_net_document_chunk_really_lists_the_httpresponse_overload(
    corpus: ConformanceCorpus,
) -> None:
    text = _symbol_chunks(corpus, "words__net")[DOCUMENT_FQN]
    overloads = [line for line in text.splitlines() if "Save(response: HttpResponse" in line]
    assert len(overloads) == 1
    assert "contentDisposition: ContentDisposition" in overloads[0]


# --------------------------------------------------------------------------------------------
# the disclosure text states the real trigger and safe pattern, and does not overstate
# --------------------------------------------------------------------------------------------


def test_3d_python_009_names_the_trigger_the_failure_modes_and_the_safe_pattern() -> None:
    defect = _defect("3D-PYTHON-009")
    text = _full_text(defect)
    assert "48" in defect.summary
    assert "none" in text
    for named in ("gltf", "stl_binary", "ply"):
        assert named in text, named
    # failure modes
    assert "attributeerror" in text
    assert "create_save_options" in text
    assert "silently ignores" in defect.user_action
    # the 3 working factories, and the one that does not work
    for factory in ("fbx7400ascii()", "gltf2()", "microsoft_3mf_format()"):
        assert factory in defect.user_action.lower(), factory
    assert "wavefront_obj()" in text and "modulenotfounderror" in text
    # safe pattern, and the index/package provenance
    assert "FileFormat.GLTF2()" in defect.user_action
    assert "65b1f577c0f16d0d9112bb6c1153d3024543ac02" in defect.evidence.repro


def test_3d_python_009_does_not_claim_the_audits_unreproduced_all_four_factories_work() -> None:
    """The ledger said all 4 factories are working singletons; WAVEFRONT_OBJ() does not reproduce
    (ModuleNotFoundError), so the entry must not repeat the unreproduced claim."""
    defect = _defect("3D-PYTHON-009")
    assert "did not reproduce" in defect.evidence.repro
    assert "raises ModuleNotFoundError" in defect.summary
    assert "all 4" not in defect.user_action.lower()


def test_words_net_005_names_the_net462_only_overload_and_the_modern_dotnet_alternative() -> None:
    defect = _defect("WORDS-NET-005")
    text = _full_text(defect)
    assert "httpresponse" in text
    assert "net462" in text
    assert "net8.0" in text and "netstandard2.0" in text
    assert "#if netframework" in text
    assert "26.3.0" in text
    # safe pattern on modern .NET: save to a Stream instead
    assert "save(stream, saveformat)" in defect.user_action.lower()
    assert "save(stream, saveoptions)" in defect.user_action.lower()
    assert "0d3f1add920f5294d726b4f286c2d6ba7c3ae22c" in defect.evidence.repro
    # the overload counts the metadata read found per framework
    assert "7 save overloads in net462" in text and "6 in each of netstandard2.0 and net8.0" in text


def test_words_net_005_does_not_claim_the_overload_is_missing_everywhere() -> None:
    defect = _defect("WORDS-NET-005")
    assert "only when the project targets .net framework (net462)" in defect.user_action.lower()
    assert "does not exist in any" not in _full_text(defect)


# --------------------------------------------------------------------------------------------
# real end-to-end: the REAL, committed registry surfaces through get_symbol and search_symbols
# --------------------------------------------------------------------------------------------


class _InMemoryStore:
    """One active generation of in-memory chunks standing in for GenerationManifestStore - the exact
    pattern the TC-796 test module uses. Only the symbol content is synthetic; the registry the calls
    below read is the real, committed file."""

    def __init__(self, repository: str, bodies: tuple[str, ...]) -> None:
        chunks = []
        for body in bodies:
            doc = make_document(
                source_kind=SourceKind.SELF_EXTRACTED,
                content_type="api_surface",
                provenance=Provenance(repository=repository, commit="tc815"),
                evidence_refs=(),
                title="API surface",
                body=body,
            )
            chunks.extend(chunk_document(doc))
        chunk_ids = [f"chunk-{index}" for index in range(len(chunks))]
        lexical = build_lexical_index(chunks, chunk_ids, "gen-tc815")
        self._manifest = SimpleNamespace(payload={"lexical_index": lexical})

    def read_active(self, scope: str) -> str:
        return "gen-tc815"

    def read_generation(self, scope: str, generation_id: str) -> SimpleNamespace:
        return self._manifest


def _file_format_body() -> str:
    lines = [
        "## FileFormat",
        "",
        f"FQN: {FILE_FORMAT_FQN}",
        "Kind: class",
        "",
        "Methods:",
        "  - GLTF2() [static]",
        "",
        "Properties:",
        "  - GLTF = None (read-only)",
        "  - STL_BINARY = None (read-only)",
    ]
    return "\n".join(lines) + "\n"


def _document_body() -> str:
    lines = [
        "## Document",
        "",
        f"FQN: {DOCUMENT_FQN}",
        "Kind: class_declaration",
        "",
        "Methods:",
        "  - Save(fileName: string) -> SaveOutputParameters",
        "  - Save(response: HttpResponse, fileName: string, contentDisposition: ContentDisposition, "
        "saveOptions: SaveOptions) -> SaveOutputParameters",
    ]
    return "\n".join(lines) + "\n"


@pytest.fixture(scope="module")
def file_format_store() -> _InMemoryStore:
    return _InMemoryStore("aspose-3d-foss/Aspose.3D-FOSS-for-Python", (_file_format_body(),))


@pytest.fixture(scope="module")
def document_store() -> _InMemoryStore:
    return _InMemoryStore("aspose-words-foss/Aspose.Words-FOSS-for-.NET", (_document_body(),))


def _joined(lines: tuple[str, ...]) -> str:
    return " ".join(lines).lower()


def test_get_symbol_surfaces_the_file_format_disclosure(file_format_store: _InMemoryStore) -> None:
    result = get_symbol(file_format_store, Scope(family="3d", platform="python"), FILE_FORMAT_FQN)  # type: ignore[arg-type]

    assert isinstance(result, SymbolSignature)
    text = _joined(result.known_defects)
    assert "48 format constants" in text and "none" in text
    assert "modulenotfounderror" in text


def test_get_symbol_resolved_member_surfaces_the_class_scoped_disclosure(
    file_format_store: _InMemoryStore,
) -> None:
    result = get_symbol(
        file_format_store,
        Scope(family="3d", platform="python"),
        f"{FILE_FORMAT_FQN}.GLTF",  # type: ignore[arg-type]
    )

    assert isinstance(result, ResolvedSignature)
    assert result.fqn == FILE_FORMAT_FQN
    assert "48 format constants" in _joined(result.known_defects)


def test_search_symbols_surfaces_the_file_format_disclosure(file_format_store: _InMemoryStore) -> None:
    result = search_symbols(file_format_store, Scope(family="3d", platform="python"), FILE_FORMAT_FQN)  # type: ignore[arg-type]

    assert isinstance(result, list), f"expected a real symbol hit, got a Miss: {result!r}"
    matches: list[SymbolMatch] = result
    assert len(matches) == 1
    assert "48 format constants" in _joined(matches[0].known_defects)


def test_get_symbol_surfaces_the_httpresponse_overload_disclosure(document_store: _InMemoryStore) -> None:
    result = get_symbol(document_store, Scope(family="words", platform="net"), DOCUMENT_FQN)  # type: ignore[arg-type]

    assert isinstance(result, SymbolSignature)
    text = _joined(result.known_defects)
    assert "httpresponse" in text and "net462" in text
    # WORDS-NET-001's own entry is still there too - this card adds, never replaces.
    assert "getancestorof" in text


def test_get_symbol_resolved_save_member_surfaces_the_disclosure(document_store: _InMemoryStore) -> None:
    result = get_symbol(document_store, Scope(family="words", platform="net"), f"{DOCUMENT_FQN}.Save")  # type: ignore[arg-type]

    assert isinstance(result, ResolvedSignature)
    assert result.fqn == DOCUMENT_FQN
    assert "net462" in _joined(result.known_defects)


def test_search_symbols_surfaces_the_httpresponse_overload_disclosure(document_store: _InMemoryStore) -> None:
    result = search_symbols(document_store, Scope(family="words", platform="net"), DOCUMENT_FQN)  # type: ignore[arg-type]

    assert isinstance(result, list), f"expected a real symbol hit, got a Miss: {result!r}"
    assert len(result) == 1
    assert "net462" in _joined(result[0].known_defects)


# --------------------------------------------------------------------------------------------
# known_hazards: schema validity and the real limitations answer for words/python and 3d/python
# --------------------------------------------------------------------------------------------


def _load_inputs(tmp_path: Path, family: str, platform: str) -> ProductReferenceInputs:
    """The REAL baked sidecar for *family*/*platform* (the way the live server loads it), copied to a
    temporary file first so nothing under infra/demo-manifests is ever opened for writing."""
    sidecar = tmp_path / f"product_reference_{family}_{platform}.json"
    shutil.copyfile(MANIFESTS / f"product_reference_{family}_{platform}.json", sidecar)
    inputs = load_manifest_sidecar(sidecar)
    assert inputs is not None, (family, platform)
    return inputs


@pytest.mark.parametrize(("family", "platform"), HAZARD_PILOTS)
def test_manifest_validates_against_the_real_schema_with_exactly_one_hazard(
    family: str, platform: str
) -> None:
    manifest = _real_manifest(family, platform)
    schema = json.loads(MANIFEST_SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(manifest)
    hazards = known_hazards_from_manifest(manifest)
    assert len(hazards) == 1, hazards
    assert hazards[0].verified_at == VERIFIED_DATE
    assert date.fromisoformat(hazards[0].verified_at).isoformat() == VERIFIED_DATE
    assert "known_hazards" == list(manifest)[-1], "known_hazards must stay the LAST top-level key"


@pytest.mark.parametrize(("family", "platform"), HAZARD_PILOTS)
def test_the_real_limitations_answer_carries_the_hazard_distinctly_framed(
    tmp_path: Path, family: str, platform: str
) -> None:
    inputs = _load_inputs(tmp_path, family, platform)
    hazards = known_hazards_from_manifest(_real_manifest(family, platform))
    assert hazards
    before = get_product_reference(inputs, "limitations", readme=None)
    after = get_product_reference(
        dataclasses.replace(inputs, known_hazards=hazards), "limitations", readme=None
    )

    assert isinstance(after, ReferenceContent)
    assert after.section == "limitations"
    assert after.untrusted_upstream_content is False
    assert after.text.count(KNOWN_HAZARD_LEAD) >= 1
    assert hazards[0].text.split()[0] in after.text
    assert VERIFIED_DATE in after.text
    # the hazard is ADDED to the answer, never replacing what was already there
    if isinstance(before, ReferenceContent):
        assert after.text != before.text
        assert before.text.strip() in after.text or KNOWN_HAZARD_LEAD in after.text


def test_words_python_limitations_states_children_is_a_private_unsupported_idiom(tmp_path: Path) -> None:
    inputs = _load_inputs(tmp_path, "words", "python")
    hazards = known_hazards_from_manifest(_real_manifest("words", "python"))
    result = get_product_reference(
        dataclasses.replace(inputs, known_hazards=hazards), "limitations", readme=None
    )

    assert isinstance(result, ReferenceContent)
    text = result.text.lower()
    assert "paragraph(children=[run(" in text
    assert "not part of the public api" in text
    assert "private" in text and "wrap-validator" in text
    assert "_absorb_children" in text and "_children" in text
    assert "intentionally not part of the public attribute surface" in text
    assert "unsupported" in text and "unstable" in text
    assert "26.7.0" in text
    # Body.children / HeaderFooter.children are ordinary public fields: the hazard must not say otherwise
    assert "body.children and headerfooter.children" in text and "ordinary public" in text


def test_3d_python_limitations_states_the_none_constants_the_failure_modes_and_the_safe_pattern(
    tmp_path: Path,
) -> None:
    inputs = _load_inputs(tmp_path, "3d", "python")
    hazards = known_hazards_from_manifest(_real_manifest("3d", "python"))
    result = get_product_reference(
        dataclasses.replace(inputs, known_hazards=hazards), "limitations", readme=None
    )

    assert isinstance(result, ReferenceContent)
    text = result.text
    assert "48 of the FileFormat class attributes" in text
    assert "genuinely None at runtime" in text
    for factory in ("FBX7400ASCII()", "GLTF2()", "MICROSOFT_3MF_FORMAT()", "WAVEFRONT_OBJ()"):
        assert factory in text, factory
    assert "ModuleNotFoundError" in text
    assert "AttributeError" in text and "create_save_options" in text
    assert "silently ignores the constant" in text
    assert "FileFormat.GLTF2()" in text
    assert "26.1.0" in text


def test_each_hazard_text_states_a_trigger_and_a_safe_pattern() -> None:
    words_py = known_hazards_from_manifest(_real_manifest("words", "python"))[0].text.lower()
    assert "unsupported and unstable" in words_py
    assert "do not build on it" in words_py or "re-check it" in words_py
    three_d = known_hazards_from_manifest(_real_manifest("3d", "python"))[0].text.lower()
    assert "the real trigger" in three_d and "the safe pattern" in three_d


# --------------------------------------------------------------------------------------------
# no other pilot's served disclosure changed
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("family", "platform"), _all_other_pilots(), ids=lambda v: str(v))
def test_no_other_pilots_hazards_or_registry_mention_the_new_disclosures(family: str, platform: str) -> None:
    for hazard in known_hazards_from_manifest(_real_manifest(family, platform)):
        lowered = hazard.text.lower()
        assert "_absorb_children" not in lowered, (family, platform)
        assert "fileformat class attributes" not in lowered, (family, platform)
    registry = REGISTRY_DIR / f"{family}__{platform}.yaml"
    if registry.exists():
        ids = {d["id"] for d in yaml.safe_load(registry.read_text(encoding="utf-8"))["defects"]}
        assert not (ids & set(DEFECT_IDS)) or (family, platform) in {("3d", "python"), ("words", "net")}


@pytest.mark.parametrize(("family", "platform"), _all_other_pilots(), ids=lambda v: str(v))
def test_no_other_pilots_limitations_answer_mentions_the_new_hazards(
    tmp_path: Path, family: str, platform: str
) -> None:
    inputs = _load_inputs(tmp_path, family, platform)
    hazards = known_hazards_from_manifest(_real_manifest(family, platform))
    result = get_product_reference(
        dataclasses.replace(inputs, known_hazards=hazards), "limitations", readme=None
    )
    text = result.text if isinstance(result, ReferenceContent) else ""
    assert "_absorb_children" not in text
    assert "48 of the FileFormat class attributes" not in text
    assert "HttpResponse" not in text or (family, platform) == ("words", "net")


def test_the_registry_ids_for_the_two_pilots_keep_their_pre_existing_entries() -> None:
    three_d = [d["id"] for d in _registry_document("3d", "python")["defects"]]
    assert three_d == [
        "3D-PYTHON-001",
        "3D-PYTHON-002",
        "3D-PYTHON-006",
        "3D-PYTHON-008",
        "3D-PYTHON-010",
        "3D-PYTHON-013",
        "3D-PYTHON-009",
    ]
    assert [d["id"] for d in _registry_document("words", "net")["defects"]] == [
        "WORDS-NET-001",
        "WORDS-NET-005",
    ]
