"""G3/TC-566: scripts/release/source_differential.py, offline.

Every expectation here comes from a HAND-WRITTEN oracle (an ``OracleSymbol``/``OracleMember`` the
test builds itself, standing in for "the real upstream source, read with the language's own tool"),
never from a committed fixture or the extractor's own output. That is the whole point of this card
(RD3-150): a test whose expectation is derived from the thing it is checking can never see that thing
lose or fabricate data. No network call is made by any test below -- the per-language
``Enumerator`` callables are injected (``Deps.enumerators``), copying
``scripts/release/verify_pushed_image.py``'s own injection style, so even the few tests that exercise
``main()`` never touch the real git path.

The ``enumerate_go`` tests (G3/TC-596) really invoke Docker (``docker run ... go run
go_enumerator.go``, against the pinned, already-cached ``golang:1.26.4`` image), and the
``enumerate_rust`` tests (G3/TC-590) really invoke Docker (``docker run ... cargo run --release
--offline``, against the pinned, already-cached ``rust:1.98.1`` image and the companion crate's
own vendored dependencies) against hand-written, on-disk synthetic ``.go``/``.rs`` fixtures --
never the real pilot, never a network call, since the Docker daemon with both pinned images
already cached is this card's own declared local ``requires:``, never something reached over the
wire mid-check. Both enumerators moved off the host's own toolchain and into a pinned container
because this host's Windows Application Control policy blocks execution of a freshly-built,
unsigned native ``.exe`` directly on the host (first observed in TC-590's Rust rework, OWNER-16;
TC-596 applies the identical fix to Go).

The ``enumerate_typescript`` tests (G3/TC-591) really invoke ``node`` directly against
hand-written, on-disk synthetic ``.ts`` fixtures through the real TypeScript Compiler API
(``scripts/release/oracle_helpers/ts_enumerator.js``) -- never the real pilot, never a network
call, never Docker. ``node`` executing a plain ``.js`` file is the already-trusted host
interpreter, not a freshly-built native binary, so the WDAC workaround the Go/Rust enumerators
need never applies here; this was confirmed directly (not merely assumed) before this card's
own enumerator was wired in.

The ``enumerate_java`` tests (G3/TC-592) really invoke ``java`` directly (its own single-file
source-launcher, ``java JavaEnumerator.java <dir>``) against hand-written, on-disk synthetic
``.java`` fixtures through the real JDK Compiler Tree API
(``scripts/release/oracle_helpers/JavaEnumerator.java``), PARSE PHASE ONLY -- never the real
pilot, never a network call, never Docker, and needing no ``--add-exports`` flag at all (this
was confirmed directly, not merely assumed: ``com.sun.source.*`` is the jdk.compiler module's
own unconditionally-exported public API, unlike ``com.sun.tools.javac.*``'s internals). ``java``
compiling and running a plain ``.java`` text file in memory is the already-trusted host
interpreter, not a freshly-built native binary on disk, so the WDAC workaround the Go/Rust
enumerators need never applies here either, exactly like TypeScript's own ``node`` case.
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
# enumerator's own output read back as the expected value). enumerate_go really invokes Go's own
# go/parser+go/ast (through scripts/release/oracle_helpers/go_enumerator.go, run with `go run`)
# against these files on local disk, built AND run entirely inside a pinned Docker container
# (docker run ... go run go_enumerator.go, against the pinned, already-cached golang:1.26.4 image
# -- G3/TC-596, mirroring enumerate_rust's own container rework below) -- no network, matching
# this card's network: false requirement, since the Docker daemon with its pinned image already
# cached is this card's own declared local `requires:` entry, never something reached over the
# wire mid-check. This also means every test below takes real wall-clock time (a real container
# start), same as the Rust tests further down.


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


def test_run_go_enumerator_shells_out_to_the_pinned_docker_image_mounting_the_helpers_dir_and_library(
    tmp_path,
):
    """Offline check of the Docker invocation's own shape (never a real container here): the
    exact pinned image tag, the oracle_helpers directory mounted read-only at /work, the library
    directory mounted read-write at /library, and never a bare host-side `go run` (G3/TC-596,
    mirroring the Rust check below)."""
    import subprocess as _subprocess

    captured: dict[str, object] = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _subprocess.CompletedProcess(args=args, returncode=0, stdout="[]", stderr="")

    library_dir = tmp_path / "library"
    library_dir.mkdir()
    result = sd._run_go_enumerator(library_dir, run=fake_run)
    assert result == []
    args = captured["args"]
    assert args[:3] == ["docker", "run", "--rm"]
    assert "golang:1.26.4" in args
    assert any(a == f"{sd._ORACLE_HELPERS_DIR}:/work:ro" for a in args)
    assert any(a == f"{library_dir}:/library" for a in args)
    assert not any(a == f"{library_dir}:/library:ro" for a in args), (
        "library_dir must be mounted read-write, not read-only"
    )
    assert "go" in args and "run" in args, "the companion program must still be invoked with go run"


def test_run_go_enumerator_raises_with_the_image_name_on_a_nonzero_docker_exit(tmp_path):
    import subprocess as _subprocess

    def failing_run(args, **kwargs):
        return _subprocess.CompletedProcess(
            args=args, returncode=1, stdout="", stderr="docker: daemon unreachable"
        )

    with pytest.raises(RuntimeError, match="golang:1.26.4"):
        sd._run_go_enumerator(tmp_path, run=failing_run)


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


# --- the TypeScript Compiler API enumerator: the fourth real, wired tool this card lands -------------
# Every fixture below is a hand-written synthetic .ts file (never the real pilot, never the
# enumerator's own output read back as the expected value). enumerate_typescript really invokes
# the REAL TypeScript Compiler API (ts.createProgram + its own TypeChecker, through
# scripts/release/oracle_helpers/ts_enumerator.js, run with `node` -- the already-trusted host
# interpreter, no Docker, no fresh native binary, so none of these tests need the Windows
# Application Control workaround enumerate_go/enumerate_rust need; G3/TC-591) against these
# files on local disk -- real wall-clock time (a real `node` subprocess + a real TypeScript
# type-check), same as the Go/Rust tests above, but no container start.


def test_enumerate_typescript_reads_a_real_class_with_the_compiler_api_offline(tmp_path):
    (tmp_path / ".git").mkdir()  # pretend already-cloned, so enumerate_typescript never shells out to git
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "widget.ts").write_text(
        "export class Gadget {\n"
        "  build(name: string, count: number = 1): string {\n"
        "    return name;\n"
        "  }\n"
        "  private hide(): void {}\n"
        "}\n",
        encoding="utf-8",
    )
    symbols = sd.enumerate_typescript("acme/widgets", "deadbeef", tmp_path)
    (gadget,) = [s for s in symbols if s.name == "Gadget"]
    assert gadget.kind == "class"  # an interface would ALSO read back as "class" -- see the
    # module's own enumerate_typescript docstring for why
    by_name = {member.name: member for member in gadget.members}
    assert set(by_name) == {"build"}  # hide is private and must never appear
    assert by_name["build"].params == (OP("name"), OP("count", has_default=True))
    assert by_name["build"].is_static is False


def test_enumerate_typescript_reads_an_interface_and_its_methods_offline(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "reader.ts").write_text(
        "export interface Reader {\n  read(path: string): string;\n}\n",
        encoding="utf-8",
    )
    (reader,) = sd.enumerate_typescript("acme/pdf", "deadbeef", tmp_path)
    assert reader.name == "Reader"
    assert reader.kind == "class"  # interfaces map onto OracleSymbol.kind == "class" too
    (read,) = reader.members
    assert read.name == "read"
    assert read.kind == "method"
    assert read.params == (OP("path"),)
    assert read.is_static is False


def test_enumerate_typescript_defect_a_private_property_is_excluded_from_the_result(tmp_path):
    """Deliberate defect fixture 1: a class property declared `private` that must be absent --
    the card's own single most important rule (no modifier means public by TypeScript's own
    default; only an explicit `private` excludes a member)."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "widget.ts").write_text(
        "export class Widget {\n"
        '  public name: string = "n";\n'  # exported/public: must appear
        "  private internal: number = 0;\n"  # private: must NOT appear
        "}\n",
        encoding="utf-8",
    )
    (widget,) = sd.enumerate_typescript("acme/widgets", "deadbeef", tmp_path)
    names = {member.name for member in widget.members}
    assert names == {"name"}, "the enumerator must exclude the private 'internal' property"
    assert "internal" not in names


