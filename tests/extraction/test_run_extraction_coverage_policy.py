"""TC-520 (REQ-G3-008): the selection policy of run_extraction's cut.

A capped fixture must keep the types the library's own README names, must spend its slots on
public API rather than engine internals, and must count types and functions separately. Offline
and deterministic: the artifacts are synthetic dicts, the committed fixtures and furnished pages are
only READ, and nothing is skipped.
"""

from __future__ import annotations

import inspect
import json
import math
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from foss_mcp.extraction import run_extraction

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
FURNISHED = FIXTURES / "furnished"
VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"


# --------------------------------------------------------------------------- synthetic artifacts


def _entry(
    name: str,
    *,
    file: str | None = None,
    class_import: str | None = None,
    refs: tuple[str, ...] = (),
    members: int | None = None,
    kind: str = "class",
) -> dict[str, Any]:
    count = len(refs) if members is None else members
    methods = [
        {"name": f"m{index}", "params": [], "return_type": refs[index] if index < len(refs) else ""}
        for index in range(count)
    ]
    return {
        "name": name,
        "kind": kind,
        "file": file or f"pkg/{name.lower()}.py",
        "class_import": class_import or f"pkg.{name}",
        "bases": [],
        "methods": methods,
        "properties": [],
    }


def _artifact(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "source_repository": "acme/widgets",
        "source_commit": "a" * 40,
        "language": "python",
        "type_count": len(entries),
        "types": entries,
    }


def _ring(size: int = 1000) -> list[dict[str, Any]]:
    """*size* types, each referenced by exactly one other (centrality 1)."""
    return [
        _entry(f"Type{index:04d}", refs=(f"Type{(index + 1) % size:04d}",), file=f"pkg/t{index}.py")
        for index in range(size)
    ]


def _lonely(names: list[str]) -> list[dict[str, Any]]:
    """Zero-centrality public types: nothing references them, so centrality alone drops them."""
    return [_entry(name, refs=("int",), file=f"pkg/{name.lower()}.py") for name in names]


def _names(fixture: dict[str, Any]) -> list[str]:
    return [entry["name"] for entry in fixture["types"]]


def _old_reduce(artifact: dict[str, Any], max_types: int) -> dict[str, Any]:
    """The pure centrality selection, as it was before README names and demotion existed."""
    types = artifact["types"]
    scores = run_extraction._centrality_scores(types)
    ordered = sorted(
        range(len(types)),
        key=lambda index: (
            -scores[index],
            types[index].get("class_import") or types[index].get("name") or "",
        ),
    )
    reduced = [types[index] for index in ordered[:max_types]]
    return {
        **{key: value for key, value in artifact.items() if key != "types"},
        "reduced_type_count": len(reduced),
        "truncated": len(reduced) < len(types),
        "types": reduced,
    }


def _page(tmp_path: Path, body: str) -> Path:
    page = tmp_path / "_index.md"
    page.write_text(body, encoding="utf-8")
    return page


# --------------------------------------------------------------------------- readme_keep_names


def test_readme_keep_names_counts_fenced_and_inline(tmp_path: Path) -> None:
    page = _page(
        tmp_path,
        "Use `Document` and `Document.save()` here.\n"
        "```python\n"
        "doc = Document()\n"
        "editor = PdfFileEditor()\n"
        "```\n"
        "```python\n"
        "doc = Document()\n"
        "```\n",
    )
    names = run_extraction.readme_keep_names(page)
    # two fences plus two inline spans
    assert names["Document"] == 4
    # one fence, no span
    assert names["PdfFileEditor"] == 1
    assert "save" not in names  # a single inline span is not enough
    assert run_extraction.readme_keep_names(page) == names


