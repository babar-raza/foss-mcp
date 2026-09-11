"""MCP protocol revision negotiation: nearest-supported-or-min.

The negotiation ALGORITHM below is ported (structure only) from the reference system's own
``negotiate_mcp_revision``: an exact match wins outright; a requested revision older than
every revision this server supports falls back to the MINIMUM supported revision, never
something even older than this server can speak; a requested revision between two supported
ones, or newer than every supported one, falls back to the NEAREST supported revision that
does not exceed it.

MISSING PROTOCOL VERSION - this project's own explicit decision, not inherited. The reference
system treated a client that sent no ``protocolVersion`` at all as a non-fallback case and
silently negotiated to its own primary/default revision. Per the MCP specification,
``initialize``'s ``protocolVersion`` is a REQUIRED field: a request missing it is a malformed
request, not a permissive default case, so this module raises ``MissingProtocolVersionError``
instead of silently substituting a default. An unparseable (not ``YYYY-MM-DD``) revision string
is treated the same way, for the same reason: it is not a value this server can reason about
at all, so it is rejected rather than smoothed over.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from enum import Enum


class RevisionNegotiationError(Exception):
    """Base class for every error this module raises."""


class MissingProtocolVersionError(RevisionNegotiationError):
    """``initialize`` arrived with no ``protocolVersion`` at all.

    This project's explicit decision (recorded in ``docs/DECISION_LOG.md``): the MCP
    specification requires ``initialize`` to carry a ``protocolVersion``, so a request missing
    it is rejected outright rather than silently negotiated to a default - the permissive
    behaviour the reference system's own negotiation chose instead.
    """


class InvalidRevisionFormatError(RevisionNegotiationError):
    """A revision string is not a valid ``YYYY-MM-DD`` MCP protocol revision."""


class FallbackReason(str, Enum):
    NONE = "none"
    UNKNOWN_REVISION = "unknown_revision"
    BELOW_MIN_SUPPORTED = "below_min_supported"


@dataclass(frozen=True)
class RevisionNegotiationResult:
    requested_revision: str
    negotiated_revision: str
    fallback_applied: bool
    fallback_reason: FallbackReason


def _parse_revision(revision: str) -> date:
    try:
        year_str, month_str, day_str = revision.split("-")
        return date(int(year_str), int(month_str), int(day_str))
    except (ValueError, TypeError) as exc:
        raise InvalidRevisionFormatError(f"invalid MCP revision format: {revision!r}") from exc


def _normalize_supported(revisions: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in revisions:
        revision = raw.strip()
        if not revision or revision in seen:
            continue
        _parse_revision(revision)
        seen.add(revision)
        normalized.append(revision)
    if not normalized:
        raise ValueError("supported_revisions must not be empty")
    return tuple(sorted(normalized, key=_parse_revision))


def negotiate_revision(
    *, requested_revision: str | None, supported_revisions: Iterable[str]
) -> RevisionNegotiationResult:
    """Nearest-supported-or-min negotiation between ``requested_revision`` and
    ``supported_revisions``.

    Raises ``MissingProtocolVersionError`` if ``requested_revision`` is ``None`` or blank, and
    ``InvalidRevisionFormatError`` if it is not a parseable ``YYYY-MM-DD`` revision - see the
    module docstring for why both are explicit rejections here, never a silent default.
    """
    if requested_revision is None or not requested_revision.strip():
        raise MissingProtocolVersionError("initialize requires a client protocol version")

    supported = _normalize_supported(supported_revisions)
    requested = requested_revision.strip()

    if requested in supported:
        return RevisionNegotiationResult(requested, requested, False, FallbackReason.NONE)

    requested_date = _parse_revision(requested)
    supported_dates = [(_parse_revision(revision), revision) for revision in supported]
    min_date, min_revision = supported_dates[0]
    if requested_date < min_date:
        return RevisionNegotiationResult(requested, min_revision, True, FallbackReason.BELOW_MIN_SUPPORTED)

    eligible = [revision for revision_date, revision in supported_dates if revision_date <= requested_date]
    negotiated = eligible[-1] if eligible else min_revision
    return RevisionNegotiationResult(requested, negotiated, True, FallbackReason.UNKNOWN_REVISION)
