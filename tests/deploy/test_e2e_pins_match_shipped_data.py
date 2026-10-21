"""G3/TC-536 (REQ-G3-008, REQ-G3-001): the literal pins of the seven docker end-to-end files still hold in
the shipped baked data.

The seven files under ``tests/e2e/*_live_content.py`` pin facts of the data they serve as module-level
string constants (a symbol, an enum member, a method or base-class signature fragment, a source commit, an
example symbol, a documentation fragment). Those facts drift whenever a pilot is re-extracted or
re-ingested; this file catches the drift OFFLINE, from the committed tree alone, before a docker run ever
starts.

Offline, no docker, no network, no skip: every e2e file is read with ``ast`` (never imported, so none of
its module-level side effects - including a file that computes a constant at collection time - ever run,
and no docker is touched); only a top-level ``NAME = "literal string"`` assignment is a case. A file that
computes a constant at run time (pdf/cpp's ``REAL_SYMBOL``, pdf/typescript's ``REAL_SOURCE_COMMIT`` and
``REAL_EXAMPLE_SOURCE_COMMIT``) contributes only its literal constants.

What this file CANNOT check: ``REAL_TASK_QUERY``, ``REAL_DOC_QUERY`` and ``REAL_DOC_CONTENT_TYPE`` (what the
live server answers for a query - a function of ranking, not a fact in the data) and any in-test numeric
expectation. Those are verified only by the docker run of the review harness.
"""

from __future__ import annotations

import ast
import functools
import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
E2E_DIR = REPO_ROOT / "tests" / "e2e"

# file (under tests/e2e) -> pilot key (family_platform), exactly the seven files TC-536 names.
FILE_PILOTS: dict[str, str] = {
    "test_cells_rust_live_content.py": "cells_rust",
    "test_pdf_cpp_live_content.py": "pdf_cpp",
    "test_pdf_go_live_content.py": "pdf_go",
    "test_pdf_java_live_content.py": "pdf_java",
    "test_pdf_net_live_content.py": "pdf_net",
    "test_pdf_typescript_live_content.py": "pdf_typescript",
    "test_slides_python_live_content.py": "slides_python",
}

# The ten pin shapes this file knows how to check against the shipped data. Any other module-level string
# constant (BASE_URL, PROJECT_NAME, REAL_TASK_QUERY, REAL_DOC_QUERY, REAL_DOC_CONTENT_TYPE, ...) is not a
# "fact in the baked data" this file can check offline, so it is deliberately not a case.
CHECKED_NAMES = frozenset(
    {
        "REAL_SOURCE_COMMIT",
        "REAL_EXAMPLE_SOURCE_COMMIT",
        "REAL_SYMBOL",
        "REAL_SYMBOL_FQN",
        "REAL_ENUM_FQN",
        "REAL_ENUM_MEMBER",
        "REAL_METHOD_FRAGMENT",
        "REAL_BASE_FRAGMENT",
        "REAL_EXAMPLE_SYMBOL",
        "REAL_DOC_FRAGMENT",
    }
)

_FQN_LINE = re.compile(r"^FQN:\s*(.+)$", re.MULTILINE)
_SOURCE_COMMIT_LINE = re.compile(r"^Source-Commit:[ \t]*(\S*)[ \t]*$", re.MULTILINE)
_SPLIT_NAME = re.compile(r"\.|::")
_VERIFIED_KIND_LINE = "Kind: verified_example"

SYMBOL = "symbol"
DOCUMENTATION = "documentation"
EXAMPLE = "example"


# -- reading the e2e files themselves, with ast only - never an import ------------------------------------


