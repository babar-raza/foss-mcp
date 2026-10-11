"""TC-363: the chart carries each Java, C++, TypeScript and Node.js pilot's verifier fields.

Offline. Renders the chart with the real helm binary (image.tag set) and parses the output with
PyYAML. A missing helm binary FAILS these tests; it never skips them.

The five library keys javaPackages, cppSourceDir, cppTarget, cppWarnings and cppVerifyMode are
forwarded to infra/build_chunks.py by infra/helm/foss-mcp/templates/ingestion-job.yaml, and only
inside the furnishedPage guard. The shipped chart renders them for each pilot that has a furnishedPage
and holds the key; an override file proves the forwarding and the guard.
"""

from __future__ import annotations

import copy
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

import infra.build_chunks as build_chunks

REPO_ROOT = Path(__file__).resolve().parents[2]
CHART = REPO_ROOT / "infra" / "helm" / "foss-mcp"
VALUES = CHART / "values.yaml"
BUILD_CHUNKS = REPO_ROOT / "infra" / "build_chunks.py"
TAG = "tc363-test-tag"

# values.yaml library key -> the build_chunks.py flag it is forwarded as.
VERIFIER_KEYS = {
    "javaPackages": "--library-java-packages",
    "cppSourceDir": "--library-cpp-source-dir",
    "cppTarget": "--library-cpp-target",
    "cppWarnings": "--library-cpp-warnings",
    "cppVerifyMode": "--library-cpp-verify-mode",
}

# Every library field TC-363 records, per (family, platform), exactly as the card specifies them.
# The packageName entries are the typescript and nodejs pilots' existing key; the other keys are the
# five VERIFIER_KEYS.
EXPECTED_LIBRARY_FIELDS: dict[tuple[str, str], dict[str, str]] = {
    ("slides", "java"): {"javaPackages": "org.aspose.slides.foss"},
    ("cells", "java"): {"javaPackages": "org.aspose.cells_foss"},
    ("jmap", "java"): {"javaPackages": "com.aspose.jmap"},
    ("3d", "java"): {"javaPackages": "com.aspose.threed"},
    ("cells", "cpp"): {
        "cppSourceDir": "Aspose.Cells.Foss.Cpp",
        "cppTarget": "aspose_cells_foss",
        "cppWarnings": "relaxed",
    },
    ("slides", "cpp"): {"cppTarget": "AsposeSlidesFoss"},
    ("email", "cpp"): {"cppTarget": "AsposeEmailFoss", "cppVerifyMode": "compile"},
    ("jmap", "cpp"): {"cppTarget": "aspose-jmap-foss-cpp", "cppVerifyMode": "compile"},
    ("cells", "typescript"): {"packageName": "excel-cells"},
    ("jmap", "typescript"): {"packageName": "aspose-jmap-foss-ts"},
    ("3d", "typescript"): {"packageName": "@aspose/3d"},
    ("jmap", "nodejs"): {"packageName": "aspose-jmap-foss"},
}

# The pilots that get a furnishedPage in the override render of test 3.
WIRED_PILOTS = [
    ("slides", "java"),
    ("cells", "cpp"),
    ("jmap", "cpp"),
    ("jmap", "typescript"),
    ("jmap", "nodejs"),
]


def _helm() -> str:
    helm = shutil.which("helm")
    if helm is None:
        pytest.fail(
            "helm is not on PATH. TC-363 requires it: source scripts/toolchain/activate.ps1, "
            "which adds C:\\dev-tools\\foss-mcp\\helm\\windows-amd64."
        )
    return helm


