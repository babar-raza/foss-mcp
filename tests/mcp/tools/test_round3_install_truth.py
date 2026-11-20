"""G3/TC-561 (REQ-G3-009, REQ-G3-008; rows RD3-080 to RD3-092): the install and product-reference answers are true.

Round-3 agents followed the install text and failed. Every test here builds the answer from the REAL upstream
files of the pilot at the chart-pinned commit, committed unchanged under ``tests/fixtures/round3_readmes/<pilot>/``
(the README, and the manifest, CMakeLists or guidance file the rule reads), and takes its expectation from an
independent oracle: the upstream CMakeLists, pom, csproj or package.json read with the stdlib (``xml``, ``json``,
``re`` on the file itself), the README text, or the registry answer recorded in ``infra/demo-manifests``. Never from
the answer's own code. Offline and deterministic.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ElementTree
from pathlib import Path

import pytest

from foss_mcp.extraction.github_release_reader import DriftRecord
from foss_mcp.extraction.repo_native_reader import DocumentNotPresent, DocumentPresent
from foss_mcp.mcp.tools import get_product_reference as product_reference
from foss_mcp.mcp.tools.get_product_reference import (
    NotAvailable,
    ProductReferenceInputs,
    ReferenceContent,
    get_product_reference,
    install_coordinate_for_verification,
    parse_cmake_facts,
)
from foss_mcp.mcp.tools.readme_sections import ReadmeSections, sections_from_documents
from tests.conformance.corpus import load_demo_manifests_corpus

REPO_ROOT = Path(__file__).resolve().parents[3]
ROUND3 = REPO_ROOT / "tests" / "fixtures" / "round3_readmes"
MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"
CHECKED_AT = "2026-10-19T00:00:00+00:00"


def _pin(pilot: str) -> dict:
    return json.loads((ROUND3 / pilot / "PIN.json").read_text(encoding="utf-8"))


def _upstream(pilot: str, relative: str) -> str:
    """A file of the pilot's repository at its pinned commit, exactly as upstream has it."""
    return (ROUND3 / pilot / relative).read_text(encoding="utf-8")


def _platform(pilot: str) -> str:
    return pilot.split("_", 1)[1]


def _inputs(pilot: str, **overrides: object) -> ProductReferenceInputs:
    """The sidecar of *pilot* as ``infra/fetch_product_reference.py`` writes it: the manifest, its path, the pin and
    the README, all read from the committed upstream files."""
    pin = _pin(pilot)
    readme = ROUND3 / pilot / "README.md"
    manifest_path = pin["manifest_path"]
    fields: dict[str, object] = {
        "platform": _platform(pilot),
        "manifest_text": _upstream(pilot, manifest_path) if manifest_path else None,
        "manifest_path": manifest_path,
        "source_repository": pin["repository"],
        "source_ref": pin["commit"],
        "readme_document": (
            DocumentPresent(
                path="README.md",
                sha="fixture",
                size=readme.stat().st_size,
                content=readme.read_text(encoding="utf-8"),
            )
            if readme.exists()
            else None
        ),
    }
    fields.update(overrides)
    return ProductReferenceInputs(**fields)  # type: ignore[arg-type]


def _registry_says_absent(pilot: str) -> ProductReferenceInputs:
    """The pilot's inputs with the registry answer recorded at ingestion (``package_registry_<pilot>.json``)."""
    recorded = json.loads((MANIFESTS / f"package_registry_{pilot}.json").read_text(encoding="utf-8"))
    assert recorded["verified"] is False, pilot
    return _inputs(
        pilot,
        install_verified=False,
        install_verified_coordinate=recorded["coordinate"],
        install_verified_checked_at=recorded["checked_at"],
    )


def _text(inputs: ProductReferenceInputs, section: str) -> str:
    result = get_product_reference(inputs, section)  # type: ignore[arg-type]
    assert isinstance(result, ReferenceContent), (section, result)
    return result.text


def _answer(inputs: ProductReferenceInputs, section: str) -> ReferenceContent:
    result = get_product_reference(inputs, section)  # type: ignore[arg-type]
    assert isinstance(result, ReferenceContent), (section, result)
    return result


# --- C++: URL, commit, subdirectory, target (RD3-080 to RD3-083) -----------------------------------------------


def test_cells_cpp_install_has_url_commit_subdirectory_target_and_nuget_package() -> None:
    pin = _pin("cells_cpp")
    cmake = _upstream("cells_cpp", "Aspose.Cells.Foss.Cpp/CMakeLists.txt")
    readme = _upstream("cells_cpp", "README.md")
    # The oracle is the upstream files: the library is defined in the subdirectory CMakeLists, not at the root
    # (the fixture has no root CMakeLists), under the target aspose_cells_foss, and the README documents the package.
    assert re.search(r"add_library\(\s*aspose_cells_foss\b", cmake)
    assert not (ROUND3 / "cells_cpp" / "CMakeLists.txt").exists()
    assert "Aspose.Cells.Cpp.FOSS" in readme

    text = _text(_inputs("cells_cpp"), "install")

    assert f"https://github.com/{pin['repository']}" in text
    assert pin["commit"] in text
    assert "SOURCE_SUBDIR Aspose.Cells.Foss.Cpp" in text
    assert "aspose_cells_foss" in text
    assert "Aspose.Cells.Cpp.FOSS" in text
    assert text.index(pin["commit"]) < text.index("The repository README's installation section")


