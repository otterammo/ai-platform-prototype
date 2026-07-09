from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from ai_platform.controllers import ControlPlane
from ai_platform.observability import build_timeline, build_trace, format_timeline, format_trace
from ai_platform.policy import ApprovalService, PolicyEffect, PolicyEngine, RuntimeAction
from ai_platform.resources import ApprovalResource, ResourceKind, parse_resource_documents
from ai_platform.storage import ResourceStore


def default_policy_rules() -> list[dict[str, Any]]:
    return [
        {"match": {"tool": "git", "operation": "use"}, "requiresApproval": True},
        {"match": {"tool": "knowledge"}, "allow": True},
        {"match": {"tool": "model"}, "allow": True},
        {"match": {"tool": "filesystem"}, "allow": True},
    ]


def populate_governed_store(
    store: ResourceStore,
    workspace_root: Path,
    policy_rules: list[dict[str, Any]] | None,
) -> None:
    knowledge_dir = workspace_root / "knowledge"
    knowledge_dir.mkdir(parents=True)
    (knowledge_dir / "prd.md").write_text("Ship authentication.", encoding="utf-8")
    manifests: list[dict[str, Any]] = [
        {
            "apiVersion": "ai.platform/v1",
            "kind": "Workspace",
            "metadata": {"name": "demo"},
            "spec": {"rootPath": str(workspace_root)},
        },
        {
            "apiVersion": "ai.platform/v1",
            "kind": "Model",
            "metadata": {"name": "stub-model"},
            "spec": {"config": {"provider": "stub", "model": "stub-model"}},
        },
        {"apiVersion": "ai.platform/v1", "kind": "Tool", "metadata": {"name": "git"}, "spec": {}},
        {"apiVersion": "ai.platform/v1", "kind": "Tool", "metadata": {"name": "filesystem"}, "spec": {}},
        {
            "apiVersion": "ai.platform/v1",
            "kind": "Capability",
            "metadata": {"name": "implement"},
            "spec": {"requires": {"tools": ["git", "filesystem"]}, "compatibleModels": ["stub-model"]},
        },
        {
            "apiVersion": "ai.platform/v1",
            "kind": "FleetTemplate",
            "metadata": {"name": "protected-feature"},
            "spec": {"agents": [{"name": "coder", "role": "coder", "capabilities": ["implement"]}]},
        },
        {
            "apiVersion": "ai.platform/v1",
            "kind": "Mission",
            "metadata": {"name": "implement-auth", "namespace": "demo"},
            "spec": {"template": "protected-feature", "inputs": {"prd": {"ref": "knowledge://prd.md"}}},
        },
    ]
    if policy_rules is not None:
        manifests.append(
            {
                "apiVersion": "ai.platform/v1",
                "kind": "Policy",
                "metadata": {"name": "default"},
                "spec": {"rules": policy_rules},
            }
        )
    for manifest in manifests:
        store.apply(manifest)


def make_governed_store(tmp_path: Path, policy_rules: list[dict[str, Any]] | None) -> ResourceStore:
    store = ResourceStore(f"sqlite:///{tmp_path / 'platform.db'}", tmp_path / "platform")
    populate_governed_store(store, tmp_path / "workspace", policy_rules)
    return store


def approval_name(store: ResourceStore) -> str:
    approvals = store.list(ResourceKind.APPROVAL)
    assert len(approvals) == 1
    return approvals[0]["metadata"]["name"]


def condition_status(resource: dict[str, Any], condition_type: str) -> str | None:
    for condition in resource["status"].get("conditions", []):
        if condition["type"] == condition_type:
            return condition["status"]
    return None


def test_policy_and_approval_resources_parse_and_validate() -> None:
    resources = parse_resource_documents(
        """
apiVersion: ai.platform/v1
kind: Policy
metadata:
  name: default
  namespace: ignored
spec:
  rules:
    - match:
        tool: git
        operation: push
      requiresApproval: true
    - match:
        tool: filesystem
      allow: true
---
apiVersion: ai.platform/v1
kind: Approval
metadata:
  name: approval-001
  namespace: ignored
spec:
  workspace: demo
  mission: implement-auth
  agent: coder
  action:
    tool: shell
    command: docker compose down
  actionHash: abc123
  policy: default
  ruleIndex: 0
status:
  phase: Pending
"""
    )

    assert [resource.kind for resource in resources] == ["Policy", "Approval"]
    assert resources[0].metadata.namespace is None
    approval = resources[1]
    assert isinstance(approval, ApprovalResource)
    assert approval.metadata.namespace is None
    assert approval.spec.action["command"] == "docker compose down"


