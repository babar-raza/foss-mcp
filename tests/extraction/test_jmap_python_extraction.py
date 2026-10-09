"""Offline tests against the committed jmap/python fixture - no network, no live clone.

``gatectl`` verifies this card with the network proxied to a dead port (TC-170 sets
``network: false``), so nothing here may reach out; everything is read from the fixture
``run_extraction.py`` already produced and committed at
``tests/fixtures/jmap_python/api_surface.json``.

JMAP is a genuinely distinct product line from the already-onboarded Email family
(config/products/email/python.yaml) - a separate repository under the same
aspose-email-foss org, not a variant of it.

Like slides/python (its structural template), this fixture was produced by the
independent pure-``ast`` reader (``foss_mcp.extraction.tree_sitter_engine.python_surface``,
adapted in ``run_extraction._python_types_from_surface``): its ``language`` field reads
literally ``"python"`` and its observed ``kind`` vocabulary across the full 71-entry
surface is only ``{"class"}`` - no ``"enum"`` or ``"function"`` entries exist in this
repository's public surface, and there is no tree-sitter grammar name here at all.

Regenerated under TC-321 (same commit f2137fa6bade0002c317e3a73081f2a0b5173a23 the
previous TC-170-era fixture already pinned - no commit-pin divergence) because that
fixture predated the entire bases/inheritance pipeline (TC-252/261/265/274/281/293/294):
every one of its types had ``bases: []``. This live re-run under the full pipeline now
populates 14 real non-empty ``bases`` lists - but ``_flatten_inheritance()`` still
copies zero ``inherited_from`` tags anywhere, hand-verified against this repository's
own real pinned source (``src/aspose_jmap_foss/``) as a genuine absence, not a stale or
broken run:

- ``JmapError``/``JmapNetworkError``/``JmapProtocolError`` (``models/common_types.py``)
  base to ``Exception``, a stdlib class outside this corpus entirely, and
  ``JmapError`` itself adds no method beyond ``__init__`` (which this pure-``ast``
  reader never captures as a "method" for any Python pilot - leading-underscore names
  are filtered before kind-specific handling).
- ``JmapTransport``/``UrllibJmapTransport`` (``models/transport.py``): the only member
  ``JmapTransport`` declares is the abstract ``send``, and ``UrllibJmapTransport``
  overrides (not additively inherits) that exact method - correctly deduplicated as a
  local override, never a copied inherited member.
- ``JmapClient`` (``__init__.py``) is composed from three real mixins -
  ``_CoreClientMixin`` (``client_core.py``), ``_MailClientMixin`` (``client_mail.py``),
  ``_SubmissionClientMixin`` (``client_submission.py``) - each carrying real public
  methods (``connect``, ``send_request``, ``list_mailboxes``, ``send``, etc.), but every
  one of those three names is leading-underscore and never exported under any public
  alias anywhere in the repository, so the engine's own public-surface filtering
  (``python_surface._public_name``) correctly excludes them from this fixture entirely -
  there is no public symbol for ``_flatten_inheritance`` to resolve a base reference
  against. This is the exact same structural pattern already confirmed and accepted,
  unmodified, for pdf/python's ``_BoundFacade``, html/python's
  ``_ConstraintValidationMixin``, words/python's ``_StyleMixin``/``_ListMixin``, and
  imaging/net's ``internal``-declared ``IFormatHandler`` implementers (see
  docs/DECISION_LOG.md's 2026-10-10 "genuinely flat" entry) - never previously flagged
  as a defect in any of those already-accepted pilots, and not one here either.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

FIXTURE = Path(__file__).parents[1] / "fixtures" / "jmap_python" / "api_surface.json"
MANIFEST = Path(__file__).parents[2] / "config" / "products" / "jmap" / "python.yaml"

_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_fixture_parses_and_has_the_expected_shape() -> None:
    data = _load_fixture()
    assert isinstance(data, dict)
    assert isinstance(data["types"], list)
    assert data["language"] == "python"


def test_the_fixture_records_the_exact_source_commit_and_repository() -> None:
    data = _load_fixture()
    assert _COMMIT_SHA.match(data["source_commit"]), data["source_commit"]
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert data["source_repository"] == manifest["repository"]


def test_the_fixture_is_non_empty() -> None:
    data = _load_fixture()
    assert len(data["types"]) > 0
    assert data["reduced_type_count"] == len(data["types"])
    assert data["type_count"] >= data["reduced_type_count"]


def test_the_fixture_contains_real_python_type_names() -> None:
    data = _load_fixture()
    names = {entry["name"] for entry in data["types"]}
    # Real Aspose.JMAP-FOSS-for-Python classes, not placeholders - would not
    # survive an empty or synthetic fixture.
    assert {"Account", "Mailbox", "EmailSubmission"} <= names
    assert all(entry["class_import"].startswith("aspose_jmap_foss") for entry in data["types"])
    assert all(entry["file"].endswith(".py") for entry in data["types"])


def test_every_entry_is_a_real_python_surface_kind() -> None:
    data = _load_fixture()
    kinds = {entry["kind"] for entry in data["types"]}
    # Observed directly from the live pure-ast reader's output for this repository - not
    # a tree-sitter grammar's node-type vocabulary, and not guessed.
    assert kinds == {"class"}


def test_the_fixture_now_carries_real_bases() -> None:
    data = _load_fixture()
    # TC-321: the pre-regeneration fixture had bases: [] on every single type (it predated
    # TC-265's real bases population entirely). This live re-run recovers 14 real non-empty
    # bases lists - observed directly, not guessed.
    by_class_import = {entry["class_import"]: entry for entry in data["types"]}
    with_bases = [entry for entry in data["types"] if entry.get("bases")]
    assert len(with_bases) == 14
    assert by_class_import["aspose_jmap_foss.JmapNetworkError"]["bases"] == ["JmapError"]
    assert by_class_import["aspose_jmap_foss.JmapProtocolError"]["bases"] == ["JmapError"]
    assert by_class_import["aspose_jmap_foss.UrllibJmapTransport"]["bases"] == ["JmapTransport"]
    assert by_class_import["aspose_jmap_foss.JmapClient"]["bases"] == [
        "_CoreClientMixin",
        "_MailClientMixin",
        "_SubmissionClientMixin",
    ]


def test_inherited_from_is_a_verified_absence_not_a_stale_or_broken_run() -> None:
    data = _load_fixture()
    # Falsifier guard matching the pdf/python (TC-281/TC-290) precedent's own
    # "test_inherited_from_tags_are_not_vacuous": a fixture that genuinely has zero
    # inherited_from tags must be distinguished from one where the pipeline silently
    # failed to run at all. Here that distinction is drawn by confirming the three real
    # mixin names JmapClient's own bases name are themselves never public anywhere in
    # this fixture - the exact reason _flatten_inheritance() has no base entry to copy
    # from, hand-verified against the real pinned source (see module docstring) rather
    # than assumed.
    names = {entry["name"] for entry in data["types"]}
    assert not ({"_CoreClientMixin", "_MailClientMixin", "_SubmissionClientMixin"} & names)
    total_inherited = sum(
        1
        for entry in data["types"]
        for member in (*entry.get("methods", []), *entry.get("properties", []))
        if "inherited_from" in member
    )
    assert total_inherited == 0
    # Guards against a regression on the other side too: JmapClient's own composition
    # bases must stay real (non-builtin) - an engine fix that someday resolves private
    # mixin bases should fail this test loudly rather than pass it silently vacuous.
    jmap_client = next(entry for entry in data["types"] if entry["name"] == "JmapClient")
    assert jmap_client["bases"] == ["_CoreClientMixin", "_MailClientMixin", "_SubmissionClientMixin"]
    assert jmap_client["methods"] == []
