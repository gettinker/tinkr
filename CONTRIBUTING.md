# Contributing to Tinkr

Thank you for your interest in contributing. This document covers how to get set up, what we look for in PRs, and the areas where help is most welcome.

---

## Ways to contribute

- **Bug reports** — open an issue with steps to reproduce, expected vs actual behavior, and your backend/OS
- **Feature requests** — open an issue describing the use case, not just the feature
- **Code** — bug fixes, new backends, new commands, performance improvements
- **Docs** — corrections, examples, tutorials, translations
- **Tests** — adding coverage for untested paths

---

## Development setup

See [DEVELOPMENT.md](DEVELOPMENT.md) for the full local setup guide.

Quick start:

```bash
git clone https://github.com/gettinker/tinkr
uv sync
uv run pytest
```

---

## Opening a pull request

1. **Fork** the repo and create a branch from `main`:
   ```bash
   git checkout -b fix/stripe-timeout-retry
   ```

2. **Make your change.** Keep it focused — one logical change per PR.

3. **Add or update tests.** PRs that reduce test coverage will not be merged.

4. **Run the full suite locally:**
   ```bash
   uv run pytest
   uv run ruff check src tests
   uv run mypy src
   ```

5. **Write a clear commit message** — explain *why*, not just *what*.

6. **Open the PR** against `main`. Fill in the template — describe the problem, the solution, and how you tested it.

7. A maintainer will review within a few days. Please address feedback promptly.

---

## What makes a good PR

- **Small and focused.** A PR that does one thing is easier to review and faster to merge than one that does five.
- **Tests included.** Every new code path should have a test. Use the existing mock patterns — no real cloud API calls in tests.
- **No breaking changes without discussion.** If you want to change a public interface (CLI flags, API shape, config keys), open an issue first.
- **Docs updated.** If you add a flag, command, or config key, update the relevant page in `docs/`.

---

## Adding a new backend

The most impactful contributions are new observability backends. The steps are documented in [CLAUDE.md](.claude/CLAUDE.md) under "Adding a new backend". In short:

1. `src/tinker/backends/<name>.py` — subclass `ObservabilityBackend`, implement `query_logs`, `get_metrics`, `detect_anomalies`, `get_traces`
2. Register it in `src/tinker/backends/__init__.py`
3. Add env vars to `src/tinker/config.py` and `.env.example`
4. Add an MCP server at `src/tinker/mcp_servers/<name>_server.py`
5. Add tests at `tests/test_backends/test_<name>.py` — use `pytest-mock` / `respx`, no real API calls
6. Add a docs page at `docs/docs/backends/<name>.md`

---

## Code style

- **Formatter / linter:** `ruff` — run `uv run ruff check src tests` and `uv run ruff format src tests`
- **Type checker:** `mypy --strict` — run `uv run mypy src`
- **Python version:** 3.12+
- **Line length:** 100
- **No `print()`** in library code — use `structlog`

CI enforces all of the above.

---

## Security issues

Please **do not** open a public issue for security vulnerabilities. Email `security@gettinker.dev` instead. We aim to respond within 48 hours and will coordinate a disclosure timeline with you.

---

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
