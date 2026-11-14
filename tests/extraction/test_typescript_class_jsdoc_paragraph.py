"""G3/TC-821 (REQ-G3-012, RD2-220): a TypeScript class/interface keeps the first PARAGRAPH of its
JSDoc, not only its first sentence.

REPRODUCED directly against the real production extractor and the real pdf/typescript source at its
pinned commit (``155bfc7a``): the class docs were reduced to their first sentence, which for many
types is a bare title fragment and drops the sentence that says what the type is for or what its
constraint is - e.g. ``FloatBoxOptions`` served "Options for {@link Document.NewFloatingBox}." and
lost "All lengths are in points."; ``RedactAnnotation`` lost "Marking removes nothing"; and a
mid-sentence ``(e.g.`` was served as the end of the doc.

Every test calls the real ``api_surface.extract_api_surface`` entrypoint on a hand-authored
TypeScript source - never a private helper and never a committed fixture - so the fix is exercised
through the real code path.
"""

from __future__ import annotations

from pathlib import Path

from tree_sitter_language_pack import get_parser

from foss_mcp.extraction.tree_sitter_engine import api_surface


def _extract(tmp_path: Path, source: str) -> dict[str, dict]:
    """The extracted types of one TypeScript source file, by simple name."""
    root = tmp_path / "tree"
    root.mkdir(parents=True)
    # bytes, so the source keeps plain "\n" line endings on every platform
    (root / "index.ts").write_bytes(source.encode("utf-8"))
    types, *_ = api_surface.extract_api_surface(get_parser("typescript"), "typescript", root, root, "x")
    return {t["name"]: t for t in types}


SOURCE = """
/**
 * A flow layout container. Create via the document. Queue content, then render it.
 * A fourth sentence that must not be kept.
 *
 * A second paragraph that must never leak into the summary.
 *
 * @example
 * const flow = new Flow();
 */
export class Flow {
  /**
   * Adds a paragraph. It is queued until render is called.
   * @param text the text to add
   * @returns nothing
   */
  AddParagraph(text: string): void {}

  /** Single member sentence. */
  Render(): void {}
}

/**
 * Options for the flow.
 * All lengths are in points.
 * Another line of the same paragraph.
 */
export interface FlowOptions {
  /** A member doc. With a second sentence. */
  width: number;
}

/** Just one sentence. */
export class Single {}

/**
 * A single sentence spread over
 * two lines.
 *
 * Second paragraph.
 * @param x ignored
 */
export class SingleWrapped {}

/** Title without a terminator */
export class NoTerminator {}

/**
 * Options for the thing: callbacks that fetch the response for each signer, plus any extra
 * certificates to embed (e.g. a trust-anchor root not carried in the CMS).
 */
export interface Callbacks {
  fetch(): void;
}

/**
 * The first sentence is short.
 * LONGSECOND_PLACEHOLDER
 * Third sentence.
 */
export class Long {}

export class Undocumented {}

/** Abstract base. Subclasses supply the body. */
export abstract class AbstractBase {
  abstract run(): void;
}

/** An enum title. Its second sentence. */
export enum Color {
  Red,
}

/** A type alias title. Its second sentence. */
export type Alias = { a: number };
""".replace("LONGSECOND_PLACEHOLDER", "Second sentence " + "x" * 400 + ".")


def test_class_doc_keeps_up_to_three_sentences_of_the_first_paragraph(tmp_path: Path) -> None:
    types = _extract(tmp_path, SOURCE)
    assert types["Flow"]["doc"] == (
        "A flow layout container. Create via the document. Queue content, then render it."
    )


def test_class_doc_never_leaks_the_second_paragraph_or_tags(tmp_path: Path) -> None:
    doc = _extract(tmp_path, SOURCE)["Flow"]["doc"]
    assert "fourth sentence" not in doc
    assert "second paragraph" not in doc
    assert "@example" not in doc
    assert "new Flow" not in doc


def test_interface_doc_keeps_the_first_paragraph_over_several_lines(tmp_path: Path) -> None:
    types = _extract(tmp_path, SOURCE)
    assert types["FlowOptions"]["doc"] == (
        "Options for the flow. All lengths are in points. Another line of the same paragraph."
    )


def test_abstract_class_doc_keeps_its_second_sentence(tmp_path: Path) -> None:
    types = _extract(tmp_path, SOURCE)
    assert types["AbstractBase"]["doc"] == "Abstract base. Subclasses supply the body."


