"""TC-392: the extractor reports only the public API surface.

Two defects put non-API members into every tree-sitter pilot's surface:

1. ``_flatten_inheritance`` copied EVERY parent method into each subclass, constructors and
   destructors included. Constructors are not inherited in C#, Java, C++ or TypeScript, so a
   subclass listed its base's ``Base()`` / ``~Base()`` as "inherited" members.
2. ``_collect_source_files`` excluded test DIRECTORIES but not test FILES, so Go's
   ``*_test.go`` files (which live next to the code) were walked and their ``ExampleXxx``
   functions and test helpers appeared as API.

Everything here is offline: synthetic source trees under ``tmp_path`` parsed with the same
real tree-sitter parsers the sibling tests use.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tree_sitter_language_pack import get_parser

from foss_mcp.extraction.tree_sitter_engine import api_surface
from foss_mcp.extraction.tree_sitter_engine.api_surface import _is_constructor_like
from foss_mcp.extraction.tree_sitter_engine.tree_helpers import _collect_source_files, _is_test_source_file


def _write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _extract(language: str, package: Path, root: Path) -> list[dict]:
    types, *_ = api_surface.extract_api_surface(get_parser(language), language, package, root, "widget")
    return types


def _type(types: list[dict], name: str) -> dict:
    (match,) = (t for t in types if t["name"] == name)
    return match


def _method_names(entry: dict) -> list[str]:
    return [m["name"] for m in entry["methods"]]


def _all_names(types: list[dict]) -> set[str]:
    """Every type name and every member name in the surface, for absence assertions."""
    names: set[str] = set()
    for t in types:
        names.add(t["name"])
        names.update(m["name"] for m in t.get("methods", []))
        names.update(p["name"] for p in t.get("properties", []))
    return names


# ---------------------------------------------------------------------------
# Constructors and destructors are not inherited (per language, through the real extractor)
# ---------------------------------------------------------------------------

CSHARP = """
namespace P
{
    public class Base
    {
        public Base() { }
        public Base(int size) { }
        public void Save() { }
    }

    public class Derived : Base
    {
        public Derived() { }
        public void Load() { }
    }
}
"""

JAVA_BASE = """
package p;

public class Base {
    public Base() { }
    public Base(int size) { }
    public void save() { }
}
"""

JAVA_DERIVED = """
package p;

public class Derived extends Base {
    public Derived() { }
    public void load() { }
}
"""

CPP = """
namespace p {

class Base {
public:
    Base() {}
    virtual ~Base() {}
    void Save();
};

class Derived : public Base {
public:
    Derived() {}
    void Load();
};

}
"""

TYPESCRIPT = """
export class Base {
    constructor(size: number) { }
    save(): void { }
}

export class Derived extends Base {
    load(): void { }
}
"""


def test_csharp_subclass_inherits_the_method_but_not_the_base_constructor(tmp_path: Path) -> None:
    _write(tmp_path, "src/P/Widget.cs", CSHARP)
    types = _extract("csharp", tmp_path / "src" / "P", tmp_path)
    derived = _type(types, "Derived")
    names = _method_names(derived)

    save = next(m for m in derived["methods"] if m["name"] == "Save")
    assert save["inherited_from"].endswith("Base")
    assert "Base" not in names
    assert "Load" in names


def test_java_subclass_inherits_the_method_but_not_the_base_constructor(tmp_path: Path) -> None:
    _write(tmp_path, "src/p/Base.java", JAVA_BASE)
    _write(tmp_path, "src/p/Derived.java", JAVA_DERIVED)
    types = _extract("java", tmp_path / "src", tmp_path)
    derived = _type(types, "Derived")
    names = _method_names(derived)

    save = next(m for m in derived["methods"] if m["name"] == "save")
    assert save["inherited_from"].endswith("Base")
    assert "Base" not in names
    assert "load" in names


def test_cpp_subclass_inherits_the_method_but_not_the_constructor_or_destructor(tmp_path: Path) -> None:
    _write(tmp_path, "src/widget.h", CPP)
    types = _extract("cpp", tmp_path / "src", tmp_path)
    # The extractor does see the base's inline constructor and destructor, so their absence
    # from the subclass is a real filtering result, not an extraction gap.
    assert {"Base", "~Base"} <= set(_method_names(_type(types, "Base")))
    derived = _type(types, "Derived")
    names = _method_names(derived)

    assert "Save" in names
    assert "Base" not in names
    assert "~Base" not in names
    assert "Load" in names


def test_typescript_subclass_inherits_the_method_but_not_the_base_constructor(tmp_path: Path) -> None:
    _write(tmp_path, "package.json", '{"name": "widget"}\n')
    _write(tmp_path, "src/shapes.ts", TYPESCRIPT)
    types = _extract("typescript", tmp_path / "src", tmp_path)
    derived = _type(types, "Derived")
    names = _method_names(derived)

    assert "save" in names
    assert "constructor" not in names
    assert "Base" not in names
    assert "load" in names


# ---------------------------------------------------------------------------
# _is_constructor_like unit cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method, short_name",
    [
        ({"name": "Anything", "is_constructor": True}, "Widget"),
        ({"name": "Widget", "return_type": "Widget"}, "Widget"),
        ({"name": "~Widget"}, "Widget"),
        ({"name": "constructor"}, "Widget"),
        ({"name": "__init__"}, "Widget"),
    ],
)
def test_is_constructor_like_recognises_every_constructor_shape(method: dict, short_name: str) -> None:
    assert _is_constructor_like(method, short_name) is True


def test_is_constructor_like_ignores_a_method_that_merely_returns_the_class_type() -> None:
    clone = {"name": "Clone", "params": [], "return_type": "Widget", "is_constructor": False}
    assert _is_constructor_like(clone, "Widget") is False


@pytest.mark.parametrize(
    "method, short_name",
    [
        ({"name": "Render"}, "Widget"),
        ({"name": "~Other"}, "Widget"),
        ({"name": "widget"}, "Widget"),
        ({"name": "Widgets"}, "Widget"),
        ({"name": "Render"}, ""),
    ],
)
def test_is_constructor_like_rejects_ordinary_methods(method: dict, short_name: str) -> None:
    assert _is_constructor_like(method, short_name) is False


# ---------------------------------------------------------------------------
# Test source files are not API
# ---------------------------------------------------------------------------

GO_DOC = """// Package widget draws pages.
package widget
"""

GO_PAGE = """package widget

