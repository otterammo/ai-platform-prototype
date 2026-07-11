from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ai_platform.api import create_app
from ai_platform.cli import main
from ai_platform.controllers import ToolInvocationController
from ai_platform.observability import build_trace, format_trace
from ai_platform.policy import ApprovalService
from ai_platform.resources import Observation, ResourceKind, parse_resource_documents
from ai_platform.runtime import ToolRuntimeRegistry
from ai_platform.storage import ResourceStore


class SlowRuntime:
    runtime_id = "test.slow"

    def execute(self, _invocation: Any) -> Observation:
        time.sleep(0.05)
        return Observation(summary="Too late")


def base_manifests(policy_rules: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    manifests: list[dict[str, Any]] = [
        {
            "apiVersion": "ai.platform/v1",
            "kind": "Workspace",
            "metadata": {"name": "demo"},
            "spec": {},
        },
        {
            "apiVersion": "ai.platform/v1",
            "kind": "Mission",
            "metadata": {"name": "implement-auth", "namespace": "demo"},
            "spec": {"objective": "Implement auth"},
        },
        {
            "apiVersion": "ai.platform/v1",
            "kind": "Fleet",
            "metadata": {"name": "implement-auth-fleet", "namespace": "demo"},
            "spec": {"workspace": "demo", "mission": "implement-auth"},
        },
        {
            "apiVersion": "ai.platform/v1",
            "kind": "Agent",
            "metadata": {"name": "implement-auth-agent", "namespace": "demo"},
            "spec": {
                "workspace": "demo",
                "mission": "implement-auth",
                "fleet": "implement-auth-fleet",
            },
        },
        {
            "apiVersion": "ai.platform/v1",
            "kind": "AgentRun",
            "metadata": {"name": "run-1", "namespace": "demo"},
            "spec": {
                "agentRef": {"name": "implement-auth-agent"},
                "missionRef": {"name": "implement-auth"},
                "contextRef": {"name": "run-1-context"},
            },
        },
        {
            "apiVersion": "ai.platform/v1",
            "kind": "Tool",
            "metadata": {"name": "fake"},
            "spec": {
                "description": "Fake test runtime",
                "operations": [
                    {
                        "name": "echo",
                        "timeoutSeconds": 5,
                        "riskLevel": "low",
                        "inputSchema": {
                            "type": "object",
                            "required": ["message"],
                            "properties": {"message": {"type": "string"}},
                        },
                        "outputSchema": {"type": "object"},
                    }
                ],
                "timeoutSeconds": 5,
                "riskLevel": "low",
                "inputSchema": {
                    "type": "object",
                    "required": ["message"],
                    "properties": {"message": {"type": "string"}},
                },
                "outputSchema": {"type": "object"},
            },
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
    manifests.append(tool_invocation_manifest())
    return manifests


def tool_invocation_manifest(name: str = "invoke-0001") -> dict[str, Any]:
    return {
        "apiVersion": "ai.platform/v1",
        "kind": "ToolInvocation",
        "metadata": {"name": name, "namespace": "demo"},
        "spec": {
            "agentRunRef": {"name": "run-1"},
            "tool": "fake",
            "operation": "echo",
            "arguments": {"message": "hello"},
        },
        "status": {"phase": "Pending"},
    }


def make_store(tmp_path: Path, policy_rules: list[dict[str, Any]] | None = None) -> ResourceStore:
    store = ResourceStore(f"sqlite:///{tmp_path / 'platform.db'}", tmp_path / "platform")
    for manifest in base_manifests(policy_rules):
        store.apply(manifest)
    return store


def test_tool_invocation_and_tool_definition_parse() -> None:
    resources = parse_resource_documents(
        """
apiVersion: ai.platform/v1
kind: Tool
metadata:
  name: fake
spec:
  operations:
    - echo
  timeoutSeconds: 5
  riskLevel: low
  inputSchema:
    type: object
    required:
      - message
    properties:
      message:
        type: string
  outputSchema:
    type: object
---
apiVersion: ai.platform/v1
kind: ToolInvocation
metadata:
  name: invoke-0001
  namespace: demo
spec:
  agentRunRef:
    name: run-1
  tool: fake
  operation: echo
  arguments:
    message: hello
status:
  phase: Pending
"""
    )

    tool = resources[0]
    invocation = resources[1]
    assert tool.kind == ResourceKind.TOOL
    assert tool.spec.operations[0].name == "echo"
    assert tool.spec.timeoutSeconds == 5
    assert tool.spec.riskLevel == "low"
    assert invocation.kind == ResourceKind.TOOL_INVOCATION
    assert invocation.spec.agentRunRef.name == "run-1"
    assert invocation.status.phase == "Pending"


def test_fake_tool_runtime_returns_observation() -> None:
    invocation = parse_resource_documents(
        """
apiVersion: ai.platform/v1
kind: ToolInvocation
metadata:
  name: invoke-0001
  namespace: demo
spec:
  agentRunRef:
    name: run-1
  tool: fake
  operation: echo
  arguments:
    message: hello
"""
    )[0]

    observation = ToolRuntimeRegistry().execute(invocation)

    assert observation.summary == "Echo completed"
    assert observation.payload == {"message": "hello"}


def test_fake_tool_invocation_requires_tool_definition(tmp_path: Path) -> None:
    store = ResourceStore(f"sqlite:///{tmp_path / 'platform.db'}", tmp_path / "platform")
    for manifest in base_manifests()[:-1]:
        if manifest["kind"] != "Tool":
            store.apply(manifest)

    with pytest.raises(ValueError, match="Tool fake does not exist"):
        store.apply(tool_invocation_manifest())


def test_tool_invocation_controller_authorizes_executes_records_observation_events_and_trace(tmp_path: Path) -> None:
    store = make_store(tmp_path, [{"match": {"tool": "fake", "operation": "echo"}, "allow": True}])

    result = asyncio.run(ToolInvocationController(store).reconcile_once())

    assert result.changed == 1
    invocation = store.get(ResourceKind.TOOL_INVOCATION, "invoke-0001", "demo")
    assert invocation is not None
    assert invocation["status"]["phase"] == "Succeeded"
    assert invocation["status"]["observation"] == {
        "summary": "Echo completed",
        "payload": {"message": "hello"},
    }
    assert invocation["status"]["data"]["runtime"] == "builtin.fake"

    event_types = [event["type"] for event in store.list_events(namespace="demo", limit=50, ascending=True)]
    assert "ToolInvocationCreated" in event_types
    assert "ToolInvocationAuthorized" in event_types
    assert "ToolInvocationStarted" in event_types
    assert "ToolInvocationCompleted" in event_types
    assert "ObservationRecorded" in event_types
    tool_events = [
        event
        for event in store.list_events(namespace="demo", limit=50)
        if event["type"].startswith("ToolInvocation") or event["type"] == "ObservationRecorded"
    ]
    assert tool_events
    for event in tool_events:
        assert event["correlationId"]
        assert event["workspace"] == "demo"
        assert event["payload"]["agentRun"] == "run-1"
        assert event["payload"]["toolInvocation"] == "invoke-0001"

    trace = build_trace(store, "implement-auth", "demo")
    assert trace is not None
    formatted = format_trace(trace)
    assert "AgentRun run-1" in formatted
    assert "ToolInvocation invoke-0001" in formatted
    assert "Tool fake.echo" in formatted
    assert "Succeeded" in formatted
    assert "Observation" in formatted
    assert "Echo completed" in formatted


def test_running_tool_invocation_is_not_replayed(tmp_path: Path) -> None:
    store = make_store(tmp_path, [{"match": {"tool": "fake", "operation": "echo"}, "allow": True}])
    store.update_status(
        ResourceKind.TOOL_INVOCATION,
        "invoke-0001",
        "demo",
        "Running",
        "Simulated in-flight execution",
        {"runtime": "builtin.fake"},
    )

    asyncio.run(ToolInvocationController(store).reconcile_once())

    invocation = store.get(ResourceKind.TOOL_INVOCATION, "invoke-0001", "demo")
    assert invocation is not None
    assert invocation["status"]["phase"] == "Failed"
    assert invocation["status"]["observation"]["error"]["reason"] == "ExecutionStateUnknown"
    event_types = {event["type"] for event in store.list_events(namespace="demo", limit=50)}
    assert "ToolInvocationFailed" in event_types
    assert "ToolInvocationCompleted" not in event_types


def test_invalid_tool_invocation_arguments_fail_before_policy_and_runtime(tmp_path: Path) -> None:
    store = make_store(tmp_path, [{"match": {"tool": "fake", "operation": "echo"}, "allow": True}])
    invalid = tool_invocation_manifest()
    invalid["metadata"]["name"] = "invoke-invalid"
    invalid["spec"]["arguments"] = {"message": 123}
    store.apply(invalid)

    asyncio.run(ToolInvocationController(store).reconcile_once())

    invocation = store.get(ResourceKind.TOOL_INVOCATION, "invoke-invalid", "demo")
    assert invocation is not None
    assert invocation["status"]["phase"] == "Failed"
    assert "arguments.message must be string" in invocation["status"]["message"]
    assert invocation["status"]["observation"]["error"]["reason"] == "ToolInvocationFailed"
    invalid_events = [
        event
        for event in store.list_events(namespace="demo", resource_kind=ResourceKind.TOOL_INVOCATION, limit=100)
        if event["resourceName"] == "invoke-invalid"
    ]
    event_types = {event["type"] for event in invalid_events}
    assert "ToolInvocationFailed" in event_types
    assert "ToolInvocationAuthorized" not in event_types
    assert "ToolInvocationStarted" not in event_types
    assert "ToolInvocationCompleted" not in event_types
    assert not [
        event
        for event in store.list_events(namespace="demo", limit=100)
        if event["type"] == "PolicyEvaluated"
        and event["payload"]["runtimeAction"]["details"].get("toolInvocation") == "invoke-invalid"
    ]


def test_array_item_schema_is_validated_before_policy_and_runtime(tmp_path: Path) -> None:
    store = ResourceStore(f"sqlite:///{tmp_path / 'platform.db'}", tmp_path / "platform")
    for manifest in base_manifests()[:-1]:
        if manifest["kind"] != "Tool":
            store.apply(manifest)
    store.apply(
        {
            "apiVersion": "ai.platform/v1",
            "kind": "Tool",
            "metadata": {"name": "array-tool"},
            "spec": {
                "operations": [
                    {
                        "name": "echo",
                        "inputSchema": {
                            "type": "object",
                            "required": ["messages"],
                            "properties": {
                                "messages": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                }
                            },
                        },
                    }
                ]
            },
        }
    )
    invocation = tool_invocation_manifest("invoke-array-invalid")
    invocation["spec"]["tool"] = "array-tool"
    invocation["spec"]["arguments"] = {"messages": ["ok", 1]}
    store.apply(invocation)

    asyncio.run(ToolInvocationController(store).reconcile_once())

    failed = store.get(ResourceKind.TOOL_INVOCATION, "invoke-array-invalid", "demo")
    assert failed is not None
    assert failed["status"]["phase"] == "Failed"
    assert "arguments.messages[1] must be string" in failed["status"]["message"]
    assert not [
        event
        for event in store.list_events(namespace="demo", limit=100)
        if event["type"] == "PolicyEvaluated"
        and event["payload"]["runtimeAction"]["details"].get("toolInvocation") == "invoke-array-invalid"
    ]


def test_tool_invocation_spec_is_immutable_on_reapply(tmp_path: Path) -> None:
    store = make_store(tmp_path, [{"match": {"tool": "fake", "operation": "echo"}, "allow": True}])
    asyncio.run(ToolInvocationController(store).reconcile_once())

    changed = tool_invocation_manifest()
    changed["spec"]["arguments"] = {"message": "changed"}
    with pytest.raises(ValueError, match="ToolInvocation demo/invoke-0001 spec is immutable"):
        store.apply(changed)

    invocation = store.get(ResourceKind.TOOL_INVOCATION, "invoke-0001", "demo")
    assert invocation is not None
    assert invocation["spec"]["arguments"] == {"message": "hello"}
    assert invocation["status"]["phase"] == "Succeeded"
    assert invocation["status"]["observation"]["payload"] == {"message": "hello"}


def test_tool_invocation_policy_deny_does_not_execute_runtime(tmp_path: Path) -> None:
    store = make_store(tmp_path, [{"match": {"tool": "fake", "operation": "echo"}, "deny": True}])

    asyncio.run(ToolInvocationController(store).reconcile_once())

    invocation = store.get(ResourceKind.TOOL_INVOCATION, "invoke-0001", "demo")
    assert invocation is not None
    assert invocation["status"]["phase"] == "Denied"
    assert invocation["status"]["observation"]["error"]["reason"] == "PolicyDenied"
    assert "payload" not in invocation["status"]["observation"]
    event_types = {event["type"] for event in store.list_events(namespace="demo", limit=50)}
    assert "ToolInvocationDenied" in event_types
    assert "ToolInvocationStarted" not in event_types
    assert "ToolInvocationCompleted" not in event_types


def test_tool_invocation_requires_approval_and_resumes_after_approval(tmp_path: Path) -> None:
    store = make_store(tmp_path, [{"match": {"tool": "fake", "operation": "echo"}, "requiresApproval": True}])
    controller = ToolInvocationController(store)
    store.update_status(
        ResourceKind.AGENT_RUN,
        "run-1",
        "demo",
        "Succeeded",
        "Existing AgentRun already completed",
    )

    asyncio.run(controller.reconcile_once())
    asyncio.run(controller.reconcile_once())

    invocation = store.get(ResourceKind.TOOL_INVOCATION, "invoke-0001", "demo")
    run = store.get(ResourceKind.AGENT_RUN, "run-1", "demo")
    approvals = store.list(ResourceKind.APPROVAL)
    assert invocation is not None
    assert invocation["status"]["phase"] == "WaitingForApproval"
    assert "observation" not in invocation["status"]
    assert run is not None
    assert run["status"]["phase"] == "Succeeded"
    assert len(approvals) == 1
    assert "agentRun" not in approvals[0]["spec"]
    approval_name = approvals[0]["metadata"]["name"]
    assert invocation["status"]["data"]["approvalId"] == approval_name
    event_types = {event["type"] for event in store.list_events(namespace="demo", limit=50)}
    assert "ToolInvocationWaitingForApproval" in event_types
    assert "AgentRunWaiting" not in event_types
    assert "ToolInvocationStarted" not in event_types

    ApprovalService(store).approve(approval_name, actor="test")
    approved_run = store.get(ResourceKind.AGENT_RUN, "run-1", "demo")
    assert approved_run is not None
    assert approved_run["status"]["phase"] == "Succeeded"
    event_types = {event["type"] for event in store.list_events(namespace="demo", limit=50)}
    assert "AgentRunResumed" not in event_types
    asyncio.run(controller.reconcile_once())

    invocation = store.get(ResourceKind.TOOL_INVOCATION, "invoke-0001", "demo")
    assert invocation is not None
    assert invocation["status"]["phase"] == "Succeeded"
    assert invocation["status"]["observation"]["payload"] == {"message": "hello"}


def test_tool_invocation_runtime_timeout_marks_timed_out(tmp_path: Path) -> None:
    store = make_store(tmp_path, [{"match": {"tool": "fake", "operation": "echo"}, "allow": True}])
    timed = tool_invocation_manifest("invoke-timeout")
    timed["spec"]["timeoutSeconds"] = 0.01
    store.apply(timed)

    registry = ToolRuntimeRegistry({"fake": SlowRuntime()})
    asyncio.run(ToolInvocationController(store, runtime_registry=registry).reconcile_once())

    invocation = store.get(ResourceKind.TOOL_INVOCATION, "invoke-timeout", "demo")
    assert invocation is not None
    assert invocation["status"]["phase"] == "TimedOut"
    assert invocation["status"]["observation"]["error"]["reason"] == "ToolInvocationTimedOut"
    event_types = {
        event["type"]
        for event in store.list_events(namespace="demo", resource_kind=ResourceKind.TOOL_INVOCATION, limit=100)
        if event["resourceName"] == "invoke-timeout"
    }
    assert "ToolInvocationTimedOut" in event_types
    assert "ToolInvocationCompleted" not in event_types


def test_tool_invocation_cli_lists_describes_and_projects_observations(tmp_path: Path, capsys) -> None:
    manifest = tmp_path / "tool-invocation.yaml"
    manifest.write_text("\n---\n".join(json.dumps(item) for item in base_manifests()), encoding="utf-8")
    db = f"sqlite:///{tmp_path / 'platform.db'}"
    root = str(tmp_path / "platform")

    assert main(["--db", db, "--root", root, "apply", str(manifest)]) == 0
    asyncio.run(ToolInvocationController(ResourceStore(db, root)).reconcile_once())
    assert main(["--db", db, "--root", root, "get", "toolinvocations", "-n", "demo"]) == 0
    assert main(["--db", db, "--root", root, "describe", "toolinvocation", "invoke-0001"]) == 0
    assert main(["--db", db, "--root", root, "get", "observations", "-n", "demo"]) == 0

    output = capsys.readouterr().out
    assert "kind: ToolInvocation" in output
    assert "Echo completed" in output
    assert "Observations are embedded in ToolInvocation status for v1.1" in output


def test_tool_invocation_api_lists_and_gets_invocations(tmp_path: Path) -> None:
    app = create_app(f"sqlite:///{tmp_path / 'platform.db'}", str(tmp_path / "platform"))
    for manifest in base_manifests():
        app.state.store.apply(manifest)
    asyncio.run(ToolInvocationController(app.state.store).reconcile_once())

    with TestClient(app) as client:
        list_response = client.get("/toolinvocations", params={"namespace": "demo"})
        assert list_response.status_code == 200
        assert list_response.json()["items"][0]["kind"] == "ToolInvocation"

        get_response = client.get("/toolinvocations/invoke-0001", params={"namespace": "demo"})
        assert get_response.status_code == 200
        body = get_response.json()
        assert body["status"]["phase"] == "Succeeded"
        assert body["status"]["observation"]["summary"] == "Echo completed"