def test_cells_cpp_subdirectory_comes_from_the_readme_when_the_sidecar_has_no_manifest_path() -> None:
    text = _text(_inputs("cells_cpp", manifest_path=None), "install")

    assert "SOURCE_SUBDIR Aspose.Cells.Foss.Cpp" in text


def test_slides_cpp_names_only_the_target_the_cmakelists_defines() -> None:
    cmake = _upstream("slides_cpp", "CMakeLists.txt")
    readme = _upstream("slides_cpp", "README.md")
    assert re.search(r"add_library\(\s*AsposeSlidesFoss\b", cmake)
    assert re.search(r"add_library\(\s*AsposeSlidesFoss::AsposeSlidesFoss\s+ALIAS", cmake)
    assert not re.search(r"add_library\(\s*aspose_slides_foss\b", cmake)
    # the upstream README really does link the target that does not exist
    assert re.search(r"target_link_libraries\([^)]*aspose_slides_foss\)", readme)

    result = _answer(_inputs("slides_cpp"), "install")

    assert "AsposeSlidesFoss::AsposeSlidesFoss" in result.text
    assert not re.search(r"target_link_libraries\([^)]*\baspose_slides_foss\b", result.text)
    assert not re.search(r"FetchContent_\w+\(\s*aspose_slides_foss\b", result.text)
    assert "`aspose_slides_foss` target" not in result.text
    assert (
        result.notice is not None
        and "aspose_slides_foss -> AsposeSlidesFoss::AsposeSlidesFoss" in result.notice
    )


def test_pdf_cpp_leads_with_the_find_package_route_and_drops_the_broken_subdirectory_route() -> None:
    cmake = _upstream("pdf_cpp", "CMakeLists.txt")
    readme = _upstream("pdf_cpp", "README.md")
    assert "${CMAKE_SOURCE_DIR}" in cmake  # why add_subdirectory and FetchContent fail upstream
    assert "add_subdirectory(aspose.pdf-foss-for-cpp)" in readme  # the route the README repeats
    pin = _pin("pdf_cpp")

    text = _text(_inputs("pdf_cpp"), "install")

    assert text.index("find_package(aspose_pdf_foss 1.0 CONFIG REQUIRED)") < text.index(
        "The repository README's installation section"
    )
    assert text.index("cmake --install") < text.index("find_package(")
    assert "add_subdirectory(" not in text
    assert "FetchContent_Declare" not in text
    assert "aspose_pdf_foss::aspose_pdf_foss" in text
    assert pin["commit"] in text.split("find_package(")[0]
    assert "Aspose.PDF.Cpp.FOSS" in text  # the NuGet package the README documents


def test_pdf_cpp_drift_notice_no_longer_recommends_fetchcontent_once_embedding_is_disclosed_broken() -> None:
    """PDF-CPP-004 (round-2, P1, mcp-content-wrong): the body already states (twice) that
    add_subdirectory/FetchContent do not work at this commit - the real
    ``_annotate_broken_embedding`` disclosure above already proved present in ``result.text`` by the
    sibling test above. Before G3/TC-719, the SAME response's ``notice`` (built independently by
    ``_drift_notice`` from the index-versus-package drift this pilot's real production sidecar
    recorded - ``infra/demo-manifests/product_reference_pdf_cpp.json``: indexed commit 2 commits
    ahead of released tag v1.0.1) then instructed the reader to use exactly that broken route via
    ``FetchContent_Declare``/``FetchContent_MakeAvailable``. This pins the real recorded drift
    values (this fixture's own ``PIN.json`` carries no drift field, so they are supplied here) and
    proves the contradiction is gone: the always-valid ``git clone``/``git checkout`` sentence and
    the broken-embedding disclosure both stay, the FetchContent sentence does not."""
    drift = DriftRecord(
        indexed_commit="4b83c9fec1e37fd205156770161f6843bac00ceb",
        tag="v1.0.1",
        tag_commit="f31c812c46a6568856681c0991fff786905c5c41",
        ahead_by=2,
        behind_by=0,
        status="ahead",
    )

    result = _answer(_inputs("pdf_cpp", drift=drift), "install")

    # the body's own broken-embedding disclosure is still present, unchanged by this card.
    assert "add_subdirectory and FetchContent do not work at this commit" in result.text
    assert "(Removed: add_subdirectory and FetchContent do not work at this commit" in result.text

    assert result.notice is not None
    # the git clone/checkout sentence is always valid (cloning works regardless of embedding) and
    # must stay.
    assert "git clone https://github.com/aspose-pdf-foss/Aspose.PDF-FOSS-for-Cpp" in result.notice
    assert "git checkout 4b83c9fec1e37fd205156770161f6843bac00ceb" in result.notice
    # PDF-CPP-004: the notice no longer recommends the exact route the body just said is broken.
    assert "FetchContent_Declare" not in result.notice
    assert "FetchContent_MakeAvailable" not in result.notice


