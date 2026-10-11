"""G3/TC-351 (REQ-G3-005): every committed furnished page is valid, and pinned to the chart.

A furnished page is a committed fixture the ingestion image bakes in. Each one must

* load through the real, accepted loader (``infra.build_chunks._load_furnished_page``),
* carry a non-empty top-level ``title:`` (``scripts/poc/pilot_workflow_proof.py`` check 5 reads it),
* contain at least one fenced code block, and
* if it was produced by ``infra/generate_furnished_page.py --source github`` - every such page
  carries the front-matter key ``doc_source: github_readme`` - and its pilot is in the chart, record
  ``doc_source_commit`` equal to that pilot's pinned ``library.commit``. A generated page built from
  a different commit is stale, and fails.

Pages WITHOUT the ``doc_source`` key are the original sealed-candidate pages. They deliberately carry
the sealed candidate's own commit (an accepted test in test_generate_furnished_page.py asserts that
it differs from the chart pin) or none, so they are exempt from the pin rule. They still must load,
have a title and hold a fenced block, and this module never touches or regenerates them.

The test is parametrized over every directory under ``tests/fixtures/furnished`` that holds
``pages/_index.md`` (``sealed_current`` holds only vendored commit pointers, so it is not a pilot
and has none). It is offline: it reads the chart's values.yaml as text.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest
import yaml

import infra.build_chunks as build_chunks
import infra.generate_furnished_page as gen
from foss_mcp.extraction.repo_native_reader import DocumentPresent
from foss_mcp.indexing.doc_candidates import extract_doc_sections

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "furnished"
CHART_VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"
NOT_A_PILOT = {"sealed_current"}
GENERATED_MARK = ("doc_source", "github_readme")  # the front-matter key/value a generated page carries
FENCE_RE = re.compile(r"^[ \t]*(```|~~~)", re.MULTILINE)


def _pilot_dirs() -> list[Path]:
    return sorted(
        directory
        for directory in FIXTURES.iterdir()
        if directory.is_dir()
        and directory.name not in NOT_A_PILOT
        and (directory / "pages" / "_index.md").is_file()
    )


PILOT_DIRS = _pilot_dirs()


def _chart_pilots() -> dict[str, dict]:
    values = yaml.safe_load(CHART_VALUES.read_text(encoding="utf-8"))
    return {f"{p['family']}_{p['platform']}": p for p in values["ingestion"]["pilots"]}


def _furnished_title(page_text: str) -> str | None:
    script = REPO_ROOT / "scripts" / "poc" / "pilot_workflow_proof.py"
    spec = importlib.util.spec_from_file_location("pilot_workflow_proof_for_fixture_validity", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.furnished_title(page_text)


def check_loads(page_path: Path) -> dict:
    page = build_chunks._load_furnished_page(page_path)
    assert isinstance(page, dict) and page, f"{page_path} loaded to nothing"
    # The doc-section extractor is the other real consumer: it must accept the page without error.
    extract_doc_sections(page)
    return page


def check_title(page_path: Path, page: dict) -> None:
    title = page.get("title")
    assert isinstance(title, str) and title.strip(), f"{page_path} has no non-empty top-level 'title:'"
    assert _furnished_title(page_path.read_text(encoding="utf-8")) == title.strip(), (
        f"{page_path}: the proof script's furnished_title does not read the page's title"
    )


def check_has_fenced_code(page_path: Path) -> None:
    assert FENCE_RE.search(page_path.read_text(encoding="utf-8")), f"{page_path} has no fenced code block"


def is_generated(page: dict) -> bool:
    """True for a page produced by the README source (it carries ``doc_source: github_readme``)."""
    key, value = GENERATED_MARK
    return page.get(key) == value


def check_pinned_to_chart(pilot_name: str, page: dict, chart: dict[str, dict]) -> None:
    """The commit-pin rule. It binds generated pages only; the original sealed pages are exempt."""
    if not is_generated(page):
        return
    pilot = chart.get(pilot_name)
    if pilot is None:
        return  # the pilot is not in the chart, so there is no pin to compare against
    pinned = (pilot.get("library") or {}).get("commit")
    assert isinstance(pinned, str) and pinned, f"{pilot_name}: the chart pins no library.commit"
    recorded = page.get("doc_source_commit")
    assert recorded is not None, (
        f"{pilot_name}: the page records no doc_source_commit (the chart pins {pinned})"
    )
    assert str(recorded) == pinned, (
        f"{pilot_name}: stale page - doc_source_commit {recorded} is not the chart's pinned library.commit {pinned}"
    )


def test_the_pilot_directories_are_found() -> None:
    names = [directory.name for directory in PILOT_DIRS]
    assert len(names) >= 7, names
    assert NOT_A_PILOT.isdisjoint(names)
    for known in (
        "pdf_net",
        "pdf_java",
        "pdf_go",
        "pdf_typescript",
        "slides_python",
        "cells_rust",
        "cells_cpp",
    ):
        assert known in names


@pytest.mark.parametrize("pilot_dir", PILOT_DIRS, ids=lambda directory: directory.name)
def test_the_page_loads_through_the_real_loader(pilot_dir: Path) -> None:
    check_loads(pilot_dir / "pages" / "_index.md")


@pytest.mark.parametrize("pilot_dir", PILOT_DIRS, ids=lambda directory: directory.name)
def test_the_page_has_a_non_empty_top_level_title(pilot_dir: Path) -> None:
    page_path = pilot_dir / "pages" / "_index.md"
    check_title(page_path, check_loads(page_path))


@pytest.mark.parametrize("pilot_dir", PILOT_DIRS, ids=lambda directory: directory.name)
def test_the_page_contains_a_fenced_code_block(pilot_dir: Path) -> None:
    check_has_fenced_code(pilot_dir / "pages" / "_index.md")


@pytest.mark.parametrize("pilot_dir", PILOT_DIRS, ids=lambda directory: directory.name)
def test_a_page_has_no_doc_source_value_that_would_dodge_the_pin_rule(pilot_dir: Path) -> None:
    """``doc_source`` is either absent (an original sealed page) or exactly ``github_readme``.

    Any other value (a typo such as ``github-readme``) would silently exempt a generated page from
    the pin rule, so it fails here instead.
    """
    page = check_loads(pilot_dir / "pages" / "_index.md")
    assert "doc_source" not in page or is_generated(page), (
        f"{pilot_dir.name}: doc_source is {page['doc_source']!r}, not {GENERATED_MARK[1]!r}"
    )


@pytest.mark.parametrize("pilot_dir", PILOT_DIRS, ids=lambda directory: directory.name)
def test_a_generated_page_is_pinned_to_the_charts_library_commit(pilot_dir: Path) -> None:
    """The pin rule for this committed page: binding if it is generated, exempt if it is not."""
    page = check_loads(pilot_dir / "pages" / "_index.md")
    check_pinned_to_chart(pilot_dir.name, page, _chart_pilots())


def test_a_generated_page_passes_every_one_of_these_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The generator's own output is held to the same rules as the committed pages.

    Built for a real chart entry from an in-memory README (the network is never reached), then
    checked with the very functions the parametrized tests above use.
    """
    chart = _chart_pilots()
    pilot = chart["pdf_go"]
    repository, commit = pilot["library"]["repository"], pilot["library"]["commit"]
    readme = "\n".join(
        [
            "# Demo Library for Go",
            "",
            "Demo Library for Go is a small pure-Go library used to prove the generator.",
            "",
            "## Quick Start",
            "",
            "```go",
            "d := demo.New()",
            "```",
            "",
            *(f"- line {number}" for number in range(40)),
        ]
    )

    def fake_read_repo_document(repo: str, path: str, *, ref: str | None = None, etag: str | None = None):
        assert (repo, path, ref) == (repository, "README.md", commit)
        return DocumentPresent(path=path, sha="f" * 40, size=len(readme), content=readme)

    monkeypatch.setattr(gen, "read_repo_document", fake_read_repo_document)
    page_path = tmp_path / "pdf_go" / "pages" / "_index.md"
    page_path.parent.mkdir(parents=True)
    page_path.write_bytes(
        gen.build_page_from_readme(pilot, gen.readme_doc_fields(repository, commit)).encode()
    )

    page = check_loads(page_path)
    check_title(page_path, page)
    check_has_fenced_code(page_path)
    # The generator marks its pages, so the pin rule binds this one (it is not exempt) ...
    assert is_generated(page)
    assert page["doc_source_commit"] == commit
    check_pinned_to_chart("pdf_go", page, chart)

    # ... and a generated page built from any other commit is stale and must fail it.
    page["doc_source_commit"] = "0" * 40
    with pytest.raises(AssertionError, match="stale page"):
        check_pinned_to_chart("pdf_go", page, chart)
    del page["doc_source_commit"]
    with pytest.raises(AssertionError, match="no doc_source_commit"):
        check_pinned_to_chart("pdf_go", page, chart)


