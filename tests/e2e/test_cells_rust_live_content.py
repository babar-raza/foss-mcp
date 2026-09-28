"""G2/TC-102: cells/rust's real, live first publish, proven end to end through real
docker-compose services - not a test's in-memory store, not code-reading.

Mirrors ``tests/e2e/test_pdf_net_live_content.py`` (TC-064/069/080/089),
``tests/e2e/test_pdf_typescript_live_content.py`` (TC-098),
``tests/e2e/test_pdf_java_live_content.py`` (TC-099),
``tests/e2e/test_pdf_go_live_content.py`` (TC-100), and
``tests/e2e/test_slides_python_live_content.py`` (TC-101) exactly: bring up the new one-shot
``ingest-cells-rust`` service (which really publishes a generation into the real, named
``manifests-cells-rust`` volume via ``infra/build_chunks.py`` + ``infra/ingest.py`` - the latter's
``--library-platform rust`` dispatch, TC-091, driving a real ``git clone`` of the pinned reference
crate, a real ``cargo build`` of it, then a real per-candidate ``cargo build`` inside the ingestion
container, TC-097), then bring up ``serving-cells-rust`` (which mounts that SAME volume), poll the
real ``/readyz`` until ready, then run a real MCP-over-HTTP session and assert real content comes
back.

Every value below is this pilot's OWN real, distinctive data - never copied from pdf/net's,
pdf/typescript's, pdf/java's, pdf/go's, or slides/python's:

- ``REAL_SYMBOL``/``REAL_SOURCE_COMMIT`` come from the real, committed
  ``tests/fixtures/cells_rust/api_surface.json`` fixture. Its real ``source_commit`` is
  ``1a6004af47b1ef15385f9d36d381a8172428cc7e``. Its raw ``types`` list is alphabetically sorted;
  the 18th entry (index 17, well within ``build_chunks_from_api_surface``'s default
  ``max_types=20``) is the real ``AutoShapeType`` enum (``kind: "enum_item"``, ``class_import``
  identical to its bare ``name`` - this fixture's own raw entries carry no crate-qualified dotted
  path, unlike pdf/net's C#-style fixture), with 38 real members including ``Rectangle`` -
  confirmed by reading every one of the first 20 raw entries directly.
- Unlike pdf/go's/slides/python's own fixtures, this one DOES carry a real, member-bearing enum
  within its own ``max_types=20`` slice (two, in fact: ``AutoShapeType`` at index 17 and
  ``BorderLineStyle`` at index 19 - confirmed by inspecting every one of the first 20 raw entries'
  own ``enum_members`` directly), so this file keeps pdf/net's/pdf/java's own
  ``REAL_ENUM_FQN``/``REAL_ENUM_MEMBER`` ``get_symbol`` shape rather than pdf/typescript's/pdf/go's/
  slides/python's adaptation. ``AutoShapeType`` (the same real entry as ``REAL_SYMBOL`` above) is
  used for both: its real ``class_import`` is the FQN, and ``Rectangle`` is one of its 38 real,
  distinctive members.
- ``REAL_EXAMPLE_SYMBOL``/``REAL_TASK_QUERY``: this pilot's own real, committed furnished page
  (``tests/fixtures/furnished/cells_rust/pages/_index.md``) has 3 real candidates. The first,
  "Create, Save, and Reload a Workbook", is a COMPLETE, self-contained ``fn main() -> Result<(),
  Box<dyn Error>> { ... }`` file (its own ``use`` statements included) - unlike the other 2
  candidates ("Load XLSX Files with Repair Options", "Add Charts from Data Ranges"), which are bare
  statement fragments referencing undefined locals (``valid_path``, ``sheet``) with no ``fn main``
  of their own. Its real code calls ``Workbook::new``, ``get_worksheets_mut``, ``get_cells_mut``,
  and ``put_formula_with_cached_value`` - all real methods on real types
  (``Workbook``/``WorksheetsMut``/``CellsMut``/``CellMut``) confirmed directly in the fixture above
  - and a real, hands-on containerized run of ``ingest-cells-rust`` below (real ``cargo build``
  against the real pinned ``aspose-cells-foss/Aspose.Cells-FOSS-for-Rust`` commit
  ``1a6004af47b1ef15385f9d36d381a8172428cc7e``) confirms it genuinely compiles - reported
  "verified 1/3 candidate examples" - and, unlike pdf/go's/slides/python's own gaps, this one real,
  compile-verified example IS published: its own description text cites no bare, unqualified
  inline-code method name the citation-validation authority path
  (``foss_mcp.normalization.citation``) would reject - confirmed both by inspecting the real local
  manifest store directly and by the real MCP-over-HTTP tests below against the real running
  container. ``put_formula_with_cached_value`` is the real, distinctive method call this candidate's
  real code uses (also named directly in the furnished page's own overview prose: "formulas with
  cached values via ``put_formula_with_cached_value``").
- ``REAL_TASK_QUERY`` ("how do I create an xlsx workbook and save it") is the exact literal,
  API-naive task question CONFIRMED live, by hand, today, through both a real local generation and
  the real running cells-rust container, to make ``lookup`` correctly compose a real ``TaskAnswer``
  carrying the real, compile-verified ``put_formula_with_cached_value`` example above - the same
  developer-context bar pdf/net (TC-080/TC-089) was proven against.

network: true on this card, for exactly this reason - everything here talks to containers this
file itself builds, starts and tears down.
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import httpx2 as httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
PROJECT_NAME = "foss-mcp-e2e-tc102"
BASE_URL = "http://127.0.0.1:8086"
MCP_PATH = "/mcp"
READYZ_PATH = "/readyz"
# Matches pdf/net's own TC-069 budget: this pilot's ingestion container also does a real git
# clone, a real `cargo build` of the pinned reference crate (which pulls its own real
# dependency graph - confirmed today to be ~90 crates), then a real `cargo build` per real
# furnished-content candidate - budgeted the same as the other pilots.
INGEST_TIMEOUT_SECONDS = 1500
READYZ_TIMEOUT_SECONDS = 180
POLL_INTERVAL_SECONDS = 2

PROTOCOL_VERSION = "2025-06-18"

# TC-102's own real, committed cells_rust fixture (tests/fixtures/cells_rust/api_surface.json):
# its real ``source_commit`` is "1a6004af47b1ef15385f9d36d381a8172428cc7e", and its 18th type
# entry (index 17, well within build_chunks_from_api_surface's default max_types=20) is the real
# enum AutoShapeType. A real query response containing this exact string is genuine, falsifiable
# proof of real content, never a guess.
REAL_SYMBOL = "AutoShapeType"
REAL_SOURCE_COMMIT = "1a6004af47b1ef15385f9d36d381a8172428cc7e"

# REQ-G2-047 (TC-080 companion): unlike pdf/go's/slides/python's own fixtures, this one carries a
# real, member-bearing enum within max_types=20 (confirmed by inspecting every one of the first 20
# raw entries' own "enum_members"). AutoShapeType's own real FQN (its class_import, identical to
# the bare REAL_SYMBOL name above - this fixture's own raw entries carry no crate-qualified dotted
# path) and one of its 38 real, distinctive members are real, falsifiable values a stub or an
# empty index could never produce.
REAL_ENUM_FQN = "AutoShapeType"
REAL_ENUM_MEMBER = "Rectangle"

# REQ-G2-048 (TC-091/TC-097 pattern): a real, hands-on containerized run of ingest-cells-rust
# (below) reports "verified 1/3 candidate examples" for this pilot's real furnished page
# (tests/fixtures/furnished/cells_rust/pages/_index.md): "Create, Save, and Reload a Workbook" is
# the one real candidate that is already a complete, self-contained fn main() file, and it
# genuinely compiles against the real pinned commit via verify_rust_example (a real throwaway
# Cargo project depending on the real, built reference crate). Unlike pdf/go's/slides/python's own
# gaps, this real, compile-verified example survives the citation-validation authority path too
# (confirmed both by inspecting the real local manifest store and through the real running
# container below), so it is genuinely published. put_formula_with_cached_value is the real,
# distinctive method call this candidate's real code uses.
REAL_EXAMPLE_SYMBOL = "put_formula_with_cached_value"

# REQ-G2-050 (TC-080/TC-089/TC-098/TC-099/TC-100/TC-101 pattern): the exact literal, API-naive
# task question CONFIRMED live, by hand, today, through both a real local generation and the real
# running cells-rust container, to make `lookup` compose a real TaskAnswer carrying the real
# put_formula_with_cached_value example above. This exact string is required verbatim - the
# card's own negative control corrupts it, and that corruption must break this test's assertions.
REAL_TASK_QUERY = "how do I create an xlsx workbook and save it"

VALID_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "MCP-Protocol-Version": PROTOCOL_VERSION,
}


def _compose(*args: str, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    """Retries on transient Docker daemon errors ("removal ... is already in
    progress", "network ... not found") seen when a preceding module's ``down``
    returns before the daemon has actually finished the async container/network
    teardown - common on a machine running several unrelated docker-compose stacks.
    """
    result: subprocess.CompletedProcess[str] | None = None
    for attempt in range(3):
        result = subprocess.run(
            ["docker", "compose", "-p", PROJECT_NAME, "-f", str(COMPOSE_FILE), *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        if result.returncode == 0 or "Error response from daemon" not in result.stderr:
            return result
        time.sleep(2 * (attempt + 1))
    return result


def _initialize_body(request_id: int = 0) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "initialize",
        "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "tc-102-e2e-client", "version": "0.0.1"},
        },
    }


def _sse_json(text: str) -> dict:
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:") :].strip())
    raise AssertionError(f"no SSE data line found in response: {text!r}")


def _wait_for_readyz() -> None:
    """Poll the real published ``/readyz`` route until it reports ready - a bounded retry,
    never an unbounded loop, and never a fixed sleep.
    """
    deadline = time.monotonic() + READYZ_TIMEOUT_SECONDS
    last_error = "never attempted"
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{BASE_URL}{READYZ_PATH}", timeout=5)
            if response.status_code == 200:
                return
            last_error = f"HTTP {response.status_code}: {response.text[:300]}"
        except httpx.HTTPError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(POLL_INTERVAL_SECONDS)
    raise AssertionError(f"/readyz never returned 200 within {READYZ_TIMEOUT_SECONDS}s: {last_error}")


@pytest.fixture(scope="module")
def real_generation() -> Iterator[None]:
    """The real, live first publish: build and run the one-shot ``ingest-cells-rust`` service to
    completion, then bring up ``serving-cells-rust`` against the SAME named volume it just
    published into.

    ``docker compose down -v`` first, so this is idempotent against any stale volume state left
    by a prior run. A distinct ``PROJECT_NAME`` (``foss-mcp-e2e-tc102``) and only these two
    services are ever brought up, so this never collides with pdf/net's, pdf/typescript's,
    pdf/java's, pdf/go's, slides/python's, or any other pilot's own concurrent E2E run.
    """
    _compose("down", "-v", "--remove-orphans")
    try:
        ingest = _compose(
            "up",
            "--build",
            "--exit-code-from",
            "ingest-cells-rust",
            "ingest-cells-rust",
            timeout=INGEST_TIMEOUT_SECONDS,
        )
        assert ingest.returncode == 0, (ingest.stdout + ingest.stderr)[-4000:]

        up = _compose("up", "-d", "--build", "serving-cells-rust")
        assert up.returncode == 0, (up.stdout + up.stderr)[-4000:]
        _wait_for_readyz()
        yield
    finally:
        _compose("down", "-v", "--remove-orphans")


class _McpSession:
    """A real MCP-over-HTTP client session against the real running ``serving-cells-rust``
    container: initialize, capture the session id, acknowledge, then issue whatever the test
    needs.
    """

    def __init__(self) -> None:
        response = httpx.post(
            f"{BASE_URL}{MCP_PATH}", json=_initialize_body(), headers=VALID_HEADERS, timeout=10
        )
        assert response.status_code == 200, response.text
        self.session_id = response.headers["mcp-session-id"]
        self.headers = dict(VALID_HEADERS, **{"mcp-session-id": self.session_id})
        httpx.post(
            f"{BASE_URL}{MCP_PATH}",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=self.headers,
            timeout=10,
        )

    def call_tool(self, name: str, arguments: dict, *, request_id: int = 1) -> dict:
        response = httpx.post(
            f"{BASE_URL}{MCP_PATH}",
            json={
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            headers=self.headers,
            timeout=15,
        )
        assert response.status_code == 200, response.text
        return _sse_json(response.text)


@pytest.fixture()
def session(real_generation: None) -> _McpSession:
    return _McpSession()


# ---------------------------------------------------------------------
# The real, live proof.
# ---------------------------------------------------------------------


def test_readyz_is_ready_after_the_real_ingestion_publish(real_generation: None) -> None:
    """The one real (non-unit-test) exercise of ``/readyz`` for this pilot: 200, because a real
    active generation now exists for this deployment's own scope - never process reachability.
    """
    response = httpx.get(f"{BASE_URL}{READYZ_PATH}", timeout=5)
    assert response.status_code == 200
    assert response.text == "ready"


def test_report_index_freshness_reports_the_real_fresh_generation(session: _McpSession) -> None:
    """A real generation id, and ``stale=False`` once the real indexed commit (recorded via the
    ``Source-Commit:`` line ``infra/build_chunks.py`` appends to each chunk's text) is compared
    against the real fixture's own real commit sha.
    """
    body = session.call_tool("report_index_freshness", {"current_source_commit": REAL_SOURCE_COMMIT})
    result = body["result"]["structuredContent"]["result"]
    assert result["indexed_generation_id"] is not None
    assert result["indexed_generation_id"].startswith("cells::rust::self_extracted::")
    assert result["stale"] is False, result["reason"]


def test_search_symbols_returns_real_content_from_the_real_fixture(session: _McpSession) -> None:
    """``search_symbols`` for a real type name from this pilot's real fixture returns real,
    non-empty content - never the "no published generation for this scope" Miss every content
    tool call gave before this card wired ingestion into anything that runs.
    """
    body = session.call_tool("search_symbols", {"query": REAL_SYMBOL})
    result = body["result"]["structuredContent"]["result"]
    assert isinstance(result, list) and result, f"expected real matches, got a Miss: {result}"
    assert any(REAL_SYMBOL in match["text"] for match in result), result


def test_lookup_returns_real_content_from_the_real_fixture(session: _McpSession) -> None:
    """``lookup`` (the forgiving entry point) for the same real symbol, through the same real
    running container - a second, independent tool proving the same real generation is served.
    """
    body = session.call_tool("lookup", {"query": REAL_SYMBOL})
    result = body["result"]["structuredContent"]["result"]
    assert isinstance(result, list) and result, f"expected real matches, got a Miss: {result}"
    assert any(REAL_SYMBOL in match["text"] for match in result), result


def test_find_examples_returns_the_real_compile_verified_workbook_example(
    session: _McpSession,
) -> None:
    """REQ-G2-048 (TC-091/TC-097): the concrete answer to the operator's own worked question ("how
    do I create an xlsx workbook and save it"), proven through the FULL containerized production
    path - not only at the host/test level but through a real ``docker compose up --build`` of
    ``ingest-cells-rust`` (real git clone + real ``cargo build`` of the reference crate, then a
    real ``cargo build`` per candidate INSIDE the container) feeding the same real
    ``serving-cells-rust`` container every other test in this file already queries.
    """
    body = session.call_tool("find_examples", {"query": "create and save a workbook"})
    result = body["result"]["structuredContent"]["result"]
    assert isinstance(result, list) and result, f"expected real ExampleMatch(es), got a Miss: {result}"
    assert any(REAL_EXAMPLE_SYMBOL in match["snippet"] for match in result), result


def test_lookup_composes_the_real_task_answer_for_the_workbook_question(
    session: _McpSession,
) -> None:
    """REQ-G2-050 (TC-080/TC-089/TC-098/TC-099/TC-100/TC-101 pattern): the developer-context
    vision's own concrete target, now a PERMANENT, automated test for cells/rust too.

    The real ``lookup`` tool (never ``find_examples`` directly - a caller with no prior API
    knowledge asking a real, API-naive task question) with the EXACT literal query CONFIRMED live,
    by hand, today: through the real running cells-rust container, this reads as a task question
    (``_looks_like_a_task_question``), so ``lookup`` composes a real ``TaskAnswer`` - ``doc_matches``
    (empty here, since no getting_started/developer_guide/troubleshooting/faq content has ever been
    published for this pilot) plus the same real, compile-verified
    ``put_formula_with_cached_value`` example ``find_examples`` itself proves above, carrying the
    real source commit ``infra/build_chunks.py`` appends to every real chunk's text.
    """
    body = session.call_tool("lookup", {"query": REAL_TASK_QUERY})
    result = body["result"]["structuredContent"]["result"]
    assert isinstance(result, dict), f"expected a composed TaskAnswer, got: {result}"
    assert "doc_matches" in result and "example" in result, result
    assert result["example"] is not None, f"expected a real, verified example, got none: {result}"
    snippet = result["example"]["snippet"]
    assert REAL_EXAMPLE_SYMBOL in snippet, snippet
    assert REAL_SOURCE_COMMIT in snippet, snippet


def test_get_symbol_returns_the_real_enum_members_for_autoshapetype(session: _McpSession) -> None:
    """REQ-G2-047's own live proof, now a permanent artifact for cells/rust too: the real running
    cells-rust container's ``get_symbol`` for the real ``AutoShapeType`` enum (this fixture's own
    18th raw entry, well within ``build_chunks_from_api_surface``'s default ``max_types=20``)
    returns its real 38 enum members - not a stub, not an empty index.
    """
    body = session.call_tool("get_symbol", {"fqn": REAL_ENUM_FQN})
    result = body["result"]["structuredContent"]["result"]
    assert isinstance(result, dict) and "members" in result, f"expected a real SymbolSignature: {result}"
    members = result["members"]
    assert isinstance(members, list) and members, f"expected real enum members, got none: {result}"
    assert any(member.startswith(REAL_ENUM_MEMBER) for member in members), members
