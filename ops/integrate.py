"""Atomic integration of an accepted card onto main (redesign D7, DECISION_LOG 2026-10-04).

Integration was a hand-run sequence: cherry-pick the worker's commits, copy the status
line, rebuild state, then commit the evidence. Each step was a place for a mistake, and
they all happened: a fast-forward refused, a receipt landed on main without its code,
a status line was copied by hand. This module runs the whole sequence, and refuses before
it changes anything if a precondition fails.

Preconditions, all checked before the first write:
  1. The card has an accepted receipt.
  2. The receipt's head_rev is an ancestor of the worker branch, so the reviewed commit
     is really on the branch that was reviewed.
  3. The working tree has no uncommitted product paths (the same rule as commit-guard).
  4. Every commit to replay applies cleanly. A conflict aborts the cherry-pick sequence.

Nothing is pushed. Pushing publishes, and AGENTS.md reserves it for an accepted gate.
"""

from __future__ import annotations

import json
from pathlib import Path

import gatectl as G
import gateverify as V


class IntegrateRefused(RuntimeError):
    """A precondition failed. Nothing was written."""


def _replay_commits(head: str) -> list[str]:
    """Commits reachable from head that main does not have, oldest first."""
    rc, out, err = G.git("rev-list", "--reverse", head, "^main")
    if rc != 0:
        raise IntegrateRefused(f"cannot list commits to replay: {err}")
    return [c for c in out.split() if c]


_SUPERVISOR_OWNED = ("ops/", "plans/", "project/", "schemas/", "evidence/", "docs/", ".githooks/")


def _tree_is_clean_of_product_paths() -> tuple[bool, list[str]]:
    """Any uncommitted path outside supervisor-owned governance counts as product (commit-guard's rule)."""
    rc, out, _ = G.git("status", "--porcelain")
    dirty = []
    for line in out.splitlines():
        path = line[3:].strip().strip('"')
        if path.startswith(_SUPERVISOR_OWNED):
            continue
        dirty.append(path)
    return (not dirty), dirty


def _status_line_for(card_id: str, head: str, worktree: Path, main_status: Path) -> str | None:
    """The worker's own status line for this card, copied verbatim, or None if already present.

    The line is never rewritten. Its timestamp and commit hash are gatectl's own output.
    """
    src = worktree / "ops" / "status.jsonl"
    if not src.exists():
        return None
    existing = main_status.read_text(encoding="utf-8") if main_status.exists() else ""
    for raw in reversed(src.read_text(encoding="utf-8").splitlines()):
        line = raw.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("card") == card_id and rec.get("commit") == head:
            return None if line in existing else line
    return None


def plan(card_id: str) -> dict:
    """Check every precondition and return the plan. Writes nothing."""
    receipt_gate = None
    for gate in ("G0", "G1", "G2", "G3", "G4", "G5"):
        r = V.load_receipt(gate, card_id)
        if r is not None:
            receipt_gate, receipt = gate, r
            break
    if receipt_gate is None:
        raise IntegrateRefused(f"{card_id}: no receipt recorded")
    if not receipt.get("accepted"):
        raise IntegrateRefused(f"{card_id}: the receipt is not accepted; integrate only accepted work")

    head = receipt["head_rev"]
    branch = f"worker/{card_id}"
    rc, _, _ = G.git("rev-parse", "--verify", branch)
    if rc != 0:
        raise IntegrateRefused(f"{card_id}: branch {branch} does not exist")
    rc, _, _ = G.git("merge-base", "--is-ancestor", head, branch)
    if rc != 0:
        raise IntegrateRefused(f"{card_id}: reviewed commit {head[:12]} is not on {branch}")

    clean, dirty = _tree_is_clean_of_product_paths()
    if not clean:
        raise IntegrateRefused(f"{card_id}: uncommitted paths in the tree: {', '.join(dirty[:6])}")

    commits = _replay_commits(head)
    if not commits:
        raise IntegrateRefused(f"{card_id}: nothing to replay; {head[:12]} is already on main")

    worktree = G.REPO / ".gatectl-worktrees" / f"wt-{card_id}"
    status_line = _status_line_for(card_id, head, worktree, G.REPO / "ops" / "status.jsonl")
    return {
        "card": card_id,
        "gate": receipt_gate,
        "reviewed_head": head,
        "branch": branch,
        "commits_to_replay": commits,
        "status_line_to_copy": status_line,
    }


def integrate(card_id: str) -> dict:
    """Run the plan. Cherry-picks, copies the status line, rebuilds state, commits evidence."""
    p = plan(card_id)
    _, start, _ = G.git("rev-parse", "HEAD")
    applied = []
    for sha in p["commits_to_replay"]:
        rc, out, err = G.git("cherry-pick", sha)
        if rc != 0:
            # Undo every commit this call made, back to the start. --keep refuses to
            # discard anything uncommitted, so a failed rollback stops loudly instead.
            G.git("cherry-pick", "--abort")
            rc2, _, err2 = G.git("reset", "--keep", start)
            if rc2 != 0:
                raise IntegrateRefused(
                    f"{card_id}: cherry-pick of {sha[:12]} conflicted and the rollback to {start[:12]} "
                    f"failed. Main needs a manual look before anything else. {err2[:160]}"
                )
            raise IntegrateRefused(
                f"{card_id}: cherry-pick of {sha[:12]} conflicted after {len(applied)} commit(s); "
                f"rolled back to {start[:12]}. {err[:200]}"
            )
        applied.append(sha)

    main_status = G.REPO / "ops" / "status.jsonl"
    if p["status_line_to_copy"]:
        with main_status.open("a", encoding="utf-8") as fh:
            fh.write(p["status_line_to_copy"] + "\n")

    G.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    G.STATE_FILE.write_text(G.dump_yaml(V.rebuild_state()), encoding="utf-8")

    # Stage only paths that exist. A missing pathspec makes git abort the whole add, so
    # nothing would be staged and the commit would fail for a reason that looks unrelated.
    stage = [
        p_
        for p_ in (f"evidence/build/{p['gate']}/{card_id}", "project/state.yaml", "ops/status.jsonl")
        if (G.REPO / p_).exists()
    ]
    if stage:
        G.git("add", "--", *stage)
    msg = (
        f"chore(evidence): record {card_id} acceptance receipt, rebuild derived state ({p['gate']}/{card_id})\n\n"
        "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
    )
    rc, _, err = G.git("commit", "-m", msg)
    if rc != 0:
        raise IntegrateRefused(f"{card_id}: the evidence commit failed: {err[:200]}")
    rc, sha, _ = G.git("rev-parse", "--short", "HEAD")
    return {**p, "replayed": applied, "evidence_commit": sha}
