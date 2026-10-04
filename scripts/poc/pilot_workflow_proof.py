"""G2/TC-197: a full MCP workflow proof for one POC pilot, against a live serving endpoint.

The earlier live POC check called search_symbols once per pilot. That shows a symbol exists.
This script runs eleven checks, in order, against one pilot's live /mcp endpoint. The checks
cover lookup, symbol detail, members, documentation, examples, install guidance, freshness,
recent changes, honest misses and scope isolation. Every answer is checked against that pilot's
own fixtures: tests/fixtures/<pilot>/api_surface.json and, when one exists,
tests/fixtures/furnished/<pilot>/pages/_index.md. No expected value is chosen by hand.

Each check records pass, fail or not_applicable with its evidence. A check that cannot run is a
fail with its reason, never a silent skip. The script writes one JSON report and exits 0 only
when every applicable check passes.

The MCP client is tests/e2e/test_cells_rust_live_content.py's _McpSession (G2/TC-102). It is
imported and wrapped here, not copied. The one change it needs is its BASE_URL module global,
which this script sets from --url before the session opens.

Usage:
    python scripts/poc/pilot_workflow_proof.py --pilot pdf_net --url http://127.0.0.1:8086 \
        --fixtures tests/fixtures --report evidence/pilot_workflow_proof/pdf_net.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]

# The nine tools, exactly as tools/list names them (server.py's tool registry).
TOOL_NAMES = (
    "lookup",
    "search_docs",
    "search_symbols",
    "get_symbol",
    "list_members",
    "find_examples",
    "get_product_reference",
    "list_recent_changes",
    "report_index_freshness",
)
CONTENT_TYPES = ("getting_started", "developer_guide", "troubleshooting", "faq")
# build_chunks_from_api_surface's default max_types: the leading slice of a fixture a generation
# publishes. A probe symbol must come from this slice, or the live index cannot contain it.
INDEXED_TYPE_LIMIT = 20
# A name no fixture declares. Searching for it must produce an honest miss.
ABSENT_SYMBOL = "Tc197AbsentSymbolProbe"
# Title words that name the vendor or the shape of the title, not the product.
TITLE_STOPWORDS = frozenset({"aspose", "foss", "for", "via"})
# A probe must be a real identifier of at least this many characters, and never a common word.
MIN_PROBE_LENGTH = 4
COMMON_WORDS = frozenset(
    {"name", "none", "null", "value", "type", "item", "data", "list", "object", "result"}
)
_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_]*")

CHECK_TITLES: dict[int, str] = {
    1: "initialize returns a protocol version and a server name; tools/list returns the nine tools",
    2: "search_symbols returns an exact match, every result carries this pilot's scope",
    3: "lookup resolves the same symbol to the same FQN",
    4: "get_symbol returns the kind; list_members returns members where the symbol declares them",
    5: "search_docs returns a non-empty answer about this pilot, for the furnished page title",
    6: "find_examples returns a verified example, or an honest no-example answer",
    7: "get_product_reference returns non-empty product information",
    8: "report_index_freshness reports the same generation id search_symbols reported",
    9: "list_recent_changes returns a well-formed list",
    10: "an absent symbol returns an honest miss, never a match",
    11: "a symbol from another pilot's fixture returns no exact match on this endpoint",
}


class CheckFailed(Exception):
    """A check whose answer is wrong. Carries the evidence that shows it."""

    def __init__(self, reason: str, evidence: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.evidence = evidence or {}


class NotApplicable(Exception):
    """A check that does not apply to this pilot. Recorded as not_applicable, never as a pass."""


_FQN_LINE = re.compile(r"^FQN:\s*(.+)$", re.MULTILINE)


def extract_fqn(text: str) -> str:
    """The FQN a chunk's ``FQN:`` line names, or an empty string when it has none."""
    match = _FQN_LINE.search(text)
    return match.group(1).strip() if match else ""


def fqn_of(item: dict[str, Any]) -> str:
    """The FQN a search result describes, read from its text."""
    text = item.get("text")
    return extract_fqn(text) if isinstance(text, str) else ""


