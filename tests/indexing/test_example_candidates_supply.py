"""Offline tests for TC-458 (REQ-G3-007): the candidate supply of ``example_candidates``.

Legacy extraction (``extract_candidate_examples(page)``) is unchanged: the first fence of each
example block, unfiltered. Platform mode (``extract_candidate_supply(page, platform)``) reads every
fence of every example block and of the README content blocks, keeps the pilot's own language,
de-duplicates by a normalised sha256, marks example-starting, continuation and import-only blocks and
gives a continuation the import lines of its chain. Nothing here uses a network, a toolchain or the
clock; the pages are the real committed furnished pages of every pilot in the Helm values.
"""

from __future__ import annotations

import dataclasses
import hashlib
import inspect
import re
from pathlib import Path

import pytest
import yaml

from foss_mcp.indexing import example_verifier
from foss_mcp.indexing.example_candidates import (
    CHAIN_PREFIX_PLATFORMS,
    IMPORT_ONLY_GUARD_PLATFORMS,
    PLATFORM_FENCE_TAGS,
    CandidateExample,
    CandidateSupply,
    effective_candidate,
    extract_candidate_examples,
    extract_candidate_supply,
    import_like_lines,
    normalised_code_sha256,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FURNISHED = REPO_ROOT / "tests/fixtures/furnished"
VALUES = REPO_ROOT / "infra/helm/foss-mcp/values.yaml"

_FENCE_RE = re.compile(r"```[ \t]*([^\n`]*)\n(.*?)```", re.DOTALL)

# One fence tag that every platform accepts, for synthetic pages.
TAG = {
    "dotnet": "csharp",
    "python": "python",
    "rust": "rust",
    "go": "go",
    "java": "java",
    "typescript": "typescript",
    "nodejs": "javascript",
    "cpp": "cpp",
}


def _pilots() -> list[tuple[str, str]]:
    """(furnished directory name, library.platform) of every pilot in the Helm values."""
    values = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
    pilots = []
    for pilot in values["ingestion"]["pilots"]:
        page = pilot.get("furnishedPage")
        if not page:
            continue
        name = page.split("/furnished/")[1].split("/")[0]
        pilots.append((name, pilot["library"]["platform"]))
    return pilots


PILOTS = _pilots()


def _load_page(name: str) -> dict:
    text = (FURNISHED / name / "pages" / "_index.md").read_text(encoding="utf-8")
    parsed = yaml.safe_load(text.split("---", 2)[1])
    assert isinstance(parsed, dict)
    return parsed


def _tag_of(info: str) -> str:
    match = re.match(r"[a-z0-9_#+.-]+", info.strip().lower().lstrip("{").lstrip().lstrip("."))
    return match.group(0) if match else ""


def _single_page(*blocks: tuple[str, str]) -> dict:
    return {"single": {"block": [{"title": title, "content": content} for title, content in blocks]}}


def _fenced(platform: str, *codes: str) -> str:
    return "\n\n".join(f"```{TAG[platform]}\n{code}\n```" for code in codes)


def _candidates(platform: str, *codes: str) -> tuple[CandidateExample, ...]:
    """One example block per code, in order, fenced with the platform's own tag."""
    page = _single_page(*((f"Block {n}", _fenced(platform, code)) for n, code in enumerate(codes)))
    supply = extract_candidate_supply(page, platform)
    assert not supply.fallback
    return supply.candidates


def _one(platform: str, code: str) -> CandidateExample:
    (candidate,) = _candidates(platform, code)
    return candidate


# --- the hash rule ---------------------------------------------------------------------------------


def test_normalised_hash_is_sha256_of_the_normalised_utf8_text() -> None:
    assert normalised_code_sha256("a\nb") == hashlib.sha256(b"a\nb").hexdigest()
    assert normalised_code_sha256("") == hashlib.sha256(b"").hexdigest()


def test_normalised_hash_ignores_crlf_cr_trailing_spaces_and_surrounding_blank_lines() -> None:
    plain = normalised_code_sha256("a = 1\nb = 2")
    assert normalised_code_sha256("\r\n\na = 1  \r\nb = 2\t\r\n\r\n") == plain
    assert normalised_code_sha256("a = 1\rb = 2\r") == plain
    assert normalised_code_sha256("\n\n\na = 1\nb = 2\n\n\n") == plain


def test_normalised_hash_changes_with_a_changed_statement_or_inner_blank_line() -> None:
    assert normalised_code_sha256("a = 1\nb = 2") != normalised_code_sha256("a = 1\nb = 3")
    assert normalised_code_sha256("a = 1\nb = 2") != normalised_code_sha256("a = 1\n\nb = 2")
    assert normalised_code_sha256("a = 1\nb = 2") != normalised_code_sha256("  a = 1\nb = 2")


# --- legacy mode, on every committed pilot page -----------------------------------------------------


def test_the_pilot_list_comes_from_the_helm_values() -> None:
    # re-pinned by TC-551: 40 -> 32
    assert len(PILOTS) >= 32
    for name, platform in PILOTS:
        assert (FURNISHED / name / "pages" / "_index.md").is_file()
        assert platform in PLATFORM_FENCE_TAGS


@pytest.mark.parametrize(("name", "platform"), PILOTS)
def test_legacy_extraction_is_the_first_fence_of_each_example_block(name: str, platform: str) -> None:
    page = _load_page(name)
    expected = []
    for block in page["single"]["block"]:
        match = _FENCE_RE.search(block["content"])
        if match is None:
            continue
        expected.append(
            (
                block["title"],
                block["content"][: match.start()].strip(),
                match.group(1).strip(),
                match.group(2).strip(),
            )
        )
    legacy = extract_candidate_examples(page)
    assert [(c.title, c.description, c.language, c.code) for c in legacy] == expected
    page_code = tuple(code for _, _, _, code in expected)
    for candidate in legacy:
        assert candidate.code_sha256 == normalised_code_sha256(candidate.code)
        assert candidate.page_code == page_code
        assert candidate.source == "single"
        assert not candidate.starts_example
        assert not candidate.imports_only
        assert candidate.import_prefix == ()


# --- platform mode, on every committed pilot page ---------------------------------------------------


@pytest.mark.parametrize(("name", "platform"), PILOTS)
def test_platform_mode_supply_is_clean_unique_and_keeps_every_legacy_candidate(
    name: str, platform: str
) -> None:
    page = _load_page(name)
    supply = extract_candidate_supply(page, platform)
    assert not supply.fallback
    candidates = supply.candidates
    accepted = PLATFORM_FENCE_TAGS[platform]

    assert all(_tag_of(c.language) in accepted for c in candidates)
    assert len({c.title for c in candidates}) == len(candidates)
    assert len({c.code_sha256 for c in candidates}) == len(candidates)
    page_code = tuple(c.code for c in candidates)
    for candidate in candidates:
        assert candidate.code_sha256 == normalised_code_sha256(candidate.code)
        assert candidate.page_code == page_code
        assert candidate.source in ("single", "content")

    assert supply.from_single_blocks + supply.from_content_blocks == len(candidates)
    assert supply.from_single_blocks == sum(1 for c in candidates if c.source == "single")
    assert supply.imports_only == sum(1 for c in candidates if c.imports_only)

    legacy_accepted = [c for c in extract_candidate_examples(page) if _tag_of(c.language) in accepted]
    singles = [c for c in candidates if c.source == "single"]
    assert [(c.title, c.description, c.language, c.code) for c in singles] == [
        (c.title, c.description, c.language, c.code) for c in legacy_accepted
    ]
    # Single candidates always come before content candidates.
    assert [c.source for c in candidates] == sorted(
        (c.source for c in candidates), key=["single", "content"].index
    )

    assert extract_candidate_examples(page, platform=platform) == list(candidates)


@pytest.mark.parametrize(("name", "platform"), PILOTS)
def test_a_starter_and_the_first_candidate_of_each_source_carry_no_prefix(name: str, platform: str) -> None:
    candidates = extract_candidate_supply(_load_page(name), platform).candidates
    for candidate in candidates:
        if candidate.starts_example:
            assert candidate.import_prefix == ()
    first_of_each_chain = {}
    for candidate in candidates:
        first_of_each_chain.setdefault(candidate.source, candidate)
    for first in first_of_each_chain.values():
        assert first.import_prefix == ()


# --- the real pages named by the card -----------------------------------------------------------------


def test_pdf_net_gains_exactly_one_content_candidate_and_no_bash() -> None:
    supply = extract_candidate_supply(_load_page("pdf_net"), "dotnet")
    content = [c for c in supply.candidates if c.source == "content"]
    assert len(content) == 1
    assert content[0].code.startswith("using Aspose.Pdf;")
    assert supply.from_content_blocks == 1
    assert all(_tag_of(c.language) != "bash" for c in supply.candidates)
    assert supply.other_language_dropped >= 2


def test_3d_java_has_no_kotlin_candidate() -> None:
    page = _load_page("3d_java")
    assert any(c.language == "kotlin" for c in extract_candidate_examples(page))
    supply = extract_candidate_supply(page, "java")
    assert all(c.language != "kotlin" for c in supply.candidates)
    assert supply.other_language_dropped >= 1


def test_slides_net_examples_block_is_import_only_and_its_usings_prefix_the_continuations() -> None:
    candidates = extract_candidate_supply(_load_page("slides_net"), "dotnet").candidates
    assert candidates[1].title == "Examples"
    assert candidates[1].imports_only
    assert candidates[1].starts_example
    units = import_like_lines(candidates[1].code, "dotnet")
    assert len(units) == 3
    assert all(unit.startswith("using Aspose.Slides.Foss") for unit in units)
    for candidate in candidates[2:9]:
        assert not candidate.starts_example
        assert candidate.import_prefix == units


def test_python_second_candidate_takes_the_import_of_the_quick_start() -> None:
    # re-pinned by TC-551: the page shape (a quick start with the page's only import, then a block that has none) was
    # read from a removed pilot's page; it is now built inline with a neutral library name.
    page = _single_page(
        (
            "Quick Start",
            _fenced(
                "python",
                "from sample_pkg import SampleClient, SampleOptions\n\n"
                'options = SampleOptions(session_url="https://sample.example.test/session", username="user")\n'
                "with SampleClient(options) as client:\n    client.connect()",
            ),
        ),
        (
            "Bearer token authentication",
            _fenced(
                "python",
                'options = SampleOptions(session_url="https://sample.example.test/session", bearer_token="t")',
            ),
        ),
    )
    candidates = extract_candidate_supply(page, "python").candidates
    assert candidates[0].starts_example
    assert not candidates[1].starts_example
    assert candidates[1].import_prefix == ("from sample_pkg import SampleClient, SampleOptions",)


def test_cells_go_first_candidate_is_import_only() -> None:
    candidates = extract_candidate_supply(_load_page("cells_go"), "go").candidates
    assert candidates[0].imports_only
    assert not any(c.imports_only for c in candidates[1:])


def test_pdf_cpp_installation_block_is_import_only() -> None:
    supply = extract_candidate_supply(_load_page("pdf_cpp"), "cpp")
    assert supply.candidates[0].imports_only
    assert supply.candidates[0].code.strip().startswith("#include <aspose.pdf.foss.hpp>")
    assert supply.imports_only == 1


def test_pdf_net_with_a_foreign_platform_falls_back_to_the_legacy_list() -> None:
    page = _load_page("pdf_net")
    supply = extract_candidate_supply(page, "rust")
    legacy = extract_candidate_examples(page)
    assert supply.fallback
    assert list(supply.candidates) == legacy
    assert [c.page_code for c in supply.candidates] == [c.page_code for c in legacy]
    assert [c.code_sha256 for c in supply.candidates] == [c.code_sha256 for c in legacy]
    assert supply.from_single_blocks == len(legacy)
    assert supply.from_content_blocks == supply.duplicates_dropped == supply.other_language_dropped == 0
    assert supply.imports_only == 0


# --- synthetic: counting, parts, content blocks, duplicates, titles -----------------------------------


def test_one_block_python_bash_python_gives_the_title_and_a_part_two() -> None:
    page = _single_page(
        (
            "T",
            "Intro prose.\n\n```python\nprint(1)\n```\n\nInstall it.\n\n```bash\npip install x\n```\n\n"
            "Then continue.\n\n```python\nprint(2)\n```\n",
        )
    )
    supply = extract_candidate_supply(page, "python")
    assert [c.title for c in supply.candidates] == ["T", "T (part 2)"]
    assert [c.description for c in supply.candidates] == ["Intro prose.", "Then continue."]
    assert [c.code for c in supply.candidates] == ["print(1)", "print(2)"]
    assert [c.language for c in supply.candidates] == ["python", "python"]
    assert supply.other_language_dropped == 1


def test_counts_for_a_page_with_duplicates_content_blocks_and_foreign_fences() -> None:
    page = _single_page(
        ("Alpha", "```python\nprint(1)\n```\n\n```bash\nls\n```"),
        ("Beta", "```python\nprint(1)\n```"),
        ("Gamma", "```\nnothing tagged\n```"),
        ("Delta", "No code here."),
    )
    page["content"] = {
        "block": [
            {
                "title_left": "Left",
                "content_left": "```py\nprint(3)\n```",
                "title_right": "Right",
                "content_right": "```python\nprint(1)\n```\n\n```cmake\nproject(x)\n```",
            },
            {"title_left": "Only left", "content_left": "```python\nprint(4)\n```"},
        ]
    }
    supply = extract_candidate_supply(page, "python")
    assert isinstance(supply, CandidateSupply)
    assert [c.title for c in supply.candidates] == ["Alpha", "Left", "Only left"]
    assert [c.source for c in supply.candidates] == ["single", "content", "content"]
    assert supply.from_single_blocks == 1
    assert supply.from_content_blocks == 2
    assert supply.duplicates_dropped == 2
    assert supply.other_language_dropped == 3  # bash, the untagged fence, cmake
    assert supply.imports_only == 0
    assert not supply.fallback


def test_crlf_and_trailing_space_variants_deduplicate_and_are_counted() -> None:
    page = _single_page(
        ("One", "```python\na = 1\nb = 2\n```"),
        ("Two", "```python\r\na = 1  \r\nb = 2\r\n```"),
        ("Three", "```python\n\na = 1\nb = 2   \n\n```"),
        ("Four", "```python\na = 1\nb = 3\n```"),
    )
    supply = extract_candidate_supply(page, "python")
    assert [c.title for c in supply.candidates] == ["One", "Four"]
    assert supply.duplicates_dropped == 2


def test_a_content_fence_equal_to_an_example_block_is_dropped() -> None:
    page = _single_page(("Quick Start", "```rust\nfn main() {}\n```"))
    page["content"] = {
        "block": [
            {
                "title_left": "Installation and Quick Start",
                "content_left": "```bash\ncargo add x\n```\n\n```rust\nfn main() {}\n```",
                "title_right": "Capabilities",
                "content_right": "```rs\nlet x = 1;\n```",
            }
        ]
    }
    supply = extract_candidate_supply(page, "rust")
    assert [(c.title, c.source) for c in supply.candidates] == [
        ("Quick Start", "single"),
        ("Capabilities", "content"),
    ]
    assert supply.duplicates_dropped == 1
    assert supply.other_language_dropped == 1


def test_content_blocks_are_read_left_then_right_with_their_own_titles_and_parts() -> None:
    page = {
        "content": {
            "block": [
                {
                    "title_left": "L",
                    "content_left": "```go\nx := 1\n```\n\nmore\n\n```go\ny := 2\n```",
                    "title_right": "R",
                    "content_right": "```go\nz := 3\n```",
                },
                {"title_left": "Next", "content_left": "```golang\nw := 4\n```"},
                "not a mapping",
            ]
        }
    }
    supply = extract_candidate_supply(page, "go")
    assert [c.title for c in supply.candidates] == ["L", "L (part 2)", "R", "Next"]
    assert supply.candidates[1].description == "more"
    assert all(c.source == "content" for c in supply.candidates)
    assert supply.from_content_blocks == 4


def test_untagged_fences_match_no_platform_and_a_page_of_them_falls_back() -> None:
    page = _single_page(("Only", "```\nprint(1)\n```"), ("Other", "```bash\nls\n```"))
    supply = extract_candidate_supply(page, "python")
    assert supply.fallback
    assert [c.code for c in supply.candidates] == ["print(1)", "ls"]
    assert supply.candidates == tuple(extract_candidate_examples(page))


def test_a_page_with_no_fence_at_all_is_an_empty_fallback() -> None:
    supply = extract_candidate_supply({"single": {"block": [{"title": "T", "content": "prose"}]}}, "go")
    assert supply.fallback
    assert supply.candidates == ()
    assert extract_candidate_supply({}, "go").fallback


def test_an_unknown_platform_raises_value_error_everywhere() -> None:
    page = _single_page(("T", "```python\nprint(1)\n```"))
    with pytest.raises(ValueError, match="cobol"):
        extract_candidate_supply(page, "cobol")
    with pytest.raises(ValueError, match="cobol"):
        extract_candidate_examples(page, platform="cobol")
    with pytest.raises(ValueError, match="cobol"):
        import_like_lines("x", "cobol")


@pytest.mark.parametrize(
    ("info", "platform"),
    [
        ("C#", "dotnet"),
        ("csharp", "dotnet"),
        ("CS", "dotnet"),
        ("{.python}", "python"),
        ("{ .python }", "python"),
        (".py", "python"),
        ("Python3", "python"),
        ("ts title=x", "typescript"),
        ("typescript", "typescript"),
        ("JavaScript", "nodejs"),
        ("mjs", "nodejs"),
        ("c++", "cpp"),
        ("CXX", "cpp"),
        ("rs", "rust"),
        ("golang", "go"),
        ("java", "java"),
    ],
)
def test_fence_tags_are_normalised_before_matching(info: str, platform: str) -> None:
    page = _single_page(("T", f"```{info}\nx\n```"))
    supply = extract_candidate_supply(page, platform)
    assert not supply.fallback
    assert [c.code for c in supply.candidates] == ["x"]
    assert supply.candidates[0].language == info.strip()


@pytest.mark.parametrize(
    ("info", "platform"),
    [
        ("csharp", "java"),
        ("python", "nodejs"),
        ("javascript", "typescript"),
        ("c", "cpp"),
        ("kotlin", "java"),
    ],
)
def test_a_tag_of_another_platform_is_not_accepted(info: str, platform: str) -> None:
    page = _single_page(("T", f"```{info}\nx\n```\n\n```{TAG[platform]}\ny\n```"))
    supply = extract_candidate_supply(page, platform)
    assert [c.code for c in supply.candidates] == ["y"]
    assert supply.other_language_dropped == 1


def test_a_title_already_used_gets_the_smallest_free_suffix() -> None:
    page = _single_page(
        ("Same", "```python\na()\n```"),
        ("Same", "```python\nb()\n```"),
        ("Same", "```python\nc()\n```"),
        ("Same (2)", "```python\nd()\n```"),
    )
    titles = [c.title for c in extract_candidate_supply(page, "python").candidates]
    assert titles == ["Same", "Same (2)", "Same (3)", "Same (2) (2)"]


def test_a_part_title_that_is_already_taken_is_renamed_too() -> None:
    page = _single_page(
        ("T", "```python\na()\n```\n\n```python\nb()\n```"),
        ("T (part 2)", "```python\nc()\n```"),
    )
    titles = [c.title for c in extract_candidate_supply(page, "python").candidates]
    assert titles == ["T", "T (part 2)", "T (part 2) (2)"]


def test_candidates_compare_equal_whatever_their_new_fields_hold() -> None:
    plain = CandidateExample("t", "d", "python", "x")
    marked = CandidateExample(
        "t",
        "d",
        "python",
        "x",
        page_code=("x", "y"),
        code_sha256="abc",
        source="content",
        starts_example=True,
        imports_only=True,
        import_prefix=("import os",),
    )
    assert plain == marked
    assert plain.source == "single"
    assert plain.code_sha256 == ""
    assert plain.starts_example is False
    assert plain.import_prefix == ()
    assert dataclasses.replace(plain, import_prefix=("a",)) == plain


# --- import_like_lines -----------------------------------------------------------------------------------


def test_python_import_units_including_multi_line_and_never_future_or_indented() -> None:
    code = (
        "import os\n"
        "import numpy as np, sys\n"
        "from a.b import (\n    c,\n    d,\n)\n"
        "from __future__ import annotations\n"
        "from . import sibling\n"
        "import x, \\\n    y\n"
        "print(os.name)\n"
        "    import hidden\n"
    )
    assert import_like_lines(code, "python") == (
        "import os",
        "import numpy as np, sys",
        "from a.b import (\n    c,\n    d,\n)",
        "from . import sibling",
        "import x, \\\n    y",
    )


def test_dotnet_import_units_never_include_using_declarations_or_statements() -> None:
    code = (
        "using System;\n"
        "global using System.Linq;\n"
        "using static System.Math;\n"
        "using Json = System.Text.Json; // alias\n"
        "using var stream = new MemoryStream();\n"
        "using (var other = new MemoryStream())\n{\n}\n"
        "    using Indented;\n"
    )
    assert import_like_lines(code, "dotnet") == (
        "using System;",
        "global using System.Linq;",
        "using static System.Math;",
        "using Json = System.Text.Json; // alias",
    )


def test_rust_import_units_include_multi_line_use_and_extern_crate_but_not_nested_use() -> None:
    code = (
        "use std::io;\n"
        "use a::{\n    B,\n    C,\n};\n"
        "extern crate serde;\n"
        "pub use x::y;\n"
        "fn main() {\n    use nested::thing;\n}\n"
    )
    assert import_like_lines(code, "rust") == (
        "use std::io;",
        "use a::{\n    B,\n    C,\n};",
        "extern crate serde;",
        "pub use x::y;",
    )


def test_go_import_units_cover_single_aliased_and_grouped_imports() -> None:
    code = (
        'package main\n\nimport "fmt"\nimport f "os"\n'
        'import (\n\t"strings"\n\t// a comment\n\tm "math"\n\t_ "embed"\n)\n\nfunc main() {}\n'
    )
    assert import_like_lines(code, "go") == (
        'import "fmt"',
        'import f "os"',
        'import "strings"',
        'import m "math"',
        'import _ "embed"',
    )


def test_java_import_units_skip_the_package_line() -> None:
    code = "package com.x;\nimport java.util.List;\nimport static java.lang.Math.max;\nimport a.b.*;\nclass A {}\n"
    assert import_like_lines(code, "java") == (
        "import java.util.List;",
        "import static java.lang.Math.max;",
        "import a.b.*;",
    )


@pytest.mark.parametrize("platform", ["typescript", "nodejs"])
def test_typescript_and_javascript_import_units_include_multi_line_and_require(platform: str) -> None:
    code = (
        "import { A } from 'a';\n"
        'import {\n  B,\n  C,\n} from "b";\n'
        "import 'side-effect';\n"
        "import * as ns from 'ns'\n"
        "const fs = require('fs');\n"
        "let late = await import('dynamic');\n"
        "console.log(A);\n"
    )
    assert import_like_lines(code, platform) == (
        "import { A } from 'a';",
        'import {\n  B,\n  C,\n} from "b";',
        "import 'side-effect';",
        "import * as ns from 'ns'",
        "const fs = require('fs');",
    )


def test_cpp_import_units_are_includes_and_using_directives_at_the_top_level() -> None:
    code = (
        "#pragma once\n#include <vector>\n"
        '#include "my.h"  // mine\n'
        "using namespace std;\nusing std::string;\nusing Alias = int;\n"
        "int main() {\n  using namespace inner;\n}\n"
    )
    assert import_like_lines(code, "cpp") == (
        "#include <vector>",
        '#include "my.h"  // mine',
        "using namespace std;",
        "using std::string;",
    )


# --- starts_example and imports_only ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("platform", "code", "starts", "only"),
    [
        ("python", "# setup\nimport os\nprint(os.name)", True, False),
        ("python", "import os\nfrom a import b", True, True),
        ("python", "print(1)\nimport os", False, False),
        ("python", "from __future__ import annotations\nimport os", False, False),
        ("python", "# only a comment", False, False),
        ("dotnet", "// c\nusing System;\nConsole.WriteLine(1);", True, False),
        ("dotnet", "/* block\ncomment */\nusing System;\nusing Foo;", True, True),
        ("dotnet", "var x = 1;\nusing System;", False, False),
        ("rust", "// c\nuse a::b;", True, True),
        ("rust", "fn main() {}", False, False),
        ("go", 'package main\n\nimport "fmt"\n', True, True),
        ("go", 'package main\n\nimport "fmt"\n\nfunc main() { fmt.Println() }', True, False),
        ("go", "func main() {}", False, False),
        ("java", "package a;\nimport b.C;", True, True),
        ("java", "import b.C;\nclass X {}", True, False),
        ("java", "class X {}", False, False),
        ("typescript", "import a from 'a';\nconsole.log(a);", True, False),
        ("typescript", "import { a } from 'a';\nimport 'b';", True, True),
        ("nodejs", "// c\nimport { a } from 'a';", True, True),
        ("cpp", "#pragma once\n/* block\ncomment */\n#include <a.hpp>\n", True, True),
        ("cpp", "#ifndef X\n#define X\n#include <a.hpp>\n#endif", True, True),
        ("cpp", "#include <a.hpp>\nint main() { return 0; }", True, False),
        ("cpp", "int main() { return 0; }", False, False),
    ],
)
def test_starts_example_and_imports_only_per_platform(
    platform: str, code: str, starts: bool, only: bool
) -> None:
    candidate = _one(platform, code)
    assert candidate.starts_example is starts
    assert candidate.imports_only is only


