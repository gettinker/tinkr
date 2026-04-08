---
sidebar_position: 9
title: Security
---

# Security Model

Tinker is designed with a defense-in-depth approach. This page explains the trust boundaries, credential model, and the guardrails that prevent accidental or unauthorized actions.

---

## Trust model

```
Developer laptop         Tinker Server              Cloud APIs
     │                        │                          │
     │  Bearer <api-key>       │                          │
     ├───────────────────────► │  IAM / Workload          │
     │                        │  Identity (automatic)    │
     │                        ├─────────────────────────►│
     │                        │◄─────────────────────────┤
     │◄───────────────────────┤                          │
```

**The Tinker server is the single point of credential trust.** The CLI and Slack bot authenticate to the server — they never talk to cloud APIs directly.

---

## Credentials

### Cloud backends — no long-lived keys

| Cloud | Production mechanism |
|---|---|
| AWS | IAM Task Role (ECS) or instance profile (EC2) |
| GCP | Workload Identity (Cloud Run / GKE) |
| Azure | Managed Identity (Container Apps / AKS) |
| Grafana | API key (stored in secrets manager, injected as env var) |
| Datadog | API key + App key (same pattern) |
| Elastic | API key (same pattern) |

The SDKs (`boto3`, `google-auth`, `DefaultAzureCredential`) discover credentials from the instance metadata service automatically. **Zero credential configuration on the server** — no `AWS_ACCESS_KEY_ID`, no service account JSON files.

### API keys — short strings, stored hashed

Developer and bot API keys are short random strings, stored as SHA-256 hashes in `TINKR_API_KEYS`:

```bash
# Generate
python -c "import secrets; print(secrets.token_urlsafe(32))"

# Hash — store this
python -c "import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" <raw>
```

The raw key is given to the developer. Only the hash is stored on the server. If the database is compromised, the raw keys cannot be recovered from it.

### Anthropic API key — the only LLM credential

The server requires `ANTHROPIC_API_KEY`. It should be stored in your cloud secrets manager:

- **AWS**: AWS Secrets Manager → ECS task definition secret ref
- **GCP**: Secret Manager → Cloud Run secret volume mount
- **Azure**: Key Vault → Container Apps secret ref

**Never commit `ANTHROPIC_API_KEY` to source control.**

---

## Human-in-the-loop

The most impactful actions — applying code fixes and opening pull requests — require explicit human approval. This is non-negotiable.

### Approval chain

| Interface | Approval mechanism |
|---|---|
| CLI (`tinkr investigate`) | `approve` REPL command, requires `oncall` or `sre-lead` role |
| Slack | `/tinker-approve <session-id>`, checked against Slack user email → role mapping |
| API | `POST /api/v1/approve`, requires `oncall` or `sre-lead` in JWT/API key roles |
| Claude Code | `apply_fix` is in the `deny` list by default; user must explicitly allow |

The `ApprovalRequired` guardrail in `guardrails.py` enforces this check centrally. Every code path that calls `apply_fix` or `create_pr` goes through it.

### Deny list (Claude Code)

By default, `.claude/settings.json` blocks write operations:

```json
{
  "permissions": {
    "deny": ["github_create_pr", "apply_fix"]
  }
}
```

Claude Code will prompt the user before executing these tools. The user must approve each time — a one-time approval does not persist.

---

## Log sanitization

All log data passes through `sanitize_log_content()` before being included in any LLM prompt or returned from an MCP tool. The sanitizer strips:

- AWS access keys (`AKIA...`)
- Generic API keys and tokens (`sk-...`, `ghp_...`, `xoxb-...`)
- Passwords in connection strings (`postgresql://user:password@...`)
- Credit card numbers (PAN — 13–19 digit sequences)
- Prompt injection patterns (`IGNORE PREVIOUS INSTRUCTIONS`, etc.)

Sanitization happens in `src/tinker/guardrails.py` and cannot be bypassed from the API.

---

## Audit logging

Every tool call to the MCP server is logged with:

- Timestamp
- Subject (from API key)
- Tool name
- Service
- Input parameters (credentials redacted)
- Duration

Audit logs go to the server's stdout and are captured by your cloud's logging service (CloudWatch Logs, Cloud Logging, Log Analytics, Loki).

---

## Network security

### Server exposure

The Tinker server only needs to be reachable from:
- Developer workstations (CLI)
- Slack (webhook events)
- Claude Code (MCP over SSE)

**Do not expose the server to the public internet** unless protected by a VPN, IP allowlist, or mTLS. Use internal load balancers:

- **AWS**: Internal ALB in a private subnet
- **GCP**: Cloud Run with `--no-allow-unauthenticated` + IAP or VPC Service Controls
- **Azure**: Container Apps with internal ingress

### TLS

Always terminate TLS at the load balancer. The Tinker server itself speaks HTTP internally.

---

## Slack security

Tinker verifies every Slack request using the signing secret (`SLACK_SIGNING_SECRET`). Requests with an invalid or missing `X-Slack-Signature` header are rejected with 401.

The signing secret is never logged.

---

## GitHub token scope

The GitHub token only needs the minimum permissions to function:

| Permission | Level | Purpose |
|---|---|---|
| Contents | Read | Fetch source files for code context |
| Commits | Read | Deploy correlation |
| Pull requests | Write | Open fix PRs |
| Metadata | Read (auto) | Required by GitHub |

Use a **fine-grained personal access token** scoped to the specific repositories Tinker needs. Never use a classic token with broad `repo` scope.

---

## What Tinker never does

- Never stores raw API keys — only SHA-256 hashes
- Never sends raw log data to the LLM — always sanitized first
- Never auto-applies code fixes — always requires explicit human approval
- Never commits to `main` — always creates a branch + PR
- Never stores cloud credentials — uses native identity mechanisms
- Never skips the `ApprovalRequired` guardrail outside of test environments
