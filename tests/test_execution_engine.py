from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

from ai_platform.cli import main
from ai_platform.controllers import LocalAgentRunWorker, ToolInvocationController
from ai_platform.models import Message, ModelClient, normalize_decision_content
from ai_platform.observability import build_trace
from ai_platform.policy import ApprovalService
from ai_platform.resources import (
    AgentResource,
    ContextResource,
    MissionResource,
    Observation,
    ResourceKind,
    parse_resource,
)
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


class BlockingModel(ModelClient):
    def __init__(self, item: Any) -> None:
        self.item = item
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def generate(self, _messages: list[Message]) -> str:
        self.calls += 1
        self.started.set()
        await self.release.wait()
        if isinstance(self.item, dict):
            return json.dumps(self.item)
        return str(self.item)


class FailingRuntime:
    runtime_id = "test.failing"

    def __init__(self) -> None:
        self.calls = 0

    def execute(self, _invocation: Any) -> Observation:
        self.calls += 1
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


def run_git_command(workspace_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=workspace_root,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.invalid",
        },
        text=True,
        capture_output=True,
        check=True,
    )


def runtime_with(
    store: ResourceStore, model: SequenceModel, registry: ToolRuntimeRegistry | None = None
) -> AgentRuntime:
    return AgentRuntime(store, model_client_factory=lambda _config, _store: model, tool_runtime_registry=registry)


def invoke_tool_decision(tool: str, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": "v1",
        "type": "invoke_tool",
        "tool": tool,
        "operation": operation,
        "arguments": arguments,
    }


def make_autonomous_store(
    tmp_path: Path,
    *,
    workspace_root: Path,
    tools: list[dict[str, Any]],
    agent_tools: list[str],
    policy_rules: list[dict[str, Any]] | None = None,
) -> ResourceStore:
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
            "metadata": {"name": "autonomous-workload", "namespace": "demo"},
            "spec": {"objective": "Run an autonomous workload"},
        },
        {
            "apiVersion": "ai.platform/v1",
            "kind": "Fleet",
            "metadata": {"name": "autonomous-workload-fleet", "namespace": "demo"},
            "spec": {"workspace": "demo", "mission": "autonomous-workload"},
        },
        {
            "apiVersion": "ai.platform/v1",
            "kind": "Agent",
            "metadata": {"name": "runner", "namespace": "demo"},
            "spec": {
                "workspace": "demo",
                "mission": "autonomous-workload",
                "fleet": "autonomous-workload-fleet",
                "tools": agent_tools,
            },
        },
        {
            "apiVersion": "ai.platform/v1",
            "kind": "AgentRun",
            "metadata": {"name": "run-1", "namespace": "demo"},
            "spec": {
                "agentRef": {"name": "runner"},
                "missionRef": {"name": "autonomous-workload"},
                "contextRef": {"name": "run-1-context"},
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
            "spec": {
                "mission": "autonomous-workload",
                "agentRun": "run-1",
                "query": "Run autonomously",
                "knowledgeIndex": "default",
            },
        },
        *tools,
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
        {"renderedContext": "Context:\nRun autonomously", "chunkCount": 0, "sources": []},
        event_type="ContextBuilt",
    )
    store.update_status(
        ResourceKind.AGENT_RUN,
        "run-1",
        "demo",
        "Scheduled",
        "AgentRun scheduled for autonomous workload",
        {"worker": "test"},
        event_type="AgentRunScheduled",
    )
    return store


def builtin_tool_manifest(name: str, operations: list[str], config: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "apiVersion": "ai.platform/v1",
        "kind": "Tool",
        "metadata": {"name": name},
        "spec": {
            "operations": operations,
            **({"config": config} if config is not None else {}),
        },
    }


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


