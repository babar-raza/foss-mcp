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

G3/TC-679 (O3, REQ-G3-012) adds a second, independent wiring to this same file: ``enumerate_python``'s
directory-exclusion check (``any(part in {"tests", "test", "examples", "docs"} for part in
path.parts)``) used to hardcode that four-name set as a Python literal; it now reads it from this
file's own ``excluded_dirs`` field through ``_python_excluded_dirs()``. The OLD hardcoded set is
reproduced verbatim below as ``_excluded_dirs_v1_reference`` -- TEST-ONLY, never imported from
``source_differential`` itself, which no longer contains it -- purely to prove the new,
config-driven check agrees with the old hardcoded one on every ``Path`` this card's own measurement
exercises: both excluded-dir shapes and non-excluded shapes, built from the same repo-root/filename
conventions ``tests/release/test_source_differential.py``'s own existing ``enumerate_python`` and
``enumerate_java`` fixtures already use (``acme/widgets`` as the cloned library root; ``widgets.py``,
``colors.py``, ``broken.py``, ``good.py`` as real non-excluded filenames; the
``src/test/java``-style nested-directory shape ``enumerate_java``'s own fixture already exercises,
adapted to each of the four real ``excluded_dirs`` names). No existing ``enumerate_python`` fixture
in that file already places a file under an excluded directory, so this card's test builds that
shape itself rather than inventing unrelated names -- see
``test_new_config_driven_exclusion_matches_the_old_hardcoded_exclusion_across_every_measured_path``
below for the exact path list and its provenance.
``test_config_driven_exclusion_really_reads_the_file_at_runtime`` proves the live-read property
directly against a synthetic file with a different ``excluded_dirs`` list, the same style the
``visibility`` live-read test above already uses.
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


# --- (3) G3/TC-679 (O3): excluded_dirs wiring into enumerate_python's directory-exclusion check ----


def _excluded_dirs_v1_reference(path: Path) -> bool:
    """The OLD hardcoded exclusion check ``enumerate_python`` used to inline at its own call site,
    reproduced verbatim here, TEST-ONLY -- ``source_differential`` itself no longer contains this
    literal as of this card. Never imported by, nor exported to, any production path."""
    return any(part in {"tests", "test", "examples", "docs"} for part in path.parts)


# A real, representative measured corpus of Path objects: four excluded-dir shapes (one per real
# name in config/public_api_definition/python.yaml's own excluded_dirs list) plus four non-excluded
# shapes, all built from the same "acme/widgets" library-root convention and the same real,
# already-committed filenames (widgets.py, colors.py, broken.py, good.py) that
# tests/release/test_source_differential.py's own existing enumerate_python fixtures already use -
# no existing enumerate_python fixture there already places a file under an excluded directory, so
# the excluded-dir shapes below are built directly from this card's own python.yaml excluded_dirs
# entries, the same "src/test/java"-style nested-directory shape
# test_enumerate_java_skips_a_src_test_java_directory already exercises for a sibling enumerator.
_MEASURED_PATHS = tuple(
    Path(p)
    for p in (
        # excluded-dir shapes: one per real excluded_dirs entry, nested like a real cloned library.
        "acme/widgets/tests/widgets.py",
        "acme/widgets/test/colors.py",
        "acme/widgets/examples/broken.py",
        "acme/widgets/docs/good.py",
        "acme/widgets/src/nested/tests/deep.py",  # excluded name appears mid-path, not just at top
        # non-excluded shapes: the real top-level fixture files already committed in
        # tests/release/test_source_differential.py's own enumerate_python tests.
        "acme/widgets/widgets.py",
        "acme/widgets/colors.py",
        "acme/widgets/broken.py",
        "acme/widgets/good.py",
        "acme/widgets/src/nested/testing_helpers.py",  # a near-miss name ("testing_helpers", not
        # "test"/"tests") - proves the check matches whole path parts, never a substring.
    )
)


def test_new_config_driven_exclusion_matches_the_old_hardcoded_exclusion_across_every_measured_path() -> None:
    sd._load_public_api_definition.cache_clear()
    mismatches = [
        path
        for path in _MEASURED_PATHS
        if bool(any(part in sd._python_excluded_dirs() for part in path.parts))
        != _excluded_dirs_v1_reference(path)
    ]
    assert mismatches == [], (
        f"config-driven excluded_dirs check diverged from the old hardcoded reference on "
        f"{len(mismatches)} of {len(_MEASURED_PATHS)} measured paths: {mismatches!r}"
    )


