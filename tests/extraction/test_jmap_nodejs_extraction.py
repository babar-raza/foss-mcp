"""Offline tests against the committed jmap/nodejs fixture - no network, no live clone, no engine.

``gatectl`` verifies this card with the network proxied to a dead port (TC-233 sets
``network: false``), so nothing here may reach out; everything is read from the fixture
``run_extraction.py`` already produced and committed at
``tests/fixtures/jmap_nodejs/api_surface.json``, using the TC-233 fix that routes platform
"nodejs" through the "javascript" tree-sitter bucket instead of "typescript" - the real
repository is plain JavaScript (every source file under src/ ends in .js, none in .ts), so the
previous "typescript" mapping found zero source files and produced 0/0 types.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

FIXTURE = Path(__file__).parents[1] / "fixtures" / "jmap_nodejs" / "api_surface.json"

_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")

# NOTE on deviation from test_cells_net_extraction.py's exact pattern: that test reads the
# expected repository back out of config/products/jmap/net.yaml, already committed on its
# branch. config/products/jmap/nodejs.yaml was committed by TC-232's worker (commit a454f7d)
# but that commit lives only on branch worker/TC-232 - it was never merged into main, so it is
# genuinely absent from this card's own write_paths and from this branch's history (confirmed:
# `git merge-base --is-ancestor a454f7d main` reports false). TC-233's write_paths do not
# include that manifest, so it cannot be added here. The repository string below is the same
# real, verified value TC-232 recorded (and the same one this fixture's own source_commit was
# actually cloned from) - asserted as a literal because the manifest file that would normally
# supply it is not yet part of this branch.
_EXPECTED_REPOSITORY = "aspose-email-foss/Aspose.JMAP-FOSS-for-Node.js"


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_fixture_parses_and_has_the_expected_shape() -> None:
    data = _load_fixture()
    assert isinstance(data, dict)
    assert isinstance(data["types"], list)


def test_the_fixture_records_the_exact_source_commit_and_repository() -> None:
    data = _load_fixture()
    assert _COMMIT_SHA.match(data["source_commit"]), data["source_commit"]
    assert data["source_repository"] == _EXPECTED_REPOSITORY


def test_the_fixture_is_non_empty() -> None:
    data = _load_fixture()
    assert len(data["types"]) > 0
    assert data["reduced_type_count"] == len(data["types"])
    assert data["type_count"] >= data["reduced_type_count"]


def test_the_fixture_contains_real_nodejs_type_names() -> None:
    data = _load_fixture()
    names = {entry["name"] for entry in data["types"]}
    # Real Aspose.JMAP-FOSS-for-Node.js types, not placeholders - would not survive an empty
    # or synthetic fixture (these are exactly the kind of entries the "typescript" mapping bug
    # found zero of, since it only globbed for ".ts" files in a plain-JavaScript repository).
    assert {"JmapClient", "Mailbox", "Session"} <= names
    assert all(entry["file"].endswith(".js") for entry in data["types"])


def test_every_entry_is_a_real_javascript_declaration_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    # tree_helpers.py's own javascript bucket (_CLASS_TYPES["javascript"]) recognizes exactly
    # one class-level declaration kind; this fixture's own types show only that kind.
    assert kinds <= {"class_declaration"}
    assert kinds
