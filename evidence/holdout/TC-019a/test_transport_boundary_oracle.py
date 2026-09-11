"""HOLDOUT oracle for TC-019a — supervisor-authored, not named by the card.

Three of the four transport MUSTs are satisfied by a function that rejects
*everything*, so the load-bearing assertion here is the fourth: a well-formed
request must still get through. Without it, "rejects a bad Origin", "rejects a
missing version" and "rejects an invalid version" are all passed by
`return "no"`.

Two further things a card's own suite tends not to probe:

- **Header case.** HTTP header names are case-insensitive. A dict lookup keyed on
  one exact spelling passes every test written with that same spelling and fails
  against real clients, which send whatever casing they like. `MCP-Protocol-Version`
  and `mcp-protocol-version` must behave identically.
- **Schema leakage.** Each tool wrapper closes over the deployment's own store and
  scope. If either reached a tool's public JSON schema, a client could supply it
  — which is exactly the client-controlled-scope defect the whole isolation model
  exists to prevent, reintroduced through the tool surface instead of a header.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

WT = pathlib.Path(__file__).resolve().parents[3]
for p in (str(WT), str(WT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from foss_mcp.mcp.transport_security import reject_request  # noqa: E402

ALLOWED = ("https://client.example",)
GOOD_VERSION = "2025-06-18"


def headers(**kw):
    """Build a header mapping, dropping None so 'absent' is expressible."""
    return {k.replace("_", "-"): v for k, v in kw.items() if v is not None}


# --------------------------------------------------------------- the MUSTs
def test_a_missing_protocol_version_is_rejected():
    """The SDK does NOT do this. mcp 2.2.0's streamable_http falls through to
    DEFAULT_NEGOTIATED_VERSION when the header is absent, so this rejection can
    only come from our own middleware - verified in the installed SDK source."""
    reason = reject_request(headers(Origin=ALLOWED[0]), allowed_origins=ALLOWED)
    assert reason is not None, "a request with no MCP-Protocol-Version was allowed through"
    assert "protocol" in reason.lower() or "version" in reason.lower(), (
        f"the rejection reason does not name the failing MUST: {reason!r}"
    )


@pytest.mark.parametrize("bad", ["not-a-date", "2026-13-99", "latest", "2026/06/18", ""])
def test_an_unparseable_protocol_version_is_rejected(bad):
    reason = reject_request(headers(Origin=ALLOWED[0], MCP_Protocol_Version=bad), allowed_origins=ALLOWED)
    assert reason is not None, f"an unparseable protocol version was allowed: {bad!r}"


def test_an_unapproved_origin_is_rejected():
    reason = reject_request(
        headers(Origin="https://evil.example", MCP_Protocol_Version=GOOD_VERSION),
        allowed_origins=ALLOWED,
    )
    assert reason is not None, "a request from an unapproved Origin was allowed through"
    assert "origin" in reason.lower(), f"the reason does not name Origin: {reason!r}"


# ------------------------------------------------------- the anti-vacuity MUST
def test_a_well_formed_request_is_allowed():
    """The assertion that makes the other three mean anything.

    `reject_request = lambda *a, **k: "no"` passes every rejection test above
    while making the server answer nothing at all.
    """
    reason = reject_request(
        headers(Origin=ALLOWED[0], MCP_Protocol_Version=GOOD_VERSION), allowed_origins=ALLOWED
    )
    assert reason is None, f"a valid request was rejected: {reason!r}"


def test_an_absent_origin_is_allowed_but_a_wrong_one_is_not():
    """Pins the card's deliberate decision so it cannot drift silently either way.

    Absent Origin is allowed: non-browser clients never send one, and DNS
    rebinding - the threat this check exists for - is browser-specific. That
    leniency must NOT extend to an Origin that is present and wrong.
    """
    assert reject_request(headers(MCP_Protocol_Version=GOOD_VERSION), allowed_origins=ALLOWED) is None, (
        "an absent Origin was rejected; non-browser clients legitimately omit it"
    )

    assert (
        reject_request(
            headers(Origin="https://evil.example", MCP_Protocol_Version=GOOD_VERSION),
            allowed_origins=ALLOWED,
        )
        is not None
    ), "absent-Origin leniency leaked into allowing a WRONG Origin"


# ------------------------------------------------------------- header casing
@pytest.mark.parametrize(
    "version_key", ["MCP-Protocol-Version", "mcp-protocol-version", "Mcp-Protocol-Version"]
)
def test_header_lookup_is_case_insensitive_for_the_version(version_key):
    """Real clients send whatever casing they like; HTTP says that is fine."""
    allowed = reject_request({"Origin": ALLOWED[0], version_key: GOOD_VERSION}, allowed_origins=ALLOWED)
    assert allowed is None, (
        f"a valid request was rejected because the version header was spelled {version_key!r} - "
        f"HTTP header names are case-insensitive"
    )


@pytest.mark.parametrize("origin_key", ["Origin", "origin", "ORIGIN"])
def test_header_lookup_is_case_insensitive_for_origin(origin_key):
    reason = reject_request(
        {origin_key: "https://evil.example", "MCP-Protocol-Version": GOOD_VERSION},
        allowed_origins=ALLOWED,
    )
    assert reason is not None, (
        f"a hostile Origin spelled {origin_key!r} slipped past the check - case-sensitive header "
        f"lookup is a bypass, not a style issue"
    )


# ------------------------------------------------------------ schema leakage
def test_no_tool_schema_exposes_the_deployment_store_or_scope():
    """The isolation model, re-checked at the tool surface.

    Each wrapper closes over the server's own store and scope. If either appeared
    as a tool parameter, a client could supply it - reintroducing
    client-controlled scope through the tool surface instead of a header.
    """
    import json

    from foss_mcp.mcp.routing import DeploymentConfig, resolve_scope
    from foss_mcp.mcp.server import (
        ProductReferenceInputs,
        _build_tool_registry,
        _default_manifest_store,
        schema_for,
    )

    scope = resolve_scope(DeploymentConfig(family="pdf", platform="net"), request=None)
    registry = _build_tool_registry(_default_manifest_store(), scope, ProductReferenceInputs(), ())
    assert len(registry) == 9, f"expected all nine tools registered, got {sorted(registry)}"

    for name, handler in registry.items():
        schema = schema_for(handler)
        assert schema, f"{name} has an empty JSON schema"
        rendered = json.dumps(schema).lower()
        for forbidden in ("store", "scope", "manifest", "deployment"):
            assert forbidden not in rendered, (
                f"{name}'s public schema exposes {forbidden!r}: {rendered[:250]}. A client able to "
                f"supply it would control its own scope."
            )
