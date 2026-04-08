# Tinkr

> **Early development** — APIs and config formats may change. Contributions welcome!

Open-source AI-powered observability and incident response agent. Connects to your cloud backend, analyzes logs and metrics, cross-references incidents with your codebase, and suggests fixes — with human approval before any code changes.

**Full documentation: [gettinker.github.io/tinkr](https://gettinker.github.io/tinkr/)**

---

## Quick start

```bash
# Install
uv tool install tinkr   # or: pipx install tinkr

# On the machine with cloud access — run setup wizard + start server
tinkr-server init
tinkr-server start

# On each developer machine — connect CLI to the server
tinkr init
tinkr doctor

# Run your first query
tinkr anomaly payments-api
tinkr rca payments-api
```

For backend setup, deployment options (Docker, AWS, GCP, Azure), Slack bot, and GitHub integration see the [docs](https://gettinker.github.io/tinkr/).

---

## Development setup

See [DEVELOPMENT.md](DEVELOPMENT.md) for the full setup guide, including running the [tinker-test-services](https://github.com/gettinker/tinker-test-services) stack (Loki + Prometheus + dummy microservices) for a no-cloud-credentials local environment.

---

## Testing

```bash
uv run pytest                          # all tests
uv run pytest tests/test_backends/    # backend unit tests
uv run pytest tests/test_agent/       # agent + guardrail tests
uv run pytest -k cloudwatch           # filter by name
```

- AWS: `moto` mocks — no real AWS calls
- GCP / Azure / Datadog: `pytest-mock` + `respx` for HTTP mocks
- LLM: `anthropic.Anthropic` is mocked — no real API calls
- Server routes: FastAPI `TestClient`

---

## Contributing

- [Open an issue](https://github.com/gettinker/tinkr/issues) for bugs or feature requests
- [CONTRIBUTING.md](CONTRIBUTING.md) — PR workflow, code style, commit conventions
- [DEVELOPMENT.md](DEVELOPMENT.md) — adding backends, MCP servers, and agent tools

For the security model, credential design, and architecture decisions, see [Security](https://gettinker.github.io/tinkr/security) and [CLAUDE.md](.claude/CLAUDE.md) in this repo.

---

## License

[MIT](LICENSE)