def _render(*extra: str) -> list[dict[str, Any]]:
    result = subprocess.run(
        [_helm(), "template", "foss-mcp", str(CHART), "--set", f"image.tag={TAG}", *extra],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def _values() -> dict[str, Any]:
    return yaml.safe_load(VALUES.read_text(encoding="utf-8"))


def _pilot(values: dict[str, Any], family: str, platform: str) -> dict[str, Any]:
    matches = [p for p in values["ingestion"]["pilots"] if (p["family"], p["platform"]) == (family, platform)]
    assert len(matches) == 1, f"expected one {family}/{platform} pilot, got {len(matches)}"
    return matches[0]


def _jobs(docs: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """Every rendered ingestion Job, keyed by (family, platform) from its name suffix."""
    values = _values()
    jobs = [doc for doc in docs if doc.get("kind") == "Job"]
    by_pilot: dict[tuple[str, str], dict[str, Any]] = {}
    for pilot in values["ingestion"]["pilots"]:
        suffix = f"-ingest-{pilot['family']}-{pilot['platform']}"
        matches = [job for job in jobs if job["metadata"]["name"].endswith(suffix)]
        assert len(matches) == 1, f"expected one Job for {pilot['family']}/{pilot['platform']}"
        by_pilot[(pilot["family"], pilot["platform"])] = matches[0]
    return by_pilot


def _build_chunks_tokens(job: dict[str, Any]) -> list[str]:
    """The tokens of the build_chunks.py command only: the chain up to its first '&&'."""
    container = job["spec"]["template"]["spec"]["containers"][0]
    tokens = shlex.split(container["args"][0])
    start = tokens.index("/app/infra/build_chunks.py") + 1
    end = tokens.index("&&", start)
    return tokens[start:end]


def _flags(tokens: list[str]) -> dict[str, str]:
    """Flag -> value for a build_chunks.py command. Every flag takes exactly one value."""
    flags: dict[str, str] = {}
    index = 0
    while index < len(tokens):
        flag = tokens[index]
        assert flag.startswith("--"), f"expected a flag, got {flag!r} in {tokens}"
        assert index + 1 < len(tokens), f"flag {flag} has no value in {tokens}"
        value = tokens[index + 1]
        assert not value.startswith("-"), f"value {value!r} of {flag} begins with a dash"
        assert flag not in flags, f"flag {flag} rendered twice"
        flags[flag] = value
        index += 2
    return flags


def _override(tmp_path: Path, wired: list[tuple[str, str]] | None) -> Path:
    """An override file giving the named pilots (None: every pilot) a furnishedPage.

    Helm replaces a list wholesale, so the override carries the complete pilots list, copied from
    values.yaml with only furnishedPage added.
    """
    pilots = copy.deepcopy(_values()["ingestion"]["pilots"])
    for pilot in pilots:
        key = (pilot["family"], pilot["platform"])
        if wired is None or key in wired:
            pilot["furnishedPage"] = (
                f"/app/fixtures/furnished/{pilot['family']}_{pilot['platform']}/pages/_index.md"
            )
    path = tmp_path / ("override_all.yaml" if wired is None else "override_wired.yaml")
    path.write_text(yaml.safe_dump({"ingestion": {"pilots": pilots}}), encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def shipped_jobs() -> dict[tuple[str, str], dict[str, Any]]:
    return _jobs(_render())


@pytest.fixture(scope="module")
def wired_jobs(tmp_path_factory: pytest.TempPathFactory) -> dict[tuple[str, str], dict[str, Any]]:
    path = _override(tmp_path_factory.mktemp("tc363_wired"), WIRED_PILOTS)
    return _jobs(_render("-f", str(path)))


@pytest.fixture(scope="module")
def all_wired_jobs(tmp_path_factory: pytest.TempPathFactory) -> dict[tuple[str, str], dict[str, Any]]:
    path = _override(tmp_path_factory.mktemp("tc363_all"), None)
    return _jobs(_render("-f", str(path)))


def _help_choices(flag: str) -> set[str]:
    """The choices build_chunks.py's own --help advertises for a flag."""
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}
    result = subprocess.run(
        [sys.executable, str(BUILD_CHUNKS), "--help"],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    match = re.search(re.escape(flag) + r"\s+\{([^}]*)\}", result.stdout)
    assert match is not None, f"{flag} not found with choices in build_chunks.py --help"
    return set(match.group(1).split(","))


# (1) values.yaml holds exactly the card's values, and no other pilot holds a verifier key.


def test_the_twelve_pilots_hold_exactly_the_recorded_library_fields() -> None:
    values = _values()
    verifier_keys = set(VERIFIER_KEYS)
    for (family, platform), expected in EXPECTED_LIBRARY_FIELDS.items():
        library = _pilot(values, family, platform)["library"]
        for key, value in expected.items():
            assert library.get(key) == value, f"{family}/{platform} library.{key}"
        held = verifier_keys & set(library)
        assert held == verifier_keys & set(expected), f"{family}/{platform} holds {sorted(held)}"


def test_no_other_pilot_holds_a_verifier_key() -> None:
    keys = set(VERIFIER_KEYS)
    for pilot in _values()["ingestion"]["pilots"]:
        if (pilot["family"], pilot["platform"]) in EXPECTED_LIBRARY_FIELDS:
            continue
        held = keys & set(pilot.get("library", {}))
        assert not held, f"{pilot['family']}/{pilot['platform']} unexpectedly holds {sorted(held)}"


def test_the_typescript_and_nodejs_pilots_each_hold_a_package_name() -> None:
    values = _values()
    for family, platform in [
        ("cells", "typescript"),
        ("jmap", "typescript"),
        ("3d", "typescript"),
        ("jmap", "nodejs"),
    ]:
        library = _pilot(values, family, platform)["library"]
        assert library["packageName"] == EXPECTED_LIBRARY_FIELDS[(family, platform)]["packageName"]


# (2) with the shipped values nothing is forwarded.


def test_shipped_values_render_exactly_the_held_verifier_flags_of_wired_pilots(
    shipped_jobs: dict[tuple[str, str], dict[str, Any]],
) -> None:
    """Forward-compatible: a pilot with a furnishedPage renders exactly the verifier flags its library
    block holds, each with the key's value; a pilot without one renders none. No pilot ever renders a
    verifier flag its library block does not hold, and no rendered value begins with a dash (checked
    by _flags).
    """
    pilots = _values()["ingestion"]["pilots"]
    assert len(shipped_jobs) == len(pilots)
    for pilot in pilots:
        family, platform = pilot["family"], pilot["platform"]
        flags = _flags(_build_chunks_tokens(shipped_jobs[(family, platform)]))
        library = pilot.get("library") or {}
        if pilot.get("furnishedPage"):
            for key, flag in VERIFIER_KEYS.items():
                if key in library:
                    assert flags.get(flag) == str(library[key]), f"{family}/{platform} {flag}"
                else:
                    assert flag not in flags, f"{family}/{platform} renders {flag} it has no key for"
        else:
            for flag in VERIFIER_KEYS.values():
                assert flag not in flags, f"{family}/{platform} has no furnishedPage yet renders {flag}"
        for key in ("repository", "commit", "platform"):
            if pilot.get("furnishedPage") and key in library:
                assert flags[f"--library-{key}"] == str(library[key]), f"{family}/{platform} --library-{key}"


# (3) with a page wired, each pilot renders exactly its flags.


def test_wired_pilots_render_exactly_their_own_flags(
    wired_jobs: dict[tuple[str, str], dict[str, Any]],
) -> None:
    values = _values()
    for family, platform in WIRED_PILOTS:
        flags = _flags(_build_chunks_tokens(wired_jobs[(family, platform)]))
        library = _pilot(values, family, platform)["library"]
        expected_fields = EXPECTED_LIBRARY_FIELDS[(family, platform)]
        for key, flag in VERIFIER_KEYS.items():
            if key in expected_fields:
                assert flags.get(flag) == expected_fields[key], f"{family}/{platform} {flag}"
            else:
                assert flag not in flags, f"{family}/{platform} renders {flag} it has no key for"
        assert flags["--library-platform"] == library["platform"]
        assert flags["--library-repository"] == library["repository"]
        assert flags["--library-commit"] == library["commit"]
        if "packageName" in library:
            assert flags["--library-package-name"] == library["packageName"]
        else:
            assert "--library-package-name" not in flags


def test_slides_java_renders_only_the_java_packages_flag(
    wired_jobs: dict[tuple[str, str], dict[str, Any]],
) -> None:
    flags = _flags(_build_chunks_tokens(wired_jobs[("slides", "java")]))
    assert flags["--library-java-packages"] == "org.aspose.slides.foss"
    assert not {f for f in flags if f.startswith("--library-cpp-")}


def test_cells_cpp_renders_its_three_cpp_flags_and_no_verify_mode(
    wired_jobs: dict[tuple[str, str], dict[str, Any]],
) -> None:
    flags = _flags(_build_chunks_tokens(wired_jobs[("cells", "cpp")]))
    assert flags["--library-cpp-source-dir"] == "Aspose.Cells.Foss.Cpp"
    assert flags["--library-cpp-target"] == "aspose_cells_foss"
    assert flags["--library-cpp-warnings"] == "relaxed"
    assert "--library-cpp-verify-mode" not in flags
    assert "--library-java-packages" not in flags


def test_jmap_cpp_renders_target_and_compile_mode_only(
    wired_jobs: dict[tuple[str, str], dict[str, Any]],
) -> None:
    flags = _flags(_build_chunks_tokens(wired_jobs[("jmap", "cpp")]))
    assert flags["--library-cpp-target"] == "aspose-jmap-foss-cpp"
    assert flags["--library-cpp-verify-mode"] == "compile"
    assert "--library-cpp-source-dir" not in flags
    assert "--library-cpp-warnings" not in flags


def test_jmap_typescript_renders_its_package_name(wired_jobs: dict[tuple[str, str], dict[str, Any]]) -> None:
    flags = _flags(_build_chunks_tokens(wired_jobs[("jmap", "typescript")]))
    assert flags["--library-platform"] == "typescript"
    assert flags["--library-package-name"] == "aspose-jmap-foss-ts"


def test_jmap_nodejs_renders_the_nodejs_platform_with_its_package_name(
    wired_jobs: dict[tuple[str, str], dict[str, Any]],
) -> None:
    tokens = _build_chunks_tokens(wired_jobs[("jmap", "nodejs")])
    flags = _flags(tokens)
    assert flags["--library-platform"] == "nodejs"
    assert flags["--library-package-name"] == "aspose-jmap-foss"
    joined = " ".join(tokens)
    assert "--library-platform nodejs --library-package-name aspose-jmap-foss" in joined


def test_the_furnished_page_guard_withholds_every_verifier_flag(tmp_path: Path) -> None:
    """Render with an override that strips furnishedPage from wired pilots holding verifier keys: they
    must render no verifier flag and no --furnished-page, whatever their library block holds.
    """
    values = _values()
    stripped = [
        (pilot["family"], pilot["platform"])
        for pilot in values["ingestion"]["pilots"]
        if pilot.get("furnishedPage") and set(VERIFIER_KEYS) & set(pilot.get("library") or {})
    ]
    assert stripped, "no wired pilot holds a verifier key; the guard has nothing to protect"
    pilots = copy.deepcopy(values["ingestion"]["pilots"])
    for pilot in pilots:
        if (pilot["family"], pilot["platform"]) in stripped:
            del pilot["furnishedPage"]
    path = tmp_path / "override_stripped.yaml"
    path.write_text(yaml.safe_dump({"ingestion": {"pilots": pilots}}), encoding="utf-8")
    jobs = _jobs(_render("-f", str(path)))
    for key in stripped:
        command = jobs[key]["spec"]["template"]["spec"]["containers"][0]["args"][0]
        assert "--furnished-page" not in command, key
        for flag in VERIFIER_KEYS.values():
            assert flag not in command, f"{key[0]}/{key[1]} renders {flag} without a furnishedPage"


# (4) every rendered value is one build_chunks.py accepts.


def test_every_rendered_platform_and_cpp_choice_is_one_build_chunks_accepts(
    all_wired_jobs: dict[tuple[str, str], dict[str, Any]],
) -> None:
    platforms = set(sorted(build_chunks._PLATFORM_DISPATCH))
    warnings = _help_choices("--library-cpp-warnings")
    verify_modes = _help_choices("--library-cpp-verify-mode")
    assert warnings == {"strict", "relaxed"}
    assert verify_modes == {"run", "compile"}
    seen_platforms: set[str] = set()
    seen_verifier_flags: set[str] = set()
    for (family, platform), job in all_wired_jobs.items():
        flags = _flags(_build_chunks_tokens(job))
        assert flags["--library-platform"] in platforms, f"{family}/{platform}"
        seen_platforms.add(flags["--library-platform"])
        if "--library-cpp-warnings" in flags:
            assert flags["--library-cpp-warnings"] in warnings, f"{family}/{platform}"
        if "--library-cpp-verify-mode" in flags:
            assert flags["--library-cpp-verify-mode"] in verify_modes, f"{family}/{platform}"
        seen_verifier_flags |= set(VERIFIER_KEYS.values()) & set(flags)
    assert "nodejs" in seen_platforms
    assert seen_verifier_flags == set(VERIFIER_KEYS.values())
