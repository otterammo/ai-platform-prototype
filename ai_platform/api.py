from __future__ import annotations

import asyncio
import os
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException, Query, Request, Response
from pydantic import BaseModel

from .controllers import ControlPlane
from .knowledge import DEFAULT_INDEX_NAME, KeywordRetriever, KnowledgeIndexer
from .observability import build_timeline, build_trace
from .policy import ApprovalService
from .resources import ResourceKind, parse_resource_documents
from .storage import DEFAULT_DB_URL, ResourceStore


class ApprovalDecisionRequest(BaseModel):
    actor: str = "manual"
    reason: str | None = None


def create_app(
    database_url: str | None = None,
    platform_root: str | None = None,
) -> FastAPI:
    store = ResourceStore(
        database_url or os.environ.get("AI_PLATFORM_DB", DEFAULT_DB_URL),
        platform_root or os.environ.get("AI_PLATFORM_ROOT", ".platform"),
    )
    control_plane = ControlPlane(store)
    app = FastAPI(title="AI Platform Prototype", version="0.1.0")
    app.state.store = store
    app.state.control_plane = control_plane

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/resources/apply")
    async def apply_resource(request: Request) -> dict[str, Any]:
        body = await request.body()
        if not body:
            raise HTTPException(status_code=400, detail="request body is required")
        content_type = request.headers.get("content-type", "")
        try:
            if "yaml" in content_type or "text/plain" in content_type:
                resources = parse_resource_documents(body.decode("utf-8"))
                applied = [store.apply(resource.model_dump(mode="json", exclude_none=True)) for resource in resources]
                return {"items": applied}
            raw = await request.json()
            if "manifest" in raw:
                raw = raw["manifest"]
            return store.apply(raw)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/resources")
    async def list_resources(
        kind: ResourceKind | None = None,
        namespace: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        return {"items": store.list(kind, namespace)}

    @app.get("/resources/{kind}/{name}")
    async def get_resource(kind: ResourceKind, name: str, namespace: str | None = None) -> dict[str, Any]:
        resource = store.get(kind, name, namespace)
        if resource is None:
            raise HTTPException(status_code=404, detail="resource not found")
        return resource

    @app.delete("/resources/{kind}/{name}", status_code=204)
    async def delete_resource(kind: ResourceKind, name: str, namespace: str | None = None) -> Response:
        if not store.delete(kind, name, namespace):
            raise HTTPException(status_code=404, detail="resource not found")
        return Response(status_code=204)

    @app.post("/reconcile")
    async def reconcile() -> dict[str, list[dict[str, Any]]]:
        results = await control_plane.reconcile_once()
        return {"controllers": [result.__dict__ for result in results]}

    @app.post("/controllers/run")
    async def run_controllers(intervalSeconds: float = Query(default=2.0, ge=0.1)) -> dict[str, str]:
        if getattr(app.state, "controller_task", None):
            return {"status": "already-running"}
        app.state.controller_task = asyncio.create_task(control_plane.run_forever(intervalSeconds))
        return {"status": "started"}

    @app.get("/events")
    async def list_events(
        namespace: str | None = None,
        resourceKind: ResourceKind | None = None,
        resourceName: str | None = None,
        correlationId: str | None = None,
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> dict[str, list[dict[str, Any]]]:
        return {"items": store.list_events(namespace, resourceKind, resourceName, limit, correlationId)}

    @app.get("/knowledge")
    async def list_knowledge(namespace: str | None = None) -> dict[str, list[dict[str, Any]]]:
        return {"items": store.list(ResourceKind.KNOWLEDGE, namespace)}

    @app.get("/knowledge/search")
    async def search_knowledge(
        namespace: str,
        query: str,
        index: str = DEFAULT_INDEX_NAME,
        limit: int = Query(default=10, ge=1, le=100),
    ) -> dict[str, list[dict[str, Any]]]:
        KnowledgeIndexer(store).ensure_indexed(namespace, index)
        results = KeywordRetriever(store).retrieve(namespace, index, query, limit=limit)
        return {"items": [_search_result(item) for item in results]}

    @app.get("/knowledge/indexes")
    async def list_knowledge_indexes(namespace: str | None = None) -> dict[str, list[dict[str, Any]]]:
        return {"items": store.list(ResourceKind.KNOWLEDGE_INDEX, namespace)}

    @app.get("/contexts/{mission}")
    async def get_context(mission: str, namespace: str) -> dict[str, Any]:
        context = store.get(ResourceKind.CONTEXT, mission, namespace)
        if context is None:
            raise HTTPException(status_code=404, detail="context not found")
        return context

    @app.get("/approvals")
    async def list_approvals() -> dict[str, list[dict[str, Any]]]:
        return {"items": store.list(ResourceKind.APPROVAL)}

    @app.get("/approvals/{approval_id}")
    async def get_approval(approval_id: str) -> dict[str, Any]:
        approval = store.get(ResourceKind.APPROVAL, approval_id)
        if approval is None:
            raise HTTPException(status_code=404, detail="approval not found")
        return approval

    @app.post("/approvals/{approval_id}/approve")
    async def approve_approval(
        approval_id: str,
        request: ApprovalDecisionRequest | None = None,
    ) -> dict[str, Any]:
        decision = request or ApprovalDecisionRequest()
        try:
            approval = ApprovalService(store).approve(approval_id, actor=decision.actor, reason=decision.reason)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        results = await control_plane.reconcile_once()
        return {"approval": approval, "controllers": [result.__dict__ for result in results]}

    @app.post("/approvals/{approval_id}/reject")
    async def reject_approval(
        approval_id: str,
        request: ApprovalDecisionRequest | None = None,
    ) -> dict[str, Any]:
        decision = request or ApprovalDecisionRequest()
        try:
            approval = ApprovalService(store).reject(approval_id, actor=decision.actor, reason=decision.reason)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        results = await control_plane.reconcile_once()
        return {"approval": approval, "controllers": [result.__dict__ for result in results]}

    @app.get("/trace/{mission}")
    async def trace_mission(mission: str, namespace: str) -> dict[str, Any]:
        trace = build_trace(store, mission, namespace)
        if trace is None:
            raise HTTPException(status_code=404, detail="mission not found")
        return trace

    @app.get("/timeline/{mission}")
    async def timeline_mission(mission: str, namespace: str) -> dict[str, Any]:
        timeline = build_timeline(store, mission, namespace)
        if timeline is None:
            raise HTTPException(status_code=404, detail="mission not found")
        return timeline

    @app.get("/artifacts")
    async def list_artifacts(
        namespace: str | None = None,
        mission: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        return {"items": store.list_artifacts(namespace, mission)}

    @app.get("/openapi.yaml", include_in_schema=False)
    async def openapi_yaml() -> Response:
        return Response(yaml.safe_dump(app.openapi(), sort_keys=False), media_type="application/yaml")

    return app


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


app = create_app()
