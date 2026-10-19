"""G3/TC-553 (REQ-G3-001, REQ-G3-005, REQ-G3-007, REQ-G3-008): the shipped data holds the WHOLE public surface of every pilot.

This is the repository's proof of FULL COVERAGE of the shipped data. For every pilot of the chart it joins the committed
fixture (``tests/fixtures/<key>/api_surface.json``) with the committed baked generation (``infra/demo-manifests/<family>__
<platform>__<sourceKind>/``, the generation the active pointer names, documents under ``payload.lexical_index.documents``)
and asserts that what is served is exactly what was extracted: the number AND the identity of the type chunks and of the
function chunks. A partial index, a silently dropped function, a duplicated symbol or a type served as a function fails here.

How types and functions are told apart (``chunk_builder._format_type_text``, ``_entry_fqn``, ``_unique_fqns``,
``_is_test_source``): a symbol chunk is a document whose ``FQN:`` line does not start with ``Example: `` or ``Doc: ``; its SECOND
line is ``Kind: <entry kind>``; a FUNCTION chunk is ``Kind: function`` and every other kind is a TYPE chunk. On the fixture side
an entry is a function when ``kind == 'function'``. The served multiset of (FQN, Kind) pairs must equal the multiset of
(``_unique_fqns(entries)``, entry kind) over the fixture entries that are not test-source entries. For a fixture that carries
``symbol_counts`` the served type chunks equal ``symbol_counts.types`` and the served function chunks equal
``symbol_counts.functions``; an older fixture (no ``symbol_counts``) is compared with the recount of its own entries.

Every number is DERIVED from the committed data; the only constants are the anti-vacuity floors below, deliberately under the
measurement so a later re-extraction does not need a re-pin (re-measure and raise them there, in one place).

Offline and deterministic: no network, no docker, no wall clock, no skip. The table of stale baked pilots is read from
``tests.deploy.test_fleet_consistency`` with ``getattr(module, 'STALE_BAKED_PILOTS', {})`` so deleting that mechanism later
cannot break this file.
"""

from __future__ import annotations

import functools
import importlib
import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple

import pytest
import yaml

from foss_mcp.indexing.chunk_builder import _is_test_source, _unique_fqns

REPO_ROOT = Path(__file__).resolve().parents[2]
VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
FIXTURES = REPO_ROOT / "tests" / "fixtures"

# -- the anti-vacuity floors: the ONE place to re-measure ----------------------------------------------------------------------
# Measured 2026-10-17 on the committed data: 17 pilots with function entries, 3,918 function entries, 10,411 type entries over
# the fleet. The floors sit well below so a later re-extraction does not need a re-pin; raise them, never lower them.
MIN_PILOTS_WITH_FUNCTIONS = 15
# TC-533: 3_000 -> 900. The committed fixtures now hold 953 function entries: pdf/typescript serves only its export surface
# (93 exported functions instead of 3,060). The floor stays well above zero, below the measured 953.
MIN_FUNCTION_ENTRIES = 900
MIN_TYPE_ENTRIES = 9_000
MIN_PILOTS = 32

FUNCTION_KIND = "function"
_ID_SEP = "::"
_FQN_LINE = re.compile(r"^FQN:\s*(.+)$", re.MULTILINE)
_COVERAGE_LINE = re.compile(r"^API-Coverage:.*$", re.MULTILINE)
_COVERAGE_VALUE = re.compile(
    r"^API-Coverage: (\d+) of (\d+) types"
    r"(?:; (\d+) of (\d+) functions)?"
    r"(?:; (?:(\d+) of (\d+) source files could not be parsed|at least (\d+) source files could not be parsed))?$"
)


class Coverage(NamedTuple):
    """What one ``API-Coverage:`` line states."""

    types_indexed: int
    types_total: int
    functions_indexed: int | None
    functions_total: int | None


