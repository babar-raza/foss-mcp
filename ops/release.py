"""Release image records - the state machine's durable memory of the final standalone image.

REQ-G3-004 (DECISION_LOG 2026-10-11): the operator's lean standalone 40-pilot demo image must be
recorded in the state machine so it cannot be lost. This module is the whole of that mechanism:

* `record()` is what `gatectl release-record` runs. It inspects the real local image, checks that the
  pre-deployment workflow gate report (TC-345) is for THIS exact image id, passed, and covered every
  pilot, optionally folds in the registry push result (TC-347), and writes
  `evidence/release/<image>__<tag>.json` (validated against schemas/release-image.schema.json) plus a
  copy of the gate report beside it.
* `state_rows()` is the derived `release_images` section of project/state.yaml.
* `problems()` is what `gatectl validate` asserts about every record.
* `gate_exit_problems()` is what `gatectl gate-exit G3` additionally requires: at least one image that
  is PUSHED, gate-tested 40-of-40 and built from a commit that is in this history.

Nothing here is authored by a model: every field comes from `docker image inspect`, git, or a report a
tool wrote. A record is immutable per tag: re-recording the same tag with a different image id is refused.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

import gatectl as G

SCHEMA = "release-image.schema.json"
REVISION_LABEL = "org.opencontainers.image.revision"
REF_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]*:[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
GATE_REPORT_SUFFIX = ".gate-report.json"


class ReleaseRefused(Exception):
    """A precondition failed; nothing was written."""


def release_dir() -> Path:
    return G.EVIDENCE / "release"


def stem_for(reference: str) -> str:
    return reference.replace(":", "__").replace("/", "_")


def record_path(reference: str) -> Path:
    return release_dir() / f"{stem_for(reference)}.json"


def expected_pilot_count() -> int:
    data = G.load_yaml(G.REPO / "infra" / "helm" / "foss-mcp" / "values.yaml") or {}
    return len((data.get("ingestion") or {}).get("pilots") or [])


def inspect_image(reference: str) -> dict:
    """The facts `docker image inspect` reports for a local image, or ReleaseRefused."""
    rc, out, err = G.run(["docker", "image", "inspect", reference])
    if rc != 0:
        raise ReleaseRefused(f"docker cannot inspect {reference}: {(err or out).strip()[:200]}")
    try:
        info = json.loads(out)[0]
    except (ValueError, IndexError) as exc:
        raise ReleaseRefused(f"docker inspect of {reference} returned unreadable output: {exc}") from exc
    labels = (info.get("Config") or {}).get("Labels") or {}
    return {
        "image_id": info.get("Id", ""),
        "size_bytes": int(info.get("Size") or 0),
        "created": info.get("Created", ""),
        "source_commit": labels.get(REVISION_LABEL, ""),
    }


def commit_problem(sha: str) -> str | None:
    """None when `sha` is a commit in this repository's history, otherwise why not."""
    rc, out, _ = G.git("cat-file", "-t", sha)
    if rc != 0 or out.strip() != "commit":
        return f"source commit {sha[:12]} is not a commit in this repository"
    rc, _, _ = G.run(["git", "merge-base", "--is-ancestor", sha, "HEAD"])
    if rc != 0:
        return f"source commit {sha[:12]} is not an ancestor of HEAD"
    return None


def load_gate_report(path: Path) -> tuple[dict, str]:
    """(normalized fields, sha256 of the file). The report is the TC-345 image workflow gate's summary.json."""
    if not path.is_file():
        raise ReleaseRefused(f"gate report not found: {path}")
    raw = path.read_bytes()
    try:
        rep = json.loads(raw.decode("utf-8-sig"))
    except ValueError as exc:
        raise ReleaseRefused(f"gate report is not JSON: {exc}") from exc
    for key in ("image_id", "verdict", "pilots_total", "pilots_passed"):
        if key not in rep:
            raise ReleaseRefused(f"gate report lacks `{key}`")
    peaks: list[float] = []
    for pilot in rep.get("pilots") or []:
        if isinstance(pilot, dict) and isinstance(pilot.get("peak_memory_mb"), (int, float)):
            peaks.append(float(pilot["peak_memory_mb"]))
    out = {
        "verdict": rep["verdict"],
        "pilots_total": int(rep["pilots_total"]),
        "pilots_passed": int(rep["pilots_passed"]),
        "image_id": rep["image_id"],
    }
    if peaks:
        out["peak_memory_mb"] = max(peaks)
    budget = (rep.get("budget") or {}).get("memory")
    if budget:
        out["memory_budget"] = str(budget)
    return out, hashlib.sha256(raw).hexdigest()


