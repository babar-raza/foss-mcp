"""G3/TC-831 (REQ-G3-017): scripts/release/answer_diff.py, the seeded offline served-answer comparator.

Two tiny synthetic manifest trees prove each class of verdict (identical, changed, lost, new), the exit codes, the
``--allow-lost`` accounting and the determinism of the query set. A liveness test proves ``main()`` really calls the
diff function. One run over the real committed ``infra/demo-manifests`` tree against itself proves a zero diff on
the data that ships (and that the comparator answers, i.e. is not vacuously comparing two walls of misses).
Nothing here uses the network, an embedding gateway, docker or the clock.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from foss_mcp.indexing.generation_manifest import GenerationManifestStore, build_manifest
from foss_mcp.indexing.lexical_index_writer import build_lexical_index
from foss_mcp.indexing.publisher import content_chunk_id

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE_DIR = REPO_ROOT / "scripts" / "release"
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"

if str(RELEASE_DIR) not in sys.path:
    sys.path.insert(0, str(RELEASE_DIR))

import answer_diff as ad  # noqa: E402

PILOT = "demo__net"
VERSION = "20260101T000000000000Z-aaaaaaaa"

WIDGET = (
    "FQN: Demo.Widget\nKind: class_declaration\n\nMethods:\n  - Render() -> void\n  - Resize(width: int) -> void\n"
    "\nAPI-Coverage: 3 of 3 types\nSource-Commit: 1111111"
)
WIDGET_CHANGED = (
    "FQN: Demo.Widget\nKind: class_declaration\n\nMethods:\n  - Render(scale: int) -> void\n  - Resize(width: int)"
    " -> void\n\nAPI-Coverage: 3 of 3 types\nSource-Commit: 2222222"
)
GADGET = "FQN: Demo.Gadget\nKind: class_declaration\n\nMethods:\n  - Spin() -> void\n\nSource-Commit: 1111111"
GIZMO = "FQN: Demo.Gizmo\nKind: class_declaration\n\nMethods:\n  - Twist() -> void\n\nSource-Commit: 2222222"
INSTALL_DOC = (
    "FQN: Doc: Installation and Quick Start\nInstall the package with dotnet add package Demo.Foss, then add a using"
    " directive. This is the quick start for installing the Demo library."
)
EXAMPLE = (
    "FQN: Example: Render a demo widget\nKind: verified_example\nProvenance: authored\nVerification: passed\n"
    "Renders one widget.\n\nExample:\nvar widget = new Widget();\nwidget.Render();\nSource-Commit: 1111111"
)

OLD_TEXTS = [WIDGET, GADGET, INSTALL_DOC, EXAMPLE]


def write_tree(root: Path, pilots: dict[str, list[str]]) -> Path:
    """Write a manifest tree of synthetic pilots (``family__platform`` -> chunk texts) through the real store."""
    root.mkdir(parents=True, exist_ok=True)
    store = GenerationManifestStore(root)
    for name, texts in pilots.items():
        family, platform = name.split("__")
        chunks = [SimpleNamespace(section_title="", text=text) for text in texts]
        generation_id = build_manifest(family, platform, "self_extracted", VERSION).generation_id
        payload = {
            "lexical_index": build_lexical_index(chunks, [content_chunk_id(c) for c in chunks], generation_id)
        }
        scope = f"{family}::{platform}::self_extracted"
        lease = store.acquire_lease(scope, "test", "pending")
        store.write_and_validate(build_manifest(family, platform, "self_extracted", VERSION, payload))
        store.cas_activate(scope, None, generation_id, lease)
        (root / f"{name}__self_extracted").mkdir(exist_ok=True)
    return root


@pytest.fixture(autouse=True)
def _no_embedding_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ad._ENDPOINT_ENV, raising=False)
    monkeypatch.delenv(ad._API_KEY_ENV, raising=False)


def run_main(tmp_path: Path, old: list[str], new: list[str], *extra: str) -> tuple[int, dict[str, Any]]:
    """``main()`` over two synthetic trees; returns (exit code, the JSON report it wrote)."""
    old_dir = write_tree(tmp_path / "old-tree", {PILOT: old})
    new_dir = write_tree(tmp_path / "new-tree", {PILOT: new})
    out = tmp_path / "report.json"
    code = ad.main(
        [
            "--old-dir",
            str(old_dir),
            "--new-dir",
            str(new_dir),
            "--queries-per-pilot",
            "10",
            "--json-out",
            str(out),
            *extra,
        ]
    )
    return code, json.loads(out.read_text(encoding="utf-8"))


def tool_row(report: dict[str, Any], tool: str) -> dict[str, Any]:
    return report["pilots"][PILOT]["tools"][tool]


def test_identical_trees_diff_to_zero_and_the_synthetic_pilot_really_answers(tmp_path: Path) -> None:
    code, report = run_main(tmp_path, OLD_TEXTS, OLD_TEXTS)
    assert code == 0
    assert report["totals"]["changed"] == report["totals"]["lost"] == report["totals"]["new"] == 0
    assert report["totals"]["identical"] > 10
    # not a vacuous comparison of two walls of misses: symbols, an example and a doc question all answer
    assert tool_row(report, "get_symbol")["old_answered"] >= 2
    assert tool_row(report, "list_members")["old_answered"] >= 2
    assert tool_row(report, "find_examples")["old_answered"] >= 1
    assert report["old_answered"] == report["new_answered"] > 0


def test_a_removed_symbol_is_a_lost_answer_and_fails_the_run(tmp_path: Path) -> None:
    code, report = run_main(tmp_path, OLD_TEXTS, [WIDGET, INSTALL_DOC, EXAMPLE])
    assert code == 1
    assert report["failed"] is True
    assert tool_row(report, "get_symbol")["lost"] == 1
    assert tool_row(report, "list_members")["lost"] == 1
    lost_ids = {entry["id"] for entry in report["lost"]}
    assert f"{PILOT}::get_symbol::Demo.Gadget" in lost_ids
    diff = tool_row(report, "get_symbol")["diffs"][0]
    assert diff["class"] == "lost"
    assert "Demo.Gadget" in diff["diff"] or "Gadget" in diff["diff"]


def test_allow_lost_accepts_a_pilot_or_an_exact_id_and_records_the_reason(tmp_path: Path) -> None:
    code, report = run_main(
        tmp_path / "pilot",
        OLD_TEXTS,
        [WIDGET, INSTALL_DOC, EXAMPLE],
        "--allow-lost",
        f"{PILOT}=Gadget removed on purpose",
    )
    assert code == 0
    assert report["failed"] is False
    assert report["lost"] and all(entry["allowed"] == "Gadget removed on purpose" for entry in report["lost"])
    assert report["unused_allowances"] == {}

    exact = [
        f"{PILOT}::{tool}::Demo.Gadget=gone"
        for tool in ("get_symbol", "list_members", "search_symbols_exact")
    ]
    args: list[str] = []
    for entry in (
        *exact,
        f"{PILOT}::search_symbols_prefix::Gad=gone",
        f"{PILOT}::find_examples::unused=never hit",
    ):
        args += ["--allow-lost", entry]
    code, report = run_main(tmp_path / "exact", OLD_TEXTS, [WIDGET, INSTALL_DOC, EXAMPLE], *args)
    assert code == 0, report["lost"]
    assert list(report["unused_allowances"]) == [f"{PILOT}::find_examples::unused"]


def test_a_changed_answer_is_reported_with_its_diff_but_is_not_a_failure(tmp_path: Path) -> None:
    code, report = run_main(tmp_path, OLD_TEXTS, [WIDGET_CHANGED, GADGET, INSTALL_DOC, EXAMPLE])
    assert code == 0
    assert report["totals"]["lost"] == 0
    row = tool_row(report, "get_symbol")
    assert row["changed"] == 1
    assert row["identical"] >= 1
    diff = row["diffs"][0]
    assert diff["class"] == "changed"
    assert "Render(scale: int)" in diff["diff"]
    assert diff["diff"].startswith("--- old")


def test_a_new_symbol_is_a_new_answer_and_not_a_failure(tmp_path: Path) -> None:
    code, report = run_main(tmp_path, OLD_TEXTS, [*OLD_TEXTS, GIZMO])
    assert code == 0
    assert tool_row(report, "get_symbol")["new"] == 1
    assert tool_row(report, "list_members")["new"] == 1
    assert report["totals"]["lost"] == 0


def test_classify_treats_a_lower_match_quality_as_lost_and_a_higher_one_as_changed() -> None:
    confident, loose, miss = ad.Outcome(2, "a"), ad.Outcome(1, "a"), ad.Outcome(0, "miss")
    assert ad.classify(confident, miss) == "lost"
    assert ad.classify(confident, loose) == "lost"
    assert ad.classify(loose, miss) == "lost"
    assert ad.classify(loose, confident) == "changed"
    assert ad.classify(confident, ad.Outcome(2, "b")) == "changed"
    assert ad.classify(confident, ad.Outcome(2, "a")) == "identical"
    assert ad.classify(miss, ad.Outcome(0, "another miss reason")) == "identical"
    assert ad.classify(miss, loose) == "new"


def test_rank_reads_match_quality_then_confidence_and_treats_misses_as_zero() -> None:
    assert ad._rank_of([SimpleNamespace(match_quality="partial")]) == 1
    assert ad._rank_of([SimpleNamespace(match_quality="exact")]) == 2
    assert ad._rank_of([SimpleNamespace(confidence="loose")]) == 1
    assert ad._rank_of([SimpleNamespace(confidence="confident")]) == 2
    assert ad._rank_of(SimpleNamespace(match_quality="partial")) == 1
    assert ad._rank_of(("Render() -> void",)) == 2
    assert ad._rank_of([]) == 0
    for name in ("Miss", "NoExampleFound", "NotFound"):
        assert ad._rank_of(type(name, (), {})()) == 0


def test_the_query_set_is_deterministic_for_a_seed_and_covers_every_tool(tmp_path: Path) -> None:
    many = [
        f"FQN: Demo.Type{i:02d}\nKind: class_declaration\n\nMethods:\n  - Run{i}() -> void" for i in range(20)
    ]
    tree = ad.load_tree(write_tree(tmp_path / "t", {PILOT: [*many, INSTALL_DOC, EXAMPLE]}), "t")
    pilot = tree.pilots[PILOT]
    first = ad.build_queries(PILOT, pilot, pilot, seed=7, n=3)
    assert first == ad.build_queries(PILOT, pilot, pilot, seed=7, n=3)
    other = ad.build_queries(PILOT, pilot, pilot, seed=8, n=3)
    assert {q for q in first if q.tool == "get_symbol"} != {q for q in other if q.tool == "get_symbol"}
    assert {q.tool for q in first} == set(ad.TOOL_ORDER)
    docs = {q.argument for q in first if q.tool == "search_docs"}
    assert docs == {question for _label, question in ad.CANONICAL_QUESTIONS}
    assert len(ad.CANONICAL_QUESTIONS) == 6
    assert any(q.tool == "find_examples" and q.argument == "Render a demo" for q in first)
    bounded = ad.build_queries(PILOT, pilot, pilot, seed=7, n=1)
    assert len([q for q in bounded if q.tool == "get_symbol"]) <= 2  # at most one per side


def test_a_pilot_missing_from_one_side_loses_every_answer(tmp_path: Path) -> None:
    old = ad.load_tree(write_tree(tmp_path / "old", {PILOT: OLD_TEXTS, "other__net": OLD_TEXTS}), "old")
    new = ad.load_tree(write_tree(tmp_path / "new", {PILOT: OLD_TEXTS}), "new")
    report = ad.run_answer_diff(old, new, queries_per_pilot=5)
    assert report["pilots"]["other__net"]["status"] == "removed"
    assert report["pilots"][PILOT]["status"] == "both"
    assert report["failed"] is True
    assert {entry["pilot"] for entry in report["lost"]} == {"other__net"}
    with pytest.raises(ad.AnswerDiffError):
        ad.run_answer_diff(old, new, pilots=["nope__net"])


def test_unusable_inputs_exit_2_and_a_diff_is_bounded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    good = write_tree(tmp_path / "good", {PILOT: OLD_TEXTS})
    assert ad.main(["--old-dir", str(tmp_path / "missing"), "--new-dir", str(good)]) == 2
    assert ad.main(["--old-dir", str(good), "--new-dir", str(good), "--pilots", "nope__net"]) == 2
    assert ad.main(["--old-dir", str(good), "--new-dir", str(good), "--allow-lost", "demo__net="]) == 2
    assert ad.main(["--old-rev", "no-such-rev-831", "--new-dir", str(good)]) == 2
    assert "answer_diff:" in capsys.readouterr().err
    huge = ad._bounded_diff(ad.Outcome(2, "x\n" * 5000), ad.Outcome(2, "y\n" * 5000))
    assert len(huge) < ad.MAX_DIFF_CHARS + 100
    assert "truncated" in huge


def test_main_calls_the_diff_function_and_runs_with_the_gateway_env_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Liveness: a correct comparator nothing calls proves nothing. ``main`` must reach ``run_answer_diff``."""
    tree = ast.parse((RELEASE_DIR / "answer_diff.py").read_text(encoding="utf-8"))
    main_def = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    called = {
        c.func.id for c in ast.walk(main_def) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
    }
    assert "run_answer_diff" in called
    assert "render_markdown" in called

    seen: dict[str, Any] = {}
    real = ad.run_answer_diff

    def spy(old: Any, new: Any, **kwargs: Any) -> dict[str, Any]:
        seen["endpoint"] = ad._ENDPOINT_ENV in ad.os.environ
        seen["key"] = ad._API_KEY_ENV in ad.os.environ
        seen["kwargs"] = kwargs
        return real(old, new, **kwargs)

    monkeypatch.setattr(ad, "run_answer_diff", spy)
    monkeypatch.setenv(ad._ENDPOINT_ENV, "http://example.invalid")
    monkeypatch.setenv(ad._API_KEY_ENV, "not-a-real-key")
    good = write_tree(tmp_path / "good", {PILOT: OLD_TEXTS})
    markdown = tmp_path / "report.md"
    code = ad.main(
        ["--old-dir", str(good), "--new-dir", str(good), "--seed", "5", "--markdown-out", str(markdown)]
    )
    assert code == 0
    assert seen["endpoint"] is False and seen["key"] is False
    assert seen["kwargs"]["seed"] == 5
    assert ad.os.environ[ad._ENDPOINT_ENV] == "http://example.invalid"  # restored afterwards
    assert "# Served-answer diff" in markdown.read_text(encoding="utf-8")


