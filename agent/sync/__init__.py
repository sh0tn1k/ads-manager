"""Read-only synchronisation from the Ads API into the agent store.

Only read methods are called here: getCurrentAccount, getAdsList, getAdStats.
"""

from agent.sync.syncers import AccountSync, AdsSync, StatsSync, SyncResult, SyncService

__all__ = ["AccountSync", "AdsSync", "StatsSync", "SyncResult", "SyncService"]
