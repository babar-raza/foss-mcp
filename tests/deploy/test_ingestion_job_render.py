"""G3/TC-575 (REQ-G3-008, REQ-G3-009): the product-reference fetch step's failure is visible, not swallowed.

``infra/helm/foss-mcp/templates/ingestion-job.yaml`` wraps the ``fetch_product_reference.py`` sub-command in a
POSIX sh brace group ending ``|| true;`` - so when that command fails (OWNER-14: the cluster's GITHUB_TOKEN is
rejected with HTTP 401), the Job step was treated as a silent no-op success and ingestion proceeded with whatever
sidecar already existed. This module renders the real chart offline (no cluster, no network) and proves, for
every pilot that gets the fetch step, that:

  (a) the step still falls through on failure - the Job's overall chain is not blocked, exactly as before
      (GRR-02/D-01's own settled design, which this card does not change); and
  (b) the swallowed failure is now echoed to the Job's own stdout as a visible construct, instead of being
      silently discarded by a bare ``|| true``.

Assertions on (b) are structural (a visible-output command runs in place of the old no-op, and its message
names the pilot and an exit code), never on the literal wording of the message, which may drift.

This file touches ONLY the fetch_product_reference.py sub-command's own handling. Every OTHER ``|| true`` in
this template (the verify_product_reference_install.py step and the unconditional fetch_recent_releases.py step)
is asserted UNCHANGED - this is a narrow, one-sub-command fix, not a general error-handling rewrite.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CHART = REPO_ROOT / "infra" / "helm" / "foss-mcp"
VALUES = CHART / "values.yaml"
TAG = "tc575-render-test"

FETCH_COMMAND = "/app/infra/fetch_product_reference.py"
VERIFY_INSTALL_COMMAND = "/app/infra/verify_product_reference_install.py"
RECENT_RELEASES_COMMAND = "/app/infra/fetch_recent_releases.py"

# A visible-output POSIX command a sh script could use to report a failure to the Job's own stdout.
# Structural, not the exact wording of the message that follows it.
_VISIBLE_REPORTING_COMMANDS = ("echo", "printf")


def _helm() -> str:
    helm = shutil.which("helm")
    if helm is None:
        pytest.fail(
            "helm is not on PATH. TC-575 requires it: source scripts/toolchain/activate.ps1, "
            "which adds C:\\dev-tools\\foss-mcp\\helm\\windows-amd64."
        )
    return helm


def _render() -> list[dict[str, Any]]:
    result = subprocess.run(
        [_helm(), "template", "foss-mcp", str(CHART), "--set", f"image.tag={TAG}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def _values() -> dict[str, Any]:
    return yaml.safe_load(VALUES.read_text(encoding="utf-8"))


def _pilots_with_a_manifest_path() -> list[dict[str, Any]]:
    return [p for p in _values()["ingestion"]["pilots"] if "manifestPath" in p]


_PILOTS = _pilots_with_a_manifest_path()
_PILOT_KEYS = [f"{p['family']}/{p['platform']}" for p in _PILOTS]


def _job(docs: list[dict[str, Any]], pilot: dict[str, Any]) -> dict[str, Any]:
    suffix = f"-ingest-{pilot['family']}-{pilot['platform']}"
    matches = [job for job in docs if job.get("kind") == "Job" and job["metadata"]["name"].endswith(suffix)]
    assert len(matches) == 1, (
        f"expected one Job for {pilot['family']}/{pilot['platform']}, got {len(matches)}"
    )
    return matches[0]


def _job_tokens(job: dict[str, Any]) -> list[str]:
    container = job["spec"]["template"]["spec"]["containers"][0]
    return shlex.split(container["args"][0])


@pytest.fixture(scope="module")
def rendered_docs() -> list[dict[str, Any]]:
    return _render()


# -- (a) the fetch step still falls through on failure -------------------------------------------------------


@pytest.mark.parametrize("pilot", _PILOTS, ids=_PILOT_KEYS)
def test_the_fetch_step_still_falls_through_to_the_next_step(
    rendered_docs: list[dict[str, Any]], pilot: dict[str, Any]
) -> None:
    identity = f"{pilot['family']}/{pilot['platform']}"
    tokens = _job_tokens(_job(rendered_docs, pilot))
    fetch_index = tokens.index(FETCH_COMMAND)
    or_index = tokens.index("||", fetch_index)
    # Whatever runs after "||" (the visibility fix), the brace group's own closing "}" must still
    # immediately follow it (scanning past exactly one reporting command and its one message token),
    # and the unconditional "&&" chain to the verify-install step must still continue - the Job step
    # as a whole is NOT allowed to fail because the fetch sub-command failed.
    close_index = tokens.index("}", or_index)
    assert tokens[close_index + 1 : close_index + 4] == ["&&", "{", "python"], (
        identity,
        tokens[fetch_index:],
    )
    assert tokens[close_index + 4] == VERIFY_INSTALL_COMMAND, (identity, tokens[fetch_index:])


@pytest.mark.parametrize("pilot", _PILOTS, ids=_PILOT_KEYS)
def test_the_fetch_step_no_longer_ends_on_a_bare_true(
    rendered_docs: list[dict[str, Any]], pilot: dict[str, Any]
) -> None:
    identity = f"{pilot['family']}/{pilot['platform']}"
    tokens = _job_tokens(_job(rendered_docs, pilot))
    fetch_index = tokens.index(FETCH_COMMAND)
    or_index = tokens.index("||", fetch_index)
    reported = tokens[or_index + 1]
    assert reported.rstrip(";") != "true", (
        "the fetch step must no longer silently swallow its own failure with a bare '|| true'",
        identity,
        tokens[fetch_index:],
    )


# -- (b) the failure is now visibly reported ------------------------------------------------------------------


@pytest.mark.parametrize("pilot", _PILOTS, ids=_PILOT_KEYS)
def test_the_fetch_step_failure_is_reported_with_a_visible_command(
    rendered_docs: list[dict[str, Any]], pilot: dict[str, Any]
) -> None:
    """Structural only: SOME visible-output command runs in place of the old no-op. The exact wording
    of its message is deliberately not pinned here (it may drift); only its presence and its carrying
    the pilot identity and an exit-code reference are checked."""
    identity = f"{pilot['family']}/{pilot['platform']}"
    tokens = _job_tokens(_job(rendered_docs, pilot))
    fetch_index = tokens.index(FETCH_COMMAND)
    or_index = tokens.index("||", fetch_index)
    reporting_command = tokens[or_index + 1]
    assert reporting_command in _VISIBLE_REPORTING_COMMANDS, (
        f"expected a visible-output command ({_VISIBLE_REPORTING_COMMANDS}) after the fetch step's '||', "
        f"found {reporting_command!r}",
        identity,
    )
    message = tokens[or_index + 2]
    assert pilot["family"] in message, (identity, message)
    assert pilot["platform"] in message, (identity, message)
    # Some reference to the failed command's own exit status, not a fixed, unconditional message.
    assert "$?" in message or "exit" in message.lower(), (identity, message)


# -- scope: nothing else in the template changed --------------------------------------------------------------


def test_the_verify_install_step_is_still_a_bare_or_true(rendered_docs: list[dict[str, Any]]) -> None:
    pilot = next(p for p in _PILOTS if p["family"] == "pdf" and p["platform"] == "cpp")
    tokens = _job_tokens(_job(rendered_docs, pilot))
    verify_index = tokens.index(VERIFY_INSTALL_COMMAND)
    or_index = tokens.index("||", verify_index)
    assert tokens[or_index + 1].rstrip(";") == "true", (
        "the verify-install step's own '|| true' must be untouched by this card",
        tokens[verify_index:],
    )


@pytest.mark.parametrize("pilot", _PILOTS, ids=_PILOT_KEYS)
def test_the_recent_releases_step_is_still_a_bare_or_true(
    rendered_docs: list[dict[str, Any]], pilot: dict[str, Any]
) -> None:
    identity = f"{pilot['family']}/{pilot['platform']}"
    tokens = _job_tokens(_job(rendered_docs, pilot))
    fetch_index = tokens.index(RECENT_RELEASES_COMMAND)
    or_index = tokens.index("||", fetch_index)
    assert tokens[or_index + 1].rstrip(";") == "true", (
        "the unconditional recent-releases step's own '|| true' must be untouched by this card",
        identity,
        tokens[fetch_index:],
    )


def test_a_pilot_without_a_manifest_path_gets_no_fetch_step(tmp_path: Path) -> None:
    # Every real pilot in the chart currently has a manifestPath, so this invariant cannot be
    # exercised against the real values.yaml alone - skipping it would hide a real regression if
    # the fetch step's "with $p.manifestPath" guard were ever dropped. Instead, override
    # ingestion.pilots with one synthetic pilot that deliberately omits manifestPath (Helm
    # replaces, not merges, a list value given in an override file), and render that for real.
    pilot = {
        "family": "synthetic",
        "platform": "nomanifest",
        "sourceKind": "self_extracted",
        "heldBy": "ingestion-synthetic-nomanifest",
        "apiSurface": "/app/fixtures/synthetic_nomanifest/api_surface.json",
        "title": "synthetic/nomanifest API surface (test-only, no manifestPath)",
        "maxTypes": 1,
    }
    override = tmp_path / "no-manifest-pilot.yaml"
    override.write_text(yaml.dump({"ingestion": {"pilots": [pilot]}}), encoding="utf-8")
    result = subprocess.run(
        [_helm(), "template", "foss-mcp", str(CHART), "--set", f"image.tag={TAG}", "--values", str(override)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    docs = [doc for doc in yaml.safe_load_all(result.stdout) if doc]
    tokens = _job_tokens(_job(docs, pilot))
    assert FETCH_COMMAND not in tokens, (
        "a pilot without manifestPath must get no fetch_product_reference.py step"
    )


def test_at_least_one_pilot_with_a_manifest_path_exists() -> None:
    assert _PILOTS, "expected at least one pilot with a manifestPath in values.yaml"
