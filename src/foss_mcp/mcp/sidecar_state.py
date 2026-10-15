"""The state of one optional serving sidecar, and the words that state is reported in (G3/TC-507).

A sidecar (the product-reference file, the recent-releases file) is in exactly one of three states
when the serving container starts. ``infra/serve_http.py`` decides the state; the two tools that
read the sidecars report it, and a failed fetch is never reported as an absence.
"""

from __future__ import annotations

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


class SidecarFetchFailedError(RuntimeError):
    """The sidecar a tool reads could not be fetched or is unusable. Not a ``ValueError``, so it is
    never classified as an invalid argument."""
