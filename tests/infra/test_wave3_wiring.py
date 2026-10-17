"""TC-375: the nine wave-3 Java, C++ and TypeScript documentation pages are wired into the chart and compose.

Offline. Reads infra/helm/foss-mcp/values.yaml, docker-compose.yml and the committed furnished pages
only; no helm, no docker, no network. For each of the nine pilots it checks that the chart names
the page, the page exists (with front matter pinned to the pilot's own library commit), and that compose
passes the same page and every library flag the chart would pass to build_chunks.py, before ``--out``.
Expected values are read from values.yaml; the library-key to flag mapping is LIBRARY_FLAGS from
test_helm_pilots_match_compose.py, imported rather than retyped.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.infra.test_helm_pilots_match_compose import LIBRARY_FLAGS

REPO_ROOT = Path(__file__).resolve().parents[2]
VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"
COMPOSE = REPO_ROOT / "docker-compose.yml"
FIXTURES = REPO_ROOT / "tests" / "fixtures"
IMAGE_FIXTURES = "/app/fixtures/"

WAVE3_PILOTS = [
    ("slides", "java"),
    ("cells", "java"),
    ("3d", "java"),
    ("pdf", "cpp"),
    ("cells", "cpp"),
    ("slides", "cpp"),
    ("email", "cpp"),
    ("cells", "typescript"),
    ("3d", "typescript"),
]

# cells/cpp's page is an original sealed-candidate page: it carries no doc_source front matter.
SEALED_PAGE_PILOTS = {("cells", "cpp")}

IDS = [f"{family}/{platform}" for family, platform in WAVE3_PILOTS]
KEY_TO_FLAG = {key: flag for flag, key in LIBRARY_FLAGS.items()}


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


@pytest.mark.parametrize(("family", "platform"), WAVE3_PILOTS, ids=IDS)
def test_chart_names_the_pilots_documentation_page(family: str, platform: str) -> None:
    pilot = _pilot(family, platform)
    assert pilot.get("furnishedPage") == f"{IMAGE_FIXTURES}furnished/{family}_{platform}/pages/_index.md"


@pytest.mark.parametrize(("family", "platform"), WAVE3_PILOTS, ids=IDS)
def test_page_exists_and_is_pinned_to_the_library_commit(family: str, platform: str) -> None:
    pilot = _pilot(family, platform)
    page = _host_path(pilot["furnishedPage"])
    assert page.is_file(), f"{page} does not exist"
    front = _front_matter(page)
    if (family, platform) in SEALED_PAGE_PILOTS:
        title = front.get("title")
        assert isinstance(title, str) and title.strip(), "top-level title is missing or empty"
        return
    assert front.get("doc_source") == "github_readme", front.get("doc_source")
    assert front.get("doc_source_commit") == pilot["library"]["commit"], "doc_source_commit"


@pytest.mark.parametrize(("family", "platform"), WAVE3_PILOTS, ids=IDS)
def test_compose_passes_the_charts_page_and_every_library_flag_before_out(family: str, platform: str) -> None:
    pilot = _pilot(family, platform)
    library = pilot["library"]
    build = _build_chunks_tokens(family, platform)
    expected = {"--furnished-page": pilot["furnishedPage"]}
    for key, value in library.items():
        assert key in KEY_TO_FLAG, f"library.{key} has no flag in LIBRARY_FLAGS"
        expected[KEY_TO_FLAG[key]] = str(value)
    for required in ("--library-repository", "--library-commit", "--library-platform"):
        assert required in expected, f"library block lacks the key behind {required}"
    out_index = _flag_index(build, "--out")
    for flag, value in expected.items():
        assert _flag_value(build, flag) == value, flag
        assert _flag_index(build, flag) < out_index, f"{flag} must come before --out"


@pytest.mark.parametrize(("family", "platform"), WAVE3_PILOTS, ids=IDS)
def test_compose_passes_no_library_flag_the_chart_would_not(family: str, platform: str) -> None:
    library = _pilot(family, platform)["library"]
    build = _build_chunks_tokens(family, platform)
    allowed = {KEY_TO_FLAG[key] for key in library}
    extra = {token for token in build if token.startswith("--library-")} - allowed
    assert not extra, f"compose passes {sorted(extra)} which the chart would not"


def test_every_furnished_page_exists_and_the_wired_sets_have_one() -> None:
    """Forward-compatible: every pilot with a furnishedPage points at an existing file, and the set of
    pilots with one contains the nine. Later waves may add more.
    """
    with_page: dict[tuple[str, str], str] = {}
    for pilot in _pilots():
        if pilot.get("furnishedPage"):
            with_page[(pilot["family"], pilot["platform"])] = pilot["furnishedPage"]
    for key, page in with_page.items():
        assert _host_path(page).is_file(), f"{key}: furnishedPage {page} has no page file"
    assert set(WAVE3_PILOTS) <= set(with_page), sorted(set(WAVE3_PILOTS) - set(with_page))