def test_enumerate_typescript_defect_an_optional_marker_is_never_confused_with_a_default(tmp_path):
    """Deliberate defect fixture 2: a parameter's `?` optional marker must never be read as
    `has_default` -- only a real initializer (an actual default VALUE) may set it. The pilot's
    own baked api_surface.json fixture keeps "optional" and "default" as two separate fields
    for exactly this reason."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "doc.ts").write_text(
        'export class Doc {\n  build(label?: string): string {\n    return label || "";\n  }\n}\n',
        encoding="utf-8",
    )
    (doc,) = sd.enumerate_typescript("acme/pdf", "deadbeef", tmp_path)
    (build,) = doc.members
    assert build.params == (OP("label", has_default=False),), (
        "an optional '?' parameter with no initializer must never read as has_default=True"
    )


def test_enumerate_typescript_defect_a_method_overload_set_is_never_duplicated_or_dropped(tmp_path):
    """Deliberate defect fixture 3: TypeScript allows several signatures for one
    implementation; the real implementation signature must appear exactly once, never
    duplicated once per overload and never dropped entirely."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "calc.ts").write_text(
        "export class Calc {\n"
        "  add(a: number): number;\n"
        "  add(a: number, b: number): number;\n"
        "  add(a: number, b?: number): number {\n"
        "    return a + (b || 0);\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    (calc,) = sd.enumerate_typescript("acme/widgets", "deadbeef", tmp_path)
    adds = [member for member in calc.members if member.name == "add"]
    assert len(adds) == 1, "an overload set must collapse to exactly one member, never one per signature"
    assert adds[0].params == (OP("a"), OP("b")), "the real, callable implementation signature must win"


