from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_platform.controllers import ControlPlane
from ai_platform.observability import build_timeline, build_trace, describe_resource, format_timeline, format_trace
from ai_platform.resources import ResourceKind, parse_resource_documents
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


def make_store_with_template_mission(
    tmp_path: Path,
    *,
    omit_capability: bool = False,
    omit_tool: bool = False,
    omit_model: bool = False,
    incompatible_models: bool = False,
) -> tuple[ResourceStore, Path]:
    workspace_root = tmp_path / "workspace"
    knowledge_dir = workspace_root / "knowledge"
    knowledge_dir.mkdir(parents=True)
    (knowledge_dir / "prd.md").write_text("# PRD\n\nShip authentication.", encoding="utf-8")

    store = ResourceStore(f"sqlite:///{tmp_path / 'platform.db'}", tmp_path / "platform")
    store.apply(
        {
            "apiVersion": "ai.platform/v1",
            "kind": "Workspace",
            "metadata": {"name": "demo"},
            "spec": {"rootPath": str(workspace_root)},
        }
    )
    if not omit_model:
        store.apply(
            {
                "apiVersion": "ai.platform/v1",
                "kind": "Model",
                "metadata": {"name": "stub-model"},
                "spec": {"config": {"provider": "stub", "model": "stub-model"}},
            }
        )
    if incompatible_models:
        store.apply(
            {
                "apiVersion": "ai.platform/v1",
                "kind": "Model",
                "metadata": {"name": "other-model"},
                "spec": {"config": {"provider": "stub", "model": "other-model"}},
            }
        )
    if not omit_tool:
        store.apply({"apiVersion": "ai.platform/v1", "kind": "Tool", "metadata": {"name": "git"}, "spec": {}})
        store.apply({"apiVersion": "ai.platform/v1", "kind": "Tool", "metadata": {"name": "filesystem"}, "spec": {}})
    if not omit_capability:
        store.apply(
            {
                "apiVersion": "ai.platform/v1",
                "kind": "Capability",
                "metadata": {"name": "plan"},
                "spec": {
                    "requires": {"tools": ["filesystem"]},
                    "compatibleModels": ["stub-model"],
                },
            }
        )
        store.apply(
            {
                "apiVersion": "ai.platform/v1",
                "kind": "Capability",
                "metadata": {"name": "implement"},
                "spec": {
                    "requires": {"tools": ["git", "filesystem"]},
                    "compatibleModels": ["stub-model"],
                },
            }
        )
        store.apply(
            {
                "apiVersion": "ai.platform/v1",
                "kind": "Capability",
                "metadata": {"name": "review"},
                "spec": {
                    "requires": {"tools": ["git"]},
                    "compatibleModels": ["other-model"] if incompatible_models else ["stub-model"],
                },
            }
        )
    store.apply(
        {
            "apiVersion": "ai.platform/v1",
            "kind": "FleetTemplate",
            "metadata": {"name": "software-feature"},
            "spec": {
                "agents": [
                    {"name": "planner", "role": "planner", "capabilities": ["plan"]},
                    {"name": "coder", "role": "coder", "capabilities": ["implement"]},
                    {
                        "name": "reviewer",
                        "role": "reviewer",
                        "capabilities": ["plan", "review"] if incompatible_models else ["review"],
                    },
                ]
            },
        }
    )
    store.apply(
        {
            "apiVersion": "ai.platform/v1",
            "kind": "Knowledge",
            "metadata": {"name": "prd", "namespace": "demo"},
            "spec": {"type": "PRD", "ref": "knowledge://prd.md"},
        }
    )
    store.apply(
        {
            "apiVersion": "ai.platform/v1",
            "kind": "Mission",
            "metadata": {"name": "build-auth", "namespace": "demo"},
            "spec": {
                "template": "software-feature",
                "inputs": {"prd": {"ref": "knowledge://prd.md"}},
                "outputs": {"code": True, "report": True},
            },
        }
    )
    return store, workspace_root


def condition_status(resource: dict, condition_type: str) -> str | None:
    for condition in resource["status"].get("conditions", []):
        if condition["type"] == condition_type:
            return condition["status"]
    return None


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
        "ArtifactCreated",
        "MissionCompleted",
    }.issubset(event_types)


