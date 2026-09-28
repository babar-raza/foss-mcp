"""G2/TC-100: pdf/go's real, live first publish, proven end to end through real
docker-compose services - not a test's in-memory store, not code-reading.

Mirrors ``tests/e2e/test_pdf_net_live_content.py`` (TC-064/069/080/089),
``tests/e2e/test_pdf_typescript_live_content.py`` (TC-098), and
``tests/e2e/test_pdf_java_live_content.py`` (TC-099) exactly: bring up the new one-shot
``ingest-pdf-go`` service (which really publishes a generation into the real, named
``manifests-pdf-go`` volume via ``infra/build_chunks.py`` + ``infra/ingest.py`` - the latter's
``--library-platform go`` dispatch, TC-091, driving a real ``go build ./...`` of the pinned
reference module inside the ingestion container), then bring up ``serving-pdf-go`` (which mounts
that SAME volume), poll the real ``/readyz`` until ready, then run a real MCP-over-HTTP session
and assert real content comes back.

Every value below is this pilot's OWN real, distinctive data - never copied from pdf/net's,
pdf/typescript's, or pdf/java's:

- ``REAL_SYMBOL``/``REAL_SOURCE_COMMIT`` come from the real, committed
  ``tests/fixtures/pdf_go/api_surface.json`` fixture. Its real ``source_commit`` is
  ``286484d235196d65c9a458c5eff3d3d6539216dc``, and its 17th type entry (index 16, well within
  ``build_chunks_from_api_surface``'s default ``max_types=20``) is the real ``BarcodeField``
  type_spec (``bases: ["TextBoxField"]``, 12 real methods including ``SetSymbology``) - a real,
  distinctive, well within-reach entry.
- No enum-shaped entry exists anywhere within this fixture's own ``max_types=20`` slice
  (confirmed by inspecting every one of the first 20 entries' own ``kind`` - only ``type_spec``
  and ``function`` appear; several ``type_spec`` entries carry ``bases: ["int"]``, Go's idiom
  for an enum-like type, but the raw extraction fixture never emits a separate ``enum_members``
  list for them the way pdf/java's fixture does for ``ArtifactSubtype`` - confirmed by reading
  every one of the first 20 raw entries directly). This file therefore keeps pdf/typescript's own
  adaptation rather than pdf/net's/pdf/java's enum-``get_symbol`` shape: there is no
  ``REAL_ENUM_FQN``/``REAL_ENUM_MEMBER`` pair for this pilot; the ``get_symbol`` test below
  instead asserts the real ``BarcodeField`` type_spec's own ``Bases:``/``Methods:`` lines.
- ``REAL_EXAMPLE_SYMBOL``/``REAL_TASK_QUERY``: TC-100's own worker found a REAL, REPRODUCIBLE GAP
  here - at the time, a real, hands-on run of the full ``infra/build_chunks.py`` CLI against this
  pilot's own real, committed furnished page (``tests/fixtures/furnished/pdf_go/pages/_index.md``)
  showed **0 of its 4 real candidates compile-verifying** ("verified 0/4 candidate examples"),
  because every one of the 4 real code fences in that page is a bare Go statement fragment (e.g.
  ``doc, _ := pdf.Open("input.pdf")`` ... with no ``package``/``import``/``func main`` of its
  own) and ``verify_go_example`` (``src/foss_mcp/indexing/example_verifier.py``) had no
  fragment-wrapping step analogous to ``verify_java_example``'s.

  TC-104 fixed this for real: ``verify_go_example`` now wraps a bare fragment (synthetic
  ``package``/``import``/``func main``) before compiling it, exactly as ``verify_java_example``
  already did for Java. TC-106 (this card) confirmed the fix live, hands-on, through the FULL
  containerized ``docker compose up --build ingest-pdf-go`` path below, today: the real ingestion
  log now reads **"verified 4/4 candidate examples"** - all 4 of pdf/go's real furnished
  candidates, including the "Split and Merge PDFs" one, now genuinely compile-verify.

  Consequently, for this pilot, ``find_examples``/``lookup`` now DO return real, compile-verified
  content, confirmed by a real MCP-over-HTTP query against the real running ``serving-pdf-go``
  container below (never assumed - the exact response was observed first-hand before either test
  was rewritten). ``REAL_EXAMPLE_SYMBOL`` is ``doc.Split()`` - the real, distinctive method call
  in the furnished page's "Split and Merge PDFs" candidate, confirmed present verbatim in the real
  ``find_examples``/``lookup`` snippet text returned by the real running container.
  ``REAL_TASK_QUERY`` ("how do I split and merge PDF documents") is the exact literal, API-naive
  task question CONFIRMED live, by hand, today, through the real running pdf/go container, to make
  ``lookup`` correctly compose a real ``TaskAnswer`` - ``doc_matches`` empty (no doc content has
  ever been published for this pilot), ``example`` carrying the real, compile-verified "Split and
  Merge PDFs" snippet, with the real source commit ``286484d235196d65c9a458c5eff3d3d6539216dc`` -
  never a fabricated one, and never the honest-but-incomplete ``Miss`` this file asserted before
  TC-104's fix. REQ-G2-050's "a real, composed lookup task-answer returns a real, genuinely
  verified example" bar is now fully met for pdf/go, matching every other pilot's own complete
  bar.

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
PROJECT_NAME = "foss-mcp-e2e-tc100"
BASE_URL = "http://127.0.0.1:8084"
MCP_PATH = "/mcp"
READYZ_PATH = "/readyz"
# Matches pdf/net's own TC-069 budget: this pilot's ingestion container also does a real git
# clone plus a real `go build ./...` of the reference module, then a real `go build ./...`
# compile attempt per real furnished-content candidate - budgeted the same as the other
# pilots even though a Go build is typically faster than npm/mvn.
INGEST_TIMEOUT_SECONDS = 1500
READYZ_TIMEOUT_SECONDS = 180
POLL_INTERVAL_SECONDS = 2

PROTOCOL_VERSION = "2025-06-18"

# TC-100's own real, committed pdf/go fixture (tests/fixtures/pdf_go/api_surface.json): its real
# ``source_commit`` is "286484d235196d65c9a458c5eff3d3d6539216dc", and its 17th type entry (index
# 16, well within build_chunks_from_api_surface's default max_types=20) is the real type_spec
# BarcodeField (bases: ["TextBoxField"]). A real query response containing this exact string is
# genuine, falsifiable proof of real content, never a guess.
REAL_SYMBOL = "BarcodeField"
REAL_SOURCE_COMMIT = "286484d235196d65c9a458c5eff3d3d6539216dc"

# No enum-shaped entry exists anywhere within this fixture's own max_types=20 slice (confirmed by
# inspecting every entry's own "kind" - only "type_spec" and "function" appear, and no "type_spec"
# entry carries a separate "enum_members" list even when its "bases" is ["int"]); BarcodeField's
# own real Bases:/Methods: lines are asserted directly in get_symbol's test below instead.
REAL_BASE_FRAGMENT = "Bases:\n  - TextBoxField"
REAL_METHOD_FRAGMENT = "SetSymbology(s: BarcodeSymbology) -> error"

# REQ-G2-048 (TC-091/TC-097/TC-104/TC-106 pattern): TC-104 fixed verify_go_example
# (src/foss_mcp/indexing/example_verifier.py) to wrap a bare Go statement fragment (synthetic
# package/import/func main) before compiling it, exactly as verify_java_example already did for
# Java. TC-106 confirmed live, hands-on, through a real, full containerized
# `docker compose up --build ingest-pdf-go` run: the real ingestion log now reads "verified 4/4
# candidate examples" for this pilot's real furnished page
# (tests/fixtures/furnished/pdf_go/pages/_index.md) - all 4 real candidates, up from 0/4 before
# TC-104. ``doc.Split()`` is the real, distinctive method call in the "Split and Merge PDFs"
# candidate, confirmed present verbatim in the real find_examples/lookup snippet text returned by
# the real running serving-pdf-go container (observed first-hand before this file was rewritten,
# never assumed).
REAL_EXAMPLE_SYMBOL = "doc.Split()"

# REQ-G2-050 (TC-080/TC-089/TC-098/TC-099/TC-106 pattern): the exact literal, API-naive task
# question matching REAL_EXAMPLE_SYMBOL's own real topic. CONFIRMED live, by hand, today, through
# the real running pdf/go container: because pdf/go now has a real, compile-verified example for
# this topic (see REAL_EXAMPLE_SYMBOL above), `lookup` composes a real TaskAnswer - empty
# doc_matches (no doc content has ever been published for this pilot) plus the real,
# compile-verified "Split and Merge PDFs" example, carrying the real source commit
# REAL_SOURCE_COMMIT - rather than falling through to a bare Miss. This exact string is required
# verbatim - the card's own negative control corrupts it, and that corruption must break this
# test's assertions.
REAL_TASK_QUERY = "how do I split and merge PDF documents"

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
            "clientInfo": {"name": "tc-100-e2e-client", "version": "0.0.1"},
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
    """The real, live first publish: build and run the one-shot ``ingest-pdf-go`` service to
    completion, then bring up ``serving-pdf-go`` against the SAME named volume it just published
    into.

    ``docker compose down -v`` first, so this is idempotent against any stale volume state left
    by a prior run. A distinct ``PROJECT_NAME`` (``foss-mcp-e2e-tc100``) and only these two
    services are ever brought up, so this never collides with pdf/net's, pdf/typescript's,
    pdf/java's, or any other pilot's own concurrent E2E run.
    """
    _compose("down", "-v", "--remove-orphans")
    try:
        ingest = _compose(
            "up",
            "--build",
            "--exit-code-from",
            "ingest-pdf-go",
            "ingest-pdf-go",
            timeout=INGEST_TIMEOUT_SECONDS,
        )
        assert ingest.returncode == 0, (ingest.stdout + ingest.stderr)[-4000:]

        up = _compose("up", "-d", "--build", "serving-pdf-go")
        assert up.returncode == 0, (up.stdout + up.stderr)[-4000:]
        _wait_for_readyz()
        yield
    finally:
        _compose("down", "-v", "--remove-orphans")


class _McpSession:
    """A real MCP-over-HTTP client session against the real running ``serving-pdf-go``
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
    assert result["indexed_generation_id"].startswith("pdf::go::self_extracted::")
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