def test_enumerate_typescript_marks_a_static_method_is_static(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "workbook.ts").write_text(
        "export class Workbook {\n"
        "  static create(): Workbook {\n"
        "    return new Workbook();\n"
        "  }\n"
        "  save(path: string): void {}\n"
        "}\n",
        encoding="utf-8",
    )
    (workbook,) = sd.enumerate_typescript("acme/widgets", "deadbeef", tmp_path)
    by_name = {member.name: member for member in workbook.members}
    assert by_name["create"].is_static is True
    assert by_name["save"].is_static is False


def test_enumerate_typescript_a_file_with_a_syntax_error_does_not_crash_the_enumerator(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "broken.ts").write_text("export class Broken {\n  build(:\n", encoding="utf-8")
    (tmp_path / "src" / "good.ts").write_text("export class Fine {\n  ok(): void {}\n}\n", encoding="utf-8")
    symbols = sd.enumerate_typescript("acme/pdf", "deadbeef", tmp_path)
    names = {s.name for s in symbols}
    assert "Fine" in names, "a syntax error in one file must never prevent reading another file's class"


def test_enumerate_typescript_scopes_to_the_declared_package_entry_point_when_one_exists(tmp_path):
    """When a package.json with a "types"/"main" entry point exists, only symbols reachable
    from that entry point are read -- an internal, file-local-only export that the entry point
    never re-exports must not appear. This is the real pilot's own shape (G3/TC-591's own
    investigation found the pinned pdf/typescript pilot exports 776 classes/interfaces when
    read per-file, versus 334 when scoped to its own src/index.ts -- within 3 of the baked
    fixture's own 337 class/interface/abstract_class types)."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "package.json").write_text('{"types": "dist/index.d.ts"}\n', encoding="utf-8")
    (tmp_path / "src" / "index.ts").write_text("export { Public } from './internal.js';\n", encoding="utf-8")
    (tmp_path / "src" / "internal.ts").write_text(
        "export class Public {\n  run(): void {}\n}\n"
        "export class NeverReexported {\n  hidden(): void {}\n}\n",
        encoding="utf-8",
    )
    symbols = sd.enumerate_typescript("acme/pdf", "deadbeef", tmp_path)
    names = {s.name for s in symbols}
    assert names == {"Public"}, "only the entry point's own reachable symbols may appear"


def test_run_typescript_enumerator_shells_out_to_node_directly_never_docker(tmp_path):
    """Offline check of the node invocation's own shape (never a real subprocess here): no
    Docker anywhere in the command, the companion script's real path, and both positional
    arguments the script expects."""
    import subprocess as _subprocess

    captured: dict[str, object] = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _subprocess.CompletedProcess(args=args, returncode=0, stdout="[]", stderr="")

    library_dir = tmp_path / "library"
    library_dir.mkdir()
    ts_package_dir = tmp_path / "typescript"
    ts_package_dir.mkdir()
    result = sd._run_typescript_enumerator(library_dir, run=fake_run, typescript_package_dir=ts_package_dir)
    assert result == []
    args = captured["args"]
    assert args[0] == sd.NODE_BINARY == "node"
    assert "docker" not in [str(a).lower() for a in args]
    assert args[1] == str(sd.TS_ENUMERATOR_SOURCE)
    assert args[2] == str(library_dir)
    assert args[3] == str(ts_package_dir)


def test_run_typescript_enumerator_raises_with_a_clear_message_on_a_nonzero_node_exit(tmp_path):
    import subprocess as _subprocess

    ts_package_dir = tmp_path / "typescript"
    ts_package_dir.mkdir()

    def failing_run(args, **kwargs):
        return _subprocess.CompletedProcess(
            args=args, returncode=1, stdout="", stderr="TSError: something failed"
        )

    with pytest.raises(RuntimeError, match="ts_enumerator.js"):
        sd._run_typescript_enumerator(tmp_path, run=failing_run, typescript_package_dir=ts_package_dir)


def test_run_typescript_enumerator_raises_a_clear_error_when_the_typescript_package_is_missing(tmp_path):
    with pytest.raises(RuntimeError, match="typescript npm package"):
        sd._run_typescript_enumerator(tmp_path, typescript_package_dir=tmp_path / "does-not-exist")


# --- the JDK Compiler Tree API enumerator: the fifth real, wired tool this card lands ----------------
# Every fixture below is a hand-written synthetic .java file (never the real pilot, never the
# enumerator's own output read back as the expected value). enumerate_java really invokes the
# REAL JDK Compiler Tree API (com.sun.source.tree/com.sun.source.util, PARSE PHASE ONLY, through
# scripts/release/oracle_helpers/JavaEnumerator.java, run with the JDK's own single-file
# source-launcher `java JavaEnumerator.java <dir>` -- the already-trusted host interpreter, no
# Docker, no fresh native binary, so none of these tests need the Windows Application Control
# workaround enumerate_go/enumerate_rust need; G3/TC-592, confirmed directly the same way TC-591
# confirmed it for node/TypeScript) against these files on local disk -- real wall-clock time (a
# real `java` subprocess + a real parse), same as the TypeScript tests above, but no container
# start and no type resolution at all (parse-phase only, so an unresolvable import never breaks
# anything -- confirmed directly before this enumerator was written; see JavaEnumerator.java's
# own module docstring).


def test_enumerate_java_reads_a_real_class_with_the_compiler_tree_api_offline(tmp_path):
    (tmp_path / ".git").mkdir()  # pretend already-cloned, so enumerate_java never shells out to git
    (tmp_path / "Widget.java").write_text(
        "public class Widget {\n"
        "    public String name;\n"
        "    String packagePrivateField;\n"  # no modifier: package-private, must NOT appear
        "\n"
        "    public void build(String name, int count) {\n"
        "    }\n"
        "\n"
        "    private void helper() {\n"
        "    }\n"  # private: must NOT appear
        "}\n",
        encoding="utf-8",
    )
    symbols = sd.enumerate_java("acme/widgets", "deadbeef", tmp_path)
    (widget,) = [s for s in symbols if s.name == "Widget"]
    assert widget.kind == "class"
    by_name = {member.name: member for member in widget.members}
    assert set(by_name) == {"name", "build"}, "packagePrivateField and helper must never appear"
    assert by_name["name"].kind == "property"
    assert by_name["build"].kind == "method"
    assert by_name["build"].params == (OP("name"), OP("count"))
    assert by_name["build"].is_static is False


def test_enumerate_java_reads_an_interface_and_its_methods_offline(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "Reader.java").write_text(
        "public interface Reader {\n    String read(String path);\n}\n",
        encoding="utf-8",
    )
    (reader,) = sd.enumerate_java("acme/pdf", "deadbeef", tmp_path)
    assert reader.name == "Reader"
    assert reader.kind == "class"  # an interface maps onto OracleSymbol.kind == "class" too
    (read,) = reader.members
    assert read.name == "read"
    assert read.kind == "method"
    assert read.params == (OP("path"),)
    assert read.is_static is False


def test_enumerate_java_defect_a_protected_method_is_excluded_from_the_result(tmp_path):
    """Deliberate defect fixture 1 (the card's own named case): a `protected` method must be
    excluded -- protected is not public, and must never be confused with "not private"."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "Document.java").write_text(
        "public class Document {\n"
        "    public void save(String path) {\n"  # public: must appear
        "    }\n"
        "\n"
        "    protected void adjust() {\n"  # protected: must NOT appear
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    (document,) = sd.enumerate_java("acme/pdf", "deadbeef", tmp_path)
    names = {member.name for member in document.members}
    assert names == {"save"}, "the enumerator must exclude the protected 'adjust' method"
    assert "adjust" not in names


def test_enumerate_java_defect_enum_constructor_arguments_are_read_exactly_not_merely_counted(tmp_path):
    """Deliberate defect fixture 2: an enum constant's own explicit constructor argument must be
    read EXACTLY, not merely counted -- a bug that counts constants right but gets a value wrong
    must fail this. A constant with no constructor argument must report enum_value as None ("not
    checked"), matching enumerate_rust's own "no explicit discriminant" convention."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "Status.java").write_text(
        "public enum Status {\n"
        "    OK(0), BAD(2), UNSET(-7);\n"
        "\n"
        "    private final int code;\n"  # a genuine field: must NOT be read as a 4th constant
        "\n"
        "    Status(int code) {\n"
        "        this.code = code;\n"
        "    }\n"
        "\n"
        "    public int getCode() {\n"
        "        return code;\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    (status,) = sd.enumerate_java("acme/widgets", "deadbeef", tmp_path)
    assert status.kind == "enum"
    by_name = {member.name: member for member in status.members}
    assert set(by_name) == {"OK", "BAD", "UNSET", "getCode"}, "the private 'code' field must never appear"
    assert by_name["OK"].kind == "enum_value"
    assert by_name["OK"].enum_value == "0"
    assert by_name["BAD"].enum_value == "2"
    assert by_name["UNSET"].enum_value == "-7"
    assert by_name["getCode"].kind == "method"


def test_enumerate_java_a_plain_enum_constant_with_no_constructor_args_has_no_enum_value(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "PlainEnum.java").write_text(
        "public enum PlainEnum {\n    None, Left, Center, Right\n}\n", encoding="utf-8"
    )
    (plain,) = sd.enumerate_java("acme/pdf", "deadbeef", tmp_path)
    names = {member.name for member in plain.members}
    assert names == {"None", "Left", "Center", "Right"}
    assert all(member.enum_value is None for member in plain.members)


def test_enumerate_java_an_interface_field_is_always_static_even_without_the_keyword(tmp_path):
    """JLS 9.3: every interface field is implicitly `public static final`, even though the Tree
    API itself never synthesises that implicit `static` modifier (confirmed directly)."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "Limits.java").write_text(
        "public interface Limits {\n    int MAX = 100;\n}\n", encoding="utf-8"
    )
    (limits,) = sd.enumerate_java("acme/widgets", "deadbeef", tmp_path)
    (max_field,) = limits.members
    assert max_field.name == "MAX"
    assert max_field.kind == "property"
    assert max_field.is_static is True


