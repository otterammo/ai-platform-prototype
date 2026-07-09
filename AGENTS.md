# AGENTS.md

## Repository Expectations

- Use the Makefile targets for quality checks: `make fmt`, `make lint`, `make typecheck`, `make test`, and `make check`.
- Before finishing code, test, packaging, CI, or hook changes, run `make check` and fix failures.
- Run `pre-commit run --all-files` when changing YAML, TOML, Markdown, hooks, or repository hygiene files.
- The Makefile automatically prefers `.venv/bin/python` when the local virtualenv exists; do not require users to activate it just to run checks.
- Do not commit or push unless the user explicitly asks.

## Project Routing

- Persistence changes usually belong in `ai_platform/storage.py`.
- Reconciliation behavior usually belongs in `ai_platform/controllers.py`.
- Model provider behavior should stay isolated in `ai_platform/models.py`.
- Runtime execution and artifact writing usually belong in `ai_platform/runtime.py`.
- Resource schema and validation changes usually belong in `ai_platform/resources.py`.

## Reporting

- Final responses should name the checks that ran.
- If a required check cannot run, explain the command, failure, and remaining risk.
