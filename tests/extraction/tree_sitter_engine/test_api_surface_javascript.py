"""``api_surface.extract_api_surface`` derives a real namespace signal for JavaScript.

TC-315: the per-language ``canonical_namespace``/``class_import`` derivation (added
incrementally for cpp, java, csharp, python, typescript, and rust) had no branch at all
for ``javascript`` -- the language this project's own "nodejs" pilots actually extract
under (``run_extraction._LANGUAGE_BY_PLATFORM["nodejs"] == "javascript"``, confirmed live
against the real, pinned jmap/nodejs repository). Without a namespace signal, two
genuinely distinct same-bare-name classes from different files collided into one
``consolidate_classes()`` dedup group as if they were the same type, and the "stub vs
full" heuristic (Category 2: a class with ``bases`` is assumed more complete than one
without) then discarded the real, full implementation in favor of a near-empty wrapper
-- exactly the shape of the real jmap/nodejs repository's ``JmapClient``:
``src/client-core.js`` declares the real, full implementation (``bases=[]``), and
``src/index.js`` separately declares ``class JmapClient extends CoreClient {}`` via an
aliased import (``import { JmapClient as CoreClient } from "./client-core.js"``), which
defeats ``consolidate_classes``' own Category 3 shim-detection (it only matches a base
literally named after the dropped duplicate).

These tests reproduce that exact shape with a synthetic two-file fixture (the real,
pinned repository is network-dependent and is exercised separately as part of this
card's own live verification, not committed here), and separately prove the pre-existing
typescript/python/rust branches are completely unchanged by this addition.
"""

from __future__ import annotations

from pathlib import Path

from tree_sitter_language_pack import get_parser

from foss_mcp.extraction.tree_sitter_engine import api_surface

# Mirrors the real jmap/nodejs shape: a real, full implementation in one file, and a
# near-empty wrapper sharing the same bare class name in another, linked via an aliased
# import that defeats the shim-detection heuristic.
JS_CLIENT_CORE = """
export class JmapClient {
  constructor(options) {
    this.options = options;
  }

  async connect() {
    return true;
  }

  async sendRequest(req) {
    return req;
  }

  async echo(args) {
    return args;
  }

  async uploadBlob(accountId, content, contentType) {
    return { accountId, content, contentType };
  }

  async downloadBlob(accountId, blobId, type, name) {
    return { accountId, blobId, type, name };
  }
}
"""

JS_INDEX_WRAPPER = """
import { JmapClient as CoreClient } from "./client-core.js";

export class JmapClient extends CoreClient {}
"""


def _write_jmap_nodejs_fixture(tmp_path: Path) -> Path:
    (tmp_path / "package.json").write_text('{"name": "jmap"}\n', encoding="utf-8")
    package = tmp_path / "src"
    package.mkdir()
    (package / "client-core.js").write_text(JS_CLIENT_CORE, encoding="utf-8")
    (package / "index.js").write_text(JS_INDEX_WRAPPER, encoding="utf-8")
    return package


def test_javascript_same_name_classes_in_different_files_derive_distinct_namespaces(
    tmp_path: Path,
) -> None:
    package = _write_jmap_nodejs_fixture(tmp_path)
    types, *_ = api_surface.extract_api_surface(get_parser("javascript"), "javascript", package, tmp_path, "jmap")

    jmap_clients = [t for t in types if t.get("name") == "JmapClient"]

    # The exact TC-315 bug: before the fix, only one `JmapClient` entry survived
    # `consolidate_classes()` -- the near-empty wrapper -- because neither entry had a
    # namespace signal, so both landed in the same dedup group and the "has bases = more
    # complete" heuristic kept the wrapper (bases=["CoreClient"]) and discarded the real
    # implementation (bases=[]).
    assert len(jmap_clients) == 2

    by_file = {c["file"]: c for c in jmap_clients}
    core = by_file["src/client-core.js"]
    index = by_file["src/index.js"]

    # The two entries must now derive DIFFERENT canonical_namespace/class_import values
    # so they no longer collide in the same consolidate_classes dedup group at all.
    assert core.get("canonical_namespace") != index.get("canonical_namespace")
    assert core.get("class_import") != index.get("class_import")

    # The file-path-derived module path for client-core.js is NOT collapsed (it is not
    # named "index"), mirroring the typescript branch's own convention exactly.
    assert core.get("canonical_namespace") == "client-core"
    assert core.get("class_import") == "client-core.JmapClient"

    # The real, full implementation (with its real methods) survives extraction rather
    # than being discarded.
    assert core["bases"] == []
    method_names = {m["name"] for m in core.get("methods", [])}
    assert {"connect", "sendRequest", "echo", "uploadBlob", "downloadBlob"} <= method_names

    # The near-empty wrapper also survives (as a distinct entry), unmerged.
    assert index["bases"] == ["CoreClient"]


