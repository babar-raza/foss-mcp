"""Origin and protocol-version rejection at the transport boundary - enforced BEFORE a
request ever reaches tool dispatch, so a protocol-level rejection can never be confused with a
tool reporting no match (a different shape entirely: a plain HTTP error here, a normal
``CallToolResult`` there).

``reject_request`` is the ONE place both MUSTs are decided, and the stable contract later
cards (and this card's own falsifier) bind to: a client-controlled Origin header is a
confirmed reference-system defect (it read the header and validated nothing), and mcp==2.2.0's
own default handling lets a MISSING ``MCP-Protocol-Version`` through unchallenged - verified
directly in ``mcp.server.streamable_http_manager``, which only routes a PRESENT-but-invalid
value to its own rejection path. Both gaps are closed here.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS

from foss_mcp.mcp.revision_negotiation import (
    InvalidRevisionFormatError,
    MissingProtocolVersionError,
    negotiate_revision,
)

ORIGIN_HEADER = "origin"
MCP_PROTOCOL_VERSION_HEADER = "mcp-protocol-version"

# The MCP revisions this server accepts a client's request to be shaped as. This header check's
# only legitimate job is to confirm consistency with what the installed SDK's own `initialize`
# handler already negotiated and already answered to the client - that handler is private, in
# `mcp.server.runner`, with no override hook, so this project does not itself decide which
# revisions a handshake can produce. SUPPORTED_PROTOCOL_REVISIONS must therefore always match
# what that handler can actually negotiate (`HANDSHAKE_PROTOCOL_VERSIONS`, not the broader
# `KNOWN_PROTOCOL_VERSIONS`, which also includes "2026-07-28", a non-handshake stateless-envelope
# revision never reachable via initialize) - never an independently narrower, hand-maintained
# allow-list that can drift behind the SDK's own handshake set, as it did before this fix.
SUPPORTED_PROTOCOL_REVISIONS: tuple[str, ...] = HANDSHAKE_PROTOCOL_VERSIONS


def _header(headers: Mapping[str, str], name: str) -> str | None:
    """Case-insensitive header lookup - HTTP header names are never case-sensitive."""
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None


def reject_request(
    headers: Mapping[str, str],
    *,
    allowed_origins: Iterable[str] = (),
    supported_revisions: Iterable[str] = SUPPORTED_PROTOCOL_REVISIONS,
) -> str | None:
    """A human-readable reason to reject this request, or ``None`` to allow it.

    Checked in this order - Origin, then protocol version - so the reason string always names
    the FIRST MUST the request actually failed:

    - an Origin header that is present but not in ``allowed_origins`` is rejected (an absent
      Origin is not rejected on its own: non-browser clients legitimately never send one, and
      DNS rebinding - the attack this check exists for - is a browser-specific threat).
    - an ABSENT ``MCP-Protocol-Version`` header is ALLOWED THROUGH: it is a Streamable HTTP
      transport-layer convention used only on requests made AFTER a protocol version has been
      negotiated - a genuine client's very first ``initialize`` request never carries it yet
      (the client has not negotiated a version, or predates the header's existence entirely).
      That request's REQUIRED protocol version lives in the JSON-RPC body's own
      ``initialize.params.protocolVersion`` field instead, which the mcp SDK's own initialize
      handler validates independently once it parses the body - this function has no business
      re-enforcing that same MUST a second time, against the wrong location, before the body is
      even read.
    - a PRESENT-but-unparseable ``MCP-Protocol-Version`` header is still rejected: once a client
      sends the header at all, it must be a value this server can actually reason about.
    - a PRESENT, well-formed ``MCP-Protocol-Version`` header naming a revision outside
      ``supported_revisions`` is ALSO rejected: ``negotiate_revision`` had to fall back to its
      nearest-supported-or-min policy rather than match one of ``supported_revisions`` exactly.
      ``supported_revisions`` (``SUPPORTED_PROTOCOL_REVISIONS`` by default) tracks the installed
      MCP SDK's own ``mcp_types.version.HANDSHAKE_PROTOCOL_VERSIONS`` - the exact set its private,
      unoverridable ``initialize`` handler can negotiate and already answered to the client - so
      this check's only legitimate job is confirming the header is CONSISTENT with that prior
      answer, never an independent, narrower claim about what this project itself supports.

    Returns ``None`` only when the Origin check passes AND the protocol-version header is either
    absent or an EXACT match against ``supported_revisions`` - any negotiation fallback, below-
    minimum or nearest-unknown alike, is a rejection here, never a pass-through.
    """
    origin = _header(headers, ORIGIN_HEADER)
    allowed = set(allowed_origins)
    if origin is not None and origin not in allowed:
        return f"origin {origin!r} is not in the allowed origin list"

    protocol_version = _header(headers, MCP_PROTOCOL_VERSION_HEADER)
    if protocol_version is None:
        return None

    try:
        result = negotiate_revision(
            requested_revision=protocol_version, supported_revisions=supported_revisions
        )
    except MissingProtocolVersionError:
        return "missing MCP-Protocol-Version header"
    except InvalidRevisionFormatError:
        return f"invalid MCP-Protocol-Version header: {protocol_version!r}"

    if result.fallback_applied:
        return (
            f"unsupported MCP-Protocol-Version header: {protocol_version!r} is well-formed but "
            "not a revision this server declares support for"
        )

    return None
