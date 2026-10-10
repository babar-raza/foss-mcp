"""Deterministic, offline tests for ``GatewayEmbeddingProvider`` (G2/TC-330).

No test here makes a real network call: every test injects a fake transport callable via
``GatewayEmbeddingProvider``'s keyword-only ``_transport``/``_sleep`` parameters, per this
card's own instructions. The ONE real, manual, live call against the actual gateway is run
separately by the worker, outside this suite, and is not part of these checks (gatectl's own
verification environment strips host env vars, so a real API key is never available here).
"""

from __future__ import annotations

import json

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


class FakeTransport:
    """Records every call it receives and returns programmed responses/errors in order.

    Never touches the network: a plain Python callable test double, matching this card's own
    dependency-injection instructions for ``GatewayEmbeddingProvider``'s ``_transport`` param.
    """

    def __init__(self, responses=None, errors=None):
        self.calls: list[dict] = []
        self._responses = list(responses) if responses is not None else None
        self._errors = list(errors) if errors is not None else None

    def __call__(self, url, body, headers, timeout):
        self.calls.append({"url": url, "body": body, "headers": headers, "timeout": timeout})
        if self._errors:
            error = self._errors.pop(0)
            if error is not None:
                raise error
        if self._responses is not None:
            return self._responses.pop(0)
        payload = json.loads(body.decode("utf-8"))
        data = [{"index": i, "embedding": [float(i), float(i)]} for i in range(len(payload["input"]))]
        return json.dumps({"data": data, "model": payload["model"], "object": "list", "usage": {}}).encode(
            "utf-8"
        )


def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _GATEWAY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _set_required_env(monkeypatch: pytest.MonkeyPatch, api_key: str = "test-key") -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv("FOSS_MCP_EMBEDDING_GATEWAY_ENDPOINT", "https://gateway.example.test/v1/")
    monkeypatch.setenv("FOSS_MCP_EMBEDDING_GATEWAY_API_KEY", api_key)


def _no_sleep(_seconds: float) -> None:
    return None


def test_embed_batches_requests_by_configured_batch_size(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv("FOSS_MCP_EMBEDDING_GATEWAY_BATCH_SIZE", "3")
    transport = FakeTransport()
    provider = GatewayEmbeddingProvider(_transport=transport, _sleep=_no_sleep)

    texts = [f"text-{i}" for i in range(7)]
    vectors = provider.embed(texts)

    assert len(vectors) == 7
    batch_sizes = [len(json.loads(call["body"].decode("utf-8"))["input"]) for call in transport.calls]
    assert batch_sizes == [3, 3, 1]


def test_embed_returns_vectors_in_input_order_even_when_gateway_reorders_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_env(monkeypatch)
    response = json.dumps(
        {
            "data": [
                {"index": 2, "embedding": [2.0, 2.0]},
                {"index": 0, "embedding": [0.0, 0.0]},
                {"index": 1, "embedding": [1.0, 1.0]},
            ],
            "model": "qwen3-embedding-8b",
            "object": "list",
            "usage": {},
        }
    ).encode("utf-8")
    transport = FakeTransport(responses=[response])
    provider = GatewayEmbeddingProvider(_transport=transport, _sleep=_no_sleep)

    vectors = provider.embed(["alpha", "beta", "gamma"])

    assert vectors == [(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)]
    assert len(transport.calls) == 1


def test_transient_error_retries_up_to_bounded_limit_then_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch)
    errors = [
        GatewayTransportError(500, "internal server error"),
        GatewayTransportError(502, "bad gateway"),
        GatewayTransportError(503, "service unavailable"),
    ]
    transport = FakeTransport(errors=errors)
    sleeps: list[float] = []
    provider = GatewayEmbeddingProvider(_transport=transport, _sleep=sleeps.append)

    with pytest.raises(GatewayEmbeddingError):
        provider.embed(["alpha"])

    assert len(transport.calls) == 3
    assert sleeps == [1.0, 2.0]


def test_non_transient_error_raises_immediately_with_no_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch)
    transport = FakeTransport(errors=[GatewayTransportError(400, "malformed request")])
    provider = GatewayEmbeddingProvider(_transport=transport, _sleep=_no_sleep)

    with pytest.raises(GatewayEmbeddingError):
        provider.embed(["alpha"])

    assert len(transport.calls) == 1


def test_budget_cap_blocks_a_subsequent_call_with_no_further_transport_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv("FOSS_MCP_EMBEDDING_GATEWAY_MAX_REQUESTS", "1")
    transport = FakeTransport()
    provider = GatewayEmbeddingProvider(_transport=transport, _sleep=_no_sleep)

    first = provider.embed(["alpha", "beta"])
    assert len(first) == 2
    assert len(transport.calls) == 1

    with pytest.raises(EmbeddingBudgetExceededError):
        provider.embed(["gamma"])

    # The budget was already exhausted by the first call: no further transport call is made.
    assert len(transport.calls) == 1


def test_api_key_never_appears_in_any_raised_exceptions_str(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "SECRET-DO-NOT-LEAK-c0ffee"
    _set_required_env(monkeypatch, api_key=secret)
    transport = FakeTransport(errors=[GatewayTransportError(400, "malformed request")])
    provider = GatewayEmbeddingProvider(_transport=transport, _sleep=_no_sleep)

    with pytest.raises(GatewayEmbeddingError) as excinfo:
        provider.embed(["alpha"])

    assert secret not in str(excinfo.value)

    # The header actually sent carries the key (proving this is a genuine, non-vacuous check -
    # the key really was used - rather than one that would pass even if auth were broken).
    assert transport.calls[0]["headers"]["Authorization"] == f"Bearer {secret}"


def test_missing_endpoint_env_var_raises_clear_error_naming_it(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv("FOSS_MCP_EMBEDDING_GATEWAY_API_KEY", "test-key")

    with pytest.raises(RuntimeError, match="FOSS_MCP_EMBEDDING_GATEWAY_ENDPOINT"):
        GatewayEmbeddingProvider()


def test_missing_api_key_env_var_raises_clear_error_naming_it(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv("FOSS_MCP_EMBEDDING_GATEWAY_ENDPOINT", "https://gateway.example.test/v1/")

    with pytest.raises(RuntimeError, match="FOSS_MCP_EMBEDDING_GATEWAY_API_KEY"):
        GatewayEmbeddingProvider()


def test_zero_argument_construction_works_with_only_required_env_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mirrors ``infra/ingest.py``'s own zero-argument calling convention - the real transport
    is built internally (and never invoked here, since nothing calls ``embed()``)."""
    _set_required_env(monkeypatch)

    provider = GatewayEmbeddingProvider()

    assert provider.dimension == 4096