def test_javascript_canonical_namespace_derived_from_nested_file_path(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name": "widget"}\n', encoding="utf-8")
    package = tmp_path / "src"
    (package / "models").mkdir(parents=True)
    (package / "models" / "widget.js").write_text(
        """
export class Widget {
  constructor(name) {
    this.name = name;
  }
}
""",
        encoding="utf-8",
    )
    types, *_ = api_surface.extract_api_surface(get_parser("javascript"), "javascript", package, tmp_path, "widget")
    by_name = {t["name"]: t for t in types}

    # module_path derived from the file's path relative to pkg_root, stripping the .js
    # extension -- mirroring the typescript/python branches' own convention exactly.
    assert by_name["Widget"]["canonical_namespace"] == "models.widget"
    assert by_name["Widget"]["class_import"] == "models.widget.Widget"


def test_javascript_index_file_segment_is_collapsed_from_module_path(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name": "widget"}\n', encoding="utf-8")
    package = tmp_path / "src"
    package.mkdir()
    (package / "index.js").write_text(
        """
export class Standalone {
  constructor() {}
}
""",
        encoding="utf-8",
    )
    types, *_ = api_surface.extract_api_surface(get_parser("javascript"), "javascript", package, tmp_path, "widget")
    by_name = {t["name"]: t for t in types}

    # The lone "index" path segment collapses to an empty module_path (exactly as the
    # typescript branch already collapses "index" for .ts/.tsx), so no
    # canonical_namespace/class_import is set at all here -- there is nothing to prefix.
    assert "canonical_namespace" not in by_name["Standalone"]
    assert "class_import" not in by_name["Standalone"]


def test_typescript_branch_is_unaffected_by_the_new_javascript_branch(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name": "widget"}\n', encoding="utf-8")
    package = tmp_path / "src"
    package.mkdir()
    (package / "shapes.ts").write_text(
        """
export class Shape {
  area(): number {
    return 0;
  }
}
""",
        encoding="utf-8",
    )
    types, *_ = api_surface.extract_api_surface(get_parser("typescript"), "typescript", package, tmp_path, "widget")
    by_name = {t["name"]: t for t in types}

    # Unchanged from before this card: the typescript branch (an earlier `elif` in the
    # same chain) still derives its own file-path-based module_path exactly as before --
    # the new `elif language == "javascript":` branch added after it is never reached
    # for a typescript extraction.
    assert by_name["Shape"]["canonical_namespace"] == "shapes"
    assert by_name["Shape"]["class_import"] == "shapes.Shape"


def test_python_branch_is_unaffected_by_the_new_javascript_branch(tmp_path: Path) -> None:
    package = tmp_path / "widget"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "core.py").write_text(
        """
class Widget:
    def __init__(self, name):
        self.name = name
""",
        encoding="utf-8",
    )
    types, *_ = api_surface.extract_api_surface(get_parser("python"), "python", package, tmp_path, "widget")
    by_name = {t["name"]: t for t in types}

    # Unchanged from before this card: the python branch derives module_path (relative
    # to pkg_root, i.e. the `widget` package dir itself) exactly as before.
    assert by_name["Widget"]["canonical_namespace"] == "core"
    assert by_name["Widget"]["class_import"] == "core.Widget"


def test_rust_branch_is_unaffected_by_the_new_javascript_branch(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "widget"\nversion = "0.1.0"\n', encoding="utf-8")
    package = tmp_path / "src"
    package.mkdir()
    (package / "worksheet.rs").write_text(
        """
pub struct Worksheet {
    pub name: String,
}
""",
        encoding="utf-8",
    )
    types, *_ = api_surface.extract_api_surface(get_parser("rust"), "rust", package, tmp_path, "widget")
    by_name = {t["name"]: t for t in types}

    # Unchanged from before this card: the rust branch (which comes AFTER the new
    # javascript branch in the elif chain) derives module_path exactly as before.
    assert by_name["Worksheet"]["canonical_namespace"] == "worksheet"
    assert by_name["Worksheet"]["class_import"] == "worksheet::Worksheet"
