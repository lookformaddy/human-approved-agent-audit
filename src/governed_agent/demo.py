"""End-to-end demo: three flows through the governed runtime.

1. AUTO-APPROVED  - only low-risk steps; runs start to finish untouched.
2. HUMAN-APPROVED - a medium-risk record update pauses; a reviewer
                    approves and the run resumes and completes.
3. REJECTED       - a high-risk refund pauses; the reviewer rejects it and
                    the run aborts before any money moves.

Run it with:  python -m governed_agent.demo
"""
from __future__ import annotations

from .models import ActionRequest, RunStatus
from .runtime import AgentRuntime


def _banner(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def run_demo() -> AgentRuntime:
    runtime = AgentRuntime(approval_ttl_seconds=3600)

    _banner("FLOW 1 - AUTO-APPROVED (low risk only)")
    run1 = runtime.start_run(
        goal="Onboard a routine support case",
        steps=[
            ActionRequest(tool="create_ticket",
                          args={"title": "Customer cannot log in", "queue": "support",
                                "priority": "normal"},
                          rationale="Customer reported a login problem."),
            ActionRequest(tool="send_notification",
                          args={"recipient": "avery@example.invalid",
                                "message": "We opened ticket for your login issue.",
                                "channel": "email"},
                          rationale="Acknowledge the case to the customer."),
            ActionRequest(tool="issue_refund",
                          args={"order_id": "ORD-5001", "amount": 25.00,
                                "reason": "goodwill credit for the outage"},
                          rationale="Small goodwill refund under the auto-approval threshold."),
        ],
    )
    print(f"run {run1.id}: status={run1.status.value}, "
          f"steps executed={len(run1.results)} (no human involved)")

    _banner("FLOW 2 - HUMAN-APPROVED (sensitive update pauses, then resumes)")
    run2 = runtime.start_run(
        goal="Fix a customer's billing address and confirm by email",
        steps=[
            ActionRequest(tool="update_record",
                          args={"record_id": "CUST-1001", "field": "billing_address",
                                "value": "742 Evergreen Terrace"},
                          rationale="Customer moved; billing address must match the card issuer."),
            ActionRequest(tool="send_notification",
                          args={"recipient": "avery@example.invalid",
                                "message": "Your billing address was updated.",
                                "channel": "email"},
                          rationale="Confirm the change to the customer."),
        ],
    )
    pending = runtime.list_approvals()
    print(f"run {run2.id}: status={run2.status.value} - paused for approval")
    packet = pending[-1].context_packet
    print(f"  context packet: tier={packet['risk_tier']}, "
          f"citations={packet['policy_citations']}")
    print(f"  what: {packet['what']}")
    print(f"  why:  {packet['why']}")
    run2 = runtime.approve(pending[-1].id, reviewer="sam.reviewer",
                           reason="Verified the move with the customer on case T-1001.")
    print(f"  after approval: status={run2.status.value}, "
          f"steps executed={len(run2.results)}")

    _banner("FLOW 3 - REJECTED (high risk refund refused; run aborts)")
    run3 = runtime.start_run(
        goal="Refund a large order and notify the customer",
        steps=[
            ActionRequest(tool="issue_refund",
                          args={"order_id": "ORD-5001", "amount": 480.00,
                                "reason": "agent-suggested compensation"},
                          rationale="Agent proposed a large goodwill refund."),
            ActionRequest(tool="send_notification",
                          args={"recipient": "avery@example.invalid",
                                "message": "Your refund is on its way.",
                                "channel": "email"},
                          rationale="Tell the customer the refund was issued."),
        ],
    )
    pending = [a for a in runtime.list_approvals()
               if a.run_id == run3.id and a.status.value == "pending"]
    print(f"run {run3.id}: status={run3.status.value} - paused for approval "
          f"(tier={pending[0].decision.tier.value})")
    run3 = runtime.reject(pending[0].id, reviewer="pat.financelead",
                          reason="No matching service failure on this order; "
                                 "refund not justified.")
    print(f"  after rejection: status={run3.status.value}")
    print(f"  abort reason: {run3.abort_reason}")
    print(f"  refunds actually issued by the tools layer: "
          f"{len(runtime.tools.refunds)} (only flow 1's small refund)")

    _banner("AUDIT LOG")
    result = runtime.audit.verify()
    print(f"entries={result.entries_checked}, chain valid={result.valid} "
          f"({result.detail})")
    assert run1.status == RunStatus.COMPLETED
    assert run2.status == RunStatus.COMPLETED
    assert run3.status == RunStatus.ABORTED
    assert result.valid
    print("Demo finished: all three flows behaved as governed.")
    return runtime


if __name__ == "__main__":
    run_demo()
