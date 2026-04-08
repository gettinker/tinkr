---
sidebar_position: 13
title: deploy
---

# tinker deploy

List recent deployments for a service and correlate them with anomaly timestamps. Requires the [GitHub integration](../integrations/github).

```
tinker deploy <subcommand> [options]
```

## Subcommands

| Subcommand | Description |
|---|---|
| `list <service>` | List recent commits (deploys) from GitHub |
| `correlate <service>` | Cross-reference deploys with anomalies |

---

## `tinker deploy list`

List recent commits for a service's repository path.

```bash
tinker deploy list <service> [options]
```

### Options

| Flag | Default | Description |
|---|---|---|
| `--since TEXT` | `7d` | How far back to look — e.g. `3d`, `7d`, `30d` |
| `--json` | off | Emit raw JSON |

### Examples

```bash
tinker deploy list payments-api
tinker deploy list payments-api --since 30d
tinker deploy list payments-api --json
```

### Output

```
Recent deploys — payments-api (last 7d)

  SHA       AUTHOR    TIME                 MESSAGE
  a3f2b1c   alice     2026-04-07 13:50     fix: increase Stripe timeout to 60s
  9e8d7c6   bob       2026-04-06 09:12     feat: add idempotency keys to charge API
  5b4c3d2   alice     2026-04-05 16:40     chore: update stripe-python to 7.3.0
  1a2b3c4   carol     2026-04-04 11:05     fix: retry logic on card validation
```

---

## `tinker deploy correlate`

Cross-reference recent commits with anomaly timestamps. Commits within 30 minutes of an anomaly are flagged.

```bash
tinker deploy correlate <service> [options]
```

### Options

| Flag | Default | Description |
|---|---|---|
| `--since TEXT` | `7d` | Look-back window |
| `--json` | off | Emit raw JSON |

### Examples

```bash
tinker deploy correlate payments-api
tinker deploy correlate payments-api --since 14d
```

### Output

```
Deploy correlation — payments-api (last 7d)

  SHA       AUTHOR    TIME                 MESSAGE                           ANOMALIES
  a3f2b1c   alice     2026-04-07 13:50     fix: increase Stripe timeout      ⚠  2 anomalies at 14:01
  9e8d7c6   bob       2026-04-06 09:12     feat: add idempotency keys        ✓  no anomalies nearby
  5b4c3d2   alice     2026-04-05 16:40     chore: update stripe-python       ✓  no anomalies nearby
  1a2b3c4   carol     2026-04-04 11:05     fix: retry logic on card validation ✓  no anomalies nearby

  ⚠  Correlation found: a3f2b1c deployed at 13:50, anomalies detected at 14:01 (+11 min)
     Anomalies: error_count (HIGH), latency_p99 (MEDIUM)
```

---

## How deploy detection works

Tinkr uses the **GitHub Commits API** to list commits for a service's configured repository path. The service-to-repo mapping is configured in your profile:

```toml title="~/.tinkr/config.toml"
[github]
token        = "env:GITHUB_TOKEN"
default_repo = "acme/monorepo"

[profiles.aws-prod.services.payments-api]
repo = "acme/payments"

[profiles.aws-prod.services.auth-service]
repo = "acme/auth"
```

If a service has no `repo` configured, Tinkr falls back to `[github].default_repo`.

## Correlation window

A commit is flagged as correlated if anomalies were detected within **±30 minutes** of the commit timestamp. This window accounts for:

- Container image pull and restart time (typically 1–5 minutes)
- Gradual traffic ramp-up (canary deployments)
- Delayed impact from config changes

## Requirements

- GitHub integration configured (see [GitHub Integration](../integrations/github))
- Your GitHub token must have `Contents: Read` and `Commits: Read` permissions

## See also

- [GitHub Integration](../integrations/github)
- [`tinker anomaly`](anomaly) — detect anomalies without deploy context
- [`tinker diff`](diff) — compare before/after a deploy
