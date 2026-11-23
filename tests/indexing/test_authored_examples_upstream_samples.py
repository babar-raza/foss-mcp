"""Offline tests for TC-872 (REQ-G3-014): the ``kind: upstream-sample`` entry of the authored-example sidecar.

An upstream sample is example code adapted from a file of the pilot's own upstream repository at the pinned
commit, carried in the SAME sidecar as the authored examples with a mandatory ``source`` block. This file
covers the loader/validator, the JSON schema (the plain-Python validator and the schema must agree), the
candidate provenance and the module documentation. Chunk rendering and the honest-publish path are in
``tests/infra/test_build_chunks_upstream_samples.py``.

REPRODUCED FIRST (against the unchanged tree): a sidecar with a 7th authored entry is refused ("examples must
hold 1 to 6 items, not 7") and an entry with ``kind: upstream-sample`` was refused as an unknown field
("example 1 has unexpected key 'kind'"). The first stays true, the second is what this card changes.

Nothing here reaches a network, a toolchain or a real interpreter.
"""

from __future__ import annotations

import copy
import dataclasses
import json
from pathlib import Path

import jsonschema
import pytest
import yaml

from foss_mcp.indexing import authored_examples
from foss_mcp.indexing.authored_examples import (
    AUTHORED_LABEL,
    KIND_AUTHORED,
    KIND_UPSTREAM_SAMPLE,
    LANGUAGE_BY_PLATFORM,
    SIDECAR_NAME,
    UPSTREAM_SAMPLE_LABEL,
    AuthoredExamplesError,
    load_authored_examples,
    upstream_sample_label,
    validate_authored_examples,
)
from foss_mcp.indexing.example_candidates import (
    CandidateExample,
    merge_authored_candidates,
    normalised_code_sha256,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "schemas" / "product" / "authored-examples.schema.json"
FURNISHED = REPO_ROOT / "tests" / "fixtures" / "furnished"

PILOT = "demo_python"
COMMIT = "a" * 40
OTHER_COMMIT = "b" * 40
REPOSITORY = "example-org/Demo-FOSS-for-Python"

API_TYPES = [
    {
        "name": "Widget",
        "canonical_namespace": "Acme.Widgets",
        "class_import": "Acme.Widgets.Widget",
        "methods": [{"name": "Spin"}, {"name": "Stop"}],
        "properties": [{"name": "Speed"}],
    },
    {"name": "Gear", "canonical_namespace": "Acme.Widgets", "methods": [{"name": "Turn"}]},
]


# ---------------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------------


def _authored(n: int = 0, **over: object) -> dict:
    code = f"# Authored example: spin {n}\nimport acme\nwidget = acme.Widget()\nwidget.Spin({n})\n"
    example: dict = {
        "title": f"Authored spin {n}",
        "description": "Create a widget and spin it once.",
        "symbols": ["Widget.Spin"],
        "code": code,
    }
    example.update(over)
    example.setdefault("code_sha256", normalised_code_sha256(example["code"]))
    return example


def _source(**over: object) -> dict:
    source: dict = {
        "path": "tests/test_widget.py",
        "commit": COMMIT,
        "license": "MIT",
        "adaptation": "Removed the unittest scaffolding and inlined the fixture.",
    }
    source.update(over)
    return source


def _upstream(n: int = 0, **over: object) -> dict:
    code = f"import acme\nwidget = acme.Widget()\nwidget.Spin({n + 100})\nprint(widget.Speed)\n"
    example: dict = {
        "kind": "upstream-sample",
        "title": f"Upstream spin {n}",
        "description": "Spin a widget the way the upstream test does.",
        "symbols": ["Widget.Spin"],
        "code": code,
        "source": _source(),
    }
    example.update(over)
    example.setdefault("code_sha256", normalised_code_sha256(example["code"]))
    return example


def _document(examples: list[dict] | None = None, **over: object) -> dict:
    document: dict = {
        "schema_version": 1,
        "pilot": PILOT,
        "library_repository": REPOSITORY,
        "library_commit": COMMIT,
        "language": "python",
        "examples": [_authored(), _upstream()] if examples is None else examples,
    }
    document.update(over)
    return document


def _validate(document: object, platform: str = "python", **over: object) -> list[str]:
    kwargs: dict = {
        "pilot": PILOT,
        "library_platform": platform,
        "library_commit": COMMIT,
        "api_types": API_TYPES,
    }
    kwargs.update(over)
    return validate_authored_examples(document, **kwargs)


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _schema_errors(document: object) -> list:
    return list(jsonschema.Draft202012Validator(_schema()).iter_errors(document))


def _write(path: Path, document: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8", newline="\n")
    return path


def _load(path: Path) -> tuple[CandidateExample, ...]:
    return load_authored_examples(
        path, pilot=PILOT, library_platform="python", library_commit=COMMIT, api_types=API_TYPES
    )


# ---------------------------------------------------------------------------------------------------
# reproduction of today's behaviour, and what the card changes
# ---------------------------------------------------------------------------------------------------


def test_the_authored_cap_is_still_six_and_a_seventh_authored_entry_is_still_refused() -> None:
    assert authored_examples._MAX_EXAMPLES == 6
    seven = _document([_authored(n) for n in range(7)])
    assert _schema_errors(seven), "the schema accepted seven authored examples"
    assert any("1 to 6 items, not 7" in problem for problem in _validate(seven))
    six = _document([_authored(n) for n in range(6)])
    assert _schema_errors(six) == [] and _validate(six) == []


def test_a_kind_field_is_no_longer_an_unknown_field() -> None:
    document = _document([_upstream()])
    assert _validate(document) == []
    assert not any("unexpected key 'kind'" in problem for problem in _validate(document))
    assert _schema_errors(document) == []


def test_the_upstream_sample_cap_is_forty() -> None:
    assert authored_examples._MAX_UPSTREAM_SAMPLES == 40


# ---------------------------------------------------------------------------------------------------
# the caps, by the validator AND the schema
# ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("authored", "upstream"), [(0, 1), (1, 0), (1, 1), (6, 0), (0, 40), (6, 40), (3, 17)]
)
def test_every_count_within_the_two_caps_is_accepted_by_the_schema_and_the_validator(
    authored: int, upstream: int
) -> None:
    examples = [_authored(n) for n in range(authored)] + [_upstream(n) for n in range(upstream)]
    document = _document(examples)
    assert _schema_errors(document) == [], (authored, upstream)
    assert _validate(document) == [], (authored, upstream)


@pytest.mark.parametrize(("authored", "upstream"), [(0, 0), (7, 1), (7, 40), (6, 41), (0, 41), (1, 41)])
def test_every_count_beyond_a_cap_is_rejected_by_the_schema_and_the_validator(
    authored: int, upstream: int
) -> None:
    examples = [_authored(n) for n in range(authored)] + [_upstream(n) for n in range(upstream)]
    document = _document(examples)
    assert _schema_errors(document), (authored, upstream)
    assert any(problem.startswith("rule 1") for problem in _validate(document)), (authored, upstream)


def test_the_two_caps_are_independent_of_the_order_of_the_entries() -> None:
    examples = [_upstream(n) for n in range(40)] + [_authored(n) for n in range(6)]
    assert _validate(_document(examples)) == []
    assert _schema_errors(_document(examples)) == []
    interleaved = []
    for n in range(6):
        interleaved += [_authored(n), _upstream(n)]
    interleaved += [_upstream(n) for n in range(6, 40)]
    assert _validate(_document(interleaved)) == []
    assert _schema_errors(_document(interleaved)) == []


def test_the_schema_states_both_caps() -> None:
    schema = _schema()
    examples = schema["properties"]["examples"]
    assert examples["minItems"] == 1
    assert examples["maxItems"] == authored_examples._MAX_EXAMPLES + authored_examples._MAX_UPSTREAM_SAMPLES
    maxima = sorted(branch["maxContains"] for branch in examples["allOf"])
    assert maxima == [authored_examples._MAX_EXAMPLES, authored_examples._MAX_UPSTREAM_SAMPLES]


# ---------------------------------------------------------------------------------------------------
# structural cases the schema and the validator must agree on
# ---------------------------------------------------------------------------------------------------


def _mutate_upstream(mutate) -> dict:
    document = _document([_upstream()])
    mutate(document["examples"][0])
    return document


def _mutate_source(**over: object):
    return lambda example: example["source"].update(over)


def _drop_source_key(key: str):
    return lambda example: example["source"].pop(key)


STRUCTURAL_CASES = {
    "unknown-kind": lambda e: e.update(kind="bogus"),
    "kind-null": lambda e: e.update(kind=None),
    "kind-list": lambda e: e.update(kind=["upstream-sample"]),
    "kind-wrong-case": lambda e: e.update(kind="Upstream-Sample"),
    "source-missing": lambda e: e.pop("source"),
    "source-null": lambda e: e.update(source=None),
    "source-string": lambda e: e.update(source="tests/test_widget.py"),
    "source-list": lambda e: e.update(source=[_source()]),
    "source-missing-path": _drop_source_key("path"),
    "source-missing-commit": _drop_source_key("commit"),
    "source-missing-license": _drop_source_key("license"),
    "source-missing-adaptation": _drop_source_key("adaptation"),
    "source-extra-key": _mutate_source(url="https://example.com/x"),
    "source-path-empty": _mutate_source(path=""),
    "source-path-blank": _mutate_source(path="   "),
    "source-path-not-a-string": _mutate_source(path=7),
    "source-path-absolute": _mutate_source(path="/etc/passwd"),
    "source-path-backslash": _mutate_source(path="tests\\test_widget.py"),
    "source-path-parent": _mutate_source(path="../test_widget.py"),
    "source-path-inner-parent": _mutate_source(path="tests/../../x.py"),
    "source-path-trailing-parent": _mutate_source(path="tests/.."),
    "source-path-drive-letter": _mutate_source(path="C:/tests/x.py"),
    "source-path-newline": _mutate_source(path="tests/x.py\n"),
    "source-path-leading-space": _mutate_source(path=" tests/x.py"),
    "source-commit-short": _mutate_source(commit="a" * 39),
    "source-commit-long": _mutate_source(commit="a" * 41),
    "source-commit-uppercase": _mutate_source(commit="A" * 40),
    "source-commit-not-hex": _mutate_source(commit="g" * 40),
    "source-commit-not-a-string": _mutate_source(commit=1234),
    "source-license-apache": _mutate_source(license="Apache-2.0"),
    "source-license-lowercase": _mutate_source(license="mit"),
    "source-license-empty": _mutate_source(license=""),
    "source-license-null": _mutate_source(license=None),
    "source-adaptation-empty": _mutate_source(adaptation=""),
    "source-adaptation-blank": _mutate_source(adaptation="  \n "),
    "source-adaptation-not-a-string": _mutate_source(adaptation=["x"]),
    "upstream-missing-title": lambda e: e.pop("title"),
    "upstream-missing-symbols": lambda e: e.pop("symbols"),
    "upstream-missing-code-sha256": lambda e: e.pop("code_sha256"),
    "upstream-extra-key": lambda e: e.update(notes="x"),
    "upstream-title-too-long": lambda e: e.update(title="x" * 121),
    "upstream-symbols-empty": lambda e: e.update(symbols=[]),
    "upstream-hash-short": lambda e: e.update(code_sha256="0" * 63),
}


@pytest.mark.parametrize("name", sorted(STRUCTURAL_CASES))
def test_the_schema_and_the_validator_agree_on_every_upstream_structural_negative(name: str) -> None:
    document = _mutate_upstream(STRUCTURAL_CASES[name])
    assert _schema_errors(document), f"the schema accepted {name}"
    problems = _validate(document)
    assert any(problem.startswith("rule 1") for problem in problems), (name, problems)


def test_an_authored_entry_may_not_carry_a_source_block_or_an_upstream_kind_key_set() -> None:
    document = _document([_authored(source=_source())])
    assert _schema_errors(document)
    assert any("unexpected key 'source'" in problem for problem in _validate(document))


def test_an_authored_entry_may_name_its_kind_explicitly_and_it_stays_authored() -> None:
    document = _document([_authored(kind="authored")])
    assert _schema_errors(document) == []
    assert _validate(document) == []


@pytest.mark.parametrize(
    "path",
    [
        "tests/test_widget.py",
        "a.py",
        "src/pkg/sub dir/file name.py",
        "docs/..hidden/x.py",
        "docs/file..py",
        "a/b/c/d/e.py",
    ],
)
def test_repo_relative_paths_are_accepted_by_the_schema_and_the_validator(path: str) -> None:
    document = _document([_upstream(source=_source(path=path))])
    assert _schema_errors(document) == [], path
    assert _validate(document) == [], path


def test_a_plain_authored_sidecar_is_valid_before_and_after_with_no_kind_and_no_source() -> None:
    document = _document([_authored(n) for n in range(6)])
    assert all("kind" not in example and "source" not in example for example in document["examples"])
    assert _schema_errors(document) == []
    assert _validate(document) == []


def test_the_schema_is_a_valid_draft_2020_12_schema_with_the_new_definitions() -> None:
    jsonschema.Draft202012Validator.check_schema(_schema())
    assert set(_schema()["$defs"]) == {"authoredExample", "upstreamSample", "upstreamSource"}


# ---------------------------------------------------------------------------------------------------
# the rules only the loader can enforce
# ---------------------------------------------------------------------------------------------------


def test_a_source_commit_that_is_not_the_sidecars_library_commit_is_rejected_by_rule_3() -> None:
    document = _document([_upstream(source=_source(commit=OTHER_COMMIT))])
    assert _schema_errors(document) == [], "a schema cannot compare two fields"
    problems = _validate(document)
    assert any(problem.startswith("rule 3 (commit)") and "source.commit" in problem for problem in problems)


def test_a_stale_sidecar_commit_is_rejected_even_when_the_source_commit_matches_it() -> None:
    document = _document([_upstream(source=_source(commit=OTHER_COMMIT))], library_commit=OTHER_COMMIT)
    problems = _validate(document)
    assert any("library_commit is" in problem for problem in problems), problems
    assert not any("source.commit" in problem for problem in problems)


def test_an_upstream_sample_that_duplicates_another_entrys_code_is_rejected_by_rule_9() -> None:
    first = _upstream(0)
    copy_of_first = _upstream(1, code=first["code"] + "\n\n", code_sha256=first["code_sha256"])
    problems = _validate(_document([first, copy_of_first]))
    assert any(problem.startswith("rule 9 (duplicate code)") for problem in problems), problems


def test_whitespace_only_differences_are_still_duplicates() -> None:
    first = _upstream(0)
    spaced = first["code"].replace("\n", "   \n")
    problems = _validate(_document([first, _upstream(1, code=spaced)]))
    assert any(problem.startswith("rule 9") for problem in problems), problems


def test_an_upstream_sample_that_duplicates_an_authored_entrys_code_is_rejected_by_rule_9() -> None:
    authored = _authored(0)
    clone = _upstream(1, code=authored["code"])
    problems = _validate(_document([authored, clone]))
    assert any(problem.startswith("rule 9") and "example 2" in problem for problem in problems), problems


def test_distinct_code_is_not_a_duplicate() -> None:
    assert not any(problem.startswith("rule 9") for problem in _validate(_document()))


def test_two_authored_entries_with_equal_code_are_still_accepted_as_before() -> None:
    first = _authored(0)
    second = _authored(1, code=first["code"])
    assert not any(problem.startswith("rule 9") for problem in _validate(_document([first, second])))


def test_a_title_must_be_unique_across_both_kinds() -> None:
    problems = _validate(_document([_authored(0), _upstream(1, title="Authored spin 0")]))
    assert any("is not unique within the file" in problem for problem in problems), problems


def test_the_twelve_word_title_limit_applies_to_an_upstream_sample() -> None:
    title = " ".join(f"w{n}" for n in range(13))
    document = _document([_upstream(title=title)])
    assert any("13 words" in problem for problem in _validate(document))
    assert _validate(_document([_upstream(title=" ".join(f"w{n}" for n in range(12)))])) == []


def test_the_forbidden_patterns_apply_to_an_upstream_sample() -> None:
    document = _document([_upstream(code="import os\nvalue = os.environ['HOME']\nWidget.Spin(value)\n")])
    assert any(problem.startswith("rule 8") for problem in _validate(document))
    document = _document(
        [_upstream(code='import requests\nWidget.Spin(1)\nrequests.get("https://real.org")\n')]
    )
    assert any(problem.startswith("rule 8") for problem in _validate(document))


def test_the_symbol_rules_apply_to_an_upstream_sample() -> None:
    unknown = _document([_upstream(symbols=["Widget.Explode"])])
    assert any(problem.startswith("rule 6") and "no declaration" in problem for problem in _validate(unknown))
    absent = _document([_upstream(symbols=["Widget.Stop"])])
    assert any("does not appear in the code as a whole word" in problem for problem in _validate(absent))


def test_the_authored_example_header_is_required_of_authored_entries_only() -> None:
    upstream = _upstream()
    assert "Authored example" not in upstream["code"]
    assert _validate(_document([upstream])) == []
    headerless = _authored(0, code="import acme\nacme.Widget().Spin()\n")
    problems = _validate(_document([headerless, upstream]))
    assert any(problem.startswith("rule 7") and "example 1" in problem for problem in problems), problems
    assert not any(problem.startswith("rule 7") and "example 2" in problem for problem in problems)


def test_the_code_hash_rule_applies_to_an_upstream_sample() -> None:
    document = _document([_upstream(code_sha256="0" * 64)])
    assert any(problem.startswith("rule 5") for problem in _validate(document))


def test_the_language_and_pilot_rules_still_apply_to_a_sidecar_of_upstream_samples() -> None:
    document = _document([_upstream()], language="java")
    assert any(problem.startswith("rule 4") for problem in _validate(document))
    assert any(problem.startswith("rule 2") for problem in _validate(_document([_upstream()]), pilot="other"))


def test_a_csharp_upstream_sample_is_judged_on_the_slash_slash_authored_header_not_at_all() -> None:
    code = "var widget = new Widget();\nwidget.Spin();\n"
    document = _document([_upstream(code=code)], language=LANGUAGE_BY_PLATFORM["dotnet"])
    assert _validate(document, "dotnet") == []


def test_one_malformed_entry_does_not_hide_the_problems_of_the_others() -> None:
    document = _document(
        [_authored(0), _upstream(1, source=_source(license="GPL")), _upstream(2, symbols=[])]
    )
    problems = _validate(document)
    assert any("example 2" in p and "license" in p for p in problems)
    assert any("example 3" in p and "symbols" in p for p in problems)


# ---------------------------------------------------------------------------------------------------
# the loader and the candidates
# ---------------------------------------------------------------------------------------------------


def test_a_mixed_sidecar_loads_in_file_order_with_the_right_provenance_and_source(tmp_path: Path) -> None:
    document = _document([_upstream(0), _authored(1), _upstream(2, source=_source(path="docs/x.py"))])
    candidates = _load(_write(tmp_path / SIDECAR_NAME, document))
    assert [c.title for c in candidates] == ["Upstream spin 0", "Authored spin 1", "Upstream spin 2"]
    assert [c.provenance for c in candidates] == ["upstream-sample", "authored", "upstream-sample"]
    assert candidates[0].sample_source == ("tests/test_widget.py", COMMIT)
    assert candidates[1].sample_source == ()
    assert candidates[2].sample_source == ("docs/x.py", COMMIT)
    for candidate in candidates:
        assert (candidate.source, candidate.starts_example, candidate.imports_only) == (
            "sidecar",
            True,
            False,
        )
        assert candidate.import_prefix == () and candidate.page_code == (candidate.code,)
        assert candidate.code_sha256 == normalised_code_sha256(candidate.code)


def test_the_same_mixed_bytes_give_the_same_candidates(tmp_path: Path) -> None:
    path = _write(tmp_path / SIDECAR_NAME, _document())
    first = _load(path)
    assert [dataclasses.astuple(c) for c in first] == [dataclasses.astuple(c) for c in _load(path)]


def test_the_loader_message_lists_every_upstream_problem(tmp_path: Path) -> None:
    document = _document([_upstream(0, source=_source(commit=OTHER_COMMIT, license="GPL"))])
    with pytest.raises(AuthoredExamplesError) as error:
        _load(_write(tmp_path / SIDECAR_NAME, document))
    message = str(error.value)
    assert "license" in message and "source.commit" in message


def test_an_authored_only_sidecar_loads_exactly_as_before(tmp_path: Path) -> None:
    candidates = _load(_write(tmp_path / SIDECAR_NAME, _document([_authored(n) for n in range(3)])))
    assert [c.provenance for c in candidates] == ["authored"] * 3
    assert all(c.sample_source == () for c in candidates)


def test_the_candidate_dataclass_keeps_provenance_before_sample_source_and_excludes_both_from_equality() -> (
    None
):
    names = [field.name for field in dataclasses.fields(CandidateExample)]
    assert names.index("provenance") < names.index("sample_source") == len(names) - 1
    plain = CandidateExample("t", "d", "python", "x = 1")
    assert plain.sample_source == ()
    assert plain == dataclasses.replace(plain, provenance="upstream-sample", sample_source=("a.py", COMMIT))


# ---------------------------------------------------------------------------------------------------
# merge_authored_candidates
# ---------------------------------------------------------------------------------------------------


def _raw_upstream(title: str = "Up", code: str = "x = 1") -> CandidateExample:
    return CandidateExample(
        title,
        "d",
        "python",
        code,
        provenance="upstream-sample",
        sample_source=("src/a.py", COMMIT),
        source="single",
        starts_example=False,
        imports_only=True,
        import_prefix=("import q",),
    )


def test_merge_keeps_the_upstream_provenance_and_source_and_normalises_the_rest() -> None:
    merged, skipped = merge_authored_candidates([], [_raw_upstream()])
    assert skipped == []
    candidate = merged[0]
    assert (candidate.provenance, candidate.sample_source) == ("upstream-sample", ("src/a.py", COMMIT))
    assert (candidate.source, candidate.starts_example, candidate.imports_only) == ("sidecar", True, False)
    assert candidate.import_prefix == () and candidate.page_code == ("x = 1",)
    assert candidate.code_sha256 == normalised_code_sha256("x = 1")


def test_merge_still_turns_any_other_provenance_into_authored_with_no_sample_source() -> None:
    odd = dataclasses.replace(_raw_upstream(), provenance="readme")
    merged, _ = merge_authored_candidates([], [odd])
    assert (merged[0].provenance, merged[0].sample_source) == ("authored", ())


def test_merge_skips_an_upstream_sample_whose_title_is_already_taken() -> None:
    readme = CandidateExample("Up", "d", "python", "y = 2")
    merged, skipped = merge_authored_candidates(
        [readme], [_raw_upstream("Up"), _raw_upstream("Other", "z = 3")]
    )
    assert skipped == ["Up"]
    assert [c.title for c in merged] == ["Up", "Other"]
    assert merged[0].provenance == "readme"


def test_merge_keeps_readme_candidates_first_and_authored_and_upstream_in_the_given_order() -> None:
    readme = CandidateExample("Readme one", "d", "python", "r = 1")
    authored = CandidateExample("Auth", "d", "python", "a = 1", provenance="authored")
    merged, _ = merge_authored_candidates([readme], [_raw_upstream("Up"), authored])
    assert [(c.title, c.provenance) for c in merged] == [
        ("Readme one", "readme"),
        ("Up", "upstream-sample"),
        ("Auth", "authored"),
    ]


# ---------------------------------------------------------------------------------------------------
# the label and the documentation
# ---------------------------------------------------------------------------------------------------


def test_the_upstream_sample_label_is_the_exact_documented_sentence() -> None:
    assert UPSTREAM_SAMPLE_LABEL == (
        "Upstream sample: adapted from {path} of the pinned upstream repository (MIT); published only after "
        "the ingestion verifier ran it against that commit."
    )
    assert upstream_sample_label("tests/test_widget.py") == (
        "Upstream sample: adapted from tests/test_widget.py of the pinned upstream repository (MIT); "
        "published only after the ingestion verifier ran it against that commit."
    )
    assert "{" not in upstream_sample_label("a/{b}.py").replace("{b}", "")


def test_the_two_labels_and_kinds_are_distinct() -> None:
    assert upstream_sample_label("a.py") != AUTHORED_LABEL
    assert (KIND_AUTHORED, KIND_UPSTREAM_SAMPLE) == ("authored", "upstream-sample")


def test_the_module_docstring_documents_the_entry_shape_and_both_caps() -> None:
    doc = authored_examples.__doc__ or ""
    for needle in (
        "upstream-sample",
        "source",
        "path",
        "commit",
        "license",
        "adaptation",
        "UPSTREAM_SAMPLE_LABEL",
        "Sample-Source",
        "(6)",
        "(40)",
    ):
        assert needle in doc, needle


# ---------------------------------------------------------------------------------------------------
# no committed pilot changes: every existing sidecar is still valid, as authored entries
# ---------------------------------------------------------------------------------------------------

_COMMITTED_SIDECARS = sorted(FURNISHED.glob(f"*/{SIDECAR_NAME}"))


def test_there_are_committed_sidecars_to_check() -> None:
    assert len(_COMMITTED_SIDECARS) >= 20, len(_COMMITTED_SIDECARS)


@pytest.mark.parametrize("sidecar", _COMMITTED_SIDECARS, ids=lambda p: p.parent.name)
def test_every_committed_sidecar_is_unchanged_in_meaning_and_valid_against_the_schema(sidecar: Path) -> None:
    document = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
    assert _schema_errors(document) == [], sidecar.parent.name
    assert authored_examples._structure_problems(document) == [], sidecar.parent.name
    examples = document["examples"]
    assert len(examples) <= authored_examples._MAX_EXAMPLES
    assert all("kind" not in example and "source" not in example for example in examples)
    assert all(authored_examples._example_kind(example) == KIND_AUTHORED for example in examples)


def test_the_structure_check_of_a_committed_sidecar_is_the_old_one_for_a_deep_copy() -> None:
    sidecar = _COMMITTED_SIDECARS[0]
    document = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
    pristine = copy.deepcopy(document)
    authored_examples._structure_problems(document)
    assert document == pristine, "validation never mutates the document"
