"""The state of one optional serving sidecar, and the words that state is reported in (G3/TC-507, TC-564).

A sidecar (the product-reference file, the recent-releases file) is in exactly one of three states
when the serving container looks at it. ``infra/serve_http.py`` decides the state; the two tools that
read the sidecars report it, and a failed fetch is never reported as an absence.

G3/TC-564 (RD3-120): an ABSENT sidecar used to be reported as an empty, "complete" answer: a deployment
whose releases file was simply missing told an agent "this product has no releases" (pdf/go really has
12), with no log line and a green ``/readyz``. ``SIDECAR_STATE_WARNINGS`` is the one switch for the
honest behaviour: while it is ``True`` a missing sidecar is logged at WARNING, appears in ``/readyz``
``warnings``, is counted in ``/metrics``, and ``list_recent_changes`` answers ``unavailable`` with
``RELEASES_NOT_LOADED_MESSAGE`` instead of an empty list. While it is ``False`` the server behaves
exactly as it did before the card (silent, "complete answer" note).
"""

from __future__ import annotations

SIDECAR_STATE_WARNINGS = True

SIDECAR_PRESENT = "present"
SIDECAR_NOT_PRESENT = "not_present"
SIDECAR_FETCH_FAILED = "fetch_failed"

FETCH_FAILED_REASON = (
    "the product reference could not be fetched during ingestion; retry later; "
    "this is not a statement that the repository lacks it"
)
RELEASES_FETCH_FAILED_MESSAGE = (
    "Release information could not be fetched during ingestion. Retry later; "
    "this is not a statement that the product has no releases."
)
RELEASES_NOT_LOADED_MESSAGE = (
    "Release information is not loaded on this server: its recent-releases file was not found. "
    "This is not a statement that the product has no releases; the operator must provide the file "
    "(a sidecar added later is picked up on the next request)."
)


class SidecarFetchFailedError(RuntimeError):
    """The sidecar a tool reads could not be fetched or is unusable. Not a ``ValueError``, so it is
    never classified as an invalid argument."""


class SidecarNotLoadedError(RuntimeError):
    """The sidecar a tool reads is not on this server (G3/TC-564). Answered as ``unavailable``, never
    as an empty list that an agent would read as "this product has no releases"."""
