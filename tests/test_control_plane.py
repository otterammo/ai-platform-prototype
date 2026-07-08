from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_platform.controllers import ControlPlane
from ai_platform.resources import ResourceKind
from ai_platform.storage import ResourceStore


def make_store_with_mission(
    tmp_path: Path,
    *,
    brief_ref: str = "knowledge://prd.md",
    write_knowledge: bool = True,
) -> tuple[ResourceStore, Path]:
    workspace_root = tmp_path / "workspace"
    knowledge_dir = workspace_root / "knowledge"
    knowledge_dir.mkdir(parents=True)
    if write_knowledge:
        (knowledge_dir / "prd.md").write_text("# Brief\n\nShip authentication.", encoding="utf-8")

    store = ResourceStore(f"sqlite:///{tmp_path / 'platform.db'}", tmp_path / "platform")
    store.apply(
        {
            "apiVersion": "ai.platform/v1",
            "kind": "Workspace",
            "metadata": {"name": "demo"},
            "spec": {"rootPath": str(workspace_root)},
        }
    )
    store.apply(
        {
            "apiVersion": "ai.platform/v1",
            "kind": "Mission",
            "metadata": {"name": "build-auth", "namespace": "demo"},
            "spec": {
                "objective": "Build authentication",
                "brief": {"ref": brief_ref},
            },
        }
    )
    return store, workspace_root


def test_reconcile_creates_fleet_agent_artifact_and_events(tmp_path: Path) -> None:
    store, _workspace_root = make_store_with_mission(tmp_path)

    results = asyncio.run(ControlPlane(store).reconcile_once())

    assert [result.changed for result in results] == [1, 1, 1]
    mission = store.get(ResourceKind.MISSION, "build-auth", "demo")
    fleet = store.get(ResourceKind.FLEET, "build-auth-fleet", "demo")
    agent = store.get(ResourceKind.AGENT, "build-auth-fleet-agent-1", "demo")

    assert mission is not None
    assert mission["status"]["phase"] == "Completed"
    assert mission["status"]["observedGeneration"] == mission["metadata"]["generation"]
    assert fleet is not None
    assert fleet["status"]["phase"] == "Succeeded"
    assert agent is not None
    assert agent["status"]["phase"] == "Succeeded"

    artifact_path = Path(agent["status"]["data"]["artifactPath"])
    assert artifact_path.exists()
    artifact_text = artifact_path.read_text(encoding="utf-8")
    assert "Build authentication" in artifact_text
    assert "Ship authentication" in artifact_text

    event_types = {event["type"] for event in store.list_events(limit=50)}
    assert {
        "MissionCreated",
        "FleetCreated",
        "AgentCreated",
        "AgentStarted",
        "ModelInvoked",
        "ArtifactWritten",
        "MissionCompleted",
    }.issubset(event_types)


def test_reapplying_completed_mission_refreshes_children_and_reruns_agent(tmp_path: Path) -> None:
    store, _workspace_root = make_store_with_mission(tmp_path)
    asyncio.run(ControlPlane(store).reconcile_once())

    store.apply(
        {
            "apiVersion": "ai.platform/v1",
            "kind": "Mission",
            "metadata": {"name": "build-auth", "namespace": "demo"},
            "spec": {
                "objective": "Build authorization",
                "brief": {"ref": "knowledge://prd.md"},
            },
        }
    )

    results = asyncio.run(ControlPlane(store).reconcile_once())

    assert [result.changed for result in results] == [1, 1, 1]
    mission = store.get(ResourceKind.MISSION, "build-auth", "demo")
    fleet = store.get(ResourceKind.FLEET, "build-auth-fleet", "demo")
    agent = store.get(ResourceKind.AGENT, "build-auth-fleet-agent-1", "demo")

    assert mission is not None
    assert mission["metadata"]["generation"] == 2
    assert mission["status"]["phase"] == "Completed"
    assert mission["status"]["observedGeneration"] == 2
    assert fleet is not None
    assert fleet["metadata"]["generation"] == 2
    assert fleet["status"]["observedGeneration"] == 2
    assert agent is not None
    assert agent["metadata"]["generation"] == 2
    assert agent["status"]["observedGeneration"] == 2
    artifacts = store.list_artifacts("demo", "build-auth")
    assert len(artifacts) == 2
    artifact_text = Path(agent["status"]["data"]["artifactPath"]).read_text(encoding="utf-8")
    assert "Build authorization" in artifact_text


