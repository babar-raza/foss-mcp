"""G3/TC-568: scripts/release/claims_ledger.py, the reproducible claims ledger (REQ-G3-009, REQ-G3-008).

Offline and deterministic. Parts (a)-(c) build hand-made manifest trees whose every number is known by
construction; part (d) pins the REAL committed tree against an independent count made here by reading the
generation JSON directly (first-line prefixes, never the script's helpers).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE_DIR = REPO_ROOT / "scripts" / "release"
TOOL = RELEASE_DIR / "claims_ledger.py"
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
FIXTURES = REPO_ROOT / "tests" / "fixtures"

sys.path.insert(0, str(RELEASE_DIR))

import claims_ledger as ledger  # noqa: E402

SHA_A = "a" * 40
SHA_B = "b" * 40


# --------------------------------------------------------------------------- helpers (hand-made trees)


def sym(fqn: str, kind: str, bullets: tuple[str, ...] = (), sha: str = SHA_A) -> str:
    body = "".join(f"  - {b}\n" for b in bullets)
    return f"FQN: {fqn}\nKind: {kind}\nSummary.\n\nMethods:\n{body}\nAPI-Coverage: 1 of 1 types\nSource-Commit: {sha}"


def doc(title: str, body: str = "Some documentation text.") -> str:
    return f"FQN: Doc: {title}\n{body}"


def example(title: str, body: str = "var x = 1;") -> str:
    return f"FQN: Example: {title}\nKind: verified_example\nExample:\n{body}"


def write_scope(
    root: Path,
    family: str,
    platform: str,
    chunks: list[str],
    *,
    version: str = "20261001T000000000000Z-aaaaaaaa",
    source_kind: str = "self_extracted",
    stats: dict[str, Any] | None = None,
) -> Path:
    scope = f"{family}__{platform}__{source_kind}"
    gen_id = f"{family}::{platform}::{source_kind}::{version}"
    d = root / scope
    d.mkdir(parents=True, exist_ok=True)
    documents = {
        f"{gen_id}::doc::{i:016x}": {"chunk_id": f"{i:016x}", "text": text, "tokens": []}
        for i, text in enumerate(chunks)
    }
    (d / "active_pointer.json").write_text(json.dumps({"generation_id": gen_id}), encoding="utf-8")
    gen = {
        "family": family,
        "generation_id": gen_id,
        "payload": {
            "lexical_index": {"doc_freq": {}, "documents": documents, "generation_id": gen_id},
            "stats": stats if stats is not None else {},
            "vector_index": {"dimension": 4, "generation_id": gen_id, "points": []},
        },
        "platform": platform,
        "source_kind": source_kind,
        "version": version,
    }
    (d / f"generation__{version}.json").write_text(json.dumps(gen), encoding="utf-8")
    return d


def write_fixture(
    fixtures: Path, family: str, platform: str, *, symbol_counts: dict[str, int], failed: int = 0
) -> None:
    d = fixtures / f"{family}_{platform}"
    d.mkdir(parents=True, exist_ok=True)
    surface = {
        "counts": {"functions": symbol_counts.get("functions", 0), "types": symbol_counts.get("types", 0)},
        "extractor_version": 7,
        "parse_failures": {"engine": "tree-sitter", "examples": [], "failed": failed, "total": 10},
        "source_commit": SHA_A,
        "symbol_counts": symbol_counts,
        "types": [],
    }
    (d / "api_surface.json").write_text(json.dumps(surface), encoding="utf-8")


def write_cache(fixtures: Path, family: str, platform: str, verdicts: list[Any]) -> None:
    d = fixtures / "verified_examples"
    d.mkdir(parents=True, exist_ok=True)
    entries = []
    for i, v in enumerate(verdicts):
        entry: dict[str, Any] = {"title": f"e{i}", "code_sha256": f"{i:064x}"}
        if v is not None:
            entry["verified"] = v
        entries.append(entry)
    (d / f"{family}_{platform}.json").write_text(
        json.dumps({"entries": entries, "schema_version": 1}), encoding="utf-8"
    )


def run_cli(*args: str | Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(TOOL), *map(str, args)],
        capture_output=True,
        timeout=600,
        check=False,
    )


@pytest.fixture
def tree(tmp_path: Path) -> tuple[Path, Path]:
    """Two pilots with known numbers. pdf/net: 3 types (5 members), 2 functions, 1 constant, 4 docs in 2
    categories, 2 example chunks, 1 unclassified chunk. words/python: 1 type, 1 doc."""
    manifests = tmp_path / "manifests"
    fixtures = tmp_path / "fixtures"
    chunks = [
        sym("A.One", "class_declaration", ("M1() -> void", "M2() -> void", "P: int (read-only)")),
        sym("A.Two", "enum_declaration", ("V1", "V2")),
        sym("A.Three", "interface_declaration"),
        sym("f1", "function", ("f1(x: int) -> int",)),
        sym("f2", "function", ("f2() -> void",)),
        sym("K", "constant"),
        doc("Overview", "d1"),
        doc("Overview", "d2"),
        doc("Overview", "d3"),
        doc("Limits"),
        example("Ex one"),
        example("Ex two"),
        "stray text without any header",
    ]
    write_scope(manifests, "pdf", "net", chunks, stats={"symbols": 6, "docs": 4, "examples": 2, "total": 12})
    write_scope(
        manifests,
        "words",
        "python",
        [sym("W.Only", "class", ("m() -> None",), SHA_B), doc("Intro")],
        version="v2",
    )
    write_fixture(fixtures, "pdf", "net", symbol_counts={"functions": 2, "types": 3}, failed=4)
    write_cache(fixtures, "pdf", "net", [True, True, True, False, None])
    return manifests, fixtures


# --------------------------------------------------------------------------- (a) hand-made known answers


def test_constants_are_the_mandated_prefixes() -> None:
    text = TOOL.read_text(encoding="utf-8")
    assert 'EXAMPLE_PREFIX = "FQN: Example: "' in text.splitlines()
    assert 'DOC_PREFIX = "FQN: Doc: "' in text.splitlines()
    assert ledger.EXAMPLE_PREFIX == "FQN: Example: "
    assert ledger.DOC_PREFIX == "FQN: Doc: "


def test_symbol_doc_example_counts_on_a_hand_made_tree(tree: tuple[Path, Path]) -> None:
    manifests, fixtures = tree
    out = ledger.build_ledger(manifests, fixtures)
    p = out["pilots"]["pdf/net"]
    assert p["symbols"]["total"] == 6
    assert p["symbols"]["types"] == 3
    assert p["symbols"]["functions"] == 2
    assert p["symbols"]["constants"] == 1
    assert p["symbols"]["by_kind"] == {
        "class_declaration": 1,
        "constant": 1,
        "enum_declaration": 1,
        "function": 2,
        "interface_declaration": 1,
    }
    assert p["documentation"]["total"] == 4
    assert p["documentation"]["by_category"] == {"Limits": 1, "Overview": 3}
    assert p["examples"]["served_chunks"] == 2
    assert p["unclassified_chunks"] == 1


def test_members_count_bullets_of_type_symbols_only(tree: tuple[Path, Path]) -> None:
    manifests, fixtures = tree
    p = ledger.build_ledger(manifests, fixtures)["pilots"]["pdf/net"]
    # 3 + 2 + 0 members on the types; the one-line function signatures are functions, not members.
    assert p["members"] == 5


def test_source_commit_generation_id_and_identity(tree: tuple[Path, Path]) -> None:
    manifests, fixtures = tree
    out = ledger.build_ledger(manifests, fixtures)
    p = out["pilots"]["words/python"]
    assert p["source_commit"] == SHA_B
    assert p["generation_id"] == "words::python::self_extracted::v2"
    assert (p["family"], p["platform"], p["source_kind"]) == ("words", "python", "self_extracted")
    assert out["pilots"]["pdf/net"]["source_commit"] == SHA_A


def test_declared_stats_are_echoed_next_to_the_counted_numbers(tree: tuple[Path, Path]) -> None:
    manifests, fixtures = tree
    p = ledger.build_ledger(manifests, fixtures)["pilots"]["pdf/net"]
    assert p["declared_stats"] == {"docs": 4, "examples": 2, "symbols": 6, "total": 12}


# --------------------------------------------------------------------------- (b) the three example figures


def test_three_example_figures_are_reported_separately(tree: tuple[Path, Path]) -> None:
    manifests, fixtures = tree
    ex = ledger.build_ledger(manifests, fixtures)["pilots"]["pdf/net"]["examples"]
    assert ex["served_chunks"] == 2  # chunks the generation serves
    assert ex["cache_entries"] == 5  # entries in the verified-example cache file
    assert ex["verified_entries"] == 3  # of those, entries whose verdict is verified
    assert ex["failed_entries"] == 1
    assert ex["other_entries"] == 1
    assert len({ex["served_chunks"], ex["cache_entries"], ex["verified_entries"]}) == 3


def test_fixture_fields_and_missing_fixture(tree: tuple[Path, Path]) -> None:
    manifests, fixtures = tree
    out = ledger.build_ledger(manifests, fixtures)
    f = out["pilots"]["pdf/net"]["fixture"]
    assert f["present"] is True
    assert f["extractor_version"] == 7
    assert f["symbol_counts"] == {"functions": 2, "types": 3}
    assert f["parse_failures"] == {"engine": "tree-sitter", "examples": [], "failed": 4, "total": 10}
    assert f["parse_failures_failed"] == 4
    w = out["pilots"]["words/python"]
    assert w["fixture"]["present"] is False
    assert w["examples"]["cache_entries"] == 0


def test_sidecar_states(tree: tuple[Path, Path]) -> None:
    manifests, fixtures = tree
    (manifests / "package_registry_pdf_net.json").write_text(
        json.dumps({"coordinate": "x"}), encoding="utf-8"
    )
    (manifests / "product_reference_pdf_net.json").write_text(
        json.dumps({"state": "fetch_failed", "status": "fetch_failed"}), encoding="utf-8"
    )
    (manifests / "recent_releases_pdf_net.json").write_text("[]", encoding="utf-8")
    (manifests / "package_registry_words_python.json").write_text("{ not json", encoding="utf-8")
    (manifests / "product_reference_words_python.json").write_text(
        json.dumps({"manifest_text": "m"}), encoding="utf-8"
    )
    (manifests / "recent_releases_words_python.json").write_text(
        json.dumps([{"tag_name": "v1"}]), encoding="utf-8"
    )
    out = ledger.build_ledger(manifests, fixtures)
    s = out["pilots"]["pdf/net"]["sidecars"]
    assert s == {
        "package_registry": "present",
        "product_reference": "fetch_failed",
        "recent_releases": "empty",
    }
    w = out["pilots"]["words/python"]["sidecars"]
    assert w["package_registry"] == "unreadable"
    assert w["product_reference"] == "present"
    assert w["recent_releases"] == "present"
    # an absent sidecar is reported as absent
    (manifests / "recent_releases_words_python.json").unlink()
    again = ledger.build_ledger(manifests, fixtures)
    assert again["pilots"]["words/python"]["sidecars"]["recent_releases"] == "absent"
    counts = again["totals"]["sidecars"]["recent_releases"]
    assert counts == {"absent": 1, "empty": 1, "fetch_failed": 0, "present": 0, "unreadable": 0}


def test_data_bytes_are_the_sizes_of_the_files(tree: tuple[Path, Path]) -> None:
    manifests, fixtures = tree
    (manifests / "recent_releases_pdf_net.json").write_text("[]", encoding="utf-8")
    (manifests / "product_reference_pdf_net.json").write_text('{"a": 1}', encoding="utf-8")
    p = ledger.build_ledger(manifests, fixtures)["pilots"]["pdf/net"]
    gen = next((manifests / "pdf__net__self_extracted").glob("generation__*.json")).stat().st_size
    ptr = (manifests / "pdf__net__self_extracted" / "active_pointer.json").stat().st_size
    assert p["data_bytes"]["generation"] == gen
    assert p["data_bytes"]["sidecars"] == 2 + 8
    assert p["data_bytes"]["total"] == gen + ptr + 2 + 8


def test_totals_sum_the_pilots(tree: tuple[Path, Path]) -> None:
    manifests, fixtures = tree
    out = ledger.build_ledger(manifests, fixtures)
    t = out["totals"]
    assert t["pilots"] == 2
    assert t["symbols"]["total"] == 6 + 1
    assert t["symbols"]["types"] == 3 + 1
    assert t["symbols"]["functions"] == 2
    assert t["documentation"]["total"] == 4 + 1
    assert t["members"] == 5 + 1
    assert t["examples"] == {
        "cache_entries": 5,
        "failed_entries": 1,
        "other_entries": 1,
        "served_chunks": 2,
        "verified_entries": 3,
    }
    assert t["fixtures"]["symbol_counts"] == {"functions": 2, "types": 3}
    assert t["fixtures"]["parse_failures_failed"] == 4


def test_cache_files_with_no_pilot_are_listed(tree: tuple[Path, Path]) -> None:
    manifests, fixtures = tree
    write_cache(fixtures, "ghost", "cpp", [True])
    out = ledger.build_ledger(manifests, fixtures)
    assert out["unmatched_verified_example_files"] == ["ghost_cpp.json"]
    assert out["totals"]["examples"]["cache_entries"] == 5  # a cache with no pilot is not counted in a pilot


# --------------------------------------------------------------------------- (c) determinism and the CLI


def test_output_is_byte_identical_on_two_runs(tree: tuple[Path, Path]) -> None:
    manifests, fixtures = tree
    first = run_cli("--manifests", manifests, "--fixtures", fixtures)
    second = run_cli("--manifests", manifests, "--fixtures", fixtures)
    assert first.returncode == 0, first.stderr.decode("utf-8", "replace")
    assert first.stdout == second.stdout
    assert first.stdout.endswith(b"\n")
    doc_out = json.loads(first.stdout)
    assert doc_out["totals"]["symbols"]["total"] == 7
    assert b"\r" not in first.stdout


def test_render_has_no_timestamp_and_sorted_keys(tree: tuple[Path, Path]) -> None:
    manifests, fixtures = tree
    text = ledger.render(ledger.build_ledger(manifests, fixtures)).decode("utf-8")
    assert json.loads(text)
    assert "generated_at" not in text and "timestamp" not in text
    keys = list(json.loads(text)["pilots"])
    assert keys == sorted(keys)


# --------------------------------------------------------------------------- --check claims.json


def test_check_lists_exactly_the_claims_that_differ(tree: tuple[Path, Path], tmp_path: Path) -> None:
    manifests, fixtures = tree
    claims = {
        "claims": [
            {"claim": "pdf/net symbols", "path": "pilots.pdf/net.symbols.total", "value": 6},
            {
                "claim": "served examples (conflated once)",
                "path": "totals.examples.served_chunks",
                "value": 183,
            },
            {"claim": "verified entries", "path": "totals.examples.verified_entries", "value": 3},
            {"claim": "typo", "path": "totals.nothing.here", "value": 1},
        ]
    }
    claims_path = tmp_path / "claims.json"
    claims_path.write_text(json.dumps(claims), encoding="utf-8")
    res = run_cli("--manifests", manifests, "--fixtures", fixtures, "--check", claims_path)
    assert res.returncode == 1
    report = json.loads(res.stdout)
    assert report["checked"] == 4
    assert [m["path"] for m in report["mismatches"]] == [
        "totals.examples.served_chunks",
        "totals.nothing.here",
    ]
    assert report["mismatches"][0] == {
        "actual": 2,
        "claim": "served examples (conflated once)",
        "claimed": 183,
        "path": "totals.examples.served_chunks",
    }
    assert report["mismatches"][1]["actual"] is None


def test_check_passes_on_a_plain_path_to_value_mapping(tree: tuple[Path, Path], tmp_path: Path) -> None:
    manifests, fixtures = tree
    claims_path = tmp_path / "claims.json"
    claims_path.write_text(
        json.dumps({"totals.symbols.total": 7, "pilots.words/python.documentation.total": 1}),
        encoding="utf-8",
    )
    res = run_cli("--manifests", manifests, "--fixtures", fixtures, "--check", claims_path)
    assert res.returncode == 0, res.stdout
    assert json.loads(res.stdout) == {"checked": 2, "mismatches": []}


def test_unusable_inputs_exit_two(tmp_path: Path) -> None:
    res = run_cli("--manifests", tmp_path / "missing", "--fixtures", tmp_path)
    assert res.returncode == 2
    assert b"claims_ledger" in res.stderr


# --------------------------------------------------------------------------- (d) the real committed tree


def _independent_count(generation_path: Path) -> dict[str, int]:
    """Count straight from the generation JSON, by first-line prefix, without the script's helpers."""
    data = json.loads(generation_path.read_text(encoding="utf-8"))
    docs = data["payload"]["lexical_index"]["documents"]
    n_doc = n_ex = n_sym = n_other = 0
    for entry in docs.values():
        text = entry["text"]
        first = text.split("\n", 1)[0]
        if first.startswith("FQN: Doc: "):
            n_doc += 1
        elif first.startswith("FQN: Example: "):
            n_ex += 1
        elif first.startswith("FQN: "):
            n_sym += 1
        else:
            n_other += 1
    return {"docs": n_doc, "examples": n_ex, "symbols": n_sym, "other": n_other, "chunks": len(docs)}


