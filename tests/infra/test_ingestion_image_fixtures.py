"""TC-209 / TC-352: every pilot in the chart has its fixtures in the ingestion image.

Offline and static. It reads infra/helm/foss-mcp/values.yaml, docker-compose.yml and Dockerfile.ingestion
as text, and checks the repository tree. It never runs docker and never touches the network.

The live fourteen-pilot run found Jobs failing with FileNotFoundError because the chart named a
fixture path the image never COPYed. Nothing tied the chart's pilot list to the image contents.

TC-352: a fixture is covered by an exact ``COPY <file> <destination>`` pair OR by a ``COPY`` of any ancestor
directory whose destination maps the file to the path the chart and compose expect. That lets new furnished
pages reach the image through one directory COPY. It stays strict: a referenced fixture no COPY covers, or
one a COPY lands at the wrong destination, still fails.
"""

from __future__ import annotations

import posixpath
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
VALUES = ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"
COMPOSE = ROOT / "docker-compose.yml"
DOCKERFILE = ROOT / "Dockerfile.ingestion"

EXPECTED_PILOT_COUNT = 40
APP_PREFIX = "/app/"
FIXTURES_PREFIX = "fixtures/"
FURNISHED_SOURCE = "tests/fixtures/furnished/"
FURNISHED_DIRECTORY_COPY = (FURNISHED_SOURCE, "./fixtures/furnished/")
COMPOSE_FIXTURE_PATH = re.compile(r"/app/fixtures/[A-Za-z0-9_./-]+")


def _pilots() -> list[dict]:
    values = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
    return values["ingestion"]["pilots"]


def _pilot_id(pilot: dict) -> str:
    return f"{pilot['family']}/{pilot['platform']}"


def _parse_copy_pairs(dockerfile_text: str) -> set[tuple[str, str]]:
    """Return the (source, destination) of every plain three-token COPY instruction in the text."""
    pairs: set[tuple[str, str]] = set()
    for raw in dockerfile_text.splitlines():
        parts = raw.strip().split()
        if len(parts) == 3 and parts[0].upper() == "COPY":
            pairs.add((parts[1], parts[2]))
    return pairs


def _copy_pairs() -> set[tuple[str, str]]:
    """Return the (source, destination) of every COPY instruction in Dockerfile.ingestion."""
    return _parse_copy_pairs(DOCKERFILE.read_text(encoding="utf-8"))


def _covers(copy_source: str, copy_destination: str, source: str, destination: str) -> bool:
    """True when this COPY lands ``source`` at ``destination``.

    Either an exact file-to-file COPY, or a COPY of an ancestor directory of ``source`` whose
    destination directory plus the file's path inside that directory equals ``destination``.
    """
    copy_source = posixpath.normpath(copy_source)
    copy_destination = posixpath.normpath(copy_destination)
    source = posixpath.normpath(source)
    destination = posixpath.normpath(destination)
    if copy_source == source:
        return copy_destination == destination
    if source.startswith(copy_source + "/"):
        inside = source[len(copy_source) + 1 :]
        return posixpath.join(copy_destination, inside) == destination
    return False


def _is_covered(pairs: set[tuple[str, str]], source: str, destination: str) -> bool:
    return any(
        _covers(copy_source, copy_destination, source, destination) for copy_source, copy_destination in pairs
    )


def _cases() -> list:
    cases = []
    for pilot in _pilots():
        for key in ("apiSurface", "furnishedPage"):
            path = pilot.get(key)
            if path:
                cases.append(pytest.param(path, id=f"{_pilot_id(pilot)}-{key}"))
    return cases


def _compose_cases() -> list:
    found = {path.rstrip(".-") for path in COMPOSE_FIXTURE_PATH.findall(COMPOSE.read_text(encoding="utf-8"))}
    return [pytest.param(path, id=path[len(APP_PREFIX) :]) for path in sorted(found)]


def _assert_in_the_image(path: str) -> None:
    assert path.startswith(APP_PREFIX), f"{path} is not under {APP_PREFIX}"
    relative = path[len(APP_PREFIX) :]
    assert relative.startswith(FIXTURES_PREFIX), f"{path} is not under {APP_PREFIX}{FIXTURES_PREFIX}"
    source = "tests/" + relative
    assert (ROOT / source).is_file(), f"{source} does not exist in the repository"
    destination = "./" + relative
    assert _is_covered(_copy_pairs(), source, destination), (
        f"Dockerfile.ingestion has no COPY that lands {source} at {destination} for {path}"
    )