@pytest.mark.parametrize(
    "rule",
    [
        {"match": {"tool": "git"}, "allow": True, "deny": True},
        {"match": {"tool": "git"}},
    ],
)
def test_policy_rules_require_exactly_one_effect(rule: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="exactly one"):
        parse_resource_documents(
            f"""
apiVersion: ai.platform/v1
kind: Policy
metadata:
  name: default
spec:
  rules:
    - {rule}
"""
        )


def test_approval_status_phase_is_validated() -> None:
    with pytest.raises(ValueError, match="Approval status.phase"):
        parse_resource_documents(
            """
apiVersion: ai.platform/v1
kind: Approval
metadata:
  name: approval-001
spec:
  workspace: demo
  mission: implement-auth
  agent: coder
  action:
    tool: git
  actionHash: abc123
status:
  phase: Waiting
"""
        )


def test_policy_engine_matching_allow_deny_approval_and_default_deny(tmp_path: Path) -> None:
    store = ResourceStore(f"sqlite:///{tmp_path / 'platform.db'}", tmp_path / "platform")
    engine = PolicyEngine(store)

    no_policy_decision = engine.evaluate(RuntimeAction(tool="shell", operation="use"))
    assert no_policy_decision.effect == PolicyEffect.ALLOW
    assert no_policy_decision.reason == "NoPolicies"

    store.apply(
        {
            "apiVersion": "ai.platform/v1",
            "kind": "Policy",
            "metadata": {"name": "default"},
            "spec": {
                "rules": [
                    {"match": {"tool": "git", "operation": "push"}, "requiresApproval": True},
                    {"match": {"tool": "git"}, "allow": True},
                    {"match": {"tool": "filesystem", "operation": "delete"}, "deny": True},
                    {"match": {"tool": "filesystem"}, "allow": True},
                ]
            },
        }
    )

    assert engine.evaluate(RuntimeAction(tool="git", operation="push")).effect == PolicyEffect.REQUIRE_APPROVAL
    assert engine.evaluate(RuntimeAction(tool="git", operation="status")).effect == PolicyEffect.ALLOW
    assert engine.evaluate(RuntimeAction(tool="filesystem", operation="delete")).effect == PolicyEffect.DENY
    default_deny = engine.evaluate(RuntimeAction(tool="shell", operation="use"))
    assert default_deny.effect == PolicyEffect.DENY
    assert default_deny.reason == "NoMatchingPolicyRule"


def test_protected_action_creates_approval_pauses_and_does_not_duplicate(tmp_path: Path) -> None:
    store = make_governed_store(tmp_path, default_policy_rules())

    asyncio.run(ControlPlane(store).reconcile_once())

    approval_id = approval_name(store)
    approval = store.get(ResourceKind.APPROVAL, approval_id)
    mission = store.get(ResourceKind.MISSION, "implement-auth", "demo")
    fleet = store.get(ResourceKind.FLEET, "implement-auth-fleet", "demo")
    agent = store.get(ResourceKind.AGENT, "implement-auth-fleet-coder", "demo")
    assert approval is not None
    assert approval["status"]["phase"] == "Pending"
    assert mission is not None
    assert mission["status"]["phase"] == "Waiting"
    assert condition_status(mission, "WaitingForApproval") == "True"
    assert fleet is not None
    assert fleet["status"]["phase"] == "Waiting"
    assert agent is not None
    assert agent["status"]["phase"] == "Waiting"
    assert agent["status"]["data"]["pendingApproval"] == approval_id

    asyncio.run(ControlPlane(store).reconcile_once())

    assert len(store.list(ResourceKind.APPROVAL)) == 1
    event_types = {event["type"] for event in store.list_events(namespace="demo", limit=100)}
    assert {"PolicyEvaluated", "ApprovalRequested", "AgentRunWaiting", "FleetWaiting", "MissionWaiting"}.issubset(
        event_types
    )