# The final segment of an FQN follows its last dot or its last ::, so Aspose.Pdf.Document and
# Aspose::Pdf::Document both end in Document.
_FINAL_SEGMENT = re.compile(r"\.|::")


def is_exact_hit(fqn: str, symbol: str) -> bool:
    """The exact-match rule every symbol check applies.

    A result is an exact hit only when the final dot or :: separated segment of its FQN equals the
    symbol. A fuzzy or partial match never satisfies it: "Aspose.Pdf.Document.Save" is not a hit
    for "Document", and "DocumentBuilder" is not a hit for "Document".
    """
    return bool(fqn) and bool(symbol) and _FINAL_SEGMENT.split(fqn)[-1] == symbol


def exact_hits(results: list[dict[str, Any]], symbol: str) -> list[dict[str, Any]]:
    return [item for item in results if is_exact_hit(fqn_of(item), symbol)]


def in_scope(item: dict[str, Any], pilot: Pilot) -> bool:
    scope = item.get("scope")
    return (
        isinstance(scope, dict)
        and scope.get("family") == pilot.family
        and scope.get("platform") == pilot.platform
    )


def shape(value: Any) -> str:
    """A short description of a tool answer's shape, for evidence."""
    if isinstance(value, list):
        return f"list[{len(value)}]"
    if isinstance(value, dict):
        return f"object with keys {sorted(value)}"
    return type(value).__name__


def distinctive_tokens(title: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]+", title.lower()) if token not in TITLE_STOPWORDS]


def mentions_all(text: str, tokens: list[str]) -> bool:
    lowered = text.lower()
    return all(re.search(rf"\b{re.escape(token)}\b", lowered) for token in tokens)


@dataclass(frozen=True)
class Pilot:
    name: str
    family: str
    platform: str
    source_commit: str
    symbol: str | None
    symbol_kind: str
    other_pilot: str | None
    other_symbol: str | None
    furnished_page: Path | None
    furnished_title: str | None
    probe_error: str | None


def choose_probe(types: list[Any], fixture: str = "the fixture") -> str:
    """The name this pilot's own fixture offers to probe with.

    It comes from the leading INDEXED_TYPE_LIMIT types, in document order, so the live generation
    publishes it. A usable name is a real identifier of at least MIN_PROBE_LENGTH characters that is
    not a common word. An enum with members is preferred, then a type with methods or properties,
    then any other usable type. A fixture with no usable name raises ValueError naming the fixture,
    so a short name is never probed.
    """
    usable = [
        t
        for t in types[:INDEXED_TYPE_LIMIT]
        if isinstance(t, dict)
        and isinstance(t.get("name"), str)
        and _IDENTIFIER.fullmatch(t["name"])
        and len(t["name"]) >= MIN_PROBE_LENGTH
        and t["name"].lower() not in COMMON_WORDS
    ]
    for predicate in (
        lambda t: "enum" in str(t.get("kind", "")).lower() and bool(t.get("enum_members")),
        lambda t: bool(t.get("methods") or t.get("properties")),
        lambda t: True,
    ):
        for candidate in usable:
            if predicate(candidate):
                return str(candidate["name"])
    raise ValueError(
        f"{fixture} declares no type with a usable probe name among its first {INDEXED_TYPE_LIMIT} types"
    )


def other_pilot_symbol(fixtures: Path, pilot: str, own_names: set[str]) -> tuple[str, str] | None:
    """A symbol from another pilot's fixture that this pilot's own fixture does not declare.

    The longest name wins, so the probe is as distinctive as the fixtures allow. Ties go to the
    alphabetically first pilot, which keeps the choice reproducible.
    """
    candidates: list[tuple[int, str, str]] = []
    for directory in sorted(fixtures.iterdir()):
        api_path = directory / "api_surface.json"
        if directory.name == pilot or not api_path.is_file():
            continue
        types = json.loads(api_path.read_text(encoding="utf-8")).get("types") or []
        for entry in types[:INDEXED_TYPE_LIMIT]:
            name = entry.get("name") if isinstance(entry, dict) else None
            if isinstance(name, str) and name and name not in own_names and name != ABSENT_SYMBOL:
                candidates.append((-len(name), directory.name, name))
    if not candidates:
        return None
    _, other, name = min(candidates)
    return other, name


