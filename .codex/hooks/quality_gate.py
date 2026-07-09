#!/usr/bin/env python3
"""Run the repository quality gate from Codex lifecycle hooks."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

QUALITY_PATHS = {
    ".pre-commit-config.yaml",
    "AGENTS.md",
    "Makefile",
    "pyproject.toml",
}

QUALITY_PREFIXES = (
    ".agents/",
    ".codex/",
    ".github/",
    "ai_platform/",
    "examples/",
    "tests/",
)

QUALITY_SUFFIXES = (
    ".py",
    ".toml",
    ".yaml",
    ".yml",
)


def run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def repo_root() -> Path:
    result = run(["git", "rev-parse", "--show-toplevel"], Path.cwd())
    if result.returncode == 0:
        return Path(result.stdout.strip())
    return Path.cwd()


def changed_paths(root: Path) -> list[str]:
    result = run(["git", "status", "--porcelain", "--untracked-files=all"], root)
    if result.returncode != 0:
        print(result.stdout.strip())
        return []

    paths: list[str] = []
    for line in result.stdout.splitlines():
        if not line:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", maxsplit=1)[1]
        paths.append(path)
    return paths


def should_run_quality_gate(paths: list[str]) -> bool:
    for path in paths:
        if path in QUALITY_PATHS:
            return True
        if path.startswith(QUALITY_PREFIXES):
            return True
        if path.endswith(QUALITY_SUFFIXES):
            return True
    return False


def main() -> int:
    root = repo_root()
    paths = changed_paths(root)
    if not paths:
        print("Codex quality gate: no changed files detected.")
        return 0
    if not should_run_quality_gate(paths):
        print("Codex quality gate: no quality-sensitive changes detected.")
        return 0
    if not (root / "Makefile").is_file():
        print("Codex quality gate: Makefile not found; skipping.")
        return 0

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    result = subprocess.run(
        ["make", "check"],
        cwd=root,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
    )
    if result.stdout:
        print(result.stdout.rstrip())
    if result.returncode != 0:
        print("Codex quality gate failed. Fix the issue, then rerun `make check`.")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
