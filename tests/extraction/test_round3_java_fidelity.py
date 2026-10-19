"""G3/TC-557 (REQ-G3-009, rows RD3-040 to RD3-047): the Java extraction states what the Java language declares.

The round-3 review compared the served Java signatures with the compiled libraries and found members the extractor dropped:
varargs parameters (``Line.fromPoints(Vector3...)`` was served ``fromPoints()``), ``throws`` clauses (0 of 609), ``static``
(0 of 799), public mutable instance fields, generic method type parameters, the implicit default constructor, the members of a
record, and methods of anonymous classes attributed to the enclosing type.

INDEPENDENT ORACLE. Nothing here reads the extractor's own output as the expectation:

* The synthetic tests extract small Java sources written in this file. The expected answer of each follows from the rules of the
  Java Language Specification (the section is named at the test): a varargs parameter is written ``T...`` (8.4.1), a ``throws``
  clause lists the declared exceptions (8.4.6), a class that declares no constructor has an implicit default constructor (8.8.9),
  a record has a canonical constructor and one accessor per component (8.10.3, 8.10.4), an interface field is implicitly
  ``static final`` (9.3), the body of an anonymous class belongs to the anonymous class (15.9.5).
* The real-library tests read the committed fixtures and compare them with facts read from the pinned upstream sources
  (the file and line are named at each assertion). No test here calls javac, javap or maven.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tree_sitter_language_pack import get_parser

from foss_mcp.extraction.tree_sitter_engine import api_surface

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _load(family: str) -> dict:
    return json.loads((FIXTURES / f"{family}_java" / "api_surface.json").read_text(encoding="utf-8"))


def _extract(tmp_path: Path, source: str) -> dict[str, dict]:
    """The extracted types of one source file, by simple name."""
    package = tmp_path / "src" / "p"
    package.mkdir(parents=True)
    (package / "Sample.java").write_text(source, encoding="utf-8")
    types, *_ = api_surface.extract_api_surface(get_parser("java"), "java", package, tmp_path, "widget")
    return {t["name"]: t for t in types}


def _methods(entry: dict, name: str) -> list[dict]:
    return [m for m in entry["methods"] if m["name"] == name]


def _prop(entry: dict, name: str) -> dict:
    return next(p for p in entry["properties"] if p["name"] == name and "inherited_from" not in p)


def _own(entry: dict, key: str) -> list[dict]:
    return [m for m in entry[key] if "inherited_from" not in m]


# -- RD3-040: varargs (JLS 8.4.1) -----------------------------------------------------------------------------------------------


def test_a_varargs_parameter_keeps_its_three_dots(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        """
package p;
public class Line {
    public static Line fromPoints(Vector3... points) { return null; }
}
""",
    )
    (method,) = _methods(types["Line"], "fromPoints")
    assert [(p["name"], p["type"]) for p in method["params"]] == [("points", "Vector3...")]
    assert method["params"][0]["varargs"] is True


def test_an_array_parameter_is_not_a_varargs_parameter(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        """
package p;
public class Box {
    public void takeArray(int[] values) {}
    public void takeSpread(int... values) {}
}
""",
    )
    (array,) = _methods(types["Box"], "takeArray")
    (spread,) = _methods(types["Box"], "takeSpread")
    assert array["params"] == [{"name": "values", "type": "int[]"}]
    assert spread["params"][0]["type"] == "int..."
    assert "varargs" not in array["params"][0]


def test_a_varargs_parameter_follows_the_fixed_parameters_and_may_be_final_annotated_or_generic(
    tmp_path: Path,
) -> None:
    types = _extract(
        tmp_path,
        """
