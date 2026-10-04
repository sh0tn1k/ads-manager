"""Wiring of agent components for callers (the bot) that must not build them by hand."""

from __future__ import annotations

from dataclasses import dataclass

from ads_api.client import AdsClient
from agent.candidates.cycle import ProposalCycle
from agent.clock import Clock, system_clock
from agent.config import AgentConfig
from agent.executor import Executor
from agent.planner.balance import FreeBalanceProvider
from agent.proposals.service import ProposalService
from agent.store import Store
from agent.sync import SyncService
from agent.validator.dictionaries import DictionaryCache


@dataclass
class AgentServices:
    config: AgentConfig
    store: Store
    sync: SyncService
    balance: FreeBalanceProvider
    proposals: ProposalService
    cycle: ProposalCycle

    @classmethod
    def build(
        cls,
        ads: AdsClient,
        config: AgentConfig | None = None,
        *,
        store: Store | None = None,
        clock: Clock = system_clock,
        validate_dictionaries: bool = True,
    ) -> "AgentServices":
        config = config or AgentConfig()
        store = store or Store.open(config.db_path)
        balance = FreeBalanceProvider(ads, store, config)
        dictionaries = DictionaryCache(ads) if validate_dictionaries else None
        executor = Executor(
            ads,
            store,
            config,
            balance,
            dictionaries=dictionaries.get if dictionaries else None,
            clock=clock,
        )
        proposals = ProposalService(store, config, balance, executor, clock)
        return cls(
            config=config,
            store=store,
            sync=SyncService(ads, store, clock),
            balance=balance,
            proposals=proposals,
            cycle=ProposalCycle(store, config, balance, proposals, clock),
        )
