from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


API_VERSION = "ai.platform/v1"


class ResourceKind(StrEnum):
    WORKSPACE = "Workspace"
    MISSION = "Mission"
    FLEET = "Fleet"
    AGENT = "Agent"


class Condition(BaseModel):
    type: str
    status: Literal["True", "False", "Unknown"] = "Unknown"
    reason: str | None = None
    message: str | None = None


class Status(BaseModel):
    phase: str = "Pending"
    observedGeneration: int = 0
    message: str | None = None
    conditions: list[Condition] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)


class Metadata(BaseModel):
    name: str
    namespace: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    generation: int = 1

    @field_validator("name", "namespace")
    @classmethod
    def validate_dnsish_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not value:
            raise ValueError("must not be empty")
        allowed = set("abcdefghijklmnopqrstuvwxyz0123456789-.")
        lowered = value.lower()
        if lowered != value or any(char not in allowed for char in value):
            raise ValueError("use lowercase letters, numbers, hyphens, and dots")
        return value


class KnowledgeRef(BaseModel):
    ref: str

    @field_validator("ref")
    @classmethod
    def validate_ref(cls, value: str) -> str:
        prefix = "knowledge://"
        if not value.startswith(prefix):
            raise ValueError("knowledge references must use knowledge://")
        path = value.removeprefix(prefix)
        if not path:
            raise ValueError("knowledge references must include a path")
        if path.startswith("/"):
            raise ValueError("knowledge references must be relative paths")
        if "\\" in path or "//" in path:
            raise ValueError("knowledge references must use normalized slash-separated paths")
        parts = path.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError("knowledge references must not contain dot segments")
        return value

    @property
    def path(self) -> str:
        return self.ref.removeprefix("knowledge://")


class ModelConfig(BaseModel):
    provider: Literal["stub", "openai-compatible"] = "stub"
    model: str = "stub-model"
    baseUrl: str | None = None
    apiKeyEnv: str = "OPENAI_API_KEY"
    temperature: float = 0.2
    timeoutSeconds: float = 60.0


class WorkspaceSpec(BaseModel):
    rootPath: str | None = None
    model: ModelConfig = Field(default_factory=ModelConfig)

    def resolved_root(self, platform_root: Path, workspace_name: str) -> Path:
        if self.rootPath:
            path = Path(self.rootPath).expanduser()
            return path if path.is_absolute() else platform_root / path
        return platform_root / "workspaces" / workspace_name


class MissionSpec(BaseModel):
    objective: str
    brief: KnowledgeRef | None = None
    model: ModelConfig | None = None


class FleetSpec(BaseModel):
    workspace: str
    mission: str
    strategy: Literal["single-agent"] = "single-agent"
    agentCount: int = Field(default=1, ge=1, le=16)


class AgentSpec(BaseModel):
    workspace: str
    mission: str
    fleet: str
    role: str = "executor"
    model: ModelConfig = Field(default_factory=ModelConfig)


class BaseResource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    apiVersion: Literal[API_VERSION] = API_VERSION
    kind: ResourceKind
    metadata: Metadata
    spec: BaseModel
    status: Status = Field(default_factory=Status)


class WorkspaceResource(BaseResource):
    kind: Literal[ResourceKind.WORKSPACE] = ResourceKind.WORKSPACE
    spec: WorkspaceSpec = Field(default_factory=WorkspaceSpec)

    @model_validator(mode="after")
    def clear_namespace(self) -> WorkspaceResource:
        self.metadata.namespace = None
        return self


class MissionResource(BaseResource):
    kind: Literal[ResourceKind.MISSION] = ResourceKind.MISSION
    spec: MissionSpec

    @model_validator(mode="after")
    def require_namespace(self) -> MissionResource:
        if not self.metadata.namespace:
            raise ValueError("Mission metadata.namespace must name a Workspace")
        return self


class FleetResource(BaseResource):
    kind: Literal[ResourceKind.FLEET] = ResourceKind.FLEET
    spec: FleetSpec

    @model_validator(mode="after")
    def require_namespace(self) -> FleetResource:
        if not self.metadata.namespace:
            raise ValueError("Fleet metadata.namespace must name a Workspace")
        if self.spec.workspace != self.metadata.namespace:
            raise ValueError("Fleet spec.workspace must match metadata.namespace")
        return self


class AgentResource(BaseResource):
    kind: Literal[ResourceKind.AGENT] = ResourceKind.AGENT
    spec: AgentSpec

    @model_validator(mode="after")
    def require_namespace(self) -> AgentResource:
        if not self.metadata.namespace:
            raise ValueError("Agent metadata.namespace must name a Workspace")
        if self.spec.workspace != self.metadata.namespace:
            raise ValueError("Agent spec.workspace must match metadata.namespace")
        return self


Resource = Annotated[
    Union[WorkspaceResource, MissionResource, FleetResource, AgentResource],
    Field(discriminator="kind"),
]

RESOURCE_BY_KIND: dict[str, type[WorkspaceResource | MissionResource | FleetResource | AgentResource]] = {
    ResourceKind.WORKSPACE.value: WorkspaceResource,
    ResourceKind.MISSION.value: MissionResource,
    ResourceKind.FLEET.value: FleetResource,
    ResourceKind.AGENT.value: AgentResource,
}


def parse_resource(raw: dict[str, Any]) -> WorkspaceResource | MissionResource | FleetResource | AgentResource:
    kind = raw.get("kind")
    resource_type = RESOURCE_BY_KIND.get(str(kind))
    if resource_type is None:
        valid = ", ".join(item.value for item in ResourceKind)
        raise ValueError(f"unsupported kind {kind!r}; expected one of: {valid}")
    return resource_type.model_validate(raw)


def parse_resource_documents(raw_text: str) -> list[WorkspaceResource | MissionResource | FleetResource | AgentResource]:
    docs = [doc for doc in yaml.safe_load_all(raw_text) if doc]
    return [parse_resource(doc) for doc in docs]


def resource_key(kind: str | ResourceKind, name: str, namespace: str | None = None) -> tuple[str, str, str]:
    normalized_kind = ResourceKind(kind).value
    if normalized_kind == ResourceKind.WORKSPACE.value:
        namespace = ""
    return normalized_kind, namespace or "", name


def dump_resource(resource: WorkspaceResource | MissionResource | FleetResource | AgentResource) -> dict[str, Any]:
    return resource.model_dump(mode="json", exclude_none=True)
