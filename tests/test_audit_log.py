"""Audit log: hash-chain integrity and tamper detection."""
from governed_agent.audit_log import AuditLog
from governed_agent.models import AuditEventType


def _populated_log() -> AuditLog:
    log = AuditLog()
    for i in range(6):
        log.append(AuditEventType.STEP_EXECUTED, "run-1", {"step": i})
    return log


def test_chain_verifies_when_untouched():
    result = _populated_log().verify()
    assert result.valid is True
    assert result.entries_checked == 6
    assert result.first_bad_seq is None


def test_empty_log_verifies():
    result = AuditLog().verify()
    assert result.valid is True
    assert result.entries_checked == 0


def test_tampered_entry_data_is_detected():
    dicts = _populated_log().to_dicts()
    dicts[3]["data"]["step"] = 999  # rewrite history
    result = AuditLog.from_dicts(dicts).verify()
    assert result.valid is False
    assert result.first_bad_seq == 3


def test_tampered_hash_is_detected():
    dicts = _populated_log().to_dicts()
    dicts[2]["hash"] = "f" * 64
    result = AuditLog.from_dicts(dicts).verify()
    assert result.valid is False
    assert result.first_bad_seq == 2


def test_deleted_entry_is_detected():
    dicts = _populated_log().to_dicts()
    del dicts[2]  # remove a middle entry
    result = AuditLog.from_dicts(dicts).verify()
    assert result.valid is False


def test_reordered_entries_are_detected():
    dicts = _populated_log().to_dicts()
    dicts[1], dicts[2] = dicts[2], dicts[1]
    result = AuditLog.from_dicts(dicts).verify()
    assert result.valid is False


def test_entries_have_monotonic_seq_and_linkage():
    log = _populated_log()
    entries = log.entries()
    assert [e.seq for e in entries] == list(range(6))
    assert entries[0].prev_hash == "0" * 64
    for prev, cur in zip(entries, entries[1:]):
        assert cur.prev_hash == prev.hash


def test_filter_by_run_and_event():
    log = AuditLog()
    log.append(AuditEventType.RUN_STARTED, "run-a", {})
    log.append(AuditEventType.RUN_STARTED, "run-b", {})
    log.append(AuditEventType.RUN_COMPLETED, "run-a", {})
    assert len(log.entries(run_id="run-a")) == 2
    assert len(log.entries(event=AuditEventType.RUN_COMPLETED)) == 1
