from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

import yaml

from . import __version__
from .controllers import ControlPlane
from .knowledge import DEFAULT_INDEX_NAME, KeywordRetriever, KnowledgeIndexer
from .observability import build_timeline, build_trace, describe_resource, format_timeline, format_trace
from .policy import ApprovalService
from .resources import ResourceKind, parse_resource_documents
from .storage import DEFAULT_DB_URL, ResourceStore

RESOURCE_ALIASES = {
    "workspace": ResourceKind.WORKSPACE.value,
    "workspaces": ResourceKind.WORKSPACE.value,
    "mission": ResourceKind.MISSION.value,
    "missions": ResourceKind.MISSION.value,
    "fleet": ResourceKind.FLEET.value,
    "fleets": ResourceKind.FLEET.value,
    "agent": ResourceKind.AGENT.value,
    "agents": ResourceKind.AGENT.value,
    "agentrun": ResourceKind.AGENT_RUN.value,
    "agentruns": ResourceKind.AGENT_RUN.value,
    "agent-run": ResourceKind.AGENT_RUN.value,
    "agent-runs": ResourceKind.AGENT_RUN.value,
    "toolinvocation": ResourceKind.TOOL_INVOCATION.value,
    "toolinvocations": ResourceKind.TOOL_INVOCATION.value,
    "tool-invocation": ResourceKind.TOOL_INVOCATION.value,
    "tool-invocations": ResourceKind.TOOL_INVOCATION.value,
    "observation": "Observation",
    "observations": "Observation",
    "artifact": ResourceKind.ARTIFACT.value,
    "artifacts": ResourceKind.ARTIFACT.value,
    "policy": ResourceKind.POLICY.value,
    "policies": ResourceKind.POLICY.value,
    "approval": ResourceKind.APPROVAL.value,
    "approvals": ResourceKind.APPROVAL.value,
    "model": ResourceKind.MODEL.value,
    "models": ResourceKind.MODEL.value,
    "tool": ResourceKind.TOOL.value,
    "tools": ResourceKind.TOOL.value,
    "capability": ResourceKind.CAPABILITY.value,
    "capabilities": ResourceKind.CAPABILITY.value,
    "fleettemplate": ResourceKind.FLEET_TEMPLATE.value,
    "fleettemplates": ResourceKind.FLEET_TEMPLATE.value,
    "fleet-template": ResourceKind.FLEET_TEMPLATE.value,
    "fleet-templates": ResourceKind.FLEET_TEMPLATE.value,
    "knowledge": ResourceKind.KNOWLEDGE.value,
    "knowledgeindex": ResourceKind.KNOWLEDGE_INDEX.value,
    "knowledgeindexes": ResourceKind.KNOWLEDGE_INDEX.value,
    "knowledge-index": ResourceKind.KNOWLEDGE_INDEX.value,
    "knowledge-indexes": ResourceKind.KNOWLEDGE_INDEX.value,
    "context": ResourceKind.CONTEXT.value,
    "contexts": ResourceKind.CONTEXT.value,
}


