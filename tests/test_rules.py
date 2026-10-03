from __future__ import annotations

import time
from pathlib import Path

import pytest
from ads_api.models import Ad

from bot.rules import (
    POLL_MAX,
    POLL_MIN,
    POLL_NEAR,
    RuleStore,
    can_pause,
    crossed,
    metric_value,
    poll_interval,
    utc_day,
)
from bot.watcher import RuleWatcher, _until_due


def _ad(**kwargs) -> Ad:
    payload = {
        "ad_id": 156,
        "title": "AI Posted Search Brands",
        "currency": "TON",
        "promote_url": "https://t.me/ai_posted",
        "cpm": 0.1,
        "spent_budget": 0.0022,
        "remaining_budget": 0.9978,
        "views": 22,
        "clicks": 4,
        "actions": 1,
        "status": "active",
        "is_paused": False,
    }
    payload.update(kwargs)
    return Ad.model_validate(payload)


def test_metric_value_and_cross():
    ad = _ad(views=1000, clicks=4, actions=1, spent_budget=0.5, daily_spent_budget=0.2)
    assert metric_value(ad, "views") == 1000
    assert metric_value(ad, "clicks") == 4
    assert metric_value(ad, "spent") == 0.5
    assert metric_value(ad, "daily_spent") == 0.2
    assert crossed("views", 1000, 1000)
    assert crossed("views", 1000, 1003)
    assert not crossed("views", 1000, 999)
    assert crossed("spent", 0.5, 0.5)
    assert not crossed("spent", 1, 0.5)
    assert can_pause(ad)
    assert not can_pause(_ad(is_paused=True))
    assert not can_pause(_ad(status="in_review"))


def test_poll_interval_tightens_near_threshold():
    far = poll_interval(metric="views", threshold=1000, value=20, last_value=None, dt=None)
    near = poll_interval(metric="views", threshold=1000, value=960, last_value=940, dt=8)
    hit = poll_interval(metric="views", threshold=1000, value=1000, last_value=980, dt=4)
    assert far >= 10
    assert far <= POLL_MAX
    assert near <= POLL_MIN * 1.5
    assert hit == POLL_NEAR


def test_poll_interval_uses_speed_for_eta():
    wait = poll_interval(metric="views", threshold=1000, value=900, last_value=850, dt=10)
    assert 4 <= wait <= 8


@pytest.mark.asyncio
async def test_rule_store_roundtrip():
    path = Path("data") / "test_rules.sqlite"
    path.parent.mkdir(exist_ok=True)
    if path.exists():
        path.unlink()
    store = RuleStore(path)
    rule = await store.add(
        ad_id=156,
        account_id=None,
        chat_id=1,
        user_id=2,
        metric="views",
        threshold=1000,
    )
    assert rule.status == "active"
    loaded = await store.get(rule.id)
    assert loaded is not None
    assert loaded.threshold == 1000
    await store.record_check(rule.id, 22, int(time.time()))
    checked = await store.get(rule.id)
    assert checked is not None
    assert checked.last_value == 22
    await store.mark_fired(rule.id, 1003, int(time.time()))
    fired = await store.get(rule.id)
    assert fired is not None
    assert fired.status == "fired"
    assert fired.fired_value == 1003
    await store.set_status(rule.id, "active")
    armed = await store.get(rule.id)
    assert armed is not None
    assert armed.status == "active"
    await store.delete(rule.id)
    assert await store.get(rule.id) is None


@pytest.mark.asyncio
async def test_rearm_daily_spent():
    path = Path("data") / "test_rules_daily.sqlite"
    path.parent.mkdir(exist_ok=True)
    if path.exists():
        path.unlink()
    store = RuleStore(path)
    rule = await store.add(
        ad_id=1,
        account_id=None,
        chat_id=1,
        user_id=2,
        metric="daily_spent",
        threshold=1,
    )
    yesterday = int(time.time()) - 90000
    await store.mark_fired(rule.id, 1.2, yesterday)
    assert utc_day(yesterday) < utc_day(int(time.time()))
    count = await store.rearm_daily(int(time.time()))
    assert count == 1
    armed = await store.get(rule.id)
    assert armed is not None
    assert armed.status == "active"


@pytest.mark.asyncio
async def test_watcher_pauses_when_views_hit_limit():
    path = Path("data") / "test_rules_watch.sqlite"
    path.parent.mkdir(exist_ok=True)
    if path.exists():
        path.unlink()
    store = RuleStore(path)
    rule = await store.add(
        ad_id=156,
        account_id=None,
        chat_id=42,
        user_id=7,
        metric="views",
        threshold=1000,
    )
    ad = _ad(views=1003)

    class FakeAds:
        def __init__(self) -> None:
            self.paused: list[int] = []

        async def get_ads_by_id(self, ad_ids, **kwargs):
            return [ad]

        async def edit_ad(self, **kwargs):
            self.paused.append(kwargs["ad_id"])
            return ad

    class FakeBot:
        def __init__(self) -> None:
            self.sent = []

        async def send_rich_message(self, **kwargs):
            self.sent.append(kwargs)

    ads = FakeAds()
    bot = FakeBot()
    watcher = RuleWatcher(ads, bot, store)  # type: ignore[arg-type]
    delay = await watcher.tick()
    assert ads.paused == [156]
    assert bot.sent
    fired = await store.get(rule.id)
    assert fired is not None
    assert fired.status == "fired"
    assert fired.fired_value == 1003
    assert delay >= POLL_NEAR


def test_until_due_is_immediate_without_history():
    from bot.rules import AdRule

    rule = AdRule(
        id=1,
        ad_id=1,
        account_id=None,
        chat_id=1,
        user_id=1,
        metric="views",
        threshold=1000,
    )
    assert _until_due(rule, int(time.time())) == 0.0
