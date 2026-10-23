# Releasing the standalone demo image

This page is for the operator. It follows one image from a build, through the checks that decide whether it may be
hosted, to a record in the project's state machine that cannot be lost. Every registry host below is a placeholder
(`registry.example.internal:5000`); substitute your own. No tool in this repository stores a registry credential,
and none should appear in this file, in a command line you commit, or in a record.

The rule the whole page serves: **an image without a passing gate report for that exact image id is not hosted.**

Status of this image: it is a release candidate for peer testing, not a final release. The independent
pre-production review (REQ-G3-008) is not closed by this page, and nothing here claims that it is. Two kinds of
limitation apply and a peer should read them first: the review is open, and several extractor and documentation
improvements were cut from this release (section 9). No pilot index is partial: every pilot indexes the whole public
surface the extractor found at its pinned commit (for example pdf/java holds 1,239 of 1,239 types). The server still says
how much it holds: symbol chunks carry an `API-Coverage:` line and a miss carries a `coverage_note` only when an index
is partial (section 9, "What tells an agent"). The accepted findings are in section 8 and in
`docs/REVIEW_LEDGER.md`.

## 1. What the standalone demo image is

The demo image is the `demo` stage of `Dockerfile.serving`. It bakes in the ingested data of every pilot in the
Helm chart (`ingestion.pilots` in `infra/helm/foss-mcp/values.yaml`, currently 32), so a peer needs nothing but
Docker: no cluster, no PersistentVolumeClaim, no ingestion Job and no start-up wait.

One image, one container per pilot. A container serves exactly one pilot, chosen at start by
`-e FOSS_MCP_PILOT=<family>/<platform>` (`family_platform` also works). The image's entrypoint,
`infra/demo_entrypoint.py`, sets that pilot's identity from the matching directory under `/data/manifests` and
replaces itself with the HTTP server on port 8080. `docker run IMAGE` with no pilot prints what is baked in and
exits 2; `docker run IMAGE --list` prints one line per baked pilot.

Per-pilot isolation is deliberate and is kept. Each pilot's retrieval index, readiness probe and memory budget stay
its own, so one pilot's data can never answer another pilot's question, and a container that runs out of memory
takes down only its own pilot. The cost is one process per pilot you want to run, which is why the image is made
lean (next section) and why the gate measures memory per container.

## 2. How it is built

1. Compact the baked manifests. Every 4096-dimension chunk vector bloats a generation file by tens of megabytes,
   and the only consumer of the vector index (the `find_examples` fallback) can return only chunks whose text
   contains the `Example:` marker.

   ```powershell
   python scripts/release/compact_demo_manifests.py --dry-run
   python scripts/release/compact_demo_manifests.py
   python scripts/release/compact_demo_manifests.py --check
   ```

   The tool is offline and idempotent. Per scope it keeps only the active generation file and only the vector
   points that carry the example marker. Commit the result: the image is built from committed bytes.
   The committed tree stays under 45 MB (the lean ceiling); the exact budgets are pinned in tests/deploy/test_compact_demo_manifests.py.

2. Build the `demo` stage from a clean tree at the commit you want to ship, with an explicit tag that is not
   `latest`, and label it with the full commit id. The label is mandatory: `gatectl release-record` reads
   `org.opencontainers.image.revision` and refuses an image that lacks it, or whose commit is not in this history.

   ```powershell
   $sha = git rev-parse HEAD
   docker build -f Dockerfile.serving --target demo --label org.opencontainers.image.revision=$sha -t foss-mcp-serving:demo-$($sha.Substring(0,7)) .
   ```

   `scripts/release/build_images.py` builds the same labelled images from a `git archive` export of `HEAD`, which
   guarantees that nothing outside the commit reaches the build. It builds the `runtime` stage of the serving image
   (not `demo`) and the ingestion image, so it does not produce the demo image on its own. Use it for those two;
   use the `docker build -f Dockerfile.serving --target demo` command above, with a clean tree, for the demo image.

## 3. The pre-deployment gate

