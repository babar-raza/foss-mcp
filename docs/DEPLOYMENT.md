# Deploying and proving the thirty-two pilots

This is the runbook for an operator who was not in the session that proved the pilots. It covers the
Helm chart at `infra/helm/foss-mcp`, the three tools that install and prove it, and what to do when a
step fails. Everything here is taken from the repository; a value this document does not give is
found in `infra/helm/foss-mcp/values.yaml`, not guessed.

The chart deploys one release per pilot. Each release has one serving pod (`foss-mcp-serving`), one
ingestion Job per pilot, and its own manifests volume. Thirty-two releases serve thirty-two pilots. `ingestion.pilots` lists 32 pilots; the pilots that were
removed from the chart are described in `docs/RELEASE.md` section 8.

The three tools are `scripts/deploy/install_pilots.py`, `scripts/deploy/prove_pilots.py`, and
`scripts/deploy/pilots.py`. The last one holds no command: it reads the chart's `ingestion.pilots` from
`infra/helm/foss-mcp/values.yaml` and maps each pilot to its release and its minimal values. The other
two use it.

## Prerequisites

- **Docker**, running. `scripts/release/build_images.py` builds the images with it.
- **A Kubernetes cluster** and a current `kubectl` pointed at it. The steps below use `kind` for a
  local cluster.
- **Helm 3** (`helm version` must report v3).
- **kubectl**, with access to the target namespace.
- **The repository's pinned toolchain.** From the repository root in PowerShell:

  ```powershell
  . .\scripts\toolchain\activate.ps1
  ```

  The tools are Python. Run them with the repository's Python, which has PyYAML and pytest.

## 1. Build the two images

```powershell
python scripts/release/build_images.py
```

This builds `foss-mcp-serving` and `foss-mcp-ingestion` from `git archive` of `HEAD`, so the build
context is the committed bytes only. It refuses to run if tracked files have uncommitted changes.
Commit first. It tags both images `rev-<sha7>` of `HEAD` and prints the tag. Use `--tag` to choose
another tag. Keep the tag: every install step takes it as `--image-tag`.

## 2. Make the images available to the cluster

For a `kind` cluster, load both images into it:

```powershell
kind load docker-image foss-mcp-serving:rev-1234567 foss-mcp-ingestion:rev-1234567
```

Replace `rev-1234567` with the tag `build_images.py` printed.

For a remote cluster, pushing the two images to a registry the cluster can pull from is an owner
action. The chart's `image.repository` value (`foss-mcp-serving`, and `foss-mcp-ingestion` for the
Jobs) names the image without a registry, and `install_pilots.py` does not yet take a repository
override. A remote install therefore needs that override set by hand, and the chart's
`image.pullPolicy` is `IfNotPresent`. The standalone demo image has its own push tool; see "Hosting the
standalone demo image" below.

## 3. Install the thirty-two pilots

```powershell
python scripts/deploy/install_pilots.py --namespace foss-mcp --image-tag rev-1234567
```

What it does:

- Creates the namespace if `kubectl get namespace` cannot find it.
- Writes one minimal values file per pilot into `--values-dir`. The default is a folder named
  `foss-mcp-pilot-install` in the system temporary directory. Each file holds that pilot's deployment
  identity and its one `ingestion.pilots` entry.
- Runs `helm upgrade --install <release> infra/helm/foss-mcp -n <namespace> --set image.tag=<tag> -f
  <values> --wait --timeout 30m` for each pilot. Release names are `foss-mcp-<family>-<platform>`.
- Runs at most `--parallel` installs at once (default 2).
- Prints a result table and writes `install-results.json` into `--values-dir`.
- Exits 0 only if every install succeeded.

`helm --wait` does not wait for Jobs. Without `--wait-for-jobs`, a pilot whose ingestion Job FAILED while
its serving pod kept running the previous generation is reported `installed` with stale content. For any
install that must not do that, run with `--wait-for-jobs` (recommended):

```powershell
python scripts/deploy/install_pilots.py --namespace foss-mcp --image-tag rev-1234567 --wait-for-jobs
```