def literal_constants(path: Path) -> dict[str, str]:
    """Every top-level ``NAME = "literal string"`` assignment among ``CHECKED_NAMES``.

    A dynamically computed assignment (``REAL_SYMBOL = _real_class_name()``, ``REAL_EXAMPLE_SOURCE_COMMIT =
    REAL_SOURCE_COMMIT``) is not an ``ast.Constant`` value and is silently skipped, exactly as the card asks:
    such a file "contributes only its literal ones".
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or target.id not in CHECKED_NAMES:
            continue
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            found[target.id] = value.value
    return found


# -- reading the shipped baked data (a small loader of this file's own) -----------------------------------


@functools.cache
def chart_pilots() -> list[dict[str, Any]]:
    data = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
    return [dict(p) for p in data["ingestion"]["pilots"]]


@functools.cache
def chart_pilot(key: str) -> dict[str, Any]:
    family, platform = key.split("_", 1)
    return next(p for p in chart_pilots() if p["family"] == family and p["platform"] == platform)


@functools.cache
def pilot_chunk_texts(key: str) -> tuple[str, ...]:
    """Every chunk's text of the pilot's ACTIVE generation: active_pointer.json -> the one generation file
    it names -> ``payload.lexical_index.documents[*].text``, read the way
    ``tests/deploy/test_fleet_consistency.py`` does."""
    pilot = chart_pilot(key)
    scope_dir = DEMO_MANIFESTS / f"{pilot['family']}__{pilot['platform']}__{pilot['sourceKind']}"
    pointer = json.loads((scope_dir / "active_pointer.json").read_text(encoding="utf-8"))
    files = sorted(scope_dir.glob("generation__*.json"))
    assert len(files) == 1, (key, [f.name for f in files])
    generation = json.loads(files[0].read_text(encoding="utf-8"))
    assert generation["generation_id"] == pointer["generation_id"], key
    documents = generation["payload"]["lexical_index"]["documents"]
    return tuple(str(document.get("text", "")) for document in documents.values())


def chunk_kind(text: str) -> str:
    """The rule of ``tests/deploy/test_fleet_consistency.py``'s own ``chunk_kind``, reimplemented so this
    file never imports serving or that test module."""
    match = _FQN_LINE.search(text)
    if match is None:
        return DOCUMENTATION
    fqn = match.group(1).strip()
    if fqn.startswith("Example: "):
        return EXAMPLE
    if fqn.startswith("Doc: "):
        return DOCUMENTATION
    return SYMBOL


def fqn_of(text: str) -> str | None:
    match = _FQN_LINE.search(text)
    return match.group(1).strip() if match else None


def _last_segment(fqn: str) -> str:
    return _SPLIT_NAME.split(fqn)[-1]


def symbol_chunk_for(key: str, real_symbol: str) -> str | None:
    """The symbol chunk whose FQN, or whose FQN's last dot-/double-colon segment, equals *real_symbol*."""
    for text in pilot_chunk_texts(key):
        if chunk_kind(text) != SYMBOL:
            continue
        fqn = fqn_of(text)
        if fqn is not None and (fqn == real_symbol or _last_segment(fqn) == real_symbol):
            return text
    return None


# -- one problems-function per pin shape, each a pure function returning a list of problem strings ---------


def commit_problems(key: str, value: str) -> list[str]:
    pin = str(chart_pilot(key)["library"]["commit"])
    problems: list[str] = []
    if value != pin:
        problems.append(f"{key}: {value!r} differs from the chart pin {pin!r}")
    commits = {commit for text in pilot_chunk_texts(key) for commit in _SOURCE_COMMIT_LINE.findall(text)}
    if value not in commits:
        problems.append(
            f"{key}: {value!r} occurs in no Source-Commit: line (baked commits: {sorted(commits)})"
        )
    return problems


def symbol_problems(key: str, value: str) -> list[str]:
    if symbol_chunk_for(key, value) is None:
        return [f"{key}: {value!r} is not the name nor the FQN's last segment of any symbol chunk"]
    return []


def fqn_problems(key: str, value: str) -> list[str]:
    fqns = {fqn_of(text) for text in pilot_chunk_texts(key)} - {None}
    if value not in fqns:
        return [f"{key}: {value!r} is not any chunk's FQN: line"]
    return []


def enum_member_problems(key: str, enum_fqn: str, member: str) -> list[str]:
    chunk = next((text for text in pilot_chunk_texts(key) if fqn_of(text) == enum_fqn), None)
    if chunk is None:
        return [f"{key}: no chunk has the FQN {enum_fqn!r}"]
    lines = [line.rstrip() for line in chunk.splitlines()]
    plain = f"  - {member}"
    assigned = f"  - {member} ="
    if not any(line == plain or line.startswith(assigned) for line in lines):
        return [f"{key}: {member!r} is not a member line of the {enum_fqn!r} chunk"]
    return []


def fragment_problems(key: str, real_symbol: str, fragment: str, label: str) -> list[str]:
    chunk = symbol_chunk_for(key, real_symbol)
    if chunk is None:
        return [
            f"{key}: no symbol chunk is selected by REAL_SYMBOL {real_symbol!r}, so {label} cannot be checked"
        ]
    if fragment not in chunk:
        return [
            f"{key}: {label} {fragment!r} is not a substring of the symbol chunk selected by {real_symbol!r}"
        ]
    return []


def example_problems(key: str, value: str) -> list[str]:
    for text in pilot_chunk_texts(key):
        lines = {line.rstrip() for line in text.splitlines()}
        if _VERIFIED_KIND_LINE in lines and value in text:
            return []
    return [f"{key}: {value!r} is not a substring of any chunk carrying the line {_VERIFIED_KIND_LINE!r}"]


