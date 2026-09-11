"""resolve_scope: product identity comes from deployment config alone, never the request."""

from __future__ import annotations

from foss_mcp.mcp.routing import DeploymentConfig, Scope, resolve_scope


class _ExplodingRequest:
    """Any attribute or item access is a bug: resolve_scope must never touch this."""

    def __getattr__(self, name: str) -> None:
        raise AssertionError(f"resolve_scope must never read request.{name}")

    def __getitem__(self, key: object) -> None:
        raise AssertionError(f"resolve_scope must never read request[{key!r}]")


def test_scope_comes_from_deployment_config_alone() -> None:
    config = DeploymentConfig(family="pdf", platform="net")
    scope = resolve_scope(config, request={"scope": "irrelevant"})
    assert scope == Scope(family="pdf", platform="net", source_kind="self_extracted")


def test_a_hostile_scope_supplied_in_the_request_is_ignored() -> None:
    """The exact confirmed reference-system defect: a client-controlled scope header (or a
    tool argument, or any other field of the request) naming a DIFFERENT product entirely.
    """
    config = DeploymentConfig(family="pdf", platform="net", source_kind="self_extracted")
    hostile_request = {
        "scope": "cells::python::self_extracted",
        "headers": {"X-Scope": "cells::python::self_extracted"},
        "tool_arguments": {"scope": "cells::python::self_extracted"},
    }
    scope = resolve_scope(config, hostile_request)
    assert scope == Scope(family="pdf", platform="net", source_kind="self_extracted")


def test_resolve_scope_never_reads_any_attribute_or_item_of_the_request() -> None:
    """The strongest form of the rule: a request object that raises on ANY access still
    resolves correctly, because resolve_scope never touches it at all.
    """
    config = DeploymentConfig(family="pdf", platform="net")
    scope = resolve_scope(config, _ExplodingRequest())
    assert scope == Scope(family="pdf", platform="net", source_kind="self_extracted")


def test_two_different_deployments_get_two_different_scopes() -> None:
    pdf_scope = resolve_scope(DeploymentConfig(family="pdf", platform="net"), request=None)
    cells_scope = resolve_scope(DeploymentConfig(family="cells", platform="python"), request=None)
    assert pdf_scope != cells_scope
