"""Local autostop rules. Not part of Telegram Ads API — the bot watches metrics and pauses."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ads_api.models import Ad

METRICS = ("views", "clicks", "actions", "spent", "daily_spent")

METRIC_CODE = {
    "v": "views",
    "c": "clicks",
    "a": "actions",
    "s": "spent",
    "d": "daily_spent",
}
METRIC_TO_CODE = {name: code for code, name in METRIC_CODE.items()}

METRIC_RU = {
    "views": "показы",
    "clicks": "клики",
    "actions": "действия",
    "spent": "траты",
    "daily_spent": "дневной расход",
}

STATUS_RU = {
    "active": "активно",
    "fired": "сработало",
    "disabled": "выкл",
}

PRESETS: dict[str, list[float]] = {
    "views": [100, 500, 1000, 5000],
    "clicks": [10, 50, 100, 500],
    "actions": [5, 10, 25, 100],
    "spent": [0.5, 1, 5, 10],
    "daily_spent": [0.5, 1, 5, 10],
}

NEAR_ABS = {
    "views": 80,
    "clicks": 12,
    "actions": 4,
    "spent": 0.15,
    "daily_spent": 0.15,
}

POLL_MIN = 2.0
POLL_NEAR = 1.0
POLL_MAX = 25.0
POLL_IDLE = 40.0
NEAR_PCT = 0.08


@dataclass(slots=True)
class AdRule:
    id: int
    ad_id: int
    account_id: str | None
    chat_id: int
    user_id: int
    metric: str
    threshold: float
    action: str = "pause"
    status: str = "active"
    created_at: int = 0
    fired_at: int | None = None
    fired_value: float | None = None
    last_value: float | None = None
    last_checked_at: int | None = None


def metric_value(ad: Ad, metric: str) -> float:
    if metric == "views":
        return float(ad.views or 0)
    if metric == "clicks":
        return float(ad.clicks or 0)
    if metric == "actions":
        return float(ad.actions or 0)
    if metric == "spent":
        return float(ad.spent_budget or 0)
    if metric == "daily_spent":
        return float(ad.daily_spent_budget or 0)
    raise ValueError(f"unknown metric {metric}")


def crossed(metric: str, threshold: float, value: float) -> bool:
    if metric in {"spent", "daily_spent"}:
        return value + 1e-9 >= threshold
    return value >= threshold


def can_pause(ad: Ad) -> bool:
    status = str(ad.status or "").rsplit(".", 1)[-1]
    return status == "active" and not bool(ad.is_paused)


def poll_interval(
    *,
    metric: str,
    threshold: float,
    value: float,
    last_value: float | None,
    dt: float | None,
) -> float:
    """Shorter sleep as the metric approaches the limit so autostop overshoots less."""
    if threshold <= 0:
        return POLL_MIN
    remaining = threshold - value
    if remaining <= 0:
        return POLL_NEAR
    near_abs = NEAR_ABS.get(metric, threshold * NEAR_PCT)
    close = remaining <= near_abs or remaining <= threshold * NEAR_PCT
    speed = 0.0
    if last_value is not None and dt and dt > 0:
        speed = max(0.0, (value - last_value) / dt)
    if speed > 0:
        eta = remaining / speed
        if close:
            return _clamp(eta / 3.0, POLL_NEAR, POLL_MIN * 1.5)
        return _clamp(eta / 4.0, POLL_MIN, POLL_MAX)
    if close:
        return POLL_MIN
    frac = min(1.0, remaining / threshold)
    return _clamp(POLL_MIN + frac * (POLL_MAX - POLL_MIN), POLL_MIN, POLL_MAX)


def format_metric(metric: str, value: float, currency: str | None = None) -> str:
    from bot.formatters import money

    if metric in {"spent", "daily_spent"}:
        return money(value, currency)
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:g}"


def rule_title(rule: AdRule) -> str:
    name = METRIC_RU.get(rule.metric, rule.metric)
    return f"{name} ≥ {format_metric(rule.metric, rule.threshold)}"


def utc_day(ts: int) -> int:
    return int(ts) // 86400


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _row(row: sqlite3.Row) -> AdRule:
    return AdRule(
        id=row["id"],
        ad_id=row["ad_id"],
        account_id=row["account_id"],
        chat_id=row["chat_id"],
        user_id=row["user_id"],
        metric=row["metric"],
        threshold=row["threshold"],
        action=row["action"],
        status=row["status"],
        created_at=row["created_at"],
        fired_at=row["fired_at"],
        fired_value=row["fired_value"],
        last_value=row["last_value"],
        last_checked_at=row["last_checked_at"],
    )


class RuleStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ad_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ad_id INTEGER NOT NULL,
                    account_id TEXT,
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    metric TEXT NOT NULL,
                    threshold REAL NOT NULL,
                    action TEXT NOT NULL DEFAULT 'pause',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at INTEGER NOT NULL,
                    fired_at INTEGER,
                    fired_value REAL,
                    last_value REAL,
                    last_checked_at INTEGER
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ad_rules_status ON ad_rules(status, ad_id)"
            )
            conn.commit()

    async def add(
        self,
        *,
        ad_id: int,
        account_id: str | None,
        chat_id: int,
        user_id: int,
        metric: str,
        threshold: float,
    ) -> AdRule:
        if metric not in METRICS:
            raise ValueError(f"unknown metric {metric}")
        if threshold <= 0:
            raise ValueError("threshold must be > 0")
        now = int(time.time())
        async with self._lock:
            rule_id = await asyncio.to_thread(
                self._insert,
                ad_id,
                account_id,
                chat_id,
                user_id,
                metric,
                float(threshold),
                now,
            )
        rule = await self.get(rule_id)
        assert rule is not None
        return rule

    def _insert(
        self,
        ad_id: int,
        account_id: str | None,
        chat_id: int,
        user_id: int,
        metric: str,
        threshold: float,
        created_at: int,
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO ad_rules (
                    ad_id, account_id, chat_id, user_id, metric, threshold, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (ad_id, account_id, chat_id, user_id, metric, threshold, created_at),
            )
            conn.commit()
            return int(cur.lastrowid)

    async def get(self, rule_id: int) -> AdRule | None:
        async with self._lock:
            return await asyncio.to_thread(self._get, rule_id)

    def _get(self, rule_id: int) -> AdRule | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM ad_rules WHERE id=?", (rule_id,)).fetchone()
            return _row(row) if row else None

    async def list_by_ad(self, ad_id: int) -> list[AdRule]:
        async with self._lock:
            return await asyncio.to_thread(self._list, "ad_id=?", (ad_id,))

    async def list_all(self) -> list[AdRule]:
        async with self._lock:
            return await asyncio.to_thread(self._list, "1=1", ())

    async def list_active(self) -> list[AdRule]:
        async with self._lock:
            return await asyncio.to_thread(self._list, "status='active'", ())

    async def list_fired(self) -> list[AdRule]:
        async with self._lock:
            return await asyncio.to_thread(self._list, "status='fired'", ())

    def _list(self, where: str, args: tuple[Any, ...]) -> list[AdRule]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM ad_rules WHERE {where} ORDER BY id DESC",
                args,
            ).fetchall()
            return [_row(row) for row in rows]

    async def record_check(self, rule_id: int, value: float, checked_at: int) -> None:
        async with self._lock:
            await asyncio.to_thread(self._record_check, rule_id, value, checked_at)

    def _record_check(self, rule_id: int, value: float, checked_at: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE ad_rules SET last_value=?, last_checked_at=? WHERE id=?",
                (value, checked_at, rule_id),
            )
            conn.commit()

    async def mark_fired(self, rule_id: int, value: float, fired_at: int) -> None:
        async with self._lock:
            await asyncio.to_thread(self._mark_fired, rule_id, value, fired_at)

    def _mark_fired(self, rule_id: int, value: float, fired_at: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE ad_rules
                SET status='fired', fired_at=?, fired_value=?, last_value=?, last_checked_at=?
                WHERE id=?
                """,
                (fired_at, value, value, fired_at, rule_id),
            )
            conn.commit()

    async def set_status(self, rule_id: int, status: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._set_status, rule_id, status)

    def _set_status(self, rule_id: int, status: str) -> None:
        with self._connect() as conn:
            if status == "active":
                conn.execute(
                    """
                    UPDATE ad_rules
                    SET status='active', fired_at=NULL, fired_value=NULL
                    WHERE id=?
                    """,
                    (rule_id,),
                )
            else:
                conn.execute("UPDATE ad_rules SET status=? WHERE id=?", (status, rule_id))
            conn.commit()

    async def delete(self, rule_id: int) -> None:
        async with self._lock:
            await asyncio.to_thread(self._delete, rule_id)

    def _delete(self, rule_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM ad_rules WHERE id=?", (rule_id,))
            conn.commit()

    async def rearm_daily(self, now: int) -> int:
        async with self._lock:
            return await asyncio.to_thread(self._rearm_daily, now)

    def _rearm_daily(self, now: int) -> int:
        today = utc_day(now)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, fired_at FROM ad_rules WHERE metric='daily_spent' AND status='fired'"
            ).fetchall()
            count = 0
            for row in rows:
                fired_at = row["fired_at"]
                if fired_at is None or utc_day(int(fired_at)) < today:
                    conn.execute(
                        """
                        UPDATE ad_rules
                        SET status='active', fired_at=NULL, fired_value=NULL
                        WHERE id=?
                        """,
                        (row["id"],),
                    )
                    count += 1
            conn.commit()
            return count
