"""TC-241: a nested/inner class's FQN must include its real enclosing class.

An independent product-readiness audit (2026-10-07) found that a class declared
inside another class/interface/struct/enum silently dropped its enclosing type
from ``class_import``/``canonical_namespace`` in Java, C++, and C# extraction --
confirmed in the committed ``tests/fixtures/jmap_java/api_surface.json`` fixture,
whose real ``JmapClientOptions.Builder`` was recorded as
``class_import="com.aspose.jmap.Builder"``, dropping ``JmapClientOptions``
entirely and producing a non-importable name.

Two kinds of test:

(a) Synthetic, offline -- one small hand-written snippet per language (Java,
    C++, C#), each declaring an outer class containing one nested class plus a
    completely separate, non-nested class in the same file. Confirms the nested
    class's class_import/canonical_namespace now includes the enclosing class
    name in the correct position/separator for that language, AND that the
    non-nested class in the same snippet is completely unaffected (identical
    class_import to before this fix) -- this card must not change behavior for
    the overwhelmingly common non-nested case.

(b) Real-data, offline -- reads the REGENERATED
    ``tests/fixtures/jmap_java/api_surface.json`` directly (no network) and
    pins the real fixture's own corrected ``Builder`` entry, making the
    negative control non-vacuous: it checks the real enclosing-class name this
    exact bug dropped, not just a synthetic snippet.
"""

from __future__ import annotations

import json
from pathlib import Path

from tree_sitter_language_pack import get_parser

from foss_mcp.extraction.tree_sitter_engine import api_surface
from foss_mcp.extraction.tree_sitter_engine.tree_helpers import (
    _CLASS_TYPES,
    child_by_field,
    collect_nodes,
    node_text,
)

FIXTURE = Path(__file__).parents[1] / "fixtures" / "jmap_java" / "api_surface.json"

# ---------------------------------------------------------------------------
# (a) Synthetic, offline snippets
# ---------------------------------------------------------------------------

JAVA_NESTED = """
package com.example;

public class Outer {
    public static class Inner {
        public void run() { }
    }
}

public class Standalone {
    public void go() { }
}
"""

CPP_NESTED = """
namespace Aspose {

class Outer {
public:
    class Inner {
    public:
        void run();
    };
};

class Standalone {
public:
    void go();
};

}
"""

CSHARP_NESTED = """
namespace Aspose.Widget
{
    public class Outer
    {
        public class Inner
        {
            public void Run() { }
        }
    }

    public class Standalone
    {
        public void Go() { }
    }
}
"""


def _package(root: Path, name: str, source: str) -> Path:
    package = root / "src"
    package.mkdir(parents=True, exist_ok=True)
    (package / name).write_text(source, encoding="utf-8")
    return package


def _extract(language: str, package: Path, root: Path, family: str = "widget") -> list[dict]:
    types, *_ = api_surface.extract_api_surface(get_parser(language), language, package, root, family)
    return types


def test_a_nested_java_class_carries_its_enclosing_class_in_its_fqn(tmp_path: Path) -> None:
    package = _package(tmp_path, "Outer.java", JAVA_NESTED)
    types = _extract("java", package, tmp_path)
    by_name = {t["name"]: t for t in types}

    inner = by_name["Inner"]
    assert inner["class_import"] == "com.example.Outer.Inner"
    assert inner["canonical_namespace"] == "com.example.Outer"

    outer = by_name["Outer"]
    assert outer["class_import"] == "com.example.Outer"
    assert outer["canonical_namespace"] == "com.example"


def test_a_non_nested_java_class_in_the_same_file_is_unaffected(tmp_path: Path) -> None:
    package = _package(tmp_path, "Outer.java", JAVA_NESTED)
    types = _extract("java", package, tmp_path)
    by_name = {t["name"]: t for t in types}

    standalone = by_name["Standalone"]
    assert standalone["class_import"] == "com.example.Standalone"
    assert standalone["canonical_namespace"] == "com.example"


def _cpp_class_node_by_name(source: str, name: str):
    """Parse *source* directly and return the ``class_specifier``/
    ``struct_specifier`` node named *name*, bypassing ``extract_api_surface``'s
    full pipeline (including ``is_public``) entirely.

    A nested C++ class definition is wrapped by tree-sitter-cpp in a
    ``field_declaration`` node (verified via a live parse probe), which
    ``is_public()`` (in ``tree_helpers.py`` -- not a write_path of this card)
    does not walk through for its access_specifier check, so a nested C++
    class is filtered out of ``extract_api_surface``'s output before this
    card's canonical-namespace fix is ever reached. That gap is a separate,
    pre-existing concern outside this card's scope (the card's own inputs
    diagnose the root cause as exactly the three namespace/FQN-building
    functions, not ``is_public``). This test instead does exactly what the
    card's own test-design input permits: parse directly with ``get_parser``
    and call the fixed function directly against the real parsed node.
    """
    parser = get_parser("cpp")
    tree = parser.parse(source.encode("utf-8"))
    for node in collect_nodes(tree.root_node, _CLASS_TYPES["cpp"]):
        name_node = child_by_field(node, "name")
        if name_node is not None and node_text(name_node) == name:
            return node
    raise AssertionError(f"no cpp class-type node named {name!r} found")


def test_a_nested_cpp_class_carries_its_enclosing_class_in_its_fqn() -> None:
    inner = _cpp_class_node_by_name(CPP_NESTED, "Inner")
    assert api_surface._cpp_canonical_namespace(inner) == "Aspose::Outer"

    outer = _cpp_class_node_by_name(CPP_NESTED, "Outer")
    assert api_surface._cpp_canonical_namespace(outer) == "Aspose"


def test_a_non_nested_cpp_class_in_the_same_file_is_unaffected() -> None:
    standalone = _cpp_class_node_by_name(CPP_NESTED, "Standalone")
    assert api_surface._cpp_canonical_namespace(standalone) == "Aspose"


def test_a_nested_csharp_class_carries_its_enclosing_class_in_its_fqn(tmp_path: Path) -> None:
    package = _package(tmp_path, "Outer.cs", CSHARP_NESTED)
    types = _extract("csharp", package, tmp_path)
    by_name = {t["name"]: t for t in types}

    inner = by_name["Inner"]
    assert inner["class_import"] == "Aspose.Widget.Outer.Inner"
    assert inner["canonical_namespace"] == "Aspose.Widget.Outer"

    outer = by_name["Outer"]
    assert outer["class_import"] == "Aspose.Widget.Outer"
    assert outer["canonical_namespace"] == "Aspose.Widget"


def test_a_non_nested_csharp_class_in_the_same_file_is_unaffected(tmp_path: Path) -> None:
    package = _package(tmp_path, "Outer.cs", CSHARP_NESTED)
    types = _extract("csharp", package, tmp_path)
    by_name = {t["name"]: t for t in types}

    standalone = by_name["Standalone"]
    assert standalone["class_import"] == "Aspose.Widget.Standalone"
    assert standalone["canonical_namespace"] == "Aspose.Widget"


# ---------------------------------------------------------------------------
# (b) Real-data, offline: pin the regenerated jmap/java fixture's own real
# enclosing-class name for Builder (the exact bug the audit found).
# ---------------------------------------------------------------------------


def test_the_real_jmap_java_builder_fixture_now_carries_its_enclosing_class() -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    builder = next(t for t in data["types"] if t["name"] == "Builder")
    assert "JmapClientOptions" in builder["class_import"]
    assert builder["class_import"] == "com.aspose.jmap.JmapClientOptions.Builder"