def normalize_kind(value: str) -> str:
    try:
        return ResourceKind(value).value
    except ValueError:
        pass
    normalized = RESOURCE_ALIASES.get(value.lower())
    if normalized:
        return normalized
    valid = ", ".join(sorted(RESOURCE_ALIASES))
    raise argparse.ArgumentTypeError(f"invalid resource kind {value!r}; expected one of: {valid}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="platform", description="Declarative AI platform prototype CLI")
    parser.add_argument(
        "--db",
        default=os.environ.get("AI_PLATFORM_DB", DEFAULT_DB_URL),
        help="SQLAlchemy database URL",
    )
    parser.add_argument("--root", default=os.environ.get("AI_PLATFORM_ROOT", ".platform"), help="Platform data root")
    subparsers = parser.add_subparsers(dest="command", required=True)

    version_parser = subparsers.add_parser("version", help="Show platform CLI version")
    version_parser.add_argument("-o", "--output", choices=["yaml", "json"], default="yaml")

    health_parser = subparsers.add_parser("health", help="Check local store or API health")
    health_parser.add_argument("--api-url", help="Base API URL or /health endpoint to check")
    health_parser.add_argument("-o", "--output", choices=["yaml", "json"], default="yaml")

    apply_parser = subparsers.add_parser("apply", help="Apply one or more YAML resource manifests")
    apply_parser.add_argument("path", nargs="?", type=Path)
    apply_parser.add_argument("-f", "--file", type=Path)

    get_parser = subparsers.add_parser("get", help="Get or list resources")
    get_parser.add_argument("kind", type=normalize_kind)
    get_parser.add_argument("name", nargs="?")
    get_parser.add_argument("-n", "--namespace")
    get_parser.add_argument("-o", "--output", choices=["yaml", "json"], default="yaml")

    list_parser = subparsers.add_parser("list", help="List resources")
    list_parser.add_argument("kind", nargs="?", type=normalize_kind)
    list_parser.add_argument("-n", "--namespace")
    list_parser.add_argument("-o", "--output", choices=["yaml", "json"], default="yaml")

    delete_parser = subparsers.add_parser("delete", help="Delete a resource")
    delete_parser.add_argument("kind", type=normalize_kind)
    delete_parser.add_argument("name")
    delete_parser.add_argument("-n", "--namespace")

    describe_parser = subparsers.add_parser("describe", help="Describe a resource with related events")
    describe_parser.add_argument("kind", type=normalize_kind)
    describe_parser.add_argument("name")
    describe_parser.add_argument("-n", "--namespace")
    describe_parser.add_argument("-o", "--output", choices=["yaml", "json"], default="yaml")

    reconcile_parser = subparsers.add_parser("reconcile", help="Run controllers")
    reconcile_parser.add_argument("--watch", action="store_true", help="Run continuously")
    reconcile_parser.add_argument("--interval", type=float, default=2.0)

    wait_parser = subparsers.add_parser("wait", help="Wait for a resource condition")
    wait_parser.add_argument("kind", type=normalize_kind)
    wait_parser.add_argument("name")
    wait_parser.add_argument("-n", "--namespace")
    wait_parser.add_argument(
        "--for",
        dest="wait_for",
        required=True,
        help="Condition to wait for, e.g. phase=Completed",
    )
    wait_parser.add_argument("--timeout", type=float, default=60.0)
    wait_parser.add_argument("--interval", type=float, default=1.0)
    wait_parser.add_argument("--reconcile", action="store_true", help="Run reconciliation while waiting")
    wait_parser.add_argument("-o", "--output", choices=["yaml", "json"], default="yaml")

    trace_parser = subparsers.add_parser("trace", help="Show an execution trace")
    trace_subparsers = trace_parser.add_subparsers(dest="trace_kind", required=True)
    trace_mission_parser = trace_subparsers.add_parser("mission", help="Trace a Mission execution")
    trace_mission_parser.add_argument("name")
    trace_mission_parser.add_argument("-n", "--namespace", required=True)
    trace_mission_parser.add_argument("-o", "--output", choices=["text", "yaml", "json"], default="text")

    timeline_parser = subparsers.add_parser("timeline", help="Show an event timeline")
    timeline_subparsers = timeline_parser.add_subparsers(dest="timeline_kind", required=True)
    timeline_mission_parser = timeline_subparsers.add_parser("mission", help="Timeline a Mission execution")
    timeline_mission_parser.add_argument("name")
    timeline_mission_parser.add_argument("-n", "--namespace", required=True)
    timeline_mission_parser.add_argument("-o", "--output", choices=["text", "yaml", "json"], default="text")

    knowledge_parser = subparsers.add_parser("knowledge", help="Manage and search workspace knowledge")
    knowledge_subparsers = knowledge_parser.add_subparsers(dest="knowledge_command", required=True)
    knowledge_index_parser = knowledge_subparsers.add_parser("index", help="Build a KnowledgeIndex")
    knowledge_index_parser.add_argument("-n", "--namespace", required=True)
    knowledge_index_parser.add_argument("--index", default=DEFAULT_INDEX_NAME)
    knowledge_index_parser.add_argument("-o", "--output", choices=["yaml", "json"], default="yaml")
    knowledge_search_parser = knowledge_subparsers.add_parser("search", help="Search indexed knowledge")
    knowledge_search_parser.add_argument("query")
    knowledge_search_parser.add_argument("-n", "--namespace", required=True)
    knowledge_search_parser.add_argument("--index", default=DEFAULT_INDEX_NAME)
    knowledge_search_parser.add_argument("--limit", type=int, default=10)
    knowledge_search_parser.add_argument("-o", "--output", choices=["yaml", "json"], default="yaml")

    events_parser = subparsers.add_parser("events", help="List recent events")
    events_parser.add_argument("-n", "--namespace")
    events_parser.add_argument("--kind", type=normalize_kind)
    events_parser.add_argument("--name")
    events_parser.add_argument("--correlation-id")
    events_parser.add_argument("--limit", type=int, default=100)
    events_parser.add_argument("-o", "--output", choices=["yaml", "json"], default="yaml")

    approvals_parser = subparsers.add_parser("approvals", help="List approval requests")
    approvals_parser.add_argument("-o", "--output", choices=["yaml", "json"], default="yaml")

    approve_parser = subparsers.add_parser("approve", help="Approve a pending Approval")
    approve_parser.add_argument("name")
    approve_parser.add_argument("--by", default="manual")
    approve_parser.add_argument("--reason")

    reject_parser = subparsers.add_parser("reject", help="Reject a pending Approval")
    reject_parser.add_argument("name")
    reject_parser.add_argument("--by", default="manual")
    reject_parser.add_argument("--reason")

    serve_parser = subparsers.add_parser("serve", help="Run the FastAPI server with uvicorn")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)
    return parser


