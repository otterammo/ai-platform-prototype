from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .resources import (
    AgentResource,
    FleetResource,
    MissionResource,
    ResourceKind,
    WorkspaceResource,
    parse_resource,
)
from .runtime import AgentRuntime
from .storage import ResourceStore


MISSION_GENERATION_ANNOTATION = "ai.platform/mission-generation"
FLEET_GENERATION_ANNOTATION = "ai.platform/fleet-generation"


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
            fleet = parse_resource(fleet_manifest) if fleet_manifest else None
            if isinstance(fleet, FleetResource) and self._fleet_matches_mission(fleet, mission):
                continue

            self.store.apply(self._fleet_manifest(mission, fleet))
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

    def _fleet_manifest(self, mission: MissionResource, fleet: FleetResource | None) -> dict:
        namespace = mission.metadata.namespace
        fleet_name = f"{mission.metadata.name}-fleet"
        labels = dict(fleet.metadata.labels if fleet else {})
        labels["mission"] = mission.metadata.name
        annotations = dict(fleet.metadata.annotations if fleet else {})
        annotations[MISSION_GENERATION_ANNOTATION] = str(mission.metadata.generation)
        return {
            "apiVersion": "ai.platform/v1",
            "kind": "Fleet",
            "metadata": {
                "name": fleet_name,
                "namespace": namespace,
                "labels": labels,
                "annotations": annotations,
            },
            "spec": {
                "workspace": namespace,
                "mission": mission.metadata.name,
                "strategy": "single-agent",
                "agentCount": 1,
            },
        }

    def _fleet_matches_mission(self, fleet: FleetResource, mission: MissionResource) -> bool:
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
            for index in range(fleet.spec.agentCount):
                agent_name = f"{fleet.metadata.name}-agent-{index + 1}"
                agent_manifest = self.store.get(ResourceKind.AGENT, agent_name, fleet.metadata.namespace)
                agent = parse_resource(agent_manifest) if agent_manifest else None
                if isinstance(agent, AgentResource) and self._agent_matches_fleet(agent, fleet):
                    continue
                model_config = self._model_for_fleet(fleet)
                self.store.apply(self._agent_manifest(fleet, agent_name, model_config, agent))
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

    def _model_for_fleet(self, fleet: FleetResource) -> dict:
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

    def _agent_manifest(
        self,
        fleet: FleetResource,
        agent_name: str,
        model_config: dict,
        agent: AgentResource | None,
    ) -> dict:
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
                "role": "executor",
                "model": model_config,
            },
        }

    def _agent_matches_fleet(self, agent: AgentResource, fleet: FleetResource) -> bool:
        return (
            agent.spec.workspace == fleet.spec.workspace
            and agent.spec.mission == fleet.spec.mission
            and agent.spec.fleet == fleet.metadata.name
            and agent.metadata.annotations.get(FLEET_GENERATION_ANNOTATION) == str(fleet.metadata.generation)
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