# --- chains ----------------------------------------------------------------------------------------------


def test_a_continuation_takes_the_units_of_its_nearest_earlier_starter_and_those_between() -> None:
    a, b, c, d, e = _candidates(
        "python",
        "import os\nprint(os.name)",
        "print(1)",
        "import sys\nprint(sys.argv)",
        "print(2)",
        "x = 1\nimport json",
    )
    assert [a.starts_example, b.starts_example, c.starts_example, d.starts_example, e.starts_example] == [
        True,
        False,
        True,
        False,
        False,
    ]
    assert (a.import_prefix, b.import_prefix, c.import_prefix) == ((), ("import os",), ())
    assert d.import_prefix == ("import sys",)
    assert e.import_prefix == ("import sys",)


def test_the_candidates_between_the_starter_and_the_continuation_contribute_their_units() -> None:
    a, b, c = _candidates("python", "import os\nprint(os.name)", "x = 1\nimport json", "print(2)")
    assert b.import_prefix == ("import os",)
    assert c.import_prefix == ("import os", "import json")


def test_with_no_starter_every_earlier_candidate_contributes() -> None:
    a, b, c = _candidates("python", "x = 1\nimport os", "y = 2\nimport json", "print(3)")
    assert not any(candidate.starts_example for candidate in (a, b, c))
    assert a.import_prefix == ()
    assert b.import_prefix == ("import os",)
    assert c.import_prefix == ("import os", "import json")


