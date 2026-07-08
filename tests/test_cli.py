from __future__ import annotations

from pathlib import Path

from ai_platform.cli import main


def test_cli_apply_and_get(tmp_path: Path, capsys) -> None:
    manifest = tmp_path / "resources.yaml"
    workspace_root = tmp_path / "workspace"
    knowledge_dir = workspace_root / "knowledge"
    knowledge_dir.mkdir(parents=True)
    (knowledge_dir / "prd.md").write_text("Ship authentication.", encoding="utf-8")
    manifest.write_text(
        f"""
apiVersion: ai.platform/v1
kind: Workspace
metadata:
  name: demo
spec:
  rootPath: {workspace_root}
---
apiVersion: ai.platform/v1
kind: Mission
metadata:
  name: build-auth
  namespace: demo
spec:
  objective: Build authentication
  brief:
    ref: knowledge://prd.md
""",
        encoding="utf-8",
    )

    db = f"sqlite:///{tmp_path / 'platform.db'}"
    root = str(tmp_path / "platform")

    assert main(["--db", db, "--root", root, "apply", str(manifest)]) == 0
    assert main(["--db", db, "--root", root, "get", "Workspace", "demo"]) == 0
    assert main(["--db", db, "--root", root, "get", "missions"]) == 0
    assert main(["--db", db, "--root", root, "describe", "mission", "build-auth", "-n", "demo"]) == 0

    output = capsys.readouterr().out
    assert "kind: Workspace" in output
    assert "name: demo" in output
    assert "kind: Mission" in output
    assert "resource:" in output


def test_cli_apply_still_accepts_file_flag(tmp_path: Path, capsys) -> None:
    manifest = tmp_path / "resources.yaml"
    manifest.write_text(
        """
apiVersion: ai.platform/v1
kind: Workspace
metadata:
  name: demo
""",
        encoding="utf-8",
    )
    db = f"sqlite:///{tmp_path / 'platform.db'}"
    root = str(tmp_path / "platform")

    assert main(["--db", db, "--root", root, "apply", "-f", str(manifest)]) == 0
    assert main(["--db", db, "--root", root, "list", "workspaces"]) == 0

    output = capsys.readouterr().out
    assert "kind: Workspace" in output
