"""G3/TC-566: scripts/release/source_differential.py, offline.

Every expectation here comes from a HAND-WRITTEN oracle (an ``OracleSymbol``/``OracleMember`` the
test builds itself, standing in for "the real upstream source, read with the language's own tool"),
never from a committed fixture or the extractor's own output. That is the whole point of this card
(RD3-150): a test whose expectation is derived from the thing it is checking can never see that thing
lose or fabricate data. No network call is made by any test below -- the per-language
``Enumerator`` callables are injected (``Deps.enumerators``), copying
``scripts/release/verify_pushed_image.py``'s own injection style, so even the few tests that exercise
``main()`` never touch the real git path.

The ``enumerate_go`` tests really invoke the local Go toolchain (``go run``), and the
``enumerate_rust`` tests (G3/TC-590) really invoke Docker (``docker run ... cargo run --release
--offline``, against the pinned, already-cached ``rust:1.98.1`` image and the companion crate's
own vendored dependencies) against hand-written, on-disk synthetic ``.rs`` fixtures -- never the
real pilot, never a network call, since both the Go toolchain and the Docker daemon with its
pinned image already cached are this card's own declared local ``requires:``, never something
reached over the wire mid-check.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE_DIR = REPO_ROOT / "scripts" / "release"
SOURCE = RELEASE_DIR / "source_differential.py"

sys.path.insert(0, str(RELEASE_DIR))

import source_differential as sd  # noqa: E402

OM = sd.OracleMember
OP = sd.OracleParam
OS = sd.OracleSymbol


def _baked_type(
    name: str,
    *,
    methods: list[dict] | None = None,
    properties: list[dict] | None = None,
    enum_members: list[dict] | None = None,
) -> dict:
    return {
        "name": name,
        "kind": "class_declaration",
        "class_import": name,
        "methods": methods or [],
        "properties": properties or [],
        "enum_members": enum_members or [],
    }


# --- diff_symbol: the pure comparison engine --------------------------------------------------------


def test_diff_symbol_reports_zero_when_the_oracle_and_the_baked_type_agree_exactly():
    oracle = OS(
        "Widget",
        "class",
        members=(OM("build", "method", params=(OP("name"),), return_type="Widget", is_static=False),),
    )
    baked = _baked_type(
        "Widget", methods=[{"name": "build", "params": [{"name": "name"}], "return_type": "Widget"}]
    )
    counts, findings = sd.diff_symbol(oracle, baked)
    assert counts.as_dict() == {
        "missing": 0,
        "extra": 0,
        "signature": 0,
        "static": 0,
        "default": 0,
        "enum_value": 0,
        "checked": 1,
    }
    assert findings == []


def test_diff_symbol_counts_a_missing_member():
    oracle = OS("Widget", "class", members=(OM("vanished", "method"),))
    baked = _baked_type("Widget", methods=[])
    counts, findings = sd.diff_symbol(oracle, baked)
    assert counts.missing == 1
    assert findings[0].category == "missing" and findings[0].member == "vanished"


def test_diff_symbol_counts_a_fabricated_extra_member():
    oracle = OS("Widget", "class", members=())
    baked = _baked_type("Widget", methods=[{"name": "phantom", "params": []}])
    counts, findings = sd.diff_symbol(oracle, baked)
    assert counts.extra == 1
    assert findings[0].category == "extra" and findings[0].member == "phantom"


def test_diff_symbol_counts_a_signature_mismatch_on_params():
    oracle = OS("Widget", "class", members=(OM("build", "method", params=(OP("name"), OP("count"))),))
    baked = _baked_type("Widget", methods=[{"name": "build", "params": [{"name": "name"}]}])
    counts, findings = sd.diff_symbol(oracle, baked)
    assert counts.signature == 1
    assert findings[0].category == "signature"


def test_diff_symbol_counts_a_signature_mismatch_on_return_type():
    oracle = OS("Widget", "class", members=(OM("build", "method", return_type="Widget"),))
    baked = _baked_type("Widget", methods=[{"name": "build", "params": [], "return_type": "void"}])
    counts, _findings = sd.diff_symbol(oracle, baked)
    assert counts.signature == 1


def test_diff_symbol_a_return_type_the_oracle_never_checked_is_never_counted():
    oracle = OS("Widget", "class", members=(OM("build", "method", return_type=None),))
    baked = _baked_type("Widget", methods=[{"name": "build", "params": [], "return_type": "anything"}])
    counts, _findings = sd.diff_symbol(oracle, baked)
    assert counts.signature == 0


def test_diff_symbol_counts_a_static_mismatch():
    oracle = OS("Widget", "class", members=(OM("make", "method", is_static=True),))
    baked = _baked_type("Widget", methods=[{"name": "make", "params": [], "modifiers": []}])
    counts, findings = sd.diff_symbol(oracle, baked)
    assert counts.static == 1
    assert findings[0].category == "static"


def test_diff_symbol_counts_a_default_mismatch():
    oracle = OS("Widget", "class", members=(OM("build", "method", params=(OP("name", has_default=True),)),))
    baked = _baked_type("Widget", methods=[{"name": "build", "params": [{"name": "name"}]}])
    counts, findings = sd.diff_symbol(oracle, baked)
    assert counts.default == 1
    assert findings[0].category == "default"


def test_diff_symbol_counts_an_enum_value_mismatch():
    oracle = OS("Color", "enum", members=(OM("Red", "enum_value", enum_value="1"),))
    baked = _baked_type("Color", enum_members=[{"name": "Red", "value": "0"}])
    counts, findings = sd.diff_symbol(oracle, baked)
    assert counts.enum_value == 1
    assert findings[0].category == "enum_value"


def test_diff_symbol_a_missing_enum_member_is_missing_not_enum_value():
    oracle = OS("Color", "enum", members=(OM("Blue", "enum_value", enum_value="2"),))
    baked = _baked_type("Color", enum_members=[])
    counts, findings = sd.diff_symbol(oracle, baked)
    assert counts.missing == 1 and counts.enum_value == 0
    assert findings[0].category == "missing"


def test_diff_symbol_a_property_is_checked_against_properties_not_methods():
    oracle = OS("Widget", "class", members=(OM("Name", "property", is_static=True),))
    baked = _baked_type("Widget", properties=[{"name": "Name", "modifiers": ["static"]}])
    counts, _findings = sd.diff_symbol(oracle, baked)
    assert counts.as_dict()["missing"] == 0 and counts.static == 0


def test_diff_symbol_a_wholly_absent_baked_type_counts_every_oracle_member_missing():
    oracle = OS(
        "Widget",
        "class",
        members=(OM("build", "method"), OM("Name", "property"), OM("Red", "enum_value")),
    )
    counts, findings = sd.diff_symbol(oracle, None)
    assert counts.missing == 3 and counts.checked == 3
    assert len(findings) == 3 and all(f.category == "missing" for f in findings)


def test_diff_symbol_classmethod_modifier_also_counts_as_static():
    oracle = OS("Widget", "class", members=(OM("make", "method", is_static=True),))
    baked = _baked_type("Widget", methods=[{"name": "make", "params": [], "modifiers": ["classmethod"]}])
    counts, _findings = sd.diff_symbol(oracle, baked)
    assert counts.static == 0


# --- diff_language: aggregation across several symbols ----------------------------------------------


def test_diff_language_aggregates_every_symbol_and_matches_by_bare_name():
    symbols = [
        OS("Widget", "class", members=(OM("build", "method"),)),
        OS("Color", "enum", members=(OM("Red", "enum_value", enum_value="1"),)),
    ]
    baked_types = [
        _baked_type("Widget", methods=[]),  # build is missing
        _baked_type("Color", enum_members=[{"name": "Red", "value": "1"}]),  # matches exactly
    ]
    counts, findings = sd.diff_language(symbols, baked_types)
    assert counts.missing == 1 and counts.checked == 2
    assert len(findings) == 1


def test_diff_language_an_oracle_symbol_with_no_matching_baked_type_counts_missing():
    symbols = [OS("Ghost", "class", members=(OM("gone", "method"),))]
    counts, _findings = sd.diff_language(symbols, [])
    assert counts.missing == 1


# --- the extractor_version gate ---------------------------------------------------------------------


def test_check_extractor_version_accepts_the_current_or_newer_schema():
    assert sd.check_extractor_version({"extractor_version": 5}, current_version=5) is None
    assert sd.check_extractor_version({"extractor_version": 6}, current_version=5) is None


def test_check_extractor_version_refuses_a_stale_or_missing_or_non_integer_value():
    assert sd.check_extractor_version({"extractor_version": 4}, current_version=5) is not None
    assert sd.check_extractor_version({}, current_version=5) is not None
    assert sd.check_extractor_version({"extractor_version": "5"}, current_version=5) is not None
    assert sd.check_extractor_version({"extractor_version": True}, current_version=5) is not None


# --- ceilings ----------------------------------------------------------------------------------------


def test_default_language_error_ceilings_are_all_zero():
    for language, rates in sd.LANGUAGE_ERROR_CEILINGS.items():
        assert set(rates) == set(sd.CATEGORIES)
        assert all(value == 0 for value in rates.values()), language


def test_load_ceilings_with_no_file_returns_the_defaults():
    ceilings = sd.load_ceilings(None)
    assert ceilings == sd.LANGUAGE_ERROR_CEILINGS
    assert ceilings is not sd.LANGUAGE_ERROR_CEILINGS  # never the live mutable default


def test_load_ceilings_merges_an_override_file(tmp_path):
    path = tmp_path / "ceilings.json"
    path.write_text(json.dumps({"java": {"missing": 3, "static": 1}}), encoding="utf-8")
    ceilings = sd.load_ceilings(path)
    assert ceilings["java"]["missing"] == 3 and ceilings["java"]["static"] == 1
    assert ceilings["java"]["extra"] == 0  # categories not named in the override keep the default
    assert ceilings["python"] == {category: 0 for category in sd.CATEGORIES}  # other languages untouched


def test_load_ceilings_refuses_a_malformed_override_file(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
    with pytest.raises(sd.Refused):
        sd.load_ceilings(path)


def test_evaluate_ceilings_passes_within_the_ceiling_and_fails_above_it():
    counts = sd.DiffCounts(missing=2, extra=0, signature=0, static=0, default=0, enum_value=0, checked=10)
    assert (
        sd.evaluate_ceilings(
            counts, {"missing": 2, "extra": 0, "signature": 0, "static": 0, "default": 0, "enum_value": 0}
        )
        == []
    )
    reasons = sd.evaluate_ceilings(counts, {category: 0 for category in sd.CATEGORIES})
    assert reasons and "missing" in reasons[0]


# --- verify_pilot: the combined per-pilot verdict --------------------------------------------------


def test_verify_pilot_passes_when_the_oracle_matches_and_the_version_is_current():
    fixture = {
        "extractor_version": 5,
        "types": [_baked_type("Widget", methods=[{"name": "build", "params": []}])],
    }
    symbols = [OS("Widget", "class", members=(OM("build", "method"),))]
    report = sd.verify_pilot(
        pilot="pdf_python",
        language="python",
        fixture=fixture,
        symbols=symbols,
        ceilings=sd.LANGUAGE_ERROR_CEILINGS,
        require_extractor_version=True,
        current_extractor_version=5,
    )
    assert report.passed is True
    assert report.as_dict()["status"] == "pass"


def test_verify_pilot_fails_on_a_stale_extractor_version_even_with_a_perfect_diff():
    fixture = {
        "extractor_version": 1,
        "types": [_baked_type("Widget", methods=[{"name": "build", "params": []}])],
    }
    symbols = [OS("Widget", "class", members=(OM("build", "method"),))]
    report = sd.verify_pilot(
        pilot="pdf_python",
        language="python",
        fixture=fixture,
        symbols=symbols,
        ceilings=sd.LANGUAGE_ERROR_CEILINGS,
        require_extractor_version=True,
        current_extractor_version=5,
    )
    assert report.passed is False
    assert report.extractor_version_reason is not None and "pilot" not in ""  # sanity: reason is set
    assert report.as_dict()["status"] == "fail"


def test_verify_pilot_skips_the_version_gate_when_told_to():
    fixture = {"types": []}  # no extractor_version key at all
    report = sd.verify_pilot(
        pilot="pdf_python",
        language="python",
        fixture=fixture,
        symbols=[],
        ceilings=sd.LANGUAGE_ERROR_CEILINGS,
        require_extractor_version=False,
        current_extractor_version=5,
    )
    assert report.extractor_version_reason is None
    assert report.passed is True


def test_verify_pilot_fails_when_a_ceiling_is_exceeded():
    fixture = {"extractor_version": 5, "types": [_baked_type("Widget", methods=[])]}
    symbols = [OS("Widget", "class", members=(OM("build", "method"),))]  # missing: 1
    report = sd.verify_pilot(
        pilot="pdf_python",
        language="python",
        fixture=fixture,
        symbols=symbols,
        ceilings=sd.LANGUAGE_ERROR_CEILINGS,  # every ceiling 0
        require_extractor_version=True,
        current_extractor_version=5,
    )
    assert report.passed is False
    assert any("missing" in reason for reason in report.reasons)
    assert report.findings and report.findings[0]["category"] == "missing"


# --- per-language offline micro-libraries (acceptance (a)) -------------------------------------------
# One hand-written oracle per language this card names, each matched against a hand-written baked
# fixture type that agrees with it exactly, then a second, deliberately wrong baked type proving the
# diff engine actually reports a real per-category count for that language's own member shapes.

_LANGUAGE_MICRO_LIBRARIES = [
    pytest.param(
        "python",
        OS("Gadget", "class", members=(OM("build", "method", params=(OP("name"),), is_static=False),)),
        _baked_type("Gadget", methods=[{"name": "build", "params": [{"name": "name"}], "modifiers": []}]),
        _baked_type("Gadget", methods=[{"name": "build", "params": [], "modifiers": ["classmethod"]}]),
        id="python",
    ),
    pytest.param(
        "typescript",
        OS("Page", "class", members=(OM("AddText", "method", params=(OP("text"),), is_static=False),)),
        _baked_type("Page", methods=[{"name": "AddText", "params": [{"name": "text"}], "modifiers": []}]),
        _baked_type("Page", methods=[{"name": "AddText", "params": [{"name": "text"}, {"name": "x"}]}]),
        id="typescript",
    ),
    pytest.param(
        "go",
        OS("Document", "struct", members=(OM("Save", "method", params=(OP("path"),)),)),
        _baked_type("Document", methods=[{"name": "Save", "params": [{"name": "path"}]}]),
        _baked_type("Document", methods=[]),
        id="go",
    ),
    pytest.param(
        "rust",
        OS("Workbook", "struct", members=(OM("new", "method", is_static=True),)),
        _baked_type("Workbook", methods=[{"name": "new", "params": [], "modifiers": ["static"]}]),
        _baked_type("Workbook", methods=[{"name": "new", "params": [], "modifiers": []}]),
        id="rust",
    ),
    pytest.param(
        "java",
        OS("Document", "class", members=(OM("getPages", "method", is_static=False),)),
        _baked_type("Document", methods=[{"name": "getPages", "params": [], "modifiers": []}]),
        _baked_type("Document", methods=[{"name": "getPages", "params": [], "modifiers": ["static"]}]),
        id="java",
    ),
    pytest.param(
        "net",
        OS("Workbook", "class", members=(OM("Save", "method", params=(OP("path"),)),)),
        _baked_type("Workbook", methods=[{"name": "Save", "params": [{"name": "path"}]}]),
        _baked_type("Workbook", methods=[{"name": "Save", "params": [{"name": "path", "default": "null"}]}]),
        id="net",
    ),
    pytest.param(
        "cpp",
        OS("Workbook", "class", members=(OM("Save", "method", params=(OP("path"),)),)),
        _baked_type(
            "Workbook", methods=[{"name": "Save", "params": [{"name": "path"}], "modifiers": ["const"]}]
        ),
        _baked_type(
            "Workbook", methods=[{"name": "Save", "params": [{"name": "path"}], "modifiers": ["static"]}]
        ),
        id="cpp",
    ),
]


@pytest.mark.parametrize(("language", "oracle", "matching_baked", "wrong_baked"), _LANGUAGE_MICRO_LIBRARIES)
def test_per_language_micro_library_matches_exactly(language, oracle, matching_baked, wrong_baked):
    counts, findings = sd.diff_symbol(oracle, matching_baked)
    assert counts.as_dict()["missing"] == 0 and counts.as_dict()["extra"] == 0
    assert findings == [], language


@pytest.mark.parametrize(("language", "oracle", "matching_baked", "wrong_baked"), _LANGUAGE_MICRO_LIBRARIES)
def test_per_language_micro_library_reports_a_real_disagreement(
    language, oracle, matching_baked, wrong_baked
):
    counts, findings = sd.diff_symbol(oracle, wrong_baked)
    total_errors = sum(getattr(counts, category) for category in sd.CATEGORIES)
    assert total_errors >= 1, language
    assert findings, language


def test_every_card_named_language_has_a_micro_library_case():
    covered = {param.id for param in _LANGUAGE_MICRO_LIBRARIES}
    assert covered == {"python", "typescript", "go", "rust", "java", "net", "cpp"}


# --- the Python ast enumerator: the one real, wired tool this card lands -----------------------------


def test_enumerate_python_reads_a_real_class_with_ast_offline(tmp_path):
    (tmp_path / ".git").mkdir()  # pretend already-cloned, so enumerate_python never shells out to git
    (tmp_path / "widgets.py").write_text(
        "class Gadget:\n"
        "    def build(self, name, count=1):\n"
        "        return name\n"
        "    @staticmethod\n"
        "    def make():\n"
        "        return Gadget()\n"
        "    def _private(self):\n"
        "        pass\n",
        encoding="utf-8",
    )
    symbols = sd.enumerate_python("acme/widgets", "deadbeef", tmp_path)
    (gadget,) = [s for s in symbols if s.name == "Gadget"]
    by_name = {member.name: member for member in gadget.members}
    assert set(by_name) == {"build", "make"}  # _private is never public
    assert by_name["build"].params == (OP("name"), OP("count", has_default=True))
    assert by_name["make"].is_static is True


def test_enumerate_python_reads_enum_members_offline(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "colors.py").write_text(
        "from enum import Enum\n\nclass Color(Enum):\n    Red = 1\n    Blue = 2\n    _Hidden = 3\n",
        encoding="utf-8",
    )
    (color,) = sd.enumerate_python("acme/widgets", "deadbeef", tmp_path)
    assert color.kind == "enum"
    names = {member.name for member in color.members}
    assert names == {"Red", "Blue"}


def test_enumerate_python_skips_a_file_that_fails_to_parse(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    (tmp_path / "good.py").write_text("class Fine:\n    def ok(self):\n        pass\n", encoding="utf-8")
    symbols = sd.enumerate_python("acme/widgets", "deadbeef", tmp_path)
    assert [s.name for s in symbols] == ["Fine"]


# --- the Go go/parser+go/ast enumerator: the second real, wired tool this card lands -----------------
# Every fixture below is a hand-written synthetic .go file (never the real pilot, never the
# enumerator's own output read back as the expected value). enumerate_go really invokes the Go
# toolchain's own go/parser+go/ast (through scripts/release/oracle_helpers/go_enumerator.go, run
# with `go run`) against these files on local disk -- no network, matching this card's network:
# false requirement, since the Go toolchain itself is a declared local `requires:` entry, never
# something reached over the wire.


def test_enumerate_go_reads_a_real_struct_with_go_ast_offline(tmp_path):
    (tmp_path / ".git").mkdir()  # pretend already-cloned, so enumerate_go never shells out to git
    (tmp_path / "document.go").write_text(
        "package pdf\n\n"
        "type Document struct {\n"
        "\tTitle string\n"
        "\tpages []int\n"
        "}\n\n"
        "func (d *Document) Save(path string, overwrite bool) error { return nil }\n"
        "func (d Document) pageCount() int { return 0 }\n",
        encoding="utf-8",
    )
    symbols = sd.enumerate_go("acme/pdf", "deadbeef", tmp_path)
    (document,) = [s for s in symbols if s.name == "Document"]
    assert document.kind == "struct"
    by_name = {member.name: member for member in document.members}
    assert set(by_name) == {"Title", "Save"}  # pageCount is unexported and must not appear
    assert by_name["Title"].kind == "property"
    assert by_name["Save"].kind == "method"
    assert by_name["Save"].params == (OP("path"), OP("overwrite"))
    assert by_name["Save"].is_static is False


def test_enumerate_go_reads_an_interface_and_its_methods_offline(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "reader.go").write_text(
        "package pdf\n\n"
        "type Reader interface {\n"
        "\tRead(path string) (*Document, error)\n"
        "\tunexportedProbe()\n"
        "}\n",
        encoding="utf-8",
    )
    (reader,) = sd.enumerate_go("acme/pdf", "deadbeef", tmp_path)
    assert reader.kind == "interface"
    names = {member.name for member in reader.members}
    assert names == {"Read"}  # unexportedProbe is never public
    (read,) = [m for m in reader.members if m.name == "Read"]
    assert read.params == (OP("path"),)


def test_enumerate_go_defect_an_unexported_field_is_excluded_from_the_result(tmp_path):
    """Deliberate defect fixture 1: a struct field that must be absent because it is unexported."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "widget.go").write_text(
        "package widgets\n\n"
        "type Widget struct {\n"
        "\tName string\n"  # exported: must appear
        "\tinternal int\n"  # unexported (lowercase): must NOT appear
        "}\n",
        encoding="utf-8",
    )
    (widget,) = sd.enumerate_go("acme/widgets", "deadbeef", tmp_path)
    names = {member.name for member in widget.members}
    assert names == {"Name"}, "the enumerator must exclude the unexported 'internal' field"
    assert "internal" not in names


