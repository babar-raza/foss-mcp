"""gatectl command surface.

Every verdict in this system is one of these exit codes. In particular `accept`
is a pure table lookup over gatectl's own computations - no model decides
whether a card passed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import gatectl as G
import gateverify as V

SCHEMA_FOR = {
    "taskcard": "taskcard.schema.json",
    "state": "state.schema.json",
    "status": "status-line.schema.json",
    "instruction": "instruction-line.schema.json",
    "receipt": "receipt.schema.json",
    "question": "open-question.schema.json",
}


# --------------------------------------------------------------------------
# validate
# --------------------------------------------------------------------------
def cmd_validate(args) -> int:
    problems: list[str] = []
    cards = G.all_cards()

    # 1. every card validates, declares only legal paths, and never claims a
    #    globally denied path (enforced at AUTHORING time, before any worker
    #    is spawned - not after the damage).
    card_schema = G.load_schema(SCHEMA_FOR["taskcard"])
    for cid, c in sorted(cards.items()):
        for e in G.schema_errors(card_schema, c):
            problems.append(f"{cid}: {e}")
        if c.get("id") != cid:
            problems.append(f"{cid}: id field disagrees with filename")
        for wp in c.get("write_paths", []):
            if G.matches_any(wp, G.GLOBAL_DENY):
                problems.append(f"{cid}: write_paths declares globally denied path {wp!r}")
            for reason in G.illegal_path_reasons(wp):
                problems.append(f"{cid}: write_path {wp!r} {reason}")

    # 2. the DAG must be acyclic and fully resolvable
    for cid, c in cards.items():
        for d in c.get("depends_on", []):
            if d not in cards:
                problems.append(f"{cid}: depends on unknown card {d}")
    problems.extend(_cycle_problems(cards))

    # 3. no two non-terminal cards may own overlapping write_paths
    problems.extend(_overlap_problems(cards))

    # 4. case-only collisions are invisible on this repo (core.ignorecase=true)
    seen: dict[str, str] = {}
    for cid, c in sorted(cards.items()):
        for wp in c.get("write_paths", []):
            k = G.norm_path(wp).casefold()
            if k in seen and seen[k] != wp:
                problems.append(f"{cid}: write_path {wp!r} collides case-only with {seen[k]!r}")
            seen[k] = wp

    # 5. channel lines and questions validate line by line
    problems.extend(_jsonl_problems(G.STATUS_JSONL, SCHEMA_FOR["status"], "status"))
    problems.extend(_jsonl_problems(G.INSTRUCTIONS_JSONL, SCHEMA_FOR["instruction"], "instruction"))
    problems.extend(_jsonl_problems(G.QUESTIONS_JSONL, SCHEMA_FOR["question"], "question"))

    # 6. receipts validate
    receipt_schema = G.load_schema(SCHEMA_FOR["receipt"])
    for f in sorted((G.EVIDENCE / "build").rglob("receipt.json")):
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            problems.append(f"{f}: unparseable ({exc})")
            continue
        for e in G.schema_errors(receipt_schema, r):
            problems.append(f"{f}: {e}")

    # 7. source_refs must resolve, and the plan must not have moved under an
    #    issued card (bootstrap-fidelity control F2)
    problems.extend(_source_ref_problems(cards))

    # 8. REQ coverage (bootstrap-fidelity control F1) - catches OMISSION, which
    #    a per-card review structurally cannot see.
    problems.extend(_req_problems(cards))

    # 9. the derived status authority must equal a fresh rebuild
    rebuilt = V.rebuild_state()
    state_schema = G.load_schema(SCHEMA_FOR["state"])
    for e in G.schema_errors(state_schema, rebuilt):
        problems.append(f"rebuilt state: {e}")
    if G.STATE_FILE.exists():
        committed = G.load_yaml(G.STATE_FILE)
        if _state_core(committed) != _state_core(rebuilt):
            problems.append(
                "project/state.yaml does not match a fresh rebuild from receipts+git "
                "(run `gatectl state --write`); the status authority must never be hand-edited"
            )
    elif cards:
        problems.append("project/state.yaml is missing (run `gatectl state --write`)")

    # 10. every schema-declared budget checked against the REAL artifact.
    #     A const nothing reads is a lie with a certificate.
    problems.extend(_budget_problems())

    for p in problems:
        print(f"FAIL {p}")
    if problems:
        print(f"\n{len(problems)} problem(s)")
        return G.EXIT_FAIL
    print(f"OK  {len(cards)} card(s), state matches rebuild, all artifacts valid")
    return G.EXIT_OK


def _state_core(s):
    """Compare everything except the generation timestamp."""
    return {k: v for k, v in s.items() if k != "generated_at"} if isinstance(s, dict) else s


def _cycle_problems(cards):
    colour: dict[str, int] = {}
    out: list[str] = []

    def visit(n, stack):
        if colour.get(n) == 2:
            return
        if colour.get(n) == 1:
            out.append(f"dependency cycle: {' -> '.join([*stack, n])}")
            return
        colour[n] = 1
        for d in cards.get(n, {}).get("depends_on", []):
            if d in cards:
                visit(d, [*stack, n])
        colour[n] = 2

    for cid in sorted(cards):
        visit(cid, [])
    return out


def _ancestors(cards, cid, seen=None):
    """Every card `cid` transitively depends on."""
    seen = seen if seen is not None else set()
    for d in cards.get(cid, {}).get("depends_on", []):
        if d in cards and d not in seen:
            seen.add(d)
            _ancestors(cards, d, seen)
    return seen


def _overlap_problems(cards):
    """Overlapping write_paths are a race - but only between cards that can be
    in flight at the same time.

    Two cards ordered by a dependency edge can never race: the later one starts
    only after the earlier is ACCEPTED. So a skeleton card owning `src/pkg/**`
    and a later card owning `src/pkg/indexing/**` is correct design, not a
    conflict. Flagging it anyway would push card authors into contorted,
    file-by-file path lists for no safety gain.
    """
    out = []
    ids = sorted(cards)
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            if b in _ancestors(cards, a) or a in _ancestors(cards, b):
                continue  # dependency-ordered: they cannot overlap in time
            for x in cards[a].get("write_paths", []):
                for y in cards[b].get("write_paths", []):
                    nx, ny = G.norm_path(x).casefold(), G.norm_path(y).casefold()
                    if nx == ny or G.path_matches(y.rstrip("/*"), x) or G.path_matches(x.rstrip("/*"), y):
                        out.append(f"{a} and {b} both claim overlapping write_paths ({x} / {y})")
    return out


def _jsonl_problems(path: Path, schema_name: str, label: str):
    if not path.exists():
        return []
    schema = G.load_schema(schema_name)
    out, prev_ts = [], ""
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            out.append(f"{path.name}:{n}: unparseable ({exc})")
            continue
        for e in G.schema_errors(schema, obj):
            out.append(f"{path.name}:{n}: {e}")
        ts = obj.get("ts", "")
        if ts and prev_ts and ts < prev_ts:
            out.append(f"{path.name}:{n}: timestamp {ts} goes backwards (after {prev_ts})")
        prev_ts = ts or prev_ts
    return out


def _source_ref_problems(cards):
    out = []
    for cid, c in sorted(cards.items()):
        for ref in c.get("source_refs", []):
            f = Path(ref["file"])
            if not f.is_absolute():
                f = G.REPO / f
            if not f.exists():
                out.append(f"{cid}: source_ref file does not exist: {ref['file']}")
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
            if ref["anchor"] not in text:
                out.append(f"{cid}: source_ref anchor not found in {ref['file']}: {ref['anchor']!r}")
                continue
            digest = G.sha256_bytes(_section_bytes(text, ref["anchor"]))
            if digest != ref["sha256"]:
                out.append(
                    f"{cid}: source_ref {ref['anchor']!r} has changed since this card was written "
                    f"(expected {ref['sha256'][:12]}, got {digest[:12]}) - re-read the section "
                    f"and re-issue the card rather than updating the hash"
                )
    return out


def _section_bytes(text: str, anchor: str) -> bytes:
    """Bytes of the section starting at `anchor` up to the next heading."""
    i = text.index(anchor)
    rest = text[i:]
    lines = rest.splitlines()
    body = [lines[0]]
    for ln in lines[1:]:
        if ln.startswith("#") or ln.startswith("### "):
            break
        body.append(ln)
    return "\n".join(body).encode("utf-8")


def _req_problems(cards):
    reqs_file = G.OPS / "requirements.yaml"
    if not reqs_file.exists():
        return []
    data = G.load_yaml(reqs_file) or {}
    reqs = {r["id"]: r for r in data.get("requirements", [])}
    out = []
    claimed: dict[str, list[str]] = {}
    proven: set[str] = set()
    for cid, c in sorted(cards.items()):
        for r in c.get("satisfies", []):
            if r not in reqs:
                out.append(f"{cid}: satisfies unknown requirement {r}")
            claimed.setdefault(r, []).append(cid)
        for chk in c.get("checks", []):
            for r in chk.get("proves", []):
                if r not in reqs:
                    out.append(f"{cid}: a check proves unknown requirement {r}")
                proven.add(r)
    for rid, r in sorted(reqs.items()):
        if rid not in claimed:
            out.append(f"{rid} ({r.get('gate')}) is claimed by no card - a missing REQ is a missing card")
        elif rid not in proven:
            out.append(f"{rid} is claimed by {claimed[rid]} but no check names it in `proves`")
    return out


def _budget_problems():
    """Walk schema-declared limits and assert them against the real file."""
    out = []
    budgets = {
        G.REPO / "AGENTS.md": 200,
    }
    for path, maxlines in budgets.items():
        if path.exists():
            n = len(path.read_text(encoding="utf-8").splitlines())
            if n > maxlines:
                out.append(f"{path.name} is {n} lines, budget is {maxlines}")
    return out


def _dispatch_rev_for(card_id: str):
    """The revision this card was last dispatched at.

    The supervisor should not have to remember a base SHA per card - getting it
    wrong produced two spurious verdicts already (an attempt-1 base reused for
    attempt 2 swept the supervisor's own governance commits into the diff and
    reported a scope violation that was not the worker's). The dispatch log
    already records it, so read it from there.
    """
    rev = None
    for ins in V.read_jsonl(G.INSTRUCTIONS_JSONL):
        if ins.get("target_card") == card_id and ins.get("kind") in ("dispatch", "rework"):
            rev = ins.get("issue_rev")
    return rev if rev and rev != "NONE" else None


def _resolve_base_and_issue(args):
    """Fill --base / --issue-rev from the dispatch log when not given."""
    base = getattr(args, "base", None)
    issue = getattr(args, "issue_rev", None)
    logged = _dispatch_rev_for(args.card)
    if not base:
        if not logged:
            G.die(f"no dispatch recorded for {args.card}; pass --base explicitly")
        base = logged
    if not issue:
        # Read the card as it stands on the current branch tip, which is where
        # a supervisor correction to the card itself would live.
        rc, head, _ = G.git("rev-parse", "HEAD")
        issue = _last_rev_touching(f"plans/{args.card}.yaml") or head
    return base, issue


def _last_rev_touching(path: str):
    rc, out, _ = G.git("log", "-1", "--format=%H", "--", path)
    return out.strip() or None


# --------------------------------------------------------------------------
# the worker loop's single decision point
# --------------------------------------------------------------------------
def _open_dispatch():
    """The dispatch the worker still owes work for, if any.

    A dispatch is OPEN when the latest dispatch/rework instruction for a card
    has no matching `committed` status line at that same attempt. This is the
    whole handshake: two append-only files, one writer each, and no shared
    mutable state to race over.
    """
    latest = {}
    for ins in V.read_jsonl(G.INSTRUCTIONS_JSONL):
        if ins.get("kind") in ("dispatch", "rework") and ins.get("target_card") != "ALL":
            latest[ins["target_card"]] = ins
    if not latest:
        return None
    ins = max(latest.values(), key=lambda i: i["ts"])

    done = any(
        st.get("card") == ins["target_card"]
        and st.get("attempt") == ins["attempt"]
        and st.get("phase") in ("committed", "blocked")
        for st in V.read_jsonl(G.STATUS_JSONL)
    )
    return None if done else ins


def cmd_worker_tick(args) -> int:
    """Print exactly one instruction for the worker loop, then exit.

    The worker never decides what to work on, never verifies, and never
    accepts. It does the work named here and stops. Verification authority
    stays with the supervisor - a loop that graded its own homework would
    forfeit the one property this design rests on.
    """
    state = V.rebuild_state()
    cards = state["cards"]

    if cards and all(c["status"] == "ACCEPTED" for c in cards) and not state["open_questions"]["open"]:
        print("DONE")
        print("Every card is ACCEPTED and no open question remains. Stop the loop.")
        return G.EXIT_OK

    ins = _open_dispatch()
    if ins is None:
        print("WAIT")
        blocked = [c["id"] for c in cards if c["status"] in ("FAILED_INTERNAL", "BLOCKED_EXTERNAL")]
        if blocked:
            print(f"No open dispatch. Cards needing supervisor attention: {blocked}")
        else:
            print("No open dispatch. Your last commit is awaiting supervisor verification,")
            print("or the supervisor has not dispatched the next card yet. Do no work.")
        return G.EXIT_OK

    card_id = ins["target_card"]
    print(f"WORK {card_id} attempt={ins['attempt']}")
    print(f"card file    : plans/{card_id}.yaml")
    print(f"card_sha256  : {ins['card_sha256'][:16]}")
    print(f"dispatched at: {ins['issue_rev'][:12]}")
    print()
    print("SUPERVISOR INSTRUCTION:")
    print(f"  {ins['instruction']}")

    # Corrections issued AFTER the dispatch must reach the worker too. Without
    # this only dispatch/rework lines were ever surfaced, so a mid-flight
    # root_cause - a card defect the supervisor found before it bit - would sit
    # in the channel unread until the card had already failed once.
    later = [
        i
        for i in V.read_jsonl(G.INSTRUCTIONS_JSONL)
        if i["ts"] > ins["ts"]
        and i.get("kind") in ("root_cause", "stop")
        and i.get("target_card") in (card_id, "ALL")
    ]
    for i in later:
        print()
        print(f"LATER {i['kind'].upper()} ({i['ts']}):")
        print(f"  {i['instruction']}")
    return G.EXIT_OK


def cmd_dispatch_next(args) -> int:
    """Supervisor: dispatch the deterministic next card in one step."""
    state = V.rebuild_state()
    card_id = args.card or V.next_card(state)
    if not card_id:
        print("no READY card to dispatch")
        return G.EXIT_FAIL
    ns = argparse.Namespace(
        target=card_id,
        kind=args.kind,
        instruction=args.instruction
        or (
            f"Execute plans/{card_id}.yaml exactly. Write only inside its write_paths. "
            "You do not decide whether your card passed."
        ),
        attempt=args.attempt,
    )
    return cmd_instruct(ns)


def cmd_question_append(args) -> int:
    """Open a provisional decision. Work continues; gate exit does not.

    This is how "no human in the loop" survives contact with a question nobody
    present can answer: the loop records what it decided and why, keeps going,
    and the debt is collected at a gate boundary in daylight instead of stalling
    at 3am.
    """
    existing = V.read_jsonl(G.QUESTIONS_JSONL)
    nid = f"OQ-{len(existing) + 1:03d}"
    line = {
        "ts": G.now_utc(),
        "id": nid,
        "card": args.card,
        "question": args.question,
        "provisional_decision": args.decision,
        "rationale": args.rationale,
        "status": "OPEN",
        "consumed_by": args.consumed_by.split(","),
    }
    errs = G.schema_errors(G.load_schema(SCHEMA_FOR["question"]), line)
    if errs:
        for e in errs:
            print(f"FAIL {e}")
        return G.EXIT_FAIL
    G.QUESTIONS_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with G.QUESTIONS_JSONL.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")
    G.STATE_FILE.write_text(G.dump_yaml(V.rebuild_state()), encoding="utf-8")
    print(f"opened {nid} (blocks gate exit for {line['consumed_by']}, blocks no work)")
    return G.EXIT_OK


def cmd_holdout_check(args) -> int:
    """Run a holdout against the WORKING TREE before it is ever wired into a verdict.

    Holdouts are the one thing in this system nothing else checks. They are
    supervisor-authored, unreviewed, and they can reject a card outright - so a
    bug in a holdout is indistinguishable, from the worker's side, from a real
    defect in its own work. Three consecutive bugs in one holdout (wrong
    constructor, wrong field names, invalid enum value) is what made this a
    command rather than a habit.

    Green here does not mean the card is right. It means the ORACLE runs, so a
    red result during verification is a real finding rather than my typo.
    """
    src = G.EVIDENCE / "holdout" / args.card
    if not src.is_dir():
        print(f"no holdout at {src.relative_to(G.REPO)}")
        return G.EXIT_FAIL
    py = G.venv_python()
    rc, out, err = G.run([str(py), "-m", "pytest", str(src), "-q", "-p", "no:cacheprovider"], timeout=600)
    print(out[-1500:] or err[-1500:])
    if rc == 0:
        print(f"HOLDOUT OK - {args.card}'s oracle runs clean against the working tree")
    else:
        print(f"HOLDOUT BROKEN - fix the oracle before trusting any verdict it produces (exit {rc})")
    return rc


def cmd_review(args) -> int:
    """The supervisor's whole per-card action: verify, then accept or reject.

    Deliberately one command producing a bounded verdict. The dominant cost in
    this system is not the workers, it is a supervisor re-reading context every
    few minutes, so the review surface is a short program output rather than a
    reasoning task over raw logs.
    """
    base, issue = _resolve_base_and_issue(args)
    r = V.do_verify(args.card, base, args.head, issue)
    print(f"--- {args.card} ---")
    print(f"base         : {base[:12]}   card as of: {issue[:12]}")
    print(
        f"scope        : {'ok' if r['scope']['ok'] else 'VIOLATION'} "
        f"({len(r['scope']['changed_paths'])} paths)"
    )
    for v in r["scope"]["violations"][:5]:
        print(f"  ! {v}")
    for run_ in r["runs"]:
        c0 = run_["checks"][0]
        counts = (
            f" tests={c0['tests']} skipped={c0['skipped']} failures={c0['failures']}" if "tests" in c0 else ""
        )
        print(f"clean run {run_['run']}  : exit {[c['exit_code'] for c in run_['checks']]}{counts}")
    nc = r["negative_control"]
    print(
        f"falsifier    : {nc['kind']} applied={nc['applied']} made_checks_fail={nc['patch_applied_failed']}"
    )
    print(f"verdict      : {r['reason']}")
    if not r["accepted"]:
        print(f"REWORK REQUIRED — see evidence/build/{r['gate']}/{args.card}/stdout.log")
        return G.EXIT_FAIL
    args_accept = argparse.Namespace(card=args.card, force_order=args.force_order)
    return cmd_accept(args_accept)


def cmd_gate_exit(args) -> int:
    """Re-verify EVERY card in the gate from scratch, ignoring stored receipts.

    Receipts are a cache, and this is where the cache is invalidated. The whole
    trust model otherwise rests on the supervisor - the same class of pressured
    executor that fabricates elsewhere - having actually run what it says it
    ran. There are only a handful of gates, so the cost is small and the
    property it buys is the one the design claims to have.

    It also catches cross-card damage that per-card verification cannot: a later
    card breaking an earlier card's checks looks green card-by-card and red
    here. That happened for real in G0.
    """
    cards = {k: v for k, v in G.all_cards().items() if v["gate"] == args.gate}
    if not cards:
        G.die(f"no cards in gate {args.gate}")

    questions = V.read_jsonl(G.QUESTIONS_JSONL)
    open_q = [
        q["id"] for q in questions if q.get("status") == "OPEN" and args.gate in q.get("consumed_by", [])
    ]

    rc, head, _ = G.git("rev-parse", "HEAD")
    results, failures = {}, []
    for cid in sorted(cards):
        base = _dispatch_rev_for(cid)
        if not base:
            failures.append(f"{cid}: never dispatched")
            continue
        issue = _last_rev_touching(f"plans/{cid}.yaml") or head
        print(f"re-verifying {cid} at HEAD ...", flush=True)
        r = V.do_verify(cid, base, head, issue, evaluate_scope=False)
        results[cid] = r
        if not r["accepted"]:
            failures.append(f"{cid}: {r['reason']}")

    print()
    print(f"=== GATE {args.gate} EXIT ===")
    for cid in sorted(results):
        r = results[cid]
        mark = "PASS" if r["accepted"] else "FAIL"
        print(f"  [{mark}] {cid}")
    for f in failures:
        print(f"  ! {f}")
    if open_q:
        print(f"  ! unresolved open questions consumed by {args.gate}: {open_q}")

    if failures or open_q:
        print(f"GATE {args.gate} NOT MET")
        return G.EXIT_FAIL

    outdir = G.EVIDENCE / "build" / args.gate
    outdir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "gate": args.gate,
        "gate_status": "ACCEPTED",
        "generated_by": "gatectl",
        "recorded_at": G.now_utc(),
        "control_revision": head,
        "method": "every card re-verified from scratch at this revision; stored receipts ignored",
        "fingerprint": G.fingerprint(),
        "cards": {
            cid: {
                "accepted": r["accepted"],
                "head_rev": r["head_rev"],
                "tests": r["runs"][0]["checks"][0].get("tests"),
                "falsifier_made_checks_fail": r["negative_control"]["patch_applied_failed"],
                "receipt": f"evidence/build/{args.gate}/{cid}/receipt.json",
            }
            for cid, r in sorted(results.items())
        },
    }
    (outdir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    G.STATE_FILE.write_text(G.dump_yaml(V.rebuild_state()), encoding="utf-8")
    print(f"GATE {args.gate} MET - evidence/build/{args.gate}/manifest.json")
    return G.EXIT_OK


# --------------------------------------------------------------------------
# state / next / scope / verify / accept
# --------------------------------------------------------------------------
def cmd_state(args) -> int:
    s = V.rebuild_state()
    if args.write:
        G.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        G.STATE_FILE.write_text(G.dump_yaml(s), encoding="utf-8")
        print(f"wrote {G.STATE_FILE.relative_to(G.REPO)}")
    else:
        print(G.dump_yaml(s))
    return G.EXIT_OK


def cmd_next(args) -> int:
    s = V.rebuild_state()
    n = V.next_card(s)
    if not n:
        print("NONE")
        return G.EXIT_FAIL
    print(n)
    return G.EXIT_OK


def cmd_scope(args) -> int:
    card, _ = G.load_card_at_rev(args.card, args.issue_rev or args.head)
    ok, changed, violations = G.check_scope(card, args.base, args.head)
    for p in changed:
        print(f"  changed: {p}")
    for v in violations:
        print(f"FAIL {v}")
    print("SCOPE OK" if ok else f"SCOPE VIOLATION ({len(violations)})")
    return G.EXIT_OK if ok else G.EXIT_FAIL


def cmd_verify(args) -> int:
    r = V.do_verify(args.card, args.base, args.head, args.issue_rev)
    print(f"card={r['card']} accepted={r['accepted']}")
    print(f"reason: {r['reason']}")
    for run_ in r["runs"]:
        codes = [c["exit_code"] for c in run_["checks"]]
        print(f"  clean run {run_['run']}: exit codes {codes}")
    nc = r["negative_control"]
    print(
        f"  negative control: applied={nc['applied']} checks_failed_as_required={nc['patch_applied_failed']}"
    )
    print(f"  receipt: evidence/build/{r['gate']}/{r['card']}/receipt.json")
    return G.EXIT_OK if r["accepted"] else G.EXIT_FAIL


def cmd_accept(args) -> int:
    """Pure table lookup. No judgement, no prose, no discretion."""
    # Ask what `next` WOULD have said before this card was verified: once a
    # receipt exists the card reads as ACCEPTED and next has moved on, so
    # comparing against the current queue would always refuse.
    expected = V.next_card(V.rebuild_state(as_if_unstarted=args.card))
    r = V.load_receipt(_gate_of(args.card), args.card)
    conditions = {
        "receipt exists": r is not None,
        "receipt.accepted": bool(r and r.get("accepted")),
        "scope ok": bool(r and r["scope"]["ok"]),
        "negative control failed as required": bool(r and r["negative_control"]["patch_applied_failed"]),
        "card was the deterministic next card": expected == args.card or args.force_order,
    }
    for k, v in conditions.items():
        print(f"  [{'x' if v else ' '}] {k}")
    if not all(conditions.values()):
        print("ACCEPT REFUSED")
        return G.EXIT_FAIL
    G.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    G.STATE_FILE.write_text(G.dump_yaml(V.rebuild_state()), encoding="utf-8")
    print(f"ACCEPTED {args.card}")
    return G.EXIT_OK


def _gate_of(card_id: str) -> str:
    cards = G.all_cards()
    if card_id not in cards:
        G.die(f"unknown card {card_id}")
    return cards[card_id]["gate"]


# --------------------------------------------------------------------------
# channel append (gatectl fills ts and commit itself)
# --------------------------------------------------------------------------
def cmd_status_append(args) -> int:
    rc, head, _ = G.git("rev-parse", "HEAD")
    line = {
        "ts": G.now_utc(),
        "card": args.card,
        "phase": args.phase,
        "verdict": args.verdict,
        "summary": args.summary,
        "commit": head,
        "attempt": args.attempt,
    }
    errs = G.schema_errors(G.load_schema(SCHEMA_FOR["status"]), line)
    if errs:
        for e in errs:
            print(f"FAIL {e}")
        return G.EXIT_FAIL
    G.STATUS_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with G.STATUS_JSONL.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")
    print("appended")
    return G.EXIT_OK


def cmd_instruct(args) -> int:
    rc, head, _ = G.git("rev-parse", "HEAD")
    card_sha = "NONE"
    if args.target != "ALL":
        p = G.card_path(args.target)
        card_sha = G.sha256_file(p) if p.exists() else "NONE"
    line = {
        "ts": G.now_utc(),
        "target_card": args.target,
        "kind": args.kind,
        "instruction": args.instruction,
        "card_sha256": card_sha,
        "issue_rev": head if head else "NONE",
        "attempt": args.attempt,
    }
    errs = G.schema_errors(G.load_schema(SCHEMA_FOR["instruction"]), line)
    if errs:
        for e in errs:
            print(f"FAIL {e}")
        return G.EXIT_FAIL
    G.INSTRUCTIONS_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with G.INSTRUCTIONS_JSONL.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")
    # A dispatch changes derived state (the card becomes IN_PROGRESS), so refresh
    # the cursor here. Otherwise the very next `validate` fails on a mismatch the
    # dispatch itself caused - which blocked a push once already.
    G.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    G.STATE_FILE.write_text(G.dump_yaml(V.rebuild_state()), encoding="utf-8")
    print(f"dispatched {args.target} (card_sha256={card_sha[:12]}, issue_rev={line['issue_rev'][:12]})")
    return G.EXIT_OK


# --------------------------------------------------------------------------
# tick / doctor / resume-brief
# --------------------------------------------------------------------------
def cmd_tick(args) -> int:
    """The supervisor's ENTIRE per-iteration input. Bounded on purpose.

    The dominant cost in this system is not the workers; it is a supervisor
    re-reading context every few minutes. So this prints a fixed-size brief and
    the supervisor is instructed to read nothing else.
    """
    s = V.rebuild_state()
    G.HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
    G.HEARTBEAT.write_text(
        json.dumps(
            {
                "ts": G.now_utc(),
                "gate": s["current_gate"]["id"],
                "status": s["current_gate"]["status"],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    counts: dict[str, int] = {}
    for r in s["cards"]:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    if not s["cards"]:
        print("STATE: no taskcards exist yet - bootstrap is incomplete")
        print("ACTION: author the G0 cards, then re-run tick")
        return G.EXIT_OK

    print(
        f"GATE {s['current_gate']['id']} ({s['current_gate']['status']})  "
        f"accepted_gates={s['accepted_gates'] or 'none'}"
    )
    print("CARDS " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if s["open_questions"]["open"]:
        print(f"OPEN QUESTIONS {s['open_questions']['ids']} (these block gate exit, not work)")
    for r in s["cards"]:
        if r["status"] in ("IN_PROGRESS", "FAILED_INTERNAL", "BLOCKED_EXTERNAL"):
            print(
                f"  {r['id']}: {r['status']} attempt={r['attempt']}"
                + (f" blocker={r['blocker']['summary'][:80]}" if r["blocker"] else "")
            )
    nxt = V.next_card(s)
    if all(r["status"] == "ACCEPTED" for r in s["cards"]) and not s["open_questions"]["open"]:
        print("ALL_GATES_COMPLETE")
        return G.EXIT_OK
    print(f"ACTION: dispatch {nxt}" if nxt else "ACTION: no READY card - resolve a blocker above")
    return G.EXIT_OK


def cmd_doctor(args) -> int:
    """Detect what path checks structurally cannot: drift outside the repo."""
    flags = []

    hp = G.git("config", "--get", "core.hooksPath")[1]
    if hp != ".githooks":
        flags.append(f"core.hooksPath is {hp!r}, expected '.githooks' - versioned hooks are inert without it")

    if not (G.REPO / ".gitattributes").exists():
        flags.append(".gitattributes missing - core.autocrlf=true means disk bytes != blob bytes")

    rc, out, _ = G.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "if (Get-Process OneDrive -ErrorAction SilentlyContinue) {'RUNNING'} else {'STOPPED'}",
        ]
    )
    if "RUNNING" in out and "OneDrive" in str(G.REPO):
        flags.append(
            "OneDrive is RUNNING against this repo path - sync will rewrite mtimes and "
            "contend for .git/index.lock; exclude .git/.venv or move the repo"
        )

    if not G.LOCKFILE.exists():
        flags.append("requirements.lock missing - verification environment is unpinned")

    hb = G.HEARTBEAT
    if hb.exists():
        import datetime as _dt

        ts = json.loads(hb.read_text(encoding="utf-8")).get("ts", "")
        try:
            age = _dt.datetime.now(_dt.UTC) - _dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=_dt.UTC
            )
            if age.total_seconds() > 900:
                flags.append(
                    f"heartbeat is {int(age.total_seconds() / 60)} min old (> 15) - the loop may be wedged"
                )
        except ValueError:
            flags.append(f"heartbeat timestamp unparseable: {ts!r}")

    for f in flags:
        print(f"FLAG {f}")
    if not flags:
        print("OK  no flags")
    return G.EXIT_FAIL if flags else G.EXIT_OK


def cmd_resume_brief(args) -> int:
    """Everything a COLD supervisor needs, small enough to paste."""
    s = V.rebuild_state()
    print("# foss-mcp supervised loop - resume brief")
    print(f"repo: {G.REPO}")
    print(f"gate: {s['current_gate']['id']} ({s['current_gate']['status']})")
    print(f"accepted gates: {s['accepted_gates'] or 'none'}")
    print("\n## cards")
    for r in s["cards"]:
        extra = f" blocker: {r['blocker']['summary']}" if r["blocker"] else ""
        print(f"  {r['id']} {r['status']} attempt={r['attempt']}{extra}")
    if s["open_questions"]["open"]:
        print(f"\n## open questions (block gate exit only): {s['open_questions']['ids']}")
    print(f"\n## next action\n  dispatch {V.next_card(s) or '(none READY)'}")
    print("\n## how to continue\n  python ops/gatectl.py tick   # then do exactly what it prints")
    return G.EXIT_OK


def build_parser():
    p = argparse.ArgumentParser(prog="gatectl", description="deterministic core of the foss-mcp loop")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("validate", help="schema + DAG + coverage + state-rebuild assertions")

    sp = sub.add_parser("state", help="derive project/state.yaml from primary evidence")
    sp.add_argument("--write", action="store_true")

    sub.add_parser("next", help="print exactly one card id to dispatch")
    sub.add_parser("tick", help="the supervisor's bounded per-iteration brief")
    sub.add_parser("worker-tick", help="the worker loop's single decision point: WORK / WAIT / DONE")

    sp = sub.add_parser("holdout-check", help="run a card's holdout against the working tree")
    sp.add_argument("card")

    sp = sub.add_parser("question-append", help="open a provisional decision; blocks gate exit only")
    sp.add_argument("--card", required=True)
    sp.add_argument("--question", required=True)
    sp.add_argument("--decision", required=True)
    sp.add_argument("--rationale", required=True)
    sp.add_argument("--consumed-by", required=True)

    sp = sub.add_parser("dispatch-next", help="supervisor: dispatch the deterministic next card")
    sp.add_argument("--card")
    sp.add_argument("--kind", default="dispatch")
    sp.add_argument("--instruction")
    sp.add_argument("--attempt", type=int, default=1)
    sub.add_parser("doctor", help="out-of-repo drift and liveness flags")
    sub.add_parser("resume-brief", help="cold-start brief")

    sp = sub.add_parser("scope", help="assert a diff stays inside a card's write_paths")
    sp.add_argument("card")
    sp.add_argument("--base", required=True)
    sp.add_argument("--head", default="HEAD")
    sp.add_argument("--issue-rev")

    sp = sub.add_parser("verify", help="run a card's checks and the falsifier; write the receipt")
    sp.add_argument("card")
    sp.add_argument("--base", required=True)
    sp.add_argument("--head", default="HEAD")
    sp.add_argument("--issue-rev")

    sp = sub.add_parser("gate-exit", help="re-verify every card in a gate from scratch")
    sp.add_argument("gate")

    sp = sub.add_parser("review", help="verify then accept - the supervisor's whole per-card action")
    sp.add_argument("card")
    sp.add_argument("--base")
    sp.add_argument("--head", default="HEAD")
    sp.add_argument("--issue-rev")
    sp.add_argument("--force-order", action="store_true")

    sp = sub.add_parser("accept", help="table lookup; refuses unless every condition holds")
    sp.add_argument("card")
    sp.add_argument(
        "--force-order", action="store_true", help="bypass the deterministic-order check (canaries only)"
    )

    sp = sub.add_parser("status-append", help="worker channel; ts and commit are filled by gatectl")
    sp.add_argument("--card", required=True)
    sp.add_argument("--phase", required=True)
    sp.add_argument("--verdict", default="n/a")
    sp.add_argument("--summary", required=True)
    sp.add_argument("--attempt", type=int, default=1)

    sp = sub.add_parser("instruct", help="supervisor channel; pins card_sha256 and issue_rev")
    sp.add_argument("--target", required=True)
    sp.add_argument("--kind", required=True)
    sp.add_argument("--instruction", required=True)
    sp.add_argument("--attempt", type=int, default=1)
    return p


HANDLERS = {
    "validate": cmd_validate,
    "state": cmd_state,
    "next": cmd_next,
    "scope": cmd_scope,
    "verify": cmd_verify,
    "accept": cmd_accept,
    "review": cmd_review,
    "gate-exit": cmd_gate_exit,
    "status-append": cmd_status_append,
    "instruct": cmd_instruct,
    "tick": cmd_tick,
    "worker-tick": cmd_worker_tick,
    "dispatch-next": cmd_dispatch_next,
    "question-append": cmd_question_append,
    "holdout-check": cmd_holdout_check,
    "doctor": cmd_doctor,
    "resume-brief": cmd_resume_brief,
}


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return HANDLERS[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
