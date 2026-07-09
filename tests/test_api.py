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
