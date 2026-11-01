"""Cross-check a furnished page's own FAQ/content text against its pilot's real
``LoadFormat``/``SaveFormat`` enum data (G3/TC-648, REQ-G3-013).

The round-2 content audit found one confirmed fabricated claim across all 32 pilots
(``CELLS-CPP-001``): cells/cpp's own committed FAQ answer said "``.csv`` load is
supported" while the real, already-extracted ``LoadFormat`` enum in its
``api_surface.json`` has no ``Csv`` member at all (members are only ``{Auto, Xlsx}``),
matching the README's own "Scope and Limitations" section and a live runtime repro. The
audit's own ``suggested_fix`` named the general remedy: "an automated cross-check that
flags FAQ/doc assertions that contradict the formats/limitations section or the enum
list." This module is that cross-check.

A furnished page's overview/content/faq text is turned into ``DocCandidate`` records by
``foss_mcp.indexing.doc_candidates.extract_doc_sections`` — an already-accepted sibling
module in this same ``indexing`` layer, reused here rather than reinvented. Each
candidate's body is split into clauses and scanned for a *positive* support claim: a
known format extension (``csv``, ``xlsx``, ``pdf``, ...) co-occurring with a
support-claiming verb (``support``/``read``/``write``/``load``/``save``/``export``/
``import``) and no negation marker in the same clause. A positive claim is a
contradiction exactly when the claimed format's name has no case-insensitive match among
the pilot's own ``LoadFormat``/``SaveFormat`` enum member names (read straight from its
committed ``api_surface.json`` — real, already-extracted ground truth, never re-derived).

This module intentionally does NOT import ``foss_mcp.mcp.tools.get_product_reference``'s
own ``_FORMAT_WORDS``/``_FORMAT_VERB_RE``/``_format_statements``, even though they cover
the same ground, and reimplements a small, focused version of the same idea locally
instead. Confirmed directly (not assumed) before writing this module:
``get_product_reference.py`` itself imports ``foss_mcp.indexing.known_defects`` — so
``indexing`` is a LOWER layer that ``mcp.tools`` already depends on, and nothing under
``indexing/`` imports from ``mcp/`` today. Importing ``get_product_reference``'s private
regexes from here would be a new, backwards layering dependency — the identical
"reimplement, never cross-layer-import" convention ``known_defects.py``'s own docstring
documents for its own version-comparison helper (see that module's docstring, decision C).
The same reasoning applies to ``infra.build_chunks._load_furnished_page``: ``infra``
imports FROM ``foss_mcp.indexing`` (confirmed: ``infra/build_chunks.py`` imports
``extract_doc_sections`` from this package), so this module reimplements that function's
own tiny, three-line front-matter-parsing pattern (split on the first two ``---`` fences,
``yaml.safe_load``) locally rather than importing it.

This is a *small, focused* extraction, not a full natural-language claim checker: it is
deliberately conservative (it never guesses at a direction it cannot read off the clause
itself) and is scoped to the one documented failure mode — a format-support claim with no
matching enum member — rather than every way prose could misstate capability.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from foss_mcp.indexing.doc_candidates import extract_doc_sections

__all__ = [
    "Contradiction",
    "claimed_supported_formats",
    "enum_supported_formats",
    "check_page_text",
    "check_pilot",
    "discover_pilots",
    "sweep_all_pilots",
]

# src/foss_mcp/indexing/claim_check.py -> parents[3] is the repository root (same
# convention as cached_embedding_provider.py's _DEFAULT_ROOT and known_defects.py's
# REGISTRY_DIR in this same package).
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FURNISHED_ROOT = REPO_ROOT / "tests" / "fixtures" / "furnished"
DEFAULT_API_SURFACE_ROOT = REPO_ROOT / "tests" / "fixtures"

# A short, known list of format extensions a FAQ/content answer might claim support for.
# Deliberately not exhaustive (the card itself says this is enough): the goal is to catch
# the CELLS-CPP-001 failure class, not to parse arbitrary prose.
_FORMAT_EXTENSIONS = (
    "xlsx",
    "xlsb",
    "xls",
    "csv",
    "ods",
    "pdf",
    "docx",
    "doc",
    "pptx",
    "ppt",
    "odp",
    "odt",
    "rtf",
    "txt",
    "xml",
    "json",
    "html",
    "epub",
)
_FORMAT_WORD_RE = re.compile(r"\b(" + "|".join(_FORMAT_EXTENSIONS) + r")\b", re.IGNORECASE)

# A support-claiming verb. Deliberately a short list (the card's own suggestion), not a
# reuse of get_product_reference._FORMAT_VERB_RE (see module docstring for why not).
_SUPPORT_VERB_RE = re.compile(
    r"\b(?:support|read|write|load|save|export|import)(?:s|ed|ing)?\b", re.IGNORECASE
)
_LOAD_WORD_RE = re.compile(r"\bload(?:s|ed|ing)?\b", re.IGNORECASE)
_SAVE_WORD_RE = re.compile(r"\bsave(?:s|d|ing)?\b", re.IGNORECASE)

_NEGATION_RE = re.compile(
    r"\bnot\b|\bno\b|\bn't\b|\bnever\b|\bwithout\b|\bunsupported\b|\bunavailable\b",
    re.IGNORECASE,
)

# Splits clause text on a sentence-ending period (one followed by whitespace or end of
# string — the period inside a format token like "`.csv`" is immediately followed by a
# letter, never whitespace, so it is never a split point), a semicolon, or a newline.
_CLAUSE_SPLIT_RE = re.compile(r"\.(?=\s|$)|;|\n")

_NOT_A_PILOT = frozenset({"sealed_current"})


@dataclass(frozen=True)
class Contradiction:
    """One FAQ/content clause that claims support for a format with no matching
    ``LoadFormat``/``SaveFormat`` enum member in the pilot's own ``api_surface.json``."""

    pilot: str
    origin: str
    title: str
    format_name: str
    direction: str  # "load", "save", or "load/save" (ambiguous — checked against both)
    clause: str

    def describe(self) -> str:
        return (
            f"{self.pilot} [{self.origin}] {self.title!r}: claims {self.format_name!r} "
            f"{self.direction} is supported but no matching enum member exists "
            f"(clause: {self.clause!r})"
        )