def test_missing_knowledge_fails_agent_fleet_and_mission(tmp_path: Path) -> None:
    store, _workspace_root = make_store_with_mission(tmp_path, write_knowledge=False)

    asyncio.run(ControlPlane(store).reconcile_once())

    mission = store.get(ResourceKind.MISSION, "build-auth", "demo")
    fleet = store.get(ResourceKind.FLEET, "build-auth-fleet", "demo")
    agent = store.get(ResourceKind.AGENT, "build-auth-fleet-agent-1", "demo")
    assert mission is not None
    assert mission["status"]["phase"] == "Failed"
    assert fleet is not None
    assert fleet["status"]["phase"] == "Failed"
    assert agent is not None
    assert agent["status"]["phase"] == "Failed"

    event_types = {event["type"] for event in store.list_events(limit=50)}
    assert {"AgentFailed", "FleetFailed", "MissionFailed"}.issubset(event_types)


def test_symlinked_knowledge_outside_workspace_fails_execution(tmp_path: Path) -> None:
    store, workspace_root = make_store_with_mission(
        tmp_path,
        brief_ref="knowledge://linked.md",
        write_knowledge=False,
    )
    outside_file = tmp_path / "outside.md"
    outside_file.write_text("outside workspace", encoding="utf-8")
    try:
        (workspace_root / "knowledge" / "linked.md").symlink_to(outside_file)
    except OSError as exc:
        pytest.skip(f"symlinks are not available: {exc}")

    asyncio.run(ControlPlane(store).reconcile_once())

    mission = store.get(ResourceKind.MISSION, "build-auth", "demo")
    agent = store.get(ResourceKind.AGENT, "build-auth-fleet-agent-1", "demo")
    assert mission is not None
    assert mission["status"]["phase"] == "Failed"
    assert agent is not None
    assert agent["status"]["phase"] == "Failed"
    assert "outside workspace knowledge" in agent["status"]["message"]


def test_delete_mission_cascades_resources_and_artifact_records_only(tmp_path: Path) -> None:
    store, _workspace_root = make_store_with_mission(tmp_path)
    asyncio.run(ControlPlane(store).reconcile_once())
    agent = store.get(ResourceKind.AGENT, "build-auth-fleet-agent-1", "demo")
    assert agent is not None
    artifact_path = Path(agent["status"]["data"]["artifactPath"])
    assert artifact_path.exists()
    assert store.list_artifacts("demo", "build-auth")

    assert store.delete(ResourceKind.MISSION, "build-auth", "demo")

    assert store.get(ResourceKind.WORKSPACE, "demo") is not None
    assert store.get(ResourceKind.MISSION, "build-auth", "demo") is None
    assert store.get(ResourceKind.FLEET, "build-auth-fleet", "demo") is None
    assert store.get(ResourceKind.AGENT, "build-auth-fleet-agent-1", "demo") is None
    assert store.list_artifacts("demo", "build-auth") == []
    assert artifact_path.exists()


def test_delete_workspace_cascades_namespaced_resources_and_artifact_records(tmp_path: Path) -> None:
    store, _workspace_root = make_store_with_mission(tmp_path)
    asyncio.run(ControlPlane(store).reconcile_once())
    assert store.list_artifacts("demo", "build-auth")

    assert store.delete(ResourceKind.WORKSPACE, "demo")

    assert store.get(ResourceKind.WORKSPACE, "demo") is None
    assert store.list(namespace="demo") == []
    assert store.list_artifacts("demo") == []