def test_readme_keep_names_ignores_prose_and_shell_fences(tmp_path: Path) -> None:
    page = _page(
        tmp_path,
        "Prose mentions Widgetizer and Gadgetron in plain words, twice: Widgetizer, Gadgetron.\n"
        "```bash\npip install ShellOnly\n```\n"
        "```mermaid\nflowchart TD; MermaidOnly\n```\n"
        "```python\n# Commented(1)\nreal = Real('Quoted')  # Trailing\n```\n",
    )
    names = run_extraction.readme_keep_names(page)
    for absent in ("Widgetizer", "Gadgetron", "ShellOnly", "MermaidOnly", "Commented", "Quoted", "Trailing"):
        assert absent not in names
    assert "Real" in names


def test_readme_keep_names_needs_a_fence_or_two_mentions(tmp_path: Path) -> None:
    page = _page(
        tmp_path,
        "One span `OnceOnly` here. Two spans `Twice` and again `Twice` there.\n"
        "A long span `" + "x" * 130 + " TooLong` is not a span.\n"
        "```java\nFenced fenced2;\n```\n",
    )
    names = run_extraction.readme_keep_names(page)
    assert "OnceOnly" not in names
    assert names["Twice"] == 2
    assert names["Fenced"] == 1
    assert "TooLong" not in names


def test_readme_keep_names_reads_front_matter_strings(tmp_path: Path) -> None:
    page = _page(
        tmp_path,
        "---\ntitle: Demo\nsingle:\n  block:\n  - title: One\n    content: |-\n"
        "      ```csharp\n      var x = new FrontMatterType();\n      ```\n---\nBody.\n",
    )
    assert "FrontMatterType" in run_extraction.readme_keep_names(page)


# --------------------------------------------------------------------------- reduce_fixture: README tier


def test_reduce_fixture_unchanged_without_readme_names() -> None:
    entries = []
    for index in range(500):
        refs = tuple(f"T{(index * 7 + step) % 500:03d}" for step in range(index % 4))
        entries.append(_entry(f"T{index:03d}", refs=refs, class_import=f"pkg.mod{index % 9}.T{index:03d}"))
    artifact = _artifact(entries)
    for cap in (1, 37, 300, 500, 900):
        plain = run_extraction.reduce_fixture(artifact, max_types=cap)
        assert plain == run_extraction.reduce_fixture(artifact, max_types=cap, readme_names=None)
        assert plain == run_extraction.reduce_fixture(artifact, max_types=cap, readme_names={})
        assert json.dumps(plain, sort_keys=True, indent=2) == json.dumps(
            _old_reduce(artifact, cap), sort_keys=True, indent=2
        )


def test_readme_names_are_kept_before_centrality() -> None:
    artifact = _artifact(_ring() + _lonely(["PdfFileEditor", "TextBuilder"]))
    assert "PdfFileEditor" not in _names(run_extraction.reduce_fixture(artifact, max_types=300))
    kept = run_extraction.reduce_fixture(
        artifact, max_types=300, readme_names={"PdfFileEditor": 3, "TextBuilder": 1}
    )
    assert {"PdfFileEditor", "TextBuilder"} <= set(_names(kept))
    assert len(kept["types"]) == 300
    assert kept["truncated"] is True


def test_readme_names_are_capped_by_the_fraction() -> None:
    assert run_extraction.README_KEEP_FRACTION == 0.7
    named = [f"Named{index:02d}" for index in range(30)]
    artifact = _artifact(_ring(200) + _lonely(named))
    kept = run_extraction.reduce_fixture(artifact, max_types=10, readme_names=dict.fromkeys(named, 1))
    assert len(kept["types"]) == 10
    assert sum(1 for name in _names(kept) if name.startswith("Named")) == math.ceil(10 * 0.7) == 7
    assert sum(1 for name in _names(kept) if name.startswith("Type")) == 3


def test_readme_names_rank_by_mention_count() -> None:
    named = [f"Named{index:02d}" for index in range(10)]
    artifact = _artifact(_ring(200) + _lonely(named))
    counts = {name: index + 1 for index, name in enumerate(named)}  # Named09 is the most mentioned
    kept = run_extraction.reduce_fixture(artifact, max_types=10, readme_names=counts)
    kept_named = {name for name in _names(kept) if name.startswith("Named")}
    assert kept_named == {f"Named{index:02d}" for index in range(3, 10)}  # the seven most mentioned


