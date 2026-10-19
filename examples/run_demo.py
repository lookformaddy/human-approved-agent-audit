"""Walk the three governed flows over HTTP using FastAPI's TestClient.

Usage:  python examples/run_demo.py
(Equivalent to `python -m governed_agent.demo`, but through the API.)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient

from governed_agent.service import create_app

client = TestClient(create_app())

# 1. Auto-approved: only low-risk steps.
r = client.post("/runs", json={
    "goal": "Open a case and acknowledge it",
    "steps": [
        {"tool": "create_ticket", "args": {"title": "Login issue", "queue": "support"}},
        {"tool": "send_notification",
         "args": {"recipient": "avery@example.invalid", "message": "Case opened."}},
    ],
}).json()
print("auto flow ->", r["status"])

# 2. Human-approved: the record update pauses the run.
r = client.post("/runs", json={
    "goal": "Upgrade a customer's plan",
    "steps": [
        {"tool": "update_record",
         "args": {"record_id": "CUST-1002", "field": "plan", "value": "enterprise"},
         "rationale": "Customer signed an enterprise agreement."},
    ],
}).json()
print("gated flow ->", r["status"])
queue = client.get("/approvals", params={"status": "pending"}).json()
print("pending approval packet:", queue[0]["context_packet"]["what"])
r = client.post(f"/approvals/{queue[0]['id']}/approve",
                json={"reviewer": "sam.reviewer", "reason": "Agreement on file."}).json()
print("after approval ->", r["status"])

# 3. Audit: the whole story, verifiable.
v = client.get("/audit/verify").json()
print(f"audit chain valid={v['valid']} across {v['entries_checked']} entries")
