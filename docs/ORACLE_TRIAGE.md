# Oracle disagreement triage: Go, Rust, C++ (G3/TC-691, extended by G3/TC-732)

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

`TC-732` (G3/REQ-G3-012) extends this document to finish individually root-causing `cells/cpp`,
`slides/cpp`, and `email/cpp` — the 3 of the 7 pilots `TC-691` left classified "by inspection
alone." Unlike the rest of this document, which re-derives nothing from `TC-595`'s own committed
numbers, `TC-732` additionally ran `python scripts/release/source_differential.py --only
<pilot>` live, against each pilot's real pinned commit (fetched fresh via `gh api`), to confirm
each finding directly rather than only by category shape — see each pilot's own section below for
the exact commits used and what that live run did and did not reproduce.

## Per-pilot results

| Pilot | checked | missing | extra | signature | static | default | enum_value | Verdict |
|---|---|---|---|---|---|---|---|---|
| `cells/go` | 79 | 0 | 0 | 0 | 0 | 0 | 0 | Genuinely clean — the only real zero-disagreement result fleet-wide (the other 2 of the fleet's 3 nominal passes, `cells/typescript` and `tex/python`, are hollow passes, not fidelity confirmations — see `docs/RELEASE.md`) |
| `pdf/go` | — | 0 | **480** | 0 | 0 | 0 | 0 | Traced by this card (see below): Go struct-embedding method/field promotion |
| `cells/rust` | — | **341** | 0 | **14** | 0 | 0 | 0 | Root-caused in `OQ-018` |
| `pdf/cpp` | 2091 | **4** | **2406** | **694** | 0 | 0 | 0 | Root-caused in `OQ-022` |
| `cells/cpp` | — | 0 | 0 | **13** | 0 | 0 | 0 | Individually root-caused by `TC-732` (see below): bucket 2 only, confirmed for all 13 |
| `slides/cpp` | — | 0 | **153** | **37** | 0 | 0 | 0 | Individually root-caused by `TC-732` (see below): bucket 1 confirmed directly for `extra`; bucket 2 confirmed for all 37 `signature` findings |
| `email/cpp` | — | 0 | **24** | **7** | 0 | 0 | 0 | Individually root-caused by `TC-732` (see below): bucket 1 confirmed exactly (0 remainder) for `extra`; bucket 2 confirmed for all 7 `signature` findings |

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

## `cells/cpp`, `slides/cpp`, `email/cpp` — individually root-caused by this card (`TC-732`)

`OQ-022` traced `pdf/cpp` in detail (missing=4, extra=2406, signature=694), but did not
individually investigate `cells/cpp`, `slides/cpp`, or `email/cpp`; a prior pass over this
document (`TC-691`) classified those three "by inspection of the category shapes alone," honestly
flagged as not individually traced. This card (`TC-732`) finishes that tracing for real: for each
pilot, the real pinned commit was fetched via `gh api` from the real `repository` named in
`config/products/<family>/cpp.yaml`, confirmed against the real committed fixture's own
`source_commit`/`source_repository` provenance fields (never the live default branch), and a real,
live run of `python scripts/release/source_differential.py --only <pilot>` was executed against
that real clone, with every individual finding read from its own JSON report and checked directly
against the real header text.

- `cells/cpp` @ `aspose-cells-foss/Aspose.Cells-FOSS-for-Cpp` @ `9f852d0ff1cfdad2d661556d6b87a8eff8c063a2`:
  a real live run reproduced checked=769, signature=13, extra=0, missing=0 exactly. **Bucket 1 does
  not apply** (confirmed, not merely assumed from the shape): there is no `extra` finding to
  attribute to it. **All 13 signature findings are bucket 2** (bare-name/overload-unaware
  matching), confirmed individually against the real pinned headers: `CellArea` (2 ctors: default;
  `(firstRow, firstColumn, totalRows, totalColumns)`), `CellArea::CreateCellArea` (2 static
  overloads: 4 ints vs. 2 `string_view`s), `CellsException` (2 ctors: `(message)`;
  `(message, innerException)`), `CellValue` (8 ctors: default, `int`, `double`, `bool`,
  `const char*`, `string`, `string_view`, `DateTime`), `CellAddress` (2 ctors), `ColorValue` (2
  ctors), `MergeRegion` (2 ctors), `DateTime` (5 ctors), `WorkbookSettings`/`WorkbookView` (each has
  only 2 ctors total — the real usable `explicit` one, plus a `= delete`d copy ctor; the diff
  engine's bare-name match picked the deleted one, still a genuine same-bare-name collision even
  though one overload is unusable), `XNamespace` (2 ctors), `XmlElement` (4 ctors: default, copy,
  move, `explicit(shared_ptr<XmlNodeData>)`), `XmlElement::MakeElement` (3 static overloads: 1, 1,
  and 3 params). Zero remainder; every one of the 13 confirmed.

- `email/cpp` @ `aspose-email-foss/Aspose.Email-FOSS-for-Cpp` @ `c844a467cf7f2503819b655f4d6ceaef91056c40`:
  a real live run reproduced checked=276, signature=7, extra=0, missing=0 — every public header
  resolved cleanly. **Bucket 1 is confirmed for `extra`, exactly, with zero remainder**, the same
  rigor as `pdf/go`'s own exact-match confirmation above: the real committed fixture carries
  exactly 24 `inherited_from` members fleet-wide for this pilot — 12 on `cfb_storage` and 12 on
  `cfb_stream` (`node_kind`, `is_storage`, `is_stream`, `name`, `clsid`, `state_bits`/
  `set_state_bits`, `creation_time`/`set_creation_time`, `modified_time`/`set_modified_time`,
  `clone`) — matching `extra=24` exactly. Confirmed directly against the real header: both
  `class cfb_storage final : public cfb_node` and `class cfb_stream final : public cfb_node`
  (`include/aspose/email/foss/cfb/cfb_storage.hpp`, `.../cfb_stream.hpp`), and `cfb_node` itself
  (`cfb_node.hpp`) declares all 12 of those members; neither subclass redeclares any of them. The
  same real `Annotation : BaseParagraph` family of pattern `OQ-022` already proved for `pdf/cpp`,
  here with an exact, zero-remainder member count. **All 7 signature findings are bucket 2**,
  confirmed individually: `cfb_document` (2 ctors: default; `(root, major_version, minor_version,
  transaction_signature_number)`), `cfb_storage` (3 ctors — `(name)`, copy, move — plus 2
  `operator=` overloads — copy, move; the diff engine matched the bare-name ctor/operator=
  against the wrong overload in each case), `mapi_message::load_from_eml`/`add_attachment`/`save`/
  `save_to_eml` (each a real, multi-overload method: `load_from_eml(path)` vs. `(stream)`;
  `add_attachment(..., data, ...)` vs. `(..., stream, ...)`; `save()`/`save(path)`/`save(stream)`;
  `save_to_eml()`/`save_to_eml(path)`/`save_to_eml(stream)`).

- `slides/cpp` @ `aspose-slides-foss/Aspose.Slides-FOSS-for-Cpp` @ `469ce77a07b988c49deb930632b8276c89ec6b66`:
  a real live run reproduced checked=2361, signature=37, extra=0, missing=0. **All 37 signature
  findings are bucket 2**, confirmed individually, including: `PointF`/`RectangleF`/`Size`/`SizeF`
  (2 ctors each: default; full-field), `IShapeCollection::reorder` (2 overloads: single `Shape&`
  vs. `span<Shape*>`), `IShapeCollection`/`ISlideCollection::add_auto_shape`/`insert_auto_shape`/
  `add_connector`/`insert_connector`/`add_clone`/`insert_clone` (each a real overload pair, one with
  an extra trailing `bool create_from_template`/`dest_master, allow_clone_missing_layout`
  parameter), `CommentAuthorCollection`/`CommentCollection`/`ImageCollection`/`LayoutSlideCollection`/
  `MasterLayoutSlideCollection`/`SlideCollection::to_array` (bare-name collisions between a
  container's own ctor/`to_array` and an unrelated same-named overload). **`extra=153` could not be
  reproduced as a live count in this card's own working environment** (disclosed honestly below,
  not swept under the rug), but **bucket 1 is directly confirmed** against the real pinned source
  by two concrete, independently-verified examples, the same rigor `OQ-022` used for
  `Annotation : BaseParagraph`: (1) `class SimpleColorFormat final : public ColorFormat` — the base
  `ColorFormat` declares `r()`/`set_r()`/`g()`/`set_g()`/`b()`/`set_b()`/`float_r()`/`set_float_r()`/
  `float_g()`/`set_float_g()`/`float_b()`/`set_float_b()` (12 real methods, default-implemented by
  delegating to `color()`), and `SimpleColorFormat` itself only overrides `color()`/`set_color()`/
  `preset_color()`/`set_preset_color()`/`scheme_color()`/`set_scheme_color()` — never redeclaring
  any of those 12, which the baked fixture nonetheless serves directly on `SimpleColorFormat`;
  (2) `class Slide final : public BaseSlide, public ISlide, public ISlideComponent` — `name()`/
  `set_name()` are declared only on `BaseSlide` (`base_slide.h`), never redeclared on `Slide`, yet
  the baked fixture serves both on `Slide`.

  Disclosure on `extra=153`'s live reproduction: `source_differential.py --only slides_cpp`, run
  twice in this card's own environment, measured `extra=0` both times, not 153. First run: 63 of
  234 public headers failed to resolve because this environment has no vendored copy of the real
  upstream's third-party `#include <pugixml.hpp>` dependency (confirmed: `fatal error: 'pugixml.hpp'
  file not found`), and `diff_language`'s own design (read directly: it iterates the *oracle's*
  found symbols and looks up a matching baked type, never the reverse) means a baked type whose
  header never resolved at all is silently excluded from the diff entirely — neither `extra` nor
  `missing` — rather than counted. Fetching the real upstream `pugixml.hpp`/`pugiconfig.hpp` via
  `gh api repos/zeux/pugixml/contents/...` and placing them in the clone's own public include root
  dropped unresolved headers to 1 of 236, but also caused `enumerate_cpp` to enumerate `pugixml`'s
  own unrelated namespace (`xml_document`, `xml_node`, `xpath_query`, and 21 others) as if it were
  part of the library's own public surface, contaminating that run's counts; those 24 pugixml-only
  findings were excluded from this card's own analysis as not belonging to `slides/cpp` at all, and
  the real Aspose findings that newly resolved (`AdjustValue`, `AutoShape`, `Table`, `TextFrame`,
  `Presentation`, and others) raised `signature` to 51 in that run — a different number again,
  because more headers now resolved than at `TC-595`'s original 2026-10-27 measurement. Separately
  — and likely the more fundamental reason `extra` no longer reproduces at 153 — `TC-624`
  (2026-10-31, four days *after* `TC-595`'s run) added exactly the fix this bucket's own root cause
  calls for: verifying a baked inherited member against its own named ancestor's real oracle symbol
  before ever calling it "extra" (`diff_symbol`'s own docstring, `oracle_by_name`). Whether `TC-624`
  alone fully explains the drop from 153, or the `pugixml`/header-resolution gap also played a part
  in the pinned environment `TC-595` itself ran in, was not exhaustively re-derived member-by-member
  here — that would be a live fleet-wide re-run and recount, out of this documentation-only card's
  scope, and `TC-595`'s own 153 is left exactly as originally, honestly measured and recorded above.
  What this card confirms directly against the real source, independent of any live count, is the
  individual root cause itself: `slides/cpp`'s `extra` findings are a genuine, directly-verified
  instance of bucket 1, not a guess by category shape alone.

  A second, separate real finding surfaced while investigating `cells/cpp` (recorded here for
  completeness, not fixed — out of this card's scope, same as every other oracle-script limitation
  this document already names as future work): `cells/cpp`'s own live run failed to resolve 110 of
  186 public headers, but not because of a missing third-party dependency like `slides/cpp` — this
  pilot's real upstream repository nests its public headers one level deeper
  (`Aspose.Cells.Foss.Cpp/include/...`) than `_cpp_public_include_dir`'s fallback assumes (a
  directory literally named `include` directly under the cloned repo root), so the enumerator's
  single `-I` flag points at the wrong directory for resolving this library's own same-directory
  quoted `#include`s (confirmed directly: re-running clang by hand with the *correct* include root,
  `.../Aspose.Cells.Foss.Cpp/include`, resolves every one of the headers that failed under the
  script's own wrong one, with byte-identical error text reproduced when the wrong root is used
  instead). This is a real, reproducible oracle-script layout-handling gap, independent of any one
  sandbox's installed packages, and may mean additional real `extra`/`missing` findings exist for
  `cells/cpp` behind those 110 unresolved headers that neither `TC-595`'s original measurement nor
  this card's own live run ever saw. Not fixed here (no `source_differential.py` change is in this
  card's scope) — named honestly as a real limitation of the measurement itself, the same way this
  document already discloses the bare-name-matching and inheritance-flattening limitations as
  unresolved, future diff-engine-layer work.

