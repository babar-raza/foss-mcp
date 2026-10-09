"""Offline tests against the committed words/python fixture - no network, no live clone.

``gatectl`` verifies this card with the network proxied to a dead port (TC-150 sets
``network: false``), so nothing here may reach out; everything is read from the fixture
``run_extraction.py`` already produced and committed at
``tests/fixtures/words_python/api_surface.json``.

Like slides/python (and unlike pdf/net, a tree-sitter/C# reader), this fixture was produced
by the independent pure-``ast`` reader (``foss_mcp.extraction.tree_sitter_engine.python_surface``,
adapted in ``run_extraction._python_types_from_surface``): its ``language`` field reads
literally ``"python"`` and there is no tree-sitter grammar name here at all. Unlike
slides/python's own reduced fixture, this repository's alphabetically-truncated top 300
entries DO include real top-level ``"function"`` entries alongside ``"class"``/``"enum"`` -
observed directly from this card's own live run, not assumed from the slides precedent.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "words_python" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "words" / "python.yaml"

_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_fixture_parses_and_has_the_expected_shape() -> None:
    data = _load_fixture()
    assert isinstance(data, dict)
    assert isinstance(data["types"], list)
    assert data["language"] == "python"


def test_the_fixture_records_the_exact_source_commit_and_repository() -> None:
    data = _load_fixture()
    assert _COMMIT_SHA.match(data["source_commit"]), data["source_commit"]
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert data["source_repository"] == manifest["repository"]


def test_the_fixture_is_non_empty() -> None:
    data = _load_fixture()
    assert len(data["types"]) > 0
    assert data["reduced_type_count"] == len(data["types"])
    assert data["type_count"] >= data["reduced_type_count"]


def test_the_fixture_contains_real_python_type_names() -> None:
    data = _load_fixture()
    names = {entry["name"] for entry in data["types"]}
    # Real Aspose.Words-FOSS-for-Python classes/enums, not placeholders - would not
    # survive an empty or synthetic fixture.
    assert {"Document", "NodeType", "SaveFormat", "LoadFormat"} <= names
    assert all(entry["class_import"].startswith("aspose.words_foss") for entry in data["types"])
    assert all(entry["file"].endswith(".py") for entry in data["types"])


def test_every_entry_is_a_real_python_surface_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    # Observed directly from the live pure-ast reader's output for this repository - not
    # a tree-sitter grammar's node-type vocabulary, and not guessed. Unlike slides/python's
    # reduced fixture, this repository's truncated top 300 entries do include top-level
    # functions.
    assert kinds == {"class", "enum", "function"}


def test_the_highest_centrality_type_survived_the_truncation() -> None:
    # Measured directly from this card's own live run (TC-270): over the full real 391-type
    # set, ``aspose.words_foss.md_import.Block`` is referenced by other types' bases/
    # return_type/params more often than any other type (score 50 - computed the same way
    # run_extraction._centrality_scores does, by whole-word bare-name reference count), ahead
    # of a second, differently-qualified ``Block`` (``...md_import.blocks.Block``, score 49)
    # and ``Document`` (score 13). TC-252's centrality-ranked reduce_fixture() is only a
    # genuine fix for this pilot if that real top type actually survives the cut to 300 -
    # this is this pilot's own concrete proof of that, since no audit already names one.
    data = _load_fixture()
    assert any(
        entry["name"] == "Block" and entry["class_import"] == "aspose.words_foss.md_import.Block"
        for entry in data["types"]
    )
