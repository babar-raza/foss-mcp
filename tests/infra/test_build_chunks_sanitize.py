"""Offline tests for infra/build_chunks.py's chunk sanitising (REQ-G3-006, TC-385).

Chunk text used to be published exactly as upstream wrote it: zero-width and bidirectional
control characters, Hugo shortcodes in example descriptions, and no bound on size. These tests
pin the one sanitising step every chunk now passes: control characters out everywhere, shortcodes
out of prose (never out of verified code), and a hard cap with an explicit truncation line.

Everything is offline: the rust prepare and verify are replaced, the same seams
tests/infra/test_build_chunks_degradation.py uses. No test depends on time.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import infra.build_chunks as build_chunks
from foss_mcp.indexing.example_verifier import VerificationResult

LIMIT = 24_000
EXAMPLE_API_SURFACE = Path(__file__).resolve().parents[2] / "tests/fixtures/pdf_net/api_surface.json"

LIBRARY_REPOSITORY = "example-org/example-rust-library"
LIBRARY_COMMIT = "1" * 40
SOURCE_COMMIT = "2" * 40

# Built with chr(), never typed literally.
REMOVED_CODE_POINTS = (
    [0x00AD]
    + list(range(0x200B, 0x2010))
    + list(range(0x202A, 0x202F))
    + list(range(0x2060, 0x2065))
    + list(range(0x2066, 0x206A))
    + [0xFEFF]
)
ZWSP = chr(0x200B)
RLO = chr(0x202E)

SHORTCODE = '{{< package-install "pdf" "go" >}}'
PERCENT_SHORTCODE = '{{% notice "warning" %}}'


def _sanitize(text: str) -> str:
    return build_chunks._sanitize_chunk_text(text)


# (1) invisible and bidirectional control characters


@pytest.mark.parametrize("code_point", REMOVED_CODE_POINTS, ids=lambda cp: f"U+{cp:04X}")
def test_each_listed_code_point_is_removed(code_point: int) -> None:
    assert _sanitize(f"ab{chr(code_point)}cd") == "abcd"


def test_the_removed_set_is_exactly_the_listed_ranges() -> None:
    assert len(REMOVED_CODE_POINTS) == 1 + 5 + 5 + 5 + 4 + 1
    # The neighbours just outside each range are ordinary characters and are kept.
    for code_point in (
        0x00AC,
        0x00AE,
        0x200A,
        0x2010,
        0x2029,
        0x202F,
        0x205F,
        0x2065,
        0x206A,
        0xFEFE,
        0xFF00,
    ):
        text = f"a{chr(code_point)}b"
        assert _sanitize(text) == text


def test_ordinary_non_ascii_text_is_kept() -> None:
    text = "café naïve 中文文档 \U0001f600 مرحبا   —"
    assert _sanitize(text) == text


def test_control_characters_are_removed_inside_code_too() -> None:
    text = f"Kind: verified_example\ndescription\n\nExample:\nlet x = 1;{ZWSP}\nlet y = 2;{RLO}\n"
    assert _sanitize(text) == "Kind: verified_example\ndescription\n\nExample:\nlet x = 1;\nlet y = 2;\n"


# (2) Hugo shortcodes in prose only


def test_angle_bracket_shortcode_is_removed_from_a_description() -> None:
    text = f"Kind: verified_example\nInstall it. {SHORTCODE} Then open a document.\n\nExample:\ncode"
    expected = "Kind: verified_example\nInstall it. Then open a document.\n\nExample:\ncode"
    assert _sanitize(text) == expected


def test_percent_shortcode_is_removed_from_a_description() -> None:
    text = f"Read this {PERCENT_SHORTCODE} carefully.\n\nExample:\ncode"
    assert _sanitize(text) == "Read this carefully.\n\nExample:\ncode"


def test_shortcode_removal_is_non_greedy_across_two_shortcodes_on_one_line() -> None:
    text = "keep {{< a >}} middle {{% b %}} tail"
    assert _sanitize(text) == "keep middle tail"


def test_a_line_emptied_by_shortcode_removal_is_dropped() -> None:
    text = f"first\n{SHORTCODE}\nsecond\n\nthird"
    assert _sanitize(text) == "first\nsecond\n\nthird"


def test_a_blank_line_that_was_already_empty_is_kept() -> None:
    assert _sanitize("a\n\n\nb") == "a\n\n\nb"


def test_a_shortcode_in_text_with_no_example_line_is_removed() -> None:
    assert _sanitize('FQN: Doc: Page\nUse {{< ref "x" >}} here.') == "FQN: Doc: Page\nUse here."


def test_shortcode_looking_text_after_the_example_line_is_kept_verbatim() -> None:
    code = f'print("{SHORTCODE}")\ntemplate = "{PERCENT_SHORTCODE}"\n{{{{< raw >}}}}'
    text = f"Describe {SHORTCODE} it.\n\nExample:\n{code}"
    assert _sanitize(text) == f"Describe it.\n\nExample:\n{code}"


def test_only_a_line_that_is_exactly_example_marks_the_code_boundary() -> None:
    # "FQN: Example: ..." and "# Example: ..." are not the boundary; the shortcode in the
    # description that follows them is still prose.
    text = f"# Example: T\n\nFQN: Example: T\nbody {SHORTCODE} here\n\nExample:\ncode"
    assert _sanitize(text) == "# Example: T\n\nFQN: Example: T\nbody here\n\nExample:\ncode"


# (3) the size cap


def test_text_of_exactly_the_limit_is_unchanged() -> None:
    text = "y" * LIMIT
    assert len(text) == LIMIT
    assert _sanitize(text) == text


def test_text_over_the_limit_is_cut_at_the_last_newline_with_an_explicit_line() -> None:
    text = "\n".join(["x" * 99] * 300)
    assert len(text) == 29_999
    # Newlines sit at indexes 99, 199, ...; the last one at or before 24,000 is index 23,999.
    result = _sanitize(text)
    assert result == text[:23_999] + "\nTruncated: 6000 characters omitted"
    assert result.splitlines()[-1] == "Truncated: 6000 characters omitted"


def test_text_one_character_over_the_limit_is_capped() -> None:
    text = "a" * 10 + "\n" + "b" * (LIMIT - 10)
    assert len(text) == LIMIT + 1
    assert _sanitize(text) == "a" * 10 + "\nTruncated: " + str(LIMIT + 1 - 10) + " characters omitted"


def test_text_over_the_limit_with_no_newline_is_cut_at_the_limit() -> None:
    text = "z" * (LIMIT + 500)
    assert _sanitize(text) == "z" * LIMIT + "\nTruncated: 500 characters omitted"


def test_the_cap_counts_after_the_clean_up() -> None:
    text = ("w" * 100 + ZWSP + "\n") * 10  # 1,010 characters, 1,000 after clean-up
    assert _sanitize(text) == ("w" * 100 + "\n") * 10


# idempotence and no-op


def test_sanitising_twice_equals_sanitising_once() -> None:
    long_text = "\n".join([f"line {SHORTCODE} {i}{ZWSP}" + "p" * 80 for i in range(400)])
    for text in (long_text, f"a{ZWSP}b {{{{< x >}}}} c\n\nExample:\n{SHORTCODE}", "plain", ""):
        once = _sanitize(text)
        assert _sanitize(once) == once
    assert "Truncated: " in _sanitize(long_text)


def test_text_with_nothing_to_sanitise_is_returned_unchanged() -> None:
    text = "FQN: Aspose.Pdf.Page\nKind: class_declaration\n\nMethods:\n  - Save(path: str) -> void\n"
    assert _sanitize(text) == text
    assert _sanitize(text).encode("utf-8") == text.encode("utf-8")


# end to end through main


def _api_surface(tmp_path: Path) -> Path:
    fixture = {
        "language": "rust",
        "source_commit": SOURCE_COMMIT,
        "source_repository": LIBRARY_REPOSITORY,
        "types": [
            {
                "name": "Widget",
                "class_import": "demo::Widget",
                "kind": "struct",
                "doc": f"A widget{ZWSP} that is used {RLO}everywhere.",
                "methods": [{"name": "open", "params": [], "return_type": "Widget"}],
            }
        ],
    }
    path = tmp_path / "api_surface.json"
    path.write_text(json.dumps(fixture), encoding="utf-8")
    return path


EXAMPLE_CODE = f'fn main() {{\n    println!("{SHORTCODE}");\n    let t = "{PERCENT_SHORTCODE}";\n}}'


def _furnished_page(tmp_path: Path) -> Path:
    page = {
        "single": {
            "block": [
                {
                    "title": "Install and open",
                    "content": f"Install the crate. {SHORTCODE} Then open it.\n\n```rust\n{EXAMPLE_CODE}\n```\n",
                }
            ]
        },
        "overview": {"enable": True, "title": "Widget", "content": f"Widgets{ZWSP} are small."},
    }
    import yaml

    path = tmp_path / "page.md"
    path.write_text("---\n" + yaml.safe_dump(page, allow_unicode=True) + "---\n", encoding="utf-8")
    return path


def _run_main(argv: list[str]) -> None:
    old_argv = sys.argv
    sys.argv = ["build_chunks.py"] + argv
    try:
        build_chunks.main()
    finally:
        sys.argv = old_argv


def _build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, furnished: bool) -> list[dict]:
    argv = [
        "--api-surface",
        str(_api_surface(tmp_path)),
        "--title",
        "Sanitise fixture",
        "--out",
        str(tmp_path / "chunks.json"),
    ]
    if furnished:
        monkeypatch.setattr(build_chunks, "prepare_rust_library", lambda repository, commit, workdir: workdir)

        def fake_verify(candidate, *, workdir, library_crate_name, library_dir):
            return VerificationResult(candidate=candidate, verified=True, output="fake output")

        monkeypatch.setitem(build_chunks._PLATFORM_DISPATCH["rust"], "verify", fake_verify)
        argv += [
            "--furnished-page",
            str(_furnished_page(tmp_path)),
            "--library-repository",
            LIBRARY_REPOSITORY,
            "--library-commit",
            LIBRARY_COMMIT,
            "--library-platform",
            "rust",
            "--library-crate-name",
            "example_crate",
        ]
    _run_main(argv)
    return json.loads((tmp_path / "chunks.json").read_text(encoding="utf-8"))["chunks"]


def test_end_to_end_chunks_have_no_invisible_characters_or_shortcodes_in_prose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunks = _build(tmp_path, monkeypatch, furnished=True)
    texts = [chunk["text"] for chunk in chunks]
    assert any("FQN: demo::Widget" in text for text in texts), "the API-surface chunk must be written"
    example_texts = [text for text in texts if "FQN: Example: " in text]
    assert example_texts, "the stubbed-verified example must be written"
    for text in texts:
        assert not any(chr(code_point) in text for code_point in REMOVED_CODE_POINTS)
    for text in example_texts:
        prose = text.split("\nExample:\n", 1)[0]
        assert "{{" not in prose and "}}" not in prose
        assert "Install the crate. Then open it." in prose


def test_end_to_end_example_code_is_byte_identical(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    chunks = _build(tmp_path, monkeypatch, furnished=True)
    example_texts = [chunk["text"] for chunk in chunks if "FQN: Example: " in chunk["text"]]
    assert len(example_texts) == 1
    code = example_texts[0].split("\nExample:\n", 1)[1].rsplit("\nSource-Commit: ", 1)[0]
    assert code == EXAMPLE_CODE
    assert SHORTCODE in code and PERCENT_SHORTCODE in code


def test_end_to_end_every_chunk_still_ends_with_its_source_commit_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunks = _build(tmp_path, monkeypatch, furnished=True)
    assert chunks
    for chunk in chunks:
        last_line = chunk["text"].split("\n")[-1]
        assert last_line == f"Source-Commit: {chunk['provenance']['commit']}"
        assert chunk["text"].count("Source-Commit: ") == 1


def test_end_to_end_the_cap_never_cuts_the_source_commit_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    long_doc = "\n".join(f"{'q' * 90} {i}" for i in range(500))
    fixture = json.loads(_api_surface(tmp_path).read_text(encoding="utf-8"))
    fixture["types"][0]["doc"] = long_doc
    (tmp_path / "api_surface.json").write_text(json.dumps(fixture), encoding="utf-8")
    _run_main(
        [
            "--api-surface",
            str(tmp_path / "api_surface.json"),
            "--title",
            "Long fixture",
            "--out",
            str(tmp_path / "long.json"),
        ]
    )
    chunks = json.loads((tmp_path / "long.json").read_text(encoding="utf-8"))["chunks"]
    texts = [chunk["text"] for chunk in chunks]
    assert any("Truncated: " in text for text in texts)
    for chunk in chunks:
        lines = chunk["text"].split("\n")
        assert lines[-1] == f"Source-Commit: {chunk['provenance']['commit']}"
        assert len(chunk["text"]) <= LIMIT + 200


def test_end_to_end_clean_input_chunks_are_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The committed pdf_net fixture holds nothing to sanitise, so every chunk's text must equal
    # the unsanitised chunk text with only the Source-Commit line appended.
    from foss_mcp.indexing.chunk_builder import build_chunks_from_api_surface

    fixture = json.loads(EXAMPLE_API_SURFACE.read_text(encoding="utf-8"))
    expected = build_chunks_from_api_surface(fixture, title="Unchanged fixture", max_types=300)
    assert expected
    for chunk in expected:
        assert _sanitize(chunk.text) == chunk.text, "fixture must hold nothing to sanitise"
    _run_main(
        [
            "--api-surface",
            str(EXAMPLE_API_SURFACE),
            "--title",
            "Unchanged fixture",
            "--out",
            str(tmp_path / "clean.json"),
        ]
    )
    written = json.loads((tmp_path / "clean.json").read_text(encoding="utf-8"))["chunks"]
    assert [chunk["text"] for chunk in written] == [
        f"{chunk.text}\nSource-Commit: {chunk.provenance.commit}" for chunk in expected
    ]
