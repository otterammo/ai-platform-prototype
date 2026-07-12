from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from ai_platform.cli import main
from ai_platform.controllers import LocalAgentRunWorker
from ai_platform.models import Message, ModelClient
from ai_platform.observability import build_trace
from ai_platform.policy import ApprovalService
from ai_platform.resources import Observation, ResourceKind, parse_resource
from ai_platform.runtime import AgentRuntime, ToolRuntimeError, ToolRuntimeRegistry, utciso
from ai_platform.storage import ResourceStore


class SequenceModel(ModelClient):
    def __init__(self, items: list[Any]) -> None:
        self.items = list(items)
        self.calls = 0

    async def generate(self, _messages: list[Message]) -> str:
        self.calls += 1
        if not self.items:
            raise AssertionError("model called after sequence was exhausted")
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, dict):
            return json.dumps(item)
        return str(item)


class FailingRuntime:
    runtime_id = "test.failing"

    def execute(self, _invocation: Any) -> Observation:
        raise AssertionError("tool runtime should not execute")


class AlwaysFailingRuntime:
    runtime_id = "test.always-failing"

    def execute(self, _invocation: Any) -> Observation:
        raise ToolRuntimeError("permanent runtime failure")


class FlakyRuntime:
    runtime_id = "test.flaky"

    def __init__(self) -> None:
        self.calls = 0

    def execute(self, invocation: Any) -> Observation:
        self.calls += 1
        if self.calls == 1:
            raise ToolRuntimeError("temporary runtime failure")
        return Observation(summary="Echo completed", payload={"message": invocation.spec.arguments.get("message")})


def complete_decision(summary: str = "done") -> dict[str, Any]:
    return {"version": "v1", "type": "complete", "summary": summary, "outputs": []}


def invoke_fake_decision(message: str = "hello") -> dict[str, Any]:
    return {
        "version": "v1",
        "type": "invoke_tool",
        "tool": "fake",
        "operation": "echo",
        "arguments": {"message": message},
    }


def make_engine_store(
    tmp_path: Path,
    *,
    execution: dict[str, Any] | None = None,
    policy_rules: list[dict[str, Any]] | None = None,
) -> ResourceStore:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    store = ResourceStore(f"sqlite:///{tmp_path / 'platform.db'}", tmp_path / "platform")
    for manifest in [
        {
            "apiVersion": "ai.platform/v1",
            "kind": "Workspace",
            "metadata": {"name": "demo"},
            "spec": {"rootPath": str(workspace_root)},
        },
        {
            "apiVersion": "ai.platform/v1",
            "kind": "Mission",
            "metadata": {"name": "run-loop", "namespace": "demo"},
            "spec": {"objective": "Exercise the execution engine"},
        },
        {
            "apiVersion": "ai.platform/v1",
            "kind": "Fleet",
            "metadata": {"name": "run-loop-fleet", "namespace": "demo"},
            "spec": {"workspace": "demo", "mission": "run-loop"},
        },
        {
            "apiVersion": "ai.platform/v1",
            "kind": "Agent",
            "metadata": {"name": "runner", "namespace": "demo"},
            "spec": {
                "workspace": "demo",
                "mission": "run-loop",
                "fleet": "run-loop-fleet",
                "tools": ["fake"],
            },
        },
        {
            "apiVersion": "ai.platform/v1",
            "kind": "AgentRun",
            "metadata": {"name": "run-1", "namespace": "demo"},
            "spec": {
                "agentRef": {"name": "runner"},
                "missionRef": {"name": "run-loop"},
                "contextRef": {"name": "run-1-context"},
                **({"execution": execution} if execution else {}),
            },
        },
        {
            "apiVersion": "ai.platform/v1",
            "kind": "Context",
            "metadata": {
                "name": "run-1-context",
                "namespace": "demo",
                "ownerReferences": [{"kind": "AgentRun", "name": "run-1", "controller": True}],
            },
            "spec": {"mission": "run-loop", "agentRun": "run-1", "query": "Exercise", "knowledgeIndex": "default"},
        },
        {
            "apiVersion": "ai.platform/v1",
            "kind": "Tool",
            "metadata": {"name": "fake"},
            "spec": {
                "operations": [
                    {
                        "name": "echo",
                        "inputSchema": {
                            "type": "object",
                            "required": ["message"],
                            "properties": {"message": {"type": "string"}},
                        },
                    }
                ]
            },
        },
    ]:
        store.apply(manifest)
    if policy_rules is not None:
        store.apply(
            {
                "apiVersion": "ai.platform/v1",
                "kind": "Policy",
                "metadata": {"name": "default"},
                "spec": {"rules": policy_rules},
            }
        )
    store.update_status(
        ResourceKind.CONTEXT,
        "run-1-context",
        "demo",
        "Ready",
        "Context ready",
        {"renderedContext": "Context:\nTest context", "chunkCount": 1, "sources": []},
        event_type="ContextBuilt",
    )
    store.update_status(
        ResourceKind.AGENT_RUN,
        "run-1",
        "demo",
        "Scheduled",
        "AgentRun scheduled for test",
        {"worker": "test"},
        event_type="AgentRunScheduled",
    )
    return store


