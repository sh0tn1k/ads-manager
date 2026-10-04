"""Wiring of agent components for callers (the bot) that must not build them by hand."""

from __future__ import annotations

from dataclasses import dataclass

from ads_api.client import AdsClient
from agent.clock import Clock, system_clock
from agent.config import AgentConfig
from agent.planner.balance import FreeBalanceProvider
from agent.store import Store
from agent.sync import SyncService


@dataclass
class AgentServices:
    config: AgentConfig
    store: Store
    sync: SyncService
    balance: FreeBalanceProvider

    @classmethod
    def build(
        cls,
        ads: AdsClient,
        config: AgentConfig | None = None,
        *,
        store: Store | None = None,
        clock: Clock = system_clock,
    ) -> "AgentServices":
        config = config or AgentConfig()
        store = store or Store.open(config.db_path)
        return cls(
            config=config,
            store=store,
            sync=SyncService(ads, store, clock),
            balance=FreeBalanceProvider(ads, store, config),
        )
