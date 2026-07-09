from __future__ import annotations

from datetime import datetime
from typing import Any

from .events import correlation_id_from_manifest
from .resources import AgentResource, FleetResource, MissionResource, ResourceKind, parse_resource
from .storage import ResourceStore

JsonDict = dict[str, Any]
JsonDictList = list[JsonDict]

DECISION_EVENT_TYPES = {
    "FleetTemplateSelected",
    "CapabilityResolved",
    "ToolResolved",
    "ModelResolved",
    "PolicyEvaluated",
    "PolicyAllowed",
    "PolicyDenied",
    "ApprovalRequested",
    "ApprovalGranted",
    "ApprovalRejected",
    "AgentPaused",
    "AgentResumed",
    "KnowledgeIndexed",
    "ChunkCreated",
    "RetrievalStarted",
    "RetrievalCompleted",
    "ContextBuilt",
    "ContextConsumed",
    "ReconciliationStarted",
    "ReconciliationCompleted",
}


def build_trace(store: ResourceStore, mission_name: str, namespace: str | None) -> JsonDict | None:
    mission_manifest = store.get(ResourceKind.MISSION, mission_name, namespace)
    if mission_manifest is None:
        return None
    mission = parse_resource(mission_manifest)
    if not isinstance(mission, MissionResource):
        return None

    events = _mission_events(store, mission_manifest, mission.metadata.namespace, ascending=True)
    artifacts = store.list_artifacts(mission.metadata.namespace, mission.metadata.name)
    fleets = _mission_fleets(store, mission)
    trace_fleets = []
    for fleet in fleets:
        agents = _fleet_agents(store, fleet)
        trace_fleets.append(
            {
                "name": fleet.metadata.name,
                "status": fleet.status.phase,
                "strategy": fleet.spec.strategy,
                "template": fleet.spec.template,
                "conditions": fleet.status.model_dump(mode="json", exclude_none=True).get("conditions", []),
                "agents": [_agent_trace(agent, artifacts, events) for agent in agents],
                "events": _resource_events(events, ResourceKind.FLEET.value, fleet.metadata.name),
            }
        )

    return {
        "mission": {
            "name": mission.metadata.name,
            "namespace": mission.metadata.namespace,
            "status": mission.status.phase,
            "correlationId": correlation_id_from_manifest(mission_manifest),
            "conditions": mission.status.model_dump(mode="json", exclude_none=True).get("conditions", []),
        },
        "fleets": trace_fleets,
        "events": events,
        "decisions": [event for event in events if event["type"] in DECISION_EVENT_TYPES],
        "artifacts": artifacts,
        "knowledge": _knowledge_trace(events),
    }


def build_timeline(store: ResourceStore, mission_name: str, namespace: str | None) -> JsonDict | None:
    mission_manifest = store.get(ResourceKind.MISSION, mission_name, namespace)
    if mission_manifest is None:
        return None
    events = _mission_events(store, mission_manifest, namespace, ascending=True)
    return {
        "mission": mission_name,
        "namespace": namespace,
        "correlationId": correlation_id_from_manifest(mission_manifest),
        "items": [
            {
                "id": event["id"],
                "timestamp": event["timestamp"],
                "time": _event_time(event),
                "event": event["type"],
                "message": _timeline_message(event),
                "controller": event["controller"],
                "resourceKind": event["resourceKind"],
                "resourceName": event["resourceName"],
                "reason": event["reason"],
            }
            for event in events
        ],
    }


def describe_resource(store: ResourceStore, kind: str, name: str, namespace: str | None) -> JsonDict | None:
    resource = store.get(kind, name, namespace)
    if resource is None:
        return None

    status = resource.get("status") or {}
    recent_events = _recent_events_for_resource(store, resource, kind, name, namespace)
    artifacts = _artifacts_for_resource(store, resource, kind, name, namespace)
    return {
        "resource": resource,
        "desiredState": resource.get("spec") or {},
        "observedState": status,
        "children": _children_for_resource(store, resource, kind, name, namespace),
        "recentEvents": recent_events,
        "statusConditions": status.get("conditions") or [],
        "referencedResources": _referenced_resources(resource, kind),
        "artifactsProduced": artifacts,
        "knowledgeUsed": _knowledge_used(store, resource, kind, name, namespace, recent_events),
        "events": recent_events,
        "artifacts": artifacts,
    }


