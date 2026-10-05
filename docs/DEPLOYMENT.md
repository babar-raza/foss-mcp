# Deploying and proving the fourteen pilots

This is the runbook for an operator who was not in the session that proved the pilots. It covers the
Helm chart at `infra/helm/foss-mcp`, the three tools that install and prove it, and what to do when a
step fails. Everything here is taken from the repository; a value this document does not give is
found in `infra/helm/foss-mcp/values.yaml`, not guessed.

The chart deploys one release per pilot. Each release has one serving pod (`foss-mcp-serving`), one
ingestion Job per pilot, and its own manifests volume. Fourteen releases serve fourteen pilots.

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
`image.pullPolicy` is `IfNotPresent`.

## 3. Install the fourteen pilots

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

Useful options:

| Option | Meaning |
| --- | --- |
| `--only pdf_net` | Install only that pilot. Repeat the option for several. An unknown name is an error. |
| `--dry-run` | Print each helm command. Run nothing and write nothing. |
| `--parallel N` | Installs running at once. Default 2. |
| `--timeout 45m` | `helm --timeout` for each install. Default 30m. |

The tool never removes a release or a resource. It runs no uninstall and no delete.

A failed install prints the pod states of that release and the last 20 lines of its ingestion Job log,
so the cause is on screen.

## 4. Prove the fourteen pilots

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

## What the init container does, and why a pod waits at Init

Each serving pod has an init container, `wait-for-product-reference`. It loops until the file
`product_reference_<family>_<platform>.json` exists on the manifests volume. That file is the
packaging-manifest sidecar. The ingestion Job for the same pilot writes it as its last step.

The serving process reads the sidecar once, when it starts. A serving pod that started earlier would
run without it. So a serving pod stays at `Init:0/1` until its ingestion Job has written the sidecar.
If the pod stays there, the cause is the Job, not the pod. Read the Job's log first.

## The optional GITHUB_TOKEN secret

Ingestion Jobs fetch product reference data from GitHub. Unauthenticated requests share a small
anonymous rate limit, and when it is exhausted GitHub answers HTTP 403. The Job then fails.

Two cards address this, and neither is accepted at this commit:

- TC-220 adds a `GITHUB_TOKEN` header to those requests and a bounded wait on a rate-limit answer.
  Its state is IN_PROGRESS.
- TC-221 adds an optional `ingestion.githubToken` value that reads `GITHUB_TOKEN` from a Kubernetes
  Secret the operator creates. Its state is PENDING, and `values.yaml` has no such value yet.

Until both are accepted, an install has no token and a rate-limited Job fails. Wait for the limit to
reset, then retry the pilot (see below). Do not put a token in a values file or the repository.

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

Helm removes everything the release created, including its manifests volume claim, so the published
generation is lost with it. To remove all fourteen, list the releases with `helm list -n foss-mcp` and
uninstall each one. The namespace is left in place.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `FileNotFoundError` for `/app/fixtures/<pilot>/api_surface.json` in an ingestion Job log | The image was built before that pilot's fixture was added to the repository. | Commit, rebuild both images with `build_images.py` (step 1), load or push them, and reinstall with the new tag. |
| `HTTP 403` and `rate limit exceeded` in an ingestion Job log | GitHub's anonymous rate limit is exhausted. There is no token yet (see the GITHUB_TOKEN section). | Wait for the limit to reset, then delete that Job and retry the pilot. |
| A serving pod stuck at `Init:0/1` | Its ingestion Job has not written the product-reference sidecar, usually because the Job failed. | Read that Job's log with `kubectl logs job/<job-name> -n <namespace>`, fix the cause, then retry the pilot. |
| `port 8201 already in use`, or a port-forward that exits at once | The local port `prove_pilots.py` chose is taken by another process. | Rerun with `--base-port` set to a free range, for example `--base-port 9401`. |
| `docker is not on PATH`, or `Cannot connect to the Docker daemon` | Docker is not running, or the toolchain is not activated. | Start Docker Desktop, run `. .\scripts\toolchain\activate.ps1`, and rerun `build_images.py`. |

## Known limits

- **Eight pilots have no documentation search or verified examples.** These are the two C++ pilots
  (`pdf_cpp`, `cells_cpp`) and six other second-product pilots (`words_python`, `words_net`,
  `slides_java`, `cells_go`, `cells_typescript`, `jmap_rust`). They have no furnished page in the
  chart, so their proof reports check 5 (`search_docs`) and check 6 (`find_examples`) as not
  applicable. They do prove symbols, lookup, members, product reference, freshness, honest miss and
  isolation. The other six pilots have furnished pages and run every check.
- **Each release has its own 1 Gi manifests volume**, from the chart's `manifests.size`. The serving
  pod and its Job must schedule on one node, because the claim is `ReadWriteOnce`.
