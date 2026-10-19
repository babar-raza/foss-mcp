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

import ipaddress
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS

from foss_mcp.mcp.revision_negotiation import (
    InvalidRevisionFormatError,
    MissingProtocolVersionError,
    negotiate_revision,
)

ORIGIN_HEADER = "origin"
HOST_HEADER = "host"
MCP_PROTOCOL_VERSION_HEADER = "mcp-protocol-version"

# G3/TC-564 (RD3-125): the Host header is validated, not merely read. A browser reaching this server
# through DNS rebinding sends the ATTACKER's registered name as Host, so the default policy accepts only
# names that no outsider can register: the loopback names, any IP literal, a single-label name (the
# container, Compose service or pod name a peer on the network reaches it by), and the in-cluster
# service domains. Anything else is refused unless FOSS_MCP_ALLOWED_HOSTS lists it. The error kinds below
# say which MUST failed; they select the HTTP status and ride in the rejection log line.
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
CLUSTER_HOST_SUFFIXES = (".svc", ".svc.cluster.local")
REJECT_ORIGIN = "origin"
REJECT_HOST = "host"
REJECT_PROTOCOL_VERSION = "protocol_version"
# JSON-RPC 2.0 "Invalid Request": every transport rejection shares the envelope the SDK itself answers with.
JSONRPC_INVALID_REQUEST = -32600
HOST_REJECTION_STATUS = 421
DEFAULT_REJECTION_STATUS = 400

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


@dataclass(frozen=True)
class Rejection:
    """Why one request was refused: ``kind`` is ``origin``, ``host`` or ``protocol_version``."""

    kind: str
    reason: str

    @property
    def status(self) -> int:
        return HOST_REJECTION_STATUS if self.kind == REJECT_HOST else DEFAULT_REJECTION_STATUS


def host_name_of(host_header: str) -> str:
    """The lower-case host name of a ``Host`` header value: the port, the brackets of an IPv6 literal and
    a trailing dot are removed."""
    value = host_header.strip().lower()
    if value.startswith("["):
        end = value.find("]")
        name = value[1:end] if end != -1 else value[1:]
    elif value.count(":") == 1:
        name = value.split(":", 1)[0]
    else:
        name = value
    return name.rstrip(".")


def host_is_allowed(host_header: str | None, allowed_hosts: Iterable[str] = ()) -> bool:
    """Whether a ``Host`` header value passes the policy described at ``LOOPBACK_HOSTS``.

    An absent header is allowed (HTTP/1.0 clients and probes; DNS rebinding needs a browser, which always
    sends one). ``allowed_hosts`` holds exact names, ``*.suffix`` / ``.suffix`` patterns and ``*`` (any)."""
    if host_header is None:
        return True
    name = host_name_of(host_header)
    if not name:
        return False
    if name in LOOPBACK_HOSTS or "." not in name:
        # A name with no dot is a single label: it cannot be a registrable public domain.
        return True
    try:
        ipaddress.ip_address(name)
    except ValueError:
        pass
    else:
        return True
    if name.endswith(CLUSTER_HOST_SUFFIXES):
        return True
    for entry in allowed_hosts:
        pattern = entry.strip().lower().rstrip(".")
        if not pattern:
            continue
        if pattern in ("*", name):
            return True
        if pattern.startswith("*.") and name.endswith(pattern[1:]):
            return True
        if pattern.startswith(".") and name.endswith(pattern):
            return True
    return False


def rejection_body(reason: str) -> dict[str, Any]:
    """The ONE body shape of a transport rejection: a JSON-RPC 2.0 error with a null id, the same shape the
    MCP SDK answers a bad session or a parse failure with."""
    return {"jsonrpc": "2.0", "id": None, "error": {"code": JSONRPC_INVALID_REQUEST, "message": reason}}


def reject_request(
    headers: Mapping[str, str],
    *,
    allowed_origins: Iterable[str] = (),
    supported_revisions: Iterable[str] = SUPPORTED_PROTOCOL_REVISIONS,
    allowed_hosts: Iterable[str] = (),
) -> str | None:
    """The reason string of ``reject_request_detail``, or ``None`` to allow the request."""
    rejection = reject_request_detail(
        headers,
        allowed_origins=allowed_origins,
        supported_revisions=supported_revisions,
        allowed_hosts=allowed_hosts,
    )
    return rejection.reason if rejection is not None else None


def reject_request_detail(
    headers: Mapping[str, str],
    *,
    allowed_origins: Iterable[str] = (),
    supported_revisions: Iterable[str] = SUPPORTED_PROTOCOL_REVISIONS,
    allowed_hosts: Iterable[str] = (),
) -> Rejection | None:
    """A ``Rejection`` (its ``reason`` is human-readable) to reject this request, or ``None`` to allow it.

    Checked in this order - Origin, Host (G3/TC-564), then protocol version - so the reason string
    always names the FIRST MUST the request actually failed:

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
        return Rejection(REJECT_ORIGIN, f"origin {origin!r} is not in the allowed origin list")

    host = _header(headers, HOST_HEADER)
    if not host_is_allowed(host, allowed_hosts):
        return Rejection(REJECT_HOST, f"host {host!r} is not in the allowed host list")

    protocol_version = _header(headers, MCP_PROTOCOL_VERSION_HEADER)
    if protocol_version is None:
        return None

    try:
        result = negotiate_revision(
            requested_revision=protocol_version, supported_revisions=supported_revisions
        )
    except MissingProtocolVersionError:
        return Rejection(REJECT_PROTOCOL_VERSION, "missing MCP-Protocol-Version header")
    except InvalidRevisionFormatError:
        return Rejection(
            REJECT_PROTOCOL_VERSION, f"invalid MCP-Protocol-Version header: {protocol_version!r}"
        )

    if result.fallback_applied:
        return Rejection(
            REJECT_PROTOCOL_VERSION,
            f"unsupported MCP-Protocol-Version header: {protocol_version!r} is well-formed but "
            "not a revision this server declares support for",
        )

    return None
