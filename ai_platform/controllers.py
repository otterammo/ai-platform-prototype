from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from .events import CORRELATION_ID_ANNOTATION, CORRELATION_ID_STATUS_KEY, EventContext
from .knowledge import (
    DEFAULT_INDEX_NAME,
    ContextBuilder,
    KnowledgeIndexer,
    mission_knowledge_refs,
    mission_query,
)
from .policy import ApprovalRequired, PolicyDenied, PolicyEngine, RuntimeAction
from .resources import (
    AgentResource,
    AgentRunResource,
    CapabilityResource,
    ContextResource,
    FleetResource,
    FleetTemplateAgentSpec,
    FleetTemplateResource,
    KnowledgeIndexResource,
    MissionResource,
    Observation,
    ObservationError,
    ResourceKind,
    ToolInvocationResource,
    ToolResource,
    WorkspaceResource,
    parse_resource,
)
from .runtime import AgentRuntime, ToolRuntimeError, ToolRuntimeRegistry
from .storage import CONTROLLER_FIELD_MANAGER, ResourceStore

MISSION_GENERATION_ANNOTATION = "ai.platform/mission-generation"
FLEET_GENERATION_ANNOTATION = "ai.platform/fleet-generation"
AGENT_GENERATION_ANNOTATION = "ai.platform/agent-generation"
FLEET_TEMPLATE_GENERATION_ANNOTATION = "ai.platform/fleet-template-generation"


class ReconcileError(Exception):
    pass


@dataclass
class ReconcileResult:
    controller: str
    changed: int = 0


def resource_correlation_id(
    resource: MissionResource | FleetResource | AgentResource | AgentRunResource | ToolInvocationResource,
) -> str | None:
    annotated = resource.metadata.annotations.get(CORRELATION_ID_ANNOTATION)
    if annotated:
        return annotated
    value = resource.status.data.get(CORRELATION_ID_STATUS_KEY)
    return value if isinstance(value, str) else None


def is_current(
    resource: (
        MissionResource | FleetResource | AgentResource | AgentRunResource | ToolInvocationResource | ContextResource
    ),
) -> bool:
    return resource.status.observedGeneration == resource.metadata.generation


def owner_reference(kind: ResourceKind, name: str) -> list[dict[str, Any]]:
    return [{"kind": kind.value, "name": name, "controller": True}]


def run_name_for_agent(agent: AgentResource) -> str:
    return f"{agent.metadata.name}-run-{agent.metadata.generation}"


def context_name_for_agent_run(run_name: str) -> str:
    return f"{run_name}-context"


