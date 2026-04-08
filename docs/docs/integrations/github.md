---
sidebar_position: 1
title: GitHub
---

# GitHub Integration

The GitHub integration lets Tinker read your source code during investigations and open pull requests when a fix is approved. It is required for the `fix` and `approve` REPL commands and the `rca` command's code context feature.

---

## What it enables

| Feature | Without GitHub | With GitHub |
|---|---|---|
| `tinkr investigate` → `explain` | ✓ log-based only | ✓ log + code context |
| `tinkr investigate` → `fix` | ✗ unavailable | ✓ patch with diff |
| `tinkr investigate` → `approve` | ✗ unavailable | ✓ PR opened |
| `tinkr rca` | ✓ log + metrics + traces | ✓ + code context from highest-severity anomaly |
| `tinkr deploy list/correlate` | ✗ unavailable | ✓ commit history |

---

## Setup

### 1. Create a fine-grained personal access token

Go to **GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.

Select the repository (or repositories) Tinker should access and grant:

| Permission | Level |
|---|---|
| **Contents** | Read |
| **Commits** | Read |
| **Pull requests** | Write |
| **Metadata** | Read (auto-granted) |

Copy the generated token (`github_pat_...`).

### 2. Add to server config

`tinkr init server` asks for this in Step 3. For manual setup:

```bash title="~/.tinker/.env"
GITHUB_TOKEN=github_pat_xxxxxxxxxxxxxxxx
```

```toml title="~/.tinker/config.toml"
[github]
token        = "env:GITHUB_TOKEN"
default_repo = "acme/monorepo"
```

### 3. Per-service repos (optional)

If different services live in different repos, configure them per service inside their profile:

```toml title="~/.tinker/config.toml"
[profiles.aws-prod.services.payments-api]
repo          = "acme/payments"
resource_type = "ecs"

[profiles.aws-prod.services.auth-service]
repo          = "acme/auth"
resource_type = "lambda"
```

When a service has no `repo` configured, Tinker falls back to `[github].default_repo`.

---

## How code investigation works

During `explain` and `fix`, Tinker:

1. **Classifies the error** — `transient`, `logic_bug`, `config_error`, or `dependency_down`
2. **Extracts stack frame file paths** from the log summary
3. **Fetches those files** from GitHub at the default branch
4. **Includes ±30 lines of context** around each relevant line
5. For `logic_bug`: also searches the repo for related patterns using `github_search_code`

The LLM never receives more than the relevant code context — full files are never sent.

---

## How auto-PRs work

When you type `approve` in the `tinkr investigate` REPL:

1. Tinker creates a branch named `tinker/fix-{uuid}` on the configured repo
2. Applies the proposed file changes (the exact `old_string → new_string` patch)
3. Commits with the explanation as the commit message
4. Opens a PR targeting the default branch

The `approve` action requires the `oncall` or `sre-lead` role in your API key config:

```toml title="~/.tinker/config.toml"
[auth]
api_keys = [
  { hash = "<sha256>", subject = "alice", roles = ["oncall"] }
]
```

---

## Deploy correlation

`tinkr deploy list` and `tinkr deploy correlate` use the GitHub Commits API to list recent commits for a service path and cross-reference them with anomaly timestamps.

```bash
tinkr deploy list payments-api --since 7d
tinkr deploy correlate payments-api --since 7d
```

Commits with anomalies detected within 30 minutes are highlighted in red.

---

## Bitbucket / GitLab

Native support is not yet built in. Workaround: mirror the relevant repos to GitHub and point `default_repo` there.
