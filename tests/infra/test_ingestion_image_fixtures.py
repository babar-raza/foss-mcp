"""TC-209: every pilot in the chart has its fixtures in the ingestion image.

Offline and static. It reads infra/helm/foss-mcp/values.yaml and Dockerfile.ingestion as text, and
checks the repository tree. It never runs docker and never touches the network.

The live fourteen-pilot run found Jobs failing with FileNotFoundError because the chart named a
fixture path the image never COPYed. Nothing tied the chart's pilot list to the image contents.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
VALUES = ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"
DOCKERFILE = ROOT / "Dockerfile.ingestion"

EXPECTED_PILOT_COUNT = 14
APP_PREFIX = "/app/"
FIXTURES_PREFIX = "fixtures/"


def _pilots() -> list[dict]:
    values = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
    return values["ingestion"]["pilots"]


def _pilot_id(pilot: dict) -> str:
    return f"{pilot['family']}/{pilot['platform']}"


def _copy_pairs() -> set[tuple[str, str]]:
    """Return the (source, destination) of every COPY instruction in Dockerfile.ingestion."""
    pairs: set[tuple[str, str]] = set()
    for raw in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        parts = raw.strip().split()
        if len(parts) == 3 and parts[0].upper() == "COPY":
            pairs.add((parts[1], parts[2]))
    return pairs


def _cases() -> list:
    cases = []
    for pilot in _pilots():
        for key in ("apiSurface", "furnishedPage"):
            path = pilot.get(key)
            if path:
                cases.append(pytest.param(path, id=f"{_pilot_id(pilot)}-{key}"))
    return cases


def test_chart_has_fourteen_pilots() -> None:
    ids = [_pilot_id(p) for p in _pilots()]
    assert len(ids) == EXPECTED_PILOT_COUNT, ids
    assert len(set(ids)) == EXPECTED_PILOT_COUNT, ids


def test_every_pilot_sets_an_api_surface() -> None:
    missing = [_pilot_id(p) for p in _pilots() if not p.get("apiSurface")]
    assert missing == []


@pytest.mark.parametrize("path", _cases())
def test_fixture_is_in_the_ingestion_image(path: str) -> None:
    assert path.startswith(APP_PREFIX), f"{path} is not under {APP_PREFIX}"
    relative = path[len(APP_PREFIX):]
    assert relative.startswith(FIXTURES_PREFIX), f"{path} is not under {APP_PREFIX}{FIXTURES_PREFIX}"
    source = "tests/" + relative
    assert (ROOT / source).is_file(), f"{source} does not exist in the repository"
    destination = "./" + relative
    assert (source, destination) in _copy_pairs(), (
        f"Dockerfile.ingestion has no 'COPY {source} {destination}' for {path}"
    )
