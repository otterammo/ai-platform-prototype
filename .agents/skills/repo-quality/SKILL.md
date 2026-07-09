---
name: repo-quality
description: Run this repo's formatters, linters, type checks, tests, and pre-commit hygiene. Use when code, tests, packaging, CI, hooks, or developer tooling changes, or before final verification.
---

# Repo Quality

Use this workflow to verify changes in this repository.

## Workflow

1. Read `AGENTS.md`, `Makefile`, and `pyproject.toml` if they have not already been inspected in the current turn.
2. Inspect the current change set with `git status --short` and, when useful, `git diff --name-only`.
3. For Python or formatting-sensitive changes, run `make fmt`.
4. Run `make lint`, `make typecheck`, and `make test` individually while fixing failures, or run `make check` when you expect the full suite to pass.
5. Run `pre-commit run --all-files` when the change touches YAML, TOML, Markdown, hooks, CI, `.gitignore`, or repository hygiene.
6. If any check fails, fix the smallest relevant issue and rerun the failed check. Do not mark the work complete while required checks are failing.
7. In the final response, list the exact verification commands that passed. If a check could not run, include the reason and the risk.

## Command Reference

```bash
make fmt
make lint
make typecheck
make test
make check
pre-commit run --all-files
```

The Makefile prefers `.venv/bin/python` when available and falls back to `python3`.
