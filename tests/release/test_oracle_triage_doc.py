"""G3/TC-691: docs/ORACLE_TRIAGE.md, the classified Go/Rust/C++ oracle-disagreement report.

Every figure asserted below is this test's OWN hardcoded ground truth, pinned directly from the real,
committed measurement sources (`TC-595`'s own commit message, `git log --format=%B -n 1 5c8bc6fe`, and
`docs/RELEASE.md`'s "Independent extraction-fidelity oracle (REQ-G3-009)" section) -- never read back
out of `docs/ORACLE_TRIAGE.md` or those sources at test-collection time. That is the whole point: this
test exists to catch the document drifting away from the real numbers later, not to prove the document
is merely self-consistent with itself.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOC = REPO_ROOT / "docs" / "ORACLE_TRIAGE.md"

# The 7 Go/Rust/C++ pilots this card covers, and their real, committed per-category disagreement
# counts, pinned here as ground truth -- hand-transcribed from TC-595's own commit message and
# cross-checked against docs/RELEASE.md, never computed from either at test time.
PILOT_COUNTS: dict[str, dict[str, int]] = {
    "cells/go": {"missing": 0, "extra": 0, "signature": 0, "static": 0, "default": 0, "enum_value": 0},
    "pdf/go": {"missing": 0, "extra": 480, "signature": 0, "static": 0, "default": 0, "enum_value": 0},
    "cells/rust": {"missing": 341, "extra": 0, "signature": 14, "static": 0, "default": 0, "enum_value": 0},
    "pdf/cpp": {"missing": 4, "extra": 2406, "signature": 694, "static": 0, "default": 0, "enum_value": 0},
    "cells/cpp": {"missing": 0, "extra": 0, "signature": 13, "static": 0, "default": 0, "enum_value": 0},
    "slides/cpp": {"missing": 0, "extra": 153, "signature": 37, "static": 0, "default": 0, "enum_value": 0},
    "email/cpp": {"missing": 0, "extra": 24, "signature": 7, "static": 0, "default": 0, "enum_value": 0},
}

CROSS_CUTTING_EXAMPLES = (
    "AutoFilterColorFilterModel",
    "AutoFilterColorFilter",
    "CellObjectValue",
    "Annotation",
    "BaseParagraph",
)


def _read_doc() -> str:
    assert DOC.is_file(), f"{DOC} does not exist -- TC-691 must create it"
    return DOC.read_text(encoding="utf-8")


def test_doc_exists_and_names_all_7_pilots() -> None:
    text = _read_doc()
    for pilot in PILOT_COUNTS:
        assert pilot in text, f"pilot {pilot!r} is never named in {DOC}"


def test_doc_cites_tc595_as_the_real_measurement_source() -> None:
    text = _read_doc()
    assert "TC-595" in text
    assert "2026-10-27" in text
    assert "source_differential.py" in text
    # the real command that produced the measurement, and an explicit disclosure that it ran with
    # no --only filter (fleet-wide, not a Go/Rust/C++-only re-run)
    assert "--fixtures tests/fixtures" in text
    assert "no `--only` filter" in text or "no --only filter" in text


def test_every_pilot_real_count_appears_in_the_document() -> None:
    """Each pilot's real, pinned non-zero counts must appear in the document, verbatim, as numbers --
    never silently rounded, omitted, or substituted."""
    text = _read_doc()
    for pilot, counts in PILOT_COUNTS.items():
        nonzero = {category: value for category, value in counts.items() if value}
        for category, value in nonzero.items():
            assert str(value) in text, (
                f"{pilot}'s real {category}={value} does not appear anywhere in {DOC} as a number"
            )


def test_cells_go_is_disclosed_as_the_genuine_clean_result() -> None:
    text = _read_doc()
    assert "cells/go" in text
    assert "79" in text  # the real member count the clean pilot checked
    # it must not be silently conflated with every category being literally absent from the doc
    assert "0 disagreements" in text or "clean" in text.lower()


def test_both_cross_cutting_buckets_are_named_with_real_examples() -> None:
    text = _read_doc()
    lowered = text.lower()
    assert "inherit" in lowered or "flatten" in lowered, "inheritance/base-class flattening bucket missing"
    assert "bare" in lowered or "overload" in lowered or "namespace" in lowered, (
        "namespace/overload-unaware bare-name matching bucket missing"
    )
    for symbol in CROSS_CUTTING_EXAMPLES:
        assert symbol in text, f"real cited example symbol {symbol!r} (OQ-018/OQ-022) missing from {DOC}"


def test_pdf_go_extra_480_is_explicitly_addressed_near_the_pilot_name() -> None:
    """pdf/go's extra=480 must never be silently omitted: the document must discuss the number 480 in
    the same neighbourhood as the pilot's own name, either naming a real traced bucket or saying
    plainly that it is untraced."""
    text = _read_doc()
    assert "pdf/go" in text and "480" in text
    # find every occurrence of "480" and require at least one to have "pdf/go" within a nearby window,
    # rather than merely requiring both substrings to exist anywhere in an unrelated part of the file
    found_nearby = False
    for match in re.finditer(r"480", text):
        start = max(0, match.start() - 600)
        end = min(len(text), match.end() + 600)
        if "pdf/go" in text[start:end]:
            found_nearby = True
            break
    assert found_nearby, "480 never appears near 'pdf/go' -- looks like an omission, not a disclosure"
    # the document must say something concrete about it: either a named real bucket or an explicit
    # "untraced" disclosure -- never leave the reader with neither
    lowered = text.lower()
    named_a_bucket = "promot" in lowered or "embed" in lowered  # Go struct-embedding, if traced
    explicitly_untraced = "untraced" in lowered
    assert named_a_bucket or explicitly_untraced, (
        "pdf/go's extra=480 is neither traced to a named bucket nor explicitly disclosed as untraced"
    )


def test_the_three_non_pdf_cpp_c_plus_plus_pilots_are_not_claimed_as_individually_traced() -> None:
    """cells/cpp, slides/cpp, and email/cpp must be distinguished from pdf/cpp's actually-confirmed,
    individually-traced root cause (OQ-022) -- the document must not imply all 4 C++ pilots were
    equally investigated."""
    text = _read_doc()
    for pilot in ("cells/cpp", "slides/cpp", "email/cpp"):
        assert pilot in text
    lowered = text.lower()
    assert (
        "not individually" in lowered
        or "not individually root-caused" in lowered
        or ("by inspection" in lowered)
    ), "the document must honestly disclose that these 3 pilots were not individually root-caused"


def test_pdf_cpp_root_cause_citation_oq_022_present() -> None:
    text = _read_doc()
    assert "OQ-022" in text
    assert "OQ-018" in text  # cells/rust root cause
