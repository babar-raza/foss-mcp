#!/usr/bin/env python3
"""Served-versus-committed provenance probes for the release gate (TC-539).

Usage (audit one running endpoint, for example a cluster port-forward):
    python scripts/release/served_provenance.py --url http://127.0.0.1:PORT --pilot FAMILY_PLATFORM
        [--fixtures DIR] [--manifests DIR] [--report FILE]

Requirements met here: REQ-G3-008 and REQ-G3-001. A gate that asks a server about itself cannot notice that the
server is not the thing that was committed. These two probes compare what a running endpoint serves with what is
committed in the repository:

  P1  the generation the container serves (``report_index_freshness.indexed_generation_id``) equals the id in
      ``infra/demo-manifests/<family>__<platform>__self_extracted/active_pointer.json``;
  P2  30 (type, method) signatures sampled from the pilot's committed fixture, rendered by the production chunk
      builder, are served exactly by the live ``get_symbol`` and ``list_members``.

Both are pure functions over any MCP client with ``invoke(name, arguments)`` returning the tool result as plain
JSON (``scripts/poc/pilot_workflow_proof.py``'s ``LiveMcpClient`` is one). ``run_probes`` is what
``scripts/release/image_workflow_gate.py`` calls for every pilot. Exit 0 only when neither entry failed and at
least one passed; a ``not_applicable`` entry is never a pass.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = REPO_ROOT / "src"
_DEPLOY_DIR = REPO_ROOT / "scripts" / "deploy"
PROOF_SCRIPT = REPO_ROOT / "scripts" / "poc" / "pilot_workflow_proof.py"
DEFAULT_FIXTURES = REPO_ROOT / "tests" / "fixtures"
DEFAULT_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"

if str(_SRC) not in sys.path:
    # foss_mcp is not an installed package; this module must also run as a plain script.
    sys.path.insert(0, str(_SRC))

PROVENANCE_TITLES: dict[str, str] = {
    "P1": "the generation the container serves is the one committed under infra/demo-manifests "
    "(report_index_freshness.indexed_generation_id equals active_pointer.json)",
    "P2": "30 sampled (type, method) signatures served by get_symbol and list_members equal the committed "
    "fixture rendered by the production chunk builder",
}
SAMPLE_SIZE = 30
SOURCE_KIND_DIRECTORY = "self_extracted"
DEFAULT_MAX_TYPES = 300
MAX_REPORTED_FAILURES = 5
_BULLET_PREFIX = "  - "
_NON_SYMBOL_PREFIXES = ("Example: ", "Doc: ")
_ARROW = " -> "


def make_entry(
    number: str, status: str, evidence: dict[str, Any] | None = None, reason: str | None = None
) -> dict[str, Any]:
    """One probe entry, in the shape ``image_workflow_gate._entry`` returns."""
    return {
        "id": number,
        "name": PROVENANCE_TITLES[number],
        "status": status,
        "evidence": evidence or {},
        "reason": reason,
    }


# ---------------------------------------------------------------------------------------------------------------
# The committed pointer
# ---------------------------------------------------------------------------------------------------------------


def pointer_path(manifests_dir: Path, family: str, platform: str) -> Path:
    return Path(manifests_dir) / f"{family}__{platform}__{SOURCE_KIND_DIRECTORY}" / "active_pointer.json"


def committed_generation_id(manifests_dir: Path, family: str, platform: str) -> str:
    """The ``generation_id`` committed in ``<family>__<platform>__self_extracted/active_pointer.json``.

    Raises FileNotFoundError when the file is absent and ValueError when it is not a pointer; both messages
    name the file.
    """
    path = pointer_path(manifests_dir, family, platform)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"the committed active pointer {path} does not exist") from exc
    try:
        document = json.loads(text)
    except ValueError as exc:
        raise ValueError(f"the committed active pointer {path} is not valid JSON: {exc}") from exc
    generation_id = document.get("generation_id") if isinstance(document, dict) else None
    if not isinstance(generation_id, str) or not generation_id.strip():
        raise ValueError(f"the committed active pointer {path} carries no generation_id")
    return generation_id


# ---------------------------------------------------------------------------------------------------------------
# Reading a rendered method line
# ---------------------------------------------------------------------------------------------------------------


def _strip_bullet(line: str) -> str:
    text = line.strip()
    return text[2:].lstrip() if text.startswith("- ") else text


def _first_group(text: str) -> tuple[str, int] | None:
    """(the text inside the first balanced parenthesis group, the index just after its closing paren)."""
    start = text.find("(")
    if start < 0:
        return None
    depth = 0
    quote = ""
    significant = ""
    for index in range(start, len(text)):
        char = text[index]
        if quote:
            if char == quote:
                quote = ""
            continue
        if _opens_string(char, significant):
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[start + 1 : index], index + 1
        if not char.isspace():
            significant = char
    return None


def _opens_string(char: str, significant: str) -> bool:
    """A double quote always opens a string literal; a single quote only right after ``=`` (a default value),
    so a Rust lifetime such as ``&'a str`` is never mistaken for one."""
    return char == '"' or (char == "'" and significant == "=")