The flag adds `--wait-for-jobs` to each helm command, right after `--wait`. After a successful helm exit the
tool reads that release's ingestion Jobs (`kubectl get job -n <namespace> -l ...,app.kubernetes.io/component=ingestion
-o json`) and records `succeeded`, `failed`, `running`, `missing` or `unknown` as `ingestion` in
`install-results.json`. `INGESTION FAILED` in the table means helm succeeded but the pilot's ingestion Job
failed, so the serving pod is still running the previous generation: the pilot counts as not installed, its
pod states and the last 20 Job log lines are printed, and the exit status is 1. `installed (ingestion
unverified)` means the Job was still running or its state could not be read (no `kubectl`, a non-zero
`kubectl` exit, unparseable output); it does not fail the run, so check that pilot by hand. Without the
flag no extra `kubectl` call is made and nothing about the output changes.

Useful options:

| Option | Meaning |
| --- | --- |
| `--only pdf_net` | Install only that pilot. Repeat the option for several. An unknown name is an error. |
| `--dry-run` | Print each helm command. Run nothing and write nothing. |
| `--parallel N` | Installs running at once. Default 2. |
| `--timeout 45m` | `helm --timeout` for each install. Default 30m. |
| `--wait-for-jobs` | Also wait for the ingestion Jobs and report a failed ingestion as `INGESTION FAILED` (recommended). Off by default. |
| `--embedding-gateway-secret NAME` | Name of an operator-created Secret holding the embedding gateway. Adds `--set-string embeddingGateway.existingSecret=NAME` to every install. See the section on passing the Secrets. |
| `--github-token-secret NAME` | Name of an operator-created Secret holding a GitHub token. Adds `--set-string ingestion.githubToken.existingSecret=NAME` to every install. |

The tool never removes a release or a resource. It runs no uninstall and no delete.

A failed install prints the pod states of that release and the last 20 lines of its ingestion Job log,
so the cause is on screen.

## 4. Prove the thirty-two pilots

```powershell
python scripts/deploy/prove_pilots.py --namespace foss-mcp
```

For each pilot, one at a time, the tool:

1. Starts `kubectl port-forward` to the release's Service on its own local port. The first pilot uses
   `--base-port` (default 8401), the next uses the port after it, and so on.
2. Waits up to 30 seconds each for `/healthz` and `/readyz` to answer 200.
3. Runs `scripts/poc/pilot_workflow_proof.py` against the forwarded endpoint. That script runs eleven
   checks against the pilot's own fixtures under `tests/fixtures`.
4. Stops the port-forward, even when the proof raises.

It prints one row per pilot with the `healthz`, `readyz`, `passed`, `failed` and `n/a` counts. A
pilot with no applicable check does not count as passed, and the tool names it. The tool exits 0 only
if every pilot passed every applicable check. Each pilot's proof report and logs are written to
`--report-dir`, which defaults to `foss-mcp-pilot-proofs` in the system temporary directory.

To prove one pilot: `python scripts/deploy/prove_pilots.py --namespace foss-mcp --only pdf_net`.

## Hosting the standalone demo image

The `demo` stage of `Dockerfile.serving` builds a self-contained serving image with the pilots' manifests
baked in. To host it on the internal registry, do these steps in this order. The registry host is the
operator's to supply; every host below is a placeholder, so substitute your own.

1. Build the demo image from the repository root, with an explicit tag. The tag must not be `latest`:

   ```powershell
   docker build --target demo -t foss-mcp-serving:<tag> .
   ```

2. Run the pre-deployment workflow gate on that exact image (see "The pre-deployment workflow gate"
   below). An image without a passing 32-of-32 `summary.json` is not tagged for hosting and is not pushed.
3. Log in to the internal registry yourself, in your own terminal:
   `docker login registry.example.internal:5000`. The push tool never takes, reads or stores a credential,
   and it has no option for one.
4. Push the image and capture its digest:

   ```powershell
   python scripts/release/push_image.py --image foss-mcp-serving:<tag> --registry registry.example.internal:5000/foss-mcp --record push-result.json
   ```

5. Record the result in the project's state with `gatectl release-record --gate-report <report-dir>/summary.json
   --push-result push-result.json`. Run `python ops/gatectl.py release-record --help` for the other options it
   takes. The record refuses a gate report that is for a different image id or is not a passing report covering
   every pilot in the chart.

What `push_image.py` does, in order, stopping at the first failure:

- Refuses to run, exit 2 and nothing executed, unless a registry is named and is a plain
  `HOST[:PORT][/PATH]`: lowercase host labels, an optional port, optional path segments of lowercase
  letters, digits, `.`, `_` and `-`. A scheme, a space, a `..` segment or a `user:pass@` prefix is refused.
  A refused value is never printed back.
- Refuses an `--image` that is not a local `NAME:TAG` with an explicit tag other than `latest`.
- Confirms the local image exists with `docker image inspect`, then runs `docker tag` and `docker push` for
  `<registry>/<name>:<tag>`.
- Reads the `digest: sha256:<64 hex> size: <n>` line from the push output. A push whose output has no digest
  line is not a success and exits non-zero.
- Prints, and with `--record` also writes, one JSON object with exactly the keys `image`,
  `pushed_reference`, `digest`, `local_image_id` and `timestamp` (UTC).

| Option | Meaning |
| --- | --- |
| `--image NAME:TAG` | The local image to push. Required. The tag is explicit and is never `latest`. |
| `--registry HOST[:PORT][/PATH]` | The internal registry, with an optional path prefix. Required unless the environment variable `FOSS_MCP_REGISTRY` holds it; with neither the tool exits 2. |
| `--dry-run` | Print the `docker tag` and `docker push` commands. Run nothing and write nothing. |
| `--record FILE` | Also write the JSON result to this file, after a successful push only. |

The tool never runs `docker login`, never reads the Docker client configuration, and never prints an
environment variable. Check a new registry value with `--dry-run` before the real push.

### The pre-deployment workflow gate

`scripts/release/image_workflow_gate.py` runs the exact image that would ship, one container per pilot, under an
explicit memory and CPU budget, and proves each container through a real MCP session before the image is tagged
for hosting. It needs a running Docker and the image in the local image store.

```powershell
python scripts/release/image_workflow_gate.py --image foss-mcp-serving:<tag> --report-dir gate-report
```

For each pilot in the chart (`ingestion.pilots`, read through `scripts/deploy/pilots.py`, so the count is never
written down here) the gate:

1. Starts one container the way a peer starts it: `docker run -d --rm --read-only --tmpfs /tmp --memory M --cpus C
   --pids-limit 256 --publish 127.0.0.1::8080 -e FOSS_MCP_PILOT=<family>/<platform> IMAGE`, with a unique
   container name, no volume mount and no command override, so the image's own entrypoint
   (`infra/demo_entrypoint.py`) selects the pilot and starts the HTTP server. The port is bound to loopback only
   and found with `docker port`.
2. Polls `/readyz` until it answers 200 or `--ready-timeout` runs out.
3. Runs the eleven checks of `scripts/poc/pilot_workflow_proof.py` over a real MCP session, then a short
   task-oriented workflow recorded as its own entries: `W1` `tools/list` returns a non-empty registry that includes
   `lookup`, `search_symbols`, `get_symbol`, `list_members` and `find_examples`; `W2` `lookup` answers a natural
   question about the pilot's probe symbol with a non-empty answer that names it (the report says whether the
   question or the bare name satisfied it); `W3` `find_examples` with the title of the verified example the proof
   found returns a match with a non-empty snippet, or `not_applicable` with the reason when the pilot has none.
   Every entry keeps a short excerpt as evidence, not only a boolean.
4. Samples `docker stats --no-stream` memory before and after the checks and keeps the larger value.
5. Removes the container with `docker rm -f`, always: on success, on failure, on a readiness timeout and on Ctrl-C.

A pilot fails when any applicable check or workflow entry fails, when its container never became ready, when it was
OOM-killed or its peak memory is over the budget, when its memory could not be measured, or when `docker run`
itself failed. A `not_applicable` entry is never counted as a pass, and a pilot with no passing entry does not
pass. The gate exits 0 only when every selected pilot passes, prints one line per pilot and the verdict, and never
prints an environment variable's value.

| Option | Meaning |
| --- | --- |
| `--image NAME:TAG` | The image to test. Required. The tag is explicit and is never `latest`; an untagged or malformed reference exits 2 and runs nothing. |
| `--only FAMILY_PLATFORM` | Test only that pilot, for example `pdf_net`. Repeat the option for several. An unknown name exits 2 and runs nothing. |
| `--fixtures DIR` | The fixtures root the proof's expected values come from. Default `tests/fixtures`. |
| `--report-dir DIR` | Where the reports are written. Default: `foss-mcp-image-workflow-gate` in the system temporary directory. |
| `--memory 256m` | Per-container memory limit, passed to `docker run --memory`. Default 256m. |
| `--cpus 0.5` | Per-container CPU limit, passed to `docker run --cpus`. Default 0.5. |
| `--ready-timeout 120` | Seconds to wait for each container to become ready. Default 120. |
| `--parallel 2` | Containers starting at once, at least 1. Default 2. MCP sessions never overlap; only the start-up does. |
| `--dry-run` | Print the docker commands each pilot would get. Run nothing and write nothing. |
| `--with-embedding-gateway` | Pass `FOSS_MCP_EMBEDDING_GATEWAY_ENDPOINT` and `FOSS_MCP_EMBEDDING_GATEWAY_API_KEY` from your environment to each container, by name only (`-e NAME`), so docker reads the value itself and it never appears in a command line, a log or a report. Without the flag they are never passed; with it and either variable unset the gate exits 2. |

Reports. `--report-dir` receives one `<family>_<platform>.json` per pilot (the proof checks, the workflow entries,
the memory samples and the container name) and `summary.json`. `summary.json` is the file the project's state
machine reads: `gatectl release-record --gate-report` needs `schema_version`, `image`, `image_id` (the full
`sha256:` id from `docker image inspect`), `image_size_bytes`, `budget` (`memory` and `cpus`), `generated_at`,
`verdict` (exactly `PASS` or `FAIL`), `pilots_total`, `pilots_passed` and `pilots`, one object per pilot with
`key`, `status`, `checks` (`pass`, `fail`, `not_applicable`), `peak_memory_mb`, `startup_seconds` and `reasons`.

The rule: an image without a passing 32-of-32 `summary.json` (`verdict` `PASS`, every pilot in the chart passed,
on the image id being hosted) is not tagged for hosting and is not pushed. A gate run with `--only` is a diagnostic,
never release evidence. Check the commands with `--dry-run` first.

## What the init container does, and why a pod waits at Init

Each serving pod has an init container, `wait-for-product-reference-and-recent-releases`. It waits until
BOTH sidecar files for the pilot, `product_reference_<family>_<platform>.json` and
`recent_releases_<family>_<platform>.json`, exist on the manifests volume AND are non-empty (an empty or
truncated file does not pass). The ingestion Job for the same pilot writes them as its last steps. The
serving process reads both once, when it starts, so a pod that started earlier would run without them.
The container asks for 10m CPU and 16Mi memory and is limited to 50m and 64Mi (`initGate.resources`).

The wait is bounded. The chart value `initGate.timeoutSeconds` (default 3900) is how long the gate waits,
and `initGate.intervalSeconds` (default 2) is the pause between checks. The default is the Job deadline
(`ingestion.activeDeadlineSeconds`, 3600 s) plus 300 s: a fresh install's Job (build, example
verification, publish, then the sidecar fetches) takes 5 to 40 minutes, serving reads its sidecars only
once, and a shorter gate would start pods before the files exist. Lower it to 120 only once serving
re-reads its sidecars.

While the Job is healthy the pod shows `Init:0/1` and then starts. If the Job hung or failed, the gate
gives up when the bound is reached, writes a line to its log naming the missing files (`init gate timed
out after 3900 seconds waiting for non-empty ... starting WITHOUT them (degraded)`) and exits 0, so the
pod starts without its sidecars. To an agent that pod answers the product reference as not available and
`list_recent_changes` with an empty list; the serving cards own the exact wording of that degraded state.
The cure is a retry of the pilot (see Retrying one pilot); once the retried Job has written the files,
restart the degraded serving pod so that it reads them. A pod therefore leaves `Init:0/1` after at most
`initGate.timeoutSeconds`; while it waits, the cause is the Job, so read the Job's log first.

The serving container also has a `startupProbe` on `/healthz` (`probes.startup`: every 5 s, up to 30
failures), which holds liveness and readiness off until the process first answers. Every ingestion Job is
killed after `ingestion.activeDeadlineSeconds` (3600 s) and removed `ingestion.ttlSecondsAfterFinished`
(86400 s) after it ends; set either to 0 to turn it off. No pod mounts a service-account token or receives
service-link variables (`pod.automountServiceAccountToken`, `pod.enableServiceLinks`), and every pod runs
with the runtime-default seccomp profile and group 10001.

## The optional GITHUB_TOKEN secret

Ingestion Jobs fetch product reference data from GitHub. Unauthenticated requests share a small
anonymous rate limit, and when it is exhausted GitHub answers HTTP 403. The Job then fails.

Two things address this:

- The readers send a `GITHUB_TOKEN` header when the variable is set, and wait out a rate-limit answer
  for a bounded time.
- The chart's optional `ingestion.githubToken.existingSecret` value makes every ingestion Job read
  `GITHUB_TOKEN` from a Kubernetes Secret the operator creates.

With no Secret named, an install has no token and a rate-limited Job fails. Wait for the limit to
reset, then retry the pilot (see below), or name a Secret as described in the next section. Do not put
a token in a values file or the repository.

## Passing the gateway and GitHub-token Secrets to the installer

Two optional installer flags name Secrets that the **operator** creates. The installer and the chart
only ever name a Secret; neither creates one or carries a credential value.

- `--embedding-gateway-secret NAME` adds `--set-string embeddingGateway.existingSecret=NAME` to the
  helm command of every pilot. The Secret needs the keys `endpoint` and `apiKey`.
- `--github-token-secret NAME` adds `--set-string ingestion.githubToken.existingSecret=NAME` to the
  helm command of every pilot. The Secret needs the key `token`.

The pairs use `--set-string`, not `--set`, so helm keeps the name as text. Plain `--set` would turn a
Secret named `null`, `true` or `123` into a non-string, and `null` would silently switch the feature
off.

Create the namespace and the Secrets first. The values below are placeholders; type your own, and do
not write them into a file in the repository:

```powershell
kubectl create namespace foss-mcp
kubectl create secret generic NAME -n NAMESPACE --from-literal=endpoint=<GATEWAY_ENDPOINT_URL> --from-literal=apiKey=<GATEWAY_API_KEY>
kubectl create secret generic NAME -n NAMESPACE --from-literal=token=<GITHUB_TOKEN>
```

Use a different NAME for each Secret and the pilots' namespace for NAMESPACE. Then install, giving the
same names to the installer:

```powershell
python scripts/deploy/install_pilots.py --namespace foss-mcp --image-tag rev-1234567 --embedding-gateway-secret foss-mcp-gateway --github-token-secret foss-mcp-github-token
```

What to expect:

- With the gateway Secret set, ingestion embeds through the gateway, so the stored vectors and the
  query vectors come from one embedding model. An ingestion pod whose Secret lacks `endpoint` or
  `apiKey` fails loudly with `CreateContainerConfigError` instead of building a different index. The
  serving pods read the same two keys as optional: a missing key leaves `find_examples` on lexical
  matching. Setting the Secret also renders an extra egress NetworkPolicy for TCP 443, to the
  destinations in `embeddingGateway.egressCidrs` (default: any).
- With the GitHub-token Secret set, ingestion authenticates to GitHub. A missing `token` key leaves
  the Job running unauthenticated.
- Without either flag nothing changes: the helm command is exactly the one in step 3.
- A NAME must be a valid Kubernetes name: lowercase letters, digits, `-` and `.`, starting and ending
  with a letter or digit, at most 253 characters. Anything else, a comma or an equals sign included,
  makes the installer print an error and exit 2 before it builds or runs any command, `--dry-run`
  included.
- `--dry-run` prints the helm line each pilot would get, `--set-string` pairs included, so check it before a
  real run.

## Retrying one pilot

1. Find the cause in the install output or the Job log (see the troubleshooting table).
2. Fix the cause. For a missing fixture, that means a new image (step 1).
3. If the ingestion Job already failed, delete that Job by hand. Helm does not re-run an unchanged Job,
   and the tools never delete anything:

   ```powershell
   kubectl -n foss-mcp delete job foss-mcp-pdf-net-ingest-pdf-net
   ```

   The Job name is `<release>-ingest-<family>-<platform>`.
4. Reinstall that pilot only, then prove it:

   ```powershell
   python scripts/deploy/install_pilots.py --namespace foss-mcp --image-tag rev-1234567 --only pdf_net
   python scripts/deploy/prove_pilots.py --namespace foss-mcp --only pdf_net
   ```

## Uninstalling

The tools do not uninstall. An operator removes a release by hand, one release at a time:

```powershell
helm uninstall foss-mcp-pdf-net -n foss-mcp
```

The manifests volume claim is annotated `helm.sh/resource-policy: keep` (`manifests.keepOnUninstall`,
default true), so it survives `helm uninstall` and the published generation is not lost with the release.
Delete it deliberately with `kubectl delete pvc -n foss-mcp <release-fullname>-manifests`, or set
`manifests.keepOnUninstall=false` before the release is installed so Helm removes it. To remove all thirty-two,
list the releases with `helm list -n foss-mcp` and uninstall each one. The namespace is left in place.

## Rolling back a generation

A bad publish that was nevertheless accepted is undone by pointing the scope back at an earlier
generation. The ingestion image (which the Job below uses) and the standalone `demo` image both contain
`/app/infra/rollback.py`; no new image is needed. A rollback travels the same fenced path as a publish (it takes
a new lease, so a stale writer cannot overwrite it) and needs no restart: the serving pod reads the active pointer on every probe, so `/readyz` and the tools follow
the pointer on the next call.

The manifests claim is `foss-mcp-<family>-<platform>-manifests`, mounted read-only by the serving pod. It is
`ReadWriteOnce`, so the rollback Job must run on the same node as the serving pod: find it with
`kubectl get pod -n <namespace> -o wide` and put it in `nodeName`. Replace `<namespace>`, `<node>`, `<tag>`
and the family and platform below with the release's own values, and use the chart's ingestion image
(`ingestion.image.repository`, `foss-mcp-ingestion`, at the same `image.tag` the release was installed with):

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: foss-mcp-rollback-pdf-go
  namespace: <namespace>
spec:
  backoffLimit: 0
  template:
    spec:
      restartPolicy: Never
      nodeName: <node>
      securityContext:
        runAsUser: 10001
        fsGroup: 10001
      containers:
        - name: rollback
          image: foss-mcp-ingestion:<tag>
          command:
            - python
            - /app/infra/rollback.py
            - --family
            - pdf
            - --platform
            - go
            - --source-kind
            - self_extracted
            - --manifest-store
            - /data/manifests
            - --list
          volumeMounts:
            - name: manifests
              mountPath: /data/manifests
      volumes:
        - name: manifests
          persistentVolumeClaim:
            claimName: foss-mcp-pdf-go-manifests
```

