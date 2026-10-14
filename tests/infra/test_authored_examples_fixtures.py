"""Offline checks of every committed authored-example sidecar (TC-460, REQ-G3-007).

``tests/fixtures/furnished/<pilot>/authored_examples.yaml`` is the single source of the reviewed,
worker-written examples of a pilot whose README cannot supply two verified ones. Zero sidecars are
committed when this card lands; the authoring cards TC-461..TC-464 add four. The sidecar check is ONE
test that loops over whatever is committed (never parametrized over a possibly empty list, so nothing
is ever skipped) and names the failing pilot in its message.

Nothing here reaches a network. This file deliberately carries no network-client strings: the
forbidden-pattern scan lives in ``foss_mcp.indexing.authored_examples.FORBIDDEN_CODE_PATTERNS`` and is
imported, so the authoring cards that run this file stay offline.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import yaml

import infra.build_chunks as build_chunks
from foss_mcp.indexing.authored_examples import (
    FORBIDDEN_CODE_PATTERNS,
    SIDECAR_NAME,
    validate_authored_examples,
)
from foss_mcp.indexing.example_candidates import (
    extract_candidate_examples,
    normalised_code_sha256,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
FURNISHED = FIXTURES / "furnished"
CHART_VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"
SCHEMA_PATH = REPO_ROOT / "schemas" / "product" / "authored-examples.schema.json"


def _chart_pilots() -> dict[str, dict]:
    values = yaml.safe_load(CHART_VALUES.read_text(encoding="utf-8"))
    return {f"{p['family']}_{p['platform']}": p for p in values["ingestion"]["pilots"]}


def _readme_titles(pilot_name: str, platform: str) -> set[str]:
    """Every title the README candidates of the pilot's committed page can carry."""
    page_path = FURNISHED / pilot_name / "pages" / "_index.md"
    page = build_chunks._load_furnished_page(page_path)
    titles = {candidate.title for candidate in extract_candidate_examples(page)}
    titles |= {candidate.title for candidate in extract_candidate_examples(page, platform=platform)}
    return titles


def sidecar_problems(path: Path, pilot_name: str) -> list[str]:
    """Every problem with the sidecar at ``path`` for the chart pilot ``pilot_name`` (empty means valid)."""
    chart = _chart_pilots()
    if pilot_name not in chart:
        return [f"{pilot_name} is not a pilot in infra/helm/foss-mcp/values.yaml ingestion.pilots"]
    library = chart[pilot_name]["library"]

    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    problems = [
        f"schema: {'/'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}"
        for error in jsonschema.Draft202012Validator(schema).iter_errors(document)
    ]

    api_surface = FIXTURES / pilot_name / "api_surface.json"
    api_types = json.loads(api_surface.read_text(encoding="utf-8"))["types"]
    problems += validate_authored_examples(
        document,
        pilot=pilot_name,
        library_platform=library["platform"],
        library_commit=library["commit"],
        api_types=api_types,
    )

    if isinstance(document, dict):
        if document.get("library_repository") != library["repository"]:
            problems.append(
                f"library_repository {document.get('library_repository')!r} is not the chart's {library['repository']!r}"
            )
        readme_titles = _readme_titles(pilot_name, library["platform"])
        for example in document.get("examples") or []:
            title = example.get("title") if isinstance(example, dict) else None
            if title in readme_titles:
                problems.append(
                    f"example title {title!r} equals a README candidate title of the committed page"
                )
    return problems


def test_every_committed_sidecar_is_valid_for_its_pilot() -> None:
    failures: list[str] = []
    for path in sorted(FURNISHED.glob(f"*/{SIDECAR_NAME}")):
        pilot_name = path.parent.name
        failures += [f"{pilot_name}: {problem}" for problem in sidecar_problems(path, pilot_name)]
    assert not failures, "invalid authored-example sidecars:\n" + "\n".join(failures)


def _synthetic_sidecar(tmp_path: Path, **over: object) -> Path:
    """A valid sidecar for the chart's pdf_net pilot, written to ``tmp_path``."""
    library = _chart_pilots()["pdf_net"]["library"]
    code = "// Authored example: read the first page\nvar page = document.Pages[1]; // Page\n"
    document: dict = {
        "schema_version": 1,
        "pilot": "pdf_net",
        "library_repository": library["repository"],
        "library_commit": library["commit"],
        "language": "csharp",
        "examples": [
            {
                "title": "Authored fixture example that no README uses",
                "description": "Read the first page of a document.",
                "symbols": ["Aspose.Pdf.Page"],
                "code": code,
                "code_sha256": normalised_code_sha256(code),
            }
        ],
    }
    document.update(over)
    path = tmp_path / "pdf_net" / SIDECAR_NAME
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8", newline="\n")
    return path


def test_a_synthetic_valid_sidecar_is_reported_clean(tmp_path: Path) -> None:
    assert sidecar_problems(_synthetic_sidecar(tmp_path), "pdf_net") == []


def test_a_synthetic_stale_copy_with_a_changed_commit_is_reported(tmp_path: Path) -> None:
    stale = _synthetic_sidecar(tmp_path, library_commit="0" * 40)
    problems = sidecar_problems(stale, "pdf_net")
    assert any("rule 3" in problem for problem in problems), problems


def test_a_sidecar_for_a_directory_outside_the_chart_is_reported(tmp_path: Path) -> None:
    problems = sidecar_problems(_synthetic_sidecar(tmp_path), "not_a_pilot")
    assert problems and "not_a_pilot" in problems[0]


def test_a_title_that_equals_a_readme_candidate_title_is_reported(tmp_path: Path) -> None:
    page = build_chunks._load_furnished_page(FURNISHED / "pdf_net" / "pages" / "_index.md")
    readme_title = extract_candidate_examples(page)[0].title
    path = _synthetic_sidecar(tmp_path)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["examples"][0]["title"] = readme_title
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8", newline="\n")
    assert any("README candidate title" in problem for problem in sidecar_problems(path, "pdf_net"))


def test_a_repository_that_differs_from_the_chart_is_reported(tmp_path: Path) -> None:
    path = _synthetic_sidecar(tmp_path, library_repository="someone/else")
    assert any("library_repository" in problem for problem in sidecar_problems(path, "pdf_net"))


def test_the_schema_file_itself_is_valid_draft_2020_12() -> None:
    jsonschema.Draft202012Validator.check_schema(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


def test_the_forbidden_pattern_constant_is_imported_not_retyped() -> None:
    assert len(FORBIDDEN_CODE_PATTERNS) > 20
    source = Path(__file__).read_text(encoding="utf-8")
    assert "FORBIDDEN_CODE_PATTERNS" in source