def furnished_title(page_text: str) -> str | None:
    """The ``title:`` line of a Hugo-style page's front matter, or None."""
    lines = page_text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if line.startswith("title:"):
            return line[len("title:") :].strip().strip("\"'") or None
    return None


def chart_furnishes_page(chart_values: Path, family: str, platform: str) -> bool:
    """True when the chart's ingestion.pilots entry for this family and platform sets furnishedPage."""
    values = yaml.safe_load(chart_values.read_text(encoding="utf-8")) or {}
    ingestion = values.get("ingestion") if isinstance(values, dict) else None
    pilots = ingestion.get("pilots") if isinstance(ingestion, dict) else None
    for entry in pilots or []:
        if isinstance(entry, dict) and entry.get("family") == family and entry.get("platform") == platform:
            return bool(entry.get("furnishedPage"))
    return False


def load_pilot(name: str, fixtures: Path, *, chart_values: Path | None = None) -> Pilot:
    """Load one pilot's fixtures.

    With chart_values None, a furnished fixture file alone gives the pilot a furnished page. With a
    path to the chart's values file, the page also needs the chart to ingest one for the pilot.
    """
    family, separator, platform = name.partition("_")
    if not separator or not family or not platform:
        raise ValueError(f"pilot name {name!r} must look like family_platform, e.g. pdf_net")
    api_surface = json.loads((fixtures / name / "api_surface.json").read_text(encoding="utf-8"))
    types = api_surface.get("types") or []
    own_names = {t["name"] for t in types if isinstance(t, dict) and isinstance(t.get("name"), str)}
    if ABSENT_SYMBOL in own_names:
        raise ValueError(
            f"{name}'s fixture declares {ABSENT_SYMBOL!r}, so it cannot serve as the absent probe"
        )
    # A pilot with no usable probe name still loads: check 2 records the reason as its fail.
    try:
        symbol, probe_error = choose_probe(types, name), None
    except ValueError as exc:
        symbol, probe_error = None, str(exc)
    probe = next((t for t in types if isinstance(t, dict) and t.get("name") == symbol), {})
    other = other_pilot_symbol(fixtures, name, own_names)
    page = fixtures / "furnished" / name / "pages" / "_index.md"
    furnished = page if page.is_file() else None
    if furnished is not None and chart_values is not None:
        if not chart_furnishes_page(chart_values, family, platform):
            furnished = None
    return Pilot(
        name=name,
        family=family,
        platform=platform,
        source_commit=str(api_surface.get("source_commit") or ""),
        symbol=symbol,
        symbol_kind=str(probe.get("kind", "")),
        other_pilot=other[0] if other else None,
        other_symbol=other[1] if other else None,
        furnished_page=furnished,
        furnished_title=furnished_title(page.read_text(encoding="utf-8")) if furnished else None,
        probe_error=probe_error,
    )


def check_arguments(schemas: dict[str, dict[str, Any]], tool: str, arguments: dict[str, Any]) -> None:
    """Refuse a call whose argument names are not in the tool's own tools/list schema."""
    if not schemas:
        return  # tools/list was not read; check 1 reports that, and the names come from the server source
    schema = schemas.get(tool)
    if schema is None:
        raise CheckFailed(f"tool {tool!r} is not in tools/list")
    properties = set((schema.get("properties") or {}).keys())
    unknown = sorted(set(arguments) - properties)
    if unknown:
        raise CheckFailed(f"{tool}: argument(s) {unknown} are not in its input schema {sorted(properties)}")
    missing = sorted(set(schema.get("required") or []) - set(arguments))
    if missing:
        raise CheckFailed(f"{tool}: required argument(s) {missing} were not supplied")


