"""G3/TC-346: scripts/release/compact_demo_manifests.py and the lean committed infra/demo-manifests/ tree.

Offline: no docker, no network, no embedding call. Part (a)-(c) exercise the tool on synthetic scopes in
tmp_path; part (d) pins the REAL committed tree - its size budget, and that every scope still loads
through the real GenerationManifestStore and still carries real, non-empty lexical content.
"""

from __future__ import annotations

import importlib
import json
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE_DIR = REPO_ROOT / "scripts" / "release"
TOOL = RELEASE_DIR / "compact_demo_manifests.py"
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"

sys.path.insert(0, str(RELEASE_DIR))

import compact_demo_manifests as compact  # noqa: E402

from foss_mcp.indexing.generation_manifest import GenerationManifestStore, build_manifest  # noqa: E402
from foss_mcp.indexing.vector_index_writer import EXAMPLE_MARKER  # noqa: E402

# Measured after compaction on the committed tree (TC-346): 40 scopes, total 17,505,012 bytes (16.7 MiB),
# median generation file 209,084 bytes, largest 2,344,171 bytes (before: 967.6 MiB, up to 45 MB per file).
# Each bound carries about 25% headroom over its measurement.
# re-pinned by TC-551: 40 -> 32
EXPECTED_SCOPES = 32
# Re-pinned 2026-10-14 (TC-503, rev-b487453): the final re-ingest grew the tree to 28_069_555 bytes total, median 577_817, largest 2_944_920
# (the largest-file budget above still holds), because of complete API extraction plus quantised vectors for verified-example chunks.
# Re-measured 2026-10-17 on the supervisor data commit 58ba5861 (cluster re-ingest of all 32 pilots on rev-e4a6ada): tree 41,687,973 bytes over 32 scopes,
# median generation file 762,806, largest 5,098,150 (pdf/java), then pdf/typescript 4,767,300, pdf/net 3,430,234, html/python 3,052,169.
# The tree grew because thirteen pilots now hold their WHOLE public surface (pdf/java 1,239 types, pdf/typescript 1,333 types and 3,060
# functions, pdf/net 901 types) instead of a 300-type cap. Each budget is the measurement x 1.05 rounded up to the next 10,000.
# re-pinned by TC-553: 28_000_000 -> 43_780_000
TREE_BYTES_BUDGET = 43_780_000
# re-pinned by TC-553: 2_950_000 -> 5_360_000
LARGEST_FILE_BYTES_BUDGET = 5_360_000
# TC-503: median re-measured at 577_817 bytes on 2026-10-14 (rev-b487453), see the note above TREE_BYTES_BUDGET.
# re-pinned by TC-553: 710_000 -> 810_000
MEDIAN_FILE_BYTES_BUDGET = 810_000
# The lean standalone image was agreed with the operator at 45 MB for the baked tree. Only the operator moves it; a worker never does.
LEAN_CEILING_BYTES = 45_000_000


def budget_is_within_the_lean_ceiling(budget: int, ceiling: int = LEAN_CEILING_BYTES) -> bool:
    """True when a tree-size budget does not exceed the operator-agreed lean ceiling."""
    return budget <= ceiling


FAMILY, PLATFORM, SOURCE_KIND = "pdf", "go", "self_extracted"
SCOPE_DIR_NAME = f"{FAMILY}__{PLATFORM}__{SOURCE_KIND}"
SCOPE = f"{FAMILY}::{PLATFORM}::{SOURCE_KIND}"


def _point(generation_id: str, n: int, text: str, **extra: Any) -> dict[str, Any]:
    return {
        "chunk_id": f"chunk{n}",
        "point_id": f"{generation_id}::point::chunk{n}",
        "text": text,
        "vector": [0.1 * n, -0.25, 1.0 / 3.0, 2.5e-07],
        **extra,
    }


def _payload(generation_id: str) -> dict[str, Any]:
    return {
        "lexical_index": {
            "doc_freq": {"alpha": 2, "café": 1},
            "documents": {
                "chunk0": {"tf": {"alpha": 1}, "length": 3},
                "chunk1": {"tf": {"alpha": 1, "café": 1}, "length": 5},
            },
            "generation_id": generation_id,
        },
        "vector_index": {
            "dimension": 4,
            "generation_id": generation_id,
            "points": [
                _point(generation_id, 0, "plain prose with no sample block"),
                _point(generation_id, 1, f"{EXAMPLE_MARKER} café ✓ code()", rank=7),
                _point(generation_id, 2, "reference text, nothing special"),
                _point(generation_id, 3, f"intro\n{EXAMPLE_MARKER}\n  run()"),
                _point(generation_id, 4, "another plain chunk"),
            ],
        },
    }


