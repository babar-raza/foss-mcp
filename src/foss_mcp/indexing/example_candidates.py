"""Extraction of candidate example code blocks from a furnished-content page.

This module performs pure extraction only: it turns an already-parsed
furnished page (a Hugo page-bundle dict) into structured
``CandidateExample`` records. It does not verify, compile, or execute any
extracted code — that is deliberately deferred to a later stage of the
verified-example pipeline (REQ-G2-048).

Extraction is confirmed, by real per-pilot tests against real extraction
fixtures, across all 6 pilots: ``pdf_net``, ``pdf_java``, ``pdf_typescript``,
``pdf_go``, ``cells_rust``, and ``slides_python`` (see
``tests/fixtures/furnished/<pilot>/pages/_index.md`` and
``tests/indexing/test_example_candidates.py``) — not just ``pdf_net``.

Two modes (TC-458, REQ-G3-007):

* LEGACY - ``extract_candidate_examples(page)``: the first fence of every ``single.block`` entry,
  unfiltered. Unchanged since TC-066.
* PLATFORM - ``extract_candidate_supply(page, platform)`` (and ``extract_candidate_examples(page,
  platform=...)``): every fence of every example block and of the README content blocks that is
  tagged with the pilot's own language, de-duplicated by a normalised sha256, each marked as
  example-starting, a continuation or import-only, and a continuation given the import lines of its
  chain (``import_prefix``) for the platforms whose verifier borrows no page imports
  (``effective_candidate``).
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

__all__ = [
    "CHAIN_PREFIX_PLATFORMS",
    "IMPORT_ONLY_GUARD_PLATFORMS",
    "PLATFORM_FENCE_TAGS",
    "CandidateExample",
    "CandidateSupply",
    "effective_candidate",
    "extract_candidate_examples",
    "extract_candidate_supply",
    "import_like_lines",
    "normalised_code_sha256",
]

_FENCE_RE = re.compile(
    r"```[ \t]*([^\n`]*)\n(.*?)```",
    re.DOTALL,
)

# A fence's info string is reduced to one tag: lowercased, a leading ``{`` or ``.`` dropped, then the
# first run of these characters. ``{.python}``, ``Python`` and ``python title=x`` are all ``python``.
_FENCE_TAG_RE = re.compile(r"[a-z0-9_#+.-]+")

# TC-458: the fence tags each ``--library-platform`` value accepts.
PLATFORM_FENCE_TAGS: Mapping[str, frozenset[str]] = {
    "dotnet": frozenset({"csharp", "cs", "c#"}),
    "python": frozenset({"python", "py", "python3"}),
    "rust": frozenset({"rust", "rs"}),
    "go": frozenset({"go", "golang"}),
    "java": frozenset({"java"}),
    "typescript": frozenset({"typescript", "ts"}),
    "nodejs": frozenset({"javascript", "js", "mjs", "cjs", "node"}),
    "cpp": frozenset({"cpp", "c++", "cxx", "cc"}),
}

# The platforms whose verifier borrows no import lines from the rest of the page (the .NET, Go, Rust
# and C++ verifiers assemble the page's imports themselves since TC-450..TC-455, and the Java one is
# handed every page import line), so a continuation needs the import lines of its chain put in front.
CHAIN_PREFIX_PLATFORMS: frozenset[str] = frozenset({"python", "typescript", "nodejs"})

# The platforms whose committed verifier does NOT reject a block that holds nothing but imports
# (``_NO_STATEMENTS_MESSAGE``). dotnet, go, typescript, python and rust reject such a block; cpp
# compiles and counts it, java wraps it in a class and compiles it, and nodejs passes its link check.
IMPORT_ONLY_GUARD_PLATFORMS: frozenset[str] = frozenset({"cpp", "java", "nodejs"})


def normalised_code_sha256(code: str) -> str:
    """sha256 (lowercase hex) of ``code`` after normalising incidental whitespace (TC-441), so a
    CRLF/CR save, a trailing-space edit, or a surrounding blank line never changes a verified
    verdict's cache key, while a changed statement always does.

    In order: CRLF and CR become LF; trailing whitespace is stripped from every line; leading and
    trailing blank lines are dropped; then the UTF-8 bytes of what remains are hashed.
    """
    text = code.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    while lines and lines[0] == "":
        lines.pop(0)
    while lines and lines[-1] == "":
        lines.pop()
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CandidateExample:
    """A single, not-yet-verified example extracted from furnished content.

    ``page_code`` is the code of EVERY candidate on the same page, in document order, including
    this candidate's own. A verifier may use it to supply what a README writes once for the whole
    page (for example the ``using`` directives of a .NET page). It is excluded from equality,
    hashing and repr, so two candidates compare equal whenever their four content fields match.

    TC-458 adds five fields, all excluded from equality for the same reason: ``code_sha256`` (the
    ``normalised_code_sha256`` of ``code``), ``source`` (``"single"`` for an example block,
    ``"content"`` for a README content block), ``starts_example`` (the first line of code is an
    import or package line), ``imports_only`` (the code holds nothing but imports and package
    lines) and ``import_prefix`` (the import units of the chain this continuation belongs to).
    """

    title: str
    description: str
    language: str
    code: str
    page_code: tuple[str, ...] = field(default=(), compare=False, repr=False)
    code_sha256: str = field(default="", compare=False, repr=False)
    source: str = field(default="single", compare=False, repr=False)
    starts_example: bool = field(default=False, compare=False, repr=False)
    imports_only: bool = field(default=False, compare=False, repr=False)
    import_prefix: tuple[str, ...] = field(default=(), compare=False, repr=False)


@dataclass(frozen=True)
class CandidateSupply:
    """What ``extract_candidate_supply`` found on a page for one platform.

    ``from_single_blocks`` and ``from_content_blocks`` count the returned candidates by source;
    ``duplicates_dropped`` and ``other_language_dropped`` count the fences left out; ``imports_only``
    counts the returned candidates that hold nothing but imports. ``fallback`` is True when no
    fence of the page carries an accepted tag, so ``candidates`` is the legacy list.
    """

    candidates: tuple[CandidateExample, ...]
    from_single_blocks: int
    from_content_blocks: int
    duplicates_dropped: int
    other_language_dropped: int
    imports_only: int
    fallback: bool


# --- import units --------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Unit:
    """One import unit: its text, its import key and the (inclusive) line range it covers."""

    text: str
    key: str
    first: int
    last: int


@dataclass(frozen=True)
class _Hit:
    units: tuple[_Unit, ...]
    last: int
    is_package: bool = False


_Finder = Callable[[list[str], int], _Hit | None]

_MAX_UNIT_LINES = 60


def _col0(line: str) -> bool:
    return line[:1] not in (" ", "\t")


def _unit_text(lines: list[str], first: int, last: int) -> str:
    return "\n".join(line.rstrip() for line in lines[first : last + 1]).strip()


def _single(lines: list[str], index: int, key: str) -> _Hit:
    return _Hit((_Unit(lines[index].strip(), key, index, index),), index)


# python --------------------------------------------------------------------------------------------

_PY_IMPORT_RE = re.compile(r"import[ \t]+([A-Za-z_][\w.]*)")
_PY_FROM_RE = re.compile(r"from[ \t]+(\.*[A-Za-z_][\w.]*|\.+)[ \t]+import\b")
_PY_FUTURE_RE = re.compile(r"^from[ \t]+__future__[ \t]+import\b", re.MULTILINE)


def _paren_delta(line: str) -> int:
    code = line.split("#", 1)[0]
    return code.count("(") - code.count(")")


def _find_python(lines: list[str], index: int) -> _Hit | None:
    line = lines[index]
    if not _col0(line):
        return None
    match = _PY_IMPORT_RE.match(line)
    if match is None:
        match = _PY_FROM_RE.match(line)
        if match is None or match.group(1) == "__future__":
            return None
    last = index
    depth = _paren_delta(line)
    while (depth > 0 or lines[last].rstrip().endswith("\\")) and last + 1 < len(lines):
        if last - index >= _MAX_UNIT_LINES:
            break
        last += 1
        depth += _paren_delta(lines[last])
    return _Hit((_Unit(_unit_text(lines, index, last), match.group(1), index, last),), last)


# dotnet --------------------------------------------------------------------------------------------

_DOTNET_USING_RE = re.compile(
    r"(?:global[ \t]+)?using[ \t]+"
    r"(?:(?P<alias>[A-Za-z_]\w*)[ \t]*=[ \t]*(?P<target>[^;=]+?)"
    r"|(?P<static>static[ \t]+)?(?P<ns>[A-Za-z_][\w.]*))"
    r"[ \t]*;[ \t]*(?://.*)?"
)


def _find_dotnet(lines: list[str], index: int) -> _Hit | None:
    line = lines[index]
    if not _col0(line):
        return None
    match = _DOTNET_USING_RE.fullmatch(line.rstrip())
    if match is None:
        return None
    key = match.group("alias") or (("static " if match.group("static") else "") + match.group("ns"))
    return _single(lines, index, key)


# rust ----------------------------------------------------------------------------------------------

_RUST_USE_START_RE = re.compile(r"(?:pub(?:\([^)]*\))?[ \t]+)?use[ \t]")
_RUST_EXTERN_CRATE_RE = re.compile(
    r"extern[ \t]+crate[ \t]+(\w+)(?:[ \t]+as[ \t]+\w+)?[ \t]*;[ \t]*(?://.*)?"
)
_RUST_USE_PREFIX_RE = re.compile(r"^(?:pub(?:\([^)]*\))?\s+)?use\s+")


def _find_rust(lines: list[str], index: int) -> _Hit | None:
    line = lines[index]
    if not _col0(line):
        return None
    extern = _RUST_EXTERN_CRATE_RE.fullmatch(line.rstrip())
    if extern is not None:
        return _single(lines, index, f"extern crate {extern.group(1)}")
    if _RUST_USE_START_RE.match(line) is None:
        return None
    last = index
    while ";" not in lines[last].split("//", 1)[0]:
        last += 1
        if last >= len(lines) or last - index >= _MAX_UNIT_LINES:
            return None
    text = _unit_text(lines, index, last)
    body = _RUST_USE_PREFIX_RE.sub("", " ".join(text.split()))
    body = body.split(";", 1)[0]
    key = body.split("{", 1)[0].strip().rstrip(":").strip()
    return _Hit((_Unit(text, key, index, last),), last)


# go ------------------------------------------------------------------------------------------------

_GO_SPEC = r'(?:[A-Za-z_.]\w*[ \t]+)?(?:"[^"\n]+"|`[^`\n]+`)'
_GO_IMPORT_ONE_RE = re.compile(rf"import[ \t]+({_GO_SPEC})[ \t]*(?://.*)?")
_GO_IMPORT_OPEN_RE = re.compile(r"import[ \t]*\([ \t]*(?://.*)?")
_GO_SPEC_LINE_RE = re.compile(rf"({_GO_SPEC})[ \t]*(?://.*)?")
_GO_QUOTED_RE = re.compile(r'"([^"\n]+)"|`([^`\n]+)`')
_GO_PACKAGE_RE = re.compile(r"package[ \t]+\w+[ \t]*(?://.*)?")


def _go_unit(spec: str, first: int, last: int) -> _Unit:
    quoted = _GO_QUOTED_RE.search(spec)
    assert quoted is not None
    return _Unit(f"import {spec}", quoted.group(1) or quoted.group(2), first, last)


def _find_go(lines: list[str], index: int) -> _Hit | None:
    line = lines[index]
    if not _col0(line):
        return None
    text = line.rstrip()
    if _GO_PACKAGE_RE.fullmatch(text) is not None:
        return _Hit((), index, is_package=True)
    single = _GO_IMPORT_ONE_RE.fullmatch(text)
    if single is not None:
        return _Hit((_go_unit(single.group(1), index, index),), index)
    if _GO_IMPORT_OPEN_RE.fullmatch(text) is None:
        return None
    units: list[_Unit] = []
    last = index
    while last + 1 < len(lines) and last - index < _MAX_UNIT_LINES:
        last += 1
        inner = lines[last].strip()
        if inner.startswith(")"):
            break
        spec = _GO_SPEC_LINE_RE.fullmatch(inner)
        if spec is not None:
            units.append(_go_unit(spec.group(1), last, last))
    return _Hit(tuple(units), last)


# java ----------------------------------------------------------------------------------------------

_JAVA_IMPORT_RE = re.compile(r"import[ \t]+(?:static[ \t]+)?([A-Za-z_][\w.]*(?:\.\*)?)[ \t]*;[ \t]*(?://.*)?")
_JAVA_PACKAGE_RE = re.compile(r"package[ \t]+[A-Za-z_][\w.]*[ \t]*;[ \t]*(?://.*)?")


def _find_java(lines: list[str], index: int) -> _Hit | None:
    line = lines[index]
    if not _col0(line):
        return None
    text = line.rstrip()
    if _JAVA_PACKAGE_RE.fullmatch(text) is not None:
        return _Hit((), index, is_package=True)
    match = _JAVA_IMPORT_RE.fullmatch(text)
    if match is None:
        return None
    return _single(lines, index, match.group(1))


# typescript and nodejs -----------------------------------------------------------------------------

_TS_IMPORT_START_RE = re.compile(r"""import(?:[ \t]+(?![(.])|(?=[{*'"]))""")
_TS_IMPORT_FULL_RE = re.compile(
    r"""import\b(?:[^'"`;]*?\bfrom)?[ \t\r\n]*(?P<q>['"])(?P<spec>[^'"\n]+)(?P=q)[ \t]*;?[ \t]*(?://[^\n]*)?""",
)
_TS_REQUIRE_RE = re.compile(
    r"""(?:const|let|var)[ \t]+[^=\n]+?=[ \t]*require\([ \t]*(?P<q>['"])(?P<spec>[^'"\n]+)(?P=q)[ \t]*\)"""
    r"""[ \t]*;?[ \t]*(?://.*)?"""
)


def _find_typescript(lines: list[str], index: int) -> _Hit | None:
    line = lines[index]
    if not _col0(line):
        return None
    required = _TS_REQUIRE_RE.fullmatch(line.rstrip())
    if required is not None:
        return _single(lines, index, required.group("spec"))
    if _TS_IMPORT_START_RE.match(line) is None:
        return None
    last = index
    while True:
        text = "\n".join(piece.rstrip() for piece in lines[index : last + 1])
        full = _TS_IMPORT_FULL_RE.fullmatch(text)
        if full is not None:
            return _Hit((_Unit(text.strip(), full.group("spec"), index, last),), last)
        last += 1
        if last >= len(lines) or last - index >= _MAX_UNIT_LINES:
            return None


# cpp -----------------------------------------------------------------------------------------------

_CPP_INCLUDE_RE = re.compile(r'#[ \t]*include[ \t]*[<"]([^>"\n]+)[>"][ \t]*(?://.*|/\*.*\*/[ \t]*)?')
_CPP_USING_NAMESPACE_RE = re.compile(r"using[ \t]+namespace[ \t]+([A-Za-z_][\w:]*)[ \t]*;[ \t]*(?://.*)?")
_CPP_USING_NAME_RE = re.compile(
    r"using[ \t]+(?!namespace\b)([A-Za-z_][\w]*(?:::[A-Za-z_]\w*)+)[ \t]*;[ \t]*(?://.*)?"
)


def _find_cpp(lines: list[str], index: int) -> _Hit | None:
    line = lines[index]
    text = line.strip()
    include = _CPP_INCLUDE_RE.fullmatch(text)
    if include is not None:
        return _single(lines, index, include.group(1).strip())
    if not _col0(line):
        return None
    namespace = _CPP_USING_NAMESPACE_RE.fullmatch(text)
    if namespace is not None:
        return _single(lines, index, f"namespace {namespace.group(1)}")
    name = _CPP_USING_NAME_RE.fullmatch(text)
    if name is not None:
        return _single(lines, index, name.group(1))
    return None


_FINDERS: dict[str, _Finder] = {
    "python": _find_python,
    "dotnet": _find_dotnet,
    "rust": _find_rust,
    "go": _find_go,
    "java": _find_java,
    "typescript": _find_typescript,
    "nodejs": _find_typescript,
    "cpp": _find_cpp,
}


@dataclass(frozen=True)
class _Scan:
    units: tuple[_Unit, ...]
    starts_example: bool
    imports_only: bool


def _check_platform(platform: str) -> None:
    if platform not in PLATFORM_FENCE_TAGS:
        raise ValueError(
            f"unknown platform {platform!r}; expected one of {', '.join(sorted(PLATFORM_FENCE_TAGS))}"
        )


def _scan(code: str, platform: str) -> _Scan:
    """Find the import units of ``code`` and classify its first line and its whole content."""
    finder = _FINDERS[platform]
    lines = code.splitlines()
    units: list[_Unit] = []
    in_block_comment = False
    significant = 0
    other = False
    starts: bool | None = None
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if in_block_comment:
            if "*/" in stripped:
                in_block_comment = False
            index += 1
            continue
        if not stripped:
            index += 1
            continue
        if platform == "python":
            if stripped.startswith("#"):
                index += 1
                continue
        else:
            if stripped.startswith("/*"):
                in_block_comment = "*/" not in stripped[2:]
                index += 1
                continue
            if stripped.startswith(("//", "*")):
                index += 1
                continue
        hit = finder(lines, index)
        if hit is None:
            if platform == "cpp" and stripped.startswith("#"):
                index += 1  # a preprocessor line other than #include is neither code nor an import
                continue
            significant += 1
            other = True
            if starts is None:
                starts = False
            index += 1
            continue
        significant += 1
        units.extend(hit.units)
        if starts is None:
            starts = True
        index = hit.last + 1
    return _Scan(tuple(units), bool(starts), significant > 0 and not other)


def import_like_lines(code: str, platform: str) -> tuple[str, ...]:
    """The import units of ``code`` for ``platform``, in order (TC-458).

    Each unit is stripped; a multi-line unit (a Python ``from x import (`` through ``)``, a Rust
    ``use`` through ``;``, a TypeScript or JavaScript ``import {`` through its ``from '...'`` line)
    is one string whose lines are joined with LF. Each spec of a Go ``import ( ... )`` block is its
    own unit written ``import <spec>``. ``from __future__`` lines, C# ``using var ...`` and
    ``using (...)`` are never units. Raises ``ValueError`` for an unknown platform.
    """
    _check_platform(platform)
    return tuple(unit.text for unit in _scan(code, platform).units)


# --- extraction ----------------------------------------------------------------------------------


def _with_page_fields(candidates: list[CandidateExample]) -> list[CandidateExample]:
    page_code = tuple(candidate.code for candidate in candidates)
    return [
        dataclasses.replace(
            candidate,
            page_code=page_code,
            code_sha256=normalised_code_sha256(candidate.code),
        )
        for candidate in candidates
    ]


def _legacy_candidates(page: Mapping[str, object]) -> list[CandidateExample]:
    single = page.get("single", {})
    if not isinstance(single, Mapping):
        return []
    blocks = single.get("block", [])
    if not isinstance(blocks, list):
        return []

    candidates: list[CandidateExample] = []
    for block in blocks:
        if not isinstance(block, Mapping):
            continue
        title = block.get("title")
        content = block.get("content")
        if not isinstance(title, str) or not isinstance(content, str):
            continue

        match = _FENCE_RE.search(content)
        if match is None:
            # No code fence at all: an honest partial extraction, skip it.
            continue

        description = content[: match.start()].strip()
        language = match.group(1).strip()
        code = match.group(2).strip()

        candidates.append(
            CandidateExample(
                title=title,
                description=description,
                language=language,
                code=code,
            )
        )

    return _with_page_fields(candidates)


def _fence_tag(info: str) -> str:
    """The one tag of a fence info string (``""`` for an untagged fence)."""
    match = _FENCE_TAG_RE.match(info.strip().lower().lstrip("{").lstrip().lstrip("."))
    return match.group(0) if match is not None else ""


@dataclass(frozen=True)
class _Field:
    """One run of markdown that holds fences: an example block, or one side of a content block."""

    title: str
    content: str
    source: str
    chain: tuple[str, int, str]


def _fields(page: Mapping[str, object]) -> list[_Field]:
    fields: list[_Field] = []
    single = page.get("single", {})
    if isinstance(single, Mapping) and isinstance(single.get("block", []), list):
        for block in single.get("block", []):
            if not isinstance(block, Mapping):
                continue
            title, content = block.get("title"), block.get("content")
            if isinstance(title, str) and isinstance(content, str):
                fields.append(_Field(title, content, "single", ("single", 0, "")))
    content_section = page.get("content", {})
    if isinstance(content_section, Mapping) and isinstance(content_section.get("block", []), list):
        for number, block in enumerate(content_section.get("block", [])):
            if not isinstance(block, Mapping):
                continue
            for side in ("left", "right"):
                title, content = block.get(f"title_{side}"), block.get(f"content_{side}")
                if isinstance(title, str) and isinstance(content, str):
                    fields.append(_Field(title, content, "content", ("content", number, side)))
    return fields


def _chain_prefixes(
    candidates: list[CandidateExample], chains: list[tuple[str, int, str]], platform: str
) -> list[CandidateExample]:
    """Mark every candidate's ``starts_example`` / ``imports_only`` and give a continuation the import
    units of its chain (the nearest earlier starter and the candidates between, else every earlier one)."""
    scans = [_scan(candidate.code, platform) for candidate in candidates]
    members: dict[tuple[str, int, str], list[int]] = {}
    for position, chain in enumerate(chains):
        members.setdefault(chain, []).append(position)

    prefixes: dict[int, tuple[str, ...]] = {}
    for positions in members.values():
        for place, position in enumerate(positions):
            if scans[position].starts_example:
                continue
            start = 0
            for earlier in range(place - 1, -1, -1):
                if scans[positions[earlier]].starts_example:
                    start = earlier
                    break
            own_keys = {unit.key for unit in scans[position].units}
            seen: set[str] = set()
            prefix: list[str] = []
            for source in positions[start:place]:
                texts = import_like_lines(candidates[source].code, platform)
                keys = [unit.key for unit in scans[source].units]
                assert len(texts) == len(keys)
                for text, key in zip(texts, keys, strict=True):
                    if key in seen or key in own_keys:
                        continue
                    seen.add(key)
                    prefix.append(text)
            if platform == "python" and _PY_FUTURE_RE.search(candidates[position].code):
                prefix = []
            prefixes[position] = tuple(prefix)

    return [
        dataclasses.replace(
            candidate,
            starts_example=scans[position].starts_example,
            imports_only=scans[position].imports_only,
            import_prefix=prefixes.get(position, ()),
        )
        for position, candidate in enumerate(candidates)
    ]


def extract_candidate_supply(page: Mapping[str, object], platform: str) -> CandidateSupply:
    """Every same-language fence of ``page``, cleaned and annotated (TC-458, platform mode).

    Candidates come from every fence of every ``single.block`` entry and then from the
    ``content_left`` / ``content_right`` of every ``content.block`` entry, in page order. A fence
    whose tag is not one of ``PLATFORM_FENCE_TAGS[platform]`` (an untagged fence included) is
    dropped and counted. The first accepted fence of a block keeps the block's title; a later one is
    ``<title> (part <n>)``. A candidate whose normalised sha256 equals an earlier one's is dropped
    and counted, and a title already used gets `` (<k>)`` with the smallest free ``k`` from 2.

    When no fence of the page carries an accepted tag the supply is the legacy result and
    ``fallback`` is True. Raises ``ValueError`` for an unknown ``platform``.
    """
    _check_platform(platform)
    accepted_tags = PLATFORM_FENCE_TAGS[platform]

    raw: list[CandidateExample] = []
    chains: list[tuple[str, int, str]] = []
    other_language = 0
    for field_ in _fields(page):
        ordinal = 0
        previous_end = 0
        for match in _FENCE_RE.finditer(field_.content):
            description = field_.content[previous_end : match.start()].strip()
            previous_end = match.end()
            if _fence_tag(match.group(1)) not in accepted_tags:
                other_language += 1
                continue
            ordinal += 1
            title = field_.title if ordinal == 1 else f"{field_.title} (part {ordinal})"
            code = match.group(2).strip()
            raw.append(
                CandidateExample(
                    title=title,
                    description=description,
                    language=match.group(1).strip(),
                    code=code,
                    code_sha256=normalised_code_sha256(code),
                    source=field_.source,
                )
            )
            chains.append(field_.chain)

    if not raw:
        legacy = _legacy_candidates(page)
        # No fence of the page carries an accepted tag (or the page has no fence at all).
        return CandidateSupply(
            candidates=tuple(legacy),
            from_single_blocks=len(legacy),
            from_content_blocks=0,
            duplicates_dropped=0,
            other_language_dropped=0,
            imports_only=0,
            fallback=True,
        )

    kept: list[CandidateExample] = []
    kept_chains: list[tuple[str, int, str]] = []
    seen_hashes: set[str] = set()
    used_titles: set[str] = set()
    duplicates = 0
    for candidate, chain in zip(raw, chains, strict=True):
        if candidate.code_sha256 in seen_hashes:
            duplicates += 1
            continue
        seen_hashes.add(candidate.code_sha256)
        title = candidate.title
        if title in used_titles:
            suffix = 2
            while f"{candidate.title} ({suffix})" in used_titles:
                suffix += 1
            title = f"{candidate.title} ({suffix})"
        used_titles.add(title)
        kept.append(dataclasses.replace(candidate, title=title))
        kept_chains.append(chain)

    annotated = _with_page_fields(_chain_prefixes(kept, kept_chains, platform))
    return CandidateSupply(
        candidates=tuple(annotated),
        from_single_blocks=sum(1 for candidate in annotated if candidate.source == "single"),
        from_content_blocks=sum(1 for candidate in annotated if candidate.source == "content"),
        duplicates_dropped=duplicates,
        other_language_dropped=other_language,
        imports_only=sum(1 for candidate in annotated if candidate.imports_only),
        fallback=False,
    )


def extract_candidate_examples(
    page: Mapping[str, object], *, platform: str | None = None
) -> list[CandidateExample]:
    """Extract candidate examples from an already-parsed furnished page dict.

    ``page`` is the dict produced by ``yaml.safe_load`` on the front-matter
    block of a furnished-content page bundle (never a file path — callers
    load and parse the page themselves).

    LEGACY MODE (``platform`` None): each entry of ``page["single"]["block"]`` is inspected: the
    markdown text before the first fenced code block becomes ``description``, the fence's language
    tag becomes ``language``, and the fence's exact body becomes ``code``. A block with no fenced
    code at all is skipped, not treated as an error. Only the first fence of a block is read, no
    language filter applies and nothing is de-duplicated.

    PLATFORM MODE: the candidates of ``extract_candidate_supply(page, platform)``.

    Every returned candidate carries ``page_code``: the code of all returned candidates, in
    document order, including its own, and ``code_sha256``.
    """
    if platform is None:
        return _legacy_candidates(page)
    return list(extract_candidate_supply(page, platform).candidates)


# --- effective candidate -------------------------------------------------------------------------


def effective_candidate(candidate: CandidateExample, platform: str) -> CandidateExample:
    """The candidate a verifier should be handed (TC-458).

    On a platform in ``CHAIN_PREFIX_PLATFORMS`` a continuation's ``import_prefix`` is put in front
    of its code (the prefix units joined with LF, one LF, then the original code), with
    ``code_sha256`` recomputed and ``import_prefix`` emptied. Every other platform, and a candidate
    with no prefix, is returned unchanged (the very same object).
    """
    if platform not in CHAIN_PREFIX_PLATFORMS or not candidate.import_prefix:
        return candidate
    code = "\n".join(candidate.import_prefix) + "\n" + candidate.code
    return dataclasses.replace(
        candidate, code=code, code_sha256=normalised_code_sha256(code), import_prefix=()
    )
