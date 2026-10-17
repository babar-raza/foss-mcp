"""G3/TC-513: the 32 shipped pilots are consistent with their own fixtures, chart pin and baked generations (REQ-G3-008).

The live workflow proof takes the symbol, commit and title from the very fixtures the generation was built from, so it
cannot see a pilot whose data is mixed, thin, or carries unresolved symbols. This test closes that gap offline and
deterministically, from the committed tree only: tests/fixtures/<key>/api_surface.json, infra/helm/foss-mcp/values.yaml
and infra/demo-manifests. For every pilot it asserts:

* the fixture's ``source_commit``, the chart pin (``ingestion.pilots[].library.commit``) and EVERY ``Source-Commit:`` line
  of the baked generation are one and the same commit;
* one generation, a pointer that names it, and an identity (family, platform, source kind, document ids) that is this pilot's;
* the product-reference, recent-releases and package-registry sidecars belong to this pilot;
* every symbol chunk carries an honest ``API-Coverage: N of M types`` line;
* the composition floor (symbol chunks and documentation chunks) holds;
* the fixture's unresolved-symbol count stays within a documented table.

Each documented exception is a table entry with a reason, and the test of the table FAILS as soon as the exception no
longer applies, so an exception cannot outlive its cause (as tests/deploy/test_verified_examples_coverage.py does for
tex/python). The helper functions are pure and return lists of problem strings, so synthetic tests can prove each one
fails when it should.
"""

from __future__ import annotations

import functools
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
FIXTURES = REPO_ROOT / "tests" / "fixtures"

# re-pinned by TC-551: 40 -> 32
EXPECTED_PILOTS = 32
# The same floors TC-510 and TC-506 enforce on the shipped data.
SYMBOL_FLOOR = 3
DOC_FLOOR = 1
# The package registry each platform publishes to. cpp has none, so it has no registry sidecar.
ECOSYSTEM_BY_PLATFORM = {
    "net": "nuget",
    "python": "pypi",
    "typescript": "npm",
    "nodejs": "npm",
    "go": "go",
    "java": "maven",
    "rust": "cargo",
}
NO_REGISTRY_PLATFORM = "cpp"

# pilot -> why its commits are not one. An entry is deleted by the card that re-pins the pilot; the test of the table
# FAILS while a listed pilot is consistent, so the exception cannot be forgotten.
# re-pinned by TC-553: {pdf_typescript: ...} -> {} (the baked pdf/typescript generation is ONE commit, 155bfc7, everywhere)
COMMIT_EXCEPTIONS: dict[str, str] = {}
# pilot -> the allowed MAXIMUM of the fixture's ``unresolved`` list. The entry is removed once the count reaches 0.
UNRESOLVED_EXCEPTIONS = {
    # tex/python: the pinned upstream library does not parse, so its extraction leaves 13 references unresolved (ISOPROV-01).
    "tex_python": 13,
    # email/python: 38 references to types the extraction could not resolve.
    "email_python": 38,
    # words/python: 10 unresolved references.
    "words_python": 10,
    # pdf/python: 4 unresolved references.
    "pdf_python": 4,
    # html/python: 1 unresolved reference.
    "html_python": 1,
}
# pilot -> why its API-Coverage line disagrees with the fixture's ``truncated`` flag. Empty: its only entry (a Go pilot whose
# fixture counted Go functions that are never chunked) left the product with the pilots removed by TC-551. A new entry needs a
# reason, and the test of the table fails while the listed pilot is consistent.
COVERAGE_EXCEPTIONS: dict[str, str] = {}
# pilot -> why its baked generation is checked against ITS OWN API-Coverage lines and not against the fixture.
# transition table: a pilot whose fixture was re-extracted before the supervisor re-ingests it is listed
# here with a reason; empty after TC-553; TC-536 deletes the mechanism
# re-pinned by TC-553: 13 entries -> {} (the thirteen pilots were re-ingested; their baked generations are fresh)
STALE_BAKED_PILOTS: dict[str, str] = {}
# pilot -> why it may sit below the composition floor. Empty: every pilot meets it.
COMPOSITION_EXCEPTIONS: dict[str, str] = {}

_ID_SEP = "::"
TRUNCATION_MARKER = "disagrees with the fixture's truncated"
_FQN_LINE = re.compile(r"^FQN:\s*(.+)$", re.MULTILINE)
_COVERAGE_LINE = re.compile(r"^API-Coverage:.*$", re.MULTILINE)
# The grammar chunk_builder._coverage_line writes: ``Nt of Mt types`` + optional ``; Nf of Mf functions`` + optional
# ``; X of Y source files could not be parsed`` or ``; at least K source files could not be parsed``.
# re-pinned by TC-553: ``N of M types`` only -> the full grammar (a pilot with functions states a functions clause)
_COVERAGE_VALUE = re.compile(
    r"^API-Coverage: (\d+) of (\d+) types"
    r"(?:; (\d+) of (\d+) functions)?"
    r"(?:; (?:(\d+) of (\d+) source files could not be parsed|at least (\d+) source files could not be parsed))?$"
)
_SOURCE_COMMIT_LINE = re.compile(r"^Source-Commit:[ \t]*(\S*)[ \t]*$", re.MULTILINE)

SYMBOL = "symbol"
DOCUMENTATION = "documentation"
EXAMPLE = "example"


# -- the pilots, derived from the chart --------------------------------------------------------------------------------


def chart_pilots() -> list[dict[str, Any]]:
    data = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
    return [dict(p) for p in data["ingestion"]["pilots"]]


def pilot_key(pilot: Mapping[str, Any]) -> str:
    """family_platform, e.g. pdf_net."""
    return f"{pilot['family']}_{pilot['platform']}"


def scope_name(pilot: Mapping[str, Any]) -> str:
    return f"{pilot['family']}__{pilot['platform']}__{pilot['sourceKind']}"


