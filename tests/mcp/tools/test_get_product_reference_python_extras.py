"""G3/TC-681 (C6, narrowed): surface real Python manifest extras (``[pdf]``) in install guidance.

REPRODUCE-FIRST (confirmed this card, against the real, committed
``infra/demo-manifests/product_reference_note_python.json`` manifest text): before this change,
``read_python_manifest`` had no ``extras`` field at all, and ``_python_install_text`` always emitted
the bare ``pip install <name>`` (plus an optional upper-bound-version caveat) with zero awareness
that note/python's real manifest states ``pdf = ["reportlab>=3.6"]`` under
``[project.optional-dependencies]`` - a genuine, real, user-facing PDF-export feature a user asking
this tool how to install note/python was never told about.

MEASURED against the real 32-pilot corpus's 11 real Python pilots (``infra/demo-manifests/
product_reference_*_python.json``), the real ``extras`` dicts ``read_python_manifest`` now returns:

    3d:     {"dev": ("pytest>=7.0.0",)}
    barcode: {}                                                    (only PEP 735 [dependency-groups];
                                                                     not [project.optional-dependencies]
                                                                     or extras_require - correctly not
                                                                     parsed as a pip extra at all)
    cells:  {"dev": ("pytest>=7.0.0", "pytest-cov>=4.0.0")}
    email:  {}
    html:   {"js": ("quickjs>=1.19,<2",), "test": ("pytest>=8",)}
    note:   {"pdf": ("reportlab>=3.6",),
             "test-pdf": ("pypdf>=5.3", "Pillow>=10.0", "PyMuPDF>=1.25"),
             "dev": ("build>=1.2", "twine>=5.0")}
    page:   {"test": ("pypdf", "pypdfium2", "Pillow")}
    pdf:    {"images": ("Pillow>=12.3",), "woff2": ("Brotli>=1.2",),
             "text-layout": ("uharfbuzz>=0.37", "python-bidi>=0.6", "fonttools>=4.60.2"),
             "fuzz": ("atheris>=2.3",), "dev": (...)}
    slides: {"test": ("pytest>=7", "python-pptx>=1.0")}
    tex:    {"dev": (...)}
    words:  {"dev": ("Pillow>=10.0.0", "pytest>=9.0.2")}

DECISION (dev/test-noise exclusion list, Decision-F discipline): measured against that real corpus,
the noise groups actually present are ``dev`` (8 of 11 pilots), ``test`` (3 pilots: html, page,
slides), and ``test-pdf`` (note) - a group that is ITSELF a test-dependency bundle for testing the
real ``pdf`` feature, not the feature. ``pdf/python`` additionally has ``fuzz`` (the ``atheris``
fuzzing-only group) - a dev/QA tool a real user never installs as a "feature". The chosen exclusion
rule is therefore: the exact names ``dev``/``test``/``tests`` (the project's own ``excluded_dirs``
precedent, with ``tests`` added for the plural form never observed but anticipated), the exact name
``fuzz``, and any group starting with ``test-``/``test_`` (catches ``test-pdf`` and any future
sibling without naming every one individually). Applying this rule to the measured corpus above
leaves exactly: note -> {pdf}, html -> {js}, pdf -> {images, woff2, text-layout}; every other pilot
is left with zero feature extras, so its install answer is untouched.
"""

from __future__ import annotations

from foss_mcp.extraction.manifest_reader import read_python_manifest
from foss_mcp.mcp.tools import get_product_reference as product_reference
from foss_mcp.mcp.tools.get_product_reference import (
    NotAvailable,
    ProductReferenceInputs,
    ReferenceContent,
    get_product_reference,
)

# --- (a) REPRODUCE-FIRST: the real note/python "pdf" extra now appears, with the correct syntax. ---

NOTE_PYTHON_PYPROJECT_TEXT = """
[project]
name = "aspose-note-foss"
version = "26.9.0"
requires-python = ">=3.10"

[project.optional-dependencies]
pdf = ["reportlab>=3.6"]
test-pdf = ["pypdf>=5.3", "Pillow>=10.0", "PyMuPDF>=1.25"]
dev = ["build>=1.2", "twine>=5.0"]
"""


