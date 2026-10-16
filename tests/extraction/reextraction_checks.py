"""The shared checks of the re-extraction chain TC-530 to TC-535 (a plain module, nothing here is collected).

Every function reads the COMMITTED tree only: ``tests/fixtures/<pilot>/api_surface.json``,
``infra/helm/foss-mcp/values.yaml``, ``docker-compose.yml`` and the extractor's constants. A function that
returns problems returns an empty list when all is well. Free of any import from ``tests/deploy``.
"""

from __future__ import annotations

import functools
import json
import re
import shlex
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import yaml

from foss_mcp.extraction import run_extraction
from foss_mcp.indexing.chunk_builder import build_chunks_from_api_surface
from tests.extraction.docs_identifiers import docs_identifiers, unresolved

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"
VALUES_PATH = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"
COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"

# The extractor's data-contract version the committed fixtures must carry at least.
REQUIRED_EXTRACTOR_VERSION: int = run_extraction.EXTRACTOR_VERSION

DEFAULT_CAP = 300
_SPLIT_NAME = re.compile(r"\.|::|/")
_ABBREVIATION_TAIL = re.compile(r"(?<![A-Za-z])(?:e\.g|i\.e)\.?$")


def pilot_key(family: str, platform: str) -> str:
    """``family_platform``, the directory name under tests/fixtures."""
    return f"{family}_{platform}"


def _family_platform(key: str) -> tuple[str, str]:
    family, platform = key.split("_", 1)
    return family, platform


@functools.cache
def _chart_pilots() -> dict[str, dict[str, Any]]:
    data = yaml.safe_load(VALUES_PATH.read_text(encoding="utf-8"))
    return {pilot_key(p["family"], p["platform"]): dict(p) for p in data["ingestion"]["pilots"]}


def chart_pilot(key: str) -> dict[str, Any]:
    """The ``ingestion.pilots[]`` entry of the chart for *key*."""
    return _chart_pilots()[key]


def pinned_commit(key: str) -> str:
    return str(chart_pilot(key)["library"]["commit"])


def recommended_cap(key: str) -> int:
    family, platform = _family_platform(key)
    return run_extraction.RECOMMENDED_MAX_TYPES.get(f"{family}/{platform}", DEFAULT_CAP)


def chart_max_types(key: str) -> int:
    return int(chart_pilot(key)["maxTypes"])


def compose_max_types(key: str) -> int:
    """The value after ``--max-types`` in the command of the ``ingest-<family>-<platform>`` compose service."""
    family, platform = _family_platform(key)
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    command = compose["services"][f"ingest-{family}-{platform}"]["command"]
    text = " ".join(command) if isinstance(command, list) else str(command)
    tokens = shlex.split(text)
    return int(tokens[tokens.index("--max-types") + 1])


@functools.cache
def load_fixture(key: str) -> dict[str, Any]:
    return json.loads(fixture_text(key))


def fixture_text(key: str) -> str:
    return (FIXTURES_DIR / key / "api_surface.json").read_text(encoding="utf-8")


def type_names(fixture: Mapping[str, Any]) -> set[str]:
    """Every entry ``name`` and the last segment of every ``class_import`` (split on ``.``, ``::`` and ``/``)."""
    names: set[str] = set()
    for entry in fixture.get("types") or []:
        if entry.get("name"):
            names.add(str(entry["name"]))
        if entry.get("class_import"):
            names.add(_SPLIT_NAME.split(str(entry["class_import"]))[-1])
    return names


def fqns(fixture: Mapping[str, Any]) -> set[str]:
    """Every non-empty ``class_import``."""
    return {str(e["class_import"]) for e in fixture.get("types") or [] if e.get("class_import")}


def members(fixture: Mapping[str, Any]) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    """``(entry, member)`` for every dict in an entry's ``methods`` and ``properties``."""
    for entry in fixture.get("types") or []:
        for field in ("methods", "properties"):
            for member in entry.get(field) or []:
                if isinstance(member, dict):
                    yield entry, member