`scripts/release/image_workflow_gate.py` runs the exact image that would ship, one container per pilot, under an
explicit budget (default 256 MiB of memory and 0.5 CPU per container, read-only filesystem, loopback port only, no
mounts, no command override), and proves each container through a real MCP session.

```powershell
python scripts/release/image_workflow_gate.py --image foss-mcp-serving:<tag> --report-dir gate-report
```

For each pilot it waits for `/readyz` to answer 200, runs the eleven checks of
`scripts/poc/pilot_workflow_proof.py`, then three task-oriented workflow entries (`W1` the tool registry lists
`lookup`, `search_symbols`, `get_symbol`, `list_members` and `find_examples`; `W2` `lookup` answers a natural
question and names the pilot's probe symbol; `W3` `find_examples` returns a snippet for a verified example, or is
`not_applicable` when the pilot has none). It samples `docker stats` memory before and after, and removes the
container whatever happens. A pilot fails if any applicable entry fails, if it never became ready, if it was
OOM-killed or exceeded the budget, or if its memory could not be measured. A `not_applicable` entry is never a pass.

The report goes to `--report-dir`: one `<family>_<platform>.json` per pilot and `summary.json`. `summary.json` is the
file the state machine reads. It carries the image's full `sha256:` id, the verdict (`PASS` or `FAIL`), the pilot
counts, the budget, and one object per pilot (the record tool cross-checks that list against the chart). Run the gate
against the exact image you will host: its report names that image id, and a report for any other image proves
nothing about this one. A run with `--only` is a diagnostic and never release evidence.

## 4. Hosting

Hosting needs an internal registry host and a login that only you hold. That is owner item **OWNER-13**; it blocks
gate exit for G3 and nothing else. Until you provide the host and run the login, an image stays `LOCAL_ONLY`.

1. Log in yourself, in your own terminal. No tool in this repository reads, passes or stores a registry credential,
   and none has an option for one:

   ```powershell
   docker login registry.example.internal:5000
   ```

2. Check the commands, then push with an explicit registry:

   ```powershell
   python scripts/release/push_image.py --image foss-mcp-serving:<tag> --registry registry.example.internal:5000/foss-mcp --dry-run
   python scripts/release/push_image.py --image foss-mcp-serving:<tag> --registry registry.example.internal:5000/foss-mcp --record push-result.json
   ```

   `push_image.py` refuses an image without an explicit tag other than `latest` and a registry that is not a plain
   `HOST[:PORT][/PATH]`. It tags the image, pushes it, and reads the `digest: sha256:...` line from the push output; a
   push with no digest is not a success. `push-result.json` carries exactly `image`, `pushed_reference`, `digest`,
   `local_image_id` and `timestamp`.

### Delivery runbook: push, pull back, record

Rehearsed on 2026-10-19 against a throw-away local `registry:2` container (SUP-7); the real delivery repeats the same steps with the delivery registry the operator names. The GitLab project registry that was probed on 2026-10-19 is NOT the delivery registry and is never pushed to.

1. Preconditions: the image passed the gate 32/32 and the adversarial suite, `gatectl release-record` wrote the record, and the operator has run `docker login <registry host>` in this shell's Docker config (an agent never logs in and never reads the credential).
2. `python scripts/release/push_image.py --image NAME:TAG --registry HOST[:PORT][/PATH] --dry-run` prints the tag and push commands; run it without `--dry-run` and `--record push-result.json` to push. The result file carries the pushed reference, the local image id and the digest. With the containerd image store (Docker 29 here) the image id, the registry manifest digest and the `Docker-Content-Digest` header are the same `sha256:` value; the rehearsal confirmed it by reading the manifest from the registry.
3. Prove the registry copy, not the local one: remove every local tag of the image first (`docker rmi <pushed reference> NAME:TAG ...`), otherwise the pull reuses the local layers and a half-pushed image would still verify. Then run `python scripts/release/verify_pushed_image.py --push-result push-result.json --record <release record> --report-dir DIR`. It pulls by digest, checks the manifest digest, the image id and the `org.opencontainers.image.revision` label against the record, then runs the 32-pilot workflow gate and the adversarial suite against the digest reference. Exit 0 means verified; the report `verify_pushed_image.json` says which step failed otherwise.
4. Record: `gatectl release-record --push-result push-result.json`, then `gatectl gate-exit G3`, then the push-log row in `evidence/release/push-log.jsonl`.
5. Rehearsal result: push, pull by digest, inspect, manifest, image id and revision steps passed; the gate step FAILED 22 of 32 on purpose of the data: the rehearsed image was the old demo-91dcf9b whose baked Python generations no longer match the fixtures regenerated by TC-532, which is exactly what the gate must report. The final image is gated again after the re-ingest.

