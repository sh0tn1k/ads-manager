"""SQLite store for the agent (``data/agent.sqlite``), separate from the bot FSM."""

from __future__ import annotations

from pathlib import Path

from agent.store.db import Database
from agent.proposals.repository import ProposalRepo
from agent.store.repositories import (
    AccountSnapshotRepo,
    AdRepo,
    AdSnapshotRepo,
    AdStatsRepo,
    ReservationQueries,
)


class Store:
    """All repositories over one database."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.accounts = AccountSnapshotRepo(db)
        self.ads = AdRepo(db)
        self.ad_snapshots = AdSnapshotRepo(db)
        self.ad_stats = AdStatsRepo(db)
        self.reservations = ReservationQueries(db)
        self.proposals = ProposalRepo(db)

    @classmethod
    def open(cls, path: str | Path) -> "Store":
        return cls(Database(path))

    def close(self) -> None:
        self.db.close()


__all__ = ["Database", "Store"]