def test_reconciliation_events_share_correlation_id_and_structured_fields(tmp_path: Path) -> None:
    store, _workspace_root = make_store_with_template_mission(tmp_path)

    asyncio.run(ControlPlane(store).reconcile_once())

    mission = store.get(ResourceKind.MISSION, "build-auth", "demo")
    assert mission is not None
    correlation_id = mission["status"]["data"]["correlationId"]
    events = store.list_events(namespace="demo", correlation_id=correlation_id, limit=None, ascending=True)

    assert events
    assert all(event["correlationId"] == correlation_id for event in events)
    for event in events:
        assert event["timestamp"]
        assert event["workspace"] == "demo"
        assert event["controller"]
        assert event["action"]
        assert event["reason"]
        assert "correlationId" in event


def test_conditions_are_updated_for_completed_resources(tmp_path: Path) -> None:
    store, _workspace_root = make_store_with_template_mission(tmp_path)

    asyncio.run(ControlPlane(store).reconcile_once())

    mission = store.get(ResourceKind.MISSION, "build-auth", "demo")
    fleet = store.get(ResourceKind.FLEET, "build-auth-fleet", "demo")
    agent = store.get(ResourceKind.AGENT, "build-auth-fleet-planner", "demo")
    assert mission is not None
    assert fleet is not None
    assert agent is not None

    assert condition_status(mission, "Completed") == "True"
    assert condition_status(mission, "Failed") == "False"
    assert condition_status(fleet, "Completed") == "True"
    assert condition_status(fleet, "Scheduled") == "True"
    assert condition_status(agent, "Completed") == "True"
    assert condition_status(agent, "Scheduled") == "True"


def test_decision_events_record_template_capabilities_tools_and_models(tmp_path: Path) -> None:
    store, _workspace_root = make_store_with_template_mission(tmp_path)

    asyncio.run(ControlPlane(store).reconcile_once())

    event_types = {event["type"] for event in store.list_events(namespace="demo", limit=200)}
    assert {
        "FleetTemplateSelected",
        "CapabilityResolved",
        "ToolResolved",
        "ModelResolved",
        "ReconciliationStarted",
        "ReconciliationCompleted",
    }.issubset(event_types)

    model_events = [
        event
        for event in store.list_events(namespace="demo", limit=200)
        if event["type"] == "ModelResolved" and event["resourceName"] == "build-auth-fleet-planner"
    ]
    assert model_events
    assert model_events[0]["controller"] == "FleetController"
    assert model_events[0]["reason"] == "CompatibleModelFound"
    assert model_events[0]["payload"]["model"] == "stub-model"


def test_trace_generation_reconstructs_execution_graph(tmp_path: Path) -> None:
    store, _workspace_root = make_store_with_template_mission(tmp_path)

    asyncio.run(ControlPlane(store).reconcile_once())

    trace = build_trace(store, "build-auth", "demo")
    assert trace is not None
    assert trace["mission"]["status"] == "Completed"
    assert trace["mission"]["correlationId"]
    assert trace["fleets"][0]["template"] == "software-feature"
    assert [agent["role"] for agent in trace["fleets"][0]["agents"]] == ["planner", "coder", "reviewer"]
    assert trace["fleets"][0]["agents"][0]["capabilities"] == ["plan"]
    assert trace["fleets"][0]["agents"][0]["model"] == "stub-model"
    assert trace["fleets"][0]["agents"][1]["tools"] == ["git", "filesystem"]
    assert len(trace["artifacts"]) == 3

    formatted = format_trace(trace)
    assert "Mission build-auth" in formatted
    assert "Status: Completed" in formatted
    assert "Planner Agent" in formatted
    assert "Model stub-model" in formatted
    assert "Tool filesystem" in formatted


def test_timeline_orders_events_by_timestamp(tmp_path: Path) -> None:
    store, _workspace_root = make_store_with_template_mission(tmp_path)

    asyncio.run(ControlPlane(store).reconcile_once())

    timeline = build_timeline(store, "build-auth", "demo")
    assert timeline is not None
    items = timeline["items"]
    assert [item["id"] for item in items] == sorted(item["id"] for item in items)
    assert any(item["message"] == "Mission created" for item in items)
    assert any(item["message"] == "Fleet created" for item in items)
    assert any(item["message"] == "Planner completed" for item in items)

    formatted = format_timeline(timeline)
    assert "Mission created" in formatted
    assert "Mission completed" in formatted