## 5. Recording

Recording is what makes the image impossible to lose. From the repository root:

```powershell
python ops/gatectl.py release-record --image foss-mcp-serving:<tag> --gate-report gate-report/summary.json --push-result push-result.json
python ops/gatectl.py state --write
git add evidence/release project/state.yaml
git commit
```

Leave `--push-result` out to record an image that is built and gate-tested but not yet hosted; run the command again
with it after the push. `release-record` asks docker about the real local image and writes nothing unless all of these
hold:

- the reference has an explicit tag other than `latest`;
- the image carries the revision label, and that commit is an ancestor of `HEAD`;
- the gate report is for this image id, its verdict is `PASS`, every pilot in the chart passed, and the pilots it lists
  are exactly the chart's pilots;
- a push result, when given, has a valid `sha256:` digest, a usable pushed reference, and a `local_image_id` equal to
  this image's id.

It then writes `evidence/release/<name>__<tag>--<8 hex>.json` (the reference with `:` as `__` and `/` as `_`, plus an
8-character hash of the full reference, so two references can never share a file) and a copy of the gate report beside
it as `...gate-report.json`. The record is validated against `schemas/release-image.schema.json`. Its status is
`PUSHED` when a registry digest is present and `LOCAL_ONLY` otherwise. Re-recording a tag with a push result upgrades
it; recording again without one never downgrades it; recording a tag for a different image id is refused, so use a new
tag. `gatectl state --write` then derives the `release_images` section of `project/state.yaml` from these files. That
section is derived, never typed by hand.

## 6. What the checks say about it

- `gatectl validate` checks every file in `evidence/release`: it matches the schema; its file name and the gate-report
  path are the ones derived from the image reference; the source commit is in this history; the copied gate report
  exists, its sha256 matches the record, and the numbers in the record still agree with the report itself; the status
  agrees with the presence of a registry digest. It also asserts that the committed `project/state.yaml` equals a fresh
  rebuild.
- `gatectl gate-exit G3` additionally requires at least one record that validates, is `PUSHED`, and carries a `PASS`
  gate report in which every pilot in the chart passed, built from a commit in this history. A `LOCAL_ONLY` image does
  not satisfy it; that is the point at which OWNER-13 matters.
- `tests/deploy/test_release_record_contract.py` pins the record's schema as a contract in CI, and checks that every
  committed record has a gate report whose sha256 matches.

## 7. How a peer pulls and runs it

Prefer the digest from the record (`registry_digest` in `project/state.yaml`) to the tag, so the peer gets exactly the
image that was gate-tested:

```powershell
docker login registry.example.internal:5000
docker pull registry.example.internal:5000/foss-mcp/foss-mcp-serving@sha256:<digest-from-the-record>
docker run --rm <image> --list
docker run -d --rm --read-only --tmpfs /tmp --memory 256m --cpus 0.5 -p 127.0.0.1:8080:8080 -e FOSS_MCP_PILOT=pdf/net <image>
```

The pilot is one of the names `--list` prints. Wait for `http://127.0.0.1:8080/readyz` to answer 200; the container
serves one pilot, so start one container per pilot you need, each on its own host port. The 256 MiB and 0.5 CPU figures
are the budget the image was gated under, not a minimum.

## 8. Known limitations

The image serves 32 pilots. The eight jmap pilots (jmap/cpp, jmap/go, jmap/java, jmap/net, jmap/nodejs, jmap/python, jmap/rust and jmap/typescript) were removed from it on 2026-10-16 because their upstream GitHub repositories no longer exist, so they can neither be extracted nor verified; no entry below applies to them.

