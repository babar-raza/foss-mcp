"""G3/TC-546: README.md, docs/RELEASE.md and the review ledger agree with the chart, the compose file and the tools.

A peer's first hour starts at README.md, so it must describe the release that exists: the pilot count of the Helm
chart, the nine tools of the registry, the loopback ports of the demo compose file and the `docker run` command of
docs/RELEASE.md. RELEASE.md section 9 must also publish what the four cut extractor cards would have fixed. The
tests read committed files as text, and the one module they import (the ledger tool) has no import side effects.
Offline and deterministic.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
README = REPO_ROOT / "README.md"
RELEASE_DOC = REPO_ROOT / "docs" / "RELEASE.md"
COMPOSE = REPO_ROOT / "docker-compose.demo.yml"
VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"
SERVER = REPO_ROOT / "src" / "foss_mcp" / "mcp" / "server.py"
LEDGER_TOOL = REPO_ROOT / "scripts" / "release" / "build_review_ledger.py"
PLANS_DIR = REPO_ROOT / "plans"

SECTION_9_HEADING = "## 9. Deferred extraction improvements"
WHAT_TELLS_HEADING = "### What tells an agent"
NOT_PART_HEADING = "### Not part of this release"

CUT_CARD_NUMBERS = {
    "TC-526": ("6 of 6", "34 properties", "259", "1,434"),
    "TC-527": ("8 of 13", "1,037"),
    "TC-528": ("FileExistsError", "e.g."),
    "TC-531": ("1,239", "193", "33 percent"),
}

STALE_PHRASES = (
    "seven pilots",
    "7 pilots",
    "G2",
    "in progress",
    "Known integration gap",
    "NotAvailable/empty on every live deployment",
    "8082",
    "no ingestion job yet",
    "7×6",
)


def _readme() -> str:
    return README.read_text(encoding="utf-8")


def _release() -> str:
    return RELEASE_DOC.read_text(encoding="utf-8")


def _chart_pilots() -> list[dict[str, object]]:
    data = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
    return [dict(p) for p in data["ingestion"]["pilots"]]


def _chart_families() -> list[str]:
    return sorted({str(p["family"]) for p in _chart_pilots()})


def _tool_names() -> list[str]:
    """The keys of TOOL_DESCRIPTIONS in server.py, read as text (the module is not imported)."""
    text = SERVER.read_text(encoding="utf-8")
    start = text.index("TOOL_DESCRIPTIONS: dict[str, str] = {")
    end = text.index("\n}\n", start)
    return re.findall(r'^    "([a-z_]+)": \(', text[start:end], re.MULTILINE)


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    rest = text[start + len(heading) :]
    match = re.search(r"^## ", rest, re.MULTILINE)
    return rest if match is None else rest[: match.start()]


def _section_9() -> str:
    return _section(_release(), SECTION_9_HEADING)


def _section_8() -> str:
    return _section(_release(), "## 8. Known limitations")


def _bullet(section: str, ident: str) -> str:
    """The text of the bullet that starts ``- **<ident>**``, including its continuation lines."""
    lines = section.splitlines()
    start = [i for i, line in enumerate(lines) if line.startswith(f"- **{ident}**")]
    assert len(start) == 1, f"{ident} must start exactly one bullet, found {len(start)}"
    out = [lines[start[0]]]
    for line in lines[start[0] + 1 :]:
        if line.startswith(("- ", "#")) or not line.strip():
            break
        out.append(line)
    return " ".join(part.strip() for part in out)


def _docker_run_line(text: str) -> str:
    lines = [line for line in text.splitlines() if line.startswith("docker run -d")]
    assert len(lines) == 1, f"expected exactly one 'docker run -d' line, found {len(lines)}"
    return lines[0]


def _tool_table_names(readme: str) -> list[str]:
    section = _section(readme, "## The nine tools")
    return re.findall(r"^\| `([a-z_]+)` \|", section, re.MULTILINE)


def _load_ledger_tool() -> ModuleType:
    name = "readme_test_build_review_ledger"
    spec = importlib.util.spec_from_file_location(name, LEDGER_TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# -- the README against the chart, the compose file and the tool registry -------------------------------------


def test_the_chart_has_forty_pilots_and_thirteen_families() -> None:
    assert len(_chart_pilots()) == 40
    assert len(_chart_families()) == 13


def test_the_readme_pilot_count_is_the_charts() -> None:
    count = len(_chart_pilots())
    assert f"{count} pilots" in _readme()


@pytest.mark.parametrize("family", _chart_families())
def test_the_readme_names_every_product_family(family: str) -> None:
    assert re.search(rf"(?<![A-Za-z0-9_]){re.escape(family)}(?![A-Za-z0-9_])", _readme()), family


def test_the_tool_registry_has_nine_tools() -> None:
    assert len(_tool_names()) == 9
    assert len(set(_tool_names())) == 9


@pytest.mark.parametrize("tool", _tool_names())
def test_the_readme_lists_every_tool(tool: str) -> None:
    assert tool in _tool_table_names(_readme())


def test_the_readme_tool_table_lists_nothing_that_is_not_a_tool() -> None:
    listed = _tool_table_names(_readme())
    assert len(listed) == len(set(listed)) == 9
    assert set(listed) - set(_tool_names()) == set()


@pytest.mark.parametrize("phrase", STALE_PHRASES)
def test_the_readme_has_no_stale_phrases(phrase: str) -> None:
    assert phrase not in _readme()


def test_the_readme_has_no_seven_pilot_port_table() -> None:
    text = _readme()
    for removed in ("## Current status", "## What's planned", "| Pilot | Port |"):
        assert removed not in text


def test_the_readme_docker_run_flags_are_the_release_runbooks() -> None:
    readme_flags = re.findall(r"(?<!\S)(--[a-z][a-z-]*)", _docker_run_line(_readme()))
    release_flags = set(re.findall(r"(?<!\S)(--[a-z][a-z-]*)", _docker_run_line(_release())))
    assert readme_flags, "the README docker run line has no flags"
    assert set(readme_flags) - release_flags == set()
    assert "FOSS_MCP_PILOT=pdf/net" in _docker_run_line(_readme())


def test_the_readme_states_no_authentication_and_no_tls_once_each() -> None:
    lower = _readme().lower()
    assert lower.count("no authentication") == 1
    assert lower.count("no tls") == 1


@pytest.mark.parametrize(
    "needle", ["/mcp", "/readyz", "/healthz", "/metrics", "--list", "FOSS_MCP_PILOT", "Streamable HTTP"]
)
def test_the_readme_names_the_endpoint_and_probe_paths(needle: str) -> None:
    assert needle in " ".join(_readme().split())


def test_the_readme_names_the_origin_allow_list() -> None:
    assert "FOSS_MCP_ALLOWED_ORIGINS" in _readme()


def test_the_readme_compose_facts_match_the_compose_header() -> None:
    assert COMPOSE.is_file()
    header = "\n".join(
        line for line in COMPOSE.read_text(encoding="utf-8").splitlines() if line.startswith("#")
    )
    readme = _readme()
    assert "FOSS_MCP_DEMO_IMAGE" in readme
    assert "FOSS_MCP_DEMO_IMAGE" in header
    for port in ("8101", "8140"):
        assert port in readme, f"README does not name port {port}"
        assert port in header, f"compose header does not name port {port}"


def test_every_docs_path_the_readme_mentions_exists() -> None:
    paths = sorted(set(re.findall(r"docs/[A-Za-z0-9_./-]+\.md", _readme())))
    assert paths, "the README mentions no docs path"
    for rel in paths:
        assert (REPO_ROOT / rel).is_file(), f"README names {rel}, which does not exist"


def test_the_readme_points_at_release_sections_8_and_9_and_the_ledger() -> None:
    readme = _readme()
    assert "docs/RELEASE.md" in readme
    assert "section 8" in readme
    assert "section 9" in readme
    assert "docs/REVIEW_LEDGER.md" in readme


def test_the_readme_says_mcp_gateway_and_the_cluster_are_not_part_of_the_release() -> None:
    flat = " ".join(_readme().split())
    assert "`mcp-gateway` and the Kubernetes install are not part of the release" in flat
    assert "docs/DEPLOYMENT.md" in flat


def test_the_readme_says_this_is_a_release_candidate_with_an_incomplete_review() -> None:
    flat = " ".join(_readme().split())
    assert "release candidate" in flat
    assert "review of it is not complete" in flat
    assert "300 of 1,239 types" in flat
    assert "coverage_note" in flat


def test_the_readme_keeps_its_true_sections() -> None:
    readme = _readme()
    for heading in (
        "## How it works",
        "### The three ingestion sources",
        "## Governance and contributing",
        "## License",
    ):
        assert heading in readme
    assert "MIT (`pyproject.toml`)." in readme


def test_the_readme_is_plain_utf8_without_a_byte_order_mark_or_crlf() -> None:
    raw = README.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in raw


# -- docs/RELEASE.md section 9 and 8 ------------------------------------------------------------------------


def test_release_section_9_names_mcp_gateway_as_not_part_of_the_release() -> None:
    body = _section_9()
    after = body[body.index(NOT_PART_HEADING) :]
    flat = " ".join(after.split())
    assert "`mcp-gateway`" in flat
    assert "is not part of the release" in flat
    assert "14 of the 40 pilots" in flat


def test_release_section_9_has_the_two_subsections_without_bullets() -> None:
    body = _section_9()
    assert body.count(WHAT_TELLS_HEADING) == 1
    assert body.count(NOT_PART_HEADING) == 1
    assert body.index(WHAT_TELLS_HEADING) < body.index(NOT_PART_HEADING)
    first = body.index(WHAT_TELLS_HEADING)
    # Every bullet comes before the first subsection, and the subsections hold plain prose only.
    assert not [line for line in body[first:].splitlines() if line.startswith("- ")]
    assert [line for line in body[:first].splitlines() if line.startswith("- ")]


def test_release_section_9_what_tells_an_agent_names_the_coverage_signals() -> None:
    body = _section_9()
    block = " ".join(body[body.index(WHAT_TELLS_HEADING) : body.index(NOT_PART_HEADING)].split())
    for needle in ("API-Coverage:", "coverage_note", "indexed_types", "total_types", "300 of 1239 types"):
        assert needle in block, needle


@pytest.mark.parametrize(
    ("card_id", "numbers"),
    sorted(CUT_CARD_NUMBERS.items()),
)
def test_the_cut_card_bullet_carries_its_measured_numbers(card_id: str, numbers: tuple[str, ...]) -> None:
    bullet = _bullet(_section_9(), card_id)
    assert "Would have" in bullet
    for number in numbers:
        assert number in bullet, f"{card_id} bullet lacks {number!r}"


@pytest.mark.parametrize("card_id", sorted(CUT_CARD_NUMBERS))
def test_every_cut_card_has_a_plan_file_and_a_bullet(card_id: str) -> None:
    assert (PLANS_DIR / f"{card_id}.yaml").is_file()
    assert _section_9().count(f"**{card_id}**") == 1


def test_release_md_says_the_image_is_a_release_candidate_and_the_review_is_open() -> None:
    flat = " ".join(_release().split())
    assert "release candidate" in flat
    assert "(REQ-G3-008) is not closed by this page" in flat


def test_release_md_builds_the_demo_stage_from_dockerfile_serving() -> None:
    text = _release()
    assert "docker build -f Dockerfile.serving --target demo " in text
    assert "docker build --target demo" not in text


def test_the_l054_bullet_describes_the_title_rule_and_not_the_old_behaviour() -> None:
    bullet = _bullet(_section_8(), "L-054")
    assert "find_examples" in bullet
    for needle in ("title", "60%", "match_basis"):
        assert needle in bullet, needle
    assert "any verified example whose text uses that word" not in bullet
    assert "returns any verified example whose text uses that word" not in bullet


# -- the generated ledger -------------------------------------------------------------------------------------


def test_the_real_ledger_check_is_clean() -> None:
    tool = _load_ledger_tool()
    rows, _meta = tool.compute(REPO_ROOT, False)
    assert [r.f.id for r in rows if r.status == "OPEN"] == []
    assert tool.post_checks(rows) == []
    l054 = [r for r in rows if r.f.id == "L-054"]
    assert len(l054) == 1
    assert l054[0].status == "ACCEPTED"
    assert "title" in l054[0].evidence


def test_the_committed_ledger_row_for_l054_states_the_title_rule() -> None:
    ledger = (REPO_ROOT / "docs" / "REVIEW_LEDGER.md").read_text(encoding="utf-8")
    rows = [line for line in ledger.splitlines() if line.startswith("| L-054 |")]
    assert len(rows) == 1
    assert "title" in rows[0]
    assert "60%" in rows[0]
    assert "one-word query" not in rows[0]
