"""Offline tests against the committed tex/python fixture - no network, no live clone.

``gatectl`` verifies this card with the network proxied to a dead port (this card's own
``network: false``), so nothing here may reach out; everything is read from the fixture
``run_extraction.py`` already produced and committed at
``tests/fixtures/tex_python/api_surface.json``.

Like pdf/python and slides/python, this fixture was produced by the independent pure-``ast``
reader (``foss_mcp.extraction.tree_sitter_engine.python_surface``, adapted in
``run_extraction._python_types_from_surface``): its ``language`` field reads literally
``"python"`` and there is no tree-sitter grammar name here at all.

This repository's real public surface is almost entirely UNRESOLVED, not merely small: at the
pinned commit, ``src/aspose_tex/presentation/__init__.py`` has a genuine Python syntax error
("expected an indented block after function definition on line 107") at line 108, which the
pure-``ast`` reader cannot parse past. Every name the top-level ``aspose_tex`` package
re-exports from that broken ``presentation`` submodule (``TeXJob``, ``OutputDevice``,
``PdfDevice``, ``SvgDevice``, ``DviDevice``, ``OutputFormat``, ``create_input_source``) and
three more re-exported from ``aspose_tex._input.reader`` end up in the fixture's own
``unresolved`` list instead of ``types`` - the reader surfaces the failure rather than
silently dropping it, exactly as ``python_surface.PublicSurface.unresolved`` is designed to
do. This is a genuine, reproducible defect in the upstream repository at this commit, not an
engine bug and not a package-root detection failure, so it is reported here rather than
worked around. The only module that parsed cleanly is ``src/aspose_tex/exceptions.py``, whose
four exception classes are exactly what this fixture's ``types`` contains - a real,
non-empty, non-fabricated extraction result, just a small one.

RECONFIRMED LIVE on 2026-10-10 (TC-324, sibling of TC-318 through TC-323): a real
``run_extraction.py config/products/tex/python.yaml`` run against a fresh shallow clone of
``aspose-tex-foss/Aspose.TeX-FOSS-for-Python`` still resolved HEAD to the exact same pinned
commit ``181015d2eee3e19f8af3d84e4a932ac84ad47c7c``, and a separate manual clone of that same
commit was read directly (not assumed from a prior session) to confirm
``src/aspose_tex/presentation/__init__.py`` still has the identical indentation defect at
lines 107-108 - ``ast.parse`` on the live file reproduces the exact same
``SyntaxError: expected an indented block after function definition on line 107`` at line
108 recorded in ``unresolved`` below, and the same 12-entry ``unresolved`` list and the same
4 resolvable exception-class types came back unchanged. The one real, honest change this
re-run surfaced: TC-265/TC-274/TC-281/TC-293/TC-294 (the Python ``bases``/inheritance-
flattening work that landed on main after this fixture was first generated) now populate
each exception class's real ``bases`` from ``src/aspose_tex/exceptions.py`` - previously
empty ``[]`` on all four. There are still zero ``inherited_from`` tags, because none of these
four classes declare any method of their own to inherit one onto (every ``methods`` list is
and remains empty) - this is the real, honest shape of a four-class exception hierarchy with
no method bodies, not a flattening gap.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "tex_python" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "tex" / "python.yaml"

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
    # Real Aspose.TeX-FOSS-for-Python exception classes, not placeholders - would not survive
    # an empty or synthetic fixture. The real repository's larger public surface (TeXJob,
    # OutputDevice, PdfDevice, SvgDevice, DviDevice, ...) is unresolved, not extracted - see
    # this module's docstring - so it is deliberately not asserted on here.
    assert {"AsposeTeXError", "EngineError", "FontError", "InputError"} <= names
    assert all(entry["class_import"].startswith("aspose_tex") for entry in data["types"])
    assert all(entry["file"].endswith(".py") for entry in data["types"])


def test_the_real_exception_hierarchy_bases_are_populated_and_methods_are_empty() -> None:
    data = _load_fixture()
    by_name = {entry["name"]: entry for entry in data["types"]}
    # Read directly from src/aspose_tex/exceptions.py at the pinned commit on 2026-10-10 (see
    # this module's docstring): AsposeTeXError(Exception), and the other three each subclass
    # AsposeTeXError directly. TC-265/TC-274/TC-281/TC-293/TC-294 are what makes this
    # fixture's own `bases` non-empty now - before that work landed, every entry here had
    # `bases == []` even though the real source always declared one.
    assert by_name["AsposeTeXError"]["bases"] == ["Exception"]
    assert by_name["EngineError"]["bases"] == ["AsposeTeXError"]
    assert by_name["FontError"]["bases"] == ["AsposeTeXError"]
    assert by_name["InputError"]["bases"] == ["AsposeTeXError"]
    # None of these four classes defines a single method of its own (each body is only a
    # docstring), so there is nothing for _flatten_inheritance to tag with inherited_from -
    # zero inherited_from tags here is the honest result of an empty-methods hierarchy, not a
    # sign the flattening fix is missing, unlike the larger pilots in TC-281/TC-293's own
    # negative controls.
    for entry in data["types"]:
        assert entry["methods"] == []


def test_every_entry_is_a_real_python_surface_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    # Observed directly from the live pure-ast reader's output for this repository, not
    # guessed: the only module that parsed cleanly contributes nothing but exception classes,
    # a subset of the reader's full {"class", "enum", "function"} vocabulary.
    assert kinds == {"class"}
    assert kinds <= {"class", "enum", "function"}
