"""TC-521: a method supplied by a JavaScript object-literal mixin renders its mixin and defining file.

The suffix ``  (mixin <Name>, <file>)`` sits after the modifiers and before the inherited/deprecated
suffixes. The member name still ends at the first ``(`` or ``:`` (``search_symbols._member_name``), and
a member without the ``mixin`` key renders exactly as it did before.
"""

from __future__ import annotations

from collections.abc import Mapping

from foss_mcp.indexing.chunk_builder import build_chunks_from_api_surface
from foss_mcp.mcp.tools.search_symbols import _member_name


def _lines(methods: list[dict[str, object]]) -> list[str]:
    fixture: Mapping[str, object] = {
        "source_repository": "org/repo",
        "source_commit": "c0ffee",
        "types": [{"name": "JmapClient", "kind": "class_declaration", "methods": methods}],
    }
    chunks = build_chunks_from_api_surface(fixture, title="t", max_types=10_000)
    return "\n".join(chunk.text for chunk in chunks).splitlines()


MIXIN_METHOD: dict[str, object] = {
    "name": "listMailboxes",
    "params": [{"name": "accountId", "type": ""}, {"name": "ids", "type": "", "default": "null"}],
    "return_type": "",
    "modifiers": ["async"],
    "mixin": "MailClientMixin",
    "file": "src/client-mail.js",
}


def test_the_suffix_text_and_position() -> None:
    assert (
        "  - listMailboxes(accountId, ids = null) [async]  (mixin MailClientMixin, src/client-mail.js)"
        in (_lines([MIXIN_METHOD]))
    )


def test_the_suffix_precedes_the_inherited_and_deprecated_suffixes() -> None:
    method = {**MIXIN_METHOD, "inherited_from": "client-core.JmapClient", "deprecated": True}
    line = next(line for line in _lines([method]) if "listMailboxes" in line)
    assert line.endswith(
        "[async]  (mixin MailClientMixin, src/client-mail.js)  (inherited from client-core.JmapClient) (deprecated)"
    )


def test_a_member_without_the_key_renders_as_before() -> None:
    plain = {"name": "connect", "params": [], "return_type": "", "file": "src/client-core.js"}
    lines = _lines([plain])
    assert "  - connect()" in lines
    assert not any("mixin" in line for line in lines)


def test_a_mixin_member_without_modifiers_or_file() -> None:
    inline = {"name": "ping", "params": [], "mixin": "<inline>"}
    assert "  - ping()  (mixin <inline>)" in _lines([inline])
    arrow = {"name": "count", "params": [], "mixin": "M", "file": "src\\m.js"}
    assert "  - count()  (mixin M, src/m.js)" in _lines([arrow])


def test_the_rendered_line_still_parses_to_the_bare_method_name() -> None:
    line = next(line for line in _lines([MIXIN_METHOD]) if "listMailboxes" in line)
    assert _member_name(line[len("  - ") :].strip()) == "listMailboxes"


def test_a_destructured_parameter_renders_as_one_object_param() -> None:
    constructor = {
        "name": "constructor",
        "params": [
            {
                "name": "params",
                "type": "{ id?: string; name: string; parentId?: string | null }",
                "default": "{}",
                "destructured": [
                    {"name": "id", "type": "string", "default": "", "optional": True, "doc": ""}
                ],
            }
        ],
        "is_constructor": True,
    }
    assert "  - constructor(params: { id?: string; name: string; parentId?: string | null } = {})" in _lines(
        [constructor]
    )
