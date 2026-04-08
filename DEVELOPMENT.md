# Development Guide

Everything you need to run, test, and extend Tinker locally.

---

## Prerequisites

| Tool | Version | Install |
|---|---|---|
| Python | 3.12+ | [python.org](https://python.org) or `brew install python@3.12` |
| uv | latest | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Docker | any recent | [docs.docker.com](https://docs.docker.com/get-docker/) |
| Git | any | system package manager |

---

## Setup

```bash
git clone https://github.com/gettinker/tinker
cd tinker

# Create virtualenv and install all deps (including dev deps)
uv sync

# Verify
uv run tinkr --version
uv run pytest --collect-only   # should collect tests with no errors
```

---

## Running the server locally

### With Grafana backend (no cloud credentials needed)

Set up a minimal `.env`:

```bash
cat > ~/.tinker/.env <<EOF
ANTHROPIC_API_KEY=sk-ant-...
TINKER_BACKEND=grafana
GRAFANA_LOKI_URL=http://localhost:3100
GRAFANA_PROMETHEUS_URL=http://localhost:9090
TINKER_API_KEYS='[{"hash":"<sha256>","subject":"dev","roles":["oncall"]}]'
EOF
```

Start the server in dev mode (auto-reloads on file change):

```bash
uv run tinkr-server --reload
# or
TINKER_BACKEND=grafana uv run python -m tinker.server.app
```

If you want Loki and Prometheus locally, run them separately with Docker:

```bash
docker run -d --name loki -p 3100:3100 grafana/loki:latest
docker run -d --name prometheus -p 9090:9090 \
  -v $(pwd)/prometheus.yml:/etc/prometheus/prometheus.yml \
  prom/prometheus:latest
```

### With a real cloud backend (local dev)

For CloudWatch:
```bash
aws configure   # or aws sso login
export TINKER_BACKEND=cloudwatch
export AWS_REGION=us-east-1
uv run tinkr-server --reload
```

For GCP:
```bash
gcloud auth application-default login
export TINKER_BACKEND=gcp
export GCP_PROJECT_ID=your-project
uv run tinkr-server --reload
```

---

## Running tests

```bash
# All tests
uv run pytest

# Backend tests only
uv run pytest tests/test_backends/

# MCP server tests
uv run pytest tests/test_mcp_servers/

# Agent and guardrail tests
uv run pytest tests/test_agent/

# Filter by name
uv run pytest -k "cloudwatch"

# With output (don't suppress stdout)
uv run pytest -s

# Stop on first failure
uv run pytest -x
```

### Test mocking conventions

- **AWS (CloudWatch):** use `moto` — `@mock_aws` decorator, never hit real AWS
- **GCP / Azure / Datadog / Elastic / OTel:** use `pytest-mock` + `respx` for httpx mocks
- **Grafana:** `respx` HTTP mocks (all Loki/Prometheus/Tempo calls are HTTP)
- **LLM:** mock `anthropic.Anthropic` — no real API calls, no cost in tests
- **Server routes:** use FastAPI `TestClient`
- **GitHub API:** mock `PyGithub` with `pytest-mock`

Never use `--no-verify` or bypass guardrails in tests. Use `MockApproval` for approval flows.

---

## Linting and type checking

```bash
# Lint + auto-fix
uv run ruff check src tests --fix
uv run ruff format src tests

# Type check (strict)
uv run mypy src
```

CI runs both on every PR. Fix all errors before opening a PR.

---

## Project structure

```
src/tinker/
├── backends/       ObservabilityBackend ABC + one file per provider
│   ├── base.py     Dataclasses: LogEntry, MetricPoint, Anomaly, Trace, TraceSpan
│   ├── cloudwatch.py
│   ├── gcp.py
│   ├── azure.py
│   ├── grafana.py
│   ├── datadog.py
│   ├── elastic.py
│   └── otel.py
├── mcp_servers/    MCP wrappers (stdio for local dev, /mcp/sse in prod)
├── server/         FastAPI app
│   ├── app.py      Route registration, lifespan
│   ├── auth.py     API key verification
│   └── routes/     One file per route group
├── agent/          Claude orchestrator, tool definitions, guardrails
├── interfaces/     CLI (Typer) + Slack bot (Bolt)
├── monitor/        Background anomaly detection (watches)
├── code/           Git/GitHub integration, fix application
├── store/          SQLite persistence (TinkerDB)
├── notifiers/      Slack, Discord, webhook notifiers
└── config.py       Pydantic-settings — all env vars
```

---

## Adding a new backend

1. Create `src/tinker/backends/<name>.py` — subclass `ObservabilityBackend`:
   ```python
   class MyBackend(ObservabilityBackend):
       async def query_logs(self, service, since, filter_pattern, limit) -> list[LogEntry]: ...
       async def get_metrics(self, service, metric_name, since) -> list[MetricPoint]: ...
       async def detect_anomalies(self, service, since) -> list[Anomaly]: ...
       async def get_traces(self, service, since, limit, tags) -> list[Trace]: ...
   ```

2. Register in `src/tinker/backends/__init__.py`:
   ```python
   _REGISTRY = {
       ...
       "mybackend": "tinker.backends.mybackend.MyBackend",
   }
   ```

3. Add env vars to `src/tinker/config.py` and `.env.example`

4. Create `src/tinker/mcp_servers/<name>_server.py`

5. Add entry point in `pyproject.toml`:
   ```toml
   tinker-mybackend-mcp = "tinker.mcp_servers.mybackend_server:main"
   ```

6. Write tests at `tests/test_backends/test_<name>.py`

7. Add docs at `docs/docs/backends/<name>.md`

---

## Key design rules (do not violate)

- **`apply_fix` and `create_pr` always require explicit approval** — never bypass `ApprovalRequired` in non-test code
- **Sanitize before LLM** — always pass log data through `sanitize_log_content()` before including in a prompt
- **MCP servers are stateless** — no session state, no in-memory caches between calls
- **Backends are selected once at startup** — no per-request backend switching
- **No cloud credentials on the server** — use IAM role / Workload Identity / Managed Identity
- **No `print()` in library code** — use `structlog.get_logger()`

---

## Docs

The docs site lives in `docs/` and is built with Docusaurus.

```bash
cd docs
npm install
npm start        # dev server at http://localhost:3000
npm run build    # production build → docs/build/
```

When adding a feature, update the relevant page in `docs/docs/`. The sidebar is configured in `docs/sidebars.js`.

---

## Releasing

Releases are managed by maintainers. The process:

1. Bump version in `pyproject.toml`
2. Update `CHANGELOG.md` (if it exists)
3. Tag: `git tag v0.x.0 && git push --tags`
4. GitHub Actions builds and pushes the Docker image to the registry
5. Docs deploy automatically on push to `main`
