"""G3/TC-471: the ``compatibility`` answer for C++ names the language standard, and for Python it
also reads a setup.py ``python_requires`` keyword.

Offline, deterministic, committed files only. The real-sidecar cases derive the pilots from
infra/helm/foss-mcp/values.yaml and read the committed infra/demo-manifests/product_reference_*.json
sidecars as plain JSON; nothing is retyped and nothing is fetched.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from foss_mcp.extraction.manifest_reader import read_python_manifest
from foss_mcp.mcp.tools.get_product_reference import (
    NotAvailable,
    ProductReferenceInputs,
    ReferenceContent,
    get_product_reference,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"
DEMO_MANIFESTS = REPO_ROOT / "infra" / "demo-manifests"

CPP_NOT_AVAILABLE_REASON = "manifest does not state a C++ standard"
PYTHON_NOT_AVAILABLE_REASON = "manifest does not state requires-python"


def _compatibility(manifest_text: str, platform: str) -> ReferenceContent | NotAvailable:
    return get_product_reference(
        ProductReferenceInputs(manifest_text=manifest_text, platform=platform), "compatibility"
    )


def _pilots() -> list[tuple[str, str]]:
    data: dict[str, Any] = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
    return [(p["family"], p["platform"]) for p in data["ingestion"]["pilots"]]


def _pilots_on(platform: str) -> list[tuple[str, str]]:
    return [pilot for pilot in _pilots() if pilot[1] == platform]


def _sidecar_manifest_text(family: str, platform: str) -> str:
    sidecar = DEMO_MANIFESTS / f"product_reference_{family}_{platform}.json"
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    assert data["platform"] == platform, (family, platform, data["platform"])
    text = data["manifest_text"]
    assert isinstance(text, str) and text, (family, platform)
    return text


def _real_compatibility(family: str, platform: str) -> ReferenceContent | NotAvailable:
    return _compatibility(_sidecar_manifest_text(family, platform), platform)


def _cmake_with_standard(standard: int) -> str:
    return (
        "cmake_minimum_required(VERSION 3.20)\n"
        "project(Demo VERSION 1.0.0)\n"
        f"set(CMAKE_CXX_STANDARD {standard})\n"
    )


# ---------------------------------------------------------------------------------------------------
# Synthetic manifests
# ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("standard", [11, 14, 17, 20, 23])
def test_cpp_standard_is_named_as_cpp(standard: int) -> None:
    result = _compatibility(_cmake_with_standard(standard), "cpp")
    assert isinstance(result, ReferenceContent)
    assert result.text == f"C++{standard}"


def test_cpp_without_a_standard_stays_not_available() -> None:
    result = _compatibility("cmake_minimum_required(VERSION 3.20)\nproject(Demo VERSION 1.0.0)\n", "cpp")
    assert isinstance(result, NotAvailable)
    assert result.reason == CPP_NOT_AVAILABLE_REASON


def test_setup_py_double_quoted_python_requires_is_answered() -> None:
    text = 'from setuptools import setup\nsetup(\n    name="demo",\n    python_requires=">=3.7",\n)\n'
    result = _compatibility(text, "python")
    assert isinstance(result, ReferenceContent)
    assert result.text == ">=3.7"


def test_setup_py_single_quoted_python_requires_is_answered() -> None:
    text = "from setuptools import setup\nsetup(\n    name='demo',\n    python_requires='>=3.9',\n)\n"
    result = _compatibility(text, "python")
    assert isinstance(result, ReferenceContent)
    assert result.text == ">=3.9"


def test_setup_py_python_requires_with_spaces_around_equals_is_answered() -> None:
    text = 'setup(name="demo", python_requires = ">=3.8,<4")\n'
    result = _compatibility(text, "python")
    assert isinstance(result, ReferenceContent)
    assert result.text == ">=3.8,<4"


def test_pyproject_requires_python_wins_over_a_setup_py_keyword() -> None:
    text = '[project]\nname = "demo"\nrequires-python = ">=3.10"\n\n# legacy: python_requires=">=3.6"\n'
    assert read_python_manifest(text).requires_python == ">=3.10"
    result = _compatibility(text, "python")
    assert isinstance(result, ReferenceContent)
    assert result.text == ">=3.10"


def test_setup_py_without_the_keyword_stays_not_available() -> None:
    text = 'from setuptools import setup\nsetup(\n    name="demo",\n    version="1.0.0",\n)\n'
    result = _compatibility(text, "python")
    assert isinstance(result, NotAvailable)
    assert result.reason == PYTHON_NOT_AVAILABLE_REASON


def test_setup_py_keyword_without_a_quoted_value_stays_not_available() -> None:
    text = "setup(\n    name='demo',\n    python_requires=PYTHON_FLOOR,\n)\n"
    result = _compatibility(text, "python")
    assert isinstance(result, NotAvailable)
    assert result.reason == PYTHON_NOT_AVAILABLE_REASON


# ---------------------------------------------------------------------------------------------------
# The real committed sidecars, pilots derived from values.yaml
# ---------------------------------------------------------------------------------------------------


def test_the_roster_has_cpp_and_python_pilots() -> None:
    assert _pilots_on("cpp"), "values.yaml names no cpp pilot"
    assert len(_pilots_on("python")) >= 12, _pilots_on("python")


@pytest.mark.parametrize("family", sorted(family for family, _ in _pilots_on("cpp")))
def test_every_cpp_pilot_answers_a_cpp_standard(family: str) -> None:
    result = _real_compatibility(family, "cpp")
    assert isinstance(result, ReferenceContent), (family, result)
    assert result.text.startswith("C++"), (family, result.text)
    assert result.text[len("C++") :].isdigit(), (family, result.text)


def test_3d_python_answers_its_setup_py_python_requires() -> None:
    result = _real_compatibility("3d", "python")
    assert isinstance(result, ReferenceContent)
    assert result.text == ">=3.7"


@pytest.mark.parametrize("family", sorted(family for family, _ in _pilots_on("python") if family != "3d"))
def test_every_other_python_pilot_answers_what_the_reader_gives(family: str) -> None:
    text = _sidecar_manifest_text(family, "python")
    expected = read_python_manifest(text).requires_python
    result = _compatibility(text, "python")
    if expected:
        assert isinstance(result, ReferenceContent), (family, result)
        assert result.text == expected, family
    else:
        assert isinstance(result, NotAvailable), (family, result)
        assert result.reason == PYTHON_NOT_AVAILABLE_REASON, family


@pytest.mark.parametrize(
    ("family", "platform"),
    [("cells", "rust"), ("jmap", "rust"), ("cells", "typescript")],
    ids=["cells-rust", "jmap-rust", "cells-typescript"],
)
def test_pilots_whose_manifest_states_nothing_stay_not_available(family: str, platform: str) -> None:
    assert (family, platform) in _pilots()
    result = _real_compatibility(family, platform)
    assert isinstance(result, NotAvailable), (family, platform, result)