class MissionController:
    def __init__(self, store: ResourceStore) -> None:
        self.store = store

    async def reconcile_once(self) -> ReconcileResult:
        changed = 0
        for manifest in self.store.list(ResourceKind.MISSION):
            mission = parse_resource(manifest)
            if not isinstance(mission, MissionResource):
                continue
            if mission.status.phase in {"Completed", "Failed"} and is_current(mission):
                continue
            namespace = mission.metadata.namespace
            self.store.emit_event(
                "ReconciliationStarted",
                ResourceKind.MISSION,
                mission.metadata.name,
                namespace,
                f"MissionController started reconciling Mission {mission.metadata.name}",
                event_context=self._context(mission, "ReconcileMission", "ReconciliationStarted"),
            )
            fleet_name = f"{mission.metadata.name}-fleet"
            fleet = self._fleet(fleet_name, namespace)
            if fleet and self._fleet_matches_mission(fleet, mission):
                if self._aggregate_mission(mission, fleet):
                    changed += 1
                self.store.emit_event(
                    "ReconciliationCompleted",
                    ResourceKind.MISSION,
                    mission.metadata.name,
                    namespace,
                    f"Fleet {fleet_name} already matches Mission {mission.metadata.name}",
                    {"fleet": fleet_name},
                    event_context=self._context(mission, "SkipResource", "FleetCurrent"),
                )
                continue

            try:
                desired_fleet = self._fleet_manifest(mission, fleet)
            except ReconcileError as exc:
                self.store.update_status(
                    ResourceKind.MISSION,
                    mission.metadata.name,
                    namespace,
                    "Failed",
                    str(exc),
                    event_type="MissionFailed",
                    event_context=self._context(mission, "ReconcileMission", "ReconcileError"),
                )
                changed += 1
                continue

            if desired_fleet["spec"].get("template"):
                self.store.emit_event(
                    "FleetTemplateSelected",
                    ResourceKind.MISSION,
                    mission.metadata.name,
                    namespace,
                    f"Selected FleetTemplate {desired_fleet['spec']['template']} for Mission {mission.metadata.name}",
                    {"fleetTemplate": desired_fleet["spec"]["template"], "fleet": fleet_name},
                    event_context=self._context(mission, "SelectFleetTemplate", "FleetTemplateSelected"),
                )
            self.store.apply(
                desired_fleet,
                event_context=self._context(
                    mission,
                    "CreateFleet" if fleet is None else "UpdateFleet",
                    "FleetMissing" if fleet is None else "FleetOutdated",
                ),
                field_manager=CONTROLLER_FIELD_MANAGER,
            )
            self.store.update_status(
                ResourceKind.MISSION,
                mission.metadata.name,
                namespace,
                "Reconciling",
                "Mission controller reconciled Fleet",
                {"fleet": fleet_name},
                event_type="ReconciliationCompleted",
                event_context=self._context(mission, "ReconcileMission", "FleetReconciled"),
            )
            changed += 1
        return ReconcileResult("mission", changed)

    def _fleet(self, name: str, namespace: str | None) -> FleetResource | None:
        manifest = self.store.get(ResourceKind.FLEET, name, namespace)
        if manifest is None:
            return None
        resource = parse_resource(manifest)
        return resource if isinstance(resource, FleetResource) else None

    def _context(self, mission: MissionResource, action: str, reason: str) -> EventContext:
        return EventContext(
            controller="MissionController",
            action=action,
            reason=reason,
            correlation_id=resource_correlation_id(mission),
            workspace=mission.metadata.namespace,
            mission=mission.metadata.name,
        )

    def _fleet_manifest(self, mission: MissionResource, fleet: FleetResource | None) -> dict[str, Any]:
        namespace = mission.metadata.namespace
        fleet_name = f"{mission.metadata.name}-fleet"
        labels = dict(fleet.metadata.labels if fleet else {})
        labels["mission"] = mission.metadata.name
        annotations = dict(fleet.metadata.annotations if fleet else {})
        annotations[MISSION_GENERATION_ANNOTATION] = str(mission.metadata.generation)
        strategy = "single-agent"
        agent_count = 1
        template_name = None
        agents: list[dict[str, Any]] = []
        if mission.spec.template:
            template_manifest = self.store.get(ResourceKind.FLEET_TEMPLATE, mission.spec.template)
            if template_manifest is None:
                raise ReconcileError(f"FleetTemplate {mission.spec.template} not found")
            template = parse_resource(template_manifest)
            if not isinstance(template, FleetTemplateResource):
                raise ReconcileError(f"FleetTemplate {mission.spec.template} could not be loaded")
            strategy = "template"
            agent_count = len(template.spec.agents)
            template_name = template.metadata.name
            agents = [agent.model_dump(mode="json") for agent in template.spec.agents]
            annotations[FLEET_TEMPLATE_GENERATION_ANNOTATION] = str(template.metadata.generation)

        spec: dict[str, Any] = {
            "workspace": namespace,
            "mission": mission.metadata.name,
            "strategy": strategy,
            "agentCount": agent_count,
        }
        if template_name:
            spec["template"] = template_name
            spec["agents"] = agents
        return {
            "apiVersion": "ai.platform/v1",
            "kind": "Fleet",
            "metadata": {
                "name": fleet_name,
                "namespace": namespace,
                "labels": labels,
                "annotations": annotations,
                "ownerReferences": owner_reference(ResourceKind.MISSION, mission.metadata.name),
            },
            "spec": spec,
        }

    def _fleet_matches_mission(self, fleet: FleetResource, mission: MissionResource) -> bool:
        if mission.spec.template:
            template_manifest = self.store.get(ResourceKind.FLEET_TEMPLATE, mission.spec.template)
            if template_manifest is None:
                return False
            template = parse_resource(template_manifest)
            if not isinstance(template, FleetTemplateResource):
                return False
            return (
                fleet.spec.workspace == mission.metadata.namespace
                and fleet.spec.mission == mission.metadata.name
                and fleet.spec.strategy == "template"
                and fleet.spec.template == mission.spec.template
                and fleet.metadata.annotations.get(MISSION_GENERATION_ANNOTATION) == str(mission.metadata.generation)
                and fleet.metadata.annotations.get(FLEET_TEMPLATE_GENERATION_ANNOTATION)
                == str(template.metadata.generation)
            )
        return (
            fleet.spec.workspace == mission.metadata.namespace
            and fleet.spec.mission == mission.metadata.name
            and fleet.spec.strategy == "single-agent"
            and fleet.spec.agentCount == 1
            and fleet.metadata.annotations.get(MISSION_GENERATION_ANNOTATION) == str(mission.metadata.generation)
        )

    def _aggregate_mission(self, mission: MissionResource, fleet: FleetResource) -> bool:
        phase = fleet.status.phase
        if phase == "Succeeded":
            return self._set_mission_phase(
                mission,
                "Completed",
                "Mission completed by Fleet",
                "MissionCompleted",
                {"fleet": fleet.metadata.name},
            )
        if phase == "Failed":
            return self._set_mission_phase(
                mission,
                "Failed",
                fleet.status.message or "Mission failed by Fleet",
                "MissionFailed",
                {"fleet": fleet.metadata.name, **fleet.status.data},
            )
        if phase == "Waiting":
            return self._set_mission_phase(
                mission,
                "Waiting",
                fleet.status.message or "Mission waiting on Fleet",
                "MissionWaiting",
                {"fleet": fleet.metadata.name, **fleet.status.data},
            )
        return self._set_mission_phase(
            mission,
            "Reconciling",
            "Mission waiting for Fleet completion",
            "ReconciliationCompleted",
            {"fleet": fleet.metadata.name},
        )

    def _set_mission_phase(
        self,
        mission: MissionResource,
        phase: str,
        message: str,
        event_type: str,
        data: dict[str, Any],
    ) -> bool:
        if mission.status.phase == phase and is_current(mission):
            return False
        self.store.update_status(
            ResourceKind.MISSION,
            mission.metadata.name,
            mission.metadata.namespace,
            phase,
            message,
            data,
            event_type=event_type,
            event_context=self._context(mission, "AggregateFleet", phase),
        )
        return True


