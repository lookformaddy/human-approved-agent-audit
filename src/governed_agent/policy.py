"""Risk-tier policy engine.

Every planned action is classified into a risk tier before it may run:

* LOW      -> executes automatically.
* MEDIUM   -> pauses for a human approval.
* HIGH     -> pauses for a human approval (senior reviewer recommended).
* CRITICAL -> pauses for a human approval; flagged as critical in the packet.

Classification starts from a per-tool base tier and is then adjusted by
deterministic rules (amount thresholds, sensitive fields, PII flags, bulk
operations). Every rule that fires contributes a human-readable reason and
a policy citation (e.g. ``POL-FIN-02``) that travels into the approval
context packet and the audit log, so a reviewer can see *why* the action
was gated, not just that it was.
"""
from __future__ import annotations

from .models import ActionRequest, PolicyDecision, RiskTier, escalate_tier, max_tier

BASE_TIERS: dict[str, RiskTier] = {
    "create_ticket": RiskTier.LOW,
    "send_notification": RiskTier.LOW,
    "update_record": RiskTier.MEDIUM,
    "issue_refund": RiskTier.MEDIUM,
    "export_data": RiskTier.HIGH,
    "delete_record": RiskTier.HIGH,
}

SENSITIVE_FIELDS = {
    "payment_method",
    "billing_address",
    "account_status",
    "password",
    "ssn",
    "salary",
}


class PolicyEngine:
    """Deterministic, explainable risk classification for agent actions."""

    def evaluate(self, action: ActionRequest) -> PolicyDecision:
        reasons: list[str] = []
        citations: list[str] = []

        if action.tool not in BASE_TIERS:
            # Fail closed: an unregistered tool is never auto-executed.
            return PolicyDecision(
                tier=RiskTier.CRITICAL,
                requires_approval=True,
                reasons=[f"tool '{action.tool}' is not in the registered tool catalog"],
                policy_citations=["POL-GEN-00"],
            )

        tier = BASE_TIERS[action.tool]
        reasons.append(f"base tier for {action.tool} is {tier.value} (POL-GEN-01)")
        citations.append("POL-GEN-01")
        args = action.args

        # -- financial thresholds (POL-FIN-*) -----------------------------
        if action.tool == "issue_refund":
            amount = float(args.get("amount", 0) or 0)
            if amount >= 1000:
                tier = max_tier(tier, RiskTier.CRITICAL)
                reasons.append(f"refund amount {amount:.2f} >= 1000.00 (POL-FIN-03)")
                citations.append("POL-FIN-03")
            elif amount >= 250:
                tier = max_tier(tier, RiskTier.HIGH)
                reasons.append(f"refund amount {amount:.2f} >= 250.00 (POL-FIN-02)")
                citations.append("POL-FIN-02")
            elif amount >= 50:
                tier = max_tier(tier, RiskTier.MEDIUM)
                reasons.append(f"refund amount {amount:.2f} >= 50.00 (POL-FIN-01)")
                citations.append("POL-FIN-01")
            else:
                tier = RiskTier.LOW
                reasons.append(f"refund amount {amount:.2f} < 50.00: routine goodwill refund (POL-FIN-00)")
                citations.append("POL-FIN-00")

        # -- data sensitivity (POL-DATA-*) --------------------------------
        if action.tool == "update_record" and args.get("field") in SENSITIVE_FIELDS:
            tier = max_tier(tier, RiskTier.HIGH)
            reasons.append(f"field '{args.get('field')}' is classified sensitive (POL-DATA-02)")
            citations.append("POL-DATA-02")

        if action.tool == "delete_record" and (args.get("bulk") or int(args.get("count", 1) or 1) > 1):
            tier = RiskTier.CRITICAL
            reasons.append("bulk record deletion is irreversible at scale (POL-DATA-05)")
            citations.append("POL-DATA-05")

        if args.get("contains_pii"):
            tier = escalate_tier(tier)
            reasons.append("action payload is flagged as containing PII: tier escalated one level (POL-DATA-03)")
            citations.append("POL-DATA-03")

        if action.tool == "send_notification" and args.get("audience") == "external":
            tier = max_tier(tier, RiskTier.MEDIUM)
            reasons.append("notification leaves the company boundary (POL-COM-01)")
            citations.append("POL-COM-01")

        return PolicyDecision(
            tier=tier,
            requires_approval=tier != RiskTier.LOW,
            reasons=reasons,
            policy_citations=sorted(set(citations)),
        )
