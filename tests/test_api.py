from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from ai_platform.api import create_app
from ai_platform.resources import ResourceKind

from .test_policy import default_policy_rules, populate_governed_store


def test_api_approval_endpoints_approve_and_resume(tmp_path: Path) -> None:
    app = create_app(f"sqlite:///{tmp_path / 'platform.db'}", str(tmp_path / "platform"))
    populate_governed_store(app.state.store, tmp_path / "workspace", default_policy_rules())
    asyncio.run(app.state.control_plane.reconcile_once())
    approvals = app.state.store.list(ResourceKind.APPROVAL)
    assert len(approvals) == 1
    approval_name = approvals[0]["metadata"]["name"]

    with TestClient(app) as client:
        list_response = client.get("/approvals")
        assert list_response.status_code == 200
        assert list_response.json()["items"][0]["metadata"]["name"] == approval_name

        get_response = client.get(f"/approvals/{approval_name}")
        assert get_response.status_code == 200
        assert get_response.json()["status"]["phase"] == "Pending"

        approve_response = client.post(
            f"/approvals/{approval_name}/approve",
            json={"actor": "api", "reason": "approved from test"},
        )
        assert approve_response.status_code == 200
        assert approve_response.json()["approval"]["status"]["phase"] == "Approved"

    mission = app.state.store.get(ResourceKind.MISSION, "implement-auth", "demo")
    approval = app.state.store.get(ResourceKind.APPROVAL, approval_name)
    assert mission is not None
    assert mission["status"]["phase"] == "Completed"
    assert approval is not None
    assert approval["status"]["data"]["approvedBy"] == "api"


def test_api_approval_endpoint_rejects_pending_action(tmp_path: Path) -> None:
    app = create_app(f"sqlite:///{tmp_path / 'platform.db'}", str(tmp_path / "platform"))
    populate_governed_store(app.state.store, tmp_path / "workspace", default_policy_rules())
    asyncio.run(app.state.control_plane.reconcile_once())
    approval_name = app.state.store.list(ResourceKind.APPROVAL)[0]["metadata"]["name"]

    with TestClient(app) as client:
        reject_response = client.post(
            f"/approvals/{approval_name}/reject",
            json={"actor": "api", "reason": "rejected from test"},
        )
        assert reject_response.status_code == 200
        assert reject_response.json()["approval"]["status"]["phase"] == "Rejected"

    mission = app.state.store.get(ResourceKind.MISSION, "implement-auth", "demo")
    agent = app.state.store.get(ResourceKind.AGENT, "implement-auth-fleet-coder", "demo")
    assert mission is not None
    assert mission["status"]["phase"] == "Failed"
    assert agent is not None
    assert agent["status"]["phase"] == "Failed"


def test_api_knowledge_search_indexes_and_context_endpoint_returns_runtime_context(tmp_path: Path) -> None:
    app = create_app(f"sqlite:///{tmp_path / 'platform.db'}", str(tmp_path / "platform"))
    workspace_root = tmp_path / "workspace"
    knowledge_dir = workspace_root / "knowledge"
    knowledge_dir.mkdir(parents=True)
    (knowledge_dir / "prd.md").write_text("# PRD\n\nShip authentication.", encoding="utf-8")
    for manifest in [
        {
            "apiVersion": "ai.platform/v1",
            "kind": "Workspace",
            "metadata": {"name": "demo"},
            "spec": {"rootPath": str(workspace_root)},
        },
        {
            "apiVersion": "ai.platform/v1",
            "kind": "Knowledge",
            "metadata": {"name": "prd", "namespace": "demo"},
            "spec": {"type": "PRD", "ref": "knowledge://prd.md"},
        },
        {
            "apiVersion": "ai.platform/v1",
            "kind": "KnowledgeIndex",
            "metadata": {"name": "default", "namespace": "demo"},
            "spec": {"sources": ["knowledge://prd.md"]},
        },
        {
            "apiVersion": "ai.platform/v1",
            "kind": "Mission",
            "metadata": {"name": "build-auth", "namespace": "demo"},
            "spec": {"objective": "Build authentication", "brief": {"ref": "knowledge://prd.md"}},
        },
    ]:
        app.state.store.apply(manifest)

    with TestClient(app) as client:
        knowledge_response = client.get("/knowledge", params={"namespace": "demo"})
        assert knowledge_response.status_code == 200
        assert knowledge_response.json()["items"][0]["metadata"]["name"] == "prd"

        indexes_response = client.get("/knowledge/indexes", params={"namespace": "demo"})
        assert indexes_response.status_code == 200
        assert indexes_response.json()["items"][0]["metadata"]["name"] == "default"

        search_response = client.get(
            "/knowledge/search",
            params={"namespace": "demo", "query": "authentication"},
        )
        assert search_response.status_code == 200
        assert search_response.json()["items"][0]["document"] == "prd.md"

        reconcile_response = client.post("/reconcile")
        assert reconcile_response.status_code == 200

        runs_response = client.get("/agentruns", params={"namespace": "demo"})
        assert runs_response.status_code == 200
        assert runs_response.json()["items"][0]["kind"] == "AgentRun"

        context_response = client.get("/contexts/build-auth", params={"namespace": "demo"})
        assert context_response.status_code == 200
        context = context_response.json()
        assert context["kind"] == "Context"
        assert context["status"]["data"]["chunkCount"] == 1
        assert "Source: knowledge://prd.md" in context["status"]["data"]["renderedContext"]

        artifact_response = client.get("/artifact-resources", params={"namespace": "demo"})
        assert artifact_response.status_code == 200
        assert artifact_response.json()["items"][0]["kind"] == "Artifact"
