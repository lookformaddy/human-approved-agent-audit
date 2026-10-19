"""Governed agent runtime.

The runtime executes a planned list of actions step by step. Before each
step it asks the policy engine for a risk decision:

* LOW risk steps execute immediately.
* Anything above LOW creates an :class:`ApprovalRequest` containing a full
  context packet (what the action is, why the agent wants to do it, the
  expected effect, the risk tier, and the policy citations) and *pauses*
  the run. Nothing about the step has touched the outside world yet.

A reviewer then approves or rejects via the runtime (or the HTTP service):

* Approve -> the decision is audited and the paused run resumes from the
  exact step it stopped at.
* Reject  -> the decision is audited and the run aborts; remaining steps
  are never executed.

Pending approvals carry a TTL. :meth:`AgentRuntime.check_timeouts` moves a
timed-out approval to ESCALATED (a senior reviewer is paged and the clock
restarts once); a second timeout marks it EXPIRED and aborts the run, so a
consequential action can never slip through by being ignored.
"""
from __future__ import annotations

import time
from typing import Any, Callable

from .audit_log import AuditLog
from .models import (
    ActionRequest,
    ApprovalRequest,
    ApprovalStatus,
    AuditEventType,
    PolicyDecision,
    RiskTier,
    Run,
    RunStatus,
)
from .policy import PolicyEngine
from .tools import EnterpriseTools


class ApprovalError(Exception):
    """Raised when an approval operation is invalid for the current state."""