These are findings of the independent adversarial reviews that this release accepts rather than fixes. The full
ledger is `docs/REVIEW_LEDGER.md`; each entry below repeats one accepted finding with its reason.

### Example search without the embedding gateway

`find_examples` searches the verified examples lexically first. Its vector stage needs the embedding gateway at run
time to embed the query, because the example vectors baked into the image come from the 4096-dimension gateway model.
Without `FOSS_MCP_EMBEDDING_GATEWAY_ENDPOINT` and `FOSS_MCP_EMBEDDING_GATEWAY_API_KEY` the container searches
examples lexically only, and the mode is `lexical-only`. With both set, and a configured dimension that matches the
stored vectors, the mode is `lexical+vector(gateway)`. The mode means configured, not reachable: nothing checks that
the gateway answers.

You can see the mode in three places, and all three come from the same helper, so they cannot disagree:

- the `example_search` field (with `example_search_reason`) of `report_index_freshness`;
- one startup log record with the message `example search mode`, which carries `example_search`,
  `example_search_reason`, `family` and `platform`;
- the `X-Example-Search` response header on `/readyz`, on both the 200 and the 503 answer, read from the generation
  that is active when the request arrives.

There is no offline fallback, and this is measured, not assumed. The obvious candidate was the hashing embedding
provider, which needs no network. On the 14 verified example chunks of the six pilots that have them, at 64, 256 and
1024 dimensions, the examples' own titles scored 0.159 to 0.599, while unrelated queries such as "bake a chocolate
cake" and "send a fax via modem" scored up to 0.507 at 64 dimensions and up to 0.267 at 1024. No similarity floor
separates the two. Every true query already has full lexical coverage, so the lexical path answers it before a
vector stage would run. A hashed bag of words sees only the tokens the lexical path already sees, so the only extra
queries it could answer are the partial-overlap ones that the lexical coverage floor rejects on purpose, and that
would bring back confident answers to nonsense. So a container without the gateway answers examples lexically, and
says so.

### Standalone image

- **L-022** A request whose `MCP-Protocol-Version` header is `2026-07-28` is rejected. The server accepts only the
  protocol revisions a client reaches through the `initialize` handshake, and 2026-07-28 is the stateless revision,
  which has no handshake. A client that starts with `initialize`, as the MCP SDK clients do, is not affected.
- **L-054** A `find_examples` query of one or two distinctive words is answered only by an example whose title
  contains one of them, so `save` no longer returns every example whose code calls Save. A longer question needs 60%
  of its other words in the example, so "split a pdf into pages" or "bake a cake" get an honest miss. The
  product-family word never counts. Every example states in `match_basis` what its coverage number means. Ask with a
  few specific words to get a specific example.
- **L-071** Fixed in this release (TC-507, TC-518, TC-542; review finding AGT-02, D-12): get_product_reference now
  fills formats and limitations from the pilot's own indexed README sections (Key Capabilities, Scope and Limitations)
  instead of answering a fixed NotAvailable, so a peer sees the real supported-format and limitation list for every
  pilot that documents one.
- **L-072** The four C++ pilots (pdf/cpp, cells/cpp, slides/cpp and email/cpp) have no package-registry
  check, because C++ has no package-manager coordinate to check. Their install answer says there is no
  package-manager install command and points to where the distribution notes are. A committed test requires the
  registry file to be absent for exactly these four pilots, so the gap cannot hide another one.
- **L-080** Fixed in this release (TC-506; review finding GRR-04, GRR-01): /readyz now requires real floors before
  answering ready (at least 3 indexed symbols, 1 documentation chunk, and a generation whose scope, family and
  platform match this deployment), so a near-empty or wrong-scope generation is reported not ready instead of
  masquerading as healthy.
- **L-089** The server describes each library at one pinned commit, but the install command installs the registry's
  current release, which can be older or newer. pdf/go's index is 66 commits past v0.9.0 and slides/python is 50
  commits past PyPI 26.8.0, so a peer may find a type in `search_symbols` that the installed package lacks. Every
  install answer carries a notice naming the indexed commit, and Go answers give `go get <module>@<commit>`. The server
  does not compute the difference, because that needs network access at ingestion and a new data file per pilot; it is
  planned after the pilot.
