"""G3/TC-530: the re-extracted .NET fixtures (REQ-G3-008, REQ-G3-005). Offline, deterministic, no skips.

The fixtures are regenerated at their chart pins by the merged extractor (README-mention selection, the raised
pdf/net and words/net caps). These tests pin what the regeneration achieved and give the later re-extraction cards of
the chain (TC-531 to TC-535) a proven helper, ``tests/extraction/reextraction_checks.py``.

The member-documentation floors pin the first-sentence text the extractor
keeps today: the extractor does not yet capture ``<param>``, ``<returns>`` or ``<exception>`` text (TC-527 was cut).
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from tests.extraction import docs_identifiers
from tests.extraction import reextraction_checks as rc

PILOTS = ["pdf_net", "words_net", "cells_net", "email_net", "imaging_net", "3d_net", "slides_net"]

# Ratchets measured on the regenerated fixtures: a later extraction may raise them, never lower them.
#
# TC-558 LOWERED six floors, deliberately: the fixtures now serve members that no source comment documents (the
# implicit constructors and ``Deconstruct``/``==``/``!=`` the C# language declares, the record components, the public
# fields such as ``Vector3.X``), so the share of documented members fell although no documented member was lost:
# pdf_net 0.78 -> 0.77 (measured 0.7727), cells_net 0.95 -> 0.93 (0.9394), email_net 0.09 -> 0.07 (0.0756),
# 3d_net 0.79 -> 0.76 (0.7663), slides_net 0.57 -> 0.55 (0.5561), words_net 0.97 -> 0.95 (0.9544, 135 implicit
# constructors). imaging_net did not move.
MEMBER_DOC_RATIO_FLOOR = {
    "pdf_net": 0.77,
    "words_net": 0.95,
    "cells_net": 0.93,
    "email_net": 0.07,
    "imaging_net": 0.0,
    "3d_net": 0.76,
    "slides_net": 0.55,
}
PARAM_DOC_FLOOR = {
    "pdf_net": 0,
    "words_net": 0,
    "cells_net": 0,
    "email_net": 0,
    "imaging_net": 0,
    "3d_net": 0,
    "slides_net": 0,
}
RECALL_FLOOR = {
    "pdf_net": 0.90,
    "words_net": 0.90,
    "cells_net": 0.90,
    "email_net": 0.90,
    "imaging_net": 0.50,
    "3d_net": 0.90,
    "slides_net": 0.38,
}
RECALL_EXCEPTION_REASON = {
    "imaging_net": (
        "its README names two identifiers; the one unresolved is Identify, in `ImageSharp.Image.Identify`: "
        "a method of the third-party SixLabors.ImageSharp package the README tells the reader to use, not a symbol "
        "of this library"
    ),
    "slides_net": (
        "the README's 'Not in the public API' table names members that DO NOT EXIST on purpose (AddChart, "
        "AddSmartArt, AddOleObjectFrame, AddVideoFrame, AddAudioFrame, AddGroupShape, HyperlinkClick, Timeline, "
        "SlideShowTransition, Background, MasterTheme, SlideSize, Protect) and otherwise only System types "
        "(DateTime, FileStream, MemoryStream, NotSupportedException, IO, Drawing), build keywords (DllImport, "
        "LibraryImport, PackageReference, FOSS, Foss) and OOXML part names (oMath, presProps, props, tableStyles, "
        "viewProps): none of them is an API symbol this library defines"
    ),
}
README_TYPES = {
    "pdf_net": [
        "Aspose.Pdf.Facades.PdfFileEditor",
        "Aspose.Pdf.Devices.PngDevice",
        "Aspose.Pdf.Text.TextBuilder",
        "Aspose.Pdf.Facades.PdfFileSignature",
        "Aspose.Pdf.PageCollection",
        "Aspose.Pdf.Text.TableAbsorber",
        "Aspose.Pdf.Document.MergeOptions",
    ],
    "words_net": [
        "Aspose.Words.Replacing.FindReplaceOptions",
        "Aspose.Words.JoinRunsOptions",
        "Aspose.Words.PaperSize",
        "Aspose.Words.Orientation",
    ],
}

_HEX40 = re.compile(r"^[0-9a-f]{40}$")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


# -- generic tests, one case per pilot ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("key", PILOTS)
def test_fixture_is_extracted_at_the_chart_pin(key: str) -> None:
    commit = rc.load_fixture(key)["source_commit"]
    assert commit == rc.pinned_commit(key)
    assert _HEX40.match(commit)


@pytest.mark.parametrize("key", PILOTS)
def test_fixture_carries_the_current_extractor_keys(key: str) -> None:
    fixture = rc.load_fixture(key)
    assert _is_int(fixture.get("extractor_version"))
    assert fixture["extractor_version"] >= rc.REQUIRED_EXTRACTOR_VERSION
    counts = fixture.get("symbol_counts")
    assert isinstance(counts, dict)
    assert _is_int(counts.get("types")) and _is_int(counts.get("functions"))
    failures = fixture.get("parse_failures")
    if failures is not None:
        assert failures["engine"] in {"ast", "tree-sitter"}
        assert _is_int(failures["failed"]) and _is_int(failures["total"])
        assert failures["failed"] <= failures["total"]


@pytest.mark.parametrize("key", PILOTS)
def test_fixture_is_canonical_json(key: str) -> None:
    text = rc.fixture_text(key)
    assert text == json.dumps(json.loads(text), indent=2, sort_keys=True) + chr(10)


@pytest.mark.parametrize("key", PILOTS)
def test_fixture_has_no_test_file_entries(key: str) -> None:
    assert rc.test_file_entries(rc.load_fixture(key)) == []


@pytest.mark.parametrize("key", PILOTS)
def test_cap_is_one_number_in_extractor_chart_and_compose(key: str) -> None:
    assert rc.cap_problems(key) == []


@pytest.mark.parametrize("key", PILOTS)
def test_ingestion_slice_keeps_every_extracted_entry(key: str) -> None:
    assert rc.ingestion_slice_problems(key) == []


@pytest.mark.parametrize("key", PILOTS)
def test_member_docs_reach_the_fixture(key: str) -> None:
    stats = rc.member_doc_stats(rc.load_fixture(key))
    assert stats["members"] > 0
    assert stats["members_with_doc"] / stats["members"] >= MEMBER_DOC_RATIO_FLOOR[key], stats
    assert stats["params_with_doc"] >= PARAM_DOC_FLOOR[key], stats


@pytest.mark.parametrize("key", PILOTS)
def test_readme_recall_has_a_floor(key: str) -> None:
    recall = rc.readme_recall(key)
    assert recall >= RECALL_FLOOR[key], (recall, sorted(docs_identifiers.unresolved(key)))
    if RECALL_FLOOR[key] < 0.90:
        assert RECALL_EXCEPTION_REASON.get(key, "").strip(), f"{key} needs a stated exception"


def test_recall_exceptions_are_real() -> None:
    for key, reason in RECALL_EXCEPTION_REASON.items():
        assert isinstance(reason, str) and reason.strip(), key
        assert key in PILOTS, key
        assert rc.readme_recall(key) < 0.90, f"{key} reaches 0.90 now; delete its exception"


# -- the dotnet-specific facts -----------------------------------------------------------------------------------------------


def _complete(key: str, total: int) -> None:
    fixture = rc.load_fixture(key)
    assert fixture["type_count"] == total
    assert fixture["reduced_type_count"] == total
    assert len(fixture["types"]) == total
    assert fixture["truncated"] is False


def test_pdf_net_is_the_complete_surface() -> None:
    # TC-558: 901 -> 915 (the 14 delegate types of pdf/net are types of the surface now; RD3-054)
    _complete("pdf_net", 915)


def test_words_net_is_the_complete_surface() -> None:
    # TC-558: 618 -> 615 (INodeCollection, DocumentPositionMovement and NodeEnumerator are internal in the packaged library; RD3-055)
    _complete("words_net", 615)


def test_pdf_net_indexes_the_types_the_readme_names() -> None:
    assert set(README_TYPES["pdf_net"]) <= rc.fqns(rc.load_fixture("pdf_net"))


def test_words_net_indexes_the_types_the_readme_names() -> None:
    assert set(README_TYPES["words_net"]) <= rc.fqns(rc.load_fixture("words_net"))


def test_pdf_net_and_words_net_readme_recall_is_at_least_90_percent() -> None:
    for key in ("pdf_net", "words_net"):
        assert rc.readme_recall(key) >= 0.90, key
        assert key not in RECALL_EXCEPTION_REASON, key


def test_static_methods_are_marked_in_pdf_net() -> None:
    fixture = rc.load_fixture("pdf_net")
    methods = [m for entry in fixture["types"] for m in entry.get("methods") or []]
    assert any("static" in (m.get("modifiers") or []) for m in methods)


def test_imaging_net_keeps_its_three_types() -> None:
    fixture = rc.load_fixture("imaging_net")
    assert fixture["type_count"] == 3
    assert fixture["symbol_counts"]["types"] == 3


def test_no_dotnet_fixture_is_truncated_except_by_design() -> None:
    assert [key for key in PILOTS if rc.load_fixture(key)["truncated"]] == []


# -- helper tests: synthetic fixtures ---------------------------------------------------------------------------------------


def _fixture() -> dict[str, Any]:
    return {
        "types": [
            {
                "name": "Alpha",
                "class_import": "Ns.Sub.Alpha",
                "doc": "Alpha thing.",
                "methods": [
                    {
                        "name": "Go",
                        "doc": "Goes.",
                        "params": [{"name": "a", "doc": "the a"}, {"name": "b", "doc": ""}],
                        "return_doc": "a value",
                        "throws_doc": "ArgumentException: when bad",
                    },
                    {"name": "Stop", "doc": "", "params": []},
                ],
                "properties": [{"name": "Size", "doc": "The size."}],
            },
            {"name": "Beta", "class_import": "ns::beta::Beta", "methods": [], "properties": []},
            {"name": "Gamma", "class_import": "pkg/gamma/Gamma"},
        ]
    }


def test_member_doc_stats_counts_documented_members_and_params() -> None:
    stats = rc.member_doc_stats(_fixture())
    assert stats["members"] == 3
    assert stats["members_with_doc"] == 2
    assert stats["params"] == 2
    assert stats["params_with_doc"] == 1


def test_member_doc_stats_counts_return_and_throws_docs() -> None:
    stats = rc.member_doc_stats(_fixture())
    assert stats["methods_with_return_doc"] == 1
    assert stats["methods_with_throws_doc"] == 1
    assert rc.member_doc_stats({"types": []})["members"] == 0


def test_members_yields_methods_and_properties_with_their_entry() -> None:
    pairs = [(entry["name"], member["name"]) for entry, member in rc.members(_fixture())]
    assert pairs == [("Alpha", "Go"), ("Alpha", "Stop"), ("Alpha", "Size")]


def test_abbreviation_cut_docs_flags_eg_and_ie() -> None:
    fixture = {
        "types": [
            {
                "name": "T",
                "doc": "Takes a mode, e.g.",
                "methods": [
                    {
                        "name": "M",
                        "doc": "Does it, i.e",
                        "params": [{"name": "p", "doc": " the value (e.g. "}],
                    },
                    {"name": "N", "doc": "x", "return_doc": "Returns it, i.e.", "throws_doc": "Raises, e.g"},
                ],
            }
        ]
    }
    cut = rc.abbreviation_cut_docs(fixture)
    assert "Takes a mode, e.g." in cut
    assert "Does it, i.e" in cut
    assert "Returns it, i.e." in cut
    assert "Raises, e.g" in cut
    assert " the value (e.g. " in cut  # stripped, the parameter text ends in e.g.
    assert len(cut) == 5


def test_abbreviation_cut_docs_ignores_a_complete_sentence() -> None:
    fixture = {
        "types": [
            {
                "name": "T",
                "doc": "Returns the page, e.g. the first one.",
                "methods": [
                    {"name": "M", "doc": "A message.", "params": [{"name": "p", "doc": "College age."}]}
                ],
            }
        ]
    }
    assert rc.abbreviation_cut_docs(fixture) == []


def test_type_names_uses_the_last_segment_of_class_import() -> None:
    assert rc.type_names(_fixture()) == {"Alpha", "Beta", "Gamma"}
    assert rc.type_names({"types": [{"name": "A", "class_import": "x.y::Z/W"}]}) == {"A", "W"}


def test_fqns_lists_every_non_empty_class_import() -> None:
    fixture = _fixture()
    fixture["types"].append({"name": "NoImport", "class_import": ""})
    assert rc.fqns(fixture) == {"Ns.Sub.Alpha", "ns::beta::Beta", "pkg/gamma/Gamma"}


def test_test_file_entries_names_entries_read_from_test_sources() -> None:
    fixture = {
        "types": [
            {"name": "Real", "class_import": "A.Real", "file": "src/Real.cs"},
            {"name": "Fake", "class_import": "A.Fake", "file": "tests/FakeTests.cs"},
        ]
    }
    assert rc.test_file_entries(fixture) == ["A.Fake"]


def test_cap_problems_flags_a_chart_compose_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rc, "chart_max_types", lambda key: 901)
    monkeypatch.setattr(rc, "compose_max_types", lambda key: 300)
    problems = rc.cap_problems("pdf_net")
    assert any("chart maxTypes 901" in p and "compose --max-types 300" in p for p in problems)


def test_cap_problems_flags_a_truncated_flag_that_disagrees_with_the_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = dict(rc.load_fixture("cells_net"))
    lying = {**real, "truncated": True}
    monkeypatch.setattr(rc, "load_fixture", lambda key: lying)
    problems = rc.cap_problems("cells_net")
    assert any("disagrees" in p for p in problems)
    assert any("fits the cap" in p for p in problems)
    short = {**real, "reduced_type_count": 5}
    monkeypatch.setattr(rc, "load_fixture", lambda key: short)
    assert any("reduced_type_count 5 != len(types)" in p for p in rc.cap_problems("cells_net"))


def test_readme_recall_is_one_when_nothing_is_unresolved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rc, "docs_identifiers", lambda key: {"A", "B"})
    monkeypatch.setattr(rc, "unresolved", lambda key: set())
    assert rc.readme_recall("pdf_net") == 1.0
    monkeypatch.setattr(rc, "docs_identifiers", lambda key: set())
    assert rc.readme_recall("pdf_net") == 1.0


def test_readme_recall_is_the_resolved_share(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rc, "docs_identifiers", lambda key: {"A", "B", "C", "D"})
    monkeypatch.setattr(rc, "unresolved", lambda key: {"D"})
    assert rc.readme_recall("pdf_net") == 0.75


def test_chart_pin_and_compose_cap_are_read_from_the_committed_tree() -> None:
    assert _HEX40.match(rc.pinned_commit("pdf_net"))
    assert rc.chart_pilot("pdf_net")["family"] == "pdf"
    assert rc.compose_max_types("words_net") == rc.chart_max_types("words_net") == 615  # TC-558: 618 -> 615
    assert rc.pilot_key("pdf", "net") == "pdf_net"
    assert rc.recommended_cap("cells_net") == 300
    assert rc.recommended_cap("pdf_net") == 915  # TC-558: 901 -> 915


def test_ingestion_slice_problems_flags_a_slice_that_drops_an_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rc, "chart_max_types", lambda key: 2)
    problems = rc.ingestion_slice_problems("cells_net")
    assert any("max_types=2" in p for p in problems)
