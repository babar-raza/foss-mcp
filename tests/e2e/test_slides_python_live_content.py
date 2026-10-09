"""G2/TC-101: slides/python's real, live first publish, proven end to end through real
docker-compose services - not a test's in-memory store, not code-reading.

Mirrors ``tests/e2e/test_pdf_net_live_content.py`` (TC-064/069/080/089),
``tests/e2e/test_pdf_typescript_live_content.py`` (TC-098),
``tests/e2e/test_pdf_java_live_content.py`` (TC-099), and
``tests/e2e/test_pdf_go_live_content.py`` (TC-100) exactly: bring up the new one-shot
``ingest-slides-python`` service (which really publishes a generation into the real, named
``manifests-slides-python`` volume via ``infra/build_chunks.py`` + ``infra/ingest.py`` - the
latter's ``--library-platform python`` dispatch, TC-091, driving a real ``git clone`` of the
pinned reference library, a real venv, a real ``pip install -e``, and a real interpreter run of
every candidate example inside the ingestion container), then bring up ``serving-slides-python``
(which mounts that SAME volume), poll the real ``/readyz`` until ready, then run a real
MCP-over-HTTP session and assert real content comes back.

Every value below is this pilot's OWN real, distinctive data - never copied from pdf/net's,
pdf/typescript's, pdf/java's, or pdf/go's:

- ``REAL_SYMBOL``/``REAL_SOURCE_COMMIT`` come from the real, committed
  ``tests/fixtures/slides_python/api_surface.json`` fixture. Its real ``source_commit`` is
  ``4e63447ba79d1c27a5192844847d9f872c5b92ad`` (unchanged across TC-264/TC-277 - same pinned
  commit, only the kept selection changed). G2/TC-277 regenerated this fixture for real with
  TC-252's centrality-ranked ``reduce_fixture()`` now that TC-265 makes every Python-sourced
  entry's ``bases``/``return_type``/``param_types`` real - previously every Python type scored
  zero centrality, a complete no-op (TC-264 found this directly). ``infra/build_chunks.py``
  publishes this pilot with ``--max-types 300`` (docker-compose.yml) - the WHOLE kept fixture, not
  some smaller slice of it - so every one of this fixture's own 300 kept entries is genuinely
  reachable through this live container, not only an early slice of it.

  Its raw ``types`` list still carries TWO real entries per class (one facade entry read from
  ``aspose/slides_foss/__init__.py`` with an empty ``methods`` list, one from the class's own
  module with the real method list) - confirmed by reading this fixture's own committed content
  directly. The OLD anchor, ``AutoShape``, does not survive this real centrality cut (confirmed
  directly: its centrality score is 0, because this library types almost everything against its
  own interfaces - ``IAutoShape``, ``IShape``, etc. - rather than the concrete class, so nothing
  else's real ``bases``/``return_type``/``params`` textually references the bare word
  "AutoShape" anywhere in the real corpus; the same real reason "Presentation" itself - the
  symbol this whole multi-card effort originally chased - legitimately never survives the cut
  either). The real replacement, ``Shape``/``aspose.slides_foss.Shape.Shape``, DOES survive
  (confirmed directly against this fixture's own committed content, at index 136 of 300): its
  real definition entry carries 27 real methods and, unlike every previous anchor this file has
  used, a real non-empty ``Bases:`` list too (``StrictAttributes``, ``IShape``,
  ``ISlideComponent``, ``IPresentationComponent``, ``IHyperlinkContainer``, ``ABC``) - TC-265's
  fix reaching this live container for real, not just the offline fixture test.
- No enum entry anywhere in this real, regenerated fixture carries any real members (confirmed by
  reading every one of its 24 real enum entries' own ``enum_members`` directly - the pure-``ast``
  Python reader was never asked to extract member values, unchanged by TC-265). This file
  therefore keeps pdf/typescript's/pdf/go's own adaptation rather than pdf/net's/pdf/java's
  enum-``get_symbol`` shape: there is no ``REAL_ENUM_FQN``/``REAL_ENUM_MEMBER`` pair for this
  pilot; the ``get_symbol`` test below instead asserts the real ``Shape`` class_spec's own
  ``Methods:`` line AND - for the first time for this pilot - its real ``Bases:`` line too.
- ``REAL_EXAMPLE_SYMBOL``/``REAL_TASK_QUERY``: TC-101's own worker found a real, reproducible gap
  here, confirmed both by a real, hands-on host-level run of the full ``infra/build_chunks.py``
  CLI and independently reproduced through the full containerized ``docker compose up --build
  ingest-slides-python`` below, against this pilot's own real, committed furnished page
  (``tests/fixtures/furnished/slides_python/pages/_index.md``). That page has 2 real candidates;
  ``build_chunks.py`` itself reports **"verified 1/2 candidate examples"** - "Create a
  Presentation and Add a Shape" really runs end to end against the pinned commit with
  ``verify_python_example`` (a real venv + a real ``pip install -e`` + a real interpreter run),
  while "Format Text and Apply a Fill Effect" genuinely fails (it opens ``output.pptx``, a file
  only the FIRST candidate's own isolated run ever produces, so run independently it raises a
  real, honest ``FileNotFoundError``). This part is expected and correct: it matches pdf/java's
  own "1/3 candidates" pattern, not a defect.

  TC-101's worker also found that, even though "Create a Presentation and Add a Shape" really
  compiled/ran, its real chunk was STILL dropped before publish by the citation-validation
  authority path (``foss_mcp.normalization.citation``): its own real description text cites two
  bare, unqualified inline-code method names - `` `add_auto_shape()` `` and `` `add_text_frame()`
  `` (the trailing ``()`` stripped by ``find_symbol_anchors``) - that
  ``symbol_index_from_api_surface`` never registers as anchors (that index only ever registers
  ``ClassName``/``ClassName.MethodName`` anchors, never a bare, unqualified method name), so both
  anchors failed to resolve and ``validate_chunk`` set the whole example chunk's own verdict to
  the worst one any of its claims earned - excluding this real, run-verified example from
  ``citable_chunks`` entirely. The published generation used to end up with ZERO example chunks
  as a result (21 chunks written by ``build_chunks.py``, only 20 published by ``ingest.py``).

  G2/TC-105 fixed exactly this gap: ``foss_mcp.normalization.citation`` now exempts a
  compile-verified example chunk's own bare-method-name claims from the symbol-anchor check, so
  this real, run-verified example now genuinely survives publish. This file's own worker (TC-107)
  independently confirmed that today, first-hand: a real, hands-on re-run of the full
  containerized ``docker compose up --build ingest-slides-python`` pipeline below reports the
  SAME real, published generation now carrying this chunk (21 chunks written, 21 published), and
  real MCP-over-HTTP queries against the real running ``serving-slides-python`` container return
  its real content for both ``find_examples`` and ``lookup``.

  ``REAL_EXAMPLE_SYMBOL`` is ``add_auto_shape`` (``slide.shapes.add_auto_shape(...)``), the real,
  distinctive method call in the furnished page's "Create a Presentation and Add a Shape"
  candidate - confirmed today to appear verbatim in the real snippet text both ``find_examples``
  and ``lookup`` now return for this pilot. ``REAL_TASK_QUERY`` ("how do I insert a rectangle into
  a pptx file") is the exact literal, API-naive task question this file has always used; confirmed
  today, by hand, through the real running slides-python container, that ``lookup`` now composes a
  real, genuine ``TaskAnswer`` for it - ``doc_matches`` empty (no doc content has ever been
  published for this pilot), ``example`` carrying this same real, compile-verified example, its
  snippet containing both ``REAL_EXAMPLE_SYMBOL`` and the real source commit
  ``4e63447ba79d1c27a5192844847d9f872c5b92ad`` - never a fabricated match, and never a fallback to
  the ``search_symbols`` Miss this pilot used to return for this exact query. This closes
  REQ-G2-050 for slides/python for real, matching every other pilot's own complete bar
  (pdf/net's ``test_lookup_composes_the_real_task_answer_for_the_watermark_question`` and its
  siblings).

- ``REAL_DOC_QUERY``/``REAL_DOC_CONTENT_TYPE``/``REAL_DOC_FRAGMENT`` (REQ-G2-047, TC-117): real
  doc content, observed live through this exact container by hand on 2026-09-29, now genuinely
  published and served. slides/python's own furnished-content page (regenerated from
  repository-presenter's real sealed candidate via TC-119, same mechanism as the other pilots)
  carries a real "Installation, Dependencies, and Quick Start" content block whose own
  "Required Package Dependencies" section lists a real, distinctive package pin - `` `lxml>=4.9`
  ``. Confirmed live, by hand, by querying every one of the 4 real content_types against this
  exact running container: content_type="getting_started" for "lxml" returns exactly one real
  "FQN: Doc: Installation, Dependencies, and Quick Start" chunk whose own text is the literal
  "- `lxml>=4.9`" (and no others) - never a guess at what a container might return. This is a
  FOURTH distinct real outcome across this project's own pilots so far (pdf/net landed in
  "developer_guide", pdf/java landed in "troubleshooting", pdf/go landed in "getting_started" via
  its own "install "/"quick start" wording - see TC-113/TC-115/TC-116); this pilot's own real
  "Scope and Limitations" content block also lands in "developer_guide" (confirmed live: it
  trips no getting_started/troubleshooting/faq hint word except where it literally names
  "troubleshooting-relevant scope boundaries", which DOES land that one specific chunk in
  "troubleshooting" instead - confirmed live too), so a bare content_type="troubleshooting" query
  is not a universal Miss for this pilot the way it is for some others; this file only pins down
  what it actually observed (the "lxml" dependency pin, genuinely "getting_started"), not every
  other chunk's own classification.
- ``REAL_DOC_TASK_QUERY`` (REQ-G2-047, TC-117): the real, API-naive task question CONFIRMED live,
  by hand, on 2026-09-29, to make lookup's own doc-fallback path (``_compose_from_docs``) surface
  the SAME real "getting_started"-classified ``lxml`` chunk above as ``doc_matches``, with
  ``example`` genuinely ``None`` (this exact query matches no real verified example - confirmed
  live, ``find_examples`` itself returns a bare Miss for it) - never ``()`` as every prior
  comprehensive live verification pass found for every pilot, for every query, before TC-109
  through TC-112 (and TC-113/TC-114/TC-115/TC-116/this card) closed the gap.

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
PROJECT_NAME = "foss-mcp-e2e-tc101"
BASE_URL = "http://127.0.0.1:8085"
MCP_PATH = "/mcp"
READYZ_PATH = "/readyz"
# Matches pdf/net's own TC-069 budget: this pilot's ingestion container also does a real git
# clone, a real venv creation, and a real `pip install -e` of the reference library, then a real
# interpreter run per real furnished-content candidate - budgeted the same as the other pilots.
INGEST_TIMEOUT_SECONDS = 1500
READYZ_TIMEOUT_SECONDS = 180
POLL_INTERVAL_SECONDS = 2

PROTOCOL_VERSION = "2025-06-18"

# G2/TC-277's own real, regenerated slides_python fixture
# (tests/fixtures/slides_python/api_surface.json): its real "source_commit" is
# "4e63447ba79d1c27a5192844847d9f872c5b92ad" (same pinned commit as before TC-277 - only the kept
# selection changed), now selected by TC-252/TC-265's real, non-degenerate centrality ranking
# rather than alphabetical order. "Shape"'s real definition entry (class_import
# "aspose.slides_foss.Shape.Shape", kept at index 136 of 300 - confirmed directly against the
# committed fixture) carries 27 real methods. A real query response containing this exact string
# is genuine, falsifiable proof of real content, never a guess.
REAL_SYMBOL = "Shape"
REAL_SOURCE_COMMIT = "4e63447ba79d1c27a5192844847d9f872c5b92ad"

# No enum entry in this real, regenerated fixture carries any real members (confirmed by
# inspecting every one of its 24 real enum entries' own "enum_members" directly - the pure-ast
# Python reader was never asked to extract member values, unchanged by TC-265); "Shape"'s own real
# FQN (its class_import, distinct from the bare REAL_SYMBOL name above - this fixture's own
# class_import values are dotted paths, unlike pdf/go's bare-name fixture) and its own real
# Methods:/Bases: lines are asserted directly in get_symbol's test below instead.
REAL_SYMBOL_FQN = "aspose.slides_foss.Shape.Shape"
REAL_METHOD_FRAGMENT = "presentation() -> IPresentation"
# TC-265's real base-class data reaching this live container for real, not just the offline
# fixture test: confirmed directly, "Shape"'s own real "bases" list is
# ["StrictAttributes", "IShape", "ISlideComponent", "IPresentationComponent",
# "IHyperlinkContainer", "ABC"] - the first entry this file has EVER been able to assert a real
# Bases: line for.
REAL_BASE_FRAGMENT = "IPresentationComponent"

# REQ-G2-048 (TC-091/TC-097/TC-105 pattern): a real, hands-on run of the full containerized
# ingest-slides-python pipeline (below) reports "verified 1/2 candidate examples" for this
# pilot's real furnished page (tests/fixtures/furnished/slides_python/pages/_index.md); the one
# real, run-verified candidate ("Create a Presentation and Add a Shape") used to be dropped
# before publish by the citation-validation authority path, because its own real description
# text cites two bare, unqualified inline-code method names (`add_auto_shape()`/
# `add_text_frame()`) that this project's own symbol_index_from_api_surface never registers as
# anchors - G2/TC-105 exempted a compile-verified example chunk's own bare-method-name claims
# from that check, so this real chunk now genuinely survives publish (see the module docstring
# for the full, confirmed mechanism). ``add_auto_shape`` (``slide.shapes.add_auto_shape(...)``)
# is the real, distinctive method call in that candidate; confirmed today, by hand, through the
# real running slides-python container, to appear verbatim in the real snippet find_examples/
# lookup now return.
REAL_EXAMPLE_SYMBOL = "add_auto_shape"

# REQ-G2-050 (TC-080/TC-089/TC-098/TC-099/TC-100 pattern): the exact literal, API-naive task
# question CONFIRMED live, by hand, today, through both a real local generation and the real
# running slides-python container: now that TC-105 fixed the citation-validation gap above, this
# pilot has one real citable example (see REAL_EXAMPLE_SYMBOL), so `lookup` composes a real
# TaskAnswer for this query - doc_matches empty (zero published doc content for this pilot),
# example carrying the real, compile-verified "Create a Presentation and Add a Shape" example -
# never a fabricated TaskAnswer, and never a merely-incidental lexical match either (several
# other natural phrasings of this same task were tried first, during TC-101, and returned real
# but incidental search_symbols hits instead, since common English words like "shape"/"slide"
# appear verbatim in several published class docstrings - see the module docstring). This exact
# string is required verbatim - the card's own negative control corrupts it, and that corruption
# must break this test's assertions.
REAL_TASK_QUERY = "how do I insert a rectangle into a pptx file"

# REQ-G2-047 (TC-117): real doc content, observed live through this exact container by hand on
# 2026-09-29, now genuinely published and served. slides/python's own furnished-content page
# carries a real "Installation, Dependencies, and Quick Start" content block whose own "Required
# Package Dependencies" section lists a real, distinctive package pin - `lxml>=4.9`. Confirmed
# live, by hand, by querying every one of the 4 real content_types against this exact running
# container: content_type="getting_started" for "lxml" returns exactly this one real "FQN: Doc:
# Installation, Dependencies, and Quick Start" chunk whose own text is "- `lxml>=4.9`" (and no
# others) - never a guess at what a container might return.
REAL_DOC_QUERY = "lxml"
REAL_DOC_CONTENT_TYPE = "getting_started"
REAL_DOC_FRAGMENT = "lxml>=4.9"

# REQ-G2-047 (TC-117): the real, API-naive task question CONFIRMED live, by hand, on 2026-09-29,
# to make lookup's own doc-fallback path (_compose_from_docs) surface the SAME real
# "getting_started"-classified lxml chunk above as doc_matches - never [] as every prior
# comprehensive live verification pass found for every pilot, for every query, before TC-109
# through TC-112 (and TC-113/TC-114/TC-115/TC-116/this card) closed the gap. This query's own
# words have zero lexical overlap with this pilot's own one real verified example (confirmed
# live: example=None for this exact query, run by hand against the real running container - even
# find_examples on its own returns a bare Miss for it), so this is also a real, live instance of
# a TaskAnswer composed from a doc match alone, with example genuinely None - never fabricated.
REAL_DOC_TASK_QUERY = "does this library require lxml"

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
            "clientInfo": {"name": "tc-101-e2e-client", "version": "0.0.1"},
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
    """The real, live first publish: build and run the one-shot ``ingest-slides-python`` service
    to completion, then bring up ``serving-slides-python`` against the SAME named volume it just
    published into.

    ``docker compose down -v`` first, so this is idempotent against any stale volume state left
    by a prior run. A distinct ``PROJECT_NAME`` (``foss-mcp-e2e-tc101``) and only these two
    services are ever brought up, so this never collides with pdf/net's, pdf/typescript's,
    pdf/java's, pdf/go's, or any other pilot's own concurrent E2E run.
    """
    _compose("down", "-v", "--remove-orphans")
    try:
        ingest = _compose(
            "up",
            "--build",
            "--exit-code-from",
            "ingest-slides-python",
            "ingest-slides-python",
            timeout=INGEST_TIMEOUT_SECONDS,
        )
        assert ingest.returncode == 0, (ingest.stdout + ingest.stderr)[-4000:]

        up = _compose("up", "-d", "--build", "serving-slides-python")
        assert up.returncode == 0, (up.stdout + up.stderr)[-4000:]
        _wait_for_readyz()
        yield
    finally:
        _compose("down", "-v", "--remove-orphans")


class _McpSession:
    """A real MCP-over-HTTP client session against the real running ``serving-slides-python``
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
    assert result["indexed_generation_id"].startswith("slides::python::self_extracted::")
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


