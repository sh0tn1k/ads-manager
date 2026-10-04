"""Free balance for proposals (section 8).

The exact meaning of ``Account.remaining_budget`` vs ``ads_budget`` is NOT confirmed
(docs/agent-recon.md section 2). The unverified part is isolated in ``compute_free_balance``.
"""

from __future__ import annotations

from dataclasses import dataclass

from ads_api.client import AdsClient
from ads_api.models import Account
from agent.config import AgentConfig
from agent.store import Store


@dataclass(frozen=True)
class BalanceView:
    currency: str
    account_remaining: float
    account_ads_budget: float
    account_spent: float
    live_ads_remaining: float
    reserved: float
    free: float
    semantics: str


def compute_free_balance(
    *,
    account_remaining: float,
    live_ads_remaining: float,
    reserved: float,
    semantics: str,
) -> float:
    """[VERIFY] UNVERIFIED FORMULA — the only place that encodes balance semantics.

    ``includes_ads`` (default, conservative): remaining_budget still contains money sitting on ad
    budgets, so live ads' remaining budgets are subtracted (plan section 8 formula).
    ``excludes_ads``: remaining_budget is already net of ad budgets.

    Switch with AGENT_BALANCE_SEMANTICS after running scripts/agent_recon.py.
    """
    if semantics == "includes_ads":
        base = account_remaining - live_ads_remaining
    elif semantics == "excludes_ads":
        base = account_remaining
    else:  # pragma: no cover - guarded by AgentConfig validator
        raise ValueError(f"unknown balance semantics {semantics!r}")
    return round(base - reserved, 5)


class FreeBalanceProvider:
    def __init__(self, ads: AdsClient, store: Store, config: AgentConfig) -> None:
        self.ads = ads
        self.store = store
        self.config = config

    def view(self, account: Account, *, exclude_proposal_id: str | None = None) -> BalanceView:
        live = self.store.ads.live_remaining_total()
        reserved = self.store.reservations.reserved_total(exclude_proposal_id=exclude_proposal_id)
        free = compute_free_balance(
            account_remaining=float(account.remaining_budget),
            live_ads_remaining=live,
            reserved=reserved,
            semantics=self.config.balance_semantics,
        )
        return BalanceView(
            currency=str(getattr(account.currency, "value", account.currency)),
            account_remaining=float(account.remaining_budget),
            account_ads_budget=float(account.ads_budget),
            account_spent=float(account.spent_budget),
            live_ads_remaining=live,
            reserved=reserved,
            free=free,
            semantics=self.config.balance_semantics,
        )

    async def current(self, *, exclude_proposal_id: str | None = None) -> BalanceView:
        """Fresh getCurrentAccount (read-only) + store state."""
        account = await self.ads.get_current_account()
        return self.view(account, exclude_proposal_id=exclude_proposal_id)