def _active_generation(scope_dir: Path) -> Path:
    gid = json.loads((scope_dir / "active_pointer.json").read_text(encoding="utf-8"))["generation_id"]
    return scope_dir / f"generation__{gid.rsplit('::', 1)[-1]}.json"


@pytest.fixture(scope="module")
def real_ledger() -> dict[str, Any]:
    return ledger.build_ledger(DEMO_MANIFESTS, FIXTURES)


def test_real_tree_counts_equal_an_independent_read(real_ledger: dict[str, Any]) -> None:
    scopes = sorted(p for p in DEMO_MANIFESTS.iterdir() if p.is_dir())
    assert len(real_ledger["pilots"]) == len(scopes) > 0
    for scope in scopes:
        family, platform, _kind = scope.name.split("__")
        got = real_ledger["pilots"][f"{family}/{platform}"]
        want = _independent_count(_active_generation(scope))
        assert got["symbols"]["total"] == want["symbols"], scope.name
        assert got["documentation"]["total"] == want["docs"], scope.name
        assert got["examples"]["served_chunks"] == want["examples"], scope.name
        assert got["unclassified_chunks"] == want["other"], scope.name
        assert (
            got["symbols"]["total"]
            + got["documentation"]["total"]
            + got["examples"]["served_chunks"]
            + got["unclassified_chunks"]
            == want["chunks"]
        ), scope.name