class FleetController:
    def __init__(self, store: ResourceStore) -> None:
        self.store = store

    async def reconcile_once(self) -> ReconcileResult:
        changed = 0
        for manifest in self.store.list(ResourceKind.FLEET):
            fleet = parse_resource(manifest)
            if not isinstance(fleet, FleetResource):
                continue
            if fleet.status.phase in {"Succeeded", "Failed"} and is_current(fleet):
                continue
            self.store.emit_event(
                "ReconciliationStarted",
                ResourceKind.FLEET,
                fleet.metadata.name,
                fleet.metadata.namespace,
                f"FleetController started reconciling Fleet {fleet.metadata.name}",
                event_context=self._context(fleet, "ReconcileFleet", "ReconciliationStarted"),
            )
            try:
                desired_agents = self._desired_agents(fleet)
                resolved_agents = [
                    (
                        desired_agent,
                        *self._resolve_capabilities(
                            desired_agent.capabilities,
                            f"{fleet.metadata.name}-{desired_agent.name}",
                            fleet,
                        ),
                    )
                    for desired_agent in desired_agents
                ]
            except ReconcileError as exc:
                self._fail_fleet(fleet, str(exc))
                changed += 1
                continue

            fleet_changed = 0
            for desired_agent, tools, model_ref in resolved_agents:
                agent_name = f"{fleet.metadata.name}-{desired_agent.name}"
                agent = self._agent(agent_name, fleet.metadata.namespace)
                if isinstance(agent, AgentResource) and self._agent_matches_fleet(
                    agent, fleet, desired_agent, tools, model_ref
                ):
                    self.store.emit_event(
                        "ReconciliationCompleted",
                        ResourceKind.AGENT,
                        agent_name,
                        fleet.metadata.namespace,
                        f"Agent {agent_name} already matches Fleet {fleet.metadata.name}",
                        {"agent": agent_name, "fleet": fleet.metadata.name},
                        event_context=self._context(fleet, "SkipResource", "AgentCurrent"),
                    )
                    continue
                model_config = None
                if model_ref is None:
                    model_config, model_reason = self._model_for_fleet(fleet)
                    if model_config:
                        self.store.emit_event(
                            "ModelResolved",
                            ResourceKind.AGENT,
                            agent_name,
                            fleet.metadata.namespace,
                            f"Selected {model_config['model']} for Agent {agent_name}",
                            {
                                "agent": agent_name,
                                "model": model_config["model"],
                                "provider": model_config.get("provider"),
                                "source": model_reason,
                            },
                            event_context=self._context(fleet, "ResolveModel", model_reason),
                        )
                self.store.apply(
                    self._agent_manifest(fleet, agent_name, desired_agent, tools, model_ref, model_config, agent),
                    event_context=self._context(
                        fleet,
                        "CreateAgent" if agent is None else "UpdateAgent",
                        "AgentMissing" if agent is None else "AgentOutdated",
                    ),
                    field_manager=CONTROLLER_FIELD_MANAGER,
                )
                self.store.update_status(
                    ResourceKind.AGENT,
                    agent_name,
                    fleet.metadata.namespace,
                    "Pending",
                    "Agent scheduled by Fleet controller",
                    {"fleet": fleet.metadata.name, "mission": fleet.spec.mission},
                    event_type="AgentScheduled",
                    event_context=self._context(fleet, "ScheduleAgent", "AgentScheduled"),
                )
                fleet_changed += 1

            if fleet_changed:
                self.store.update_status(
                    ResourceKind.FLEET,
                    fleet.metadata.name,
                    fleet.metadata.namespace,
                    "Running",
                    "Fleet controller created Agents",
                    event_type="FleetStarted",
                    event_context=self._context(fleet, "ReconcileFleet", "AgentsScheduled"),
                )
                changed += fleet_changed
            elif self._aggregate_fleet(fleet):
                changed += 1
            self.store.emit_event(
                "ReconciliationCompleted",
                ResourceKind.FLEET,
                fleet.metadata.name,
                fleet.metadata.namespace,
                f"FleetController completed reconciling Fleet {fleet.metadata.name}",
                {"changed": fleet_changed},
                event_context=self._context(fleet, "ReconcileFleet", "ReconciliationCompleted"),
            )
        return ReconcileResult("fleet", changed)

    def _context(self, fleet: FleetResource, action: str, reason: str) -> EventContext:
        return EventContext(
            controller="FleetController",
            action=action,
            reason=reason,
            correlation_id=resource_correlation_id(fleet),
            workspace=fleet.metadata.namespace,
            mission=fleet.spec.mission,
        )

    def _agent(self, name: str, namespace: str | None) -> AgentResource | None:
        manifest = self.store.get(ResourceKind.AGENT, name, namespace)
        if manifest is None:
            return None
        resource = parse_resource(manifest)
        return resource if isinstance(resource, AgentResource) else None

    def _model_for_fleet(self, fleet: FleetResource) -> tuple[dict[str, Any], str]:
        namespace = fleet.metadata.namespace
        mission_manifest = self.store.get(ResourceKind.MISSION, fleet.spec.mission, namespace)
        workspace_manifest = self.store.get(ResourceKind.WORKSPACE, fleet.spec.workspace)
        mission = parse_resource(mission_manifest) if mission_manifest else None
        workspace = parse_resource(workspace_manifest) if workspace_manifest else None
        if isinstance(mission, MissionResource) and mission.spec.model:
            return mission.spec.model.model_dump(mode="json", exclude_none=True), "MissionModelSelected"
        if isinstance(workspace, WorkspaceResource):
            return workspace.spec.model.model_dump(mode="json", exclude_none=True), "WorkspaceDefaultModelSelected"
        return {}, "NoModelConfig"

    def _desired_agents(self, fleet: FleetResource) -> list[FleetTemplateAgentSpec]:
        if fleet.spec.agents:
            return fleet.spec.agents
        return [
            FleetTemplateAgentSpec(name=f"agent-{index + 1}", role="executor", capabilities=[])
            for index in range(fleet.spec.agentCount)
        ]

    def _resolve_capabilities(
        self,
        capabilities: list[str],
        agent_name: str,
        fleet: FleetResource,
    ) -> tuple[list[str], str | None]:
        if not capabilities:
            return [], None
        tool_names: list[str] = []
        compatible_sets: list[list[str]] = []
        for capability_name in capabilities:
            capability_manifest = self.store.get(ResourceKind.CAPABILITY, capability_name)
            if capability_manifest is None:
                raise ReconcileError(f"Capability {capability_name} not found")
            capability = parse_resource(capability_manifest)
            if not isinstance(capability, CapabilityResource):
                raise ReconcileError(f"Capability {capability_name} could not be loaded")
            self.store.emit_event(
                "CapabilityResolved",
                ResourceKind.AGENT,
                agent_name,
                fleet.metadata.namespace,
                f"Resolved Capability {capability_name} for Agent {agent_name}",
                {"agent": agent_name, "capability": capability_name},
                event_context=self._context(fleet, "ResolveCapability", "CapabilityResolved"),
            )
            for tool_name in capability.spec.requires.tools:
                if self.store.get(ResourceKind.TOOL, tool_name) is None:
                    raise ReconcileError(f"Tool {tool_name} required by Capability {capability_name} not found")
                if tool_name not in tool_names:
                    tool_names.append(tool_name)
                    self.store.emit_event(
                        "ToolResolved",
                        ResourceKind.AGENT,
                        agent_name,
                        fleet.metadata.namespace,
                        f"Resolved Tool {tool_name} for Agent {agent_name}",
                        {"agent": agent_name, "capability": capability_name, "tool": tool_name},
                        event_context=self._context(fleet, "ResolveTool", "ToolResolved"),
                    )
            if capability.spec.compatibleModels:
                compatible_sets.append(capability.spec.compatibleModels)
        if not compatible_sets:
            raise ReconcileError(f"Capabilities {', '.join(capabilities)} do not declare compatibleModels")
        for model_name in compatible_sets[0]:
            if all(model_name in compatible_models for compatible_models in compatible_sets):
                if self.store.get(ResourceKind.MODEL, model_name):
                    self.store.emit_event(
                        "ModelResolved",
                        ResourceKind.AGENT,
                        agent_name,
                        fleet.metadata.namespace,
                        f"Selected {model_name} for Agent {agent_name}",
                        {"agent": agent_name, "model": model_name, "source": "compatibleModels"},
                        event_context=self._context(fleet, "ResolveModel", "CompatibleModelFound"),
                    )
                    return tool_names, model_name
        raise ReconcileError(f"No available Model is compatible with capabilities: {', '.join(capabilities)}")

    def _agent_manifest(
        self,
        fleet: FleetResource,
        agent_name: str,
        desired_agent: FleetTemplateAgentSpec,
        tools: list[str],
        model_ref: str | None,
        model_config: dict | None,
        agent: AgentResource | None,
    ) -> dict[str, Any]:
        labels = dict(agent.metadata.labels if agent else {})
        labels["mission"] = fleet.spec.mission
        labels["fleet"] = fleet.metadata.name
        annotations = dict(agent.metadata.annotations if agent else {})
        annotations[FLEET_GENERATION_ANNOTATION] = str(fleet.metadata.generation)
        return {
            "apiVersion": "ai.platform/v1",
            "kind": "Agent",
            "metadata": {
                "name": agent_name,
                "namespace": fleet.metadata.namespace,
                "labels": labels,
                "annotations": annotations,
                "ownerReferences": owner_reference(ResourceKind.FLEET, fleet.metadata.name),
            },
            "spec": {
                "workspace": fleet.spec.workspace,
                "mission": fleet.spec.mission,
                "fleet": fleet.metadata.name,
                "role": desired_agent.role,
                "capabilities": desired_agent.capabilities,
                "tools": tools,
                **({"pilot": {"strategy": "direct", "modelRef": model_ref}} if model_ref else {}),
                **({"model": model_config} if model_config else {}),
            },
        }

    def _agent_matches_fleet(
        self,
        agent: AgentResource,
        fleet: FleetResource,
        desired_agent: FleetTemplateAgentSpec,
        tools: list[str],
        model_ref: str | None,
    ) -> bool:
        return (
            agent.spec.workspace == fleet.spec.workspace
            and agent.spec.mission == fleet.spec.mission
            and agent.spec.fleet == fleet.metadata.name
            and agent.spec.role == desired_agent.role
            and agent.spec.capabilities == desired_agent.capabilities
            and agent.spec.tools == tools
            and ((agent.spec.pilot.modelRef if agent.spec.pilot else None) == model_ref)
            and agent.metadata.annotations.get(FLEET_GENERATION_ANNOTATION) == str(fleet.metadata.generation)
        )

    def _aggregate_fleet(self, fleet: FleetResource) -> bool:
        agents = self._owned_agents(fleet)
        if not agents:
            return False
        if any(agent.status.phase == "Failed" for agent in agents):
            phase, event_type, message = "Failed", "FleetFailed", "One or more Agents failed"
        elif any(agent.status.phase == "Waiting" for agent in agents):
            phase, event_type, message = "Waiting", "FleetWaiting", "One or more Agents are waiting"
        elif all(agent.status.phase == "Succeeded" and is_current(agent) for agent in agents):
            phase, event_type, message = "Succeeded", "FleetCompleted", "All Agents completed successfully"
        else:
            phase, event_type, message = "Running", "FleetStarted", "Fleet is waiting for Agents"
        if fleet.status.phase == phase and is_current(fleet):
            return False
        data = {"agents": [agent.metadata.name for agent in agents]}
        self.store.update_status(
            ResourceKind.FLEET,
            fleet.metadata.name,
            fleet.metadata.namespace,
            phase,
            message,
            data,
            event_type=event_type,
            event_context=self._context(fleet, "AggregateAgents", phase),
        )
        return True

    def _owned_agents(self, fleet: FleetResource) -> list[AgentResource]:
        agents: list[AgentResource] = []
        for manifest in self.store.list(ResourceKind.AGENT, fleet.metadata.namespace):
            agent = parse_resource(manifest)
            if isinstance(agent, AgentResource) and agent.spec.fleet == fleet.metadata.name:
                agents.append(agent)
        return agents

    def _fail_fleet(self, fleet: FleetResource, error: str) -> None:
        namespace = fleet.metadata.namespace
        message = f"Fleet {fleet.metadata.name} failed: {error}"
        self.store.update_status(
            ResourceKind.FLEET,
            fleet.metadata.name,
            namespace,
            "Failed",
            message,
            {"error": error},
            event_type="FleetFailed",
            event_context=self._context(fleet, "ReconcileFleet", "ReconcileError"),
        )


