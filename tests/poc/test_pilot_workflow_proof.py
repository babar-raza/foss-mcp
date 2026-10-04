"""G2/TC-197: offline tests for scripts/poc/pilot_workflow_proof.py.

A fake client returns scripted answers, so no network is needed. The tests drive the same
eleven checks the live run does, against small fixtures written under tmp_path.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

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
