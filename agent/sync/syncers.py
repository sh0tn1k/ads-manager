from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ads_api.client import AdsClient
from ads_api.enums import StatsInterval
from ads_api.models import Account, Ad
from agent.clock import Clock, system_clock
from agent.store import Store

logger = logging.getLogger("agent.sync")

MAX_POINTS_PER_REQUEST = 1000  # docs: period <= 1000 * interval
DEFAULT_LOOKBACK_DAYS = 90


@dataclass
class SyncResult:
    account_snapshot_added: bool = False
    ads_seen: int = 0
    ad_snapshots_added: int = 0
    stats_rows: int = 0
    errors: list[str] = field(default_factory=list)


class AccountSync:
    def __init__(self, ads: AdsClient, store: Store, clock: Clock = system_clock) -> None:
        self.ads = ads
        self.store = store
        self.clock = clock

    async def run(self) -> tuple[Account, bool]:
        account = await self.ads.get_current_account()
        added = self.store.accounts.add(account, self.clock())
        return account, added


class AdsSync:
    """Full getAdsList sync with ``return_target=True``; upserts ads and status snapshots."""

    def __init__(self, ads: AdsClient, store: Store, clock: Clock = system_clock, page_size: int = 100) -> None:
        self.ads = ads
        self.store = store
        self.clock = clock
        self.page_size = page_size

    async def run(self) -> tuple[list[Ad], int]:
        ts = self.clock()
        seen: list[Ad] = []
        snapshots = 0
        async for ad in self.ads.iter_ads(limit=self.page_size, return_target=True):
            self.store.ads.upsert(ad, ts)
            if self.store.ad_snapshots.add(ad, ts):
                snapshots += 1
            seen.append(ad)
        return seen, snapshots

    async def refresh(self, ad_ids: list[int]) -> list[Ad]:
        """Refresh specific ads (e.g. the active ones every ~15 minutes)."""
        if not ad_ids:
            return []
        ts = self.clock()
        ads = await self.ads.get_ads_by_id(ad_ids, return_target=True)
        for ad in ads:
            self.store.ads.upsert(ad, ts)
            self.store.ad_snapshots.add(ad, ts)
        return ads


class StatsSync:
    """getAdStats into ad_stats keyed by (ad_id, from_time, interval)."""

    def __init__(self, ads: AdsClient, store: Store, clock: Clock = system_clock) -> None:
        self.ads = ads
        self.store = store
        self.clock = clock

    async def run_for_ad(
        self,
        ad_id: int,
        *,
        interval: int = int(StatsInterval.ONE_DAY),
        from_time: int | None = None,
        to_time: int | None = None,
        created_date: int | None = None,
    ) -> int:
        now = to_time if to_time is not None else self.clock()
        if from_time is None:
            last = self.store.ad_stats.last_from_time(ad_id, interval)
            if last is not None:
                from_time = last  # re-fetch the last (possibly partial) bucket
            elif created_date:
                from_time = created_date
            else:
                from_time = now - DEFAULT_LOOKBACK_DAYS * 86400
        rows = 0
        span = MAX_POINTS_PER_REQUEST * interval
        start = from_time
        while start < now:
            end = min(start + span, now)
            items = await self.ads.get_ad_stats(ad_id=ad_id, from_time=start, to_time=end, interval=interval)
            for item in items:
                self.store.ad_stats.upsert(ad_id, interval, item)
                rows += 1
            start = end
        return rows


class SyncService:
    """Composes the three syncers in the order of section 12."""

    def __init__(self, ads: AdsClient, store: Store, clock: Clock = system_clock) -> None:
        self.account = AccountSync(ads, store, clock)
        self.ads = AdsSync(ads, store, clock)
        self.stats = StatsSync(ads, store, clock)
        self.store = store

    async def full(self, *, interval: int = int(StatsInterval.ONE_DAY)) -> SyncResult:
        result = SyncResult()
        _, result.account_snapshot_added = await self.account.run()
        ads, result.ad_snapshots_added = await self.ads.run()
        result.ads_seen = len(ads)
        for ad in ads:
            if not ad.created_date and not ad.views:
                continue
            result.stats_rows += await self.stats.run_for_ad(
                ad.ad_id, interval=interval, created_date=ad.created_date
            )
        return result

    async def active(self) -> SyncResult:
        """~15-minute loop: refresh live ads and their 5-minute stats."""
        result = SyncResult()
        ids = self.store.ads.live_ids()
        ads = await self.ads.refresh(ids)
        result.ads_seen = len(ads)
        for ad in ads:
            result.stats_rows += await self.stats.run_for_ad(
                ad.ad_id, interval=int(StatsInterval.FIVE_MINUTES), created_date=ad.created_date
            )
        return result