def test_examples_keep_precedence_over_readme_names() -> None:
    used = [f"Used{index}" for index in range(8)]
    only_readme = [f"Readme{index}" for index in range(5)]
    artifact = _artifact(_ring(200) + _lonely(used + only_readme))
    kept = run_extraction.reduce_fixture(
        artifact,
        max_types=10,
        must_keep=frozenset(used),
        readme_names=dict.fromkeys(only_readme, 9),
    )
    names = set(_names(kept))
    assert set(used) <= names  # examples keep their (unchanged) precedence, above the README budget
    assert not names & set(only_readme)  # eight chosen already exceeds the seven-slot README budget


def test_unknown_readme_names_change_nothing() -> None:
    artifact = _artifact(_ring())
    assert run_extraction.reduce_fixture(artifact) == run_extraction.reduce_fixture(
        artifact, readme_names={"NotAType": 5, "AlsoNot": 1}
    )


# --------------------------------------------------------------------------- README recall on real fixtures

UNTRUNCATED = [
    "3d_java",
    "slides_net",
    "cells_java",
    "cells_rust",
    "page_python",
    "words_python",
    "cells_python",
]


def _strong_names(pilot: str) -> tuple[dict[str, Any], Path, dict[str, int], list[str]]:
    fixture = json.loads((FIXTURES / pilot / "api_surface.json").read_text(encoding="utf-8"))
    page = FURNISHED / pilot / "pages" / "_index.md"
    readme = run_extraction.readme_keep_names(page)
    defined = {e.get("name") for e in fixture["types"] if not run_extraction._is_test_entry(e)}
    return fixture, page, readme, sorted(name for name in readme if name in defined)


def _recall(kept: dict[str, Any], strong: list[str]) -> float:
    names = {entry.get("name") for entry in kept["types"]}
    return sum(1 for name in strong if name in names) / len(strong)


@pytest.mark.parametrize("pilot", UNTRUNCATED)
def test_readme_recall_on_committed_untruncated_fixtures(pilot: str) -> None:
    fixture, page, readme, strong = _strong_names(pilot)
    assert strong, f"{pilot}: the page names no type the fixture defines"
    kept = run_extraction.reduce_fixture(
        fixture,
        max_types=2 * len(strong),
        must_keep=run_extraction.names_used_in_examples(page),
        readme_names=readme,
    )
    assert _recall(kept, strong) >= 0.9


def test_example_only_selection_misses_the_readme_names() -> None:
    """The defect: with only the examples' types protected, the cap drops what the README names."""
    below = 0
    for pilot in UNTRUNCATED:
        fixture, page, _readme, strong = _strong_names(pilot)
        kept = run_extraction.reduce_fixture(
            fixture, max_types=2 * len(strong), must_keep=run_extraction.names_used_in_examples(page)
        )
        below += _recall(kept, strong) < 0.9
    assert below >= 5


# --------------------------------------------------------------------------- demotion of internals


@pytest.mark.parametrize(
    ("file", "class_import", "internal"),
    [
        ("src/org/aspose/pdf/engine/xfa/X.java", "org.aspose.pdf.engine.xfa.X", True),
        ("include/internal/a.hpp", "", True),
        ("Aspose.Slides.Foss/Internal/Opc/P.cs", "Aspose.Slides.Foss.Internal.Opc.P", True),
        ("pkg/generated/m.py", "pkg.generated.M", True),
        ("src/vendor/x.ts", "", True),
        ("src/xmldom.d.ts", "", True),
        ("pkg/_internal/m.py", "pkg._internal.M", True),
        ("pkg/m.py", "a.detail.b", True),
        ("pkg/m.py", "ns::detail::Helper", True),
        ("node_modules/lib/index.js", "", True),
        ("third_party/lib/a.cc", "", True),
        ("src\\3rdparty\\a.cc", "", True),
        ("pkg/impl/a.py", "", True),
        ("pkg/m.py", "Pkg.ENGINE.Thing", True),
        ("pkg/engine_room_notes.py", "pkg.engine_room_notes.Notes", False),
        ("src/Aspose.Pdf/Document.cs", "Aspose.Pdf.Document", False),
        ("pkg/core.py", "pkg.core.Core", False),
        ("src/internals_overview.md", "", False),
        ("", "", False),
    ],
)
def test_internal_markers(file: str, class_import: str, internal: bool) -> None:
    entry = {"name": "X", "file": file, "class_import": class_import}
    assert run_extraction._is_internal_entry(entry) is internal