def test_find_examples_returns_the_real_compile_verified_shape_example(
    session: _McpSession,
) -> None:
    """REQ-G2-048, now closed for real for this pilot: G2/TC-105 fixed the citation-validation
    gap TC-101 found (a compile-verified example chunk's own bare-method-name claims, e.g.
    `add_auto_shape()`, used to sink the WHOLE chunk's verdict even though the example genuinely
    ran end to end - see the module docstring), so the real running ``serving-slides-python``
    container's ``find_examples`` now returns the one real, compile-verified "Create a
    Presentation and Add a Shape" example for this real, on-topic query - proven through the FULL
    containerized production path (a real ``docker compose up --build ingest-slides-python``
    below, not merely TC-105's own host-level replay), not a stub, and not a silently-empty list.
    """
    body = session.call_tool("find_examples", {"query": "create a presentation and add a shape"})
    result = body["result"]["structuredContent"]["result"]
    assert isinstance(result, list) and result, f"expected real ExampleMatch(es), got a Miss: {result}"
    assert any(REAL_EXAMPLE_SYMBOL in match["snippet"] for match in result), result
    assert any(REAL_SOURCE_COMMIT in match["snippet"] for match in result), result


def test_lookup_composes_the_real_task_answer_for_the_rectangle_question(
    session: _McpSession,
) -> None:
    """REQ-G2-050 (TC-080/TC-089/TC-098/TC-099/TC-100 pattern), now met for real for
    slides/python too: now that TC-105's fix lets this pilot's one real, compile-verified example
    survive publish (see module docstring), the real running container's ``lookup`` composes a
    real ``TaskAnswer`` for this real, API-naive task question - ``doc_matches`` empty (no
    getting_started/developer_guide/troubleshooting/faq content has ever been published for this
    pilot), ``example`` carrying the same real, compile-verified "Create a Presentation and Add a
    Shape" example ``find_examples`` itself proves above, its snippet carrying both the real
    symbol and the real source commit ``infra/build_chunks.py`` appends to every real chunk's
    text - never a fabricated ``TaskAnswer``, and never the honest-but-incomplete ``Miss`` this
    exact query used to fall back to before TC-105's fix.
    """
    body = session.call_tool("lookup", {"query": REAL_TASK_QUERY})
    result = body["result"]["structuredContent"]["result"]
    assert isinstance(result, dict), f"expected a composed TaskAnswer, got: {result}"
    assert "doc_matches" in result and "example" in result, result
    assert result["example"] is not None, f"expected a real, verified example, got none: {result}"
    snippet = result["example"]["snippet"]
    assert REAL_EXAMPLE_SYMBOL in snippet, snippet
    assert REAL_SOURCE_COMMIT in snippet, snippet


