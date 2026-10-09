"""G2/TC-064: pdf/net's real, live first publish, proven end to end through real
docker-compose services - not a test's in-memory store, not code-reading.

The 2026-09-25 audit found, by hand, that the real running ``serving`` container answered
every content tool call with "no published generation for this scope": ``docker-compose.yml``
mounted no volume for ``/data/manifests`` and nothing in production ever invoked the
extraction-to-publish pipeline (``infra/ingest.py``). TC-060 (citation validation), TC-061
(a real ``/readyz``), TC-062 (``build_chunks_from_api_surface``) and TC-063
(``HashingEmbeddingProvider``) built the missing pieces; this file is the one place they are
actually wired together and run for real: ``docker compose up --build`` the new one-shot
``ingest-pdf-net`` service (which really publishes a generation into the real, named
``manifests-pdf-net`` volume via ``infra/build_chunks.py`` + ``infra/ingest.py``), then bring up
``serving`` (which mounts that SAME volume), poll the real ``/readyz`` until ready, then run a
real MCP-over-HTTP session and assert real content comes back.

Mirrors ``tests/e2e/test_container_session.py``'s established real-docker-compose pattern
(compose helpers, an SSE-decoding MCP session, teardown even on failure) - never reinvented.

network: true on this card, for exactly this reason - everything here talks to containers this
file itself builds, starts and tears down.

TC-280: ``REAL_SOURCE_COMMIT`` (the committed ``tests/fixtures/pdf_net/api_surface.json``
fixture's own ``source_commit``) and ``REAL_EXAMPLE_SOURCE_COMMIT`` (``docker-compose.yml``'s
``--library-commit`` pin for ``ingest-pdf-net``, used only to compile-verify the furnished
watermark example) are two genuinely separate pins that happened to share one literal value
before TC-263 regenerated the api_surface fixture with the fixed, centrality-ranked selection -
they must never be assumed equal again. Mirrors
``tests/e2e/test_pdf_typescript_live_content.py``'s identical split for the identical reason.
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
PROJECT_NAME = "foss-mcp-e2e-tc064"
BASE_URL = "http://127.0.0.1:8080"
MCP_PATH = "/mcp"
READYZ_PATH = "/readyz"
# REQ-G2-048 (TC-069) widened this: the ingest-pdf-net image now bakes in a real .NET 8 SDK
# download/install, and the container itself now does a real git clone plus real `dotnet
# build`s (the reference library once, then one per real furnished-content candidate) -
# genuinely slower than the pre-TC-069 type-only publish this budget originally covered.
INGEST_TIMEOUT_SECONDS = 1500
READYZ_TIMEOUT_SECONDS = 180
POLL_INTERVAL_SECONDS = 2

PROTOCOL_VERSION = "2025-06-18"

# TC-280: TC-263 regenerated this exact fixture (tests/fixtures/pdf_net/api_surface.json) with
# the fixed, centrality-ranked selection (899->971 real upstream types, 300 kept) - the
# AFRelationship-anchored values this file originally carried (TC-011-era) went stale the moment
# that regeneration landed, because AFRelationship does not survive the centrality-ranked cut at
# all (confirmed directly against the committed fixture: absent from all 300 kept entries, not
# merely renamed or moved). Re-verified directly against the currently committed fixture: real
# source_commit "10a363830fea952c29df20fdb37d35ad3a881433", 971 real upstream types, 300 kept.
# "Page" is a real, sealed class (class_import "Aspose.Pdf.Page", kind "class_declaration") that
# genuinely survives the cut (confirmed live - it is this fixture's own #1-centrality type per
# TC-263's report) - its bare name is a whole word of its own FQN's final segment, so
# is_exact_symbol_hit (search_symbols.py) matches it unambiguously, and "Page" literally appears
# in its own chunk's FQN line, so it is genuine, falsifiable proof of real content, never a guess.
REAL_SYMBOL = "Page"
REAL_SOURCE_COMMIT = "10a363830fea952c29df20fdb37d35ad3a881433"

# REQ-G2-048 (TC-069): the real, compile-verified example this card's real containerized
# ingestion run produces - of the 3 real candidates in tests/fixtures/furnished/pdf_net's
# real furnished page, only "Add a Watermark Annotation" really compiles against the real
# pinned Aspose.PDF-FOSS-for-.NET commit (confirmed by hand today, at the host level, by
# TC-067/TC-068). ``AddWatermarkAnnotation`` is the real, distinctive method call this
# candidate's real code uses - never a guess at what a container might return.
REAL_EXAMPLE_SYMBOL = "AddWatermarkAnnotation"

# TC-280: the furnished example's own compile-verification commit is a SEPARATE pin from the
# api_surface fixture's source_commit above: docker-compose.yml's ingest-pdf-net service passes
# this exact value as its own ``--library-commit`` flag (never re-derived from the live
# extraction), so it stays "b717287..." even though TC-263 regenerated
# tests/fixtures/pdf_net/api_surface.json (and therefore REAL_SOURCE_COMMIT above) to a newer
# value. Confirmed live today (the previous, single-constant version of this file genuinely
# failed test_lookup_composes_the_real_task_answer_for_the_watermark_question against the real
# running container once REAL_SOURCE_COMMIT above was updated, because the example chunk's own
# real ``Source-Commit:`` footer is still this value, not the newer one) - mirrors
# ``tests/e2e/test_pdf_typescript_live_content.py``'s identical
# ``REAL_EXAMPLE_SOURCE_COMMIT``/``REAL_SOURCE_COMMIT`` split for the exact same reason.
REAL_EXAMPLE_SOURCE_COMMIT = "b7172877651413cff57a8bfe41fb8a8befb2406b"

# REQ-G2-050 (TC-080): the exact literal, API-naive task question CONFIRMED live, by hand,
# multiple times today, to make ``lookup`` compose a real ``TaskAnswer`` carrying the real
# AddWatermarkAnnotation example above. This exact string is required verbatim - the card's
# own negative control corrupts it, and that corruption must break this test's assertions.
# TC-089: this literal is load-bearing for a genuinely falsifying negative control that
# replaces the ENTIRE query with unrelated nonsense (not merely appends noise to it), so the
# real matching keywords ("watermark", "PDF") are actually removed and the lookup assertions
# below actually break.
REAL_TASK_QUERY = "how do I add a watermark to a PDF"

# REQ-G2-047 (TC-080 companion), re-anchored by TC-280: AFRelationship does not survive
# TC-263's centrality-ranked cut (confirmed absent from the 300 kept types in the currently
# committed fixture), so this now uses the real enum FQN (``class_import``, not the bare
# ``name``) the fixture carries for Aspose.Pdf.HorizontalAlignment, and one of its 6 real
# enum_members - real, distinctive values a stub or an empty index could never produce.
REAL_ENUM_FQN = "Aspose.Pdf.HorizontalAlignment"
REAL_ENUM_MEMBER = "FullJustify"

# REQ-G2-047 (TC-113): real doc content, observed live through this exact container by hand on
# 2026-09-29, now genuinely published and served - TC-119's regenerated furnished-content page
# for pdf/net carries a "Scope and Limitations" content block (extract_doc_sections' "content"
# origin), and search_docs's own classify_content_type buckets its "These limitations don't
# apply to ... Enterprise Edition ..." / "... open-source subset of Aspose.PDF for .NET ..."
# chunks as "developer_guide" (its own default: neither chunk's own text happens to contain any
# of "getting started"/"quickstart"/"installation"/"prerequisites"). Confirmed live: querying
# content_type="developer_guide" for "limitations" returns exactly these 2 real "FQN: Doc: Scope
# and Limitations" chunks - never a guess at what a container might return.
REAL_DOC_QUERY = "limitations"
REAL_DOC_CONTENT_TYPE = "developer_guide"
REAL_DOC_FRAGMENT = "open-source subset of Aspose.PDF for .NET"

# REQ-G2-047 (TC-113): the real, API-naive task question CONFIRMED live, by hand, on 2026-09-29,
# to make lookup's own doc-fallback path (_compose_from_docs) surface the SAME real "Scope and
# Limitations" chunks above as doc_matches - never [] as every prior comprehensive live
# verification pass found for every pilot, for every query, before TC-109 through TC-112 (and
# this card) closed the gap. This query happens to match no verified example, so lookup's
# TaskAnswer carries a real, non-empty doc_matches with example=None - a doc match found on its
# own, exactly as lookup.py's own module docstring says a TaskAnswer may legitimately look.
REAL_DOC_TASK_QUERY = "what are the known limitations of this library"

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
            "clientInfo": {"name": "tc-064-e2e-client", "version": "0.0.1"},
        },
    }


def _sse_json(text: str) -> dict:
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:") :].strip())
    raise AssertionError(f"no SSE data line found in response: {text!r}")


def _wait_for_readyz() -> None:
    """Poll the real published ``/readyz`` route (TC-061) until it reports ready - a bounded
    retry, never an unbounded loop, and never a fixed sleep: this is exactly what /readyz was
    built for, and this is its one real (non-unit-test) exercise.
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
    """The real, live first publish: build and run the one-shot ``ingest-pdf-net`` service to
    completion (this IS the real ingestion the 2026-09-25 audit found never ran anywhere), then
    bring up ``serving`` against the SAME named volume it just published into.

    ``docker compose down -v`` first, per the card's own recovery note, so this is idempotent
    against any stale volume state left by a prior run.
    """
    _compose("down", "-v", "--remove-orphans")
    try:
        ingest = _compose(
            "up",
            "--build",
            "--exit-code-from",
            "ingest-pdf-net",
            "ingest-pdf-net",
            timeout=INGEST_TIMEOUT_SECONDS,
        )
        assert ingest.returncode == 0, (ingest.stdout + ingest.stderr)[-4000:]

        up = _compose("up", "-d", "--build", "serving")
        assert up.returncode == 0, (up.stdout + up.stderr)[-4000:]
        _wait_for_readyz()
        yield
    finally:
        _compose("down", "-v", "--remove-orphans")


