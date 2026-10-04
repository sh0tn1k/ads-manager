"""Proposal persistence. Every status change is a conditional UPDATE (compare-and-set)."""

from __future__ import annotations

import json
from typing import Any

from agent.store.db import Database
from agent.proposals.models import (
    RESERVING,
    Proposal,
    ProposalPayload,
    ProposalStatus,
    can_transition,
    payload_hash,
    payload_json,
    reserve_amount,
)


class ReservationExceeded(Exception):
    def __init__(self, needed: float, available: float) -> None:
        super().__init__(f"reservation {needed} exceeds available {available}")
        self.needed = needed
        self.available = available


class InvalidTransition(Exception):
    pass


_RESERVING_SQL = ",".join(f"'{s.value}'" for s in sorted(RESERVING))


class ProposalRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    # --- reads ---

    def get(self, proposal_id: str) -> Proposal | None:
        row = self.db.fetchone("SELECT * FROM proposals WHERE proposal_id=?", (proposal_id,))
        return Proposal.from_row(row) if row else None

    def by_status(self, *statuses: ProposalStatus | str) -> list[Proposal]:
        marks = ",".join("?" for _ in statuses)
        rows = self.db.fetchall(
            f"SELECT * FROM proposals WHERE status IN ({marks}) ORDER BY created_ts, proposal_id",
            tuple(str(getattr(s, "value", s)) for s in statuses),
        )
        return [Proposal.from_row(r) for r in rows]

    def all(self) -> list[Proposal]:
        return [Proposal.from_row(r) for r in self.db.fetchall("SELECT * FROM proposals ORDER BY created_ts")]

    def reserved_total(self, *, exclude_proposal_id: str | None = None) -> float:
        sql = f"SELECT COALESCE(SUM(reserved_amount),0) FROM proposals WHERE status IN ({_RESERVING_SQL})"
        params: tuple = ()
        if exclude_proposal_id:
            sql += " AND proposal_id != ?"
            params = (exclude_proposal_id,)
        return float(self.db.scalar(sql, params) or 0.0)

    def count_pending(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM proposals WHERE status='proposed'"))

    def needing_submit(self) -> list[Proposal]:
        rows = self.db.fetchall("SELECT * FROM proposals WHERE needs_submit=1 AND created_ad_id IS NOT NULL")
        return [Proposal.from_row(r) for r in rows]

    def fingerprints_since(self, since_ts: int, statuses: tuple[str, ...]) -> set[str]:
        marks = ",".join("?" for _ in statuses)
        rows = self.db.fetchall(
            f"SELECT fingerprint FROM proposals WHERE fingerprint IS NOT NULL AND status IN ({marks})"
            " AND COALESCE(decided_ts, created_ts) >= ?",
            (*statuses, since_ts),
        )
        return {r["fingerprint"] for r in rows}

    def events(self, proposal_id: str) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            "SELECT ts, event, details_json FROM proposal_events WHERE proposal_id=? ORDER BY id", (proposal_id,)
        )
        return [
            {"ts": r["ts"], "event": r["event"], "details": json.loads(r["details_json"]) if r["details_json"] else None}
            for r in rows
        ]

    # --- writes ---

    def add_event(self, proposal_id: str, ts: int, event: str, details: dict[str, Any] | None = None) -> None:
        self.db.execute(
            "INSERT INTO proposal_events(proposal_id, ts, event, details_json) VALUES(?,?,?,?)",
            (proposal_id, ts, event, json.dumps(details, ensure_ascii=False, sort_keys=True) if details else None),
        )

    def insert_reserving(
        self,
        *,
        proposal_id: str,
        payload: ProposalPayload,
        now: int,
        ttl_seconds: int,
        fingerprint: str | None,
        free_before_reserves: float,
    ) -> Proposal:
        """Insert a proposal and its reservation atomically (I3).

        ``free_before_reserves`` = free balance with no proposal reservations subtracted. Inside one
        IMMEDIATE transaction the existing reservations are summed, so concurrent inserts cannot
        oversubscribe the balance.
        """
        amount = reserve_amount(payload)
        with self.db.transaction() as conn:
            reserved = float(
                conn.execute(
                    f"SELECT COALESCE(SUM(reserved_amount),0) FROM proposals WHERE status IN ({_RESERVING_SQL})"
                ).fetchone()[0]
            )
            available = round(free_before_reserves - reserved, 5)
            if amount > available + 1e-9:
                raise ReservationExceeded(amount, available)
            conn.execute(
                """
                INSERT INTO proposals(proposal_id, created_ts, expires_ts, status, kind, hypothesis_id, stage,
                                      payload_json, payload_hash, reserved_amount, fingerprint)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    proposal_id,
                    now,
                    now + ttl_seconds,
                    ProposalStatus.PROPOSED.value,
                    payload.kind.value,
                    payload.hypothesis_id,
                    None if payload.stage is None else str(payload.stage),
                    payload_json(payload),
                    payload_hash(payload),
                    amount,
                    fingerprint,
                ),
            )
            conn.execute(
                "INSERT INTO proposal_events(proposal_id, ts, event, details_json) VALUES(?,?,?,?)",
                (proposal_id, now, "proposed", json.dumps({"reserved": amount})),
            )
        proposal = self.get(proposal_id)
        assert proposal is not None
        return proposal

    def try_accept(self, proposal_id: str, user_id: int, now: int) -> bool:
        """Section 11 step 2: atomic proposed → accepted, only before expiry (double-click guard)."""
        cur = self.db.execute(
            "UPDATE proposals SET status='accepted', decided_by=?, decided_ts=?"
            " WHERE proposal_id=? AND status='proposed' AND expires_ts>?",
            (user_id, now, proposal_id, now),
        )
        if cur.rowcount == 1:
            self.add_event(proposal_id, now, "accepted", {"user_id": user_id})
            return True
        return False

    def transition(
        self,
        proposal_id: str,
        src: ProposalStatus,
        dst: ProposalStatus,
        now: int,
        *,
        event: str | None = None,
        details: dict[str, Any] | None = None,
        **fields: Any,
    ) -> bool:
        if not can_transition(src, dst):
            raise InvalidTransition(f"{src} -> {dst}")
        allowed = {
            "decided_by",
            "decided_ts",
            "reject_reason",
            "reject_reason_code",
            "created_ad_id",
            "error",
            "needs_submit",
            "dry_run",
            "executed_ts",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unknown fields {unknown}")
        sets = ["status=?"] + [f"{k}=?" for k in fields]
        params = [dst.value, *fields.values(), proposal_id, src.value]
        cur = self.db.execute(
            f"UPDATE proposals SET {', '.join(sets)} WHERE proposal_id=? AND status=?", tuple(params)
        )
        if cur.rowcount == 1:
            self.add_event(proposal_id, now, event or dst.value, details)
            return True
        return False

    def update_fields(self, proposal_id: str, **fields: Any) -> None:
        allowed = {"error", "needs_submit", "reject_reason_code", "created_ad_id", "executed_ts"}
        if set(fields) - allowed:
            raise ValueError("field not updatable")
        sets = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE proposals SET {sets} WHERE proposal_id=?", (*fields.values(), proposal_id))

    def expire_due(self, now: int) -> list[str]:
        rows = self.db.fetchall(
            "SELECT proposal_id FROM proposals WHERE status='proposed' AND expires_ts<=?", (now,)
        )
        expired = []
        for row in rows:
            if self.transition(row["proposal_id"], ProposalStatus.PROPOSED, ProposalStatus.EXPIRED, now):
                expired.append(row["proposal_id"])
        return expired

    def stored_hash_matches(self, proposal: Proposal) -> bool:
        """Recompute the hash from the stored payload JSON (detects tampering of either column)."""
        try:
            return payload_hash(proposal.payload) == proposal.payload_hash
        except Exception:  # noqa: BLE001 - unparsable payload is tampering too
            return False