PILOTS = chart_pilots()
PILOT_KEYS = [pilot_key(p) for p in PILOTS]
PILOTS_BY_KEY = {pilot_key(p): p for p in PILOTS}


# -- loading the committed data (each file parsed once) -------------------------------------------------------------------


@functools.cache
def load_fixture(key: str) -> dict[str, Any]:
    return json.loads((FIXTURES / key / "api_surface.json").read_text(encoding="utf-8"))


@functools.cache
def load_scope(key: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The pilot's active pointer and every generation file in its scope directory (parsed once)."""
    scope_dir = DEMO_MANIFESTS / scope_name(PILOTS_BY_KEY[key])
    pointer = json.loads((scope_dir / "active_pointer.json").read_text(encoding="utf-8"))
    generations = [
        json.loads(path.read_text(encoding="utf-8")) for path in sorted(scope_dir.glob("generation__*.json"))
    ]
    return pointer, generations


def active_generation(key: str) -> dict[str, Any]:
    pointer, generations = load_scope(key)
    version = pointer["generation_id"].rsplit(_ID_SEP, 1)[-1]
    return next(g for g in generations if g.get("version") == version)


def chunk_texts(generation: Mapping[str, Any]) -> list[str]:
    documents = generation["payload"]["lexical_index"]["documents"]
    return [str(chunk.get("text", "")) for chunk in documents.values()]


def read_sidecar(name: str) -> Any:
    """A sidecar's parsed JSON, or None when the file is absent."""
    path = DEMO_MANIFESTS / name
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


# -- chunk kinds (the rule of relevance.chunk_kind, reimplemented so no serving code is imported) -----------------------


def chunk_kind(text: str) -> str:
    match = _FQN_LINE.search(text)
    if match is None:
        return DOCUMENTATION
    fqn = match.group(1).strip()
    if fqn.startswith("Example: "):
        return EXAMPLE
    if fqn.startswith("Doc: "):
        return DOCUMENTATION
    return SYMBOL


def texts_of_kind(texts: Iterable[str], kind: str) -> list[str]:
    return [text for text in texts if chunk_kind(text) == kind]


def source_commits_of(texts: Iterable[str]) -> list[str]:
    """Every ``Source-Commit:`` value in the chunks, one entry per line."""
    return [value for text in texts for value in _SOURCE_COMMIT_LINE.findall(text)]


def chunks_without_source_commit(texts: Iterable[str]) -> list[str]:
    """The first line of each chunk that has no ``Source-Commit:`` line with a value."""
    missing: list[str] = []
    for text in texts:
        if not any(_SOURCE_COMMIT_LINE.findall(text)):
            missing.append(text.split("\n", 1)[0][:80])
    return missing


# -- the pure checks: each returns a list of problem strings ---------------------------------------------------------------


def commit_problems(fixture_commit: str, pin: str, source_commits: Sequence[str]) -> list[str]:
    """The fixture commit, the chart pin and every Source-Commit line of the generation must be one commit."""
    problems: list[str] = []
    if not fixture_commit:
        problems.append("the fixture records no source_commit")
    if not pin:
        problems.append("the chart pin (library.commit) is empty")
    if fixture_commit and pin and fixture_commit != pin:
        problems.append(f"the fixture commit {fixture_commit} differs from the chart pin {pin}")
    if not source_commits:
        problems.append("the generation carries no Source-Commit line")
        return problems
    distinct = sorted(set(source_commits))
    if len(distinct) > 1:
        counts = {commit: source_commits.count(commit) for commit in distinct}
        problems.append(f"the generation mixes {len(distinct)} commits: {counts}")
    stray = sorted({commit for commit in source_commits if pin and commit != pin})
    if stray:
        problems.append(f"Source-Commit line(s) {stray} differ from the chart pin {pin}")
    return problems


def scope_problems(
    pilot: Mapping[str, Any], pointer: Mapping[str, Any], generation: Mapping[str, Any]
) -> list[str]:
    """The pointer and the generation both carry this pilot's family::platform::source_kind identity."""
    problems: list[str] = []
    prefix = _ID_SEP.join((pilot["family"], pilot["platform"], pilot["sourceKind"])) + _ID_SEP
    pointer_id = str(pointer.get("generation_id", ""))
    generation_id = str(generation.get("generation_id", ""))
    if not pointer_id.startswith(prefix):
        problems.append(f"the active pointer {pointer_id!r} does not start with {prefix!r}")
    if not generation_id.startswith(prefix):
        problems.append(f"the generation id {generation_id!r} does not start with {prefix!r}")
    if pointer_id != generation_id:
        problems.append(f"the active pointer {pointer_id!r} does not name the generation {generation_id!r}")
    for field, expected in (
        ("family", pilot["family"]),
        ("platform", pilot["platform"]),
        ("source_kind", pilot["sourceKind"]),
    ):
        if generation.get(field) != expected:
            problems.append(
                f"the generation's {field} is {generation.get(field)!r}, the pilot's is {expected!r}"
            )
    lexical = generation.get("payload", {}).get("lexical_index", {})
    if lexical.get("generation_id") != generation_id:
        problems.append(
            f"the lexical index generation_id {lexical.get('generation_id')!r} is not the generation's own {generation_id!r}"
        )
    document_prefix = f"{generation_id}{_ID_SEP}doc{_ID_SEP}"
    foreign = [key for key in lexical.get("documents", {}) if not str(key).startswith(document_prefix)]
    if foreign:
        problems.append(
            f"{len(foreign)} document key(s) do not start with {document_prefix!r}, e.g. {foreign[0]!r}"
        )
    return problems


def sidecar_problems(
    pilot: Mapping[str, Any],
    product_reference: Mapping[str, Any] | None,
    releases: Sequence[Mapping[str, Any]] | None,
    registry: Mapping[str, Any] | None,
) -> list[str]:
    """The three sidecars identify this pilot: its platform, its repository, its pin, its release URLs, its registry."""
    problems: list[str] = []
    platform = pilot["platform"]
    library = pilot.get("library") or {}
    repository = str(library.get("repository", ""))
    pin = str(library.get("commit", ""))
    if product_reference is None:
        problems.append("the product-reference sidecar is absent")
    else:
        if product_reference.get("platform") != platform:
            problems.append(
                f"product reference platform {product_reference.get('platform')!r} is not the pilot's {platform!r}"
            )
        if str(product_reference.get("source_repository", "")).lower() != repository.lower():
            problems.append(
                f"product reference source_repository {product_reference.get('source_repository')!r} "
                f"is not the chart's {repository!r}"
            )
        if product_reference.get("source_ref") != pin:
            problems.append(
                f"product reference source_ref {product_reference.get('source_ref')!r} is not the pin {pin!r}"
            )
        if "family" in product_reference and product_reference["family"] != pilot["family"]:
            problems.append(
                f"product reference family {product_reference['family']!r} is not the pilot's {pilot['family']!r}"
            )
    if releases is None:
        problems.append("the recent-releases sidecar is absent")
    else:
        needle = f"github.com/{repository}/".lower()
        for release in releases:
            url = str(release.get("url", ""))
            if needle not in url.lower():
                problems.append(f"release {release.get('tag_name')!r} url {url!r} is outside {repository!r}")
    if platform == NO_REGISTRY_PLATFORM:
        if registry is not None:
            problems.append(
                f"a {NO_REGISTRY_PLATFORM} pilot has no package registry, but a registry sidecar exists"
            )
    elif platform not in ECOSYSTEM_BY_PLATFORM:
        problems.append(f"platform {platform!r} has no expected package-registry ecosystem")
    elif registry is None:
        problems.append("the package-registry sidecar is absent")
    elif registry.get("ecosystem") != ECOSYSTEM_BY_PLATFORM[platform]:
        problems.append(
            f"registry ecosystem {registry.get('ecosystem')!r} is not {ECOSYSTEM_BY_PLATFORM[platform]!r} for {platform}"
        )
    return problems


def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_function_chunk(text: str) -> bool:
    """A function chunk is one whose SECOND line is ``Kind: function`` (the layout ``_format_type_text`` writes)."""
    lines = text.split("\n")
    return len(lines) > 1 and lines[1].strip() == "Kind: function"


def coverage_problems(fixture: Mapping[str, Any], symbol_texts: Sequence[str]) -> list[str]:
    """Every symbol chunk states the one shared ``API-Coverage:`` line, and the line is honest about the fixture.

    A fixture with ``symbol_counts`` (an integer ``types``) is read by kind: ``Nt`` is the number of non-function
    symbol chunks and ``Mt`` is ``symbol_counts.types``; with ``symbol_counts.functions > 0`` the line carries a
    functions clause whose ``Nf`` is the number of ``Kind: function`` chunks and whose ``Mf`` is
    ``symbol_counts.functions``; with 0 functions neither a clause nor a function chunk is allowed. A fixture without
    ``symbol_counts`` keeps the old rule: N is every symbol chunk, M the fixture's ``type_count``, and a functions
    clause is a problem. In both, the shortfall must equal the fixture's ``truncated`` flag, and a parse-failure
    clause must agree with a ``parse_failures`` mapping when the fixture has one."""
    problems: list[str] = []
    truncated = bool(fixture.get("truncated"))
    counts = fixture.get("symbol_counts")
    by_kind = isinstance(counts, Mapping) and _is_count(counts.get("types"))
    function_chunks = sum(1 for text in symbol_texts if _is_function_chunk(text))
    type_chunks = len(symbol_texts) - function_chunks
    seen: set[tuple[Any, ...]] = set()
    for text in symbol_texts:
        lines = _COVERAGE_LINE.findall(text)
        if len(lines) != 1:
            problems.append(
                f"a symbol chunk has {len(lines)} API-Coverage lines, expected 1: {text.split(chr(10), 1)[0][:60]!r}"
            )
            continue
        match = _COVERAGE_VALUE.match(lines[0])
        if match is None:
            problems.append(f"malformed API-Coverage line {lines[0]!r}")
            continue
        seen.add(tuple(None if group is None else int(group) for group in match.groups()))
    parse_failures = fixture.get("parse_failures")
    for nt, mt, nf, mf, failed, total, _at_least in sorted(seen, key=repr):
        if by_kind:
            counts_map = cast(Mapping[str, Any], counts)
            want_types = counts_map["types"]
            want_functions = counts_map["functions"] if _is_count(counts_map.get("functions")) else 0
            if nt != type_chunks:
                problems.append(f"API-Coverage says N={nt}, the generation holds {type_chunks} type chunks")
            if mt != want_types:
                problems.append(
                    f"API-Coverage says M={mt}, the fixture's symbol_counts.types is {want_types}"
                )
            if want_functions > 0:
                if nf is None:
                    problems.append(
                        f"API-Coverage has no functions clause, the fixture has {want_functions} functions"
                    )
                else:
                    if nf != function_chunks:
                        problems.append(
                            f"API-Coverage says Nf={nf}, the generation holds {function_chunks} function chunks"
                        )
                    if mf != want_functions:
                        problems.append(
                            f"API-Coverage says Mf={mf}, the fixture's symbol_counts.functions is {want_functions}"
                        )
            else:
                if nf is not None:
                    problems.append("API-Coverage has a functions clause, the fixture has no functions")
                if function_chunks:
                    problems.append(f"{function_chunks} function chunk(s), the fixture has no functions")
            shortfall = nt < mt or (nf is not None and mf is not None and nf < mf)
            readout = f"{nt} of {mt}" + ("" if nf is None else f"; {nf} of {mf} functions")
        else:
            expected_m = fixture.get("type_count")
            if nf is not None:
                problems.append("API-Coverage has a functions clause, the fixture has no symbol_counts")
            if nt != len(symbol_texts):
                problems.append(
                    f"API-Coverage says N={nt}, the generation holds {len(symbol_texts)} symbol chunks"
                )
            if mt != expected_m:
                problems.append(f"API-Coverage says M={mt}, the fixture's type_count is {expected_m}")
            shortfall = nt < mt
            readout = f"{nt} of {mt}"
        if shortfall != truncated:
            problems.append(f"API-Coverage {readout} {TRUNCATION_MARKER}={truncated}")
        if failed is not None and isinstance(parse_failures, Mapping):
            if (failed, total) != (parse_failures.get("failed"), parse_failures.get("total")):
                problems.append(
                    f"API-Coverage says {failed} of {total} source files could not be parsed, the fixture's "
                    f"parse_failures say {parse_failures.get('failed')} of {parse_failures.get('total')}"
                )
    return problems


def baked_self_view(symbol_texts: Sequence[str]) -> dict[str, Any]:
    """The fixture-shaped view a baked generation states about itself.

    A first line WITHOUT a functions clause gives ``type_count`` = its M and ``truncated`` = whether the symbol-chunk
    count N is below it (the old view). A first line WITH one gives ``symbol_counts`` {types: Mt, functions: Mf},
    ``type_count`` = Mt + Mf and ``truncated`` from the two comparisons (non-function chunks below Mt, or function
    chunks below Mf). ``coverage_problems`` on this view flags a generation whose lines disagree with each other or
    with the symbol-chunk count."""
    for text in symbol_texts:
        for line in _COVERAGE_LINE.findall(text):
            match = _COVERAGE_VALUE.match(line)
            if match is None:
                continue
            mt = int(match.group(2))
            if match.group(4) is None:
                return {"type_count": mt, "truncated": len(symbol_texts) < mt}
            mf = int(match.group(4))
            function_chunks = sum(1 for t in symbol_texts if _is_function_chunk(t))
            return {
                "symbol_counts": {"types": mt, "functions": mf},
                "type_count": mt + mf,
                "truncated": len(symbol_texts) - function_chunks < mt or function_chunks < mf,
            }
    return {"type_count": None, "truncated": False}


def composition_problems(symbols: int, docs: int) -> list[str]:
    problems: list[str] = []
    if symbols < SYMBOL_FLOOR:
        problems.append(f"{symbols} symbol chunk(s), the floor is {SYMBOL_FLOOR}")
    if docs < DOC_FLOOR:
        problems.append(f"{docs} documentation chunk(s), the floor is {DOC_FLOOR}")
    return problems


def unresolved_problems(key: str, actual: int, table: Mapping[str, int] | None = None) -> list[str]:
    """The fixture's unresolved count may not exceed the documented maximum; an unlisted pilot's maximum is 0."""
    allowed = (UNRESOLVED_EXCEPTIONS if table is None else table).get(key, 0)
    if actual > allowed:
        listed = "listed" if key in (UNRESOLVED_EXCEPTIONS if table is None else table) else "not listed"
        return [f"{key}: {actual} unresolved symbol(s), the documented maximum is {allowed} ({listed})"]
    return []


# -- derived facts of one pilot ------------------------------------------------------------------------------------------


def problems_commit(key: str) -> list[str]:
    pilot = PILOTS_BY_KEY[key]
    fixture = load_fixture(key)
    texts = chunk_texts(active_generation(key))
    return commit_problems(
        str(fixture.get("source_commit") or ""),
        str((pilot.get("library") or {}).get("commit") or ""),
        source_commits_of(texts),
    )


def unresolved_count(key: str) -> int:
    unresolved = load_fixture(key).get("unresolved")
    return len(unresolved) if isinstance(unresolved, list) else 0


# -- the chart ----------------------------------------------------------------------------------------------------------


def test_the_chart_names_thirty_two_pilots() -> None:
    assert len(PILOTS) == EXPECTED_PILOTS
    assert len(set(PILOT_KEYS)) == EXPECTED_PILOTS


# -- the assertions on the shipped data, one per pilot ----------------------------------------------------------------------


@pytest.mark.parametrize("key", PILOT_KEYS)
def test_fixture_commit_equals_the_pin_equals_every_source_commit_line(key: str) -> None:
    problems = problems_commit(key)
    if key in COMMIT_EXCEPTIONS:
        # A documented exception is asserted, not skipped: it must still be inconsistent (the table test says when it is not).
        assert problems, f"{key} is consistent now; delete the exception"
        return
    assert not problems, problems


@pytest.mark.parametrize("key", PILOT_KEYS)
def test_every_chunk_carries_a_source_commit_line(key: str) -> None:
    missing = chunks_without_source_commit(chunk_texts(active_generation(key)))
    assert not missing, f"{len(missing)} chunk(s) carry no Source-Commit line, e.g. {missing[:3]}"


@pytest.mark.parametrize("key", PILOT_KEYS)
def test_scope_has_one_generation_a_matching_pointer_and_its_own_identity(key: str) -> None:
    pointer, generations = load_scope(key)
    assert len(generations) == 1, f"{key} bakes {len(generations)} generations, expected exactly 1"
    assert not scope_problems(PILOTS_BY_KEY[key], pointer, active_generation(key))


@pytest.mark.parametrize("key", PILOT_KEYS)
def test_sidecars_identify_this_pilot(key: str) -> None:
    problems = sidecar_problems(
        PILOTS_BY_KEY[key],
        read_sidecar(f"product_reference_{key}.json"),
        read_sidecar(f"recent_releases_{key}.json"),
        read_sidecar(f"package_registry_{key}.json"),
    )
    assert not problems, problems


@pytest.mark.parametrize("key", PILOT_KEYS)
def test_api_coverage_lines_are_present_and_honest(key: str) -> None:
    symbols = texts_of_kind(chunk_texts(active_generation(key)), SYMBOL)
    assert symbols, f"{key} bakes no symbol chunk"
    if key in STALE_BAKED_PILOTS:
        # The fixture was re-extracted; the baked generation is checked against its OWN lines until the re-ingest.
        problems = coverage_problems(baked_self_view(symbols), symbols)
        assert not problems, problems
        return
    problems = coverage_problems(load_fixture(key), symbols)
    if key in COVERAGE_EXCEPTIONS:
        # Asserted, not skipped: only the truncation reading may disagree; N and M must stay honest.
        assert problems, f"{key} is consistent now; delete the exception"
        assert all(TRUNCATION_MARKER in p for p in problems), problems
        return
    assert not problems, problems


@pytest.mark.parametrize("key", PILOT_KEYS)
def test_the_composition_floor_holds(key: str) -> None:
    texts = chunk_texts(active_generation(key))
    problems = composition_problems(
        len(texts_of_kind(texts, SYMBOL)), len(texts_of_kind(texts, DOCUMENTATION))
    )
    assert not problems, problems


@pytest.mark.parametrize("key", PILOT_KEYS)
def test_unresolved_symbols_stay_within_the_documented_table(key: str) -> None:
    problems = unresolved_problems(key, unresolved_count(key))
    assert not problems, problems


# -- the exception tables ---------------------------------------------------------------------------------------------------


def test_every_exception_names_a_pilot_of_the_chart() -> None:
    for table in (
        COMMIT_EXCEPTIONS,
        UNRESOLVED_EXCEPTIONS,
        COVERAGE_EXCEPTIONS,
        COMPOSITION_EXCEPTIONS,
        STALE_BAKED_PILOTS,
    ):
        unknown = sorted(set(table) - set(PILOT_KEYS))
        assert not unknown, f"exception(s) for pilot(s) the chart does not name: {unknown}"


def test_every_commit_exception_still_applies() -> None:
    # A loop, not a parametrisation: an emptied table must leave a passing test, not a skipped one.
    for key in sorted(COMMIT_EXCEPTIONS):
        assert problems_commit(key), (
            f"{key} now has one commit everywhere; delete the exception from COMMIT_EXCEPTIONS"
        )


def test_every_coverage_exception_still_applies() -> None:
    for key in sorted(COVERAGE_EXCEPTIONS):
        symbols = texts_of_kind(chunk_texts(active_generation(key)), SYMBOL)
        assert coverage_problems(load_fixture(key), symbols), (
            f"{key} now states an honest API-Coverage line; delete the exception from COVERAGE_EXCEPTIONS"
        )


def test_every_stale_baked_pilot_still_applies() -> None:
    for key in sorted(STALE_BAKED_PILOTS):
        symbols = texts_of_kind(chunk_texts(active_generation(key)), SYMBOL)
        assert coverage_problems(load_fixture(key), symbols), (
            "the baked generation matches the fixture again; delete the pilot from STALE_BAKED_PILOTS"
        )


def test_stale_baked_pilots_are_disjoint_from_the_coverage_exceptions() -> None:
    both = sorted(set(STALE_BAKED_PILOTS) & set(COVERAGE_EXCEPTIONS))
    assert not both, f"pilot(s) in both STALE_BAKED_PILOTS and COVERAGE_EXCEPTIONS: {both}"


def test_every_unresolved_exception_still_applies() -> None:
    for key in sorted(UNRESOLVED_EXCEPTIONS):
        assert unresolved_count(key) > 0, (
            f"{key} now has no unresolved symbol; delete the exception from UNRESOLVED_EXCEPTIONS"
        )


def test_the_composition_exception_table_is_empty() -> None:
    assert COMPOSITION_EXCEPTIONS == {}


def test_every_exception_carries_a_reason() -> None:
    for key, reason in COMMIT_EXCEPTIONS.items():
        assert isinstance(reason, str) and reason.strip(), f"{key} has no reason"
    for key, reason in COVERAGE_EXCEPTIONS.items():
        assert isinstance(reason, str) and reason.strip(), f"{key} has no reason"
    for key, reason in COMPOSITION_EXCEPTIONS.items():
        assert isinstance(reason, str) and reason.strip(), f"{key} has no reason"
    for key, reason in STALE_BAKED_PILOTS.items():
        assert isinstance(reason, str) and reason.strip(), f"{key} has no reason"


def test_unresolved_table_values_are_positive_ints() -> None:
    for key, maximum in UNRESOLVED_EXCEPTIONS.items():
        assert isinstance(maximum, int) and not isinstance(maximum, bool) and maximum > 0, key


# -- synthetic tests: every helper fails when it should --------------------------------------------------------------------------

A = "a" * 40
B = "b" * 40


def test_commit_problems_flags_mixed_commits() -> None:
    assert commit_problems(A, A, [A, A, A]) == []
    problems = commit_problems(A, A, [A, A, B])
    assert any("mixes 2 commits" in p for p in problems)
    assert any(B in p and "differ from the chart pin" in p for p in problems)


def test_commit_problems_flags_a_fixture_pin_mismatch() -> None:
    problems = commit_problems(A, B, [B])
    assert any("differs from the chart pin" in p for p in problems)
    assert any("records no source_commit" in p for p in commit_problems("", A, [A]))
    assert any("pin" in p and "empty" in p for p in commit_problems(A, "", [A]))


def test_commit_problems_flags_a_generation_without_commit_lines() -> None:
    assert any("no Source-Commit line" in p for p in commit_problems(A, A, []))
    assert chunks_without_source_commit(["FQN: X\nSource-Commit: " + A, "FQN: Y\nno commit here"]) == [
        "FQN: Y"
    ]
    assert chunks_without_source_commit(["FQN: Z\nSource-Commit:"]) == ["FQN: Z"]


def _pilot() -> dict[str, Any]:
    return {
        "family": "pdf",
        "platform": "net",
        "sourceKind": "self_extracted",
        "library": {"repository": "Aspose-Pdf-Foss/Aspose.PDF", "commit": A},
    }


def _generation(generation_id: str = "pdf::net::self_extracted::v1") -> dict[str, Any]:
    return {
        "family": "pdf",
        "platform": "net",
        "source_kind": "self_extracted",
        "generation_id": generation_id,
        "payload": {
            "lexical_index": {
                "generation_id": generation_id,
                "documents": {f"{generation_id}::doc::1": {"text": "FQN: A"}},
            }
        },
    }


def test_scope_problems_flag_a_foreign_family_prefix_and_foreign_document_ids() -> None:
    pilot = _pilot()
    good = _generation()
    assert scope_problems(pilot, {"generation_id": good["generation_id"]}, good) == []
    foreign = _generation("slides::net::self_extracted::v1")
    problems = scope_problems(pilot, {"generation_id": foreign["generation_id"]}, foreign)
    assert any("does not start with" in p for p in problems)
    wrong_field = {**good, "family": "slides"}
    assert any(
        "family" in p for p in scope_problems(pilot, {"generation_id": good["generation_id"]}, wrong_field)
    )
    assert any(
        "does not name the generation" in p
        for p in scope_problems(pilot, {"generation_id": "pdf::net::self_extracted::v2"}, good)
    )
    bad_lexical = _generation()
    bad_lexical["payload"]["lexical_index"]["generation_id"] = "pdf::net::self_extracted::v9"
    assert any(
        "lexical index" in p
        for p in scope_problems(pilot, {"generation_id": bad_lexical["generation_id"]}, bad_lexical)
    )
    foreign_docs = _generation()
    foreign_docs["payload"]["lexical_index"]["documents"]["slides::net::self_extracted::v1::doc::7"] = {
        "text": "x"
    }
    assert any(
        "document key" in p
        for p in scope_problems(pilot, {"generation_id": foreign_docs["generation_id"]}, foreign_docs)
    )


def _sidecars() -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    return (
        {"platform": "net", "source_repository": "aspose-pdf-foss/aspose.pdf", "source_ref": A},
        [{"tag_name": "v1", "url": "https://github.com/aspose-pdf-foss/Aspose.PDF/releases/tag/v1"}],
        {"ecosystem": "nuget", "coordinate": "X"},
    )


def test_sidecar_problems_flag_a_wrong_platform_repository_pin_family_ecosystem_and_release_url() -> None:
    pilot = _pilot()
    reference, releases, registry = _sidecars()
    assert sidecar_problems(pilot, reference, releases, registry) == []
    assert any(
        "platform" in p
        for p in sidecar_problems(pilot, {**reference, "platform": "python"}, releases, registry)
    )
    assert any(
        "source_repository" in p
        for p in sidecar_problems(pilot, {**reference, "source_repository": "x/y"}, releases, registry)
    )
    assert any(
        "source_ref" in p for p in sidecar_problems(pilot, {**reference, "source_ref": B}, releases, registry)
    )
    assert any(
        "family" in p for p in sidecar_problems(pilot, {**reference, "family": "slides"}, releases, registry)
    )
    assert sidecar_problems(pilot, {**reference, "family": "pdf"}, releases, registry) == []
    foreign_release = [{"tag_name": "v2", "url": "https://github.com/other/Repo/releases/tag/v2"}]
    assert any("outside" in p for p in sidecar_problems(pilot, reference, foreign_release, registry))
    assert any("ecosystem" in p for p in sidecar_problems(pilot, reference, releases, {"ecosystem": "pypi"}))
    assert any(
        "registry sidecar is absent" in p or "package-registry" in p
        for p in sidecar_problems(pilot, reference, releases, None)
    )
    assert any("absent" in p for p in sidecar_problems(pilot, None, releases, registry))
    assert any("absent" in p for p in sidecar_problems(pilot, reference, None, registry))


def test_a_cpp_pilot_with_a_registry_sidecar_is_flagged() -> None:
    pilot = {**_pilot(), "platform": "cpp"}
    reference = {"platform": "cpp", "source_repository": "aspose-pdf-foss/aspose.pdf", "source_ref": A}
    releases: list[dict[str, Any]] = []
    assert sidecar_problems(pilot, reference, releases, None) == []
    problems = sidecar_problems(pilot, reference, releases, {"ecosystem": "nuget"})
    assert any("has no package registry" in p for p in problems)
    unknown = {**_pilot(), "platform": "cobol"}
    assert any(
        "no expected package-registry ecosystem" in p
        for p in sidecar_problems(unknown, {**reference, "platform": "cobol"}, releases, None)
    )


def _symbol_text(name: str, n: int, m: int) -> str:
    return f"FQN: {name}\nKind: class\nAPI-Coverage: {n} of {m} types\nSource-Commit: {A}"


def test_coverage_problems_flag_a_missing_line_a_wrong_count_and_a_wrong_total() -> None:
    fixture = {"type_count": 5, "truncated": True}
    good = [_symbol_text(f"T{i}", 3, 5) for i in range(3)]
    assert coverage_problems(fixture, good) == []
    assert (
        coverage_problems(
            {"type_count": 3, "truncated": False}, [_symbol_text(f"T{i}", 3, 3) for i in range(3)]
        )
        == []
    )
    missing = [*good[:2], "FQN: T9\nKind: class"]
    assert any("0 API-Coverage lines" in p for p in coverage_problems(fixture, missing))
    doubled = [*good[:2], good[2] + "\nAPI-Coverage: 3 of 5 types"]
    assert any("2 API-Coverage lines" in p for p in coverage_problems(fixture, doubled))
    assert any(
        "N=4" in p for p in coverage_problems(fixture, [_symbol_text(f"T{i}", 4, 5) for i in range(3)])
    )
    assert any(
        "M=9" in p for p in coverage_problems(fixture, [_symbol_text(f"T{i}", 3, 9) for i in range(3)])
    )
    assert any("malformed" in p for p in coverage_problems(fixture, ["FQN: T\nAPI-Coverage: lots"]))
    # N < M with a fixture that does not say it is truncated, and N == M with a fixture that says it is.
    assert any("truncated" in p for p in coverage_problems({"type_count": 5, "truncated": False}, good))
    assert any(
        "truncated" in p
        for p in coverage_problems(
            {"type_count": 3, "truncated": True}, [_symbol_text(f"T{i}", 3, 3) for i in range(3)]
        )
    )


def test_baked_self_view_flags_an_inconsistent_generation() -> None:
    consistent = [_symbol_text(f"T{i}", 3, 5) for i in range(3)]
    view = baked_self_view(consistent)
    assert view == {"type_count": 5, "truncated": True}
    assert coverage_problems(view, consistent) == []
    complete = [_symbol_text(f"T{i}", 3, 3) for i in range(3)]
    assert coverage_problems(baked_self_view(complete), complete) == []
    # Lines that disagree on M: the second chunk says 9 where the first says 5.
    disagreeing = [consistent[0], _symbol_text("T1", 3, 9), consistent[2]]
    assert any("M=9" in p for p in coverage_problems(baked_self_view(disagreeing), disagreeing))
    # An N that is not the symbol-chunk count: every line says N=4 for 3 chunks.
    wrong_n = [_symbol_text(f"T{i}", 4, 5) for i in range(3)]
    assert any("N=4" in p for p in coverage_problems(baked_self_view(wrong_n), wrong_n))
    # A generation without any parsable line has no view and is flagged chunk by chunk.
    unlabelled = ["FQN: T0\nKind: class", "FQN: T1\nKind: class"]
    assert baked_self_view(unlabelled) == {"type_count": None, "truncated": False}
    assert any(
        "0 API-Coverage lines" in p for p in coverage_problems(baked_self_view(unlabelled), unlabelled)
    )


def test_composition_problems_flag_two_symbols_and_zero_docs() -> None:
    assert composition_problems(SYMBOL_FLOOR, DOC_FLOOR) == []
    assert any("symbol" in p for p in composition_problems(2, 5))
    assert any("documentation" in p for p in composition_problems(10, 0))
    assert len(composition_problems(0, 0)) == 2
    kinds = [chunk_kind(t) for t in ("FQN: A.B", "FQN: Doc: Install", "FQN: Example: A", "Just a page")]
    assert kinds == [SYMBOL, DOCUMENTATION, EXAMPLE, DOCUMENTATION]


def test_unresolved_problems_flag_growth_over_the_table_and_any_unlisted_pilot() -> None:
    table = {"tex_python": 13}
    assert unresolved_problems("tex_python", 13, table) == []
    assert unresolved_problems("tex_python", 0, table) == []
    assert any("14 unresolved" in p for p in unresolved_problems("tex_python", 14, table))
    assert any("not listed" in p for p in unresolved_problems("pdf_net", 1, table))
    assert unresolved_problems("pdf_net", 0, table) == []
    assert any("unresolved" in p for p in unresolved_problems("pdf_net", 1, {}))


# -- synthetic tests of the API-Coverage grammar with a functions clause (TC-553) ----------------------------------------------


def _kind_text(name: str, kind: str, line: str) -> str:
    return f"FQN: {name}\nKind: {kind}\nAPI-Coverage: {line}\nSource-Commit: {A}"


def _mixed(types: int, functions: int, line: str) -> list[str]:
    """``types`` class chunks and ``functions`` function chunks, every one stating *line*."""
    return [_kind_text(f"T{i}", "class", line) for i in range(types)] + [
        _kind_text(f"f{i}", "function", line) for i in range(functions)
    ]


_COUNTED = {"type_count": 5, "symbol_counts": {"types": 3, "functions": 2}, "truncated": False}


def test_coverage_grammar_accepts_the_types_only_line() -> None:
    fixture = {"type_count": 3, "symbol_counts": {"types": 3, "functions": 0}, "truncated": False}
    assert coverage_problems(fixture, _mixed(3, 0, "3 of 3 types")) == []
    # The old reading is untouched: a fixture with only type_count and a plain line.
    assert coverage_problems({"type_count": 3, "truncated": False}, _mixed(3, 0, "3 of 3 types")) == []


def test_coverage_grammar_accepts_a_functions_clause() -> None:
    assert coverage_problems(_COUNTED, _mixed(3, 2, "3 of 3 types; 2 of 2 functions")) == []
    match = _COVERAGE_VALUE.match("API-Coverage: 259 of 259 types; 98 of 98 functions")
    assert match is not None and match.group(3) == "98" and match.group(4) == "98"


def test_coverage_grammar_accepts_a_parse_failure_clause() -> None:
    clause = "3 of 3 types; 2 of 2 functions; 7 of 259 source files could not be parsed"
    fixture = {**_COUNTED, "parse_failures": {"engine": "tree-sitter", "failed": 7, "total": 259}}
    assert coverage_problems(fixture, _mixed(3, 2, clause)) == []
    # The "at least" form and a types-only line with a parse clause are part of the grammar.
    assert _COVERAGE_VALUE.match("API-Coverage: 3 of 3 types; at least 4 source files could not be parsed")
    assert _COVERAGE_VALUE.match("API-Coverage: 3 of 3 types; 7 of 259 source files could not be parsed")
    at_least = "3 of 3 types; 2 of 2 functions; at least 4 source files could not be parsed"
    assert coverage_problems(_COUNTED, _mixed(3, 2, at_least)) == []
    # A fixture whose parse_failures disagree with the numbers of the clause is flagged.
    wrong = {**_COUNTED, "parse_failures": {"engine": "tree-sitter", "failed": 8, "total": 259}}
    assert any("could not be parsed" in p for p in coverage_problems(wrong, _mixed(3, 2, clause)))


def test_coverage_grammar_rejects_a_functions_clause_that_disagrees_with_the_fixture() -> None:
    problems = coverage_problems(_COUNTED, _mixed(3, 2, "3 of 3 types; 2 of 9 functions"))
    assert any("Mf=9" in p for p in problems)
    problems = coverage_problems(_COUNTED, _mixed(3, 2, "3 of 3 types; 5 of 2 functions"))
    assert any("Nf=5" in p for p in problems)
    # The clause is missing although the fixture has functions.
    assert any("no functions clause" in p for p in coverage_problems(_COUNTED, _mixed(3, 2, "3 of 3 types")))


def test_coverage_grammar_rejects_a_wrong_types_count() -> None:
    assert any(
        "N=4" in p for p in coverage_problems(_COUNTED, _mixed(3, 2, "4 of 3 types; 2 of 2 functions"))
    )
    assert any(
        "M=9" in p for p in coverage_problems(_COUNTED, _mixed(3, 2, "3 of 9 types; 2 of 2 functions"))
    )


def test_coverage_grammar_rejects_two_lines() -> None:
    texts = _mixed(3, 2, "3 of 3 types; 2 of 2 functions")
    texts[0] += "\nAPI-Coverage: 3 of 3 types; 2 of 2 functions"
    assert any("2 API-Coverage lines" in p for p in coverage_problems(_COUNTED, texts))


def test_coverage_grammar_rejects_a_missing_line() -> None:
    texts = _mixed(3, 2, "3 of 3 types; 2 of 2 functions")
    texts[4] = "FQN: f1\nKind: function"
    assert any("0 API-Coverage lines" in p for p in coverage_problems(_COUNTED, texts))
    assert any(
        "malformed" in p for p in coverage_problems(_COUNTED, [_kind_text("T", "class", "3 of 3 functions")])
    )


def test_truncated_reading_is_unchanged() -> None:
    line = "2 of 3 types; 2 of 2 functions"
    truncated_fixture = {**_COUNTED, "truncated": True}
    assert coverage_problems(truncated_fixture, _mixed(2, 2, line)) == []
    assert any(TRUNCATION_MARKER in p for p in coverage_problems(_COUNTED, _mixed(2, 2, line)))
    # A shortfall in the functions alone is a truncation too.
    short_functions = "3 of 3 types; 1 of 2 functions"
    assert coverage_problems(truncated_fixture, _mixed(3, 1, short_functions)) == []
    assert any(TRUNCATION_MARKER in p for p in coverage_problems(_COUNTED, _mixed(3, 1, short_functions)))
    # A complete line with a fixture that says it is truncated.
    assert any(
        TRUNCATION_MARKER in p
        for p in coverage_problems(truncated_fixture, _mixed(3, 2, "3 of 3 types; 2 of 2 functions"))
    )


def test_coverage_grammar_rejects_a_functions_clause_on_a_fixture_without_symbol_counts() -> None:
    old_fixture = {"type_count": 5, "truncated": False}
    problems = coverage_problems(old_fixture, _mixed(3, 2, "5 of 5 types; 2 of 2 functions"))
    assert any("no symbol_counts" in p for p in problems)
    # Without symbol_counts a function chunk is simply a symbol chunk: N counts all five.
    assert coverage_problems(old_fixture, _mixed(3, 2, "5 of 5 types")) == []


def test_coverage_grammar_rejects_a_function_chunk_counted_as_a_type() -> None:
    # The fixture has 3 types and 2 functions; the line counts all five chunks as types.
    problems = coverage_problems(_COUNTED, _mixed(3, 2, "5 of 3 types; 2 of 2 functions"))
    assert any("N=5" in p for p in problems)
    zero_functions = {"type_count": 3, "symbol_counts": {"types": 3, "functions": 0}, "truncated": False}
    assert any("function chunk" in p for p in coverage_problems(zero_functions, _mixed(3, 1, "3 of 3 types")))
    assert any(
        "has a functions clause" in p
        for p in coverage_problems(zero_functions, _mixed(3, 0, "3 of 3 types; 0 of 0 functions"))
    )


def test_coverage_grammar_rejects_a_missing_function_chunk_while_the_fixture_has_functions() -> None:
    # The line promises 2 of 2 functions but only one function chunk is served.
    problems = coverage_problems(_COUNTED, _mixed(3, 1, "3 of 3 types; 2 of 2 functions"))
    assert any("Nf=2" in p for p in problems)


def test_baked_self_view_of_a_line_without_the_functions_clause() -> None:
    texts = _mixed(3, 0, "3 of 5 types")
    assert baked_self_view(texts) == {"type_count": 5, "truncated": True}
    assert coverage_problems(baked_self_view(texts), texts) == []


def test_baked_self_view_of_a_line_with_the_functions_clause() -> None:
    line = "3 of 3 types; 2 of 2 functions"
    texts = _mixed(3, 2, line)
    view = baked_self_view(texts)
    assert view == {"symbol_counts": {"types": 3, "functions": 2}, "type_count": 5, "truncated": False}
    assert coverage_problems(view, texts) == []
    # A generation that lost a function chunk after its line was written is truncated by its own account.
    partial = _mixed(3, 1, line)
    partial_view = baked_self_view(partial)
    assert partial_view["truncated"] is True
    assert coverage_problems(partial_view, partial) != []


def test_the_stale_branch_reads_a_functions_line_against_its_own_chunks() -> None:
    texts = _mixed(3, 2, "3 of 3 types; 2 of 2 functions")
    assert coverage_problems(baked_self_view(texts), texts) == []
    # ... and flags a generation that lost a function chunk after the line was written.
    assert coverage_problems(baked_self_view(texts), texts[:-1]) != []