def _clauses(text: str) -> list[str]:
    return [clause.strip() for clause in _CLAUSE_SPLIT_RE.split(text) if clause.strip()]


def _direction(clause: str) -> str:
    has_load = bool(_LOAD_WORD_RE.search(clause))
    has_save = bool(_SAVE_WORD_RE.search(clause))
    if has_load and not has_save:
        return "load"
    if has_save and not has_load:
        return "save"
    return "load/save"


def claimed_supported_formats(text: str) -> list[tuple[str, str, str]]:
    """Return ``(format_name, direction, clause)`` for every clause of ``text`` that makes
    a positive support claim: a known format extension and a support-claiming verb in the
    same clause, with no negation marker anywhere in that clause.

    ``direction`` is ``"load"`` or ``"save"`` when the clause itself says which, and
    ``"load/save"`` when it says neither explicitly (e.g. a bare "CSV export is
    supported") — in that ambiguous case the caller checks both enums and only flags a
    contradiction when the format matches neither.
    """
    claims: list[tuple[str, str, str]] = []
    for clause in _clauses(text):
        if not _SUPPORT_VERB_RE.search(clause):
            continue
        if _NEGATION_RE.search(clause):
            continue
        direction = _direction(clause)
        seen: set[str] = set()
        for match in _FORMAT_WORD_RE.finditer(clause):
            name = match.group(1).lower()
            if name in seen:
                continue
            seen.add(name)
            claims.append((name, direction, clause))
    return claims