def test_single_sentence_class_doc_is_unchanged(tmp_path: Path) -> None:
    types = _extract(tmp_path, SOURCE)
    assert types["Single"]["doc"] == "Just one sentence."


def test_single_sentence_wrapped_over_two_lines_is_unchanged(tmp_path: Path) -> None:
    # The first paragraph is ONE sentence (the second paragraph and the tag are not part of it).
    types = _extract(tmp_path, SOURCE)
    assert types["SingleWrapped"]["doc"] == "A single sentence spread over two lines."


def test_single_sentence_docs_are_byte_identical_to_the_first_sentence_summary(tmp_path: Path) -> None:
    # The new paragraph logic must hand back exactly what _extract_doc_comment produced whenever the
    # first paragraph is a single sentence.
    root = tmp_path / "tree"
    root.mkdir()
    source = "/** Just one sentence. */\nexport class Single {}\n/**\n * Wrapped one\n * sentence.\n */\nexport class W {}\n"
    (root / "index.ts").write_bytes(source.encode("utf-8"))
    parser = get_parser("typescript")
    tree = parser.parse(source.encode("utf-8"))
    for node in tree.root_node.children:
        if node.type != "export_statement":
            continue
        cls = node.named_children[-1]
        old = api_surface._extract_doc_comment(cls, "typescript")
        assert api_surface._ts_class_doc(cls, old) == old


def test_a_title_without_terminator_keeps_the_existing_summary(tmp_path: Path) -> None:
    # No sentence terminator in the first paragraph: not a multi-sentence paragraph, so the
    # pre-existing summary is returned untouched.
    types = _extract(tmp_path, SOURCE)
    assert types["NoTerminator"]["doc"] == "Title without a terminator"


def test_an_abbreviation_inside_one_sentence_is_not_cut_off(tmp_path: Path) -> None:
    doc = _extract(tmp_path, SOURCE)["Callbacks"]["doc"]
    assert doc.endswith("(e.g. a trust-anchor root not carried in the CMS).")
    assert doc.startswith("Options for the thing:")


def test_the_paragraph_is_capped_at_a_sentence_boundary_within_400_chars(tmp_path: Path) -> None:
    doc = _extract(tmp_path, SOURCE)["Long"]["doc"]
    # the 400+ character second sentence is dropped whole, never cut mid-sentence; the third sentence
    # is dropped with it (sentences are kept in order, never skipped over)
    assert doc == "The first sentence is short."
    assert len(doc) <= 400


def test_a_long_first_sentence_is_still_kept_whole(tmp_path: Path) -> None:
    source = "/**\n * " + "word " * 120 + "end. Second.\n */\nexport class BigFirst {}\n"
    doc = _extract(tmp_path, source)["BigFirst"]["doc"]
    assert doc.endswith("end.")
    assert "Second" not in doc


def test_undocumented_class_has_an_empty_doc(tmp_path: Path) -> None:
    assert _extract(tmp_path, SOURCE)["Undocumented"]["doc"] == ""


def test_member_docs_are_untouched(tmp_path: Path) -> None:
    types = _extract(tmp_path, SOURCE)
    methods = {m["name"]: m for m in types["Flow"]["methods"]}
    assert methods["AddParagraph"]["doc"] == "Adds a paragraph."
    assert methods["Render"]["doc"] == "Single member sentence."
    props = {p["name"]: p for p in types["FlowOptions"]["properties"]}
    assert props["width"]["doc"] == "A member doc."


def test_enum_and_type_alias_docs_are_untouched(tmp_path: Path) -> None:
    types = _extract(tmp_path, SOURCE)
    assert types["Color"]["doc"] == "An enum title."
    assert types["Alias"]["doc"] == "A type alias title."


def test_class_doc_helper_stays_on_the_typescript_path_only() -> None:
    # JavaScript and every other language keep the first-sentence summary: the paragraph logic is
    # reached only for language == "typescript" class/interface nodes.
    assert api_surface._TS_CLASS_DOC_NODE_TYPES == frozenset(
        {"class_declaration", "abstract_class_declaration", "interface_declaration"}
    )


def test_javascript_class_doc_is_unchanged(tmp_path: Path) -> None:
    root = tmp_path / "tree"
    root.mkdir()
    source = "/**\n * Title sentence. Second sentence.\n */\nexport class Foo {}\n"
    (root / "index.js").write_bytes(source.encode("utf-8"))
    types, *_ = api_surface.extract_api_surface(get_parser("javascript"), "javascript", root, root, "x")
    assert {t["name"]: t for t in types}["Foo"]["doc"] == "Title sentence."
