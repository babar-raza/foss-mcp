"""G3/TC-671 (REQ-G3-011): proves the nested-pytest subprocess timeout budget inside
``test_relevance_doc_scope_weights_enabled.py`` was actually raised at its one real call site
(``_run_pytest(list(RATCHET_BUNDLE), timeout=...)``), and that the raised budget still produces a
genuinely passing run of the real, slow ratchet-bundle re-run test - not merely that the literal
``300`` is gone from the source.

This file does not re-measure the bundle's wall-clock time itself (the supervisor already measured
736s on a worker branch and 464s for a subset on a clean main worktree, both with zero failures -
see ``plans/TC-671.yaml``'s ``purpose`` field). It proves two narrower, directly-checkable things:

1. The hardcoded ``timeout=300`` literal is gone from the real, current source of
   ``test_relevance_doc_scope_weights_enabled.py``, and whatever number replaced it at that one
   real call site is at least 900 seconds - enough headroom over the worst measurement recorded
   above (736s) that a correctly-passing-but-slow run no longer looks like a regression.
2. Re-invoking the real, slow
   ``test_the_full_ratchet_bundle_passes_completely_through_the_real_wired_search_docs_and_lookup``
   test in-process (via ``pytest.main``) with the new budget in place still completes successfully -
   direct proof that raising the number did not, by typo or otherwise, break the function.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
TARGET_FILE = REPO_ROOT / "tests" / "mcp" / "tools" / "test_relevance_doc_scope_weights_enabled.py"
TARGET_NODEID = (
    "tests/mcp/tools/test_relevance_doc_scope_weights_enabled.py::"
    "test_the_full_ratchet_bundle_passes_completely_through_the_real_wired_search_docs_and_lookup"
)

MIN_ACCEPTABLE_TIMEOUT_SECONDS = 900

# The one real call site this card's own second check command exercises, reused verbatim here so
# this test is anchored on the actual source text, not a restated number.
_CALL_SITE_PATTERN = re.compile(r"_run_pytest\(list\(RATCHET_BUNDLE\),\s*timeout=(\d+)\)")


def _real_call_site_timeout() -> int:
    source = TARGET_FILE.read_text(encoding="utf-8")
    match = _CALL_SITE_PATTERN.search(source)
    assert match is not None, (
        "could not find the '_run_pytest(list(RATCHET_BUNDLE), timeout=...)' call site in "
        f"{TARGET_FILE} - it may have been rewritten in a way this test no longer anchors on"
    )
    return int(match.group(1))


def test_the_hardcoded_timeout_300_literal_is_gone_and_the_real_budget_has_enough_headroom() -> None:
    """Reads the real, current source (not a cached copy) and confirms both that the old, too-tight
    ``timeout=300`` literal is gone, and that whatever value replaced it at the one real call site
    is at least 900s - enough margin over the supervisor's own worst direct measurement (736s)."""
    source = TARGET_FILE.read_text(encoding="utf-8")
    assert "timeout=300" not in source, (
        "the old, too-tight timeout=300 literal is still present in "
        f"{TARGET_FILE} - the TC-671 fix was expected to remove it"
    )

    real_timeout = _real_call_site_timeout()
    assert real_timeout >= MIN_ACCEPTABLE_TIMEOUT_SECONDS, (
        f"the real timeout at the RATCHET_BUNDLE call site is {real_timeout}s, which is below the "
        f"{MIN_ACCEPTABLE_TIMEOUT_SECONDS}s floor this card requires for adequate headroom over the "
        "supervisor's measured worst case (736s)"
    )


def test_the_full_ratchet_bundle_test_still_passes_in_process_with_the_new_timeout_budget() -> None:
    """Direct proof the new, raised timeout did not break anything: re-invokes the real, slow
    ratchet-bundle re-run test in-process via ``pytest.main`` against the real, current source tree.
    This is expected to take several minutes (it re-runs an 8-file ratchet bundle as a nested
    subprocess) - that is by design, not a bug, and must not be shrunk."""
    exit_code = pytest.main(["-q", "-p", "no:cacheprovider", TARGET_NODEID])
    assert exit_code == 0, (
        f"the real ratchet-bundle re-run test failed or errored under the new timeout budget "
        f"(pytest exit code {exit_code}) - the TC-671 fix must not have broken the call site"
    )
