"""Offline tests against the committed slides/python fixture - no network, no live clone.

``gatectl`` verifies this card with the network proxied to a dead port (TC-025 sets
``network: false``), so nothing here may reach out; everything is read from the fixture
``run_extraction.py`` already produced and committed at
``tests/fixtures/slides_python/api_surface.json``.

Unlike pdf/net (a tree-sitter/C# reader), this fixture was produced by the independent
pure-``ast`` reader (``foss_mcp.extraction.tree_sitter_engine.python_surface``, adapted in
``run_extraction._python_types_from_surface``): its ``language`` field reads literally
``"python"`` and its observed ``kind`` vocabulary among the reduced 300 entries is only
``{"class", "enum"}`` - no top-level ``"function"`` entry survived the centrality-ranked
(TC-252/TC-265) truncation, and there is no tree-sitter grammar name here at all.

G2/TC-277: regenerated for real against the same pinned commit (``4e63447ba79d1c27a5192844847d9f872c5b92ad``)
now that TC-265 makes every Python-sourced entry's ``bases``/``return_type``/``param_types`` real
(TC-264 found every Python type scored zero centrality before that fix - a complete no-op).
Centrality is genuinely non-degenerate now: scores range 0-115 over the full 1023-type corpus
before truncation, with ``IPresentationComponent`` the single highest-scoring type (115) - and,
since ``reduce_fixture()`` keeps the full pre-truncation sort order, it is this fixture's own
first entry.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "slides_python" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "slides" / "python.yaml"

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
    # Real Aspose.Slides-FOSS-for-Python classes/enums, not placeholders - would not
    # survive an empty or synthetic fixture. Observed directly in this centrality-ranked
    # (TC-252/TC-265) regeneration: this library's own public surface types almost everything
    # against its interfaces (IPresentationComponent, ISlideComponent, ...), not against
    # concrete implementation classes, so the top of the ranking is interface-heavy rather than
    # the concrete classes ("AutoShape", "Presentation", ...) a reader would expect - neither of
    # those two survives this real cut, for the same real reason (see module docstring).
    assert {"IPresentationComponent", "ISlideComponent", "NullableBool"} <= names
    assert all(entry["class_import"].startswith("aspose.slides_foss") for entry in data["types"])
    assert all(entry["file"].endswith(".py") for entry in data["types"])


def test_every_entry_is_a_real_python_surface_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    # Observed directly from the live pure-ast reader's output for this repository - not
    # a tree-sitter grammar's node-type vocabulary, and not guessed.
    assert kinds == {"class", "enum"}


def test_the_highest_centrality_type_survives_the_cut() -> None:
    """This pilot's own proof TC-265's fix is real and working: the single highest-scoring type
    over the FULL 1023-type corpus (``IPresentationComponent``, centrality 115 - every other type
    in the corpus references it by bare name, far more than any other type) is kept, and -
    because ``reduce_fixture()`` keeps its own full pre-truncation sort order rather than
    re-sorting afterward - is this fixture's own first entry. "Presentation" itself cannot serve
    this role for this pilot (it legitimately scores 0 and is absent - see module docstring), so
    this is the real, concrete stand-in proof of fix for slides/python.
    """
    data = _load_fixture()
    assert data["types"][0]["name"] == "IPresentationComponent"
    assert data["types"][0]["class_import"] == "aspose.slides_foss.IPresentationComponent"
    assert "Presentation" not in {entry["name"] for entry in data["types"]}