def test_enumerate_go_defect_pointer_receiver_params_are_read_exactly(tmp_path):
    """Deliberate defect fixture 2: a pointer-receiver method's own params, not the receiver
    itself, must be returned, in order, even when several params share one type."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "shape.go").write_text(
        "package shapes\n\n"
        "type Shape struct {\n"
        "\tName string\n"
        "}\n\n"
        "func (s *Shape) Resize(width, height int, label string) error { return nil }\n",
        encoding="utf-8",
    )
    (shape,) = sd.enumerate_go("acme/shapes", "deadbeef", tmp_path)
    (resize,) = [m for m in shape.members if m.name == "Resize"]
    assert resize.params == (OP("width"), OP("height"), OP("label")), (
        "a pointer receiver must never leak into the params list, and every real param must survive"
    )


def test_enumerate_go_attaches_methods_declared_in_a_different_file(tmp_path):
    """A real Go package routinely splits one type's methods across several files; both halves
    must be attached to the same symbol, and a method for a type that is never declared is
    dropped rather than fabricating a phantom symbol."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "document.go").write_text(
        "package pdf\n\ntype Document struct {\n\tTitle string\n}\n\n"
        "func (d *Document) Save(path string) error { return nil }\n",
        encoding="utf-8",
    )
    (tmp_path / "document_io.go").write_text(
        "package pdf\n\n"
        "func (d *Document) Close() error { return nil }\n"
        "func (m *Missing) Run() {}\n",  # Missing is never declared: must not create a symbol
        encoding="utf-8",
    )
    symbols = sd.enumerate_go("acme/pdf", "deadbeef", tmp_path)
    assert [s.name for s in symbols] == ["Document"]
    (document,) = symbols
    names = {member.name for member in document.members}
    assert names == {"Title", "Save", "Close"}