def test_reproduce_first_note_python_pdf_extra_now_appears_with_correct_syntax() -> None:
    inputs = ProductReferenceInputs(manifest_text=NOTE_PYTHON_PYPROJECT_TEXT, platform="python")

    result = get_product_reference(inputs, "install")

    assert isinstance(result, ReferenceContent)
    assert result.text == (
        "pip install aspose-note-foss\n"
        "The pdf feature needs its own extra: pip install aspose-note-foss[pdf]."
    )
    # The dev/test-noise groups from the SAME manifest must never appear.
    assert "test-pdf" not in result.text
    assert "[dev]" not in result.text


def test_read_python_manifest_extracts_all_three_note_python_groups_verbatim() -> None:
    # The reader itself (before this card's filtering) returns every real group, unfiltered -
    # filtering which groups are worth surfacing is _python_feature_extras's job, not the reader's.
    manifest = read_python_manifest(NOTE_PYTHON_PYPROJECT_TEXT)

    assert manifest.extras == {
        "pdf": ("reportlab>=3.6",),
        "test-pdf": ("pypdf>=5.3", "Pillow>=10.0", "PyMuPDF>=1.25"),
        "dev": ("build>=1.2", "twine>=5.0"),
    }


# --- (b) a dev/test-only-extras manifest (real cells/python shape) is byte-for-byte unaffected. ----

CELLS_PYTHON_PYPROJECT_TEXT = """
[project]
name = "aspose-cells-foss"
version = "26.7.0"
requires-python = ">=3.7"

[project.optional-dependencies]
dev = ["pytest>=7.0.0", "pytest-cov>=4.0.0"]
"""

# This is the literal, pre-TC-681 output of the unmodified _python_install_text on this exact
# input: before this card, PythonManifest had no extras field at all, so the function's only two
# possible lines were "pip install <name>" and, when requires-python has an upper bound, the
# "Requires Python ..." caveat. cells/python's requires-python (">=3.7") has no upper bound, so the
# pre-change output was exactly this one line - confirmed by inspection of the pre-change function
# (git show HEAD:src/foss_mcp/mcp/tools/get_product_reference.py, same logic, no extras input at all).
CELLS_PYTHON_SNAPSHOT_TEXT = "pip install aspose-cells-foss"


def test_dev_only_extras_manifest_is_byte_for_byte_unaffected() -> None:
    inputs = ProductReferenceInputs(manifest_text=CELLS_PYTHON_PYPROJECT_TEXT, platform="python")

    result = get_product_reference(inputs, "install")

    assert isinstance(result, ReferenceContent)
    assert result.text == CELLS_PYTHON_SNAPSHOT_TEXT


# --- (c) a manifest with multiple real feature extras (pdf/python's images/woff2/text-layout) lists
# all of them, none dropped - and its own dev/fuzz noise groups are excluded. ------------------------

PDF_PYTHON_PYPROJECT_TEXT = """
[project]
name = "aspose-pdf-foss-for-python"
requires-python = ">=3.11"

[project.optional-dependencies]
images = ["Pillow>=12.3"]
woff2 = ["Brotli>=1.2"]
text-layout = ["uharfbuzz>=0.37", "python-bidi>=0.6", "fonttools>=4.60.2"]
fuzz = ["atheris>=2.3"]
dev = ["build>=1.2", "pytest>=8", "ruff>=0.6"]
"""


def test_multiple_real_feature_extras_are_all_listed_none_dropped() -> None:
    inputs = ProductReferenceInputs(manifest_text=PDF_PYTHON_PYPROJECT_TEXT, platform="python")

    result = get_product_reference(inputs, "install")

    assert isinstance(result, ReferenceContent)
    lines = result.text.splitlines()
    assert lines[0] == "pip install aspose-pdf-foss-for-python"
    feature_lines = set(lines[1:])
    assert feature_lines == {
        "The images feature needs its own extra: pip install aspose-pdf-foss-for-python[images].",
        "The woff2 feature needs its own extra: pip install aspose-pdf-foss-for-python[woff2].",
        "The text-layout feature needs its own extra: pip install aspose-pdf-foss-for-python[text-layout].",
    }
    # dev and fuzz are noise - never surfaced.
    assert "[dev]" not in result.text
    assert "[fuzz]" not in result.text


