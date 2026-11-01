"""Known-defects registry: the committed ledger of UPSTREAM library defects this project
discloses rather than silently works around (KD1, workstream J, REQ-G3-015).

docs/DECISION_LOG.md's ``## 2026-10-30`` entry and the operator's 2026-11-01 explicit decision:
a clear, evidenced disclosure IS the production-grade fix for an upstream library bug, not a
code-level patch. This module is that disclosure mechanism's foundation only: a committed,
schema-validated per-pilot file, ``config/known_defects/<family>__<platform>.yaml`` (one file per
pilot; an absent file is valid - most pilots have none recorded yet, that is not an error), and a
loader that validates and returns it.

This module only LOADS and VALIDATES. It never imports, compiles or runs anything, and it makes
no network call. The structural rules are also written down as
``schemas/product/known_defects.schema.json``; the schema file is not copied into the ingestion
image, so ``validate_known_defects`` enforces the same structure in plain Python (mirroring
``authored_examples.py``'s established pattern in this codebase), and a test proves the two agree.

Beyond the schema's own structure, this loader hand-enforces three rules a JSON Schema document
cannot express:

1. **Cross-item ``id`` uniqueness.** Two entries sharing an ``id`` within one pilot's file would
   make the ledger id meaningless as a stable reference.
2. **Real calendar-date validity.** The schema's ``evidence.verified_on.date`` pattern only checks
   the *shape* ``YYYY-MM-DD``; it cannot reject an out-of-range date such as ``2026-13-40``
   (month 13, day 40). This loader parses it with ``datetime.date.fromisoformat`` and rejects
   anything that does not round-trip to a real date.
3. **The status/evidence non-contradiction rule.** A ``status: fixed_upstream`` entry is a claim
   that re-checking against a currently served package version can confirm or refute later (KD5
   wires exactly that re-check into ``report_index_freshness``, via ``defect_is_stale`` below) -
   that is the entire point of marking something fixed rather than simply deleting the entry. A
   ``fixed_upstream`` entry scoped ONLY to ``applies_to.indexed_commit`` can never be re-checked
   that way: a frozen commit does not track "has the served version moved past this", only a
   package version does. So ``status: fixed_upstream`` requires ``applies_to.package_versions``
   to be present (``indexed_commit`` may additionally be present too; it just cannot be the only
   scope given for a claim that depends on comparing package versions over time).

``defect_is_stale`` is the disclosure-expiry comparison helper from Decision C
(DECISION_LOG.md's ``## 2026-10-30`` entry, decision C): "a known-defect entry is version-scoped
(``verified_on {package, version, date}``); once the served package version moves past that, the
served text downgrades from an assertion to 'last verified broken on X; not re-verified on Y'
rather than silently going stale." It reuses the exact padded-equal-length-tuple comparison
pattern ``foss_mcp.mcp.tools.release_alignment.release_in_index`` already uses for the identical
reason: plain ``tuple`` comparison treats a shorter tuple as less than any longer tuple sharing
its prefix (``(26, 7) < (26, 7, 0)`` is ``True`` even though "26.7" and "26.7.0" are the same
version) - TC-629 fixed exactly this bug class elsewhere in this codebase. The pattern is
reimplemented locally rather than imported: ``indexing`` modules are a lower layer that
``mcp.tools`` already depends on (see ``release_alignment.py`` importing from
``get_product_reference.py``, itself built on this package), and nothing in ``indexing`` imports
from ``mcp.tools`` today; adding that edge here would be a new, backwards layering dependency for
two lines of arithmetic. ``defect_is_stale`` itself is not called by any live tool in this card -
KD5 wires it into ``report_index_freshness`` later - but its correctness is proven here, by this
card's own tests, not deferred.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "REGISTRY_DIR",
    "AppliesTo",
    "Evidence",
    "KnownDefect",
    "KnownDefectsError",
    "Subject",
    "VerifiedOn",
    "defect_is_stale",
    "load_known_defects",
    "registry_path_for",
    "validate_known_defects",
]

REGISTRY_DIR = Path(__file__).resolve().parents[3] / "config" / "known_defects"

_TOP_LEVEL_KEYS = frozenset({"schema_version", "pilot", "defects"})
_DEFECT_KEYS = frozenset({"id", "subject", "applies_to", "summary", "user_action", "evidence", "status"})
_SUBJECT_KEYS = frozenset({"kind", "ref"})
_APPLIES_TO_KEYS = frozenset({"indexed_commit", "package_versions"})
_EVIDENCE_KEYS = frozenset({"repro", "verified_on"})
_VERIFIED_ON_KEYS = frozenset({"package", "version", "date"})

_SUBJECT_KINDS = frozenset({"method", "class", "commit", "package"})
_STATUSES = frozenset({"open", "fixed_upstream", "disputed"})

_PILOT_RE = re.compile(r"^[a-z0-9]+_[a-z0-9]+$")
_DEFECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MAX_SUMMARY_CHARS = 240

# The same padded-equal-length-tuple version-run pattern release_alignment.parse_release_version
# uses, kept local - see the module docstring for why this is reimplemented rather than imported.
_VERSION_RUN = re.compile(r"\d+(?:\.\d+)*")


class KnownDefectsError(ValueError):
    """A registry file that cannot be loaded; the message lists every problem found."""


def _non_blank(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_real_date(value: str) -> bool:
    if not _DATE_RE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _verified_on_problems(verified_on: Any, label: str) -> list[str]:
    if not isinstance(verified_on, Mapping):
        return [f"{label}: evidence.verified_on must be a mapping"]
    problems: list[str] = []
    keys = set(verified_on)
    for missing in sorted(_VERIFIED_ON_KEYS - keys):
        problems.append(f"{label}: evidence.verified_on is missing key {missing!r}")
    for extra in sorted(keys - _VERIFIED_ON_KEYS, key=str):
        problems.append(f"{label}: evidence.verified_on has unexpected key {extra!r}")
    for name in ("package", "version"):
        if name in verified_on and not _non_blank(verified_on[name]):
            problems.append(f"{label}: evidence.verified_on.{name} must be a non-empty string")
    if (
        "version" in verified_on
        and _non_blank(verified_on["version"])
        and not any(char.isdigit() for char in verified_on["version"])
    ):
        problems.append(f"{label}: evidence.verified_on.version must contain at least one digit")
    if "date" in verified_on:
        raw_date = verified_on["date"]
        if not (isinstance(raw_date, str) and _is_real_date(raw_date)):
            problems.append(
                f"{label}: evidence.verified_on.date must be a real calendar date (YYYY-MM-DD), "
                f"got {raw_date!r}"
            )
    return problems


def _evidence_problems(evidence: Any, label: str) -> list[str]:
    if not isinstance(evidence, Mapping):
        return [f"{label}: evidence must be a mapping"]
    problems: list[str] = []
    keys = set(evidence)
    for missing in sorted(_EVIDENCE_KEYS - keys):
        problems.append(f"{label}: evidence is missing key {missing!r}")
    for extra in sorted(keys - _EVIDENCE_KEYS, key=str):
        problems.append(f"{label}: evidence has unexpected key {extra!r}")
    if "repro" in evidence and not _non_blank(evidence["repro"]):
        problems.append(f"{label}: evidence.repro must be a non-empty string")
    if "verified_on" in evidence:
        problems.extend(_verified_on_problems(evidence["verified_on"], label))
    return problems


def _subject_problems(subject: Any, label: str) -> list[str]:
    if not isinstance(subject, Mapping):
        return [f"{label}: subject must be a mapping"]
    problems: list[str] = []
    keys = set(subject)
    for missing in sorted(_SUBJECT_KEYS - keys):
        problems.append(f"{label}: subject is missing key {missing!r}")
    for extra in sorted(keys - _SUBJECT_KEYS, key=str):
        problems.append(f"{label}: subject has unexpected key {extra!r}")
    if "kind" in subject and subject["kind"] not in _SUBJECT_KINDS:
        problems.append(
            f"{label}: subject.kind must be one of {sorted(_SUBJECT_KINDS)}, got {subject['kind']!r}"
        )
    if "ref" in subject and not _non_blank(subject["ref"]):
        problems.append(f"{label}: subject.ref must be a non-empty string")
    return problems


def _applies_to_problems(applies_to: Any, label: str) -> list[str]:
    if not isinstance(applies_to, Mapping):
        return [f"{label}: applies_to must be a mapping"]
    problems: list[str] = []
    keys = set(applies_to)
    for extra in sorted(keys - _APPLIES_TO_KEYS, key=str):
        problems.append(f"{label}: applies_to has unexpected key {extra!r}")
    if "indexed_commit" in applies_to:
        commit = applies_to["indexed_commit"]
        if not (isinstance(commit, str) and _COMMIT_RE.fullmatch(commit)):
            problems.append(f"{label}: applies_to.indexed_commit must be 40 lowercase hex characters")
    if "package_versions" in applies_to:
        versions = applies_to["package_versions"]
        if not isinstance(versions, list) or not versions:
            problems.append(f"{label}: applies_to.package_versions must be a non-empty list")
        elif not all(_non_blank(version) for version in versions):
            problems.append(f"{label}: every applies_to.package_versions entry must be a non-empty string")
        elif len(set(versions)) != len(versions):
            problems.append(f"{label}: applies_to.package_versions must be unique")
    if not ({"indexed_commit", "package_versions"} & keys):
        problems.append(f"{label}: applies_to must have at least one of indexed_commit or package_versions")
    return problems


def _defect_problems(defect: Any, label: str) -> list[str]:
    if not isinstance(defect, Mapping):
        return [f"{label} must be a mapping"]
    problems: list[str] = []
    keys = set(defect)
    for missing in sorted(_DEFECT_KEYS - keys):
        problems.append(f"{label} is missing key {missing!r}")
    for extra in sorted(keys - _DEFECT_KEYS, key=str):
        problems.append(f"{label} has unexpected key {extra!r}")

    if "id" in defect:
        defect_id = defect["id"]
        if not (isinstance(defect_id, str) and _DEFECT_ID_RE.fullmatch(defect_id)):
            problems.append(f"{label}: id must be a non-empty string matching {_DEFECT_ID_RE.pattern!r}")
    if "subject" in defect:
        problems.extend(_subject_problems(defect["subject"], label))
    if "applies_to" in defect:
        problems.extend(_applies_to_problems(defect["applies_to"], label))
    if "summary" in defect:
        summary = defect["summary"]
        if not (_non_blank(summary) and len(summary) <= _MAX_SUMMARY_CHARS):
            problems.append(f"{label}: summary must be 1 to {_MAX_SUMMARY_CHARS} characters")
    if "user_action" in defect and not _non_blank(defect["user_action"]):
        problems.append(f"{label}: user_action must be a non-empty string")
    if "evidence" in defect:
        problems.extend(_evidence_problems(defect["evidence"], label))
    status = defect.get("status")
    if "status" in defect and status not in _STATUSES:
        problems.append(f"{label}: status must be one of {sorted(_STATUSES)}, got {status!r}")

    # Rule: a status: fixed_upstream claim must be re-checkable against a served package
    # version later (defect_is_stale / KD5), which requires applies_to.package_versions -
    # see the module docstring for why an indexed_commit-only scope cannot express that claim.
    if status == "fixed_upstream" and isinstance(defect.get("applies_to"), Mapping):
        if "package_versions" not in defect["applies_to"]:
            problems.append(
                f"{label}: status 'fixed_upstream' requires applies_to.package_versions "
                "(an indexed_commit-only scope cannot be re-checked against a served package "
                "version, which is the entire point of marking a defect fixed upstream)"
            )
    return problems


def _structure_problems(document: Any) -> list[str]:
    if not isinstance(document, Mapping):
        return [f"the document must be a mapping, not {type(document).__name__}"]
    problems: list[str] = []
    keys = set(document)
    for missing in sorted(_TOP_LEVEL_KEYS - keys):
        problems.append(f"missing key {missing!r}")
    for extra in sorted(keys - _TOP_LEVEL_KEYS, key=str):
        problems.append(f"unexpected key {extra!r}")

    version = document.get("schema_version")
    if "schema_version" in document and not (type(version) is int and version == 1):
        problems.append("schema_version must be the integer 1")
    pilot = document.get("pilot")
    if "pilot" in document and not (isinstance(pilot, str) and _PILOT_RE.fullmatch(pilot)):
        problems.append(f"pilot must match {_PILOT_RE.pattern!r}, got {pilot!r}")

    if "defects" not in document:
        return problems
    defects = document["defects"]
    if not isinstance(defects, list):
        problems.append("defects must be a list")
        return problems

    ids_seen: set[str] = set()
    for number, defect in enumerate(defects, start=1):
        label = f"defect {number}"
        problems.extend(_defect_problems(defect, label))
        if isinstance(defect, Mapping) and isinstance(defect.get("id"), str):
            defect_id = defect["id"]
            if defect_id in ids_seen:
                problems.append(f"{label}: id {defect_id!r} is not unique within this file")
            else:
                ids_seen.add(defect_id)
    return problems


def validate_known_defects(document: object, *, pilot: str) -> list[str]:
    """Every problem found in a parsed registry ``document``; an empty list means it is valid.

    Checks the full structural shape (mirroring ``schemas/product/known_defects.schema.json``),
    that ``pilot`` is the expected pilot, and the three rules a schema cannot express: cross-item
    ``id`` uniqueness, real calendar-date validity of every ``evidence.verified_on.date``, and the
    ``status: fixed_upstream`` / ``applies_to`` non-contradiction rule documented in this module's
    own docstring.
    """
    problems = _structure_problems(document)
    if isinstance(document, Mapping) and "pilot" in document and document["pilot"] != pilot:
        problems.append(f"pilot is {document['pilot']!r}, expected {pilot!r}")
    return problems


@dataclass(frozen=True, slots=True)
class VerifiedOn:
    """The exact package, version and date a defect was last confirmed on."""

    package: str
    version: str
    date: str


@dataclass(frozen=True, slots=True)
class Evidence:
    """How a defect was confirmed, and against what."""

    repro: str
    verified_on: VerifiedOn


@dataclass(frozen=True, slots=True)
class Subject:
    """What a defect is about."""

    kind: str
    ref: str


@dataclass(frozen=True, slots=True)
class AppliesTo:
    """The scope a defect applies to. At least one of the two fields is not ``None``."""

    indexed_commit: str | None
    package_versions: tuple[str, ...] | None


@dataclass(frozen=True, slots=True)
class KnownDefect:
    """One disclosed upstream defect, as recorded in a pilot's known-defects registry file."""

    id: str
    subject: Subject
    applies_to: AppliesTo
    summary: str
    user_action: str
    evidence: Evidence
    status: str