def test_enumerate_go_skips_a_file_that_fails_to_parse(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "broken.go").write_text("package pdf\n\nfunc broken(:\n", encoding="utf-8")
    (tmp_path / "good.go").write_text("package pdf\n\ntype Fine struct {\n\tOK bool\n}\n", encoding="utf-8")
    symbols = sd.enumerate_go("acme/pdf", "deadbeef", tmp_path)
    assert [s.name for s in symbols] == ["Fine"]


def test_enumerate_go_never_enumerates_bare_package_level_functions(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "helpers.go").write_text(
        "package pdf\n\nfunc Helper() {}\n\ntype Document struct {\n\tTitle string\n}\n",
        encoding="utf-8",
    )
    (document,) = sd.enumerate_go("acme/pdf", "deadbeef", tmp_path)
    assert {member.name for member in document.members} == {"Title"}


# --- the Rust `syn` enumerator: the third real, wired tool this card lands (G3/TC-590) ---------
# Every fixture below is a hand-written synthetic .rs file (never the real pilot, never the
# enumerator's own output read back as the expected value). enumerate_rust really invokes Docker
# (docker run ... cargo run --release --offline, against the pinned rust:1.98.1 image and this
# crate's own vendored dependencies -- see scripts/release/oracle_helpers/rust_enumerator/) against
# these files on local disk -- no network, matching this card's network: false requirement, since
# both the Docker daemon and the already-cached pinned image are this card's own declared local
# requires: entry, never something reached over the wire mid-check. This also means every test
# below takes real wall-clock time (a real container start), unlike the pure-Python diff tests
# above -- that cost is the whole point: it is what proves the WDAC-driven Docker rework works at
# all, not merely that its own source compiles.


