"""TC-394: which types survive run_extraction's cut, and extraction at an exact commit.

Everything here is offline and deterministic. The committed furnished pages are read as they
are; the artifacts are synthetic dicts; the git tests build a tiny repository in ``tmp_path``
and point ``run_extraction.repository_url`` at it, so no test touches the network. ``git`` is
required (declared by the card) and nothing is skipped.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from foss_mcp.extraction import run_extraction

REPO_ROOT = Path(__file__).resolve().parents[2]
FURNISHED = REPO_ROOT / "tests" / "fixtures" / "furnished"


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
    """One artifact entry. ``refs`` are type names its methods return (what centrality counts);
    ``members`` is the method count (defaults to one per ref, so a ref-free entry is a shell)."""
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


def _thousand_types() -> list[dict[str, Any]]:
    """1,000 types in a ring (each referenced by exactly one other, score 1), plus a
    zero-centrality ``Presentation`` nobody references."""
    ring = [
        _entry(f"Type{index:04d}", refs=(f"Type{(index + 1) % 999:04d}",), file=f"pkg/t{index}.py")
        for index in range(999)
    ]
    ring.append(_entry("Presentation", refs=("int",), file="pkg/presentation.py"))
    return ring


def _names(fixture: dict[str, Any]) -> list[str]:
    return [entry["name"] for entry in fixture["types"]]


def _old_reduce(artifact: dict[str, Any], max_types: int) -> dict[str, Any]:
    """The selection exactly as it was before TC-394 (a copy, to pin byte-identical output)."""
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


# --------------------------------------------------------------------------- names_used_in_examples


def test_names_used_in_examples_slides_python_page_names_presentation() -> None:
    names = run_extraction.names_used_in_examples(FURNISHED / "slides_python" / "pages" / "_index.md")
    assert "Presentation" in names
    assert "SaveFormat" in names  # `SaveFormat.PPTX`


def test_names_used_in_examples_words_net_page_names_document_builder() -> None:
    names = run_extraction.names_used_in_examples(FURNISHED / "words_net" / "pages" / "_index.md")
    assert "DocumentBuilder" in names
    assert "Document" in names


@pytest.mark.parametrize("pilot", ["slides_python", "words_net", "pdf_net", "cells_rust"])
def test_names_used_in_examples_are_camelcase_only_and_deterministic(pilot: str) -> None:
    page = FURNISHED / pilot / "pages" / "_index.md"
    names = run_extraction.names_used_in_examples(page)
    assert names, f"{pilot}: the page has example code but no names were read"
    for name in names:
        assert re.fullmatch(r"[A-Z]\w*[a-z]\w*", name), f"{name!r} is not a CamelCase identifier"
    assert run_extraction.names_used_in_examples(page) == names


def test_names_used_in_examples_reads_every_reference_form(tmp_path: Path) -> None:
    page = tmp_path / "_index.md"
    page.write_text(
        "---\n"
        "title: Demo\n"
        "single:\n"
        "  block:\n"
        "  - title: One\n"
        "    content: |-\n"
        "      ```python\n"
        "      import acme.widgets as aw\n"
        "      import acme.other\n"
        "      from acme import tools\n"
        "      a = Called(1)\n"
        "      b = Receiver.load()\n"
        "      c = aw.AliasOnly\n"
        "      d = acme.other.Qualified()\n"
        "      e = tools.FromImport\n"
        "      def f(x: Annotated) -> None: ...\n"
        "      ```\n"
        "  - title: Two\n"
        "    content: |-\n"
        "      ```csharp\n"
        "      var g = new Constructed(); var h = new Ns.Deep.Nested();\n"
        "      Cpp::Scoped s; List<Generic> l; Map<Key, Value> m;\n"
        "      class Derived : BaseClass { }\n"
        '      doc.Save("Quoted.docx"); // Commented(1)\n'
        "      /* Blocked(2) */\n"
        "      ```\n"
        "  - title: Three\n"
        "    content: |-\n"
        "      ```bash\n"
        "      pip install ShellOnly(1)\n"
        "      ```\n"
        "      ```mermaid\n"
        "      flowchart TD; MermaidOnly(a)\n"
        "      ```\n"
        "---\n"
        "Body text mentions Prose(1) outside any fence.\n"
        "```java\n"
        "BodyBlock.run();\n"
        "```\n",
        encoding="utf-8",
    )
    names = run_extraction.names_used_in_examples(page)
    assert {
        "Called",
        "Receiver",
        "AliasOnly",
        "Qualified",
        "FromImport",
        "Annotated",
        "Constructed",
        "Nested",
        "Scoped",
        "Generic",
        "Key",
        "Value",
        "BaseClass",
        "BodyBlock",
    } <= names
    # not code, or not a class reference: a method called on an object, comments, strings,
    # shell and diagram fences, prose outside a fence
    for absent in ("Save", "Quoted", "Commented", "Blocked", "ShellOnly", "MermaidOnly", "Prose", "docx"):
        assert absent not in names
    assert all(name[0].isupper() for name in names)


# --------------------------------------------------------------------------- reduce_fixture: must_keep


def test_must_keep_retains_a_zero_centrality_type_the_cap_would_drop() -> None:
    artifact = _artifact(_thousand_types())
    assert "Presentation" not in _names(run_extraction.reduce_fixture(artifact))
    kept = run_extraction.reduce_fixture(artifact, max_types=300, must_keep=frozenset({"Presentation"}))
    assert "Presentation" in _names(kept)
    assert len(kept["types"]) == 300
    assert kept["reduced_type_count"] == 300
    assert kept["truncated"] is True
    assert kept["type_count"] == 1000


def test_must_keep_never_exceeds_the_cap_and_keeps_in_deterministic_order() -> None:
    entries = [_entry(f"Used{index}", refs=("int",), file=f"pkg/u{index}.py") for index in range(5)]
    entries += [_entry(f"Hub{index}", refs=("Hub0",), file=f"pkg/h{index}.py") for index in range(1, 6)]
    artifact = _artifact(entries)
    must = frozenset(f"Used{index}" for index in range(5))
    first = run_extraction.reduce_fixture(artifact, max_types=3, must_keep=must)
    assert run_extraction.reduce_fixture(artifact, max_types=3, must_keep=must) == first
    assert len(first["types"]) == 3
    assert set(_names(first)) <= must  # the budget went to the examples' types, none to Hub


def test_must_keep_with_unknown_names_changes_nothing() -> None:
    artifact = _artifact(_thousand_types())
    assert run_extraction.reduce_fixture(artifact) == run_extraction.reduce_fixture(
        artifact, must_keep=frozenset({"NotAType"})
    )


def test_selection_without_extras_is_byte_identical_to_the_previous_algorithm() -> None:
    entries = []
    for index in range(500):
        refs = tuple(f"T{(index * 7 + step) % 500:03d}" for step in range(index % 4))
        entries.append(_entry(f"T{index:03d}", refs=refs, class_import=f"pkg.mod{index % 9}.T{index:03d}"))
    artifact = _artifact(entries)
    for cap in (1, 37, 300, 500, 900):
        new = run_extraction.reduce_fixture(artifact, max_types=cap)
        old = _old_reduce(artifact, cap)
        assert json.dumps(new, sort_keys=True, indent=2) == json.dumps(old, sort_keys=True, indent=2)


# --------------------------------------------------------------------------- reduce_fixture: tests


@pytest.mark.parametrize(
    "path",
    [
        "pkg/test_widget.py",
        "pkg/widget_test.py",
        "cmd/widget_test.go",
        "src/widget.test.ts",
        "src/widget.spec.ts",
        "src/widget.test.js",
        "src/widget.spec.js",
        "lib/tests/helpers.py",
        "lib/test/Helpers.java",
        "src/__tests__/widget.ts",
    ],
)
def test_test_source_entries_are_never_selected(path: str) -> None:
    artifact = _artifact([_entry("Real", refs=("int",)), _entry("TestOnly", file=path, refs=("int",))])
    assert _names(run_extraction.reduce_fixture(artifact)) == ["Real"]


def test_test_source_entries_do_not_count_against_the_budget_or_the_score() -> None:
    entries = [
        _entry("Helper", refs=("int",)),
        _entry("User", refs=("Helper",)),
        _entry("Lonely", refs=("int",)),
        # three test-file types that all reference Lonely: they must not make it central
        *[_entry(f"Spec{i}", file=f"tests/test_{i}.py", refs=("Lonely",)) for i in range(3)],
    ]
    artifact = _artifact(entries)
    fixture = run_extraction.reduce_fixture(artifact, max_types=1)
    assert _names(fixture) == ["Helper"]  # Lonely's three test-file references were ignored
    assert fixture["type_count"] == len(entries)  # the count before any drop
    roomy = run_extraction.reduce_fixture(artifact, max_types=3)
    assert sorted(_names(roomy)) == ["Helper", "Lonely", "User"]  # specs took no slot
    assert roomy["reduced_type_count"] == 3
    assert roomy["truncated"] is True  # three entries were left out


# --------------------------------------------------------------------------- reduce_fixture: shells


def _shell(name: str, *, package: str = "pkg", file: str | None = None) -> dict[str, Any]:
    return _entry(
        name,
        file=file or f"{package.replace('.', '/')}/__init__.py",
        class_import=f"{package}.{name}",
        members=0,
    )


def test_a_stub_and_its_definition_are_kept_together_and_counted_once() -> None:
    entries = [
        _shell("Cart"),
        _entry("Cart", class_import="pkg.core.Cart", file="pkg/core.py", refs=("int", "int")),
        _entry("UserA", refs=("Cart",), file="pkg/a.py"),
        _entry("UserB", refs=("Cart",), file="pkg/b.py"),
        _entry("Other", refs=("int",), file="pkg/other.py"),
        _entry("Other2", refs=("int",), file="pkg/other2.py"),
    ]
    two = run_extraction.reduce_fixture(_artifact(entries), max_types=1)
    assert sorted(entry["class_import"] for entry in two["types"]) == ["pkg.Cart", "pkg.core.Cart"]
    assert two["reduced_type_count"] == 2  # two entries, one slot
    three = run_extraction.reduce_fixture(_artifact(entries), max_types=2)
    assert three["reduced_type_count"] == 3  # the pair plus one more type


def test_a_pair_is_dropped_together_too() -> None:
    entries = [
        _entry("Hub", refs=("int",), file="pkg/hub.py"),
        _entry("Spoke", refs=("Hub",), file="pkg/spoke.py"),
        _shell("Quiet"),
        _entry("Quiet", class_import="pkg.core.Quiet", file="pkg/core.py", refs=("int",)),
    ]
    fixture = run_extraction.reduce_fixture(_artifact(entries), max_types=1)
    assert _names(fixture) == ["Hub"]


def test_a_shell_never_outranks_the_real_class_it_re_exports() -> None:
    """The flaw: the real class's references to ITSELF were credited to its empty shells, so
    two shells outranked it and the class with 82 methods was cut (pdf/python Document)."""
    real = _entry(
        "Document",
        class_import="pkg.document.Document",
        file="pkg/document.py",
        refs=("Document", "Document", "Document", "Document"),
    )
    shells = [
        _shell("Document"),
        _entry(
            "Document",
            class_import="pkg.generated.document.Document",
            file="pkg/generated/document.py",
            members=0,
        ),
    ]
    entries = [
        real,
        *shells,
        _entry("Page", refs=("Document", "Helper"), file="pkg/page.py"),
        _entry("Widget", refs=("Helper", "Document"), file="pkg/widget.py"),
        _entry("Helper", refs=("int",), file="pkg/helper.py"),
    ]
    artifact = _artifact(entries)

    # the old scoring really does rank a shell above the class (self-reference credited to it)
    old_scores = dict(
        zip((e["class_import"] for e in entries), run_extraction._centrality_scores(entries), strict=True)
    )
    assert old_scores["pkg.Document"] > old_scores["pkg.document.Document"]
    assert _names(_old_reduce(artifact, 2)) == ["Document", "Document"]
    assert "pkg.document.Document" not in {e["class_import"] for e in _old_reduce(artifact, 2)["types"]}

    fixture = run_extraction.reduce_fixture(artifact, max_types=2)
    kept = {entry["class_import"] for entry in fixture["types"]}
    assert "pkg.document.Document" in kept  # the real definition
    assert {"pkg.Document", "pkg.generated.document.Document"} <= kept  # its shells, with it
    assert len(kept) == 4  # Helper plus the three-entry Document group, two slots


def test_a_shell_without_any_definition_is_just_a_type() -> None:
    entries = [_shell("Orphan"), _entry("Real", refs=("int",), file="pkg/real.py")]
    fixture = run_extraction.reduce_fixture(_artifact(entries), max_types=5)
    assert sorted(_names(fixture)) == ["Orphan", "Real"]


def test_same_named_types_outside_python_are_never_grouped() -> None:
    entries = [
        _entry("Options", file="src/A.java", class_import="a.Options", refs=("int",)),
        _entry("Options", file="src/B.java", class_import="b.Options", members=0),
        _entry("Other", file="src/C.java", class_import="c.Other", refs=("int",)),
    ]
    fixture = run_extraction.reduce_fixture(_artifact(entries), max_types=1)
    assert len(fixture["types"]) == 1


def test_reduce_fixture_of_a_real_python_package_keeps_the_class_not_its_shells(tmp_path: Path) -> None:
    root = tmp_path
    (root / "pyproject.toml").write_text('[project]\nname = "shopkit"\n', encoding="utf-8")
    package = root / "src" / "shopkit"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        "from .core import Cart\nfrom .api import Cart as ApiCart\n__all__ = ['Cart', 'ApiCart']\n",
        encoding="utf-8",
    )
    (package / "api.py").write_text("from .core import Cart\n__all__ = ['Cart']\n", encoding="utf-8")
    (package / "core.py").write_text(
        "class Cart:\n"
        "    def add(self, other: 'Cart') -> 'Cart':\n        return self\n"
        "    def merge(self, other: 'Cart') -> 'Cart':\n        return self\n"
        "class Order:\n    def cart(self) -> 'Cart':\n        return None\n"
        "class Alpha:\n    def run(self) -> int:\n        return 1\n"
        "class Beta:\n    def run(self) -> int:\n        return 1\n",
        encoding="utf-8",
    )
    types, _ = run_extraction.extract_from_clone(
        root, {"platform": "python", "family": "shopkit", "repository": "acme/shopkit"}
    )
    shells = [e for e in types if e["name"] == "Cart" and not e["methods"]]
    assert len(shells) >= 2, "the package must produce re-export shells for this test to mean anything"
    fixture = run_extraction.reduce_fixture(_artifact(types), max_types=1)
    assert "shopkit.core.Cart" in {e["class_import"] for e in fixture["types"]}


# --------------------------------------------------------------------------- python enum members


def _enum_package(root: Path) -> None:
    (root / "pyproject.toml").write_text('[project]\nname = "paints"\n', encoding="utf-8")
    package = root / "src" / "paints"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        "from .colors import Color, Perm, Level, Mode\n__all__ = ['Color', 'Perm', 'Level', 'Mode']\n",
        encoding="utf-8",
    )
    (package / "colors.py").write_text(
        "import enum\n"
        "from enum import Enum, IntEnum, Flag, auto\n"
        "\n"
        "class Color(Enum):\n"
        '    """Colors."""\n'
        "    RED = 1\n"
        "    BLUE = 'b'\n"
        "    GREEN = auto()\n"
        "    MIX = (1, 2)\n"
        "    _hidden = 9\n"
        "\n"
        "    def describe(self):\n"
        "        return self.name\n"
        "\n"
        "class Perm(Flag):\n"
        "    READ = 1\n"
        "    WRITE = 2\n"
        "\n"
        "class Level(IntEnum):\n"
        "    LOW: int = 0\n"
        "    HIGH = -1\n"
        "\n"
        "class Mode(enum.Enum):\n"
        "    FAST = 'fast'\n"
        "    SLOW = compute()\n",
        encoding="utf-8",
    )


def _members(entry: dict[str, Any]) -> list[tuple[str, str]]:
    return [(member["name"], member["value"]) for member in entry["enum_members"]]


def test_fill_python_enum_members_reads_the_class_body(tmp_path: Path) -> None:
    _enum_package(tmp_path)
    types, _ = run_extraction.extract_from_clone(
        tmp_path, {"platform": "python", "family": "paints", "repository": "acme/paints"}
    )
    run_extraction.fill_python_enum_members(types, tmp_path)
    by_import = {entry["class_import"]: entry for entry in types}
    assert _members(by_import["paints.colors.Color"]) == [
        ("RED", "1"),
        ("BLUE", "'b'"),
        ("GREEN", ""),  # auto(): a member, no literal value
        ("MIX", "(1, 2)"),
    ]
    assert _members(by_import["paints.colors.Perm"]) == [("READ", "1"), ("WRITE", "2")]
    assert _members(by_import["paints.colors.Level"]) == [("LOW", "0"), ("HIGH", "-1")]
    assert _members(by_import["paints.colors.Mode"]) == [("FAST", "'fast'"), ("SLOW", "")]
    # a re-export stub has no class body of its own and stays empty; no class gained members
    assert by_import["paints.Color"]["enum_members"] == []
    assert all("enum_members" not in e for e in types if e["kind"] == "class")


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "--quiet")
    _git(path, "config", "uploadpack.allowAnySHA1InWant", "true")


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-C",
            str(cwd),
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _commit_package(repo: Path, body: str, message: str) -> str:
    (repo / "pyproject.toml").write_text('[project]\nname = "widgets"\n', encoding="utf-8")
    package = repo / "src" / "widgets"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text(body, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


OLD_BODY = "class OldThing:\n    def run(self) -> int:\n        return 1\n\n__all__ = ['OldThing']\n"
NEW_BODY = (
    "class NewThing:\n    def run(self) -> int:\n        return 2\n\n"
    "class Color:\n    pass\n\n__all__ = ['NewThing']\n"
)


@pytest.fixture
def local_origin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A local repository with two commits, ``repository_url`` pointed at it, and a manifest."""
    origin = tmp_path / "origin"
    _init_repo(origin)
    older = _commit_package(origin, OLD_BODY, "first")
    newer = _commit_package(origin, NEW_BODY, "second")
    monkeypatch.setattr(run_extraction, "repository_url", lambda _repository: origin.as_uri())
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("platform: python\nfamily: widgets\nrepository: acme/widgets\n", encoding="utf-8")
    return {"origin": origin, "older": older, "newer": newer, "manifest": manifest}