def test_enumerate_java_marks_a_static_method_is_static(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "Workbook.java").write_text(
        "public class Workbook {\n"
        "    public static Workbook create() {\n"
        "        return new Workbook();\n"
        "    }\n"
        "\n"
        "    public void save(String path) {\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    (workbook,) = sd.enumerate_java("acme/widgets", "deadbeef", tmp_path)
    by_name = {member.name: member for member in workbook.members}
    assert by_name["create"].is_static is True
    assert by_name["save"].is_static is False


def test_enumerate_java_a_file_with_a_syntax_error_does_not_crash_the_enumerator(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "Broken.java").write_text("public class Broken {\n", encoding="utf-8")
    (tmp_path / "Fine.java").write_text(
        "public class Fine {\n    public void ok() {\n    }\n}\n", encoding="utf-8"
    )
    symbols = sd.enumerate_java("acme/pdf", "deadbeef", tmp_path)
    names = {s.name for s in symbols}
    assert "Fine" in names, "a syntax error in one file must never prevent reading another file's class"
    assert "Broken" not in names


def test_enumerate_java_skips_a_package_private_top_level_class(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "Internal.java").write_text(
        "class Internal {\n    public void run() {\n    }\n}\n", encoding="utf-8"
    )
    symbols = sd.enumerate_java("acme/widgets", "deadbeef", tmp_path)
    assert symbols == [], "a package-private top-level class is not part of the public API"


def test_enumerate_java_flattens_a_public_nested_enum_into_the_same_symbol_list(tmp_path):
    """Confirmed necessary directly against the real pinned pdf/java pilot: a top-level-only
    reading silently produced zero symbol for roughly a fifth of the pilot's own distinct public
    type names, because they are declared as public static NESTED classes/enums, a routine Java
    API pattern (e.g. a settings class's own nested status enum). The real extractor's own baked
    fixture flattens these into the same flat type list by bare name, so this enumerator must
    too."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "Settings.java").write_text(
        "public class Settings {\n"
        "    public enum Mode {\n"
        "        FAST, SLOW\n"
        "    }\n"
        "\n"
        "    public void apply() {\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    symbols = sd.enumerate_java("acme/widgets", "deadbeef", tmp_path)
    names = {s.name: s for s in symbols}
    assert set(names) == {"Settings", "Mode"}, "the nested public enum must be flattened in, by its own name"
    assert names["Mode"].kind == "enum"
    assert {m.name for m in names["Mode"].members} == {"FAST", "SLOW"}


def test_enumerate_java_never_flattens_a_nested_type_of_a_non_public_outer_class(tmp_path):
    """Real Java accessibility: an outside caller can never name a nested type whose enclosing
    type it cannot name, so a public nested enum inside a package-private outer class is not
    part of the real public API and must not be flattened in."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "Internal.java").write_text(
        "class Internal {\n    public enum Mode {\n        FAST, SLOW\n    }\n}\n",
        encoding="utf-8",
    )
    symbols = sd.enumerate_java("acme/widgets", "deadbeef", tmp_path)
    assert symbols == [], "a nested type of an unreachable (package-private) outer class is not public API"