def test_enumerate_rust_reads_a_real_struct_with_syn_offline(tmp_path):
    (tmp_path / ".git").mkdir()  # pretend already-cloned, so enumerate_rust never shells out to git
    (tmp_path / "document.rs").write_text(
        "pub struct Document {\n"
        "    pub title: String,\n"
        "    pages: Vec<i32>,\n"  # not pub: must not appear
        "}\n\n"
        "impl Document {\n"
        "    pub fn save(&self, path: String, overwrite: bool) -> bool { true }\n"
        "    fn page_count(&self) -> i32 { 0 }\n"  # not pub: must not appear
        "}\n",
        encoding="utf-8",
    )
    symbols = sd.enumerate_rust("acme/pdf", "deadbeef", tmp_path)
    (document,) = [s for s in symbols if s.name == "Document"]
    assert document.kind == "class"
    by_name = {member.name: member for member in document.members}
    assert set(by_name) == {"title", "save"}, "page_count and pages must not appear"
    assert by_name["title"].kind == "property"
    assert by_name["save"].kind == "method"
    assert by_name["save"].params == (OP("path"), OP("overwrite"))
    assert by_name["save"].is_static is False  # &self receiver


def test_enumerate_rust_defect_a_non_pub_field_is_excluded_from_the_result(tmp_path):
    """Deliberate defect fixture 1: a struct field that must be absent because it is not pub."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "widget.rs").write_text(
        "pub struct Widget {\n"
        "    pub name: String,\n"  # pub: must appear
        "    internal: i32,\n"  # not pub: must NOT appear
        "}\n",
        encoding="utf-8",
    )
    (widget,) = sd.enumerate_rust("acme/widgets", "deadbeef", tmp_path)
    names = {member.name for member in widget.members}
    assert names == {"name"}, "the enumerator must exclude the non-pub 'internal' field"
    assert "internal" not in names


def test_enumerate_rust_defect_enum_discriminants_are_read_exactly_not_merely_counted(tmp_path):
    """Deliberate defect fixture 2: an enum's explicit discriminant values must be read exactly,
    not merely counted -- a bug that counts variants right but gets a value wrong must fail this."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "status.rs").write_text(
        "pub enum Status {\n"
        "    Ok = 0,\n"
        "    Bad = 2,\n"
        "    Unset,\n"  # no explicit discriminant: enum_value stays "not checked" (None)
        "}\n",
        encoding="utf-8",
    )
    (status,) = sd.enumerate_rust("acme/widgets", "deadbeef", tmp_path)
    assert status.kind == "enum"
    by_name = {member.name: member for member in status.members}
    assert set(by_name) == {"Ok", "Bad", "Unset"}
    assert by_name["Ok"].kind == "enum_value"
    assert by_name["Ok"].enum_value == "0"
    assert by_name["Bad"].enum_value == "2"
    assert by_name["Unset"].enum_value is None


