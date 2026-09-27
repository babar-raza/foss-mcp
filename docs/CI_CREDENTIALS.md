# CI credentials — GitHub/GitLab mirror

This document names the credentials `.github/workflows/mirror-gitlab.yml` (this
repo) depends on, and cross-references how the sibling repo
`repository-presenter` implements the identical pattern with a confirmed-working
setup. It exists to prevent two token names from being confused for one
another; it never records an actual token value.

## This repo's mirror

- `origin` (GitHub, `babar-raza/foss-mcp`) is the source of truth.
- `gitlab` (`gitlab.recruitize.ai/sialkot/cantt-smallize/aspose-foss-dev-context-mcp.git`)
  is a one-way, push-only mirror of `main` and tags.
- The mirror is driven by `.github/workflows/mirror-gitlab.yml` on every push to
  `main` (or `workflow_dispatch`) — **not** by the local `gitlab` git remote. A
  local remote only pushes when someone manually pushes from that one machine;
  the workflow keeps GitLab current regardless of which machine or worktree
  pushed to GitHub.
- The workflow authenticates with a single GitHub Actions repository secret,
  `GITLAB_TOKEN`, injected as an environment variable and embedded in the push
  URL using GitLab's `oauth2:<token>@host` convention:
  ```
  git push "https://oauth2:${GITLAB_TOKEN}@gitlab.recruitize.ai/sialkot/cantt-smallize/aspose-foss-dev-context-mcp.git" HEAD:refs/heads/main
  ```
- The push is intentionally **not** forced, so a diverged GitLab `main` fails
  the job loudly instead of silently discarding commits.
- `GITLAB_TOKEN` must be configured under this GitHub repo's
  **Settings → Secrets and variables → Actions**, scoped with write access to
  the GitLab project. Its live status is tracked as `OWNER-02` in
  `ops/owner_items.yaml` — as of this writing it has never been proven to run,
  because the credentials needed to check `gh secret list` /
  `gh run list` from this environment are themselves broken (`OWNER-03`).
- The local `gitlab` git remote's own credentials are irrelevant to this
  workflow: the workflow pushes over HTTPS with the secret embedded in the URL
  inside the GitHub Actions runner, never through a named local remote.

## Cross-check: `repository-presenter` (confirmed working)

`H:\...\repository-presenter` runs the same GitHub→GitLab mirror shape and its
setup is a useful reference precisely because the names do **not** overload:

| Variable | Direction | Purpose | Where consumed |
| --- | --- | --- | --- |
| `GITLAB_TOKEN` | push, GitHub → GitLab | Repository-scoped GitLab PAT (`oauth2:<token>@...` push auth), same mechanism as this repo | GitHub Actions secret, used only inside `.github/workflows/mirror-gitlab.yml`'s "push to GitLab" step |
| `GH_TOKEN` | read, GitHub-side | An **unrelated**, repository-scoped **read-only** GitHub token used for that project's own content analysis (cloning/`GET /repos/{owner}/{repo}`) — it plays no role in the GitLab mirror | Consumed by that repo's `present`/`metadata` code paths, documented in its `README.md` / `.env.example` |
| ambient `github.token` | read, GitHub Actions | Auto-issued Actions token used by an unrelated liveness workflow | `.github/workflows/liveness.yml` |

The mirror workflow's checkout step there also sets
`persist-credentials: false` with `permissions: contents: read`, so nothing
ever writes back to GitHub using a token — only `GITLAB_TOKEN` is used for
writes, and only against GitLab.

**Why this matters:** `GITLAB_TOKEN` and `GH_TOKEN` are two different
credentials for two different remotes/purposes, in both repos. Do not assume
a `GH_TOKEN` env var (if one is ever added here) has anything to do with the
GitLab mirror, and do not assume the local `gitlab` remote's auth state says
anything about whether the CI secret `GITLAB_TOKEN` is configured or valid —
`repository-presenter`'s working mirror uses the CI secret exclusively, never
the local remote.

No token value is stored in either repo's tree, `.env`, or netrc; both rely
solely on the GitHub Actions repository secret store.