def load_push_result(path: Path) -> dict:
    """The JSON `scripts/release/push_image.py --record` wrote (TC-347)."""
    if not path.is_file():
        raise ReleaseRefused(f"push result not found: {path}")
    try:
        res = json.loads(path.read_text(encoding="utf-8-sig"))
    except ValueError as exc:
        raise ReleaseRefused(f"push result is not JSON: {exc}") from exc
    digest = res.get("digest", "")
    if not DIGEST_RE.match(digest):
        raise ReleaseRefused(
            "push result has no valid sha256 digest - a push with no captured digest is not recorded"
        )
    ref = res.get("pushed_reference", "")
    if not ref:
        raise ReleaseRefused("push result has no pushed_reference")
    return {
        "reference": ref,
        "digest": digest,
        "pushed_at": res.get("timestamp") or G.now_utc(),
        "local_image_id": res.get("local_image_id", ""),
    }


def record(reference: str, gate_report: Path, push_result: Path | None = None) -> tuple[dict, Path]:
    """Write (or complete) the record for one image. Raises ReleaseRefused before writing anything."""
    if not REF_RE.match(reference) or reference.endswith(":latest"):
        raise ReleaseRefused(f"image reference needs an explicit tag other than latest: {reference!r}")
    info = inspect_image(reference)
    if not DIGEST_RE.match(info["image_id"]):
        raise ReleaseRefused(f"docker reported no usable image id for {reference}")
    if not re.fullmatch(r"[0-9a-f]{40}", info["source_commit"]):
        raise ReleaseRefused(
            f"{reference} carries no {REVISION_LABEL} label; build it with scripts/release/build_images.py"
        )
    bad = commit_problem(info["source_commit"])
    if bad:
        raise ReleaseRefused(bad)

    rep, rep_sha = load_gate_report(gate_report)
    if rep["image_id"] != info["image_id"]:
        raise ReleaseRefused(
            f"the gate report is for image {rep['image_id'][:19]}, not {info['image_id'][:19]}: a report for another image proves nothing about this one"
        )
    expected = expected_pilot_count()
    if (
        rep["verdict"] != "PASS"
        or rep["pilots_passed"] != rep["pilots_total"]
        or rep["pilots_total"] != expected
    ):
        raise ReleaseRefused(
            f"the gate report is {rep['verdict']} {rep['pilots_passed']}/{rep['pilots_total']}; need PASS {expected}/{expected}"
        )

    registry = None
    if push_result is not None:
        pr = load_push_result(push_result)
        if pr["local_image_id"] and pr["local_image_id"] != info["image_id"]:
            raise ReleaseRefused(
                "the push result is for a different local image id than the image being recorded"
            )
        registry = {"reference": pr["reference"], "digest": pr["digest"], "pushed_at": pr["pushed_at"]}

    path = record_path(reference)
    if path.exists():
        old = json.loads(path.read_text(encoding="utf-8"))
        if old["image"]["image_id"] != info["image_id"]:
            raise ReleaseRefused(
                f"{reference} is already recorded for a different image id; tags are immutable here - use a new tag"
            )
        if old.get("registry") and registry is None:
            registry = old["registry"]

    release_dir().mkdir(parents=True, exist_ok=True)
    copied = release_dir() / f"{stem_for(reference)}{GATE_REPORT_SUFFIX}"
    shutil.copyfile(gate_report, copied)
    doc = {
        "schema_version": 1,
        "generated_by": "gatectl",
        "recorded_at": G.now_utc(),
        "status": "PUSHED" if registry else "LOCAL_ONLY",
        "image": {
            "reference": reference,
            "image_id": info["image_id"],
            "size_bytes": info["size_bytes"],
            "source_commit": info["source_commit"],
            "created": info["created"],
        },
        "gate_report": {"file": copied.relative_to(G.REPO).as_posix(), "sha256": rep_sha, **rep},
        "registry": registry,
    }
    errs = G.schema_errors(G.load_schema(SCHEMA), doc)
    if errs:
        raise ReleaseRefused("record would not validate: " + "; ".join(errs[:3]))
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return doc, path


