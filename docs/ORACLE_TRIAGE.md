# Oracle disagreement triage: Go, Rust, C++ (G3/TC-691)

## Scope and provenance

This document classifies the independent extraction-fidelity oracle's real, already-measured
disagreement for the 7 Go/Rust/C++ pilots out of the 32-pilot fleet: `cells/go`, `pdf/go`,
`cells/rust`, `pdf/cpp`, `cells/cpp`, `slides/cpp`, `email/cpp`.

The measurement is `TC-595`'s real, fleet-wide run (2026-10-27):

```
python scripts/release/source_differential.py --fixtures tests/fixtures
```

— no `--only` filter, all 32 pilots, each against the real upstream source at its pinned commit,
read with that language's own tooling (never this project's tree-sitter engine): Go's own
`go/parser`+`go/ast`, Rust's `syn` crate, and Clang's own AST (`-Xclang -ast-dump=json`) for C++.
The real per-pilot, per-category counts live verbatim in that card's own commit message
(`git log --format=%B -n 1 5c8bc6fe`, subject `docs(release): run the independent oracle
fleet-wide and close the Python-only disclosure gap (G3/TC-595)`) and are restated in
`docs/RELEASE.md`'s "Independent extraction-fidelity oracle (REQ-G3-009)" section (updated
2026-10-27). This card re-derives nothing; every number below was read back from those two
sources and cross-checked against each other before being transcribed here.

This card is documentation/classification only. No `src/` file and no
`scripts/release/source_differential.py` change is in scope.

## Per-pilot results