class _Run:
    """The state one pilot's checks share: the client, the pilot, and what earlier checks found."""

    def __init__(self, client: Any, pilot: Pilot) -> None:
        self.client = client
        self.pilot = pilot
        self.schemas: dict[str, dict[str, Any]] = {}
        self.generation_id: str | None = None
        self.fqn: str | None = None

    def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        check_arguments(self.schemas, tool, arguments)
        return self.client.invoke(tool, arguments)

    def require_fqn(self, number: int) -> str:
        if self.fqn is None:
            raise CheckFailed(
                f"check {number} needs the FQN that check 2 resolves, and check 2 did not establish one"
            )
        return self.fqn


def check_1(run: _Run) -> dict[str, Any]:
    handshake = run.client.handshake()
    protocol = handshake.get("protocolVersion")
    server = (handshake.get("serverInfo") or {}).get("name")
    evidence: dict[str, Any] = {"protocol_version": protocol, "server_name": server}
    if not isinstance(protocol, str) or not protocol:
        raise CheckFailed("initialize returned no protocol version", evidence)
    if not isinstance(server, str) or not server:
        raise CheckFailed("initialize returned no server name", evidence)
    tools = run.client.list_tools()
    run.schemas = {tool["name"]: tool.get("inputSchema") or {} for tool in tools}
    names = sorted(run.schemas)
    missing = sorted(set(TOOL_NAMES) - set(names))
    unexpected = sorted(set(names) - set(TOOL_NAMES))
    evidence.update(tool_count=len(tools), tools=names)
    if missing or unexpected or len(tools) != len(TOOL_NAMES):
        raise CheckFailed(
            f"tools/list does not return the nine tools (missing {missing}, unexpected {unexpected})",
            evidence,
        )
    return evidence


def check_2(run: _Run) -> dict[str, Any]:
    pilot = run.pilot
    if pilot.symbol is None:
        raise CheckFailed(pilot.probe_error or f"{pilot.name} has no probe symbol")
    results = run.call("search_symbols", {"query": pilot.symbol})
    evidence: dict[str, Any] = {"query": pilot.symbol, "result_kind": shape(results)}
    if not isinstance(results, list) or not results:
        raise CheckFailed(
            f"search_symbols returned no matches for {pilot.symbol!r}: {shape(results)}", evidence
        )
    evidence["match_count"] = len(results)
    out_of_scope = [
        {"fqn": fqn_of(item), "scope": item.get("scope")} for item in results if not in_scope(item, pilot)
    ]
    if out_of_scope:
        raise CheckFailed(
            f"{len(out_of_scope)} result(s) carry another scope", {**evidence, "out_of_scope": out_of_scope}
        )
    hits = exact_hits(results, pilot.symbol)
    evidence["exact_hit_fqns"] = [fqn_of(item) for item in hits]
    if not hits:
        raise CheckFailed(f"no result's final FQN segment equals {pilot.symbol!r}", evidence)
    generations = sorted({str(item.get("generation_id")) for item in results})
    evidence["generation_ids"] = generations
    if len(generations) != 1:
        raise CheckFailed("the results span more than one generation", evidence)
    run.fqn = fqn_of(hits[0])
    run.generation_id = str(hits[0].get("generation_id"))
    evidence["fqn"] = run.fqn
    return evidence


def check_3(run: _Run) -> dict[str, Any]:
    fqn = run.require_fqn(3)
    pilot = run.pilot
    results = run.call("lookup", {"query": pilot.symbol})
    evidence: dict[str, Any] = {"query": pilot.symbol, "result_kind": shape(results), "expected_fqn": fqn}
    if not isinstance(results, list):
        raise CheckFailed(f"lookup did not return symbol matches: {shape(results)}", evidence)
    resolved = sorted({fqn_of(item) for item in exact_hits(results, pilot.symbol)})
    evidence["resolved_fqns"] = resolved
    if fqn not in resolved:
        raise CheckFailed(f"lookup did not resolve {pilot.symbol!r} to {fqn!r}", evidence)
    return evidence


