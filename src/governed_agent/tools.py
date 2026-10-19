"""Synthetic enterprise tools the agent may call.

These are in-memory stand-ins for the systems a real deployment would
integrate with (ticketing, CRM records, notifications, payments, data
export). All data is fabricated; nothing references any real company.
Each tool also knows how to describe its own expected effect, which the
runtime puts into the human approval context packet.
"""
from __future__ import annotations

import itertools
from typing import Any


class UnknownToolError(Exception):
    pass


_EFFECT_TEMPLATES: dict[str, str] = {
    "create_ticket": "Creates a new support ticket in queue '{queue}' with priority '{priority}'. No existing data is changed.",
    "send_notification": "Sends a {channel} notification to '{recipient}'. The message becomes visible to the recipient immediately.",
    "update_record": "Changes field '{field}' of record '{record_id}' to '{value}'. The previous value is overwritten.",
    "issue_refund": "Issues a refund of {amount} {currency} against order '{order_id}'. Money movement is initiated and cannot be undone by the agent.",
    "delete_record": "Permanently deletes record '{record_id}'. This cannot be undone.",
    "export_data": "Exports dataset '{dataset}' as {format}. The export leaves the governed environment.",
}


class EnterpriseTools:
    """A small, stateful, fully synthetic tool catalog."""

    def __init__(self) -> None:
        self.tickets: dict[str, dict[str, Any]] = {}
        self.records: dict[str, dict[str, Any]] = {
            "CUST-1001": {
                "record_id": "CUST-1001", "name": "Avery Example",
                "email": "avery@example.invalid", "account_status": "active",
                "billing_address": "100 Demo Street", "plan": "standard",
            },
            "CUST-1002": {
                "record_id": "CUST-1002", "name": "Jordan Sample",
                "email": "jordan@example.invalid", "account_status": "active",
                "billing_address": "200 Test Avenue", "plan": "premium",
            },
            "ORD-5001": {
                "record_id": "ORD-5001", "customer_id": "CUST-1001",
                "total": 129.99, "status": "delivered",
            },
        }
        self.notifications: list[dict[str, Any]] = []
        self.refunds: list[dict[str, Any]] = []
        self.exports: list[dict[str, Any]] = []
        self._ticket_seq = itertools.count(1001)
        self._refund_seq = itertools.count(501)
        self._export_seq = itertools.count(77)

    # -- dispatch --------------------------------------------------------
    def call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        handler = getattr(self, f"_tool_{tool}", None)
        if handler is None:
            raise UnknownToolError(f"unknown tool: {tool}")
        return handler(**args)

    def describe_effect(self, tool: str, args: dict[str, Any]) -> str:
        template = _EFFECT_TEMPLATES.get(tool, "Executes tool '{tool}'.")
        try:
            return template.format(tool=tool, **{k: args.get(k, "?") for k in
                                                 ("queue", "priority", "channel", "recipient",
                                                  "field", "record_id", "value", "amount",
                                                  "currency", "order_id", "dataset", "format")})
        except Exception:
            return template

    # -- tools -----------------------------------------------------------
    def _tool_create_ticket(self, title: str, queue: str = "general",
                            priority: str = "normal", **_: Any) -> dict[str, Any]:
        ticket_id = f"T-{next(self._ticket_seq)}"
        ticket = {"ticket_id": ticket_id, "title": title, "queue": queue,
                  "priority": priority, "status": "open"}
        self.tickets[ticket_id] = ticket
        return ticket

    def _tool_send_notification(self, recipient: str, message: str,
                                channel: str = "email", **_: Any) -> dict[str, Any]:
        note = {"notification_id": f"N-{len(self.notifications) + 1:04d}",
                "recipient": recipient, "channel": channel, "message": message,
                "status": "sent"}
        self.notifications.append(note)
        return note

    def _tool_update_record(self, record_id: str, field: str, value: Any,
                            **_: Any) -> dict[str, Any]:
        if record_id not in self.records:
            raise ValueError(f"record not found: {record_id}")
        record = self.records[record_id]
        old = record.get(field)
        record[field] = value
        return {"record_id": record_id, "field": field, "old_value": old,
                "new_value": value}

    def _tool_issue_refund(self, order_id: str, amount: float,
                           currency: str = "USD", reason: str = "",
                           **_: Any) -> dict[str, Any]:
        refund = {"refund_id": f"RF-{next(self._refund_seq)}",
                  "order_id": order_id, "amount": float(amount),
                  "currency": currency, "reason": reason, "status": "issued"}
        self.refunds.append(refund)
        return refund

    def _tool_delete_record(self, record_id: str, **_: Any) -> dict[str, Any]:
        if record_id not in self.records:
            raise ValueError(f"record not found: {record_id}")
        removed = self.records.pop(record_id)
        return {"record_id": record_id, "deleted": True,
                "tombstone": {"name": removed.get("name", removed.get("record_id"))}}

    def _tool_export_data(self, dataset: str, format: str = "csv",
                          **_: Any) -> dict[str, Any]:
        export = {"export_id": f"EX-{next(self._export_seq)}",
                  "dataset": dataset, "format": format, "status": "ready"}
        self.exports.append(export)
        return export