package p;
import java.util.List;
public class Fmt {
    public String format(String pattern, final Object... args) { return pattern; }
    public void merge(@Deprecated List<String>... lists) {}
    public Fmt(int first, byte[]... chunks) {}
}
""",
    )
    fmt = types["Fmt"]
    (format_method,) = _methods(fmt, "format")
    assert [p["type"] for p in format_method["params"]] == ["String", "Object..."]
    assert [p["name"] for p in format_method["params"]] == ["pattern", "args"]
    (merge,) = _methods(fmt, "merge")
    assert [p["type"] for p in merge["params"]] == ["List<String>..."]
    (constructor,) = [m for m in fmt["methods"] if m["is_constructor"]]
    assert [p["type"] for p in constructor["params"]] == ["int", "byte[]..."]


# -- RD3-041: throws (JLS 8.4.6) ------------------------------------------------------------------------------------------------


def test_a_throws_clause_is_recorded_in_source_order(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        """
package p;
import java.io.IOException;
public class Saver {
    public void save(String path) throws IOException {}
    public void both() throws java.io.IOException, IllegalStateException {}
    public void none() {}
}
""",
    )
    saver = types["Saver"]
    assert _methods(saver, "save")[0]["throws"] == ["IOException"]
    assert _methods(saver, "both")[0]["throws"] == ["java.io.IOException", "IllegalStateException"]
    assert "throws" not in _methods(saver, "none")[0]


def test_a_constructor_and_an_interface_method_record_their_throws_clause(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        """
package p;
import java.io.IOException;
public class Reader {
    public Reader(String path) throws IOException {}
}
public interface Sink {
    void write(byte[] data) throws IOException;
}
""",
    )
    (constructor,) = [m for m in types["Reader"]["methods"] if m["is_constructor"]]
    assert constructor["throws"] == ["IOException"]
    assert _methods(types["Sink"], "write")[0]["throws"] == ["IOException"]


def test_a_type_variable_in_a_throws_clause_is_kept_as_written(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        """
package p;
public class Try {
    public <E extends Exception> void run(Runnable r) throws E {}
}
""",
    )
    method = _methods(types["Try"], "run")[0]
    assert method["throws"] == ["E"]
    assert method["generics"] == "<E extends Exception>"


# -- RD3-042: static (JLS 8.4.3.2, 8.3.1.1) -------------------------------------------------------------------------------------


def test_a_static_method_is_marked_and_an_instance_method_is_not(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        """
package p;
public class Util {
    public static int twice(int x) { return 2 * x; }
    public int once(int x) { return x; }
    public final synchronized void locked() {}
}
public interface Maker {
    public static Maker make() { return null; }
    void build();
}
""",
    )
    util = types["Util"]
    assert _methods(util, "twice")[0]["modifiers"] == ["static"]
    assert "modifiers" not in _methods(util, "once")[0]
    assert "modifiers" not in _methods(util, "locked")[0]
    assert _methods(types["Maker"], "make")[0]["modifiers"] == ["static"]
    assert "modifiers" not in _methods(types["Maker"], "build")[0]


def test_a_static_field_is_marked_and_an_instance_constant_is_not(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        """
package p;
public class Limits {
    public static final int MAX = 10;
    public final int fixed = 3;
    public static int counter;
}
""",
    )
    limits = types["Limits"]
    assert _prop(limits, "MAX")["modifiers"] == ["static"]
    assert "modifiers" not in _prop(limits, "fixed")
    assert _prop(limits, "counter")["modifiers"] == ["static"]


# -- RD3-043: public mutable instance fields (JLS 8.3) ---------------------------------------------------------------------------


def test_a_public_mutable_field_is_a_writable_property_with_its_type(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        """
package p;
public class Vector3 {
    public double x;
    public double y, z;
    public int[] cells;
    public int legacy[];
    public java.util.List<String> names;
}
""",
    )
    vector = types["Vector3"]
    assert [(p["name"], p["type"], p["writable"]) for p in vector["properties"]] == [
        ("x", "double", True),
        ("y", "double", True),
        ("z", "double", True),
        ("cells", "int[]", True),
        ("legacy", "int[]", True),
        ("names", "java.util.List<String>", True),
    ]
    assert all(p["kind"] == "field" for p in vector["properties"])
    assert all("modifiers" not in p for p in vector["properties"])


def test_a_field_that_is_not_public_or_is_final_is_not_a_mutable_property(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        """
package p;
public class Hidden {
    private int secret;
    protected int shared;
    int packageLevel;
    public final int constant = 1;
    public int visible;
}
public interface Consts {
    int IMPLICIT = 1;
}
""",
    )
    hidden = types["Hidden"]
    mutable = [p["name"] for p in hidden["properties"] if p.get("kind") == "field"]
    assert mutable == ["visible"]
    assert [p["name"] for p in types["Consts"]["properties"]] == []


def test_a_field_of_an_anonymous_class_is_not_a_field_of_the_enclosing_class(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        """
package p;
public class Host {
    public int own;
    Runnable r = new Runnable() {
        public int inner;
        public void run() {}
    };
}
""",
    )
    assert [p["name"] for p in types["Host"]["properties"] if p.get("kind") == "field"] == ["own"]


# -- RD3-044: generic method type parameters (JLS 8.4.4) -------------------------------------------------------------------------


def test_generic_method_type_parameters_and_bounds_are_recorded(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        """
package p;
import java.util.List;
public class Node {
    public <T extends Entity> T getEntity(Class<T> type) { return null; }
    public static <K, V extends List<K> & java.io.Serializable> V pick(K key) { return null; }
    public String plain() { return ""; }
}
""",
    )
    node = types["Node"]
    assert _methods(node, "getEntity")[0]["generics"] == "<T extends Entity>"
    assert _methods(node, "pick")[0]["generics"] == "<K, V extends List<K> & java.io.Serializable>"
    assert "generics" not in _methods(node, "plain")[0]


def test_a_generic_class_parameter_is_not_a_method_type_parameter(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        """
package p;
public class Holder<T> {
    public T get() { return null; }
}
""",
    )
    assert "generics" not in _methods(types["Holder"], "get")[0]


# -- RD3-045: anonymous classes (JLS 15.9.5) -------------------------------------------------------------------------------------


def test_methods_of_anonymous_classes_are_not_members_of_the_enclosing_type(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        """
package p;
import java.util.Iterator;
public class Page {
    public void add(Object artifact) {}
    public Iterator<String> iterator() {
        return new Iterator<String>() {
            public boolean hasNext() { return false; }
            public String next() { return null; }
        };
    }
    private final Runnable field = new Runnable() {
        public void run() {}
    };
}
""",
    )
    names = sorted(m["name"] for m in types["Page"]["methods"])
    # the implicit default constructor is the class's own; hasNext, next and run belong to the anonymous classes
    assert names == ["Page", "add", "iterator"]


def test_a_method_of_an_enum_constant_body_is_not_a_member_of_the_enum(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        """
package p;
public enum Mode {
    FAST { public int cost() { return 1; } },
    SLOW { public int cost() { return 9; } };
    public int cost() { return 0; }
    public String label() { return name(); }
}
""",
    )
    assert sorted(m["name"] for m in types["Mode"]["methods"]) == ["cost", "label"]
    assert len(_methods(types["Mode"], "cost")) == 1


# -- RD3-046: records (JLS 8.10) ---------------------------------------------------------------------------------------------------


def test_a_record_has_its_components_canonical_constructor_and_accessors(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        """
package p;
public record Point(int x, int y) {
    public int sum() { return x + y; }
}
""",
    )
    point = types["Point"]
    assert point["kind"] == "record_declaration"
    assert [(p["name"], p["type"], p["writable"]) for p in point["properties"]] == [
        ("x", "int", False),
        ("y", "int", False),
    ]
    constructors = [m for m in point["methods"] if m["is_constructor"]]
    assert [[(p["name"], p["type"]) for p in c["params"]] for c in constructors] == [
        [("x", "int"), ("y", "int")]
    ]
    assert {m["name"]: m["return_type"] for m in point["methods"] if not m["is_constructor"]} == {
        "sum": "int",
        "x": "int",
        "y": "int",
    }


def test_a_record_does_not_repeat_an_accessor_or_a_canonical_constructor_it_declares(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        """
package p;
public record Range(int low, int high) {
    /** The explicit accessor. */
    public int low() { return low; }
    public Range(int low, int high) {
        this.low = low;
        this.high = high;
    }
}
""",
    )
    range_type = types["Range"]
    assert len(_methods(range_type, "low")) == 1
    assert _methods(range_type, "low")[0]["doc"] == "The explicit accessor."
    assert "implicit" not in (_methods(range_type, "low")[0].get("modifiers") or [])
    assert len(_methods(range_type, "high")) == 1
    assert len([m for m in range_type["methods"] if m["is_constructor"]]) == 1


def test_a_compact_constructor_is_the_canonical_constructor_of_the_record(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        """
package p;
public record FontData(String fontName) {
    /** Creates a font. */
    public FontData {
        java.util.Objects.requireNonNull(fontName);
    }
}
""",
    )
    constructors = [m for m in types["FontData"]["methods"] if m["is_constructor"]]
    assert len(constructors) == 1
    assert [(p["name"], p["type"]) for p in constructors[0]["params"]] == [("fontName", "String")]
    assert constructors[0]["doc"] == "Creates a font."


def test_a_varargs_record_component_is_an_array_to_its_accessor(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        """
package p;
public record Tags(String label, String... values) {}
""",
    )
    tags = types["Tags"]
    (constructor,) = [m for m in tags["methods"] if m["is_constructor"]]
    assert [p["type"] for p in constructor["params"]] == ["String", "String..."]
    assert _methods(tags, "values")[0]["return_type"] == "String[]"


# -- RD3-047: the implicit default constructor (JLS 8.8.9) ---------------------------------------------------------------------


def test_a_class_that_declares_no_constructor_has_an_implicit_default_constructor(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        """
package p;
public class Plain {
    public void go() {}
}
""",
    )
    constructors = [m for m in types["Plain"]["methods"] if m["is_constructor"]]
    assert len(constructors) == 1
    assert constructors[0]["name"] == "Plain"
    assert constructors[0]["params"] == []
    assert constructors[0]["modifiers"] == ["implicit"]


def test_a_class_that_declares_any_constructor_has_no_implicit_one(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        """
package p;
public class WithPublic {
    public WithPublic(int x) {}
}
public class OnlyPrivate {
    private OnlyPrivate() {}
    public static OnlyPrivate make() { return null; }
}
""",
    )
    public_ctors = [m for m in types["WithPublic"]["methods"] if m["is_constructor"]]
    assert [[p["type"] for p in c["params"]] for c in public_ctors] == [["int"]]
    assert "implicit" not in (public_ctors[0].get("modifiers") or [])
    # a private constructor is declared, so there is no implicit one, and it is not public API either
    assert [m for m in types["OnlyPrivate"]["methods"] if m["is_constructor"]] == []


def test_an_interface_an_enum_and_an_annotation_type_have_no_implicit_constructor(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        """
package p;
public interface Shape { double area(); }
public enum Color { RED, GREEN }
public @interface Marker { String value(); }
""",
    )
    for name in ("Shape", "Color", "Marker"):
        assert [m for m in types[name]["methods"] if m["is_constructor"]] == [], name


def test_an_abstract_class_and_a_nested_public_class_have_an_implicit_constructor(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        """
package p;
public abstract class Base {
    public static class Options {}
}
""",
    )
    assert [m["name"] for m in types["Base"]["methods"] if m["is_constructor"]] == ["Base"]
    assert [m["name"] for m in types["Options"]["methods"] if m["is_constructor"]] == ["Options"]


def test_an_implicit_constructor_is_not_inherited_by_a_subclass(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        """
package p;
public class Parent {
    public void run() {}
}
public class Child extends Parent {
    public Child(int x) {}
}
""",
    )
    child_constructors = [m for m in types["Child"]["methods"] if m["is_constructor"]]
    assert [[p["type"] for p in c["params"]] for c in child_constructors] == [["int"]]
    assert [m["name"] for m in types["Child"]["methods"] if m.get("inherited_from")] == ["run"]


# -- interface members are implicitly public (JLS 9.4) -----------------------------------------------------------------------------


def test_an_interface_static_or_default_method_is_public_api_and_a_private_one_is_not(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        """
package p;
public interface Shape {
    double area();
    static Shape unit() { return null; }
    default String label() { return "shape"; }
    @Deprecated default void old() {}
    private void helper() {}
    private static void staticHelper() {}
}
""",
    )
    shape = types["Shape"]
    assert sorted(m["name"] for m in shape["methods"]) == ["area", "label", "old", "unit"]
    assert _methods(shape, "unit")[0]["modifiers"] == ["static"]
    assert "modifiers" not in _methods(shape, "label")[0]


def test_a_package_private_static_method_of_a_class_is_still_not_public_api(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        """
package p;
public class Util {
    static int hidden() { return 1; }
    protected static int guarded() { return 3; }
    public static int shown() { return 2; }
}
public @interface Marker {
    String value();
}
""",
    )
    assert [m["name"] for m in types["Util"]["methods"] if not m["is_constructor"]] == ["shown"]


def test_a_nested_interface_inside_a_class_gets_the_same_rule(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        """
package p;
public class Outer {
    public interface Inner {
        static Inner make() { return null; }
        default void run() {}
    }
}
""",
    )
    assert sorted(m["name"] for m in types["Inner"]["methods"]) == ["make", "run"]


def test_the_interface_rule_does_not_reach_other_languages(tmp_path: Path) -> None:
    from foss_mcp.extraction.tree_sitter_engine.tree_helpers import is_public

    parser = get_parser("csharp")
    tree = parser.parse(b"public interface I { static void M() {} }")
    method = next(n for n in _walk(tree.root_node) if n.type == "method_declaration")
    # C# keeps its own rule: a ``static`` modifier without ``public`` is not public here (unchanged by TC-557)
    assert is_public(method, "csharp") is False


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


# -- nothing that was faithful changes ---------------------------------------------------------------------------------------------


def test_ordinary_members_keep_their_names_types_and_overloads(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        """
package p;
import java.util.List;
public class Doc {
    /** Gets the page count. */
    public int getPageCount() { return 0; }
    public List<String> names(int limit) { return null; }
    public void put(String key, int value) {}
    public void put(String key, String value) {}
}
""",
    )
    doc = types["Doc"]
    assert _methods(doc, "getPageCount")[0]["return_type"] == "int"
    assert _methods(doc, "getPageCount")[0]["doc"] == "Gets the page count."
    assert _methods(doc, "names")[0]["return_type"] == "List<String>"
    assert [[p["type"] for p in m["params"]] for m in _methods(doc, "put")] == [
        ["String", "int"],
        ["String", "String"],
    ]
    # the getter still yields its synthesised property
    assert any(p["name"] == "pageCount" and p["type"] == "int" for p in doc["properties"])


def test_a_field_and_a_getter_of_one_name_keep_the_field(tmp_path: Path) -> None:
    types = _extract(
        tmp_path,
        """