Pair = tuple[str, str]  # (served or expected FQN, entry kind)


# -- the pilots, derived from the chart --------------------------------------------------------------------------------------


def chart_pilots() -> list[dict[str, Any]]:
    data = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
    return [dict(p) for p in data["ingestion"]["pilots"]]


PILOTS = chart_pilots()
PILOT_KEYS = [f"{p['family']}_{p['platform']}" for p in PILOTS]
PILOTS_BY_KEY = dict(zip(PILOT_KEYS, PILOTS, strict=True))


def scope_dir_of(pilot: Mapping[str, Any]) -> Path:
    return DEMO_MANIFESTS / f"{pilot['family']}__{pilot['platform']}__{pilot['sourceKind']}"


def stale_baked_pilots() -> dict[str, str]:
    """The transition table of the fleet test, or an empty one when the mechanism is gone."""
    module = importlib.import_module("tests.deploy.test_fleet_consistency")
    table = getattr(module, "STALE_BAKED_PILOTS", {})
    return dict(table)


def is_really_stale(key: str) -> bool:
    """True while the baked generation of a listed pilot still differs from its fixture: by its API-Coverage line, or (TC-557, a
    Java pilot whose counts did not move) by the member lines it renders."""
    module = importlib.import_module("tests.deploy.test_fleet_consistency")
    return bool(module.baked_is_stale(key))


# -- loading the committed data (each file parsed once) ------------------------------------------------------------------------


@functools.cache
def load_fixture(key: str) -> dict[str, Any]:
    return dict(json.loads((FIXTURES / key / "api_surface.json").read_text(encoding="utf-8")))


@functools.cache
def active_documents(key: str) -> tuple[str, ...]:
    """The texts of the documents of the generation the active pointer names."""
    scope_dir = scope_dir_of(PILOTS_BY_KEY[key])
    pointer = json.loads((scope_dir / "active_pointer.json").read_text(encoding="utf-8"))
    version = str(pointer["generation_id"]).rsplit(_ID_SEP, 1)[-1]
    generations = [
        json.loads(path.read_text(encoding="utf-8")) for path in sorted(scope_dir.glob("generation__*.json"))
    ]
    generation = next(g for g in generations if g.get("version") == version)
    documents = generation["payload"]["lexical_index"]["documents"]
    return tuple(str(document.get("text", "")) for document in documents.values())


# -- the pure helpers (each is proven to fail when it should by the synthetic tests below) ---------------------------------------


def fqn_of(text: str) -> str | None:
    match = _FQN_LINE.search(text)
    return None if match is None else match.group(1).strip()


def is_symbol_chunk(text: str) -> bool:
    """A symbol chunk has an ``FQN:`` line that is neither an ``Example: `` nor a ``Doc: `` pseudo-FQN."""
    fqn = fqn_of(text)
    return fqn is not None and not fqn.startswith(("Example: ", "Doc: "))


def kind_of(text: str) -> str | None:
    """The kind on the SECOND line (``Kind: <kind>``), or None when that line is not a kind line."""
    lines = text.split("\n")
    if len(lines) < 2 or not lines[1].startswith("Kind: "):
        return None
    return lines[1][len("Kind: ") :].strip()


def symbol_texts(texts: Iterable[str]) -> list[str]:
    return [text for text in texts if is_symbol_chunk(text)]


def served_pairs(texts: Iterable[str]) -> list[Pair]:
    """The (FQN, Kind) of every symbol chunk; a chunk with no kind line has the kind ``<none>``."""
    return [(str(fqn_of(text)), kind_of(text) or "<none>") for text in symbol_texts(texts)]


def served_counts(texts: Iterable[str]) -> tuple[int, int]:
    """(type chunks, function chunks) among the symbol chunks; ``Doc: `` and ``Example: `` chunks are ignored."""
    functions = sum(1 for text in symbol_texts(texts) if kind_of(text) == FUNCTION_KIND)
    return len(symbol_texts(texts)) - functions, functions


