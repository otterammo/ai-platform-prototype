from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from .resources import (
    AgentResource,
    CapabilityResource,
    FleetResource,
    FleetTemplateAgentSpec,
    FleetTemplateResource,
    MissionResource,
    ResourceKind,
    WorkspaceResource,
    parse_resource,
)
from .runtime import AgentRuntime
from .storage import ResourceStore

MISSION_GENERATION_ANNOTATION = "ai.platform/mission-generation"
FLEET_GENERATION_ANNOTATION = "ai.platform/fleet-generation"
FLEET_TEMPLATE_GENERATION_ANNOTATION = "ai.platform/fleet-template-generation"


class ReconcileError(Exception):
    pass


@dataclass
class ReconcileResult:
    controller: str
    changed: int = 0


def is_current(resource: MissionResource | FleetResource | AgentResource) -> bool:
    return resource.status.observedGeneration == resource.metadata.generation


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
            fleet_name = f"{mission.metadata.name}-fleet"
            fleet_manifest = self.store.get(ResourceKind.FLEET, fleet_name, namespace)
            fleet: FleetResource | None = None
            if fleet_manifest:
                parsed_fleet = parse_resource(fleet_manifest)
                if isinstance(parsed_fleet, FleetResource):
                    fleet = parsed_fleet
            if isinstance(fleet, FleetResource) and self._fleet_matches_mission(fleet, mission):
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
                )
                changed += 1
                continue

            self.store.apply(desired_fleet)
            if fleet_manifest is None:
                self.store.emit_event(
                    "FleetCreated",
                    ResourceKind.MISSION,
                    mission.metadata.name,
                    namespace,
                    f"Created Fleet {fleet_name}",
                    {"fleet": fleet_name},
                )
            self.store.update_status(
                ResourceKind.MISSION,
                mission.metadata.name,
                namespace,
                "Reconciling",
                "Mission controller reconciled Fleet",
                {"fleet": fleet_name},
            )
            changed += 1
        return ReconcileResult("mission", changed)

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
            fleet_changed = 0
            try:
                desired_agents = self._desired_agents(fleet)
            except ReconcileError as exc:
                self._fail_fleet(fleet, str(exc))
                changed += 1
                continue
            try:
                resolved_agents = [
                    (desired_agent, *self._resolve_capabilities(desired_agent.capabilities))
                    for desired_agent in desired_agents
                ]
            except ReconcileError as exc:
                self._fail_fleet(fleet, str(exc))
                changed += 1
                continue
            for desired_agent, tools, model_ref in resolved_agents:
                agent_name = f"{fleet.metadata.name}-{desired_agent.name}"
                agent_manifest = self.store.get(ResourceKind.AGENT, agent_name, fleet.metadata.namespace)
                agent: AgentResource | None = None
                if agent_manifest:
                    parsed_agent = parse_resource(agent_manifest)
                    if isinstance(parsed_agent, AgentResource):
                        agent = parsed_agent
                if isinstance(agent, AgentResource) and self._agent_matches_fleet(
                    agent, fleet, desired_agent, tools, model_ref
                ):
                    continue
                model_config = None if model_ref else self._model_for_fleet(fleet)
                self.store.apply(
                    self._agent_manifest(
                        fleet,
                        agent_name,
                        desired_agent,
                        tools,
                        model_ref,
                        model_config,
                        agent,
                    )
                )
                if agent_manifest is None:
                    self.store.emit_event(
                        "AgentCreated",
                        ResourceKind.FLEET,
                        fleet.metadata.name,
                        fleet.metadata.namespace,
                        f"Created Agent {agent_name}",
                        {"agent": agent_name},
                    )
                fleet_changed += 1
            if fleet_changed:
                self.store.update_status(
                    ResourceKind.FLEET,
                    fleet.metadata.name,
                    fleet.metadata.namespace,
                    "Running",
                    "Fleet controller created Agents",
                )
                changed += fleet_changed
        return ReconcileResult("fleet", changed)

    def _model_for_fleet(self, fleet: FleetResource) -> dict[str, Any]:
        namespace = fleet.metadata.namespace
        mission_manifest = self.store.get(ResourceKind.MISSION, fleet.spec.mission, namespace)
        workspace_manifest = self.store.get(ResourceKind.WORKSPACE, fleet.spec.workspace)
        mission = parse_resource(mission_manifest) if mission_manifest else None
        workspace = parse_resource(workspace_manifest) if workspace_manifest else None
        if isinstance(mission, MissionResource) and mission.spec.model:
            return mission.spec.model.model_dump(mode="json", exclude_none=True)
        if isinstance(workspace, WorkspaceResource):
            return workspace.spec.model.model_dump(mode="json", exclude_none=True)
        return {}

    def _desired_agents(self, fleet: FleetResource) -> list[FleetTemplateAgentSpec]:
        if fleet.spec.agents:
            return fleet.spec.agents
        return [
            FleetTemplateAgentSpec(
                name=f"agent-{index + 1}",
                role="executor",
                capabilities=[],
            )
            for index in range(fleet.spec.agentCount)
        ]

    def _resolve_capabilities(self, capabilities: list[str]) -> tuple[list[str], str | None]:
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
            for tool_name in capability.spec.requires.tools:
                if self.store.get(ResourceKind.TOOL, tool_name) is None:
                    raise ReconcileError(f"Tool {tool_name} required by Capability {capability_name} not found")
                if tool_name not in tool_names:
                    tool_names.append(tool_name)
            if capability.spec.compatibleModels:
                compatible_sets.append(capability.spec.compatibleModels)
        if not compatible_sets:
            raise ReconcileError(f"Capabilities {', '.join(capabilities)} do not declare compatibleModels")
        for model_name in compatible_sets[0]:
            if all(model_name in compatible_models for compatible_models in compatible_sets):
                if self.store.get(ResourceKind.MODEL, model_name):
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
        )
        self.store.update_status(
            ResourceKind.MISSION,
            fleet.spec.mission,
            namespace,
            "Failed",
            message,
            {"fleet": fleet.metadata.name, "error": error},
            event_type="MissionFailed",
        )