| Pilot | checked | missing | extra | signature | static | default | enum_value | Verdict |
|---|---|---|---|---|---|---|---|---|
| `cells/go` | 79 | 0 | 0 | 0 | 0 | 0 | 0 | Genuinely clean — the only real zero-disagreement result fleet-wide (the other 2 of the fleet's 3 nominal passes, `cells/typescript` and `tex/python`, are hollow passes, not fidelity confirmations — see `docs/RELEASE.md`) |
| `pdf/go` | — | 0 | **480** | 0 | 0 | 0 | 0 | Traced by this card (see below): Go struct-embedding method/field promotion |
| `cells/rust` | — | **341** | 0 | **14** | 0 | 0 | 0 | Root-caused in `OQ-018` |
| `pdf/cpp` | 2091 | **4** | **2406** | **694** | 0 | 0 | 0 | Root-caused in `OQ-022` |
| `cells/cpp` | — | 0 | 0 | **13** | 0 | 0 | 0 | Not individually root-caused by any `OQ` entry (see below) |
| `slides/cpp` | — | 0 | **153** | **37** | 0 | 0 | 0 | Not individually root-caused by any `OQ` entry (see below) |
| `email/cpp` | — | 0 | **24** | **7** | 0 | 0 | 0 | Not individually root-caused by any `OQ` entry (see below) |

Every figure above was independently confirmed against `TC-595`'s commit message and
`docs/RELEASE.md` before being written here; both sources agree verbatim for all 7 pilots.

## Two cross-cutting buckets (confirmed three times: Java, .NET, C++)

`OQ-020` (Java, first confirmation), `OQ-021` (.NET, second confirmation), and `OQ-022` (C++,
third confirmation) each independently found the *same two* root causes behind nearly every
non-trivial `extra`/`signature` finding, for languages that have real class inheritance:

### Bucket 1 — inheritance/base-class member flattening

The baked (tree-sitter) extractor deliberately flattens an ancestor class's members onto a
served subclass (recorded there as `inherited_from`). The independent oracle is parse-phase
only and, by design — consistently with every language's enumerator in this project — never
replicates that flattening, because doing so correctly needs a full semantic/symbol resolver,
not a parser. A baked member therefore appears "extra" (served, but the oracle never found it
declared on that type) whenever it is actually declared on a base class, not on the subclass
that serves it.

Real cited examples:
- `OQ-018` (Rust): `AutoFilterColorFilterModel` sits behind the crate's real public-facing
  wrapper type `AutoFilterColorFilter` — the oracle's missing=341 for `cells/rust` is mostly
  these internal `...Model` structs the fixture never serves as such, confused with the public
  wrapper (a reachability question, not literally "extra", but the same family of base/wrapper
  confusion).
- `OQ-022` (C++, `pdf/cpp`): `class Annotation : public Aspose::Pdf::BaseParagraph`, where
  `BaseParagraph` declares `Margin()`/`VerticalAlignment()`; the baked fixture serves both on
  `Annotation` even though `Annotation` never redeclares them. This is the third independent
  confirmation of the identical pattern `OQ-020` (Java) and `OQ-021` (.NET) first found.

### Bucket 2 — namespace/overload-unaware bare-name matching

`source_differential.py`'s own diff engine (`diff_symbol`/`diff_language`) matches an oracle
member to a baked member by bare name only, with no namespace/package scoping and no overload
grouping. Two unrelated things with the same bare name collide under one fixture entry:

Real cited examples:
- `OQ-018` (Rust, `cells/rust`): `CellObjectValue`'s several `impl From<T>` methods are all
  literally named `from` and all compare against one fixture entry by bare name — the
  signature=14 finding.
- `OQ-022` (C++, `pdf/cpp`): getter/setter pairs sharing one bare name (e.g. `bool Margin()
  const;` and `void Margin(bool value);`) collide under the same bare-name match — most of
  `pdf/cpp`'s signature=694 (originally 859, reduced by the `TC-594` worker's own overload-
  collapse fix for the straightforward cases; some remainder is likely genuine parameter
  differences, not investigated further, out of `OQ-022`'s own scope).

Both buckets are now confirmed three times independently (Java, .NET, C++) and account for
nearly every Rust/C++ finding in the table above. Neither has been fixed — `OQ-020`/`OQ-021`/
`OQ-022` each record it as a future, diff-engine-layer fix (fix bare-name matching once, benefit
every enumerator), not a per-pilot patch, and none of this blocks G3.

## `pdf/go`'s extra=480 — traced by this card

As of `OQ-018` and `OQ-022`, nothing explained `pdf/go`'s extra=480; no `OQ` entry covers it, and
Go has no class inheritance in the sense the bucket above describes, so this card does not
force-fit that bucket onto it without checking first.

Having checked directly against `tests/fixtures/pdf_go/api_surface.json` (pinned to
`aspose-pdf-foss/Aspose-PDF-FOSS-for-Go` @ `cdf43df10c8c565ecaa978428b1fe66ad6685f8d`) and the
Go enumerator's own source (`scripts/release/oracle_helpers/go_enumerator.go`), the cause is
real, confirmable, and **is** the same abstract pattern as bucket 1 above, manifesting through
Go's own language mechanism instead of class inheritance:

**Go struct-embedding method/field promotion.** Go has no classes or `extends`, but a struct can
embed another type anonymously (e.g. `type TextBoxField struct { fieldBase; ... }`), and Go
promotes the embedded type's exported methods/fields onto the embedding type's own method set.
The baked fixture deliberately flattens these promoted members onto the embedding type exactly
the way the other languages' extractors flatten inherited members — every such member in the
fixture carries an explicit `"inherited_from"`/`"promoted_from"` field naming the embedded base
(e.g. `TextBoxField.Flatten`, `TextBoxField.PartialName`, `TextBoxField.Style` all carry
`"promoted_from": "fieldBase"`). `go_enumerator.go` deliberately skips anonymous/embedded fields
and embedded interfaces (its own comments: "an anonymous/embedded field... is out of this card's
declared scope and is skipped", "an embedded interface: out of this card's declared scope"), by
the same parse-phase-only, no-semantic-resolver design as every other language's enumerator — and
several of the embedded base types (`fieldBase`, `annotationBase`, `markupAnnotationBase`,
`polyAnnotationBase`, `drawingAnnotationBase`, `stampBase`) are themselves unexported, so the
oracle never emits them as symbols either, meaning their promoted methods/fields are never found
anywhere under any receiver name by the independent oracle.

This is a real, fully-confirmed bucket, not a guess: summing every member in
`tests/fixtures/pdf_go/api_surface.json` across all types that carries a `"promoted_from"` key
gives exactly **480** — matching `pdf/go`'s `extra` count exactly, with 0 remainder. The 11
distinct embedded base names involved are: `ComparisonOptions`, `ComparisonResult`,
`DiffOutputStyle`, `LineStyle`, `TextBoxField` (itself an embedding base for `BarcodeField`,
`DateField`, `FileSelectBoxField`, `NumberField`, `PasswordBoxField`, etc.), `annotationBase`,
`drawingAnnotationBase`, `fieldBase`, `markupAnnotationBase`, `polyAnnotationBase`, and
`stampBase`.

Conclusion: `pdf/go`'s extra=480 is **not** untraced. It is a fourth independent confirmation of
the same "ancestor member flattened onto a served type, parse-phase oracle correctly refuses to
replicate it" pattern already found in Java/.NET/C++ (`OQ-020`/`OQ-021`/`OQ-022`), expressed
through Go's own embedding/promotion mechanism rather than class inheritance. It does not need a
new `OQ` entry describing a *different* root cause — it is the same root cause, a different
language's syntax for it — but is recorded here because no prior entry said so for Go.

## The 3 C++ pilots not individually root-caused

`OQ-022` traced `pdf/cpp` in detail (missing=4, extra=2406, signature=694). It did not
individually investigate `cells/cpp`, `slides/cpp`, or `email/cpp`. Those three are **not**
independently root-caused by any existing `OQ` entry; stating otherwise would misrepresent what
was actually investigated. By inspection of the category shapes alone (not a traced
investigation):

- `slides/cpp` (extra=153, signature=37) and `email/cpp` (extra=24, signature=7) show the same
  two-category shape as `pdf/cpp` (extra, signature), consistent with — but not individually
  confirmed to be — the same two buckets (inheritance-flattening and bare-name/overload
  collisions).
- `cells/cpp` shows a different, narrower shape: signature=13 only, with extra=0 and missing=0.
  This is consistent with bucket 2 (bare-name/overload-unaware matching) alone; there is no
  `extra` finding to attribute to bucket 1 for this pilot, so bucket 1's applicability to
  `cells/cpp` is not supported by the numbers and is not claimed here.

None of the three has been individually traced to a confirmed symbol-level example the way
`pdf/cpp` was. This document records that honestly rather than implying otherwise.

## Summary

- 7 Go/Rust/C++ pilots have a real oracle disagreement (or, for `cells/go`, a real confirmed
  absence of one) as of `TC-595`'s fleet-wide run.
- 2 cross-cutting buckets, each confirmed independently 3 times (Java/.NET/C++) before this
  card, explain nearly all of `cells/rust`'s and `pdf/cpp`'s findings, and are consistent by
  shape (not individually traced) with `slides/cpp` and `email/cpp`.
- `pdf/go`'s extra=480, previously untraced, is now traced by this card to Go struct-embedding
  method/field promotion — a fourth, Go-specific manifestation of bucket 1's same abstract
  pattern — confirmed exactly (480 promoted members in the fixture, 0 remainder).
- `cells/cpp`'s signature=13 is consistent with bucket 2 alone (no `extra`/`missing` to support
  bucket 1 for this pilot).
- No code, fixture, or oracle-script change is made by this card. Every fix remains recorded as
  future work in `OQ-018`/`OQ-020`/`OQ-021`/`OQ-022` (a diff-engine-layer fix for bucket 2;
  per-pilot fixture/extraction work, if ever re-prioritized, for bucket 1).