Exactly one of three arguments ends the command, after `--family pdf --platform go --source-kind
self_extracted --manifest-store /data/manifests`:

- `--list` prints one line per stored generation, newest first: the generation id, the word `ACTIVE` beside
  the one being served, and `symbols=N docs=N examples=N total=N`. It takes no lease and writes nothing.
  Run it first, read the Job's log with `kubectl logs job/foss-mcp-rollback-pdf-go -n <namespace>`, then
  delete the Job and create the next one.
- `--previous` rolls back to the generation the last publish (or rollback) replaced.
- `--target-generation <id>` rolls back to the named generation, copied from the `--list` output.

The target is validated before the pointer moves: it must belong to this scope (a generation id of another
product is refused even when a file with the same version exists) and its content must have a non-empty
lexical index whose documents all carry a chunk id, tokens and text. A refused rollback leaves the pointer
where it was. On success the Job prints `rolled back to <id>`.

A rollback is also held to what a forward publish is held to, unless `--force` is added: the target must
have at least 3 symbol chunks and 1 documentation chunk, must have been indexed from the same source
repository as the generation it replaces, and must not be more than 20 percent smaller in symbols (50 percent
in documentation, examples and total) than the generation it replaces. The product-reference sidecar for the
identity is read too: one that names ANOTHER repository refuses the rollback, and one that still records a
newer commit than the target is reported after the flip (the server's install notice names the active
generation's own commit, so the sidecar needs no rewrite). After the flip the readiness probe `/readyz` runs
is applied to the new active generation; if it is not ready the pointer is flipped back to the generation it
replaced and the Job exits 3 (4 when it could not flip back, and then the operator must act at once). With
`--force` a failing probe is reported and the target is kept. A refused rollback exits 1 with one line
naming every reason. `--list` never fails on a damaged store: a corrupt active pointer, a generation file
that cannot be read in full (listed as `CORRUPT`) and a file with no readable id (named on stderr) are
reported and skipped.

Retention keeps the active generation, the previous one and the last three generations by version; an
ingestion that publishes removes older generation files (never one younger than an hour) and stale
temporary files. Read `publish_log.jsonl` in the scope's directory to see what happened: a `published`
line for each accepted publish, a `refused` line for each refusal, a `rollback` line for each rollback
(with the generation it replaced in `previous_active`), and a `pruned` line listing what retention removed.
Delete the rollback Job when done.

## Accepting an intentional shrink

Every ingestion publishes through a production policy. A publish is refused, nothing is activated and the
previous generation keeps serving, when any of these holds:

- the generation has fewer than 3 symbol chunks or fewer than 1 documentation chunk (a floor);
- a kind (symbols, docs, examples or the total) fell too far below the maximum of the last three
  generations of that scope: symbols by more than 20 percent, docs, examples and the total by more than
  50 percent, and examples going from 3 or more to none (a baseline under 5 is not subject to the percent
  rule);
- two chunks have the same id, or a chunk has empty or blank text;
- the provenance repository differs from the one the active generation was built from. This is never
  overridable: a deliberate repository change needs a new store.

The refusal message ends with the exact flags that would accept a percent refusal, for example
`to accept deliberately: --accept-regression symbols:100->46 --reason TEXT`. When the shrink is intended
(upstream removed a vendored directory, say), run the ingestion again with the numbers from that message:

```text
python /app/infra/ingest.py ... --accept-regression symbols:100->46 --accept-regression total:153->58 --reason "upstream removed the vendored directory"
```

The override must match the observed numbers exactly (the baseline before the arrow, the new value after it),
otherwise the publish is refused again. It never waives the floors, duplicate or empty chunks, or the
repository identity check. Every publish and every refusal is appended to `publish_log.jsonl` in the scope's
directory beside the active pointer, and an accepted shrink is recorded there with its reason; the next
ordinary publish at the new size is then compared with the new size, not with the old one. Renaming
`active_pointer.json` aside is no longer needed and must not be done: it disables every guard.

A Kubernetes ingestion Job cannot take extra flags, so the same override is read from two environment
variables: `FOSS_MCP_ACCEPT_REGRESSION="symbols:100->46;total:153->58"` and
`FOSS_MCP_ACCEPT_REGRESSION_REASON`. The chart sets them from the chart value `ingestion.acceptRegression`.
That value applies to one pilot release and must be removed again after the publish.

The chart values are `ingestion.acceptRegression` (a list of entries such as `symbols:100->46`, joined with
`;`) and `ingestion.acceptRegressionReason`; a non-empty list with a blank reason makes `helm template`
fail, and with the defaults (empty) neither variable is rendered. For one pilot release, after deleting
that release's old ingestion Job by hand (a Job's pod template is immutable):

