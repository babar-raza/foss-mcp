"""Offline tests against the committed tex/python fixture - no network, no live clone.

``gatectl`` verifies this card with the network proxied to a dead port (this card's own
``network: false``), so nothing here may reach out; everything is read from the fixture
``run_extraction.py`` already produced and committed at
``tests/fixtures/tex_python/api_surface.json``.

Like pdf/python and slides/python, this fixture was produced by the independent pure-``ast``
reader (``foss_mcp.extraction.tree_sitter_engine.python_surface``, adapted in
``run_extraction._python_types_from_surface``): its ``language`` field reads literally
``"python"`` and there is no tree-sitter grammar name here at all.

TC-555: this repository's whole public surface is now indexed. At the pinned commit two modules,
``src/aspose_tex/_input/reader.py`` and ``src/aspose_tex/presentation/__init__.py``, are not valid
Python AS WRITTEN: they lost their indentation (every indented line has exactly one leading space, a
``def`` is followed by its docstring at the same column), and ``ast.parse`` refuses them ("expected an
indented block after function definition on line 52" and "... on line 107"). The reader used to drop
both modules, so only the four exception classes were indexed (4 of the 15 public names) and
``TeXJob``, ``TeXOptions``, ``OutputFormat``, the three devices, the input sources and
``create_input_source`` - everything the README builds on - were absent. The declarations themselves
are intact, so ``python_surface._parse_repairing_collapsed_indentation`` now closes every
compound-statement header whose body is missing with `` pass`` and parses again: reader.py needs 42
repairs and presentation/__init__.py 36. Function bodies are lost (they are not API), signatures,
class fields and decorators are kept. The repair is disclosed, never silent: the two
``repaired-syntax`` entries are the only entries of ``unresolved``.

RECONFIRMED LIVE on 2026-10-10 (TC-324) and re-extracted on 2026-10-17 (TC-555): a fresh clone of
``aspose-tex-foss/Aspose.TeX-FOSS-for-Python`` resolves to the pinned commit
``181015d2eee3e19f8af3d84e4a932ac84ad47c7c`` and still has the identical indentation defect. The
fixture is a version-5 extraction (``tests/extraction/test_python_fixture_gaps.py``). The 15 public
names are ``TeXJob``, ``TeXOptions``, ``OutputFormat``, ``PdfDevice``, ``DviDevice``, ``SvgDevice``,
``OutputDevice``, ``InputSource``, ``FileInputSource``, ``StringInputSource``, ``create_input_source``
and the four exception classes ``AsposeTeXError``, ``EngineError``, ``FontError``, ``InputError``.
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


PUBLIC_NAMES = {
    "TeXJob",
    "TeXOptions",
    "OutputFormat",
    "PdfDevice",
    "DviDevice",
    "SvgDevice",
    "OutputDevice",
    "InputSource",
    "FileInputSource",
    "StringInputSource",
    "create_input_source",
    "AsposeTeXError",
    "EngineError",
    "FontError",
    "InputError",
}
EXCEPTIONS = {"AsposeTeXError", "EngineError", "FontError", "InputError"}


def _by_import() -> dict[str, dict]:
    return {entry["class_import"]: entry for entry in _load_fixture()["types"]}


def test_the_fixture_contains_real_python_type_names() -> None:
    data = _load_fixture()
    names = {entry["name"] for entry in data["types"]}
    # TC-555: 4 of the 15 public names -> all 15. The README's own names (TeXJob, TeXOptions, the
    # devices, the input sources) are no longer lost with the two modules that did not parse.
    assert PUBLIC_NAMES <= names
    assert all(entry["class_import"].startswith("aspose_tex") for entry in data["types"])
    assert all(entry["file"].endswith(".py") for entry in data["types"])


def test_the_fixture_counts_the_functions_and_the_types_separately() -> None:
    data = _load_fixture()
    functions = [entry for entry in data["types"] if entry["kind"] == "function"]
    assert {entry["name"] for entry in functions} == {"create_input_source"}
    assert data["symbol_counts"]["functions"] == len(functions) > 0
    assert data["symbol_counts"]["types"] == len(data["types"]) - len(functions)
    # TC-555: reduced == total, nothing was cut: the whole surface is in the fixture.
    assert data["reduced_type_count"] == data["type_count"] == len(data["types"])
    assert data["truncated"] is False


def test_the_real_exception_hierarchy_bases_are_populated_and_methods_are_empty() -> None:
    by_name = {entry["name"]: entry for entry in _load_fixture()["types"] if entry["name"] in EXCEPTIONS}
    # Read directly from src/aspose_tex/exceptions.py at the pinned commit on 2026-10-10 (see
    # this module's docstring): AsposeTeXError(Exception), and the other three each subclass
    # AsposeTeXError directly. TC-265/TC-274/TC-281/TC-293/TC-294 are what makes this
    # fixture's own `bases` non-empty.
    assert by_name["AsposeTeXError"]["bases"] == ["Exception"]
    assert by_name["EngineError"]["bases"] == ["AsposeTeXError"]
    assert by_name["FontError"]["bases"] == ["AsposeTeXError"]
    assert by_name["InputError"]["bases"] == ["AsposeTeXError"]
    # None of these four classes defines a single method of its own (each body is only a
    # docstring), so there is nothing for _flatten_inheritance to tag with inherited_from.
    # TC-555: this guard looped over every entry (the four were all there was); it is now scoped to
    # the four exception classes, which is what it always described.
    for entry in by_name.values():
        assert entry["methods"] == []


def test_every_entry_is_a_real_python_surface_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    # TC-555: {"class"} -> {"class", "enum", "function"}: OutputFormat is an Enum and
    # create_input_source a module function, both recovered from presentation/__init__.py.
    assert kinds == {"class", "enum", "function"}


def test_the_recovered_classes_carry_their_bases_and_members() -> None:
    by_import = _by_import()
    presentation = "aspose_tex.presentation."

    def members(entry: dict) -> set[str]:
        return {method["name"] for method in entry["methods"]}

    def properties(entry: dict) -> set[str]:
        return {prop["name"] for prop in entry["properties"]}

    device = by_import[presentation + "OutputDevice"]
    assert device["bases"] == ["abc.ABC"]
    assert {"__init__", "finalize", "get_bytes"} <= members(device)
    assert "destination" in properties(device)
    for name in ("PdfDevice", "DviDevice", "SvgDevice"):
        entry = by_import[presentation + name]
        assert entry["bases"] == ["OutputDevice"], name
        assert {"finalize", "get_bytes"} <= members(entry), name
    assert "get_all_pages" in members(by_import[presentation + "SvgDevice"])
    job = by_import[presentation + "TeXJob"]
    assert {"__init__", "run"} <= members(job)
    assert "messages" in properties(job)
    assert {"load_format", "job_name", "magnification"} <= properties(by_import[presentation + "TeXOptions"])
    assert by_import[presentation + "OutputFormat"]["kind"] == "enum"
    source = by_import["aspose_tex.InputSource"]
    assert source["bases"] == ["abc.ABC"]
    assert "read_line" in members(source)
    for name in ("FileInputSource", "StringInputSource"):
        entry = by_import["aspose_tex." + name]
        assert entry["bases"] == ["InputSource"], name
        assert {"__init__", "read_line"} <= members(entry), name


def test_the_public_names_are_also_exposed_from_the_top_level_package() -> None:
    by_import = _by_import()
    for name in PUBLIC_NAMES - EXCEPTIONS - {"create_input_source"}:
        assert by_import["aspose_tex." + name]["file"] == "src/aspose_tex/__init__.py", name
    assert by_import["aspose_tex.create_input_source"]["kind"] == "function"


def test_the_two_repaired_files_are_the_only_unresolved_entries() -> None:
    # TC-555: 2 syntax-error + 12 unresolved-reexport -> 2 repaired-syntax. A name that stays
    # unresolved is a finding, so this list is exact, not a lower bound.
    unresolved = _load_fixture()["unresolved"]
    assert sorted(unresolved) == [
        "aspose_tex._input.reader:52:repaired-syntax:src/aspose_tex/_input/reader.py:42 header lines closed with pass",
        "aspose_tex.presentation:107:repaired-syntax:src/aspose_tex/presentation/__init__.py:36 header lines closed with pass",
    ]
    assert not any(":syntax-error:" in entry for entry in unresolved)
    assert not any(":unresolved-reexport:" in entry for entry in unresolved)


def test_every_reexport_shell_names_a_definition_that_is_in_the_fixture() -> None:
    by_import = _by_import()
    for entry in _load_fixture()["types"]:
        origin = entry.get("reexport_of")
        if origin and not any(part.startswith("_") for part in origin.split(".")):
            assert origin in by_import, (entry["class_import"], origin)
