"""The fourteen pilot releases of the foss-mcp Helm chart (G2/TC-223).

A release serves one pilot. Its values set the deployment identity (family, platform, sourceKind)
and replace the chart's ingestion.pilots list with that pilot's single entry; a list replaces the
chart default as a whole, so the release runs one ingestion Job and one serving pod.

Every function here is pure: this module imports neither helm nor kubectl, so the tests and the
two runner tools can import it offline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CHART_VALUES = REPO_ROOT / "infra" / "helm" / "foss-mcp" / "values.yaml"
RELEASE_PREFIX = "foss-mcp-"
PILOT_COUNT = 40


def load_pilots(values_path: Path = CHART_VALUES) -> list[dict[str, Any]]:
    """The chart's ingestion.pilots entries, in the order the chart declares them."""
    values = yaml.safe_load(Path(values_path).read_text(encoding="utf-8")) or {}
    ingestion = values.get("ingestion")
    pilots = ingestion.get("pilots") if isinstance(ingestion, dict) else None
    if not isinstance(pilots, list) or not pilots:
        raise ValueError(f"{values_path} declares no ingestion.pilots")
    return [dict(pilot) for pilot in pilots]


def pilot_key(pilot: dict[str, Any]) -> str:
    """The pilot's name in family_platform form, e.g. pdf_net. This is the name --only takes."""
    return f"{pilot['family']}_{pilot['platform']}"


def release_name(pilot: dict[str, Any]) -> str:
    """The Helm release name: the prefix, then family, a hyphen and platform."""
    return f"{RELEASE_PREFIX}{pilot['family']}-{pilot['platform']}"


def pilot_values(pilot: dict[str, Any]) -> dict[str, Any]:
    """The minimal values for one release: its deployment identity and its one pilot entry."""
    return {
        "deployment": {
            "family": pilot["family"],
            "platform": pilot["platform"],
            "sourceKind": pilot["sourceKind"],
        },
        "ingestion": {"pilots": [dict(pilot)]},
    }


def write_values(pilots: list[dict[str, Any]], directory: Path) -> list[Path]:
    """Write one values file per pilot into directory, named family_platform.yaml."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for pilot in pilots:
        path = directory / f"{pilot_key(pilot)}.yaml"
        text = yaml.safe_dump(pilot_values(pilot), sort_keys=False)
        path.write_text(text, encoding="utf-8")
        paths.append(path)
    return paths


def select_pilots(pilots: list[dict[str, Any]], only: list[str] | None) -> list[dict[str, Any]]:
    """The pilots named by only (family_platform each), or all of them when only is empty.

    An unknown name raises ValueError, so a typo can never silently select nothing.
    """
    if not only:
        return list(pilots)
    known = {pilot_key(pilot): pilot for pilot in pilots}
    unknown = [name for name in only if name not in known]
    if unknown:
        raise ValueError(f"unknown pilot(s): {', '.join(unknown)}; known: {', '.join(sorted(known))}")
    wanted = set(only)
    return [pilot for pilot in pilots if pilot_key(pilot) in wanted]