def doc_fragment_problems(key: str, value: str) -> list[str]:
    for text in pilot_chunk_texts(key):
        if chunk_kind(text) == DOCUMENTATION and value in text:
            return []
    return [f"{key}: {value!r} is not a substring of any documentation chunk"]


def problems_for(filename: str, pilot_key: str, constants: dict[str, str], name: str) -> list[str]:
    value = constants[name]
    if name in ("REAL_SOURCE_COMMIT", "REAL_EXAMPLE_SOURCE_COMMIT"):
        return commit_problems(pilot_key, value)
    if name == "REAL_SYMBOL":
        return symbol_problems(pilot_key, value)
    if name in ("REAL_SYMBOL_FQN", "REAL_ENUM_FQN"):
        return fqn_problems(pilot_key, value)
    if name == "REAL_ENUM_MEMBER":
        enum_fqn = constants.get("REAL_ENUM_FQN")
        if enum_fqn is None:
            return [f"{filename}: REAL_ENUM_MEMBER is pinned without a literal REAL_ENUM_FQN"]
        return enum_member_problems(pilot_key, enum_fqn, value)
    if name in ("REAL_METHOD_FRAGMENT", "REAL_BASE_FRAGMENT"):
        real_symbol = constants.get("REAL_SYMBOL")
        if real_symbol is None:
            return [f"{filename}: {name} is pinned without a literal REAL_SYMBOL"]
        return fragment_problems(pilot_key, real_symbol, value, name)
    if name == "REAL_EXAMPLE_SYMBOL":
        return example_problems(pilot_key, value)
    if name == "REAL_DOC_FRAGMENT":
        return doc_fragment_problems(pilot_key, value)
    raise AssertionError(
        f"unhandled checked constant name {name!r}"
    )  # pragma: no cover - CHECKED_NAMES is exhaustive


# -- collecting one test id per (file, constant) -------------------------------------------------------------


def _collect_cases() -> list[tuple[str, str, dict[str, str], str, str]]:
    cases: list[tuple[str, str, dict[str, str], str, str]] = []
    for filename, pilot_key in FILE_PILOTS.items():
        constants = literal_constants(E2E_DIR / filename)
        for name, value in sorted(constants.items()):
            cases.append((filename, pilot_key, constants, name, value))
    return cases


CASES = _collect_cases()
CASE_IDS = [f"{filename}-{name}" for filename, _pilot_key, _constants, name, _value in CASES]


# -- the tests --------------------------------------------------------------------------------------------------


def test_every_e2e_file_maps_to_a_chart_pilot() -> None:
    keys = {key for (key,) in ((p["family"] + "_" + p["platform"],) for p in chart_pilots())}
    for filename, pilot_key in FILE_PILOTS.items():
        assert pilot_key in keys, f"{filename}: {pilot_key!r} is not a pilot of the chart"


def test_at_least_one_case_was_collected_per_file() -> None:
    by_file = {filename for filename, *_rest in CASES}
    # pdf/cpp has no literal pin at all (REAL_SYMBOL is computed at run time) - by design.
    missing = set(FILE_PILOTS) - by_file - {"test_pdf_cpp_live_content.py"}
    assert not missing, f"file(s) with no literal pin case at all: {sorted(missing)}"


@pytest.mark.parametrize("filename,pilot_key,constants,name,value", CASES, ids=CASE_IDS)
def test_e2e_constant_still_holds_in_the_shipped_data(
    filename: str, pilot_key: str, constants: dict[str, str], name: str, value: str
) -> None:
    problems = problems_for(filename, pilot_key, constants, name)
    assert not problems, problems


# -- synthetic tests: the checker itself flags a bad pin -------------------------------------------------------


def test_the_checker_flags_a_missing_symbol_and_a_wrong_commit() -> None:
    key = "pdf_net"
    assert symbol_problems(key, "Page") == []
    assert any("is not the name nor" in p for p in symbol_problems(key, "NoSuchSymbolEver"))
    assert commit_problems(key, str(chart_pilot(key)["library"]["commit"])) == []
    assert any("differs from the chart pin" in p for p in commit_problems(key, "0" * 40))


def test_the_checker_flags_a_missing_fragment() -> None:
    key = "pdf_go"
    assert fragment_problems(key, "BarcodeField", "Bases:\n  - TextBoxField", "REAL_BASE_FRAGMENT") == []
    assert any(
        "is not a substring" in p
        for p in fragment_problems(
            key, "BarcodeField", "this text never appears anywhere", "REAL_BASE_FRAGMENT"
        )
    )
    assert doc_fragment_problems(key, "go get github.com/aspose-pdf-foss/aspose-pdf-foss-for-go") == []
    assert any(
        "is not a substring" in p for p in doc_fragment_problems(key, "this text never appears anywhere")
    )
