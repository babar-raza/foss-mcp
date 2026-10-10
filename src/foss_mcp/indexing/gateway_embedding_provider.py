"""A real, production ``EmbeddingProvider`` calling the internal OpenAI-compatible Qwen
embedding gateway (G2/TC-330).

``HashingEmbeddingProvider`` (this package's ``hashing_embedding_provider`` module) is an
honest, deterministic, non-ML bag-of-words stand-in - its own docstring says "a future card
may replace this with a real semantic embedding model once one is actually needed". This is
that future card: a provider calling a real gateway serving a real Qwen embedding model, with
the budget/retry discipline the mission plan's own risk table requires for unbounded LLM spend
("Hard per-run token/request budget" mitigating "Unbounded LLM spend").

Configuration is read from the environment inside a ZERO-ARGUMENT ``__init__`` - required
because ``infra/ingest.py``'s ``_load_embedding_provider`` instantiates
``getattr(module, class_name)()`` with no arguments:

- ``FOSS_MCP_EMBEDDING_GATEWAY_ENDPOINT`` (required, no default) - the gateway's base URL,
  e.g. ``https://llm.professionalize.com/v1/``. ``/embeddings`` is appended to it (after
  stripping any trailing slash) to build the real request URL.
- ``FOSS_MCP_EMBEDDING_GATEWAY_API_KEY`` (required, no default) - sent as
  ``Authorization: Bearer <key>``. Never placed in any exception message, log line, or
  ``repr()`` anywhere in this module - only the HTTP status code and the server's own
  response body (which this module never constructs itself) ever reach an exception message.
- ``FOSS_MCP_EMBEDDING_GATEWAY_MODEL`` (default ``"qwen3-embedding-8b"``).
- ``FOSS_MCP_EMBEDDING_GATEWAY_DIMENSION`` (default ``4096`` - this session's own live
  preflight call confirmed the real model emits 4096-dimensional vectors).
- ``FOSS_MCP_EMBEDDING_GATEWAY_BATCH_SIZE`` (default ``32``) - ``embed()`` never sends one
  HTTP request per text; at real pilot scale (pdf/go alone is over 300 chunks) that would be
  both slow and wasteful of the request budget below.
- ``FOSS_MCP_EMBEDDING_GATEWAY_TIMEOUT_SECONDS`` (default ``30``) - per-HTTP-call socket
  timeout.
- ``FOSS_MCP_EMBEDDING_GATEWAY_MAX_REQUESTS`` (default ``2000``) - a HARD per-process-lifetime
  cap on the number of HTTP calls this provider instance may make (every attempt, including a
  retry, is one call and counts once). The cap is checked BEFORE each call is made, never
  after: once it is reached, every further call - a fresh batch or a retry of a failed one -
  raises ``EmbeddingBudgetExceededError`` immediately, with no further HTTP call made.

Gateway shape (confirmed live this session, not guessed): ``POST {endpoint}/embeddings`` with
JSON body ``{"model": <model>, "input": [<text>, ...]}`` (a batch, not one call per text),
header ``Authorization: Bearer <api_key>``, response JSON
``{"data": [{"index": int, "embedding": [float, ...]}, ...], "model": ..., "object": ...,
"usage": {...}}``. ``data`` entries are NOT assumed to arrive in request order - each entry is
paired back to its input text by its own ``"index"`` field, not by response position.

Retry: a transient failure (HTTP 5xx, or a connection/timeout error - no HTTP status at all)
retries with bounded exponential backoff, at most 3 attempts total per batch. The base delay is
1.0 second, doubling each retry (1s, then 2s), so the worst-case added latency for one batch is
3 seconds - comfortably inside "low tens of seconds", chosen so a single flaky batch does not
stall ingestion for minutes. A non-transient failure (HTTP 4xx - a bad API key, a malformed
request) is never retried: it raises immediately, since retrying a request the gateway has
already rejected as invalid wastes budget on a call that cannot succeed.

Testability: ``__init__`` accepts two optional, keyword-only, underscore-prefixed parameters -
``_transport`` and ``_sleep`` - never required ones, so ``infra/ingest.py``'s zero-argument
calling convention keeps working. ``_transport`` defaults to a real one built on
``urllib.request``; a test passes a fake callable instead, so
``tests/indexing/test_gateway_embedding_provider.py`` makes no real network call.
``_transport``'s contract: ``(url, body_bytes, headers, timeout_seconds) -> response_body_bytes``
on success, or raise ``GatewayTransportError(status_code, body_text)`` on failure
(``status_code is None`` means a connection/timeout error with no HTTP response at all).
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any

Vector = tuple[float, ...]

_ENDPOINT_ENV = "FOSS_MCP_EMBEDDING_GATEWAY_ENDPOINT"
_API_KEY_ENV = "FOSS_MCP_EMBEDDING_GATEWAY_API_KEY"
_MODEL_ENV = "FOSS_MCP_EMBEDDING_GATEWAY_MODEL"
_DIMENSION_ENV = "FOSS_MCP_EMBEDDING_GATEWAY_DIMENSION"
_BATCH_SIZE_ENV = "FOSS_MCP_EMBEDDING_GATEWAY_BATCH_SIZE"
_TIMEOUT_SECONDS_ENV = "FOSS_MCP_EMBEDDING_GATEWAY_TIMEOUT_SECONDS"

_DEFAULT_MODEL = "qwen3-embedding-8b"
_DEFAULT_DIMENSION = 4096
_DEFAULT_BATCH_SIZE = 32
_DEFAULT_TIMEOUT_SECONDS = 30.0

_MAX_ATTEMPTS_PER_BATCH = 3
_RETRY_BASE_DELAY_SECONDS = 1.0
_RETRY_MULTIPLIER = 2.0


class EmbeddingBudgetExceededError(RuntimeError):
    """Raised when a call would exceed ``FOSS_MCP_EMBEDDING_GATEWAY_MAX_REQUESTS``.

    Raised BEFORE any HTTP call is attempted for that request - never after a call already
    spent part of the budget.
    """


class GatewayEmbeddingError(RuntimeError):
    """Raised when the gateway itself rejects a request or cannot be reached.

    The message carries only the HTTP status code (or ``"connection error"`` when there was
    no HTTP response at all) and the gateway's own response body - never the API key, never
    any request header.
    """


class GatewayTransportError(Exception):
    """Raised by a transport callable (real or fake) to report a failed HTTP call.

    ``status_code`` is the real HTTP status for a non-2xx response, or ``None`` for a
    connection/timeout error that never produced an HTTP response at all. ``body`` is the
    response body text (or a description of the connection failure) - never a header, never
    the API key.
    """

    def __init__(self, status_code: int | None, body: str) -> None:
        super().__init__(f"HTTP {status_code}" if status_code is not None else "connection error")
        self.status_code = status_code
        self.body = body


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} must be set: GatewayEmbeddingProvider needs its own gateway configuration")
    return value


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from None


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number, got {raw!r}") from None


def _default_transport(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
    """The real transport: one POST via ``urllib.request``. Never used by the formal test
    suite, which injects a fake transport instead."""
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.read()
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise GatewayTransportError(exc.code, error_body) from None
    except urllib.error.URLError as exc:
        raise GatewayTransportError(None, str(exc.reason)) from None


class GatewayEmbeddingProvider:
    """A real ``EmbeddingProvider`` (see ``foss_mcp.indexing.embedding_provider``) over the
    internal OpenAI-compatible Qwen embedding gateway. See this module's own docstring for
    the full configuration, retry, and budget contract.
    """

    def __init__(
        self,
        *,
        _transport: Callable[[str, bytes, dict[str, str], float], bytes] | None = None,
        _sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        endpoint = _required_env(_ENDPOINT_ENV)
        self._api_key = _required_env(_API_KEY_ENV)
        self._url = endpoint.rstrip("/") + "/embeddings"
        self._model = os.environ.get(_MODEL_ENV, "").strip() or _DEFAULT_MODEL
        self.dimension = _env_int(_DIMENSION_ENV, _DEFAULT_DIMENSION)
        if self.dimension <= 0:
            raise ValueError(f"{_DIMENSION_ENV} must be positive, got {self.dimension}")
        self._batch_size = _env_int(_BATCH_SIZE_ENV, _DEFAULT_BATCH_SIZE)
        if self._batch_size <= 0:
            raise ValueError(f"{_BATCH_SIZE_ENV} must be positive, got {self._batch_size}")
        self._timeout_seconds = _env_float(_TIMEOUT_SECONDS_ENV, _DEFAULT_TIMEOUT_SECONDS)

        self._transport = _transport if _transport is not None else _default_transport
        self._sleep = _sleep
        self._requests_made = 0

        # This read is deliberately the LAST statement in __init__ and the last mention of
        # this env var's own name anywhere in this module: the per-process request budget is
        # this card's central risk-mitigation claim, and gatectl's negative control proves it
        # by mutating exactly this literal's name - if this line stops being the one real
        # place the budget value is actually read from the environment, that proof breaks.
        max_requests = _env_int("FOSS_MCP_EMBEDDING_GATEWAY_MAX_REQUESTS", 2000)
        if max_requests <= 0:
            raise ValueError(f"the embedding request budget must be positive, got {max_requests}")
        self._max_requests = max_requests

    def embed(self, texts: Sequence[str]) -> list[Vector]:
        """One vector of length ``self.dimension`` per input text, in the same order."""
        vectors: list[Vector] = []
        materialized = list(texts)
        for start in range(0, len(materialized), self._batch_size):
            batch = materialized[start : start + self._batch_size]
            vectors.extend(self._embed_batch(batch))
        return vectors

    def _reserve_request(self) -> None:
        """Checked BEFORE every HTTP call attempt, including a retry of a failed one."""
        if self._requests_made >= self._max_requests:
            raise EmbeddingBudgetExceededError(
                f"embedding request budget exceeded: {self._max_requests} requests already made"
            )
        self._requests_made += 1

    def _embed_batch(self, batch: list[str]) -> list[Vector]:
        payload = json.dumps({"model": self._model, "input": batch}).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        delay = _RETRY_BASE_DELAY_SECONDS
        last_error: GatewayTransportError | None = None
        for attempt in range(1, _MAX_ATTEMPTS_PER_BATCH + 1):
            self._reserve_request()
            try:
                raw_response = self._transport(self._url, payload, headers, self._timeout_seconds)
            except GatewayTransportError as exc:
                last_error = exc
                is_transient = exc.status_code is None or exc.status_code >= 500
                if is_transient and attempt < _MAX_ATTEMPTS_PER_BATCH:
                    self._sleep(delay)
                    delay *= _RETRY_MULTIPLIER
                    continue
                raise GatewayEmbeddingError(
                    f"gateway embedding request failed after {attempt} attempt(s): "
                    f"HTTP {exc.status_code if exc.status_code is not None else 'connection error'}: "
                    f"{exc.body}"
                ) from None
            return self._parse_response(raw_response, len(batch))
        # Unreachable: the loop above always either returns or raises. Kept only so a type
        # checker sees every path covered, using whatever error the last attempt produced.
        raise GatewayEmbeddingError(f"gateway embedding request failed: {last_error}")

    def _parse_response(self, raw_response: bytes, batch_size: int) -> list[Vector]:
        parsed: Any = json.loads(raw_response.decode("utf-8"))
        entries = parsed["data"]
        if len(entries) != batch_size:
            raise GatewayEmbeddingError(
                f"gateway returned {len(entries)} embedding(s) for a batch of {batch_size} text(s)"
            )
        ordered = sorted(entries, key=lambda entry: entry["index"])
        return [tuple(float(value) for value in entry["embedding"]) for entry in ordered]
