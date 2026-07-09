from __future__ import annotations

from pathlib import Path
from typing import TypeVar

from .events import CORRELATION_ID_ANNOTATION, CORRELATION_ID_STATUS_KEY, EventContext
from .models import Message, build_model_client
from .resources import (
    AgentResource,
    KnowledgeRef,
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

        brief_text = self._load_brief(workspace, mission, agent)
        input_texts = self._load_inputs(workspace, mission, agent)
        model_config = self._model_for_agent(agent, mission, workspace)
        messages = self._build_messages(mission, brief_text, input_texts)
        client = build_model_client(model_config, store=self.store)

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
            data={"artifactPath": str(artifact_path)},
            event_type="AgentCompleted",
            event_context=self._context(agent, "CompleteAgent", "AgentCompleted"),
        )
        return {"artifactPath": str(artifact_path)}

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

    def _load_brief(self, workspace: WorkspaceResource, mission: MissionResource, agent: AgentResource) -> str:
        if mission.spec.brief is None:
            return ""

        return self._load_knowledge_ref(workspace, mission.spec.brief, agent, "brief")

    def _load_inputs(
        self,
        workspace: WorkspaceResource,
        mission: MissionResource,
        agent: AgentResource,
    ) -> dict[str, str]:
        return {
            name: self._load_knowledge_ref(workspace, knowledge_ref, agent, f"input:{name}")
            for name, knowledge_ref in mission.spec.inputs.items()
        }

    def _load_knowledge_ref(
        self,
        workspace: WorkspaceResource,
        knowledge_ref: KnowledgeRef,
        agent: AgentResource,
        usage: str,
    ) -> str:
        workspace_root = workspace.spec.resolved_root(self.store.platform_root, workspace.metadata.name)
        knowledge_root = (workspace_root / "knowledge").resolve(strict=False)
        candidate = knowledge_root / Path(knowledge_ref.path)
        resolved_candidate = candidate.resolve(strict=False)
        try:
            resolved_candidate.relative_to(knowledge_root)
        except ValueError as exc:
            raise PermissionError(
                f"knowledge reference {knowledge_ref.ref} resolves outside workspace knowledge"
            ) from exc
        if not resolved_candidate.is_file():
            raise FileNotFoundError(f"knowledge reference {knowledge_ref.ref} not found at {candidate}")
        text = resolved_candidate.read_text(encoding="utf-8")
        self.store.emit_event(
            "KnowledgeLoaded",
            ResourceKind.AGENT,
            agent.metadata.name,
            agent.metadata.namespace,
            f"Loaded {knowledge_ref.ref} for Agent {agent.metadata.name}",
            {
                "knowledgeRef": knowledge_ref.ref,
                "path": str(resolved_candidate),
                "usage": usage,
            },
            event_context=self._context(agent, "LoadKnowledge", "KnowledgeLoaded"),
        )
        return text

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
        brief_text: str,
        input_texts: dict[str, str],
    ) -> list[Message]:
        user_parts = []
        if mission.spec.objective:
            user_parts.append(f"Objective: {mission.spec.objective}")
        if mission.spec.template:
            user_parts.append(f"Template: {mission.spec.template}")
        if brief_text:
            user_parts.append(f"Brief:\n{brief_text}")
        for name, text in input_texts.items():
            user_parts.append(f"Input {name}:\n{text}")
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

    def _write_artifact(
        self,
        workspace: WorkspaceResource,
        mission: MissionResource,
        agent: AgentResource,
        content: str,
    ) -> Path:
        workspace_root = workspace.spec.resolved_root(self.store.platform_root, workspace.metadata.name)
        artifact_dir = workspace_root / "artifacts" / mission.metadata.name
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / f"{agent.metadata.name}.md"
        artifact_path.write_text(content, encoding="utf-8")
        return artifact_path
