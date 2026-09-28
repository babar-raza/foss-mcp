"""G2/TC-099: pdf/java's real, live first publish, proven end to end through real
docker-compose services - not a test's in-memory store, not code-reading.

Mirrors ``tests/e2e/test_pdf_net_live_content.py`` (TC-064/069/080/089) and
``tests/e2e/test_pdf_typescript_live_content.py`` (TC-098) exactly: bring up the new one-shot
``ingest-pdf-java`` service (which really publishes a generation into the real, named
``manifests-pdf-java`` volume via ``infra/build_chunks.py`` + ``infra/ingest.py`` - the latter's
``--library-platform java`` dispatch, TC-091, driving a real ``mvn package`` build of the pinned
reference library and a real ``javac`` compile of every candidate example inside the ingestion
container), then bring up ``serving-pdf-java`` (which mounts that SAME volume), poll the real
``/readyz`` until ready, then run a real MCP-over-HTTP session and assert real content comes
back.

Every value below is this pilot's OWN real, distinctive data - never copied from pdf/net's or
pdf/typescript's:

- ``REAL_SYMBOL``/``REAL_SOURCE_COMMIT`` come from the real, committed
  ``tests/fixtures/pdf_java/api_surface.json`` fixture. Its real ``source_commit`` is
  ``db2d3f0622f035825419c6d46727022064f39f15``, and its 4th type entry (index 3, well within
  ``build_chunks_from_api_surface``'s default ``max_types=20``) is the real enum
  ``ArtifactSubtype`` (``kind: "enum_declaration"``, ``class_import:
  "org.aspose.pdf.ArtifactSubtype"``) - a real, distinctive, well within-reach entry, this
  pilot's own equivalent of pdf/net's ``AFRelationship``.
- ``REAL_ENUM_FQN``/``REAL_ENUM_MEMBER``: the same ``ArtifactSubtype`` entry's real
  ``class_import`` and one of its 5 real ``enum_members`` (``Header``, ``Footer``,
  ``Watermark``, ``Background``, ``None``) - real, distinctive values a stub or an empty index
  could never produce. Unlike pdf/typescript's reduced fixture, pdf/java's real fixture DOES
  carry an enum-shaped entry within ``max_types=20``, so this file keeps pdf/net's own
  enum-``get_symbol`` shape rather than adapting it to a constant.
- ``REAL_EXAMPLE_SYMBOL``/``REAL_TASK_QUERY`` come from the real, committed
  ``tests/fixtures/furnished/pdf_java/pages/_index.md`` furnished content. A real, hands-on
  ``docker compose up --build`` run of ``ingest-pdf-java`` today reported "verified 1/3
  candidate examples" - of the 3 real candidates in this pilot's real furnished page (Widget
  Annotation, Radio Buttons, Page Dimensions), only "Inspect Document Page Dimensions" actually
  compiles with ``javac`` against the pinned Aspose.PDF-FOSS-for-Java commit (the other two
  reference real methods - e.g. ``RadioButtonField.addOption`` - that this pinned commit's real
  jar does not actually expose with that signature; a real compiler rejection, not a bug in this
  card). Confirmed by hand today, through the real container, by querying the real running
  ``serving-pdf-java`` for each candidate's own topic and inspecting the one real
  ``ExampleMatch`` that ever comes back. ``getMediaBox`` is the real, distinctive method call
  this candidate's real code uses. The literal task query below was CONFIRMED, by that same real
  run, to make ``lookup`` compose a real ``TaskAnswer`` carrying exactly this example.

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
PROJECT_NAME = "foss-mcp-e2e-tc099"
BASE_URL = "http://127.0.0.1:8083"
MCP_PATH = "/mcp"
READYZ_PATH = "/readyz"
# Matches pdf/net's own TC-069 budget: this pilot's ingestion container also does a real git
# clone plus a real `mvn package` build of the reference library, then a real `javac` compile
# per real furnished-content candidate - genuinely slower than a type-only publish.
INGEST_TIMEOUT_SECONDS = 1500
READYZ_TIMEOUT_SECONDS = 180
POLL_INTERVAL_SECONDS = 2

PROTOCOL_VERSION = "2025-06-18"

# TC-099's own real, committed pdf/java fixture (tests/fixtures/pdf_java/api_surface.json): its
# real ``source_commit`` is "db2d3f0622f035825419c6d46727022064f39f15", and its 4th type entry
# (index 3, well within build_chunks_from_api_surface's default max_types=20) is the real enum
# ArtifactSubtype (class_import "org.aspose.pdf.ArtifactSubtype"). A real query response
# containing this exact string is genuine, falsifiable proof of real content, never a guess.
REAL_SYMBOL = "ArtifactSubtype"
REAL_SOURCE_COMMIT = "db2d3f0622f035825419c6d46727022064f39f15"

# REQ-G2-047 (pdf/net's TC-080 pattern): the real enum FQN (``class_import``, not the bare
# ``name``) this pilot's own real fixture carries for org.aspose.pdf.ArtifactSubtype, and one of
# its 5 real enum_members - real, distinctive values a stub or an empty index could never
# produce.
REAL_ENUM_FQN = "org.aspose.pdf.ArtifactSubtype"
REAL_ENUM_MEMBER = "Watermark"

# REQ-G2-048 (TC-091/TC-097 pattern): the real, compile-verified example this card's real
# containerized ingestion run produces - of the 3 real candidates in
# tests/fixtures/furnished/pdf_java's real furnished page, only "Inspect Document Page
# Dimensions" really compiles against the real pinned Aspose.PDF-FOSS-for-Java commit (confirmed
# today by a real, hands-on `docker compose up --build` run of `ingest-pdf-java`: "verified 1/3
# candidate examples", then confirmed again by querying the real running `serving-pdf-java`
# container). ``getMediaBox`` is the real, distinctive method call this candidate's real code
# uses - never a guess at what a container might return.
REAL_EXAMPLE_SYMBOL = "getMediaBox"

# REQ-G2-050 (TC-080/TC-089/TC-098 pattern): the exact literal, API-naive task question
# CONFIRMED live, by hand, today, through the real running pdf/java container, to make
# ``lookup`` compose a real ``TaskAnswer`` carrying the real example above. This exact string is
# required verbatim - the card's own negative control corrupts it, and that corruption must
# break this test's assertions.
REAL_TASK_QUERY = "how do I get the page dimensions of a PDF"

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
            "clientInfo": {"name": "tc-099-e2e-client", "version": "0.0.1"},
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
    """The real, live first publish: build and run the one-shot ``ingest-pdf-java`` service to
    completion, then bring up ``serving-pdf-java`` against the SAME named volume it just
    published into.

    ``docker compose down -v`` first, so this is idempotent against any stale volume state left
    by a prior run. A distinct ``PROJECT_NAME`` (``foss-mcp-e2e-tc099``) and only these two
    services are ever brought up, so this never collides with pdf/net's, pdf/typescript's, or
    any other pilot's own concurrent E2E run.
    """
    _compose("down", "-v", "--remove-orphans")
    try:
        ingest = _compose(
            "up",
            "--build",
            "--exit-code-from",
            "ingest-pdf-java",
            "ingest-pdf-java",
            timeout=INGEST_TIMEOUT_SECONDS,
        )
        assert ingest.returncode == 0, (ingest.stdout + ingest.stderr)[-4000:]

        up = _compose("up", "-d", "--build", "serving-pdf-java")
        assert up.returncode == 0, (up.stdout + up.stderr)[-4000:]
        _wait_for_readyz()
        yield
    finally:
        _compose("down", "-v", "--remove-orphans")


class _McpSession:
    """A real MCP-over-HTTP client session against the real running ``serving-pdf-java``
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
    assert result["indexed_generation_id"].startswith("pdf::java::self_extracted::")
    assert result["stale"] is False, result["reason"]


