"""Pin the tree-sitter-language-pack spelling each configured language actually uses.

``lang.LANGUAGES`` names the language key api_surface.py passes straight through to
``tree_sitter_language_pack.get_parser``. Production uses ``"csharp"`` — not ``"c_sharp"`` or
``"c-sharp"`` — and every other key in the dict must resolve too, or ``extract_api_surface``
raises the moment that language's package is scanned.
"""

from __future__ import annotations

import pytest
from tree_sitter_language_pack import get_parser

from foss_mcp.extraction.tree_sitter_engine import lang


def test_get_parser_succeeds_with_the_production_csharp_spelling() -> None:
    assert "csharp" in lang.LANGUAGES
    parser = get_parser("csharp")
    assert parser is not None


@pytest.mark.parametrize("spelling", ["c_sharp", "c-sharp", "cs"])
def test_the_alternate_csharp_spellings_are_not_what_production_uses(spelling: str) -> None:
    """Guards against a future edit quietly renaming the key away from what's pinned above."""
    assert spelling not in lang.LANGUAGES


def test_get_parser_succeeds_for_every_configured_language() -> None:
    for name in lang.LANGUAGES:
        assert get_parser(name) is not None, name