def test_the_committed_tree_against_itself_has_zero_diff_and_answers(tmp_path: Path) -> None:
    out = tmp_path / "real.json"
    code = ad.main(
        [
            "--old-dir",
            str(DEMO_MANIFESTS),
            "--new-dir",
            str(DEMO_MANIFESTS),
            "--queries-per-pilot",
            "2",
            "--json-out",
            str(out),
        ]
    )
    report = json.loads(out.read_text(encoding="utf-8"))
    assert code == 0
    assert len(report["pilots"]) == 32
    totals = report["totals"]
    assert totals["changed"] == totals["lost"] == totals["new"] == 0
    assert totals["identical"] >= 32 * 10
    assert report["old_answered"] == report["new_answered"] > 32 * 5


def test_a_git_revision_is_exported_without_a_worktree(tmp_path: Path) -> None:
    """``--old-rev HEAD`` against the working tree: the committed tree equals the checkout, so the diff is zero."""
    out = tmp_path / "rev.json"
    code = ad.main(["--old-rev", "HEAD", "--pilots", "imaging__net,cells__python", "--json-out", str(out)])
    report = json.loads(out.read_text(encoding="utf-8"))
    assert code == 0
    assert report["old"] == "git:HEAD"
    assert report["totals"]["lost"] == report["totals"]["changed"] == 0
    assert report["old_answered"] > 20
