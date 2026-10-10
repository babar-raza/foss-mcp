"""verify_product_reference_install: the ingestion-time CLI that runs
infra/verify_package_registry.py's own checker against the live manifest sidecar
infra/fetch_product_reference.py already wrote for one pilot - the wiring TC-260's own module
docstring explicitly deferred to this card (G2/TC-275, C2 part 2, third independent audit,
2026-10-08).

Given one pilot's identity (``--family``, ``--platform``) and the shared ``--manifests-dir`` the
ingestion Job mounts read-write, this script:

1. Loads that identity's existing ``product_reference_<family>_<platform>.json`` sidecar via
   ``fetch_product_reference.load_manifest_sidecar``. Absent (this pilot's manifest-fetch step has
   not run yet, or never will for a pilot with no ``manifestPath``) -> print why and exit 0,
   writing nothing. Never an error.
2. Calls ``get_product_reference.install_coordinate_for_verification()`` on the resulting
   ``ProductReferenceInputs``. ``None`` (nothing this tool would ever serve as an install command
   for this pilot - no manifest field, cpp, or an unsupported platform) -> print why and exit 0,
   writing nothing.
3. Reuses ``verify_package_registry.py``'s own time-based freshness cache
   (``_sidecar_is_fresh``/``_SIDECAR_FRESHNESS_SECONDS``) against this pilot's OWN
   ``package_registry_<family>_<platform>.json`` sidecar path
   (``verify_package_registry_sidecar_name``) - a repeat run inside the freshness window is a
   no-op, exactly like that module's own ``main()``.
4. Otherwise calls the matching checker from ``verify_package_registry.py``'s own ``_CHECKERS``
   dict and writes the result through that module's OWN sidecar JSON shape (the same four keys
   ``load_package_registry_sidecar`` requires) - reused directly, never re-implemented.

Deliberately NOT wrapped in try/except around the checker call: a genuine transport/registry
failure must propagate exactly as ``verify_package_registry.py``'s own module docstring requires
of its callers. The Helm ``ingestion-job.yaml`` step that invokes this script is what tolerates
that failure, with the identical ``"|| true"`` brace-grouping the
``fetch_product_reference.py``/``fetch_recent_releases.py`` steps already use for the identical
reason: a transient registry/network failure must never fail the whole ingestion Job.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from fetch_product_reference import load_manifest_sidecar, sidecar_name
from verify_package_registry import (
    _CHECKERS,
    _SIDECAR_FRESHNESS_SECONDS,
    _sidecar_is_fresh,
    verify_package_registry_sidecar_name,
)

from foss_mcp.mcp.tools.get_product_reference import install_coordinate_for_verification


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument(
        "--manifests-dir",
        type=Path,
        required=True,
        help="the shared manifests claim both the product-reference sidecar is read from and the "
        "registry-verification sidecar is written to",
    )
    args = parser.parse_args()

    manifest_sidecar_path = args.manifests_dir / sidecar_name(args.family, args.platform)
    inputs = load_manifest_sidecar(manifest_sidecar_path)
    if inputs is None:
        print(
            f"nothing to verify for {args.family}/{args.platform}: no product reference sidecar "
            f"at {manifest_sidecar_path} yet"
        )
        return

    coordinate_pair = install_coordinate_for_verification(inputs)
    if coordinate_pair is None:
        print(
            f"nothing to verify for {args.family}/{args.platform}: no install coordinate to check "
            f"for platform {inputs.platform!r}"
        )
        return
    ecosystem, coordinate = coordinate_pair

    output = args.manifests_dir / verify_package_registry_sidecar_name(args.family, args.platform)
    if _sidecar_is_fresh(output):
        print(
            f"skipped verification for {coordinate} ({ecosystem}) -> {output}: "
            f"existing sidecar is less than {_SIDECAR_FRESHNESS_SECONDS} seconds old"
        )
        return

    checker = _CHECKERS[ecosystem]
    # Deliberately NOT wrapped in try/except: a genuine transport/registry failure must propagate
    # rather than being written as a false "verified: false" (see module docstring). The Helm
    # ingestion-job.yaml step that invokes this script is what tolerates that failure, with "|| true".
    verified = checker(coordinate)

    sidecar = {
        "ecosystem": ecosystem,
        "coordinate": coordinate,
        "verified": verified,
        "checked_at": datetime.now(UTC).isoformat(),
    }
    output.write_text(json.dumps(sidecar), encoding="utf-8")
    print(f"wrote verification result for {coordinate} ({ecosystem}) -> {output}: verified={verified}")


if __name__ == "__main__":
    main()
