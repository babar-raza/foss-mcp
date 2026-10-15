"""G3/TC-508 (REQ-G3-008): find_examples and lookup can no longer wait on the embedding gateway.

Offline and deterministic. The vector fallback runs through a ``VectorFallbackGuard`` that an autouse
fixture replaces with one using a 0.05 second deadline and a fake clock the test advances. A provider
that must hang blocks on a ``threading.Event`` the test sets in a ``finally``; the assertions are that
the tool RETURNED while that Event was still unset, never a measured duration. The real gateway
provider is never constructed against a network: the one test that builds the real class gives it a
fake ``_transport`` and ``_sleep``. No test is skipped and nothing is written in the repository.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TypeVar

import pytest

import foss_mcp.mcp.tools.example_search_mode as example_search_mode_module
import foss_mcp.mcp.tools.find_examples as find_examples_module
from foss_mcp.indexing.gateway_embedding_provider import (
    GatewayEmbeddingError,
    GatewayEmbeddingProvider,
    GatewayTransportError,
)
from foss_mcp.mcp.server import _to_jsonable
from foss_mcp.mcp.tools.example_search_mode import (
    EXAMPLE_SEARCH_GATEWAY,
    EXAMPLE_SEARCH_GATEWAY_DEGRADED,
    EXAMPLE_SEARCH_LEXICAL_ONLY,
    REASON_CIRCUIT_OPEN,
    REASON_GATEWAY_NOT_CONFIGURED,
    example_search_mode,
)
from foss_mcp.mcp.tools.find_examples import ExampleMatch, NoExampleFound, find_examples
from foss_mcp.mcp.tools.lookup import lookup
from foss_mcp.mcp.tools.search_symbols import Miss
from foss_mcp.mcp.tools.vector_guard import VectorFallbackGuard, _set_guard, get_guard
from tests.mcp.tools.test_retrieval_tools import (
    _VECTOR_FALLBACK_NONSENSE_QUERY,
    PDF_NET_SCOPE,
    _FakeQueryEmbeddingProvider,
    _publish_with_hand_vectors,
    _publish_without_vector_index,
    _store,
    _vector_fallback_chunk,
)
from tests.mcp.tools.test_vector_guard import FakeClock

T = TypeVar("T")

REPO_ROOT = Path(__file__).resolve().parents[3]
TOOLS_DIR = REPO_ROOT / "src" / "foss_mcp" / "mcp" / "tools"

ENDPOINT = "FOSS_MCP_EMBEDDING_GATEWAY_ENDPOINT"
API_KEY = "FOSS_MCP_EMBEDDING_GATEWAY_API_KEY"
DIMENSION = "FOSS_MCP_EMBEDDING_GATEWAY_DIMENSION"

PLAIN_MISS_REASON = "no verified example matches this query"
TASK_NONSENSE_QUERY = f"how do I {_VECTOR_FALLBACK_NONSENSE_QUERY}"


class _CountingGuard(VectorFallbackGuard):
    """A real guard that counts how many times ``call`` was reached."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.call_count = 0

    def call(self, fn: Callable[[], T]) -> tuple[str, T | None]:
        self.call_count += 1
        return super().call(fn)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture(autouse=True)
def guard(clock: FakeClock) -> Iterator[_CountingGuard]:
    pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="test-find-examples-deadline")
    installed = _CountingGuard(deadline_seconds=0.05, clock=clock, executor=pool)
    previous = get_guard()
    _set_guard(installed)
    try:
        yield installed
    finally:
        _set_guard(previous)
        pool.shutdown(wait=True)


@pytest.fixture(autouse=True)
def _gateway_variables_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (ENDPOINT, API_KEY, DIMENSION):
        monkeypatch.delenv(name, raising=False)