def _enum_member_names(api_surface: dict, enum_name: str) -> set[str] | None:
    """Return the lowercase member names of the ``enum_specifier`` named ``enum_name`` in
    ``api_surface``'s ``types`` list, or ``None`` when no such enum exists there at all.

    ``None`` (enum absent) and ``set()`` (enum present but empty) are deliberately distinct:
    a pilot whose API genuinely has no ``LoadFormat``/``SaveFormat`` enum at all (true for 30
    of the 32 committed pilots — this naming convention is a ``cells``/``slides`` convention,
    not a universal one; confirmed directly by reading every pilot's ``api_surface.json``
    before writing this module) carries no ground truth this check is built to compare
    against, so the caller must treat that direction as not-applicable rather than as
    "nothing is supported" — the latter would flag every real, true format-support
    statement in those 30 pilots' prose as a contradiction (confirmed by running an earlier,
    ungated version of this module against all 32 pilots: it produced hundreds of false
    positives, every one of them in a pilot with no ``LoadFormat``/``SaveFormat`` enum at
    all). An enum that genuinely exists but has zero members, by contrast, is itself real
    ground truth ("nothing is affirmatively known-supported") and correctly yields ``set()``.
    """
    for entry in api_surface.get("types", []):
        if not isinstance(entry, dict):
            continue
        if entry.get("kind") != "enum_specifier" or entry.get("name") != enum_name:
            continue
        names: set[str] = set()
        for member in entry.get("enum_members", []):
            if isinstance(member, dict):
                member_name = member.get("name")
                if isinstance(member_name, str):
                    names.add(member_name.lower())
        return names
    return None


def enum_supported_formats(api_surface: dict) -> tuple[set[str] | None, set[str] | None]:
    """Return ``(load_formats, save_formats)``: the lowercase format names a pilot's own
    ``LoadFormat``/``SaveFormat`` enum members name, read straight from its
    ``api_surface.json``'s ``types`` list. Either element is ``None`` when that enum does not
    exist in this pilot's ``api_surface.json`` at all (see ``_enum_member_names`` for why
    absence must not be read as "nothing is supported")."""
    return _enum_member_names(api_surface, "LoadFormat"), _enum_member_names(api_surface, "SaveFormat")


def check_page_text(pilot: str, origin: str, title: str, text: str, api_surface: dict) -> list[Contradiction]:
    """Scan one piece of furnished-page text for format-support claims that contradict
    ``api_surface``'s own ``LoadFormat``/``SaveFormat`` enum data.

    A claim is checked only in a direction whose enum actually exists in ``api_surface``
    (see ``enum_supported_formats``): a pilot with no ``LoadFormat`` enum at all is never
    flagged for a "load" claim, regardless of what it claims, because there is no extracted
    ground truth here to contradict it with. An ambiguous ("load/save") claim is checked
    against the union of whichever of the two enums IS present, and is skipped only when
    neither is present.
    """
    load_formats, save_formats = enum_supported_formats(api_surface)
    found: list[Contradiction] = []
    for format_name, direction, clause in claimed_supported_formats(text):
        if direction == "load":
            if load_formats is None:
                continue
            ok = format_name in load_formats
        elif direction == "save":
            if save_formats is None:
                continue
            ok = format_name in save_formats
        else:
            if load_formats is None and save_formats is None:
                continue
            ok = format_name in (load_formats or set()) or format_name in (save_formats or set())
        if not ok:
            found.append(Contradiction(pilot, origin, title, format_name, direction, clause))
    return found


def _load_frontmatter(page_path: Path) -> dict:
    """Parse a furnished page's Hugo front matter: split on the first two ``---`` fences,
    ``yaml.safe_load`` the middle. Reimplements ``infra.build_chunks._load_furnished_page``'s
    own three-line pattern locally rather than importing it (see module docstring)."""
    text = page_path.read_text(encoding="utf-8")
    front_matter = text.split("---", 2)[1]
    parsed = yaml.safe_load(front_matter)
    if not isinstance(parsed, dict):
        raise ValueError(f"{page_path}: front matter did not parse to a mapping")
    return parsed


