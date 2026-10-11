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

SECOND SOURCE (G3/TC-351, REQ-G3-005, decision OQ-010 - supersedes the "sealed candidates
only" scope above for every pilot beyond those five): ``--source github`` builds a COMPLETE
furnished page for any pilot in the chart from its README at the pinned library commit
(``library.repository`` at ``library.commit`` in ``infra/helm/foss-mcp/values.yaml``), fetched
through the authenticated ``read_repo_document`` reader. The default ``--source sealed`` is the
unchanged behaviour described above. A README too thin to be documentation (no H1, no
language-tagged code block, or under 40 non-blank lines) is REFUSED with exit 3 and nothing is
written - a page is never fabricated. Sections the README lacks are omitted and reported, never
invented. Output is deterministic: the same README bytes give a byte-identical page.

Run with ``src`` on the import path (``PYTHONPATH=src``), exactly as the other ``infra`` scripts
are run; this module also adds it itself when it is run as a script.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

_SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from foss_mcp.extraction.repo_native_reader import DocumentNotPresent, read_repo_document  # noqa: E402

__all__ = [
    "sealed_candidate_doc_fields",
    "top_level_spans",
    "regenerate_pilot_fixture",
    "FencedBlock",
    "ReadmeFields",
    "ReadmeTooThinError",
    "parse_readme",
    "readme_doc_fields",
    "build_page_from_readme",
    "main",
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


# ---------------------------------------------------------------------------
# Second source: the README at the pinned library commit (G3/TC-351, REQ-G3-005)
# ---------------------------------------------------------------------------

README_PATH = "README.md"
MIN_NON_BLANK_LINES = 40
EXIT_FETCH_FAILED = 1
EXIT_REFUSED = 3

_FENCE_TAG_ALIASES = {"ts": "typescript", "cs": "csharp", "js": "javascript", "c++": "cpp"}

# Blocks with these tags stay in the prose but are never turned into example entries: they are
# installation commands, diagrams, manifests and build files, not code that exercises the library.
_NON_EXAMPLE_TAGS = frozenset({"bash", "shell", "mermaid", "xml", "toml", "cmake", "powershell", "groovy"})

_PLATFORM_DISPLAY = {
    "net": ".NET",
    "cpp": "C++",
    "nodejs": "Node.js",
    "typescript": "TypeScript",
    "javascript": "JavaScript",
    "python": "Python",
    "java": "Java",
    "go": "Go",
    "rust": "Rust",
}

# Sections whose body is merged into the first and second halves of the first content block.
_LEFT_SECTIONS = ("Installation", "Dependencies", "Quick Start")
_RIGHT_SECTIONS = ("Key Capabilities", "API Reference")
_SCOPE_SECTION = "Scope and Limitations"

_FENCE_OPEN_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
_ATX_HEADING_RE = re.compile(r"^ {0,3}(?P<hashes>#{1,6})[ \t]+(?P<text>.+?)(?:[ \t]+#+)?[ \t]*$")
_SETEXT_H1_UNDERLINE_RE = re.compile(r"^ {0,3}=+[ \t]*$")
_FENCE_TAG_RE = re.compile(r"^\{?\.?(?P<tag>[A-Za-z0-9][A-Za-z0-9_+#.\-]*)")
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_HTML_TAG_RE = re.compile(r"<[^>\n]+>")
_EMPHASIS_RE = re.compile(r"(\*\*|__)(.+?)\1")
_ORDERED_ITEM_RE = re.compile(r"^\d+[.)][ \t]")
_HYPHEN_BEFORE_PAIR_RE = re.compile(r"-(?=--)")
_FOR_PLATFORM_SUFFIX_RE = re.compile(r"-for-.+$", re.IGNORECASE)

_MIN_SUMMARY_CHARS = 20
_HEAD_DESCRIPTION_CHARS = 160


class ReadmeTooThinError(ValueError):
    """The README is absent, or too thin to be documentation: refused, never fabricated around."""


@dataclass(frozen=True)
class FencedBlock:
    """One language-tagged fenced code block of the README."""

    language: str  # the normalised fence tag, e.g. "typescript" for a "ts" fence
    code: str
    heading: str  # plain text of the nearest preceding heading, "" when there is none
    description: str  # the prose paragraph just before the fence, "" when there is none

    @property
    def is_example(self) -> bool:
        """True when this block becomes a ``single.block`` entry (see ``_NON_EXAMPLE_TAGS``).

        A block whose code itself holds a triple backtick cannot be re-fenced in the page (the
        example extractor would cut it short), so it stays prose only.
        """
        return self.language not in _NON_EXAMPLE_TAGS and bool(self.code.strip()) and "```" not in self.code


@dataclass(frozen=True)
class ReadmeFields:
    """What was parsed from one README: ready for ``build_page_from_readme``."""

    repository: str
    commit: str
    title: str
    summary: str
    sections: Mapping[str, str]  # canonical required-section name -> neutralised body (found only)
    blocks: tuple[FencedBlock, ...]  # every non-empty language-tagged fenced block, in README order
    non_blank_lines: int

    @property
    def found(self) -> tuple[str, ...]:
        return tuple(name for name in _REQUIRED_SECTIONS if name in self.sections)

    @property
    def missing(self) -> tuple[str, ...]:
        return tuple(name for name in _REQUIRED_SECTIONS if name not in self.sections)

    @property
    def example_count(self) -> int:
        return sum(1 for block in self.blocks if block.is_example)


@dataclass(frozen=True)
class _Fence:
    start: int  # index of the opening line
    end: int  # index of the closing line (the last line when the fence is never closed)
    info: str
    code: str


def _plain_inline(text: str) -> str:
    """*text* with Markdown images, links, HTML tags, emphasis and backticks reduced to plain words."""
    text = _IMAGE_RE.sub("", text)
    text = _LINK_RE.sub(r"\1", text)
    text = _HTML_TAG_RE.sub("", text)
    text = _EMPHASIS_RE.sub(r"\2", text)
    return " ".join(text.replace("`", "").split())


def _fence_opener(line: str) -> re.Match[str] | None:
    match = _FENCE_OPEN_RE.match(line)
    if match is None:
        return None
    # A backtick fence's info string may not itself hold a backtick (``` inline ``` is prose).
    if match.group("fence")[0] == "`" and "`" in match.group("info"):
        return None
    return match


def _closes_fence(line: str, marker: str) -> bool:
    stripped = line.strip()
    return len(stripped) >= len(marker) and set(stripped) == {marker[0]}


def _dedent(body: Sequence[str], indent: int) -> str:
    if not indent:
        return "\n".join(body)
    pattern = re.compile(rf"^[ \t]{{0,{indent}}}")
    return "\n".join(pattern.sub("", line) for line in body)


def _trim_blank_lines(code: str) -> str:
    lines = code.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def _scan_markdown(lines: Sequence[str]) -> tuple[list[tuple[int, int, str]], list[_Fence]]:
    """``(headings, fences)`` of *lines*, where a heading is ``(line index, level, plain text)``.

    Fence-aware: a ``#`` line inside a fenced block (a shell comment, a Python comment) is code,
    never a heading. Setext ``=`` underlines are read as H1.
    """
    headings: list[tuple[int, int, str]] = []
    fences: list[_Fence] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        opener = _fence_opener(line)
        if opener is not None:
            marker = opener.group("fence")
            body: list[str] = []
            cursor = index + 1
            while cursor < len(lines) and not _closes_fence(lines[cursor], marker):
                body.append(lines[cursor])
                cursor += 1
            end = min(cursor, len(lines) - 1)
            fences.append(
                _Fence(index, end, opener.group("info").strip(), _dedent(body, len(opener.group("indent"))))
            )
            index = end + 1
            continue
        atx = _ATX_HEADING_RE.match(line)
        if atx is not None:
            text = _plain_inline(atx.group("text"))
            if text:
                headings.append((index, len(atx.group("hashes")), text))
        elif (
            line.strip()
            and index + 1 < len(lines)
            and _SETEXT_H1_UNDERLINE_RE.match(lines[index + 1])
            and not line.lstrip().startswith(("#", "|", ">", "<", "-", "*", "+"))
        ):
            text = _plain_inline(line)
            if text:
                headings.append((index, 1, text))
            index += 1
        index += 1
    return headings, fences


def _neutralise_markdown(text: str) -> str:
    """*text* made safe to embed, WITHOUT touching a single character inside a fenced block.

    Outside fences, pure table-separator / rule lines (``| --- | --- |``, ``-----``) are dropped
    (the same rule ``_sanitize_section_text`` applies) and trailing whitespace is trimmed. Inside
    fences nothing changes: a code block's own ``---`` line is real content, and is protected at
    render time by ``_render_page`` instead.
    """
    kept: list[str] = []
    marker: str | None = None
    for line in text.split("\n"):
        if marker is not None:
            kept.append(line)
            if _closes_fence(line, marker):
                marker = None
            continue
        opener = _fence_opener(line)
        if opener is not None:
            marker = opener.group("fence")
            kept.append(line)
            continue
        stripped = line.strip()
        if stripped and _TABLE_RULE_RE.match(stripped) and "-" in stripped:
            continue
        kept.append(line.rstrip())
    return "\n".join(kept).strip("\n")


def _section_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _fence_language(info: str) -> str:
    match = _FENCE_TAG_RE.match(info.strip())
    if match is None:
        return ""
    tag = match.group("tag").lower()
    return _FENCE_TAG_ALIASES.get(tag, tag)


def _last_paragraph(lines: Sequence[str]) -> str:
    paragraphs: list[list[str]] = [[]]
    for line in lines:
        if _SETEXT_H1_UNDERLINE_RE.match(line):
            continue
        if line.strip():
            paragraphs[-1].append(line.rstrip())
        elif paragraphs[-1]:
            paragraphs.append([])
    last = next((paragraph for paragraph in reversed(paragraphs) if paragraph), [])
    return _neutralise_markdown("\n".join(last)).replace("```", "").replace("~~~", "").strip()


def _first_paragraph(lines: Sequence[str], skip: set[int], start: int, stop: int) -> str:
    """The first prose paragraph in ``lines[start:stop]`` that reads as a description."""
    paragraphs: list[list[str]] = [[]]
    for index in range(start, stop):
        line = "" if index in skip else lines[index].strip()
        if line:
            paragraphs[-1].append(line)
        elif paragraphs[-1]:
            paragraphs.append([])
    for paragraph in paragraphs:
        if not paragraph or paragraph[0].startswith(("<", "![", "[![", "|", "- ", "* ", "+ ", "#")):
            continue
        if _ORDERED_ITEM_RE.match(paragraph[0]):
            continue
        plain = _plain_inline(" ".join(line.lstrip("> ").strip() for line in paragraph))
        if len(plain) >= _MIN_SUMMARY_CHARS and any(ch.isalpha() for ch in plain):
            return plain
    return ""


def parse_readme(repository: str, commit: str, text: str) -> ReadmeFields:
    """Parse README *text*; raise ``ReadmeTooThinError`` when it is too thin to be documentation.

    Too thin means: no H1, no non-empty language-tagged fenced code block, or fewer than
    ``MIN_NON_BLANK_LINES`` non-blank lines (imaging/net is the known case). Every reason that
    applies is named in the message.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    lines = text.split("\n")
    non_blank = sum(1 for line in lines if line.strip())
    headings, fences = _scan_markdown(lines)

    h1 = next((heading for heading in headings if heading[1] == 1), None)
    title = h1[2] if h1 is not None else ""

    blocks: list[FencedBlock] = []
    for fence in fences:
        language = _fence_language(fence.info)
        code = _trim_blank_lines(fence.code)
        if not language or not code:
            continue
        before = [heading for heading in headings if heading[0] < fence.start]
        previous_fence_end = max((other.end for other in fences if other.end < fence.start), default=-1)
        boundary = max(before[-1][0] if before else -1, previous_fence_end)
        blocks.append(
            FencedBlock(
                language=language,
                code=code,
                heading=before[-1][2] if before else "",
                description=_last_paragraph(lines[boundary + 1 : fence.start]),
            )
        )

    reasons: list[str] = []
    if non_blank < MIN_NON_BLANK_LINES:
        reasons.append(f"only {non_blank} non-blank lines (at least {MIN_NON_BLANK_LINES} are needed)")
    if not title:
        reasons.append("no H1 title")
    if not blocks:
        reasons.append("no language-tagged code block")
    if reasons:
        raise ReadmeTooThinError(
            f"{repository}@{commit}: the README is too thin to be documentation - " + "; ".join(reasons)
        )

    level_two = {
        _section_key(name): index
        for index, level, name in reversed(headings)
        if level == 2  # reversed, so the FIRST heading of a repeated name wins
    }
    sections: dict[str, str] = {}
    for name in _REQUIRED_SECTIONS:
        start = level_two.get(_section_key(name))
        if start is None:
            continue
        stop = next((index for index, level, _ in headings if index > start and level <= 2), len(lines))
        body = _neutralise_markdown("\n".join(lines[start + 1 : stop]))
        if body.strip():
            sections[name] = body

    assert h1 is not None
    after_h1 = h1[0] + (
        2 if h1[0] + 1 < len(lines) and _SETEXT_H1_UNDERLINE_RE.match(lines[h1[0] + 1]) else 1
    )
    next_heading = next((index for index, _, _ in headings if index >= after_h1), len(lines))
    skip = {index for fence in fences for index in range(fence.start, fence.end + 1)}
    summary = _first_paragraph(lines, skip, after_h1, next_heading)

    return ReadmeFields(
        repository=repository,
        commit=commit,
        title=title,
        summary=summary,
        sections=sections,
        blocks=tuple(blocks),
        non_blank_lines=non_blank,
    )


def readme_doc_fields(repository: str, commit: str) -> ReadmeFields:
    """Fetch ``README.md`` of *repository* at *commit* and return its parsed sections and code blocks.

    The fetch goes through ``read_repo_document`` (authenticated by ``GITHUB_TOKEN`` with bounded
    backoff). A README that does not exist at that commit is refused like a thin one.
    """
    result = read_repo_document(repository, README_PATH, ref=commit)
    if isinstance(result, DocumentNotPresent):
        raise ReadmeTooThinError(
            f"{repository}@{commit}: there is no {README_PATH} at this commit, so there is nothing to build "
            "documentation from"
        )
    return parse_readme(repository, commit, result.content)


def _join_names(names: Sequence[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return f"{', '.join(names[:-1])}, and {names[-1]}"


def _merge_readme_sections(sections: Mapping[str, str], names: Sequence[str]) -> str:
    """Concatenate *names*' bodies, re-adding each ``### <name>`` unless the body opens with a heading."""
    parts: list[str] = []
    for name in names:
        body = sections[name]
        parts.append(body if body.lstrip().startswith("#") else f"### {name}\n\n{body}")
    return "\n\n".join(parts)


def _pilot_identity(pilot: Mapping[str, object]) -> tuple[str, str, str, str]:
    """``(family, platform, repository, commit)`` of a chart ``ingestion.pilots`` entry."""
    library = pilot.get("library")
    if not isinstance(library, Mapping):
        raise ValueError("the pilot has no 'library' mapping, so its repository and commit are unknown")
    values: list[str] = []
    for label, source in (
        ("family", pilot),
        ("platform", pilot),
        ("library.repository", library),
        ("library.commit", library),
    ):
        value = source.get(label.rpartition(".")[2])
        if not isinstance(value, str) or not value:
            raise ValueError(f"the pilot has no {label} string")
        values.append(value)
    family, platform, repository, commit = values
    return family, platform, repository, commit


def _family_name(title: str, repository: str) -> str:
    """``Aspose.PDF FOSS`` from the title ``Aspose.PDF FOSS for Go``, else from the repository name."""
    head, separator, _ = title.rpartition(" for ")
    if separator and head.strip():
        return head.strip()
    name = _FOR_PLATFORM_SUFFIX_RE.sub("", repository.rsplit("/", 1)[-1])
    return " ".join(name.replace("-", " ").replace("_", " ").split())


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[: limit - 3].rsplit(" ", 1)[0].rstrip(" ,;:.")
    return f"{cut}..."


class _PageDumper(yaml.SafeDumper):
    """A SafeDumper for whole generated pages.

    Multi-line strings render as literal blocks. A string holding ``---`` is forced into
    double-quoted style so ``_render_page`` can escape its hyphens (YAML reads ``\\x2D`` as ``-``),
    which keeps the content byte-for-byte while the page text never holds a literal ``---``.
    """

    def ignore_aliases(self, data: object) -> bool:
        return True


def _represent_page_str(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    if "---" in data:
        style = '"'
    elif "\n" in data:
        style = "|"
    else:
        style = None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_PageDumper.add_representer(str, _represent_page_str)


def _render_page(front_matter: dict) -> str:
    """The whole page text for *front_matter*, proved to load back to exactly *front_matter*.

    ``infra/build_chunks.py::_load_furnished_page`` splits the file on the first two ``---``
    substrings, so any other ``---`` in embedded content (a YAML code block's document marker, a
    Markdown rule inside a code sample) would truncate the page for every consumer. Each hyphen
    that starts a run of three is therefore written as the YAML escape ``\\x2D`` inside a
    double-quoted scalar: the loaded text is unchanged, the file text has exactly the two fences.
    """
    body = yaml.dump(
        front_matter,
        Dumper=_PageDumper,
        sort_keys=False,
        allow_unicode=True,
        width=10**9,
        default_flow_style=False,
    )
    page = "---\n" + _HYPHEN_BEFORE_PAIR_RE.sub(r"\\x2D", body) + "---\n"
    if page.count("---") != 2:
        raise ValueError("the generated page still holds a literal '---' beyond its two front-matter fences")
    if yaml.safe_load(page.split("---", 2)[1]) != front_matter:
        raise ValueError("the generated page does not load back to the content it was built from")
    return page


def build_page_from_readme(pilot: Mapping[str, object], fields: ReadmeFields) -> str:
    """The whole furnished-page text for *pilot* (a chart ``ingestion.pilots`` entry) from *fields*.

    Nothing is invented: a section the README lacks is omitted (and listed by ``fields.missing``),
    ``Dependencies`` is optional, and a block of the page with no source content is left out or
    disabled. The same README always gives the byte-identical page.
    """
    family, platform, repository, commit = _pilot_identity(pilot)
    if fields.commit != commit:
        raise ValueError(
            f"the README was read at {fields.commit!r} but the chart pins {commit!r} for {family}/{platform}; "
            "a page may only claim the commit its README was read at"
        )
    title = fields.title
    description = fields.summary or title
    platform_name = _PLATFORM_DISPLAY.get(platform, platform.capitalize())

    left = [name for name in _LEFT_SECTIONS if name in fields.sections]
    right = [name for name in _RIGHT_SECTIONS if name in fields.sections]
    blocks: list[dict[str, str]] = []
    if left or right:
        pair: dict[str, str] = {}
        if left:
            pair["title_left"] = _join_names(left)
            pair["content_left"] = _merge_readme_sections(fields.sections, left)
        if right:
            pair["title_right"] = _join_names(right)
            pair["content_right"] = _merge_readme_sections(fields.sections, right)
        blocks.append(pair)
    if _SCOPE_SECTION in fields.sections:
        blocks.append(
            {
                "title_left": _SCOPE_SECTION,
                "content_left": (
                    "Known issues, unsupported functionality, and other troubleshooting-relevant scope "
                    f"boundaries for {title}, straight from its README at the pinned commit:\n\n"
                    f"{fields.sections[_SCOPE_SECTION]}"
                ),
            }
        )

    examples: list[dict[str, str]] = []
    seen_titles: dict[str, int] = {}
    for block in fields.blocks:
        if not block.is_example:
            continue
        base = block.heading or f"{block.language} example"
        seen_titles[base] = seen_titles.get(base, 0) + 1
        example_title = base if seen_titles[base] == 1 else f"{base} ({seen_titles[base]})"
        fence = f"```{block.language}\n{block.code}\n```\n"
        examples.append(
            {
                "title": example_title,
                "content": f"{block.description}\n\n{fence}" if block.description else fence,
            }
        )

    front_matter: dict[str, object] = {
        "layout": "plugin",
        "family_name": _family_name(title, repository),
        "plugin_description": description,
        "plugin_platform": platform_name,
        "head_title": f"{title} | Open-Source Library",
        "head_description": _truncate(description, _HEAD_DESCRIPTION_CHARS),
        "title": title,
        "description": description,
        "submenu": {"enable": True},
        "github_url": f"https://github.com/{repository}",
        "doc_source_commit": commit,
        "overview": (
            {"enable": True, "title": title, "content": fields.sections["At a Glance"]}
            if "At a Glance" in fields.sections
            else {"enable": False}
        ),
        "content": {"enable": bool(blocks), "block": blocks},
        "single": {"enable": bool(examples), "block": examples},
        "faq": {"enable": False, "list": []},
    }
    return _render_page(front_matter)


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def _load_chart_pilots(project_root: Path) -> dict[str, Mapping[str, object]]:
    """The chart's ``ingestion.pilots`` entries keyed ``family_platform`` (e.g. ``pdf_net``)."""
    values_path = project_root / "infra" / "helm" / "foss-mcp" / "values.yaml"
    values = yaml.safe_load(values_path.read_text(encoding="utf-8")) or {}
    ingestion = values.get("ingestion") if isinstance(values, dict) else None
    entries = ingestion.get("pilots") if isinstance(ingestion, dict) else None
    pilots: dict[str, Mapping[str, object]] = {}
    for entry in entries or []:
        if isinstance(entry, dict) and entry.get("family") and entry.get("platform"):
            pilots[f"{entry['family']}_{entry['platform']}"] = entry
    return pilots


def _fixture_path(project_root: Path, pilot_key: str) -> Path:
    return project_root / "tests" / "fixtures" / "furnished" / pilot_key / "pages" / "_index.md"


def _listing(names: Sequence[str]) -> str:
    return ", ".join(names) if names else "none"


def _run_github(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    try:
        chart = _load_chart_pilots(args.project_root)
    except (OSError, yaml.YAMLError) as exc:
        parser.error(
            f"cannot read ingestion.pilots from the chart under {args.project_root}: {type(exc).__name__}"
        )
    keys = list(dict.fromkeys(args.pilot)) if args.pilot else list(chart)
    for key in keys:
        if key not in chart:
            parser.error(
                f"unknown pilot {key!r}: it is not in the chart's ingestion.pilots ({_listing(sorted(chart))})"
            )

    refused = failed = 0
    for key in keys:
        pilot = chart[key]
        target = _fixture_path(args.project_root, key)
        try:
            _, _, repository, commit = _pilot_identity(pilot)
            fields = readme_doc_fields(repository, commit)
            if args.strict and fields.missing:
                raise ReadmeTooThinError(
                    f"{repository}@{commit}: --strict needs all seven sections, missing {_listing(fields.missing)}"
                )
            page = build_page_from_readme(pilot, fields)
        except ReadmeTooThinError as exc:
            print(f"{key}: REFUSED, nothing written - {exc}", file=sys.stderr)
            refused += 1
            continue
        except OSError as exc:
            # Only the exception's type (and an HTTP status): its text is never echoed.
            status = getattr(exc, "code", None)
            detail = f" (HTTP {status})" if isinstance(status, int) else ""
            print(
                f"{key}: FAILED, nothing written - could not read the README: {type(exc).__name__}{detail}",
                file=sys.stderr,
            )
            failed += 1
            continue
        except ValueError as exc:
            print(f"{key}: FAILED, nothing written - {exc}", file=sys.stderr)
            failed += 1
            continue

        data = page.encode("utf-8")
        if args.dry_run:
            verb = "would write"
        else:
            verb = "replaced" if target.exists() else "wrote"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)  # bytes, so "\n" stays "\n" on every platform
        shown = target.relative_to(args.project_root).as_posix()
        print(
            f"{key}: {verb} {shown} - sections found: {_listing(fields.found)}; "
            f"missing: {_listing(fields.missing)}; examples: {fields.example_count}; {len(data)} bytes"
        )
    if failed:
        return EXIT_FETCH_FAILED
    return EXIT_REFUSED if refused else 0


def _run_sealed(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.dry_run or args.strict:
        parser.error("--dry-run and --strict apply only to --source github")
    if args.repository_presenter_root is None:
        parser.error("--repository-presenter-root is required with --source sealed")
    known = [pilot_key for pilot_key, *_ in _PILOTS]
    for requested in args.pilot or []:
        if requested not in known:
            parser.error(f"unknown pilot {requested!r} for --source sealed (choose from {', '.join(known)})")

    targets = [pilot for pilot in _PILOTS if not args.pilot or pilot[0] in args.pilot]
    for pilot_key, family, platform, fixture_path in targets:
        regenerate_pilot_fixture(
            family,
            platform,
            fixture_path,
            project_root=args.project_root,
            presenter_root=args.repository_presenter_root,
        )
        print(f"regenerated {pilot_key} from sealed candidate ({fixture_path})")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--source",
        choices=("sealed", "github"),
        default="sealed",
        help="sealed (default, unchanged): regenerate the five sealed-candidate pilots from "
        "repository-presenter's local checkout. github: build a page from each pilot's README at "
        "its pinned library commit, through the authenticated GitHub reader (the only mode that "
        "touches the network)",
    )
    parser.add_argument(
        "--repository-presenter-root",
        type=Path,
        default=None,
        help="--source sealed only, and required there: repository-presenter's own local checkout root "
        "(its sealed candidates live under <root>/candidates/<org>__<repo-name>/CURRENT -> "
        "<that commit>/README.md)",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="this project's own repository root (defaults to the checkout this script lives in)",
    )
    parser.add_argument(
        "--pilot",
        action="append",
        metavar="FAMILY_PLATFORM",
        default=None,
        help="only this pilot, e.g. pdf_net (repeatable). With --source github it must be one of the "
        "chart's ingestion.pilots, which supplies the repository and the commit; with none given, "
        "every chart pilot. With --source sealed it must be one of the 5 in-scope pilots "
        "(default: all 5)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="--source github only: fetch and report, but write nothing",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="--source github only: refuse (exit 3) a README that lacks any of the seven sections",
    )
    args = parser.parse_args(argv)

    if args.source == "github":
        code = _run_github(parser, args)
        if code:
            raise SystemExit(code)
        return
    _run_sealed(parser, args)


if __name__ == "__main__":
    main()