def test_enumerate_java_skips_a_src_test_java_directory(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "Fine.java").write_text(
        "public class Fine {\n    public void ok() {\n    }\n}\n", encoding="utf-8"
    )
    test_dir = tmp_path / "src" / "test" / "java"
    test_dir.mkdir(parents=True)
    (test_dir / "FineTest.java").write_text(
        "public class FineTest {\n    public void shouldWork() {\n    }\n}\n", encoding="utf-8"
    )
    symbols = sd.enumerate_java("acme/widgets", "deadbeef", tmp_path)
    names = {s.name for s in symbols}
    assert names == {"Fine"}, "a src/test/java directory must never be read as part of the public API"


def test_run_java_enumerator_shells_out_to_java_directly_never_docker(tmp_path):
    """Offline check of the java invocation's own shape (never a real subprocess here): no
    Docker anywhere in the command, the companion program's real path, and the one positional
    argument it expects."""
    import subprocess as _subprocess

    captured: dict[str, object] = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _subprocess.CompletedProcess(args=args, returncode=0, stdout="[]", stderr="")

    library_dir = tmp_path / "library"
    library_dir.mkdir()
    result = sd._run_java_enumerator(library_dir, run=fake_run)
    assert result == []
    args = captured["args"]
    assert args[0] == sd.JAVA_BINARY == "java"
    assert "docker" not in [str(a).lower() for a in args]
    assert args[1] == str(sd.JAVA_ENUMERATOR_SOURCE)
    assert args[2] == str(library_dir)


