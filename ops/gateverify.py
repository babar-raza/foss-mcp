"""Verification engine and state derivation for gatectl.

Split from gatectl.py so the primitives (paths, scope, fingerprint) stay
readable on their own. Nothing here is imported by src/ - ops/ is supervisor
tooling and never counts toward a gate.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from gatectl import (
    EVIDENCE,
    GATES,
    INSTRUCTIONS_JSONL,
    OPS,
    QUESTIONS_JSONL,
    REPO,
    all_cards,
    check_scope,
    drop_worktree,
    ensure_shared_venv,
    fingerprint,
    load_card_at_rev,
    load_yaml,
    make_worktree,
    now_utc,
    parse_junit,
    run,
    sha256_bytes,
    sha256_file,
)

OWNER_FILE = OPS / "owner_items.yaml"


def receipt_dir(gate: str, card_id: str) -> Path:
    return EVIDENCE / "build" / gate / card_id


# --------------------------------------------------------------------------
# running a card's checks
# --------------------------------------------------------------------------
def run_checks(card, wt: Path, py: Path, log_lines):
    """Execute the card's declared checks once. Returns (checks[], all_passed)."""
    import os

    from gatectl import CANONICAL_ENV

    env = dict(CANONICAL_ENV)
    env["PATH"] = str(py.parent) + os.pathsep + os.environ.get("PATH", "")
    env["GATECTL_PYTHON"] = str(py)
    env["VIRTUAL_ENV"] = str(py.parent.parent)
    if not card.get("network", False):
        # Blunt but effective offline signal: proxies pointed at a dead port.
        env["HTTP_PROXY"] = env["HTTPS_PROXY"] = "http://127.0.0.1:9"
        env["GATECTL_OFFLINE"] = "1"

    results, all_passed = [], True
    for idx, chk in enumerate(card["checks"]):
        cmd = chk["command"].replace("{python}", str(py))
        junit = None
        if chk.get("junitxml"):
            junit = wt / f".junit-{idx}.xml"
            cmd = f'{cmd} "--junitxml={junit}"'
        cwd = wt / chk.get("cwd", ".")
        t0 = time.time()
        rc, out, err = run(cmd, cwd=cwd, env=env, timeout=chk.get("timeout_seconds", 600))
        dur = round(time.time() - t0, 3)
        log_lines.append(f"$ {cmd}\n--- stdout ---\n{out}\n--- stderr ---\n{err}\n")
        entry = {
            "command": chk["command"],
            "exit_code": rc,
            "duration_s": dur,
            "stdout_sha256": sha256_bytes((out + err).encode("utf-8", "replace")),
        }
        if junit:
            entry.update(parse_junit(junit))
        results.append(entry)
        if rc != 0:
            all_passed = False
    return results, all_passed


def structural_gate(card, checks):
    """Exit code 0 is a near-worthless predicate on its own.

    pytest exits 0 for an all-skipped run, an xfail sweep, or `assert True`.
    Where a check reports junit counts, demand real, unskipped tests.
    """
    problems = []
    min_tests = card.get("min_tests")
    for c in checks:
        if "tests" not in c:
            continue
        if min_tests is not None and c["tests"] < min_tests:
            problems.append(f"{c['command']}: collected {c['tests']} tests, card requires >= {min_tests}")
        if c.get("skipped", 0) > 0:
            problems.append(f"{c['command']}: {c['skipped']} skipped test(s); skips are not evidence")
        if c.get("errors", 0) > 0:
            problems.append(f"{c['command']}: {c['errors']} collection error(s)")
    return problems


