"""G2/TC-098: pdf/typescript's real, live first publish, proven end to end through real
docker-compose services - not a test's in-memory store, not code-reading.

Mirrors ``tests/e2e/test_pdf_net_live_content.py`` (TC-064/069/080/089) exactly: bring up the
new one-shot ``ingest-pdf-typescript`` service (which really publishes a generation into the
real, named ``manifests-pdf-typescript`` volume via ``infra/build_chunks.py`` +
``infra/ingest.py`` - the latter's ``--library-platform typescript`` dispatch, TC-091, driving a
real ``npm install`` + a real ``npx tsc`` type-check of every candidate example inside the
ingestion container), then bring up ``serving-pdf-typescript`` (which mounts that SAME volume),
poll the real ``/readyz`` until ready, then run a real MCP-over-HTTP session and assert real
content comes back.

Every value below is this pilot's OWN real, distinctive data - never copied from pdf/net's:

- ``REAL_SYMBOL``/``REAL_SOURCE_COMMIT`` come from the real, committed
  ``tests/fixtures/pdf_typescript/api_surface.json`` fixture. Unlike pdf/net's fixture, this
  one's real first 20 entries (``build_chunks_from_api_surface``'s ``max_types=20`` takes the
  raw fixture's own first 20, in file order - confirmed by reading
  ``foss_mcp.indexing.chunk_builder.build_chunks_from_api_surface``) are all ``function``/
  ``constant`` kinds, never a class/enum - this reduced fixture simply has no enum-shaped entry
  at all within reach of ``max_types=20`` (confirmed by inspecting every entry's own ``kind``).
  ``BORDER_STYLES`` (entry #11: ``readonly FieldBorderStyle[] = ['solid', 'dashed', 'beveled',
  'inset', 'underline']``) is the real, distinctive constant used here in its place - the
  ``get_symbol`` test below is adapted accordingly (asserting its real ``Value:`` line, since
  ``chunk_builder._format_type_text`` emits ``Value:`` rather than ``Members:`` for a
  ``kind == "constant"`` entry; there is no comparable ``REAL_ENUM_FQN``/``REAL_ENUM_MEMBER``
  pair for this pilot).
- ``REAL_EXAMPLE_SYMBOL``/``REAL_TASK_QUERY`` come from the real, committed
  ``tests/fixtures/furnished/pdf_typescript/pages/_index.md`` furnished content. Unlike
  pdf/net's furnished page (where only 1 of 3 candidates actually compiled against the pinned
  reference commit), all 4 of this pilot's real candidates compile-verify successfully against
  the pinned commit ``155bfc7a33f0ba23fb4252b6ba201828b02a5b9d`` (confirmed today by a real,
  hands-on run of ``prepare_typescript_library``/``verify_typescript_example`` against the real
  fixture, and again through the full ``infra/build_chunks.py`` + ``infra/ingest.py`` CLI pair
  published to a real local manifest store: "verified 4/4 candidate examples"). The literal
  query below was CONFIRMED, by that same real local run, to make ``lookup`` compose a real
  ``TaskAnswer`` carrying exactly the "Create a New PDF from Scratch" example - real BM25
  lexical ranking over all 4 real verified-example chunks, not a guess.

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
PROJECT_NAME = "foss-mcp-e2e-tc098"
BASE_URL = "http://127.0.0.1:8081"
MCP_PATH = "/mcp"
READYZ_PATH = "/readyz"
# Matches pdf/net's own TC-069 budget: this pilot's ingestion container also does a real git
# clone plus a real `npm install` + `npx tsc` build/type-check (of the reference library once,
# then one type-check per real furnished-content candidate) - genuinely slower than a type-only
# publish.
INGEST_TIMEOUT_SECONDS = 1500
READYZ_TIMEOUT_SECONDS = 180
POLL_INTERVAL_SECONDS = 2

PROTOCOL_VERSION = "2025-06-18"

# TC-097/TC-098's own real, committed pdf/typescript fixture
# (tests/fixtures/pdf_typescript/api_surface.json): its real ``source_commit`` is
# "155bfc7a33f0ba23fb4252b6ba201828b02a5b9d", and its 11th type entry (well within
# build_chunks_from_api_surface's default max_types=20) is the real constant BORDER_STYLES -
# a real, distinctive, well-documented entry. A real query response containing this exact
# string is genuine, falsifiable proof of real content, never a guess.
REAL_SYMBOL = "BORDER_STYLES"
REAL_SOURCE_COMMIT = "155bfc7a33f0ba23fb4252b6ba201828b02a5b9d"

# No enum-shaped entry exists anywhere within this fixture's own max_types=20 slice (confirmed
# by inspecting every entry's own "kind" - only "function" and "constant" appear); BORDER_STYLES
# above is a real "constant" entry instead, and its own real value is what the adapted
# get_symbol test below asserts.
REAL_CONSTANT_VALUE_FRAGMENT = "'solid', 'dashed', 'beveled', 'inset', 'underline'"

# REQ-G2-048 (TC-097/TC-091): the real, compile-verified example this card's real containerized
# ingestion run produces - of the 4 real candidates in tests/fixtures/furnished/pdf_typescript's
# real furnished page, ALL 4 really type-check against the real pinned
# aspose-pdf-foss/Aspose.PDF-FOSS-for-TypeScript commit (confirmed by hand today, at the host
# level, via a real prepare_typescript_library/verify_typescript_example run: "verified 4/4
# candidate examples"). ``AddText`` is the real, distinctive method call the "Create a New PDF
# from Scratch" candidate's real code uses - never a guess at what a container might return.
REAL_EXAMPLE_SYMBOL = "AddText"

# REQ-G2-050 (TC-080/TC-089 pattern): the exact literal, API-naive task question CONFIRMED
# today, by a real local run of the full build_chunks.py + ingest.py + lookup() pipeline against
# this pilot's own real fixtures, to make ``lookup`` compose a real ``TaskAnswer`` carrying the
# real AddText example above (real BM25 lexical ranking picks it out uniquely from among all 4
# real verified-example chunks). This exact string is required verbatim - the card's own
# negative control corrupts it, and that corruption must break this test's assertions.
REAL_TASK_QUERY = "how do I create a new PDF from scratch"

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
            "clientInfo": {"name": "tc-098-e2e-client", "version": "0.0.1"},
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
    """The real, live first publish: build and run the one-shot ``ingest-pdf-typescript``
    service to completion, then bring up ``serving-pdf-typescript`` against the SAME named
    volume it just published into.

    ``docker compose down -v`` first, so this is idempotent against any stale volume state left
    by a prior run. A distinct ``PROJECT_NAME`` (``foss-mcp-e2e-tc098``) and only these two
    services are ever brought up, so this never collides with pdf/net's own concurrent E2E run
    or any other pilot's.
    """
    _compose("down", "-v", "--remove-orphans")
    try:
        ingest = _compose(
            "up",
            "--build",
            "--exit-code-from",
            "ingest-pdf-typescript",
            "ingest-pdf-typescript",
            timeout=INGEST_TIMEOUT_SECONDS,
        )
        assert ingest.returncode == 0, (ingest.stdout + ingest.stderr)[-4000:]

        up = _compose("up", "-d", "--build", "serving-pdf-typescript")
        assert up.returncode == 0, (up.stdout + up.stderr)[-4000:]
        _wait_for_readyz()
        yield
    finally:
        _compose("down", "-v", "--remove-orphans")


class _McpSession:
    """A real MCP-over-HTTP client session against the real running ``serving-pdf-typescript``
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
    assert result["indexed_generation_id"].startswith("pdf::typescript::self_extracted::")
    assert result["stale"] is False, result["reason"]


