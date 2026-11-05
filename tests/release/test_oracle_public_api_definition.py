"""G3/TC-676 (O2, REQ-G3-012): the machine-readable public-API definition this card introduces.

Two things are proven here, offline, with no network and no toolchain beyond the interpreter:

1. **Schema validity.** ``config/public_api_definition/python.yaml`` really validates against the
   new ``schemas/product/public-api-definition.schema.json`` (``jsonschema.Draft202012Validator``,
   in test code only -- matching the established pattern in ``tests/indexing/test_known_defects.py``
   and ``tests/config/test_cells_cpp_manifest.py``). A structurally broken document is rejected too,
   so the schema itself is proven to discriminate, not merely to accept everything.

2. **Identical-behavior measurement.** ``source_differential._is_public_python_name`` used to be a
   hardcoded one-liner; this card replaced its body with one that loads
   ``config/public_api_definition/python.yaml`` and applies ``visibility.private_prefix`` /
   ``visibility.dunder_exception`` generically through ``_is_public_name_by_definition``. The OLD
   hardcoded logic is reproduced verbatim below as ``_is_public_python_name_v1_reference`` --
   TEST-ONLY, never imported from ``source_differential`` itself, which no longer contains it --
   purely to prove the new, config-driven function produces byte-for-byte identical results across
   every name this repository's own already-committed oracle test fixtures use. The name list is
   collected by grepping ``tests/release/test_source_differential.py``,
   ``tests/release/test_oracle_shell_resolution.py`` and
   ``tests/release/test_oracle_inherited_verification.py`` for literal quoted identifier-shaped
   strings (a real, reproducible measurement, not a hand-picked sample) plus the dunder names this
   card's own taskcard (``plans/TC-676.yaml``) names explicitly as the discrepancy evidence.

The negative-control mutation for this card (see ``plans/TC-676.yaml``'s ``negative_control``)
rewrites ``config/public_api_definition/python.yaml``'s ``private_prefix: "_"`` to a string no real
Python identifier starts with. ``test_config_driven_function_really_reads_the_file_at_runtime``
below additionally proves the production function depends on the file's live content (not a
cached/hardcoded copy) by pointing it at a synthetic, mutated definition directly.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import jsonschema
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE_DIR = REPO_ROOT / "scripts" / "release"
SCHEMA_PATH = REPO_ROOT / "schemas" / "product" / "public-api-definition.schema.json"
CONFIG_PATH = REPO_ROOT / "config" / "public_api_definition" / "python.yaml"

sys.path.insert(0, str(RELEASE_DIR))

import source_differential as sd  # noqa: E402

# --- the names this card's measurement runs both implementations over ------------------------------

FIXTURE_FILES = (
    REPO_ROOT / "tests" / "release" / "test_source_differential.py",
    REPO_ROOT / "tests" / "release" / "test_oracle_shell_resolution.py",
    REPO_ROOT / "tests" / "release" / "test_oracle_inherited_verification.py",
)

# Every one of the dunder names plans/TC-676.yaml names explicitly as the real, measured
# discrepancy's evidence: __repr__/__eq__/__hash__/__str__ (named in the card's own prose),
# __init__ (the constructor dunder), and PUBLISHED_DUNDERS' own four names (the extractor's real
# allow-list, read directly from src/foss_mcp/extraction/tree_sitter_engine/python_surface.py).
NAMED_DUNDERS = frozenset(
    {
        "__repr__",
        "__eq__",
        "__hash__",
        "__str__",
        "__init__",
        "__enter__",
        "__exit__",
        "__aenter__",
        "__aexit__",
    }
)

_IDENTIFIER_LITERAL = re.compile(r"""['"]([A-Za-z_][A-Za-z0-9_]*)['"]""")


def _names_from_fixture_files() -> frozenset[str]:
    """Every distinct quoted identifier-shaped string literal in the three named fixture files,
    plus the named dunders above -- a real grep over real, already-committed test source, not an
    invented list."""
    names: set[str] = set(NAMED_DUNDERS)
    for path in FIXTURE_FILES:
        text = path.read_text(encoding="utf-8")
        names.update(_IDENTIFIER_LITERAL.findall(text))
    return frozenset(names)