def test_extract_pinned_repository_without_commit_takes_the_head(local_origin: dict[str, Any]) -> None:
    artifact = run_extraction.extract_pinned_repository(local_origin["manifest"])
    assert artifact["source_commit"] == local_origin["newer"]
    assert {entry["name"] for entry in artifact["types"]} >= {"NewThing"}


def test_extract_pinned_repository_at_a_commit_extracts_the_older_commit(
    local_origin: dict[str, Any],
) -> None:
    assert local_origin["older"] != local_origin["newer"]
    artifact = run_extraction.extract_pinned_repository(
        local_origin["manifest"], commit=local_origin["older"]
    )
    assert artifact["source_commit"] == local_origin["older"]
    names = {entry["name"] for entry in artifact["types"]}
    assert "OldThing" in names
    assert "NewThing" not in names


def test_a_commit_that_is_not_the_fetched_head_is_refused(local_origin: dict[str, Any]) -> None:
    # a branch name fetches fine but its HEAD is a SHA, not the requested string
    branch = _git(local_origin["origin"], "rev-parse", "--abbrev-ref", "HEAD")
    with pytest.raises(RuntimeError, match="differs from the requested commit"):
        run_extraction.extract_pinned_repository(local_origin["manifest"], commit=branch)


def test_a_commit_the_remote_does_not_have_is_refused_clearly(local_origin: dict[str, Any]) -> None:
    with pytest.raises(RuntimeError, match="could not fetch commit"):
        run_extraction.extract_pinned_repository(local_origin["manifest"], commit="0" * 40)