def test_chart_has_forty_pilots() -> None:
    ids = [_pilot_id(p) for p in _pilots()]
    assert len(ids) == EXPECTED_PILOT_COUNT, ids
    assert len(set(ids)) == EXPECTED_PILOT_COUNT, ids


def test_every_pilot_sets_an_api_surface() -> None:
    missing = [_pilot_id(p) for p in _pilots() if not p.get("apiSurface")]
    assert missing == []


@pytest.mark.parametrize("path", _cases())
def test_fixture_is_in_the_ingestion_image(path: str) -> None:
    _assert_in_the_image(path)


@pytest.mark.parametrize("path", _compose_cases())
def test_compose_fixture_is_in_the_ingestion_image(path: str) -> None:
    _assert_in_the_image(path)


def test_furnished_pages_are_copied_as_one_directory() -> None:
    """TC-352: the furnished pages arrive by one directory COPY, not one COPY line per pilot."""
    pairs = _copy_pairs()
    assert FURNISHED_DIRECTORY_COPY in pairs, sorted(pairs)
    per_pilot = sorted(
        source for source, _ in pairs if source.startswith(FURNISHED_SOURCE) and source != FURNISHED_SOURCE
    )
    assert per_pilot == [], f"per-pilot furnished COPY lines came back: {per_pilot}"


PAGE = "tests/fixtures/furnished/pdf_net/pages/_index.md"
PAGE_DESTINATION = "./fixtures/furnished/pdf_net/pages/_index.md"


def test_a_directory_copy_covering_the_fixture_is_accepted() -> None:
    directory = _parse_copy_pairs("FROM scratch\nCOPY tests/fixtures/furnished/ ./fixtures/furnished/\n")
    assert _is_covered(directory, PAGE, PAGE_DESTINATION)
    # The directory may be written without its trailing slash.
    bare = _parse_copy_pairs("COPY tests/fixtures/furnished ./fixtures/furnished\n")
    assert _is_covered(bare, PAGE, PAGE_DESTINATION)
    # An exact file-to-file COPY is still accepted.
    exact = _parse_copy_pairs(f"COPY {PAGE} {PAGE_DESTINATION}\n")
    assert _is_covered(exact, PAGE, PAGE_DESTINATION)


def test_a_fixture_no_copy_covers_is_rejected() -> None:
    other_files = _parse_copy_pairs(
        "COPY tests/fixtures/furnished/pdf_go/pages/_index.md ./fixtures/furnished/pdf_go/pages/_index.md\n"
        "COPY tests/fixtures/pdf_net/api_surface.json ./fixtures/pdf_net/api_surface.json\n"
    )
    assert not _is_covered(other_files, PAGE, PAGE_DESTINATION)
    # A directory COPY of a path that does not contain the fixture covers nothing.
    missing = _parse_copy_pairs("COPY tests/fixtures/furnished_missing/ ./fixtures/furnished/\n")
    assert not _is_covered(missing, PAGE, PAGE_DESTINATION)
    # A sibling directory sharing only a name prefix is not an ancestor.
    sibling = _parse_copy_pairs("COPY tests/fixtures/furnished/pdf ./fixtures/furnished/pdf\n")
    assert not _is_covered(sibling, PAGE, PAGE_DESTINATION)
    assert not _is_covered(set(), PAGE, PAGE_DESTINATION)


def test_a_copy_that_maps_to_the_wrong_destination_is_rejected() -> None:
    wrong_directory = _parse_copy_pairs("COPY tests/fixtures/furnished/ ./fixtures/elsewhere/\n")
    assert not _is_covered(wrong_directory, PAGE, PAGE_DESTINATION)
    wrong_file = _parse_copy_pairs(f"COPY {PAGE} ./fixtures/furnished/pdf_net/_index.md\n")
    assert not _is_covered(wrong_file, PAGE, PAGE_DESTINATION)
    flattened = _parse_copy_pairs("COPY tests/fixtures/furnished/pdf_net/ ./fixtures/furnished/\n")
    assert not _is_covered(flattened, PAGE, PAGE_DESTINATION)
