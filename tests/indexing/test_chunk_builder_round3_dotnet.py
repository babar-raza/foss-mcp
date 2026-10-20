"""G3/TC-558 (REQ-G3-009): the rendered chunk of a .NET type states what the C# extraction now serves.

The oracle is a hand-written C# source (the answer follows from the C# language rules) extracted offline and rendered
through the production chunk builder; no dotnet, no SDK, no network. Two tests read the regenerated 3d/net fixture.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

from tree_sitter_language_pack import get_parser

from foss_mcp.extraction.tree_sitter_engine import api_surface
from foss_mcp.indexing.chunk_builder import _format_type_text, build_chunks_from_api_surface

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

SOURCE = """
using Image = SkiaSharp.SKBitmap;
namespace Lib
{
    public struct Vec
    {
        public double X, Y;
        public static Vec operator +(Vec a, Vec b) => a;
        public static implicit operator double(Vec v) => v.X;
    }
    public class Store<T> where T : struct
    {
        public event System.EventHandler Saved;
        public TOut Convert<TOut>(Image source) where TOut : class => default;
    }
    public delegate void Done(int code);
    public record Pair(string Left, int Right);
    public class Plain { }
}
"""


@functools.cache
def _types() -> tuple[dict[str, Any], ...]:
    import tempfile

    with tempfile.TemporaryDirectory() as root:
        package = Path(root) / "src" / "Lib"
        package.mkdir(parents=True)
        (package / "A.cs").write_text(SOURCE, encoding="utf-8")
        types, *_ = api_surface.extract_api_surface(
            get_parser("csharp"), "csharp", package, Path(root), "lib"
        )
    return tuple(types)


def _text(name: str) -> str:
    return _format_type_text(next(t for t in _types() if t["name"] == name))


def test_an_operator_and_a_conversion_render_as_signature_lines() -> None:
    text = _text("Vec")
    assert "  - operator +(a: Vec, b: Vec) -> Vec [static]" in text.splitlines()
    assert "  - implicit operator double(v: Vec) -> double [static]" in text.splitlines()


def test_a_public_mutable_field_renders_as_a_writable_property() -> None:
    lines = _text("Vec").splitlines()
    assert "  - X: double (writable)" in lines and "  - Y: double (writable)" in lines


def test_a_generic_type_renders_its_parameters_and_constraint() -> None:
    assert "Generics: <T> where T : struct" in _text("Store").splitlines()


def test_a_generic_method_renders_its_parameters_after_the_signature_and_resolves_the_alias() -> None:
    line = next(ln for ln in _text("Store").splitlines() if ln.startswith("  - Convert("))
    assert line == "  - Convert(source: SkiaSharp.SKBitmap) -> TOut  [generic <TOut> where TOut : class]"


def test_an_event_renders_with_its_event_mark_and_no_writability() -> None:
    assert "  - Saved: System.EventHandler [event]" in _text("Store").splitlines()


def test_a_delegate_renders_as_a_type_with_its_invoke_signature() -> None:
    text = _text("Done")
    assert text.startswith("FQN: Lib.Done\nKind: delegate_declaration")
    assert "  - Invoke(code: int) -> void" in text.splitlines()


def test_a_record_renders_its_constructor_deconstruct_and_components() -> None:
    lines = _text("Pair").splitlines()
    assert "  - Pair(Left: string, Right: int) [implicit]" in lines
    assert "  - Deconstruct(Left: out string, Right: out int) -> void [implicit]" in lines
    assert "  - Left: string [init] (writable)" in lines


def test_an_implicit_default_constructor_renders_marked_implicit() -> None:
    assert "  - Plain() [implicit]" in _text("Plain").splitlines()


def test_foo_and_foo_of_t_render_as_two_chunks_with_distinct_fqns() -> None:
    fixture = json.loads((FIXTURES / "3d_net" / "api_surface.json").read_text(encoding="utf-8"))
    chunks = build_chunks_from_api_surface(fixture, title="3d/net", max_types=len(fixture["types"]))
    fqns = [line for chunk in chunks for line in chunk.text.splitlines() if line.startswith("FQN: ")]
    assert "FQN: Aspose.ThreeD.Entities.TriMesh" in fqns
    assert "FQN: Aspose.ThreeD.Entities.TriMesh<T>" in fqns


def test_the_regenerated_3d_chunk_of_vector3_lists_the_plus_operator() -> None:
    fixture = json.loads((FIXTURES / "3d_net" / "api_surface.json").read_text(encoding="utf-8"))
    vector3 = next(t for t in fixture["types"] if t["name"] == "Vector3")
    assert (
        "  - operator +(lhs: Vector3, rhs: Vector3) -> Vector3 [static]"
        in _format_type_text(vector3).splitlines()
    )
