"""G3/TC-678 (REQ-G3-013, C4): fence-aware, offset-naming truncation in ``get_product_reference``.

``get_product_reference.py`` has its own, separate truncation helpers (``_cap_at_line``,
``_cap_at_paragraph``) that never checked fence state at all, so a cut landing inside a fenced
code block produced a genuinely malformed, unclosed-fence result - with a marker
(the old ``_TRUNCATED_MARKER``) that named no locator of any kind. This module proves the fix:

(a) the reproduce-first case (short prose, then a long fenced code block crossing a small cap) is
    now cut WITHOUT an unclosed fence, verified by running ``response_budget.fence_blocks`` over
    the result's own lines;
(b) a case with NO fence at all is byte-for-byte unaffected in its cut point - the same text is
    kept, only the marker's shape differs from before (it now names real character counts);
(c) the marker names the real N/M character counts;
(d) a fence that starts very close to the cap (almost no prose before it) is handled per this
    card's chosen minimum (show only the prose before the fence, or nothing if there is none)
    without crashing or returning anything nonsensical;
(e) a real call site (``_limitations_from_readme``) is exercised end-to-end, proving the fix
    reaches production through the shared helper, not just the helper functions in isolation.
"""

from __future__ import annotations

import re

from foss_mcp.mcp.response_budget import fence_blocks
from foss_mcp.mcp.tools import get_product_reference as gpr
from foss_mcp.mcp.tools.get_product_reference import ReferenceContent
from foss_mcp.mcp.tools.readme_sections import ReadmeSection

_MARKER_RE = re.compile(r"^\[truncated: showing (\d+) of (\d+) characters\]$")


def _has_unclosed_fence(text: str) -> bool:
    """True when any line of *text* belongs to a fenced block that ``fence_blocks`` reports as
    running all the way to the last line AND that last line is not itself a genuine closing fence
    delimiter - i.e. an odd number of fence-delimiter lines, the simplest, independent proof that a
    fence was opened and never closed within *text*."""
    lines = text.split("\n")
    fence_delimiter_lines = sum(1 for line in lines if line.strip().startswith(("```", "~~~")))
    if fence_delimiter_lines % 2 != 0:
        return True
    # Belt and suspenders: ask fence_blocks itself whether the very last line is still "inside" a
    # block whose own last member is that same last line - the shape an unclosed trailing fence
    # takes under fence_blocks' own fallback rule ("one never closed runs to the end").
    blocks = fence_blocks(lines)
    if not blocks or blocks[-1] is None:
        return False
    start, end = blocks[-1]
    return end == len(lines) - 1 and not lines[end].strip().startswith(("```", "~~~"))


def _long_fenced_text(prose: str = "Short lead-in paragraph describing a limitation.\n\n") -> str:
    """The reproduce-first shape: short prose, then a fenced python block long enough that a small
    cap lands inside it."""
    body = "\n".join(f"line_{i} = {i}" for i in range(300))
    return f"{prose}```python\n{body}\n```\n\nTrailing prose after the fence.\n"


# ---- (a) reproduce-first: now cut without an unclosed fence -------------------------------------


def test_reproduce_first_case_is_cut_without_an_unclosed_fence() -> None:
    text = _long_fenced_text()
    # First, confirm the defect is real against the raw cut this module's own helper starts from
    # (text[:cap]) - i.e. that this synthetic text really does cross the fence at this cap, so the
    # proof below is not vacuous.
    raw_cut = text[:600]
    assert _has_unclosed_fence(raw_cut), "the synthetic text does not even cross the fence at cap=600"

    result = gpr._cap_at_line(text, 600)
    assert not _has_unclosed_fence(result), result
    # The marker is present and the result is not empty: there was real prose to show.
    assert result.startswith("Short lead-in paragraph")
    marker_line = result.rstrip().rsplit("\n", 1)[-1]
    assert _MARKER_RE.match(marker_line), marker_line


def test_reproduce_first_case_via_cap_at_paragraph_too() -> None:
    text = _long_fenced_text()
    result = gpr._cap_at_paragraph(text, 600)
    assert not _has_unclosed_fence(result), result


# ---- (b) a no-fence case is byte-for-byte unaffected in its cut point ---------------------------


def test_cap_at_line_no_fence_keeps_the_identical_cut_point() -> None:
    """Same synthetic text the pre-existing tests/conformance/test_reference_content.py ground-truth
    test uses: the KEPT TEXT (before the marker) must be byte-for-byte identical to what the old,
    fence-unaware implementation produced - only the marker's format is allowed to change."""
    clean = "first line\n" + "x" * 40
    result = gpr._cap_at_line(clean, 15)
    kept, _, marker_line = result.partition("\n")
    assert kept == "first line"
    assert _MARKER_RE.match(marker_line), marker_line

    no_boundary = "a" * 20
    result = gpr._cap_at_line(no_boundary, 15)
    kept, _, marker_line = result.partition("\n")
    assert kept == "a" * 15
    assert _MARKER_RE.match(marker_line), marker_line