def test_config_driven_excluded_dirs_set_matches_the_real_committed_yaml_list() -> None:
    sd._load_public_api_definition.cache_clear()
    assert sd._python_excluded_dirs() == frozenset({"tests", "test", "examples", "docs"})


def test_config_driven_exclusion_really_reads_the_file_at_runtime(tmp_path, monkeypatch) -> None:
    """Proves ``_python_excluded_dirs`` (and therefore ``enumerate_python``'s own exclusion check)
    depends on ``excluded_dirs``' live content, not a cached/hardcoded copy - the same live-read
    property the ``visibility`` test above proves for ``_is_public_python_name``, exercised here
    directly against a synthetic file with a different ``excluded_dirs`` list."""
    synthetic_dir = tmp_path / "public_api_definition"
    synthetic_dir.mkdir()
    (synthetic_dir / "python.yaml").write_text(
        "language: python\nscope_roots: module-level\n"
        "excluded_dirs: [vendored, docs]\n"
        'visibility:\n  private_prefix: "_"\n  dunder_exception: all\n'
        "kind_model: [class]\nmember_kinds: [method]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sd, "PUBLIC_API_DEFINITION_DIR", synthetic_dir)
    sd._load_public_api_definition.cache_clear()
    try:
        # under the synthetic list, "vendored" is excluded (never true under the real file) and
        # "tests"/"test"/"examples" are no longer excluded (unlike under the real file) - the
        # exclusion check's real behavior tracks the live file, not a cached/hardcoded copy.
        assert sd._python_excluded_dirs() == frozenset({"vendored", "docs"})
        assert (
            any(part in sd._python_excluded_dirs() for part in Path("acme/widgets/vendored/x.py").parts)
            is True
        )
        assert (
            any(part in sd._python_excluded_dirs() for part in Path("acme/widgets/tests/x.py").parts) is False
        )
    finally:
        sd._load_public_api_definition.cache_clear()


# --- (4) G3/TC-682 (O4): kw-only params, *args/**kwargs, and enum.auto() signature completeness ---
#
# plans/TC-682.yaml's own fixture (reproduced directly against the unmodified code before this
# card changed anything): a kw-only param and a **kwargs catch-all were COMPLETELY INVISIBLE in
# `configure`'s params; *args was equally invisible in `varargs_method`; `Mode.RED = enum.auto()`
# showed `enum_value=None`, identical to a genuinely unparseable/value-less constant, with zero
# signal that this was `auto()` specifically. The five tests below prove: (a) a kw-only param is
# now captured with its correct default-or-not status; (b) *args/**kwargs are now represented,
# each as a synthesized OracleParam named `*<real name>`/`**<real name>` appended to the existing
# `params` tuple IN REAL PYTHON DECLARATION ORDER (positional, then *args, then kw-only, then
# **kwargs) - chosen over adding a new OracleMember/OracleParam field because it keeps the
# dataclass shape every other six enumerators (Go/Rust/TS/Java/.NET/C++) already rely on completely
# untouched, and flows straight through diff_symbol's existing params-by-name/has_default-by-
# position comparison with no new special case needed there; (c) `enum.auto()` (both the bare
# `auto()` and the `enum.auto()` attribute-access forms, mirroring `_python_enum_bases`' own
# bare-name/attribute-access recognition) is now distinguished, via the distinct marker
# `enum_value="auto()"`, from both a literal value and a genuinely unparseable one (represented
# unchanged as `enum_value=None`); (d) a method using only plain positional-or-keyword params is
# BYTE-FOR-BYTE unaffected - same OracleMember this card's predecessor code already produced; (e)
# a literal enum value (`BLUE = 2`) is likewise BYTE-FOR-BYTE unaffected.


def test_enumerate_python_reads_keyword_only_params_with_correct_default_status(tmp_path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "widgets.py").write_text(
        "class Widget:\n    def configure(self, a, *, b, c=2, **kwargs):\n        pass\n",
        encoding="utf-8",
    )
    (widget,) = sd.enumerate_python("acme/widgets", "deadbeef", tmp_path)
    (configure,) = widget.members
    assert configure.params == (
        sd.OracleParam("a"),
        sd.OracleParam("b", has_default=False),  # kw-only, no default: kw_defaults entry is None
        sd.OracleParam("c", has_default=True),  # kw-only, WITH a default
        sd.OracleParam("**kwargs"),
    )


def test_enumerate_python_reads_varargs_and_kwargs_in_real_declaration_order(tmp_path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "widgets.py").write_text(
        "class Widget:\n"
        "    def varargs_method(self, *args):\n"
        "        pass\n"
        "    def full_shape(self, a, *values, b, **opts):\n"
        "        pass\n",
        encoding="utf-8",
    )
    (widget,) = sd.enumerate_python("acme/widgets", "deadbeef", tmp_path)
    by_name = {member.name: member for member in widget.members}
    assert by_name["varargs_method"].params == (sd.OracleParam("*args"),)
    # real declaration order: positional, then *args (named after the real variable, "values"),
    # then kw-only "b", then **opts (named after the real variable, "opts") - never a hardcoded
    # "*args"/"**kwargs" literal regardless of what the pilot actually named them.
    assert by_name["full_shape"].params == (
        sd.OracleParam("a"),
        sd.OracleParam("*values"),
        sd.OracleParam("b", has_default=False),
        sd.OracleParam("**opts"),
    )


def test_enumerate_python_distinguishes_auto_from_literal_and_unparseable_enum_values(tmp_path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "modes.py").write_text(
        "import enum\n"
        "from enum import Enum, auto\n\n"
        "class Mode(Enum):\n"
        "    ATTR_AUTO = enum.auto()\n"  # attribute-access form, mirrors _python_enum_bases
        "    BARE_AUTO = auto()\n"  # bare-name form, mirrors _python_enum_bases
        "    LITERAL = 2\n"
        "    UNPARSEABLE = some_runtime_expression()\n",  # a real Call that is NOT auto()
        encoding="utf-8",
    )
    (mode,) = sd.enumerate_python("acme/widgets", "deadbeef", tmp_path)
    by_name = {member.name: member.enum_value for member in mode.members}
    assert by_name["ATTR_AUTO"] == "auto()"
    assert by_name["BARE_AUTO"] == "auto()"
    assert by_name["LITERAL"] == "2"
    # a genuinely unparseable, non-auto() call is unchanged from before this card: None, with no
    # false "auto()" signal and no false literal value.
    assert by_name["UNPARSEABLE"] is None


def test_enumerate_python_plain_signature_method_is_byte_for_byte_unaffected(tmp_path) -> None:
    """Decision E (plans/TC-682.yaml): a method using none of kw-only/vararg/kwarg must produce the
    exact same OracleMember this card's predecessor code already produced."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "widgets.py").write_text(
        "class Gadget:\n"
        "    def build(self, name, count=1):\n"
        "        return name\n"
        "    @staticmethod\n"
        "    def make():\n"
        "        return Gadget()\n",
        encoding="utf-8",
    )
    (gadget,) = sd.enumerate_python("acme/widgets", "deadbeef", tmp_path)
    by_name = {member.name: member for member in gadget.members}
    assert by_name["build"] == sd.OracleMember(
        name="build",
        kind="method",
        params=(sd.OracleParam("name"), sd.OracleParam("count", has_default=True)),
        is_static=False,
    )
    assert by_name["make"] == sd.OracleMember(name="make", kind="method", params=(), is_static=True)


def test_enumerate_python_literal_enum_value_is_byte_for_byte_unaffected(tmp_path) -> None:
    """Decision E (plans/TC-682.yaml): a literal enum value (the BLUE = 2 case) must produce the
    exact same OracleMember this card's predecessor code already produced."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "colors.py").write_text(
        "from enum import Enum\n\nclass Color(Enum):\n    Red = 1\n    Blue = 2\n",
        encoding="utf-8",
    )
    (color,) = sd.enumerate_python("acme/widgets", "deadbeef", tmp_path)
    assert color.members == (
        sd.OracleMember(name="Red", kind="enum_value", enum_value="1"),
        sd.OracleMember(name="Blue", kind="enum_value", enum_value="2"),
    )
