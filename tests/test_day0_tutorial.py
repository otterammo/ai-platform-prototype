from __future__ import annotations

import shutil
from pathlib import Path

from ai_platform.cli import main
from ai_platform.resources import ResourceKind
from ai_platform.storage import ResourceStore


def test_day0_tutorial_acceptance_flow(tmp_path: Path, capsys) -> None:
    repo_root = Path(__file__).parents[1]
    source_assets = repo_root / "docs" / "tutorials" / "assets" / "day0"
    day0_dir = tmp_path / "day0"
    shutil.copytree(source_assets, day0_dir)

    db = f"sqlite:///{tmp_path / 'platform.db'}"
    root = str(tmp_path / "platform")

    def run(*args: str) -> None:
        assert main(["--db", db, "--root", root, *args]) == 0

    run("version")
    run("health")
    run("apply", str(day0_dir / "workspace.yaml"))
    run("get", "workspaces")
    run("apply", str(day0_dir / "knowledge.yaml"))
    run("knowledge", "index", "-n", "day0")
    run("knowledge", "search", "login", "-n", "day0")
    run("apply", str(day0_dir / "mission.yaml"))
    run(
        "wait",
        "mission",
        "implement-login-page",
        "-n",
        "day0",
        "--for",
        "phase=Waiting",
        "--reconcile",
        "--timeout",
        "5",
        "--interval",
        "0.01",
    )
    run("get", "missions", "-n", "day0")
    run("get", "fleets", "-n", "day0")
    run("get", "agents", "-n", "day0")
    run("get", "agentruns", "-n", "day0")
    run("events", "-n", "day0", "--kind", "Mission", "--name", "implement-login-page", "--limit", "20")
    run("timeline", "mission", "implement-login-page", "-n", "day0")
    run("trace", "mission", "implement-login-page", "-n", "day0")
    run("approvals")

    store = ResourceStore(db, root)
    approvals = store.list(ResourceKind.APPROVAL)
    assert len(approvals) == 1
    approval_name = approvals[0]["metadata"]["name"]
    mission = store.get(ResourceKind.MISSION, "implement-login-page", "day0")
    assert mission is not None
    assert mission["status"]["phase"] == "Waiting"

    run("describe", "approval", approval_name)
    run("approve", approval_name, "--by", "day0-test", "--reason", "Day 0 acceptance test")
    run(
        "wait",
        "mission",
        "implement-login-page",
        "-n",
        "day0",
        "--for",
        "phase=Completed",
        "--reconcile",
        "--timeout",
        "5",
        "--interval",
        "0.01",
    )
    run("get", "artifacts", "-n", "day0")

    artifacts = store.list_artifacts("day0", "implement-login-page")
    assert len(artifacts) == 1
    artifact_name = artifacts[0]["name"]
    artifact_path = Path(artifacts[0]["path"])
    assert artifact_path.exists()
    artifact_text = artifact_path.read_text(encoding="utf-8")
    assert "Template: login-page" in artifact_text
    assert "login page" in artifact_text.lower()
    assert "Source: knowledge://prd.md" in artifact_text
    run("describe", "artifact", artifact_name, "-n", "day0")

    run("trace", "mission", "implement-login-page", "-n", "day0")
    output = capsys.readouterr().out
    assert "Approval required" in output
    assert "Approval granted" in output
    assert "Artifact" in output

    run("delete", "mission", "implement-login-page", "-n", "day0")
    assert store.get(ResourceKind.MISSION, "implement-login-page", "day0") is None
    assert store.get(ResourceKind.FLEET, "implement-login-page-fleet", "day0") is None
    assert store.get(ResourceKind.AGENT, "implement-login-page-fleet-implementer", "day0") is None
    assert store.get(ResourceKind.AGENT_RUN, "implement-login-page-fleet-implementer-run-1", "day0") is None
    assert store.list_artifacts("day0", "implement-login-page") == []
    assert artifact_path.exists()

    run("delete", "approval", approval_name)
    run("delete", "workspace", "day0")
    assert store.get(ResourceKind.WORKSPACE, "day0") is None
    assert store.list(namespace="day0") == []