def format_trace(trace: JsonDict) -> str:
    mission = trace["mission"]
    lines = [
        f"Mission {mission['name']}",
        f"Status: {mission['status']}",
    ]
    if mission.get("correlationId"):
        lines.append(f"Correlation ID: {mission['correlationId']}")
    lines.extend(_knowledge_detail_lines(trace))
    lines.append("Mission")
    fleets = trace["fleets"]
    for fleet_index, fleet in enumerate(fleets):
        fleet_last = fleet_index == len(fleets) - 1
        fleet_branch = "└──" if fleet_last else "├──"
        fleet_prefix = "    " if fleet_last else "│   "
        lines.append(f"{fleet_branch} Fleet {fleet['name']}")
        agents = fleet["agents"]
        for agent_index, agent in enumerate(agents):
            agent_last = agent_index == len(agents) - 1
            agent_branch = "└──" if agent_last else "├──"
            agent_prefix = f"{fleet_prefix}{'    ' if agent_last else '│   '}"
            lines.append(f"{fleet_prefix}{agent_branch} {_agent_label(agent)}")
            detail_lines = _agent_detail_lines(agent)
            for detail_index, detail in enumerate(detail_lines):
                detail_last = detail_index == len(detail_lines) - 1
                detail_branch = "└──" if detail_last else "├──"
                lines.append(f"{agent_prefix}{detail_branch} {detail}")
    return "\n".join(lines)


def format_timeline(timeline: JsonDict) -> str:
    return "\n".join(f"{item['time']} {item['message']}" for item in timeline["items"])


def _knowledge_trace(events: JsonDictList) -> JsonDict:
    indexed = [event for event in events if event["type"] == "KnowledgeIndexed"]
    retrievals = [event for event in events if event["type"] == "RetrievalCompleted"]
    contexts = [event for event in events if event["type"] == "ContextBuilt"]
    consumed = [event for event in events if event["type"] == "ContextConsumed"]
    latest_index = indexed[-1]["payload"] if indexed else {}
    latest_retrieval = retrievals[-1]["payload"] if retrievals else {}
    latest_context = contexts[-1]["payload"] if contexts else {}
    sources = latest_retrieval.get("sources") or latest_context.get("sources") or []
    return {
        "index": latest_retrieval.get("knowledgeIndex")
        or latest_context.get("knowledgeIndex")
        or latest_index.get("knowledgeIndex"),
        "retrieved": sources,
        "contextChunkCount": latest_context.get("chunkCount") or latest_retrieval.get("chunkCount") or 0,
        "consumedBy": [event["payload"].get("agent") for event in consumed if event["payload"].get("agent")],
    }


def _knowledge_detail_lines(trace: JsonDict) -> list[str]:
    knowledge = trace.get("knowledge") or {}
    if not knowledge.get("index") and not knowledge.get("retrieved") and not knowledge.get("contextChunkCount"):
        return []
    lines = ["Knowledge"]
    if knowledge.get("index"):
        lines.append(f"Index: {knowledge['index']}")
    lines.append("Retrieved:")
    retrieved = knowledge.get("retrieved") or []
    if retrieved:
        for source in retrieved:
            document = source.get("document") or source.get("sourceRef")
            lines.append(f"{document}")
            lines.append(f"  {source.get('chunkCount', 0)} chunks")
    else:
        lines.append("none")
    lines.append("Context")
    lines.append(f"{knowledge.get('contextChunkCount', 0)} chunks")
    consumed_by = knowledge.get("consumedBy") or []
    if consumed_by:
        lines.append("Consumed by")
        lines.extend(_consumer_label(str(agent)) for agent in consumed_by)
    return lines


