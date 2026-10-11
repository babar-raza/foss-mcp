# Releasing the standalone demo image

This page is for the operator. It follows one image from a build, through the checks that decide whether it may be
hosted, to a record in the project's state machine that cannot be lost. Every registry host below is a placeholder
(`registry.example.internal:5000`); substitute your own. No tool in this repository stores a registry credential,
and none should appear in this file, in a command line you commit, or in a record.

The rule the whole page serves: **an image without a passing gate report for that exact image id is not hosted.**

## 1. What the standalone demo image is

The demo image is the `demo` stage of `Dockerfile.serving`. It bakes in the ingested data of every pilot in the
Helm chart (`ingestion.pilots` in `infra/helm/foss-mcp/values.yaml`, currently 40), so a peer needs nothing but
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

2. Build the `demo` stage from a clean tree at the commit you want to ship, with an explicit tag that is not
   `latest`, and label it with the full commit id. The label is mandatory: `gatectl release-record` reads
   `org.opencontainers.image.revision` and refuses an image that lacks it, or whose commit is not in this history.

   ```powershell
   $sha = git rev-parse HEAD
   docker build --target demo --label org.opencontainers.image.revision=$sha -t foss-mcp-serving:demo-$($sha.Substring(0,7)) .
   ```

   `scripts/release/build_images.py` builds the same labelled images from a `git archive` export of `HEAD`, which
   guarantees that nothing outside the commit reaches the build. It builds the `runtime` stage of the serving image
   (not `demo`) and the ingestion image, so it does not produce the demo image on its own. Use it for those two;
   use the `docker build --target demo` command above, with a clean tree, for the demo image.

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
