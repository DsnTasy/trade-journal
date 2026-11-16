import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.core import approvals
from app.core.db import make_session_factory
from app.core.models import ActionType, ApprovalStatus


@pytest.fixture
def client(settings, engine):
    return TestClient(create_app(settings, engine))


@pytest.mark.parametrize("path", ["/", "/products", "/approvals", "/logs", "/api/status"])
def test_pages_load(client, path):
    assert client.get(path).status_code == 200


def test_dry_run_banner_shown(client):
    assert "DRY-RUN MODE" in client.get("/").text


def test_approve_button_flow(client, engine):
    with make_session_factory(engine)() as s:
        req = approvals.request_approval(s, action_type=ActionType.SEND_EMAIL, title="Email supplier")
    assert "Email supplier" in client.get("/approvals").text

    resp = client.post(f"/approvals/{req.id}/approve", headers={"HX-Request": "true"})
    assert resp.status_code == 200 and "approved" in resp.text
    with make_session_factory(engine)() as s:
        assert s.get(type(req), req.id).status == ApprovalStatus.APPROVED


def test_decision_without_dashboard_header_is_refused(client, engine):
    with make_session_factory(engine)() as s:
        req = approvals.request_approval(s, action_type=ActionType.SEND_EMAIL, title="x")
    assert client.post(f"/approvals/{req.id}/approve").status_code == 403


def test_status_api(client):
    data = client.get("/api/status").json()
    assert data["dry_run"] is True and data["pending_approvals"] == 0
