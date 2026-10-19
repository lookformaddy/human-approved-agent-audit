"""Core data models for the governed agent runtime.

All models are plain pydantic types so they serialize cleanly to the audit
log, the API, and JSON exports. No employer-specific concepts appear here:
the domain is a generic enterprise (tickets, customer records, refunds,
notifications, data exports) with fully synthetic data.
"""
from __future__ import annotations

import enum
import uuid
from typing import Any

from pydantic import BaseModel, Field


class RiskTier(str, enum.Enum):
    """Ordered risk tiers. Anything above LOW requires a human decision."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


TIER_ORDER: list[RiskTier] = [
    RiskTier.LOW,
    RiskTier.MEDIUM,
    RiskTier.HIGH,
    RiskTier.CRITICAL,
]


def max_tier(a: RiskTier, b: RiskTier) -> RiskTier:
    return a if TIER_ORDER.index(a) >= TIER_ORDER.index(b) else b


def escalate_tier(tier: RiskTier, steps: int = 1) -> RiskTier:
    idx = min(TIER_ORDER.index(tier) + steps, len(TIER_ORDER) - 1)
    return TIER_ORDER[idx]


class ActionRequest(BaseModel):
    """One planned step: a tool call plus the agent's stated rationale."""

    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""


class PolicyDecision(BaseModel):
    """Output of the policy engine for one action."""

    tier: RiskTier
    requires_approval: bool
    reasons: list[str] = Field(default_factory=list)
    policy_citations: list[str] = Field(default_factory=list)


class ApprovalStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    ESCALATED = "escalated"  # timed out once; a senior reviewer is paged
    EXPIRED = "expired"  # timed out again after escalation; run is aborted


class ApprovalRequest(BaseModel):
    """A paused step packaged for a human reviewer."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    run_id: str
    step_index: int
    action: ActionRequest
    decision: PolicyDecision
    context_packet: dict[str, Any] = Field(default_factory=dict)
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: float = 0.0
    expires_at: float = 0.0
    escalation_level: int = 0
    reviewer: str | None = None
    review_reason: str | None = None
    decided_at: float | None = None


class RunStatus(str, enum.Enum):
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    ABORTED = "aborted"
    FAILED = "failed"


class Run(BaseModel):
    """A single agent workflow execution."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    goal: str
    steps: list[ActionRequest]
    status: RunStatus = RunStatus.RUNNING
    current_step: int = 0
    approved_steps: list[int] = Field(default_factory=list)
    results: list[dict[str, Any]] = Field(default_factory=list)
    created_at: float = 0.0
    finished_at: float | None = None
    abort_reason: str | None = None


class AuditEventType(str, enum.Enum):
    RUN_STARTED = "run_started"
    POLICY_EVALUATED = "policy_evaluated"
    STEP_EXECUTED = "step_executed"
    STEP_FAILED = "step_failed"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_REJECTED = "approval_rejected"
    APPROVAL_ESCALATED = "approval_escalated"
    APPROVAL_EXPIRED = "approval_expired"
    RUN_PAUSED = "run_paused"
    RUN_RESUMED = "run_resumed"
    RUN_COMPLETED = "run_completed"
    RUN_ABORTED = "run_aborted"


class AuditEntry(BaseModel):
    """One hash-chained, append-only audit record."""

    seq: int
    timestamp: float
    event: AuditEventType
    run_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    prev_hash: str
    hash: str


class VerificationResult(BaseModel):
    valid: bool
    entries_checked: int
    first_bad_seq: int | None = None
    detail: str = ""