def make_store(args: argparse.Namespace) -> ResourceStore:
    return ResourceStore(args.db, args.root)


def print_data(data: Any, output: str = "yaml") -> None:
    if output == "json":
        print(json.dumps(data, indent=2))
        return
    print(yaml.safe_dump(data, sort_keys=False))


async def run_async(args: argparse.Namespace) -> int:
    if args.command == "version":
        print_data({"version": __version__}, args.output)
        return 0

    if args.command == "health" and args.api_url:
        try:
            print_data(_api_health(args.api_url), args.output)
            return 0
        except Exception as exc:
            print_data({"status": "unhealthy", "error": str(exc), "apiUrl": _health_url(args.api_url)}, args.output)
            return 1

    store = make_store(args)
    if args.command == "health":
        health = _local_health(store)
        print_data(health, args.output)
        return 0 if health["status"] == "ok" else 1

    if args.command == "apply":
        path = args.file or args.path
        if path is None:
            print("apply requires a manifest path or -f/--file", file=sys.stderr)
            return 2
        resources = parse_resource_documents(path.read_text(encoding="utf-8"))
        applied = [store.apply(resource.model_dump(mode="json", exclude_none=True)) for resource in resources]
        print_data({"items": applied})
        return 0

    if args.command == "get":
        if args.kind == "Observation":
            observations = _embedded_observations(store, args.namespace)
            if args.name is None:
                print_data(
                    {
                        "message": (
                            "Observations are embedded in ToolInvocation status for v1.1; "
                            "Observation is not a standalone resource yet."
                        ),
                        "items": observations,
                    },
                    args.output,
                )
                return 0
            for observation in observations:
                if observation["toolInvocation"] == args.name:
                    print_data(observation, args.output)
                    return 0
            print(f"Observation {args.name} not found", file=sys.stderr)
            return 1
        if args.name is None:
            print_data({"items": store.list(args.kind, args.namespace)}, args.output)
            return 0
        resource = _get_resource(store, args.kind, args.name, args.namespace)
        if not resource:
            print(f"{args.kind} {args.name} not found", file=sys.stderr)
            return 1
        print_data(resource, args.output)
        return 0

    if args.command == "list":
        if args.kind == "Observation":
            print_data(
                {
                    "message": (
                        "Observations are embedded in ToolInvocation status for v1.1; "
                        "Observation is not a standalone resource yet."
                    ),
                    "items": _embedded_observations(store, args.namespace),
                },
                args.output,
            )
            return 0
        print_data({"items": store.list(args.kind, args.namespace)}, args.output)
        return 0

    if args.command == "describe":
        namespace = args.namespace
        if args.kind == ResourceKind.TOOL_INVOCATION.value and namespace is None:
            resource = _get_resource(store, args.kind, args.name, None)
            if resource is not None:
                namespace = (resource.get("metadata") or {}).get("namespace")
        description = describe_resource(store, args.kind, args.name, namespace)
        if not description:
            print(f"{args.kind} {args.name} not found", file=sys.stderr)
            return 1
        print_data(description, args.output)
        return 0

    if args.command == "delete":
        if not store.delete(args.kind, args.name, args.namespace):
            print(f"{args.kind} {args.name} not found", file=sys.stderr)
            return 1
        print(f"deleted {args.kind} {args.name}")
        return 0

    if args.command == "reconcile":
        control_plane = ControlPlane(store)
        if args.watch:
            await control_plane.run_forever(args.interval)
            return 0
        results = await control_plane.reconcile_once()
        print_data({"controllers": [result.__dict__ for result in results]})
        return 0

    if args.command == "wait":
        try:
            field, expected = _parse_wait_for(args.wait_for)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        ok, result = await _wait_for_resource(
            store,
            args.kind,
            args.name,
            args.namespace,
            field,
            expected,
            timeout_seconds=args.timeout,
            interval_seconds=args.interval,
            reconcile=args.reconcile,
        )
        print_data(result, args.output)
        return 0 if ok else 1

    if args.command == "trace":
        trace = build_trace(store, args.name, args.namespace)
        if trace is None:
            print(f"Mission {args.name} not found", file=sys.stderr)
            return 1
        if args.output == "text":
            print(format_trace(trace))
        else:
            print_data(trace, args.output)
        return 0

    if args.command == "timeline":
        timeline = build_timeline(store, args.name, args.namespace)
        if timeline is None:
            print(f"Mission {args.name} not found", file=sys.stderr)
            return 1
        if args.output == "text":
            print(format_timeline(timeline))
        else:
            print_data(timeline, args.output)
        return 0

    if args.command == "knowledge":
        if args.knowledge_command == "index":
            index = KnowledgeIndexer(store).index(args.namespace, args.index)
            print_data({"index": index}, args.output)
            return 0
        if args.knowledge_command == "search":
            KnowledgeIndexer(store).ensure_indexed(args.namespace, args.index)
            search_results = KeywordRetriever(store).retrieve(args.namespace, args.index, args.query, limit=args.limit)
            print_data({"items": [_search_result(item) for item in search_results]}, args.output)
            return 0

    if args.command == "events":
        print_data(
            {
                "items": store.list_events(
                    namespace=args.namespace,
                    resource_kind=args.kind,
                    resource_name=args.name,
                    limit=args.limit,
                    correlation_id=args.correlation_id,
                )
            },
            args.output,
        )
        return 0

    if args.command == "approvals":
        print_data({"items": store.list(ResourceKind.APPROVAL)}, args.output)
        return 0

    if args.command == "approve":
        try:
            approval = ApprovalService(store).approve(args.name, actor=args.by, reason=args.reason)
        except (KeyError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        results = await ControlPlane(store).reconcile_once()
        print_data({"approval": approval, "controllers": [result.__dict__ for result in results]})
        return 0

    if args.command == "reject":
        try:
            approval = ApprovalService(store).reject(args.name, actor=args.by, reason=args.reason)
        except (KeyError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        results = await ControlPlane(store).reconcile_once()
        print_data({"approval": approval, "controllers": [result.__dict__ for result in results]})
        return 0

    raise AssertionError(f"unhandled command {args.command}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        return _serve(args)
    return asyncio.run(run_async(args))


def _serve(args: argparse.Namespace) -> int:
    os.environ["AI_PLATFORM_DB"] = args.db
    os.environ["AI_PLATFORM_ROOT"] = args.root
    import uvicorn

    uvicorn.run("ai_platform.api:app", host=args.host, port=args.port, reload=False)
    return 0


def _local_health(store: ResourceStore) -> dict[str, Any]:
    platform = store.get(ResourceKind.PLATFORM, "local")
    phase = (platform.get("status") or {}).get("phase") if platform else None
    return {
        "status": "ok" if phase == "Ready" else "unhealthy",
        "version": __version__,
        "database": store.database_url,
        "root": str(store.platform_root),
        "platform": {
            "name": "local",
            "phase": phase,
        },
    }


def _health_url(api_url: str) -> str:
    base = api_url.rstrip("/")
    if base.endswith("/health"):
        return base
    return f"{base}/health"


def _api_health(api_url: str) -> dict[str, Any]:
    url = _health_url(api_url)
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=5) as response:
        raw_body = response.read().decode("utf-8")
    body = json.loads(raw_body) if raw_body.strip() else {}
    if not isinstance(body, dict):
        raise ValueError("health endpoint did not return an object")
    return {
        "status": body.get("status", "unknown"),
        "version": __version__,
        "apiUrl": url,
        "api": body,
    }


def _parse_wait_for(value: str) -> tuple[str, str]:
    field, separator, expected = value.partition("=")
    if not separator or not field or not expected:
        raise ValueError("wait requires --for phase=<Phase>")
    if field != "phase":
        raise ValueError("wait currently supports only --for phase=<Phase>")
    return field, expected


async def _wait_for_resource(
    store: ResourceStore,
    kind: str,
    name: str,
    namespace: str | None,
    field: str,
    expected: str,
    *,
    timeout_seconds: float,
    interval_seconds: float,
    reconcile: bool,
) -> tuple[bool, dict[str, Any]]:
    deadline = time.monotonic() + timeout_seconds
    control_plane = ControlPlane(store) if reconcile else None
    last_value: str | None = None
    last_resource: dict[str, Any] | None = None

    while True:
        if control_plane:
            await control_plane.reconcile_once()
        resource = store.get(kind, name, namespace)
        if resource is not None:
            last_resource = resource
            last_value = _resource_wait_value(resource, field)
            if last_value == expected:
                return True, {
                    "status": "met",
                    "kind": kind,
                    "name": name,
                    "namespace": namespace,
                    "condition": {"field": field, "value": expected},
                    "resource": resource,
                }

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False, {
                "status": "timeout",
                "kind": kind,
                "name": name,
                "namespace": namespace,
                "condition": {"field": field, "value": expected},
                "observed": last_value,
                "resource": last_resource,
            }
        await asyncio.sleep(min(interval_seconds, remaining))


def _resource_wait_value(resource: dict[str, Any], field: str) -> str | None:
    if field == "phase":
        value = (resource.get("status") or {}).get("phase")
        return value if isinstance(value, str) else None
    raise ValueError(f"unsupported wait field {field}")


def _embedded_observations(store: ResourceStore, namespace: str | None = None) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for manifest in store.list(ResourceKind.TOOL_INVOCATION, namespace):
        status = manifest.get("status") or {}
        observation = status.get("observation")
        if not observation:
            continue
        metadata = manifest.get("metadata") or {}
        spec = manifest.get("spec") or {}
        observations.append(
            {
                "namespace": metadata.get("namespace"),
                "toolInvocation": metadata.get("name"),
                "agentRun": (spec.get("agentRunRef") or {}).get("name"),
                "tool": spec.get("tool"),
                "operation": spec.get("operation"),
                "phase": status.get("phase"),
                "observation": observation,
            }
        )
    return observations


def _get_resource(store: ResourceStore, kind: str, name: str, namespace: str | None = None) -> dict[str, Any] | None:
    resource = store.get(kind, name, namespace)
    if resource is not None or namespace is not None or kind != ResourceKind.TOOL_INVOCATION.value:
        return resource
    matches = [
        item for item in store.list(ResourceKind.TOOL_INVOCATION) if (item.get("metadata") or {}).get("name") == name
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def _search_result(chunk: dict[str, Any]) -> dict[str, Any]:
    content = str(chunk.get("content") or "")
    preview = " ".join(content.split())
    if len(preview) > 160:
        preview = f"{preview[:157]}..."
    return {
        "document": chunk["document"],
        "section": chunk["section"],
        "sourceRef": chunk["sourceRef"],
        "chunkId": chunk["chunkId"],
        "score": chunk["score"],
        "preview": preview,
    }


if __name__ == "__main__":
    raise SystemExit(main())
