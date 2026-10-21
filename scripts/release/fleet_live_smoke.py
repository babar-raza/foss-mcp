#!/usr/bin/env python3
"""Fleet live-content smoke test (TC-566 / REQ-G3-009, RD3-151).

AGENTS.md's Integration/liveness clause names the exact gap this closes: "a deployment that correctly
answers 'I have nothing' is not a passing deployment" and "every gate exit must include a live-content
smoke test: bring up the real container(s) and confirm a real query returns real, non-empty content".
Before this card, live container coverage was 7 of 32 pilots (``tests/e2e``), and the one pdf/typescript
e2e pin had already rotted stale. This script starts EVERY one of the chart's pilots -- one container
each, exactly as ``scripts/release/image_workflow_gate.py`` does, reusing its own docker/readiness
helpers rather than copying them -- and asks each one for a small, language-appropriate set of real
symbol facts: a central class, its constructor, an enum member, a static member.

This is a LIVENESS check, not an independent-fidelity check. Its "oracle" is each pilot's own already
-baked fixture (the same ``tests/fixtures/<family>_<platform>/api_surface.json`` the served chunks are
built from): it answers "does the running container actually serve what was baked for it", never "is
what was baked true" -- that second question is ``scripts/release/source_differential.py``'s separate,
genuinely-independent-oracle job. Conflating the two would let a self-consistent-but-wrong fixture pass
both checks for the same wrong reason; keeping them separate is deliberate.

Usage:
    python scripts/release/fleet_live_smoke.py --image NAME:TAG --dry-run
        # builds the fact plan for every chart pilot from its committed fixture; starts nothing.
    python scripts/release/fleet_live_smoke.py --image NAME:TAG [--only FAMILY_PLATFORM ...]
        # the live run: one container per selected pilot, real MCP queries, real teardown.

Exit 0 only when every selected pilot passed every fact it was possible to build for it AND at least
one fact was built (a pilot for which every fact class comes back not-applicable fails, naming why --
mirroring image_workflow_gate's own "a not_applicable entry is never counted as a pass" rule). Exit 1 on
any failure; exit 2 when the request could not be run as asked (bad image, unknown --only name).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
_RELEASE_DIR = Path(__file__).resolve().parent
_DEPLOY_DIR = REPO_ROOT / "scripts" / "deploy"
DEFAULT_FIXTURES = REPO_ROOT / "tests" / "fixtures"
# A temp directory, never the repository, so a run with no --report-dir never leaves output behind
# in the working tree (matches image_workflow_gate.py's own DEFAULT_REPORT_DIR convention).
DEFAULT_REPORT_DIR = Path(tempfile.gettempdir()) / "foss-mcp-fleet-live-smoke"

if str(_DEPLOY_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOY_DIR))
if str(_RELEASE_DIR) not in sys.path:
    sys.path.insert(0, str(_RELEASE_DIR))

import image_workflow_gate as gate  # noqa: E402
import pilots as chart  # noqa: E402

SCHEMA_VERSION = 1
REPORT_NAME = "summary.json"
EXIT_FAILED = 1
EXIT_REFUSED = 2

# Deps is reused wholesale from image_workflow_gate: the fields this script needs (run, http_get,
# client_factory, sleep, clock, environ, run_id) are exactly the ones it already declares. The fields
# this script never touches (proof_runner, pilot_loader, provenance_runner) simply stay None.
Deps = gate.Deps

CLASS_KINDS = frozenset(
    {
        "class",
        "class_declaration",
        "abstract_class_declaration",
        "struct_declaration",
        "struct_item",
        "struct_specifier",
        "record_declaration",
        "type_spec",
        "interface_declaration",
        "trait_item",
    }
)
ENUM_KINDS = frozenset({"enum", "enum_declaration", "enum_item", "enum_specifier"})

FACT_TITLES: dict[str, str] = {
    "F1": "get_symbol returns the pilot's own central class/struct/enum",
    "F2": "search_symbols finds the central symbol's own constructor",
    "F3": "search_symbols finds a real enum member",
    "F4": "search_symbols finds a real static/classmethod member",
}


# ---------------------------------------------------------------------------------------------------------------
# Building the fact plan from a pilot's own committed fixture (never from a container, never fabricated).
# ---------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Fact:
    id: str
    name: str
    tool: str
    arguments: dict[str, Any]
    expect_contains: str


@dataclass
class PilotPlan:
    key: str
    facts: list[Fact] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)  # why a fact class could not be built, never silent


def _is_static_entry(entry: Mapping[str, Any]) -> bool:
    modifiers = entry.get("modifiers") or []
    return "static" in modifiers or "classmethod" in modifiers


def _central_class(types: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    candidates = [
        t for t in types if t.get("kind") in CLASS_KINDS and (t.get("methods") or t.get("properties"))
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda t: (
            len(t.get("methods") or []) + len(t.get("properties") or []),
            str(t.get("name") or ""),
        ),
    )


def _constructor_of(entry: Mapping[str, Any]) -> Mapping[str, Any] | None:
    for method in entry.get("methods") or []:
        if method.get("is_constructor"):
            return method
    return None


def _an_enum(types: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], Mapping[str, Any]] | None:
    best_type: Mapping[str, Any] | None = None
    best_count = 0
    for t in types:
        if t.get("kind") in ENUM_KINDS:
            members = t.get("enum_members") or []
            if len(members) > best_count:
                best_type, best_count = t, len(members)
    if best_type is None:
        return None
    member = (best_type.get("enum_members") or [])[0]
    return best_type, member


def _a_static_member(types: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], str] | None:
    for t in types:
        for method in t.get("methods") or []:
            if _is_static_entry(method):
                return t, str(method.get("name", ""))
        for prop in t.get("properties") or []:
            if _is_static_entry(prop):
                return t, str(prop.get("name", ""))
    return None


def build_plan(key: str, fixture: Mapping[str, Any]) -> PilotPlan:
    """The fact plan for one pilot, derived only from its own already-committed fixture."""
    types = fixture.get("types") or []
    plan = PilotPlan(key=key)

    central = _central_class(types)
    if central is None:
        plan.reasons.append("F1 (a central class): no class/struct/enum-like type with any member was found")
        plan.reasons.append("F2 (a constructor): no central class was found to look for one on")
    else:
        fqn = str(central.get("class_import") or central.get("name") or "")
        name = str(central.get("name") or fqn)
        plan.facts.append(Fact("F1", FACT_TITLES["F1"], "get_symbol", {"fqn": fqn}, name))

        constructor = _constructor_of(central)
        if constructor is None:
            plan.reasons.append(f"F2 (a constructor): {name!r} has no method marked is_constructor")
        else:
            ctor_name = str(constructor.get("name", ""))
            plan.facts.append(
                Fact("F2", FACT_TITLES["F2"], "search_symbols", {"query": ctor_name}, ctor_name)
            )

    enum_hit = _an_enum(types)
    if enum_hit is None:
        plan.reasons.append("F3 (an enum): no enum-like type with any member was found")
    else:
        _enum_type, member = enum_hit
        member_name = str(member.get("name", ""))
        plan.facts.append(
            Fact("F3", FACT_TITLES["F3"], "search_symbols", {"query": member_name}, member_name)
        )

    static_hit = _a_static_member(types)
    if static_hit is None:
        plan.reasons.append(
            "F4 (a static member): no method or property carries a static/classmethod modifier"
        )
    else:
        _owner, static_name = static_hit
        plan.facts.append(
            Fact("F4", FACT_TITLES["F4"], "search_symbols", {"query": static_name}, static_name)
        )

    return plan


# ---------------------------------------------------------------------------------------------------------------
# Running one fact against a real (or fake, in tests) MCP client.
# ---------------------------------------------------------------------------------------------------------------


def _result_text(result: Any) -> str:
    """The searchable text of a tool result -- never a Miss's own ``reason``/``query`` fields.

    A Miss (``{"query": ..., "reason": f"no symbol matches {query!r}", ...}``) often echoes the very
    name it failed to find inside its own ``reason`` text, so that field must never be searched for a
    substring match -- the same trap ``image_workflow_gate.describe_lookup`` guards against.
    """
    if isinstance(result, list):
        parts = [
            str(item.get("text") or item.get("raw_text") or item.get("snippet") or "")
            for item in result
            if isinstance(item, dict)
        ]
        return " ".join(parts)
    if isinstance(result, dict):
        if "reason" in result:
            return ""
        return str(result.get("raw_text") or "") + " " + json.dumps(result, sort_keys=True, default=str)
    return ""


def fact_result(client: Any, fact: Fact) -> dict[str, Any]:
    try:
        result = client.invoke(fact.tool, dict(fact.arguments))
    except Exception as exc:
        return {
            "id": fact.id,
            "name": fact.name,
            "status": "fail",
            "reason": f"could not invoke {fact.tool}: {type(exc).__name__}: {exc}",
        }
    text = _result_text(result)
    if fact.expect_contains in text:
        return {"id": fact.id, "name": fact.name, "status": "pass", "evidence": {"excerpt": text[:200]}}
    return {
        "id": fact.id,
        "name": fact.name,
        "status": "fail",
        "reason": f"{fact.tool}({fact.arguments!r}) never returned {fact.expect_contains!r}",
        "evidence": {"excerpt": text[:200]},
    }


# ---------------------------------------------------------------------------------------------------------------
# One pilot, live: reuses image_workflow_gate's own container/readiness machinery, never a copy of it.
# ---------------------------------------------------------------------------------------------------------------


def run_pilot_smoke(entry: dict[str, Any], plan: PilotPlan, ctx: Any) -> dict[str, Any]:
    name = gate.container_name(entry, ctx.run_id)
    ctx.containers.add(name)
    reasons = list(plan.reasons)
    results: list[dict[str, Any]] = []
    try:
        command = gate.run_command(ctx.image, name, entry, ctx.memory, ctx.cpus, with_gateway=False)
        launched = gate.run_docker(ctx.deps, command[1:], timeout=gate.DOCKER_RUN_TIMEOUT_S)
        if launched.rc != 0:
            detail = " ".join((launched.stderr or launched.stdout).split())[:300]
            reasons.append(f"docker run failed (exit {launched.rc}): {detail}")
        else:
            mapped = gate.run_docker(ctx.deps, gate.port_command(name)[1:])
            port = gate.host_port(mapped.stdout) if mapped.rc == 0 else None
            if port is None:
                reasons.append("docker port did not report a loopback host port for the container")
            else:
                url = f"http://127.0.0.1:{port}"
                not_ready = gate.wait_until_ready(ctx, name, url)
                if not_ready is not None:
                    reasons.append(f"the container never became ready: {not_ready}")
                elif ctx.deps.client_factory is None:
                    reasons.append("no client_factory was supplied; cannot query the container")
                else:
                    client = ctx.deps.client_factory(url)
                    if not plan.facts:
                        reasons.append("no language-appropriate fact could be derived from the fixture")
                    for fact in plan.facts:
                        outcome = fact_result(client, fact)
                        results.append(outcome)
                        if outcome["status"] == "fail":
                            reasons.append(f"{outcome['id']} failed: {outcome['reason']}")
    finally:
        ctx.containers.remove(name)
    status = "fail" if reasons else "pass"
    return {"key": plan.key, "status": status, "facts": results, "reasons": reasons}


# ---------------------------------------------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------------------------------------------


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Start every chart pilot and assert a language-appropriate set of real symbol facts.",
    )
    parser.add_argument(
        "--image", required=True, help="image NAME:TAG to test (an explicit tag, never latest)"
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="FAMILY_PLATFORM",
        help="test only this pilot (repeatable)",
    )
    parser.add_argument(
        "--fixtures", type=Path, default=DEFAULT_FIXTURES, help="fixtures root (default tests/fixtures)"
    )
    parser.add_argument(
        "--report-dir", type=Path, default=DEFAULT_REPORT_DIR, help=f"where {REPORT_NAME} is written"
    )
    parser.add_argument(
        "--memory",
        default=gate.DEFAULT_MEMORY,
        help=f"per-container memory limit (default {gate.DEFAULT_MEMORY})",
    )
    parser.add_argument(
        "--cpus",
        type=float,
        default=gate.DEFAULT_CPUS,
        help=f"per-container CPU limit (default {gate.DEFAULT_CPUS:g})",
    )
    parser.add_argument(
        "--ready-timeout",
        type=float,
        default=gate.DEFAULT_READY_TIMEOUT,
        help=f"seconds to wait for each container's readiness probe (default {gate.DEFAULT_READY_TIMEOUT:g})",
    )
    parser.add_argument("--dry-run", action="store_true", help="build and print the fact plan; start nothing")
    return parser.parse_args(argv)


def print_dry_run(plans: list[PilotPlan]) -> None:
    for plan in plans:
        print(f"{plan.key}: {len(plan.facts)} fact(s) planned")
        for fact in plan.facts:
            print(
                f"    {fact.id}: {fact.name} -> {fact.tool}({fact.arguments!r}) expects {fact.expect_contains!r}"
            )
        for reason in plan.reasons:
            print(f"    not_applicable: {reason}")
    print(
        f"fleet_live_smoke: dry run for {len(plans)} pilot(s); nothing was run and nothing was written.",
        file=sys.stderr,
    )


def main(argv: list[str] | None = None, deps: Deps | None = None) -> int:
    deps = deps or Deps()
    args = parse_args(argv)
    try:
        gate.validate_image(args.image)
        entries = chart.select_pilots(chart.load_pilots(), args.only)
    except (gate.Refused, ValueError) as exc:
        print(f"fleet_live_smoke: {exc}", file=sys.stderr)
        return EXIT_REFUSED

    plans: list[PilotPlan] = []
    for entry in entries:
        key = chart.pilot_key(entry)
        fixture_path = args.fixtures / key / "api_surface.json"
        try:
            fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            plans.append(
                PilotPlan(key=key, reasons=[f"could not read {fixture_path}: {type(exc).__name__}: {exc}"])
            )
            continue
        plans.append(build_plan(key, fixture))

    if args.dry_run:
        print_dry_run(plans)
        return 0

    run_id = deps.run_id or uuid.uuid4().hex[:8]
    try:
        _image_id, _size = gate.inspect_image(deps, args.image)
    except gate.Refused as exc:
        print(f"fleet_live_smoke: {exc}", file=sys.stderr)
        return EXIT_FAILED

    containers = gate.Containers(deps)
    ctx = gate.Context(
        image=args.image,
        memory=args.memory,
        memory_mib=gate.parse_memory_limit_mib(args.memory),
        cpus=args.cpus,
        ready_timeout=args.ready_timeout,
        fixtures=args.fixtures,
        report_dir=args.report_dir,
        with_gateway=False,
        run_id=run_id,
        deps=deps,
        proof=None,
        containers=containers,
        redact=gate.Redactor([]),
    )

    outcomes: list[dict[str, Any]] = []
    try:
        for entry, plan in zip(entries, plans, strict=True):
            outcomes.append(run_pilot_smoke(entry, plan, ctx))
    finally:
        containers.remove_all()

    args.report_dir.mkdir(parents=True, exist_ok=True)
    passed = sum(1 for outcome in outcomes if outcome["status"] == "pass")
    document = {
        "schema_version": SCHEMA_VERSION,
        "image": args.image,
        "verdict": "PASS" if outcomes and passed == len(outcomes) else "FAIL",
        "pilots_total": len(outcomes),
        "pilots_passed": passed,
        "pilots": outcomes,
    }
    (args.report_dir / REPORT_NAME).write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    for outcome in outcomes:
        print(f"{outcome['key']:<24} {outcome['status'].upper():<4}")
        for reason in outcome["reasons"]:
            print(f"    reason: {reason}")
    print(f"verdict: {document['verdict']} ({passed} of {len(outcomes)} pilots passed)")
    return 0 if document["verdict"] == "PASS" else EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