def member_doc_stats(fixture: Mapping[str, Any]) -> dict[str, int]:
    stats = {
        "members": 0,
        "members_with_doc": 0,
        "params": 0,
        "params_with_doc": 0,
        "methods_with_return_doc": 0,
        "methods_with_throws_doc": 0,
    }
    for _entry, member in members(fixture):
        stats["members"] += 1
        if member.get("doc"):
            stats["members_with_doc"] += 1
        for param in member.get("params") or []:
            if isinstance(param, dict):
                stats["params"] += 1
                if param.get("doc"):
                    stats["params_with_doc"] += 1
        if member.get("return_doc"):
            stats["methods_with_return_doc"] += 1
        if member.get("throws_doc"):
            stats["methods_with_throws_doc"] += 1
    return stats


def _texts_of_member(member: Mapping[str, Any]) -> Iterator[str]:
    for value in (member.get("doc"), member.get("return_doc"), member.get("throws_doc")):
        if isinstance(value, str):
            yield value
    for param in member.get("params") or []:
        if isinstance(param, dict) and isinstance(param.get("doc"), str):
            yield param["doc"]


def abbreviation_cut_docs(fixture: Mapping[str, Any]) -> list[str]:
    """Every description whose stripped value ends in ``e.g.``, ``i.e.``, ``e.g`` or ``i.e``: cut at an abbreviation."""
    cut: list[str] = []
    for entry in fixture.get("types") or []:
        text = entry.get("doc")
        if isinstance(text, str) and _ABBREVIATION_TAIL.search(text.strip()):
            cut.append(text)
    for _entry, member in members(fixture):
        for text in _texts_of_member(member):
            if _ABBREVIATION_TAIL.search(text.strip()):
                cut.append(text)
    return cut


def test_file_entries(fixture: Mapping[str, Any]) -> list[str]:
    """The ``class_import`` or ``name`` of every entry the extractor itself would call a test entry."""
    return [
        str(e.get("class_import") or e.get("name"))
        for e in fixture.get("types") or []
        if run_extraction._is_test_entry(e)
    ]


def ingestion_slice_problems(key: str) -> list[str]:
    """The ingestion slice (``--max-types`` of the chart) must drop nothing the extraction kept."""
    fixture = load_fixture(key)
    sliced = build_chunks_from_api_surface(fixture, title=key, max_types=chart_max_types(key))
    whole = build_chunks_from_api_surface(fixture, title=key, max_types=10**6)
    if len(sliced) != len(whole):
        return [
            f"{key}: ingesting with max_types={chart_max_types(key)} builds {len(sliced)} chunks, "
            f"the whole fixture builds {len(whole)}"
        ]
    return []


def cap_problems(key: str) -> list[str]:
    """The chart cap, the compose cap and the extractor's recommendation are one number, and the counts are honest."""
    problems: list[str] = []
    fixture = load_fixture(key)
    chart, compose, cap = chart_max_types(key), compose_max_types(key), recommended_cap(key)
    if not chart == compose == cap:
        problems.append(f"{key}: chart maxTypes {chart}, compose --max-types {compose}, extractor cap {cap}")
    reduced = fixture.get("reduced_type_count")
    if reduced != len(fixture.get("types") or []):
        problems.append(
            f"{key}: reduced_type_count {reduced} != len(types) {len(fixture.get('types') or [])}"
        )
    type_count = fixture.get("type_count")
    if isinstance(reduced, int) and isinstance(type_count, int):
        if bool(fixture.get("truncated")) != (reduced < type_count):
            problems.append(
                f"{key}: truncated {fixture.get('truncated')!r} disagrees with {reduced} kept of {type_count}"
            )
        if type_count <= cap and fixture.get("truncated"):
            problems.append(
                f"{key}: type_count {type_count} fits the cap {cap} but the fixture says truncated"
            )
    else:
        problems.append(
            f"{key}: reduced_type_count {reduced!r} or type_count {type_count!r} is not an integer"
        )
    return problems


def readme_recall(key: str) -> float:
    """The share of the README's API-shaped identifiers the fixture resolves (1.0 when it names none)."""
    named = docs_identifiers(key)
    if not named:
        return 1.0
    return 1 - len(unresolved(key)) / len(named)