def non_test_entries(fixture: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [entry for entry in fixture["types"] if not _is_test_source(entry.get("file"))]


def entries_from_test_sources(fixture: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [entry for entry in fixture["types"] if _is_test_source(entry.get("file"))]


def recount(entries: Iterable[Mapping[str, Any]]) -> tuple[int, int]:
    """(type entries, function entries) of fixture entries, by ``kind``."""
    kinds = Counter(entry.get("kind") == FUNCTION_KIND for entry in entries)
    return kinds[False], kinds[True]


def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def has_symbol_counts(fixture: Mapping[str, Any]) -> bool:
    counts = fixture.get("symbol_counts")
    return isinstance(counts, Mapping) and _is_count(counts.get("types"))


def fixture_counts(fixture: Mapping[str, Any]) -> tuple[int, int]:
    """(types, functions) the fixture declares: ``symbol_counts`` when present, else the recount of its entries."""
    if has_symbol_counts(fixture):
        counts = fixture["symbol_counts"]
        functions = counts.get("functions")
        return int(counts["types"]), int(functions) if _is_count(functions) else 0
    return recount(fixture["types"])


def expected_pairs(fixture: Mapping[str, Any]) -> list[Pair]:
    """What the builder publishes: (``_unique_fqns`` of the non-test entries, each entry's kind)."""
    entries = non_test_entries(fixture)
    return [
        (fqn, str(entry.get("kind", ""))) for fqn, entry in zip(_unique_fqns(entries), entries, strict=True)
    ]


def multiset_problems(served: Sequence[Pair], expected: Sequence[Pair]) -> list[str]:
    """Nothing missing, nothing extra, no FQN served twice, no type served as a function (or the reverse)."""
    problems: list[str] = []
    fqn_counts = Counter(fqn for fqn, _ in served)
    duplicated = sorted(fqn for fqn, n in fqn_counts.items() if n > 1)
    if duplicated:
        problems.append(f"{len(duplicated)} FQN(s) served more than once, e.g. {duplicated[:3]}")
    expected_kind = {fqn: kind for fqn, kind in expected}
    served_kind = {fqn: kind for fqn, kind in served}
    wrong_kind = sorted(
        fqn for fqn in served_kind if fqn in expected_kind and served_kind[fqn] != expected_kind[fqn]
    )
    if wrong_kind:
        problems.append(
            f"{len(wrong_kind)} symbol(s) served with another kind, e.g. "
            f"{[(f, served_kind[f], expected_kind[f]) for f in wrong_kind[:3]]}"
        )
    missing = sorted((Counter(expected) - Counter(served)).elements())
    extra = sorted((Counter(served) - Counter(expected)).elements())
    only_missing = [pair for pair in missing if pair[0] not in served_kind]
    only_extra = [pair for pair in extra if pair[0] not in expected_kind]
    if only_missing:
        problems.append(f"{len(only_missing)} extracted symbol(s) are not served, e.g. {only_missing[:3]}")
    if only_extra:
        problems.append(f"{len(only_extra)} served symbol(s) were not extracted, e.g. {only_extra[:3]}")
    return problems


def parse_coverage(line: str) -> Coverage | None:
    """The numbers of one ``API-Coverage:`` line, or None when it is malformed."""
    match = _COVERAGE_VALUE.match(line)
    if match is None:
        return None
    nf, mf = match.group(3), match.group(4)
    return Coverage(
        int(match.group(1)),
        int(match.group(2)),
        None if nf is None else int(nf),
        None if mf is None else int(mf),
    )


def shared_coverage(symbols: Sequence[str]) -> tuple[Coverage | None, list[str]]:
    """The one line every symbol chunk states, and the problems: exactly one line per chunk, well formed, the same everywhere."""
    problems: list[str] = []
    seen: set[Coverage] = set()
    for text in symbols:
        lines = _COVERAGE_LINE.findall(text)
        if len(lines) != 1:
            problems.append(
                f"a symbol chunk has {len(lines)} API-Coverage lines, expected 1: {text.split(chr(10), 1)[0][:60]!r}"
            )
            continue
        parsed = parse_coverage(lines[0])
        if parsed is None:
            problems.append(f"malformed API-Coverage line {lines[0]!r}")
            continue
        seen.add(parsed)
    if len(seen) > 1:
        problems.append(f"the symbol chunks state {len(seen)} different API-Coverage lines")
    return (next(iter(seen)) if len(seen) == 1 else None), problems


def coverage_problems(fixture: Mapping[str, Any], texts: Sequence[str]) -> list[str]:
    """The shared line states ``Nt of Mt types`` (and ``Nf of Mf functions`` when the fixture has functions in its
    symbol_counts) with every number equal to the served and the fixture counts, and nothing is partial."""
    symbols = symbol_texts(texts)
    line, problems = shared_coverage(symbols)
    if line is None:
        return problems or ["the generation has no symbol chunk"]
    types, functions = served_counts(texts)
    want_types, want_functions = fixture_counts(fixture) if has_symbol_counts(fixture) else (0, 0)
    if has_symbol_counts(fixture):
        if (line.types_indexed, line.types_total) != (types, want_types):
            problems.append(
                f"the line says {line.types_indexed} of {line.types_total} types; served {types}, fixture {want_types}"
            )
        if want_functions > 0:
            if line.functions_indexed is None:
                problems.append(
                    f"the line has no functions clause; the fixture has {want_functions} functions"
                )
            elif (line.functions_indexed, line.functions_total) != (functions, want_functions):
                problems.append(
                    f"the line says {line.functions_indexed} of {line.functions_total} functions; "
                    f"served {functions}, fixture {want_functions}"
                )
        elif line.functions_indexed is not None or functions:
            problems.append("a functions clause or a function chunk although the fixture has no functions")
    else:
        everything = types + functions
        if line.functions_indexed is not None:
            problems.append("a functions clause on a fixture without symbol_counts")
        if (line.types_indexed, line.types_total) != (everything, fixture.get("type_count")):
            problems.append(
                f"the line says {line.types_indexed} of {line.types_total} types; served {everything}, "
                f"fixture type_count {fixture.get('type_count')}"
            )
    if line.types_indexed != line.types_total:
        problems.append(f"partial index: {line.types_indexed} of {line.types_total} types")
    if line.functions_indexed is not None and line.functions_indexed != line.functions_total:
        problems.append(f"partial index: {line.functions_indexed} of {line.functions_total} functions")
    return problems


def stale_coverage_problems(texts: Sequence[str]) -> list[str]:
    """A stale baked generation agrees with its OWN line: the served counts by kind equal the numbers it states."""
    line, problems = shared_coverage(symbol_texts(texts))
    if line is None:
        return problems or ["the generation has no symbol chunk"]
    types, functions = served_counts(texts)
    if line.functions_indexed is None:
        if line.types_indexed != types + functions:
            problems.append(
                f"the line says {line.types_indexed} types, the generation serves {types + functions} symbol chunks"
            )
    else:
        if line.types_indexed != types:
            problems.append(f"the line says {line.types_indexed} types, the generation serves {types}")
        if line.functions_indexed != functions:
            problems.append(
                f"the line says {line.functions_indexed} functions, the generation serves {functions}"
            )
    return problems


def fixture_completeness_problems(fixture: Mapping[str, Any]) -> list[str]:
    problems: list[str] = []
    if fixture.get("truncated") is not False:
        problems.append(f"the fixture is truncated or does not say it is not: {fixture.get('truncated')!r}")
    entries = fixture["types"]
    if fixture.get("type_count") != len(entries):
        problems.append(f"type_count {fixture.get('type_count')} != {len(entries)} entries")
    test_entries = entries_from_test_sources(fixture)
    if test_entries:
        problems.append(f"{len(test_entries)} test-source entries, e.g. {test_entries[0].get('file')!r}")
    if has_symbol_counts(fixture):
        types, functions = fixture_counts(fixture)
        if types + functions != fixture.get("type_count"):
            problems.append(f"symbol_counts {types} + {functions} != type_count {fixture.get('type_count')}")
        if recount(entries) != (types, functions):
            problems.append(
                f"symbol_counts {(types, functions)} != the recount of the entries by kind {recount(entries)}"
            )
    return problems


# -- the single tests ----------------------------------------------------------------------------------------------------------


def test_every_chart_pilot_is_checked() -> None:
    assert len(PILOTS) >= MIN_PILOTS
    assert len(set(PILOT_KEYS)) == len(PILOT_KEYS)
    for key in PILOT_KEYS:
        assert (FIXTURES / key / "api_surface.json").is_file(), f"{key}: no fixture"
        scope_dir = scope_dir_of(PILOTS_BY_KEY[key])
        generations = sorted(scope_dir.glob("generation__*.json"))
        assert len(generations) == 1, f"{key}: {len(generations)} generation files"
        pointer = json.loads((scope_dir / "active_pointer.json").read_text(encoding="utf-8"))
        version = str(pointer["generation_id"]).rsplit(_ID_SEP, 1)[-1]
        assert generations[0].name == f"generation__{version}.json", (
            f"{key}: the pointer does not name the generation"
        )


def test_the_function_assertions_are_not_vacuous() -> None:
    """The floors are deliberately below the measurement; they prove the function checks run against real function data."""
    per_pilot = {key: recount(load_fixture(key)["types"]) for key in PILOT_KEYS}
    assert sum(1 for _, functions in per_pilot.values() if functions > 0) >= MIN_PILOTS_WITH_FUNCTIONS
    assert sum(functions for _, functions in per_pilot.values()) >= MIN_FUNCTION_ENTRIES
    assert sum(types for types, _ in per_pilot.values()) >= MIN_TYPE_ENTRIES
    # ... and the baked side really serves function chunks (not only the fixture side).
    served = {key: served_counts(active_documents(key)) for key in PILOT_KEYS}
    assert sum(1 for _, functions in served.values() if functions > 0) >= MIN_PILOTS_WITH_FUNCTIONS
    assert sum(functions for _, functions in served.values()) >= MIN_FUNCTION_ENTRIES


def test_no_served_symbol_chunk_is_without_a_kind_line() -> None:
    missing = {
        key: sum(1 for text in symbol_texts(active_documents(key)) if kind_of(text) is None)
        for key in PILOT_KEYS
    }
    assert {key: n for key, n in missing.items() if n} == {}


# -- the per-pilot proof -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("key", PILOT_KEYS)
def test_fixture_is_complete_and_has_no_test_file_entries(key: str) -> None:
    problems = fixture_completeness_problems(load_fixture(key))
    assert not problems, problems


@pytest.mark.parametrize("key", PILOT_KEYS)
def test_served_type_chunks_equal_the_fixture_types(key: str) -> None:
    served_types, _ = served_counts(active_documents(key))
    if key in stale_baked_pilots():
        line, problems = shared_coverage(symbol_texts(active_documents(key)))
        assert not problems, problems
        assert line is not None
        assert served_types == line.types_indexed or line.functions_indexed is None
        assert is_really_stale(key), f"{key} is not stale: delete it from the table"
        return
    fixture_types, _ = fixture_counts(load_fixture(key))
    assert served_types == fixture_types, (
        f"{key}: serves {served_types} type chunks, the fixture has {fixture_types}"
    )


@pytest.mark.parametrize("key", PILOT_KEYS)
def test_served_function_chunks_equal_the_fixture_functions(key: str) -> None:
    _, served_functions = served_counts(active_documents(key))
    if key in stale_baked_pilots():
        problems = stale_coverage_problems(active_documents(key))
        assert not problems, problems
        assert is_really_stale(key), f"{key} is not stale: delete it from the table"
        return
    _, fixture_functions = fixture_counts(load_fixture(key))
    assert served_functions == fixture_functions, (
        f"{key}: serves {served_functions} function chunks, the fixture has {fixture_functions}"
    )


@pytest.mark.parametrize("key", PILOT_KEYS)
def test_served_symbol_fqns_are_exactly_the_fixture_entries(key: str) -> None:
    served = served_pairs(active_documents(key))
    if key in stale_baked_pilots():
        # A stale generation is compared with its own line (the tests above); here only its identities must be unique.
        assert not multiset_problems(served, served)
        return
    problems = multiset_problems(served, expected_pairs(load_fixture(key)))
    assert not problems, problems


@pytest.mark.parametrize("key", PILOT_KEYS)
def test_served_coverage_line_states_the_same_counts(key: str) -> None:
    texts = active_documents(key)
    if key in stale_baked_pilots():
        problems = stale_coverage_problems(texts)
        assert not problems, problems
        assert is_really_stale(key), f"{key} is not stale: delete it from the table"
        return
    problems = coverage_problems(load_fixture(key), texts)
    assert not problems, problems


# -- anti-vacuity: every helper fails when it should ---------------------------------------------------------------------------


def _chunk(fqn: str, kind: str | None, line: str | None = None) -> str:
    lines = [f"FQN: {fqn}"]
    if kind is not None:
        lines.append(f"Kind: {kind}")
    if line is not None:
        lines.append(f"API-Coverage: {line}")
    lines.append("Source-Commit: " + "a" * 40)
    return "\n".join(lines)


def _generation(types: int, functions: int, line: str) -> list[str]:
    return [_chunk(f"T{i}", "class", line) for i in range(types)] + [
        _chunk(f"f{i}", FUNCTION_KIND, line) for i in range(functions)
    ]


def test_the_served_count_helper_counts_a_function_chunk_and_a_type_chunk_apart() -> None:
    texts = [
        _chunk("A", "class"),
        _chunk("B", "interface"),
        _chunk("f", FUNCTION_KIND),
        _chunk("g", FUNCTION_KIND),
    ]
    assert served_counts(texts) == (2, 2)
    assert served_counts([_chunk("A", "class")]) == (1, 0)
    assert served_counts([_chunk("f", FUNCTION_KIND)]) == (0, 1)


def test_the_served_count_helper_ignores_doc_and_example_chunks() -> None:
    texts = [_chunk("A", "class"), _chunk("Doc: Install", None), _chunk("Example: A", None), "Just a page"]
    assert served_counts(texts) == (1, 0)
    assert served_pairs(texts) == [("A", "class")]
    assert not is_symbol_chunk("Just a page")
    assert not is_symbol_chunk(_chunk("Doc: X", "function"))


def test_the_kind_is_read_from_the_second_line_only() -> None:
    assert kind_of("FQN: A\nKind: function\nx") == FUNCTION_KIND
    assert kind_of("FQN: A\nDeprecated: yes\nKind: function") is None
    assert kind_of("FQN: A") is None


def test_the_multiset_check_accepts_equal_pairs() -> None:
    pairs = [("A", "class"), ("f", FUNCTION_KIND)]
    assert multiset_problems(pairs, list(reversed(pairs))) == []


def test_the_multiset_check_flags_a_missing_function_chunk() -> None:
    expected = [("A", "class"), ("f", FUNCTION_KIND), ("g", FUNCTION_KIND)]
    problems = multiset_problems([("A", "class"), ("f", FUNCTION_KIND)], expected)
    assert any("not served" in p and "'g'" in p for p in problems)


def test_the_multiset_check_flags_an_extra_chunk() -> None:
    problems = multiset_problems([("A", "class"), ("Z", "class")], [("A", "class")])
    assert any("were not extracted" in p and "'Z'" in p for p in problems)


def test_the_multiset_check_flags_a_duplicated_fqn() -> None:
    problems = multiset_problems([("A", "class"), ("A", "class")], [("A", "class")])
    assert any("more than once" in p for p in problems)


def test_the_multiset_check_flags_a_type_served_as_a_function() -> None:
    problems = multiset_problems([("A", FUNCTION_KIND)], [("A", "class")])
    assert any("another kind" in p for p in problems)
    assert multiset_problems([("A", "class")], [("A", FUNCTION_KIND)])


def test_the_line_parser_reads_the_types_only_line() -> None:
    assert parse_coverage("API-Coverage: 12 of 12 types") == Coverage(12, 12, None, None)


def test_the_line_parser_reads_the_functions_clause() -> None:
    assert parse_coverage("API-Coverage: 259 of 259 types; 98 of 98 functions") == Coverage(259, 259, 98, 98)


def test_the_line_parser_reads_the_parse_failure_clause() -> None:
    assert parse_coverage(
        "API-Coverage: 3 of 3 types; 2 of 2 functions; 7 of 259 source files could not be parsed"
    ) == Coverage(3, 3, 2, 2)
    assert parse_coverage(
        "API-Coverage: 3 of 3 types; at least 4 source files could not be parsed"
    ) == Coverage(3, 3, None, None)


def test_the_line_parser_flags_a_malformed_line() -> None:
    assert parse_coverage("API-Coverage: lots") is None
    assert parse_coverage("API-Coverage: 3 of 3 types; 2 of 2 widgets") is None
    _, problems = shared_coverage([_chunk("A", "class", "lots")])
    assert any("malformed" in p for p in problems)


def test_the_shared_line_check_flags_two_lines_a_missing_line_and_a_disagreement() -> None:
    assert shared_coverage([_chunk("A", "class", "1 of 1 types")])[1] == []
    assert any("0 API-Coverage lines" in p for p in shared_coverage([_chunk("A", "class")])[1])
    doubled = _chunk("A", "class", "1 of 1 types") + "\nAPI-Coverage: 1 of 1 types"
    assert any("2 API-Coverage lines" in p for p in shared_coverage([doubled])[1])
    mixed = [_chunk("A", "class", "1 of 2 types"), _chunk("B", "class", "2 of 2 types")]
    assert any("different API-Coverage lines" in p for p in shared_coverage(mixed)[1])


_FIXTURE = {"type_count": 5, "truncated": False, "symbol_counts": {"types": 3, "functions": 2}}


def test_the_coverage_check_accepts_an_honest_line_and_flags_every_wrong_number() -> None:
    assert coverage_problems(_FIXTURE, _generation(3, 2, "3 of 3 types; 2 of 2 functions")) == []
    assert coverage_problems(_FIXTURE, _generation(3, 2, "3 of 4 types; 2 of 2 functions"))
    assert coverage_problems(_FIXTURE, _generation(3, 2, "3 of 3 types; 2 of 3 functions"))
    assert coverage_problems(_FIXTURE, _generation(3, 2, "3 of 3 types"))
    assert coverage_problems(
        _FIXTURE, _generation(3, 1, "3 of 3 types; 2 of 2 functions")
    )  # a function chunk is missing
    assert coverage_problems(
        _FIXTURE, _generation(2, 2, "3 of 3 types; 2 of 2 functions")
    )  # a type chunk is missing
    assert any(
        "partial" in p
        for p in coverage_problems(
            {**_FIXTURE, "symbol_counts": {"types": 4, "functions": 2}},
            _generation(3, 2, "3 of 4 types; 2 of 2 functions"),
        )
    )


def test_the_coverage_check_reads_an_older_fixture_by_its_own_entries() -> None:
    old = {"type_count": 5, "truncated": False}
    assert coverage_problems(old, _generation(3, 2, "5 of 5 types")) == []
    assert coverage_problems(old, _generation(3, 2, "5 of 5 types; 2 of 2 functions"))
    assert coverage_problems(old, _generation(3, 2, "3 of 5 types"))


def test_the_coverage_check_flags_a_function_chunk_when_the_fixture_has_no_functions() -> None:
    zero = {"type_count": 3, "truncated": False, "symbol_counts": {"types": 3, "functions": 0}}
    assert coverage_problems(zero, _generation(3, 0, "3 of 3 types")) == []
    assert coverage_problems(zero, _generation(3, 1, "3 of 3 types"))


def test_the_stale_branch_compares_with_the_chunks_own_line() -> None:
    assert stale_coverage_problems(_generation(3, 2, "3 of 3 types; 2 of 2 functions")) == []
    assert stale_coverage_problems(_generation(3, 2, "300 of 901 types; 2 of 2 functions"))
    assert stale_coverage_problems(_generation(3, 1, "3 of 3 types; 2 of 2 functions"))
    assert (
        stale_coverage_problems(_generation(3, 2, "5 of 901 types")) == []
    )  # a types-only line counts every symbol chunk
    # ... and such a generation really is stale against the fixture.
    assert coverage_problems(_FIXTURE, _generation(3, 2, "300 of 901 types; 2 of 2 functions"))


def test_the_fixture_recount_counts_every_entry_by_kind() -> None:
    entries = [{"kind": "class"}, {"kind": "interface"}, {"kind": FUNCTION_KIND}, {"kind": "enum"}, {}]
    assert recount(entries) == (4, 1)
    assert fixture_counts({"types": entries}) == (4, 1)
    assert fixture_counts({"types": entries, "symbol_counts": {"types": 7, "functions": 2}}) == (7, 2)
    assert fixture_counts({"types": entries, "symbol_counts": {"types": True}}) == (4, 1)
    assert fixture_counts({"types": entries, "symbol_counts": {"types": 7}}) == (7, 0)


def test_the_expected_pairs_follow_the_builder_and_skip_entries_from_test_sources() -> None:
    fixture = {
        "types": [
            {"name": "A", "kind": "class", "file": "src/a.py"},
            {"name": "f", "kind": FUNCTION_KIND, "file": "src/a.py"},
            {"name": "t", "kind": FUNCTION_KIND, "file": "tests/test_a.py"},
            {"name": "A", "kind": "class", "file": "src/b.py"},
        ]
    }
    pairs = expected_pairs(fixture)
    assert len(pairs) == 3
    assert ("f", FUNCTION_KIND) in pairs
    assert len({fqn for fqn, _ in pairs}) == 3  # the colliding "A" pair is qualified, so every FQN is unique
    assert len(entries_from_test_sources(fixture)) == 1


def test_the_completeness_check_flags_a_truncated_fixture_a_test_entry_and_a_count_mismatch() -> None:
    good: dict[str, Any] = {
        "type_count": 2,
        "truncated": False,
        "types": [{"kind": "class", "file": "a.py"}, {"kind": "class", "file": "b.py"}],
    }
    assert fixture_completeness_problems(good) == []
    assert fixture_completeness_problems({**good, "truncated": True})
    assert fixture_completeness_problems({**good, "type_count": 3})
    with_test = {**good, "types": [*good["types"][:1], {"kind": "class", "file": "tests/test_b.py"}]}
    assert any("test-source" in p for p in fixture_completeness_problems(with_test))
    counted = {**good, "symbol_counts": {"types": 1, "functions": 1}}
    assert fixture_completeness_problems(counted)  # the entries are two types, not one type and one function
    assert fixture_completeness_problems({**good, "symbol_counts": {"types": 2, "functions": 0}}) == []