def test_search_symbols_returns_real_content_from_the_real_fixture(session: _McpSession) -> None:
    """``search_symbols`` for a real constant name from this pilot's real fixture returns real,
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


def test_find_examples_returns_the_real_compile_verified_addtext_example(
    session: _McpSession,
) -> None:
    """REQ-G2-048 (TC-091/TC-097): the concrete answer to the operator's own worked question
    ("how do I create a new PDF from scratch"), proven through the FULL containerized
    production path - not only at the host/test level, but through a real
    ``docker compose up --build`` of ``ingest-pdf-typescript`` (real git clone + a real
    ``npm install`` + ``npx tsc`` type-check INSIDE the container) feeding the same real
    ``serving-pdf-typescript`` container every other test in this file already queries.

    A task-oriented query ('create a new pdf from scratch') takes ``find_examples``'s
    semantic/lexical fallback path (this real fixture chunk is deliberately labeled
    ``FQN: Example: Create a New PDF from Scratch``, not a real symbol FQN, so no exact-match
    path would fire here) - ranking every real ``Example:``-bearing chunk against the query text
    and returning the one real, compile-verified match.
    """
    body = session.call_tool("find_examples", {"query": "create a new pdf from scratch"})
    result = body["result"]["structuredContent"]["result"]
    assert isinstance(result, list) and result, f"expected real ExampleMatch(es), got a Miss: {result}"
    assert any(REAL_EXAMPLE_SYMBOL in match["snippet"] for match in result), result


def test_lookup_composes_the_real_task_answer_for_the_new_pdf_question(
    session: _McpSession,
) -> None:
    """REQ-G2-050 (TC-080/TC-089 pattern): the developer-context vision's own concrete target,
    now proven for pdf/typescript too - not only the supervisor's repeated manual, ephemeral
    hand-verification.

    The real ``lookup`` tool (never ``find_examples`` directly - a caller with no prior API
    knowledge asking a real, API-naive task question) with the EXACT literal query CONFIRMED
    live, by hand, today: through the real running pdf/typescript container, this reads as a
    task question (``_looks_like_a_task_question``), so ``lookup`` composes a real
    ``TaskAnswer`` - ``doc_matches`` (empty here, since no getting_started/developer_guide/
    troubleshooting/faq content has ever been published for this pilot) plus the same real,
    compile-verified ``AddText`` example ``find_examples`` itself proves above, carrying the
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


def test_get_symbol_returns_the_real_value_for_border_styles(session: _McpSession) -> None:
    """This pilot's own equivalent of pdf/net's enum-``get_symbol`` proof, adapted to the real
    entry shape this fixture actually has: no enum-shaped entry exists anywhere within
    ``max_types=20`` of this real, reduced fixture (confirmed by inspecting every entry's own
    ``kind``), so this asserts the real running pdf/typescript container's ``get_symbol`` for
    the real ``BORDER_STYLES`` constant returns its real, distinctive value line - not a stub,
    not an empty index.
    """
    body = session.call_tool("get_symbol", {"fqn": REAL_SYMBOL})
    result = body["result"]["structuredContent"]["result"]
    assert isinstance(result, dict) and "raw_text" in result, f"expected a real SymbolSignature: {result}"
    assert result["kind"] == "constant", result
    assert REAL_CONSTANT_VALUE_FRAGMENT in result["raw_text"], result
