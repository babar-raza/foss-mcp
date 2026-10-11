"""TC-371: the ten wave-2 .NET, Go and Rust documentation pages are wired into the chart and compose.

Offline. Reads infra/helm/foss-mcp/values.yaml, docker-compose.yml and the committed furnished pages
only; no helm, no docker, no network. For each of the ten pilots it checks that the chart names the
page, the page exists with front matter pinned to the pilot's own library commit, and that compose
passes the same page and library flags to build_chunks.py, before ``--out``. Expected values are read
from values.yaml, never hard-coded.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"
COMPOSE = REPO_ROOT / "docker-compose.yml"
FIXTURES = REPO_ROOT / "tests" / "fixtures"
IMAGE_FIXTURES = "/app/fixtures/"

WAVE2_PILOTS = [
    ("words", "net"),
    ("cells", "net"),
    ("slides", "net"),
    ("email", "net"),
    ("imaging", "net"),
    ("jmap", "net"),
    ("3d", "net"),
    ("cells", "go"),
    ("jmap", "go"),
    ("jmap", "rust"),
]

# Pilots that had a furnishedPage before this card. With WAVE2_PILOTS this is every pilot allowed to
# carry one; a pilot outside both sets that gains a furnishedPage must be wired by its own card.
PRE_EXISTING_PAGES = {
    ("pdf", "net"),
    ("slides", "python"),
    ("pdf", "typescript"),
    ("pdf", "go"),
    ("pdf", "java"),
    ("cells", "rust"),
    ("words", "python"),
    ("cells", "python"),
    ("jmap", "python"),
    ("pdf", "python"),
    ("email", "python"),
    ("barcode", "python"),
    ("note", "python"),
    ("html", "python"),
    ("page", "python"),
    ("3d", "python"),
    ("tex", "python"),
}

# library.platform -> (library block key, build_chunks.py flag) of the platform's own field.
PLATFORM_FIELD = {
    "dotnet": ("csproj", "--library-csproj"),
    "go": ("modulePath", "--library-module-path"),
    "rust": ("crateName", "--library-crate-name"),
}

GO_MODULE_PATH = re.compile(r"^[a-z0-9][a-z0-9.\-]*\.[a-z]{2,}/[A-Za-z0-9._~\-]+(/[A-Za-z0-9._~\-]+)*$")
CRATE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]*$")

IDS = [f"{family}/{platform}" for family, platform in WAVE2_PILOTS]


def _pilots() -> list[dict[str, Any]]:
    return yaml.safe_load(VALUES.read_text(encoding="utf-8"))["ingestion"]["pilots"]


def _pilot(family: str, platform: str) -> dict[str, Any]:
    matches = [p for p in _pilots() if p["family"] == family and p["platform"] == platform]
    assert len(matches) == 1, f"expected exactly one {family}/{platform} pilot, got {len(matches)}"
    return matches[0]


def _build_chunks_tokens(family: str, platform: str) -> list[str]:
    """The tokens of the build_chunks.py invocation: everything before the first ``&&``."""
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    tokens = shlex.split(services[f"ingest-{family}-{platform}"]["command"][0])
    build = tokens[: tokens.index("&&")]
    assert build[:2] == ["python", "/app/infra/build_chunks.py"], build[:2]
    return build


def _flag_index(build: list[str], flag: str) -> int:
    assert flag in build, f"{flag} is missing from the build_chunks.py invocation"
    assert build.count(flag) == 1, f"{flag} appears more than once"
    return build.index(flag)


def _flag_value(build: list[str], flag: str) -> str:
    return build[_flag_index(build, flag) + 1]


def _front_matter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path} has no front matter"
    end = text.index("\n---", 4)
    return yaml.safe_load(text[4:end])


def _host_path(image_path: str) -> Path:
    assert image_path.startswith(IMAGE_FIXTURES), image_path
    return FIXTURES / image_path[len(IMAGE_FIXTURES) :]


@pytest.mark.parametrize(("family", "platform"), WAVE2_PILOTS, ids=IDS)
def test_chart_names_the_pilots_documentation_page(family: str, platform: str) -> None:
    pilot = _pilot(family, platform)
    assert pilot.get("furnishedPage") == f"{IMAGE_FIXTURES}furnished/{family}_{platform}/pages/_index.md"


@pytest.mark.parametrize(("family", "platform"), WAVE2_PILOTS, ids=IDS)
def test_page_exists_and_is_pinned_to_the_library_commit(family: str, platform: str) -> None:
    pilot = _pilot(family, platform)
    page = _host_path(pilot["furnishedPage"])
    assert page.is_file(), f"{page} does not exist"
    front = _front_matter(page)
    assert front.get("doc_source") == "github_readme", front.get("doc_source")
    assert front.get("doc_source_commit") == pilot["library"]["commit"], "doc_source_commit"


@pytest.mark.parametrize(("family", "platform"), WAVE2_PILOTS, ids=IDS)
def test_compose_passes_the_charts_page_and_library_flags_before_out(family: str, platform: str) -> None:
    pilot = _pilot(family, platform)
    library = pilot["library"]
    field_key, field_flag = PLATFORM_FIELD[library["platform"]]
    build = _build_chunks_tokens(family, platform)
    expected = {
        "--furnished-page": pilot["furnishedPage"],
        "--library-repository": library["repository"],
        "--library-commit": library["commit"],
        "--library-platform": library["platform"],
        field_flag: library[field_key],
    }
    out_index = _flag_index(build, "--out")
    for flag, value in expected.items():
        assert _flag_value(build, flag) == value, flag
        assert _flag_index(build, flag) < out_index, f"{flag} must come before --out"


@pytest.mark.parametrize(("family", "platform"), WAVE2_PILOTS, ids=IDS)
def test_compose_passes_no_other_platform_field(family: str, platform: str) -> None:
    library = _pilot(family, platform)["library"]
    _, own_flag = PLATFORM_FIELD[library["platform"]]
    build = _build_chunks_tokens(family, platform)
    for _, flag in PLATFORM_FIELD.values():
        if flag != own_flag:
            assert flag not in build, f"{flag} does not belong to a {library['platform']} pilot"


@pytest.mark.parametrize(("family", "platform"), WAVE2_PILOTS, ids=IDS)
def test_platform_field_is_present_and_plausible(family: str, platform: str) -> None:
    library = _pilot(family, platform)["library"]
    assert library.get("platform") in PLATFORM_FIELD, library.get("platform")
    key, _ = PLATFORM_FIELD[library["platform"]]
    value = library.get(key)
    assert isinstance(value, str) and value.strip(), f"library.{key} is missing or empty"
    if key == "csproj":
        assert value.endswith(".csproj"), value
    elif key == "modulePath":
        assert GO_MODULE_PATH.match(value), value
    else:
        assert CRATE_NAME.match(value), value


def test_no_pilot_outside_the_wired_sets_gained_a_furnished_page() -> None:
    allowed = PRE_EXISTING_PAGES | set(WAVE2_PILOTS)
    with_page = {(p["family"], p["platform"]) for p in _pilots() if p.get("furnishedPage")}
    assert with_page <= allowed, sorted(with_page - allowed)
    assert set(WAVE2_PILOTS) <= with_page, sorted(set(WAVE2_PILOTS) - with_page)
