# Architecture

## Overview

The system is a small, layered Python service. The core idea: **an agent may
plan anything, but it may only *execute* what policy allows** — and every
decision, human or machine, is written to a tamper-evident log before the
next step happens.

```
            +----------------------+
 plan ----> |      AgentRuntime    | ----> EnterpriseTools (synthetic)
            |  policy gate per     |        tickets / records / refunds /
            |  step; pause/resume  |        notifications / exports
            +----+-----------+-----+
                 |           |
        PolicyEngine    Approval queue
        (risk tiers)    (context packets)
                 |           |
            +----v-----------v-----+
            |  AuditLog            |
            |  append-only,        |
            |  SHA-256 hash chain  |
            +----------------------+
                 ^
            FastAPI service (service.py) exposes all of it over HTTP
```

## Components

### `AgentRuntime` (`runtime.py`)
Owns run state machines and approval state machines.

Run lifecycle: `running -> paused -> running -> completed`, with terminal
branches to `aborted` (rejection, expiry) and `failed` (tool error).
Execution is a loop over plan steps; a gated step records
`current_step` and returns *before* the tool is invoked, so a paused step
has zero side effects. Approval marks the step as approved and re-enters
the same loop, which is what makes resume exact rather than approximate.

### `PolicyEngine` (`policy.py`)
Pure function from `ActionRequest` to `PolicyDecision`. Starts from a
per-tool base tier, then applies deterministic rules:

| Rule | Citation | Effect |
| --- | --- | --- |
| Unknown tool | POL-GEN-00 | CRITICAL (fail closed) |
| Base tool tier | POL-GEN-01 | catalog default |
| Refund >= 50 / 250 / 1000 | POL-FIN-01/02/03 | MEDIUM / HIGH / CRITICAL |
| Refund < 50 | POL-FIN-00 | LOW (routine goodwill) |
| Sensitive field update | POL-DATA-02 | at least HIGH |
| Payload flagged PII | POL-DATA-03 | escalate one tier |
| Bulk delete | POL-DATA-05 | CRITICAL |
| External notification | POL-COM-01 | at least MEDIUM |

Every fired rule contributes a reason string and a citation; both are
stored in the approval packet and the audit log.

### Approval queue (`runtime.py` + `service.py`)
Each pending item carries a **context packet**:

- `what` — the exact tool call and arguments;
- `why` — the agent's stated rationale for the step;
- `expected_effect` — a plain-language description from the tool catalog;
- `risk_tier` + `senior_review_recommended`;
- `policy_reasons` / `policy_citations` — why the step was gated;
- `prior_steps_completed` — what the run has already done;
- `run_goal` — the overall objective the step serves.

Decisions require a reviewer identity and accept a reason; both are
written to the audit log. Double decisions are rejected with a conflict.

**Timeouts & escalation.** A pending approval has a TTL (configurable,
default 1h). `check_timeouts()` — callable from the API, a cron job, or a
background task — moves an overdue item to `escalated` (senior reviewer
paged, clock restarts once). A second timeout moves it to `expired` and
aborts the run: **silence can never become consent.**

### `AuditLog` (`audit_log.py`)
Append-only list of `AuditEntry` records. Entry *n* stores
`hash = SHA-256(prev_hash || canonical(seq, timestamp, event, run_id, data))`
with a genesis `prev_hash` of 64 zero characters. `verify()` replays the
chain and returns the sequence number of the first entry whose content,
hash, linkage, or ordering was altered — which detects edits, deletions,
and reordering in any exported copy (`to_dicts` / `from_dicts` round-trip
preserves hashes precisely so verification stays meaningful).

### `EnterpriseTools` (`tools.py`)
In-memory stand-ins for enterprise systems, with fabricated data only:
tickets, customer/order records, notifications, refunds, exports. Each
tool exposes `describe_effect()` text used in context packets. In a real
deployment this is the only layer that changes — it becomes adapters to
real systems behind the same `call(tool, args)` interface.

### FastAPI service (`service.py`)
Thin HTTP facade: run creation/inspection, the approval queue and
decision endpoints, a timeout sweep endpoint, and audit query/verify
endpoints. State lives in the runtime instance; swapping in a database
means replacing the runtime's dicts, not the API.

## Trust boundaries & guarantees

- **No side effects before approval.** The tool layer is only invoked
  after the policy gate passes or a human approves.
- **Fail closed.** Unknown tools classify CRITICAL; expired approvals
  abort runs; tool exceptions fail the run and are audited, never
  swallowed.
- **Reviewer accountability.** Approvals and rejections are meaningless
  without identity, so the API requires a non-empty `reviewer`.
- **Tamper evidence, not tamper proofing.** The hash chain makes edits
  to history detectable. Anchoring the chain head to an external system
  (e.g., periodically publishing the latest hash) would make them
  unforgeable as well — see the roadmap in the README.
