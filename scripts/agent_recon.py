"""Read-only recon for the proposals agent (phase 0).

Settles the balance-semantics question (getCurrentAccount vs sum of getAdsList remaining_budget vs
getAccountTransactionsList) and prints placement x target.type pairs of existing ads.

Makes ZERO mutating calls: every request goes through ``ReadOnlyAdsClient.request`` which refuses
any method outside ``READ_ONLY_METHODS`` before anything is sent. The token is never printed.

    python scripts/agent_recon.py [--json]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ads_api.client import AdsClient  # noqa: E402
from ads_api.config import AdsApiConfig  # noqa: E402
from ads_api.models import Account, AccountTransaction, Ad  # noqa: E402

READ_ONLY_METHODS = frozenset({"getCurrentAccount", "getAdsList", "getAccountTransactionsList"})
LIVE_STATUSES = frozenset({"active", "on_hold", "in_review", "ready_for_review"})
TOLERANCE = 0.01


class MutatingCallRefused(RuntimeError):
    pass


class ReadOnlyAdsClient(AdsClient):
    """AdsClient that can only call the methods this script needs."""

    async def request(self, method: str, params: Mapping[str, Any] | None = None, **kwargs: Any) -> Any:
        if method not in READ_ONLY_METHODS:
            raise MutatingCallRefused(f"recon script refuses to call {method}")
        if kwargs.get("upload") is not None or kwargs.get("idempotency_key"):
            raise MutatingCallRefused("recon script never uploads or uses idempotency keys")
        return await super().request(method, params)


def _status(ad: Ad) -> str:
    return str(ad.status or "").rsplit(".", 1)[-1]


def analyze(account: Account, ads: list[Ad], transactions: list[AccountTransaction]) -> dict[str, Any]:
    ads_remaining = round(sum(ad.remaining_budget for ad in ads), 5)
    live_remaining = round(sum(ad.remaining_budget for ad in ads if _status(ad) in LIVE_STATUSES), 5)
    ads_spent = round(sum(ad.spent_budget for ad in ads), 5)
    tx_by_peer: dict[str, float] = defaultdict(float)
    for tx in transactions:
        tx_by_peer[str(tx.peer.type).rsplit(".", 1)[-1]] += tx.amount
    tx_by_peer = {k: round(v, 5) for k, v in tx_by_peer.items()}
    tx_total = round(sum(tx_by_peer.values()), 5)

    ads_budget_matches_sum = abs(account.ads_budget - ads_remaining) <= TOLERANCE
    # H1: remaining_budget is free cash, ad budgets already moved out of it.
    h1_ledger = abs(tx_total - account.remaining_budget) <= TOLERANCE
    # H2: remaining_budget still contains money sitting on ad budgets.
    h2_ledger = abs(tx_total + account.ads_budget - account.remaining_budget) <= TOLERANCE
    if h1_ledger and not h2_ledger:
        verdict = "H1 excludes_ads: free = remaining_budget - reserves"
    elif h2_ledger and not h1_ledger:
        verdict = "H2 includes_ads: free = remaining_budget - sum(live ad remaining) - reserves"
    else:
        verdict = "inconclusive: send this output to the agent maintainer"

    pairs = Counter(
        f"{str(ad.placement or '-').rsplit('.', 1)[-1]} x "
        f"{str(getattr(ad.target, 'type', '-') or '-').rsplit('.', 1)[-1]}"
        for ad in ads
    )
    action_types = Counter(str(ad.action_type or "-").rsplit(".", 1)[-1] for ad in ads)
    text_lengths = [len(ad.text) for ad in ads if ad.text]
    return {
        "account": {
            "account_id": account.account_id,
            "currency": str(account.currency),
            "spent_budget": account.spent_budget,
            "remaining_budget": account.remaining_budget,
            "ads_budget": account.ads_budget,
        },
        "ads_count": len(ads),
        "sum_ad_remaining_budget": ads_remaining,
        "sum_live_ad_remaining_budget": live_remaining,
        "sum_ad_spent_budget": ads_spent,
        "ads_budget_minus_sum_ad_remaining": round(account.ads_budget - ads_remaining, 5),
        "ads_budget_matches_sum_ad_remaining": ads_budget_matches_sum,
        "transactions_count": len(transactions),
        "transactions_by_peer": tx_by_peer,
        "transactions_total": tx_total,
        "h1_ledger_match": h1_ledger,
        "h2_ledger_match": h2_ledger,
        "verdict": verdict,
        "placement_x_target_type": dict(pairs),
        "action_types": dict(action_types),
        "max_text_length": max(text_lengths) if text_lengths else None,
    }


async def collect(client: AdsClient) -> tuple[Account, list[Ad], list[AccountTransaction]]:
    account = await client.get_current_account()
    ads = [ad async for ad in client.iter_ads(return_target=True)]
    transactions: list[AccountTransaction] = []
    offset: int | str | None = None
    while True:
        page = await client.get_account_transactions_list(offset=offset, limit=100)
        transactions.extend(page.transactions)
        if page.next_offset in (None, "") or not page.transactions:
            break
        offset = page.next_offset
    return account, ads, transactions


def render(report: dict[str, Any]) -> str:
    lines = ["== Account =="]
    lines += [f"  {k}: {v}" for k, v in report["account"].items()]
    lines.append("== Balance semantics ==")
    for key in (
        "ads_count",
        "sum_ad_remaining_budget",
        "sum_live_ad_remaining_budget",
        "sum_ad_spent_budget",
        "ads_budget_minus_sum_ad_remaining",
        "ads_budget_matches_sum_ad_remaining",
        "transactions_count",
        "transactions_by_peer",
        "transactions_total",
        "h1_ledger_match",
        "h2_ledger_match",
        "verdict",
    ):
        lines.append(f"  {key}: {report[key]}")
    lines.append("== placement x target.type ==")
    lines += [f"  {k}: {v}" for k, v in sorted(report["placement_x_target_type"].items())]
    lines.append("== action_type ==")
    lines += [f"  {k}: {v}" for k, v in sorted(report["action_types"].items())]
    lines.append(f"max_text_length: {report['max_text_length']}")
    return "\n".join(lines)


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")
    args = parser.parse_args(argv)
    config = AdsApiConfig()  # type: ignore[call-arg]
    async with ReadOnlyAdsClient.from_config(config) as client:
        report = analyze(*await collect(client))
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