def load_records() -> list[tuple[Path, dict]]:
    d = release_dir()
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        if p.name.endswith(GATE_REPORT_SUFFIX):
            continue
        try:
            out.append((p, json.loads(p.read_text(encoding="utf-8"))))
        except ValueError:
            out.append((p, {}))
    return out


def state_rows() -> list[dict]:
    rows = []
    for p, d in load_records():
        if not d:
            continue
        img, gr, reg = d["image"], d["gate_report"], d.get("registry")
        rows.append(
            {
                "image": img["reference"],
                "image_id": img["image_id"],
                "source_commit": img["source_commit"],
                "size_mb": round(img["size_bytes"] / 1048576, 1),
                "status": d["status"],
                "gate_report": f"{gr['verdict']} {gr['pilots_passed']}/{gr['pilots_total']}",
                "registry_reference": reg["reference"] if reg else None,
                "registry_digest": reg["digest"] if reg else None,
                "record": p.relative_to(G.REPO).as_posix(),
            }
        )
    rows.sort(key=lambda r: (r["image"], r["record"]))
    return rows


def problems() -> list[str]:
    """What `gatectl validate` asserts about every record on disk."""
    out: list[str] = []
    schema = G.load_schema(SCHEMA)
    for p, d in load_records():
        rel = p.relative_to(G.REPO).as_posix()
        if not d:
            out.append(f"{rel}: not valid JSON")
            continue
        for e in G.schema_errors(schema, d):
            out.append(f"{rel}: {e}")
        if "image" not in d:
            continue
        if p.stem != stem_for(d["image"]["reference"]):
            out.append(f"{rel}: file name disagrees with the recorded image reference")
        bad = commit_problem(d["image"]["source_commit"])
        if bad:
            out.append(f"{rel}: {bad}")
        gr = d.get("gate_report") or {}
        gfile = G.REPO / gr.get("file", "")
        if not gr.get("file") or not gfile.is_file():
            out.append(f"{rel}: the copied gate report {gr.get('file')!r} is missing")
        elif hashlib.sha256(gfile.read_bytes()).hexdigest() != gr.get("sha256"):
            out.append(f"{rel}: the gate report's sha256 no longer matches the record")
        if d.get("status") == "PUSHED" and not d.get("registry"):
            out.append(f"{rel}: status PUSHED but no registry digest")
        if d.get("status") == "LOCAL_ONLY" and d.get("registry"):
            out.append(f"{rel}: status LOCAL_ONLY but a registry digest is recorded")
    return out


def gate_exit_problems(gate: str) -> list[str]:
    """What gate exit additionally requires. Only G3 carries the standalone-image requirement."""
    if gate != "G3":
        return []
    expected = expected_pilot_count()
    for _, d in load_records():
        if not d or d.get("status") != "PUSHED":
            continue
        gr = d["gate_report"]
        if gr["verdict"] == "PASS" and gr["pilots_passed"] == gr["pilots_total"] == expected:
            if commit_problem(d["image"]["source_commit"]) is None:
                return []
    return [
        f"no standalone demo image is recorded as PUSHED with a passing {expected}/{expected} workflow gate report "
        "(REQ-G3-001..004): build it, run scripts/release/image_workflow_gate.py, push it with "
        "scripts/release/push_image.py, then `gatectl release-record`"
    ]