def make_scope(root: Path, name: str = SCOPE_DIR_NAME) -> tuple[Path, str, str]:
    """Write a scope with TWO generations through the real store; the newer one is active.

    Returns (scope_dir, old_generation_id, active_generation_id)."""
    family, platform, source_kind = name.split("__")
    store = GenerationManifestStore(root)
    ids = []
    for version in ("20260101T000000000000Z-aaaa1111", "20260202T000000000000Z-bbbb2222"):
        generation_id = build_manifest(family, platform, source_kind, version).generation_id
        manifest = build_manifest(family, platform, source_kind, version, _payload(generation_id))
        store.write_generation(manifest)
        ids.append(manifest.generation_id)
    scope_dir = root / name
    (scope_dir / "active_pointer.json").write_text(json.dumps({"generation_id": ids[1]}), encoding="utf-8")
    return scope_dir, ids[0], ids[1]


def generation_files(scope_dir: Path) -> list[Path]:
    return sorted(scope_dir.glob("generation__*.json"))


def snapshot(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def real_scope_dirs() -> list[Path]:
    return sorted(p for p in DEMO_MANIFESTS.iterdir() if p.is_dir())


def real_generation_file(scope_dir: Path) -> Path:
    files = generation_files(scope_dir)
    assert len(files) == 1, (scope_dir.name, [f.name for f in files])
    return files[0]


# ---------------------------------------------------------------------------------------------------
# (a) synthetic scope: what compaction keeps, drops and preserves
# ---------------------------------------------------------------------------------------------------


def test_only_the_active_generation_remains(tmp_path: Path) -> None:
    scope_dir, old_id, active_id = make_scope(tmp_path)
    assert len(generation_files(scope_dir)) == 2

    assert compact.main(["--root", str(tmp_path)]) == 0

    remaining = generation_files(scope_dir)
    assert [p.name for p in remaining] == [f"generation__{active_id.rsplit('::', 1)[-1]}.json"]
    assert old_id.rsplit("::", 1)[-1] not in remaining[0].name
    assert json.loads((scope_dir / "active_pointer.json").read_text(encoding="utf-8")) == {
        "generation_id": active_id
    }


def test_only_example_points_remain_with_every_field_intact(tmp_path: Path) -> None:
    scope_dir, _, active_id = make_scope(tmp_path)
    before = _payload(active_id)["vector_index"]["points"]

    assert compact.main(["--root", str(tmp_path)]) == 0

    doc = json.loads(generation_files(scope_dir)[0].read_text(encoding="utf-8"))
    vector_index = doc["payload"]["vector_index"]
    assert vector_index["points"] == [before[1], before[3]]  # order kept, every field kept (incl. "rank")
    assert all(EXAMPLE_MARKER in p["text"] for p in vector_index["points"])
    assert vector_index["points"][0]["rank"] == 7
    assert vector_index["dimension"] == 4
    assert vector_index["generation_id"] == active_id


def test_lexical_index_is_equal_and_the_file_is_compact_sorted_utf8_json(tmp_path: Path) -> None:
    scope_dir, _, active_id = make_scope(tmp_path)

    assert compact.main(["--root", str(tmp_path)]) == 0

    path = generation_files(scope_dir)[0]
    raw = path.read_bytes()
    doc = json.loads(raw.decode("utf-8"))
    assert doc["payload"]["lexical_index"] == _payload(active_id)["lexical_index"]
    assert raw.endswith(b"}\n") and not raw.endswith(b"\n\n")
    assert raw == (json.dumps(doc, separators=(",", ":"), sort_keys=True, ensure_ascii=False) + "\n").encode()
    assert b"\n" not in raw[:-1]  # one line: no indentation (separator padding is ruled out by the equality)
    assert "café".encode() in raw  # genuine UTF-8, not \u escapes
    assert list(doc) == sorted(doc)


def test_compacted_scope_still_loads_through_the_real_store(tmp_path: Path) -> None:
    scope_dir, _, active_id = make_scope(tmp_path)
    assert compact.main(["--root", str(tmp_path)]) == 0

    store = GenerationManifestStore(tmp_path)
    assert store.read_active(SCOPE) == active_id
    manifest = store.read_generation(SCOPE, active_id)
    assert manifest.generation_id == active_id
    assert manifest.payload["lexical_index"]["documents"]
    assert sorted(p.name for p in scope_dir.iterdir()) == sorted(
        ["active_pointer.json", generation_files(scope_dir)[0].name]
    )


def test_a_second_run_is_a_noop_with_identical_bytes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    make_scope(tmp_path)
    assert compact.main(["--root", str(tmp_path)]) == 0
    first = snapshot(tmp_path)
    capsys.readouterr()

    assert compact.main(["--root", str(tmp_path)]) == 0

    assert snapshot(tmp_path) == first
    assert "0 scope(s) compact" in capsys.readouterr().out  # nothing was left to change
    assert compact.main(["--root", str(tmp_path), "--check"]) == 0


def test_the_report_has_one_line_per_scope_and_a_total(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    make_scope(tmp_path)
    make_scope(tmp_path, "pdf__java__self_extracted")

    assert compact.main(["--root", str(tmp_path)]) == 0

    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 3
    assert lines[0].startswith("pdf__go__self_extracted:") and "kept 2 dropped 3" in lines[0]
    assert lines[1].startswith("pdf__java__self_extracted:")
    assert lines[2].startswith("total: 2 scopes,") and "kept 4 dropped 6" in lines[2]


# ---------------------------------------------------------------------------------------------------
# (b) a pointer naming a missing generation
# ---------------------------------------------------------------------------------------------------


def test_a_pointer_naming_a_missing_generation_exits_2_naming_the_scope_and_changes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    make_scope(tmp_path, "pdf__java__self_extracted")
    broken, _, _ = make_scope(tmp_path)
    (broken / "active_pointer.json").write_text(
        json.dumps({"generation_id": f"{SCOPE}::20991231T000000000000Z-ffffffff"}), encoding="utf-8"
    )
    before = snapshot(tmp_path)

    assert compact.main(["--root", str(tmp_path)]) == 2

    assert SCOPE_DIR_NAME in capsys.readouterr().err
    assert snapshot(tmp_path) == before  # the healthy sibling was not touched either


def test_a_scope_without_an_active_pointer_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scope_dir, _, _ = make_scope(tmp_path)
    (scope_dir / "active_pointer.json").unlink()

    assert compact.main(["--root", str(tmp_path)]) == 2
    assert SCOPE_DIR_NAME in capsys.readouterr().err
    assert len(generation_files(scope_dir)) == 2


# ---------------------------------------------------------------------------------------------------
# (c) --check and --dry-run
# ---------------------------------------------------------------------------------------------------


def test_check_exits_1_on_a_non_compact_scope_and_0_after_compaction_and_changes_nothing(
    tmp_path: Path,
) -> None:
    make_scope(tmp_path)
    before = snapshot(tmp_path)

    assert compact.main(["--root", str(tmp_path), "--check"]) == 1
    assert snapshot(tmp_path) == before

    assert compact.main(["--root", str(tmp_path)]) == 0
    after = snapshot(tmp_path)
    assert after != before

    assert compact.main(["--root", str(tmp_path), "--check"]) == 0
    assert snapshot(tmp_path) == after


def test_a_scope_with_a_stale_generation_file_alone_is_not_compact(tmp_path: Path) -> None:
    scope_dir, old_id, _ = make_scope(tmp_path)
    assert compact.main(["--root", str(tmp_path)]) == 0
    stale = scope_dir / f"generation__{old_id.rsplit('::', 1)[-1]}.json"
    stale.write_text("{}", encoding="utf-8")

    assert compact.main(["--root", str(tmp_path), "--check"]) == 1
    assert compact.main(["--root", str(tmp_path)]) == 0
    assert not stale.exists()


def test_dry_run_reports_but_changes_nothing_and_exits_0(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    make_scope(tmp_path)
    before = snapshot(tmp_path)

    assert compact.main(["--root", str(tmp_path), "--dry-run"]) == 0

    assert snapshot(tmp_path) == before
    assert "kept 2 dropped 3" in capsys.readouterr().out


def test_the_tool_runs_as_a_standalone_script_and_imports_the_marker_instead_of_copying_it(
    tmp_path: Path,
) -> None:
    make_scope(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(TOOL), "--root", str(tmp_path), "--check"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=tmp_path,
        timeout=120,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "total: 1 scopes" in proc.stdout

    source = TOOL.read_text(encoding="utf-8")
    assert "from foss_mcp.indexing.vector_index_writer import EXAMPLE_MARKER" in source
    assert 'EXAMPLE_MARKER = "' not in source
    assert compact.EXAMPLE_MARKER is EXAMPLE_MARKER


# ---------------------------------------------------------------------------------------------------
# (d) the REAL committed tree
# ---------------------------------------------------------------------------------------------------


def test_real_tree_has_thirty_two_scopes_each_with_one_generation_file_and_its_pointer() -> None:
    scopes = real_scope_dirs()
    assert len(scopes) == EXPECTED_SCOPES
    for scope_dir in scopes:
        generation_file = real_generation_file(scope_dir)
        assert sorted(p.name for p in scope_dir.iterdir()) == sorted(
            ["active_pointer.json", generation_file.name]
        ), scope_dir.name


def test_real_tree_is_within_the_lean_budget() -> None:
    sizes = [real_generation_file(d).stat().st_size for d in real_scope_dirs()]
    total = sum(p.stat().st_size for p in DEMO_MANIFESTS.rglob("*") if p.is_file())

    assert total <= TREE_BYTES_BUDGET, (total, TREE_BYTES_BUDGET)
    assert max(sizes) <= LARGEST_FILE_BYTES_BUDGET, (max(sizes), LARGEST_FILE_BYTES_BUDGET)
    assert statistics.median(sizes) <= MEDIAN_FILE_BYTES_BUDGET, (
        statistics.median(sizes),
        MEDIAN_FILE_BYTES_BUDGET,
    )


def test_the_tree_budgets_stay_under_the_agreed_lean_ceiling() -> None:
    # lazy: the sidecars test module imports this one
    sidecars_test = importlib.import_module("tests.deploy.test_demo_manifests_sidecars")

    assert budget_is_within_the_lean_ceiling(TREE_BYTES_BUDGET), (TREE_BYTES_BUDGET, LEAN_CEILING_BYTES)
    assert budget_is_within_the_lean_ceiling(sidecars_test.TREE_BYTES_BUDGET), (
        sidecars_test.TREE_BYTES_BUDGET,
        LEAN_CEILING_BYTES,
    )
    assert LEAN_CEILING_BYTES == 45_000_000


def test_the_ceiling_helper_rejects_a_budget_above_the_ceiling() -> None:
    assert budget_is_within_the_lean_ceiling(45_000_000)
    assert not budget_is_within_the_lean_ceiling(45_000_001)
    assert not budget_is_within_the_lean_ceiling(60_000_000)
    assert budget_is_within_the_lean_ceiling(1_000, ceiling=1_000)
    assert not budget_is_within_the_lean_ceiling(1_001, ceiling=1_000)


def test_release_md_names_the_lean_ceiling_and_this_test_file() -> None:
    text = " ".join((REPO_ROOT / "docs" / "RELEASE.md").read_text(encoding="utf-8").split())
    assert "45 MB" in text
    assert "test_compact_demo_manifests.py" in text


def test_real_tree_check_exits_0_so_the_committed_tree_is_already_compact(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert compact.main(["--root", str(DEMO_MANIFESTS), "--check"]) == 0
    assert "0 scope(s) compact" in capsys.readouterr().out


def test_real_tree_every_scope_loads_through_the_store_with_real_non_empty_lexical_content() -> None:
    store = GenerationManifestStore(DEMO_MANIFESTS)
    for scope_dir in real_scope_dirs():
        scope = scope_dir.name.replace("__", "::")
        active_id = store.read_active(scope)
        assert active_id, scope_dir.name
        manifest = store.read_generation(scope, active_id)
        assert manifest.generation_id == active_id
        lexical = manifest.payload["lexical_index"]
        assert isinstance(lexical["documents"], dict) and len(lexical["documents"]) > 0, scope_dir.name
        assert isinstance(lexical["doc_freq"], dict) and len(lexical["doc_freq"]) > 0, scope_dir.name
        assert any(doc for doc in lexical["documents"].values()), scope_dir.name


def test_real_tree_every_kept_vector_point_is_an_example_chunk_and_keeps_its_fields() -> None:
    kept_total = 0
    for scope_dir in real_scope_dirs():
        doc = json.loads(real_generation_file(scope_dir).read_text(encoding="utf-8"))
        vector_index = doc["payload"]["vector_index"]
        assert vector_index["generation_id"] == doc["generation_id"], scope_dir.name
        assert vector_index["dimension"] == 4096, scope_dir.name
        for point in vector_index["points"]:
            assert EXAMPLE_MARKER in point["text"], (scope_dir.name, point["point_id"])
            assert sorted(point) == ["chunk_id", "point_id", "text", "vector"], scope_dir.name
            assert len(point["vector"]) == vector_index["dimension"], (scope_dir.name, point["point_id"])
        kept_total += len(vector_index["points"])
    assert kept_total > 0  # the pdf/go-style example chunks really survived