```powershell
helm upgrade foss-mcp-pdf-net infra/helm/foss-mcp -n foss-mcp --reuse-values --set-json 'ingestion.acceptRegression=["symbols:100->46","total:153->58"]' --set ingestion.acceptRegressionReason="upstream removed the vendored directory"
```

Once the Job has published, upgrade again with `--set-json 'ingestion.acceptRegression=[]'` and
`--set ingestion.acceptRegressionReason=` to remove them.

## Operating notes for the serving container

Behaviour an operator can see and tune. Every variable is optional; none is a secret.

- **A missing sidecar is reported, never served as "no data".** The ingestion Job always writes the
  product-reference and recent-releases files. If either is not in the manifests directory the container logs a
  WARNING naming the file (`sidecar not loaded, the file was not found`), `/readyz` carries a `warnings` entry
  (`recent_releases sidecar not loaded: recent_releases_<family>_<platform>.json was not found`; the status
  stays 200), `/metrics` shows `foss_mcp_sidecar_state{sidecar=...,state=...}` and `foss_mcp_sidecars{state=...}`,
  and `list_recent_changes` answers an `unavailable` error that says the release data is not loaded instead of
  an empty list. A file copied in later is picked up on the next request (its size and modification time are
  compared on every call); no restart is needed.
- **`/metrics` is the Prometheus text exposition by default.** The two-field JSON form
  (`usage_events_queued`, `usage_events_dropped`) is `/metrics?format=json`.
