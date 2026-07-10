from __future__ import annotations

import json
from pathlib import Path

from ai_platform.cli import main
from ai_platform.resources import ResourceKind
from ai_platform.storage import ResourceStore


def test_cli_version_does_not_create_store(tmp_path: Path, capsys) -> None:
    db_path = tmp_path / "platform.db"

    assert main(["--db", f"sqlite:///{db_path}", "--root", str(tmp_path / "platform"), "version", "-o", "json"]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["version"] == "0.1.0"
    assert not db_path.exists()


def test_cli_health_checks_local_store(tmp_path: Path, capsys) -> None:
    db_path = tmp_path / "platform.db"

    assert main(["--db", f"sqlite:///{db_path}", "--root", str(tmp_path / "platform"), "health", "-o", "json"]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "ok"
    assert output["platform"]["phase"] == "Ready"
    assert db_path.exists()


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


def test_cli_wait_with_reconcile_and_filtered_events(tmp_path: Path, capsys) -> None:
    manifest = tmp_path / "resources.yaml"
    manifest.write_text(
        f"""
apiVersion: ai.platform/v1
kind: Workspace
metadata:
  name: demo
spec:
  rootPath: {tmp_path / "workspace"}
---
apiVersion: ai.platform/v1
kind: Mission
metadata:
  name: build-login
  namespace: demo
spec:
  objective: Build login page
""",
        encoding="utf-8",
    )
    db = f"sqlite:///{tmp_path / 'platform.db'}"
    root = str(tmp_path / "platform")

    assert main(["--db", db, "--root", root, "apply", str(manifest)]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "--db",
                db,
                "--root",
                root,
                "wait",
                "mission",
                "build-login",
                "-n",
                "demo",
                "--for",
                "phase=Completed",
                "--reconcile",
                "--timeout",
                "5",
                "--interval",
                "0.01",
                "-o",
                "json",
            ]
        )
        == 0
    )
    waited = json.loads(capsys.readouterr().out)
    assert waited["status"] == "met"
    assert waited["resource"]["status"]["phase"] == "Completed"

    assert (
        main(
            [
                "--db",
                db,
                "--root",
                root,
                "events",
                "-n",
                "demo",
                "--kind",
                "Mission",
                "--name",
                "build-login",
                "--limit",
                "20",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "MissionCreated" in output
    assert "AgentRunCreated" not in output


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


def test_cli_lists_v1_registry_resources(tmp_path: Path, capsys) -> None:
    manifest = tmp_path / "v1.yaml"
    manifest.write_text(
        """
apiVersion: ai.platform/v1
kind: Workspace
metadata:
  name: demo
---
apiVersion: ai.platform/v1
kind: Model
metadata:
  name: stub-model
spec:
  config:
    provider: stub
    model: stub-model
---
apiVersion: ai.platform/v1
kind: Tool
metadata:
  name: git
spec: {}
---
apiVersion: ai.platform/v1
kind: Capability
metadata:
  name: code-review
spec:
  requires:
    tools:
      - git
  compatibleModels:
    - stub-model
---
apiVersion: ai.platform/v1
kind: FleetTemplate
metadata:
  name: software-feature
spec:
  agents:
    - name: reviewer
      role: reviewer
      capabilities:
        - code-review
---
apiVersion: ai.platform/v1
kind: Knowledge
metadata:
  name: prd
  namespace: demo
spec:
  type: PRD
  ref: knowledge://prd.md
""",
        encoding="utf-8",
    )
    db = f"sqlite:///{tmp_path / 'platform.db'}"
    root = str(tmp_path / "platform")

    assert main(["--db", db, "--root", root, "apply", str(manifest)]) == 0
    assert main(["--db", db, "--root", root, "get", "models"]) == 0
    assert main(["--db", db, "--root", root, "get", "tools"]) == 0
    assert main(["--db", db, "--root", root, "get", "capabilities"]) == 0
    assert main(["--db", db, "--root", root, "get", "fleettemplates"]) == 0
    assert main(["--db", db, "--root", root, "get", "knowledge", "-n", "demo"]) == 0
    assert main(["--db", db, "--root", root, "describe", "capability", "code-review"]) == 0

    output = capsys.readouterr().out
    assert "kind: Model" in output
    assert "kind: Tool" in output
    assert "kind: Capability" in output
    assert "kind: FleetTemplate" in output
    assert "kind: Knowledge" in output
    assert "resource:" in output


def test_cli_trace_and_timeline_mission(tmp_path: Path, capsys) -> None:
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
    assert main(["--db", db, "--root", root, "reconcile"]) == 0
    assert main(["--db", db, "--root", root, "trace", "mission", "build-auth", "-n", "demo"]) == 0
    assert main(["--db", db, "--root", root, "timeline", "mission", "build-auth", "-n", "demo"]) == 0

    output = capsys.readouterr().out
    assert "Mission build-auth" in output
    assert "Status: Completed" in output
    assert "Executor Agent" in output
    assert "Knowledge" in output
    assert "Index: default" in output
    assert "Mission created" in output
    assert "Mission completed" in output


def test_cli_knowledge_index_search_and_aliases(tmp_path: Path, capsys) -> None:
    manifest = tmp_path / "resources.yaml"
    workspace_root = tmp_path / "workspace"
    knowledge_dir = workspace_root / "knowledge"
    knowledge_dir.mkdir(parents=True)
    (knowledge_dir / "prd.md").write_text("# PRD\n\nShip authentication.", encoding="utf-8")
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
kind: Knowledge
metadata:
  name: prd
  namespace: demo
spec:
  type: PRD
  ref: knowledge://prd.md
---
apiVersion: ai.platform/v1
kind: KnowledgeIndex
metadata:
  name: default
  namespace: demo
spec:
  sources:
    - knowledge://prd.md
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
    assert main(["--db", db, "--root", root, "knowledge", "index", "-n", "demo"]) == 0
    assert main(["--db", db, "--root", root, "knowledge", "search", "authentication", "-n", "demo"]) == 0
    assert main(["--db", db, "--root", root, "describe", "knowledgeindex", "default", "-n", "demo"]) == 0
    assert main(["--db", db, "--root", root, "reconcile"]) == 0
    assert main(["--db", db, "--root", root, "get", "agentruns", "-n", "demo"]) == 0
    assert main(["--db", db, "--root", root, "get", "contexts", "-n", "demo"]) == 0
    assert main(["--db", db, "--root", root, "get", "artifacts", "-n", "demo"]) == 0
    assert (
        main(
            [
                "--db",
                db,
                "--root",
                root,
                "describe",
                "artifact",
                "build-auth-fleet-agent-1-run-1-artifact",
                "-n",
                "demo",
            ]
        )
        == 0
    )

    output = capsys.readouterr().out
    assert "kind: KnowledgeIndex" in output
    assert "document: prd.md" in output
    assert "preview: '# PRD Ship authentication.'" in output
    assert "kind: AgentRun" in output
    assert "kind: Context" in output
    assert "kind: Artifact" in output


def test_cli_approval_workflow(tmp_path: Path, capsys) -> None:
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
kind: Model
metadata:
  name: stub-model
spec:
  config:
    provider: stub
    model: stub-model
---
apiVersion: ai.platform/v1
kind: Tool
metadata:
  name: git
spec: {{}}
---
apiVersion: ai.platform/v1
kind: Capability
metadata:
  name: implement
spec:
  requires:
    tools:
      - git
  compatibleModels:
    - stub-model
---
apiVersion: ai.platform/v1
kind: FleetTemplate
metadata:
  name: protected-feature
spec:
  agents:
    - name: coder
      role: coder
      capabilities:
        - implement
---
apiVersion: ai.platform/v1
kind: Policy
metadata:
  name: default
spec:
  rules:
    - match:
        tool: git
        operation: use
      requiresApproval: true
    - match:
        tool: knowledge
      allow: true
    - match:
        tool: model
      allow: true
    - match:
        tool: filesystem
      allow: true
---
apiVersion: ai.platform/v1
kind: Mission
metadata:
  name: build-auth
  namespace: demo
spec:
  template: protected-feature
  inputs:
    prd:
      ref: knowledge://prd.md
""",
        encoding="utf-8",
    )
    db = f"sqlite:///{tmp_path / 'platform.db'}"
    root = str(tmp_path / "platform")

    assert main(["--db", db, "--root", root, "apply", str(manifest)]) == 0
    assert main(["--db", db, "--root", root, "reconcile"]) == 0
    assert main(["--db", db, "--root", root, "approvals"]) == 0
    store = ResourceStore(db, root)
    approvals = store.list(ResourceKind.APPROVAL)
    assert len(approvals) == 1
    approval_name = approvals[0]["metadata"]["name"]
    assert main(["--db", db, "--root", root, "describe", "approval", approval_name]) == 0
    assert main(["--db", db, "--root", root, "approve", approval_name, "--by", "cli"]) == 0

    mission = store.get(ResourceKind.MISSION, "build-auth", "demo")
    approval = store.get(ResourceKind.APPROVAL, approval_name)
    assert mission is not None
    assert mission["status"]["phase"] == "Completed"
    assert approval is not None
    assert approval["status"]["phase"] == "Approved"
    output = capsys.readouterr().out
    assert "kind: Approval" in output
    assert "approvedBy: cli" in output


def test_cli_serve_invokes_uvicorn(tmp_path: Path, monkeypatch) -> None:
    import uvicorn

    import ai_platform.cli as cli

    calls = {}

    def fake_run(app: str, host: str, port: int, reload: bool) -> None:
        calls["app"] = app
        calls["host"] = host
        calls["port"] = port
        calls["reload"] = reload

    def fail_asyncio_run(coro: object) -> int:
        raise AssertionError(f"serve must not run inside asyncio.run: {coro!r}")

    monkeypatch.setattr(cli.asyncio, "run", fail_asyncio_run)
    monkeypatch.setattr(uvicorn, "run", fake_run)

    assert (
        main(
            [
                "--db",
                f"sqlite:///{tmp_path / 'platform.db'}",
                "--root",
                str(tmp_path / "platform"),
                "serve",
                "--host",
                "0.0.0.0",
                "--port",
                "9001",
            ]
        )
        == 0
    )
    assert calls == {
        "app": "ai_platform.api:app",
        "host": "0.0.0.0",
        "port": 9001,
        "reload": False,
    }