- **L-090** Class-expression mixins (an object of functions applied to a class with `Object.assign`) are not flattened by
  the extractor. The only pilots that were built this way were removed from the image, so no pilot of the image is
  affected, and the extractor limitation stays accepted with that risk.
- **L-091** Go and Rust symbol names are package-relative (`Cells`, `session::Session`) and are not import paths. The
  tool descriptions say so, and for Go the install answer gives the module path, which is the import root. A peer still
  has to read the README for sub-package or crate paths.
- **L-095** Ingestion refuses to publish only when the document count drops by more than half. There is no automatic
  gate on signature quality, such as the share of empty types or zero-parameter methods. Signature fidelity in this
  release is pinned by per-language extractor tests, per-pilot fixture tests, and a re-run of the review harnesses on the
  final image. A signature defect in a pilot those tests do not sample could still ship.
- **L-097** Fixed in this release (TC-525, TC-535; review finding RST-08): Go typed constants declared in const (...)
  groups are now folded into their owning type's Members list and indexed by source rather than by centrality, so the
  named constant values the README examples use are retrievable through get_symbol and list_members.
- **L-098** Over stdio, when the client closes stdin while requests are still running, the MCP SDK cancels those
  requests and their replies are lost. A client that waits for its replies before closing is not affected. The
  standalone image serves HTTP, where this does not apply.
- **L-099** Every tool result carries its JSON both as text and as `structuredContent`, which the MCP specification
  recommends for client compatibility. `get_symbol` also carries `raw_text`, the full indexed chunk, beside the parsed
  fields, so its answers are large (about 55 KB for pdf/net `Document`). Clients that read `structuredContent` can
  ignore the text.
- **L-100** Fixed in this release (TC-509; review finding PS-03): /metrics now exposes a full Prometheus exposition
  with family, platform, tool and outcome labels and histograms, not just the two queued and dropped usage-event
  counters, and rejections and readiness failures are logged with a reason.
- **L-102** Fixed in this release (TC-518, TC-542; review finding AGT-02, D-12): pdf/cpp's install section now carries
  real build-from-source steps drawn from its README instead of answering NotAvailable, because C++ coordinates still
  have no package-manager registry to check against in this release.
- **L-105** Each pilot's index holds exactly one version, the pinned commit. `lookup` ignores version words in a
  question (such as "in version 0.1.0") and answers for the indexed version. Use `list_recent_changes` for release
  history and `get_product_reference` install for the indexed commit.
- **L-106** There is no reverse discovery, such as "which method returns a Page". `search_symbols` matches names, not
  return types. It is planned as a later capability.
- **L-107** Fixed in this release (TC-515, TC-517, TC-562, TC-563; review finding RST-09): search_docs now classifies
  documentation by the chunk's own section title rather than keyword hints, so troubleshooting and FAQ content that
  exists in a pilot's README is served as such instead of collapsing into a single generic stub or an unrelated
  category.
- **L-108** Registry verification proves that a package name exists on its registry, not that it is published from the
  configured repository. PyPI `aspose-3d-foss` points to a different repository URL, and cells/typescript's generic
  `excel-cells` does not exist today, so its install answer already says not found, but it would be served as installable
  if someone registered the name. Checking the registry's repository URL is planned after the pilot.
- **L-109** Some upstream packages are themselves defective: the 3d/net package does not work, 3d/python OBJ export is
  buggy and cells/typescript ships a demo-only entry point. These are upstream
  defects. The MCP reports the packages as they are, and `list_recent_changes` labels templated notes.
- **L-110** One item (B08) of the second independent audit was never specified, and no text for it exists anywhere. The
  third and fourth review rounds re-verified every earlier finding and found nothing unmapped. It is retired as
  unidentifiable, and the owner closes it as OWNER-11.
- **L-111** Integration replays worker commits onto main with new hashes, so an ancestry check of the worker commit
  cannot prove integration. A card's integration is proven by its `(G3/TC-NNN)` commit subject on main and its
  acceptance receipt.
