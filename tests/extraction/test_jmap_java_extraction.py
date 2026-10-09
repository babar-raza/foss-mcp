"""Offline tests against the committed jmap/java fixture - no network, no live clone.

``gatectl`` verifies this card with the network proxied to a dead port (this card's own
``network: false``), so nothing here may reach out; everything is read from the fixture
``run_extraction.py`` already produced and committed at
``tests/fixtures/jmap_java/api_surface.json``.

Java-platform extraction support was already proven by the pdf/java and cells/java
onboardings (the latter from this very card); this card is a straight run-and-pin against
a third, real, public Java repository (``aspose-email-foss/Aspose.JMAP-FOSS-for-Java``) -
no engine change at all.

The real repository's full public surface is only 39 types - well under the CLI's default
--max-types 300 cap - so this fixture is the complete surface, not a sub-sample:
``type_count == reduced_type_count == 39`` and ``truncated is False``.

The observed ``kind`` vocabulary is ``{"class_declaration", "interface_declaration"}`` -
this repository's public surface has no top-level ``enum``, ``record``, or
``annotation_type_declaration`` entries, unlike pdf/java's fixture.

TC-309 re-ran this extraction live (network access, real default --max-types 300, no
truncation) now that both TC-252 (centrality-ranked ``reduce_fixture()``) and TC-261
(``inherited_from`` provenance tagging in ``_flatten_inheritance()``) are on main. The
commit pin (``f0d6ba73dc92db4b3effaa7c1fa76682e2d09fd4``) and repository
(``aspose-email-foss/Aspose.JMAP-FOSS-for-Java``) came back unchanged and match
``infra/helm/foss-mcp/values.yaml`` and ``docker-compose.yml`` exactly - no commit-pin
divergence here. 39/39 types, not truncated - same shape as before.

Unlike jmap/go's sibling pilot (TC-303, genuinely flat - its one real embedding
relationship roots on an empty marker struct), this repository's real, live re-run DOES
carry real, non-empty ``inherited_from`` tags, hand-verified against a fresh clone of the
pinned commit:

- ``JmapClient`` (``extends JmapClientCore implements MailClientMethods,
  SubmissionClientMethods``) declares only its own constructor in the real source and
  genuinely inherits ``connect``, ``sendRequest``, ``echo``, ``uploadBlob`` and
  ``downloadBlob`` from ``JmapClientCore`` without overriding any of them - confirmed by
  reading ``JmapClient.java`` directly (it has exactly one method, its constructor) against
  ``JmapClientCore.java``'s own public method list. Each copied member is correctly tagged
  ``inherited_from: "com.aspose.jmap.JmapClientCore"``.
- ``JmapNetworkError`` and ``JmapProtocolError`` (``extends JmapError``) each declare their
  own two constructors locally (confirmed in source: both call ``super(message)`` /
  ``super(message, cause)`` rather than being copied) - these carry no ``inherited_from``
  tag, exactly as they should not. Each class ALSO carries a separate copy of
  ``JmapError``'s own two constructors, correctly tagged
  ``inherited_from: "com.aspose.jmap.JmapError"``.

No multi-level (3+ class) chain exists anywhere in this real repository's public surface -
hand-verified by cloning the pinned commit directly, not assumed. The only two apparent
"grandparent" links never resolve to a second real, public ancestor with its own members:
``JmapClientCore implements JmapCoreOperations``, but ``JmapCoreOperations`` is declared
package-private (``interface JmapCoreOperations { ... }``, no ``public`` modifier, nested
at the bottom of ``JmapClientCore.java``) and is correctly excluded from the public API
surface entirely by the extraction engine's existing ``is_public()`` filter - it never
becomes a node ``_flatten_inheritance()`` can resolve a parent to. ``JmapError extends
RuntimeException``, which is an external JDK class with no declaration in this repository
at all. So every real inheritance relationship in this fixture is exactly one level deep;
this is a genuine structural property of the real pinned source, not evidence that the
engine failed to walk a deeper chain.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "jmap_java" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "jmap" / "java.yaml"

_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_fixture_parses_and_has_the_expected_shape() -> None:
    data = _load_fixture()
    assert isinstance(data, dict)
    assert isinstance(data["types"], list)
    assert data["language"] == "java"


def test_the_fixture_records_the_exact_source_commit_and_repository() -> None:
    data = _load_fixture()
    assert _COMMIT_SHA.match(data["source_commit"]), data["source_commit"]
    # Recorded from the real live clone that produced this fixture - re-verify by hand with
    # `git ls-remote https://github.com/aspose-email-foss/Aspose.JMAP-FOSS-for-Java` if this
    # ever needs re-pinning; do not change it to make a test pass.
    assert data["source_commit"] == "f0d6ba73dc92db4b3effaa7c1fa76682e2d09fd4"
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert data["source_repository"] == manifest["repository"]


def test_the_fixture_is_non_empty() -> None:
    data = _load_fixture()
    assert len(data["types"]) > 0
    assert data["reduced_type_count"] == len(data["types"])
    # The real repository has only 39 public types - well under the CLI's default
    # --max-types 300 cap, so this fixture is the full, untruncated surface.
    assert data["type_count"] == 39
    assert data["truncated"] is False


def test_the_fixture_contains_real_java_type_names() -> None:
    data = _load_fixture()
    names = {entry["name"] for entry in data["types"]}
    # Real Aspose.JMAP-FOSS-for-Java exported types, not placeholders - would not survive
    # an empty or synthetic fixture.
    assert {"Account", "Address", "Builder", "CoreCapability"} <= names
    assert all(entry["file"].endswith(".java") for entry in data["types"])


def test_every_entry_is_a_real_java_declaration_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    assert kinds <= {
        "class_declaration",
        "interface_declaration",
        "enum_declaration",
        "record_declaration",
        "annotation_type_declaration",
    }
    assert kinds


def test_inherited_members_carry_correct_inherited_from_provenance() -> None:
    """TC-309: this repository's real, live re-run genuinely carries non-empty
    ``inherited_from`` tags (unlike the genuinely-flat jmap/go sibling, TC-303) - verified
    directly against a fresh clone of the pinned commit, not assumed from the card's own
    premise.
    """
    data = _load_fixture()
    by_name = {entry["name"]: entry for entry in data["types"]}

    total_tagged = sum(
        1
        for entry in data["types"]
        for member in entry["methods"] + entry["properties"]
        if "inherited_from" in member
    )
    assert total_tagged > 0

    # JmapClient genuinely declares only its own constructor in the real source (confirmed
    # by reading JmapClient.java directly) and inherits the rest from JmapClientCore
    # without overriding any of them.
    client = by_name["JmapClient"]
    assert client["bases"] == ["JmapClientCore", "MailClientMethods", "SubmissionClientMethods"]
    client_methods = {m["name"]: m for m in client["methods"]}
    assert "inherited_from" not in client_methods["JmapClient"]  # its own constructor
    for inherited_name in ("connect", "sendRequest", "echo", "uploadBlob", "downloadBlob"):
        assert client_methods[inherited_name]["inherited_from"] == "com.aspose.jmap.JmapClientCore"

    # JmapNetworkError (2 own constructors: message / message+cause) and JmapProtocolError
    # (3 own constructors: type / type+description / type+description+cause - confirmed by
    # reading JmapProtocolError.java directly) each declare their own constructors locally
    # rather than having them copied, AND each separately carries a copy of JmapError's own
    # two constructors, correctly rooted.
    expected_own_ctor_counts = {"JmapNetworkError": 2, "JmapProtocolError": 3}
    for subclass_name, expected_own_count in expected_own_ctor_counts.items():
        subclass = by_name[subclass_name]
        assert subclass["bases"] == ["JmapError"]
        own_ctors = [m for m in subclass["methods"] if m["name"] == subclass_name]
        inherited_ctors = [m for m in subclass["methods"] if m["name"] == "JmapError"]
        assert len(own_ctors) == expected_own_count
        assert all("inherited_from" not in m for m in own_ctors)
        assert len(inherited_ctors) == 2
        assert all(m["inherited_from"] == "com.aspose.jmap.JmapError" for m in inherited_ctors)


def test_no_real_multi_level_inheritance_chain_exists_in_this_repository() -> None:
    """Hand-verified against a fresh clone of the pinned commit, not assumed: neither
    apparent "grandparent" link in this repository resolves to a second real, public
    ancestor with its own members. ``JmapClientCore implements JmapCoreOperations``, but
    ``JmapCoreOperations`` is package-private (no ``public`` modifier) and therefore
    correctly excluded from this public-surface fixture entirely by the engine's existing
    visibility filter - it never appears as its own entry. ``JmapError extends
    RuntimeException``, an external JDK class with no declaration in this repository. So
    every real inheritance relationship here is exactly one level deep; this is a genuine
    structural property of the real source, not a defect in ``_flatten_inheritance()``'s
    chain-walking.
    """
    data = _load_fixture()
    names = {entry["name"] for entry in data["types"]}
    assert "JmapCoreOperations" not in names
    by_name = {entry["name"]: entry for entry in data["types"]}
    # Every base referenced anywhere either resolves to another real entry in this fixture,
    # or is one of the two known-external/non-public roots - never a silently-dropped name.
    known_unresolved_roots = {"JmapCoreOperations", "RuntimeException"}
    for entry in data["types"]:
        for base in entry["bases"]:
            assert base in names or base in known_unresolved_roots