def _defect_from_mapping(raw: Mapping[str, Any]) -> KnownDefect:
    subject = raw["subject"]
    applies_to = raw["applies_to"]
    evidence = raw["evidence"]
    verified_on = evidence["verified_on"]
    package_versions = applies_to.get("package_versions")
    return KnownDefect(
        id=raw["id"],
        subject=Subject(kind=subject["kind"], ref=subject["ref"]),
        applies_to=AppliesTo(
            indexed_commit=applies_to.get("indexed_commit"),
            package_versions=tuple(package_versions) if package_versions is not None else None,
        ),
        summary=raw["summary"],
        user_action=raw["user_action"],
        evidence=Evidence(
            repro=evidence["repro"],
            verified_on=VerifiedOn(
                package=verified_on["package"],
                version=verified_on["version"],
                date=verified_on["date"],
            ),
        ),
        status=raw["status"],
    )


def registry_path_for(family: str, platform: str, *, base_dir: Path | None = None) -> Path:
    """The registry file path for ``<family>__<platform>``, under ``base_dir`` (default
    ``REGISTRY_DIR``). The file need not exist - an absent file is a valid, defect-free pilot."""
    directory = REGISTRY_DIR if base_dir is None else base_dir
    return directory / f"{family}__{platform}.yaml"