def check_4(run: _Run) -> dict[str, Any]:
    fqn = run.require_fqn(4)
    signature = run.call("get_symbol", {"fqn": fqn})
    evidence: dict[str, Any] = {"fqn": fqn, "result_kind": shape(signature)}
    if not isinstance(signature, dict) or "kind" not in signature:
        raise CheckFailed(f"get_symbol returned no signature for {fqn!r}: {shape(signature)}", evidence)
    kind = signature.get("kind")
    evidence["kind"] = kind
    if not isinstance(kind, str) or not kind.strip():
        raise CheckFailed(f"get_symbol returned an empty kind for {fqn!r}", evidence)
    if signature.get("fqn") != fqn:
        raise CheckFailed(f"get_symbol answered for {signature.get('fqn')!r}, not {fqn!r}", evidence)
    declared = {key: len(signature.get(key) or []) for key in ("methods", "properties", "members")}
    evidence["declared_member_counts"] = declared
    if not any(declared.values()):
        evidence["list_members"] = "not required: get_symbol declares no methods, properties or members"
        return evidence
    members = run.call("list_members", {"fqn": fqn})
    evidence["list_members_result"] = shape(members)
    if not isinstance(members, list) or not members:
        raise CheckFailed(
            f"list_members returned no members for {fqn!r}, though get_symbol declares some", evidence
        )
    evidence.update(member_count=len(members), first_members=members[:3])
    return evidence


def check_5(run: _Run) -> dict[str, Any]:
    pilot = run.pilot
    if pilot.furnished_page is None:
        raise NotApplicable(
            f"the chart ingests no furnished page for pilot {pilot.name} "
            f"(fixtures/furnished/{pilot.name}/pages/_index.md is absent or not ingested)"
        )
    title = pilot.furnished_title
    if not title:
        raise CheckFailed("the furnished page has no title line to search for")
    tokens = distinctive_tokens(title)
    evidence: dict[str, Any] = {"query": title, "tokens": tokens, "attempts": []}
    if not tokens:
        raise CheckFailed(f"the title {title!r} has no distinctive token to judge the answer by", evidence)
    for content_type in CONTENT_TYPES:
        results = run.call("search_docs", {"query": title, "content_type": content_type})
        if not isinstance(results, list) or not results:
            evidence["attempts"].append({"content_type": content_type, "result": shape(results)})
            continue
        about = [item for item in results if mentions_all(str(item.get("text", "")), tokens)]
        evidence["attempts"].append(
            {"content_type": content_type, "match_count": len(results), "about_pilot": len(about)}
        )
        if about:
            evidence.update(
                content_type=content_type,
                match_count=len(results),
                about_pilot_count=len(about),
                sample_doc_id=about[0].get("doc_id"),
            )
            return evidence
    raise CheckFailed(
        f"no content_type returned a non-empty answer about this pilot (tokens {tokens})", evidence
    )


def check_6(run: _Run) -> dict[str, Any]:
    symbol = run.pilot.symbol
    if symbol is None:
        raise CheckFailed(
            f"check 6 needs the probe symbol, which check 2 could not choose: {run.pilot.probe_error}"
        )
    results = run.call("find_examples", {"query": symbol})
    evidence: dict[str, Any] = {"query": symbol, "result_kind": shape(results)}
    if isinstance(results, list):
        if not results:
            raise CheckFailed("find_examples returned an empty list instead of an answer", evidence)
        blank = [index for index, item in enumerate(results) if not str(item.get("snippet") or "").strip()]
        if blank:
            raise CheckFailed(f"{len(blank)} example result(s) carry no example text", evidence)
        evidence.update(
            answer="verified example", example_count=len(results), example_fqn=results[0].get("fqn")
        )
        return evidence
    if isinstance(results, dict) and "query" in results and "snippet" not in results:
        evidence["answer"] = "honest no-verified-example miss"
        return evidence
    raise CheckFailed("find_examples returned neither examples nor an explicit no-example answer", evidence)


