"""Offline tests for TC-460 (REQ-G3-007): the authored-example sidecar, its loader and validator, the
opt-in merge into the candidate list, and the build_chunks wiring that verifies and labels them.

Nothing here reaches a network, a toolchain or a real interpreter. The sidecars are synthetic files in
``tmp_path`` checked against a small synthetic api_surface type list; the build_chunks end-to-end tests
replace the python prepare and verify functions the way test_build_chunks_verification_report.py does.
This file carries sample forbidden network-client strings as DATA (they are inputs to the scan), which
is the only reason the card is marked ``network: true``; no test here opens a connection.
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import json
import re
import shutil
import sys
from pathlib import Path

import jsonschema
import pytest
import yaml

import infra.build_chunks as build_chunks
import infra.generate_furnished_page as gen
from foss_mcp.indexing import authored_examples
from foss_mcp.indexing.authored_examples import (
    AUTHORED_LABEL,
    FORBIDDEN_CODE_PATTERNS,
    LANGUAGE_BY_PLATFORM,
    SIDECAR_NAME,
    AuthoredExamplesError,
    load_authored_examples,
    validate_authored_examples,
)
from foss_mcp.indexing.example_candidates import (
    CandidateExample,
    effective_candidate,
    extract_candidate_examples,
    extract_candidate_supply,
    merge_authored_candidates,
    normalised_code_sha256,
)
from foss_mcp.indexing.example_verifier import VerificationResult

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "schemas" / "product" / "authored-examples.schema.json"
CHART_VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"
DOCKERFILE_INGESTION = REPO_ROOT / "Dockerfile.ingestion"
BUILD_CHUNKS_SOURCE = REPO_ROOT / "infra" / "build_chunks.py"
API_SURFACE = REPO_ROOT / "tests" / "fixtures" / "pdf_net" / "api_surface.json"

PILOT = "demo_python"
COMMIT = "a" * 40
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

_HEADER = {"python": "# Authored example: ", "csharp": "// Authored example: "}


def _code(platform: str = "python", title: str = "spin a widget") -> str:
    header = _HEADER["python" if platform == "python" else "csharp"] + title
    if platform == "python":
        return f"{header}\nimport acme\nwidget = acme.Widget()\nwidget.Spin()\n"
    return f"{header}\nvar widget = new Widget();\nwidget.Spin();\n"


def _example(platform: str = "python", **over: object) -> dict:
    example: dict = {
        "title": "Spin a widget",
        "description": "Create a widget and spin it once.",
        "symbols": ["Widget.Spin"],
        "code": _code(platform),
    }
    example.update(over)
    if "code_sha256" not in example:
        example["code_sha256"] = normalised_code_sha256(example["code"])
    return example


def _document(platform: str = "python", **over: object) -> dict:
    document: dict = {
        "schema_version": 1,
        "pilot": PILOT,
        "library_repository": REPOSITORY,
        "library_commit": COMMIT,
        "language": LANGUAGE_BY_PLATFORM[platform],
        "examples": [_example(platform)],
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


def _write_sidecar(path: Path, document: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8", newline="\n")
    return path


def _load(path: Path, platform: str = "python") -> tuple[CandidateExample, ...]:
    return load_authored_examples(
        path, pilot=PILOT, library_platform=platform, library_commit=COMMIT, api_types=API_TYPES
    )


def _schema_errors(document: object) -> list:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return list(jsonschema.Draft202012Validator(schema).iter_errors(document))


# ---------------------------------------------------------------------------------------------------
# hash normalisation and the candidate dataclass
# ---------------------------------------------------------------------------------------------------


def test_the_hash_normalisation_ignores_incidental_whitespace_but_not_a_changed_statement() -> None:
    base = "# a\nx = 1\ny = 2"
    same = [
        "# a\r\nx = 1\r\ny = 2",
        "# a\rx = 1\ry = 2",
        "# a   \nx = 1\t\ny = 2  ",
        "\n\n# a\nx = 1\ny = 2\n\n\n",
    ]
    assert len({normalised_code_sha256(text) for text in [base, *same]}) == 1
    assert normalised_code_sha256("# a\nx = 1\ny = 3") != normalised_code_sha256(base)


def test_the_loader_reuses_the_one_hash_function_of_example_candidates() -> None:
    assert authored_examples.normalised_code_sha256 is normalised_code_sha256


def test_candidate_provenance_defaults_to_readme_is_last_and_excluded_from_equality() -> None:
    fields = [field.name for field in dataclasses.fields(CandidateExample)]
    assert fields[-1] == "provenance"
    plain = CandidateExample("t", "d", "python", "x = 1")
    assert plain.provenance == "readme"
    assert plain == dataclasses.replace(plain, provenance="authored")


# ---------------------------------------------------------------------------------------------------
# the loader
# ---------------------------------------------------------------------------------------------------


def test_a_valid_sidecar_validates_clean() -> None:
    assert _validate(_document()) == []
    assert _validate(_document("dotnet"), "dotnet") == []


def test_a_valid_sidecar_loads_in_file_order_with_provenance_authored(tmp_path: Path) -> None:
    second_code = "# Authored example: stop\nimport acme\nacme.Widget().Stop()\n"
    document = _document(
        examples=[
            _example(),
            _example(title="Stop a widget", symbols=["Widget.Stop"], code=second_code),
        ]
    )
    path = _write_sidecar(tmp_path / SIDECAR_NAME, document)

    loaded = _load(path)

    assert [candidate.title for candidate in loaded] == ["Spin a widget", "Stop a widget"]
    for candidate, source in zip(loaded, document["examples"], strict=True):
        assert candidate.provenance == "authored"
        assert candidate.source == "sidecar"
        assert candidate.starts_example is True
        assert candidate.imports_only is False
        assert candidate.import_prefix == ()
        assert candidate.language == "python"
        assert candidate.code == source["code"].strip()
        assert candidate.code_sha256 == source["code_sha256"]
        assert candidate.page_code == (candidate.code,)


def test_the_same_sidecar_bytes_give_the_same_candidates_in_the_same_order(tmp_path: Path) -> None:
    path = _write_sidecar(tmp_path / SIDECAR_NAME, _document())
    first, second = _load(path), _load(path)
    assert first == second
    assert [c.page_code for c in first] == [c.page_code for c in second]


def test_the_loader_message_lists_every_problem_found(tmp_path: Path) -> None:
    document = _document(pilot="wrong_python", library_commit="b" * 40)
    path = _write_sidecar(tmp_path / SIDECAR_NAME, document)
    with pytest.raises(AuthoredExamplesError) as raised:
        _load(path)
    message = str(raised.value)
    assert "rule 2" in message and "rule 3" in message
    assert isinstance(raised.value, ValueError)


def test_the_loader_rejects_unreadable_and_unparsable_files(tmp_path: Path) -> None:
    with pytest.raises(AuthoredExamplesError):
        _load(tmp_path / "missing.yaml")
    broken = tmp_path / "broken.yaml"
    broken.write_text("examples: [unclosed\n", encoding="utf-8")
    with pytest.raises(AuthoredExamplesError):
        _load(broken)
    binary = tmp_path / "binary.yaml"
    binary.write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(AuthoredExamplesError):
        _load(binary)


def test_the_language_table_covers_every_build_chunks_platform() -> None:
    assert dict(LANGUAGE_BY_PLATFORM) == {
        "dotnet": "csharp",
        "cpp": "cpp",
        "python": "python",
        "java": "java",
        "go": "go",
        "rust": "rust",
        "typescript": "typescript",
        "nodejs": "javascript",
    }
    assert set(LANGUAGE_BY_PLATFORM) == set(build_chunks._PLATFORM_DISPATCH)


# ---------------------------------------------------------------------------------------------------
# validation rules (1)-(8), each rejected by its own case
# ---------------------------------------------------------------------------------------------------


def _with_example(**over: object):
    def mutate(document: dict) -> None:
        document["examples"][0].update(over)

    return mutate


def _set(key: str, value: object):
    def mutate(document: dict) -> None:
        document[key] = value

    return mutate


def _break_hash(document: dict) -> None:
    document["examples"][0]["code_sha256"] = "0" * 64


RULE_CASES = [
    ("rule 1", "python", _set("schema_version", 2)),
    ("rule 2", "python", _set("pilot", "other_python")),
    ("rule 3", "python", _set("library_commit", "b" * 40)),
    ("rule 4", "python", _set("language", "java")),
    ("rule 4", "dotnet", _set("language", "cpp")),
    ("rule 5", "python", _break_hash),
    ("rule 6", "python", _with_example(symbols=["Nope.Missing"])),
    ("rule 6", "python", _with_example(symbols=["Acme.Widgets"])),  # a namespace alone does not count
    ("rule 6", "python", _with_example(symbols=["Widget.Stop"])),  # declared, but never used in the code
    (
        "rule 6",
        "python",
        _with_example(  # "Spinner" does not contain the whole word "Spin"
            symbols=["Widget.Spin"], code="# Authored example: x\nSpinner()\nWidget\n"
        ),
    ),
    ("rule 7", "python", _with_example(code="import acme\nacme.Widget().Spin()\n")),
    ("rule 7", "python", _with_example(code="# spin it\nimport acme\nacme.Widget().Spin()\n")),
    ("rule 7", "python", _with_example(code="// Authored example: x\nacme.Widget().Spin()\n")),
    ("rule 7", "dotnet", _with_example(code="# Authored example: x\nvar w = new Widget(); w.Spin();\n")),
    (
        "rule 8",
        "python",
        _with_example(code="# Authored example: x\nimport os\nos.environ['X']\nacme.Widget().Spin()\n"),
    ),
]


@pytest.mark.parametrize(
    ("rule", "platform", "mutate"),
    RULE_CASES,
    ids=[f"{case[0]}-{index}" for index, case in enumerate(RULE_CASES)],
)
def test_each_validation_rule_is_rejected_by_its_own_case(rule: str, platform: str, mutate) -> None:
    document = _document(platform)
    mutate(document)
    # Keep the hash honest when a case edits the code, so the case isolates its own rule.
    example = document["examples"][0]
    if rule != "rule 5" and rule != "rule 1":
        example["code_sha256"] = normalised_code_sha256(example["code"])
    problems = _validate(document, platform)
    assert problems, f"{rule} was not rejected"
    assert any(problem.startswith(rule) for problem in problems), problems


def test_the_blank_leading_lines_before_the_header_comment_are_allowed() -> None:
    code = "\n\n# Authored example: x\nimport acme\nacme.Widget().Spin()\n"
    assert _validate(_document(examples=[_example(code=code)])) == []


# ---------------------------------------------------------------------------------------------------
# FORBIDDEN_CODE_PATTERNS and URLs
# ---------------------------------------------------------------------------------------------------

FORBIDDEN_SAMPLES = [
    "os.environ['HOME']",
    "os.getenv('TOKEN')",
    'std::getenv("TOKEN")',
    'os.Getenv("TOKEN")',
    'Environment.GetEnvironmentVariable("TOKEN")',
    'System.getenv("TOKEN")',
    "process.env.TOKEN",
    'std::env::var("TOKEN")',
    "import subprocess",
    "from subprocess import run",
    "subprocess.run(['ls'])",
    "os.system('ls')",
    'Process.Start("tool")',
    "os.popen('ls')",
    "subprocess.Popen(['ls'])",
    'exec.Command("ls")',
    'Runtime.getRuntime().exec("ls")',
    "import socket",
    "socket.socket()",
    'new Socket("host", 1)',
    "TcpStream::connect(address)",
    "urlopen(address)",
    "import urllib.request",
    "import requests",
    "requests.get(address)",
    "import http.client",
    "var c = new HttpClient();",
    "var c = new WebClient();",
    "var c = new TcpClient();",
    'net.Dial("tcp", address)',
    "http.Get(address)",
    "fetch(address)",
    "globalThis.fetch(address)",
    "use reqwest::Client;",
    "answer = input('?')",
    "Console.ReadLine()",
    "std::cin >> answer;",
]


@pytest.mark.parametrize("sample", FORBIDDEN_SAMPLES)
def test_a_forbidden_usage_is_rejected_by_rule_8(sample: str) -> None:
    code = f"# Authored example: x\n{sample}\nWidget.Spin\n"
    document = _document(examples=[_example(code=code)])
    problems = _validate(document)
    assert any(problem.startswith("rule 8") for problem in problems), problems
    assert any(pattern.search(code) for pattern in FORBIDDEN_CODE_PATTERNS)


def test_every_forbidden_pattern_entry_is_rejected_by_at_least_one_sample() -> None:
    assert FORBIDDEN_CODE_PATTERNS and all(hasattr(p, "search") for p in FORBIDDEN_CODE_PATTERNS)
    for pattern in FORBIDDEN_CODE_PATTERNS:
        matching = [sample for sample in FORBIDDEN_SAMPLES if pattern.search(sample)]
        assert matching, f"no sample exercises {pattern.pattern!r}"
        code = f"# Authored example: x\n{matching[0]}\nWidget.Spin\n"
        problems = _validate(_document(examples=[_example(code=code)]))
        assert any("rule 8" in problem and repr(pattern.pattern) in problem for problem in problems), (
            pattern.pattern,
            problems,
        )


@pytest.mark.parametrize(
    "comment",
    [
        "build two requests and compare them",
        "the socket stays closed here",
        "the input (a path) is optional",
        "read the environment of the caller",
        "a subprocess would be overkill",
        "fetch the widget list",
        "the console is not used",
    ],
)
def test_prose_in_a_comment_is_not_mistaken_for_a_forbidden_usage(comment: str) -> None:
    code = f"# Authored example: x\n# {comment}\nimport acme\nacme.Widget().Spin()\n"
    assert _validate(_document(examples=[_example(code=code)])) == []


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/widgets",
        "http://api.example.org/v1",
        "https://sub.domain.example.net:8443/x?y=1",
        "https://service.example/x",
        "https://host.test/x",
        "https://nothing.invalid",
        "HTTPS://EXAMPLE.COM/upper",
    ],
)
def test_a_reserved_host_url_is_accepted(url: str) -> None:
    code = f'# Authored example: x\nimport acme\nacme.Widget().Spin()\nurl = "{url}"\n'
    assert _validate(_document(examples=[_example(code=code)])) == []


@pytest.mark.parametrize(
    "url",
    [
        "https://api.github.com/repos",
        "http://real-service.io/x",
        "https://notexample.com/x",
        "https://example.com.evil.io/x",
        "http://user@evil.com/x",
        "http://127.0.0.1:8080/x",
        "https://localhost/x",
    ],
)
def test_a_real_looking_host_url_is_rejected(url: str) -> None:
    code = f'# Authored example: x\nimport acme\nacme.Widget().Spin()\nurl = "{url}"\n'
    problems = _validate(_document(examples=[_example(code=code)]))
    assert any(problem.startswith("rule 8") for problem in problems), problems


# ---------------------------------------------------------------------------------------------------
# the schema file agrees with the plain-Python structural rules
# ---------------------------------------------------------------------------------------------------


def _drop(path: tuple):
    def mutate(document: dict) -> None:
        target = document
        for key in path[:-1]:
            target = target[key]
        del target[path[-1]]

    return mutate


STRUCTURAL_CASES = {
    **{f"missing-{key}": _drop((key,)) for key in _document()},
    **{
        f"example-missing-{key}": _drop(("examples", 0, key))
        for key in ("title", "description", "symbols", "code", "code_sha256")
    },
    "extra-top-level-key": _set("notes", "x"),
    "extra-example-key": _with_example(notes="x"),
    "schema-version-two": _set("schema_version", 2),
    "schema-version-string": _set("schema_version", "1"),
    "schema-version-bool": _set("schema_version", True),
    "pilot-blank": _set("pilot", "  "),
    "repository-empty": _set("library_repository", ""),
    "commit-uppercase": _set("library_commit", "A" * 40),
    "commit-short": _set("library_commit", "a" * 39),
    "language-empty": _set("language", ""),
    "examples-empty": _set("examples", []),
    "examples-six": lambda document: document.__setitem__("examples", [_example() for _ in range(6)]),
    "examples-not-a-list": _set("examples", {"title": "x"}),
    "example-not-a-mapping": lambda document: document.__setitem__("examples", ["text"]),
    "title-empty": _with_example(title=""),
    "title-too-long": _with_example(title="x" * 121),
    "description-blank": _with_example(description="   "),
    "symbols-empty": _with_example(symbols=[]),
    "symbols-duplicate": _with_example(symbols=["Widget.Spin", "Widget.Spin"]),
    "symbols-not-strings": _with_example(symbols=[1]),
    "code-blank": _with_example(code="\n  \n"),
    "hash-short": _with_example(code_sha256="0" * 63),
    "hash-uppercase": _with_example(code_sha256="A" * 64),
}


def test_the_schema_file_is_itself_a_valid_draft_2020_12_schema() -> None:
    jsonschema.Draft202012Validator.check_schema(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


def test_the_schema_and_the_validator_agree_on_the_valid_document() -> None:
    document = _document()
    assert _schema_errors(document) == []
    assert _validate(document) == []


def test_the_schema_and_the_validator_agree_on_a_maximal_valid_document() -> None:
    examples = [_example(title=f"Example {n}", symbols=["Widget.Spin", "Widget"]) for n in range(5)]
    document = _document(examples=examples)
    assert _schema_errors(document) == []
    assert _validate(document) == []


@pytest.mark.parametrize("name", sorted(STRUCTURAL_CASES))
def test_the_schema_and_the_validator_agree_on_every_structural_negative(name: str) -> None:
    document = _document()
    STRUCTURAL_CASES[name](document)
    assert _schema_errors(document), f"the schema accepted {name}"
    problems = _validate(document)
    assert any(problem.startswith("rule 1") for problem in problems), (name, problems)


def test_the_schema_and_the_validator_both_reject_a_non_mapping_document() -> None:
    for document in (None, [], "text", 7):
        assert _schema_errors(document)
        assert any(problem.startswith("rule 1") for problem in _validate(document))


def test_a_duplicate_title_is_a_loader_rule_the_schema_cannot_express() -> None:
    other = _example(title="Spin a widget", code=_code(title="again"))
    document = _document(examples=[_example(), other])
    assert _schema_errors(document) == []
    assert any("not unique" in problem for problem in _validate(document))


# ---------------------------------------------------------------------------------------------------
# merge into the candidate list
# ---------------------------------------------------------------------------------------------------

PAGE = {
    "single": {
        "block": [
            {
                "title": "Create the deck",
                "content": "Make one.\n\n```python\nimport acme\nacme.make()\n```\n",
            },
            {
                "title": "Save the deck",
                "content": "Save it.\n\n```python\nimport acme\nacme.save()\n```\n",
            },
        ]
    },
    "content": {
        "block": [
            {
                "title_left": "Quick start",
                "content_left": "Do this.\n\n```python\nacme.quick()\n```\n",
                "title_right": "Other",
                "content_right": "No code here.",
            }
        ]
    },
}
CSHARP_ONLY_PAGE = {
    "single": {"block": [{"title": "Native", "content": "Text.\n\n```cpp\nint main() { return 0; }\n```\n"}]}
}


def _authored(tmp_path: Path, *titles: str) -> tuple[CandidateExample, ...]:
    examples = [_example(title=title, code=_code(title=title), symbols=["Widget.Spin"]) for title in titles]
    return _load(_write_sidecar(tmp_path / SIDECAR_NAME, _document(examples=examples)))


def _full_state(candidates: list[CandidateExample]) -> list[tuple]:
    return [
        (
            c.title, c.description, c.language, c.code, c.page_code, c.code_sha256, c.source,
            c.starts_example, c.imports_only, c.import_prefix, c.provenance,
        )
        for c in candidates
    ]  # fmt: skip


def test_an_empty_authored_argument_changes_nothing_in_legacy_and_platform_mode() -> None:
    assert _full_state(extract_candidate_examples(PAGE, authored=())) == _full_state(
        extract_candidate_examples(PAGE)
    )
    assert _full_state(extract_candidate_examples(PAGE, platform="python", authored=())) == _full_state(
        extract_candidate_examples(PAGE, platform="python")
    )
    assert extract_candidate_supply(PAGE, "python", authored=()) == extract_candidate_supply(PAGE, "python")


def test_readme_candidates_stay_a_prefix_with_unchanged_page_code(tmp_path: Path) -> None:
    authored = _authored(tmp_path, "Spin a widget")
    for platform in (None, "python"):
        without = extract_candidate_examples(PAGE, platform=platform)
        merged = extract_candidate_examples(PAGE, platform=platform, authored=authored)
        assert _full_state(merged[: len(without)]) == _full_state(without)
        assert [c.title for c in merged[len(without) :]] == ["Spin a widget"]
        for readme in merged[: len(without)]:
            assert readme.provenance == "readme"
            assert authored[0].code not in readme.page_code


def test_an_authored_candidate_stands_alone(tmp_path: Path) -> None:
    authored = _authored(tmp_path, "Spin a widget")
    merged = extract_candidate_examples(PAGE, platform="python", authored=authored)
    candidate = merged[-1]
    assert candidate.provenance == "authored"
    assert candidate.page_code == (candidate.code,)
    assert candidate.import_prefix == ()
    assert candidate.starts_example is True and candidate.imports_only is False
    for platform in ("python", "typescript", "nodejs", "dotnet", "go", "rust", "java", "cpp"):
        assert effective_candidate(candidate, platform) is candidate


def test_the_supply_counters_and_printed_line_never_count_authored_candidates(tmp_path: Path) -> None:
    authored = _authored(tmp_path, "Spin a widget")
    plain = extract_candidate_supply(PAGE, "python")
    merged = extract_candidate_supply(PAGE, "python", authored=authored)
    assert len(merged.candidates) == len(plain.candidates) + 1
    for name in (
        "from_single_blocks",
        "from_content_blocks",
        "duplicates_dropped",
        "other_language_dropped",
        "imports_only",
        "fallback",
    ):
        assert getattr(merged, name) == getattr(plain, name), name


def test_authored_candidates_are_also_appended_on_the_fallback_list(tmp_path: Path) -> None:
    authored = _authored(tmp_path, "Spin a widget")
    supply = extract_candidate_supply(CSHARP_ONLY_PAGE, "python", authored=authored)
    assert supply.fallback is True
    assert [c.provenance for c in supply.candidates] == ["readme", "authored"]
    assert supply.from_single_blocks == 1


def test_a_title_clash_is_skipped_and_never_yields_two_candidates_with_one_title(tmp_path: Path) -> None:
    authored = _authored(tmp_path, "Create the deck", "Spin a widget")
    readme = extract_candidate_examples(PAGE, platform="python")
    merged, skipped = merge_authored_candidates(readme, authored)
    assert skipped == ["Create the deck"]
    titles = [c.title for c in merged]
    assert len(titles) == len(set(titles))
    assert titles[-1] == "Spin a widget"
    assert merged[0].provenance == "readme"
    assert [c.title for c in extract_candidate_examples(PAGE, platform="python", authored=authored)] == titles


def test_merge_normalises_a_hand_built_authored_candidate() -> None:
    raw = CandidateExample(
        title="Hand built",
        description="d",
        language="python",
        code="x = 1",
        page_code=("import other", "x = 1"),
        import_prefix=("import other",),
        imports_only=True,
        starts_example=False,
        source="single",
    )
    merged, skipped = merge_authored_candidates([], [raw])
    assert skipped == []
    candidate = merged[0]
    assert (candidate.provenance, candidate.source) == ("authored", "sidecar")
    assert candidate.page_code == ("x = 1",)
    assert candidate.import_prefix == () and candidate.imports_only is False
    assert candidate.starts_example is True
    assert candidate.code_sha256 == normalised_code_sha256("x = 1")


# ---------------------------------------------------------------------------------------------------
# build_chunks end to end (fake prepare and verify)
# ---------------------------------------------------------------------------------------------------

LIBRARY_REPOSITORY = "example/Demo-FOSS-for-Python"
LIBRARY_COMMIT = "c" * 40
PILOT_DIR_NAME = "demo_python"
README_FIRST = "Create the deck"
README_SECOND = "Save the deck"
AUTHORED_TITLE = "Page walk-through"
AUTHORED_SECOND_TITLE = "Page rotation"
PAGE_SYMBOL = "Aspose.Pdf.Page"
README_VERIFIED_OUTPUT = "readme verified\n"
AUTHORED_VERIFIED_OUTPUT = "authored verified\n"


def _real_api_types() -> list[dict]:
    return json.loads(API_SURFACE.read_text(encoding="utf-8"))["types"]


def _write_pilot_page(pilot_dir: Path) -> Path:
    front_matter = {
        "title": "Demo",
        "overview": {"enable": True, "title": "Overview", "content": "Demo library overview prose."},
        "single": {
            "block": [
                {
                    "title": README_FIRST,
                    "content": "Make a deck.\n\n```python\nimport demo\ndemo.make()\n```\n",
                },
                {
                    "title": README_SECOND,
                    "content": "Save the deck.\n\n```python\nimport demo\ndemo.save()\n```\n",
                },
            ]
        },
    }
    page = pilot_dir / "pages" / "_index.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        "---\n" + yaml.safe_dump(front_matter, sort_keys=False) + "---\n", encoding="utf-8", newline="\n"
    )
    return page


def _authored_code(title: str) -> str:
    return f"# Authored example: {title}\nimport demo\npage = demo.Page()\npage.rotate()\n"


def _write_pilot_sidecar(pilot_dir: Path, *, commit: str = LIBRARY_COMMIT, titles=(AUTHORED_TITLE,)) -> Path:
    examples = [
        {
            "title": title,
            "description": f"Walk through the page API ({title}).",
            "symbols": [PAGE_SYMBOL],
            "code": _authored_code(title),
            "code_sha256": normalised_code_sha256(_authored_code(title)),
        }
        for title in titles
    ]
    document = {
        "schema_version": 1,
        "pilot": pilot_dir.name,
        "library_repository": LIBRARY_REPOSITORY,
        "library_commit": commit,
        "language": "python",
        "examples": examples,
    }
    return _write_sidecar(pilot_dir / SIDECAR_NAME, document)


def _argv(out_dir: Path, page: Path, extra: list[str] | None = None) -> list[str]:
    return [
        "--api-surface", str(API_SURFACE),
        "--title", "Authored examples fixture",
        "--out", str(out_dir / "chunks.json"),
        "--furnished-page", str(page),
        "--library-repository", LIBRARY_REPOSITORY,
        "--library-commit", LIBRARY_COMMIT,
        "--library-platform", "python",
        *(extra or []),
    ]  # fmt: skip


def _run_main(argv: list[str]) -> None:
    old_argv = sys.argv
    sys.argv = ["build_chunks.py", *argv]
    try:
        build_chunks.main()
    finally:
        sys.argv = old_argv


class _Verifier:
    """Records every verify call and judges by title."""

    def __init__(self, verified: set[str]) -> None:
        self.verified = verified
        self.calls: list[dict] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> _Verifier:
        def fake_prepare(repository: str, commit: str, workdir: Path) -> Path:
            return Path("dummy-venv") / "python"

        def fake_verify(candidate, *, venv_python: Path, workdir: Path) -> VerificationResult:
            self.calls.append(
                {"title": candidate.title, "candidate": candidate, "venv": venv_python, "workdir": workdir}
            )
            if candidate.title in self.verified:
                output = (
                    AUTHORED_VERIFIED_OUTPUT if candidate.provenance == "authored" else README_VERIFIED_OUTPUT
                )
                return VerificationResult(candidate=candidate, verified=True, output=output)
            return VerificationResult(candidate=candidate, verified=False, output="rejected\n")

        monkeypatch.setattr(build_chunks, "prepare_python_library", fake_prepare)
        monkeypatch.setitem(build_chunks._PLATFORM_DISPATCH["python"], "verify", fake_verify)
        return self


def _chunks(out_dir: Path) -> list[dict]:
    return json.loads((out_dir / "chunks.json").read_text(encoding="utf-8"))["chunks"]


def _report(out_dir: Path) -> list[dict]:
    return json.loads((out_dir / "chunks.verification.json").read_text(encoding="utf-8"))


def _example_chunk(chunks: list[dict], title: str) -> dict | None:
    marker = f"FQN: Example: {title}\n"
    found = [chunk for chunk in chunks if marker in chunk["text"]]
    assert len(found) <= 1
    return found[0] if found else None


@pytest.fixture
def pilot(tmp_path: Path) -> tuple[Path, Path]:
    pilot_dir = tmp_path / PILOT_DIR_NAME
    return pilot_dir, _write_pilot_page(pilot_dir)


def _run_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pilot_dir: Path, page: Path, verified: set[str]
) -> tuple[Path, Path]:
    """One run without a sidecar and one with, each in its own out directory."""
    _Verifier(verified).install(monkeypatch)
    plain_out = tmp_path / "plain"
    _run_main(_argv(plain_out, page))
    _write_pilot_sidecar(pilot_dir)
    sidecar_out = tmp_path / "sidecar"
    _run_main(_argv(sidecar_out, page))
    return plain_out, sidecar_out


def test_a_verified_authored_example_is_published_with_the_three_lines_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pilot, capsys: pytest.CaptureFixture[str]
) -> None:
    pilot_dir, page = pilot
    _write_pilot_sidecar(pilot_dir)
    _Verifier({README_FIRST, README_SECOND, AUTHORED_TITLE}).install(monkeypatch)

    _run_main(_argv(tmp_path, page))

    chunk = _example_chunk(_chunks(tmp_path), AUTHORED_TITLE)
    assert chunk is not None, "the verified authored example must be published"
    lines = chunk["text"].split("\n")
    kind = lines.index("Kind: verified_example")
    assert lines[kind + 1 : kind + 4] == ["Provenance: authored", "Verification: passed", AUTHORED_LABEL]
    assert lines[kind + 4].startswith("Walk through the page API")
    assert "Example:\n" + _authored_code(AUTHORED_TITLE).strip() in chunk["text"]

    printed = capsys.readouterr().out.splitlines()
    verified_line = printed.index("verified 3/3 candidate examples")
    assert printed[verified_line + 1] == "authored examples: 1 of 1 verified"


def test_the_authored_label_text_is_the_exact_documented_sentence() -> None:
    assert AUTHORED_LABEL == (
        "Authored example: written for foss-mcp against the pinned library, not taken from the upstream "
        "README; published only after the ingestion verifier compiled or ran it against that commit."
    )


def test_the_authored_verification_record_has_kind_authored_and_continues_the_numbering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pilot
) -> None:
    pilot_dir, page = pilot
    _write_pilot_sidecar(pilot_dir)
    _Verifier({README_FIRST, README_SECOND, AUTHORED_TITLE}).install(monkeypatch)

    _run_main(_argv(tmp_path, page))

    report = _report(tmp_path)
    assert [(r["title"], r["kind"], r["index"]) for r in report] == [
        (README_FIRST, "candidate", 0),
        (README_SECOND, "candidate", 1),
        (AUTHORED_TITLE, "authored", 2),
    ]
    assert report[2]["verified"] is True and report[2]["output"] == AUTHORED_VERIFIED_OUTPUT


def test_an_unverified_authored_example_is_never_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pilot, capsys: pytest.CaptureFixture[str]
) -> None:
    pilot_dir, page = pilot
    _write_pilot_sidecar(pilot_dir)
    _Verifier({README_FIRST, README_SECOND}).install(monkeypatch)

    _run_main(_argv(tmp_path, page))

    chunks = _chunks(tmp_path)
    assert _example_chunk(chunks, AUTHORED_TITLE) is None
    assert not any(AUTHORED_TITLE in chunk["text"] for chunk in chunks)
    assert not any("Provenance: authored" in chunk["text"] for chunk in chunks)
    record = _report(tmp_path)[2]
    assert (record["kind"], record["verified"]) == ("authored", False)
    printed = capsys.readouterr().out
    assert "verified 2/3 candidate examples" in printed
    assert "authored examples: 0 of 1 verified" in printed


def test_authored_candidates_go_through_the_same_verifier_with_the_same_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pilot
) -> None:
    pilot_dir, page = pilot
    _write_pilot_sidecar(pilot_dir)
    verifier = _Verifier({README_FIRST, README_SECOND, AUTHORED_TITLE}).install(monkeypatch)

    _run_main(_argv(tmp_path, page))

    assert [call["title"] for call in verifier.calls] == [README_FIRST, README_SECOND, AUTHORED_TITLE]
    assert len({call["venv"] for call in verifier.calls}) == 1
    assert len({call["workdir"] for call in verifier.calls}) == 1, "python's shared page workdir rule holds"
    authored_call = verifier.calls[-1]["candidate"]
    assert authored_call.code == _authored_code(AUTHORED_TITLE).strip()
    assert authored_call.page_code == (authored_call.code,)
    for call in verifier.calls[:2]:
        assert _authored_code(AUTHORED_TITLE).strip() not in call["candidate"].page_code


def test_every_readme_example_chunk_is_identical_with_and_without_a_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pilot
) -> None:
    pilot_dir, page = pilot
    plain_out, sidecar_out = _run_pair(
        tmp_path, monkeypatch, pilot_dir, page, {README_FIRST, README_SECOND, AUTHORED_TITLE}
    )

    plain, with_sidecar = _chunks(plain_out), _chunks(sidecar_out)
    assert len(with_sidecar) == len(plain) + 1
    remaining = [chunk for chunk in with_sidecar if AUTHORED_TITLE not in chunk["text"]]
    assert remaining == plain, "README example chunks, doc chunks and API chunks must be untouched"
    for title in (README_FIRST, README_SECOND):
        chunk = _example_chunk(plain, title)
        assert chunk is not None
        assert "Provenance:" not in chunk["text"] and "Verification:" not in chunk["text"]
        assert _example_chunk(with_sidecar, title) == chunk


def test_an_invalid_sidecar_is_ignored_with_one_printed_line_and_the_readme_still_publishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pilot, capsys: pytest.CaptureFixture[str]
) -> None:
    pilot_dir, page = pilot
    verifier = _Verifier({README_FIRST, README_SECOND, AUTHORED_TITLE}).install(monkeypatch)
    plain_out = tmp_path / "plain"
    _run_main(_argv(plain_out, page))
    capsys.readouterr()

    _write_pilot_sidecar(pilot_dir, commit="d" * 40)  # stale: not the pinned commit
    stale_out = tmp_path / "stale"
    _run_main(_argv(stale_out, page))

    printed = capsys.readouterr().out.splitlines()
    ignored = [line for line in printed if line.startswith("authored examples ignored: ")]
    assert len(ignored) == 1 and "rule 3" in ignored[0]
    assert len(ignored[0]) <= len("authored examples ignored: ") + 600
    assert not any(line.startswith("authored examples:") for line in printed)
    assert _chunks(stale_out) == _chunks(plain_out), "README examples and doc chunks are unaffected"
    assert [record["kind"] for record in _report(stale_out)] == ["candidate", "candidate"]
    assert AUTHORED_TITLE not in [call["title"] for call in verifier.calls]


@pytest.mark.parametrize(
    "damage", ["not yaml: [", "- a\n- list\n", ""], ids=["syntax", "wrong-shape", "empty"]
)
def test_a_damaged_sidecar_never_stops_the_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pilot, capsys: pytest.CaptureFixture[str], damage: str
) -> None:
    pilot_dir, page = pilot
    (pilot_dir / SIDECAR_NAME).write_text(damage, encoding="utf-8")
    _Verifier({README_FIRST, README_SECOND}).install(monkeypatch)

    _run_main(_argv(tmp_path, page))

    assert "authored examples ignored: " in capsys.readouterr().out
    assert _example_chunk(_chunks(tmp_path), README_FIRST) is not None


def test_a_sidecar_for_another_pilot_directory_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pilot, capsys: pytest.CaptureFixture[str]
) -> None:
    pilot_dir, page = pilot
    sidecar = _write_pilot_sidecar(pilot_dir)
    document = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
    document["pilot"] = "someone_else"
    _write_sidecar(sidecar, document)
    _Verifier({README_FIRST, README_SECOND, AUTHORED_TITLE}).install(monkeypatch)

    _run_main(_argv(tmp_path, page))

    assert "rule 2" in capsys.readouterr().out
    assert _example_chunk(_chunks(tmp_path), AUTHORED_TITLE) is None


def test_no_sidecar_is_looked_for_unless_the_page_sits_in_a_pages_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pilot_dir = tmp_path / PILOT_DIR_NAME
    page = _write_pilot_page(pilot_dir)
    loose = pilot_dir / "loose.md"
    shutil.copyfile(page, loose)
    _write_pilot_sidecar(pilot_dir)
    _Verifier({README_FIRST, README_SECOND, AUTHORED_TITLE}).install(monkeypatch)

    _run_main(_argv(tmp_path, loose))

    printed = capsys.readouterr().out
    assert "authored examples" not in printed
    assert _example_chunk(_chunks(tmp_path), AUTHORED_TITLE) is None


def test_a_page_with_no_sidecar_prints_no_authored_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pilot, capsys: pytest.CaptureFixture[str]
) -> None:
    _, page = pilot
    _Verifier({README_FIRST, README_SECOND}).install(monkeypatch)

    _run_main(_argv(tmp_path, page))

    assert "authored examples" not in capsys.readouterr().out


# --- the verification cache (TC-441) --------------------------------------------------------------


def _cache_entries(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["entries"]


def test_an_authored_verdict_is_cached_with_provenance_authored_and_the_sidecars_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pilot
) -> None:
    pilot_dir, page = pilot
    sidecar = _write_pilot_sidecar(pilot_dir)
    sidecar_hash = yaml.safe_load(sidecar.read_text(encoding="utf-8"))["examples"][0]["code_sha256"]
    monkeypatch.setattr(build_chunks, "toolchain_id", lambda platform: "Python 3.13.1")
    _Verifier({README_FIRST, README_SECOND, AUTHORED_TITLE}).install(monkeypatch)
    cache_out = tmp_path / "cache.json"

    _run_main(_argv(tmp_path, page, ["--verification-cache-out", str(cache_out)]))

    entries = {entry["title"]: entry for entry in _cache_entries(cache_out)}
    assert entries[AUTHORED_TITLE]["provenance"] == "authored"
    assert entries[AUTHORED_TITLE]["code_sha256"] == sidecar_hash
    assert entries[AUTHORED_TITLE]["verified"] is True
    assert entries[README_FIRST]["provenance"] == "readme"


def test_a_cached_and_a_fresh_authored_verdict_publish_identical_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pilot
) -> None:
    pilot_dir, page = pilot
    _write_pilot_sidecar(pilot_dir)
    monkeypatch.setattr(build_chunks, "toolchain_id", lambda platform: "Python 3.13.1")
    verifier = _Verifier({README_FIRST, README_SECOND, AUTHORED_TITLE}).install(monkeypatch)
    cache = tmp_path / "cache.json"
    fresh_out = tmp_path / "fresh"
    _run_main(_argv(fresh_out, page, ["--verification-cache-out", str(cache)]))
    assert len(verifier.calls) == 3

    cached_out = tmp_path / "cached"
    _run_main(_argv(cached_out, page, ["--verification-cache", str(cache)]))

    assert len(verifier.calls) == 3, "a fully cached page verifies nothing again"
    assert [r["cached"] for r in _report(cached_out)] == [True, True, True]
    assert _chunks(cached_out) == _chunks(fresh_out)


def test_a_readme_cache_entry_never_satisfies_an_authored_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pilot
) -> None:
    pilot_dir, page = pilot
    _write_pilot_sidecar(pilot_dir)
    monkeypatch.setattr(build_chunks, "toolchain_id", lambda platform: "Python 3.13.1")
    verifier = _Verifier({README_FIRST, README_SECOND, AUTHORED_TITLE}).install(monkeypatch)
    cache = tmp_path / "cache.json"

    def entry(title: str, code: str, provenance: str) -> dict:
        return {
            "provenance": provenance,
            "title": title,
            "code_sha256": normalised_code_sha256(code),
            "library_commit": LIBRARY_COMMIT,
            "platform": "python",
            "toolchain": "Python 3.13.1",
            "verified": True,
            "output": "recorded",
        }

    entries = [
        entry(README_FIRST, "import demo\ndemo.make()", "readme"),
        entry(README_SECOND, "import demo\ndemo.save()", "readme"),
        entry(AUTHORED_TITLE, _authored_code(AUTHORED_TITLE), "readme"),  # same code, wrong provenance
    ]
    cache.write_text(build_chunks.verification_cache_document(entries), encoding="utf-8", newline="\n")

    _run_main(_argv(tmp_path, page, ["--verification-cache", str(cache)]))

    assert AUTHORED_TITLE in [call["title"] for call in verifier.calls]


# --- wiring and the image ---------------------------------------------------------------------------


def test_build_chunks_source_wires_the_loader_and_passes_authored_to_the_supply() -> None:
    source = BUILD_CHUNKS_SOURCE.read_text(encoding="utf-8")
    assert "load_authored_examples(" in source
    assert "authored=" in source
    assert "from foss_mcp.indexing.authored_examples import" in source
    assert "extract_candidate_supply(page, platform, authored=authored)" in source


def test_the_ingestion_dockerfile_still_copies_the_furnished_directory_whole() -> None:
    lines = [line.strip() for line in DOCKERFILE_INGESTION.read_text(encoding="utf-8").splitlines()]
    assert "COPY tests/fixtures/furnished/ ./fixtures/furnished/" in lines


# --- regeneration never touches a sidecar -----------------------------------------------------------

README = "\n".join(
    [
        "# Demo Library for .NET",
        "",
        "Demo Library for .NET is a small library used to prove that a sidecar survives regeneration.",
        "",
        "## Quick Start",
        "",
        "Create a document and save it:",
        "",
        "```csharp",
        "var document = new Document();",
        'document.Save("out.pdf");',
        "```",
        "",
        *(f"- development note {number}" for number in range(60)),
    ]
)


def _regeneration_root(root: Path) -> Path:
    values = root / "infra" / "helm" / "foss-mcp" / "values.yaml"
    values.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(CHART_VALUES, values)
    return root


def test_regenerating_a_page_never_touches_a_sidecar_and_never_changes_the_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        gen, "readme_doc_fields", lambda repository, commit: gen.parse_readme(repository, commit, README)
    )
    with_sidecar = _regeneration_root(tmp_path / "with")
    without_sidecar = _regeneration_root(tmp_path / "without")
    furnished = Path("tests") / "fixtures" / "furnished" / "pdf_net"
    sidecar = with_sidecar / furnished / SIDECAR_NAME
    sidecar_bytes = b"# a sidecar the generator must never read or rewrite\r\nschema_version: 1\n"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_bytes(sidecar_bytes)

    gen.main(["--source", "github", "--project-root", str(with_sidecar), "--pilot", "pdf_net"])
    gen.main(["--source", "github", "--project-root", str(without_sidecar), "--pilot", "pdf_net"])

    assert sidecar.read_bytes() == sidecar_bytes
    page_with = (with_sidecar / furnished / "pages" / "_index.md").read_bytes()
    page_without = (without_sidecar / furnished / "pages" / "_index.md").read_bytes()
    assert page_with == page_without and page_with
    assert sorted(path.name for path in (with_sidecar / furnished).iterdir()) == [SIDECAR_NAME, "pages"]
    assert not any(SIDECAR_NAME in str(path) for path in (without_sidecar / furnished).rglob("*"))


def test_an_authored_example_is_also_published_when_the_page_has_no_fence_in_the_pilots_language(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pilot_dir = tmp_path / PILOT_DIR_NAME
    page = pilot_dir / "pages" / "_index.md"
    page.parent.mkdir(parents=True)
    front_matter = {
        "title": "Demo",
        "single": {
            "block": [{"title": "Native only", "content": "Text.\n\n```cpp\nint main() { return 0; }\n```\n"}]
        },
    }
    page.write_text(
        "---\n" + yaml.safe_dump(front_matter, sort_keys=False) + "---\n", encoding="utf-8", newline="\n"
    )
    _write_pilot_sidecar(pilot_dir)
    verifier = _Verifier({AUTHORED_TITLE}).install(monkeypatch)

    _run_main(_argv(tmp_path, page))

    assert "no python code fences on the page" in capsys.readouterr().out
    assert [call["title"] for call in verifier.calls] == ["Native only", AUTHORED_TITLE]
    assert _example_chunk(_chunks(tmp_path), AUTHORED_TITLE) is not None
    assert _example_chunk(_chunks(tmp_path), "Native only") is None
    assert [(r["kind"], r["index"]) for r in _report(tmp_path)] == [("candidate", 0), ("authored", 1)]


def test_the_schema_document_is_not_mutated_by_validation() -> None:
    document = _document()
    before = copy.deepcopy(document)
    _validate(document)
    assert document == before


# ---------------------------------------------------------------------------------------------------
# TC-565 (REQ-G3-009): the authored examples that 3d/net and 3d/java carry in authored_examples.py
# ---------------------------------------------------------------------------------------------------

BUILTIN_PILOTS = {
    "3d_net": ("dotnet", "52b0f00ebf28a0b4173921725ff170685ec2c502", "csharp"),
    "3d_java": ("java", "3d2ed6be91f5abdd1c52ecbcfa192719883cb1b3", "java"),
}


def _surface_types(pilot: str) -> list[dict]:
    path = REPO_ROOT / "tests" / "fixtures" / pilot / "api_surface.json"
    return json.loads(path.read_text(encoding="utf-8"))["types"]


def _builtin(pilot: str) -> tuple[CandidateExample, ...]:
    platform, commit, _language = BUILTIN_PILOTS[pilot]
    return authored_examples.load_builtin_authored_examples(
        pilot, library_platform=platform, library_commit=commit, api_types=_surface_types(pilot)
    )


@pytest.mark.parametrize("pilot", sorted(BUILTIN_PILOTS))
def test_tc565_each_3d_pilot_carries_at_least_two_authored_examples(pilot: str) -> None:
    candidates = _builtin(pilot)
    assert len(candidates) >= 2
    assert {c.provenance for c in candidates} == {"authored"}
    assert len({c.title for c in candidates}) == len(candidates)


@pytest.mark.parametrize("pilot", sorted(BUILTIN_PILOTS))
def test_tc565_the_builtin_document_passes_every_sidecar_rule(pilot: str) -> None:
    platform, commit, language = BUILTIN_PILOTS[pilot]
    document = authored_examples.builtin_authored_document(pilot)
    assert document["language"] == language and document["pilot"] == pilot
    assert (
        validate_authored_examples(
            document,
            pilot=pilot,
            library_platform=platform,
            library_commit=commit,
            api_types=_surface_types(pilot),
        )
        == []
    )


@pytest.mark.parametrize("pilot", sorted(BUILTIN_PILOTS))
def test_tc565_the_builtin_document_validates_against_the_json_schema(pilot: str) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(authored_examples.builtin_authored_document(pilot), schema)


@pytest.mark.parametrize("pilot", sorted(BUILTIN_PILOTS))
def test_tc565_a_builtin_example_never_attaches_a_primitive_directly_or_uses_an_unimplemented_format(
    pilot: str,
) -> None:
    """The two README failures this card closes, checked on the code itself: a primitive must go through
    ToMesh/toMesh (attached directly it has no polygons and STL is written empty), and the format must be STL
    (the one format the pilot's scope says works end to end)."""
    for candidate in _builtin(pilot):
        assert re.search(r"\.[Tt]oMesh\(\)", candidate.code), candidate.title
        assert not re.search(r"\.obj\b|WavefrontOBJ", candidate.code, re.IGNORECASE), candidate.title
        assert ".stl" in candidate.code, candidate.title
        assert normalised_code_sha256(candidate.code) == candidate.code_sha256


@pytest.mark.parametrize("pilot", sorted(BUILTIN_PILOTS))
def test_tc565_the_builtin_titles_do_not_repeat_a_readme_title(pilot: str) -> None:
    page = (REPO_ROOT / "tests" / "fixtures" / "furnished" / pilot / "pages" / "_index.md").read_text(
        encoding="utf-8"
    )
    for candidate in _builtin(pilot):
        assert f"title: {candidate.title}" not in page


@pytest.mark.parametrize("pilot", sorted(BUILTIN_PILOTS))
def test_tc565_a_builtin_example_is_rejected_when_the_commit_is_not_the_one_it_was_written_against(
    pilot: str,
) -> None:
    platform = BUILTIN_PILOTS[pilot][0]
    with pytest.raises(AuthoredExamplesError, match="rule 3"):
        authored_examples.load_builtin_authored_examples(
            pilot, library_platform=platform, library_commit="f" * 40, api_types=_surface_types(pilot)
        )


def test_tc565_a_builtin_example_naming_an_undeclared_symbol_is_rejected() -> None:
    with pytest.raises(AuthoredExamplesError, match="rule 6"):
        authored_examples.load_builtin_authored_examples(
            "3d_java",
            library_platform="java",
            library_commit=BUILTIN_PILOTS["3d_java"][1],
            api_types=[{"name": "Unrelated"}],
        )


def test_tc565_only_the_two_3d_pilots_carry_builtin_examples() -> None:
    assert authored_examples.has_builtin_authored_examples("3d_net")
    assert authored_examples.has_builtin_authored_examples("3d_java")
    assert not authored_examples.has_builtin_authored_examples("pdf_net")
    assert not authored_examples.has_builtin_authored_examples("3d_python")
    with pytest.raises(KeyError):
        authored_examples.builtin_authored_document("pdf_net")


@pytest.mark.parametrize("pilot", sorted(BUILTIN_PILOTS))
def test_tc565_build_chunks_loads_the_builtin_examples_for_a_pilot_without_a_sidecar(pilot: str) -> None:
    platform, commit, _language = BUILTIN_PILOTS[pilot]
    pilot_dir = REPO_ROOT / "tests" / "fixtures" / "furnished" / pilot
    assert not (pilot_dir / SIDECAR_NAME).exists()
    args = argparse.Namespace(
        furnished_page=pilot_dir / "pages" / "_index.md",
        api_surface=REPO_ROOT / "tests" / "fixtures" / pilot / "api_surface.json",
        library_platform=platform,
        library_commit=commit,
    )
    loaded = build_chunks._load_authored_candidates(args)
    assert [c.title for c in loaded] == [c.title for c in _builtin(pilot)]


def test_tc565_build_chunks_ignores_the_builtin_examples_when_the_commit_has_moved(
    capsys: pytest.CaptureFixture[str],
) -> None:
    pilot_dir = REPO_ROOT / "tests" / "fixtures" / "furnished" / "3d_java"
    args = argparse.Namespace(
        furnished_page=pilot_dir / "pages" / "_index.md",
        api_surface=REPO_ROOT / "tests" / "fixtures" / "3d_java" / "api_surface.json",
        library_platform="java",
        library_commit="e" * 40,
    )
    assert build_chunks._load_authored_candidates(args) == ()
    assert "authored examples ignored" in capsys.readouterr().out


def test_tc565_a_sidecar_file_still_wins_over_the_builtin_examples(tmp_path: Path) -> None:
    pilot_dir = tmp_path / "3d_java"
    (pilot_dir / "pages").mkdir(parents=True)
    (pilot_dir / "pages" / "_index.md").write_text("---\ntitle: x\n---\n", encoding="utf-8")
    code = '// Authored example: scene\nScene scene = new Scene();\nscene.save("a.stl");\n'
    document = {
        "schema_version": 1,
        "pilot": "3d_java",
        "library_repository": "aspose-3d-foss/Aspose.3D-FOSS-for-Java",
        "library_commit": BUILTIN_PILOTS["3d_java"][1],
        "language": "java",
        "examples": [
            {
                "title": "Own sidecar example",
                "description": "d",
                "symbols": ["Scene"],
                "code": code,
                "code_sha256": normalised_code_sha256(code),
            }
        ],
    }
    (pilot_dir / SIDECAR_NAME).write_text(yaml.safe_dump(document), encoding="utf-8")
    args = argparse.Namespace(
        furnished_page=pilot_dir / "pages" / "_index.md",
        api_surface=REPO_ROOT / "tests" / "fixtures" / "3d_java" / "api_surface.json",
        library_platform="java",
        library_commit=BUILTIN_PILOTS["3d_java"][1],
    )
    assert [c.title for c in build_chunks._load_authored_candidates(args)] == ["Own sidecar example"]