- **Every tool call is traceable.** The call's log line carries `generation_id` and `source_commit` (the commit
  the API symbols were extracted from), and a successful result carries its correlation id in `_meta.correlation_id`
  as an error carries it in `structuredContent.correlation_id`. `serverInfo.version` is `0.1.0+<12 hex of the build
  commit>`; a plain `docker build` of the `demo` target (or of the ingestion image) takes it from
  `--build-arg FOSS_MCP_BUILD_REVISION=<commit>`, and the release build writes it into the image itself.
- **Transport limits.** Host is validated: loopback, IP literals, single-label names (the container, Compose service
  or pod name), the machine host name, `*.svc` and `*.svc.cluster.local` are accepted; any other name needs
  `FOSS_MCP_ALLOWED_HOSTS` (comma separated names, `*.suffix` patterns or `*`) and is answered 421 otherwise.
  A rejection (Origin, Host or protocol version) is a JSON-RPC error body with a null id and is logged with its
  reason. At the session cap (`FOSS_MCP_MAX_SESSIONS`, 200) the oldest session that has been idle for at least
  `FOSS_MCP_SESSION_EVICT_MIN_IDLE_SECONDS` (10) is evicted to admit a new one; a session used more recently is never
  evicted and the new client gets the 503 with `Retry-After`. A request head that does not complete within
  `FOSS_MCP_HEADER_TIMEOUT_SECONDS` (15) closes the connection, and a request body that stalls for
  `FOSS_MCP_BODY_TIMEOUT_SECONDS` (30) is answered 408. A POST whose JSON-RPC `id` is not a string or an integer is
  answered 400 `Invalid Request`. A client hanging up mid-request is a DEBUG line, not an ERROR.