def test_openai_compatible_adapter_normalizes_json_code_fence() -> None:
    fenced = '```json\n{"version":"v1","type":"complete","summary":"done","outputs":[]}\n```'
    assert json.loads(normalize_decision_content(fenced)) == {
        "version": "v1",
        "type": "complete",
        "summary": "done",
        "outputs": [],
    }
    wrapped = '{"decision":{"tool":"git","operation":"status","arguments":[]}}'
    assert json.loads(normalize_decision_content(wrapped)) == {
        "version": "v1",
        "type": "invoke_tool",
        "tool": "git",
        "operation": "status",
        "arguments": {},
    }
    failed = '{"decision":{"type":"fail","message":"cannot continue"}}'
    assert json.loads(normalize_decision_content(failed)) == {
        "version": "v1",
        "type": "fail",
        "message": "cannot continue",
        "reason": "cannot continue",
        "retryable": False,
    }
    noisy_wrapped = (
        '{"decision":{"tool":"git","operation":"commit","arguments":{"message":"docs: add live dogfood summary"}}}}'
    )
    assert json.loads(normalize_decision_content(noisy_wrapped)) == {
        "version": "v1",
        "type": "invoke_tool",
        "tool": "git",
        "operation": "commit",
        "arguments": {"message": "docs: add live dogfood summary"},
    }
    assert normalize_decision_content('{"version":"v1"}') == '{"version":"v1"}'


def test_runtime_prompt_includes_agent_tools_and_current_budgets(tmp_path: Path) -> None:
    store = make_engine_store(tmp_path)
    store.apply(
        {
            "apiVersion": "ai.platform/v1",
            "kind": "Tool",
            "metadata": {"name": "fake"},
            "spec": {
                "config": {"apiKey": "secret-token"},
                "operations": [
                    {
                        "name": "echo",
                        "inputSchema": {
                            "type": "object",
                            "required": ["message"],
                            "properties": {"message": {"type": "string"}},
                        },
                    }
                ],
            },
        }
    )
    runtime = runtime_with(store, SequenceModel([]))
    run = run_resource(store)
    agent_manifest = store.get(ResourceKind.AGENT, "runner", "demo")
    mission_manifest = store.get(ResourceKind.MISSION, "run-loop", "demo")
    context_manifest = store.get(ResourceKind.CONTEXT, "run-1-context", "demo")
    assert agent_manifest is not None
    assert mission_manifest is not None
    assert context_manifest is not None
    agent = parse_resource(agent_manifest)
    mission = parse_resource(mission_manifest)
    context = parse_resource(context_manifest)
    assert isinstance(agent, AgentResource)
    assert isinstance(mission, MissionResource)
    assert isinstance(context, ContextResource)
    data = {
        "budgetUsage": {
            "iterations": 1,
            "modelInvocations": 1,
            "toolInvocations": 0,
            "decisionFailures": 0,
            "toolFailures": 0,
            "failures": 0,
            "wallTimeSeconds": 0,
            "inputTokens": "Unknown",
            "outputTokens": "Unknown",
        },
        "executionFrames": [],
    }

    messages = runtime._build_messages(mission, agent, context, data, {"iteration": 1}, run.spec.execution)

    user_content = messages[1]["content"]
    assert '"tools": [{"description": null, "name": "fake"' in user_content
    assert '"operations": [{"inputSchema": {"properties": {"message": {"type": "string"}}' in user_content
    assert "secret-token" not in user_content
    assert "apiKey" not in user_content
    assert "Current budgets:" in user_content
    assert '"maxIterations": 50' in user_content