def test_enumerate_rust_is_macro_expansion_blind_and_does_not_crash(tmp_path):
    """The design table's own fidelity note: "Full (macro-expansion-blind, disclosed)". A type
    produced only inside a macro_rules! expansion is correctly absent, never crashed on, never
    fabricated -- this is what makes the "disclosed" claim in rust_enumerator's own module
    docstring an actually-tested fact, not merely prose."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "phantom.rs").write_text(
        "pub struct Real {\n    pub value: i32,\n}\n\n"
        "macro_rules! make_struct {\n"
        "    ($name:ident) => {\n"
        "        pub struct $name { pub value: i32 }\n"
        "    };\n"
        "}\n"
        "make_struct!(Phantom);\n",
        encoding="utf-8",
    )
    symbols = sd.enumerate_rust("acme/widgets", "deadbeef", tmp_path)
    names = {s.name for s in symbols}
    assert "Real" in names, "the real, syntactically-present struct must still be read"
    assert "Phantom" not in names, (
        "syn parses syntax, not macro-expanded output -- a macro-only type must be silently "
        "absent, never fabricated and never a crash"
    )


def test_enumerate_rust_reads_a_trait_and_its_methods(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "reader.rs").write_text(
        "pub trait Reader {\n    fn read(&self, path: String) -> bool;\n}\n",
        encoding="utf-8",
    )
    (reader,) = sd.enumerate_rust("acme/pdf", "deadbeef", tmp_path)
    assert reader.kind == "class"  # a trait maps to kind "class", matching the card
    (read,) = reader.members
    assert read.kind == "method"
    assert read.params == (OP("path"),)
    assert read.is_static is False  # &self receiver


def test_enumerate_rust_attaches_impl_methods_declared_in_a_different_file(tmp_path):
    """A real Rust crate routinely splits one type's impl blocks across several files; both
    halves must be attached to the same symbol, and a method for a type that is never declared
    is dropped rather than fabricating a phantom symbol."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "document.rs").write_text(
        "pub struct Document {\n    pub title: String,\n}\n\n"
        "impl Document {\n    pub fn save(&self, path: String) -> bool { true }\n}\n",
        encoding="utf-8",
    )
    (tmp_path / "document_io.rs").write_text(
        "impl Document {\n    pub fn close(&self) -> bool { true }\n}\n"
        "impl Missing {\n    pub fn run(&self) {}\n}\n",  # Missing is never declared: no symbol
        encoding="utf-8",
    )
    symbols = sd.enumerate_rust("acme/pdf", "deadbeef", tmp_path)
    assert [s.name for s in symbols] == ["Document"]
    (document,) = symbols
    names = {member.name for member in document.members}
    assert names == {"title", "save", "close"}