class AgentController:
    def __init__(self, store: ResourceStore) -> None:
        self.store = store

    async def reconcile_once(self) -> ReconcileResult:
        changed = 0
        for manifest in self.store.list(ResourceKind.AGENT):
            agent = parse_resource(manifest)
            if not isinstance(agent, AgentResource):
                continue
            if agent.status.phase in {"Succeeded", "Failed"} and is_current(agent):
                continue
            self.store.emit_event(
                "ReconciliationStarted",
                ResourceKind.AGENT,
                agent.metadata.name,
                agent.metadata.namespace,
                f"AgentController started reconciling Agent {agent.metadata.name}",
                event_context=self._context(agent, "ReconcileAgent", "ReconciliationStarted"),
            )
            try:
                mission = self._mission(agent)
                run = self._ensure_agent_run(agent, mission)
                self._ensure_context(agent, mission, run)
                if self._aggregate_agent(agent, run):
                    changed += 1
            except Exception as exc:
                error = str(exc)
                self.store.update_status(
                    ResourceKind.AGENT,
                    agent.metadata.name,
                    agent.metadata.namespace,
                    "Failed",
                    error,
                    event_type="AgentFailed",
                    event_context=self._context(agent, "ReconcileAgent", "ReconcileError"),
                )
                changed += 1
                continue
            self.store.emit_event(
                "ReconciliationCompleted",
                ResourceKind.AGENT,
                agent.metadata.name,
                agent.metadata.namespace,
                f"AgentController completed reconciling Agent {agent.metadata.name}",
                event_context=self._context(agent, "ReconcileAgent", "ReconciliationCompleted"),
            )
        return ReconcileResult("agent", changed)

    def _context(self, agent: AgentResource, action: str, reason: str) -> EventContext:
        return EventContext(
            controller="AgentController",
            action=action,
            reason=reason,
            correlation_id=resource_correlation_id(agent),
            workspace=agent.metadata.namespace,
            mission=agent.spec.mission,
        )

    def _mission(self, agent: AgentResource) -> MissionResource:
        manifest = self.store.get(ResourceKind.MISSION, agent.spec.mission, agent.metadata.namespace)
        if manifest is None:
            raise KeyError(f"Mission {agent.spec.mission} not found")
        mission = parse_resource(manifest)
        if not isinstance(mission, MissionResource):
            raise TypeError(f"expected MissionResource, got {type(mission).__name__}")
        return mission

    def _ensure_context(self, agent: AgentResource, mission: MissionResource, run: AgentRunResource) -> str:
        namespace = mission.metadata.namespace
        context_name = run.spec.contextRef.name
        self._ensure_knowledge_index(mission)
        manifest = {
            "apiVersion": "ai.platform/v1",
            "kind": "Context",
            "metadata": {
                "name": context_name,
                "namespace": namespace,
                "labels": {
                    "agent": agent.metadata.name,
                    "agentRun": run.metadata.name,
                    "mission": mission.metadata.name,
                    "fleet": agent.spec.fleet,
                },
                "annotations": {
                    CORRELATION_ID_ANNOTATION: resource_correlation_id(agent) or "",
                },
                "ownerReferences": owner_reference(ResourceKind.AGENT_RUN, run.metadata.name),
            },
            "spec": {
                "mission": mission.metadata.name,
                "agentRun": run.metadata.name,
                "query": mission_query(mission),
                "knowledgeIndex": DEFAULT_INDEX_NAME,
            },
        }
        existing_manifest = self.store.get(ResourceKind.CONTEXT, context_name, namespace)
        if existing_manifest is None or (existing_manifest.get("spec") or {}) != manifest["spec"]:
            self.store.apply(
                manifest,
                event_context=EventContext(
                    controller="AgentController",
                    action="ApplyContext",
                    reason="ContextRequired",
                    correlation_id=resource_correlation_id(mission),
                    workspace=namespace,
                    mission=mission.metadata.name,
                ),
                field_manager=CONTROLLER_FIELD_MANAGER,
            )
        return context_name

    def _ensure_knowledge_index(self, mission: MissionResource) -> None:
        namespace = mission.metadata.namespace
        refs = mission_knowledge_refs(mission)
        existing_manifest = self.store.get(ResourceKind.KNOWLEDGE_INDEX, DEFAULT_INDEX_NAME, namespace)
        existing_refs: list[str] = []
        if existing_manifest:
            existing = parse_resource(existing_manifest)
            if isinstance(existing, KnowledgeIndexResource):
                existing_refs = [source.ref for source in existing.spec.sources]
        desired_refs = list(existing_refs)
        for ref in refs:
            if ref.ref not in desired_refs:
                desired_refs.append(ref.ref)
        if existing_manifest is None or desired_refs != existing_refs:
            self.store.apply(
                {
                    "apiVersion": "ai.platform/v1",
                    "kind": "KnowledgeIndex",
                    "metadata": {
                        "name": DEFAULT_INDEX_NAME,
                        "namespace": namespace,
                        "ownerReferences": owner_reference(ResourceKind.WORKSPACE, namespace or ""),
                    },
                    "spec": {"sources": [{"ref": ref} for ref in desired_refs]},
                },
                event_context=EventContext(
                    controller="AgentController",
                    action="ApplyKnowledgeIndex",
                    reason="ContextRequired",
                    correlation_id=resource_correlation_id(mission),
                    workspace=namespace,
                    mission=mission.metadata.name,
                ),
                field_manager=CONTROLLER_FIELD_MANAGER,
            )

    def _ensure_agent_run(self, agent: AgentResource, mission: MissionResource) -> AgentRunResource:
        namespace = agent.metadata.namespace
        run_name = run_name_for_agent(agent)
        context_name = context_name_for_agent_run(run_name)
        existing_manifest = self.store.get(ResourceKind.AGENT_RUN, run_name, namespace)
        run: AgentRunResource | None = None
        if existing_manifest:
            parsed = parse_resource(existing_manifest)
            if isinstance(parsed, AgentRunResource):
                run = parsed
        desired = {
            "apiVersion": "ai.platform/v1",
            "kind": "AgentRun",
            "metadata": {
                "name": run_name,
                "namespace": namespace,
                "labels": {"agent": agent.metadata.name, "mission": mission.metadata.name, "fleet": agent.spec.fleet},
                "annotations": {
                    AGENT_GENERATION_ANNOTATION: str(agent.metadata.generation),
                    CORRELATION_ID_ANNOTATION: resource_correlation_id(agent) or "",
                },
                "ownerReferences": owner_reference(ResourceKind.AGENT, agent.metadata.name),
            },
            "spec": {
                "agentRef": {"name": agent.metadata.name},
                "missionRef": {"name": mission.metadata.name},
                "contextRef": {"name": context_name},
            },
        }
        if run is None or not self._agent_run_matches(run, agent, context_name):
            applied = self.store.apply(
                desired,
                event_context=self._context(
                    agent,
                    "CreateAgentRun" if run is None else "UpdateAgentRun",
                    "RunRequired",
                ),
                field_manager=CONTROLLER_FIELD_MANAGER,
            )
            parsed = parse_resource(applied)
            if not isinstance(parsed, AgentRunResource):
                raise TypeError(f"expected AgentRunResource, got {type(parsed).__name__}")
            run = parsed
            self.store.emit_event(
                "AgentRunCreated",
                ResourceKind.AGENT_RUN,
                run.metadata.name,
                namespace,
                f"AgentRun {run.metadata.name} created for Agent {agent.metadata.name}",
                {"agent": agent.metadata.name, "agentRun": run.metadata.name, "context": context_name},
                event_context=self._context(agent, "CreateAgentRun", "RunRequired"),
            )
        return run

    @staticmethod
    def _agent_run_matches(run: AgentRunResource, agent: AgentResource, context_name: str) -> bool:
        return (
            run.spec.agentRef.name == agent.metadata.name
            and run.spec.missionRef.name == agent.spec.mission
            and run.spec.contextRef.name == context_name
            and run.metadata.annotations.get(AGENT_GENERATION_ANNOTATION) == str(agent.metadata.generation)
        )

    def _aggregate_agent(self, agent: AgentResource, run: AgentRunResource) -> bool:
        run_manifest = self.store.get(ResourceKind.AGENT_RUN, run.metadata.name, agent.metadata.namespace)
        if run_manifest:
            parsed = parse_resource(run_manifest)
            if isinstance(parsed, AgentRunResource):
                run = parsed
        context_manifest = self.store.get(ResourceKind.CONTEXT, run.spec.contextRef.name, agent.metadata.namespace)
        if context_manifest and (context_manifest.get("status") or {}).get("phase") == "Failed":
            phase, event_type = "Failed", "AgentFailed"
            message = (context_manifest.get("status") or {}).get("message") or "Context failed"
            data = {"agentRun": run.metadata.name, "context": run.spec.contextRef.name, "error": message}
            clear_keys = ["pendingApproval"]
        elif run.status.phase == "Succeeded":
            phase, event_type, message = "Succeeded", "AgentCompleted", "AgentRun completed successfully"
            data = {"agentRun": run.metadata.name, **run.status.data}
            clear_keys = ["pendingApproval"]
        elif run.status.phase == "Failed":
            phase, event_type, message = "Failed", "AgentFailed", run.status.message or "AgentRun failed"
            data = {"agentRun": run.metadata.name, **run.status.data}
            clear_keys = ["pendingApproval"]
        elif run.status.phase == "WaitingForApproval":
            phase, event_type, message = "Waiting", "AgentWaiting", run.status.message or "AgentRun waiting"
            data = {"agentRun": run.metadata.name, **run.status.data}
            clear_keys = None
        else:
            phase, event_type, message = "Running", "AgentRunning", "AgentRun is pending execution"
            data = {"agentRun": run.metadata.name, **run.status.data}
            clear_keys = None
        if agent.status.phase == phase and is_current(agent):
            return False
        self.store.update_status(
            ResourceKind.AGENT,
            agent.metadata.name,
            agent.metadata.namespace,
            phase,
            message,
            data,
            event_type=event_type,
            event_context=self._context(agent, "AggregateAgentRun", phase),
            clear_data_keys=clear_keys,
        )
        return True


