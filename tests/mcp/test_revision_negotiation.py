"""Nearest-supported-or-min MCP protocol revision negotiation, plus this project's own
explicit missing-protocol-version policy (recorded in docs/DECISION_LOG.md).
"""

from __future__ import annotations

import pytest

from foss_mcp.mcp.revision_negotiation import (
    FallbackReason,
    InvalidRevisionFormatError,
    MissingProtocolVersionError,
    negotiate_revision,
)

SUPPORTED = ("2024-11-05", "2025-03-26", "2025-06-18")


def test_an_exact_match_is_negotiated_with_no_fallback() -> None:
    result = negotiate_revision(requested_revision="2025-03-26", supported_revisions=SUPPORTED)
    assert result.negotiated_revision == "2025-03-26"
    assert result.fallback_applied is False
    assert result.fallback_reason == FallbackReason.NONE


def test_a_revision_between_two_supported_ones_falls_back_to_the_nearer_not_exceeding_one() -> None:
    result = negotiate_revision(requested_revision="2025-01-01", supported_revisions=SUPPORTED)
    assert result.negotiated_revision == "2024-11-05"
    assert result.fallback_applied is True
    assert result.fallback_reason == FallbackReason.UNKNOWN_REVISION


def test_a_revision_newer_than_everything_supported_falls_back_to_the_newest_supported() -> None:
    result = negotiate_revision(requested_revision="2099-01-01", supported_revisions=SUPPORTED)
    assert result.negotiated_revision == "2025-06-18"


def test_a_revision_older_than_everything_supported_falls_back_to_the_minimum_supported() -> None:
    """'nearest-supported-OR-MIN': a client can never be handed something even older than
    the oldest revision this server can speak."""
    result = negotiate_revision(requested_revision="2020-01-01", supported_revisions=SUPPORTED)
    assert result.negotiated_revision == "2024-11-05"
    assert result.fallback_reason == FallbackReason.BELOW_MIN_SUPPORTED


def test_a_missing_protocol_version_is_an_explicit_rejection_not_a_silent_default() -> None:
    """This project's own decision, not inherited: the reference system silently negotiated
    a missing protocolVersion to its own default. initialize requires one."""
    with pytest.raises(MissingProtocolVersionError):
        negotiate_revision(requested_revision=None, supported_revisions=SUPPORTED)
    with pytest.raises(MissingProtocolVersionError):
        negotiate_revision(requested_revision="   ", supported_revisions=SUPPORTED)


def test_an_unparseable_revision_is_rejected_not_silently_defaulted() -> None:
    with pytest.raises(InvalidRevisionFormatError):
        negotiate_revision(requested_revision="not-a-date", supported_revisions=SUPPORTED)