def test_enumerate_rust_marks_a_receiverless_associated_function_as_static(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "shape.rs").write_text(
        "pub struct Shape {\n    pub name: String,\n}\n\n"
        "impl Shape {\n"
        "    pub fn new(name: String) -> Self { Shape { name } }\n"  # no self: static
        "    pub fn resize(&mut self, width: i32, height: i32) {}\n"  # &mut self: not static
        "}\n",
        encoding="utf-8",
    )
    (shape,) = sd.enumerate_rust("acme/shapes", "deadbeef", tmp_path)
    by_name = {member.name: member for member in shape.members}
    assert by_name["new"].is_static is True
    assert by_name["new"].params == (OP("name"),)
    assert by_name["resize"].is_static is False
    assert by_name["resize"].params == (OP("width"), OP("height"))


def test_enumerate_rust_skips_a_file_that_fails_to_parse(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "broken.rs").write_text("pub struct Broken {\n", encoding="utf-8")
    (tmp_path / "good.rs").write_text("pub struct Fine {\n    pub ok: bool,\n}\n", encoding="utf-8")
    symbols = sd.enumerate_rust("acme/pdf", "deadbeef", tmp_path)
    assert [s.name for s in symbols] == ["Fine"]


def test_run_rust_enumerator_shells_out_to_the_pinned_docker_image_mounting_only_the_crate_and_library(
    tmp_path,
):
    """Offline check of the Docker invocation's own shape (never a real container here): the
    exact pinned image tag, the crate directory mounted read-write at /work, the library
    directory mounted read-only at /library, and never a bare host-side cargo build/run."""
    import subprocess as _subprocess

    captured: dict[str, object] = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _subprocess.CompletedProcess(args=args, returncode=0, stdout="[]", stderr="")

    library_dir = tmp_path / "library"
    library_dir.mkdir()
    result = sd._run_rust_enumerator(library_dir, run=fake_run)
    assert result == []
    args = captured["args"]
    assert args[:3] == ["docker", "run", "--rm"]
    assert "rust:1.98.1" in args
    assert any(a == f"{sd.RUST_ENUMERATOR_CRATE_DIR}:/work" for a in args)
    assert any(a == f"{library_dir}:/library:ro" for a in args)
    assert "--offline" in args
    assert not any(a in ("build", "install") for a in args), "never a bare host-side cargo build"