def test_a_prefix_unit_whose_key_the_block_already_imports_is_skipped() -> None:
    _, own, other = _candidates(
        "python",
        "from m import a\nfrom n import z\nuse(a)",
        "x = 1\nfrom m import b\nuse(b)",
        "x = 1\nuse(z)",
    )
    assert own.import_prefix == ("from n import z",)
    assert other.import_prefix == ("from m import a", "from n import z")


def test_a_repeated_key_in_the_prefix_is_kept_once() -> None:
    _, second = _candidates("python", "import os\nimport os as other\nrun()", "run()")
    assert second.import_prefix == ("import os",)


def test_a_python_candidate_with_a_future_import_gets_no_prefix() -> None:
    _, future = _candidates("python", "import os\nrun()", "from __future__ import annotations\nrun()")
    assert not future.starts_example
    assert future.import_prefix == ()


def test_a_content_field_is_its_own_chain_and_a_part_follows_its_block() -> None:
    page = _single_page(
        ("Start", "```python\nimport os\nprint(os.name)\n```\n\n```python\nprint(1)\n```"),
        ("Next", "```python\nprint(2)\n```"),
    )
    page["content"] = {
        "block": [
            {
                "title_left": "Left",
                "content_left": "```python\nprint(3)\n```",
                "title_right": "Right",
                "content_right": "```python\nimport json\nprint(json)\n```\n\n```python\nprint(4)\n```",
            }
        ]
    }
    start, part, nxt, left, right, right_part = extract_candidate_supply(page, "python").candidates
    assert (start.title, part.title, nxt.title) == ("Start", "Start (part 2)", "Next")
    assert part.import_prefix == ("import os",)
    assert nxt.import_prefix == ("import os",)
    assert left.import_prefix == ()
    assert not left.starts_example
    assert right.import_prefix == ()
    assert right_part.import_prefix == ("import json",)