package p;
public class Dual {
    public double x;
    public double getX() { return x; }
}
""",
    )
    named_x = [p for p in types["Dual"]["properties"] if p["name"] == "x"]
    assert named_x[0]["kind"] == "field"
    assert named_x[0]["writable"] is True


# -- the real libraries, at their pinned commits ----------------------------------------------------------------------------------


def _type(family: str, class_import: str) -> dict:
    return next(t for t in _load(family)["types"] if t["class_import"] == class_import)


def test_3d_java_line_from_points_is_varargs_and_static() -> None:
    # upstream com/aspose/threed/Line.java line 59: ``public static Line fromPoints(Vector3... points)``
    line = _type("3d", "com.aspose.threed.Line")
    (method,) = _methods(line, "fromPoints")
    assert method["params"] == [{"name": "points", "type": "Vector3...", "varargs": True}]
    assert method["modifiers"] == ["static"]
    assert method["return_type"] == "Line"


def test_3d_java_vector3_has_public_instance_fields_x_y_z() -> None:
    # upstream com/aspose/threed/Vector3.java lines 6-8: ``public double x;`` ``public double y;`` ``public double z;``
    vector = _type("3d", "com.aspose.threed.Vector3")
    fields = {p["name"]: p for p in _own(vector, "properties") if p.get("kind") == "field"}
    assert {name: fields[name]["type"] for name in "xyz"} == {"x": "double", "y": "double", "z": "double"}
    assert all(fields[name]["writable"] is True for name in "xyz")
    # FVector3 (float): lines 6-8 of FVector3.java
    single = _type("3d", "com.aspose.threed.FVector3")
    assert {p["name"]: p["type"] for p in _own(single, "properties") if p.get("kind") == "field"} == {
        "x": "float",
        "y": "float",
        "z": "float",
    }


def test_3d_java_generic_getter_keeps_its_type_parameter_bound() -> None:
    # upstream com/aspose/threed/Geometry.java line 134: ``public <T extends Deformer> java.util.Collection<T> getDeformers2()``
    geometry = _type("3d", "com.aspose.threed.Geometry")
    (method,) = _methods(geometry, "getDeformers2")
    assert method["generics"] == "<T extends Deformer>"
    assert method["return_type"] == "java.util.Collection<T>"
    assert "generics" not in _methods(geometry, "getDeformers")[0]


def test_pdf_java_compaction_options_set_pages_is_varargs() -> None:
    # upstream org/aspose/pdf/CompactionOptions.java line 39: ``public CompactionOptions setPages(int... pages)``
    options = _type("pdf", "org.aspose.pdf.CompactionOptions")
    (method,) = _methods(options, "setPages")
    assert [p["type"] for p in method["params"]] == ["int..."]
    assert method["return_type"] == "CompactionOptions"


def test_pdf_java_serves_all_nine_varargs_methods_with_their_dots() -> None:
    pdf = _load("pdf")
    varargs = sorted(
        (t["name"], m["name"])
        for t in pdf["types"]
        for m in _own(t, "methods")
        for p in m["params"]
        if p.get("varargs")
    )
    # the nine methods the round-3 review counted in the compiled library (RD3-040: "pdf/java 9 of 9")
    assert varargs == sorted(
        [
            ("Annotation", "setFlags"),
            ("CompactionOptions", "setPages"),
            ("SetColor", "SetColor"),
            ("SetColorStroke", "SetColorStroke"),
            ("PdfSdmWriter", "regeneratePages"),
            ("PdfDictionary", "getPath"),
            ("Realm", "newArray"),
            ("DEREncoder", "encodeSequence"),
            ("DEREncoder", "encodeSet"),
        ]
    )
    assert all(
        p["type"].endswith("...")
        for t in pdf["types"]
        for m in t["methods"]
        for p in m["params"]
        if p.get("varargs")
    )


def test_pdf_java_document_save_declares_ioexception() -> None:
    # upstream org/aspose/pdf/Document.java line 1502: ``public void save(String filePath) throws IOException``
    # and line 1153: ``public PdfDictionary getCatalog() throws IOException``
    document = _type("pdf", "org.aspose.pdf.Document")
    saves = [
        m
        for m in _own(document, "methods")
        if m["name"] == "save" and [p["type"] for p in m["params"]] == ["String"]
    ]
    assert len(saves) == 1 and saves[0]["throws"] == ["IOException"]
    assert _methods(document, "getCatalog")[0]["throws"] == ["IOException"]
    assert _methods(document, "getCatalog")[0]["return_type"] == "PdfDictionary"


def test_every_java_pilot_records_the_throws_of_the_methods_that_declare_them() -> None:
    # the round-3 review found 0 of 609 rendered; the four regenerated fixtures now carry far more than that
    counts = {
        family: sum(1 for t in _load(family)["types"] for m in _own(t, "methods") if m.get("throws"))
        for family in ("3d", "cells", "pdf", "slides")
    }
    assert counts["pdf"] >= 400
    assert counts["3d"] >= 100
    assert counts["cells"] >= 1
    assert counts["slides"] >= 20
    assert sum(counts.values()) >= 609


def test_slides_java_color_from_argb_is_static() -> None:
    # upstream org/aspose/slides/foss/drawing/Color.java lines 129, 141, 151: three ``public static Color fromArgb``
    color = _type("slides", "org.aspose.slides.foss.drawing.Color")
    overloads = _methods(color, "fromArgb")
    assert sorted(len(m["params"]) for m in overloads) == [1, 3, 4]
    assert all(m["modifiers"] == ["static"] for m in overloads)


def test_slides_java_font_data_is_a_record_with_its_component_constructor_and_accessor() -> None:
    # upstream org/aspose/slides/foss/FontData.java line 10: ``public record FontData(String fontName) implements IFontData``
    # with a compact constructor and getFontName() / getFontName(Object)
    font_data = _type("slides", "org.aspose.slides.foss.FontData")
    assert font_data["kind"] == "record_declaration"
    assert [
        (p["name"], p["type"], p["writable"])
        for p in font_data["properties"]
        if p.get("kind") == "record_component"
    ] == [("fontName", "String", False)]
    (constructor,) = [m for m in font_data["methods"] if m["is_constructor"]]
    assert [(p["name"], p["type"]) for p in constructor["params"]] == [("fontName", "String")]
    assert [(m["name"], m["return_type"]) for m in font_data["methods"] if m["name"] == "fontName"] == [
        ("fontName", "String")
    ]


@pytest.mark.parametrize(
    ("family", "expected"),
    [("3d", 19), ("cells", 55), ("pdf", 84), ("slides", 9)],
)
def test_each_java_pilot_serves_the_implicit_default_constructors_the_review_counted(
    family: str, expected: int
) -> None:
    # RD3-047 counts: pdf/java 84, cells/java 55, 3d/java 19, slides/java 9 classes without a declared constructor
    implicit = [
        (t["class_import"], m["name"])
        for t in _load(family)["types"]
        if t["kind"] == "class_declaration"
        for m in _own(t, "methods")
        if m["is_constructor"] and "implicit" in (m.get("modifiers") or [])
    ]
    assert len(implicit) == expected
    assert all(name == class_import.rsplit(".", 1)[-1] for class_import, name in implicit)


def test_cells_java_anonymous_iterator_methods_are_not_members_of_the_collections() -> None:
    # upstream org/aspose/cells_foss/AutoFilter.java builds iterators with anonymous classes inside FilterColumnCollection
    # and FilterValueCollection; their hasNext()/next() are not methods of the collections (RD3-045: "cells/java 6")
    cells = _load("cells")
    collections = [
        t
        for t in cells["types"]
        if t["class_import"]
        in {
            "org.aspose.cells_foss.AutoFilter.FilterColumnCollection",
            "org.aspose.cells_foss.AutoFilter.FilterValueCollection",
            "org.aspose.cells_foss.ListColumnCollection",
        }
    ]
    assert len(collections) == 3
    for collection in collections:
        assert [m["name"] for m in collection["methods"] if m["name"] in ("hasNext", "next")] == []


def test_pdf_java_phantom_page_add_artifact_is_gone() -> None:
    # RD3-045 named ``Page.add(Artifact)``, a method of an anonymous class that was attributed to Page
    page = _type("pdf", "org.aspose.pdf.Page")
    assert [
        m for m in page["methods"] if m["name"] == "add" and [p["type"] for p in m["params"]] == ["Artifact"]
    ] == []
