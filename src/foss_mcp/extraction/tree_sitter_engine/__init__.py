"""foss_mcp.extraction.tree_sitter_engine — tree-sitter parsing backend.

See ``docs/REPOSITORY_LAYOUT.md`` for what belongs in this package and what
does not.

Provenance
----------
``api_surface.py``, ``tree_helpers.py``, ``package_manifest.py``,
``package_root.py`` and every module under ``lang/`` (including
``lang/python.py``) are ported, module-for-module, from
repository-presenter's vendored copy at commit ``16d75e95d4``
(``src/repository_presenter/components/readme/extractors/surface/_vendor/
aspose_extraction/``), itself originally vendored from aspose.org. The only
patch applied is the import path: every
``repository_presenter.components.readme.extractors.surface._vendor.aspose_extraction``
reference became ``foss_mcp.extraction.tree_sitter_engine``.

``python_surface.py`` is ported separately from repository-presenter's
production Python reader at the same commit
(``src/repository_presenter/components/readme/extractors/platforms/
python_surface.py``) — pure stdlib ``ast``, no tree-sitter involved. It is
the module actually used to extract a Python package's public surface;
``lang/python.py`` (the tree-sitter-family adapter) is carried along only as
a parity oracle and is never called for that purpose. Besides the import
path, the only change is that ``public_symbol_facts`` and ``_definition_of``
were dropped: they assemble presenter-specific ``Fact``/``Evidence``
records from a fact-graph module this repository does not have, which is a
caller's concern, not this package's (see ``docs/REPOSITORY_LAYOUT.md``).

``package_registries/`` and ``publication_probe.py``, also present in the
vendored source directory, are publication-status probes rather than
api-surface extraction and were not ported.
"""