- **L-119** `/readyz` reports ready when the pilot's lexical documents are servable. It does not check the
  product-reference or release side files, which are read once at start. If they are missing, those sections answer
  NotAvailable or an empty list.
- **L-120** In the Kubernetes deployment a new generation can be published while a client session is open. The
  standalone image bakes one generation and never re-ingests, so this cannot happen there.
- **L-121** Task-to-API coverage (about 59% of sampled tasks resolved to the right API) was measured in review round 4,
  before the G3 fixes, and was not re-measured on the final image.
- **L-122** cells/rust and cells/typescript answer compatibility NotAvailable, because their manifests state
  no rust-version or engines. The server does not infer one.
- **L-123** The image healthcheck probes every 30 s, so `docker ps` can show "starting" for up to about 30 s after the
  server is ready. The server answers as soon as it starts. A shorter start interval needs Docker Engine 25 or later.
- **L-124** For Rust types, `Bases:` lists implemented traits, including derived ones (`Debug`, `Clone`). Rust has no
  class inheritance, and these are real trait implementations.
- **L-125** `Kind:` lines show the parser's node names (`class_declaration`, `type_spec`, `struct_item`) instead of one
  vocabulary. pdf/cpp's `foundation::` namespace and page/python's `aspose.page.mcp` package are indexed because the
  source declares them public.
- **L-126** For Java and C++, a no-argument `getX()` or `isX()` method is also listed as a property `x`. The method
  itself stays listed. This is a convenience view, not a separate member.
- **L-127** `list_members` returns one flat list of member names without a kind per member. `lookup` answers in one of
  four shapes with no shape field, and clients tell them apart by their fields.
- **L-128** Fixed in this release (TC-515, TC-533; review finding AGT-08, RST-12): Identical-text chunks are now
  deduplicated before serving, and pdf/typescript's FAQ chunks, re-extracted at its single re-pinned commit by TC-533,
  no longer split a question from its answer into separate same-titled chunks, so a search result no longer shows the
  same title more than once for that case.
- **L-129** A repeated `initialize` on an open HTTP session is accepted and replaces that session's client information
  (MCP SDK behaviour). It does not affect other sessions.
- **L-133** The only pilot whose README example verifier added a library directory to the include path so that a quoted
  `#include` line compiles was removed from the image, so no pilot of the image is affected.
- **L-137** The stdio and HTTP transports frame a malformed request line slightly differently, consistent with each
  transport's own protocol; neither transport crashes, leaks data, or serves wrong content as a result. (Review
  finding PS-09.)
- **L-138** This release indexes verified examples only from each pilot's README fences; ingesting additional sources
  such as repo sample programs, CHANGELOG.md or llms.md is deferred to a future release. (Review finding AGT-06.)
- **L-139** Upstream agent_guidance that contradicts the indexed API is already served with an untrusted marker, which
  the pre-production review confirmed is the correct behavior; selectively dropping such sections is not implemented
  in this release. (Review finding AGT-10.)
- **L-140** find_examples reaches a minority of a pilot's indexed types because verified examples are sourced only
  from README fences in this release; this is a known coverage ceiling, not a retrieval defect, and is linked to the
  same README-only sourcing decision recorded elsewhere in this section. (Review finding D-07.)
- **L-142** The pdf family's confidently-wrong retrieval rate (a non-empty answer, not labelled partial, that holds
  none of the intended API in its first ten items) measures higher than every other family's. pdf's platform
  libraries name the same real-world task under structurally different, unrelated API vocabulary across platforms
  (merging two PDFs, for example, reaches `PdfFileEditor.Concatenate` on one platform and `Document.Merge` or
  `Merger` on another), so the miss is spread across many query/API pairs rather than concentrated in one fixable
  bug. This is a retrieval-coverage ceiling of the fixed corpus, not a defect, and no fix date is set. (G3/TC-579.)