def test_search_symbols_returns_real_content_from_the_real_fixture(session: _McpSession) -> None:
    """``search_symbols`` for a real enum name from this pilot's real fixture returns real,
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


def test_find_examples_returns_the_real_compile_verified_example(
    session: _McpSession,
) -> None:
    """REQ-G2-048 (TC-091/TC-097 pattern): the concrete answer to a real operator's own worked
    question, proven through the FULL containerized production path - not only at the
    host/test level, but through a real ``docker compose up --build`` of ``ingest-pdf-java``
    (real git clone + a real ``mvn package`` build + a real ``javac`` compile INSIDE the
    container) feeding the same real ``serving-pdf-java`` container every other test in this
    file already queries.

    A task-oriented query ('page dimensions') takes ``find_examples``'s semantic/lexical
    fallback path (this real fixture chunk is deliberately labeled with an
    ``FQN: Example: <title>`` pseudo-FQN, not a real symbol FQN, so no exact-match path would
    fire here) - ranking every real ``Example:``-bearing chunk against the query text and
    returning the one real, compile-verified match.
    """
    body = session.call_tool("find_examples", {"query": "page dimensions"})
    result = body["result"]["structuredContent"]["result"]
    assert isinstance(result, list) and result, f"expected real ExampleMatch(es), got a Miss: {result}"
    assert any(REAL_EXAMPLE_SYMBOL in match["snippet"] for match in result), result


def test_lookup_composes_the_real_task_answer(
    session: _McpSession,
) -> None:
    """REQ-G2-050 (TC-080/TC-089/TC-098 pattern): the developer-context vision's own concrete
    target, now proven for pdf/java too - not only the supervisor's repeated manual, ephemeral
    hand-verification.

    The real ``lookup`` tool (never ``find_examples`` directly - a caller with no prior API
    knowledge asking a real, API-naive task question) with the EXACT literal query CONFIRMED
    live, by hand, today: through the real running pdf/java container, this reads as a task
    question (``_looks_like_a_task_question``), so ``lookup`` composes a real ``TaskAnswer`` -
    ``doc_matches`` (empty here, since no getting_started/developer_guide/troubleshooting/faq
    content has ever been published for this pilot) plus the same real, compile-verified example
    ``find_examples`` itself proves above, carrying the real source commit
    ``infra/build_chunks.py`` appends to every real chunk's text.
    """
    body = session.call_tool("lookup", {"query": REAL_TASK_QUERY})
    result = body["result"]["structuredContent"]["result"]
    assert isinstance(result, dict), f"expected a composed TaskAnswer, got: {result}"
    assert "doc_matches" in result and "example" in result, result
    assert result["example"] is not None, f"expected a real, verified example, got none: {result}"
    snippet = result["example"]["snippet"]
    assert REAL_EXAMPLE_SYMBOL in snippet, snippet
    assert REAL_SOURCE_COMMIT in snippet, snippet


def test_get_symbol_returns_the_real_enum_members_for_artifact_subtype(session: _McpSession) -> None:
    """REQ-G2-047's own live proof, now proven for pdf/java too: the real running pdf/java
    container's ``get_symbol`` for the real ``org.aspose.pdf.ArtifactSubtype`` enum (this real
    fixture's 4th entry, well within ``build_chunks_from_api_surface``'s default
    ``max_types=20``) returns its real 5 enum members - not a stub, not an empty index.
    """
    body = session.call_tool("get_symbol", {"fqn": REAL_ENUM_FQN})
    result = body["result"]["structuredContent"]["result"]
    assert isinstance(result, dict) and "members" in result, f"expected a real SymbolSignature: {result}"
    members = result["members"]
    assert isinstance(members, list) and members, f"expected real enum members, got none: {result}"
    assert any(member.startswith(REAL_ENUM_MEMBER) for member in members), members