def _consumer_label(agent_name: str) -> str:
    short_name = _short_agent_name(agent_name)
    if short_name.startswith("Agent "):
        return short_name
    return f"{short_name.replace('-', ' ').title()} Agent"


def _mission_events(
    store: ResourceStore,
    mission_manifest: JsonDict,
    namespace: str | None,
    *,
    ascending: bool,
) -> JsonDictList:
    correlation_id = correlation_id_from_manifest(mission_manifest)
    if correlation_id:
        return store.list_events(namespace=namespace, limit=None, correlation_id=correlation_id, ascending=ascending)
    return store.list_events(namespace=namespace, limit=1000, ascending=ascending)


def _mission_fleets(store: ResourceStore, mission: MissionResource) -> list[FleetResource]:
    fleets: list[FleetResource] = []
    for manifest in store.list(ResourceKind.FLEET, mission.metadata.namespace):
        candidate = parse_resource(manifest)
        if isinstance(candidate, FleetResource) and candidate.spec.mission == mission.metadata.name:
            fleets.append(candidate)
    return sorted(fleets, key=lambda item: item.metadata.name)


def _fleet_agents(store: ResourceStore, fleet: FleetResource) -> list[AgentResource]:
    agents: list[AgentResource] = []
    for manifest in store.list(ResourceKind.AGENT, fleet.metadata.namespace):
        candidate = parse_resource(manifest)
        if isinstance(candidate, AgentResource) and candidate.spec.fleet == fleet.metadata.name:
            agents.append(candidate)

    order = {agent.name: index for index, agent in enumerate(fleet.spec.agents)}
    return sorted(
        agents,
        key=lambda item: (
            order.get(item.metadata.name.removeprefix(f"{fleet.metadata.name}-"), 999),
            item.metadata.name,
        ),
    )


def _agent_trace(agent: AgentResource, artifacts: JsonDictList, events: JsonDictList) -> JsonDict:
    return {
        "name": agent.metadata.name,
        "role": agent.spec.role,
        "status": agent.status.phase,
        "capabilities": agent.spec.capabilities,
        "tools": agent.spec.tools,
        "model": agent.spec.pilot.modelRef if agent.spec.pilot and agent.spec.pilot.modelRef else _model_name(agent),
        "conditions": agent.status.model_dump(mode="json", exclude_none=True).get("conditions", []),
        "artifacts": [artifact for artifact in artifacts if artifact["agent"] == agent.metadata.name],
        "events": _agent_events(events, agent.metadata.name),
    }


def _model_name(agent: AgentResource) -> str | None:
    if agent.spec.model is None:
        return None
    return agent.spec.model.model


def _resource_events(events: JsonDictList, kind: str, name: str) -> JsonDictList:
    return [event for event in events if event["resourceKind"] == kind and event["resourceName"] == name]


def _agent_events(events: JsonDictList, agent_name: str) -> JsonDictList:
    return [
        event
        for event in events
        if (event["resourceKind"] == ResourceKind.AGENT.value and event["resourceName"] == agent_name)
        or _runtime_action_agent(event) == agent_name
    ]


def _runtime_action_agent(event: JsonDict) -> str | None:
    payload = event.get("payload") or {}
    runtime_action = payload.get("runtimeAction")
    if isinstance(runtime_action, dict) and isinstance(runtime_action.get("agent"), str):
        return runtime_action["agent"]
    return None


def _agent_label(agent: JsonDict) -> str:
    role = str(agent["role"]).replace("-", " ").title()
    return f"{role} Agent"


def _agent_detail_lines(agent: JsonDict) -> list[str]:
    lines = [f"Capability {capability}" for capability in agent["capabilities"]]
    if agent.get("model"):
        lines.append(f"Model {agent['model']}")
    lines.append("Tools")
    lines.extend(f"Tool {tool}" for tool in agent["tools"])
    lines.extend(_policy_detail_lines(agent["events"]))
    lines.append(_display_status(agent["status"]))
    return lines