class AgentRuntime:
    def __init__(
        self,
        policy: PolicyEngine | None = None,
        tools: EnterpriseTools | None = None,
        audit: AuditLog | None = None,
        approval_ttl_seconds: float = 3600.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.policy = policy or PolicyEngine()
        self.tools = tools or EnterpriseTools()
        self._clock = clock
        self.audit = audit or AuditLog(clock=clock)
        self.approval_ttl_seconds = approval_ttl_seconds
        self.runs: dict[str, Run] = {}
        self.approvals: dict[str, ApprovalRequest] = {}

    # ------------------------------------------------------------------
    # run lifecycle
    # ------------------------------------------------------------------
    def start_run(self, goal: str, steps: list[ActionRequest]) -> Run:
        run = Run(goal=goal, steps=steps, created_at=self._clock())
        self.runs[run.id] = run
        self.audit.append(AuditEventType.RUN_STARTED, run.id,
                          {"goal": goal, "steps": len(steps)})
        self._execute(run)
        return run

    def get_run(self, run_id: str) -> Run:
        if run_id not in self.runs:
            raise KeyError(f"unknown run: {run_id}")
        return self.runs[run_id]

    def _execute(self, run: Run) -> Run:
        """Run steps from run.current_step until pause/completion/failure."""
        while run.current_step < len(run.steps):
            idx = run.current_step
            action = run.steps[idx]
            decision = self.policy.evaluate(action)
            self.audit.append(AuditEventType.POLICY_EVALUATED, run.id, {
                "step": idx, "tool": action.tool, "tier": decision.tier.value,
                "requires_approval": decision.requires_approval,
                "policy_citations": decision.policy_citations,
            })
            if decision.requires_approval and idx not in run.approved_steps:
                approval = self._request_approval(run, idx, action, decision)
                run.status = RunStatus.PAUSED
                self.audit.append(AuditEventType.RUN_PAUSED, run.id, {
                    "step": idx, "approval_id": approval.id,
                    "tier": decision.tier.value,
                })
                return run
            try:
                result = self.tools.call(action.tool, action.args)
            except Exception as exc:  # tool failure: audit and stop the run
                run.status = RunStatus.FAILED
                run.abort_reason = f"step {idx} ({action.tool}) failed: {exc}"
                run.finished_at = self._clock()
                self.audit.append(AuditEventType.STEP_FAILED, run.id, {
                    "step": idx, "tool": action.tool, "error": str(exc),
                })
                return run
            run.results.append({"step": idx, "tool": action.tool, "result": result})
            self.audit.append(AuditEventType.STEP_EXECUTED, run.id, {
                "step": idx, "tool": action.tool, "result": result,
            })
            run.current_step += 1
        run.status = RunStatus.COMPLETED
        run.finished_at = self._clock()
        self.audit.append(AuditEventType.RUN_COMPLETED, run.id,
                          {"steps_executed": len(run.results)})
        return run

    # ------------------------------------------------------------------
    # approvals
    # ------------------------------------------------------------------
    def _request_approval(self, run: Run, idx: int, action: ActionRequest,
                          decision: PolicyDecision) -> ApprovalRequest:
        now = self._clock()
        packet = {
            "what": f"Step {idx + 1} of {len(run.steps)}: call tool '{action.tool}' "
                    f"with arguments {action.args}",
            "why": action.rationale or "(agent gave no rationale)",
            "run_goal": run.goal,
            "expected_effect": self.tools.describe_effect(action.tool, action.args),
            "risk_tier": decision.tier.value,
            "senior_review_recommended": decision.tier in (RiskTier.HIGH, RiskTier.CRITICAL),
            "policy_reasons": decision.reasons,
            "policy_citations": decision.policy_citations,
            "prior_steps_completed": [r["tool"] for r in run.results],
        }
        approval = ApprovalRequest(
            run_id=run.id,
            step_index=idx,
            action=action,
            decision=decision,
            context_packet=packet,
            created_at=now,
            expires_at=now + self.approval_ttl_seconds,
        )
        self.approvals[approval.id] = approval
        self.audit.append(AuditEventType.APPROVAL_REQUESTED, run.id, {
            "approval_id": approval.id, "step": idx, "tool": action.tool,
            "tier": decision.tier.value, "expires_at": approval.expires_at,
        })
        return approval

    def get_approval(self, approval_id: str) -> ApprovalRequest:
        if approval_id not in self.approvals:
            raise KeyError(f"unknown approval: {approval_id}")
        return self.approvals[approval_id]

    def list_approvals(self, status: ApprovalStatus | None = None) -> list[ApprovalRequest]:
        out = list(self.approvals.values())
        if status is not None:
            out = [a for a in out if a.status == status]
        return sorted(out, key=lambda a: a.created_at)

    def approve(self, approval_id: str, reviewer: str, reason: str = "") -> Run:
        """Record an approval and resume the paused run."""
        approval = self.get_approval(approval_id)
        if approval.status not in (ApprovalStatus.PENDING, ApprovalStatus.ESCALATED):
            raise ApprovalError(
                f"approval {approval_id} is {approval.status.value}; cannot approve")
        run = self.get_run(approval.run_id)
        if run.status != RunStatus.PAUSED:
            raise ApprovalError(f"run {run.id} is not paused")
        approval.status = ApprovalStatus.APPROVED
        approval.reviewer = reviewer
        approval.review_reason = reason
        approval.decided_at = self._clock()
        self.audit.append(AuditEventType.APPROVAL_GRANTED, run.id, {
            "approval_id": approval.id, "step": approval.step_index,
            "reviewer": reviewer, "reason": reason,
        })
        run.approved_steps.append(approval.step_index)
        run.status = RunStatus.RUNNING
        self.audit.append(AuditEventType.RUN_RESUMED, run.id,
                          {"step": approval.step_index})
        return self._execute(run)

    def reject(self, approval_id: str, reviewer: str, reason: str = "") -> Run:
        """Record a rejection and abort the run; later steps never execute."""
        approval = self.get_approval(approval_id)
        if approval.status not in (ApprovalStatus.PENDING, ApprovalStatus.ESCALATED):
            raise ApprovalError(
                f"approval {approval_id} is {approval.status.value}; cannot reject")
        run = self.get_run(approval.run_id)
        approval.status = ApprovalStatus.REJECTED
        approval.reviewer = reviewer
        approval.review_reason = reason
        approval.decided_at = self._clock()
        self.audit.append(AuditEventType.APPROVAL_REJECTED, run.id, {
            "approval_id": approval.id, "step": approval.step_index,
            "reviewer": reviewer, "reason": reason,
        })
        run.status = RunStatus.ABORTED
        run.abort_reason = (f"step {approval.step_index} ({approval.action.tool}) "
                            f"rejected by {reviewer}: {reason or 'no reason given'}")
        run.finished_at = self._clock()
        self.audit.append(AuditEventType.RUN_ABORTED, run.id,
                          {"reason": run.abort_reason})
        return run

    # ------------------------------------------------------------------
    # timeouts & escalation
    # ------------------------------------------------------------------
    def check_timeouts(self) -> list[ApprovalRequest]:
        """Escalate or expire overdue pending approvals. Returns those changed."""
        now = self._clock()
        changed: list[ApprovalRequest] = []
        for approval in self.approvals.values():
            if approval.status not in (ApprovalStatus.PENDING, ApprovalStatus.ESCALATED):
                continue
            if now <= approval.expires_at:
                continue
            if approval.status == ApprovalStatus.PENDING:
                approval.status = ApprovalStatus.ESCALATED
                approval.escalation_level += 1
                approval.expires_at = now + self.approval_ttl_seconds
                self.audit.append(AuditEventType.APPROVAL_ESCALATED, approval.run_id, {
                    "approval_id": approval.id,
                    "escalation_level": approval.escalation_level,
                    "new_expires_at": approval.expires_at,
                    "note": "no decision within TTL; senior reviewer paged",
                })
                changed.append(approval)
            else:  # ESCALATED and timed out again -> expire, abort the run
                approval.status = ApprovalStatus.EXPIRED
                self.audit.append(AuditEventType.APPROVAL_EXPIRED, approval.run_id, {
                    "approval_id": approval.id,
                })
                run = self.runs.get(approval.run_id)
                if run is not None and run.status == RunStatus.PAUSED:
                    run.status = RunStatus.ABORTED
                    run.abort_reason = (f"approval {approval.id} expired without a "
                                        f"human decision; step {approval.step_index} "
                                        f"was never executed")
                    run.finished_at = now
                    self.audit.append(AuditEventType.RUN_ABORTED, run.id,
                                      {"reason": run.abort_reason})
                changed.append(approval)
        return changed
