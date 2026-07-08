from __future__ import annotations

import asyncio
import os
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException, Query, Request, Response

from .controllers import ControlPlane
from .resources import ResourceKind, parse_resource_documents
from .storage import DEFAULT_DB_URL, ResourceStore


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
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> dict[str, list[dict[str, Any]]]:
        return {"items": store.list_events(namespace, resourceKind, resourceName, limit)}

    @app.get("/artifacts")
    async def list_artifacts(namespace: str | None = None, mission: str | None = None) -> dict[str, list[dict[str, Any]]]:
        return {"items": store.list_artifacts(namespace, mission)}

    @app.get("/openapi.yaml", include_in_schema=False)
    async def openapi_yaml() -> Response:
        return Response(yaml.safe_dump(app.openapi(), sort_keys=False), media_type="application/yaml")

    return app


app = create_app()