def _policy_detail_lines(events: JsonDictList) -> list[str]:
    lines: list[str] = []
    for event in events:
        payload = event["payload"]
        event_type = event["type"]
        runtime_action = payload.get("runtimeAction") or {}
        tool = runtime_action.get("tool")
        operation = runtime_action.get("operation")
        policy = payload.get("policy")
        approval_id = payload.get("approvalId")
        if event_type == "PolicyEvaluated" and policy:
            lines.append(f"Requested {tool} {operation}")
            lines.append(f"Policy: {policy}")
        elif event_type == "PolicyDenied":
            lines.append("Policy denied")
        elif event_type == "ApprovalRequested":
            lines.append(f"Approval required {approval_id}")
        elif event_type == "ApprovalGranted":
            lines.append(f"Approval granted {approval_id}")
        elif event_type == "ApprovalRejected":
            lines.append(f"Approval rejected {approval_id}")
        elif event_type == "AgentPaused":
            lines.append("Agent paused")
        elif event_type == "AgentResumed":
            lines.append("Agent resumed")
    return lines


def _display_status(status: str) -> str:
    if status == "Succeeded":
        return "Completed"
    return status


def _event_time(event: JsonDict) -> str:
    timestamp = event["timestamp"]
    try:
        return datetime.fromisoformat(timestamp).strftime("%H:%M:%S")
    except ValueError:
        return str(timestamp)


def _timeline_message(event: JsonDict) -> str:
    payload = event["payload"]
    event_type = event["type"]
    resource_name = str(event.get("resourceName") or event.get("resource") or "")
    labels = {
        "MissionCreated": "Mission created",
        "MissionUpdated": "Mission updated",
        "MissionCompleted": "Mission completed",
        "MissionFailed": "Mission failed",
        "FleetCreated": "Fleet created",
        "FleetStarted": "Fleet started",
        "FleetCompleted": "Fleet completed",
        "AgentCreated": f"{_short_agent_name(resource_name)} created",
        "AgentScheduled": f"{_short_agent_name(resource_name)} scheduled",
        "AgentStarted": f"{_short_agent_name(resource_name)} started",
        "AgentCompleted": f"{_short_agent_name(resource_name)} completed",
        "AgentFailed": f"{_short_agent_name(resource_name)} failed",
        "ArtifactCreated": "Artifact created",
        "KnowledgeLoaded": f"Knowledge loaded {payload.get('knowledgeRef')}",
        "KnowledgeIndexed": f"Knowledge indexed {payload.get('knowledgeIndex')}",
        "ChunkCreated": f"Knowledge chunk created {payload.get('document')}",
        "RetrievalStarted": f"Retrieval started {payload.get('knowledgeIndex')}",
        "RetrievalCompleted": f"Retrieval completed {payload.get('chunkCount')} chunks",
        "ContextBuilt": f"Context built {payload.get('chunkCount')} chunks",
        "ContextConsumed": f"Context consumed by {_short_agent_name(str(payload.get('agent') or ''))}",
        "ModelInvoked": f"Model invoked {payload.get('model')}",
        "PolicyEvaluated": f"Policy evaluated {_runtime_action_label(payload)}",
        "PolicyAllowed": f"Policy allowed {_runtime_action_label(payload)}",
        "PolicyDenied": f"Policy denied {_runtime_action_label(payload)}",
        "ApprovalRequested": f"Approval requested {payload.get('approvalId')}",
        "ApprovalGranted": f"Approval granted {payload.get('approvalId')}",
        "ApprovalRejected": f"Approval rejected {payload.get('approvalId')}",
        "AgentPaused": f"{_short_agent_name(resource_name)} paused for approval",
        "AgentResumed": f"{_short_agent_name(resource_name)} resumed",
        "FleetWaiting": "Fleet waiting for approval",
        "MissionWaiting": "Mission waiting for approval",
    }
    fallback = event.get("message") or event_type
    return labels.get(event_type, str(fallback))


