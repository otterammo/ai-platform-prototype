from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

API_VERSION: Literal["ai.platform/v1"] = "ai.platform/v1"


class ResourceKind(StrEnum):
    WORKSPACE = "Workspace"
    MISSION = "Mission"
    FLEET = "Fleet"
    AGENT = "Agent"
    POLICY = "Policy"
    APPROVAL = "Approval"
    MODEL = "Model"
    TOOL = "Tool"
    CAPABILITY = "Capability"
    FLEET_TEMPLATE = "FleetTemplate"
    KNOWLEDGE = "Knowledge"


CLUSTER_SCOPED_KINDS = {
    ResourceKind.WORKSPACE.value,
    ResourceKind.POLICY.value,
    ResourceKind.APPROVAL.value,
    ResourceKind.MODEL.value,
    ResourceKind.TOOL.value,
    ResourceKind.CAPABILITY.value,
    ResourceKind.FLEET_TEMPLATE.value,
}


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


class PilotConfig(BaseModel):
    strategy: str = "direct"
    modelRef: str | None = None


class WorkspaceSpec(BaseModel):
    rootPath: str | None = None
    model: ModelConfig = Field(default_factory=ModelConfig)

    def resolved_root(self, platform_root: Path, workspace_name: str) -> Path:
        if self.rootPath:
            path = Path(self.rootPath).expanduser()
            return path if path.is_absolute() else platform_root / path
        return platform_root / "workspaces" / workspace_name