# --- (d) a manifest with no optional-dependencies/extras_require section at all (most real pilots,
# e.g. email/python's real shape) is unaffected and does not crash. ----------------------------------

EMAIL_PYTHON_PYPROJECT_TEXT = """
[project]
name = "aspose-email-foss"
version = "26.3"
requires-python = ">=3.10"
"""


def test_manifest_with_no_extras_section_at_all_is_unaffected_and_does_not_crash() -> None:
    inputs = ProductReferenceInputs(manifest_text=EMAIL_PYTHON_PYPROJECT_TEXT, platform="python")

    result = get_product_reference(inputs, "install")

    assert isinstance(result, ReferenceContent)
    assert result.text == "pip install aspose-email-foss"


def test_empty_manifest_text_does_not_crash() -> None:
    # A platform="python" install query with no manifest at all - the existing NotAvailable path
    # (no name) must still be reached without _python_feature_extras ever raising.
    inputs = ProductReferenceInputs(manifest_text="", platform="python")

    result = get_product_reference(inputs, "install")

    assert isinstance(result, NotAvailable)


# --- (e) the setup.py-style extras_require={...} shape (3d/python's real form) is also correctly
# parsed, not just the TOML [project.optional-dependencies] form. ------------------------------------

THREE_D_PYTHON_SETUP_PY_TEXT = """
from setuptools import setup, find_packages

setup(
    name="aspose-3d-foss",
    version="26.1.0",
    packages=find_packages(exclude=["tests*", "pyi*"]),
    python_requires=">=3.7",
    install_requires=[

    ],
    extras_require={
        "dev": [
            "pytest>=7.0.0",
        ],
    },
)
"""


def test_setup_py_extras_require_shape_is_parsed_by_the_reader() -> None:
    manifest = read_python_manifest(THREE_D_PYTHON_SETUP_PY_TEXT)

    assert manifest.name == "aspose-3d-foss"
    assert manifest.extras == {"dev": ("pytest>=7.0.0",)}


def test_setup_py_shape_with_only_dev_noise_is_byte_for_byte_unaffected() -> None:
    # 3d/python's real extras_require has only "dev" - confirmed parsed above, and confirmed
    # excluded as noise, so the install answer is identical to the pre-change behaviour.
    inputs = ProductReferenceInputs(manifest_text=THREE_D_PYTHON_SETUP_PY_TEXT, platform="python")

    result = get_product_reference(inputs, "install")

    assert isinstance(result, ReferenceContent)
    assert result.text == "pip install aspose-3d-foss"


SETUP_PY_WITH_FEATURE_EXTRA_TEXT = """
from setuptools import setup

setup(
    name="example-pkg",
    version="1.0.0",
    python_requires=">=3.8",
    extras_require={
        "yaml": [
            "PyYAML>=6.0",
        ],
        "dev": [
            "pytest>=7.0",
        ],
    },
)
"""


def test_setup_py_shape_with_a_real_feature_extra_surfaces_it() -> None:
    # Same extras_require={...} syntax as 3d/python's real form, but with a real feature group
    # ("yaml") alongside the dev noise - proves the setup.py parser path is not merely detecting
    # the block but correctly extracting EACH group, and the install-text filtering applies to it
    # exactly as it does to the TOML form.
    inputs = ProductReferenceInputs(manifest_text=SETUP_PY_WITH_FEATURE_EXTRA_TEXT, platform="python")

    result = get_product_reference(inputs, "install")

    assert isinstance(result, ReferenceContent)
    assert result.text == (
        "pip install example-pkg\nThe yaml feature needs its own extra: pip install example-pkg[yaml]."
    )
    assert "[dev]" not in result.text


# --- direct unit coverage of _python_feature_extras's exclusion rule. -------------------------------


def test_python_feature_extras_excludes_dev_test_tests_fuzz_and_test_prefixed_groups() -> None:
    manifest_text = """
[project]
name = "example"

[project.optional-dependencies]
dev = ["a"]
test = ["b"]
tests = ["c"]
fuzz = ["d"]
test-pdf = ["e"]
test_pdf = ["f"]
real-feature = ["g"]
"""

    assert product_reference._python_feature_extras(manifest_text) == {"real-feature": ("g",)}