def run_resource(store: ResourceStore) -> Any:
    manifest = store.get(ResourceKind.AGENT_RUN, "run-1", "demo")
    assert manifest is not None
    return parse_resource(manifest)


def runtime_with(
    store: ResourceStore, model: SequenceModel, registry: ToolRuntimeRegistry | None = None
) -> AgentRuntime:
    return AgentRuntime(store, model_client_factory=lambda _config, _store: model, tool_runtime_registry=registry)


def test_execution_engine_successful_decision_tool_observation_complete_trace(tmp_path: Path) -> None:
    store = make_engine_store(tmp_path)
    model = SequenceModel([invoke_fake_decision(), complete_decision("completed after echo")])

    asyncio.run(runtime_with(store, model).run(run_resource(store)))

    run = store.get(ResourceKind.AGENT_RUN, "run-1", "demo")
    assert run is not None
    assert run["status"]["phase"] == "Succeeded"
    frames = run["status"]["data"]["executionFrames"]
    assert [frame["decision"]["type"] for frame in frames] == ["invoke_tool", "complete"]
    assert frames[0]["toolInvocation"] == "run-1-tool-1-0001-1"
    assert frames[0]["observation"]["summary"] == "Echo completed"
    invocation = store.get(ResourceKind.TOOL_INVOCATION, "run-1-tool-1-0001-1", "demo")
    assert invocation is not None
    assert invocation["status"]["phase"] == "Succeeded"
    assert invocation["spec"]["idempotencyKey"]
    event_types = [event["type"] for event in store.list_events(namespace="demo", limit=None, ascending=True)]
    assert "ExecutionEngineStarted" in event_types
    assert "DecisionRequested" in event_types
    assert "DecisionValidated" in event_types
    assert "ObservationDelivered" in event_types
    assert "ExecutionCompleted" in event_types
    trace = build_trace(store, "run-loop", "demo")
    assert trace is not None
    trace_run = trace["fleets"][0]["agents"][0]["agentRuns"][0]
    assert trace_run["executionFrames"][0]["toolInvocation"] == "run-1-tool-1-0001-1"


def test_completed_agentrun_does_not_resume_or_duplicate_tool_invocation(tmp_path: Path) -> None:
    store = make_engine_store(tmp_path)
    model = SequenceModel([invoke_fake_decision(), complete_decision()])
    runtime = runtime_with(store, model)
    asyncio.run(runtime.run(run_resource(store)))

    asyncio.run(runtime.run(run_resource(store)))

    assert model.calls == 2
    assert len(store.list(ResourceKind.TOOL_INVOCATION, "demo")) == 1


def test_resume_after_tool_success_delivers_observation_without_reexecuting_tool(tmp_path: Path) -> None:
    store = make_engine_store(tmp_path)
    invocation_name = "run-1-tool-1-0001-1"
    decision = invoke_fake_decision()
    store.apply(
        {
            "apiVersion": "ai.platform/v1",
            "kind": "ToolInvocation",
            "metadata": {
                "name": invocation_name,
                "namespace": "demo",
                "ownerReferences": [{"kind": "AgentRun", "name": "run-1", "controller": True}],
            },
            "spec": {
                "agentRunRef": {"name": "run-1"},
                "tool": "fake",
                "operation": "echo",
                "arguments": {"message": "hello"},
            },
        }
    )
    store.update_status(
        ResourceKind.TOOL_INVOCATION,
        invocation_name,
        "demo",
        "Succeeded",
        "ToolInvocation completed",
        {},
        observation=Observation(summary="Echo completed", payload={"message": "hello"}),
    )
    store.update_status(
        ResourceKind.AGENT_RUN,
        "run-1",
        "demo",
        "WaitingForObservation",
        "Simulated crash after tool success",
        {
            "executionStartedAt": utciso(),
            "executionState": "WaitingForObservation",
            "activeFrameIndex": 0,
            "budgetUsage": {
                "iterations": 1,
                "modelInvocations": 1,
                "toolInvocations": 1,
                "decisionFailures": 0,
                "toolFailures": 0,
                "failures": 0,
                "wallTimeSeconds": 0,
                "inputTokens": "Unknown",
                "outputTokens": "Unknown",
            },
            "executionFrames": [
                {
                    "iteration": 1,
                    "state": "tool-observed",
                    "rawDecision": json.dumps(decision),
                    "decision": decision,
                    "toolInvocation": invocation_name,
                    "toolInvocationPhase": "Succeeded",
                }
            ],
            "activeToolInvocation": invocation_name,
        },
    )
    model = SequenceModel([complete_decision("resumed")])
    registry = ToolRuntimeRegistry({"fake": FailingRuntime()})

    asyncio.run(runtime_with(store, model, registry).run(run_resource(store)))

    run = store.get(ResourceKind.AGENT_RUN, "run-1", "demo")
    assert run is not None
    assert run["status"]["phase"] == "Succeeded"
    assert model.calls == 1
    assert len(store.list(ResourceKind.TOOL_INVOCATION, "demo")) == 1
    assert run["status"]["data"]["executionFrames"][0]["observation"]["summary"] == "Echo completed"


