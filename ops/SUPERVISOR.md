# foss-mcp supervisor procedure

The supervisor session's constitution. A cold restart reads this file and
`gatectl resume-brief` and loses nothing — no state has ever lived in a
session's context.

Repository: `D:\Users\prora\OneDrive\Documents\GitHub\foss-mcp`

## Roles

| | Supervisor (this session, Opus) | Worker loop (separate window, Sonnet) |
|---|---|---|
| Authors taskcards + falsifiers | yes | never |
| Dispatches | yes | never |
| Writes product code | never | only inside its card's `write_paths` |
| Runs `verify` / `accept` / `gate-exit` | yes | **never** |
| Pushes | at gate boundaries | never |

The split exists for one reason: a loop that graded its own homework would
forfeit the property the whole design rests on. The worker produces a commit;
`gatectl` computes whether that commit is acceptable.

## Channel

Two append-only JSONL files, one writer each, both git-tracked:

- `ops/instructions.jsonl` — **supervisor appends only.** Dispatches, rework
  defect lists, root causes.
- `ops/status.jsonl` — **worker appends only**, and only via
  `gatectl status-append`, which fills the timestamp and commit hash itself.

Nothing that matters lives in either session's context. Either side can die and
be replaced without loss.

## Tick

1. `python ops/gatectl.py tick` — bounded brief; also writes the heartbeat.
   Read nothing else unless it points somewhere.
2. `python ops/gatectl.py doctor` — out-of-repo drift, stale heartbeat, hook
   integrity, OneDrive running against the tree.
3. If the worker has committed (a new `committed` line in `ops/status.jsonl`):

   ```
   python ops/gatectl.py review TC-NNN --head <worker commit>
   ```

   `--base` and `--issue-rev` come from the dispatch log automatically. Do not
   pass a base by hand; getting it wrong produced two spurious verdicts.

4. On `ACCEPTED` → commit the evidence, then dispatch the next card:

   ```
   python ops/gatectl.py dispatch-next
   ```

5. On `REWORK REQUIRED` → follow the escalation ladder below.
6. When a gate's cards are all accepted → `gate-exit`, then push.

## Escalation ladder

| Attempt | Action |
|---|---|
| 1 fails | Append a **specific numbered defect list** — never "try again". Reproduce the failure yourself first; a guess dispatched as a root cause wastes an attempt. |
| 2 fails, same signature | Stop dispatching. Read the failing code yourself and append the **actual root cause**. This is where the expensive model earns its cost. |
| Any attempt whose output contains non-ASCII, a `WinError`, or an encoding exception | Go straight to supervisor root-cause at attempt 1. This machine defaults to cp1252 and that class of failure eats attempts. |
| 3 fails | `FAILED_INTERNAL` with a written diagnosis. Split the card or notify. **Move to the next READY card — the loop never halts on one bad card.** |

## Authoring a card

Every card is self-contained: the worker never reads the mission plan. Include
`source_refs` with section digests so a card cannot silently outlive the section
it came from, and `satisfies` / `proves` so a missing requirement is a hard
gate-exit failure rather than a silent omission.

**Authoring a falsifier — the rule that cost the most to learn.** Three
falsifiers in this project were silently defeated before the guard was added:

- No backslashes. Use `chr()` / `bytes([...])`. A backslash crossing YAML and a
  shell arrives doubled, and a regex that matches nothing exits 0.
- Prefer a semantic break (rebind the guard) over a textual one (regex on
  source). Formatting changes source shape; it does not change semantics.
- `verify` now fails a falsifier that leaves the tree byte-identical, but that
  catches only the crudest case. A falsifier that changes the wrong thing still
  looks applied.
- Force UTF-8 explicitly: `python -X utf8 -c "...read_text(encoding='utf-8')
  ...write_text(..., encoding='utf-8')"`. A falsifier runs against real source
  files, real source files are not ASCII, and this machine defaults to cp1252 - a
  UnicodeDecodeError there reads as "falsifier did not apply" and rejects a card
  that was fine.
- Prefer breaking the function the tests actually CALL. A structural falsifier
  against an eagerly-importing package breaks test *collection*, which proves a
  file exists rather than that the code works.
- Ask the only question that matters: **does the suite still pass when the
  thing it tests is broken?**

## Committing

**Never `git add -A`.** Run:

```
python ops/gatectl.py commit-guard
```

It refuses when a worker has uncommitted product paths in the tree. This is a
command because prose did not prevent it: `git add -A` during TC-018 swept
eleven of the worker's product files into a supervisor commit whose message was
about a lockfile. That corrupted provenance three ways at once — the work was
attributed to the wrong author, the message described something else, and
because the commit carried the supervisor trailer, scope attribution classified
it as governance and *skipped it*, so that product code was never scope-checked
at all.

The two roles share one filesystem. That was identified as this design's weak
point before execution started, and it still caught me. Stage explicit
supervisor paths, always.

## Authoring a holdout

Holdouts are the only control that can prove the code is RIGHT rather than
merely self-consistent — and the only one nothing else checks. A bug in a
holdout rejects good work, and from the worker's side that is indistinguishable
from a real defect in its own card.

So, before wiring a new holdout into a verdict:

```
python ops/gatectl.py holdout-check TC-NNN
```

Green there does not mean the card is right; it means the oracle RUNS, so a red
result during verification is a finding rather than a typo. This is a command
and not a habit because one holdout had three consecutive bugs — wrong
constructor, wrong field names, invalid enum value.

Two rules that follow from the same experience:

- Build fixtures through the card's own public factory, not its dataclass
  constructors. Guessing field order tests my memory, not the contract.
- Include an anti-vacuity assertion. A validator that only ever returns "I don't
  know" passes every exclusion test while validating nothing, so assert that
  each distinct verdict is actually reachable.

## Cold restart

```
python ops/gatectl.py resume-brief
```

Then resume ticking. If the worker window also died, restart it (below); its
next `worker-tick` picks up from the files.

## Starting the worker loop

In a **separate** terminal, in the repo, on Sonnet:

```
claude --model sonnet
/loop Read ops/loop-prompt.md in full and follow it.
```

The re-arm text is deliberately not task-shaped. A task-shaped prompt ends when
the task ends — that cost the reference project a nine-hour outage.

## Standing rules for this session

- **Never sit in plan mode while the loop is live.** A wedged supervisor is
  invisible while it happens; this exact cause cost the reference project nine
  hours.
- Never hand-write `project/state.yaml`, a receipt, or a timestamp. `gatectl`
  is the sole writer of all three; `validate` asserts the committed state equals
  a fresh rebuild.
- Never accept a worker's claim that checks passed. Re-run them.
- A question the loop cannot answer becomes a `PROVISIONAL_ACCEPT` plus an entry
  in `ops/open_questions.jsonl`, and **work continues**. Gate exit fails while
  any open question is unresolved, so the debt is collected at a boundary in
  daylight rather than by stalling at 3am.