def test_the_go_import_block_units_are_the_prefix_of_a_go_continuation() -> None:
    _, second = _candidates(
        "go", 'package main\n\nimport (\n\t"fmt"\n\tm "math"\n)\n\nfunc main() {}', "func helper() {}"
    )
    assert second.import_prefix == ('import "fmt"', 'import m "math"')


# --- effective_candidate -----------------------------------------------------------------------------------


@pytest.mark.parametrize("platform", ["python", "typescript", "nodejs"])
def test_effective_candidate_prepends_the_prefix_where_no_verifier_borrows_imports(platform: str) -> None:
    candidate = dataclasses.replace(
        _one(platform, "run();"),
        import_prefix=("import a", "import b"),
    )
    effective = effective_candidate(candidate, platform)
    assert effective is not candidate
    assert effective.code == "import a\nimport b\nrun();"
    assert effective.code_sha256 == normalised_code_sha256("import a\nimport b\nrun();")
    assert effective.import_prefix == ()
    assert effective.title == candidate.title
    assert effective.page_code == candidate.page_code
    assert candidate.import_prefix == ("import a", "import b")
    assert candidate.code == "run();"


@pytest.mark.parametrize("platform", sorted(PLATFORM_FENCE_TAGS))
def test_effective_candidate_returns_the_same_object_when_nothing_applies(platform: str) -> None:
    plain = _one(platform, "run();")
    assert effective_candidate(plain, platform) is plain
    with_prefix = dataclasses.replace(plain, import_prefix=("import a",))
    if platform in CHAIN_PREFIX_PLATFORMS:
        assert effective_candidate(with_prefix, platform) is not with_prefix
    else:
        assert effective_candidate(with_prefix, platform) is with_prefix


