"""FastAPI approval service.

Exposes the governed runtime over HTTP:

* start runs and inspect their state,
* list/inspect the pending-approval queue (each item carries the full
  context packet a reviewer needs),
* approve / reject with reviewer identity and reason,
* sweep for timed-out approvals (escalation),
* query the audit log and verify its hash chain (tamper detection).

State is in-memory, which keeps the service trivially testable; the
runtime/audit layers are storage-agnostic and can be backed by a database
in a real deployment (see docs/architecture.md).
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from .models import ActionRequest, ApprovalStatus, AuditEventType
from .runtime import AgentRuntime, ApprovalError


class CreateRunRequest(BaseModel):
    goal: str
    steps: list[ActionRequest]


class ReviewRequest(BaseModel):
    reviewer: str = Field(..., min_length=1)
    reason: str = ""


def create_app(runtime: AgentRuntime | None = None) -> FastAPI:
    rt = runtime or AgentRuntime()
    app = FastAPI(
        title="Governed Agent — Human Approval & Audit Service",
        version="1.0.0",
        description="Agent runtime where consequential actions pause for "
                    "human approval and every step lands in a hash-chained "
                    "audit log.",
    )
    app.state.runtime = rt

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    # -- runs ------------------------------------------------------------
    @app.post("/runs", status_code=201)
    def create_run(body: CreateRunRequest) -> dict:
        run = rt.start_run(body.goal, body.steps)
        return run.model_dump(mode="json")

    @app.get("/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        try:
            return rt.get_run(run_id).model_dump(mode="json")
        except KeyError:
            raise HTTPException(404, f"unknown run: {run_id}")

    # -- approvals ---------------------------------------------------------
    @app.get("/approvals")
    def list_approvals(status: ApprovalStatus | None = None) -> list[dict]:
        return [a.model_dump(mode="json") for a in rt.list_approvals(status)]

    @app.get("/approvals/{approval_id}")
    def get_approval(approval_id: str) -> dict:
        try:
            return rt.get_approval(approval_id).model_dump(mode="json")
        except KeyError:
            raise HTTPException(404, f"unknown approval: {approval_id}")

    @app.post("/approvals/{approval_id}/approve")
    def approve(approval_id: str, body: ReviewRequest) -> dict:
        try:
            run = rt.approve(approval_id, body.reviewer, body.reason)
        except KeyError:
            raise HTTPException(404, f"unknown approval: {approval_id}")
        except ApprovalError as exc:
            raise HTTPException(409, str(exc))
        return run.model_dump(mode="json")

    @app.post("/approvals/{approval_id}/reject")
    def reject(approval_id: str, body: ReviewRequest) -> dict:
        try:
            run = rt.reject(approval_id, body.reviewer, body.reason)
        except KeyError:
            raise HTTPException(404, f"unknown approval: {approval_id}")
        except ApprovalError as exc:
            raise HTTPException(409, str(exc))
        return run.model_dump(mode="json")

    @app.post("/approvals/check-timeouts")
    def check_timeouts() -> dict:
        changed = rt.check_timeouts()
        return {"changed": [a.model_dump(mode="json") for a in changed]}

    # -- audit -------------------------------------------------------------
    @app.get("/audit")
    def query_audit(run_id: str | None = None,
                    event: AuditEventType | None = Query(default=None)) -> list[dict]:
        return [e.model_dump(mode="json") for e in rt.audit.entries(run_id, event)]

    @app.get("/audit/verify")
    def verify_audit() -> dict:
        return rt.audit.verify().model_dump(mode="json")

    return app


app = create_app()