def test_execution_engine_dogfoods_filesystem_and_git_runtime(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    run_git_command(workspace_root, "init")
    (workspace_root / "README.md").write_text("# Demo\n\nThis project validates runtime providers.\n", encoding="utf-8")
    run_git_command(workspace_root, "add", "README.md")
    run_git_command(workspace_root, "commit", "-m", "docs: seed readme")
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
            "metadata": {"name": "runtime-dogfood", "namespace": "demo"},
            "spec": {"objective": "Read README, write SUMMARY.md, and commit it"},
        },
        {
            "apiVersion": "ai.platform/v1",
            "kind": "Fleet",
            "metadata": {"name": "runtime-dogfood-fleet", "namespace": "demo"},
            "spec": {"workspace": "demo", "mission": "runtime-dogfood"},
        },
        {
            "apiVersion": "ai.platform/v1",
            "kind": "Agent",
            "metadata": {"name": "runner", "namespace": "demo"},
            "spec": {
                "workspace": "demo",
                "mission": "runtime-dogfood",
                "fleet": "runtime-dogfood-fleet",
                "tools": ["filesystem", "git"],
            },
        },
        {
            "apiVersion": "ai.platform/v1",
            "kind": "AgentRun",
            "metadata": {"name": "run-1", "namespace": "demo"},
            "spec": {
                "agentRef": {"name": "runner"},
                "missionRef": {"name": "runtime-dogfood"},
                "contextRef": {"name": "run-1-context"},
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
            "spec": {
                "mission": "runtime-dogfood",
                "agentRun": "run-1",
                "query": "Dogfood runtime providers",
                "knowledgeIndex": "default",
            },
        },
        {
            "apiVersion": "ai.platform/v1",
            "kind": "Tool",
            "metadata": {"name": "filesystem"},
            "spec": {"operations": ["read", "write"], "timeoutSeconds": 5},
        },
        {
            "apiVersion": "ai.platform/v1",
            "kind": "Tool",
            "metadata": {"name": "git"},
            "spec": {"operations": ["add", "commit"], "timeoutSeconds": 5},
        },
        {
            "apiVersion": "ai.platform/v1",
            "kind": "Policy",
            "metadata": {"name": "default"},
            "spec": {
                "rules": [
                    {"match": {"tool": "model"}, "allow": True},
                    {"match": {"tool": "filesystem"}, "allow": True},
                    {"match": {"tool": "git"}, "allow": True},
                ]
            },
        },
    ]:
        store.apply(manifest)
    store.update_status(
        ResourceKind.CONTEXT,
        "run-1-context",
        "demo",
        "Ready",
        "Context ready",
        {"renderedContext": "Context:\nDogfood the real runtime providers", "chunkCount": 0, "sources": []},
        event_type="ContextBuilt",
    )
    store.update_status(
        ResourceKind.AGENT_RUN,
        "run-1",
        "demo",
        "Scheduled",
        "AgentRun scheduled for dogfood",
        {"worker": "test"},
        event_type="AgentRunScheduled",
    )
    model = SequenceModel(
        [
            invoke_tool_decision("filesystem", "read", {"path": "README.md"}),
            invoke_tool_decision(
                "filesystem",
                "write",
                {"path": "SUMMARY.md", "content": "# Summary\n\nRuntime providers can read and write files.\n"},
            ),
            invoke_tool_decision("git", "add", {"paths": ["SUMMARY.md"]}),
            invoke_tool_decision("git", "commit", {"message": "docs: add runtime summary"}),
            complete_decision("Dogfood workload completed"),
        ]
    )

    asyncio.run(runtime_with(store, model).run(run_resource(store)))

    run = store.get(ResourceKind.AGENT_RUN, "run-1", "demo")
    assert run is not None
    assert run["status"]["phase"] == "Succeeded"
    assert (workspace_root / "SUMMARY.md").read_text(encoding="utf-8").startswith("# Summary")
    assert run_git_command(workspace_root, "log", "-1", "--pretty=%s").stdout.strip() == "docs: add runtime summary"
    frames = run["status"]["data"]["executionFrames"]
    assert [frame["decision"]["tool"] for frame in frames[:-1]] == ["filesystem", "filesystem", "git", "git"]
    assert [frame["toolInvocationPhase"] for frame in frames[:-1]] == ["Succeeded"] * 4
    trace = build_trace(store, "runtime-dogfood", "demo")
    assert trace is not None
    trace_run = trace["fleets"][0]["agents"][0]["agentRuns"][0]
    assert len(trace_run["toolInvocations"]) == 4
    event_types = {event["type"] for event in store.list_events(namespace="demo", limit=None)}
    assert {"ToolInvocationAuthorized", "ToolInvocationCompleted", "ObservationRecorded"}.issubset(event_types)