def test_internal_groups_fill_only_leftover_slots() -> None:
    public = _lonely([f"Public{index}" for index in range(5)])
    internal = [
        _entry(
            f"Core{index}",
            refs=("int",),
            file=f"pkg/engine/c{index}.py",
            class_import=f"pkg.engine.Core{index}",
        )
        for index in range(5)
    ]
    # make every internal type far more central than any public one
    users = [
        _entry(f"User{index}", refs=tuple(f"Core{n}" for n in range(5)), file=f"pkg/u{index}.py")
        for index in range(4)
    ]
    artifact = _artifact(internal + public + users)
    kept = run_extraction.reduce_fixture(artifact, max_types=9)
    names = _names(kept)
    assert all(f"Public{index}" in names for index in range(5))
    assert sum(1 for name in names if name.startswith("Core")) == 0
    roomy = run_extraction.reduce_fixture(artifact, max_types=14)
    assert sum(1 for name in _names(roomy) if name.startswith("Core")) == 5  # leftover slots


def test_group_with_a_public_shell_is_not_internal() -> None:
    definition = _entry(
        "Cart", class_import="pkg.generated.Cart", file="pkg/generated/cart.py", refs=("int",)
    )
    shell = _entry("Cart", class_import="pkg.Cart", file="pkg/__init__.py", members=0)
    types = [definition, shell]
    groups = run_extraction._alias_groups(types)
    assert groups == [[0, 1]]
    assert run_extraction._is_internal_entry(definition)
    assert not run_extraction._is_internal_group(types, groups[0])
    alone = [definition]
    assert run_extraction._is_internal_group(alone, run_extraction._alias_groups(alone)[0])


def test_named_internal_group_is_still_kept() -> None:
    internal = _entry("Secret", file="pkg/engine/s.py", class_import="pkg.engine.Secret", refs=("int",))
    artifact = _artifact(_ring(50) + [internal])
    assert "Secret" not in _names(run_extraction.reduce_fixture(artifact, max_types=5))
    assert "Secret" in _names(run_extraction.reduce_fixture(artifact, max_types=5, must_keep={"Secret"}))
    assert "Secret" in _names(
        run_extraction.reduce_fixture(artifact, max_types=5, readme_names={"Secret": 2})
    )


def test_pdf_java_fixture_cap_100_keeps_no_engine_type() -> None:
    fixture = json.loads((FIXTURES / "pdf_java" / "api_surface.json").read_text(encoding="utf-8"))
    kept = run_extraction.reduce_fixture(fixture, max_types=100)
    engine = [e for e in kept["types"] if "engine" in str(e.get("class_import")).lower().split(".")]
    assert engine == []  # 108 public types exist; before this card the cut kept 83 engine types
    assert len(kept["types"]) == 100


def test_pdf_cpp_fixture_cap_150_keeps_no_include_internal_type() -> None:
    fixture = json.loads((FIXTURES / "pdf_cpp" / "api_surface.json").read_text(encoding="utf-8"))
    kept = run_extraction.reduce_fixture(fixture, max_types=150)
    internal = [e for e in kept["types"] if "include/internal/" in str(e.get("file")).replace("\\", "/")]
    assert internal == []  # before this card the cut kept 5
    assert len(kept["types"]) == 150


# --------------------------------------------------------------------------- symbol counts, caps, wiring


