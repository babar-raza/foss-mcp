"""Offline tests against the committed pilot export bundle - no network, no `gh` call.

``gatectl`` verifies TC-013 with the network proxied to a dead port (``network: false``); the
live export itself (``export_subtree``, which shells out to ``gh api``) is exercised only by
hand, with real credentialed access, to produce the fixture committed at
``tests/fixtures/furnished/pdf_net/``. Everything here reads that fixture, or a tampered copy
of it made locally in ``tmp_path`` - never the network.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from scripts.pilot_manual_export import NON_PRODUCTION_BANNER, print_banner, verify_bundle

FIXTURE_BUNDLE = Path(__file__).parent / "fixtures" / "furnished" / "pdf_net"
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def _copy_bundle(tmp_path: Path) -> Path:
    destination = tmp_path / "bundle"
    shutil.copytree(FIXTURE_BUNDLE, destination)
    return destination


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
