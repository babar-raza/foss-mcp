"""G3/TC-853 (RD2-258, REQ-G3-017): tex/python is re-pinned at the upstream release 26.11.1 and served regenerated.

tex/python was pinned at upstream commit 181015d2, where 35 of 45 source files failed ``ast.parse`` (IndentationError),
``import aspose_tex`` raised, the pilot was registered BLOCKED_UPSTREAM and the registry carried TEX-PYTHON-001. The upstream
release 26.11.1 (commit 07db3203, PyPI ``aspose-tex-foss`` 26.11.1) parses clean, so the pilot is re-pinned there and every
artefact is regenerated at that pin. Re-verified before the disclosure was retired (2026-11-20, the TC-853 worker, outside the
repository): a clone at 07db3203 has 248 ``.py`` files and every one passes ``ast.parse`` and ``py_compile``, the 59 under
``src/`` among them; ``pip install`` of that clone in a clean venv imports ``aspose_tex`` and
``from aspose_tex import TeXJob, TeXOptions, PdfDevice, create_input_source`` works (57 of the 58 package modules import; the
58th, ``aspose_tex.mcp``, needs the optional ``mcp`` extra and says so).

This test pins, offline from the committed files only: the pin in every place it is recorded, the regenerated fixture and the
frozen old one, the baked generation (one example per chunk, 4096-dim vectors, no extraction notice, an honest coverage line),
the three sidecars fetched AT the pin, the retired disclosure, the furnished page and the example cache.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml

from tests.deploy.test_fleet_consistency import STALE_BAKED_PILOTS, STALE_BY_MEMBERS, UNRESOLVED_EXCEPTIONS

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
SCOPE_DIR = REPO_ROOT / "infra" / "demo-manifests" / "tex__python__self_extracted"
MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"

PIN = "07db320334a0a58e11107b6ca8092b2e5047c1f8"
OLD_PIN = "181015d2eee3e19f8af3d84e4a932ac84ad47c7c"
REPOSITORY = "aspose-tex-foss/Aspose.TeX-FOSS-for-Python"
GATEWAY_DIMENSION = 4096
_EXAMPLE_FQN_LINE = re.compile(r"^FQN: Example: ", re.MULTILINE)


def _generation() -> dict[str, Any]:
    pointer = json.loads((SCOPE_DIR / "active_pointer.json").read_text(encoding="utf-8"))
    version = pointer["generation_id"].rsplit("::", 1)[-1]
    return json.loads((SCOPE_DIR / f"generation__{version}.json").read_text(encoding="utf-8"))


def _chunks() -> dict[str, str]:
    documents = _generation()["payload"]["lexical_index"]["documents"]
    return {chunk_id: str(chunk.get("text", "")) for chunk_id, chunk in documents.items()}


def _examples() -> dict[str, str]:
    return {chunk_id: text for chunk_id, text in _chunks().items() if _EXAMPLE_FQN_LINE.search(text)}


def _fixture() -> dict[str, Any]:
    return json.loads((FIXTURES / "tex_python" / "api_surface.json").read_text(encoding="utf-8"))


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


# -- the pin, everywhere it is recorded -----------------------------------------------------------------------------------------


def test_the_chart_pins_tex_python_at_the_release_commit() -> None:
    values = yaml.safe_load(
        (REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml").read_text(encoding="utf-8")
    )
    pilots = [p for p in values["ingestion"]["pilots"] if (p["family"], p["platform"]) == ("tex", "python")]
    assert len(pilots) == 1
    assert pilots[0]["library"] == {"repository": REPOSITORY, "commit": PIN, "platform": "python"}


def test_compose_pins_tex_python_at_the_same_commit_and_no_longer_at_the_blocked_one() -> None:
    text = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    start = text.index("  ingest-tex-python:")
    end = text.index("  serving-tex-python:")
    block = text[start:end]
    assert block.count(PIN) == 2  # --library-commit and the product-reference --ref
    assert (
        OLD_PIN not in block and OLD_PIN not in text[text.rindex("# G2/TC-236: tex/python", 0, start) : start]
    )


def test_no_live_file_still_pins_the_blocked_commit() -> None:
    for path in (
        FIXTURES / "tex_python" / "api_surface.json",
        FIXTURES / "round3_readmes" / "tex_python" / "PIN.json",
        FIXTURES / "furnished" / "tex_python" / "pages" / "_index.md",
        MANIFESTS / "product_reference_tex_python.json",
        MANIFESTS / "package_registry_tex_python.json",
        MANIFESTS / "recent_releases_tex_python.json",
        SCOPE_DIR / sorted(p.name for p in SCOPE_DIR.glob("generation__*.json"))[0],
    ):
        assert OLD_PIN not in path.read_text(encoding="utf-8"), path.name
    cache = (FIXTURES / "verified_examples" / "tex_python.json").read_text(encoding="utf-8")
    assert (
        PIN in cache
    )  # the cache keeps the old entries too (failing ones are preserved); the new ones are at the pin


# -- the extraction fixture ---------------------------------------------------------------------------------------------------


def test_the_fixture_is_extracted_at_the_pin_and_nothing_failed_to_parse() -> None:
    fixture = _fixture()
    assert (fixture["source_repository"], fixture["source_commit"]) == (REPOSITORY, PIN)
    assert fixture["parse_failures"] == {"engine": "ast", "examples": [], "failed": 0, "total": 59}
    assert "unresolved" not in fixture
    assert fixture["truncated"] is False
    assert fixture["reduced_type_count"] == fixture["type_count"] == len(fixture["types"]) == 31
    assert fixture["symbol_counts"] == {"functions": 8, "types": 16}
    assert not [
        m for t in fixture["types"] for m in t.get("methods", []) if m.get("stub") or m.get("stub_reason")
    ]
    assert not [t for t in fixture["types"] if t.get("stub") or t.get("stub_reason")]


def test_the_extraction_gained_the_cli_and_mcp_helpers_and_lost_nothing() -> None:
    new = {entry["class_import"] for entry in _fixture()["types"]}
    old = {
        entry["class_import"]
        for entry in _json(FIXTURES / "tex_python" / "api_surface_181015d2_repaired.json")["types"]
    }
    assert old <= new, sorted(old - new)
    assert new - old == {
        "aspose_tex.cli.build_parser",
        "aspose_tex.cli.main",
        "aspose_tex.mcp.compile_tex",
        "aspose_tex.mcp.tex_to_pdf",
        "aspose_tex.mcp.tex_to_svg",
        "aspose_tex.mcp.tex_version",
    }


def test_the_blocked_commit_extraction_is_kept_frozen_for_the_repair_tests() -> None:
    frozen = _json(FIXTURES / "tex_python" / "api_surface_181015d2_repaired.json")
    assert frozen["source_commit"] == OLD_PIN
    assert frozen["parse_failures"]["as_written_failed"] == 35 and frozen["parse_failures"]["total"] == 45
    assert len(frozen["unresolved"]) == 3


def test_tex_python_is_no_longer_an_unresolved_exception_and_is_not_listed_stale() -> None:
    assert "tex_python" not in UNRESOLVED_EXCEPTIONS
    assert "tex_python" not in STALE_BAKED_PILOTS
    assert "tex_python" not in STALE_BY_MEMBERS


# -- the baked generation ----------------------------------------------------------------------------------------------------


def test_every_chunk_carries_the_pin_and_only_the_pin() -> None:
    commits = {
        value
        for text in _chunks().values()
        for value in re.findall(r"^Source-Commit: (\S+)$", text, re.MULTILINE)
    }
    assert commits == {PIN}
    stats = _generation()["payload"]["stats"]
    assert (stats["symbols"], stats["docs"], stats["examples"], stats["total"]) == (31, 7, 9, 47)
    assert stats["repositories"] == [REPOSITORY]


def test_the_extraction_notice_is_gone_and_the_coverage_line_counts_no_unparsed_file() -> None:
    chunks = _chunks()
    assert not [text for text in chunks.values() if text.startswith("FQN: Doc: Extraction notice")]
    lines = {
        line for text in chunks.values() for line in text.splitlines() if line.startswith("API-Coverage:")
    }
    assert lines == {"API-Coverage: 16 of 16 types; 8 of 8 functions"}
    assert "could not be parsed" not in "\n".join(chunks.values())
    assert "cannot be imported" not in "\n".join(chunks.values())


def test_the_install_chunk_no_longer_claims_there_is_no_published_package() -> None:
    """The 181015d2 README said "No PyPI package has been published ... install it from source"; at 26.11.1 the package is on
    PyPI, so no served chunk may repeat that claim. (The published coordinate itself is served by the product-reference
    install section, pinned by the sidecar tests below and by test_round3_install_truth.)"""
    chunks = _chunks()
    install = [t for t in chunks.values() if t.startswith("FQN: Doc: Installation\n")]
    assert len(install) == 1
    assert "pip install -e" in install[0]
    assert "No PyPI package has been published" not in "\n".join(chunks.values())


def test_every_vector_is_gateway_dimension_and_every_example_has_a_point() -> None:
    vector_index = _generation()["payload"]["vector_index"]
    assert vector_index["dimension"] == GATEWAY_DIMENSION
    points = vector_index["points"]
    assert len(points) == 9
    assert {len(point["vector"]) for point in points} == {GATEWAY_DIMENSION}
    point_texts = {str(point["text"]) for point in points}
    assert {text for text in _examples().values()} == point_texts


def test_the_examples_are_verified_ran_and_hold_one_example_each() -> None:
    examples = _examples()
    assert len(examples) == 9
    for chunk_id, text in examples.items():
        assert len(_EXAMPLE_FQN_LINE.findall(text)) == 1, chunk_id
        assert re.search(r"^Verification-Level: ran-(?:produced-output|exit0)$", text, re.MULTILINE), chunk_id
        assert re.search(r"^Verified-With: Python 3\.\d+", text, re.MULTILINE), chunk_id


EXPECTED_EXAMPLE_TITLES = [
    "DVI Output",
    "DVI Output (2)",
    "Job Options",
    "Messages and Logging (2)",
    "Quick Start",
    "SVG Output",
    "SVG Output (2)",
    "SVG Output (3)",
    "`create_input_source` accepts a `Path`, a `str`, or `bytes`",
]


def test_the_published_examples_are_the_nine_that_run() -> None:
    titles = sorted(re.match(r"FQN: Example: (.*)", text).group(1) for text in _examples().values())  # type: ignore[union-attr]
    assert titles == sorted(EXPECTED_EXAMPLE_TITLES)


def test_the_readme_example_that_fails_is_not_published_and_the_cache_records_why() -> None:
    """TC-802: "Messages and Logging" needs a ``tex-inputs/chapter1.tex`` the README never creates; ``\\input chapter1``
    raises InputError at 26.11.1, so it is recorded as failed and never published as a verified example."""
    assert "FQN: Example: Messages and Logging\n" not in "\n".join(_chunks().values())
    entries = _json(FIXTURES / "verified_examples" / "tex_python.json")["entries"]
    failed = [e for e in entries if e["title"] == "Messages and Logging" and e["library_commit"] == PIN]
    assert len(failed) == 1 and failed[0]["verified"] is False
    assert "cannot resolve input file 'chapter1'" in failed[0]["output"]


def test_the_cache_holds_a_ran_entry_at_the_pin_for_every_published_example() -> None:
    entries = [
        e
        for e in _json(FIXTURES / "verified_examples" / "tex_python.json")["entries"]
        if e["library_commit"] == PIN
    ]
    ran = sorted(e["title"] for e in entries if e["verified"] is True)
    assert ran == sorted(EXPECTED_EXAMPLE_TITLES)
    assert {e["verification_level"] for e in entries if e["verified"] is True} == {"ran-exit0"}


def test_the_embedding_replay_file_is_rebuilt_from_the_generation() -> None:
    replay = _json(REPO_ROOT / "infra" / "demo-inputs" / "embeddings" / "tex__python.json")
    points = _generation()["payload"]["vector_index"]["points"]
    assert replay["dimension"] == GATEWAY_DIMENSION
    assert replay["entries"] == {
        hashlib.sha256(p["text"].encode("utf-8")).hexdigest(): p["vector"] for p in points
    }


# -- the three sidecars, fetched AT the pin ----------------------------------------------------------------------------------


def test_the_product_reference_sidecar_is_at_the_pin_and_names_the_published_package() -> None:
    sidecar = _json(MANIFESTS / "product_reference_tex_python.json")
    assert (sidecar["source_repository"], sidecar["source_ref"], sidecar["family"]) == (
        REPOSITORY,
        PIN,
        "tex",
    )
    assert (
        'name = "aspose-tex-foss"' in sidecar["manifest_text"]
        and 'version = "26.11.1"' in sidecar["manifest_text"]
    )
    assert sidecar["install_served_version"] == "26.11.1"
    assert sidecar["drift"]["status"] == "identical" and sidecar["drift"]["indexed_commit"] == PIN
    readme = (FIXTURES / "round3_readmes" / "tex_python" / "README.md").read_text(encoding="utf-8")
    assert sidecar["readme_document"]["content"] == readme
    assert sidecar["manifest_text"] == (
        FIXTURES / "round3_readmes" / "tex_python" / "pyproject.toml"
    ).read_text(encoding="utf-8")


def test_the_registry_sidecar_verifies_the_published_package() -> None:
    sidecar = _json(MANIFESTS / "package_registry_tex_python.json")
    assert (sidecar["ecosystem"], sidecar["coordinate"], sidecar["verified"]) == (
        "pypi",
        "aspose-tex-foss",
        True,
    )
    assert sidecar["served_version"] == "26.11.1" and sidecar["source_ref"] == PIN


def test_the_recent_releases_sidecar_lists_the_release_the_index_is_built_from() -> None:
    releases = _json(MANIFESTS / "recent_releases_tex_python.json")
    assert [r["tag_name"] for r in releases] == ["26.11.1", "26.11"]
    assert releases[0]["target_commitish"] == PIN
    assert releases[0]["drift"]["status"] == "identical" and releases[0]["drift"]["behind_by"] == 0


# -- the disclosure, retired on evidence -------------------------------------------------------------------------------------


def test_the_blocked_upstream_disclosure_is_retired() -> None:
    assert not (REPO_ROOT / "config" / "known_defects" / "tex__python.yaml").exists()
    for path in sorted((REPO_ROOT / "config" / "known_defects").glob("*.yaml")):
        assert "TEX-PYTHON" not in path.read_text(encoding="utf-8"), path.name


# -- the furnished page and the README fixture ---------------------------------------------------------------------------------


def test_the_furnished_page_and_the_readme_fixture_are_at_the_pin() -> None:
    page = (FIXTURES / "furnished" / "tex_python" / "pages" / "_index.md").read_text(encoding="utf-8")
    assert f"doc_source_commit: {PIN}" in page
    assert "pip install aspose-tex-foss" in page
    assert "No PyPI package has been published" not in page
    pin = _json(FIXTURES / "round3_readmes" / "tex_python" / "PIN.json")
    assert (pin["repository"], pin["commit"], pin["manifest_path"]) == (REPOSITORY, PIN, "pyproject.toml")