class _Hang:
    """A provider factory whose ``embed`` blocks on an Event; counts constructions."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.constructed = 0

    def __call__(self) -> _Hang:
        self.constructed += 1
        return self

    def embed(self, texts):
        self.release.wait(timeout=30)
        return [(1.0, 0.0) for _ in texts]


def _use_provider(monkeypatch: pytest.MonkeyPatch, factory: Callable[[], object]) -> None:
    monkeypatch.setattr(find_examples_module, "GatewayEmbeddingProvider", factory)


def _use_query_vector(monkeypatch: pytest.MonkeyPatch, cosine: float) -> None:
    vector = (cosine, (1.0 - cosine**2) ** 0.5)  # cosine against (1.0, 0.0) is exactly *cosine*
    _use_provider(monkeypatch, lambda: _FakeQueryEmbeddingProvider(vector))


def _vector_store(tmp_path: Path):
    """A generation holding one verified example and a hand-built vector index of (1.0, 0.0)."""
    store = _store(tmp_path)
    chunks = _vector_fallback_chunk()
    _publish_with_hand_vectors(store, PDF_NET_SCOPE, chunks, {chunks[0].text: (1.0, 0.0)})
    return store


def _miss(result: object) -> NoExampleFound:
    assert isinstance(result, NoExampleFound), result
    return result


def test_a_hung_provider_returns_the_honest_miss_with_a_skipped_timeout_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hang = _Hang()
    _use_provider(monkeypatch, hang)
    try:
        result = _miss(find_examples(_vector_store(tmp_path), PDF_NET_SCOPE, _VECTOR_FALLBACK_NONSENSE_QUERY))

        assert not hang.release.is_set()  # find_examples returned while the provider was still blocked
        assert result.vector_fallback == "skipped_timeout"
        assert result.found is False
        assert result.reason.startswith("no verified example matches this query")
        assert result.reason == "no verified example matches this query (semantic fallback skipped: timeout)"
    finally:
        hang.release.set()


def test_the_note_reaches_the_json_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hang = _Hang()
    _use_provider(monkeypatch, hang)
    try:
        result = find_examples(_vector_store(tmp_path), PDF_NET_SCOPE, _VECTOR_FALLBACK_NONSENSE_QUERY)

        payload = json.loads(json.dumps(_to_jsonable(result)))
    finally:
        hang.release.set()

    assert payload["found"] is False
    assert payload["vector_fallback"] == "skipped_timeout"
    assert payload["reason"].startswith("no verified example matches this query")


def test_three_timeouts_open_the_circuit_and_the_fourth_miss_never_constructs_the_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, guard: _CountingGuard
) -> None:
    hang = _Hang()
    _use_provider(monkeypatch, hang)
    store = _vector_store(tmp_path)
    try:
        for _ in range(3):
            assert _miss(
                find_examples(store, PDF_NET_SCOPE, _VECTOR_FALLBACK_NONSENSE_QUERY)
            ).vector_fallback == ("skipped_timeout")
        assert hang.constructed == 3
        assert guard.is_open() is True

        fourth = _miss(find_examples(store, PDF_NET_SCOPE, _VECTOR_FALLBACK_NONSENSE_QUERY))

        assert fourth.vector_fallback == "skipped_circuit_open"
        assert (
            fourth.reason
            == "no verified example matches this query (semantic fallback skipped: circuit open)"
        )
        assert (
            hang.constructed == 3
        )  # the raw query was never handed to a provider while the circuit was open
    finally:
        hang.release.set()


def test_the_circuit_closes_after_injected_time_and_a_vector_match_works_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, guard: _CountingGuard, clock: FakeClock
) -> None:
    hang = _Hang()
    _use_provider(monkeypatch, hang)
    store = _vector_store(tmp_path)
    try:
        for _ in range(3):
            find_examples(store, PDF_NET_SCOPE, _VECTOR_FALLBACK_NONSENSE_QUERY)
        assert guard.is_open() is True
        clock.advance(61.0)
        assert guard.is_open() is False
    finally:
        hang.release.set()

    _use_query_vector(monkeypatch, 0.9)
    result = find_examples(store, PDF_NET_SCOPE, _VECTOR_FALLBACK_NONSENSE_QUERY)

    assert isinstance(result, list) and result
    assert all(match.source == "vector" for match in result)
    assert guard.is_open() is False


def test_a_configuration_error_gives_skipped_error_without_opening_the_circuit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, guard: _CountingGuard
) -> None:
    def _unconfigured():
        raise RuntimeError("FOSS_MCP_EMBEDDING_GATEWAY_ENDPOINT must be set")

    _use_provider(monkeypatch, _unconfigured)
    store = _vector_store(tmp_path)

    for _ in range(5):
        result = _miss(find_examples(store, PDF_NET_SCOPE, _VECTOR_FALLBACK_NONSENSE_QUERY))
        assert result.vector_fallback == "skipped_error"
        assert result.reason == "no verified example matches this query (semantic fallback skipped: error)"

    assert guard.is_open() is False


def test_a_lexical_hit_never_touches_the_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, guard: _CountingGuard
) -> None:
    hang = _Hang()
    _use_provider(monkeypatch, hang)

    result = find_examples(_vector_store(tmp_path), PDF_NET_SCOPE, "merge two documents")

    assert isinstance(result, list) and result
    assert all(match.source == "lexical" for match in result)
    assert guard.call_count == 0
    assert hang.constructed == 0


def test_a_vector_match_is_unchanged_through_the_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, guard: _CountingGuard
) -> None:
    _use_query_vector(monkeypatch, 0.9)

    result = find_examples(_vector_store(tmp_path), PDF_NET_SCOPE, _VECTOR_FALLBACK_NONSENSE_QUERY)

    assert isinstance(result, list) and result
    assert all(isinstance(match, ExampleMatch) for match in result)
    assert all(match.source == "vector" for match in result)
    assert any("document.Merge(other)" in match.snippet for match in result)
    assert all(match.coverage == pytest.approx(0.9) for match in result)
    assert guard.call_count == 1


def test_a_normal_miss_has_no_note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_query_vector(monkeypatch, 0.3)  # the stage runs and finds nothing above the threshold

    result = _miss(find_examples(_vector_store(tmp_path), PDF_NET_SCOPE, _VECTOR_FALLBACK_NONSENSE_QUERY))

    assert result.vector_fallback is None
    assert result.reason == PLAIN_MISS_REASON


def test_a_generation_without_a_vector_index_has_no_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, guard: _CountingGuard
) -> None:
    hang = _Hang()
    _use_provider(monkeypatch, hang)
    store = _store(tmp_path)
    _publish_without_vector_index(store, PDF_NET_SCOPE, _vector_fallback_chunk())

    result = _miss(find_examples(store, PDF_NET_SCOPE, _VECTOR_FALLBACK_NONSENSE_QUERY))

    assert result.vector_fallback is None
    assert result.reason == PLAIN_MISS_REASON
    assert guard.call_count == 0
    assert hang.constructed == 0


def test_lookup_returns_promptly_when_the_gateway_hangs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hang = _Hang()
    _use_provider(monkeypatch, hang)
    try:
        result = lookup(_vector_store(tmp_path), PDF_NET_SCOPE, TASK_NONSENSE_QUERY)

        assert isinstance(result, Miss)  # the normal miss, exactly as with no gateway at all
        assert hang.constructed == 1  # the vector stage really was attempted
        assert not hang.release.is_set()  # lookup returned while the provider was still blocked
    finally:
        hang.release.set()


def _gateway_payload() -> dict:
    return {
        "vector_index": {
            "dimension": 4096,
            "points": [
                {
                    "point_id": "p0",
                    "chunk_id": "c0",
                    "vector": [1.0],
                    "text": "FQN: Example: sign a file\nExample: doc.Sign()",
                }
            ],
        }
    }


def _configure_gateway(monkeypatch: pytest.MonkeyPatch, endpoint: str = "https://gateway.invalid/v1") -> None:
    monkeypatch.setenv(ENDPOINT, endpoint)
    monkeypatch.setenv(API_KEY, "unit-test-key")
    monkeypatch.setenv(DIMENSION, "4096")


def _open_the_circuit(guard: VectorFallbackGuard) -> None:
    def _down() -> None:
        raise GatewayEmbeddingError("gateway down")

    for _ in range(3):
        guard.call(_down)
    assert guard.is_open() is True


def test_example_search_mode_reports_degraded_while_open_and_recovers(
    monkeypatch: pytest.MonkeyPatch, guard: _CountingGuard, clock: FakeClock
) -> None:
    _configure_gateway(monkeypatch)
    payload = _gateway_payload()
    assert example_search_mode(payload)[0] == EXAMPLE_SEARCH_GATEWAY

    _open_the_circuit(guard)

    assert EXAMPLE_SEARCH_GATEWAY_DEGRADED == "lexical+vector(gateway) degraded"
    assert REASON_CIRCUIT_OPEN == "embedding gateway circuit open after repeated failures"
    assert example_search_mode(payload) == (EXAMPLE_SEARCH_GATEWAY_DEGRADED, REASON_CIRCUIT_OPEN)
    assert example_search_mode(payload, guard=guard) == (EXAMPLE_SEARCH_GATEWAY_DEGRADED, REASON_CIRCUIT_OPEN)

    clock.advance(61.0)

    assert example_search_mode(payload)[0] == EXAMPLE_SEARCH_GATEWAY


def test_example_search_mode_is_unchanged_without_the_gateway_variables(guard: _CountingGuard) -> None:
    payload = _gateway_payload()
    assert example_search_mode(payload) == (EXAMPLE_SEARCH_LEXICAL_ONLY, REASON_GATEWAY_NOT_CONFIGURED)

    _open_the_circuit(guard)

    # An open circuit does not turn a lexical-only deployment into a "degraded gateway" one.
    assert example_search_mode(payload) == (EXAMPLE_SEARCH_LEXICAL_ONLY, REASON_GATEWAY_NOT_CONFIGURED)
    assert example_search_mode({}) == (EXAMPLE_SEARCH_LEXICAL_ONLY, "no example vectors in this generation")


def test_the_degraded_text_leaks_no_environment_value(
    monkeypatch: pytest.MonkeyPatch, guard: _CountingGuard
) -> None:
    endpoint_canary = "https://canary-endpoint-7f3a9c1e.invalid/v1"
    key_canary = "canary-key-7f3a9c1e-do-not-leak"
    _configure_gateway(monkeypatch, endpoint_canary)
    monkeypatch.setenv(API_KEY, key_canary)
    _open_the_circuit(guard)

    mode, reason = example_search_mode(_gateway_payload())

    assert (mode, reason) == (EXAMPLE_SEARCH_GATEWAY_DEGRADED, REASON_CIRCUIT_OPEN)
    for canary in (endpoint_canary, key_canary, "canary-endpoint", "canary-key", "7f3a9c1e"):
        assert canary not in mode
        assert canary not in reason


def test_find_examples_makes_exactly_one_real_provider_attempt_per_miss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, guard: _CountingGuard
) -> None:
    _configure_gateway(monkeypatch)
    transport_calls: list[float] = []
    sleeps: list[float] = []

    def _transport(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
        transport_calls.append(timeout)
        raise GatewayTransportError(None, "connection refused")

    _use_provider(monkeypatch, lambda: GatewayEmbeddingProvider(_transport=_transport, _sleep=sleeps.append))

    result = _miss(find_examples(_vector_store(tmp_path), PDF_NET_SCOPE, _VECTOR_FALLBACK_NONSENSE_QUERY))

    assert result.vector_fallback == "skipped_error"
    assert len(transport_calls) == 1  # one attempt, not the ingestion provider's three
    assert sleeps == []  # and no backoff
    assert transport_calls == [guard.deadline_seconds]  # the socket timeout is bounded by the deadline


def test_find_examples_source_calls_get_guard() -> None:
    for name in ("find_examples.py", "example_search_mode.py"):
        source = (TOOLS_DIR / name).read_text(encoding="utf-8")
        assert "get_guard(" in source, name
    assert find_examples_module.get_guard is get_guard
    assert example_search_mode_module.get_guard is get_guard
