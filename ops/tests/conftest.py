import sys
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parent.parent
if str(OPS) not in sys.path:
    sys.path.insert(0, str(OPS))

# Repo-discovery environment variables that, if inherited from an ambient process (e.g. a
# linked worktree's own hook invocation sets GIT_DIR to that worktree's gitdir), silently
# redirect a test's own `git` subprocess calls away from the tmp_path repo it actually meant
# to operate on - the exact mechanism that leaked a worktree's GIT_DIR into a synthetic-repo
# test fixture and corrupted the real repo's shared .git/config, including core.bare=true
# there (2026-10-10 incident, see docs/DECISION_LOG.md). Mirrors tests/conftest.py's own
# identical fixture - this module predates a shared conftest helper package, so it is
# duplicated rather than imported, to keep each test tree's own bootstrap self-contained.
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
    """Clear GIT_DIR/GIT_WORK_TREE/etc. for every test's own duration - see tests/conftest.py's
    identical fixture for the full rationale."""
    for name in _GIT_REPO_DISCOVERY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
