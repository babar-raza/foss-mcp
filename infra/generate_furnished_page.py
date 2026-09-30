"""Regenerate a pilot's furnished-content page (overview/content/faq only) from
repository-presenter's own local, SEALED candidate README - never from a live
upstream GitHub fetch (G2/TC-119, REQ-G2-047).

Why a local sealed candidate, and not a live fetch. Two independent investigations
(2026-09-28) first confirmed the existing tests/fixtures/furnished/**/pages/_index.md
overview/content/faq content was a verbatim, unreviewed copy of aspose.org's own marketing
pages, then confirmed the project owner's own observation that the live upstream README at
every one of these pilots' pinned commits was ALREADY produced by repository-presenter (or its
legacy predecessor foss-readme-optimizer) - matched structurally section-for-section against
repository-presenter's own documented 20-section README contract. repository-presenter's own
README documents real grounding discipline: it reads the repository's own source, manifests,
tests, examples, license, and releases as ground truth, runs validation against a fixed set of
blocking checks and an independent, non-authoring review before it can be sealed, and proves
reproducibility (byte-identical rerun, zero further LLM calls).

FUTURE MIGRATION PATH (recorded here verbatim per this card's own inputs, so it is never
lost): once repository-presenter gains the ability to push README changes to the real
upstream repositories, and the resulting live commit/README carries an identifying tag or a
``Co-Authored-By: Repository Presenter <...>``-style trailer marking its origin, this script's
source should switch from repository-presenter's local sealed-candidate store to a live fetch
from ``raw.githubusercontent.com/<repository>/<commit>/README.md`` (this card's own original,
now-superseded design) - because a live, tagged, upstream-committed README is a stronger source
once it is verifiable: it is the same file every real consumer of the library also sees. This
exact trigger is also recorded as a formal, schema-tracked open question in
``ops/open_questions.jsonl`` that blocks G2 gate exit until consciously revisited - this
docstring is not the only record of it.

ABSOLUTE CONSTRAINT: this script and its regeneration NEVER touch a fixture's own
``single.block`` - the live source for ``src/foss_mcp/indexing/example_candidates.py``'s
compile-verified example pipeline (TC-066/079/090, ACCEPTED, in production). It edits only the
``overview``/``content``/``faq`` top-level keys (plus a new ``doc_source_commit`` marker), by
direct text surgery on the page's YAML front matter - never a full parse+re-dump of the whole
document, which would risk reformatting (and therefore corrupting the byte-identity of)
``single`` or any other untouched top-level key.

SCOPE: deliberately 5 of the 6 proven pilots (pdf/net, pdf/java, pdf/go, slides/python,
cells/rust). pdf/typescript is excluded - repository-presenter has no sealed candidate for it
yet - and its fixture is left untouched, tracked by its own open question.

Provenance note: a sealed candidate's own commit is NOT necessarily the same commit this
project pins for compile-verified examples (``--library-commit`` elsewhere in this project).
Confirmed directly: pdf/net's sealed candidate is at d10e2c829e1e41d3a9529057ad2ea7c7f53b1095
while this project's own compile-verified-example pin for pdf/net is
b7172877651413cff57a8bfe41fb8a8befb2406b (they differ); slides/python's differs too; pdf/java,
pdf/go, and cells/rust's sealed candidates happen to already match this project's own pins
exactly. The two must never be conflated: this script records the CANDIDATE's own source
commit in each regenerated page's new top-level ``doc_source_commit`` field - never the
compile-verified-example commit, which stays reserved for ``single.block`` and is completely
unaffected by this script.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import yaml

__all__ = [
    "sealed_candidate_doc_fields",
    "top_level_spans",
    "regenerate_pilot_fixture",
]

# (pilot_key, family, platform, fixture_path relative to the project root)
_PILOTS: tuple[tuple[str, str, str, Path], ...] = (
    ("pdf_net", "pdf", "net", Path("tests/fixtures/furnished/pdf_net/pages/_index.md")),
    ("pdf_java", "pdf", "java", Path("tests/fixtures/furnished/pdf_java/pages/_index.md")),
    ("pdf_go", "pdf", "go", Path("tests/fixtures/furnished/pdf_go/pages/_index.md")),
    (
        "slides_python",
        "slides",
        "python",
        Path("tests/fixtures/furnished/slides_python/pages/_index.md"),
    ),
    ("cells_rust", "cells", "rust", Path("tests/fixtures/furnished/cells_rust/pages/_index.md")),
)

_REQUIRED_SECTIONS = (
    "At a Glance",
    "Installation",
    "Dependencies",
    "Quick Start",
    "Key Capabilities",
    "API Reference",
    "Scope and Limitations",
)

_README_H1_RE = re.compile(r"^#[ \t]+(.+?)[ \t]*$", re.MULTILINE)
_README_SECTION_RE = re.compile(r"^##[ \t]+(.+?)[ \t]*$", re.MULTILINE)
_TOP_LEVEL_KEY_RE = re.compile(r"^([A-Za-z0-9_]+):", re.MULTILINE)
_TABLE_RULE_RE = re.compile(r"^[|:\s-]+$")


class _LiteralDumper(yaml.SafeDumper):
    """A SafeDumper that renders any multi-line string as a literal block scalar
    (``|``) - matching this project's own existing furnished-page convention
    (e.g. ``content: |-``) instead of PyYAML's default double-quoted/escaped style.
    """


def _represent_str(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_LiteralDumper.add_representer(str, _represent_str)


def _dump_yaml_block(mapping: dict) -> str:
    """Render *mapping* (e.g. ``{"overview": {...}}``) as a top-level YAML block,
    always ending in exactly one trailing newline.
    """
    return yaml.dump(
        mapping,
        Dumper=_LiteralDumper,
        sort_keys=False,
        allow_unicode=True,
        width=10**9,
        default_flow_style=False,
    )


def top_level_spans(front_matter: str) -> list[tuple[str, str]]:
    """``[(key, raw_text)]`` for each top-level (column-0) ``key:`` in *front_matter*.

    Every span's raw text runs up to (but not including) the next top-level key, so
    concatenating every span's text in order reproduces *front_matter* byte-for-byte.
    This is what lets the rest of this module replace exactly the ``overview``/
    ``content``/``faq`` spans while leaving every other span - most importantly
    ``single`` - untouched, character for character.
    """
    matches = list(_TOP_LEVEL_KEY_RE.finditer(front_matter))
    spans: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(front_matter)
        spans.append((match.group(1), front_matter[start:end]))
    return spans


def _leading_text(front_matter: str) -> str:
    """Whatever precedes the first top-level key in *front_matter* (normally just the
    newline right after the opening ``---`` fence) - not covered by any span in
    ``top_level_spans``, so it must be preserved separately to stay byte-identical.
    """
    match = _TOP_LEVEL_KEY_RE.search(front_matter)
    return front_matter[: match.start()] if match else front_matter


def _parse_readme_sections(readme_text: str) -> dict[str, str]:
    """Map each top-level (``## ``) heading in *readme_text* to its raw body text
    (everything up to the next ``## `` heading, or end of file). A heading's own
    nested ``###``/``####`` subheadings stay embedded in its body - they are real
    structure, not separate top-level sections.
    """
    matches = list(_README_SECTION_RE.finditer(readme_text))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(readme_text)
        sections[match.group(1).strip()] = readme_text[start:end].strip("\n")
    return sections


def _sanitize_section_text(text: str) -> str:
    """Drop pure Markdown table-separator/rule lines (e.g. ``| --- | --- |``) from
    *text*, then refuse (rather than silently mangle) anything that still contains a
    literal ``"---"`` afterwards.

    This is required, not cosmetic: repository-presenter's own sealed README tables
    use ``| --- | --- |`` header-separator rows, and the ALREADY-ACCEPTED, in-production
    ``infra/build_chunks.py::_load_furnished_page`` loads a furnished page's front
    matter with a naive ``text.split("---", 2)`` on the whole file - a stray literal
    ``"---"`` anywhere in embedded content would truncate that split in the wrong place
    and corrupt the page for every real consumer of this fixture format.
    """
    lines = [
        line
        for line in text.splitlines()
        if not (line.strip() and _TABLE_RULE_RE.match(line.strip()) and "-" in line)
    ]
    cleaned = "\n".join(lines).strip("\n")
    if "---" in cleaned:
        raise ValueError(
            "sealed candidate section text still contains a literal '---' after stripping "
            "markdown table-separator rows - refusing to embed it, since it would corrupt "
            "infra/build_chunks.py's accepted _load_furnished_page front-matter parser"
        )
    return cleaned


def _merge_sections(sections: dict[str, str], names: list[str]) -> str:
    """Concatenate *names*' sanitized section bodies into one field, re-adding each
    section's own ``### <name>`` heading UNLESS its body already starts with a heading
    of its own (e.g. Dependencies' body already opens with
    ``### Required Package Dependencies``) - avoids a redundant, near-empty chunk once
    this feeds ``chunk_document``'s heading-based splitting.
    """
    parts: list[str] = []
    for name in names:
        body = _sanitize_section_text(sections[name])
        if body.lstrip().startswith("#"):
            parts.append(body)
        else:
            parts.append(f"### {name}\n\n{body}")
    return "\n\n".join(parts)


def sealed_candidate_doc_fields(repository: str, presenter_root: Path) -> tuple[dict, str]:
    """Read repository-presenter's local, SEALED candidate for *repository* and return
    ``(fields, source_commit)``.

    *repository* is the same ``"org/repo-name"`` string already used for
    ``--library-repository`` elsewhere in this project (e.g.
    ``"aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET"``). It is mapped to
    repository-presenter's own candidate directory naming by replacing ``"/"`` with
    ``"__"`` (confirmed real convention:
    ``"aspose-pdf-foss/Aspose.PDF-FOSS-for-.NET"`` ->
    ``"aspose-pdf-foss__Aspose.PDF-FOSS-for-.NET"``).

    ``<presenter_root>/candidates/<mapped-name>/CURRENT`` (plain text, the sealed
    candidate's own commit hash) is read, then
    ``<presenter_root>/candidates/<mapped-name>/<that-commit>/README.md``. Either
    missing raises a clear, real error naming exactly which repository and path is
    missing - never a fallback to fabricated or cached content.

    *fields* has exactly 3 keys - ``overview``, ``content``, ``faq`` - matching
    ``foss_mcp.indexing.doc_candidates.extract_doc_sections``'s own expected shape:

    * ``overview`` (from the README's own ``## At a Glance`` section) - one candidate.
    * ``content.block`` - two entries: one pair (``title_left``/``content_left`` +
      ``title_right``/``content_right``) merging Installation+Dependencies+Quick Start
      on the left and Key Capabilities+API Reference on the right, and a second,
      left-only entry for Scope and Limitations (mapped toward
      ``search_docs.classify_content_type``'s ``"troubleshooting"`` bucket - its
      hint words ``"known issue"``/``"troubleshoot"`` are woven into the retained body
      text itself, never into a heading that ``chunk_document`` would later strip).
    * ``faq`` - ``{"enable": False, "list": []}``: repository-presenter's own README
      contract has no ``## FAQ`` heading, so real FAQ content does not exist to extract
      here, and none is fabricated.

    ``## Additional Examples`` is real content but is deliberately not folded in here -
    ``single.block`` stays the one and only source for anything on a compile-verification
    path, and folding prose in is optional per this card, not required.
    """
    mapped = repository.replace("/", "__")
    candidate_dir = presenter_root / "candidates" / mapped
    current_path = candidate_dir / "CURRENT"
    if not current_path.is_file():
        raise FileNotFoundError(
            f"sealed candidate CURRENT pointer missing for repository {repository!r}: {current_path}"
        )
    commit = current_path.read_text(encoding="utf-8").strip()
    if not commit:
        raise ValueError(
            f"sealed candidate CURRENT pointer is empty for repository {repository!r}: {current_path}"
        )
    readme_path = candidate_dir / commit / "README.md"
    if not readme_path.is_file():
        raise FileNotFoundError(
            f"sealed candidate README.md missing for repository {repository!r} at commit "
            f"{commit!r}: {readme_path}"
        )
    readme_text = readme_path.read_text(encoding="utf-8")
    sections = _parse_readme_sections(readme_text)

    missing = [name for name in _REQUIRED_SECTIONS if name not in sections]
    if missing:
        raise KeyError(
            f"sealed candidate README for repository {repository!r} at commit {commit!r} is "
            f"missing required section(s) {missing!r}: {readme_path}"
        )

    title_match = _README_H1_RE.search(readme_text)
    product_title = title_match.group(1).strip() if title_match else repository

    overview_content = _sanitize_section_text(sections["At a Glance"])
    install_block = _merge_sections(sections, ["Installation", "Dependencies", "Quick Start"])
    capability_block = _merge_sections(sections, ["Key Capabilities", "API Reference"])

    scope_body = _sanitize_section_text(sections["Scope and Limitations"])
    scope_content = (
        "Known issues, unsupported functionality, and other troubleshooting-relevant scope "
        f"boundaries for {product_title}, straight from its own sealed, reviewed documentation "
        f"candidate:\n\n{scope_body}"
    )

    fields = {
        "overview": {
            "enable": True,
            "title": product_title,
            "content": overview_content,
        },
        "content": {
            "enable": True,
            "block": [
                {
                    "title_left": "Installation, Dependencies, and Quick Start",
                    "content_left": install_block,
                    "title_right": "Key Capabilities and API Reference",
                    "content_right": capability_block,
                },
                {
                    "title_left": "Scope and Limitations",
                    "content_left": scope_content,
                },
            ],
        },
        "faq": {
            "enable": False,
            "list": [],
        },
    }
    return fields, commit


def _apply_fields_to_fixture(fixture_path: Path, fields: dict, source_commit: str) -> str:
    """Rewrite *fixture_path* in place: replace only its ``overview``/``content``/
    ``faq`` top-level YAML keys with freshly rendered text built from *fields*, and
    add/refresh a ``doc_source_commit`` marker recording *source_commit* - every other
    top-level key (most importantly ``single``) is copied through byte-for-byte,
    untouched, because this never parses+re-dumps the whole document.
    """
    original = fixture_path.read_text(encoding="utf-8")
    before, front_matter, after = original.split("---", 2)

    leading = _leading_text(front_matter)
    spans = top_level_spans(front_matter)
    raw_by_key = dict(spans)
    order = [key for key, _ in spans]

    for key in ("overview", "content", "faq"):
        if key not in raw_by_key:
            raise KeyError(f"fixture {fixture_path} has no existing top-level {key!r} key to replace")
        raw_by_key[key] = _dump_yaml_block({key: fields[key]})

    doc_source_commit_text = f"doc_source_commit: {source_commit}\n"
    if "doc_source_commit" in raw_by_key:
        raw_by_key["doc_source_commit"] = doc_source_commit_text
    else:
        raw_by_key["doc_source_commit"] = doc_source_commit_text
        insert_after = "github_url" if "github_url" in order else order[-1]
        insert_index = order.index(insert_after) + 1
        order.insert(insert_index, "doc_source_commit")

    new_front_matter = leading + "".join(raw_by_key[key] for key in order)
    new_text = f"{before}---{new_front_matter}---{after}"
    fixture_path.write_text(new_text, encoding="utf-8")
    return new_text


def _read_repository(family: str, platform: str, project_root: Path) -> str:
    config_path = project_root / "config" / "products" / family / f"{platform}.yaml"
    if not config_path.is_file():
        raise FileNotFoundError(f"product manifest not found: {config_path}")
    manifest = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError(f"product manifest is not a mapping: {config_path}")
    repository = manifest.get("repository")
    if not isinstance(repository, str) or not repository:
        raise ValueError(f"product manifest has no 'repository' string: {config_path}")
    return repository


def regenerate_pilot_fixture(
    family: str,
    platform: str,
    fixture_path: Path,
    *,
    project_root: Path,
    presenter_root: Path,
) -> str:
    """Regenerate one pilot's furnished-page fixture in place; returns the new file text."""
    repository = _read_repository(family, platform, project_root)
    fields, commit = sealed_candidate_doc_fields(repository, presenter_root)
    return _apply_fields_to_fixture(project_root / fixture_path, fields, commit)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository-presenter-root",
        type=Path,
        required=True,
        help="repository-presenter's own local checkout root (its sealed candidates live "
        "under <root>/candidates/<org>__<repo-name>/CURRENT -> <that commit>/README.md)",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="this project's own repository root (defaults to the checkout this script lives in)",
    )
    parser.add_argument(
        "--pilot",
        choices=[pilot_key for pilot_key, *_ in _PILOTS],
        default=None,
        help="regenerate only this pilot's fixture (default: all 5 in-scope pilots)",
    )
    args = parser.parse_args()

    targets = [pilot for pilot in _PILOTS if args.pilot is None or pilot[0] == args.pilot]
    for pilot_key, family, platform, fixture_path in targets:
        regenerate_pilot_fixture(
            family,
            platform,
            fixture_path,
            project_root=args.project_root,
            presenter_root=args.repository_presenter_root,
        )
        print(f"regenerated {pilot_key} from sealed candidate ({fixture_path})")


if __name__ == "__main__":
    main()