def test_cells_cpp_drift_notice_still_recommends_fetchcontent_without_a_broken_embedding_disclosure() -> None:
    """Negative control for G3/TC-719's fix: cells/cpp's real CMakeLists.txt never refers to
    ``${CMAKE_SOURCE_DIR}`` (``parse_cmake_facts(...).embedding_broken is False`` -
    ``test_cmake_facts_of_the_real_files`` above), so its install recipe never carries
    ``_BROKEN_EMBEDDING_NOTE``. A pilot in this situation must keep recommending FetchContent in its
    drift notice exactly as before this card - the fix must not silence the sentence for every cpp
    pilot, only for the one that disclosed embedding is broken. cells/cpp's own production sidecar
    never recorded real drift (``infra/demo-manifests/product_reference_cells_cpp.json``), so this
    uses the narrowest synthetic ``DriftRecord`` needed to exercise the index-ahead branch, the same
    way ``tests/mcp/tools/test_compatibility_drift_notice.py`` already does for this exact helper."""
    drift = DriftRecord(indexed_commit=_pin("cells_cpp")["commit"], tag="v1.0.0", ahead_by=3, behind_by=0)

    result = _answer(_inputs("cells_cpp", drift=drift), "install")

    assert result.notice is not None
    assert "git clone" in result.notice and "git checkout" in result.notice
    assert "FetchContent_Declare" in result.notice
    assert "FetchContent_MakeAvailable" in result.notice


def test_email_cpp_has_a_cloneable_url_and_the_real_target() -> None:
    cmake = _upstream("email_cpp", "CMakeLists.txt")
    assert re.search(r"add_library\(\s*AsposeEmailFoss::AsposeEmailFoss\s+ALIAS\s+AsposeEmailFoss\)", cmake)
    pin = _pin("email_cpp")

    text = _text(_inputs("email_cpp"), "install")

    assert f"https://github.com/{pin['repository']}.git" in text
    assert pin["commit"] in text
    assert "AsposeEmailFoss::AsposeEmailFoss" in text
    assert "SOURCE_SUBDIR" not in text  # the project is at the repository root


def test_a_cpp_answer_with_no_pin_is_the_readme_recipe_as_before() -> None:
    inputs = _inputs("email_cpp", source_repository=None, source_ref=None)

    result = _answer(inputs, "install")

    assert result.text.startswith("Build it from source with CMake")
    assert "FetchContent_Declare" not in result.text


def test_a_cpp_answer_without_a_layout_source_stays_absent() -> None:
    # No manifest path and no README: where the CMake project sits in the repository is unknown, so no recipe.
    inputs = _inputs("cells_cpp", manifest_path=None, readme_document=None)

    assert isinstance(get_product_reference(inputs, "install"), NotAvailable)


# --- registry contradictions (RD3-084) ---------------------------------------------------------------------------


def test_imaging_net_install_never_quotes_the_command_for_the_package_the_registry_lacks() -> None:
    readme = _upstream("imaging_net", "README.md")
    recorded = json.loads((MANIFESTS / "package_registry_imaging_net.json").read_text(encoding="utf-8"))
    # the oracle: NuGet answered 404 for this package (recorded at ingestion) and the upstream README still says this
    assert recorded["verified"] is False
    assert "dotnet add package Aspose.Imaging.Foss" in readme

    text = _text(_registry_says_absent("imaging_net"), "install")

    assert "dotnet add package Aspose.Imaging.Foss" not in text
    assert not re.search(
        r"^\s*(?:dotnet add package|Install-Package)\s+Aspose\.Imaging\.Foss", text, re.MULTILINE
    )
    assert "was not found there" in text


