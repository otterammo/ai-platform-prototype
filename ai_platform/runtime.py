from __future__ import annotations

from pathlib import Path
from typing import TypeVar

from .events import CORRELATION_ID_ANNOTATION, CORRELATION_ID_STATUS_KEY, EventContext
from .knowledge import DEFAULT_CONTEXT_LIMIT, DEFAULT_INDEX_NAME, ContextBuilder, ContextBuildResult
from .models import Message, build_model_client
from .policy import PolicyEngine, RuntimeAction
from .resources import (
    AgentResource,
    MissionResource,
    ModelConfig,
    ModelResource,
    ResourceKind,
    WorkspaceResource,
    parse_resource,
)
from .storage import ResourceStore

_ResourceT = TypeVar("_ResourceT")


def agent_correlation_id(agent: AgentResource) -> str | None:
    annotated = agent.metadata.annotations.get(CORRELATION_ID_ANNOTATION)
    if annotated:
        return annotated
    value = agent.status.data.get(CORRELATION_ID_STATUS_KEY)
    return value if isinstance(value, str) else None


class AgentRuntime:
    def __init__(self, store: ResourceStore) -> None:
        self.store = store
        self.policy_engine = PolicyEngine(store)

    async def run(self, agent: AgentResource) -> dict[str, str]:
        namespace = agent.metadata.namespace
        if namespace is None:
            raise ValueError("Agent must have a namespace")

        mission = self._load_resource(ResourceKind.MISSION, agent.spec.mission, namespace, MissionResource)
        workspace = self._load_resource(ResourceKind.WORKSPACE, namespace, None, WorkspaceResource)

        self.store.update_status(
            ResourceKind.AGENT,
            agent.metadata.name,
            namespace,
            "Running",
            "Agent execution started",
            event_type="AgentStarted",
            event_context=self._context(agent, "StartAgent", "AgentExecutionStarted"),
        )

        self._authorize_declared_tools(agent)
        self._authorize(
            agent,
            "knowledge",
            "retrieve",
            {
                "knowledgeIndex": DEFAULT_INDEX_NAME,
                "limit": DEFAULT_CONTEXT_LIMIT,
            },
        )
        context_result = ContextBuilder(self.store).build_for_mission(
            mission,
            index_name=DEFAULT_INDEX_NAME,
            limit=DEFAULT_CONTEXT_LIMIT,
            correlation_id=agent_correlation_id(agent),
        )
        model_config = self._model_for_agent(agent, mission, workspace)
        messages = self._build_messages(mission, context_result.rendered_context)
        client = build_model_client(model_config, store=self.store)
        self._emit_context_consumed(agent, mission, context_result)

        self._authorize(
            agent,
            "model",
            "invoke",
            {"provider": model_config.provider, "model": model_config.model},
        )
        self.store.emit_event(
            "ModelInvoked",
            ResourceKind.AGENT,
            agent.metadata.name,
            namespace,
            f"Invoking {model_config.provider}:{model_config.model}",
            {"provider": model_config.provider, "model": model_config.model},
            event_context=self._context(agent, "InvokeModel", "ModelInvoked"),
        )
        output = await client.generate(messages)
        artifact_path = self._write_artifact(workspace, mission, agent, output)
        artifact = self.store.record_artifact(namespace, mission.metadata.name, agent.metadata.name, artifact_path)

        self.store.emit_event(
            "ArtifactCreated",
            ResourceKind.AGENT,
            agent.metadata.name,
            namespace,
            f"Artifact written to {artifact_path}",
            artifact,
            event_context=self._context(agent, "CreateArtifact", "ArtifactCreated"),
        )
        self.store.update_status(
            ResourceKind.AGENT,
            agent.metadata.name,
            namespace,
            "Succeeded",
            "Agent completed successfully",
            data={"artifactPath": str(artifact_path), "context": mission.metadata.name},
            event_type="AgentCompleted",
            event_context=self._context(agent, "CompleteAgent", "AgentCompleted"),
        )
        return {"artifactPath": str(artifact_path)}

    def _authorize_declared_tools(self, agent: AgentResource) -> None:
        for tool_name in agent.spec.tools:
            self._authorize(agent, tool_name, "use", {"source": "agent.spec.tools"})

    def _authorize(
        self,
        agent: AgentResource,
        tool: str,
        operation: str,
        details: dict[str, object],
    ) -> None:
        self.policy_engine.authorize(
            RuntimeAction(
                tool=tool,
                operation=operation,
                details=details,
                workspace=agent.metadata.namespace,
                mission=agent.spec.mission,
                agent=agent.metadata.name,
                correlation_id=agent_correlation_id(agent),
            )
        )

    def _context(self, agent: AgentResource, action: str, reason: str) -> EventContext:
        return EventContext(
            controller="AgentRuntime",
            action=action,
            reason=reason,
            correlation_id=agent_correlation_id(agent),
            workspace=agent.metadata.namespace,
            mission=agent.spec.mission,
        )

    def _load_resource(
        self,
        kind: ResourceKind,
        name: str,
        namespace: str | None,
        expected_type: type[_ResourceT],
    ) -> _ResourceT:
        manifest = self.store.get(kind, name, namespace)
        if manifest is None:
            raise KeyError(f"{kind.value} {name} not found")
        resource = parse_resource(manifest)
        if not isinstance(resource, expected_type):
            raise TypeError(f"expected {expected_type.__name__}, got {type(resource).__name__}")
        return resource

    def _model_for_agent(
        self,
        agent: AgentResource,
        mission: MissionResource,
        workspace: WorkspaceResource,
    ) -> ModelConfig:
        if agent.spec.pilot and agent.spec.pilot.modelRef:
            model = self._load_resource(ResourceKind.MODEL, agent.spec.pilot.modelRef, None, ModelResource)
            return model.spec.config
        return agent.spec.model or mission.spec.model or workspace.spec.model

    def _build_messages(
        self,
        mission: MissionResource,
        rendered_context: str,
    ) -> list[Message]:
        user_parts = []
        if mission.spec.objective:
            user_parts.append(f"Objective: {mission.spec.objective}")
        if mission.spec.template:
            user_parts.append(f"Template: {mission.spec.template}")
        if rendered_context:
            user_parts.append(f"Context:\n{rendered_context}")
        if mission.spec.outputs:
            outputs = ", ".join(name for name, enabled in mission.spec.outputs.items() if enabled)
            if outputs:
                user_parts.append(f"Requested outputs: {outputs}")
        return [
            {
                "role": "system",
                "content": (
                    "You are an autonomous agent in a declarative AI control plane. "
                    "Produce a concise artifact that advances the mission objective."
                ),
            },
            {"role": "user", "content": "\n\n".join(user_parts)},
        ]

    def _emit_context_consumed(
        self,
        agent: AgentResource,
        mission: MissionResource,
        context_result: ContextBuildResult,
    ) -> None:
        self.store.emit_event(
            "ContextConsumed",
            ResourceKind.CONTEXT,
            mission.metadata.name,
            agent.metadata.namespace,
            f"Context {mission.metadata.name} consumed by Agent {agent.metadata.name}",
            {
                "agent": agent.metadata.name,
                "knowledgeIndex": DEFAULT_INDEX_NAME,
                "chunkCount": len(context_result.chunks),
                "sources": context_result.sources,
            },
            event_context=self._context(agent, "ConsumeContext", "ContextConsumed"),
        )

    def _write_artifact(
        self,
        workspace: WorkspaceResource,
        mission: MissionResource,
        agent: AgentResource,
        content: str,
    ) -> Path:
        artifact_path = self._artifact_path(workspace, mission, agent)
        self._authorize(agent, "filesystem", "write", {"path": str(artifact_path), "artifact": True})
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(content, encoding="utf-8")
        return artifact_path

    def _artifact_path(
        self,
        workspace: WorkspaceResource,
        mission: MissionResource,
        agent: AgentResource,
    ) -> Path:
        workspace_root = workspace.spec.resolved_root(self.store.platform_root, workspace.metadata.name)
        artifact_dir = workspace_root / "artifacts" / mission.metadata.name
        return artifact_dir / f"{agent.metadata.name}.md"