def _arity(parameters: str) -> int:
    """Top-level comma-separated items of a parameter list; the bare ``*`` and ``/`` markers are not counted."""
    items: list[str] = []
    current: list[str] = []
    depth = 0
    quote = ""
    previous = ""
    significant = ""
    for char in parameters:
        if quote:
            current.append(char)
            if char == quote:
                quote = ""
            previous = char
            continue
        if _opens_string(char, significant):
            quote = char
        elif char in "([{<" and not (char == "<" and previous in ("-", "=")):
            depth += 1
        elif char in ")]}>" and not (char == ">" and previous in ("-", "=")):
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            items.append("".join(current).strip())
            current = []
            previous = char
            significant = char
            continue
        current.append(char)
        previous = char
        if not char.isspace():
            significant = char
    items.append("".join(current).strip())
    return len([item for item in items if item and item not in ("*", "/")])


def _return_type(rest: str) -> str:
    if not rest.startswith(_ARROW):
        return ""
    text = rest[len(_ARROW) :]
    cut = len(text)
    for marker in (" [", "  ("):
        position = text.find(marker)
        if position >= 0:
            cut = min(cut, position)
    return text[:cut].strip()


def describe_line(line: str) -> dict[str, Any]:
    """``{"name", "arity", "return_type"}`` for a rendered method line (with or without its ``  - `` bullet).

    arity is the number of top-level comma-separated items of the first balanced parenthesis group, not
    counting the bare markers ``*`` and ``/``, with brackets and angle brackets nesting. return_type is the text
    after the ``) -> `` that closes that group up to the first ``" ["`` or ``"  ("`` suffix, or "" when the line
    has no arrow.
    """
    text = _strip_bullet(line)
    group = _first_group(text)
    if group is None:
        return {"name": text.split("(", 1)[0].strip(), "arity": 0, "return_type": ""}
    inside, after = group
    return {
        "name": text[: text.find("(")].strip(),
        "arity": _arity(inside),
        "return_type": _return_type(text[after:]),
    }


# ---------------------------------------------------------------------------------------------------------------
# Sampling the committed fixture
# ---------------------------------------------------------------------------------------------------------------


def _methods_block(text: str) -> list[str]:
    """The lines of a chunk's ``Methods:`` block, as ``get_symbol`` extracts them (bullet removed)."""
    lines = text.splitlines()
    if "Methods:" not in lines:
        return []
    items: list[str] = []
    for line in lines[lines.index("Methods:") + 1 :]:
        if not line.startswith(_BULLET_PREFIX):
            break
        items.append(line[len(_BULLET_PREFIX) :].strip())
    return items


