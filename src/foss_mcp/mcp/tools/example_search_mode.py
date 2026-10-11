"""example_search_mode: how ``find_examples`` will search a generation, decided without searching.

``find_examples`` always tries the lexical stages first. Only when they find nothing does it
embed the QUERY through the embedding gateway and rank the generation's stored example vectors.
Any failure of that stage (no gateway configuration, an unreachable gateway, no example vectors
in the generation, a dimension mismatch) is swallowed and returns nothing, so a standalone
container without the gateway silently searches lexically only. This module makes that visible:
it decides, from a generation's payload and the gateway configuration, which of two modes
applies, so ``report_index_freshness`` can state it.

The baked example vectors were built by the 4096-dimension gateway model, so no offline
provider can embed a query comparable to them; there is no offline fallback to report.

The gateway mode means CONFIGURED, not reachable. This module makes no network call and never
checks that the gateway answers; it only checks that everything ``find_examples`` needs in order
to TRY the vector stage is present. Configuration is read for presence and shape only: no
environment value is ever copied into a returned reason.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from foss_mcp.indexing.gateway_embedding_provider import (
    _API_KEY_ENV,
    _DEFAULT_DIMENSION,
    _DIMENSION_ENV,
    _ENDPOINT_ENV,
)
from foss_mcp.indexing.vector_index_writer import EXAMPLE_MARKER

EXAMPLE_SEARCH_GATEWAY = "lexical+vector(gateway)"
EXAMPLE_SEARCH_LEXICAL_ONLY = "lexical-only"

REASON_NO_EXAMPLE_VECTORS = "no example vectors in this generation"
REASON_GATEWAY_NOT_CONFIGURED = "embedding gateway not configured"
REASON_DIMENSION_MISMATCH = "configured embedding dimension does not match the stored vectors"
REASON_GATEWAY_CONFIGURED = "example vectors present and the embedding gateway is configured"


def _has_example_vector(vector_index: Mapping[str, Any]) -> bool:
    points = vector_index.get("points") or []
    return any(
        isinstance(point, Mapping) and EXAMPLE_MARKER in str(point.get("text", "")) for point in points
    )


def _is_set(environment: Mapping[str, str], name: str) -> bool:
    return bool(str(environment.get(name, "")).strip())


def _configured_dimension(environment: Mapping[str, str]) -> int | None:
    """The configured dimension (the default when unset or blank), or None when it is not a
    positive integer."""
    raw = str(environment.get(_DIMENSION_ENV, "")).strip()
    if not raw:
        return _DEFAULT_DIMENSION
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def example_search_mode(
    payload: Mapping[str, Any], environment: Mapping[str, str] | None = None
) -> tuple[str, str]:
    """``(mode, reason)`` for a generation *payload*: ``EXAMPLE_SEARCH_GATEWAY`` only when the
    generation holds at least one example vector, the gateway endpoint and API key are both set
    and non-blank, and the configured dimension is a positive integer equal to the stored
    vectors' dimension; otherwise ``EXAMPLE_SEARCH_LEXICAL_ONLY`` with the first failing reason.
    """
    env: Mapping[str, str] = os.environ if environment is None else environment
    vector_index = payload.get("vector_index") or {}
    if not _has_example_vector(vector_index):
        return EXAMPLE_SEARCH_LEXICAL_ONLY, REASON_NO_EXAMPLE_VECTORS
    if not (_is_set(env, _ENDPOINT_ENV) and _is_set(env, _API_KEY_ENV)):
        return EXAMPLE_SEARCH_LEXICAL_ONLY, REASON_GATEWAY_NOT_CONFIGURED
    configured = _configured_dimension(env)
    stored = vector_index.get("dimension")
    if configured is None or isinstance(stored, bool) or configured != stored:
        return EXAMPLE_SEARCH_LEXICAL_ONLY, REASON_DIMENSION_MISMATCH
    return EXAMPLE_SEARCH_GATEWAY, REASON_GATEWAY_CONFIGURED