def test_get_symbol_returns_the_real_type_spec_for_shape(session: _McpSession) -> None:
    """This pilot's own equivalent of pdf/net's/pdf/java's enum-``get_symbol`` proof, adapted to
    the real entry shape this fixture actually has: no enum entry in this real, regenerated
    fixture carries any real members (confirmed by inspecting every entry's own
    ``enum_members`` - see module docstring), so this asserts the real running slides-python
    container's ``get_symbol`` for the real ``Shape`` class (its own real, fully-qualified
    ``class_import``, distinct from the bare ``REAL_SYMBOL`` used above) returns its real,
    distinctive ``Methods:`` line - not a stub, not an empty index - AND, for the first time for
    this pilot, a real, non-empty ``Bases:`` line too, proving TC-265's real base-class data
    reaches this live container, not just the offline fixture test.
    """
    body = session.call_tool("get_symbol", {"fqn": REAL_SYMBOL_FQN})
    result = body["result"]["structuredContent"]["result"]
    assert isinstance(result, dict) and "raw_text" in result, f"expected a real SymbolSignature: {result}"
    assert result["kind"] == "class", result
    assert REAL_METHOD_FRAGMENT in result["raw_text"], result
    assert REAL_BASE_FRAGMENT in result["raw_text"], result


# ---------------------------------------------------------------------
# REQ-G2-047 (TC-117): slides/python's own real documentation content (TC-112's real
# _build_doc_chunks, reachable via TC-109's search_docs routing fix, no longer confused for a
# real symbol by search_symbols since TC-120) genuinely served through the FULL containerized
# production path - not merely replayed offline by TC-112's own unit-level check. Sourced from
# this pilot's own real furnished page, regenerated from repository-presenter's real sealed
# candidate via TC-119.
# ---------------------------------------------------------------------


