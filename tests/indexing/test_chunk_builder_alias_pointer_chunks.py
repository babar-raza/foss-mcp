"""G3/TC-686 (I4, REQ-G3-012): a re-export shell renders as a thin pointer, not a full duplicate.

Before this card, `chunk_builder._format_type_text` rendered a Python re-export shell's chunk with
`members_from=<the real definition entry>`, so every member section (`Methods:`/`Properties:`/
`Members:`/the doc paragraph) was a FULL DUPLICATE of the definition's own content, with a
`Defined-In: <fqn>` marker line added alongside it, never instead of it. This card consumes I1's
already-built, pure `symbol_identity.compute_canonical_identities` to render a THIN pointer instead,
for exactly the entries it marks `is_definition=False`: `FQN:`/`Kind:`/`Defined-In:`/`Exported-As:`
only (plus, confirmed against the real committed `3d_python` fixture, the shell's own honest
`Shadows:`/`Shadowed-By:`/`Note:` self-description, which is never duplicated content). The
definition entry's own chunk is untouched - this module never touches how a real definition's
content is built, only how a SHELL's content is built.

Synthetic fixtures built by hand (offline, deterministic) plus the real committed `slides_python` and
`3d_python` fixtures (`symbol_identity.py`'s own docstring names both as having a real `reexport_of`
fixture / a real shadow+shell combination).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from foss_mcp.indexing.chunk_builder import build_chunks_from_api_surface

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"

_FQN_LINE = re.compile(r"^FQN: (.+)$", re.MULTILINE)
_MEMBER_HEADERS = ("Methods:", "Properties:", "Members:")


def _method(name: str = "m") -> dict[str, Any]:
    return {"name": name, "params": [], "return_type": ""}


def _entry(name: str, class_import: str, file: str, **extra: Any) -> dict[str, Any]:
    return {
        "name": name,
        "kind": extra.pop("kind", "class"),
        "file": file,
        "class_import": class_import,
        "bases": [],
        "methods": [],
        "properties": [],
        **extra,
    }


def _fixture(types: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {
        "source_repository": "org/repo",
        "source_commit": "c0ffee",
        "type_count": len(types),
        "types": types,
        **extra,
    }


def _texts_by_fqn(fixture: dict[str, Any], max_types: int = 1000) -> dict[str, str]:
    texts: dict[str, str] = {}
    for chunk in build_chunks_from_api_surface(fixture, title="t", max_types=max_types):
        match = _FQN_LINE.search(chunk.text)
        assert match is not None
        texts[match.group(1)] = chunk.text
    return texts


def test_a_reexport_shell_renders_as_a_thin_pointer_and_the_definition_is_full() -> None:
    shell = _entry(
        "Facade",
        "pkg.Facade",
        "pkg/__init__.py",
        reexport_of="pkg.facades.Facade",
        doc="A shell-only docstring that must never survive thinning.",
    )
    definition = _entry(
        "Facade",
        "pkg.facades.Facade",
        "pkg/facades.py",
        methods=[_method("bind")],
        doc="The real definition's own doc paragraph.",
    )
    texts = _texts_by_fqn(_fixture([shell, definition]))
    shell_text, definition_text = texts["pkg.Facade"], texts["pkg.facades.Facade"]

    # The thin pointer: FQN/Kind/Defined-In (plus the fixture-level API-Coverage line every chunk
    # carries) - nothing from the definition's own content duplicated onto it.
    assert shell_text.splitlines()[:3] == ["FQN: pkg.Facade", "Kind: class", "Defined-In: pkg.facades.Facade"]
    for header in _MEMBER_HEADERS:
        assert header not in shell_text
    assert "shell-only docstring" not in shell_text
    assert "Exported-As" not in shell_text  # exported_as names the DEFINITION's chunk, not the shell's

    # The definition's own chunk is unchanged: full content, its own fields.
    assert "Methods:" in definition_text and "  - bind()" in definition_text
    assert "The real definition's own doc paragraph." in definition_text
    assert "Exported-As: pkg.Facade" in definition_text


def test_a_shell_that_also_carries_a_shadow_marker_keeps_its_own_disclosure() -> None:
    """TC-532 + TC-686: confirmed against the real committed `3d_python` fixture (ten real entries
    carry BOTH a `Defined-In:` shell pairing and a `shadows` marker) - the shell's own honest
    self-description is not content duplicated FROM the definition, so thinning must never drop it.
    """
    placeholder = _entry("Opts", "pkg.Opts.Opts", "pkg/Opts.py")
    real = _entry("Opts", "pkg.fbx.Opts.Opts", "pkg/fbx/Opts.py", shadowed_by="pkg.Opts")
    real["methods"] = [_method("work")]
    exported = _entry(
        "Opts", "pkg.Opts", "pkg/__init__.py", reexport_of="pkg.Opts.Opts", shadows="pkg.fbx.Opts.Opts"
    )
    texts = _texts_by_fqn(_fixture([exported, placeholder, real]))
    shell_text = texts["pkg.Opts"]
    assert shell_text.splitlines()[:5] == [
        "FQN: pkg.Opts",
        "Kind: class",
        "Defined-In: pkg.Opts.Opts",
        "Shadows: pkg.fbx.Opts.Opts",
        "Note: this exported name is an empty placeholder at runtime; the working class is pkg.fbx.Opts.Opts",
    ]
    for header in _MEMBER_HEADERS:
        assert header not in shell_text


def test_a_fixture_with_no_shell_at_all_is_unaffected() -> None:
    """Decision E: a pilot with no Python re-export alias at all renders byte-for-byte as before -
    `identities[index].is_definition` is True for every entry (`_reexport_pairs` never pairs any of
    them), so the thin-pointer branch is never taken.
    """
    a = _entry("A", "pkg.a.A", "pkg/a.py", methods=[_method("go")])
    b = _entry("B", "pkg.b.B", "pkg/b.py", properties=[{"name": "x", "type": "int"}])
    texts = _texts_by_fqn(_fixture([a, b]))
    assert "Defined-In" not in texts["pkg.a.A"]
    assert "Defined-In" not in texts["pkg.b.B"]
    assert "Methods:" in texts["pkg.a.A"] and "  - go()" in texts["pkg.a.A"]
    assert "Properties:" in texts["pkg.b.B"]


def _real_fixture(name: str) -> dict[str, Any]:
    path = FIXTURES_DIR / name / "api_surface.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_a_real_committed_python_pilot_thins_its_real_shells() -> None:
    """Confirmed directly against `slides_python` (named in `symbol_identity.py`'s own docstring as
    having a real `reexport_of` fixture): every real shell this fixture produces renders thin, and
    its paired real definition still carries full content.
    """
    fixture = _real_fixture("slides_python")
    chunks = build_chunks_from_api_surface(fixture, title="slides/python", max_types=10_000)
    texts: dict[str, str] = {}
    for chunk in chunks:
        match = _FQN_LINE.search(chunk.text)
        assert match is not None
        texts[match.group(1)] = chunk.text
    shells = {fqn: text for fqn, text in texts.items() if "Defined-In:" in text}
    assert shells, "slides_python produced no real shell - fixture no longer has a reexport_of pair"
    defined_in_line = re.compile(r"^Defined-In:\s*(\S.*)$", re.MULTILINE)
    checked = 0
    for fqn, shell_text in shells.items():
        for header in _MEMBER_HEADERS:
            assert header not in shell_text, (fqn, shell_text)
        target = defined_in_line.search(shell_text).group(1).strip()  # type: ignore[union-attr]
        assert target in texts, (fqn, target)
        checked += 1
    assert checked >= 1