def test_imaging_net_install_answer_removes_the_real_install_line_exactly_once(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """IMAGING-NET-006 (round-2, G3/TC-718): the real, committed production index chunk for
    `imaging/net`'s install section merges the README's `## Install` and `## Usage` headings into
    one "Installation and Quick Start" chunk (`infra/demo-manifests/imaging__net__self_extracted`,
    built by `infra/build_chunks.py` from the furnished page) - the real nuget install line
    (`dotnet add package Aspose.Imaging.Foss`) occurs in it exactly once, inside its own fence, and a
    second, unrelated fence (the Usage code sample) merely references the namespace in a bare
    `using Aspose.Imaging.Foss;` statement, never an install command. Before the fix, the removal-note
    placeholder was inserted TWICE for this real data: once for the genuine install line, and once
    for that unrelated `using` statement, which `_guard_unresolved_install`'s fence pass mistook for
    a second install line to redact merely because it also named the coordinate. Counting the note's
    occurrences (not just asserting a command is absent, which the existing cross-pilot test above
    already does and did not catch this) is the one assertion that was always missing."""
    root = tmp_path_factory.mktemp("tc718-imaging-net-corpus")
    corpus = load_demo_manifests_corpus(root)
    pilot = corpus.pilots["imaging__net"]
    readme = sections_from_documents({str(index): document for index, document in enumerate(pilot.documents)})
    assert readme.install is not None
    # the real committed chunk really does merge Install and Usage, and really does name the nuget
    # line exactly once - the premise this regression test depends on, not an assumption.
    assert readme.install.text.count("dotnet add package Aspose.Imaging.Foss") == 1
    assert readme.install.text.count("using Aspose.Imaging.Foss;") == 1

    inputs = _registry_says_absent("imaging_net")
    result = get_product_reference(inputs, "install", readme=readme)

    assert isinstance(result, ReferenceContent)
    assert result.text.count(product_reference._REMOVED_NOTE_PREFIX) == 1
    # the unrelated Usage statement is real content and must survive, unredacted.
    assert "using Aspose.Imaging.Foss;" in result.text


def test_the_guard_flag_is_what_removes_the_contradicting_readme_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = _registry_says_absent("imaging_net")
    assert product_reference.README_INSTALL_GUARD is True

    monkeypatch.setattr(product_reference, "README_INSTALL_GUARD", False)
    unguarded = _text(inputs, "install")

    assert "dotnet add package Aspose.Imaging.Foss" in unguarded


_ABSENT_INSTALL_LINES = [
    pytest.param("pypi", "aspose-demo-foss", "pip install aspose-demo-foss", id="pip"),
    pytest.param("npm", "@aspose/demo", "npm install @aspose/demo", id="npm"),
    pytest.param("nuget", "Aspose.Demo.FOSS", "dotnet add package Aspose.Demo.FOSS", id="nuget"),
    pytest.param("nuget", "Aspose.Demo.FOSS", "Install-Package Aspose.Demo.FOSS", id="nuget-powershell"),
    pytest.param("cargo", "aspose-demo", "cargo add aspose-demo", id="cargo"),
]


@pytest.mark.parametrize(("ecosystem", "coordinate", "line"), _ABSENT_INSTALL_LINES)
def test_a_readme_install_line_for_an_absent_package_is_removed_and_the_rest_is_kept(
    ecosystem: str, coordinate: str, line: str
) -> None:
    readme = (
        "## Installation\n\nBuild with the repository's own script first.\n\n"
        f"```bash\n{line}\nmake build\n```\n\nThen run `make test`.\n"
    )

    guarded = product_reference._guard_unresolved_install(readme, coordinate, ecosystem)

    assert line not in guarded
    assert f"{coordinate} was not found on the {ecosystem} registry" in guarded
    assert "make build" in guarded and "make test" in guarded
    assert guarded.count("```") == 2


def test_a_fence_that_holds_only_the_absent_install_command_is_replaced_whole() -> None:
    guarded = product_reference._guard_unresolved_install(
        "Install:\n\n```\ndotnet add package Aspose.Demo.FOSS\n```\n", "Aspose.Demo.FOSS", "nuget"
    )

    assert "```" not in guarded
    assert "dotnet add package" not in guarded
    assert product_reference._has_readme_content(guarded) is True  # the lead-in "Install:" is README text
    assert (
        product_reference._has_readme_content(
            "[README install line removed: x was not found on the nuget registry.]"
        )
        is False
    )


def test_a_line_that_installs_a_different_package_is_not_removed() -> None:
    text = "```bash\npip install aspose-demo-foss-extras\n```"

    assert product_reference._guard_unresolved_install(text, "aspose-demo-foss", "pypi") == text


def test_every_pilot_the_registry_lacks_quotes_no_install_line_for_it() -> None:
    checked = 0
    for pilot_dir in sorted(ROUND3.iterdir()):
        pilot = pilot_dir.name
        recorded_path = MANIFESTS / f"package_registry_{pilot}.json"
        if (
            not recorded_path.exists()
            or not (pilot_dir / "README.md").exists()
            or _pin(pilot)["manifest_path"] is None
        ):
            continue
        recorded = json.loads(recorded_path.read_text(encoding="utf-8"))
        if recorded["verified"] is not False:
            continue
        inputs = _inputs(
            pilot,
            install_verified=False,
            install_verified_coordinate=recorded["coordinate"],
            install_verified_checked_at=recorded["checked_at"],
        )
        assert install_coordinate_for_verification(inputs) == (
            recorded["ecosystem"],
            recorded["coordinate"],
        ), pilot
        result = get_product_reference(inputs, "install")
        assert isinstance(result, ReferenceContent), pilot
        # An install command is "<verb> <package>": pip/npm/cargo install|add|i, dotnet add package, Install-Package.
        command = re.compile(
            rf"(?:\binstall|\badd(?:\s+package)?|\bi)\s+[\"']?{re.escape(recorded['coordinate'])}(?![\w.-])",
            re.IGNORECASE,
        )
        offending = [line for line in result.text.split("\n") if command.search(line)]
        assert offending == [], (pilot, offending)
        checked += 1
    # re-pinned by TC-853: 3 -> 2. tex/python was re-pinned at the upstream release 26.11.1 (07db3203), whose package
    # `aspose-tex-foss` IS published on PyPI (package_registry_tex_python.json: verified true, served 26.11.1), so it left
    # the set of pilots whose recorded registry answer is "absent"; test_tex_python_install_names_the_published_package
    # below pins that. The two remaining pilots are still checked.
    assert checked >= 2


# --- bounds and build steps (RD3-085, RD3-086) -----------------------------------------------------------------------


def test_words_python_install_carries_the_python_bound() -> None:
    pyproject = _upstream("words_python", "pyproject.toml")
    assert re.search(r'requires-python\s*=\s*">=3\.10,\s*<3\.13"', pyproject)  # why pip fails on Python 3.13

    text = _text(_inputs("words_python"), "install")

    assert text.startswith("pip install aspose-words-foss")
    assert "<3.13" in text and ">=3.10" in text


def test_a_python_range_without_an_upper_bound_leaves_the_command_alone() -> None:
    inputs = ProductReferenceInputs(
        manifest_text='[project]\nname = "aspose-demo-foss"\nrequires-python = ">=3.9"\n', platform="python"
    )

    assert _text(inputs, "install") == "pip install aspose-demo-foss"


def test_a_setup_py_upper_bound_is_carried_too() -> None:
    inputs = ProductReferenceInputs(
        manifest_text="setup(name='aspose-demo-foss', python_requires='>=3.8,<3.12')\n", platform="python"
    )
    # setup.py has no pyproject name, so the command is absent; the helper that states the bound still reads it
    assert product_reference._python_requires(inputs.manifest_text or "") == ">=3.8,<3.12"


def test_pdf_typescript_install_has_the_build_steps_and_the_entry_point() -> None:
    package = json.loads(_upstream("pdf_typescript", "package.json"))
    assert package["main"] == "dist/index.js" and "build" in package["scripts"]
    assert "import { Document } from '@asposefoss/pdf';" in _upstream("pdf_typescript", "README.md")
    recorded = json.loads((MANIFESTS / "package_registry_pdf_typescript.json").read_text(encoding="utf-8"))
    assert recorded["verified"] is False  # not on npm

    text = _text(_registry_says_absent("pdf_typescript"), "install")

    assert "npm install" in text
    assert "npm run build" in text
    assert "dist/index.js" in text
    assert "import { Document } from '@asposefoss/pdf';" in text
    assert f"requires Node.js {package['engines']['node']}" in text  # the engine bound (>=22) is carried too


def test_the_registered_tool_reaches_the_new_install_logic(tmp_path: Path) -> None:
    """The production entrypoint: the tool registry the server builds answers install from the sidecar README."""
    from foss_mcp.indexing.generation_manifest import GenerationManifestStore
    from foss_mcp.mcp import server as server_module
    from foss_mcp.mcp.routing import Scope

    registry = server_module._build_tool_registry(
        GenerationManifestStore(tmp_path / "empty"),
        Scope(family="cells", platform="cpp"),
        _inputs("cells_cpp"),
        (),
    )

    install = registry["get_product_reference"](section="install")
    limitations = registry["get_product_reference"](section="limitations")

    assert isinstance(install, ReferenceContent) and "SOURCE_SUBDIR Aspose.Cells.Foss.Cpp" in install.text
    assert isinstance(limitations, ReferenceContent) and "Only `.xlsx` (OOXML)" in limitations.text


# --- Java coordinates (RD3-087) ----------------------------------------------------------------------------------


def _pom_fields(pilot: str) -> tuple[str, str, str]:
    root = ElementTree.fromstring(_upstream(pilot, "pom.xml"))
    namespace = {"m": root.tag.partition("}")[0].lstrip("{")}

    def field(name: str) -> str:
        found = root.find(f"m:{name}", namespace)
        return (found.text or "").strip() if found is not None else ""

    return field("groupId"), field("artifactId"), field("version")


@pytest.mark.parametrize("pilot", ["3d_java", "cells_java", "pdf_java", "slides_java"])
def test_java_install_carries_the_instruction_to_read_the_served_version(pilot: str) -> None:
    group, artifact, pom_version = _pom_fields(pilot)
    assert group == "org.aspose" and artifact == f"aspose-{pilot.split('_')[0]}-foss"

    text = _text(_inputs(pilot), "install")

    assert text.startswith(f"{group}:{artifact}\n")
    assert f"repo1.maven.org/maven2/org/aspose/{artifact}/maven-metadata.xml" in text
    assert f"declares {pom_version}" in text


@pytest.mark.parametrize("pilot", ["3d_java", "cells_java", "pdf_java", "slides_java"])
def test_java_install_names_the_served_version_when_the_registry_check_recorded_it(pilot: str) -> None:
    group, artifact, pom_version = _pom_fields(pilot)

    text = _text(_inputs(pilot, install_served_version="26.7.0"), "install")

    assert text.startswith(f"{group}:{artifact}:26.7.0\n")
    assert "Maven Central serves version 26.7.0" in text
    assert (f"declares {pom_version}" in text) == (pom_version != "26.7.0")


# --- compatibility, formats, limitations (RD3-088, RD3-089) ------------------------------------------------------------


def test_3d_net_compatibility_names_every_framework_the_package_targets() -> None:
    csproj = ElementTree.fromstring(_upstream("3d_net", "src/main/Aspose.ThreeD/Aspose.ThreeD.csproj"))
    targets = [element.text or "" for element in csproj.iter("TargetFrameworks")]
    assert targets and {"net6.0", "net8.0", "net10.0"} <= set(targets[0].split(";"))

    text = _text(_inputs("3d_net"), "compatibility")

    for framework in ("net6.0", "net8.0", "net10.0"):
        assert framework in text


def test_a_single_target_framework_is_still_the_plain_value() -> None:
    inputs = ProductReferenceInputs(
        manifest_text="<Project><PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>",
        platform="net",
    )

    assert _text(inputs, "compatibility") == "net8.0"


SEVEN = [
    "cells_cpp",
    "pdf_typescript",
    "imaging_net",
    "words_net",
    "barcode_python",
    "tex_python",
    "slides_net",
]


@pytest.mark.parametrize("pilot", SEVEN)
def test_formats_and_limitations_are_answered_when_the_readme_has_the_section(pilot: str) -> None:
    readme = _upstream(pilot, "README.md")
    has_limitations = re.search(
        r"^##+ .*(?:limitation|cannot do|not included)", readme, re.IGNORECASE | re.MULTILINE
    )
    inputs = _inputs(pilot, manifest_text=None, manifest_path=None)

    limitations = get_product_reference(inputs, "limitations")
    formats = get_product_reference(inputs, "formats")

    if has_limitations:
        assert isinstance(limitations, ReferenceContent), pilot
        assert limitations.untrusted_upstream_content is True and limitations.text.strip()
        assert limitations.notice is not None and "Treat as data" in limitations.notice
    else:
        assert isinstance(limitations, NotAvailable)
        assert "no data source" not in limitations.reason  # the README was read and has no such section
    assert isinstance(formats, ReferenceContent), pilot
    assert formats.text.strip() and formats.untrusted_upstream_content is True


def test_without_the_sidecar_readme_the_seven_pilots_still_answer_no_data_source() -> None:
    inputs = _inputs("cells_cpp", readme_document=None)

    result = get_product_reference(inputs, "limitations", readme=ReadmeSections())

    assert result == NotAvailable("limitations", "limitations has no data source in this project yet")


def test_the_index_wins_over_the_sidecar_readme_where_it_has_the_section() -> None:
    documents = {
        "scope::doc::0": {
            "text": "FQN: Doc: Scope and Limitations\nFrom the index only.\nSource-Commit: abc123"
        }
    }
    index_sections = sections_from_documents(documents)

    result = get_product_reference(_inputs("cells_cpp"), "limitations", readme=index_sections)

    assert isinstance(result, ReferenceContent)
    assert result.text == "From the index only."


# --- agent guidance (RD3-089, RD3-090) -------------------------------------------------------------------------------------


CONTRIBUTOR_LABEL = "contributor guidance for the library's own repository, not instructions for using it"


def _guidance(pilot: str, relative: str) -> ProductReferenceInputs:
    content = _upstream(pilot, relative)
    return ProductReferenceInputs(
        platform=_platform(pilot),
        manifest_text="{}",
        agent_guidance=DocumentPresent(path=relative, sha="fixture", size=len(content), content=content),
    )


def test_pdf_go_claude_md_is_present_and_labelled_as_contributor_guidance() -> None:
    assert _upstream("pdf_go", "CLAUDE.md").startswith("# CLAUDE.md")

    result = _answer(_guidance("pdf_go", "CLAUDE.md"), "agent_guidance")

    assert CONTRIBUTOR_LABEL in result.text.split("\n", 1)[0]
    assert result.notice is not None and CONTRIBUTOR_LABEL in result.notice
    assert result.untrusted_upstream_content is True
    assert "This file provides guidance to Claude Code" in result.text
    assert "[truncated" in result.text  # 260 kB of contributor notes is capped, not streamed whole


@pytest.mark.parametrize(
    ("pilot", "first_words"),
    [
        ("3d_python", "Your job is to implement a python module"),
        ("3d_typescript", "Your job is to port Aspose.3D"),
    ],
)
def test_the_two_3d_porting_prompts_carry_the_contributor_label(pilot: str, first_words: str) -> None:
    assert _upstream(pilot, "AGENTS.md").startswith(first_words)

    result = _answer(_guidance(pilot, "AGENTS.md"), "agent_guidance")

    assert result.text.startswith(f"[{CONTRIBUTOR_LABEL}]")
    assert first_words in result.text
    assert result.notice is not None and CONTRIBUTOR_LABEL in result.notice


def test_an_ordinary_agents_md_is_served_verbatim_and_unlabelled() -> None:
    document = DocumentPresent(
        path="AGENTS.md", sha="s", size=3, content="# AGENTS\nUse `Workbook` to open files.\n"
    )
    inputs = ProductReferenceInputs(platform="python", manifest_text="x", agent_guidance=document)

    result = _answer(inputs, "agent_guidance")

    assert result.text == document.content
    assert CONTRIBUTOR_LABEL not in (result.notice or "")


def _real_agent_guidance_document(pilot: str) -> DocumentPresent:
    """The real, committed ``agent_guidance`` document for *pilot*, read directly from the manifest
    sidecar ``infra/demo-manifests/product_reference_<pilot>.json`` (written by
    ``infra/fetch_product_reference.py``). This pilot has no AGENTS.md/agents.md checked into
    ``tests/fixtures/round3_readmes/``, so the committed manifest itself is the real, unmodified
    source of truth (G3/TC-714, CELLS-NET-005 + sibling cells/typescript)."""
    manifest = json.loads((MANIFESTS / f"product_reference_{pilot}.json").read_text(encoding="utf-8"))
    guidance = manifest["agent_guidance"]
    assert guidance["status"] == "present", pilot
    return DocumentPresent(
        path=guidance["path"], sha=guidance["sha"], size=guidance["size"], content=guidance["content"]
    )


def _guidance_from_manifest(pilot: str) -> ProductReferenceInputs:
    return ProductReferenceInputs(
        platform=_platform(pilot),
        manifest_text="{}",
        agent_guidance=_real_agent_guidance_document(pilot),
    )


@pytest.mark.parametrize("pilot", ["cells_net", "cells_typescript"])
def test_cells_net_and_cells_typescript_real_agents_md_now_carry_the_contributor_label(pilot: str) -> None:
    """G3/TC-714 (CELLS-NET-005 + sibling): both real documents open with a bare '# AGENTS.md' first
    line and the contributor-identifying sentence ("this file defines how agents should ...") a few
    lines later, inside a '## Purpose' paragraph - exactly the shape ``_CONTRIBUTOR_FIRST_LINE_RE``
    (first-line-only) could never catch, and exactly what the new ``_CONTRIBUTOR_PURPOSE_RE`` does."""
    document = _real_agent_guidance_document(pilot)
    assert document.content.splitlines()[0].strip() == "# AGENTS.md"
    assert "this file defines how agents should" in document.content.lower()

    result = _answer(_guidance_from_manifest(pilot), "agent_guidance")

    assert result.text.startswith(f"[{CONTRIBUTOR_LABEL}]")
    assert "this file defines how agents should" in result.text.lower()
    assert result.notice is not None and CONTRIBUTOR_LABEL in result.notice
    assert result.untrusted_upstream_content is True


@pytest.mark.parametrize("pilot", ["3d_net", "cells_cpp", "pdf_java"])
def test_other_pilots_with_a_bare_or_generic_agents_md_still_do_not_carry_the_contributor_label(
    pilot: str,
) -> None:
    """Regression guard for the IMPORTANT GOTCHA in TC-714: 3d/net is a deliberate exception (a
    consumer-facing implementation guide), and cells/cpp and pdf/java also happen to share a bare
    '# AGENTS.md' first line with cells/net and cells/typescript but are genuinely consumer-facing.
    None of the three contains the new regex's real, specific phrase, so none must be reclassified."""
    document = _real_agent_guidance_document(pilot)
    assert "this file defines how agents should" not in document.content.lower()
    # G3/TC-683 may strip a prompt-injection-shaped imperative line (the real 3d/net case) before
    # this comparison - that stripping is pre-existing, unrelated behavior this card must not touch.
    expected_text = product_reference._strip_agent_imperative_lines(document.content)

    result = _answer(_guidance_from_manifest(pilot), "agent_guidance")

    assert result.text == expected_text
    assert CONTRIBUTOR_LABEL not in (result.notice or "")
    assert not result.text.startswith(f"[{CONTRIBUTOR_LABEL}]")


def test_a_missing_guidance_file_is_still_an_explicit_absence() -> None:
    inputs = ProductReferenceInputs(
        platform="python", manifest_text="x", agent_guidance=DocumentNotPresent("AGENTS.md")
    )

    assert get_product_reference(inputs, "agent_guidance") == NotAvailable(
        "agent_guidance", "agent_guidance is not present in this repository"
    )


# --- the library that cannot be imported (RD3-092) ---------------------------------------------------------------------------------


IMPORT_WARNING = (
    "35 of 45 source files do not parse as written; the API was recovered by a textual repair of the lost "
    "indentation; the library cannot be imported at this commit."
)


def _tex_index() -> ReadmeSections:
    documents = {
        "scope::doc::0": {
            "text": (
                "FQN: Doc: Extraction notice\n"
                f"{IMPORT_WARNING}\n11 of 45 source files could not be read even after the repair "
                "(examples: a.py).\nSource-Commit: 181015d2eee3"
            )
        }
    }
    return sections_from_documents(documents)


def test_the_extraction_notice_statement_is_read_from_the_index() -> None:
    assert _tex_index().import_warning == IMPORT_WARNING


def test_tex_install_leads_with_the_cannot_be_imported_statement_and_the_real_count() -> None:
    """The mechanism (RD3-092): an index that holds the 181015d2 Extraction notice puts the statement first in the
    install answer. TC-853: tex/python no longer HAS that notice at 26.11.1, so the notice text below is the historical
    one and the inputs are the real, current ones (the registry answers: published, coordinate aspose-tex-foss)."""
    inputs = _inputs("tex_python")

    result = get_product_reference(inputs, "install", readme=_tex_index())

    assert isinstance(result, ReferenceContent)
    assert result.text.startswith(f"WARNING: {IMPORT_WARNING}")
    assert "cannot be imported at this commit" in result.text
    assert "35 of 45" in result.text and "11 of 45" not in result.text


def test_tex_python_install_names_the_published_package_and_warns_of_nothing() -> None:
    """TC-853 (RD2-258): at 07db3203 (release 26.11.1) the package is `aspose-tex-foss`, which PyPI serves, and no
    extraction notice exists in the index, so the install answer is the plain command with no import warning."""
    pin = _pin("tex_python")
    pyproject = _upstream("tex_python", "pyproject.toml")
    recorded = json.loads((MANIFESTS / "package_registry_tex_python.json").read_text(encoding="utf-8"))
    assert pin["commit"] == "07db320334a0a58e11107b6ca8092b2e5047c1f8"
    assert 'name = "aspose-tex-foss"' in pyproject and 'version = "26.11.1"' in pyproject
    assert (
        recorded["ecosystem"],
        recorded["coordinate"],
        recorded["verified"],
        recorded["served_version"],
    ) == (
        "pypi",
        "aspose-tex-foss",
        True,
        "26.11.1",
    )
    assert recorded["source_ref"] == pin["commit"]

    result = get_product_reference(_inputs("tex_python"), "install", readme=ReadmeSections())

    assert isinstance(result, ReferenceContent)
    assert not result.text.startswith("WARNING")
    assert result.text.startswith("pip install aspose-tex-foss")
    assert "aspose-tex " not in result.text.replace("aspose-tex-foss", "")


def test_an_install_answer_without_the_statement_is_not_prefixed() -> None:
    result = get_product_reference(_inputs("words_python"), "install", readme=ReadmeSections())

    assert isinstance(result, ReferenceContent)
    assert not result.text.startswith("WARNING")


def test_every_section_of_every_committed_pilot_is_a_well_formed_answer() -> None:
    pilots = sorted(path.name for path in ROUND3.iterdir() if path.is_dir())
    assert len(pilots) >= 19
    for pilot in pilots:
        guidance = {}
        for name in ("CLAUDE.md", "AGENTS.md"):
            if (ROUND3 / pilot / name).exists():
                content = _upstream(pilot, name)
                guidance["agent_guidance"] = DocumentPresent(name, "fixture", len(content), content)
                break
        inputs = _inputs(pilot, **guidance)
        for section in product_reference.SECTIONS:
            result = get_product_reference(inputs, section)
            assert result.section == section, (pilot, section)
            if isinstance(result, ReferenceContent):
                assert result.text.strip(), (pilot, section)
                if result.untrusted_upstream_content:
                    assert result.notice, (pilot, section)
            else:
                assert result.reason.strip(), (pilot, section)


# --- CMake facts (the parser the install rule stands on) ----------------------------------------------------------------------------------


def test_cmake_facts_of_the_real_files() -> None:
    cells = parse_cmake_facts(_upstream("cells_cpp", "Aspose.Cells.Foss.Cpp/CMakeLists.txt"))
    pdf = parse_cmake_facts(_upstream("pdf_cpp", "CMakeLists.txt"))
    slides = parse_cmake_facts(_upstream("slides_cpp", "CMakeLists.txt"))

    assert cells.libraries == ("aspose_cells_foss",) and cells.link_target == "aspose_cells_foss"
    assert cells.embedding_broken is False
    assert pdf.libraries == ("aspose_pdf_foss",) and pdf.link_target == "aspose_pdf_foss::aspose_pdf_foss"
    assert pdf.embedding_broken is True and pdf.version == "1.0.1"
    assert slides.link_target == "AsposeSlidesFoss::AsposeSlidesFoss"
    assert slides.embedding_broken is False  # `CMAKE_SOURCE_DIR STREQUAL ...` is a comparison, not a path


def test_cmake_facts_ignore_comments_and_imported_libraries() -> None:
    facts = parse_cmake_facts(
        "project(demo VERSION 2.1)\n# add_library(commented STATIC a.cpp)\n"
        "add_library(real STATIC a.cpp)\nadd_library(ext SHARED IMPORTED)\nadd_library(demo::real ALIAS real)\n"
    )

    assert facts.libraries == ("real",)
    assert facts.aliases == (("demo::real", "real"),)
    assert facts.version == "2.1" and facts.link_target == "demo::real"


def test_a_target_the_cmakelists_does_not_define_is_corrected_only_when_it_differs_in_spelling() -> None:
    facts = parse_cmake_facts("add_library(DemoLib STATIC a.cpp)\nadd_library(Demo::DemoLib ALIAS DemoLib)\n")
    text = (
        "FetchContent_Declare(demo_lib)\ntarget_link_libraries(app PRIVATE demo_lib other::thing ${EXTRA})\n"
    )

    fixed, corrections = product_reference._correct_cmake_targets(text, facts)

    assert corrections == {"demo_lib": "Demo::DemoLib"}
    assert "target_link_libraries(app PRIVATE Demo::DemoLib other::thing ${EXTRA})" in fixed
    assert "other::thing" in fixed  # an imported target from some other package is left alone


# --- through the registered tool: the sidecar path ---------------------------------------------------------------------------------------------


def test_the_sidecar_round_trip_carries_the_readme_and_the_manifest_path(tmp_path: Path) -> None:
    import sys

    sys.path.insert(0, str(REPO_ROOT / "infra"))
    from fetch_product_reference import atomic_write_json, load_manifest_sidecar

    sidecar = {
        "manifest_text": _upstream("cells_cpp", "Aspose.Cells.Foss.Cpp/CMakeLists.txt"),
        "platform": "cpp",
        "contributing": {"status": "not_present", "path": "CONTRIBUTING.md"},
        "agent_guidance": {"status": "not_present", "path": "AGENTS.md"},
        "source_repository": _pin("cells_cpp")["repository"],
        "source_ref": _pin("cells_cpp")["commit"],
        "readme_document": {
            "status": "present",
            "path": "README.md",
            "sha": "s",
            "size": 1,
            "content": _upstream("cells_cpp", "README.md"),
        },
        "manifest_path": "Aspose.Cells.Foss.Cpp/CMakeLists.txt",
        "install_served_version": "26.4.1",
    }
    path = tmp_path / "product_reference_cells_cpp.json"
    atomic_write_json(path, sidecar)

    inputs = load_manifest_sidecar(path)

    assert inputs is not None
    assert isinstance(inputs.readme_document, DocumentPresent)
    assert inputs.manifest_path == "Aspose.Cells.Foss.Cpp/CMakeLists.txt"
    assert inputs.install_served_version == "26.4.1"
    assert "SOURCE_SUBDIR Aspose.Cells.Foss.Cpp" in _text(inputs, "install")