def test_approval_workflow_resumes_and_completes(tmp_path: Path) -> None:
    store = make_governed_store(tmp_path, default_policy_rules())
    asyncio.run(ControlPlane(store).reconcile_once())
    approval_id = approval_name(store)

    ApprovalService(store).approve(approval_id, actor="alice", reason="looks safe")
    asyncio.run(ControlPlane(store).reconcile_once())

    approval = store.get(ResourceKind.APPROVAL, approval_id)
    mission = store.get(ResourceKind.MISSION, "implement-auth", "demo")
    agent = store.get(ResourceKind.AGENT, "implement-auth-fleet-coder", "demo")
    assert approval is not None
    assert approval["status"]["phase"] == "Approved"
    assert approval["status"]["data"]["approvedBy"] == "alice"
    assert mission is not None
    assert mission["status"]["phase"] == "Completed"
    assert agent is not None
    assert agent["status"]["phase"] == "Succeeded"
    assert "pendingApproval" not in agent["status"]["data"]

    correlation_id = mission["status"]["data"]["correlationId"]
    events = store.list_events(namespace="demo", correlation_id=correlation_id, limit=None, ascending=True)
    event_types = {event["type"] for event in events}
    assert {"ApprovalGranted", "AgentRunResumed", "PolicyAllowed", "AgentCompleted", "MissionCompleted"}.issubset(
        event_types
    )
    for event in events:
        if event["type"] in {
            "PolicyEvaluated",
            "PolicyAllowed",
            "ApprovalRequested",
            "ApprovalGranted",
            "AgentRunWaiting",
            "AgentRunResumed",
        }:
            assert event["controller"]
            assert event["action"]
            assert event["reason"]
            assert event["correlationId"] == correlation_id
    assert any(event["payload"].get("approvalId") == approval_id for event in events)


def test_rejection_workflow_fails_agent_fleet_and_mission(tmp_path: Path) -> None:
    store = make_governed_store(tmp_path, default_policy_rules())
    asyncio.run(ControlPlane(store).reconcile_once())
    approval_id = approval_name(store)

    ApprovalService(store).reject(approval_id, actor="bob", reason="too risky")
    asyncio.run(ControlPlane(store).reconcile_once())

    approval = store.get(ResourceKind.APPROVAL, approval_id)
    mission = store.get(ResourceKind.MISSION, "implement-auth", "demo")
    fleet = store.get(ResourceKind.FLEET, "implement-auth-fleet", "demo")
    agent = store.get(ResourceKind.AGENT, "implement-auth-fleet-coder", "demo")
    assert approval is not None
    assert approval["status"]["phase"] == "Rejected"
    assert mission is not None
    assert mission["status"]["phase"] == "Failed"
    assert fleet is not None
    assert fleet["status"]["phase"] == "Failed"
    assert agent is not None
    assert agent["status"]["phase"] == "Failed"
    assert "pendingApproval" not in agent["status"]["data"]

    event_types = {event["type"] for event in store.list_events(namespace="demo", limit=100)}
    assert {"ApprovalRejected", "AgentRunFailed", "AgentFailed", "FleetFailed", "MissionFailed"}.issubset(event_types)


def test_policy_denial_fails_without_creating_approval(tmp_path: Path) -> None:
    store = make_governed_store(
        tmp_path,
        [
            {"match": {"tool": "knowledge"}, "allow": True},
            {"match": {"tool": "model"}, "allow": True},
            {"match": {"tool": "filesystem"}, "allow": True},
        ],
    )

    asyncio.run(ControlPlane(store).reconcile_once())

    mission = store.get(ResourceKind.MISSION, "implement-auth", "demo")
    agent = store.get(ResourceKind.AGENT, "implement-auth-fleet-coder", "demo")
    assert mission is not None
    assert mission["status"]["phase"] == "Failed"
    assert agent is not None
    assert agent["status"]["phase"] == "Failed"
    assert store.list(ResourceKind.APPROVAL) == []
    event_types = {event["type"] for event in store.list_events(namespace="demo", limit=100)}
    assert "PolicyDenied" in event_types
    assert "ApprovalRequested" not in event_types


def test_trace_and_timeline_include_approval_events(tmp_path: Path) -> None:
    store = make_governed_store(tmp_path, default_policy_rules())
    asyncio.run(ControlPlane(store).reconcile_once())
    approval_id = approval_name(store)
    ApprovalService(store).approve(approval_id, actor="alice")
    asyncio.run(ControlPlane(store).reconcile_once())

    trace = build_trace(store, "implement-auth", "demo")
    assert trace is not None
    formatted_trace = format_trace(trace)
    assert "Policy: default" in formatted_trace
    assert "Approval required" in formatted_trace
    assert "Approval granted" in formatted_trace
    assert "Agent resumed" in formatted_trace

    timeline = build_timeline(store, "implement-auth", "demo")
    assert timeline is not None
    formatted_timeline = format_timeline(timeline)
    assert "Approval requested" in formatted_timeline
    assert "Approval granted" in formatted_timeline
    assert "resumed" in formatted_timeline
