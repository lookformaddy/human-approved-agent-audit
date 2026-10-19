"""Policy engine: risk tiers, thresholds, escalation rules, fail-closed."""
import pytest

from governed_agent.models import ActionRequest, RiskTier
from governed_agent.policy import PolicyEngine


@pytest.fixture()
def engine() -> PolicyEngine:
    return PolicyEngine()


def act(tool, **args):
    return ActionRequest(tool=tool, args=args)


@pytest.mark.parametrize("tool,args", [
    ("create_ticket", {"title": "x"}),
    ("send_notification", {"recipient": "a@b.c", "message": "hi"}),
    ("issue_refund", {"order_id": "ORD-1", "amount": 10.0}),
    ("issue_refund", {"order_id": "ORD-1", "amount": 49.99}),
])
def test_low_risk_auto_runs(engine, tool, args):
    decision = engine.evaluate(act(tool, **args))
    assert decision.tier == RiskTier.LOW
    assert decision.requires_approval is False


@pytest.mark.parametrize("tool,args,expected", [
    ("update_record", {"record_id": "CUST-1", "field": "plan", "value": "pro"}, RiskTier.MEDIUM),
    ("issue_refund", {"order_id": "ORD-1", "amount": 50.0}, RiskTier.MEDIUM),
    ("issue_refund", {"order_id": "ORD-1", "amount": 120.0}, RiskTier.MEDIUM),
    ("issue_refund", {"order_id": "ORD-1", "amount": 250.0}, RiskTier.HIGH),
    ("issue_refund", {"order_id": "ORD-1", "amount": 999.99}, RiskTier.HIGH),
    ("issue_refund", {"order_id": "ORD-1", "amount": 1000.0}, RiskTier.CRITICAL),
    ("issue_refund", {"order_id": "ORD-1", "amount": 5000.0}, RiskTier.CRITICAL),
    ("export_data", {"dataset": "customers"}, RiskTier.HIGH),
    ("delete_record", {"record_id": "CUST-1"}, RiskTier.HIGH),
    ("delete_record", {"record_id": "CUST-1", "bulk": True}, RiskTier.CRITICAL),
    ("delete_record", {"record_id": "CUST-1", "count": 5}, RiskTier.CRITICAL),
    ("send_notification", {"recipient": "x@y.z", "message": "m", "audience": "external"}, RiskTier.MEDIUM),
])
def test_gated_tiers(engine, tool, args, expected):
    decision = engine.evaluate(act(tool, **args))
    assert decision.tier == expected
    assert decision.requires_approval is True
    assert decision.policy_citations, "every gated decision must cite policy"


def test_sensitive_field_escalates_update(engine):
    decision = engine.evaluate(act(
        "update_record", record_id="CUST-1", field="billing_address", value="1 Main St"))
    assert decision.tier == RiskTier.HIGH
    assert "POL-DATA-02" in decision.policy_citations


def test_pii_flag_escalates_one_tier(engine):
    decision = engine.evaluate(act(
        "update_record", record_id="CUST-1", field="plan", value="pro",
        contains_pii=True))
    assert decision.tier == RiskTier.HIGH  # MEDIUM + PII -> HIGH
    assert "POL-DATA-03" in decision.policy_citations


def test_unknown_tool_fails_closed(engine):
    decision = engine.evaluate(act("wire_money_anywhere", amount=1))
    assert decision.tier == RiskTier.CRITICAL
    assert decision.requires_approval is True
    assert "POL-GEN-00" in decision.policy_citations


def test_reasons_explain_the_tier(engine):
    decision = engine.evaluate(act("issue_refund", order_id="ORD-1", amount=300.0))
    assert any("POL-FIN-02" in r for r in decision.reasons)
