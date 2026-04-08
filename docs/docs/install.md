---
sidebar_position: 2
title: Installation
---

# Installation

## Requirements

- Python **3.12** or higher (for building from source)
- Docker (for running as a container)
- An [Anthropic API key](https://console.anthropic.com/) (`ANTHROPIC_API_KEY`)
- Access to a cloud observability backend — CloudWatch, GCP, Azure, Grafana/Loki, Datadog, Elastic, or OTel

---

## Option 1 — Build from source and run with Docker (recommended)

```bash
git clone https://github.com/gettinker/tinker
cd tinker
docker build -t tinker:local .
```

Create `~/.tinker/.env`:

```bash title="~/.tinker/.env"
ANTHROPIC_API_KEY=sk-ant-...
TINKER_BACKEND=cloudwatch   # or gcp, azure, grafana, datadog, elastic, otel
TINKER_API_KEYS='[{"hash":"<sha256>","subject":"alice","roles":["oncall"]}]'
```

Run:

```bash
docker run -d \
  --name tinker \
  -p 8000:8000 \
  --env-file ~/.tinker/.env \
  -v ~/.tinker:/root/.tinker \
  tinker:local
```

---

## Option 2 — Run directly from source

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
git clone https://github.com/gettinker/tinker
cd tinker

# Install dependencies
uv sync

# Run the server
TINKER_BACKEND=cloudwatch uv run tinkr-server

# Or install the CLI globally (available in PATH everywhere)
uv tool install --editable .
tinkr --version
```

---

## Verify

```bash
curl http://localhost:8000/health
# {"status":"ok","version":"0.1.0"}

tinkr --version
tinkr --help
```

---

## First-time server setup

Run the setup wizard to generate `~/.tinker/config.toml` and `~/.tinker/.env`:

```bash
tinkr init server
```

The wizard walks through:
1. LLM provider and API key
2. Slack bot (optional)
3. GitHub integration (optional — enables `fix` and `approve`)
4. Server API key (for CLI auth)
5. Cloud backend profile

Then connect the CLI:

```bash
tinkr init cli
# Tinker server URL [http://localhost:8000]: https://tinker.acme.internal
# API token: <paste key from wizard>
```

---

## File locations

All per-user state lives in `~/.tinker/`:

| File | Written by | Purpose |
|---|---|---|
| `~/.tinker/config.toml` | `tinkr init server` | Server structure — profiles, LLM, Slack, GitHub, auth |
| `~/.tinker/.env` | `tinkr init server` | Secrets — API keys, tokens. **Never commit this file** |
| `~/.tinker/config` | `tinkr init cli` | CLI connection — server URL + API token |
| `~/.tinker/tinker.db` | auto-created | SQLite — REPL sessions, watch state, alert rules |
| `~/.tinker/repl_history` | auto-created | `tinkr investigate` command history |

---

## Environment variables

| Variable | Description | Default |
|---|---|---|
| `TINKER_BACKEND` | Active backend | `cloudwatch` |
| `ANTHROPIC_API_KEY` | Anthropic API key | — |
| `TINKER_API_KEYS` | JSON array of hashed API keys | `[]` |
| `TINKER_SERVER_URL` | Server URL (CLI override) | `http://localhost:8000` |
| `TINKER_API_TOKEN` | API token (CLI override) | — |
| `TINKER_SERVER_PORT` | Bind port | `8000` |
| `TINKER_SERVER_HOST` | Bind host | `0.0.0.0` |
| `TINKER_DB_PATH` | SQLite path | `~/.tinker/tinker.db` |

See the [Configuration Reference](/configuration) for the full list.

---

## Generating and hashing API keys

```bash
# Generate a raw key (give this to CLI users)
python -c "import secrets; print(secrets.token_urlsafe(32))"

# Hash it (store the hash in TINKER_API_KEYS)
python -c "import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" <raw-key>
```

`tinkr init server` does this automatically.
