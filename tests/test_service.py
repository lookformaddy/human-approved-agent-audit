"""HTTP service: approval queue, decisions, audit query/verify endpoints."""
import pytest
from fastapi.testclient import TestClient

from governed_agent.runtime import AgentRuntime
from governed_agent.service import create_app


@pytest.fixture()
def client():
    return TestClient(create_app(AgentRuntime(approval_ttl_seconds=600.0)))


def _gated_run(client) -> dict:
    resp = client.post("/runs", json={
        "goal": "upgrade plan",
        "steps": [
            {"tool": "create_ticket", "args": {"title": "case"}, "rationale": "intake"},
            {"tool": "update_record",
             "args": {"record_id": "CUST-1001", "field": "plan", "value": "premium"},
             "rationale": "customer asked for premium"},
        ],
    })
    assert resp.status_code == 201
    return resp.json()


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_auto_run_completes_via_api(client):
    resp = client.post("/runs", json={
        "goal": "notify",
        "steps": [{"tool": "send_notification",
                   "args": {"recipient": "a@b.c", "message": "hi"}}],
    })
    body = resp.json()
    assert body["status"] == "completed"
    assert client.get("/approvals", params={"status": "pending"}).json() == []


def test_approval_queue_and_approve_flow(client):
    run = _gated_run(client)
    assert run["status"] == "paused"

    queue = client.get("/approvals", params={"status": "pending"}).json()
    assert len(queue) == 1
    item = queue[0]
    assert item["context_packet"]["risk_tier"] == "medium"
    assert item["context_packet"]["policy_citations"]

    resp = client.post(f"/approvals/{item['id']}/approve",
                       json={"reviewer": "sam.reviewer", "reason": "verified"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"
    assert client.get("/approvals", params={"status": "pending"}).json() == []


def test_reject_flow_via_api(client):
    run = _gated_run(client)
    approval_id = client.get("/approvals").json()[0]["id"]
    resp = client.post(f"/approvals/{approval_id}/reject",
                       json={"reviewer": "pat", "reason": "no"})
    assert resp.json()["status"] == "aborted"
    # second decision conflicts
    resp2 = client.post(f"/approvals/{approval_id}/approve",
                        json={"reviewer": "pat", "reason": "changed my mind"})
    assert resp2.status_code == 409


def test_unknown_ids_return_404(client):
    assert client.get("/runs/nope").status_code == 404
    assert client.get("/approvals/nope").status_code == 404
    assert client.post("/approvals/nope/approve",
                       json={"reviewer": "x"}).status_code == 404


def test_audit_query_and_verify_endpoints(client):
    run = _gated_run(client)
    entries = client.get("/audit", params={"run_id": run["id"]}).json()
    assert entries, "audit entries must exist for the run"
    assert all(e["run_id"] == run["id"] for e in entries)
    events = {e["event"] for e in entries}
    assert {"run_started", "approval_requested", "run_paused"} <= events

    verify = client.get("/audit/verify").json()
    assert verify["valid"] is True
    assert verify["entries_checked"] == len(client.get("/audit").json())


def test_check_timeouts_endpoint(client):
    _gated_run(client)
    resp = client.post("/approvals/check-timeouts")
    assert resp.status_code == 200
    assert resp.json()["changed"] == []  # nothing overdue yet
