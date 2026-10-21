"""G3/TC-566: scripts/release/source_differential.py, offline.

Every expectation here comes from a HAND-WRITTEN oracle (an ``OracleSymbol``/``OracleMember`` the
test builds itself, standing in for "the real upstream source, read with the language's own tool"),
never from a committed fixture or the extractor's own output. That is the whole point of this card
(RD3-150): a test whose expectation is derived from the thing it is checking can never see that thing
lose or fabricate data. No compiler, interpreter, docker command or network call is made by any test
below -- the per-language ``Enumerator`` callables are injected (``Deps.enumerators``), copying
``scripts/release/verify_pushed_image.py``'s own injection style, so even the few tests that exercise
``main()`` never touch the real git/ast/compiler path.
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


def test_unimplemented_enumerators_raise_a_named_not_implemented_error():
    for platform in ("typescript", "go", "rust", "java", "net", "cpp"):
        with pytest.raises(NotImplementedError, match=platform):
            sd.DEFAULT_ENUMERATORS[platform]("acme/widgets", "deadbeef", Path("/does/not/matter"))


def test_default_enumerators_cover_python_and_every_card_named_platform():
    assert sd.DEFAULT_ENUMERATORS["python"] is sd.enumerate_python
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


def test_main_never_imports_docker_subprocess_for_network_or_cluster_use():
    source = SOURCE.read_text(encoding="utf-8")
    assert "docker" not in source.lower()
    assert "kubectl" not in source.lower()
    assert "helm" not in source.lower()