def test_autonomous_filesystem_failure_recovery_feeds_observation_to_next_decision(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    store = make_autonomous_store(
        tmp_path,
        workspace_root=workspace_root,
        tools=[builtin_tool_manifest("filesystem", ["read", "write"])],
        agent_tools=["filesystem"],
    )
    model = SequenceModel(
        [
            invoke_tool_decision("filesystem", "read", {"path": "MISSING.md"}),
            invoke_tool_decision("filesystem", "write", {"path": "SUMMARY.md", "content": "# Summary\n\nRecovered.\n"}),
            complete_decision("filesystem recovery complete"),
        ]
    )

    asyncio.run(runtime_with(store, model).run(run_resource(store)))

    run = store.get(ResourceKind.AGENT_RUN, "run-1", "demo")
    assert run is not None
    assert run["status"]["phase"] == "Succeeded"
    frames = run["status"]["data"]["executionFrames"]
    assert frames[0]["observation"]["error"]["reason"] == "PathNotFound"
    assert frames[1]["observation"]["summary"] == "Wrote SUMMARY.md"
    assert (workspace_root / "SUMMARY.md").read_text(encoding="utf-8") == "# Summary\n\nRecovered.\n"
    assert model.calls == 3


def test_autonomous_git_status_add_commit_from_decisions(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    run_git_command(workspace_root, "init")
    (workspace_root / "README.md").write_text("# Demo\n", encoding="utf-8")
    run_git_command(workspace_root, "add", "README.md")
    run_git_command(workspace_root, "commit", "-m", "docs: seed readme")
    (workspace_root / "SUMMARY.md").write_text("# Summary\n", encoding="utf-8")
    store = make_autonomous_store(
        tmp_path,
        workspace_root=workspace_root,
        tools=[builtin_tool_manifest("git", ["status", "add", "commit"])],
        agent_tools=["git"],
    )
    model = SequenceModel(
        [
            invoke_tool_decision("git", "status", {}),
            invoke_tool_decision("git", "add", {"path": "SUMMARY.md"}),
            invoke_tool_decision("git", "commit", {"message": "docs: add summary"}),
            complete_decision("git workload complete"),
        ]
    )

    asyncio.run(runtime_with(store, model).run(run_resource(store)))

    run = store.get(ResourceKind.AGENT_RUN, "run-1", "demo")
    assert run is not None
    assert run["status"]["phase"] == "Succeeded"
    frames = run["status"]["data"]["executionFrames"]
    assert [frame["decision"]["operation"] for frame in frames[:-1]] == ["status", "add", "commit"]
    assert "SUMMARY.md" in "\n".join(frames[0]["observation"]["payload"]["entries"])
    assert frames[2]["observation"]["payload"]["commit"]
    assert run_git_command(workspace_root, "log", "-1", "--pretty=%s").stdout.strip() == "docs: add summary"


def test_autonomous_shell_runtime_executes_from_decision(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    store = make_autonomous_store(
        tmp_path,
        workspace_root=workspace_root,
        tools=[builtin_tool_manifest("shell", ["execute"], {"allowedCommands": [sys.executable]})],
        agent_tools=["shell"],
    )
    model = SequenceModel(
        [
            invoke_tool_decision("shell", "execute", {"argv": [sys.executable, "-c", "print('shell ok')"]}),
            complete_decision("shell workload complete"),
        ]
    )

    asyncio.run(runtime_with(store, model).run(run_resource(store)))

    run = store.get(ResourceKind.AGENT_RUN, "run-1", "demo")
    assert run is not None
    assert run["status"]["phase"] == "Succeeded"
    frames = run["status"]["data"]["executionFrames"]
    assert frames[0]["observation"]["payload"]["stdout"] == "shell ok\n"
    assert frames[0]["observation"]["payload"]["exitCode"] == 0


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


def test_exhausted_model_retries_fail_without_active_invocation(tmp_path: Path) -> None:
    store = make_engine_store(tmp_path, execution={"maxModelRetries": 0})
    model = SequenceModel([RuntimeError("transport unavailable")])

    asyncio.run(runtime_with(store, model).run(run_resource(store)))

    run = store.get(ResourceKind.AGENT_RUN, "run-1", "demo")
    assert run is not None
    assert run["status"]["phase"] == "Failed"
    assert run["status"]["data"]["terminalReason"] == "ModelInvocationFailed"
    assert "activeModelInvocation" not in run["status"]["data"]


def test_successful_model_completion_clears_active_invocation(tmp_path: Path) -> None:
    store = make_engine_store(tmp_path)
    model = SequenceModel([complete_decision("completed")])

    asyncio.run(runtime_with(store, model).run(run_resource(store)))

    run = store.get(ResourceKind.AGENT_RUN, "run-1", "demo")
    assert run is not None
    assert run["status"]["phase"] == "Succeeded"
    assert model.calls == 1
    assert "activeModelInvocation" not in run["status"]["data"]


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


def test_late_model_response_after_terminal_agentrun_is_discarded(tmp_path: Path) -> None:
    store = make_engine_store(tmp_path)
    model = BlockingModel(invoke_fake_decision())
    runtime = runtime_with(store, model, ToolRuntimeRegistry({"fake": FailingRuntime()}))

    async def scenario() -> None:
        task = asyncio.create_task(runtime.run(run_resource(store)))
        await asyncio.wait_for(model.started.wait(), timeout=1)
        current = run_resource(store)
        runtime._timed_out(current, "AgentRunTimedOut", "AgentRun timed out during model invocation")
        model.release.set()
        await task

    asyncio.run(scenario())

    run = store.get(ResourceKind.AGENT_RUN, "run-1", "demo")
    assert run is not None
    assert run["status"]["phase"] == "TimedOut"
    assert "activeModelInvocation" not in run["status"]["data"]
    frames = run["status"]["data"]["executionFrames"]
    assert len(frames) == 1
    assert "rawDecision" not in frames[0]
    assert "decision" not in frames[0]
    assert store.list(ResourceKind.TOOL_INVOCATION, "demo") == []
    event_types = {event["type"] for event in store.list_events(namespace="demo", limit=None)}
    assert "LateModelResponseDiscarded" in event_types


def test_stale_worker_epoch_cannot_persist_execution_progress(tmp_path: Path) -> None:
    store = make_engine_store(tmp_path)
    runtime = runtime_with(store, SequenceModel([]))
    run = run_resource(store)
    agent_manifest = store.get(ResourceKind.AGENT, "runner", "demo")
    assert agent_manifest is not None
    agent = parse_resource(agent_manifest)
    assert isinstance(agent, AgentResource)
    stale = runtime._start_engine(run, agent)
    current_data = dict(stale.status.data)
    current_data["executionEpoch"] = 2
    store.update_status(
        ResourceKind.AGENT_RUN,
        stale.metadata.name,
        stale.metadata.namespace,
        stale.status.phase,
        stale.status.message,
        current_data,
    )
    stale_data = dict(stale.status.data)
    stale_data["staleMutation"] = True

    runtime._save_run_data(stale, stale_data)

    run_after = store.get(ResourceKind.AGENT_RUN, "run-1", "demo")
    assert run_after is not None
    assert run_after["status"]["data"]["executionEpoch"] == 2
    assert "staleMutation" not in run_after["status"]["data"]
    event_types = {event["type"] for event in store.list_events(namespace="demo", limit=None)}
    assert "StaleExecutionFenced" in event_types


def test_same_phase_stale_worker_cannot_overwrite_active_model_invocation(tmp_path: Path) -> None:
    store = make_engine_store(tmp_path)
    runtime = runtime_with(store, SequenceModel([]))
    run = run_resource(store)
    agent_manifest = store.get(ResourceKind.AGENT, "runner", "demo")
    mission_manifest = store.get(ResourceKind.MISSION, "run-loop", "demo")
    context_manifest = store.get(ResourceKind.CONTEXT, "run-1-context", "demo")
    assert agent_manifest is not None
    assert mission_manifest is not None
    assert context_manifest is not None
    agent = parse_resource(agent_manifest)
    mission = parse_resource(mission_manifest)
    context = parse_resource(context_manifest)
    assert isinstance(agent, AgentResource)
    assert isinstance(mission, MissionResource)
    assert isinstance(context, ContextResource)

    started = runtime._start_engine(run, agent)
    prepared = runtime._prepare_execution(started, agent, context)
    stale = runtime._ensure_active_frame(prepared, agent, mission, context)

    first_data = deepcopy(stale.status.data)
    frame, frames = runtime._active_frame(first_data)
    frame["state"] = "decision-requested"
    first_data["executionFrames"] = frames
    first_data["activeModelInvocation"] = {"id": "first", "attempt": 1}
    assert runtime._save_run_data(stale, first_data) is not None

    stale_data = deepcopy(stale.status.data)
    stale_data["activeModelInvocation"] = {"id": "second", "attempt": 1}
    stale_data["staleMutation"] = True

    assert runtime._save_run_data(stale, stale_data) is None

    run_after = store.get(ResourceKind.AGENT_RUN, "run-1", "demo")
    assert run_after is not None
    assert run_after["status"]["data"]["activeModelInvocation"]["id"] == "first"
    assert "staleMutation" not in run_after["status"]["data"]
    event_types = {event["type"] for event in store.list_events(namespace="demo", limit=None)}
    assert "StaleExecutionFenced" in event_types


def test_same_phase_stale_terminal_attempt_preserves_first_diagnostics(tmp_path: Path) -> None:
    store = make_engine_store(tmp_path)
    runtime = runtime_with(store, SequenceModel([]))
    stale = run_resource(store)

    runtime._timed_out(stale, "AgentRunTimedOut", "first timeout")
    result = runtime._timed_out(stale, "ModelInvocationTimedOut", "second timeout")

    run_after = store.get(ResourceKind.AGENT_RUN, "run-1", "demo")
    assert run_after is not None
    assert result.status.data["terminalReason"] == "AgentRunTimedOut"
    assert run_after["status"]["phase"] == "TimedOut"
    assert run_after["status"]["data"]["terminalReason"] == "AgentRunTimedOut"
    assert run_after["status"]["data"]["diagnosticSummary"] == "first timeout"
    event_types = {event["type"] for event in store.list_events(namespace="demo", limit=None)}
    assert "StaleExecutionFenced" in event_types


def test_persist_fence_stops_before_model_invocation(tmp_path: Path) -> None:
    store = make_engine_store(tmp_path)
    model = SequenceModel([complete_decision("should not be called")])

    class TerminalizingRuntime(AgentRuntime):
        def __init__(self, store: ResourceStore) -> None:
            super().__init__(store, model_client_factory=lambda _config, _store: model)
            self.terminalized = False

        def _save_run_data(self, run: Any, data: dict[str, Any]) -> Any:
            if not self.terminalized:
                self.terminalized = True
                self.store.update_status(
                    ResourceKind.AGENT_RUN,
                    run.metadata.name,
                    run.metadata.namespace,
                    "TimedOut",
                    "AgentRun timed out before model invocation",
                    {"terminalReason": "AgentRunTimedOut", **run.status.data},
                )
            return super()._save_run_data(run, data)

    asyncio.run(TerminalizingRuntime(store).run(run_resource(store)))

    run = store.get(ResourceKind.AGENT_RUN, "run-1", "demo")
    assert run is not None
    assert run["status"]["phase"] == "TimedOut"
    assert model.calls == 0
    event_types = {event["type"] for event in store.list_events(namespace="demo", limit=None)}
    assert "StaleExecutionFenced" in event_types
    assert "DecisionRequested" not in event_types
    assert "ModelInvoked" not in event_types


def test_terminal_reconciliation_is_inert_for_agentruns(tmp_path: Path) -> None:
    for phase in ["Failed", "Cancelled", "TimedOut", "BudgetExceeded", "Succeeded"]:
        case_path = tmp_path / phase.lower()
        case_path.mkdir()
        store = make_engine_store(case_path)
        store.update_status(
            ResourceKind.AGENT_RUN,
            "run-1",
            "demo",
            phase,
            f"AgentRun is {phase}",
            {"terminalReason": phase, "retryable": False, "diagnosticSummary": phase},
        )
        model = SequenceModel([invoke_fake_decision()])
        worker = LocalAgentRunWorker(store, runtime_with(store, model, ToolRuntimeRegistry({"fake": FailingRuntime()})))

        for _ in range(3):
            asyncio.run(worker.reconcile_once())

        run = store.get(ResourceKind.AGENT_RUN, "run-1", "demo")
        assert run is not None
        assert run["status"]["phase"] == phase
        assert model.calls == 0
        assert store.list(ResourceKind.TOOL_INVOCATION, "demo") == []


def test_terminal_parent_tool_invocation_is_fenced_before_runtime_execution(tmp_path: Path) -> None:
    store = make_engine_store(tmp_path)
    invocation_name = "run-1-tool-1-0001-1"
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
        ResourceKind.AGENT_RUN,
        "run-1",
        "demo",
        "TimedOut",
        "AgentRun timed out",
        {"terminalReason": "AgentRunTimedOut", "executionEpoch": 1},
    )
    runtime = FailingRuntime()
    controller = ToolInvocationController(store, ToolRuntimeRegistry({"fake": runtime}))

    asyncio.run(controller.reconcile_once())

    invocation = store.get(ResourceKind.TOOL_INVOCATION, invocation_name, "demo")
    assert invocation is not None
    assert invocation["status"]["phase"] == "Cancelled"
    assert invocation["status"]["observation"]["error"]["reason"] == "ParentAgentRunTerminal"
    assert runtime.calls == 0
    event_types = {event["type"] for event in store.list_events(namespace="demo", limit=None)}
    assert "ToolInvocationFenced" in event_types


def test_duplicate_model_invocation_is_prevented_for_active_frame(tmp_path: Path) -> None:
    store = make_engine_store(tmp_path)
    blocking_model = BlockingModel(complete_decision("first completed"))
    first_runtime = runtime_with(store, blocking_model)
    second_model = SequenceModel([complete_decision("duplicate should not run")])
    second_runtime = runtime_with(store, second_model)

    async def scenario() -> None:
        first = asyncio.create_task(first_runtime.run(run_resource(store)))
        await asyncio.wait_for(blocking_model.started.wait(), timeout=1)
        await second_runtime.run(run_resource(store))
        blocking_model.release.set()
        await first

    asyncio.run(scenario())

    assert blocking_model.calls == 1
    assert second_model.calls == 0
    run = store.get(ResourceKind.AGENT_RUN, "run-1", "demo")
    assert run is not None
    assert run["status"]["phase"] == "Succeeded"
    event_types = {event["type"] for event in store.list_events(namespace="demo", limit=None)}
    assert "DuplicateModelInvocationPrevented" in event_types


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