def check_7(run: _Run) -> dict[str, Any]:
    result = run.call("get_product_reference", {"section": "install"})
    evidence: dict[str, Any] = {
        "section": "install",
        "sections_asked": ["install"],
        "result_kind": shape(result),
        "result_kinds": [shape(result)],
    }
    if not isinstance(result, dict):
        raise CheckFailed(f"get_product_reference returned {shape(result)}", evidence)
    text = result.get("text")
    if isinstance(text, str) and text.strip():
        evidence["text"] = text.strip()[:200]
        return evidence
    if run.pilot.platform == "cpp":
        fallback = run.call("get_product_reference", {"section": "compatibility"})
        evidence["section"] = "compatibility"
        evidence["sections_asked"].append("compatibility")
        evidence["result_kind"] = shape(fallback)
        evidence["result_kinds"].append(shape(fallback))
        if not isinstance(fallback, dict):
            raise CheckFailed(f"get_product_reference returned {shape(fallback)}", evidence)
        fallback_text = fallback.get("text")
        if isinstance(fallback_text, str) and fallback_text.strip():
            evidence["text"] = fallback_text.strip()[:200]
            return evidence
        result = fallback
    raise CheckFailed(
        f"get_product_reference returned no product information: {result.get('reason', result)}", evidence
    )


def check_8(run: _Run) -> dict[str, Any]:
    pilot = run.pilot
    if run.generation_id is None:
        raise CheckFailed("check 2 did not establish a generation id to compare against")
    if not pilot.source_commit:
        raise CheckFailed(
            "the fixture's api_surface.json records no source_commit to check freshness against"
        )
    report = run.call("report_index_freshness", {"current_source_commit": pilot.source_commit})
    evidence: dict[str, Any] = {"result_kind": shape(report)}
    if not isinstance(report, dict):
        raise CheckFailed(f"report_index_freshness returned {shape(report)}", evidence)
    indexed = report.get("indexed_generation_id")
    evidence.update(
        indexed_generation_id=indexed,
        search_generation_id=run.generation_id,
        stale=report.get("stale"),
        reason=report.get("reason"),
    )
    if indexed != run.generation_id:
        raise CheckFailed(
            f"freshness reports generation {indexed!r}, search_symbols reported {run.generation_id!r}",
            evidence,
        )
    if not in_scope(report, pilot):
        raise CheckFailed("report_index_freshness carries another scope", evidence)
    return evidence


def check_9(run: _Run) -> dict[str, Any]:
    changes = run.call("list_recent_changes", {"limit": 10})
    evidence: dict[str, Any] = {"result_kind": shape(changes)}
    if not isinstance(changes, list):
        raise CheckFailed("list_recent_changes did not return a list", evidence)
    required = {"tag_name", "richness", "body"}
    malformed = [
        index
        for index, entry in enumerate(changes)
        if not isinstance(entry, dict) or not required <= set(entry)
    ]
    if malformed:
        raise CheckFailed(f"{len(malformed)} release entr(ies) lack tag_name, richness or body", evidence)
    evidence["release_count"] = len(changes)
    return evidence


def check_10(run: _Run) -> dict[str, Any]:
    absent = ABSENT_SYMBOL
    results = run.call("search_symbols", {"query": absent})
    evidence: dict[str, Any] = {"query": absent, "result_kind": shape(results)}
    if isinstance(results, list):
        if results:
            evidence.update(
                match_count=len(results),
                first_fqns=[fqn_of(item) for item in results[:5]],
                exact_hit_count=len(exact_hits(results, absent)),
            )
            raise CheckFailed(
                f"{len(results)} match(es) for {absent!r}, a symbol that does not exist", evidence
            )
        evidence["answer"] = "empty list"
        return evidence
    if isinstance(results, dict) and "reason" in results:
        evidence.update(answer="honest miss", reason=results["reason"])
        return evidence
    raise CheckFailed("search_symbols returned neither matches nor an honest miss", evidence)