class KnowledgeIndexController:
    def __init__(self, store: ResourceStore) -> None:
        self.store = store

    async def reconcile_once(self) -> ReconcileResult:
        changed = 0
        indexer = KnowledgeIndexer(self.store)
        for manifest in self.store.list(ResourceKind.KNOWLEDGE_INDEX):
            resource = parse_resource(manifest)
            if not isinstance(resource, KnowledgeIndexResource):
                continue
            before = manifest.get("status") or {}
            try:
                after = indexer.ensure_indexed(
                    resource.metadata.namespace or "",
                    resource.metadata.name,
                    correlation_id=resource.status.data.get(CORRELATION_ID_STATUS_KEY),
                )
            except Exception:
                changed += 1
                continue
            if (after.get("status") or {}) != before:
                changed += 1
        return ReconcileResult("knowledge-index", changed)


class ContextController:
    def __init__(self, store: ResourceStore) -> None:
        self.store = store

    async def reconcile_once(self) -> ReconcileResult:
        changed = 0
        builder = ContextBuilder(self.store)
        for manifest in self.store.list(ResourceKind.CONTEXT):
            context = parse_resource(manifest)
            if not isinstance(context, ContextResource):
                continue
            if context.status.phase == "Ready" and is_current(context):
                continue
            namespace = context.metadata.namespace
            mission_manifest = self.store.get(ResourceKind.MISSION, context.spec.mission, namespace)
            if mission_manifest is None:
                self._fail_context(context, f"Mission {context.spec.mission} not found")
                changed += 1
                continue
            mission = parse_resource(mission_manifest)
            if not isinstance(mission, MissionResource):
                self._fail_context(context, f"Mission {context.spec.mission} could not be loaded")
                changed += 1
                continue
            agent_run = self._agent_run_for_context(context)
            if agent_run is None:
                self._fail_context(context, "Context must be owned by an AgentRun")
                changed += 1
                continue
            try:
                builder.build_for_mission(
                    mission,
                    index_name=context.spec.knowledgeIndex,
                    correlation_id=resource_correlation_id(mission),
                    ensure_indexed=False,
                    context_name=context.metadata.name,
                    agent_run=agent_run,
                    owner_references=[
                        owner.model_dump(mode="json", exclude_none=True) for owner in context.metadata.ownerReferences
                    ],
                )
            except Exception as exc:
                self._fail_context(context, str(exc))
                changed += 1
                continue
            changed += 1
        return ReconcileResult("context", changed)

    @staticmethod
    def _agent_run_for_context(context: ContextResource) -> str | None:
        if context.spec.agentRun:
            return context.spec.agentRun
        for owner in context.metadata.ownerReferences:
            if owner.kind == ResourceKind.AGENT_RUN:
                return owner.name
        return None

    def _fail_context(self, context: ContextResource, error: str) -> None:
        self.store.update_status(
            ResourceKind.CONTEXT,
            context.metadata.name,
            context.metadata.namespace,
            "Failed",
            error,
            {"error": error},
            event_type="ContextFailed",
            event_context=EventContext(
                controller="ContextController",
                action="BuildContext",
                reason="ContextFailed",
                workspace=context.metadata.namespace,
                mission=context.spec.mission,
            ),
        )