def test_symbol_counts_separate_types_from_functions() -> None:
    types = [
        {"name": "A", "kind": "type_spec", "file": "a.go"},
        {"name": "B", "kind": "type_spec", "file": "b.go"},
        {"name": "C", "kind": "type_spec", "file": "c.go"},
        {"name": "F", "kind": "function", "file": "a.go", "receiver_type": "A"},
        {"name": "G", "kind": "function", "file": "b.go", "receiver_type": "B"},
        {"name": "TestF", "kind": "function", "file": "a_test.go"},
    ]
    assert run_extraction.summarize_symbols(types) == {"types": 3, "functions": 2}
    assert run_extraction.summarize_symbols([]) == {"types": 0, "functions": 0}


def test_recommended_max_types_are_valid() -> None:
    values = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
    pilots = {(p["family"], p["platform"]): p for p in values["ingestion"]["pilots"]}
    assert len(run_extraction.RECOMMENDED_MAX_TYPES) == 13
    for key, cap in run_extraction.RECOMMENDED_MAX_TYPES.items():
        family, platform = key.split("/")
        assert (family, platform) in pilots, key
        assert 300 <= cap <= 1250, key
        fixture_dir = Path(pilots[(family, platform)]["apiSurface"]).parent.name
        fixture = json.loads((FIXTURES / fixture_dir / "api_surface.json").read_text(encoding="utf-8"))
        assert cap <= fixture["type_count"], f"{key}: {cap} above the {fixture['type_count']} types upstream"


def test_main_resolves_the_recommended_cap_and_feeds_readme_names() -> None:
    """AGENTS.md: a function nobody calls is not a fix. main() must call the new code."""
    source = inspect.getsource(run_extraction.main)
    assert "RECOMMENDED_MAX_TYPES.get(" in source
    assert "readme_keep_names(" in source
    assert "readme_names=" in source
    assert "names_used_in_examples(" in source


def test_extractor_version_is_a_defined_integer_of_at_least_two() -> None:
    assert isinstance(run_extraction.EXTRACTOR_VERSION, int)
    assert not isinstance(run_extraction.EXTRACTOR_VERSION, bool)
    assert run_extraction.EXTRACTOR_VERSION >= 2
    text = (REPO_ROOT / "src" / "foss_mcp" / "extraction" / "run_extraction.py").read_text(encoding="utf-8")
    assert re.search(r"^EXTRACTOR_VERSION = \d+$", text, re.MULTILINE)
    assert re.search(r"^README_KEEP_FRACTION = 0\.7$", text, re.MULTILINE)


def test_extract_pinned_repository_writes_the_new_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "src_repo"
    package = source / "src" / "widgets"
    package.mkdir(parents=True)
    (source / "pyproject.toml").write_text('[project]\nname = "widgets"\n', encoding="utf-8")
    (package / "__init__.py").write_text("from .core import Gadget, make\n", encoding="utf-8")
    (package / "core.py").write_text(
        "class Gadget:\n    def build(self):\n        return None\n\n\ndef make(name):\n    return name\n",
        encoding="utf-8",
    )
    (package / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("platform: python\nfamily: widgets\nrepository: acme/widgets\n", encoding="utf-8")

    def _fake_clone(_repository: str, dest: Path) -> str:
        shutil.copytree(source, dest, dirs_exist_ok=True)
        return "b" * 40

    monkeypatch.setattr(run_extraction, "clone_shallow", _fake_clone)
    artifact = run_extraction.extract_pinned_repository(manifest)
    assert artifact["extractor_version"] == run_extraction.EXTRACTOR_VERSION
    counts = artifact["symbol_counts"]
    assert counts == run_extraction.summarize_symbols(artifact["types"])
    assert counts["types"] >= 1 and counts["functions"] >= 1  # Gadget and make (and their re-exports)
    assert counts["types"] + counts["functions"] == len(artifact["types"])
    health = artifact["parse_failures"]
    assert health["engine"] == "ast"
    assert (health["failed"], health["total"]) == (1, 3)
    assert health["examples"][0].startswith("src/widgets/broken.py:1: ")
    # the data contract adds keys and changes nothing else
    assert artifact["type_count"] == len(artifact["types"])
    assert artifact["source_commit"] == "b" * 40
