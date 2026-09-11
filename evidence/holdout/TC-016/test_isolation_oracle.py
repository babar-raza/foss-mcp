"""HOLDOUT oracle for TC-016 — supervisor-authored, not named by the card.

Two things this card claims that its own checks cannot establish.

**Scope isolation.** The reference system's confirmed defect was an
unauthenticated endpoint trusting a client-supplied `x-tenant-id` header. A test
that passes one hostile shape and asserts it is ignored proves very little — the
implementation might simply not know about *that* shape. So this oracle attacks
`resolve_scope` with a spread of adversarial request objects: dicts, attribute
carriers, nested structures, and a mapping whose keys are exactly the fields
`Scope` itself uses. If any of them can move the answer, routing is
client-influenced and the isolation model is gone.

**The server actually loads.** TC-016's checks exercise `routing.py` and
`revision_negotiation.py` only — neither needs the MCP SDK. `server.py`, the file
the card is nominally about, was never imported by anything, because the SDK was
not pinned when the card ran. So the card could have shipped a `server.py` that
does not even import. It now can be checked, and is.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

WT = pathlib.Path(__file__).resolve().parents[3]
SRC = WT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from foss_mcp.mcp.revision_negotiation import (  # noqa: E402
    InvalidRevisionFormatError,
    MissingProtocolVersionError,
    negotiate_revision,
)
from foss_mcp.mcp.routing import DeploymentConfig, resolve_scope  # noqa: E402

DEPLOYED = DeploymentConfig(family="pdf", platform="net")


class _AttrRequest:
    """A request that answers attribute access for every plausible scope field."""

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


HOSTILE_REQUESTS = [
    None,
    {},
    {"family": "cells", "platform": "python"},
    {"scope": "cells/python"},
    {"source_kind": "furnished"},
    {"headers": {"x-tenant-id": "cells", "x-scope": "cells/python"}},
    {"params": {"arguments": {"family": "slides", "platform": "java"}}},
    _AttrRequest(family="cells", platform="python", source_kind="furnished"),
    _AttrRequest(scope="cells/python"),
    [("family", "cells"), ("platform", "python")],
    "family=cells&platform=python",
]


@pytest.mark.parametrize("request_obj", HOSTILE_REQUESTS, ids=range(len(HOSTILE_REQUESTS)))
def test_no_request_shape_can_move_the_scope(request_obj):
    """Product identity comes from deployment identity alone. Every one of these
    is a real shape a client could send."""
    scope = resolve_scope(DEPLOYED, request_obj)
    assert scope.family == "pdf", f"request moved family to {scope.family!r}"
    assert scope.platform == "net", f"request moved platform to {scope.platform!r}"
    rendered = repr(scope)
    for leaked in ("cells", "python", "slides", "java", "tenant"):
        assert leaked not in rendered, f"a client-supplied value reached the resolved scope: {rendered}"


def test_scope_is_a_pure_function_of_deployment_config():
    """Anti-vacuity: resolve_scope must still DEPEND on its deployment config.

    A function returning a hardcoded pdf/net scope would pass every test above
    while being entirely broken for the other six pilots.
    """
    other = resolve_scope(DeploymentConfig(family="slides", platform="python"), {})
    assert (other.family, other.platform) == ("slides", "python"), (
        "resolve_scope ignores its deployment config too - it is not isolating, it is hardcoded"
    )


# --------------------------------------------------------------------- protocol
# Keyword-only, and the supported set is supplied by the caller rather than being
# module state - taken from the real signature, not guessed.
SUPPORTED = ("2024-11-05", "2025-06-18", "2025-11-25")


def negotiate(requested):
    return negotiate_revision(requested_revision=requested, supported_revisions=SUPPORTED)


def test_a_missing_protocol_version_is_rejected_not_defaulted():
    """The reference system quietly negotiated to its own primary revision when a
    client sent none. The spec makes protocolVersion REQUIRED, so absence is
    malformed input, not a permissive default."""
    for missing in (None, "", "   "):
        with pytest.raises(MissingProtocolVersionError):
            negotiate(missing)


def test_an_unparseable_revision_is_rejected_not_smoothed_over():
    for junk in ("not-a-date", "2026-13-99", "latest", "2026/06/18"):
        with pytest.raises(InvalidRevisionFormatError):
            negotiate(junk)


def test_negotiation_is_nearest_supported_or_min_and_never_exceeds_the_request():
    """Anti-vacuity for the algorithm: rejecting everything would satisfy the two
    tests above while negotiating nothing at all."""
    exact = negotiate("2025-06-18")
    assert exact.negotiated_revision == "2025-06-18", (
        f"an exactly-supported revision was not honoured: {exact}"
    )
    assert exact.fallback_applied is False

    below = negotiate("2001-01-01")
    assert below.negotiated_revision == "2024-11-05", (
        f"a below-minimum request must fall back to the MINIMUM supported, got {below}"
    )

    between = negotiate("2025-08-01")
    assert between.negotiated_revision == "2025-06-18", (
        f"negotiated {between.negotiated_revision!r}, which exceeds what the client asked for"
    )

    future = negotiate("2099-01-01")
    assert future.negotiated_revision == "2025-11-25", (
        "a future request must settle on the highest supported, never invent one"
    )


# ------------------------------------------------------------------------ server
def test_server_module_actually_loads_and_constructs():
    """The card's own checks never imported this file.

    They exercise routing and negotiation, neither of which needs the SDK, so
    until the SDK was pinned nothing established that server.py was even
    syntactically loadable against the real API.
    """
    from foss_mcp.mcp.server import create_server

    server = create_server(DEPLOYED)
    assert server is not None
    assert hasattr(server, "create_initialization_options"), (
        "the constructed object is not a real MCP SDK server"
    )
    server.create_initialization_options()