class AgentRunController:
    def __init__(self, store: ResourceStore) -> None:
        self.store = store

    async def reconcile_once(self) -> ReconcileResult:
        changed = 0
        for manifest in self.store.list(ResourceKind.AGENT_RUN):
            run = parse_resource(manifest)
            if not isinstance(run, AgentRunResource):
                continue
            if run.status.phase != "Pending":
                continue
            context_manifest = self.store.get(ResourceKind.CONTEXT, run.spec.contextRef.name, run.metadata.namespace)
            if context_manifest is None or (context_manifest.get("status") or {}).get("phase") != "Ready":
                continue
            self.store.update_status(
                ResourceKind.AGENT_RUN,
                run.metadata.name,
                run.metadata.namespace,
                "Scheduled",
                "AgentRun scheduled for local worker",
                {"worker": "local", "agent": run.spec.agentRef.name, "context": run.spec.contextRef.name},
                event_type="AgentRunScheduled",
                event_context=self._context(run, "ScheduleAgentRun", "Scheduled"),
            )
            changed += 1
        return ReconcileResult("agent-run", changed)

    def _context(self, run: AgentRunResource, action: str, reason: str) -> EventContext:
        return EventContext(
            controller="AgentRunController",
            action=action,
            reason=reason,
            correlation_id=resource_correlation_id(run),
            workspace=run.metadata.namespace,
            mission=run.spec.missionRef.name,
        )