# --------------------------------------------------------------------------
# verify
# --------------------------------------------------------------------------
def do_verify(card_id: str, base: str, head: str, issue_rev: str | None = None):
    """Run a card's checks for real and write the receipt.

    Order matters: clean run twice (flake detection), structural gate, then the
    negative-control patch, which MUST make the checks fail. If the checks still
    pass with the falsifier applied they prove nothing, and the card is rejected
    however green it looked.
    """
    issue_rev = issue_rev or head
    card, card_sha = load_card_at_rev(card_id, issue_rev)
    gate = card["gate"]
    outdir = receipt_dir(gate, card_id)
    outdir.mkdir(parents=True, exist_ok=True)

    ok_scope, changed, violations = check_scope(card, base, head)
    py = ensure_shared_venv()
    log_lines: list[str] = []
    runs: list[dict] = []
    nc_spec = card["negative_control"]
    nc_kind = "patch" if "patch" in nc_spec else "mutate"
    negctl = {
        "kind": nc_kind,
        "patch": nc_spec.get("patch") or nc_spec.get("mutate"),
        "patch_sha256": "0" * 64,
        "applied": False,
        "patch_applied_failed": False,
        "exit_codes": [],
    }
    structural: list[str] = []
    clean_passed = False

    wt = make_worktree(head)
    try:
        for i in (1, 2):
            log_lines.append(f"\n===== CLEAN RUN {i} =====\n")
            checks, passed = run_checks(card, wt, py, log_lines)
            runs.append({"run": i, "checks": checks, "all_passed": passed})
        structural = structural_gate(card, runs[0]["checks"])

        codes1 = [c["exit_code"] for c in runs[0]["checks"]]
        codes2 = [c["exit_code"] for c in runs[1]["checks"]]
        flaky = codes1 != codes2
        clean_passed = runs[0]["all_passed"] and runs[1]["all_passed"] and not flaky

        # ---- the falsifier ----
        # Break what the card built, then re-run its own checks. They MUST fail.
        # Only run this when the clean runs actually passed: falsifying an
        # already-red suite would prove nothing either way.
        if clean_passed:
            if nc_kind == "patch":
                patch = REPO / nc_spec["patch"]
                if patch.exists():
                    negctl["patch_sha256"] = sha256_file(patch)
                    rc, _, err = run(["git", "apply", "--whitespace=nowarn", str(patch)], cwd=wt)
                    applied, why = rc == 0, err
                else:
                    applied, why = False, f"patch missing: {patch}"
            else:
                cmd = nc_spec["mutate"]
                negctl["patch_sha256"] = sha256_bytes(cmd.encode("utf-8"))
                rc, out, err = run(cmd, cwd=wt, timeout=120)
                applied, why = rc == 0, (err or out)

            if applied:
                negctl["applied"] = True
                log_lines.append("\n===== NEGATIVE CONTROL (checks MUST now fail) =====\n")
                nchecks, npassed = run_checks(card, wt, py, log_lines)
                negctl["exit_codes"] = [c["exit_code"] for c in nchecks]
                negctl["patch_applied_failed"] = not npassed
            else:
                log_lines.append(f"\n!! falsifier did not apply ({nc_kind}): {why}\n")
        else:
            log_lines.append("\n!! clean runs failed; falsifier skipped (it would prove nothing)\n")
    finally:
        drop_worktree(wt)

    (outdir / "stdout.log").write_text("".join(log_lines), encoding="utf-8")

    reasons = []
    if not ok_scope:
        reasons.append("scope violation: " + "; ".join(violations[:5]))
    if not clean_passed:
        if runs and runs[0]["all_passed"] != runs[1]["all_passed"]:
            reasons.append("FLAKY: the two clean runs disagreed")
        elif runs and not runs[0]["all_passed"]:
            failed = [c["command"] for c in runs[0]["checks"] if c["exit_code"] != 0]
            reasons.append("checks failed: " + "; ".join(failed))
        else:
            reasons.append("clean checks did not pass identically twice")
    if structural:
        reasons.append("structural gate: " + "; ".join(structural))
    if not negctl["applied"]:
        reasons.append("negative-control patch did not apply")
    elif not negctl["patch_applied_failed"]:
        reasons.append("VACUOUS CHECKS: the suite still passed with the falsifier applied")

    receipt = {
        "schema_version": 1,
        "card": card_id,
        "gate": gate,
        "generated_by": "gatectl",
        "generated_at": now_utc(),
        "base_rev": base,
        "head_rev": head,
        "card_sha256": card_sha,
        "fingerprint": fingerprint(),
        "scope": {"ok": ok_scope, "changed_paths": changed, "violations": violations},
        "runs": runs,
        "negative_control": negctl,
        "accepted": len(reasons) == 0,
        "reason": "; ".join(reasons) if reasons else "all gates passed",
    }
    (outdir / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


# --------------------------------------------------------------------------
# derived state
# --------------------------------------------------------------------------
def read_jsonl(p: Path):
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def load_receipt(gate: str, card_id: str):
    f = receipt_dir(gate, card_id) / "receipt.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def load_owner_items():
    if not OWNER_FILE.exists():
        return []
    data = load_yaml(OWNER_FILE)
    return data.get("owner_items", []) if isinstance(data, dict) else []


def rebuild_state():
    """project/state.yaml is a FUNCTION of primary evidence, never a narration.

    Inputs: the cards on disk, the receipts gatectl wrote, and the dispatch log.
    `validate` asserts the committed file equals this rebuild, so drift in the
    status authority is impossible rather than merely discouraged.
    """
    cards = all_cards()
    instructions = read_jsonl(INSTRUCTIONS_JSONL)
    questions = read_jsonl(QUESTIONS_JSONL)

    attempts: dict[str, int] = {}
    dispatched: set[str] = set()
    for ins in instructions:
        t = ins.get("target_card")
        if t and t != "ALL":
            attempts[t] = max(attempts.get(t, 0), int(ins.get("attempt", 0)))
            if ins.get("kind") in ("dispatch", "rework"):
                dispatched.add(t)

    rows, accepted_ids = [], set()
    for cid in sorted(cards):
        c = cards[cid]
        r = load_receipt(c["gate"], cid)
        row = {
            "id": cid,
            "gate": c["gate"],
            "status": "PENDING",
            "attempt": attempts.get(cid, 0),
            "blocker": None,
        }
        if r and r.get("accepted"):
            row["status"] = "ACCEPTED"
            row["commit"] = r["head_rev"]
            row["receipt"] = f"{r['gate']}/{cid}/receipt.json"
            accepted_ids.add(cid)
        elif r and not r.get("accepted"):
            if row["attempt"] >= 3:
                row["status"] = "FAILED_INTERNAL"
                row["blocker"] = {
                    "class": "FAILED_INTERNAL",
                    "summary": r.get("reason", "verification failed"),
                    "resume_predicate": f"a new commit for {cid} passes `gatectl verify {cid}`",
                    "recorded_at": r.get("generated_at", now_utc()),
                }
            else:
                row["status"] = "IN_PROGRESS"
        elif cid in dispatched:
            row["status"] = "IN_PROGRESS"
        rows.append(row)

    for row in rows:
        if row["status"] == "PENDING":
            deps = cards[row["id"]].get("depends_on", [])
            if all(d in accepted_ids for d in deps):
                row["status"] = "READY"

    gates_present = {c["gate"] for c in cards.values()}
    accepted_gates = []
    for g in GATES:
        if g in gates_present:
            ids = [r for r in rows if r["gate"] == g]
            if ids and all(r["status"] == "ACCEPTED" for r in ids):
                accepted_gates.append(g)

    current = next((g for g in GATES if g in gates_present and g not in accepted_gates), GATES[0])
    cg_rows = [r for r in rows if r["gate"] == current]
    if cg_rows and all(r["status"] == "ACCEPTED" for r in cg_rows):
        cg_status = "ACCEPTED"
    elif any(r["status"] in ("IN_PROGRESS", "VERIFYING") for r in cg_rows):
        cg_status = "IN_PROGRESS"
    elif any(r["status"] == "FAILED_INTERNAL" for r in cg_rows):
        cg_status = "FAILED_INTERNAL"
    else:
        cg_status = "READY"

    open_q = [q["id"] for q in questions if q.get("status") == "OPEN"]
    return {
        "schema_version": 1,
        "project": "foss-mcp",
        "generated_by": "gatectl",
        "generated_at": now_utc(),
        "current_gate": {"id": current, "status": cg_status},
        "accepted_gates": accepted_gates,
        "cards": rows,
        "owner_items": load_owner_items(),
        "open_questions": {"open": len(open_q), "ids": sorted(open_q)},
        "execution_limits": {
            "workers_in_flight": 1,
            "max_attempts_per_card": 3,
            "supervisor_writes_product_code": False,
            "worker_issues_verdicts": False,
        },
    }


def ready_cards(state):
    return [r["id"] for r in state["cards"] if r["status"] == "READY"]


def next_card(state):
    """Exactly ONE card id, deterministically.

    The DAG makes an ordering legal; it does not make it determined. Two runs of
    the same plan must dispatch the same card, so: topological by gate, then
    ascending id. accept() refuses any card that was not this function's output.
    """
    ready = []
    for g in GATES:
        ready += sorted(r["id"] for r in state["cards"] if r["status"] == "READY" and r["gate"] == g)
        if ready:
            break
    return ready[0] if ready else None