MEASURED_NAMES = _names_from_fixture_files()


def _is_public_python_name_v1_reference(name: str) -> bool:
    """The OLD hardcoded body of ``source_differential._is_public_python_name``, reproduced
    verbatim here, TEST-ONLY -- ``source_differential`` itself no longer contains this logic as of
    this card. Never imported by, nor exported to, any production path."""
    return not name.startswith("_") or (name.startswith("__") and name.endswith("__"))


# --- (1) schema validity -----------------------------------------------------------------------


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _config_document() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def test_python_definition_validates_against_its_own_schema() -> None:
    jsonschema.Draft202012Validator.check_schema(_schema())
    jsonschema.validate(instance=_config_document(), schema=_schema())


def test_python_definition_documents_the_real_oracle_rule() -> None:
    document = _config_document()
    assert document["language"] == "python"
    assert document["scope_roots"] == "module-level"
    assert document["excluded_dirs"] == ["tests", "test", "examples", "docs"]
    assert document["visibility"] == {"private_prefix": "_", "dunder_exception": "all"}
    assert document["kind_model"] == ["class", "enum"]
    assert document["member_kinds"] == ["method", "enum_value"]


def test_schema_rejects_a_structurally_broken_document() -> None:
    broken = _config_document()
    del broken["visibility"]  # a required field is missing
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=broken, schema=_schema())


def test_schema_rejects_an_unrecognized_dunder_exception_value() -> None:
    broken = _config_document()
    broken["visibility"] = {"private_prefix": "_", "dunder_exception": "sometimes"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=broken, schema=_schema())


# --- (2) identical-behavior measurement -----------------------------------------------------------


def test_config_driven_function_is_wired_as_the_real_production_decision() -> None:
    """source_differential.py no longer contains the old hardcoded body - the file's own source
    text proves the OLD one-liner is gone, not merely that some new function also exists."""
    source_text = (RELEASE_DIR / "source_differential.py").read_text(encoding="utf-8")
    assert "_load_public_api_definition" in source_text
    assert "_is_public_name_by_definition" in source_text
    # the OLD literal body (the exact mutation-prone hardcoded rule) must be gone from production:
    assert 'not name.startswith("_") or (name.startswith("__") and name.endswith("__"))' not in source_text


def test_new_config_driven_rule_matches_the_old_hardcoded_rule_across_every_measured_name() -> None:
    sd._load_public_api_definition.cache_clear()
    names = MEASURED_NAMES
    assert len(names) >= 100, "the fixture-derived name list collapsed; re-check the grep pattern"
    mismatches = [
        name for name in names if sd._is_public_python_name(name) != _is_public_python_name_v1_reference(name)
    ]
    assert mismatches == [], (
        f"config-driven _is_public_python_name diverged from the old hardcoded reference on "
        f"{len(mismatches)} of {len(names)} measured names: {sorted(mismatches)!r}"
    )


def test_config_driven_function_really_reads_the_file_at_runtime(tmp_path, monkeypatch) -> None:
    """Proves the production function depends on the file's live content, not a cached/hardcoded
    copy - the same property the card's negative-control mutation exercises against the real file,
    exercised here directly against a synthetic one so this test does not depend on the mutation
    ever having run."""
    synthetic_dir = tmp_path / "public_api_definition"
    synthetic_dir.mkdir()
    (synthetic_dir / "python.yaml").write_text(
        "language: python\nscope_roots: module-level\nexcluded_dirs: []\n"
        'visibility:\n  private_prefix: "ZZZ_NEVER_MATCHES"\n  dunder_exception: all\n'
        "kind_model: [class]\nmember_kinds: [method]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sd, "PUBLIC_API_DEFINITION_DIR", synthetic_dir)
    sd._load_public_api_definition.cache_clear()
    try:
        # under the mutated prefix, nothing starts with "ZZZ_NEVER_MATCHES", so every name -
        # including one that really is private under the real rule - reads as public.
        assert sd._is_public_python_name("_private") is True
        assert sd._is_public_python_name("__dunder__") is True
    finally:
        sd._load_public_api_definition.cache_clear()