- **Sessions live in the process.** A restart (a rolling update included) forgets them: the next request with an old
  `Mcp-Session-Id` is a 404 whose message says to send a new `initialize` without the header, which MCP clients do.
- **The baked demo data cannot be changed from inside the container.** In the `demo` image `/data` and everything
  under `/data/manifests` belong to root with no write bit, so the serving user cannot delete or replace a sidecar,
  with or without `--read-only`.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `FileNotFoundError` for `/app/fixtures/<pilot>/api_surface.json` in an ingestion Job log | The image was built before that pilot's fixture was added to the repository. | Commit, rebuild both images with `build_images.py` (step 1), load or push them, and reinstall with the new tag. |
| `HTTP 403` and `rate limit exceeded` in an ingestion Job log | GitHub's anonymous rate limit is exhausted. No token is configured (see the GITHUB_TOKEN section and the one on passing the Secrets). | Wait for the limit to reset, then delete that Job and retry the pilot. |
| A serving pod stuck at `Init:0/1` | Its ingestion Job has not written the product-reference and recent-releases sidecars, usually because the Job failed. The gate gives up after `initGate.timeoutSeconds` (3900 s) and starts the pod degraded. | Read that Job's log with `kubectl logs job/<job-name> -n <namespace>`, fix the cause, then retry the pilot. |
| `INGESTION FAILED` in the `install_pilots.py --wait-for-jobs` table | Helm succeeded but the pilot's ingestion Job failed; the serving pod still runs the previous generation. | Read the printed Job log, fix the cause, then retry the pilot. |
| `list_recent_changes` answers `unavailable` ("Release information is not loaded") and `/readyz` has a `warnings` entry naming `recent_releases_<family>_<platform>.json` | That sidecar is not in the manifests directory. | Re-run the pilot's ingestion Job, or copy the file in; the next request serves it. |
| `port 8201 already in use`, or a port-forward that exits at once | The local port `prove_pilots.py` chose is taken by another process. | Rerun with `--base-port` set to a free range, for example `--base-port 9401`. |
| `docker is not on PATH`, or `Cannot connect to the Docker daemon` | Docker is not running, or the toolchain is not activated. | Start Docker Desktop, run `. .\scripts\toolchain\activate.ps1`, and rerun `build_images.py`. |

## Known limits

- **Seven pilots have no documentation search or verified examples.** These are the two C++ pilots
  (`pdf_cpp`, `cells_cpp`) and five other second-product pilots (`words_python`, `words_net`,
  `slides_java`, `cells_go`, `cells_typescript`). They have no furnished page in the
  chart, so their proof reports check 5 (`search_docs`) and check 6 (`find_examples`) as not
  applicable. They do prove symbols, lookup, members, product reference, freshness, honest miss and
  isolation. The other six pilots have furnished pages and run every check.
- **Each release has its own 1 Gi manifests volume**, from the chart's `manifests.size`. The serving
  pod and its Job must schedule on one node, because the claim is `ReadWriteOnce`.
