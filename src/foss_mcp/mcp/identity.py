"""What this server says about itself: its handshake title, instructions and advertised descriptions.

G3/TC-514 (REQ-G3-008). An independent review measured ``initialize`` and ``tools/list`` byte-identical
for every pilot it tried: the same title-less ``serverInfo``, the same "one open-source product"
instructions, and tool descriptions that quote a pdf/net example (``Aspose.Pdf.Document``) even on
pdf/go, cells/rust and words/net. This module renders all of that from the deployment's own scope
plus a few facts read from its own ACTIVE generation (a real indexed symbol, a real verified-example
title, and how ``find_examples`` will search).

Pure: no I/O of its own (the one store read is delegated to the store it is handed), no clock, no
randomness and no network. Every failure to read the generation yields the NEUTRAL facts, whose
wording names no example at all - a description never invents one.

``render_description`` has three modes, selected by the *facts* argument:

- ``None``: the LEGACY pdf/net values (``Aspose.Pdf.Document``, ``Document``, ``add a watermark to a
  PDF``, "lexical/semantic"), so every static caller and every test written before this card keeps
  today's text byte for byte;
- neutral facts (all ``None``): wording with no example value;
- real facts: the deployment's own example values.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from foss_mcp.mcp.routing import Scope
from foss_mcp.mcp.tools.example_search_mode import example_search_mode
from foss_mcp.mcp.tools.get_symbol import extract_fqn

# The display name of every family a pilot ships (infra/helm/foss-mcp/values.yaml ingestion.pilots).
FAMILY_DISPLAY: dict[str, str] = {
    "3d": "3D",
    "barcode": "Barcode",
    "cells": "Cells",
    "email": "Email",
    "html": "HTML",
    "imaging": "Imaging",
    "jmap": "JMAP",
    "note": "Note",
    "page": "Page",
    "pdf": "PDF",
    "slides": "Slides",
    "tex": "TeX",
    "words": "Words",
}

# (language label, ecosystem label, how the indexed FQNs are written) for every platform a pilot
# ships. The FQN sentences are true of the FQNs the pilots actually index.
PLATFORM_PROFILE: dict[str, tuple[str, str, str]] = {
    "cpp": ("C++", "CMake", "Namespace::Type"),
    "go": ("Go", "Go modules", "the bare type name"),
    "java": ("Java", "Maven", "package.Type"),
    "net": (".NET", "NuGet", "Namespace.Type"),
    "nodejs": ("Node.js", "npm", "module path then the class"),
    "python": (
        "Python",
        "PyPI",
        "dotted module path then the class, for example package.module.Class "
        "(a class can appear under more than one path)",
    ),
    "rust": ("Rust", "crates.io", "the bare type name"),
    "typescript": ("TypeScript", "npm", "file stem then the type (document.Document)"),
}

_TITLE_MAX = 80
_PARAGRAPH_MAX = 400
_INSTRUCTIONS_MAX = 1100
_SCOPE_KEY_SOURCE = "self_extracted"

_EXAMPLE_PREFIX = "Example: "
_NON_SYMBOL_PREFIXES = (_EXAMPLE_PREFIX, "Doc: ")
_MEMBER_HEADERS = ("Methods:", "Properties:")
_BULLET = "  - "
_SEGMENT_SPLIT = re.compile(r"\.|::")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")

_LEXICAL_ONLY = "lexical-only"

# The LEGACY pdf/net values, rendered when no facts are given.
_LEGACY_FQN = "Aspose.Pdf.Document"
_LEGACY_SHORT = "Document"
_LEGACY_TASK = "add a watermark to a PDF"
_LEGACY_CLAUSE = "a lexical/semantic search"

# The wording when nothing is readable: no example value at all.
_NEUTRAL_FQN = "the FQN that search_symbols returned"
_NEUTRAL_SHORT = "the short name of a type"
_NEUTRAL_TASK = "a short description of the task"

_CLAUSE_LEXICAL = "a lexical (word-overlap) search; this deployment has no semantic search"
_CLAUSE_VECTOR = "a lexical search with a semantic (vector) fallback through the embedding gateway"
_CLAUSE_VECTOR_DEGRADED = (
    _CLAUSE_VECTOR + "; the gateway is currently degraded, so expect lexical results only"
)


@dataclass(frozen=True)
class DeploymentFacts:
    """What a deployment's own active generation says about itself.

    ``example_fqn`` is the indexed symbol with the MOST member lines ("top" means most members, not
    importance); ``example_short`` is its last segment; ``task_example`` is the lower-cased title of
    the lexicographically first verified example; ``example_search`` is the first item of
    ``example_search_mode``. All ``None`` and ``lexical-only`` when nothing is readable.
    """

    example_fqn: str | None
    example_short: str | None
    task_example: str | None
    example_search: str


NEUTRAL_FACTS = DeploymentFacts(None, None, None, _LEXICAL_ONLY)


def _display_family(family: str) -> str:
    return FAMILY_DISPLAY.get(family, family.lower())


def _profile(platform: str) -> tuple[str, str, str]:
    fallback = platform.lower()
    return PLATFORM_PROFILE.get(platform, (fallback, fallback, "the identifier the index lists"))


def _one_line(text: str) -> str:
    """*text* with every control character (newline included) replaced by a space, and the
    template braces removed so a value can never introduce a placeholder."""
    return " ".join(_CONTROL.sub(" ", text).replace("{", "").replace("}", "").split())


def _member_count(text: str) -> int:
    """The number of member bullets under the ``Methods:`` and ``Properties:`` headers of a type's
    chunk text."""
    count = 0
    counting = False
    for line in text.splitlines():
        if line in _MEMBER_HEADERS:
            counting = True
        elif counting and line.startswith(_BULLET):
            count += 1
        else:
            counting = False
    return count


def _last_segment(fqn: str) -> str:
    return _SEGMENT_SPLIT.split(fqn)[-1]


def _chunk_texts(payload: Any) -> Iterable[str]:
    documents = (payload.get("lexical_index") or {}).get("documents") or {}
    for entry in documents.values():
        text = entry.get("text") if isinstance(entry, dict) else None
        if isinstance(text, str):
            yield text


def _facts_from_payload(payload: Any) -> DeploymentFacts:
    """The facts a generation *payload* states. Deterministic: the symbol with the most member lines
    wins and ties go to the lexicographically smaller FQN; the example is the lexicographically
    first verified-example title."""
    best: tuple[int, str] | None = None
    first_title: str | None = None
    for text in _chunk_texts(payload):
        fqn = extract_fqn(text)
        if not fqn:
            continue
        if fqn.startswith(_EXAMPLE_PREFIX):
            title = _one_line(fqn[len(_EXAMPLE_PREFIX) :]).replace("'", "")[:_TITLE_MAX]
            if title and (first_title is None or title < first_title):
                first_title = title
            continue
        if fqn.startswith(_NON_SYMBOL_PREFIXES):
            continue
        fqn = _one_line(fqn)
        if not fqn:
            continue
        members = _member_count(text)
        if best is None or members > best[0] or (members == best[0] and fqn < best[1]):
            best = (members, fqn)
    mode, _reason = example_search_mode(payload)
    return DeploymentFacts(
        example_fqn=best[1] if best is not None else None,
        example_short=_last_segment(best[1]) if best is not None else None,
        task_example=first_title.lower() if first_title is not None else None,
        example_search=mode,
    )


def read_deployment_facts(store: Any, scope: Scope) -> DeploymentFacts:
    """The facts of *scope*'s ACTIVE generation, or the neutral facts when none is readable.

    Any failure (no active generation, an unreadable or malformed manifest, a store that raises) is
    swallowed on purpose: the handshake and ``tools/list`` must always answer, with wording that
    names no example rather than an invented one."""
    try:
        key = "::".join((scope.family, scope.platform, _SCOPE_KEY_SOURCE))
        generation_id = store.read_active(key)
        if generation_id is None:
            return NEUTRAL_FACTS
        return _facts_from_payload(store.read_generation(key, generation_id).payload)
    except Exception:  # noqa: BLE001 - deliberately broad: see the docstring.
        return NEUTRAL_FACTS


def deployment_title(scope: Scope) -> str:
    """``serverInfo.title``: ``Aspose PDF FOSS for .NET - API and documentation context``, at most
    80 characters."""
    language = _profile(scope.platform)[0]
    title = f"Aspose {_display_family(scope.family)} FOSS for {language} - API and documentation context"
    return title[:_TITLE_MAX]


def deployment_instructions(base: str, scope: Scope, facts: DeploymentFacts) -> str:
    """*base* (the generic guidance, unchanged) followed by ONE paragraph of at most 400 characters
    naming the family, language, ecosystem and FQN style and, when the generation states one, a real
    example FQN. At most 1,100 characters in all and no newline."""
    language, ecosystem, fqn_style = _profile(scope.platform)
    paragraph = (
        f"This deployment serves Aspose {_display_family(scope.family)} FOSS for {language} "
        f"({ecosystem}); FQNs are written as {fqn_style}"
    )
    if facts.example_fqn is not None:
        paragraph += f", for example {_one_line(facts.example_fqn)}"
    paragraph = _one_line(paragraph[: _PARAGRAPH_MAX - 1].rstrip()) + "."
    return f"{base} {paragraph}"[:_INSTRUCTIONS_MAX]


def _example_search_clause(mode: str) -> str:
    if mode.startswith("lexical+vector"):
        return _CLAUSE_VECTOR_DEGRADED if "degraded" in mode else _CLAUSE_VECTOR
    return _CLAUSE_LEXICAL


def render_description(template: str, facts: DeploymentFacts | None) -> str:
    """*template* with its placeholders (``{example_fqn}``, ``{example_short}``, ``{task_example}``,
    ``{example_search_clause}``) replaced; see the module docstring for the three modes. Plain
    ``str.replace``, never ``str.format``: a description may hold braces."""
    if facts is None:
        fqn, short, task, clause = _LEGACY_FQN, _LEGACY_SHORT, _LEGACY_TASK, _LEGACY_CLAUSE
    else:
        fqn = facts.example_fqn or _NEUTRAL_FQN
        short = facts.example_short or _NEUTRAL_SHORT
        task = facts.task_example or _NEUTRAL_TASK
        clause = _example_search_clause(facts.example_search)
    # "how do I <task>" only reads as a question around a real task; the neutral task phrase stands alone.
    question = f"how do I {task}" if task != _NEUTRAL_TASK else task
    return (
        template.replace("how do I {task_example}", question)
        .replace("{example_fqn}", fqn)
        .replace("{example_short}", short)
        .replace("{task_example}", task)
        .replace("{example_search_clause}", clause)
    )
