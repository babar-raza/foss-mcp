"""G3/TC-508 (REQ-G3-008): ``GatewayEmbeddingProvider.set_query_time_limits``.

The query-time limits make one attempt with no backoff sleep and a short socket timeout; the defaults
(ingestion) still make three attempts with 1 s then 2 s of backoff. Offline: a fake ``_transport`` and
a recording ``_sleep``, as ``test_gateway_embedding_provider.py`` does. No real sleep, no network.
"""

from __future__ import annotations

import pytest

from foss_mcp.indexing.gateway_embedding_provider import (
    EmbeddingBudgetExceededError,
    GatewayEmbeddingError,
    GatewayEmbeddingProvider,
    GatewayTransportError,
)

_GATEWAY_ENV_VARS = (
    "FOSS_MCP_EMBEDDING_GATEWAY_ENDPOINT",
    "FOSS_MCP_EMBEDDING_GATEWAY_API_KEY",
    "FOSS_MCP_EMBEDDING_GATEWAY_MODEL",
    "FOSS_MCP_EMBEDDING_GATEWAY_DIMENSION",
    "FOSS_MCP_EMBEDDING_GATEWAY_BATCH_SIZE",
    "FOSS_MCP_EMBEDDING_GATEWAY_TIMEOUT_SECONDS",
    "FOSS_MCP_EMBEDDING_GATEWAY_MAX_REQUESTS",
)


class AlwaysFailingTransport:
    """Raises a connection-error ``GatewayTransportError`` (no HTTP status) on every call."""

    def __init__(self) -> None:
        self.timeouts: list[float] = []

    def __call__(self, url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
        self.timeouts.append(timeout)
        raise GatewayTransportError(None, "connection refused")

    @property
    def calls(self) -> int:
        return len(self.timeouts)


class SleepRecorder:
    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


@pytest.fixture(autouse=True)
def _gateway_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _GATEWAY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FOSS_MCP_EMBEDDING_GATEWAY_ENDPOINT", "https://gateway.invalid/v1/")
    monkeypatch.setenv("FOSS_MCP_EMBEDDING_GATEWAY_API_KEY", "unit-test-key")


def _provider(transport: AlwaysFailingTransport, sleeps: SleepRecorder) -> GatewayEmbeddingProvider:
    return GatewayEmbeddingProvider(_transport=transport, _sleep=sleeps)


def test_query_time_limits_make_exactly_one_attempt_and_never_sleep() -> None:
    transport, sleeps = AlwaysFailingTransport(), SleepRecorder()
    provider = _provider(transport, sleeps)
    provider.set_query_time_limits(max_attempts=1, timeout_seconds=2.0)

    with pytest.raises(GatewayEmbeddingError):
        provider.embed(["a query"])

    assert transport.calls == 1
    assert sleeps.delays == []


def test_query_time_limits_pass_a_short_timeout_to_the_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FOSS_MCP_EMBEDDING_GATEWAY_TIMEOUT_SECONDS", "30")
    transport, sleeps = AlwaysFailingTransport(), SleepRecorder()
    provider = _provider(transport, sleeps)
    provider.set_query_time_limits(max_attempts=1, timeout_seconds=2.0)

    with pytest.raises(GatewayEmbeddingError):
        provider.embed(["a query"])

    assert transport.timeouts == [min(30.0, 2.0)] == [2.0]


def test_a_longer_requested_timeout_never_raises_the_configured_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FOSS_MCP_EMBEDDING_GATEWAY_TIMEOUT_SECONDS", "1.5")
    transport, sleeps = AlwaysFailingTransport(), SleepRecorder()
    provider = _provider(transport, sleeps)
    provider.set_query_time_limits(max_attempts=1, timeout_seconds=120.0)

    with pytest.raises(GatewayEmbeddingError):
        provider.embed(["a query"])

    assert transport.timeouts == [1.5]


def test_default_ingestion_behaviour_is_unchanged_three_attempts_with_backoff() -> None:
    transport, sleeps = AlwaysFailingTransport(), SleepRecorder()
    provider = _provider(transport, sleeps)

    with pytest.raises(GatewayEmbeddingError, match="after 3 attempt"):
        provider.embed(["a document chunk"])

    assert transport.calls == 3
    assert sleeps.delays == [1.0, 2.0]
    assert transport.timeouts == [30.0, 30.0, 30.0]


@pytest.mark.parametrize(
    ("max_attempts", "timeout_seconds"),
    [(0, 2.0), (-1, 2.0), (1, 0.0), (1, -0.5)],
)
def test_set_query_time_limits_validates_its_arguments(max_attempts: int, timeout_seconds: float) -> None:
    provider = _provider(AlwaysFailingTransport(), SleepRecorder())

    with pytest.raises(ValueError):
        provider.set_query_time_limits(max_attempts=max_attempts, timeout_seconds=timeout_seconds)


def test_a_rejected_limit_leaves_the_provider_as_it_was() -> None:
    transport, sleeps = AlwaysFailingTransport(), SleepRecorder()
    provider = _provider(transport, sleeps)

    with pytest.raises(ValueError):
        provider.set_query_time_limits(max_attempts=0, timeout_seconds=2.0)
    with pytest.raises(GatewayEmbeddingError):
        provider.embed(["a document chunk"])

    assert transport.calls == 3


def test_the_request_budget_still_applies_to_the_single_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FOSS_MCP_EMBEDDING_GATEWAY_MAX_REQUESTS", "1")
    transport, sleeps = AlwaysFailingTransport(), SleepRecorder()
    provider = _provider(transport, sleeps)
    provider.set_query_time_limits(max_attempts=1, timeout_seconds=2.0)

    with pytest.raises(GatewayEmbeddingError):
        provider.embed(["first query"])
    assert transport.calls == 1

    with pytest.raises(EmbeddingBudgetExceededError):
        provider.embed(["second query"])

    assert transport.calls == 1  # the budget check ran before a second HTTP call was made
    assert sleeps.delays == []
