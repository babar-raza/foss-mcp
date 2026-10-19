"""G3/TC-568: scripts/release/generation_diff.py, the per-pilot generation diff report (REQ-G3-009, REQ-G3-008).

Offline and deterministic. Synthetic manifest trees are built here with known differences (a symbol removed,
one added, one signature changed, a documentation chunk added); the real-tree tests diff the committed
infra/demo-manifests against itself and between HEAD~1 and HEAD.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE_DIR = REPO_ROOT / "scripts" / "release"
TOOL = RELEASE_DIR / "generation_diff.py"
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"

sys.path.insert(0, str(RELEASE_DIR))

import generation_diff as gd  # noqa: E402

SHA_A = "a" * 40
SHA_B = "b" * 40


# --------------------------------------------------------------------------- helpers (hand-made trees)


def sym(
    fqn: str, kind: str = "class_declaration", bullets: tuple[str, ...] = (), sha: str = SHA_A, cov: int = 1
) -> str:
    body = "".join(f"  - {b}\n" for b in bullets)
    return f"FQN: {fqn}\nKind: {kind}\nSummary.\n\nMethods:\n{body}\nAPI-Coverage: {cov} of {cov} types\nSource-Commit: {sha}"


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
) -> None:
    gen_id = f"{family}::{platform}::self_extracted::{version}"
    d = root / f"{family}__{platform}__self_extracted"
    d.mkdir(parents=True, exist_ok=True)
    documents = {
        # chunk ids differ between generations on purpose: identity must come from the content
        f"{gen_id}::doc::{i:016x}": {"chunk_id": f"{i:016x}", "text": text, "tokens": []}
        for i, text in enumerate(chunks)
    }
    (d / "active_pointer.json").write_text(json.dumps({"generation_id": gen_id}), encoding="utf-8")
    gen = {
        "family": family,
        "generation_id": gen_id,
        "payload": {
            "lexical_index": {"doc_freq": {}, "documents": documents, "generation_id": gen_id},
            "stats": {},
            "vector_index": {"dimension": 4, "generation_id": gen_id, "points": []},
        },
        "platform": platform,
        "source_kind": "self_extracted",
        "version": version,
    }
    for stale in d.glob("generation__*.json"):
        stale.unlink()
    (d / f"generation__{version}.json").write_text(json.dumps(gen), encoding="utf-8")


BASE = [
    sym("A.Keep", bullets=("M1() -> void",)),
    sym("A.Removed", bullets=("R1() -> void", "R2() -> void")),
    sym("A.Changed", bullets=("C1(x: int) -> int", "C2() -> void")),
    doc("Overview"),
    example("Ex one"),
]


def new_chunks() -> list[str]:
    return [
        sym("A.Keep", bullets=("M1() -> void",)),
        sym("A.Added", bullets=("N1() -> void",)),
        sym("A.Changed", bullets=("C1(x: long) -> long", "C2() -> void", "C3() -> void")),
        doc("Overview"),
        doc("Added doc page", "Brand new."),
        example("Ex one"),
    ]


def two_trees(tmp_path: Path) -> tuple[Path, Path]:
    old, new = tmp_path / "old", tmp_path / "new"
    write_scope(old, "pdf", "net", BASE)
    write_scope(new, "pdf", "net", new_chunks(), version="20261002T000000000000Z-bbbbbbbb")
    return old, new


def run_cli(*args: str | Path, cwd: Path | None = None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(TOOL), *map(str, args)],
        capture_output=True,
        timeout=900,
        check=False,
        cwd=cwd,
    )


# --------------------------------------------------------------------------- (d) self diff and synthetic diff


def test_a_tree_against_itself_reports_no_change_and_exits_zero(tmp_path: Path) -> None:
    old, _new = two_trees(tmp_path)
    report = gd.diff_trees(gd.DirTree(old), gd.DirTree(old))
    assert report["changed"] is False
    p = report["pilots"]["pdf/net"]
    assert p["status"] == "unchanged"
    assert p["symbols"]["added_count"] == p["symbols"]["removed_count"] == p["symbols"]["changed_count"] == 0
    assert p["signature_lines"]["changed"] == 0
    assert p["documentation"]["delta"] == 0 and p["examples"]["delta"] == 0
    res = run_cli(old, old, "--max-removed-symbols", "0", "--max-signature-changes", "0")
    assert res.returncode == 0, res.stderr.decode("utf-8", "replace")
    assert json.loads(res.stdout)["changed"] is False


def test_synthetic_diff_reports_exactly_the_planted_differences(tmp_path: Path) -> None:
    old, new = two_trees(tmp_path)
    report = gd.diff_trees(gd.DirTree(old), gd.DirTree(new))
    assert report["changed"] is True
    p = report["pilots"]["pdf/net"]
    assert p["status"] == "changed"
    assert p["symbols"]["added"] == ["A.Added"]
    assert p["symbols"]["removed"] == ["A.Removed"]
    assert p["symbols"]["changed"] == ["A.Changed"]
    assert (p["symbols"]["old_total"], p["symbols"]["new_total"]) == (3, 3)
    assert p["documentation"]["added_count"] == 1
    assert p["documentation"]["removed_count"] == 0
    assert p["documentation"]["added"] == ["Added doc page"]
    assert (
        p["documentation"]["old_total"],
        p["documentation"]["new_total"],
        p["documentation"]["delta"],
    ) == (1, 2, 1)
    assert p["examples"]["delta"] == 0 and p["examples"]["added_count"] == 0


def test_member_count_deltas(tmp_path: Path) -> None:
    old, new = two_trees(tmp_path)
    m = gd.diff_trees(gd.DirTree(old), gd.DirTree(new))["pilots"]["pdf/net"]["members"]
    # old: Keep 1 + Removed 2 + Changed 2 = 5; new: Keep 1 + Added 1 + Changed 3 = 5
    assert (m["old"], m["new"], m["delta"]) == (5, 5, 0)
    # per symbol present on both sides: Changed went 2 -> 3
    assert m["symbols_with_member_change"] == [{"delta": 1, "fqn": "A.Changed", "new": 3, "old": 2}]


def test_signature_line_changes_are_counted_with_examples(tmp_path: Path) -> None:
    old, new = two_trees(tmp_path)
    s = gd.diff_trees(gd.DirTree(old), gd.DirTree(new))["pilots"]["pdf/net"]["signature_lines"]
    # Changed: removed "C1(x: int) -> int", added "C1(x: long) -> long" and "C3() -> void"
    assert s["removed"] == 1 and s["added"] == 2 and s["changed"] == 3
    assert s["examples"] == [
        {
            "added": ["C1(x: long) -> long", "C3() -> void"],
            "fqn": "A.Changed",
            "removed": ["C1(x: int) -> int"],
        }
    ]


def test_signature_examples_are_limited_to_n_and_sorted(tmp_path: Path) -> None:
    old, new = tmp_path / "old", tmp_path / "new"
    write_scope(old, "pdf", "net", [sym(f"S.{n}", bullets=("m() -> int",)) for n in "cbad"])
    write_scope(new, "pdf", "net", [sym(f"S.{n}", bullets=("m() -> long",)) for n in "cbad"])
    s = gd.diff_trees(gd.DirTree(old), gd.DirTree(new), examples=2)["pilots"]["pdf/net"]["signature_lines"]
    assert s["changed"] == 8  # 4 symbols x (1 removed + 1 added), counted in full
    assert [e["fqn"] for e in s["examples"]] == ["S.a", "S.b"]


def test_source_commit_and_coverage_lines_alone_do_not_change_a_symbol(tmp_path: Path) -> None:
    old, new = tmp_path / "old", tmp_path / "new"
    write_scope(old, "pdf", "net", [sym("S.a", bullets=("m() -> int",), sha=SHA_A, cov=3)])
    write_scope(
        new,
        "pdf",
        "net",
        [sym("S.a", bullets=("m() -> int",), sha=SHA_B, cov=4)],
        version="20261009T000000000000Z-cccccccc",
    )
    p = gd.diff_trees(gd.DirTree(old), gd.DirTree(new))["pilots"]["pdf/net"]
    assert p["symbols"]["changed"] == []
    assert p["source_commit"] == {"changed": True, "new": SHA_B, "old": SHA_A}
    assert p["generation"]["changed"] is True
    assert p["generation"]["old"].endswith("20261001T000000000000Z-aaaaaaaa")
    assert p["generation"]["new"].endswith("20261009T000000000000Z-cccccccc")
    assert p["status"] == "changed"


def test_example_chunks_added_and_removed_are_reported_by_title(tmp_path: Path) -> None:
    old, new = tmp_path / "old", tmp_path / "new"
    write_scope(old, "pdf", "net", [example("Gone"), example("Same"), doc("D")])
    write_scope(new, "pdf", "net", [example("Same"), example("Fresh"), example("Fresh two"), doc("D")])
    e = gd.diff_trees(gd.DirTree(old), gd.DirTree(new))["pilots"]["pdf/net"]["examples"]
    assert (e["old_total"], e["new_total"], e["delta"]) == (2, 3, 1)
    assert e["removed"] == ["Gone"] and e["added"] == ["Fresh", "Fresh two"]
    assert e["added_count"] == 2 and e["removed_count"] == 1


def test_a_pilot_present_on_one_side_only(tmp_path: Path) -> None:
    old, new = tmp_path / "old", tmp_path / "new"
    write_scope(old, "pdf", "net", [sym("S.a")])
    write_scope(old, "gone", "go", [sym("G.a"), sym("G.b")])
    write_scope(new, "pdf", "net", [sym("S.a")])
    write_scope(new, "fresh", "rust", [sym("F.a")])
    report = gd.diff_trees(gd.DirTree(old), gd.DirTree(new))
    assert report["pilots"]["gone/go"]["status"] == "removed"
    assert report["pilots"]["gone/go"]["symbols"]["removed_count"] == 2
    assert report["pilots"]["fresh/rust"]["status"] == "added"
    assert report["pilots"]["fresh/rust"]["symbols"]["added"] == ["F.a"]
    assert report["pilots"]["pdf/net"]["status"] == "unchanged"
    assert report["changed"] is True


# --------------------------------------------------------------------------- ceilings and exit codes


def test_removed_symbols_ceiling(tmp_path: Path) -> None:
    old, new = two_trees(tmp_path)
    over = run_cli(old, new, "--max-removed-symbols", "0")
    assert over.returncode == 1
    report = json.loads(over.stdout)
    assert report["ceiling_violations"] == [
        {"actual": 1, "ceiling": 0, "kind": "max_removed_symbols", "pilot": "pdf/net"}
    ]
    within = run_cli(old, new, "--max-removed-symbols", "1")
    assert within.returncode == 0
    assert json.loads(within.stdout)["ceiling_violations"] == []


def test_signature_changes_ceiling(tmp_path: Path) -> None:
    old, new = two_trees(tmp_path)
    assert run_cli(old, new, "--max-signature-changes", "2").returncode == 1
    assert run_cli(old, new, "--max-signature-changes", "3").returncode == 0
    both = json.loads(run_cli(old, new, "--max-removed-symbols", "0", "--max-signature-changes", "0").stdout)
    assert [v["kind"] for v in both["ceiling_violations"]] == ["max_removed_symbols", "max_signature_changes"]


def test_no_ceiling_means_exit_zero_even_when_changed(tmp_path: Path) -> None:
    old, new = two_trees(tmp_path)
    res = run_cli(old, new)
    assert res.returncode == 0
    assert json.loads(res.stdout)["changed"] is True


def test_unusable_input_exits_two(tmp_path: Path) -> None:
    res = run_cli(tmp_path / "nope", tmp_path / "nope2")
    assert res.returncode == 2
    assert b"generation_diff" in res.stderr


def test_output_is_byte_identical_on_two_runs(tmp_path: Path) -> None:
    old, new = two_trees(tmp_path)
    first, second = run_cli(old, new), run_cli(old, new)
    assert first.returncode == 0 and first.stdout == second.stdout
    assert first.stdout.endswith(b"\n") and b"\r" not in first.stdout


def test_text_format_names_each_changed_pilot(tmp_path: Path) -> None:
    old, new = two_trees(tmp_path)
    res = run_cli(old, new, "--format", "text")
    assert res.returncode == 0
    text = res.stdout.decode("utf-8")
    assert "pdf/net" in text and "A.Removed" in text and "A.Added" in text
    unchanged = run_cli(old, old, "--format", "text").stdout.decode("utf-8")
    assert "no change" in unchanged


# --------------------------------------------------------------------------- git revisions


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.invalid",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        timeout=120,
    )


def test_git_revision_side_equals_the_same_content_on_disk(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    manifests = repo / "infra" / "demo-manifests"
    manifests.parent.mkdir(parents=True)
    _git(tmp_path, "init", "-q", str(repo))
    write_scope(manifests, "pdf", "net", BASE)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    write_scope(manifests, "pdf", "net", new_chunks(), version="20261002T000000000000Z-bbbbbbbb")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "reingest")

    via_git = gd.diff_trees(gd.GitTree("HEAD~1", repo=repo), gd.DirTree(manifests))
    old_dir = tmp_path / "old_dir"
    write_scope(old_dir, "pdf", "net", BASE)
    via_dirs = gd.diff_trees(gd.DirTree(old_dir), gd.DirTree(manifests))
    assert via_git == via_dirs
    assert via_git["pilots"]["pdf/net"]["symbols"]["removed"] == ["A.Removed"]

    both_git = gd.diff_trees(gd.GitTree("HEAD~1", repo=repo), gd.GitTree("HEAD", repo=repo))
    assert both_git == via_dirs

    res = run_cli("--git-rev", "HEAD~1", "--max-removed-symbols", "0", cwd=repo)
    # the CLI resolves the repository from the current directory and the default manifest root from the repo
    assert res.returncode == 1, res.stderr.decode("utf-8", "replace")
    assert json.loads(res.stdout)["pilots"]["pdf/net"]["symbols"]["removed"] == ["A.Removed"]


# --------------------------------------------------------------------------- the real committed tree


def test_real_tree_against_itself_is_unchanged() -> None:
    res = run_cli(
        DEMO_MANIFESTS, DEMO_MANIFESTS, "--max-removed-symbols", "0", "--max-signature-changes", "0"
    )
    assert res.returncode == 0, res.stderr.decode("utf-8", "replace")
    report: dict[str, Any] = json.loads(res.stdout)
    assert report["changed"] is False
    assert len(report["pilots"]) == sum(1 for p in DEMO_MANIFESTS.iterdir() if p.is_dir())
    assert {p["status"] for p in report["pilots"].values()} == {"unchanged"}


def test_real_tree_head_against_head_minus_one_runs_without_error() -> None:
    res = run_cli("--git-rev", "HEAD~1", cwd=REPO_ROOT)
    assert res.returncode == 0, res.stderr.decode("utf-8", "replace")
    report: dict[str, Any] = json.loads(res.stdout)
    assert report["pilots"]
    assert set(report) >= {"changed", "pilots", "totals", "ceiling_violations"}
    in_process = gd.diff_trees(gd.GitTree("HEAD~1", repo=REPO_ROOT), gd.GitTree("HEAD", repo=REPO_ROOT))
    assert in_process["pilots"].keys() == report["pilots"].keys()