def test_invalid_decision_rejected_without_side_effects(tmp_path: Path) -> None:
    store = make_engine_store(tmp_path, execution={"maxDecisionFailures": 0})
    model = SequenceModel(["not json"])

    asyncio.run(runtime_with(store, model).run(run_resource(store)))

    run = store.get(ResourceKind.AGENT_RUN, "run-1", "demo")
    assert run is not None
    assert run["status"]["phase"] == "Failed"
    assert run["status"]["data"]["terminalReason"] == "DecisionParseFailed"
    assert store.list(ResourceKind.TOOL_INVOCATION, "demo") == []
    event_types = {event["type"] for event in store.list_events(namespace="demo", limit=None)}
    assert "DecisionRejected" in event_types
    assert "ExecutionFailed" in event_types


def test_model_transport_retry_uses_same_frame_with_new_attempt(tmp_path: Path) -> None:
    store = make_engine_store(tmp_path, execution={"maxModelRetries": 1})
    model = SequenceModel([RuntimeError("transport unavailable"), complete_decision("retry complete")])
    runtime = runtime_with(store, model)

    asyncio.run(runtime.run(run_resource(store)))
    asyncio.run(runtime.run(run_resource(store)))

    run = store.get(ResourceKind.AGENT_RUN, "run-1", "demo")
    assert run is not None
    assert run["status"]["phase"] == "Succeeded"
    assert model.calls == 2
    frames = run["status"]["data"]["executionFrames"]
    assert len(frames) == 1
    assert frames[0]["modelAttempts"] == 2
    event_types = {event["type"] for event in store.list_events(namespace="demo", limit=None)}
    assert "ExecutionRetryScheduled" in event_types


def test_model_invocation_budget_counts_after_model_approval(tmp_path: Path) -> None:
    policy_rules = [
        {"match": {"tool": "fake", "operation": "use"}, "allow": True},
        {"match": {"tool": "model", "operation": "invoke"}, "requiresApproval": True},
        {"match": {"tool": "filesystem", "operation": "write"}, "allow": True},
    ]
    store = make_engine_store(tmp_path, execution={"maxModelInvocations": 1}, policy_rules=policy_rules)
    model = SequenceModel([complete_decision("approved complete")])
    runtime = runtime_with(store, model)

    asyncio.run(runtime.run(run_resource(store)))

    waiting = store.get(ResourceKind.AGENT_RUN, "run-1", "demo")
    assert waiting is not None
    assert waiting["status"]["phase"] == "WaitingForApproval"
    assert waiting["status"]["data"]["budgetUsage"]["modelInvocations"] == 0
    assert model.calls == 0
    approvals = store.list(ResourceKind.APPROVAL)
    assert len(approvals) == 1

    ApprovalService(store).approve(approvals[0]["metadata"]["name"], actor="test")
    asyncio.run(runtime.run(run_resource(store)))

    run = store.get(ResourceKind.AGENT_RUN, "run-1", "demo")
    assert run is not None
    assert run["status"]["phase"] == "Succeeded"
    assert run["status"]["data"]["budgetUsage"]["modelInvocations"] == 1
    assert model.calls == 1