def test_the_chain_platforms_and_the_import_only_guard_platforms() -> None:
    assert CHAIN_PREFIX_PLATFORMS == frozenset({"python", "typescript", "nodejs"})
    assert IMPORT_ONLY_GUARD_PLATFORMS == frozenset({"cpp", "java", "nodejs"})
    assert set(PLATFORM_FENCE_TAGS) == set(TAG)


def test_the_guard_platforms_are_exactly_those_whose_committed_verifier_does_not_reject_import_only() -> None:
    verifiers = {
        "dotnet": example_verifier.verify_dotnet_example,
        "python": example_verifier.verify_python_example,
        "rust": example_verifier.verify_rust_example,
        "go": example_verifier.verify_go_example,
        "java": example_verifier.verify_java_example,
        "typescript": example_verifier.verify_typescript_example,
        "nodejs": example_verifier.verify_nodejs_example,
        "cpp": example_verifier.verify_cpp_example_linux,
    }
    assert set(verifiers) == set(PLATFORM_FENCE_TAGS)
    rejecting = {
        platform
        for platform, function in verifiers.items()
        if "_NO_STATEMENTS_MESSAGE" in inspect.getsource(function)
    }
    assert rejecting == set(PLATFORM_FENCE_TAGS) - IMPORT_ONLY_GUARD_PLATFORMS