// Page is a page.
type Page struct {
	Number int
}

// NewPage creates a page.
func NewPage(n int) *Page {
	return &Page{Number: n}
}

// Rotate rotates the page.
func (p *Page) Rotate(degrees int) {
}
"""

GO_PAGE_TEST = """package widget

import "testing"

func ExamplePage_Rotate() {
	p := NewPage(1)
	p.Rotate(90)
}

func TestRotate(t *testing.T) {
}

// BuildFixturePage is a test helper.
func BuildFixturePage() *Page {
	return NewPage(0)
}
"""


def test_go_test_file_symbols_never_appear_in_the_surface(tmp_path: Path) -> None:
    _write(tmp_path, "widget/doc.go", GO_DOC)
    _write(tmp_path, "widget/page.go", GO_PAGE)
    _write(tmp_path, "widget/page_test.go", GO_PAGE_TEST)
    types = _extract("go", tmp_path / "widget", tmp_path)
    names = _all_names(types)

    assert "ExamplePage_Rotate" not in names
    assert "TestRotate" not in names
    assert "BuildFixturePage" not in names
    assert {"Page", "NewPage", "Rotate"} <= names


def test_go_collection_skips_the_test_file_but_keeps_the_code_files(tmp_path: Path) -> None:
    _write(tmp_path, "widget/doc.go", GO_DOC)
    _write(tmp_path, "widget/page.go", GO_PAGE)
    _write(tmp_path, "widget/page_test.go", GO_PAGE_TEST)

    collected = sorted(f.name for f in _collect_source_files(tmp_path / "widget", ".go"))

    assert collected == ["doc.go", "page.go"]


def test_typescript_spec_and_test_files_are_not_walked(tmp_path: Path) -> None:
    _write(tmp_path, "package.json", '{"name": "widget"}\n')
    _write(tmp_path, "src/x.ts", "export function realApi(): number { return 1; }\n")
    _write(tmp_path, "src/x.spec.ts", "export function specHelper(): number { return 2; }\n")
    _write(tmp_path, "src/x.test.ts", "export function testHelper(): number { return 3; }\n")

    types = _extract("typescript", tmp_path / "src", tmp_path)
    names = _all_names(types)

    assert "realApi" in names
    assert "specHelper" not in names
    assert "testHelper" not in names


def test_python_test_prefixed_files_are_not_walked(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/x.py", "def real_api():\n    return 1\n")
    _write(tmp_path, "pkg/test_x.py", "def test_helper():\n    return 2\n")

    collected = sorted(f.name for f in _collect_source_files(tmp_path / "pkg", ".py"))
    types = _extract("python", tmp_path / "pkg", tmp_path)
    names = _all_names(types)

    assert collected == ["x.py"]
    assert "real_api" in names
    assert "test_helper" not in names


def test_files_that_merely_contain_test_in_their_name_are_still_walked(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/attestation.go", "package pkg\n\nfunc Attest() {}\n")
    _write(tmp_path, "pkg/latest.ts", "export function latest(): number { return 1; }\n")
    _write(tmp_path, "pkg/contest.py", "def contest():\n    return 1\n")

    assert [f.name for f in _collect_source_files(tmp_path / "pkg", ".go")] == ["attestation.go"]
    assert [f.name for f in _collect_source_files(tmp_path / "pkg", ".ts")] == ["latest.ts"]
    assert [f.name for f in _collect_source_files(tmp_path / "pkg", ".py")] == ["contest.py"]


@pytest.mark.parametrize(
    "name",
    [
        "page_test.go",
        "page_test.py",
        "x.test.ts",
        "x.spec.ts",
        "x.test.tsx",
        "x.spec.tsx",
        "x.test.js",
        "x.spec.js",
        "x.test.mjs",
        "test_x.py",
    ],
)
def test_is_test_source_file_matches_every_test_naming_convention(name: str) -> None:
    assert _is_test_source_file(Path("some") / "dir" / name) is True


@pytest.mark.parametrize(
    "name",
    [
        "page.go",
        "attestation.go",
        "latest.ts",
        "contest.py",
        "test.go",
        "testing.py",
        "x.ts",
        "spec.ts",
        "x.mjs",
    ],
)
def test_is_test_source_file_leaves_ordinary_files_alone(name: str) -> None:
    assert _is_test_source_file(Path("some") / "dir" / name) is False