class LocalAgentRunWorker:
    def __init__(self, store: ResourceStore, runtime: AgentRuntime | None = None) -> None:
        self.store = store
        self.runtime = runtime or AgentRuntime(store)

    async def reconcile_once(self) -> ReconcileResult:
        changed = 0
        for manifest in self.store.list(ResourceKind.AGENT_RUN):
            run = parse_resource(manifest)
            if not isinstance(run, AgentRunResource):
                continue
            if run.status.phase != "Scheduled" or not is_current(run):
                continue
            try:
                await self.runtime.run(run)
            except ApprovalRequired:
                changed += 1
                continue
            except Exception as exc:
                error = str(exc)
                self.store.update_status(
                    ResourceKind.AGENT_RUN,
                    run.metadata.name,
                    run.metadata.namespace,
                    "Failed",
                    error,
                    {"error": error},
                    event_type="AgentRunFailed",
                    event_context=EventContext(
                        controller="LocalAgentRunWorker",
                        action="ExecuteAgentRun",
                        reason="ExecutionFailed",
                        correlation_id=resource_correlation_id(run),
                        workspace=run.metadata.namespace,
                        mission=run.spec.missionRef.name,
                    ),
                )
                changed += 1
                continue
            changed += 1
        return ReconcileResult("worker", changed)


TOOL_INVOCATION_TERMINAL_PHASES = {"Succeeded", "Failed", "Denied", "TimedOut", "Cancelled"}