def _synthetic_page(**front_matter: object) -> dict:
    return {"title": "Synthetic", **front_matter}


def test_a_synthetic_generated_page_with_a_wrong_doc_source_commit_fails_the_rule() -> None:
    chart = _chart_pilots()
    pinned = chart["pdf_go"]["library"]["commit"]
    wrong = ("0" if pinned[0] != "0" else "1") + pinned[1:]

    stale = _synthetic_page(doc_source="github_readme", doc_source_commit=wrong)
    with pytest.raises(AssertionError, match="stale page"):
        check_pinned_to_chart("pdf_go", stale, chart)

    missing = _synthetic_page(doc_source="github_readme")
    with pytest.raises(AssertionError, match="no doc_source_commit"):
        check_pinned_to_chart("pdf_go", missing, chart)

    # The right commit passes, so the failures above are the rule working, not a rule that always fails.
    check_pinned_to_chart(
        "pdf_go", _synthetic_page(doc_source="github_readme", doc_source_commit=pinned), chart
    )
    # A generated page for a pilot the chart does not list has no pin to compare against.
    check_pinned_to_chart("not_a_pilot", stale, chart)


def test_a_synthetic_page_without_the_key_is_exempt_from_the_pin_rule() -> None:
    chart = _chart_pilots()
    pinned = chart["pdf_go"]["library"]["commit"]

    # The original sealed pages carry the sealed candidate's own commit, or none: both are exempt.
    check_pinned_to_chart("pdf_go", _synthetic_page(doc_source_commit="0" * 40), chart)
    check_pinned_to_chart("pdf_go", _synthetic_page(), chart)
    # The mark is exactly doc_source: github_readme; the commit alone does not make a page generated.
    assert not is_generated(_synthetic_page(doc_source_commit=pinned))
    assert not is_generated(_synthetic_page(doc_source="sealed"))
    assert is_generated(_synthetic_page(doc_source="github_readme"))
