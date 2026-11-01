"""G3/TC-652 (REQ-G3-013): closes CELLS-PY-001.

``get_product_reference(section="compatibility")`` stated a bare manifest-derived minimum (e.g.
cells/python's ``>=3.7``) with no caveat, even when the real, currently registry-published package
requires something materially newer (26.10.0 requires ``>=3.10``). This mirrors the "install" section's
own cpp branch (``_drift_notice(inputs, "cmake")``): the compatibility branch's single
``_join_notice(...)`` call now takes the same, already-measured index-versus-package drift notice as a
third argument. Strictly additive: the ``value`` text (the manifest's own stated minimum) never changes;
only ``notice`` gains a sentence when a real drift record applies.

Offline, deterministic. Every expected drift sentence below is produced by calling ``_drift_notice``
itself (never a hardcoded copy of its wording), so a future wording change there cannot make this file
lie about what it is testing.
"""

from __future__ import annotations

from foss_mcp.extraction.github_release_reader import DriftRecord
from foss_mcp.mcp.tools.get_product_reference import (
    ProductReferenceInputs,
    ReferenceContent,
    _drift_notice,
    get_product_reference,
)
from foss_mcp.mcp.tools.readme_sections import ReadmeSection, ReadmeSections

COMMIT = "0123456789abcdef0123456789abcdef01234567"
REPOSITORY = "aspose-cells-foss/Aspose.Cells-FOSS-for-Python"

PYTHON_MANIFEST = '[project]\nname = "demo"\nrequires-python = ">=3.7"\n'
JAVA_MANIFEST = (
    "<project><groupId>com.demo</groupId><artifactId>demo</artifactId>"
    "<properties><maven.compiler.release>11</maven.compiler.release></properties></project>"
)
DOTNET_MANIFEST = (
    "<Project><PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>"
)

_EMPTY_README = ReadmeSections(limitations=None, install=None, capabilities=None)


def _disagreeing_python_readme() -> ReadmeSections:
    # A README install recipe that disagrees with the manifest's own >=3.7, so the existing
    # disagreement notice (G3/TC-518) has real content to prove untouched by this card.
    section = ReadmeSection(
        title="Installation", text="Requires Python 3.9.", commit=COMMIT, ordered=True, parts=1
    )
    return ReadmeSections(limitations=None, install=section, capabilities=None)


def _inputs(manifest_text: str, platform: str, *, drift: DriftRecord | None) -> ProductReferenceInputs:
    return ProductReferenceInputs(
        manifest_text=manifest_text,
        platform=platform,
        source_repository=REPOSITORY,
        source_ref=COMMIT,
        drift=drift,
    )


def _compatibility(inputs: ProductReferenceInputs, readme: ReadmeSections | None) -> ReferenceContent:
    result = get_product_reference(inputs, "compatibility", readme=readme)
    assert isinstance(result, ReferenceContent), result
    return result


def test_index_ahead_drift_appends_the_real_sentence_to_the_python_notice() -> None:
    record = DriftRecord(indexed_commit=COMMIT, tag="v26.10.0", ahead_by=3, behind_by=0)
    inputs = _inputs(PYTHON_MANIFEST, "python", drift=record)
    expected = _drift_notice(inputs, "pypi")
    assert expected is not None and "ahead" in expected

    result = _compatibility(inputs, _EMPTY_README)

    assert result.text == ">=3.7"
    assert result.notice is not None
    assert expected in result.notice


def test_package_ahead_drift_appends_the_real_sentence_to_the_python_notice() -> None:
    record = DriftRecord(indexed_commit=COMMIT, tag="v26.10.0", ahead_by=0, behind_by=5)
    inputs = _inputs(PYTHON_MANIFEST, "python", drift=record)
    expected = _drift_notice(inputs, "pypi")
    assert expected is not None and "ahead" in expected

    result = _compatibility(inputs, _EMPTY_README)

    assert result.text == ">=3.7"
    assert result.notice is not None
    assert expected in result.notice


def test_no_drift_record_leaves_the_compatibility_notice_unchanged() -> None:
    inputs = _inputs(PYTHON_MANIFEST, "python", drift=None)
    assert _drift_notice(inputs, "pypi") is None

    result = _compatibility(inputs, _disagreeing_python_readme())

    assert result.text == ">=3.7"
    assert result.notice is not None
    assert "Sources disagree" in result.notice
    assert "ahead" not in result.notice
    assert "commit" not in result.notice


def test_an_in_sync_drift_record_leaves_the_compatibility_notice_unchanged() -> None:
    record = DriftRecord(indexed_commit=COMMIT, tag="v26.10.0", ahead_by=0, behind_by=0)
    inputs = _inputs(PYTHON_MANIFEST, "python", drift=record)
    assert _drift_notice(inputs, "pypi") is None  # in sync: nothing to disclose

    result = _compatibility(inputs, _disagreeing_python_readme())

    assert result.text == ">=3.7"
    assert result.notice is not None
    assert "Sources disagree" in result.notice
    assert "ahead" not in result.notice


def test_diverged_drift_also_appends_a_real_sentence_to_the_python_notice() -> None:
    record = DriftRecord(indexed_commit=COMMIT, tag="v26.10.0", ahead_by=2, behind_by=1)
    inputs = _inputs(PYTHON_MANIFEST, "python", drift=record)
    expected = _drift_notice(inputs, "pypi")
    assert expected is not None and "diverged" in expected

    result = _compatibility(inputs, _EMPTY_README)

    assert result.text == ">=3.7"
    assert result.notice is not None
    assert expected in result.notice


def test_the_drift_notice_is_added_for_java_too() -> None:
    record = DriftRecord(indexed_commit=COMMIT, tag="v26.10.0", ahead_by=4, behind_by=0)
    inputs = _inputs(JAVA_MANIFEST, "java", drift=record)
    expected = _drift_notice(inputs, "maven")
    assert expected is not None

    result = _compatibility(inputs, _EMPTY_README)

    assert result.text == "Java 11+"
    assert result.notice is not None
    assert expected in result.notice


def test_the_drift_notice_is_added_for_dotnet_too() -> None:
    record = DriftRecord(indexed_commit=COMMIT, tag="v26.10.0", ahead_by=0, behind_by=6)
    inputs = _inputs(DOTNET_MANIFEST, "net", drift=record)
    expected = _drift_notice(inputs, "nuget")
    assert expected is not None

    result = _compatibility(inputs, _EMPTY_README)

    assert result.text == "net8.0"
    assert result.notice is not None
    assert expected in result.notice


def test_the_value_text_is_byte_identical_with_and_without_a_drift_record() -> None:
    with_drift = _compatibility(
        _inputs(
            PYTHON_MANIFEST,
            "python",
            drift=DriftRecord(indexed_commit=COMMIT, tag="v26.10.0", ahead_by=3, behind_by=0),
        ),
        _EMPTY_README,
    )
    without_drift = _compatibility(_inputs(PYTHON_MANIFEST, "python", drift=None), _EMPTY_README)

    assert with_drift.text == without_drift.text == ">=3.7"
    assert with_drift.notice != without_drift.notice
