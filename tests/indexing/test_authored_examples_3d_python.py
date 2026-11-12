"""Offline checks of the 3d/python authored-example sidecar (TC-768, REQ-G3-014).

``tests/fixtures/furnished/3d_python/authored_examples.yaml`` holds exactly five minimal,
labelled examples written against the real public API of ``aspose-3d-foss`` at the pinned commit:
assembling a custom polygon face with ``PolygonBuilder``, interpolating between two orientations
with ``Quaternion.slerp``, recording a skeletal bind pose with ``Pose.add_bone_pose``, setting
document metadata on a ``Scene``'s ``AssetInfo``, and computing the cross/dot product and angle
between two ``Vector3`` edges. None of upstream's README examples cover this ground: the README's
own examples stop at opening/saving scenes, primitives, materials, a mesh built from control
points, triangulation, scene-graph walking and transforms (confirmed directly against the real
furnished page by this file's own ``test_the_example_titles_are_unique_and_differ_from_every_
readme_title``). Each authored example's title was also checked directly against
``title_anchored``/``title_prefix_anchored`` for every real task query of the 3d family corpus
before being chosen, to avoid reopening the fleet-wide find_examples title-collision regression
this session already closed (see ``docs/DECISION_LOG.md``'s 2026-11-11/2026-11-12 entries).

This file proves the sidecar is well-formed, labelled, tied to the chart's pinned commit and uses
only symbols that resolve in the pilot's extraction. Whether an example runs is decided later by
the ingestion verifier, never here: nothing in this file imports ``aspose.threed``, runs an
example, or reaches a network.

Every rule and constant is imported from ``foss_mcp.indexing.authored_examples`` rather than
retyped.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

import infra.build_chunks as build_chunks
from foss_mcp.indexing.authored_examples import (
    FORBIDDEN_CODE_PATTERNS,
    LANGUAGE_BY_PLATFORM,
    SIDECAR_NAME,
    load_authored_examples,
    validate_authored_examples,
)
from foss_mcp.indexing.example_candidates import (
    CandidateExample,
    extract_candidate_examples,
    normalised_code_sha256,
)
from foss_mcp.normalization.citation import qualified_symbol_index_from_api_surface

PILOT = "3d_python"

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
SIDECAR = FIXTURES / "furnished" / PILOT / SIDECAR_NAME
PAGE = FIXTURES / "furnished" / PILOT / "pages" / "_index.md"
API_SURFACE = FIXTURES / PILOT / "api_surface.json"
CHART_VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"

EXPECTED_EXAMPLES = 5
EXAMPLE_INDEXES = list(range(EXPECTED_EXAMPLES))

_IMPORT_RE = re.compile(r"(?m)^[ \t]*(?:import\s+([^\s#]+)|from\s+([^\s#]+)\s+import\b)")


def _pilot_library() -> dict:
    values = yaml.safe_load(CHART_VALUES.read_text(encoding="utf-8"))
    for pilot in values["ingestion"]["pilots"]:
        if f"{pilot['family']}_{pilot['platform']}" == PILOT:
            return pilot["library"]
    raise AssertionError(f"{PILOT} is not a pilot in values.yaml")


def _api_types() -> list[dict]:
    return json.loads(API_SURFACE.read_text(encoding="utf-8"))["types"]


def _document() -> dict:
    return yaml.safe_load(SIDECAR.read_bytes().decode("utf-8"))


def _example(index: int) -> dict:
    return _document()["examples"][index]


def _candidates() -> tuple[CandidateExample, ...]:
    library = _pilot_library()
    return load_authored_examples(
        SIDECAR,
        pilot=PILOT,
        library_platform=library["platform"],
        library_commit=library["commit"],
        api_types=_api_types(),
    )


def _readme_titles() -> set[str]:
    page = build_chunks._load_furnished_page(PAGE)
    platform = _pilot_library()["platform"]
    titles = {candidate.title for candidate in extract_candidate_examples(page)}
    titles |= {candidate.title for candidate in extract_candidate_examples(page, platform=platform)}
    return titles


def test_the_sidecar_is_not_one_of_the_two_builtin_pilots() -> None:
    # TC-768's own card: confirm 3d_python never became a third _BUILTIN_PILOTS entry alongside
    # 3d_java - it must carry its examples in the sidecar file, same as every other pilot.
    import foss_mcp.indexing.authored_examples as authored_examples_module

    assert PILOT not in authored_examples_module._BUILTIN_PILOTS


def test_the_sidecar_loads_and_yields_exactly_five_authored_candidates() -> None:
    candidates = _candidates()
    assert len(candidates) == EXPECTED_EXAMPLES
    assert {candidate.provenance for candidate in candidates} == {"authored"}
    assert {candidate.language for candidate in candidates} == {"python"}


def test_the_sidecar_header_matches_the_chart_pin() -> None:
    library = _pilot_library()
    document = _document()
    assert document["schema_version"] == 1
    assert document["pilot"] == PILOT
    assert document["library_repository"] == library["repository"]
    assert document["library_commit"] == library["commit"]
    assert document["language"] == LANGUAGE_BY_PLATFORM[library["platform"]]
    assert len(document["examples"]) == EXPECTED_EXAMPLES


def test_the_sidecar_has_no_validation_problems() -> None:
    library = _pilot_library()
    problems = validate_authored_examples(
        _document(),
        pilot=PILOT,
        library_platform=library["platform"],
        library_commit=library["commit"],
        api_types=_api_types(),
    )
    assert problems == []


def test_a_stale_commit_is_refused_by_the_validator() -> None:
    library = _pilot_library()
    document = _document()
    document["library_commit"] = "0" * 40
    problems = validate_authored_examples(
        document,
        pilot=PILOT,
        library_platform=library["platform"],
        library_commit=library["commit"],
        api_types=_api_types(),
    )
    assert any("rule 3" in problem for problem in problems), problems


def test_the_example_titles_are_unique_and_differ_from_every_readme_title() -> None:
    examples = _document()["examples"]
    titles = [example["title"] for example in examples]
    assert len(set(titles)) == len(titles)
    readme_titles = _readme_titles()
    assert readme_titles, "the committed page must carry at least one README candidate"
    assert not set(titles) & readme_titles
    for example in examples:
        assert len(example["symbols"]) >= 2, example["title"]


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_the_code_hash_matches(index: int) -> None:
    example = _example(index)
    assert example["code_sha256"] == normalised_code_sha256(example["code"])


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_the_label_comment_is_the_first_non_blank_line(index: int) -> None:
    code = _example(index)["code"]
    first = next(line.strip() for line in code.splitlines() if line.strip())
    assert first.startswith("#")
    assert "Authored example" in first


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_every_declared_symbol_resolves_and_appears_in_the_code(index: int) -> None:
    example = _example(index)
    index_of_symbols = qualified_symbol_index_from_api_surface(_api_types())
    for symbol in example["symbols"]:
        assert index_of_symbols.declarations_for(symbol), f"{symbol} has no declaration in the api_surface"
        last_segment = symbol.rsplit(".", 1)[-1]
        assert re.search(rf"(?<![A-Za-z0-9_]){re.escape(last_segment)}(?![A-Za-z0-9_])", example["code"]), (
            f"{symbol} is not used in the code"
        )


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_no_forbidden_pattern_matches(index: int) -> None:
    code = _example(index)["code"]
    hits = [pattern.pattern for pattern in FORBIDDEN_CODE_PATTERNS if pattern.search(code)]
    assert hits == []


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_every_example_imports_from_the_pilots_own_package_or_stdlib(index: int) -> None:
    code = _example(index)["code"]
    modules = [module or package for module, package in _IMPORT_RE.findall(code)]
    assert modules, "the example imports nothing"
    library_imports = [module for module in modules if module.startswith("aspose.threed")]
    assert library_imports, "the example imports nothing from aspose.threed"
    # Every other import is standard library; a third-party package would not resolve in the verifier's project.
    stdlib_modules = {"math"}
    for module in modules:
        top_level = module.split(".", 1)[0]
        assert module.startswith("aspose.threed") or top_level in stdlib_modules, (
            f"unexpected import: {module}"
        )


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_no_example_carries_unittest_or_sys_path_scaffolding(index: int) -> None:
    code = _example(index)["code"]
    assert "unittest" not in code
    assert "sys.path" not in code
    assert "self.assert" not in code


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_no_example_depends_on_an_external_file_or_network(index: int) -> None:
    code = _example(index)["code"]
    # Every example builds its scene/geometry in memory and never calls Scene.save/Scene.open,
    # so no example depends on a file that does not exist in the verifier's throwaway sandbox.
    assert ".save(" not in code
    assert ".open(" not in code


_FAMILY_TASK_QUERIES: tuple[str, ...] = (
    "load a 3d model file",
    "save the scene as FBX or STL",
    "add a cube",
    "create a node in the scene",
    "move or rotate an object",
    "add a light",
    "add a camera",
    "apply a material color",
    "convert obj to glb",
    "create a sphere or cylinder",
    "animate an object over time",
    "extrude a 2d shape",
    "get the bounding box of a mesh",
    "add a texture to a material",
    "triangulate a mesh",
    "set the uv coordinates",
    "read the vertex normals",
    "walk the children of a node",
    "make a torus or plane",
    "open a scene from a stream",
    "set the mesh vertices",
    "add a point light",
    "change the field of view of the camera",
    "scale an object",
    "export my model as obj",
    "import an fbx file",
    "set the colour of a light",
    "attach a child to a node",
    "give a node a name",
)


@pytest.mark.parametrize("index", EXAMPLE_INDEXES)
def test_no_title_anchors_an_unrelated_3d_family_task_query(index: int) -> None:
    """TC-768: the exact real queries of the 3d family corpus (``tests/mcp/tools/
    test_round3_retrieval_corpus.py``'s own ``FAMILY_TASKS["3d"]``), copied here so this file's own
    checks catch a reintroduced title collision without needing the full retrieval fixtures. None of
    this example's own code answers any of these generic scene/mesh/light/camera/animation queries
    (each example is a narrow utility: PolygonBuilder, Quaternion.slerp, a skeletal bind pose,
    AssetInfo metadata, or Vector3 cross/dot/angle), so this title must never become eligible to
    answer one via ``title_anchored``/``title_prefix_anchored`` - that is exactly the fleet-wide
    find_examples wrong-confident regression this session already closed once (see
    ``docs/DECISION_LOG.md``'s 2026-11-11/2026-11-12 entries) and must not reopen.
    """
    from foss_mcp.mcp.tools.relevance import title_anchored, title_prefix_anchored

    title = _example(index)["title"]
    for query in _FAMILY_TASK_QUERIES:
        assert not title_anchored(query, "3d", title), (query, title)
        assert not title_prefix_anchored(query, "3d", title), (query, title)
