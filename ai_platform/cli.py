from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import yaml

from .controllers import ControlPlane
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

    subparsers.add_parser("events", help="List recent events")

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
        import json

        print(json.dumps(data, indent=2))
        return
    print(yaml.safe_dump(data, sort_keys=False))


async def run_async(args: argparse.Namespace) -> int:
    store = make_store(args)
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
        if args.name is None:
            print_data({"items": store.list(args.kind, args.namespace)}, args.output)
            return 0
        resource = store.get(args.kind, args.name, args.namespace)
        if not resource:
            print(f"{args.kind} {args.name} not found", file=sys.stderr)
            return 1
        print_data(resource, args.output)
        return 0

    if args.command == "list":
        print_data({"items": store.list(args.kind, args.namespace)}, args.output)
        return 0

    if args.command == "describe":
        description = describe_resource(store, args.kind, args.name, args.namespace)
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

    if args.command == "events":
        print_data({"items": store.list_events()})
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

    if args.command == "serve":
        os.environ["AI_PLATFORM_DB"] = args.db
        os.environ["AI_PLATFORM_ROOT"] = args.root
        import uvicorn

        uvicorn.run("ai_platform.api:app", host=args.host, port=args.port, reload=False)
        return 0

    raise AssertionError(f"unhandled command {args.command}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(run_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