def _scannable_text(origin: str, body: str) -> str:
    """The part of a ``DocCandidate.body`` that is an actual factual claim, not a question.

    ``extract_doc_sections``'s own FAQ candidates deliberately format ``body`` as
    ``f"FAQ: {question}\\n\\n{answer}"`` (see that module's docstring — the ``"FAQ: "``
    prefix supplies a literal substring ``search_docs.classify_content_type`` needs). The
    question itself is never a truth claim (e.g. "Which file formats does ... support?"
    would otherwise itself be mis-read as asserting support for every format word in it),
    so only the text after the first blank line is scanned for a ``faq`` candidate.
    """
    if origin != "faq":
        return body
    _prefix, _sep, answer = body.partition("\n\n")
    return answer if _sep else body


def check_pilot(
    pilot: str,
    furnished_root: Path = DEFAULT_FURNISHED_ROOT,
    api_surface_root: Path = DEFAULT_API_SURFACE_ROOT,
) -> list[Contradiction]:
    """Run the cross-check for one pilot's committed furnished page against its own
    committed ``api_surface.json``."""
    page_path = furnished_root / pilot / "pages" / "_index.md"
    api_surface_path = api_surface_root / pilot / "api_surface.json"
    page = _load_frontmatter(page_path)
    api_surface = json.loads(api_surface_path.read_text(encoding="utf-8"))

    contradictions: list[Contradiction] = []
    for candidate in extract_doc_sections(page):
        text = _scannable_text(candidate.origin, candidate.body)
        contradictions.extend(check_page_text(pilot, candidate.origin, candidate.title, text, api_surface))
    return contradictions


def discover_pilots(
    furnished_root: Path = DEFAULT_FURNISHED_ROOT,
    api_surface_root: Path = DEFAULT_API_SURFACE_ROOT,
) -> list[str]:
    """Every directory under ``furnished_root`` that is a real pilot (holds
    ``pages/_index.md`` and has a sibling ``api_surface.json`` under
    ``api_surface_root``) — the same ``NOT_A_PILOT``/``pages/_index.md`` convention
    ``tests/infra/test_furnished_fixtures_valid.py`` already uses to exclude
    ``sealed_current`` (vendored commit pointers only, not a pilot)."""
    if not furnished_root.is_dir():
        return []
    return sorted(
        directory.name
        for directory in furnished_root.iterdir()
        if directory.is_dir()
        and directory.name not in _NOT_A_PILOT
        and (directory / "pages" / "_index.md").is_file()
        and (api_surface_root / directory.name / "api_surface.json").is_file()
    )


def sweep_all_pilots(
    furnished_root: Path = DEFAULT_FURNISHED_ROOT,
    api_surface_root: Path = DEFAULT_API_SURFACE_ROOT,
) -> list[Contradiction]:
    """Run the cross-check against every real, committed pilot fixture and return every
    contradiction found, across all of them."""
    contradictions: list[Contradiction] = []
    for pilot in discover_pilots(furnished_root, api_surface_root):
        contradictions.extend(check_pilot(pilot, furnished_root, api_surface_root))
    return contradictions


def main() -> int:
    """Independently runnable: ``python -m foss_mcp.indexing.claim_check``. Prints every
    contradiction found across all committed pilot fixtures and exits 1 if any are found,
    0 otherwise.

    Reads ``DEFAULT_FURNISHED_ROOT``/``DEFAULT_API_SURFACE_ROOT`` as module globals (not as
    bound default arguments) so a caller — a test, in particular — can point this at a
    different fixture tree via ``monkeypatch.setattr(claim_check, "DEFAULT_FURNISHED_ROOT",
    ...)`` and have this function see the change; a bound default argument is captured once,
    at function-definition time, and would not.
    """
    contradictions = sweep_all_pilots(DEFAULT_FURNISHED_ROOT, DEFAULT_API_SURFACE_ROOT)
    if not contradictions:
        print("claim_check: zero contradictions found across all pilots.")
        return 0
    print(f"claim_check: {len(contradictions)} contradiction(s) found:")
    for contradiction in contradictions:
        print(f"  - {contradiction.describe()}")
    return 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
