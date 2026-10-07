"""Offline, no network: run_extraction.py's own platform-to-language mapping for Node.js.

TC-232's worker found, by reading the real source, that ``_LANGUAGE_BY_PLATFORM`` mapped
platform "nodejs" to the literal language string "typescript" for both ``get_parser()`` and
``extract_api_surface()``'s language argument - so ``_collect_source_files`` only looked for
``.ts`` files, and every real ``.js`` file in the plain-JavaScript jmap/nodejs repository was
silently filtered out, producing 0/0 types. tree_helpers.py's own ``PLATFORM_TO_LANG`` already
mapped "nodejs" to "javascript" correctly; run_extraction.py just never used it. This test pins
the one-line fix (TC-233) against regressing, and confirms the real TypeScript platform itself
is untouched.
"""

from __future__ import annotations

from foss_mcp.extraction.run_extraction import _LANGUAGE_BY_PLATFORM


def test_nodejs_maps_to_javascript() -> None:
    assert _LANGUAGE_BY_PLATFORM["nodejs"] == "javascript"


def test_nodejs_no_longer_maps_to_typescript() -> None:
    assert _LANGUAGE_BY_PLATFORM["nodejs"] != "typescript"


def test_the_real_typescript_platform_is_unaffected() -> None:
    assert _LANGUAGE_BY_PLATFORM["typescript"] == "typescript"