def test_run_java_enumerator_raises_with_a_clear_message_on_a_nonzero_java_exit(tmp_path):
    import subprocess as _subprocess

    def failing_run(args, **kwargs):
        return _subprocess.CompletedProcess(
            args=args, returncode=1, stdout="", stderr="error: something failed"
        )

    with pytest.raises(RuntimeError, match="JavaEnumerator.java"):
        sd._run_java_enumerator(tmp_path, run=failing_run)


def test_unimplemented_enumerators_raise_a_named_not_implemented_error():
    for platform in ("net", "cpp"):
        with pytest.raises(NotImplementedError, match=platform):
            sd.DEFAULT_ENUMERATORS[platform]("acme/widgets", "deadbeef", Path("/does/not/matter"))


def test_default_enumerators_cover_python_and_every_card_named_platform():
    assert sd.DEFAULT_ENUMERATORS["python"] is sd.enumerate_python
    assert sd.DEFAULT_ENUMERATORS["go"] is sd.enumerate_go
    assert sd.DEFAULT_ENUMERATORS["rust"] is sd.enumerate_rust
    assert sd.DEFAULT_ENUMERATORS["typescript"] is sd.enumerate_typescript
    assert sd.DEFAULT_ENUMERATORS["java"] is sd.enumerate_java
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