def test_run_rust_enumerator_raises_with_the_image_name_on_a_nonzero_docker_exit(tmp_path):
    import subprocess as _subprocess

    def failing_run(args, **kwargs):
        return _subprocess.CompletedProcess(
            args=args, returncode=1, stdout="", stderr="docker: daemon unreachable"
        )

    with pytest.raises(RuntimeError, match="rust:1.98.1"):
        sd._run_rust_enumerator(tmp_path, run=failing_run)


def test_unimplemented_enumerators_raise_a_named_not_implemented_error():
    for platform in ("typescript", "java", "net", "cpp"):
        with pytest.raises(NotImplementedError, match=platform):
            sd.DEFAULT_ENUMERATORS[platform]("acme/widgets", "deadbeef", Path("/does/not/matter"))


def test_default_enumerators_cover_python_and_every_card_named_platform():
    assert sd.DEFAULT_ENUMERATORS["python"] is sd.enumerate_python
    assert sd.DEFAULT_ENUMERATORS["go"] is sd.enumerate_go
    assert sd.DEFAULT_ENUMERATORS["rust"] is sd.enumerate_rust
    assert {"typescript", "javascript", "nodejs", "go", "rust", "java", "net", "dotnet", "cpp"} <= set(
        sd.DEFAULT_ENUMERATORS
    )


# --- main(): CLI wiring, offline, injected enumerators ------------------------------------------------


def test_main_passes_a_matching_pilot_and_writes_the_report(tmp_path):
    fixtures = tmp_path / "fixtures"
    key = sd.chart.pilot_key(sd.chart.load_pilots()[0])
    platform = sd.chart.load_pilots()[0]["platform"]
    (fixtures / key).mkdir(parents=True)
    (fixtures / key / "api_surface.json").write_text(
        json.dumps(
            {"extractor_version": sd.run_extraction.EXTRACTOR_VERSION, "types": [_baked_type("Widget")]}
        ),
        encoding="utf-8",
    )
    report_dir = tmp_path / "report"
    deps = sd.Deps(enumerators={platform: lambda repo, commit, lib: [OS("Widget", "class", members=())]})
    code = sd.main(["--fixtures", str(fixtures), "--only", key, "--report-dir", str(report_dir)], deps)
    assert code == 0
    document = json.loads((report_dir / sd.REPORT_NAME).read_text(encoding="utf-8"))
    assert document["verdict"] == "PASS"
    assert document["pilots"][0]["pilot"] == key