def test_search_docs_returns_real_furnished_content_for_slides_python(session: _McpSession) -> None:
    """``search_docs`` with an explicit ``content_type`` for a real, distinctive query returns
    real, non-empty documentation content from slides/python's own real, regenerated furnished
    page (TC-119) - never the "no published generation for this scope"/empty-index Miss every
    content tool call gave before TC-112 wired real doc chunks into ingestion.

    ``getting_started`` is confirmed live, by hand, against the real running container: this
    pilot's own real "Required Package Dependencies" chunk (under the "Installation,
    Dependencies, and Quick Start" content block) lists a real, distinctive package pin -
    `` `lxml>=4.9` `` - and that chunk's own text never happens to contain any
    ``developer_guide``/``troubleshooting``/``faq`` hint word, so it genuinely buckets
    ``getting_started`` instead of the classifier's own ``developer_guide`` default.
    """
    body = session.call_tool("search_docs", {"query": REAL_DOC_QUERY, "content_type": REAL_DOC_CONTENT_TYPE})
    result = body["result"]["structuredContent"]["result"]
    assert isinstance(result, list) and result, f"expected real doc matches, got a Miss: {result}"
    assert all(match["content_type"] == REAL_DOC_CONTENT_TYPE for match in result), result
    assert any(REAL_DOC_FRAGMENT in match["text"] for match in result), result
    assert any(match["text"].startswith("FQN: Doc: ") for match in result), result