def test_docker_is_confined_to_the_go_and_rust_enumerators_own_container_invocations():
    """REWORK (G3/TC-590, extended by G3/TC-596) narrows this file's original "docker is never
    used for network or cluster use" guarantee: enumerate_go and enumerate_rust now both
    legitimately shell out to Docker, because this host's Windows Application Control policy
    blocks execution of a freshly-built, unsigned native .exe directly on the host (first fixed
    for Rust in plans/TC-590.yaml's REWORK note, then applied identically to Go in
    plans/TC-596.yaml). This proves the narrower claim that still matters -- main()'s own CLI
    parsing and orchestration path never mentions Docker, and the mentions that do exist are
    confined to the Go- and Rust-specific enumerators, not spread into this module's
    general-purpose plumbing."""
    import ast as _ast

    source = SOURCE.read_text(encoding="utf-8")
    tree = _ast.parse(source)
    top_level_functions = {node.name: node for node in tree.body if isinstance(node, _ast.FunctionDef)}
    for name in ("main", "parse_args", "diff_symbol", "diff_language", "verify_pilot"):
        segment = _ast.get_source_segment(source, top_level_functions[name]) or ""
        assert "docker" not in segment.lower(), f"{name} must never mention Docker"
    assert "docker" in source.lower(), "enumerate_go/enumerate_rust must still reference Docker somewhere"
    assert "helm" not in source.lower()
