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


def test_the_three_non_pdf_cpp_c_plus_plus_pilots_are_now_individually_root_caused_by_tc_732() -> None:
    """G3/TC-732: cells/cpp, slides/cpp, and email/cpp were left "by inspection alone" by TC-691 --
    TC-732 finished individually root-causing all three against their real pinned source. The document
    must now credit TC-732 for this, by name, not merely still carry TC-691's older disclosure
    unchanged."""
    text = _read_doc()
    for pilot in ("cells/cpp", "slides/cpp", "email/cpp"):
        assert pilot in text
    assert "TC-732" in text
    lowered = text.lower()
    assert "individually root-caused" in lowered, (
        "the document must now say these 3 pilots were individually root-caused, not merely still "
        "carry the old 'by inspection alone' disclosure with no real tracing ever added"
    )


def test_cells_cpp_signature_13_is_confirmed_bucket_2_with_real_symbols() -> None:
    """G3/TC-732's own real finding for cells/cpp: all 13 signature findings are bare-name/overload
    collisions, confirmed individually against the real pinned source (9f852d0ff1cfdad2d661556d6b87a8eff8c063a2) --
    never merely assumed from the category shape."""
    text = _read_doc()
    assert "9f852d0ff1cfdad2d661556d6b87a8eff8c063a2" in text
    for symbol in ("CellArea", "CreateCellArea", "CellsException", "CellValue", "WorkbookSettings", "MakeElement"):
        assert symbol in text, f"real cells/cpp bucket-2 symbol {symbol!r} missing from {DOC}"
    assert "bucket 1 does" in text.lower() and "not apply" in text.lower(), (
        "the document must say bucket 1 was confirmed inapplicable to cells/cpp, not merely silent"
    )


def test_email_cpp_extra_24_is_confirmed_bucket_1_with_exact_zero_remainder() -> None:
    """G3/TC-732's own real finding for email/cpp: extra=24 is bucket 1 (inheritance flattening),
    confirmed EXACTLY -- 12 real cfb_node members flattened onto each of cfb_storage and cfb_stream,
    0 remainder -- against the real pinned source (c844a467cf7f2503819b655f4d6ceaef91056c40)."""
    text = _read_doc()
    assert "c844a467cf7f2503819b655f4d6ceaef91056c40" in text
    for symbol in ("cfb_node", "cfb_storage", "cfb_stream"):
        assert symbol in text, f"real email/cpp bucket-1 symbol {symbol!r} missing from {DOC}"
    assert "zero remainder" in text.lower() or "0 remainder" in text.lower() or "zero-remainder" in text.lower()
    # the 7 real bucket-2 signature findings must also be named, not only the bucket-1 extra ones
    for symbol in ("cfb_document", "load_from_eml", "add_attachment"):
        assert symbol in text, f"real email/cpp bucket-2 symbol {symbol!r} missing from {DOC}"


def test_slides_cpp_bucket_1_and_bucket_2_are_both_confirmed_with_real_symbols() -> None:
    """G3/TC-732's own real finding for slides/cpp: signature=37 is bucket 2 (confirmed against the
    real pinned source, 469ce77a07b988c49deb930632b8276c89ec6b66), and extra=153 is confirmed bucket 1
    by direct inspection of two real inheritance examples (SimpleColorFormat:ColorFormat,
    Slide:BaseSlide), even though TC-732's own live re-run could not reproduce the 153 count itself --
    that non-reproduction must be disclosed honestly, not hidden."""
    text = _read_doc()
    assert "469ce77a07b988c49deb930632b8276c89ec6b66" in text
    for symbol in ("SimpleColorFormat", "ColorFormat", "Slide", "BaseSlide"):
        assert symbol in text, f"real slides/cpp bucket-1 symbol {symbol!r} missing from {DOC}"
    for symbol in ("PointF", "IShapeCollection", "reorder"):
        assert symbol in text, f"real slides/cpp bucket-2 symbol {symbol!r} missing from {DOC}"
    # the non-reproduction of extra=153 in TC-732's own environment must be disclosed, not silently
    # swapped in for a false "confirmed exactly" claim
    assert "could not" in text and "reproduce" in text.lower()
    assert "TC-624" in text, "the diff-engine ancestor-verification fix TC-624 landed must be cited"
    assert "pugixml" in text.lower(), "the real third-party header-resolution gap must be named"


def test_pdf_cpp_root_cause_citation_oq_022_present() -> None:
    text = _read_doc()
    assert "OQ-022" in text
    assert "OQ-018" in text  # cells/rust root cause
