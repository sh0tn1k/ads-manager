"""Repositories over the section 6 schema. Upserts are idempotent."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ads_api.models import Account, Ad, AdStatItem
from agent.canonical import canonical_json, enum_value
from agent.store.db import Database

LIVE_AD_STATUSES = ("active", "on_hold", "in_review", "ready_for_review")


@dataclass(frozen=True)
class AccountSnapshot:
    ts: int
    account_id: str | None
    currency: str
    spent_budget: float
    remaining_budget: float
    ads_budget: float


class AccountSnapshotRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def latest(self) -> AccountSnapshot | None:
        row = self.db.fetchone("SELECT * FROM account_snapshots ORDER BY ts DESC, id DESC LIMIT 1")
        if row is None:
            return None
        return AccountSnapshot(
            ts=row["ts"],
            account_id=row["account_id"],
            currency=row["currency"],
            spent_budget=row["spent_budget"],
            remaining_budget=row["remaining_budget"],
            ads_budget=row["ads_budget"],
        )

    def add(self, account: Account, ts: int) -> bool:
        """Insert a snapshot unless it equals the latest one. Returns True if inserted."""
        last = self.latest()
        values = (
            account.account_id,
            enum_value(account.currency) or "",
            float(account.spent_budget),
            float(account.remaining_budget),
            float(account.ads_budget),
        )
        if last is not None and (
            last.account_id,
            last.currency,
            last.spent_budget,
            last.remaining_budget,
            last.ads_budget,
        ) == values:
            return False
        self.db.execute(
            "INSERT INTO account_snapshots(ts, account_id, currency, spent_budget, remaining_budget, ads_budget)"
            " VALUES(?,?,?,?,?,?)",
            (ts, *values),
        )
        return True

    def count(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM account_snapshots"))


def ad_raw(ad: Ad) -> dict[str, Any]:
    return ad.model_dump(mode="json", exclude_none=True)


class AdRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert(
        self,
        ad: Ad,
        ts: int,
        *,
        hypothesis_id: str | None = None,
        proposal_id: str | None = None,
        fingerprint: str | None = None,
    ) -> None:
        target = ad.target.model_dump(mode="json", exclude_none=True) if ad.target is not None else None
        self.db.execute(
            """
            INSERT INTO ads(ad_id, title, text, placement, target_type, target_json, cpm, button, status,
                            created_date, hypothesis_id, proposal_id, fingerprint, last_synced_ts,
                            action_type, promote_url, raw_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(ad_id) DO UPDATE SET
                title=excluded.title, text=excluded.text, placement=excluded.placement,
                target_type=COALESCE(excluded.target_type, ads.target_type),
                target_json=COALESCE(excluded.target_json, ads.target_json),
                cpm=excluded.cpm, button=excluded.button, status=excluded.status,
                created_date=excluded.created_date,
                hypothesis_id=COALESCE(excluded.hypothesis_id, ads.hypothesis_id),
                proposal_id=COALESCE(excluded.proposal_id, ads.proposal_id),
                fingerprint=COALESCE(excluded.fingerprint, ads.fingerprint),
                last_synced_ts=excluded.last_synced_ts, action_type=excluded.action_type,
                promote_url=excluded.promote_url, raw_json=excluded.raw_json
            """,
            (
                ad.ad_id,
                ad.title,
                ad.text,
                enum_value(ad.placement),
                enum_value(target.get("type")) if target else None,
                canonical_json(target) if target is not None else None,
                float(ad.cpm),
                enum_value(ad.button),
                enum_value(ad.status),
                ad.created_date,
                hypothesis_id,
                proposal_id,
                fingerprint,
                ts,
                enum_value(ad.action_type),
                ad.promote_url,
                canonical_json(ad_raw(ad)),
            ),
        )

    def get(self, ad_id: int) -> Ad | None:
        row = self.db.fetchone("SELECT raw_json FROM ads WHERE ad_id=?", (ad_id,))
        return Ad.model_validate(json.loads(row["raw_json"])) if row else None

    def row(self, ad_id: int) -> dict[str, Any] | None:
        row = self.db.fetchone("SELECT * FROM ads WHERE ad_id=?", (ad_id,))
        return dict(row) if row else None

    def all(self) -> list[Ad]:
        rows = self.db.fetchall("SELECT raw_json FROM ads ORDER BY ad_id")
        return [Ad.model_validate(json.loads(r["raw_json"])) for r in rows]

    def rows(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.fetchall("SELECT * FROM ads ORDER BY ad_id")]

    def live_remaining_total(self) -> float:
        marks = ",".join("?" for _ in LIVE_AD_STATUSES)
        value = self.db.scalar(
            f"SELECT COALESCE(SUM(json_extract(raw_json, '$.remaining_budget')), 0) FROM ads WHERE status IN ({marks})",
            LIVE_AD_STATUSES,
        )
        return float(value or 0.0)

    def live_ids(self) -> list[int]:
        marks = ",".join("?" for _ in LIVE_AD_STATUSES)
        rows = self.db.fetchall(f"SELECT ad_id FROM ads WHERE status IN ({marks}) ORDER BY ad_id", LIVE_AD_STATUSES)
        return [int(r["ad_id"]) for r in rows]

    def count(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM ads"))


_SNAPSHOT_FIELDS = (
    "status",
    "is_paused",
    "remaining_budget",
    "spent_budget",
    "views",
    "opens",
    "clicks",
    "actions",
    "decline_reason_json",
)


class AdSnapshotRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def _values(ad: Ad) -> tuple:
        decline = canonical_json(ad.decline_reason) if ad.decline_reason is not None else None
        return (
            enum_value(ad.status),
            None if ad.is_paused is None else int(bool(ad.is_paused)),
            float(ad.remaining_budget),
            float(ad.spent_budget),
            int(ad.views),
            ad.opens,
            ad.clicks,
            int(ad.actions),
            decline,
        )

    def add(self, ad: Ad, ts: int) -> bool:
        values = self._values(ad)
        last = self.db.fetchone(
            f"SELECT {', '.join(_SNAPSHOT_FIELDS)} FROM ad_snapshots WHERE ad_id=? ORDER BY ts DESC, id DESC LIMIT 1",
            (ad.ad_id,),
        )
        if last is not None and tuple(last) == values:
            return False
        self.db.execute(
            f"INSERT INTO ad_snapshots(ad_id, ts, {', '.join(_SNAPSHOT_FIELDS)}) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (ad.ad_id, ts, *values),
        )
        return True

    def for_ad(self, ad_id: int) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.fetchall("SELECT * FROM ad_snapshots WHERE ad_id=? ORDER BY ts, id", (ad_id,))]

    def count(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM ad_snapshots"))


class AdStatsRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert(self, ad_id: int, interval: int, item: AdStatItem) -> None:
        self.db.execute(
            """
            INSERT INTO ad_stats(ad_id, from_time, interval, to_time, views, opens, clicks, actions, spent_budget)
            VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(ad_id, from_time, interval) DO UPDATE SET
                to_time=excluded.to_time, views=excluded.views, opens=excluded.opens,
                clicks=excluded.clicks, actions=excluded.actions, spent_budget=excluded.spent_budget
            """,
            (
                ad_id,
                item.from_time,
                interval,
                item.to_time,
                item.views,
                item.opens,
                item.clicks,
                item.actions,
                float(item.spent_budget),
            ),
        )

    def for_ad(self, ad_id: int, interval: int) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self.db.fetchall(
                "SELECT * FROM ad_stats WHERE ad_id=? AND interval=? ORDER BY from_time", (ad_id, interval)
            )
        ]

    def totals(self, ad_id: int, interval: int) -> dict[str, float]:
        row = self.db.fetchone(
            "SELECT COALESCE(SUM(views),0) v, COALESCE(SUM(clicks),0) c, COALESCE(SUM(actions),0) a,"
            " COALESCE(SUM(spent_budget),0) s FROM ad_stats WHERE ad_id=? AND interval=?",
            (ad_id, interval),
        )
        return {"views": row["v"], "clicks": row["c"], "actions": row["a"], "spent_budget": row["s"]}

    def last_from_time(self, ad_id: int, interval: int) -> int | None:
        value = self.db.scalar(
            "SELECT MAX(from_time) FROM ad_stats WHERE ad_id=? AND interval=?", (ad_id, interval)
        )
        return None if value is None else int(value)

    def count(self) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM ad_stats"))


# Statuses that hold a budget reservation (see docs/agent-questions.md #11).
RESERVING_STATUSES = ("proposed", "accepted")


class ReservationQueries:
    """Read side of reservations; the write side lives with proposals (phase 2)."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def reserved_total(self, *, exclude_proposal_id: str | None = None) -> float:
        marks = ",".join("?" for _ in RESERVING_STATUSES)
        sql = f"SELECT COALESCE(SUM(reserved_amount), 0) FROM proposals WHERE status IN ({marks})"
        params: tuple = RESERVING_STATUSES
        if exclude_proposal_id is not None:
            sql += " AND proposal_id != ?"
            params = (*params, exclude_proposal_id)
        return float(self.db.scalar(sql, params) or 0.0)

    def count_by_status(self) -> dict[str, int]:
        rows = self.db.fetchall("SELECT status, COUNT(*) n FROM proposals GROUP BY status")
        return {r["status"]: int(r["n"]) for r in rows}