def sample_method_pairs(
    fixture: Mapping[str, Any], *, max_types: int, count: int = SAMPLE_SIZE
) -> list[tuple[str, str]]:
    """``(fqn, method line)`` pairs drawn from the fixture as the production chunk builder renders it.

    Every line of every chunk's ``Methods:`` block is a candidate (``Example: `` and ``Doc: `` chunks are not
    symbols). At most ``count`` are returned, the pairs at indexes ``(k * n) // count``; no randomness, so the
    sample is the same on every run.
    """
    from foss_mcp.indexing.chunk_builder import build_chunks_from_api_surface
    from foss_mcp.mcp.tools.get_symbol import extract_fqn

    pairs: list[tuple[str, str]] = []
    for chunk in build_chunks_from_api_surface(fixture, title="probe", max_types=max_types):
        fqn = extract_fqn(chunk.text)
        if fqn is None or fqn.startswith(_NON_SYMBOL_PREFIXES):
            continue
        pairs.extend((fqn, line) for line in _methods_block(chunk.text))
    if len(pairs) <= count:
        return pairs
    return [pairs[(k * len(pairs)) // count] for k in range(count)]


# ---------------------------------------------------------------------------------------------------------------
# P1: the generation
# ---------------------------------------------------------------------------------------------------------------


def probe_generation(client: Any, family: str, platform: str, manifests_dir: Path) -> dict[str, Any]:
    """P1: the generation id the endpoint reports equals the one committed under infra/demo-manifests."""
    try:
        committed_id = committed_generation_id(manifests_dir, family, platform)
    except (FileNotFoundError, ValueError) as exc:
        return make_entry("P1", "fail", {}, f"no committed generation to compare with: {exc}")
    report = client.invoke("report_index_freshness", {})
    served_id = report.get("indexed_generation_id") if isinstance(report, dict) else None
    if not isinstance(served_id, str) or not served_id:
        return make_entry(
            "P1",
            "fail",
            {"committed_generation_id": committed_id},
            "report_index_freshness carries no indexed_generation_id, so the served generation is unknown "
            f"(committed {committed_id})",
        )
    evidence = {"served_generation_id": served_id, "committed_generation_id": committed_id}
    if served_id != committed_id:
        return make_entry(
            "P1",
            "fail",
            evidence,
            f"the container serves generation {served_id} but {family}/{platform} commits {committed_id}",
        )
    return make_entry("P1", "pass", evidence)


# ---------------------------------------------------------------------------------------------------------------
# P2: the signatures
# ---------------------------------------------------------------------------------------------------------------


def _served_methods(answer: Any) -> list[str] | None:
    """get_symbol's methods, or None when the answer says the symbol is not served."""
    if not isinstance(answer, dict) or answer.get("found") is False:
        return None
    methods = answer.get("methods")
    return [str(item) for item in methods] if isinstance(methods, list) else []


def _served_members(answer: Any) -> list[str]:
    return [str(item) for item in answer] if isinstance(answer, list) else []


def _nearest(expected: dict[str, Any], served: list[str]) -> dict[str, Any] | None:
    """The served line of the same method name closest to the expected one (arity first, then return type)."""
    candidates = [
        described for described in map(describe_line, served) if described["name"] == expected["name"]
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda item: (
            abs(item["arity"] - expected["arity"]),
            item["return_type"] != expected["return_type"],
        ),
    )


def _failure(fqn: str, expected_line: str, served: list[str] | None, problem: str) -> dict[str, Any]:
    expected = describe_line(expected_line)
    nearest = _nearest(expected, served) if served else None
    return {
        "fqn": fqn,
        "expected": expected_line,
        "expected_arity": expected["arity"],
        "served_arity": nearest["arity"] if nearest else None,
        "expected_return_type": expected["return_type"],
        "served_return_type": nearest["return_type"] if nearest else None,
        "problem": problem,
    }


def probe_signatures(client: Any, key: str, entry: Mapping[str, Any], fixtures: Path) -> dict[str, Any]:
    """P2: sampled (type, method) signatures served by the endpoint equal the committed fixture, rendered."""
    path = Path(fixtures) / key / "api_surface.json"
    fixture = json.loads(path.read_text(encoding="utf-8-sig"))
    max_types = int(entry.get("maxTypes", DEFAULT_MAX_TYPES))
    pairs = sample_method_pairs(fixture, max_types=max_types)
    if not pairs:
        return make_entry(
            "P2",
            "not_applicable",
            {},
            f"the committed fixture {key} renders no method line, so there is no signature to compare",
        )
    symbols: dict[str, list[str] | None] = {}
    members: dict[str, list[str]] = {}
    failures: list[dict[str, Any]] = []
    for fqn, line in pairs:
        if fqn not in symbols:
            symbols[fqn] = _served_methods(client.invoke("get_symbol", {"fqn": fqn}))
            members[fqn] = _served_members(client.invoke("list_members", {"fqn": fqn}))
        methods = symbols[fqn]
        if methods is None:
            failures.append(_failure(fqn, line, None, "get_symbol does not serve this type (not served)"))
        elif line not in methods:
            failures.append(
                _failure(fqn, line, methods, "get_symbol serves no method line equal to the committed one")
            )
        elif line not in members[fqn]:
            failures.append(
                _failure(fqn, line, members[fqn], "list_members lacks a line that get_symbol has")
            )
    evidence = {
        "sampled": len(pairs),
        "failed": len(failures),
        "failures": failures[:MAX_REPORTED_FAILURES],
    }
    if failures:
        first = failures[0]
        return make_entry(
            "P2",
            "fail",
            evidence,
            f"{len(failures)} of {len(pairs)} sampled signatures differ from the committed fixture; first: "
            f"{first['fqn']} expected {first['expected']!r} ({first['problem']}; expected {first['expected_arity']} "
            f"parameter(s) returning {first['expected_return_type']!r}, served {first['served_arity']} "
            f"returning {first['served_return_type']!r})",
        )
    return make_entry("P2", "pass", evidence)


# ---------------------------------------------------------------------------------------------------------------
# Both probes
# ---------------------------------------------------------------------------------------------------------------


def run_probes(
    client: Any, key: str, entry: Mapping[str, Any], *, fixtures: Path, manifests_dir: Path
) -> list[dict[str, Any]]:
    """[P1, P2]. An exception inside a probe is that probe's ``fail`` entry, never a crash."""
    family, platform = str(entry["family"]), str(entry["platform"])
    steps: list[tuple[str, Callable[[], dict[str, Any]]]] = [
        ("P1", lambda: probe_generation(client, family, platform, manifests_dir)),
        ("P2", lambda: probe_signatures(client, key, entry, fixtures)),
    ]
    entries = []
    for number, step in steps:
        try:
            entries.append(step())
        except Exception as exc:
            entries.append(make_entry(number, "fail", {}, f"could not run: {type(exc).__name__}: {exc}"))
    return entries


# ---------------------------------------------------------------------------------------------------------------
# Command line: one running endpoint
# ---------------------------------------------------------------------------------------------------------------


def _open_live_client(url: str) -> Any:
    name = "_served_provenance_pilot_workflow_proof"
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, PROOF_SCRIPT)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {PROOF_SCRIPT}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module  # its dataclass needs the module registered while it executes
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(name, None)
            raise
    return module.open_live_client(url)


def _chart_entry(pilot: str) -> dict[str, Any]:
    if str(_DEPLOY_DIR) not in sys.path:
        sys.path.insert(0, str(_DEPLOY_DIR))
    import pilots as chart

    (entry,) = chart.select_pilots(chart.load_pilots(), [pilot])
    return entry


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare what one running MCP endpoint serves with what the repository commits."
    )
    parser.add_argument(
        "--url", required=True, help="base URL of the running endpoint, e.g. http://127.0.0.1:8080"
    )
    parser.add_argument(
        "--pilot", required=True, metavar="FAMILY_PLATFORM", help="the pilot served, e.g. pdf_net"
    )
    parser.add_argument(
        "--fixtures", type=Path, default=DEFAULT_FIXTURES, help="fixtures root (default tests/fixtures)"
    )
    parser.add_argument(
        "--manifests",
        type=Path,
        default=DEFAULT_MANIFESTS,
        help="committed generations (default infra/demo-manifests)",
    )
    parser.add_argument("--report", type=Path, default=None, help="optional path of a JSON report")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, client_factory: Callable[[str], Any] | None = None) -> int:
    args = parse_args(argv)
    try:
        entry = _chart_entry(args.pilot)
    except (ValueError, OSError) as exc:
        print(f"served_provenance: {exc}", file=sys.stderr)
        return 2
    try:
        client = (client_factory or _open_live_client)(args.url)
        entries = run_probes(client, args.pilot, entry, fixtures=args.fixtures, manifests_dir=args.manifests)
    except Exception as exc:
        reason = f"could not run: {type(exc).__name__}: {exc}"
        entries = [make_entry(number, "fail", {}, reason) for number in PROVENANCE_TITLES]
    for item in entries:
        suffix = f": {item['reason']}" if item["reason"] else ""
        print(f"{item['id']} {item['status'].upper()}{suffix}".encode("ascii", "replace").decode("ascii"))
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps({"pilot": args.pilot, "url": args.url, "entries": entries}, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
    statuses = [item["status"] for item in entries]
    return 0 if "fail" not in statuses and "pass" in statuses else 1


if __name__ == "__main__":
    sys.exit(main())