- **tex/python** serves one verified example, not two. The pinned upstream library (aspose-tex-foss, commit 181015d2,
  which is also the upstream head) does not import: `src/aspose_tex/_input/catcode.py` has an IndentationError at line
  66, so no example that imports `aspose_tex` can run and none can be verified. The rest of the tex/python content is
  documentation and API symbols. The fix is upstream; the acceptance test holds this one pilot to its measured count
  and requires every other pilot to serve at least two.

### Kubernetes chart (not part of the standalone release)

- **L-115** The serving pod's init container waits for the product-reference and recent-releases side files
  with a bounded deadline (`initGate.timeoutSeconds`, default 3900 s). A GitHub 403 during ingestion that holds
  past that bound no longer leaves the pod stuck in Init: the gate logs which files are missing and exits 0, and
  the pod starts WITHOUT them (degraded), not stuck. The standalone image has no init container.
- **L-116** `scripts/deploy/install_pilots.py` runs `helm upgrade --install --wait` without `--atomic`, so a failed
  install is not rolled back automatically. This is an operator tool, not part of the image.
- **L-117** The chart's NetworkPolicy allows traffic between all pods in the namespace, so pilots can reach each
  other. The standalone image runs one pilot per container.
- **L-118** The chart does not `required()` the family and platform values, so an unset release falls back to pdf/net.
  The standalone entrypoint refuses to start without a valid pilot.

- **L-134** All of this release's pilots are deployed from one image tag per gate, verified by the fleet-consistency
  test at deploy time, not by a one-time manual check. (Review finding FK8S-04.)
- **L-135** This release's own namespace retains only the generations its own retention policy keeps; pods or PVCs in
  other namespaces on a shared cluster are outside this product's control. (Review finding FK8S-10.)
- **L-141** The richer CMake-based install guidance for a pilot whose README documents install via a manifest path
  or a separate README document depends on a working GitHub token in the cluster's ingestion Job. That token is not
  currently available, so no pilot's baked product-reference sidecar carries these fields yet; ingestion now logs
  this fetch's failure visibly instead of swallowing it silently, and the gap closes on its own once the token is
  replaced, with no further release action needed. (Review finding RD3-080.)

### Reported, not defects

The reviews reported these, and they were checked and are not defects. They are listed so that every reported
finding has a published answer.

- **L-104** Reported: a class with a member named X outranks the types named X in `search_symbols`. Not reproduced:
  `search_symbols` ranks type-name matches above member-name matches, so "PageSize" and "IPresentation" return the
  type first. The three words/python `Document` results are three real upstream classes.
- **L-130** Reported: imaging/net serves only 3 types. Its extraction is not truncated (`type_count` 3, `truncated`
  false), so these three types are the library's whole public surface.
- **L-131** Three reported problems were false alarms. pdf/net's license is MIT, as its csproj states;
  `infra/rollback.py` is a tested operator command (tests/infra/test_rollback.py), not dead code; and repeated
  identical queries give identical answers.
- **L-132** Reported: `gatectl validate` failed during one audit. It was a transient mismatch while acceptance
  receipts were being written; `gatectl validate` passes on main and is run again at gate exit.

- **L-136** A developer-tooling docstring in prove_pilots.py has stale wording from an earlier, smaller fleet size;
  the script's behavior and constants are correct and unaffected. (Review finding FK8S-12.)

## 9. Deferred extraction improvements

These extractor improvements were planned and are not in this release. The full list of review findings is in `docs/REVIEW_LEDGER.md`.