def test_real_tree_pdf_net_is_pinned_to_the_measured_chunks(real_ledger: dict[str, Any]) -> None:
    p = real_ledger["pilots"]["pdf/net"]
    # measured by reading the committed generation directly: 901 symbol, 25 documentation, 3 example chunks
    assert p["symbols"]["total"] == 901
    assert p["documentation"]["total"] == 25
    assert p["examples"]["served_chunks"] == 3
    assert p["fixture"]["symbol_counts"] == {"functions": 0, "types": 901}


def test_real_tree_example_cache_numbers_equal_a_direct_read(real_ledger: dict[str, Any]) -> None:
    entries = verified = failed = 0
    for path in sorted((FIXTURES / "verified_examples").glob("*.json")):
        for e in json.loads(path.read_text(encoding="utf-8"))["entries"]:
            entries += 1
            if e.get("verified") is True:
                verified += 1
            elif e.get("verified") is False:
                failed += 1
    ex = real_ledger["totals"]["examples"]
    unmatched = real_ledger["unmatched_verified_example_files"]
    assert unmatched == []  # every cache file belongs to a pilot, so the totals can be compared whole
    assert ex["cache_entries"] == entries
    assert ex["verified_entries"] == verified
    assert ex["failed_entries"] == failed
    assert ex["other_entries"] == entries - verified - failed
    assert ex["served_chunks"] == sum(
        _independent_count(_active_generation(s))["examples"] for s in DEMO_MANIFESTS.iterdir() if s.is_dir()
    )


def test_real_tree_totals_equal_the_sum_of_the_pilots(real_ledger: dict[str, Any]) -> None:
    pilots = real_ledger["pilots"].values()
    t = real_ledger["totals"]
    assert t["pilots"] == len(real_ledger["pilots"])
    assert t["symbols"]["total"] == sum(p["symbols"]["total"] for p in pilots)
    assert (
        t["symbols"]["types"] + t["symbols"]["functions"] + t["symbols"]["constants"] == t["symbols"]["total"]
    )
    assert t["documentation"]["total"] == sum(p["documentation"]["total"] for p in pilots)
    assert t["members"] == sum(p["members"] for p in pilots)
    assert t["data_bytes"]["total"] == sum(p["data_bytes"]["total"] for p in pilots)


def test_real_tree_cli_is_byte_identical_on_two_runs() -> None:
    first = run_cli()
    second = run_cli()
    assert first.returncode == 0, first.stderr.decode("utf-8", "replace")
    assert first.stdout == second.stdout
    assert json.loads(first.stdout)["totals"]["pilots"] > 0
