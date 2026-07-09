from __future__ import annotations

from pathlib import Path
from typing import TypeVar

from .events import CORRELATION_ID_ANNOTATION, CORRELATION_ID_STATUS_KEY, EventContext
from .models import Message, build_model_client
from .policy import PolicyEngine, RuntimeAction
from .resources import (
    AgentResource,
    AgentRunResource,
    ContextResource,
    MissionResource,
    ModelConfig,
    ModelResource,
    ResourceKind,
    WorkspaceResource,
    parse_resource,
)
from .storage import CONTROLLER_FIELD_MANAGER, ResourceStore

_ResourceT = TypeVar("_ResourceT")


def run_correlation_id(run: AgentRunResource) -> str | None:
    annotated = run.metadata.annotations.get(CORRELATION_ID_ANNOTATION)
    if annotated:
        return annotated
    value = run.status.data.get(CORRELATION_ID_STATUS_KEY)
    return value if isinstance(value, str) else None


class AgentRuntime:
    def __init__(self, store: ResourceStore) -> None:
        self.store = store
        self.policy_engine = PolicyEngine(store)

    async def run(self, agent_run: AgentRunResource) -> dict[str, str]:
        namespace = agent_run.metadata.namespace
        if namespace is None:
            raise ValueError("AgentRun must have a namespace")

        agent = self._load_resource(ResourceKind.AGENT, agent_run.spec.agentRef.name, namespace, AgentResource)
        mission = self._load_resource(ResourceKind.MISSION, agent_run.spec.missionRef.name, namespace, MissionResource)
        workspace = self._load_resource(ResourceKind.WORKSPACE, namespace, None, WorkspaceResource)
        context = self._load_resource(ResourceKind.CONTEXT, agent_run.spec.contextRef.name, namespace, ContextResource)
        if context.status.phase != "Ready":
            raise RuntimeError(f"Context {context.metadata.name} is {context.status.phase}, not Ready")
        rendered_context = context.status.data.get("renderedContext")
        if not isinstance(rendered_context, str):
            raise RuntimeError(f"Context {context.metadata.name} does not contain renderedContext")

        self.store.update_status(
            ResourceKind.AGENT_RUN,
            agent_run.metadata.name,
            namespace,
            "Running",
            "AgentRun execution started",
            {"agent": agent.metadata.name, "context": context.metadata.name},
            event_type="AgentRunStarted",
            event_context=self._context(agent_run, "StartAgentRun", "AgentRunStarted"),
        )

        self._authorize_declared_tools(agent, agent_run)
        model_config = self._model_for_agent(agent, mission, workspace)
        messages = self._build_messages(mission, rendered_context)
        client = build_model_client(model_config, store=self.store)
        self._emit_context_consumed(agent_run, context, agent)

        self._authorize(
            agent,
            agent_run,
            "model",
            "invoke",
            {"provider": model_config.provider, "model": model_config.model},
        )
        self.store.emit_event(
            "ModelInvoked",
            ResourceKind.AGENT_RUN,
            agent_run.metadata.name,
            namespace,
            f"Invoking {model_config.provider}:{model_config.model}",
            {"provider": model_config.provider, "model": model_config.model},
            event_context=self._context(agent_run, "InvokeModel", "ModelInvoked"),
        )
        output = await client.generate(messages)
        artifact_path = self._write_artifact(workspace, mission, agent, agent_run, output)
        artifact = self._record_artifact_resource(agent_run, agent, mission, artifact_path)

        self.store.update_status(
            ResourceKind.AGENT_RUN,
            agent_run.metadata.name,
            namespace,
            "Succeeded",
            "AgentRun completed successfully",
            {
                "artifact": artifact["metadata"]["name"],
                "artifactPath": str(artifact_path),
                "context": context.metadata.name,
                "agent": agent.metadata.name,
            },
            event_type="AgentRunCompleted",
            event_context=self._context(agent_run, "CompleteAgentRun", "AgentRunCompleted"),
        )
        return {"artifactPath": str(artifact_path)}

    def _authorize_declared_tools(self, agent: AgentResource, run: AgentRunResource) -> None:
        for tool_name in agent.spec.tools:
            self._authorize(agent, run, tool_name, "use", {"source": "agent.spec.tools"})

    def _authorize(
        self,
        agent: AgentResource,
        run: AgentRunResource,
        tool: str,
        operation: str,
        details: dict[str, object],
    ) -> None:
        self.policy_engine.authorize(
            RuntimeAction(
                tool=tool,
                operation=operation,
                details=details,
                workspace=run.metadata.namespace,
                mission=run.spec.missionRef.name,
                agent=agent.metadata.name,
                agentRun=run.metadata.name,
                correlation_id=run_correlation_id(run),
            )
        )

    def _context(self, run: AgentRunResource, action: str, reason: str) -> EventContext:
        return EventContext(
            controller="AgentRuntime",
            action=action,
            reason=reason,
            correlation_id=run_correlation_id(run),
            workspace=run.metadata.namespace,
            mission=run.spec.missionRef.name,
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

    def _build_messages(self, mission: MissionResource, rendered_context: str) -> list[Message]:
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
        run: AgentRunResource,
        context: ContextResource,
        agent: AgentResource,
    ) -> None:
        self.store.emit_event(
            "ContextConsumed",
            ResourceKind.CONTEXT,
            context.metadata.name,
            run.metadata.namespace,
            f"Context {context.metadata.name} consumed by AgentRun {run.metadata.name}",
            {
                "agent": agent.metadata.name,
                "agentRun": run.metadata.name,
                "knowledgeIndex": context.spec.knowledgeIndex,
                "chunkCount": context.status.data.get("chunkCount", 0),
                "sources": context.status.data.get("sources", []),
            },
            event_context=self._context(run, "ConsumeContext", "ContextConsumed"),
        )

    def _write_artifact(
        self,
        workspace: WorkspaceResource,
        mission: MissionResource,
        agent: AgentResource,
        run: AgentRunResource,
        content: str,
    ) -> Path:
        relative_path = self._artifact_relative_path(mission, run)
        workspace_root = workspace.spec.resolved_root(self.store.platform_root, workspace.metadata.name)
        artifact_path = workspace_root / relative_path
        self._authorize(agent, run, "filesystem", "write", {"path": str(artifact_path), "artifact": True})
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(content, encoding="utf-8")
        return artifact_path

    def _record_artifact_resource(
        self,
        run: AgentRunResource,
        agent: AgentResource,
        mission: MissionResource,
        artifact_path: Path,
    ) -> dict:
        namespace = run.metadata.namespace
        if namespace is None:
            raise ValueError("AgentRun must have a namespace")
        artifact_name = f"{run.metadata.name}-artifact"
        relative_path = self._artifact_relative_path(mission, run)
        self.store.apply(
            {
                "apiVersion": "ai.platform/v1",
                "kind": "Artifact",
                "metadata": {
                    "name": artifact_name,
                    "namespace": namespace,
                    "ownerReferences": [{"kind": "AgentRun", "name": run.metadata.name, "controller": True}],
                },
                "spec": {
                    "type": "markdown",
                    "path": str(relative_path),
                    "producedBy": {"kind": "AgentRun", "name": run.metadata.name},
                },
            },
            event_context=self._context(run, "CreateArtifact", "ArtifactCreated"),
            field_manager=CONTROLLER_FIELD_MANAGER,
        )
        return self.store.update_status(
            ResourceKind.ARTIFACT,
            artifact_name,
            namespace,
            "Ready",
            f"Artifact written to {artifact_path}",
            {
                "path": str(artifact_path),
                "absolutePath": str(artifact_path),
                "mission": mission.metadata.name,
                "agent": agent.metadata.name,
                "agentRun": run.metadata.name,
            },
            event_type="ArtifactReady",
            event_context=self._context(run, "CreateArtifact", "ArtifactReady"),
        )

    @staticmethod
    def _artifact_relative_path(mission: MissionResource, run: AgentRunResource) -> Path:
        return Path("artifacts") / mission.metadata.name / f"{run.metadata.name}.md"
