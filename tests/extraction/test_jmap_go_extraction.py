"""Offline tests against the committed jmap/go fixture - no network, no live clone.

``gatectl`` verifies this card with the network proxied to a dead port (this card's own
``network: false``), so nothing here may reach out; everything is read from the fixture
``run_extraction.py`` already produced and committed at
``tests/fixtures/jmap_go/api_surface.json``.

Go-platform extraction support was already proven by the pdf/go and cells/go onboardings;
this card is a straight run-and-pin against a third, real, public Go repository
(``aspose-email-foss/Aspose.JMAP-FOSS-for-Go``) - no engine change at all. Like every other
Go pilot, "go" is not special-cased in ``run_extraction._LANGUAGE_BY_PLATFORM`` and goes
through the ordinary tree-sitter path (grammar name literally ``"go"``) via
``tree_sitter_engine.api_surface``. Its ``language`` field reads literally ``"go"``, and the
observed ``kind`` vocabulary among the fixture is the tree-sitter Go grammar's own
node-type names: ``{"type_spec", "function"}`` - Go has no class/struct/enum/trait keywords
of its own, so every named type declaration surfaces as ``type_spec`` and every func
declaration (free function or method with a receiver) surfaces as ``function``.

The real repository's full public surface is only 149 types - well under the CLI's default
--max-types 300 cap - so this fixture is the complete surface, not a sub-sample:
``type_count == reduced_type_count == 149`` and ``truncated is False``.

TC-303 re-ran this extraction live (network access, real default --max-types 300, no
truncation) now that both TC-252 (centrality-ranked reduce_fixture()) and TC-261
(``inherited_from`` provenance tagging in ``_flatten_inheritance()``) are on main. The
commit pin (``327dda39116ba02657fa49ec6dbeb69965628447``) and repository
(``aspose-email-foss/Aspose.JMAP-FOSS-for-Go``) came back unchanged and match
``infra/helm/foss-mcp/values.yaml`` and ``docker-compose.yml`` exactly - no commit-pin
divergence here, unlike several sibling pilots fixed earlier this session. Re-ordering
within ``types`` (TC-252's centrality ranking, inert here since ``truncated is False``) is
the only textual change the diff shows.

TC-303 also hand-verified, against a fresh clone of the pinned commit, the ONLY struct
embedding ("inheritance") relationship this real repository's public surface contains:
``NetworkError`` and ``ProtocolError`` both embed ``JmapError`` (``common_types.go``, line
47: ``type JmapError struct{}``). ``JmapError`` is declared with zero fields and zero
methods - it exists purely as a marker/sentinel base type for ``errors.As`` dispatch, not
to share implementation. Each of ``NetworkError`` and ``ProtocolError`` declares its own
``Error()`` method directly; nothing is copied down from an empty ancestor, so
``_flatten_inheritance()`` correctly produces zero ``inherited_from`` tags anywhere in this
fixture - confirmed by grepping the fixture JSON for the literal string
``"inherited_from"`` (zero hits, both before and after this re-pin). That is accurate
extraction of accurate source, not evidence of stale tagging: TC-261's own fix is
exercised correctly by this fixture - rooting ``bases`` on the true ancestor, which is
exactly what it did - there is simply nothing on that ancestor to tag. A multi-level chain
does not exist anywhere in this repository's public surface either (only two struct
embeddings total, both one level deep, both rooted on the same empty ``JmapError``); this
was confirmed by scanning every ``.go`` file in the pinned clone for anonymous
(embedded-field) struct members.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "jmap_go" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "jmap" / "go.yaml"

_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_fixture_parses_and_has_the_expected_shape() -> None:
    data = _load_fixture()
    assert isinstance(data, dict)
    assert isinstance(data["types"], list)
    assert data["language"] == "go"


def test_the_fixture_records_the_exact_source_commit_and_repository() -> None:
    data = _load_fixture()
    assert _COMMIT_SHA.match(data["source_commit"]), data["source_commit"]
    # Recorded from the real live clone that produced this fixture - re-verify by hand with
    # `git ls-remote https://github.com/aspose-email-foss/Aspose.JMAP-FOSS-for-Go` if this
    # ever needs re-pinning; do not change it to make a test pass.
    assert data["source_commit"] == "327dda39116ba02657fa49ec6dbeb69965628447"
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert data["source_repository"] == manifest["repository"]


def test_the_fixture_is_non_empty() -> None:
    data = _load_fixture()
    assert len(data["types"]) > 0
    assert data["reduced_type_count"] == len(data["types"])
    # The real repository has only 149 public types - well under the CLI's default
    # --max-types 300 cap, so this fixture is the full, untruncated surface.
    assert data["type_count"] == 149
    assert data["truncated"] is False


def test_the_fixture_contains_real_go_type_names() -> None:
    data = _load_fixture()
    names = {entry["name"] for entry in data["types"]}
    # Real Aspose.JMAP-FOSS-for-Go exported identifiers, not placeholders - would not
    # survive an empty or synthetic fixture.
    assert {"Account", "Address", "ClientOptions"} <= names
    assert all(entry["file"].endswith(".go") for entry in data["types"])


def test_every_entry_is_a_real_go_surface_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    # Observed directly from the live tree-sitter Go grammar's own node-type vocabulary for
    # this repository, not guessed or invented. Go has no class/struct/enum/trait keywords of
    # its own: every named type declaration surfaces as "type_spec" and every func
    # declaration (free function or method) surfaces as "function".
    assert kinds == {"type_spec", "function"}


def test_the_only_real_inheritance_relationship_is_rooted_correctly() -> None:
    """NetworkError and ProtocolError both embed JmapError - the only struct embedding in
    this repository's public surface (hand-verified against a fresh clone of the pinned
    commit: every other type's "bases" is empty). _flatten_inheritance() (TC-261) correctly
    records that relationship via "bases" for both.
    """
    data = _load_fixture()
    by_name = {entry["name"]: entry for entry in data["types"]}
    assert by_name["NetworkError"]["bases"] == ["JmapError"]
    assert by_name["ProtocolError"]["bases"] == ["JmapError"]
    # Every other real type in this fixture has no base at all - this really is the only
    # inheritance relationship in the whole public surface, not a sample of a larger set.
    others = [t for t in data["types"] if t["name"] not in ("NetworkError", "ProtocolError")]
    assert all(t["bases"] == [] for t in others)


def test_no_inherited_from_tag_exists_because_the_real_ancestor_has_no_members() -> None:
    """JmapError (common_types.go:47, ``type JmapError struct{}``) is declared with zero
    fields and zero methods in the real, pinned source - it is a marker/sentinel type for
    errors.As dispatch, not a carrier of shared implementation. TC-261's
    _flatten_inheritance() copies a member down only when the parent actually declares one
    ("inherited_from" tags genuine parent-declared members - see api_surface.py); there is
    nothing on this real ancestor to copy, so correctly, zero members anywhere in this real
    fixture carry "inherited_from". This is accurate extraction of this repository's real
    shape, not evidence that TC-261's tagging is stale: a repository WITH a non-empty base
    class exercises that path elsewhere (e.g. pdf/go's own fixture and test).
    """
    data = _load_fixture()
    by_name = {entry["name"]: entry for entry in data["types"]}
    jmap_error = by_name["JmapError"]
    assert jmap_error["methods"] == []
    assert jmap_error["properties"] == []
    for entry in data["types"]:
        for member in entry["methods"] + entry["properties"]:
            assert member.get("inherited_from") is None