def test_search_docs_troubleshooting_is_an_honest_miss_for_the_dependency_query(
    session: _McpSession,
) -> None:
    """``troubleshooting`` correctly stays an honest ``Miss`` for this same query - confirmed
    live, by hand, against the real running container: the real ``lxml`` dependency pin chunk
    genuinely classifies as ``getting_started`` (asserted above), never as ``troubleshooting``
    too, so a caller who explicitly asks for ``troubleshooting`` content for this query gets a
    real, honest absence rather than the same content silently duplicated across categories.
    """
    body = session.call_tool("search_docs", {"query": REAL_DOC_QUERY, "content_type": "troubleshooting"})
    result = body["result"]["structuredContent"]["result"]
    assert isinstance(result, dict) and "reason" in result, f"expected an honest Miss, got: {result}"


def test_lookup_returns_real_doc_matches_for_an_api_naive_query(session: _McpSession) -> None:
    """The concrete, final proof the whole pipeline (TC-109 through TC-112, TC-113, TC-114,
    TC-115, TC-116, plus this card) closes the real gap end to end for slides/python too, through
    a real MCP client's own eyes: ``lookup``'s doc-fallback path (``_compose_from_docs``) now
    genuinely composes a ``TaskAnswer`` whose ``doc_matches`` is non-empty for a real, API-naive
    query - previously ``()`` for every pilot, for every query, confirmed by this project's own
    earlier comprehensive live verification pass (see ``lookup.py``'s own module docstring,
    REQ-G2-049).

    This real query matches no real verified example (confirmed live: ``example`` is ``None``),
    so this is also a real, live instance of a ``TaskAnswer`` composed from a doc match alone,
    never fabricated.
    """
    body = session.call_tool("lookup", {"query": REAL_DOC_TASK_QUERY})
    result = body["result"]["structuredContent"]["result"]
    assert isinstance(result, dict), f"expected a composed TaskAnswer, got: {result}"
    assert "doc_matches" in result and "example" in result, result
    doc_matches = result["doc_matches"]
    assert isinstance(doc_matches, list) and doc_matches, f"expected real doc_matches, got none: {result}"
    assert any(REAL_DOC_FRAGMENT in match["text"] for match in doc_matches), doc_matches
    assert result["example"] is None, f"expected no verified example for this query, got: {result['example']}"