def test_describe_mission_includes_observability_sections(tmp_path: Path) -> None:
    store, _workspace_root = make_store_with_template_mission(tmp_path)

    asyncio.run(ControlPlane(store).reconcile_once())

    description = describe_resource(store, ResourceKind.MISSION.value, "build-auth", "demo")
    assert description is not None
    assert description["desiredState"]["template"] == "software-feature"
    assert description["observedState"]["phase"] == "Completed"
    assert description["children"]["fleets"][0]["name"] == "build-auth-fleet"
    assert {child["name"] for child in description["children"]["agents"]} == {
        "build-auth-fleet-planner",
        "build-auth-fleet-coder",
        "build-auth-fleet-reviewer",
    }
    assert description["recentEvents"]
    assert description["statusConditions"]
    assert {"kind": "FleetTemplate", "name": "software-feature"} in description["referencedResources"]
    assert len(description["artifactsProduced"]) == 3
    assert any(item["ref"] == "knowledge://prd.md" for item in description["knowledgeUsed"])


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


def test_template_mission_creates_fleet_agents_artifacts_and_uses_inputs(tmp_path: Path) -> None:
    store, _workspace_root = make_store_with_template_mission(tmp_path)

    results = asyncio.run(ControlPlane(store).reconcile_once())

    assert [result.changed for result in results] == [1, 3, 3]
    mission = store.get(ResourceKind.MISSION, "build-auth", "demo")
    fleet = store.get(ResourceKind.FLEET, "build-auth-fleet", "demo")
    assert mission is not None
    assert mission["status"]["phase"] == "Completed"
    assert fleet is not None
    assert fleet["spec"]["strategy"] == "template"
    assert fleet["spec"]["template"] == "software-feature"

    for name, role in [("planner", "planner"), ("coder", "coder"), ("reviewer", "reviewer")]:
        agent = store.get(ResourceKind.AGENT, f"build-auth-fleet-{name}", "demo")
        assert agent is not None
        assert agent["spec"]["role"] == role
        assert agent["spec"]["pilot"]["modelRef"] == "stub-model"
        assert "model" not in agent["spec"]
        assert agent["status"]["phase"] == "Succeeded"

    artifacts = store.list_artifacts("demo", "build-auth")
    assert len(artifacts) == 3
    artifact_text = Path(artifacts[0]["path"]).read_text(encoding="utf-8")
    assert "Template: software-feature" in artifact_text
    assert "Input prd" in artifact_text
    assert "Ship authentication" in artifact_text
    assert "Requested outputs: code, report" in artifact_text


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"omit_capability": True}, "Capability plan not found"),
        ({"omit_tool": True}, "Tool filesystem required by Capability plan not found"),
        ({"omit_model": True}, "No available Model is compatible"),
        ({"incompatible_models": True}, "No available Model is compatible"),
    ],
)
def test_capability_resolution_failures_mark_fleet_and_mission_failed(
    tmp_path: Path,
    kwargs: dict,
    message: str,
) -> None:
    store, _workspace_root = make_store_with_template_mission(tmp_path, **kwargs)

    asyncio.run(ControlPlane(store).reconcile_once())

    mission = store.get(ResourceKind.MISSION, "build-auth", "demo")
    fleet = store.get(ResourceKind.FLEET, "build-auth-fleet", "demo")
    assert mission is not None
    assert mission["status"]["phase"] == "Failed"
    assert message in mission["status"]["message"]
    assert fleet is not None
    assert fleet["status"]["phase"] == "Failed"
    assert message in fleet["status"]["message"]
    assert store.list(ResourceKind.AGENT, "demo") == []

    event_types = {event["type"] for event in store.list_events(limit=50)}
    assert {"FleetFailed", "MissionFailed"}.issubset(event_types)


def test_checked_in_demo_manifest_reconciles_end_to_end(tmp_path: Path) -> None:
    demo_root = tmp_path / "examples" / "demo"
    knowledge_dir = demo_root / "knowledge"
    knowledge_dir.mkdir(parents=True)
    (knowledge_dir / "prd.md").write_text("Demo PRD", encoding="utf-8")
    manifest_path = Path(__file__).parents[1] / "examples" / "demo" / "resources.yaml"
    resources = parse_resource_documents(manifest_path.read_text(encoding="utf-8"))
    store = ResourceStore(f"sqlite:///{tmp_path / 'platform.db'}", tmp_path / "platform")
    for resource in resources:
        store.apply(resource.model_dump(mode="json", exclude_none=True))

    asyncio.run(ControlPlane(store).reconcile_once())

    mission = store.get(ResourceKind.MISSION, "implement-auth", "demo")
    assert mission is not None
    assert mission["status"]["phase"] == "Completed"
    assert len(store.list_artifacts("demo", "implement-auth")) == 3