class MissionSpec(BaseModel):
    objective: str | None = None
    brief: KnowledgeRef | None = None
    model: ModelConfig | None = None
    template: str | None = None
    inputs: dict[str, KnowledgeRef] = Field(default_factory=dict)
    outputs: dict[str, bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_legacy_objective_or_template(self) -> MissionSpec:
        if not self.objective and not self.template:
            raise ValueError("Mission spec requires objective or template")
        return self


class FleetTemplateAgentSpec(BaseModel):
    name: str
    role: str = "executor"
    capabilities: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return Metadata.validate_dnsish_name(value) or value


class FleetTemplateSpec(BaseModel):
    agents: list[FleetTemplateAgentSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_agents(self) -> FleetTemplateSpec:
        if not self.agents:
            raise ValueError("FleetTemplate spec.agents must not be empty")
        return self


class FleetSpec(BaseModel):
    workspace: str
    mission: str
    strategy: Literal["single-agent", "template"] = "single-agent"
    agentCount: int = Field(default=1, ge=1, le=16)
    template: str | None = None
    agents: list[FleetTemplateAgentSpec] = Field(default_factory=list)


class AgentSpec(BaseModel):
    workspace: str
    mission: str
    fleet: str
    role: str = "executor"
    model: ModelConfig | None = None
    capabilities: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    pilot: PilotConfig | None = None


class PolicyMatch(BaseModel):
    tool: str | None = None
    operation: str | None = None


class PolicyRule(BaseModel):
    match: PolicyMatch = Field(default_factory=PolicyMatch)
    allow: bool | None = None
    requiresApproval: bool | None = None
    deny: bool | None = None

    @model_validator(mode="after")
    def require_single_effect(self) -> PolicyRule:
        effects = [
            self.allow is True,
            self.requiresApproval is True,
            self.deny is True,
        ]
        if sum(effects) != 1:
            raise ValueError("Policy rule must set exactly one of allow, requiresApproval, or deny")
        return self


class PolicySpec(BaseModel):
    rules: list[PolicyRule] = Field(default_factory=list)


class ApprovalSpec(BaseModel):
    workspace: str
    mission: str
    agent: str
    action: dict[str, Any]
    actionHash: str
    policy: str | None = None
    ruleIndex: int | None = None


class ModelSpec(BaseModel):
    config: ModelConfig = Field(default_factory=ModelConfig)


class ToolSpec(BaseModel):
    description: str | None = None
    type: str = "builtin"
    config: dict[str, Any] = Field(default_factory=dict)


class CapabilityRequires(BaseModel):
    tools: list[str] = Field(default_factory=list)


class CapabilitySpec(BaseModel):
    requires: CapabilityRequires = Field(default_factory=CapabilityRequires)
    compatibleModels: list[str] = Field(default_factory=list)


class KnowledgeSpec(BaseModel):
    type: str
    ref: KnowledgeRef
    relatesTo: list[str] = Field(default_factory=list)

    @field_validator("ref", mode="before")
    @classmethod
    def coerce_ref(cls, value: Any) -> Any:
        if isinstance(value, str):
            return {"ref": value}
        return value


class BaseResource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    apiVersion: Literal["ai.platform/v1"] = API_VERSION
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


class ModelResource(BaseResource):
    kind: Literal[ResourceKind.MODEL] = ResourceKind.MODEL
    spec: ModelSpec = Field(default_factory=ModelSpec)

    @model_validator(mode="after")
    def clear_namespace(self) -> ModelResource:
        self.metadata.namespace = None
        return self


class ToolResource(BaseResource):
    kind: Literal[ResourceKind.TOOL] = ResourceKind.TOOL
    spec: ToolSpec = Field(default_factory=ToolSpec)

    @model_validator(mode="after")
    def clear_namespace(self) -> ToolResource:
        self.metadata.namespace = None
        return self


class CapabilityResource(BaseResource):
    kind: Literal[ResourceKind.CAPABILITY] = ResourceKind.CAPABILITY
    spec: CapabilitySpec = Field(default_factory=CapabilitySpec)

    @model_validator(mode="after")
    def clear_namespace(self) -> CapabilityResource:
        self.metadata.namespace = None
        return self


class FleetTemplateResource(BaseResource):
    kind: Literal[ResourceKind.FLEET_TEMPLATE] = ResourceKind.FLEET_TEMPLATE
    spec: FleetTemplateSpec

    @model_validator(mode="after")
    def clear_namespace(self) -> FleetTemplateResource:
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


class PolicyResource(BaseResource):
    kind: Literal[ResourceKind.POLICY] = ResourceKind.POLICY
    spec: PolicySpec

    @model_validator(mode="after")
    def clear_namespace(self) -> PolicyResource:
        self.metadata.namespace = None
        return self


class ApprovalResource(BaseResource):
    kind: Literal[ResourceKind.APPROVAL] = ResourceKind.APPROVAL
    spec: ApprovalSpec

    @model_validator(mode="after")
    def clear_namespace_and_validate_phase(self) -> ApprovalResource:
        self.metadata.namespace = None
        valid_phases = {"Pending", "Approved", "Rejected"}
        if self.status.phase not in valid_phases:
            raise ValueError(f"Approval status.phase must be one of: {', '.join(sorted(valid_phases))}")
        return self


class KnowledgeResource(BaseResource):
    kind: Literal[ResourceKind.KNOWLEDGE] = ResourceKind.KNOWLEDGE
    spec: KnowledgeSpec

    @model_validator(mode="after")
    def require_namespace(self) -> KnowledgeResource:
        if not self.metadata.namespace:
            raise ValueError("Knowledge metadata.namespace must name a Workspace")
        return self


Resource = Annotated[
    Union[
        WorkspaceResource,
        MissionResource,
        FleetResource,
        AgentResource,
        PolicyResource,
        ApprovalResource,
        ModelResource,
        ToolResource,
        CapabilityResource,
        FleetTemplateResource,
        KnowledgeResource,
    ],
    Field(discriminator="kind"),
]

AnyResource = (
    WorkspaceResource
    | MissionResource
    | FleetResource
    | AgentResource
    | PolicyResource
    | ApprovalResource
    | ModelResource
    | ToolResource
    | CapabilityResource
    | FleetTemplateResource
    | KnowledgeResource
)


RESOURCE_BY_KIND: dict[str, type[AnyResource]] = {
    ResourceKind.WORKSPACE.value: WorkspaceResource,
    ResourceKind.MISSION.value: MissionResource,
    ResourceKind.FLEET.value: FleetResource,
    ResourceKind.AGENT.value: AgentResource,
    ResourceKind.POLICY.value: PolicyResource,
    ResourceKind.APPROVAL.value: ApprovalResource,
    ResourceKind.MODEL.value: ModelResource,
    ResourceKind.TOOL.value: ToolResource,
    ResourceKind.CAPABILITY.value: CapabilityResource,
    ResourceKind.FLEET_TEMPLATE.value: FleetTemplateResource,
    ResourceKind.KNOWLEDGE.value: KnowledgeResource,
}


def parse_resource(raw: dict[str, Any]) -> AnyResource:
    kind = raw.get("kind")
    resource_type = RESOURCE_BY_KIND.get(str(kind))
    if resource_type is None:
        valid = ", ".join(item.value for item in ResourceKind)
        raise ValueError(f"unsupported kind {kind!r}; expected one of: {valid}")
    return resource_type.model_validate(raw)


def parse_resource_documents(raw_text: str) -> list[AnyResource]:
    docs = [doc for doc in yaml.safe_load_all(raw_text) if doc]
    return [parse_resource(doc) for doc in docs]


def resource_key(kind: str | ResourceKind, name: str, namespace: str | None = None) -> tuple[str, str, str]:
    normalized_kind = ResourceKind(kind).value
    if normalized_kind in CLUSTER_SCOPED_KINDS:
        namespace = ""
    return normalized_kind, namespace or "", name


def dump_resource(resource: AnyResource) -> dict[str, Any]:
    return resource.model_dump(mode="json", exclude_none=True)
