"""Append-only, hash-chained audit log.

Every runtime event (policy decisions, approvals, executions, aborts) is
appended as an entry whose SHA-256 hash covers the previous entry's hash
plus the canonical serialization of the new entry. Any later edit,
deletion, or reordering breaks the chain, and :meth:`AuditLog.verify`
reports the sequence number of the first broken entry.

This is "immutable-style": the class exposes no mutation API, entries are
returned as copies, and tampering is *detectable* even if someone edits an
underlying exported copy.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any, Callable

from .models import AuditEntry, AuditEventType, VerificationResult

GENESIS_HASH = "0" * 64


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _compute_hash(prev_hash: str, seq: int, timestamp: float, event: str,
                  run_id: str | None, data: dict[str, Any]) -> str:
    body = _canonical({
        "seq": seq,
        "timestamp": timestamp,
        "event": event,
        "run_id": run_id,
        "data": data,
    })
    return hashlib.sha256((prev_hash + body).encode("utf-8")).hexdigest()


class AuditLog:
    """Thread-safe append-only log with a verifiable hash chain."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._entries: list[AuditEntry] = []
        self._lock = threading.Lock()
        self._clock = clock

    # -- writing ---------------------------------------------------------
    def append(self, event: AuditEventType, run_id: str | None,
               data: dict[str, Any] | None = None) -> AuditEntry:
        with self._lock:
            seq = len(self._entries)
            prev_hash = self._entries[-1].hash if self._entries else GENESIS_HASH
            ts = self._clock()
            payload = dict(data or {})
            digest = _compute_hash(prev_hash, seq, ts, event.value, run_id, payload)
            entry = AuditEntry(
                seq=seq,
                timestamp=ts,
                event=event,
                run_id=run_id,
                data=payload,
                prev_hash=prev_hash,
                hash=digest,
            )
            self._entries.append(entry)
            return entry.model_copy(deep=True)

    # -- reading ---------------------------------------------------------
    def entries(self, run_id: str | None = None,
                event: AuditEventType | None = None) -> list[AuditEntry]:
        with self._lock:
            out = [e.model_copy(deep=True) for e in self._entries]
        if run_id is not None:
            out = [e for e in out if e.run_id == run_id]
        if event is not None:
            out = [e for e in out if e.event == event]
        return out

    def __len__(self) -> int:
        return len(self._entries)

    # -- verification ----------------------------------------------------
    def verify(self) -> VerificationResult:
        """Recompute the whole chain; report the first broken entry, if any."""
        with self._lock:
            snapshot = [e.model_copy(deep=True) for e in self._entries]
        prev_hash = GENESIS_HASH
        for expected_seq, entry in enumerate(snapshot):
            if entry.seq != expected_seq:
                return VerificationResult(
                    valid=False, entries_checked=len(snapshot),
                    first_bad_seq=entry.seq,
                    detail=f"sequence gap: expected {expected_seq}, found {entry.seq}",
                )
            if entry.prev_hash != prev_hash:
                return VerificationResult(
                    valid=False, entries_checked=len(snapshot),
                    first_bad_seq=entry.seq,
                    detail="prev_hash does not match the previous entry's hash",
                )
            digest = _compute_hash(entry.prev_hash, entry.seq, entry.timestamp,
                                   entry.event.value, entry.run_id, entry.data)
            if digest != entry.hash:
                return VerificationResult(
                    valid=False, entries_checked=len(snapshot),
                    first_bad_seq=entry.seq,
                    detail="entry content does not match its recorded hash",
                )
            prev_hash = entry.hash
        return VerificationResult(
            valid=True, entries_checked=len(snapshot),
            detail="hash chain intact",
        )

    # -- serialization ---------------------------------------------------
    def to_dicts(self) -> list[dict[str, Any]]:
        return [e.model_dump(mode="json") for e in self.entries()]

    @classmethod
    def from_dicts(cls, dicts: list[dict[str, Any]],
                   clock: Callable[[], float] = time.time) -> "AuditLog":
        """Rebuild a log from exported dicts *without* re-hashing, so that
        verify() can detect any tampering in the exported copy."""
        log = cls(clock=clock)
        log._entries = [AuditEntry.model_validate(d) for d in dicts]
        return log