def test_tool_runtime_infrastructure_retry_does_not_duplicate_invocation(tmp_path: Path) -> None:
    store = make_engine_store(tmp_path, execution={"maxToolRetries": 1})
    model = SequenceModel([invoke_fake_decision(), complete_decision("tool retry complete")])
    flaky = FlakyRuntime()

    asyncio.run(runtime_with(store, model, ToolRuntimeRegistry({"fake": flaky})).run(run_resource(store)))

    assert flaky.calls == 2
    assert len(store.list(ResourceKind.TOOL_INVOCATION, "demo")) == 1
    run = store.get(ResourceKind.AGENT_RUN, "run-1", "demo")
    assert run is not None
    assert run["status"]["phase"] == "Succeeded"
    assert run["status"]["data"]["executionFrames"][0]["toolRetryCount"] == 1


def test_tool_failure_budget_remains_budget_exceeded(tmp_path: Path) -> None:
    store = make_engine_store(tmp_path, execution={"maxToolFailures": 0, "maxToolRetries": 2})
    model = SequenceModel([invoke_fake_decision()])
    runtime = runtime_with(store, model, ToolRuntimeRegistry({"fake": AlwaysFailingRuntime()}))

    asyncio.run(runtime.run(run_resource(store)))

    run = store.get(ResourceKind.AGENT_RUN, "run-1", "demo")
    assert run is not None
    assert run["status"]["phase"] == "BudgetExceeded"
    assert run["status"]["data"]["exceededBudget"] == "maxToolFailures"
    assert run["status"]["data"]["budgetUsage"]["toolFailures"] == 1
    invocation = store.get(ResourceKind.TOOL_INVOCATION, "run-1-tool-1-0001-1", "demo")
    assert invocation is not None
    assert invocation["status"]["phase"] == "Failed"
    assert invocation["status"]["observation"]["error"]["reason"] == "ToolFailureBudgetExceeded"


def test_iteration_budget_exhaustion_becomes_budget_exceeded(tmp_path: Path) -> None:
    store = make_engine_store(tmp_path, execution={"maxIterations": 1})
    model = SequenceModel([invoke_fake_decision(), complete_decision("should not run")])

    asyncio.run(runtime_with(store, model).run(run_resource(store)))

    run = store.get(ResourceKind.AGENT_RUN, "run-1", "demo")
    assert run is not None
    assert run["status"]["phase"] == "BudgetExceeded"
    assert run["status"]["data"]["exceededBudget"] == "maxIterations"


def test_agentrun_wall_timeout_becomes_timed_out(tmp_path: Path) -> None:
    store = make_engine_store(tmp_path, execution={"maxWallTimeSeconds": 0})
    model = SequenceModel([complete_decision("should not run")])

    asyncio.run(runtime_with(store, model).run(run_resource(store)))

    run = store.get(ResourceKind.AGENT_RUN, "run-1", "demo")
    assert run is not None
    assert run["status"]["phase"] == "TimedOut"
    assert model.calls == 0


def test_cancel_command_cancels_waiting_agentrun(tmp_path: Path, capsys: Any) -> None:
    policy_rules = [
        {"match": {"tool": "fake", "operation": "use"}, "allow": True},
        {"match": {"tool": "fake", "operation": "echo"}, "requiresApproval": True},
        {"match": {"tool": "model"}, "allow": True},
        {"match": {"tool": "filesystem"}, "allow": True},
    ]
    store = make_engine_store(tmp_path, policy_rules=policy_rules)
    db = store.database_url
    root = str(store.platform_root)
    model = SequenceModel([invoke_fake_decision()])
    runtime = runtime_with(store, model)
    asyncio.run(runtime.run(run_resource(store)))
    waiting = store.get(ResourceKind.AGENT_RUN, "run-1", "demo")
    assert waiting is not None
    assert waiting["status"]["phase"] == "WaitingForTool"
    assert waiting["status"]["data"]["pendingApproval"]

    assert main(["--db", db, "--root", root, "cancel", "agentrun", "run-1", "-n", "demo"]) == 0
    capsys.readouterr()
    worker = LocalAgentRunWorker(ResourceStore(db, root), runtime=AgentRuntime(ResourceStore(db, root)))
    asyncio.run(worker.reconcile_once())

    refreshed = ResourceStore(db, root).get(ResourceKind.AGENT_RUN, "run-1", "demo")
    assert refreshed is not None
    assert refreshed["status"]["phase"] == "Cancelled"
    event_types = {event["type"] for event in ResourceStore(db, root).list_events(namespace="demo", limit=None)}
    assert {"CancellationRequested", "CancellationAcknowledged", "ExecutionCancelled"}.issubset(event_types)