class ToolInvocationController:
    def __init__(self, store: ResourceStore, runtime_registry: ToolRuntimeRegistry | None = None) -> None:
        self.store = store
        self.policy_engine = PolicyEngine(store)
        self.runtime_registry = runtime_registry or ToolRuntimeRegistry()

    async def reconcile_once(self) -> ReconcileResult:
        changed = 0
        for manifest in self.store.list(ResourceKind.TOOL_INVOCATION):
            invocation = parse_resource(manifest)
            if not isinstance(invocation, ToolInvocationResource):
                continue
            if invocation.status.phase in TOOL_INVOCATION_TERMINAL_PHASES:
                continue
            if invocation.status.phase == "Running":
                self._fail_running_replay(invocation)
                changed += 1
                continue
            if self._pending_approval(invocation):
                continue
            if self._reconcile_invocation(invocation):
                changed += 1
        return ReconcileResult("tool-invocation", changed)

    def _reconcile_invocation(self, invocation: ToolInvocationResource) -> bool:
        try:
            run = self._agent_run(invocation)
            agent = self._agent(run)
            self._validate_tool_contract(invocation)
            action = self._runtime_action(invocation, run, agent)
            decision = self.policy_engine.authorize(action)
        except ApprovalRequired as exc:
            self._mark_waiting_for_approval(invocation, exc.approval_id)
            return True
        except PolicyDenied as exc:
            run = self._agent_run(invocation)
            observation = self._error_observation("PolicyDenied", exc.reason)
            self._set_status(
                invocation,
                run,
                "Denied",
                "ToolInvocation denied by Policy",
                "ToolInvocationDenied",
                {"policyDecision": "Deny", "reason": exc.reason},
                observation=observation,
            )
            self._record_observation(invocation, run, observation)
            return True
        except Exception as exc:
            run = self._agent_run(invocation)
            observation = self._error_observation("ToolInvocationFailed", str(exc))
            self._set_status(
                invocation,
                run,
                "Failed",
                str(exc),
                "ToolInvocationFailed",
                {"error": str(exc)},
                observation=observation,
            )
            self._record_observation(invocation, run, observation)
            return True

        try:
            runtime = self.runtime_registry.resolve(invocation)
        except ToolRuntimeError as exc:
            observation = self._error_observation("ToolRuntimeError", str(exc))
            self._set_status(
                invocation,
                run,
                "Failed",
                str(exc),
                "ToolInvocationFailed",
                {"error": str(exc)},
                observation=observation,
            )
            self._record_observation(invocation, run, observation)
            return True
        runtime_id = runtime.runtime_id
        self._set_status(
            invocation,
            run,
            "Authorized",
            "ToolInvocation authorized by Policy",
            "ToolInvocationAuthorized",
            {
                "runtime": runtime_id,
                "policyDecision": decision.effect.value,
                "policy": decision.policy_name,
                "ruleIndex": decision.rule_index,
            },
        )
        self._set_status(
            invocation,
            run,
            "Running",
            "ToolInvocation execution started",
            "ToolInvocationStarted",
            {"runtime": runtime_id},
        )
        try:
            observation = runtime.execute(invocation)
        except ToolRuntimeError as exc:
            observation = self._error_observation("ToolRuntimeError", str(exc))
            self._set_status(
                invocation,
                run,
                "Failed",
                str(exc),
                "ToolInvocationFailed",
                {"runtime": runtime_id, "error": str(exc)},
                observation=observation,
            )
            self._record_observation(invocation, run, observation)
            return True
        except Exception as exc:
            observation = self._error_observation("ToolRuntimeError", str(exc))
            self._set_status(
                invocation,
                run,
                "Failed",
                str(exc),
                "ToolInvocationFailed",
                {"runtime": runtime_id, "error": str(exc)},
                observation=observation,
            )
            self._record_observation(invocation, run, observation)
            return True

        self._set_status(
            invocation,
            run,
            "Succeeded",
            "ToolInvocation completed successfully",
            "ToolInvocationCompleted",
            {"runtime": runtime_id},
            observation=observation,
        )
        self._record_observation(invocation, run, observation)
        return True

    def _pending_approval(self, invocation: ToolInvocationResource) -> bool:
        if invocation.status.phase != "WaitingForApproval":
            return False
        approval_id = invocation.status.data.get("approvalId") or invocation.status.data.get("approval")
        if not isinstance(approval_id, str):
            return False
        approval = self.store.get(ResourceKind.APPROVAL, approval_id)
        return bool(approval and (approval.get("status") or {}).get("phase") == "Pending")

    def _mark_waiting_for_approval(self, invocation: ToolInvocationResource, approval_id: str) -> None:
        run = self._agent_run(invocation)
        if invocation.status.phase == "WaitingForApproval":
            return
        self._set_status(
            invocation,
            run,
            "WaitingForApproval",
            f"ToolInvocation waiting for approval {approval_id}",
            "ToolInvocationWaitingForApproval",
            {"approval": approval_id, "approvalId": approval_id, "policyDecision": "RequireApproval"},
        )

    def _validate_tool_contract(self, invocation: ToolInvocationResource) -> None:
        manifest = self.store.get(ResourceKind.TOOL, invocation.spec.tool)
        if manifest is None:
            raise ReconcileError(f"Tool {invocation.spec.tool} not found")

        tool = parse_resource(manifest)
        if not isinstance(tool, ToolResource):
            raise ReconcileError(f"Tool {invocation.spec.tool} could not be loaded")
        operations = [operation.name for operation in tool.spec.operations]
        if not operations:
            raise ReconcileError(f"Tool {tool.metadata.name} does not define supported operations")
        if invocation.spec.operation not in operations:
            raise ReconcileError(f"Tool {tool.metadata.name} does not support operation {invocation.spec.operation}")

    def _fail_running_replay(self, invocation: ToolInvocationResource) -> None:
        run = self._agent_run(invocation)
        message = "ToolInvocation was already Running; refusing to replay execution"
        observation = self._error_observation("ExecutionStateUnknown", message)
        self._set_status(
            invocation,
            run,
            "Failed",
            message,
            "ToolInvocationFailed",
            {"error": message},
            observation=observation,
        )
        self._record_observation(invocation, run, observation)

    def _runtime_action(
        self,
        invocation: ToolInvocationResource,
        run: AgentRunResource,
        agent: AgentResource,
    ) -> RuntimeAction:
        return RuntimeAction(
            tool=invocation.spec.tool,
            operation=invocation.spec.operation,
            details={
                "toolInvocation": invocation.metadata.name,
                "arguments": invocation.spec.arguments,
            },
            workspace=invocation.metadata.namespace,
            mission=run.spec.missionRef.name,
            agent=agent.metadata.name,
            agentRun=run.metadata.name,
            correlation_id=resource_correlation_id(invocation) or resource_correlation_id(run),
        )

    def _set_status(
        self,
        invocation: ToolInvocationResource,
        run: AgentRunResource,
        phase: str,
        message: str,
        event_type: str,
        data: dict[str, Any],
        *,
        observation: Observation | None = None,
    ) -> None:
        self.store.update_status(
            ResourceKind.TOOL_INVOCATION,
            invocation.metadata.name,
            invocation.metadata.namespace,
            phase,
            message,
            {**self._event_payload(invocation, run), **data},
            event_type=event_type,
            event_context=self._context(invocation, run, event_type, phase),
            observation=observation,
        )

    def _record_observation(
        self,
        invocation: ToolInvocationResource,
        run: AgentRunResource,
        observation: Observation,
    ) -> None:
        self.store.emit_event(
            "ObservationRecorded",
            ResourceKind.TOOL_INVOCATION,
            invocation.metadata.name,
            invocation.metadata.namespace,
            f"Observation recorded for ToolInvocation {invocation.metadata.name}",
            {
                **self._event_payload(invocation, run),
                "observation": observation.model_dump(mode="json", exclude_none=True, exclude_defaults=True),
                "summary": observation.summary,
            },
            event_context=self._context(invocation, run, "RecordObservation", "ObservationRecorded"),
        )

    def _event_payload(self, invocation: ToolInvocationResource, run: AgentRunResource) -> dict[str, Any]:
        return {
            "workspace": invocation.metadata.namespace,
            "agentRun": run.metadata.name,
            "toolInvocation": invocation.metadata.name,
            "tool": invocation.spec.tool,
            "operation": invocation.spec.operation,
        }

    def _context(
        self,
        invocation: ToolInvocationResource,
        run: AgentRunResource,
        action: str,
        reason: str,
    ) -> EventContext:
        return EventContext(
            controller="ToolInvocationController",
            action=action,
            reason=reason,
            correlation_id=resource_correlation_id(invocation) or resource_correlation_id(run),
            workspace=invocation.metadata.namespace,
            mission=run.spec.missionRef.name,
        )

    def _agent_run(self, invocation: ToolInvocationResource) -> AgentRunResource:
        manifest = self.store.get(
            ResourceKind.AGENT_RUN,
            invocation.spec.agentRunRef.name,
            invocation.metadata.namespace,
        )
        if manifest is None:
            raise ReconcileError(f"AgentRun {invocation.spec.agentRunRef.name} not found")
        run = parse_resource(manifest)
        if not isinstance(run, AgentRunResource):
            raise TypeError(f"expected AgentRunResource, got {type(run).__name__}")
        return run

    def _agent(self, run: AgentRunResource) -> AgentResource:
        manifest = self.store.get(ResourceKind.AGENT, run.spec.agentRef.name, run.metadata.namespace)
        if manifest is None:
            raise ReconcileError(f"Agent {run.spec.agentRef.name} not found")
        agent = parse_resource(manifest)
        if not isinstance(agent, AgentResource):
            raise TypeError(f"expected AgentResource, got {type(agent).__name__}")
        return agent

    @staticmethod
    def _error_observation(reason: str, message: str) -> Observation:
        return Observation(
            summary=message,
            error=ObservationError(reason=reason, message=message),
        )


class ControlPlane:
    def __init__(self, store: ResourceStore) -> None:
        self.store = store
        self.missions = MissionController(store)
        self.fleets = FleetController(store)
        self.agents = AgentController(store)
        self.knowledge_indexes = KnowledgeIndexController(store)
        self.contexts = ContextController(store)
        self.agent_runs = AgentRunController(store)
        self.tool_invocations = ToolInvocationController(store)
        self.worker = LocalAgentRunWorker(store)

    async def reconcile_once(self) -> list[ReconcileResult]:
        results = [
            await self.missions.reconcile_once(),
            await self.fleets.reconcile_once(),
            await self.agents.reconcile_once(),
            await self.knowledge_indexes.reconcile_once(),
            await self.contexts.reconcile_once(),
            await self.agent_runs.reconcile_once(),
            await self.tool_invocations.reconcile_once(),
            await self.worker.reconcile_once(),
            await self.tool_invocations.reconcile_once(),
            await self.agents.reconcile_once(),
            await self.fleets.reconcile_once(),
            await self.missions.reconcile_once(),
        ]
        return results

    async def run_forever(self, interval_seconds: float = 2.0) -> None:
        while True:
            await self.reconcile_once()
            await asyncio.sleep(interval_seconds)
