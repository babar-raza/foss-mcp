"""Offline tests against the committed pilot export bundle - no network, no `gh` call.

``gatectl`` verifies TC-013 with the network proxied to a dead port (``network: false``); the
live export itself (``export_subtree``, which shells out to ``gh api``) is exercised only by
hand, with real credentialed access, to produce the fixture committed at
``tests/fixtures/furnished/pdf_net/``. Everything here reads that fixture, or a tampered copy
of it made locally in ``tmp_path`` - never the network.

TC-028 adds the local-clone export path (``export_local_subtree``, backed by read-only git
plumbing instead of ``gh api``). Its offline tests below build a SYNTHETIC git repo fresh
inside a pytest ``tmp_path`` (git init, write files, commit) - never a hardcoded dependency on
any specific machine's real local clone, since that path will not exist on every machine that
runs this suite. The one real, committed bundle this card also produces -
``tests/fixtures/furnished/slides_python/``, exported by hand from the real local clone of
``Aspose/aspose.org`` - is asserted on separately, by reading the committed fixture only.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from scripts.pilot_manual_export import (
    NON_PRODUCTION_BANNER,
    export_local_subtree,
    print_banner,
    verify_bundle,
)

FIXTURE_BUNDLE = Path(__file__).parent / "fixtures" / "furnished" / "pdf_net"
SLIDES_PYTHON_BUNDLE = Path(__file__).parent / "fixtures" / "furnished" / "slides_python"
CELLS_RUST_BUNDLE = Path(__file__).parent / "fixtures" / "furnished" / "cells_rust"
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def _copy_bundle(tmp_path: Path) -> Path:
    destination = tmp_path / "bundle"
    shutil.copytree(FIXTURE_BUNDLE, destination)
    return destination


def _run_git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _make_synthetic_clone(tmp_path: Path) -> tuple[Path, str]:
    """A fresh, real git repo in *tmp_path* with a committed subtree - never the real clone.

    Returns the clone's path and the exact commit SHA HEAD resolves to, so tests can assert
    the local-clone export path pins to it precisely, the same way the real local clone does.
    """
    clone = tmp_path / "synthetic_clone"
    clone.mkdir()
    _run_git("init", cwd=clone)
    _run_git("config", "user.email", "test@example.invalid", cwd=clone)
    _run_git("config", "user.name", "Test", cwd=clone)

    subtree_dir = clone / "content" / "products.aspose.org" / "en" / "widgets" / "python"
    subtree_dir.mkdir(parents=True)
    (subtree_dir / "_index.md").write_text(
        "---\ntitle: Widgets Python\n---\nReal content.\n", encoding="utf-8"
    )
    (subtree_dir / "nested").mkdir()
    (subtree_dir / "nested" / "page.md").write_text("nested page\n", encoding="utf-8")
    # A binary-ish file outside plain UTF-8 text, to exercise the raw-bytes blob fetch.
    (subtree_dir / "banner.bin").write_bytes(bytes(range(256)))
    # A file outside the subtree - must never be exported.
    (clone / "content" / "unrelated.md").write_text("not in scope\n", encoding="utf-8")

    _run_git("add", "-A", cwd=clone)
    _run_git("commit", "-m", "synthetic commit", cwd=clone)
    result = subprocess.run(
        ["git", "-C", str(clone), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    )
    return clone, result.stdout.strip()


def test_the_banner_announces_non_production(capsys) -> None:
    print_banner()
    captured = capsys.readouterr()
    assert "NON-PRODUCTION" in captured.out
    assert "NON-PRODUCTION" in NON_PRODUCTION_BANNER


def test_the_committed_bundle_verifies_clean() -> None:
    assert verify_bundle(FIXTURE_BUNDLE) == []


def test_the_manifest_records_a_real_commit_for_a_real_repository() -> None:
    manifest = json.loads((FIXTURE_BUNDLE / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_repository"] == "Aspose/aspose.org"
    assert _COMMIT_SHA.match(manifest["source_commit"])
    assert manifest["files"], "the bundle exported nothing"
    for entry in manifest["files"]:
        assert _COMMIT_SHA.match(entry["source_commit"])


def test_a_tampered_page_fails_verification(tmp_path: Path) -> None:
    bundle = _copy_bundle(tmp_path)
    page = bundle / "pages" / "_index.md"
    page.write_text(page.read_text(encoding="utf-8") + "\ntampered\n", encoding="utf-8")
    problems = verify_bundle(bundle)
    assert any("checksum mismatch" in problem for problem in problems)


def test_a_page_missing_its_source_commit_is_rejected(tmp_path: Path) -> None:
    bundle = _copy_bundle(tmp_path)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["files"][0]["source_commit"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    problems = verify_bundle(bundle)
    assert any("missing source_commit" in problem for problem in problems)


def test_a_missing_page_file_is_also_rejected(tmp_path: Path) -> None:
    bundle = _copy_bundle(tmp_path)
    (bundle / "pages" / "_index.md").unlink()
    problems = verify_bundle(bundle)
    assert any("file missing" in problem for problem in problems)


# --- TC-028: local-clone export path (read-only git plumbing, no `gh`, no network) ---


def test_local_clone_export_produces_a_clean_verifiable_bundle(tmp_path: Path) -> None:
    clone, expected_commit = _make_synthetic_clone(tmp_path)
    output_dir = tmp_path / "output"

    manifest = export_local_subtree(
        clone_path=clone,
        repository="Aspose/aspose.org",
        subtree="content/products.aspose.org/en/widgets/python",
        ref="HEAD",
        output_dir=output_dir,
    )

    assert manifest["source_commit"] == expected_commit
    assert manifest["source_repository"] == "Aspose/aspose.org"
    assert manifest["source_subtree"] == "content/products.aspose.org/en/widgets/python"
    assert manifest["banner"] == NON_PRODUCTION_BANNER
    assert verify_bundle(output_dir) == []


def test_local_clone_export_writes_only_files_inside_the_subtree(tmp_path: Path) -> None:
    clone, _ = _make_synthetic_clone(tmp_path)
    output_dir = tmp_path / "output"

    manifest = export_local_subtree(
        clone_path=clone,
        repository="Aspose/aspose.org",
        subtree="content/products.aspose.org/en/widgets/python",
        ref="HEAD",
        output_dir=output_dir,
    )

    paths = {entry["path"] for entry in manifest["files"]}
    assert paths == {"_index.md", "nested/page.md", "banner.bin"}
    assert not (output_dir / "pages" / "unrelated.md").exists()


def test_local_clone_export_preserves_binary_content_byte_for_byte(tmp_path: Path) -> None:
    clone, _ = _make_synthetic_clone(tmp_path)
    output_dir = tmp_path / "output"

    export_local_subtree(
        clone_path=clone,
        repository="Aspose/aspose.org",
        subtree="content/products.aspose.org/en/widgets/python",
        ref="HEAD",
        output_dir=output_dir,
    )

    exported = (output_dir / "pages" / "banner.bin").read_bytes()
    assert exported == bytes(range(256))


def test_local_clone_export_records_a_real_commit_per_file(tmp_path: Path) -> None:
    clone, expected_commit = _make_synthetic_clone(tmp_path)
    manifest = export_local_subtree(
        clone_path=clone,
        repository="Aspose/aspose.org",
        subtree="content/products.aspose.org/en/widgets/python",
        ref="HEAD",
        output_dir=tmp_path / "output",
    )
    assert manifest["files"], "expected at least one exported file"
    for entry in manifest["files"]:
        assert _COMMIT_SHA.match(entry["source_commit"])
        assert entry["source_commit"] == expected_commit


def test_resolve_local_commit_and_list_local_tree_match_real_git_plumbing(tmp_path: Path) -> None:
    from scripts.pilot_manual_export import _list_local_tree, resolve_local_commit

    clone, expected_commit = _make_synthetic_clone(tmp_path)
    resolved = resolve_local_commit(clone, "HEAD")
    assert resolved == expected_commit
    assert _COMMIT_SHA.match(resolved)

    entries = _list_local_tree(clone, resolved, "content/products.aspose.org/en/widgets/python")
    paths = {e["path"] for e in entries}
    assert paths == {
        "content/products.aspose.org/en/widgets/python/_index.md",
        "content/products.aspose.org/en/widgets/python/nested/page.md",
        "content/products.aspose.org/en/widgets/python/banner.bin",
    }
    # Every entry is a real 40-hex blob SHA, not a directory (tree) entry.
    for entry in entries:
        assert _COMMIT_SHA.match(entry["sha"])


def test_local_clone_export_is_pinned_and_reproducible_across_two_runs(tmp_path: Path) -> None:
    clone, expected_commit = _make_synthetic_clone(tmp_path)
    first = export_local_subtree(
        clone_path=clone,
        repository="Aspose/aspose.org",
        subtree="content/products.aspose.org/en/widgets/python",
        ref="HEAD",
        output_dir=tmp_path / "run1",
    )
    second = export_local_subtree(
        clone_path=clone,
        repository="Aspose/aspose.org",
        subtree="content/products.aspose.org/en/widgets/python",
        ref="HEAD",
        output_dir=tmp_path / "run2",
    )
    assert first["source_commit"] == second["source_commit"] == expected_commit
    assert sorted(e["sha256"] for e in first["files"]) == sorted(e["sha256"] for e in second["files"])


def test_a_tampered_local_clone_bundle_fails_verification(tmp_path: Path) -> None:
    clone, _ = _make_synthetic_clone(tmp_path)
    output_dir = tmp_path / "output"
    export_local_subtree(
        clone_path=clone,
        repository="Aspose/aspose.org",
        subtree="content/products.aspose.org/en/widgets/python",
        ref="HEAD",
        output_dir=output_dir,
    )

    page = output_dir / "pages" / "_index.md"
    page.write_text(page.read_text(encoding="utf-8") + "\ntampered\n", encoding="utf-8")

    problems = verify_bundle(output_dir)
    assert any("checksum mismatch" in problem for problem in problems)


def test_local_clone_working_tree_is_never_written_to(tmp_path: Path) -> None:
    """Exporting must be read-only against the clone: no new/changed files in its working tree."""
    clone, _ = _make_synthetic_clone(tmp_path)
    before = subprocess.run(
        ["git", "-C", str(clone), "status", "--porcelain"], check=True, capture_output=True, text=True
    ).stdout

    export_local_subtree(
        clone_path=clone,
        repository="Aspose/aspose.org",
        subtree="content/products.aspose.org/en/widgets/python",
        ref="HEAD",
        output_dir=tmp_path / "output",
    )

    after = subprocess.run(
        ["git", "-C", str(clone), "status", "--porcelain"], check=True, capture_output=True, text=True
    ).stdout
    assert before == after == ""


# --- TC-028: the real, committed slides/python bundle exported from Aspose/aspose.org ---


def test_the_committed_slides_python_bundle_is_real_and_verifies_clean() -> None:
    manifest = json.loads((SLIDES_PYTHON_BUNDLE / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_repository"] == "Aspose/aspose.org"
    assert manifest["source_subtree"] == "content/products.aspose.org/en/slides/python"
    assert manifest["files"], "the slides/python bundle exported nothing"
    for entry in manifest["files"]:
        assert _COMMIT_SHA.match(entry["source_commit"])
    assert _COMMIT_SHA.match(manifest["source_commit"])
    assert verify_bundle(SLIDES_PYTHON_BUNDLE) == []


# --- TC-034: the real, committed cells/rust bundle exported from Aspose/aspose.org ---


def test_the_committed_cells_rust_bundle_is_real_and_verifies_clean() -> None:
    manifest = json.loads((CELLS_RUST_BUNDLE / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_repository"] == "Aspose/aspose.org"
    assert manifest["source_subtree"] == "content/products.aspose.org/en/cells/rust"
    assert manifest["files"], "the cells/rust bundle exported nothing"
    for entry in manifest["files"]:
        assert _COMMIT_SHA.match(entry["source_commit"])
    assert _COMMIT_SHA.match(manifest["source_commit"])
    assert verify_bundle(CELLS_RUST_BUNDLE) == []
