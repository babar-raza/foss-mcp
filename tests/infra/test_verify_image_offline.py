"""TC-176: the verify image parses offline, because its tree-sitter grammars are baked at build time.

Real, not mocked. The module fixture runs a real `docker build -f Dockerfile.ingestion` (the build
has network access; that is the point). Each test then runs the built image with `--network none`
and makes the same call the extraction engine makes: tree_sitter_language_pack.get_parser(language).
A parse must produce a real tree, not an empty or error-only one.

The language set is read from the engine (lang.LANGUAGES), so a language the engine uses but the
Dockerfile does not bake fails here, inside the offline container.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from foss_mcp.extraction.tree_sitter_engine import lang

REPO_ROOT = Path(__file__).resolve().parents[2]
IMAGE = "foss-mcp-verify:local"
ENGINE_LANGUAGES = tuple(sorted(lang.LANGUAGES))

JAVA_SNIPPET = (
    "public class Greeter {\n"
    "    public String greet(String name) {\n"
    '        return "hello " + name;\n'
    "    }\n"
    "}\n"
)
PYTHON_SNIPPET = "def greet(name):\n    return 'hello ' + name\n"


@pytest.fixture(scope="module")
def verify_image() -> str:
    result = subprocess.run(
        ["docker", "build", "-f", "Dockerfile.ingestion", "-t", IMAGE, "."],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=3000,
    )
    assert result.returncode == 0, (result.stdout + result.stderr)[-6000:]
    return IMAGE


def _run_offline(script: str) -> str:
    result = subprocess.run(
        ["docker", "run", "--rm", "--network", "none", "--entrypoint", "python", IMAGE, "-c", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    assert result.returncode == 0, (result.stdout + result.stderr)[-4000:]
    return result.stdout


def _parse_offline(language: str, source: str) -> dict:
    script = "\n".join(
        [
            "import json",
            "from tree_sitter_language_pack import get_parser",
            f"source = {source!r}.encode('utf-8')",
            f"root = get_parser({language!r}).parse(source).root_node",
            "print(json.dumps({'root': root.type,",
            "                  'children': [c.type for c in root.children],",
            "                  'has_error': root.has_error}))",
        ]
    )
    return json.loads(_run_offline(script).strip().splitlines()[-1])


def test_java_snippet_parses_offline_into_a_class_declaration(verify_image: str) -> None:
    tree = _parse_offline("java", JAVA_SNIPPET)
    assert tree["has_error"] is False, tree
    assert "class_declaration" in tree["children"], tree


def test_python_snippet_parses_offline_into_a_function_definition(verify_image: str) -> None:
    tree = _parse_offline("python", PYTHON_SNIPPET)
    assert tree["has_error"] is False, tree
    assert "function_definition" in tree["children"], tree


def test_every_engine_language_loads_offline(verify_image: str) -> None:
    script = "\n".join(
        [
            "import json",
            "from tree_sitter_language_pack import get_parser",
            f"names = {list(ENGINE_LANGUAGES)!r}",
            "loaded = {name: get_parser(name).parse(b'x').root_node.type for name in names}",
            "print(json.dumps(loaded))",
        ]
    )
    loaded = json.loads(_run_offline(script).strip().splitlines()[-1])
    assert set(loaded) == set(ENGINE_LANGUAGES), loaded
    assert all(isinstance(root_type, str) and root_type for root_type in loaded.values()), loaded