class AgentController:
    def __init__(self, store: ResourceStore, runtime: AgentRuntime | None = None) -> None:
        self.store = store
        self.runtime = runtime or AgentRuntime(store)

    async def reconcile_once(self) -> ReconcileResult:
        changed = 0
        for manifest in self.store.list(ResourceKind.AGENT):
            agent = parse_resource(manifest)
            if not isinstance(agent, AgentResource):
                continue
            if agent.status.phase != "Pending" and is_current(agent):
                continue
            try:
                await self.runtime.run(agent)
            except Exception as exc:
                error = str(exc)
                self.store.update_status(
                    ResourceKind.AGENT,
                    agent.metadata.name,
                    agent.metadata.namespace,
                    "Failed",
                    error,
                    event_type="AgentFailed",
                )
                self._fail_parents(agent, error)
                changed += 1
                continue
            changed += 1
            self._complete_parents(agent)
        return ReconcileResult("agent", changed)

    def _complete_parents(self, agent: AgentResource) -> None:
        namespace = agent.metadata.namespace
        if not namespace:
            return
        fleet_manifest = self.store.get(ResourceKind.FLEET, agent.spec.fleet, namespace)
        if not fleet_manifest:
            return
        fleet = parse_resource(fleet_manifest)
        if not isinstance(fleet, FleetResource):
            return

        agents = []
        for manifest in self.store.list(ResourceKind.AGENT, namespace):
            candidate = parse_resource(manifest)
            if not isinstance(candidate, AgentResource):
                continue
            if candidate.metadata.labels.get("fleet") == fleet.metadata.name:
                agents.append(candidate)
        if not agents or not all(
            item.status.phase == "Succeeded"
            and is_current(item)
            and item.metadata.annotations.get(FLEET_GENERATION_ANNOTATION) == str(fleet.metadata.generation)
            for item in agents
        ):
            return

        self.store.update_status(
            ResourceKind.FLEET,
            fleet.metadata.name,
            namespace,
            "Succeeded",
            "All Agents completed successfully",
            event_type="FleetCompleted",
        )
        self.store.update_status(
            ResourceKind.MISSION,
            fleet.spec.mission,
            namespace,
            "Completed",
            "Mission completed by Fleet",
            {"fleet": fleet.metadata.name},
            event_type="MissionCompleted",
        )

    def _fail_parents(self, agent: AgentResource, error: str) -> None:
        namespace = agent.metadata.namespace
        if not namespace:
            return
        fleet_manifest = self.store.get(ResourceKind.FLEET, agent.spec.fleet, namespace)
        if not fleet_manifest:
            return
        fleet = parse_resource(fleet_manifest)
        if not isinstance(fleet, FleetResource):
            return
        message = f"Agent {agent.metadata.name} failed: {error}"
        self.store.update_status(
            ResourceKind.FLEET,
            fleet.metadata.name,
            namespace,
            "Failed",
            message,
            {"agent": agent.metadata.name, "error": error},
            event_type="FleetFailed",
        )
        self.store.update_status(
            ResourceKind.MISSION,
            fleet.spec.mission,
            namespace,
            "Failed",
            message,
            {"fleet": fleet.metadata.name, "agent": agent.metadata.name, "error": error},
            event_type="MissionFailed",
        )


class ControlPlane:
    def __init__(self, store: ResourceStore) -> None:
        self.store = store
        self.missions = MissionController(store)
        self.fleets = FleetController(store)
        self.agents = AgentController(store)

    async def reconcile_once(self) -> list[ReconcileResult]:
        return [
            await self.missions.reconcile_once(),
            await self.fleets.reconcile_once(),
            await self.agents.reconcile_once(),
        ]

    async def run_forever(self, interval_seconds: float = 2.0) -> None:
        while True:
            await self.reconcile_once()
            await asyncio.sleep(interval_seconds)
