"""Authored examples: the committed sidecar that gives a pilot reviewed, worker-written examples.

REQ-G3-007 (TC-460). A pilot whose README cannot supply two examples that the ingestion verifier
accepts has nowhere to put an example that is not in its README. This module is that place: a
committed, schema-validated sidecar ``tests/fixtures/furnished/<pilot>/authored_examples.yaml`` (the
single source of authored example code; the verification cache of TC-441 holds verdicts only).

This module only LOADS and VALIDATES. It never imports, compiles or runs the code it reads, and it
trusts nothing the author claims: ``infra/build_chunks.py`` hands every authored candidate to the
very same verifier that judges README examples, and publishes only the ones that pass, labelled
with ``AUTHORED_LABEL``.

The structural rules are also written down as ``schemas/product/authored-examples.schema.json``; the
schema file is not copied into the ingestion image, so ``validate_authored_examples`` enforces the
same structure in plain Python, and a test proves the two agree.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from foss_mcp.indexing.example_candidates import CandidateExample, normalised_code_sha256
from foss_mcp.normalization.citation import qualified_symbol_index_from_api_surface

__all__ = [
    "AUTHORED_LABEL",
    "FORBIDDEN_CODE_PATTERNS",
    "LANGUAGE_BY_PLATFORM",
    "SIDECAR_NAME",
    "AuthoredExamplesError",
    "builtin_authored_document",
    "has_builtin_authored_examples",
    "load_authored_examples",
    "load_builtin_authored_examples",
    "validate_authored_examples",
]

SIDECAR_NAME = "authored_examples.yaml"

AUTHORED_LABEL = (
    "Authored example: written for foss-mcp against the pinned library, not taken from the upstream "
    "README; published only after the ingestion verifier compiled or ran it against that commit."
)

# The one language tag a sidecar may carry for each build_chunks ``--library-platform`` value.
LANGUAGE_BY_PLATFORM: Mapping[str, str] = {
    "dotnet": "csharp",
    "cpp": "cpp",
    "python": "python",
    "java": "java",
    "go": "go",
    "rust": "rust",
    "typescript": "typescript",
    "nodejs": "javascript",
}

_TOP_LEVEL_KEYS = frozenset(
    {"schema_version", "pilot", "library_repository", "library_commit", "language", "examples"}
)
_EXAMPLE_KEYS = frozenset({"title", "description", "symbols", "code", "code_sha256"})
_MAX_EXAMPLES = 5
_MAX_TITLE_CHARS = 120
_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_AUTHORED_HEADER_WORDS = "Authored example"


def _usage(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


# Environment and credential access, process spawning, raw network clients and interactive input.
# Every entry matches a USAGE form (an import of the module, a qualified call, a type name) and
# never the bare English word, so a comment such as "build two requests" or "the socket stays
# closed" is not rejected. One module constant so tests and the authoring cards import it rather
# than retyping it.
FORBIDDEN_CODE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # environment and credential access
    _usage(r"\bos\.environ\b"),
    _usage(r"\b(?:os\.|std::)?[Gg]etenv\s*\("),
    _usage(r"\bGetEnvironmentVariable\s*\("),
    _usage(r"\bSystem\.getenv\s*\("),
    _usage(r"\bprocess\.env\b"),
    _usage(r"\benv::var(?:_os)?\b"),
    # process spawning
    _usage(r"\bimport\s+subprocess\b|\bfrom\s+subprocess\s+import\b|\bsubprocess\.[A-Za-z_]"),
    _usage(r"\bos\.system\s*\("),
    _usage(r"\bProcess\.Start\s*\("),
    _usage(r"\b[Pp]open\s*\("),
    _usage(r"\bexec\.Command(?:Context)?\s*\("),
    _usage(r"\bRuntime\.getRuntime\s*\("),
    # raw network clients
    _usage(
        r"\bimport\s+socket\b|\bfrom\s+socket\s+import\b|\bsocket\.[A-Za-z_]\w*\s*\("
        r"|\bnew\s+(?:Server)?Socket\s*\(|\bTcpStream::|\bUdpSocket::"
    ),
    _usage(r"\burlopen\s*\("),
    _usage(r"\burllib\.request\b|\bfrom\s+urllib\s+import\s+request\b"),
    _usage(
        r"\bimport\s+requests\b|\bfrom\s+requests\s+import\b"
        r"|\brequests\.(?:get|post|put|delete|head|patch|request|Session)\b"
    ),
    _usage(r"\bhttp\.client\b|\bfrom\s+http\s+import\s+client\b"),
    _usage(r"\bHttpClient\b"),
    _usage(r"\bWebClient\b"),
    _usage(r"\bTcpClient\b"),
    _usage(r"\bnet\.Dial\w*\s*\("),
    _usage(r"\bhttp\.(?:Get|Post|Head|PostForm)\s*\("),
    _usage(r"(?<![.\w])fetch\(|\b(?:window|globalThis|self)\.fetch\("),
    _usage(r"\breqwest::|\buse\s+reqwest\b|\bextern\s+crate\s+reqwest\b"),
    # interactive input
    _usage(r"(?<![.\w])input\("),
    _usage(r"\bConsole\.Read(?:Line|Key)?\s*\("),
    _usage(r"\bstd::cin\b"),
)

_URL_RE = re.compile(r"https?://([^\s/?#\"'<>`\\]*)", re.IGNORECASE)
_RESERVED_HOSTS = ("example.com", "example.org", "example.net")
_RESERVED_SUFFIXES = (".example", ".test", ".invalid")


class AuthoredExamplesError(ValueError):
    """A sidecar that cannot be loaded; the message lists every problem found."""


def _host_is_reserved(authority: str) -> bool:
    host = authority.rsplit("@", 1)[-1]
    if host.startswith("["):
        return False  # an IP literal is never a reserved name
    host = host.split(":", 1)[0].rstrip(".").lower()
    if not host:
        return False
    if host in _RESERVED_HOSTS or any(host.endswith("." + name) for name in _RESERVED_HOSTS):
        return True
    return any(host.endswith(suffix) for suffix in _RESERVED_SUFFIXES)


def _forbidden_findings(code: str) -> list[str]:
    findings: list[str] = []
    for pattern in FORBIDDEN_CODE_PATTERNS:
        match = pattern.search(code)
        if match is not None:
            findings.append(f"forbidden pattern {pattern.pattern!r} matched {match.group(0)!r}")
    for match in _URL_RE.finditer(code):
        if not _host_is_reserved(match.group(1)):
            findings.append(f"URL literal with a non-reserved host: {match.group(0)!r}")
    return findings


def _non_blank(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _structure_problems(document: Any) -> list[str]:
    """Rule 1: the structural shape, exactly what the JSON schema file says."""
    prefix = "rule 1 (structure): "
    if not isinstance(document, Mapping):
        return [f"{prefix}the document must be a mapping, not {type(document).__name__}"]
    problems: list[str] = []
    keys = set(document)
    for missing in sorted(_TOP_LEVEL_KEYS - keys):
        problems.append(f"{prefix}missing key {missing!r}")
    for extra in sorted(keys - _TOP_LEVEL_KEYS, key=str):
        problems.append(f"{prefix}unexpected key {extra!r}")

    version = document.get("schema_version")
    if "schema_version" in document and not (type(version) is int and version == 1):
        problems.append(f"{prefix}schema_version must be the integer 1")
    for name in ("pilot", "library_repository", "language"):
        if name in document and not _non_blank(document[name]):
            problems.append(f"{prefix}{name} must be a non-empty string")
    commit = document.get("library_commit")
    if "library_commit" in document and not (isinstance(commit, str) and _COMMIT_RE.fullmatch(commit)):
        problems.append(f"{prefix}library_commit must be 40 lowercase hex characters")

    if "examples" not in document:
        return problems
    examples = document["examples"]
    if not isinstance(examples, list):
        problems.append(f"{prefix}examples must be a list")
        return problems
    if not 1 <= len(examples) <= _MAX_EXAMPLES:
        problems.append(f"{prefix}examples must hold 1 to {_MAX_EXAMPLES} items, not {len(examples)}")
    titles_seen: set[str] = set()
    for number, example in enumerate(examples, start=1):
        label = f"{prefix}example {number}"
        if not isinstance(example, Mapping):
            problems.append(f"{label} must be a mapping")
            continue
        example_keys = set(example)
        for missing in sorted(_EXAMPLE_KEYS - example_keys):
            problems.append(f"{label} is missing key {missing!r}")
        for extra in sorted(example_keys - _EXAMPLE_KEYS, key=str):
            problems.append(f"{label} has unexpected key {extra!r}")
        if "title" in example:
            title = example["title"]
            if not (_non_blank(title) and len(title) <= _MAX_TITLE_CHARS):
                problems.append(f"{label}: title must be 1 to {_MAX_TITLE_CHARS} characters")
            elif title in titles_seen:
                problems.append(f"{label}: title {title!r} is not unique within the file")
            else:
                titles_seen.add(title)
        for name in ("description", "code"):
            if name in example and not _non_blank(example[name]):
                problems.append(f"{label}: {name} must be a non-empty string")
        if "symbols" in example:
            symbols = example["symbols"]
            if not isinstance(symbols, list) or not symbols:
                problems.append(f"{label}: symbols must be a non-empty list")
            elif not all(_non_blank(symbol) for symbol in symbols):
                problems.append(f"{label}: every symbol must be a non-empty string")
            elif len(set(symbols)) != len(symbols):
                problems.append(f"{label}: symbols must be unique")
        if "code_sha256" in example:
            digest = example["code_sha256"]
            if not (isinstance(digest, str) and _SHA256_RE.fullmatch(digest)):
                problems.append(f"{label}: code_sha256 must be 64 lowercase hex characters")
    return problems


def _whole_word(word: str, code: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(word)}(?![A-Za-z0-9_])", code) is not None


def validate_authored_examples(
    document: object,
    *,
    pilot: str,
    library_platform: str,
    library_commit: str,
    api_types: Sequence[Mapping[str, object]],
) -> list[str]:
    """Every problem found in a parsed sidecar ``document``; an empty list means it is valid.

    Each problem string starts with the rule it breaks: (1) the structural shape; (2) ``pilot`` is
    the expected pilot; (3) ``library_commit`` is the expected commit; (4) ``language`` is the one
    tag of ``library_platform``; (5) ``code_sha256`` equals ``normalised_code_sha256(code)``; (6)
    every symbol has a declaration in the api_surface (a namespace alone does not count) and its
    last dotted segment appears in the code as a whole word; (7) the first non-blank line of the
    code is a comment (``#`` for python, ``//`` otherwise) containing ``Authored example``; (8) no
    ``FORBIDDEN_CODE_PATTERNS`` match and no URL literal has a non-reserved host. Nothing is
    imported, compiled or run.
    """
    problems = _structure_problems(document)
    if not isinstance(document, Mapping):
        return problems

    if "pilot" in document and document["pilot"] != pilot:
        problems.append(f"rule 2 (pilot): pilot is {document['pilot']!r}, expected {pilot!r}")
    if "library_commit" in document and document["library_commit"] != library_commit:
        problems.append(
            f"rule 3 (commit): library_commit is {document['library_commit']!r}, expected {library_commit!r}"
        )
    expected_language = LANGUAGE_BY_PLATFORM.get(library_platform)
    if expected_language is None:
        problems.append(f"rule 4 (language): unknown library platform {library_platform!r}")
    elif "language" in document and document["language"] != expected_language:
        problems.append(
            f"rule 4 (language): language is {document['language']!r}, "
            f"expected {expected_language!r} for platform {library_platform}"
        )
    comment_prefix = "#" if library_platform == "python" else "//"

    index = qualified_symbol_index_from_api_surface(api_types)
    examples = document.get("examples")
    if not isinstance(examples, list):
        return problems
    for number, example in enumerate(examples, start=1):
        if not isinstance(example, Mapping):
            continue
        label = f"example {number}"
        code = example.get("code")
        if not isinstance(code, str):
            continue
        digest = example.get("code_sha256")
        if isinstance(digest, str) and digest != normalised_code_sha256(code):
            problems.append(f"rule 5 (code hash): {label}: code_sha256 does not match the code")
        symbols = example.get("symbols")
        if isinstance(symbols, list):
            for symbol in symbols:
                if not isinstance(symbol, str) or not symbol.strip():
                    continue
                if not index.declarations_for(symbol):
                    problems.append(
                        f"rule 6 (symbols): {label}: symbol {symbol!r} has no declaration in the api_surface"
                    )
                    continue
                last_segment = symbol.rsplit(".", 1)[-1]
                if not _whole_word(last_segment, code):
                    problems.append(
                        f"rule 6 (symbols): {label}: symbol {symbol!r} ({last_segment!r}) "
                        "does not appear in the code as a whole word"
                    )
        first_line = next((line.strip() for line in code.splitlines() if line.strip()), "")
        if not (first_line.startswith(comment_prefix) and _AUTHORED_HEADER_WORDS in first_line):
            problems.append(
                f"rule 7 (header): {label}: the first non-blank line must be a {comment_prefix} comment "
                f"containing {_AUTHORED_HEADER_WORDS!r}"
            )
        for finding in _forbidden_findings(code):
            problems.append(f"rule 8 (forbidden code): {label}: {finding}")
    return problems


def load_authored_examples(
    path: Path,
    *,
    pilot: str,
    library_platform: str,
    library_commit: str,
    api_types: Sequence[Mapping[str, object]],
) -> tuple[CandidateExample, ...]:
    """Load and validate the sidecar at ``path``; one authored ``CandidateExample`` per example.

    The candidates are in file order, with ``provenance`` ``"authored"``, ``source`` ``"sidecar"``,
    ``starts_example`` True, ``imports_only`` False, an empty ``import_prefix``, a ``page_code``
    holding only their own code and the sidecar's ``code_sha256``. Raises ``AuthoredExamplesError``
    whose message lists every problem when the file cannot be read or parsed or any rule of
    ``validate_authored_examples`` fails. The code is never imported, compiled or run here.
    """
    try:
        text = Path(path).read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise AuthoredExamplesError(f"cannot read {SIDECAR_NAME}: {type(exc).__name__}") from exc
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise AuthoredExamplesError(f"{SIDECAR_NAME} is not valid YAML: {type(exc).__name__}") from exc

    problems = validate_authored_examples(
        document,
        pilot=pilot,
        library_platform=library_platform,
        library_commit=library_commit,
        api_types=api_types,
    )
    if problems:
        raise AuthoredExamplesError("; ".join(problems))
    return _candidates_from_document(document)


def _candidates_from_document(document: Mapping[str, Any]) -> tuple[CandidateExample, ...]:
    candidates: list[CandidateExample] = []
    for example in document["examples"]:
        code = example["code"].strip()
        candidates.append(
            CandidateExample(
                title=example["title"],
                description=example["description"].strip(),
                language=document["language"],
                code=code,
                page_code=(code,),
                code_sha256=example["code_sha256"],
                source="sidecar",
                starts_example=True,
                imports_only=False,
                import_prefix=(),
                provenance="authored",
            )
        )
    return tuple(candidates)


# --- examples carried by this module (TC-565, REQ-G3-009) ---------------------------------------
#
# 3d/net and 3d/java have no working example of their own in the upstream README: the README's own
# ``Save("o.obj", FileFormat.WavefrontOBJ)`` throws NotImplementedException and its "Build a Scene From
# a Primitive" writes an empty STL (a Box attached directly has no polygons; ``ToMesh``/``toMesh`` makes
# the polygons STL needs). These two pilots therefore carry authored examples HERE, written against the
# API shape the pilot's README quotes (``Scene``, ``Node.CreateChildNode``, ``Box.ToMesh``, ``Scene.Save``
# with a ``.stl`` path or ``FileFormat.STLBinary``/``STLASCII``). They go through exactly the same
# validation as a sidecar file, are verified by the same verifier (compiled AND RUN, the artefact is read
# back), and are labelled as authored. Until the supervisor's closing run executes them, their
# verification-cache entries carry ``ran`` unknown.
_BUILTIN_PILOTS: Mapping[str, Mapping[str, Any]] = {
    "3d_net": {
        "library_repository": "aspose-3d-foss/Aspose.3D-FOSS-for-.NET",
        "library_commit": "52b0f00ebf28a0b4173921725ff170685ec2c502",
        "language": "csharp",
        "examples": (
            {
                "title": "Save a box mesh as an STL file",
                "description": (
                    "Converts a Box primitive to a mesh with ToMesh, attaches the mesh to a child node of the "
                    "scene root and saves the scene with Scene.Save to box.stl, the one format that is fully "
                    "functional in this edition; the file's size is printed."
                ),
                "symbols": ["Scene", "Scene.Save", "Box", "Box.ToMesh", "Node.CreateChildNode"],
                "code": """\
// Authored example: save a box mesh as an STL file
using Aspose.ThreeD;
using Aspose.ThreeD.Entities;

var scene = new Scene();
Mesh mesh = new Box(2, 2, 2).ToMesh();
scene.RootNode.CreateChildNode("box", mesh);
scene.Save("box.stl");
Console.WriteLine(new FileInfo("box.stl").Length);
""",
            },
            {
                "title": "Save a cylinder mesh as a binary STL with an explicit format",
                "description": (
                    "Converts a Cylinder primitive to a mesh, attaches it to a child node and saves the scene "
                    "with Scene.Save and FileFormat.STLBinary, so the output format is stated rather than "
                    "taken from the file extension."
                ),
                "symbols": ["Scene", "Scene.Save", "Cylinder", "Cylinder.ToMesh", "FileFormat.STLBinary"],
                "code": """\
// Authored example: save a cylinder mesh as a binary STL file
using Aspose.ThreeD;
using Aspose.ThreeD.Entities;

var scene = new Scene();
Mesh mesh = new Cylinder().ToMesh();
scene.RootNode.CreateChildNode("cylinder", mesh);
scene.Save("cylinder.stl", FileFormat.STLBinary);
Console.WriteLine(new FileInfo("cylinder.stl").Length);
""",
            },
        ),
    },
    "3d_java": {
        "library_repository": "aspose-3d-foss/Aspose.3D-FOSS-for-Java",
        "library_commit": "3d2ed6be91f5abdd1c52ecbcfa192719883cb1b3",
        "language": "java",
        "examples": (
            {
                "title": "Save a box mesh as an STL file",
                "description": (
                    "Converts a Box primitive to a mesh with toMesh, attaches the mesh to a child node of the "
                    "scene root and saves the scene with Scene.save to box.stl, the one format that is fully "
                    "functional in this edition. Attaching the Box itself writes an empty file."
                ),
                "symbols": ["Scene", "Scene.save", "Box", "Box.toMesh", "Node.createChildNode"],
                "code": """\
// Authored example: save a box mesh as an STL file
import com.aspose.threed.Box;
import com.aspose.threed.Mesh;
import com.aspose.threed.Scene;

Scene scene = new Scene();
Mesh mesh = new Box(2.0, 2.0, 2.0).toMesh();
scene.getRootNode().createChildNode("box", mesh);
scene.save("box.stl");
System.out.println(new File("box.stl").length());
""",
            },
            {
                "title": "Save a sphere mesh as an ASCII STL file",
                "description": (
                    "Converts a Sphere primitive to a mesh with toMesh, attaches it to a child node and saves "
                    "the scene with Scene.save and FileFormat.STLASCII, so the output is a text STL."
                ),
                "symbols": ["Scene", "Scene.save", "Sphere", "Sphere.toMesh", "Node.createChildNode"],
                "code": """\
// Authored example: save a sphere mesh as an ASCII STL file
import com.aspose.threed.FileFormat;
import com.aspose.threed.Mesh;
import com.aspose.threed.Scene;
import com.aspose.threed.Sphere;

Scene scene = new Scene();
Mesh mesh = new Sphere().toMesh();
scene.getRootNode().createChildNode("sphere", mesh);
scene.save("sphere.stl", FileFormat.STLASCII);
System.out.println(new File("sphere.stl").length());
""",
            },
        ),
    },
}


def has_builtin_authored_examples(pilot: str) -> bool:
    """True when this module carries authored examples for the pilot directory named ``pilot``."""
    return pilot in _BUILTIN_PILOTS


def builtin_authored_document(pilot: str) -> dict[str, Any]:
    """The sidecar-shaped document of the examples this module carries for ``pilot``; the hash of each
    example is computed from its code. Raises ``KeyError`` for a pilot with none."""
    spec = _BUILTIN_PILOTS[pilot]
    return {
        "schema_version": 1,
        "pilot": pilot,
        "library_repository": spec["library_repository"],
        "library_commit": spec["library_commit"],
        "language": spec["language"],
        "examples": [
            {**example, "code_sha256": normalised_code_sha256(example["code"])}
            for example in spec["examples"]
        ],
    }


def load_builtin_authored_examples(
    pilot: str,
    *,
    library_platform: str,
    library_commit: str,
    api_types: Sequence[Mapping[str, object]],
) -> tuple[CandidateExample, ...]:
    """The authored candidates this module carries for ``pilot``, after the SAME validation a sidecar
    file gets (``validate_authored_examples``). Raises ``AuthoredExamplesError`` listing every problem,
    including a ``library_commit`` that is not the one the examples were written against."""
    document = builtin_authored_document(pilot)
    problems = validate_authored_examples(
        document,
        pilot=pilot,
        library_platform=library_platform,
        library_commit=library_commit,
        api_types=api_types,
    )
    if problems:
        raise AuthoredExamplesError("; ".join(problems))
    return _candidates_from_document(document)