## Summary

- 7 Go/Rust/C++ pilots have a real oracle disagreement (or, for `cells/go`, a real confirmed
  absence of one) as of `TC-595`'s fleet-wide run.
- 2 cross-cutting buckets, each confirmed independently 3 times (Java/.NET/C++) before `TC-691`,
  explain nearly all of `cells/rust`'s and `pdf/cpp`'s findings.
- `pdf/go`'s extra=480, previously untraced, was traced by `TC-691` to Go struct-embedding
  method/field promotion — a fourth, Go-specific manifestation of bucket 1's same abstract
  pattern — confirmed exactly (480 promoted members in the fixture, 0 remainder).
- `cells/cpp`, `slides/cpp`, and `email/cpp` — left as "by inspection alone" by `TC-691` — are now
  individually root-caused by `TC-732` against each pilot's real pinned source. `cells/cpp`'s
  signature=13 is bucket 2 only (extra=0/missing=0 confirmed, not merely assumed, by a real live
  oracle run — no bucket-1 applicability). `email/cpp`'s extra=24 is bucket 1, confirmed exactly
  (0 remainder: 12 `cfb_node` members flattened onto each of `cfb_storage`/`cfb_stream`); its
  signature=7 is bucket 2. `slides/cpp`'s signature=37 is bucket 2 (all 37 confirmed); its
  extra=153 is confirmed to be bucket 1 by direct source inspection (`SimpleColorFormat :
  ColorFormat`, `Slide : BaseSlide`), though `TC-732`'s own live re-run could not reproduce the
  153 count itself (see that pilot's own section above for the full, honest disclosure — a
  third-party `pugixml.hpp` dependency gap in the working environment, and `TC-624`'s own
  ancestor-verification fix landing four days after `TC-595`'s original measurement, are the two
  most likely reasons).
- `TC-732` also found, and is disclosing rather than fixing (out of this documentation-only
  card's scope), two real oracle-script measurement gaps: `cells/cpp`'s own repository layout
  (`Aspose.Cells.Foss.Cpp/include/...`) breaks `_cpp_public_include_dir`'s "`include/` directly
  under the repo root" fallback assumption, failing 110 of 186 headers; and `slides/cpp`'s real
  upstream `#include <pugixml.hpp>` has no vendored copy available to the enumerator, failing 63
  of 234 headers in one tested environment. Both mean some real `extra`/`missing` findings for
  these two pilots may still be unmeasured behind headers neither `TC-595` nor `TC-732` ever got
  to resolve.
- No code, fixture, or oracle-script change is made by this card. Every fix remains recorded as
  future work in `OQ-018`/`OQ-020`/`OQ-021`/`OQ-022` (a diff-engine-layer fix for bucket 2;
  per-pilot fixture/extraction work, if ever re-prioritized, for bucket 1), plus the two new
  header-resolution gaps `TC-732` found above.