def load_known_defects(
    family: str,
    platform: str,
    *,
    base_dir: Path | None = None,
) -> list[KnownDefect]:
    """The known defects recorded for ``<family>_<platform>``, in file order.

    Returns ``[]`` gracefully when no registry file exists for this pilot - most pilots have none
    recorded yet, which is valid, not an error. Raises ``KnownDefectsError`` whose message lists
    every problem when a file exists but cannot be read, parsed, or fails
    ``validate_known_defects``.
    """
    pilot = f"{family}_{platform}"
    path = registry_path_for(family, platform, base_dir=base_dir)
    if not path.exists():
        return []
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise KnownDefectsError(f"cannot read {path}: {type(exc).__name__}") from exc
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise KnownDefectsError(f"{path} is not valid YAML: {type(exc).__name__}") from exc

    problems = validate_known_defects(document, pilot=pilot)
    if problems:
        raise KnownDefectsError("; ".join(problems))
    return [_defect_from_mapping(defect) for defect in document["defects"]]


def _parse_defect_version(version: str) -> tuple[int, ...]:
    match = _VERSION_RUN.search(version)
    if match is None:
        raise KnownDefectsError(f"version {version!r} has no parseable digit run")
    return tuple(int(part) for part in match.group(0).split("."))


def defect_is_stale(defect: KnownDefect, served_version: tuple[int, ...]) -> bool:
    """True when ``served_version`` is strictly newer than the version ``defect`` was last
    verified broken on (``defect.evidence.verified_on.version``), meaning the served disclosure
    text must downgrade from an assertion to "last verified broken on X; not re-verified on Y"
    (Decision C, DECISION_LOG.md's ``## 2026-10-30`` entry) rather than silently going stale.

    Both version tuples are padded to equal length with trailing zeros before comparing - the
    exact pattern ``release_alignment.release_in_index`` uses - so an equal version written at a
    different precision (``(26, 7)`` vs ``(26, 7, 0)``) is never mistaken for newer or older
    (TC-629 fixed exactly this bug class elsewhere in this codebase).
    """
    verified_version = _parse_defect_version(defect.evidence.verified_on.version)
    width = max(len(verified_version), len(served_version))
    padded_verified = verified_version + (0,) * (width - len(verified_version))
    padded_served = served_version + (0,) * (width - len(served_version))
    return padded_served > padded_verified
