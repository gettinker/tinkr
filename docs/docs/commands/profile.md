---
sidebar_position: 14
title: profile
---

# tinkr profile

Manage named profiles. A profile groups backend configuration, service definitions, and notifiers for a specific environment (e.g. `aws-prod`, `gcp-staging`).

```
tinkr profile <subcommand>
```

## Subcommands

| Subcommand | Description |
|---|---|
| `list` | List all configured profiles |
| `use <name>` | Set the active profile |
| `show` | Show the active profile's configuration |

---

## `tinkr profile list`

```bash
tinkr profile list
```

### Output

```
PROFILE       BACKEND      ACTIVE
default       grafana      
aws-prod      cloudwatch   ✓
gcp-staging   gcp          
azure-prod    azure        
```

---

## `tinkr profile use`

Switch the active profile. All subsequent commands use this profile.

```bash
tinkr profile use aws-prod
```

You can also pass `--profile` on any command without changing the default:

```bash
tinkr anomaly payments-api --profile gcp-staging
```

---

## `tinkr profile show`

Display the active profile's full configuration (secrets redacted).

```bash
tinkr profile show
```

### Output

```
Active profile: aws-prod

  backend:       cloudwatch
  region:        us-east-1
  log_group_prefix: /ecs/

  services:
    payments-api   repo=acme/payments   resource_type=ecs
    auth-service   repo=acme/auth       resource_type=ecs

  notifiers:
    pagerduty   type=webhook   url=***
    slack-ops   type=slack     channel=#prod-incidents
```

---

## Profile configuration

Profiles are defined in `~/.tinkr/config.toml`:

```toml
# Default profile — used when no --profile is given
[profiles.default]
backend = "grafana"
loki_url        = "env:GRAFANA_LOKI_URL"
prometheus_url  = "env:GRAFANA_PROMETHEUS_URL"

# AWS production
[profiles.aws-prod]
backend = "cloudwatch"
region  = "us-east-1"
log_group_prefix = "/ecs/"

[profiles.aws-prod.services.payments-api]
repo          = "acme/payments"
resource_type = "ecs"

[profiles.aws-prod.notifiers.pagerduty]
type                 = "webhook"
url                  = "env:PAGERDUTY_WEBHOOK_URL"
header_Authorization = "env:PAGERDUTY_API_KEY"

# GCP staging
[profiles.gcp-staging]
backend    = "gcp"
project_id = "acme-staging"
```

## Active profile resolution order

1. `--profile` flag on the command line
2. `TINKR_PROFILE` environment variable
3. The profile marked `active = true` in `config.toml`
4. `default` profile

## Multi-cloud workflows

Use profiles to work across multiple cloud accounts in a single terminal session:

```bash
# Check prod
tinkr anomaly payments-api --profile aws-prod

# Check staging
tinkr anomaly payments-api --profile gcp-staging

# Switch default for the session
tinkr profile use aws-prod
tinkr anomaly payments-api
```

## See also

- [Configuration Reference](../configuration) — full `config.toml` reference
- [Docker Deployment](../deployment/docker) — deploying per-environment server instances