- **TC-397** Would have fixed C++ extraction: parameters read from the declarator, out-of-line member definitions attached to their class, free functions published only from public headers, header-only constructors captured. Affected pilots are the four C++ pilots: pdf/cpp, cells/cpp, slides/cpp and email/cpp. A peer may see C++ parameter types without qualifiers or defaults, constructors missing, some free functions defined in headers missing, and functions defined only in `.cpp` files still listed.
- **TC-398** Would have made Go, Rust and TypeScript publish only their public API, with methods attached to their receiver type and import paths for symbol names. Affected pilots are pdf/go and cells/go (Go), cells/rust (Rust), and pdf/typescript, cells/typescript and 3d/typescript (TypeScript). A peer will see Go and Rust symbol names that are not import paths, and non-exported helpers or trait boilerplate listed as top-level functions.
- **TC-402** Would have re-extracted the four C++ fixtures at their pinned library commits with the corrected C++ extractor. Affected pilots are pdf/cpp, cells/cpp, slides/cpp and email/cpp. A peer will see the earlier C++ signatures unchanged.
- **TC-403** Would have re-extracted the six Go, Rust and TypeScript fixtures at their pinned library commits. Affected pilots are pdf/go, cells/go, cells/rust, pdf/typescript, cells/typescript and 3d/typescript. A peer will see the earlier fixtures unchanged.
- **TC-428** Would have fixed Go struct fields whose tag was published as their type. Affected pilots are the Go pilots pdf/go and cells/go. A peer may see a field type shown as a tag such as `json:"id,omitempty"` instead of the Go type.
- **TC-475** Would have filled empty JavaScript parameter and return types from the JSDoc tags. The only pilot it concerned was removed from the image, so no pilot of the image is affected.
- **TC-526** Would have fixed Java and Rust signature fidelity. Java (3d/java, cells/java, pdf/java, slides/java): a varargs parameter is dropped (`setPages(int... pages)` is served `setPages()`, 6 of 6 cases measured), generic bounds and `throws` clauses are not shown, public fields are not listed (34 properties of 3d/java such as the Quaternion `x`, `y`, `z`, `w`), no method is marked `static` (none of the 71, 76 and 159 static methods counted in three Java pilots), and a getter such as `isIdentity()` is also listed as a property `identity` that reads like a field. Rust (cells/rust): no method records its receiver, so cells/rust's 259 associated functions look like its 1,434 methods, and 6 cells/rust chunks are derive boilerplate. The `API-Coverage` line counts types, so no response marks these gaps.
- **TC-527** Would have served member documentation for Java and C# (13 pilots: the five Java and the eight .NET pilots). The first sentence of each member's comment is already in the fixtures (pdf/net 3,887 of 4,887 members of the first 300 types, cells/java 2,199 of 2,677, words/net 3,363 of 3,506) but no symbol chunk renders it, and the Javadoc `@param`, `@return`, `@throws` and C# `<param>`, `<returns>`, `<exception>` text is dropped. In 65 blind coding tasks on 13 pilots the served signatures were never wrong (0 in 1,037 calls), but 8 of 13 option, error and format tasks forced a guess and none of the 13 was fully answered. A peer gets signatures without parameter meaning, units, index base or exceptions.
- **TC-528** Would have served the same member documentation for Python (12 pilots), TypeScript and Node.js (5), Go (3), Rust (2) and C++ (5). Docstring Args, Returns and Raises, TSDoc, godoc, rustdoc and Doxygen text is not served. For example, the second `Document.save` call of pdf/python raises `FileExistsError` because `overwrite` defaults to False, which its docstring states and the index does not. A Python summary wrapped over two lines is served cut mid-sentence, and a sentence containing `e.g.` is cut there (cells/go `Cells`).
- **TC-531** Would have re-extracted the five Java fixtures at their pinned commits with the fidelity and documentation work, and raised pdf/java from 300 to its whole 1,239 types. The whole pdf/java surface (1,239 types) is indexed since TC-550, but the fidelity and documentation work still was not done: a peer sees the earlier Java signatures and documentation.

### What tells an agent

The coverage caveats that already exist are the only signal. Every symbol chunk carries an `API-Coverage:` line, for pdf/java `1239 of 1239 types`, and a pilot with functions adds a functions clause. `get_symbol` misses carry a `coverage_note`, and `search_symbols` and `lookup` misses carry it too (TC-515, TC-520). `report_index_freshness` returns `indexed_types`, `total_types` and the same note. Nothing marks a missing member, a dropped varargs parameter, a missing static mark or missing parameter documentation, so a peer must read the library's source or README for those.

### Not part of this release

`mcp-gateway`, the nginx reverse proxy of the development cluster, is not part of the release. It exists only in the cluster, is not in git, and routes only 14 of the pilots. Neither is anything in the Kubernetes chart's cluster install beyond what section 8 already lists under the chart. The standalone image is the release.