def check_11(run: _Run) -> dict[str, Any]:
    pilot = run.pilot
    if pilot.other_symbol is None or pilot.other_pilot is None:
        raise CheckFailed("no other pilot's fixture supplies a symbol to probe with")
    symbol = pilot.other_symbol
    results = run.call("search_symbols", {"query": symbol})
    evidence: dict[str, Any] = {
        "other_pilot": pilot.other_pilot,
        "symbol": symbol,
        "result_kind": shape(results),
    }
    if isinstance(results, dict) and "reason" in results:
        evidence["answer"] = "honest miss"
        return evidence
    if not isinstance(results, list):
        raise CheckFailed(f"search_symbols returned {shape(results)}", evidence)
    evidence["match_count"] = len(results)
    hits = exact_hits(results, symbol)
    if hits:
        evidence["exact_hit_fqns"] = [fqn_of(item) for item in hits]
        raise CheckFailed(
            f"{pilot.other_pilot}'s symbol {symbol!r} matched exactly on {pilot.name}'s endpoint", evidence
        )
    evidence["answer"] = "no exact match"
    return evidence


CHECKS: tuple[tuple[int, Callable[[_Run], dict[str, Any]]], ...] = (
    (1, check_1),
    (2, check_2),
    (3, check_3),
    (4, check_4),
    (5, check_5),
    (6, check_6),
    (7, check_7),
    (8, check_8),
    (9, check_9),
    (10, check_10),
    (11, check_11),
)


def _record(number: int, status: str, evidence: dict[str, Any], reason: str | None) -> dict[str, Any]:
    return {
        "id": number,
        "name": CHECK_TITLES[number],
        "status": status,
        "evidence": evidence,
        "reason": reason,
    }


def _execute(number: int, check: Callable[[_Run], dict[str, Any]], run: _Run) -> dict[str, Any]:
    try:
        return _record(number, "pass", check(run), None)
    except NotApplicable as exc:
        return _record(number, "not_applicable", {}, str(exc))
    except CheckFailed as exc:
        return _record(number, "fail", exc.evidence, str(exc))
    except Exception as exc:  # a check that cannot run is a fail, with its reason
        return _record(number, "fail", {}, f"check could not run: {type(exc).__name__}: {exc}")


def run_checks(client: Any, pilot: Pilot) -> list[dict[str, Any]]:
    run = _Run(client, pilot)
    return [_execute(number, check, run) for number, check in CHECKS]


def unrunnable_checks(reason: str) -> list[dict[str, Any]]:
    """Every check recorded as a fail with the same reason, for when nothing can run at all."""
    return [_record(number, "fail", {}, reason) for number, _ in CHECKS]


class _UnavailableClient:
    """Stands in for a client that could not be opened: every call raises with the reason."""

    def __init__(self, reason: str) -> None:
        self._reason = reason

    def handshake(self) -> dict[str, Any]:
        raise RuntimeError(self._reason)

    def list_tools(self) -> list[dict[str, Any]]:
        raise RuntimeError(self._reason)

    def invoke(self, name: str, arguments: dict[str, Any]) -> Any:
        raise RuntimeError(self._reason)


