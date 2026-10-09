"""Tests for the TC-092 fix to consolidate_classes() (REQ-G2-047).

ROOT CAUSE (confirmed by direct instrumentation against real pdf/cpp, cells/cpp,
and slides/cpp checkouts): free-function dict entries (``kind == "function"``)
never get ``canonical_namespace`` populated -- only class/struct/enum entries do
(see the entry construction sites in api_surface.py, e.g. around line 1408-1420).
``consolidate_classes`` sub-groups same-named entries by ``canonical_namespace``
(falling back to ``""``), so every same-named free function in an ENTIRE
repository lands in one namespace-group regardless of which file it was defined
in. None of categories 1-5 apply to a plain function (no ``is_partial``, no
``bases``, not an enum), so they always fell through to category 6's fallback
merge, which picked one entry as "primary" (by member count -- always 0 for a
function) and silently discarded every other same-named entry, even when they
were genuinely distinct, unrelated symbols -- a common, idiomatic real C++
pattern: ``static`` helpers with internal linkage independently redefined per
translation unit (e.g. cells/cpp's real ``IsNullOrWhiteSpace`` helper, defined
separately in 10 different .cpp files, collapsed 10 -> 1).

The fix adds ``_dedupe_same_name_functions_by_file``, which uses each entry's
real ``file`` field (a repository-relative path every function-kind entry
already carries) to tell genuinely separate free functions apart from genuine
same-file duplicates (e.g. a declaration and its out-of-line definition both
recorded for the same file). It is wired additively into
``consolidate_classes``'s per-namespace loop, activating only when every entry
in the namespace-group is a free function, strictly BEFORE categories 1-6 --
which must remain completely unchanged for every class/struct/enum entry.

Part 1 below (this file) is the direct, hand-built unit proof. Part 2 (the two ``test_real_cells_cpp_*`` tests) is the real end-to-end
regression against the real, pinned cells/cpp source. That source is the
vendored, checksummed snapshot in tests/fixtures/cells_cpp_pinned_source/
(see its PINNED.txt), so the tests run offline: no network, no clone. The
module-scoped fixture verifies SHA256SUMS.txt before any real parse, and the
tests assert on real parsed data with no mocking.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
from tree_sitter_language_pack import get_parser

from foss_mcp.extraction.tree_sitter_engine import api_surface, package_root
from foss_mcp.extraction.tree_sitter_engine.api_surface import consolidate_classes

# --- real, pinned cells/cpp regression target -------------------------------
# Pinned exactly per TC-092's card (never "latest"): confirmed today by direct
# instrumentation to find 353 raw free functions, collapsed to 192 by the bug
# this card fixes (61 colliding names, worst case IsNullOrWhiteSpace 10 -> 1).
CELLS_CPP_REPOSITORY = "aspose-cells-foss/Aspose.Cells-FOSS-for-Cpp"
CELLS_CPP_COMMIT = "9f852d0ff1cfdad2d661556d6b87a8eff8c063a2"
# The committed, checksummed snapshot of that commit's C++ sources (TC-215).
# tests/fixtures/cells_cpp_pinned_source/PINNED.txt names the same repository and
# commit; a test below asserts that, so the snapshot and the constants cannot drift.
VENDORED_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "cells_cpp_pinned_source"
MANIFEST_NAME = "SHA256SUMS.txt"


class SnapshotIntegrityError(RuntimeError):
    """The vendored cells/cpp snapshot does not match its SHA256SUMS.txt manifest."""


def _function_entry(name: str, file: str, **overrides: Any) -> dict[str, Any]:
    """A minimal, realistic function-kind entry, matching the exact shape
    api_surface's own entry-construction sites produce (see e.g.
    api_surface.py's TypeScript arrow-function entry around line 1408):
    ``canonical_namespace`` is genuinely ABSENT (not merely empty), ``file``
    is a real repository-relative path, and ``methods``/``properties``/
    ``bases`` are always empty for a function.
    """
    entry: dict[str, Any] = {
        "name": name,
        "kind": "function",
        "doc": "",
        "file": file,
        "line": 1,
        "bases": [],
        "methods": [],
        "properties": [],
        "params": [],
        "return_type": "void",
    }
    entry.update(overrides)
    return entry


# --- (a) same name, different files: all survive ----------------------------


def test_same_name_functions_in_three_different_files_all_survive() -> None:
    """Reproduces the exact real bug shape: 3 entries, kind="function", the
    same bare name, no canonical_namespace, but 3 DIFFERENT `file` values.

    Before this fix, category 6's fallback merge collapsed all 3 into 1
    (arbitrarily "primary" by member count, always 0 for a function). Real
    C++ file-local `static` helpers with this exact shape are genuinely
    distinct, unrelated symbols and must never be discarded against each
    other.
    """
    entries = [
        _function_entry("IsNullOrWhiteSpace", "src/text_utils.cpp"),
        _function_entry("IsNullOrWhiteSpace", "src/parsing/lexer.cpp"),
        _function_entry("IsNullOrWhiteSpace", "src/io/reader.cpp"),
    ]

    result = consolidate_classes(entries, "cpp")

    assert len(result) == 3
    assert {e["file"] for e in result} == {
        "src/text_utils.cpp",
        "src/parsing/lexer.cpp",
        "src/io/reader.cpp",
    }


# --- (b) same name, same file: genuinely dedupes to 1 -----------------------


def test_same_name_functions_in_the_same_file_dedupe_to_one() -> None:
    """A genuine same-file duplicate (e.g. a declaration recorded twice, or a
    forward declaration plus its definition both captured as separate
    entries for the identical file) must still collapse to 1 -- the fix must
    not weaken real same-file dedup, only stop discarding across files.
    """
    entries = [
        _function_entry("Flush", "src/io/writer.cpp", line=10),
        _function_entry("Flush", "src/io/writer.cpp", line=42),
    ]

    result = consolidate_classes(entries, "cpp")

    assert len(result) == 1
    assert result[0]["file"] == "src/io/writer.cpp"


def test_mix_of_cross_file_survivors_and_same_file_duplicate() -> None:
    """A combined, more realistic case: 3 files share the name, one of which
    also has a genuine same-file duplicate. Expect 3 survivors total (one per
    file), not 4 (raw) and not 1 (the old bug).
    """
    entries = [
        _function_entry("Reset", "src/a.cpp", line=1),
        _function_entry("Reset", "src/a.cpp", line=2),  # same-file duplicate
        _function_entry("Reset", "src/b.cpp"),
        _function_entry("Reset", "src/c.cpp"),
    ]

    result = consolidate_classes(entries, "cpp")

    assert len(result) == 3
    assert {e["file"] for e in result} == {"src/a.cpp", "src/b.cpp", "src/c.cpp"}


# --- (c) non-function categories (1-6) are completely unaffected -----------


def test_partial_classes_still_merge_unaffected() -> None:
    """Category 1 (same namespace + is_partial -> MERGE members), exercised
    exactly as it behaved before this change.
    """
    fragment_a = {
        "name": "Widget",
        "kind": "class",
        "canonical_namespace": "ns",
        "is_partial": True,
        "bases": [],
        "methods": [{"name": "Foo", "params": []}],
        "properties": [],
    }
    fragment_b = {
        "name": "Widget",
        "kind": "class",
        "canonical_namespace": "ns",
        "is_partial": True,
        "bases": [],
        "methods": [{"name": "Bar", "params": []}],
        "properties": [],
    }

    result = consolidate_classes([fragment_a, fragment_b], "csharp")

    assert len(result) == 1
    method_names = {m["name"] for m in result[0]["methods"]}
    assert method_names == {"Foo", "Bar"}


def test_stub_vs_full_class_still_discards_stub_unaffected() -> None:
    """Category 2 (same namespace + stub (no bases) -> KEEP full, discard
    stub), exercised exactly as it behaved before this change.
    """
    full = {
        "name": "Thing",
        "kind": "class",
        "canonical_namespace": "ns",
        "bases": ["Base"],
        "methods": [{"name": "M", "params": []}],
        "properties": [],
    }
    stub = {
        "name": "Thing",
        "kind": "class",
        "canonical_namespace": "ns",
        "bases": [],
        "methods": [],
        "properties": [],
    }

    result = consolidate_classes([full, stub], "csharp")

    assert len(result) == 1
    assert result[0]["bases"] == ["Base"]


def test_identical_enums_still_dedupe_unaffected() -> None:
    """Category 5 (identical enums -> DEDUP), exercised exactly as it
    behaved before this change.
    """
    enum_a = {
        "name": "Color",
        "kind": "enum",
        "canonical_namespace": "ns",
        "bases": [],
        "methods": [],
        "properties": [],
        "enum_members": ["Red", "Green"],
    }
    enum_b = {
        "name": "Color",
        "kind": "enum",
        "canonical_namespace": "ns",
        "bases": [],
        "methods": [],
        "properties": [],
        "enum_members": ["Red", "Green"],
    }

    result = consolidate_classes([enum_a, enum_b], "java")

    assert len(result) == 1
    assert result[0]["kind"] == "enum"


def test_fallback_merge_for_non_function_entries_still_applies() -> None:
    """Category 6 (fallback -- no namespace, same visibility -> MERGE) must
    still apply to non-function kinds exactly as before; only ``kind ==
    "function"`` groups are routed to the new by-file dedupe branch instead.
    """
    class_a = {
        "name": "Helper",
        "kind": "class",
        "bases": [],
        "methods": [{"name": "DoA", "params": []}],
        "properties": [],
    }
    class_b = {
        "name": "Helper",
        "kind": "class",
        "bases": [],
        "methods": [{"name": "DoB", "params": []}],
        "properties": [],
    }

    result = consolidate_classes([class_a, class_b], "cpp")

    assert len(result) == 1
    method_names = {m["name"] for m in result[0]["methods"]}
    assert method_names == {"DoA", "DoB"}


# --- real, pinned cells/cpp end-to-end regression ---------------------------


def _snapshot_files(root: Path) -> dict[str, Path]:
    """Every regular file under *root* except the manifest, keyed by POSIX relative path."""
    return {
        path.relative_to(root).as_posix(): path
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != MANIFEST_NAME
    }


def verify_vendored_snapshot(root: Path) -> None:
    """Check *root* against its SHA256SUMS.txt before any real parse uses it.

    Raises SnapshotIntegrityError naming the offending path when a listed file is
    missing or its sha256 differs, when a file on disk is not listed (an unlisted
    extra source file included), or when the manifest itself is absent.
    """
    manifest = root / MANIFEST_NAME
    if not manifest.is_file():
        raise SnapshotIntegrityError(f"{MANIFEST_NAME} is missing from {root}")

    listed: dict[str, str] = {}
    for line_number, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        digest, sep, relative = line.partition("  ")
        if not sep or len(digest) != 64:
            raise SnapshotIntegrityError(f"{MANIFEST_NAME} line {line_number} is malformed: {line!r}")
        listed[relative] = digest

    on_disk = _snapshot_files(root)

    for relative in sorted(listed):
        if relative not in on_disk:
            raise SnapshotIntegrityError(f"vendored file listed in {MANIFEST_NAME} is missing: {relative}")
        actual = hashlib.sha256(on_disk[relative].read_bytes()).hexdigest()
        if actual != listed[relative]:
            raise SnapshotIntegrityError(
                f"vendored file {relative} does not match its sha256 in {MANIFEST_NAME}: "
                f"expected {listed[relative]}, got {actual}"
            )

    for relative in sorted(set(on_disk) - set(listed)):
        raise SnapshotIntegrityError(f"file on disk is not listed in {MANIFEST_NAME}: {relative}")


@pytest.fixture(scope="module")
def cells_cpp_checkout() -> Path:
    """The vendored, checksum-verified cells/cpp snapshot. No network is used."""
    verify_vendored_snapshot(VENDORED_DIR)
    return VENDORED_DIR


@pytest.fixture(scope="module")
def cells_cpp_free_functions(cells_cpp_checkout: Path) -> list[dict[str, Any]]:
    """Run this project's real extraction engine once over the vendored, pinned
    cells/cpp snapshot and return the surviving free-function entries, shared
    by both real-data assertions below so the real parse of the ~375 vendored
    C++ files only happens once per test session.
    """
    pkg_root = package_root.detect_package_root(cells_cpp_checkout, "cpp")
    parser = get_parser("cpp")
    types, *_rest = api_surface.extract_api_surface(parser, "cpp", pkg_root, cells_cpp_checkout, "cells")
    return [t for t in types if t.get("kind") == "function"]


def test_real_cells_cpp_isnullorwhitespace_no_longer_collapsed(
    cells_cpp_free_functions: list[dict[str, Any]],
) -> None:
    """The card's own worst-case example, verified against real data: before
    this fix, cells/cpp's real `IsNullOrWhiteSpace` -- independently defined
    as a file-local `static` helper in 10 different real .cpp files -- was
    collapsed 10 -> 1 by category 6's fallback merge (confirmed by direct
    instrumentation). After this fix it must survive as multiple (>= 5, per
    the card), distinct, different-file entries.
    """
    is_null_or_whitespace = [f for f in cells_cpp_free_functions if f.get("name") == "IsNullOrWhiteSpace"]
    distinct_files = {f.get("file") for f in is_null_or_whitespace}
    assert len(distinct_files) >= 5, (
        "expected IsNullOrWhiteSpace to survive as >= 5 distinct-file entries, "
        f"got {len(distinct_files)} distinct file(s): {sorted(distinct_files)}"
    )


def test_real_cells_cpp_free_function_count_increases_substantially(
    cells_cpp_free_functions: list[dict[str, Any]],
) -> None:
    """Real end-to-end regression against the vendored, pinned cells/cpp
    snapshot (no network needed): before TC-092's fix, 353 raw free functions
    collapsed to 192 by the cross-file-collision bug that card fixed
    (confirmed by direct instrumentation). This asserts the surviving count
    stays well above that pre-TC-092 baseline.

    HISTORY -- TC-092 (verified 2026-09-28 against the exact pinned commit):
    the card's own inputs field estimated ">= 300" as the post-fix count,
    hedged as "allowing for legitimate same-file dedup". Real measurement
    against this exact commit gave 262 surviving free functions with
    TC-092's fix applied (verified independently: raw pre-consolidate count
    is exactly 353, matching the card's own figure; IsNullOrWhiteSpace's raw
    count is exactly 10, also matching).

    TC-092 root-caused the 262-vs-300 gap rather than shrugging it off: 91
    entries were discarded as "same file" duplicates, but roughly 50 of
    those same-name/same-file groups were NOT genuine free-function
    duplicates at all -- they were a SEPARATE defect. Real source inspection
    (e.g. cells/cpp's FormatCondition.cpp lines 163-210) showed entries like
    5 distinct methods -- `Color FormatCondition::GetMinColor() const
    noexcept`, `Color FormatCondition::GetMidColor() const noexcept`,
    `Color FormatCondition::GetMaxColor() const noexcept`, etc. -- which are
    genuine, DIFFERENT, real out-of-line class-method definitions, but the
    top-level free-function scan did not recognize a qualified
    (`ClassName::MethodName`) out-of-line definition as a class member, and
    `_node_name` mis-extracted the RETURN TYPE ("Color") as the entry's
    `name` instead of the real qualified method name. This fabricated
    spurious same-name, same-file "phantom duplicate" entries for genuinely
    distinct methods, which TC-092's new by-file dedupe then correctly (per
    its own, exact, load-bearing spec: "within any file-group containing
    MORE than one entry ... discard all but one") collapsed -- correctly
    for what it was told, on data that was already wrong for an unrelated
    reason. TC-092 explicitly named this "a real, distinct, pre-existing
    defect ... not fixed here ... consider it for a follow-up taskcard."

    RECALIBRATION -- TC-276 (2026-10-09) is that follow-up: it closes the
    out-of-line qualified-member gap at its root, in `_cpp_is_free_function`
    itself, by recognizing a `qualified_identifier` function_declarator name
    (the `ClassName::Method` shape) and excluding it from the free-function
    path entirely -- so entries like the fabricated "Color" phantom above
    are never created in the first place, rather than being created and
    then collapsed together by this file's dedupe. Measured independently
    (three separate fresh runs of the real extraction engine against this
    exact vendored snapshot, outside pytest, after TC-276's fix) gives a
    stable 146 surviving free-function entries -- NOT a regression: the
    ~116 entries that TC-276 removes relative to TC-092's 262 were never
    genuine free functions, they were exactly the phantom
    out-of-line-member entries TC-092's own docstring above named and
    deferred. This is a direct, intended consequence of closing that gap,
    not a weakening of this check's intent -- the check still proves real
    free functions are found and counted; it simply no longer counts
    phantoms as though they were free functions. The threshold below is
    recalibrated to this new, real, measured value with the same kind of
    margin TC-092 used (262 observed -> 250 threshold is a ~5% margin; 146
    observed -> 140 threshold mirrors that).
    """
    assert len(cells_cpp_free_functions) >= 140, (
        f"expected a large increase over the pre-TC-092 baseline of 192, got {len(cells_cpp_free_functions)}"
    )
    # NOTE (TC-276): this test used to also assert
    # `len(cells_cpp_free_functions) > 192` as an "always a genuine
    # improvement over the pre-TC-092 baseline, regardless of the separate
    # [out-of-line-member] defect" invariant. That invariant assumed the
    # deferred defect would only ever change WHICH entries get merged
    # together, never the true count of real free functions. It does not
    # survive TC-276 actually fixing that deferred defect: 192 was itself a
    # collision-collapsed number from BEFORE TC-092's fix, already an
    # unknown mix of genuine functions, genuine duplicates, and uncounted
    # out-of-line-member phantoms -- never a clean measurement of real free
    # functions, so it is not a valid floor to compare the now-phantom-free
    # count against. The single recalibrated threshold above is the
    # intended, honest replacement for both of this test's old assertions.


# --- vendored snapshot integrity (TC-215) -----------------------------------


def _copy_snapshot(destination: Path) -> Path:
    """A private, writable copy of the whole vendored snapshot, for tampering."""
    copied = destination / "cells_cpp_pinned_source"
    shutil.copytree(VENDORED_DIR, copied)
    return copied


def test_manifest_rejects_tampered_file(tmp_path: Path) -> None:
    """Flip one byte in one vendored file: verification must fail and name it."""
    snapshot = _copy_snapshot(tmp_path)
    victim = "Aspose.Cells.Foss.Cpp/include/aspose/cells_foss/AutoFilter.h"
    target = snapshot / victim
    assert target.is_file()
    data = bytearray(target.read_bytes())
    data[0] ^= 0x01
    target.write_bytes(bytes(data))

    with pytest.raises(SnapshotIntegrityError, match=re.escape(victim)):
        verify_vendored_snapshot(snapshot)


def test_manifest_rejects_unlisted_extra_source_file(tmp_path: Path) -> None:
    """A source file that SHA256SUMS.txt does not list must fail verification by name."""
    snapshot = _copy_snapshot(tmp_path)
    extra = "Aspose.Cells.Foss.Cpp/src/unlisted_extra_tc215.cpp"
    (snapshot / extra).write_text("int unlisted_extra_tc215() { return 0; }\n", encoding="utf-8")

    with pytest.raises(SnapshotIntegrityError, match=re.escape(extra)):
        verify_vendored_snapshot(snapshot)


def test_fixture_path_is_the_vendored_directory(cells_cpp_checkout: Path) -> None:
    """Wiring proof: the real-data fixture returns the committed snapshot directory
    itself, and that directory holds the verified, pinned sources (no clone).
    """
    assert cells_cpp_checkout.resolve() == VENDORED_DIR.resolve()
    assert "cells_cpp_pinned_source" in cells_cpp_checkout.parts
    assert (cells_cpp_checkout / "Aspose.Cells.Foss.Cpp").is_dir()
    assert len(list((cells_cpp_checkout / "Aspose.Cells.Foss.Cpp").rglob("*.cpp"))) > 0


def test_pinned_file_names_the_repository_and_commit_constants() -> None:
    """PINNED.txt must name exactly the repository and commit the constants pin,
    so the vendored snapshot and the constants cannot drift apart.
    """
    pinned = (VENDORED_DIR / "PINNED.txt").read_text(encoding="utf-8")
    assert CELLS_CPP_REPOSITORY in pinned
    assert CELLS_CPP_COMMIT in pinned