class _McpSession:
    """A real MCP-over-HTTP client session against the real running ``serving`` container:
    initialize, capture the session id, acknowledge, then issue whatever the test needs.
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
    """The one real (non-unit-test) exercise of TC-061's ``/readyz``: 200, because a real
    active generation now exists for this deployment's own scope - never process reachability.
    """
    response = httpx.get(f"{BASE_URL}{READYZ_PATH}", timeout=5)
    assert response.status_code == 200
    assert response.text == "ready"


def test_report_index_freshness_reports_the_real_fresh_generation(session: _McpSession) -> None:
    """A real generation id, and ``stale=False`` once the real indexed commit (recorded via the
    ``Source-Commit:`` line ``infra/build_chunks.py`` appends to each chunk's text) is compared
    against the real fixture's own real commit sha - never the pre-ingestion answer
    ("never published"/``stale=True``) every prior audit-era container gave.
    """
    body = session.call_tool("report_index_freshness", {"current_source_commit": REAL_SOURCE_COMMIT})
    result = body["result"]["structuredContent"]["result"]
    assert result["indexed_generation_id"] is not None
    assert result["indexed_generation_id"].startswith("pdf::net::self_extracted::")
    assert result["stale"] is False, result["reason"]


def test_search_symbols_returns_real_content_from_the_real_fixture(session: _McpSession) -> None:
    """``search_symbols`` for a real class name (``Page``, TC-280's live-verified replacement
    for the no-longer-surviving ``AFRelationship``) from the real, currently committed,
    centrality-ranked fixture returns real, non-empty content - never the "no published
    generation for this scope" Miss every content tool call gave before this card wired
    ingestion into anything that runs.
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


def test_find_examples_returns_the_real_compile_verified_watermark_example(
    session: _McpSession,
) -> None:
    """REQ-G2-048 (TC-069): the concrete answer to the operator's own worked question ("how
    do I add a watermark to a PDF"), proven through the FULL containerized production path -
    not only at the host/test level (TC-068) but through a real ``docker compose up --build``
    of ``ingest-pdf-net`` (real git clone + real ``dotnet build`` INSIDE the container) feeding
    the same real ``serving`` container every other test in this file already queries.

    A task-oriented query ('watermark') takes ``find_examples``'s semantic/lexical fallback
    path (TC-068's own real fixture chunk is deliberately labeled ``FQN: Example: Add a
    Watermark Annotation``, not a real symbol FQN, so no exact-match path would fire here) -
    ranking every real ``Example:``-bearing chunk against the query text and returning the one
    real, compile-verified match.
    """
    body = session.call_tool("find_examples", {"query": "watermark"})
    result = body["result"]["structuredContent"]["result"]
    assert isinstance(result, list) and result, f"expected real ExampleMatch(es), got a Miss: {result}"
    assert any(REAL_EXAMPLE_SYMBOL in match["snippet"] for match in result), result


def test_lookup_composes_the_real_task_answer_for_the_watermark_question(
    session: _McpSession,
) -> None:
    """REQ-G2-050 (TC-080): the developer-context vision's own concrete target, now a PERMANENT,
    automated test - not only the supervisor's repeated manual, ephemeral hand-verification.

    The real ``lookup`` tool (never ``find_examples`` directly - a caller with no prior API
    knowledge asking a real, API-naive task question) with the EXACT literal query CONFIRMED
    live, by hand, multiple times today: through the real running pdf/net container, this reads
    as a task question (``_looks_like_a_task_question``), so ``lookup`` composes a real
    ``TaskAnswer`` - ``doc_matches`` (empty here, since no getting_started/developer_guide/
    troubleshooting/faq content has ever been published for this pilot) plus the same real,
    compile-verified ``AddWatermarkAnnotation`` example ``find_examples`` itself proves above,
    carrying the furnished example's own pinned compile-verification commit
    (``REAL_EXAMPLE_SOURCE_COMMIT`` - docker-compose.yml's own ``--library-commit``, a separate
    pin from the api_surface fixture's ``REAL_SOURCE_COMMIT``) that ``infra/build_chunks.py``
    appends to every real chunk's text.
    """
    body = session.call_tool("lookup", {"query": REAL_TASK_QUERY})
    result = body["result"]["structuredContent"]["result"]
    assert isinstance(result, dict), f"expected a composed TaskAnswer, got: {result}"
    assert "doc_matches" in result and "example" in result, result
    assert result["example"] is not None, f"expected a real, verified example, got none: {result}"
    snippet = result["example"]["snippet"]
    assert REAL_EXAMPLE_SYMBOL in snippet, snippet
    assert REAL_EXAMPLE_SOURCE_COMMIT in snippet, snippet


def test_get_symbol_returns_the_real_enum_members_for_horizontalalignment(session: _McpSession) -> None:
    """REQ-G2-047's own live proof, now a permanent artifact alongside REQ-G2-050's: the real
    running pdf/net container's ``get_symbol`` for the real ``Aspose.Pdf.HorizontalAlignment``
    enum (TC-280's live-verified replacement for ``Aspose.Pdf.AFRelationship``, which does not
    survive TC-263's centrality-ranked cut at all) returns its real 6 enum members - not a stub,
    not an empty index.
    """
    body = session.call_tool("get_symbol", {"fqn": REAL_ENUM_FQN})
    result = body["result"]["structuredContent"]["result"]
    assert isinstance(result, dict) and "members" in result, f"expected a real SymbolSignature: {result}"
    members = result["members"]
    assert isinstance(members, list) and members, f"expected real enum members, got none: {result}"
    assert any(member.startswith(REAL_ENUM_MEMBER) for member in members), members


# ---------------------------------------------------------------------
# REQ-G2-047 (TC-113): pdf/net's own real documentation content (TC-112's real
# _build_doc_chunks, reachable via TC-109's search_docs routing fix, no longer confused for a
# real symbol by search_symbols since TC-120) genuinely served through the FULL containerized
# production path - not merely replayed offline by TC-112's own unit-level check.
# ---------------------------------------------------------------------


def test_search_docs_returns_real_furnished_content_for_pdf_net(session: _McpSession) -> None:
    """``search_docs`` with an explicit ``content_type`` for a real, distinctive query returns
    real, non-empty documentation content from pdf/net's own real, regenerated furnished page
    (TC-119) - never the "no published generation for this scope"/empty-index Miss every content
    tool call gave before TC-112 wired real doc chunks into ingestion.

    ``developer_guide`` is deliberately used, not ``getting_started``: it is
    ``classify_content_type``'s own default bucket, and is the category this real furnished
    page's "Scope and Limitations" content block genuinely lands in (confirmed live - neither of
    its two real chunks' own text happens to contain a ``getting_started``/``troubleshooting``/
    ``faq`` hint word).
    """
    body = session.call_tool("search_docs", {"query": REAL_DOC_QUERY, "content_type": REAL_DOC_CONTENT_TYPE})
    result = body["result"]["structuredContent"]["result"]
    assert isinstance(result, list) and result, f"expected real doc matches, got a Miss: {result}"
    assert all(match["content_type"] == REAL_DOC_CONTENT_TYPE for match in result), result
    assert any(REAL_DOC_FRAGMENT in match["text"] for match in result), result
    assert any(match["text"].startswith("FQN: Doc: ") for match in result), result


def test_lookup_returns_real_doc_matches_for_an_api_naive_query(session: _McpSession) -> None:
    """The concrete, final proof the whole pipeline (TC-109 through TC-112 plus this card)
    closes the real gap end to end, through a real MCP client's own eyes: ``lookup``'s
    doc-fallback path (``_compose_from_docs``) now genuinely composes a ``TaskAnswer`` whose
    ``doc_matches`` is non-empty for a real, API-naive query - previously ``()`` for every pilot,
    for every query, confirmed by this project's own earlier comprehensive live verification
    pass (see ``lookup.py``'s own module docstring, REQ-G2-049).

    This real query happens to match no verified example (pdf/net's only verified example is the
    unrelated watermark annotation), so this is also a real, live instance of a ``TaskAnswer``
    composed from a doc match alone, with ``example`` genuinely ``None`` - never fabricated.
    """
    body = session.call_tool("lookup", {"query": REAL_DOC_TASK_QUERY})
    result = body["result"]["structuredContent"]["result"]
    assert isinstance(result, dict), f"expected a composed TaskAnswer, got: {result}"
    assert "doc_matches" in result and "example" in result, result
    doc_matches = result["doc_matches"]
    assert isinstance(doc_matches, list) and doc_matches, f"expected real doc_matches, got none: {result}"
    assert any(REAL_DOC_FRAGMENT in match["text"] for match in doc_matches), doc_matches