def _runtime_action_label(payload: JsonDict) -> str:
    runtime_action = payload.get("runtimeAction") or {}
    tool = runtime_action.get("tool")
    operation = runtime_action.get("operation")
    if tool and operation:
        return f"{tool}/{operation}"
    return "action"


def _short_agent_name(resource_name: str) -> str:
    if not resource_name:
        return "Agent"
    parts = resource_name.split("-")
    if len(parts) >= 2 and parts[-2] == "agent" and parts[-1].isdigit():
        return f"Agent {parts[-1]}"
    return parts[-1].replace("-", " ").title()


def _recent_events_for_resource(
    store: ResourceStore,
    resource: JsonDict,
    kind: str,
    name: str,
    namespace: str | None,
) -> JsonDictList:
    if kind == ResourceKind.MISSION.value:
        correlation_id = correlation_id_from_manifest(resource)
        if correlation_id:
            return store.list_events(namespace=namespace, limit=50, correlation_id=correlation_id)
        return store.list_events(namespace=namespace, limit=50)
    return store.list_events(namespace=namespace, resource_kind=kind, resource_name=name, limit=50)


def _artifacts_for_resource(
    store: ResourceStore,
    resource: JsonDict,
    kind: str,
    name: str,
    namespace: str | None,
) -> JsonDictList:
    if kind == ResourceKind.MISSION.value:
        return store.list_artifacts(namespace, name)
    if kind == ResourceKind.FLEET.value:
        mission_name = (resource.get("spec") or {}).get("mission")
        return store.list_artifacts(namespace, mission_name) if isinstance(mission_name, str) else []
    if kind == ResourceKind.AGENT.value:
        spec = resource.get("spec") or {}
        mission_name = spec.get("mission")
        if not isinstance(mission_name, str):
            return []
        return [artifact for artifact in store.list_artifacts(namespace, mission_name) if artifact["agent"] == name]
    return []


def _children_for_resource(
    store: ResourceStore,
    resource: JsonDict,
    kind: str,
    name: str,
    namespace: str | None,
) -> JsonDict:
    if kind == ResourceKind.MISSION.value:
        fleets = []
        agents = []
        for fleet_manifest in store.list(ResourceKind.FLEET, namespace):
            fleet = parse_resource(fleet_manifest)
            if isinstance(fleet, FleetResource) and fleet.spec.mission == name:
                fleets.append({"kind": "Fleet", "name": fleet.metadata.name, "phase": fleet.status.phase})
        for agent_manifest in store.list(ResourceKind.AGENT, namespace):
            agent = parse_resource(agent_manifest)
            if isinstance(agent, AgentResource) and agent.spec.mission == name:
                agents.append(
                    {
                        "kind": "Agent",
                        "name": agent.metadata.name,
                        "fleet": agent.spec.fleet,
                        "phase": agent.status.phase,
                    }
                )
        return {"fleets": fleets, "agents": agents}
    if kind == ResourceKind.FLEET.value:
        agents = []
        for agent_manifest in store.list(ResourceKind.AGENT, namespace):
            agent = parse_resource(agent_manifest)
            if isinstance(agent, AgentResource) and agent.spec.fleet == name:
                agents.append({"kind": "Agent", "name": agent.metadata.name, "phase": agent.status.phase})
        return {"agents": agents}
    return {}