def test_cap_at_paragraph_no_fence_keeps_the_identical_cut_point() -> None:
    clean = "para one.\n\n" + "y" * 40
    result = gpr._cap_at_paragraph(clean, 15)
    kept, _, marker_line = result.partition("\n\n")
    assert kept == "para one."
    assert _MARKER_RE.match(marker_line), marker_line

    no_boundary = "b" * 20
    result = gpr._cap_at_paragraph(no_boundary, 15)
    kept, _, marker_line = result.partition("\n\n")
    assert kept == "b" * 15
    assert _MARKER_RE.match(marker_line), marker_line


def test_untouched_when_text_fits_within_the_cap() -> None:
    short = "nothing to cut here"
    assert gpr._cap_at_line(short, 1000) == short
    assert gpr._cap_at_paragraph(short, 1000) == short


# ---- (c) the marker names the real N/M character counts -----------------------------------------


def test_marker_names_the_real_shown_and_total_character_counts() -> None:
    text = "word " * 20  # 100 characters, no newline at all
    cap = 15
    result = gpr._cap_at_line(text, cap)
    kept, _, marker_line = result.partition("\n")
    match = _MARKER_RE.match(marker_line)
    assert match is not None, marker_line
    shown, total = int(match.group(1)), int(match.group(2))
    assert shown == len(kept)
    assert total == len(text)
    assert total == 100


def test_marker_names_the_real_counts_after_a_fence_snap_back() -> None:
    text = _long_fenced_text()
    result = gpr._cap_at_line(text, 600)
    kept, _, marker_line = result.rpartition("\n")
    match = _MARKER_RE.match(marker_line)
    assert match is not None, marker_line
    shown, total = int(match.group(1)), int(match.group(2))
    assert total == len(text)
    # shown reflects what was ACTUALLY kept after snapping back past the opened fence, not the raw
    # cap - strictly less than the cap because the whole fence was dropped.
    assert shown < 600
    assert shown == len(kept)


# ---- (d) a fence starting very close to the cap is handled sanely -------------------------------


def test_fence_opening_on_the_very_last_kept_line_drops_the_fence_and_keeps_the_prose() -> None:
    """The fence opens exactly on the line the cap would otherwise keep as its last line: almost no
    prose before it survives besides that lead line itself; the fence must still never appear
    unclosed, and nothing crashes."""
    text = "Lead line.\n```python\n" + "\n".join(f"x{i} = {i}" for i in range(200)) + "\n```\n"
    cap = len("Lead line.\n```python")  # the cap lands exactly at the end of the opening fence line
    result = gpr._cap_at_line(text, cap)
    assert not _has_unclosed_fence(result), result
    assert "```" not in result


def test_fence_covering_the_whole_text_from_line_zero_yields_an_empty_excerpt_not_a_crash() -> None:
    """No prose precedes the fence at all: G3/TC-678's chosen minimum is to show nothing (no
    markdown fence) rather than any malformed fragment."""
    text = "```python\n" + "\n".join(f"x{i} = {i}" for i in range(200)) + "\n```\n"
    result = gpr._cap_at_line(text, 30)
    assert not _has_unclosed_fence(result)
    assert "```" not in result
    marker_line = result.strip()
    match = _MARKER_RE.match(marker_line)
    assert match is not None, marker_line
    assert int(match.group(1)) == 0


def test_fence_near_cap_never_moves_the_cut_forward_past_the_cap() -> None:
    """Backing off to the fence's opening line only ever moves the cut BACKWARD; the result must
    never be longer than the original, uncut candidate the cap allowed."""
    text = "P\n```python\n" + "\n".join(f"x{i}=1" for i in range(50)) + "\n```\n"
    cap = 5
    result = gpr._cap_at_line(text, cap)
    kept, _, _marker_line = result.partition("\n")
    assert len(kept) <= cap
    assert not _has_unclosed_fence(result)


# ---- (e) a real call site is exercised end-to-end through the shared helper ---------------------


def test_limitations_from_readme_end_to_end_is_fence_safe_and_offset_marked() -> None:
    section = ReadmeSection(
        title="Limitations",
        text=_long_fenced_text(prose="This library has a few known limitations worth noting up front.\n\n"),
        commit="abc123def4567890",
        ordered=True,
        parts=1,
    )
    original_cap = gpr._LIMITATIONS_CAP
    try:
        gpr._LIMITATIONS_CAP = 600  # force the real cap small enough to cross the fence
        content = gpr._limitations_from_readme(section)
    finally:
        gpr._LIMITATIONS_CAP = original_cap

    assert isinstance(content, ReferenceContent)
    assert content.section == "limitations"
    assert content.untrusted_upstream_content is True
    assert not _has_unclosed_fence(content.text), content.text
    marker_line = content.text.rstrip().rsplit("\n", 1)[-1]
    assert _MARKER_RE.match(marker_line), marker_line
    assert content.text.startswith("This library has a few known limitations")
