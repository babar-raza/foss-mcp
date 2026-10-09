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

G2/TC-291 (re-pinned again, same commit, after two more real fixes landed on main): TC-281 made
``_flatten_inheritance()`` actually run for Python-sourced pilots, but this pilot's own real data
exposed a second, independent defect in that function itself - EVERY one of its 300 kept types is
one half of a facade/real-module pair sharing one bare name (a package-level re-export shell with
``methods: []`` and the real defining-module entry with the real methods), and
``_flatten_inheritance()``'s short-name index used to let the empty shell permanently win that
slot (its shorter ``class_import`` always sorts first). Confirmed directly: a real re-run against
TC-281 alone still carried zero ``inherited_from`` tags anywhere in the full 1023-type corpus for
this pilot - a complete no-op, not a truncation artifact. TC-293 fixed the index itself (prefer a
structured-data-bearing candidate over an empty shell on a collision); this real re-run, now on
top of TC-293, carries 449 ``inherited_from`` tags across the reduced 300 kept types. "Shape" (this
pilot's own live-e2e anchor, ``tests/e2e/test_slides_python_live_content.py``) is unaffected: same
27 own methods, same real ``bases``, now plus 3 genuinely inherited ones.
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


def test_shapes_own_real_method_is_untouched_by_flattening() -> None:
    """``Shape`` (``aspose.slides_foss.Shape.Shape``) is this pilot's own live-e2e anchor
    (``tests/e2e/test_slides_python_live_content.py``'s ``REAL_SYMBOL_FQN``/``REAL_METHOD_FRAGMENT``):
    it must keep its real, locally-declared ``presentation`` method exactly as TC-277 left it -
    child definitions take precedence over an inherited one of the same name, per
    ``_flatten_inheritance()``'s own documented contract - even though ``IPresentationComponent``
    (one of Shape's own six real bases) also declares a ``presentation`` method.
    """
    data = _load_fixture()
    shape = next(
        entry for entry in data["types"] if entry["class_import"] == "aspose.slides_foss.Shape.Shape"
    )
    assert shape["bases"] == [
        "StrictAttributes",
        "IShape",
        "ISlideComponent",
        "IPresentationComponent",
        "IHyperlinkContainer",
        "ABC",
    ]
    presentation = next(m for m in shape["methods"] if m["name"] == "presentation")
    assert presentation["return_type"] == "IPresentation"
    assert "inherited_from" not in presentation


def test_a_real_multi_level_inherited_member_carries_the_correct_inherited_from_tag() -> None:
    """G2/TC-291's own closing proof, using the strongest real multi-level chain in this corpus
    (per the card's own instruction to prefer one over a direct-parent case): ``IChart``'s real
    class_import (``aspose.slides_foss.charts.IChart.IChart``) declares bases
    ``["IGraphicalObject", "IFormattedTextContainer", "IChartComponent", "ABC"]`` - none of them
    ``IPresentationComponent``, ``IShape``, or ``ISlideComponent`` - yet its real ``presentation``
    method (the same method name/return-type Shape itself declares directly, see the test above)
    is tagged ``inherited_from: "aspose.slides_foss.IPresentationComponent.IPresentationComponent"``:
    the TRUE original declaring ancestor, reached only transitively (confirmed directly against
    this fixture's own committed content: ``IGraphicalObject``'s real bases are
    ``["IShape", "ABC"]``, and ``IShape``'s real bases include ``IPresentationComponent``
    directly) - never ``IGraphicalObject`` or ``IShape``, the two intermediates IChart actually
    passes through on the way there. This is real, run-verified proof that TC-293's fix to
    ``_flatten_inheritance()``'s short-name index - preferring a structured-data-bearing
    candidate over an empty package-re-export shell on a collision - reaches this pilot's real,
    committed fixture, not just TC-293's own scratch-path verification against 3d/python and
    words/python.
    """
    data = _load_fixture()
    ichart = next(
        entry for entry in data["types"] if entry["class_import"] == "aspose.slides_foss.charts.IChart.IChart"
    )
    assert ichart["bases"] == ["IGraphicalObject", "IFormattedTextContainer", "IChartComponent", "ABC"]
    presentation = next(m for m in ichart["methods"] if m["name"] == "presentation")
    assert (
        presentation["inherited_from"] == "aspose.slides_foss.IPresentationComponent.IPresentationComponent"
    )
