from __future__ import annotations

from pathlib import Path

from .models import Message, build_model_client
from .resources import (
    AgentResource,
    MissionResource,
    ResourceKind,
    WorkspaceResource,
    parse_resource,
)
from .storage import ResourceStore


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
        )

        brief_text = self._load_brief(workspace, mission)
        model_config = agent.spec.model or mission.spec.model or workspace.spec.model
        messages = self._build_messages(mission, brief_text)
        client = build_model_client(model_config, store=self.store)

        self.store.emit_event(
            "ModelInvoked",
            ResourceKind.AGENT,
            agent.metadata.name,
            namespace,
            f"Invoking {model_config.provider}:{model_config.model}",
            {"provider": model_config.provider, "model": model_config.model},
        )
        output = await client.generate(messages)
        artifact_path = self._write_artifact(workspace, mission, agent, output)
        artifact = self.store.record_artifact(namespace, mission.metadata.name, agent.metadata.name, artifact_path)

        self.store.emit_event(
            "ArtifactWritten",
            ResourceKind.AGENT,
            agent.metadata.name,
            namespace,
            f"Artifact written to {artifact_path}",
            artifact,
        )
        self.store.update_status(
            ResourceKind.AGENT,
            agent.metadata.name,
            namespace,
            "Succeeded",
            "Agent completed successfully",
            data={"artifactPath": str(artifact_path)},
            event_type="AgentCompleted",
        )
        return {"artifactPath": str(artifact_path)}

    def _load_resource(self, kind: ResourceKind, name: str, namespace: str | None, expected_type: type) -> object:
        manifest = self.store.get(kind, name, namespace)
        if manifest is None:
            raise KeyError(f"{kind.value} {name} not found")
        resource = parse_resource(manifest)
        if not isinstance(resource, expected_type):
            raise TypeError(f"expected {expected_type.__name__}, got {type(resource).__name__}")
        return resource

    def _load_brief(self, workspace: WorkspaceResource, mission: MissionResource) -> str:
        if mission.spec.brief is None:
            return ""

        workspace_root = workspace.spec.resolved_root(self.store.platform_root, workspace.metadata.name)
        knowledge_root = (workspace_root / "knowledge").resolve(strict=False)
        candidate = knowledge_root / Path(mission.spec.brief.path)
        resolved_candidate = candidate.resolve(strict=False)
        try:
            resolved_candidate.relative_to(knowledge_root)
        except ValueError as exc:
            raise PermissionError(
                f"knowledge reference {mission.spec.brief.ref} resolves outside workspace knowledge"
            ) from exc
        if not resolved_candidate.is_file():
            raise FileNotFoundError(
                f"knowledge reference {mission.spec.brief.ref} not found at {candidate}"
            )
        return resolved_candidate.read_text(encoding="utf-8")

    def _build_messages(self, mission: MissionResource, brief_text: str) -> list[Message]:
        user_parts = [f"Objective: {mission.spec.objective}"]
        if brief_text:
            user_parts.append(f"Brief:\n{brief_text}")
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
