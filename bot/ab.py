"""Same-ad A/B: freeze variant A, then compare incremental stats after a change."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bot.formatters import money

RATE_METRICS: tuple[tuple[str, str, bool], ...] = (
    ("ctr", "CTR", False),
    ("cvr", "CVR", False),
    ("cpc", "CPC", True),
    ("cpa", "CPA", True),
    ("ecpm", "eCPM", True),
)

VOLUME_METRICS: tuple[tuple[str, str], ...] = (
    ("views", "Показы"),
    ("clicks", "Клики"),
    ("actions", "Действия"),
    ("spent", "Потрачено"),
)


@dataclass(slots=True)
class Variant:
    views: float
    clicks: float
    actions: float
    spent: float
    ctr: float | None
    cvr: float | None
    cpc: float | None
    cpa: float | None
    ecpm: float | None


@dataclass(slots=True)
class MetricRow:
    key: str
    label: str
    a: float | None
    b: float | None
    delta: float | None
    volume: bool


@dataclass(slots=True)
class AbCompare:
    a: Variant
    b: Variant
    rows: list[MetricRow]
    overall: float | None


@dataclass(slots=True)
class AbTest:
    id: int
    user_id: int
    chat_id: int
    account_id: str | None
    ad_id: int
    note: str
    status: str
    started_at: int
    ended_at: int | None
    baseline: dict[str, Any]
    followup: dict[str, Any] | None


def ad_blob(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {}
    ad = payload.get("ad")
    return ad if isinstance(ad, dict) else payload


def counts_of(ad: dict[str, Any]) -> dict[str, float]:
    clicks = ad.get("clicks")
    return {
        "views": float(ad.get("views") or 0),
        "clicks": float(clicks) if clicks is not None else 0.0,
        "actions": float(ad.get("actions") or 0),
        "spent": float(ad.get("spent_budget") or 0),
    }


def increment(current: dict[str, float], baseline: dict[str, float]) -> dict[str, float]:
    return {key: max(0.0, current[key] - baseline[key]) for key in baseline}


def variant_from_counts(counts: dict[str, float]) -> Variant:
    views = counts["views"]
    clicks = counts["clicks"]
    actions = counts["actions"]
    spent = counts["spent"]
    per_view = (spent / views) if views else None
    return Variant(
        views=views,
        clicks=clicks,
        actions=actions,
        spent=spent,
        ctr=(clicks / views) if views else None,
        cvr=(actions / clicks) if clicks else None,
        cpc=(spent / clicks) if clicks else None,
        cpa=(spent / actions) if actions else None,
        ecpm=(per_view * 1000) if per_view is not None else None,
    )


def signed_delta(a: float | None, b: float | None, *, lower_is_better: bool) -> float | None:
    """Percent where positive means B is better."""
    if a is None or b is None or a == 0:
        return None
    raw = (b - a) / abs(a) * 100
    return -raw if lower_is_better else raw


def compare_counts(a_counts: dict[str, float], b_counts: dict[str, float]) -> AbCompare:
    a = variant_from_counts(a_counts)
    b = variant_from_counts(b_counts)
    rows: list[MetricRow] = []
    for key, label in VOLUME_METRICS:
        rows.append(
            MetricRow(
                key=key,
                label=label,
                a=getattr(a, key),
                b=getattr(b, key),
                delta=None,
                volume=True,
            )
        )
    deltas: list[float] = []
    for key, label, lower in RATE_METRICS:
        a_val = getattr(a, key)
        b_val = getattr(b, key)
        delta = signed_delta(a_val, b_val, lower_is_better=lower)
        if delta is not None:
            deltas.append(delta)
        rows.append(MetricRow(key=key, label=label, a=a_val, b=b_val, delta=delta, volume=False))
    overall = (sum(deltas) / len(deltas)) if deltas else None
    return AbCompare(a=a, b=b, rows=rows, overall=overall)


def compare_payloads(baseline: dict[str, Any], current: dict[str, Any]) -> AbCompare:
    a_counts = counts_of(ad_blob(baseline))
    live = counts_of(ad_blob(current))
    return compare_counts(a_counts, increment(live, a_counts))


def format_delta(delta: float | None) -> str:
    if delta is None:
        return "—"
    sign = "+" if delta > 0 else ""
    return f"{sign}{delta:.1f}%"


def format_value(key: str, value: float | None, currency: str | None) -> str:
    if value is None:
        return "—"
    if key in {"ctr", "cvr"}:
        return f"{value * 100:.2f}%"
    if key in {"cpc", "cpa", "ecpm", "spent"}:
        return money(value, currency)
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:g}"


def verdict(overall: float | None, *, b_views: float) -> str:
    if b_views <= 0:
        return "После фиксации A новых показов ещё нет. Измените объявление и дождитесь открутки."
    if overall is None:
        return "Недостаточно пересекающихся метрик для общего процента."
    if overall > 0:
        return f"Общий процент: +{overall:.1f}%. Лучше новый вариант (B)."
    if overall < 0:
        return f"Общий процент: {overall:.1f}%. Лучше первый вариант (A)."
    return "Общий процент: 0%. Варианты равны."


def _nested_id(ad: dict[str, Any], field: str) -> str:
    item = ad.get(field)
    if isinstance(item, dict) and item.get(f"{field}_id"):
        return str(item[f"{field}_id"])
    if isinstance(item, dict) and item.get("photo_id"):
        return str(item["photo_id"])
    if isinstance(item, dict) and item.get("video_id"):
        return str(item["video_id"])
    return ""


def creative_changes(baseline: dict[str, Any], current: dict[str, Any]) -> list[str]:
    a = ad_blob(baseline)
    b = ad_blob(current)
    lines: list[str] = []
    pairs = (
        ("title", "название"),
        ("text", "текст"),
        ("cpm", "ставку CPM"),
        ("button", "кнопку"),
        ("promote_url", "URL"),
        ("website_name", "сайт"),
    )
    for key, label in pairs:
        left, right = a.get(key), b.get(key)
        if left != right:
            lines.append(f"изменили {label}")
    a_photo = _nested_id(a, "photo") or str(a.get("photo_id") or "")
    b_photo = _nested_id(b, "photo") or str(b.get("photo_id") or "")
    if a_photo != b_photo:
        if b_photo and not a_photo:
            lines.append("добавили фото")
        elif a_photo and not b_photo:
            lines.append("убрали фото")
        else:
            lines.append("сменили фото")
    a_video = _nested_id(a, "video")
    b_video = _nested_id(b, "video")
    if a_video != b_video:
        lines.append("сменили видео")
    return lines


def _row(row: sqlite3.Row) -> AbTest:
    followup_raw = row["followup"]
    followup = json.loads(followup_raw) if followup_raw else None
    if followup is not None and not isinstance(followup, dict):
        followup = None
    baseline = json.loads(row["baseline"])
    if not isinstance(baseline, dict):
        baseline = {}
    return AbTest(
        id=row["id"],
        user_id=row["user_id"],
        chat_id=row["chat_id"],
        account_id=row["account_id"],
        ad_id=row["ad_id"],
        note=row["note"] or "",
        status=row["status"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        baseline=baseline,
        followup=followup,
    )


class AbStore:
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
                CREATE TABLE IF NOT EXISTS ab_tests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    account_id TEXT,
                    ad_id INTEGER NOT NULL,
                    note TEXT,
                    status TEXT NOT NULL,
                    started_at INTEGER NOT NULL,
                    ended_at INTEGER,
                    baseline TEXT NOT NULL,
                    followup TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_ab_running
                ON ab_tests(user_id, ad_id, status)
                """
            )
            conn.commit()

    async def start(
        self,
        *,
        user_id: int,
        chat_id: int,
        account_id: str | None,
        ad_id: int,
        note: str,
        baseline: dict[str, Any],
    ) -> AbTest:
        running = await self.get_running(user_id, ad_id)
        if running is not None:
            return running
        async with self._lock:
            test_id = await asyncio.to_thread(
                self._insert,
                user_id,
                chat_id,
                account_id,
                ad_id,
                note.strip(),
                json.dumps(baseline, ensure_ascii=False, default=str),
                int(time.time()),
            )
        test = await self.get(test_id, user_id)
        assert test is not None
        return test

    def _insert(
        self,
        user_id: int,
        chat_id: int,
        account_id: str | None,
        ad_id: int,
        note: str,
        baseline: str,
        started_at: int,
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO ab_tests (
                    user_id, chat_id, account_id, ad_id, note, status, started_at, baseline
                ) VALUES (?, ?, ?, ?, ?, 'running', ?, ?)
                """,
                (user_id, chat_id, account_id, ad_id, note, started_at, baseline),
            )
            conn.commit()
            return int(cur.lastrowid)

    async def get(self, test_id: int, user_id: int) -> AbTest | None:
        async with self._lock:
            return await asyncio.to_thread(self._get, test_id, user_id)

    def _get(self, test_id: int, user_id: int) -> AbTest | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM ab_tests WHERE id=? AND user_id=?",
                (test_id, user_id),
            ).fetchone()
            return _row(row) if row else None

    async def get_running(self, user_id: int, ad_id: int) -> AbTest | None:
        async with self._lock:
            return await asyncio.to_thread(self._get_running, user_id, ad_id)

    def _get_running(self, user_id: int, ad_id: int) -> AbTest | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM ab_tests
                WHERE user_id=? AND ad_id=? AND status='running'
                ORDER BY id DESC LIMIT 1
                """,
                (user_id, ad_id),
            ).fetchone()
            return _row(row) if row else None

    async def list_for_user(self, user_id: int) -> list[AbTest]:
        async with self._lock:
            return await asyncio.to_thread(self._list, user_id)

    def _list(self, user_id: int) -> list[AbTest]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ab_tests WHERE user_id=? ORDER BY id DESC",
                (user_id,),
            ).fetchall()
            return [_row(row) for row in rows]

    async def finish(self, test_id: int, user_id: int, followup: dict[str, Any]) -> AbTest | None:
        async with self._lock:
            await asyncio.to_thread(self._finish, test_id, user_id, followup)
        return await self.get(test_id, user_id)

    def _finish(self, test_id: int, user_id: int, followup: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE ab_tests
                SET status='done', ended_at=?, followup=?
                WHERE id=? AND user_id=? AND status='running'
                """,
                (
                    int(time.time()),
                    json.dumps(followup, ensure_ascii=False, default=str),
                    test_id,
                    user_id,
                ),
            )
            conn.commit()

    async def delete(self, test_id: int, user_id: int) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._delete, test_id, user_id)

    def _delete(self, test_id: int, user_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM ab_tests WHERE id=? AND user_id=?",
                (test_id, user_id),
            )
            conn.commit()
            return cur.rowcount > 0
