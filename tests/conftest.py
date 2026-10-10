"""Test bootstrap for the src/ layout.

foss_mcp is not installed (no packaging metadata exists yet at the G0 gate),
so tests need src/ on sys.path to import it. Mirrors the same pattern used by
ops/tests/conftest.py for the ops/ package.

Live tests (marker ``live``) need the network and an explicit opt-in. They are
deselected at collection when the opt-in is absent: they are not skipped, so
they never appear as skips in a gate run. The reporter states how many were
deselected so the gap stays visible.
"""

import os
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

NETWORK_OPT_IN_ENV = "FOSS_MCP_NETWORK_TESTS"

_DESELECTED = pytest.StashKey[int]()

# Repo-discovery environment variables that, if inherited from an ambient process (e.g. a
# linked worktree's own hook invocation sets GIT_DIR to that worktree's gitdir), silently
# redirect a test's own `git` subprocess calls away from the tmp_path repo it actually meant
# to operate on - the exact mechanism that leaked a worktree's GIT_DIR into a synthetic-repo
# test fixture and corrupted the real repo's shared .git/config, including core.bare=true
# there (2026-10-10 incident, see docs/DECISION_LOG.md).
_GIT_REPO_DISCOVERY_ENV_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)


@pytest.fixture(autouse=True)
def _scrub_git_repo_discovery_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear GIT_DIR/GIT_WORK_TREE/etc. for every test's own duration.

    `subprocess.run(["git", ...])` with no explicit `env=` inherits `os.environ` at call
    time, not a snapshot from process start - so this protects every test that shells out to
    `git` against a tmp_path repo, not only ones that remember to pass `env=` themselves. A
    test that genuinely needs one of these set must opt back in explicitly via its own
    `monkeypatch.setenv`, which this fixture never prevents.
    """
    for name in _GIT_REPO_DISCOVERY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _opted_in() -> bool:
    return os.environ.get(NETWORK_OPT_IN_ENV) == "1"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"live: needs the network and {NETWORK_OPT_IN_ENV}=1; deselected (not skipped) otherwise",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if _opted_in():
        return
    kept = [item for item in items if item.get_closest_marker("live") is None]
    deselected = [item for item in items if item.get_closest_marker("live") is not None]
    if not deselected:
        return
    items[:] = kept
    config.stash[_DESELECTED] = len(deselected)
    config.hook.pytest_deselected(items=deselected)


def pytest_terminal_summary(terminalreporter, exitstatus: int, config: pytest.Config) -> None:
    count = config.stash.get(_DESELECTED, 0)
    if count:
        terminalreporter.write_line(
            f"{count} live test(s) deselected; set {NETWORK_OPT_IN_ENV}=1 to run them"
        )