@pytest.mark.parametrize("bad", ["", "--upload-pack=evil"])
def test_a_commit_that_could_be_a_git_option_is_refused(local_origin: dict[str, Any], bad: str) -> None:
    with pytest.raises(RuntimeError, match="not a commit SHA"):
        run_extraction.extract_pinned_repository(local_origin["manifest"], commit=bad)


def test_extract_pinned_repository_fills_python_enum_members(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    origin = tmp_path / "enum_origin"
    _init_repo(origin)
    _commit_package(
        origin,
        "from enum import Enum, auto\n\nclass Color(Enum):\n    RED = 1\n    BLUE = auto()\n\n__all__ = ['Color']\n",
        "enum",
    )
    monkeypatch.setattr(run_extraction, "repository_url", lambda _repository: origin.as_uri())
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("platform: python\nfamily: widgets\nrepository: acme/widgets\n", encoding="utf-8")
    artifact = run_extraction.extract_pinned_repository(manifest)
    (color,) = [entry for entry in artifact["types"] if entry["name"] == "Color"]
    assert _members(color) == [("RED", "1"), ("BLUE", "")]


def test_main_extracts_at_the_commit_and_keeps_the_names_the_page_uses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    origin = tmp_path / "cli_origin"
    _init_repo(origin)
    old_sha = _commit_package(
        origin,
        "class Hub:\n    pass\n\n"
        "class Spoke1:\n    def link(self, hub: 'Hub') -> 'Hub':\n        return hub\n\n"
        "class Spoke2:\n    def link(self, hub: 'Hub') -> 'Hub':\n        return hub\n\n"
        "class Zeta:\n    def solo(self) -> int:\n        return 1\n\n"
        "__all__ = ['Hub', 'Spoke1', 'Spoke2', 'Zeta']\n",
        "first",
    )
    _commit_package(origin, "class Replaced:\n    pass\n\n__all__ = ['Replaced']\n", "second")
    monkeypatch.setattr(run_extraction, "repository_url", lambda _repository: origin.as_uri())
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("platform: python\nfamily: widgets\nrepository: acme/widgets\n", encoding="utf-8")
    page = tmp_path / "_index.md"
    page.write_text(
        "---\ntitle: Demo\nsingle:\n  block:\n  - title: Use\n    content: |-\n"
        "      ```python\n      import widgets\n      z = widgets.Zeta()\n      ```\n---\n",
        encoding="utf-8",
    )

    def run(output: Path, *extra: str) -> dict[str, Any]:
        argv = ["run_extraction", str(manifest), str(output), "--max-types", "2", "--commit", old_sha, *extra]
        monkeypatch.setattr(sys, "argv", argv)
        run_extraction.main()
        assert old_sha in capsys.readouterr().out
        return json.loads(output.read_text(encoding="utf-8"))

    plain = run(tmp_path / "plain.json")
    assert plain["source_commit"] == old_sha
    assert "Zeta" not in _names(plain)  # the cap cut it: nothing references it
    assert "Hub" in _names(plain)

    kept = run(tmp_path / "kept.json", "--keep-names-from", str(page))
    assert kept["source_commit"] == old_sha
    assert "Zeta" in _names(kept)
    assert "Hub" in _names(kept)
    assert len(kept["types"]) == 2
