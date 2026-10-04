"""G2/TC-197: offline tests for scripts/poc/pilot_workflow_proof.py.

A fake client returns scripted answers, so no network is needed. The tests drive the same
eleven checks the live run does, against small fixtures written under tmp_path.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "poc" / "pilot_workflow_proof.py"


def _load_script() -> Any:
    spec = importlib.util.spec_from_file_location("pilot_workflow_proof", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


pwp = _load_script()

PILOT = "cells_rust"
SHA = "1a6004af47b1ef15385f9d36d381a8172428cc7e"
GEN = "cells::rust::self_extracted::gen-1"
SCOPE = {"family": "cells", "platform": "rust", "source_kind": "self_extracted"}
TITLE = "Aspose.Cells FOSS for Rust"
SYMBOL = "AutoShapeType"
OTHER_SYMBOL = "PdfDocumentSignatureField"
ARGUMENT_NAMES = {
    "lookup": ("query", "content_type", "top_k"),
    "search_docs": ("query", "content_type", "top_k"),
    "search_symbols": ("query", "top_k"),
    "get_symbol": ("fqn",),
    "list_members": ("fqn",),
    "find_examples": ("query", "top_k"),
    "get_product_reference": ("section",),
    "list_recent_changes": ("limit",),
    "report_index_freshness": ("source_kind", "current_source_commit"),
}


def _write_fixtures(root: Path, *, furnished: bool = True) -> None:
    own_types = [
        {
            "name": SYMBOL,
            "kind": "enum_item",
            "class_import": SYMBOL,
            "enum_members": [{"name": "Rectangle"}, {"name": "Ellipse"}],
            "methods": [],
            "properties": [],
        },
        {"name": "Workbook", "kind": "struct_item", "class_import": "Workbook", "methods": [{"name": "new"}]},
    ]
    other_types = [{"name": OTHER_SYMBOL, "kind": "struct_item", "class_import": OTHER_SYMBOL, "methods": []}]
    for pilot, types in ((PILOT, own_types), ("pdf_net", other_types)):
        (root / pilot).mkdir(parents=True)
        (root / pilot / "api_surface.json").write_text(
            json.dumps({"source_commit": SHA, "types": types}), encoding="utf-8"
        )
    if furnished:
        page = root / "furnished" / PILOT / "pages" / "_index.md"
        page.parent.mkdir(parents=True)
        page.write_text(f"---\ntitle: {TITLE}\n---\nBody text.\n", encoding="utf-8")


class FakeClient:
    """Scripted answers. A callable answer is called with the arguments, as the live tool would be."""

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.calls: list[str] = []

    def handshake(self) -> dict[str, Any]:
        return {"protocolVersion": "2025-06-18", "serverInfo": {"name": "foss-mcp", "version": "0.0.0"}}

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": name,
                "inputSchema": {
                    "type": "object",
                    "properties": {argument: {"type": "string"} for argument in arguments},
                    "required": [],
                },
            }
            for name, arguments in ARGUMENT_NAMES.items()
        ]

    def invoke(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append(name)
        answer = self.answers[name]
        return answer(arguments) if callable(answer) else answer


def _hit(symbol: str, *, scope: dict[str, str] = SCOPE) -> list[dict[str, Any]]:
    text = f"FQN: {symbol}\nKind: enum_item\nMembers:\n  - Rectangle\n  - Ellipse\n"
    return [{"scope": scope, "generation_id": GEN, "doc_id": "doc-1", "chunk_id": "chunk-1", "text": text}]


def _miss(query: str) -> dict[str, Any]:
    return {"scope": SCOPE, "query": query, "reason": f"no symbol matches {query!r}", "suggestions": []}


def passing_answers(*, search_scope: dict[str, str] = SCOPE) -> dict[str, Any]:
    def search_symbols(arguments: dict[str, Any]) -> Any:
        if arguments["query"] == SYMBOL:
            return _hit(SYMBOL, scope=search_scope)
        return _miss(arguments["query"])

    def lookup(arguments: dict[str, Any]) -> Any:
        return _hit(SYMBOL) if arguments["query"] == SYMBOL else _miss(arguments["query"])

    def search_docs(arguments: dict[str, Any]) -> Any:
        if arguments["content_type"] == "getting_started":
            text = f"FQN: Doc: Installation\n{TITLE} is an open-source Rust crate for Excel workbooks."
            return [
                {
                    "scope": SCOPE,
                    "generation_id": GEN,
                    "doc_id": "doc-9",
                    "chunk_id": "chunk-9",
                    "content_type": "getting_started",
                    "text": text,
                }
            ]
        return {
            "scope": SCOPE,
            "query": arguments["query"],
            "content_type": arguments["content_type"],
            "reason": "none",
        }

    def get_symbol(arguments: dict[str, Any]) -> Any:
        return {
            "scope": SCOPE,
            "generation_id": GEN,
            "doc_id": "doc-1",
            "chunk_id": "chunk-1",
            "fqn": arguments["fqn"],
            "kind": "enum_item",
            "bases": [],
            "methods": [],
            "properties": [],
            "members": ["Rectangle", "Ellipse"],
            "raw_text": "",
        }

    def report_index_freshness(arguments: dict[str, Any]) -> Any:
        return {
            "scope": SCOPE,
            "source_kind": "self_extracted",
            "indexed_generation_id": GEN,
            "indexed_source_commit": SHA,
            "current_source_commit": arguments["current_source_commit"],
            "stale": False,
            "reason": "indexed generation matches the current source commit",
        }

    return {
        "search_symbols": search_symbols,
        "lookup": lookup,
        "search_docs": search_docs,
        "get_symbol": get_symbol,
        "list_members": lambda arguments: ["Rectangle", "Ellipse"],
        "find_examples": lambda arguments: {"scope": SCOPE, "query": arguments["query"]},
        "get_product_reference": lambda arguments: {"section": arguments["section"], "text": "cargo add x"},
        "report_index_freshness": report_index_freshness,
        "list_recent_changes": [],
    }


def _run_checks(root: Path, answers: dict[str, Any]) -> list[dict[str, Any]]:
    return pwp.run_checks(FakeClient(answers), pwp.load_pilot(PILOT, root))


def _check(checks: list[dict[str, Any]], number: int) -> dict[str, Any]:
    return next(check for check in checks if check["id"] == number)


def test_exact_match_rule_accepts_the_final_segment_and_rejects_a_fuzzy_match(tmp_path: Path) -> None:
    assert pwp.is_exact_hit("Aspose.Pdf.Document", "Document")
    assert pwp.is_exact_hit("Document", "Document")
    assert not pwp.is_exact_hit("Aspose.Pdf.DocumentBuilder", "Document")
    assert not pwp.is_exact_hit("Aspose.Pdf.Document.Save", "Document")
    assert not pwp.is_exact_hit("", "Document")

    # The same rule decides check 2: a fuzzy-only result is not an exact match.
    _write_fixtures(tmp_path)
    answers = passing_answers()
    fuzzy_text = f"FQN: {SYMBOL}Extended\nKind: enum_item\n"
    answers["search_symbols"] = lambda arguments: [
        {"scope": SCOPE, "generation_id": GEN, "doc_id": "d", "chunk_id": "c", "text": fuzzy_text}
    ]
    check2 = _check(_run_checks(tmp_path, answers), 2)
    assert check2["status"] == "fail"
    assert "final FQN segment" in check2["reason"]


def test_a_scope_mismatch_in_a_result_fails_check_2(tmp_path: Path) -> None:
    _write_fixtures(tmp_path)
    check2 = _check(_run_checks(tmp_path, passing_answers(search_scope={**SCOPE, "family": "slides"})), 2)
    assert check2["status"] == "fail"
    assert "another scope" in check2["reason"]


def test_an_absent_symbol_that_returns_a_match_fails_check_10(tmp_path: Path) -> None:
    _write_fixtures(tmp_path)
    answers = passing_answers()
    answers["search_symbols"] = lambda arguments: _hit(SYMBOL)  # every query "matches" the real symbol
    checks = _run_checks(tmp_path, answers)
    assert _check(checks, 10)["status"] == "fail"
    assert _check(checks, 2)["status"] == "pass"


def test_a_pilot_with_no_furnished_page_is_not_applicable_for_check_5(tmp_path: Path) -> None:
    _write_fixtures(tmp_path, furnished=False)
    fake = FakeClient(passing_answers())
    checks = pwp.run_checks(fake, pwp.load_pilot(PILOT, tmp_path))
    check5 = _check(checks, 5)
    assert check5["status"] == "not_applicable"
    assert "search_docs" not in fake.calls


def test_a_one_letter_name_is_not_chosen_and_the_next_valid_name_is() -> None:
    # "A" is an enum with members, the most preferred kind, so only the length rule keeps it out.
    types = [
        {"name": "A", "kind": "enum_item", "enum_members": [{"name": "X"}]},
        {"name": "Type", "kind": "struct_item", "methods": [{"name": "new"}]},
        {"name": "Workbook", "kind": "struct_item", "methods": [{"name": "new"}]},
    ]
    assert pwp.choose_probe(types, PILOT) == "Workbook"


def test_an_enum_is_preferred_over_a_plain_type() -> None:
    types = [
        {"name": "Workbook", "kind": "struct_item", "methods": []},
        {"name": "Canvas", "kind": "struct_item", "methods": [{"name": "draw"}]},
        {"name": SYMBOL, "kind": "enum_item", "enum_members": [{"name": "Rectangle"}]},
    ]
    assert pwp.choose_probe(types, PILOT) == SYMBOL


def test_a_fixture_with_no_valid_name_raises_value_error() -> None:
    types = [
        {"name": "A", "kind": "enum_item", "enum_members": [{"name": "X"}]},
        {"name": "Name", "kind": "struct_item", "methods": [{"name": "new"}]},
        {"name": "Value"},
        {"name": "9Lives", "kind": "struct_item"},
        {"name": "x-y-z", "kind": "struct_item"},
    ]
    with pytest.raises(ValueError, match=PILOT):
        pwp.choose_probe(types, PILOT)


def test_the_report_is_written_and_exits_zero_only_when_every_applicable_check_passes(tmp_path: Path) -> None:
    _write_fixtures(tmp_path)
    base = ["--pilot", PILOT, "--url", "http://fake.invalid", "--fixtures", str(tmp_path)]

    passing_report = tmp_path / "out" / "passing.json"
    exit_code = pwp.main(
        [*base, "--report", str(passing_report)],
        client_factory=lambda url: FakeClient(passing_answers()),
    )
    report = json.loads(passing_report.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert report["all_applicable_passed"] is True
    assert [check["id"] for check in report["checks"]] == list(range(1, 12))
    assert report["summary"]["fail"] == 0

    failing_report = tmp_path / "out" / "failing.json"
    exit_code = pwp.main(
        [*base, "--report", str(failing_report)],
        client_factory=lambda url: FakeClient(passing_answers(search_scope={**SCOPE, "platform": "python"})),
    )
    report = json.loads(failing_report.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert report["all_applicable_passed"] is False
    assert _check(report["checks"], 2)["status"] == "fail"


UNAVAILABLE_INSTALL = {"section": "install", "reason": "cpp has no package-manager install command"}
UNAVAILABLE_OTHER = {"section": "other", "reason": "not available"}
STANDARD_TEXT = "Requires a C++17 compiler and CMake 3.20."


def _reference_answers(by_section: dict[str, Any]) -> dict[str, Any]:
    answers = passing_answers()
    answers["get_product_reference"] = lambda arguments: by_section.get(
        arguments["section"], UNAVAILABLE_OTHER
    )
    return answers


def _check_7_for(root: Path, platform: str, by_section: dict[str, Any]) -> tuple[dict[str, Any], FakeClient]:
    pilot = dataclasses.replace(pwp.load_pilot(PILOT, root), platform=platform)
    client = FakeClient(_reference_answers(by_section))
    return _check(pwp.run_checks(client, pilot), 7), client


def test_a_cpp_pilot_with_no_install_command_passes_check_7_on_its_compatibility_text(tmp_path: Path) -> None:
    _write_fixtures(tmp_path)
    check7, _ = _check_7_for(
        tmp_path,
        "cpp",
        {
            "install": UNAVAILABLE_INSTALL,
            "compatibility": {"section": "compatibility", "text": STANDARD_TEXT},
        },
    )
    assert check7["status"] == "pass"
    assert check7["evidence"]["sections_asked"] == ["install", "compatibility"]
    assert check7["evidence"]["result_kinds"] == [
        "object with keys ['reason', 'section']",
        "object with keys ['section', 'text']",
    ]
    assert check7["evidence"]["text"] == STANDARD_TEXT


def test_a_cpp_pilot_with_neither_section_fails_check_7(tmp_path: Path) -> None:
    _write_fixtures(tmp_path)
    check7, _ = _check_7_for(
        tmp_path,
        "cpp",
        {
            "install": UNAVAILABLE_INSTALL,
            "compatibility": {"section": "compatibility", "text": "   ", "reason": "no standard found"},
        },
    )
    assert check7["status"] == "fail"
    assert check7["evidence"]["sections_asked"] == ["install", "compatibility"]


def test_a_non_cpp_pilot_gets_no_compatibility_fallback_in_check_7(tmp_path: Path) -> None:
    _write_fixtures(tmp_path)
    check7, client = _check_7_for(
        tmp_path,
        "rust",
        {
            "install": UNAVAILABLE_INSTALL,
            "compatibility": {"section": "compatibility", "text": STANDARD_TEXT},
        },
    )
    assert check7["status"] == "fail"
    assert check7["evidence"]["sections_asked"] == ["install"]
    assert client.calls.count("get_product_reference") == 1


def test_a_scope_separated_fqn_is_an_exact_hit_for_its_short_name() -> None:
    assert pwp.is_exact_hit("Aspose::Pdf::AFRelationship", "AFRelationship")
    assert pwp.is_exact_hit("jmap::JmapError", "JmapError")
    assert pwp.is_exact_hit("Aspose.Pdf.AFRelationship", "AFRelationship")


def test_a_scope_separated_fqn_is_not_a_hit_for_a_near_miss_or_a_scope_segment() -> None:
    assert not pwp.is_exact_hit("Aspose::Pdf::AFRelationshipX", "AFRelationship")
    assert not pwp.is_exact_hit("Aspose::Pdf::AFRelationship", "Pdf")


def _write_chart_values(path: Path, entries: list[dict[str, Any]]) -> Path:
    path.write_text(yaml.safe_dump({"ingestion": {"pilots": entries}}), encoding="utf-8")
    return path


def test_load_pilot_keeps_the_furnished_page_when_the_chart_sets_furnished_page(tmp_path: Path) -> None:
    _write_fixtures(tmp_path)
    chart = _write_chart_values(
        tmp_path / "values.yaml",
        [
            {
                "family": "cells",
                "platform": "rust",
                "furnishedPage": "/app/fixtures/furnished/cells_rust/pages/_index.md",
            }
        ],
    )
    pilot = pwp.load_pilot(PILOT, tmp_path, chart_values=chart)
    assert pilot.furnished_page == tmp_path / "furnished" / PILOT / "pages" / "_index.md"
    assert pilot.furnished_title == TITLE


def test_load_pilot_drops_the_furnished_page_when_the_chart_entry_omits_it(tmp_path: Path) -> None:
    _write_fixtures(tmp_path)
    assert (tmp_path / "furnished" / PILOT / "pages" / "_index.md").is_file()
    chart = _write_chart_values(tmp_path / "values.yaml", [{"family": "cells", "platform": "rust"}])
    pilot = pwp.load_pilot(PILOT, tmp_path, chart_values=chart)
    assert pilot.furnished_page is None
    assert pilot.furnished_title is None
    check5 = _check(pwp.run_checks(FakeClient(passing_answers()), pilot), 5)
    assert check5["status"] == "not_applicable"
    assert "chart ingests no furnished page" in check5["reason"]


def test_load_pilot_drops_the_furnished_page_when_the_chart_has_no_matching_entry(tmp_path: Path) -> None:
    _write_fixtures(tmp_path)
    chart = _write_chart_values(
        tmp_path / "values.yaml",
        [{"family": "pdf", "platform": "net", "furnishedPage": "/app/x.md"}],
    )
    assert pwp.load_pilot(PILOT, tmp_path, chart_values=chart).furnished_page is None


def test_load_pilot_without_chart_values_keeps_a_fixture_page(tmp_path: Path) -> None:
    _write_fixtures(tmp_path)
    assert pwp.load_pilot(PILOT, tmp_path).furnished_page is not None