def test_main_fails_when_the_injected_enumerator_finds_a_disagreement(tmp_path):
    fixtures = tmp_path / "fixtures"
    key = sd.chart.pilot_key(sd.chart.load_pilots()[0])
    platform = sd.chart.load_pilots()[0]["platform"]
    (fixtures / key).mkdir(parents=True)
    (fixtures / key / "api_surface.json").write_text(
        json.dumps(
            {
                "extractor_version": sd.run_extraction.EXTRACTOR_VERSION,
                "types": [_baked_type("Widget", methods=[])],
            }
        ),
        encoding="utf-8",
    )
    report_dir = tmp_path / "report"
    deps = sd.Deps(
        enumerators={
            platform: lambda repo, commit, lib: [OS("Widget", "class", members=(OM("build", "method"),))]
        }
    )
    code = sd.main(["--fixtures", str(fixtures), "--only", key, "--report-dir", str(report_dir)], deps)
    assert code == sd.EXIT_FAILED
    document = json.loads((report_dir / sd.REPORT_NAME).read_text(encoding="utf-8"))
    assert document["verdict"] == "FAIL"


def test_main_refuses_an_unknown_only_name(tmp_path, capsys):
    code = sd.main(["--only", "no_such_pilot", "--report-dir", str(tmp_path / "r")], sd.Deps())
    assert code == sd.EXIT_REFUSED
    assert "no_such_pilot" in capsys.readouterr().err
    assert not (tmp_path / "r").exists()


def test_main_names_the_pilot_when_its_fixture_cannot_be_read(tmp_path):
    key = sd.chart.pilot_key(sd.chart.load_pilots()[0])
    report_dir = tmp_path / "report"
    code = sd.main(
        ["--fixtures", str(tmp_path / "nonexistent"), "--only", key, "--report-dir", str(report_dir)],
        sd.Deps(),
    )
    assert code == sd.EXIT_FAILED
    document = json.loads((report_dir / sd.REPORT_NAME).read_text(encoding="utf-8"))
    assert document["pilots"][0]["pilot"] == key
    assert any("could not read" in reason for reason in document["pilots"][0]["reasons"])


def test_main_names_the_pilot_when_no_enumerator_is_registered_for_its_platform(tmp_path):
    fixtures = tmp_path / "fixtures"
    key = sd.chart.pilot_key(sd.chart.load_pilots()[0])
    (fixtures / key).mkdir(parents=True)
    (fixtures / key / "api_surface.json").write_text(
        json.dumps({"extractor_version": sd.run_extraction.EXTRACTOR_VERSION, "types": []}), encoding="utf-8"
    )
    report_dir = tmp_path / "report"
    code = sd.main(
        ["--fixtures", str(fixtures), "--only", key, "--report-dir", str(report_dir)], sd.Deps(enumerators={})
    )
    assert code == sd.EXIT_FAILED
    document = json.loads((report_dir / sd.REPORT_NAME).read_text(encoding="utf-8"))
    assert any("no enumerator registered" in reason for reason in document["pilots"][0]["reasons"])


def test_main_reports_not_implemented_for_an_unwired_platform(tmp_path):
    fixtures = tmp_path / "fixtures"
    entry = next(p for p in sd.chart.load_pilots() if p["platform"] != "python")
    key = sd.chart.pilot_key(entry)
    (fixtures / key).mkdir(parents=True)
    (fixtures / key / "api_surface.json").write_text(
        json.dumps({"extractor_version": sd.run_extraction.EXTRACTOR_VERSION, "types": []}), encoding="utf-8"
    )
    report_dir = tmp_path / "report"
    code = sd.main(
        ["--fixtures", str(fixtures), "--only", key, "--report-dir", str(report_dir)],
        sd.Deps(enumerators=sd.DEFAULT_ENUMERATORS),
    )
    assert code == sd.EXIT_FAILED
    document = json.loads((report_dir / sd.REPORT_NAME).read_text(encoding="utf-8"))
    assert any("no real" in reason for reason in document["pilots"][0]["reasons"])


def test_main_never_uses_kubectl_for_cluster_orchestration():
    assert "kubectl" not in SOURCE.read_text(encoding="utf-8").lower()


def test_docker_is_confined_to_the_rust_enumerators_own_container_invocation():
    """REWORK (G3/TC-590) narrows this file's original "docker is never used for network or
    cluster use" guarantee: enumerate_rust now legitimately shells out to Docker, because this
    host's Windows Application Control policy blocks execution of a freshly-built, unsigned
    native .exe directly on the host (plans/TC-590.yaml's REWORK note). This proves the narrower
    claim that still matters -- main()'s own CLI parsing and orchestration path never mentions
    Docker, and the mention that does exist is confined to the Rust-specific enumerator, not
    spread into this module's general-purpose plumbing."""
    import ast as _ast

    source = SOURCE.read_text(encoding="utf-8")
    tree = _ast.parse(source)
    top_level_functions = {node.name: node for node in tree.body if isinstance(node, _ast.FunctionDef)}
    for name in ("main", "parse_args", "diff_symbol", "diff_language", "verify_pilot"):
        segment = _ast.get_source_segment(source, top_level_functions[name]) or ""
        assert "docker" not in segment.lower(), f"{name} must never mention Docker"
    assert "docker" in source.lower(), "enumerate_rust must still reference Docker somewhere"
    assert "helm" not in source.lower()