def test_find_examples_returns_the_real_compile_verified_split_and_merge_example(
    session: _McpSession,
) -> None:
    """REQ-G2-048 (TC-104/TC-106), proven positively for pdf/go for the first time: now that
    ``verify_go_example`` wraps a bare Go statement fragment before compiling it (TC-104's real
    fix), the real running ``serving-pdf-go`` container's ``find_examples`` returns a real,
    non-empty list of compile-verified snippets for this real, on-topic query - including the
    real "Split and Merge PDFs" candidate, whose real, distinctive ``doc.Split()`` method call
    this test asserts verbatim, confirmed present in the real response observed first-hand before
    this test was written (never assumed).
    """
    body = session.call_tool("find_examples", {"query": "split and merge pdf documents"})
    result = body["result"]["structuredContent"]["result"]
    assert isinstance(result, list) and result, f"expected real ExampleMatch(es), got a Miss: {result}"
    assert any(REAL_EXAMPLE_SYMBOL in match["snippet"] for match in result), result


def test_lookup_composes_the_real_task_answer_for_the_split_and_merge_question(
    session: _McpSession,
) -> None:
    """REQ-G2-050 (TC-080/TC-089/TC-098/TC-099/TC-106 pattern), now fully met for pdf/go: the
    real ``lookup`` tool (never ``find_examples`` directly - a caller with no prior API knowledge
    asking a real, API-naive task question) with the EXACT literal query CONFIRMED live, by hand,
    today, through the real running pdf/go container: this reads as a task question
    (``_looks_like_a_task_question``), and now that pdf/go has a real, compile-verified example
    (TC-104's fix), ``lookup`` composes a real ``TaskAnswer`` - ``doc_matches`` (empty here, since
    no getting_started/developer_guide/troubleshooting/faq content has ever been published for
    this pilot) plus the same real, compile-verified "Split and Merge PDFs" example
    ``find_examples`` itself proves above, carrying the real source commit
    ``infra/build_chunks.py`` appends to every real chunk's text - never the honest-but-incomplete
    ``Miss`` this file asserted before TC-104's fix.
    """
    body = session.call_tool("lookup", {"query": REAL_TASK_QUERY})
    result = body["result"]["structuredContent"]["result"]
    assert isinstance(result, dict), f"expected a composed TaskAnswer, got: {result}"
    assert "doc_matches" in result and "example" in result, result
    assert result["example"] is not None, f"expected a real, verified example, got none: {result}"
    snippet = result["example"]["snippet"]
    assert REAL_EXAMPLE_SYMBOL in snippet, snippet
    assert REAL_SOURCE_COMMIT in snippet, snippet


def test_get_symbol_returns_the_real_type_spec_for_barcode_field(session: _McpSession) -> None:
    """This pilot's own equivalent of pdf/net's/pdf/java's enum-``get_symbol`` proof, adapted to
    the real entry shape this fixture actually has: no enum-shaped entry exists anywhere within
    ``max_types=20`` of this real fixture (confirmed by inspecting every entry's own ``kind`` -
    see module docstring), so this asserts the real running pdf/go container's ``get_symbol`` for
    the real ``BarcodeField`` type_spec returns its real, distinctive ``Bases:``/``Methods:``
    lines - not a stub, not an empty index.
    """
    body = session.call_tool("get_symbol", {"fqn": REAL_SYMBOL})
    result = body["result"]["structuredContent"]["result"]
    assert isinstance(result, dict) and "raw_text" in result, f"expected a real SymbolSignature: {result}"
    assert result["kind"] == "type_spec", result
    assert REAL_BASE_FRAGMENT in result["raw_text"], result
    assert REAL_METHOD_FRAGMENT in result["raw_text"], result