class LiveMcpClient:
    """The one MCP client: tests/e2e's _McpSession (initialize, notifications/initialized, and
    tools/call over HTTP at /mcp). This wrapper adds the two requests the checks need beyond it:
    the initialize result, which _McpSession discards, and tools/list. Both use the same helpers,
    headers and SSE parsing as _McpSession.
    """

    def __init__(self, e2e: Any, url: str) -> None:
        e2e.BASE_URL = url  # _McpSession reads this module global at call time
        self._e2e = e2e
        self._session = e2e._McpSession()

    def handshake(self) -> dict[str, Any]:
        response = self._e2e.httpx.post(
            f"{self._e2e.BASE_URL}{self._e2e.MCP_PATH}",
            json=self._e2e._initialize_body(),
            headers=self._e2e.VALID_HEADERS,
            timeout=10,
        )
        if response.status_code != 200:
            raise RuntimeError(f"initialize returned HTTP {response.status_code}: {response.text[:200]}")
        return self._e2e._sse_json(response.text)["result"]

    def list_tools(self) -> list[dict[str, Any]]:
        body = self._rpc("tools/list", {})
        return body["result"]["tools"]

    def invoke(self, name: str, arguments: dict[str, Any]) -> Any:
        body = self._session.call_tool(name, arguments)
        if "error" in body:
            raise RuntimeError(f"{name} returned a JSON-RPC error: {body['error']}")
        result = body["result"]
        if result.get("isError"):
            raise RuntimeError(f"{name} reported a tool error: {str(result.get('content'))[:300]}")
        return result["structuredContent"]["result"]

    def _rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        response = self._e2e.httpx.post(
            f"{self._e2e.BASE_URL}{self._e2e.MCP_PATH}",
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            headers=self._session.headers,
            timeout=15,
        )
        if response.status_code != 200:
            raise RuntimeError(f"{method} returned HTTP {response.status_code}: {response.text[:200]}")
        body = self._e2e._sse_json(response.text)
        if "error" in body:
            raise RuntimeError(f"{method} returned a JSON-RPC error: {body['error']}")
        return body


def open_live_client(url: str) -> LiveMcpClient:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from tests.e2e import test_cells_rust_live_content as e2e

    return LiveMcpClient(e2e, url)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Full MCP workflow proof for one POC pilot (G2/TC-197).")
    parser.add_argument("--pilot", required=True, help="pilot name, family_platform, e.g. pdf_net")
    parser.add_argument(
        "--url", required=True, help="base URL of the live serving endpoint, e.g. http://127.0.0.1:8086"
    )
    parser.add_argument("--fixtures", required=True, type=Path, help="fixtures root, e.g. tests/fixtures")
    parser.add_argument("--report", required=True, type=Path, help="where to write the JSON report")
    return parser.parse_args(argv)


def build_report(
    pilot_name: str, url: str, pilot: Pilot | None, checks: list[dict[str, Any]]
) -> dict[str, Any]:
    applicable = [check for check in checks if check["status"] != "not_applicable"]
    passed = sum(1 for check in applicable if check["status"] == "pass")
    all_passed = bool(applicable) and passed == len(applicable)
    report: dict[str, Any] = {
        "pilot": pilot_name,
        "url": url,
        "family": pilot.family if pilot else None,
        "platform": pilot.platform if pilot else None,
        "symbol": pilot.symbol if pilot else None,
        "absent_symbol": ABSENT_SYMBOL,
        "other_pilot_symbol": {"pilot": pilot.other_pilot, "symbol": pilot.other_symbol} if pilot else None,
        "checks": checks,
        "summary": {
            "applicable": len(applicable),
            "pass": passed,
            "fail": len(applicable) - passed,
            "not_applicable": len(checks) - len(applicable),
        },
        "all_applicable_passed": all_passed,
        "exit_code": 0 if all_passed else 1,
    }
    return report


def main(argv: list[str] | None = None, client_factory: Callable[[str], Any] | None = None) -> int:
    args = parse_args(argv)
    url = args.url.rstrip("/")
    pilot: Pilot | None = None
    try:
        pilot = load_pilot(
            args.pilot, args.fixtures, chart_values=REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"
        )
    except Exception as exc:
        checks = unrunnable_checks(f"pilot fixtures could not be loaded: {type(exc).__name__}: {exc}")
    else:
        factory = client_factory or open_live_client
        try:
            client = factory(url)
        except Exception as exc:
            client = _UnavailableClient(
                f"MCP session could not be opened at {url}: {type(exc).__name__}: {exc}"
            )
        checks = run_checks(client, pilot)

    report = build_report(args.pilot, url, pilot, checks)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for check in checks:
        print(f"[{check['status'].upper():14}] {check['id']:>2} {check['name']}")
        if check["reason"]:
            print(f"                 reason: {check['reason']}")
    summary = report["summary"]
    print(
        f"{args.pilot}: {summary['pass']} of {summary['applicable']} applicable checks passed, "
        f"{summary['not_applicable']} not applicable; report: {args.report}"
    )
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
