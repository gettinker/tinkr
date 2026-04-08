---
sidebar_position: 2
title: Installation
---

# Installation

## Requirements

- An [Anthropic API key](https://console.anthropic.com/) (`ANTHROPIC_API_KEY`)
- Access to a cloud observability backend — CloudWatch, GCP, Azure, Grafana/Loki, Datadog, Elastic, or OTel

---

## Install the CLI

Choose any of the three package managers:

```bash
# uv (recommended)
uv tool install tinkr

# pipx
pipx install tinkr

# pip
pip install tinkr
```

Verify:

```bash
tinkr --version
tinkr --help
```

:::tip macOS / externally managed Python
If `pip install` fails with "externally managed environment", use `uv tool install` or `pipx install` instead — both create an isolated environment automatically with no `sudo` or virtualenv needed.
:::

---

## Run the server

The CLI is a thin client — it talks to the Tinker server. Run the server on any machine that has cloud access (EC2 with an IAM role, Cloud Run with Workload Identity, your laptop, etc.):

```bash
tinkr-server init    # first-time setup wizard
tinkr-server         # start on :8000
```

Then connect the CLI from any machine:

```bash
tinkr init
# Tinker server URL [http://localhost:8000]: https://tinker.acme.internal
# API token: <paste key from wizard>
```

---

## Build from source

If you want to modify the code or run the latest unreleased version:

```bash
git clone https://github.com/gettinker/tinkr
cd tinkr
cd tinker

# Install all deps (including dev deps)
uv sync

# Run the server directly
TINKR_BACKEND=cloudwatch uv run tinkr-server

# Or install the CLI globally as editable
uv tool install --editable .
tinkr --version
```

---

## Run with Docker

```bash
git clone https://github.com/gettinker/tinkr
cd tinkr
cd tinker
docker build -t tinker:local .

docker run -d \
  --name tinker \
  -p 8000:8000 \
  --env-file ~/.tinkr/.env \
  -v ~/.tinkr:/root/.tinkr \
  tinker:local
```

See [Docker / Self-hosted](./deployment/docker.md) for Kubernetes manifests and full `.env` reference.

---

## First-time server setup

```bash
tinkr-server init
```

The wizard walks through:
1. LLM provider and API key
2. Slack bot (optional)
3. GitHub integration (optional — enables `fix` and `approve`)
4. Server API key (for CLI auth)
5. Cloud backend profile

---

## File locations

All per-user state lives in `~/.tinkr/`:

| File | Written by | Purpose |
|---|---|---|
| `~/.tinkr/config.toml` | `tinkr-server init` | Server structure — profiles, LLM, Slack, GitHub, auth |
| `~/.tinkr/.env` | `tinkr-server init` | Secrets — API keys, tokens. **Never commit this file** |
| `~/.tinkr/config` | `tinkr init` | CLI connection — server URL + API token |
| `~/.tinkr/tinker.db` | auto-created | SQLite — REPL sessions, watch state, alert rules |
| `~/.tinkr/repl_history` | auto-created | `tinkr investigate` command history |

---

## Environment variables

| Variable | Description | Default |
|---|---|---|
| `TINKR_BACKEND` | Active backend | `cloudwatch` |
| `ANTHROPIC_API_KEY` | Anthropic API key | — |
| `TINKR_API_KEYS` | JSON array of hashed API keys | `[]` |
| `TINKR_SERVER_URL` | Server URL (CLI override) | `http://localhost:8000` |
| `TINKR_API_TOKEN` | API token (CLI override) | — |
| `TINKR_SERVER_PORT` | Bind port | `8000` |
| `TINKR_SERVER_HOST` | Bind host | `0.0.0.0` |
| `TINKR_DB_PATH` | SQLite path | `~/.tinkr/tinker.db` |

See the [Configuration Reference](/configuration) for the full list.

---

## Generating and hashing API keys

```bash
# Generate a raw key (give this to CLI users)
python -c "import secrets; print(secrets.token_urlsafe(32))"

# Hash it (store the hash in TINKR_API_KEYS)
python -c "import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" <raw-key>
```

`tinkr-server init` does this automatically.