def _referenced_resources(resource: JsonDict, kind: str) -> JsonDictList:
    spec = resource.get("spec") or {}
    references: JsonDictList = []
    if kind == ResourceKind.MISSION.value:
        references.append({"kind": "Workspace", "name": (resource.get("metadata") or {}).get("namespace")})
        if spec.get("template"):
            references.append({"kind": "FleetTemplate", "name": spec["template"]})
        if spec.get("model"):
            references.append({"kind": "Model", "name": spec["model"].get("model"), "source": "mission"})
    elif kind == ResourceKind.CONTEXT.value:
        references.append({"kind": "Mission", "name": spec.get("mission")})
        references.append({"kind": "KnowledgeIndex", "name": spec.get("knowledgeIndex")})
    elif kind == ResourceKind.KNOWLEDGE_INDEX.value:
        for source in spec.get("sources") or []:
            ref = source.get("ref") if isinstance(source, dict) else source
            references.append({"kind": "Knowledge", "ref": ref})
    elif kind == ResourceKind.FLEET.value:
        references.extend(
            [
                {"kind": "Workspace", "name": spec.get("workspace")},
                {"kind": "Mission", "name": spec.get("mission")},
            ]
        )
        if spec.get("template"):
            references.append({"kind": "FleetTemplate", "name": spec["template"]})
    elif kind == ResourceKind.AGENT.value:
        references.extend(
            [
                {"kind": "Workspace", "name": spec.get("workspace")},
                {"kind": "Mission", "name": spec.get("mission")},
                {"kind": "Fleet", "name": spec.get("fleet")},
            ]
        )
        for capability in spec.get("capabilities") or []:
            references.append({"kind": "Capability", "name": capability})
        for tool in spec.get("tools") or []:
            references.append({"kind": "Tool", "name": tool})
        pilot = spec.get("pilot") or {}
        if pilot.get("modelRef"):
            references.append({"kind": "Model", "name": pilot["modelRef"]})
    return references


def _knowledge_used(
    store: ResourceStore,
    resource: JsonDict,
    kind: str,
    name: str,
    namespace: str | None,
    recent_events: JsonDictList,
) -> JsonDictList:
    spec = resource.get("spec") or {}
    refs: JsonDictList = []
    if kind == ResourceKind.MISSION.value:
        brief = spec.get("brief") or {}
        if brief.get("ref"):
            refs.append({"ref": brief["ref"], "usage": "brief"})
        for input_name, input_ref in (spec.get("inputs") or {}).items():
            if isinstance(input_ref, dict) and input_ref.get("ref"):
                refs.append({"ref": input_ref["ref"], "usage": f"input:{input_name}"})
    for event in recent_events:
        payload = event["payload"]
        if event["type"] == "KnowledgeLoaded" and payload.get("knowledgeRef"):
            refs.append(
                {
                    "ref": payload["knowledgeRef"],
                    "usage": payload.get("usage"),
                    "agent": event["resourceName"],
                    "path": payload.get("path"),
                }
            )
        elif event["type"] == "ContextBuilt":
            for source in payload.get("sources") or []:
                if isinstance(source, dict) and source.get("sourceRef"):
                    refs.append(
                        {
                            "ref": source["sourceRef"],
                            "usage": "context",
                            "document": source.get("document"),
                            "chunkCount": source.get("chunkCount"),
                        }
                    )
    knowledge_names = _knowledge_names_by_ref(store, namespace)
    seen: set[tuple[str | None, str | None, str | None]] = set()
    unique: JsonDictList = []
    for item in refs:
        key = (item.get("ref"), item.get("usage"), item.get("agent"))
        if key in seen:
            continue
        seen.add(key)
        if item.get("ref") in knowledge_names:
            item["resource"] = knowledge_names[item["ref"]]
        unique.append(item)
    if kind == ResourceKind.AGENT.value:
        return [item for item in unique if item.get("agent") in {None, name}]
    return unique


def _knowledge_names_by_ref(store: ResourceStore, namespace: str | None) -> dict[str, str]:
    names: dict[str, str] = {}
    for manifest in store.list(ResourceKind.KNOWLEDGE, namespace):
        spec = manifest.get("spec") or {}
        ref = spec.get("ref")
        ref_value = ref.get("ref") if isinstance(ref, dict) else ref
        if isinstance(ref_value, str):
            names[ref_value] = (manifest.get("metadata") or {}).get("name", ref_value)
    return names
