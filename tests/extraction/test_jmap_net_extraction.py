"""Offline tests against the committed jmap/net fixture - no network, no live clone.

``gatectl`` verifies this card with the network proxied to a dead port (this card's own
``network: false``), so nothing here may reach out; everything is read from the fixture
``run_extraction.py`` already produced and committed at
``tests/fixtures/jmap_net/api_surface.json``.

.NET-platform extraction support was already proven by the pdf/net and cells/net
onboardings; this card is a straight run-and-pin against a third, real, public .NET
repository (``aspose-email-foss/Aspose.JMAP-FOSS-for-.NET``) - no engine change at all.
"net" (not "dotnet") is the manifest's own platform value, matching every other pilot's
convention; ``run_extraction._LANGUAGE_BY_PLATFORM`` maps both spellings to "csharp".

The real repository's full public surface is only 42 types - well under the CLI's default
--max-types 300 cap - so this fixture is the complete surface, not a sub-sample:
``type_count == reduced_type_count == 42`` and ``truncated is False``.

The observed ``kind`` vocabulary is ``{"class_declaration", "interface_declaration"}`` -
this repository's public surface has no top-level ``struct`` or ``enum`` declarations,
unlike cells/net's fixture.

TC-310 re-ran this extraction live (network access, real default --max-types 300, no
truncation) now that both TC-252 (centrality-ranked ``reduce_fixture()``) and TC-261
(``inherited_from`` provenance tagging in ``_flatten_inheritance()``) are on main. The
commit pin (``0d5cf17e0e9ee185eea2847f6587b918ac0eb016``) and repository
(``aspose-email-foss/Aspose.JMAP-FOSS-for-.NET``) came back unchanged and match
``infra/helm/foss-mcp/values.yaml`` and ``docker-compose.yml`` exactly - no commit-pin
divergence here, unlike several sibling pilots fixed earlier this session. Re-ordering
within ``types`` (TC-252's centrality ranking, inert here since ``truncated is False``) is
the only textual change the diff shows.

TC-310 also hand-verified, against a fresh clone of the pinned commit, every real
inheritance relationship this repository's public surface contains:

- ``JmapNetworkException`` and ``JmapProtocolException`` both derive from the real,
  in-repo ``JmapException`` (``src/Aspose.Jmap/Models/CommonTypes.cs``, line 80:
  ``public abstract class JmapException : Exception``). ``JmapException`` itself declares
  only *protected* constructors - no public methods or properties at all - so there is
  nothing on it that a public-API extractor could copy down to either subclass; each
  subclass declares its own constructor directly.
- ``HttpJmapTransport`` implements the real, in-repo ``IJmapTransport`` interface
  (``src/Aspose.Jmap/Models/Transport.cs``, line 67), whose only member is ``SendAsync``.
  C# requires every implementing class to supply its own body for an interface member. The
  real source confirms ``HttpJmapTransport`` declares its own ``SendAsync`` directly
  (``Transport.cs``), so there is nothing to copy down there either - the implementation is
  always locally declared, never inherited in the member-copy sense.
- ``JmapClient`` (``IDisposable``, ``IAsyncDisposable``) and ``HttpJmapTransport``'s other
  two bases (``IDisposable``, ``IAsyncDisposable``) are framework interfaces, not declared
  in this repository at all, so they are not resolvable members to copy down either.

No multi-level chain exists anywhere in this repository's public surface (the deepest is
one level: ``JmapException`` -> {``JmapNetworkException``, ``JmapProtocolException``}).
``_flatten_inheritance()`` correctly produces zero ``inherited_from`` tags anywhere in this
fixture - confirmed by grepping the fixture JSON for the literal string
``"inherited_from"`` (zero hits, both before and after this re-pin). That is accurate
extraction of accurate source, not evidence of stale tagging: TC-261's own fix is exercised
correctly elsewhere (e.g. pdf/net's own fixture and test) - there is simply nothing on any
of this repository's real ancestors to tag.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "jmap_net" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "jmap" / "net.yaml"

_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_fixture_parses_and_has_the_expected_shape() -> None:
    data = _load_fixture()
    assert isinstance(data, dict)
    assert isinstance(data["types"], list)
    assert data["language"] == "csharp"


def test_the_fixture_records_the_exact_source_commit_and_repository() -> None:
    data = _load_fixture()
    assert _COMMIT_SHA.match(data["source_commit"]), data["source_commit"]
    # Recorded from the real live clone that produced this fixture - re-verify by hand with
    # `git ls-remote https://github.com/aspose-email-foss/Aspose.JMAP-FOSS-for-.NET` if this
    # ever needs re-pinning; do not change it to make a test pass.
    assert data["source_commit"] == "0d5cf17e0e9ee185eea2847f6587b918ac0eb016"
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert data["source_repository"] == manifest["repository"]


def test_the_fixture_is_non_empty() -> None:
    data = _load_fixture()
    assert len(data["types"]) > 0
    assert data["reduced_type_count"] == len(data["types"])
    # The real repository has only 42 public types - well under the CLI's default
    # --max-types 300 cap, so this fixture is the full, untruncated surface.
    assert data["type_count"] == 42
    assert data["truncated"] is False


def test_the_fixture_contains_real_dotnet_type_names() -> None:
    data = _load_fixture()
    names = {entry["name"] for entry in data["types"]}
    # Real Aspose.JMAP-FOSS-for-.NET types, not placeholders - would not survive an empty
    # or synthetic fixture.
    assert {"Account", "Address", "Comparator", "CoreCapability"} <= names
    assert all(entry["file"].endswith(".cs") for entry in data["types"])


def test_every_entry_is_a_real_csharp_declaration_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    assert kinds <= {
        "class_declaration",
        "struct_declaration",
        "interface_declaration",
        "enum_declaration",
    }
    assert kinds


def test_the_only_real_inheritance_relationships_are_exactly_these_five() -> None:
    """Confirmed by hand against a fresh clone of the pinned commit: these are the only
    five types in the real public surface with a non-empty ``bases`` list, and these are
    their exact real bases (``JmapException``, ``IJmapTransport`` are in-repo;
    ``Exception``, ``IDisposable``, ``IAsyncDisposable`` are framework types, not declared
    in this repository at all).
    """
    data = _load_fixture()
    by_name = {entry["name"]: entry for entry in data["types"]}
    expected_bases = {
        "JmapException": ["Exception"],
        "JmapClient": ["IDisposable", "IAsyncDisposable"],
        "HttpJmapTransport": ["IJmapTransport", "IDisposable", "IAsyncDisposable"],
        "JmapNetworkException": ["JmapException"],
        "JmapProtocolException": ["JmapException"],
    }
    others = [t for t in data["types"] if t["name"] not in expected_bases]
    assert all(t["bases"] == [] for t in others)
    for name, bases in expected_bases.items():
        assert by_name[name]["bases"] == bases


def test_no_inherited_from_tag_exists_because_no_real_ancestor_has_a_copyable_member() -> None:
    """``JmapException`` (``src/Aspose.Jmap/Models/CommonTypes.cs``, line 80:
    ``public abstract class JmapException : Exception``) declares only *protected*
    constructors in the real, pinned source - no public methods or properties at all - so
    there is nothing on it for ``_flatten_inheritance()`` (TC-261) to copy down to either
    ``JmapNetworkException`` or ``JmapProtocolException``; both declare their own
    constructors directly. ``IJmapTransport``'s only member, ``SendAsync``, must always be
    re-implemented by any class that implements it (C# interface semantics); the real
    source confirms ``HttpJmapTransport`` declares its own ``SendAsync`` directly rather
    than inheriting one. TC-261's own tagging is exercised correctly elsewhere (e.g.
    pdf/net's own fixture, which carries real ``inherited_from`` tags) - there is simply
    nothing on any of this repository's real ancestors to tag. This is accurate extraction
    of this repository's real shape, not evidence that TC-261's tagging is stale.
    """
    data = _load_fixture()
    by_name = {entry["name"]: entry for entry in data["types"]}
    jmap_exception = by_name["JmapException"]
    assert jmap_exception["methods"] == []
    assert jmap_exception["properties"] == []
    for entry in data["types"]:
        for member in entry["methods"] + entry["properties"]:
            assert member.get("inherited_from") is None
