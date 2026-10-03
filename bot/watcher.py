"""Background loop: poll Ads API and pause ads when a rule threshold is crossed."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict

from aiogram import Bot

from ads_api.client import AdsClient
from ads_api.errors import AdsApiError, AdsRateLimitError, AdsTransportError
from ads_api.models import Ad
from bot.rich import button_row, heading, paragraph, quote, rich
from bot.rules import (
    POLL_IDLE,
    POLL_NEAR,
    AdRule,
    RuleStore,
    can_pause,
    crossed,
    format_metric,
    metric_value,
    poll_interval,
    rule_title,
)

logger = logging.getLogger("bot.rules")

BATCH = 40


def _until_due(rule: AdRule, now: int) -> float:
    if not rule.last_checked_at:
        return 0.0
    elapsed = max(0.0, float(now - rule.last_checked_at))
    interval = poll_interval(
        metric=rule.metric,
        threshold=rule.threshold,
        value=rule.last_value or 0.0,
        last_value=rule.last_value,
        dt=elapsed or None,
    )
    return interval - elapsed


class RuleWatcher:
    def __init__(self, ads: AdsClient, bot: Bot, store: RuleStore) -> None:
        self.ads = ads
        self.bot = bot
        self.store = store

    async def run(self, stop: asyncio.Event) -> None:
        logger.info("autostop watcher started")
        while not stop.is_set():
            try:
                delay = await self.tick()
            except AdsRateLimitError as exc:
                delay = exc.retry_after or 5.0
                logger.warning("autostop rate-limited, sleep %.1fs", delay)
            except Exception:  # noqa: BLE001
                logger.exception("autostop tick failed")
                delay = 12.0
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except TimeoutError:
                continue
        logger.info("autostop watcher stopped")

    async def tick(self) -> float:
        now = int(time.time())
        await self.store.rearm_daily(now)
        rules = await self.store.list_active()
        if not rules:
            return POLL_IDLE
        due: list[AdRule] = []
        delay = POLL_IDLE
        for rule in rules:
            wait = _until_due(rule, now)
            if wait <= 0:
                due.append(rule)
            else:
                delay = min(delay, wait)
        if not due:
            return max(POLL_NEAR, delay)
        ads = await self._load_ads(due)
        for rule in due:
            ad = ads.get(rule.ad_id)
            if ad is None:
                continue
            wait = await self._apply(rule, ad, now)
            delay = min(delay, wait)
        return max(POLL_NEAR, delay)

    async def _load_ads(self, rules: list[AdRule]) -> dict[int, Ad]:
        grouped: dict[str | None, list[int]] = defaultdict(list)
        seen: set[tuple[str | None, int]] = set()
        for rule in rules:
            key = (rule.account_id, rule.ad_id)
            if key in seen:
                continue
            seen.add(key)
            grouped[rule.account_id].append(rule.ad_id)
        found: dict[int, Ad] = {}
        for account_id, ad_ids in grouped.items():
            for i in range(0, len(ad_ids), BATCH):
                chunk = ad_ids[i : i + BATCH]
                try:
                    items = await self.ads.get_ads_by_id(
                        chunk,
                        account_id=account_id,
                        return_target=False,
                    )
                except (AdsApiError, AdsTransportError):
                    logger.exception("autostop getAdsById failed account=%s ids=%s", account_id, chunk)
                    continue
                for ad in items:
                    found[ad.ad_id] = ad
        return found

    async def _apply(self, rule: AdRule, ad: Ad, now: int) -> float:
        value = metric_value(ad, rule.metric)
        dt = None
        if rule.last_checked_at:
            dt = max(0.0, float(now - rule.last_checked_at))
        interval = poll_interval(
            metric=rule.metric,
            threshold=rule.threshold,
            value=value,
            last_value=rule.last_value,
            dt=dt,
        )
        if not crossed(rule.metric, rule.threshold, value):
            await self.store.record_check(rule.id, value, now)
            return interval
        if can_pause(ad):
            try:
                await self.ads.edit_ad(
                    ad_id=ad.ad_id,
                    is_paused=True,
                    account_id=rule.account_id,
                )
            except (AdsApiError, AdsTransportError):
                logger.exception("autostop pause failed ad=%s rule=%s", ad.ad_id, rule.id)
                await self.store.record_check(rule.id, value, now)
                return POLL_MIN
        await self.store.mark_fired(rule.id, value, now)
        logger.info(
            "autostop fired rule=%s ad=%s %s value=%s threshold=%s",
            rule.id,
            ad.ad_id,
            rule.metric,
            value,
            rule.threshold,
        )
        await self._notify(rule, ad, value)
        return interval

    async def _notify(self, rule: AdRule, ad: Ad, value: float) -> None:
        currency = str(ad.currency) if ad.currency else None
        actual = format_metric(rule.metric, value, currency)
        limit = format_metric(rule.metric, rule.threshold, currency)
        payload = rich(
            heading(f"Автостоп #{ad.ad_id}"),
            quote(f"{rule_title(rule)}. Сейчас {actual} (порог {limit})."),
            paragraph(ad.title),
            paragraph("Объявление поставлено на паузу."),
            button_row(
                [
                    ("К карточке", f"ads:view:{ad.ad_id}", "primary"),
                    ("Правила", f"ads:rules:{ad.ad_id}"),
                    ("Меню", "nav:menu", "link"),
                ]
            ),
        )
        try:
            await self.bot.send_rich_message(chat_id=rule.chat_id, rich_message=payload)
        except Exception:  # noqa: BLE001
            logger.exception("autostop notify failed chat=%s rule=%s", rule.chat_id, rule.id)
